"""Model layer: ONNX Runtime session + tokenizer, loaded once and hot-swappable.

The on-disk bundle format is the contract with the ML team — see
contracts/model_bundle.md. onnxruntime / tokenizers / numpy are imported
lazily so the gate, routing and API can be tested without them.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

SUPPORTED_INPUTS = {"input_ids", "attention_mask", "token_type_ids"}


class ModelNotReady(RuntimeError):
    """No model is loaded (startup load failed or never attempted)."""


@dataclass(frozen=True)
class ScoreResult:
    risk_score: float
    label: str
    label_scores: dict[str, float]
    model_version: str
    inference_ms: float


class Scorer(Protocol):
    version: str

    def score(self, text: str) -> ScoreResult: ...

    # Optional: `score_batch(texts) -> list[ScoreResult]`, used only when the scorer also
    # sets `batch_invariant = True` (a text's score doesn't depend on what it's batched with).


@dataclass(frozen=True)
class ModelMeta:
    version: str
    labels: list[str]
    safe_label: str
    max_length: int = 256
    activation: str = "softmax"       # softmax (multi-class) | sigmoid (multi-label / single logit)
    pad_to_max_length: bool = False   # true only if the export has a fixed sequence axis
    output_name: str | None = None    # default: first model output
    model_file: str | None = None     # default: model.onnx, else the bundle's only *.onnx
    label_weights: dict[str, float] | None = None  # per-label risk weight; see _risk_from
    # True only if scores don't change when texts are batched together (FP32, static or
    # weight-only INT8). Dynamic INT8 (v1, v3) computes activation scales per batch: false.
    batch_invariant: bool = False
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: Path) -> "ModelMeta":
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = {k: raw.pop(k) for k in list(raw) if k in cls.__dataclass_fields__ and k != "extra"}
        meta = cls(**known, extra=raw)
        if meta.safe_label not in meta.labels:
            raise ValueError(f"safe_label {meta.safe_label!r} not in labels {meta.labels}")
        if meta.activation not in ("softmax", "sigmoid"):
            raise ValueError(f"activation must be softmax or sigmoid, got {meta.activation!r}")
        if meta.label_weights is not None:
            _validate_weights(meta.label_weights, meta.labels)
        return meta


class OnnxScorer:
    """Wraps one loaded model bundle. Construct once; `score` is thread-safe."""

    def __init__(self, model_dir: str | Path, *, intra_op_threads: int = 4, inter_op_threads: int = 1,
                 label_weights: dict[str, float] | None = None, providers: list[str] | None = None):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._np = np
        model_dir = Path(model_dir)
        for name in ("tokenizer.json", "model_meta.json"):
            if not (model_dir / name).exists():
                raise FileNotFoundError(f"model bundle incomplete: {model_dir / name} missing")

        self.meta = ModelMeta.from_file(model_dir / "model_meta.json")
        self.version = self.meta.version
        model_path = _find_model_file(model_dir, self.meta.model_file)
        with model_path.open("rb") as f:
            if f.read(40).startswith(b"version https://git-lfs"):
                raise FileNotFoundError(
                    f"{model_path} is a Git LFS pointer, not the model. Install Git LFS, then run `git lfs pull`.")
        # Operator override (settings.yaml) wins over the bundle's own weights.
        self.label_weights = label_weights or self.meta.label_weights
        if self.label_weights is not None:
            _validate_weights(self.label_weights, self.meta.labels)

        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=self.meta.max_length)
        if self.meta.pad_to_max_length:
            self.tokenizer.enable_padding(length=self.meta.max_length)
        else:
            self.tokenizer.no_padding()
        self.pad_id = self.tokenizer.token_to_id("[PAD]") or 0
        self.batch_invariant = self.meta.batch_invariant and not self.meta.pad_to_max_length

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = intra_op_threads
        opts.inter_op_num_threads = inter_op_threads
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # Production is CPU only (contracts/model_bundle.md). `providers` exists for offline
        # evaluation, where the same bundle can be run on the GPU to sweep thresholds quickly;
        # anything measured that way must be confirmed on CPU before it is trusted.
        self.session = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=providers or ["CPUExecutionProvider"]
        )
        self.providers = self.session.get_providers()

        self.input_names = [i.name for i in self.session.get_inputs()]
        unknown = set(self.input_names) - SUPPORTED_INPUTS
        if unknown:
            raise ValueError(f"model expects unsupported inputs {sorted(unknown)}; supported: {sorted(SUPPORTED_INPUTS)}")
        self.output_name = self.meta.output_name or self.session.get_outputs()[0].name

        # Warm-up: first run allocates buffers; also validates the output shape against labels.
        warm = self.score("warm up")
        log.info("model %s loaded from %s (warm-up %.1f ms)", self.version, model_dir, warm.inference_ms)

    def score(self, text: str) -> ScoreResult:
        np = self._np
        start = time.perf_counter()
        enc = self.tokenizer.encode(text)
        available = {
            "input_ids": enc.ids,
            "attention_mask": enc.attention_mask,
            "token_type_ids": enc.type_ids,
        }
        feeds = {name: np.asarray([available[name]], dtype=np.int64) for name in self.input_names}
        logits = self.session.run([self.output_name], feeds)[0][0].astype(np.float64)
        return self._result(logits, (time.perf_counter() - start) * 1000)

    def score_batch(self, texts: list[str], max_batch: int = 32) -> list[ScoreResult]:
        """Score many texts with padded batches (one ONNX call per `max_batch`).

        Only valid when `batch_invariant` is true. Texts are grouped by length so short
        sentences aren't padded to the longest one in the post.
        """
        if not self.batch_invariant:
            return [self.score(t) for t in texts]
        np = self._np
        encs = self.tokenizer.encode_batch(list(texts))
        order = sorted(range(len(texts)), key=lambda i: len(encs[i].ids))
        results: list[ScoreResult | None] = [None] * len(texts)
        for b in range(0, len(order), max_batch):
            idx = order[b:b + max_batch]
            start = time.perf_counter()
            width = max(len(encs[i].ids) for i in idx)
            arrays = {name: np.zeros((len(idx), width), dtype=np.int64) for name in self.input_names}
            if "input_ids" in arrays:
                arrays["input_ids"].fill(self.pad_id)
            for row, i in enumerate(idx):
                e, n = encs[i], len(encs[i].ids)
                available = {"input_ids": e.ids, "attention_mask": e.attention_mask, "token_type_ids": e.type_ids}
                for name in self.input_names:
                    arrays[name][row, :n] = available[name]
            logits = self.session.run([self.output_name], arrays)[0].astype(np.float64)
            per_text = (time.perf_counter() - start) * 1000 / len(idx)
            for row, i in enumerate(idx):
                results[i] = self._result(logits[row], per_text)
        return results  # type: ignore[return-value]

    def _result(self, logits, elapsed_ms: float) -> ScoreResult:
        np = self._np
        if logits.shape[-1] != len(self.meta.labels):
            raise ValueError(f"model output has {logits.shape[-1]} logits but meta lists {len(self.meta.labels)} labels")
        if self.meta.activation == "softmax":
            exp = np.exp(logits - logits.max())
            probs = exp / exp.sum()
        else:
            probs = 1.0 / (1.0 + np.exp(-logits))
        label_scores = {lbl: float(p) for lbl, p in zip(self.meta.labels, probs)}
        return ScoreResult(
            risk_score=_risk_from(label_scores, self.meta.safe_label, self.meta.activation, self.label_weights),
            label=max(label_scores, key=label_scores.get),
            label_scores=label_scores,
            model_version=self.version,
            inference_ms=elapsed_ms,
        )


def _find_model_file(model_dir: Path, declared: str | None) -> Path:
    if declared:
        path = model_dir / declared
        if not path.exists():
            raise FileNotFoundError(f"model bundle incomplete: {path} (model_file in meta) missing")
        return path
    if (model_dir / "model.onnx").exists():
        return model_dir / "model.onnx"
    candidates = sorted(model_dir.glob("*.onnx"))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(f"model bundle incomplete: no .onnx file in {model_dir}")
    raise FileNotFoundError(
        f"multiple .onnx files in {model_dir} ({', '.join(c.name for c in candidates)}); "
        "set model_file in model_meta.json")


def _validate_weights(weights: dict[str, float], labels: list[str]) -> None:
    if set(weights) != set(labels):
        raise ValueError(f"label_weights keys {sorted(weights)} must match labels {sorted(labels)}")
    if not all(0.0 <= float(w) <= 1.0 for w in weights.values()):
        raise ValueError(f"label_weights must be in [0, 1], got {weights}")


def _risk_from(label_scores: dict[str, float], safe_label: str, activation: str,
               weights: dict[str, float] | None = None) -> float:
    if weights:
        # Ordinal / decision-style labels (e.g. safe < review < reject): expected severity.
        if activation == "softmax":
            return min(1.0, sum(p * weights[lbl] for lbl, p in label_scores.items()))
        return max(p * weights[lbl] for lbl, p in label_scores.items())
    if len(label_scores) == 1:                      # single-logit binary model: score is P(violation)
        return next(iter(label_scores.values()))
    if activation == "softmax":
        return 1.0 - label_scores[safe_label]
    # sigmoid: independent per-label probabilities -> worst violation label
    return max(p for lbl, p in label_scores.items() if lbl != safe_label)


class StubScorer:
    """Dev-only stand-in for when no model is available. Never use in production."""

    version = "stub-0"

    def score(self, text: str) -> ScoreResult:
        # Deterministic, input-dependent score in [0, 1) so routing paths can be exercised.
        risk = (sum(map(ord, text)) % 1000) / 1000
        return ScoreResult(risk, "stub", {"stub": risk}, self.version, 0.0)


class ModelRegistry:
    """Holds the live scorer. Reload builds the new one fully, then swaps the reference,
    so in-flight requests finish on the old model and nothing ever sees a half-loaded one."""

    def __init__(self) -> None:
        self._scorer: Scorer | None = None
        self._lock = threading.Lock()
        self.last_error: str | None = None

    @property
    def ready(self) -> bool:
        return self._scorer is not None

    @property
    def version(self) -> str | None:
        return self._scorer.version if self._scorer else None

    def set(self, scorer: Scorer) -> None:
        with self._lock:
            self._scorer = scorer
            self.last_error = None

    def load(self, backend: str, model_dir: Path, **kwargs) -> None:
        try:
            scorer: Scorer = StubScorer() if backend == "stub" else OnnxScorer(model_dir, **kwargs)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.exception("model load failed")
            raise
        if backend == "stub":
            log.warning("STUB model backend active — scores are meaningless")
        self.set(scorer)

    def score(self, text: str) -> ScoreResult:
        scorer = self._scorer
        if scorer is None:
            raise ModelNotReady(self.last_error or "model not loaded")
        result = scorer.score(text)
        if not math.isfinite(result.risk_score):
            raise ValueError(f"model returned non-finite score {result.risk_score}")
        return result

    def score_many(self, texts: list[str]) -> list[ScoreResult]:
        """Score several texts: batched when the live model is batch-invariant, else one by one."""
        scorer = self._scorer
        if scorer is None:
            raise ModelNotReady(self.last_error or "model not loaded")
        if getattr(scorer, "batch_invariant", False) and hasattr(scorer, "score_batch"):
            results = scorer.score_batch(texts)
        else:
            results = [scorer.score(t) for t in texts]
        for r in results:
            if not math.isfinite(r.risk_score):
                raise ValueError(f"model returned non-finite score {r.risk_score}")
        return results
