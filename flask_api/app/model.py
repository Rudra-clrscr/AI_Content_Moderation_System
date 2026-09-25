"""Model layer: ONNX Runtime session + tokenizer, loaded once and hot-swappable.

The on-disk bundle format is the contract with Intern 1 — see
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


@dataclass(frozen=True)
class ModelMeta:
    version: str
    labels: list[str]
    safe_label: str
    max_length: int = 256
    activation: str = "softmax"       # softmax (multi-class) | sigmoid (multi-label / single logit)
    pad_to_max_length: bool = False   # true only if the export has a fixed sequence axis
    output_name: str | None = None    # default: first model output
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
        return meta


class OnnxScorer:
    """Wraps one loaded model bundle. Construct once; `score` is thread-safe."""

    def __init__(self, model_dir: str | Path, *, intra_op_threads: int = 1, inter_op_threads: int = 1):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._np = np
        model_dir = Path(model_dir)
        for name in ("model.onnx", "tokenizer.json", "model_meta.json"):
            if not (model_dir / name).exists():
                raise FileNotFoundError(f"model bundle incomplete: {model_dir / name} missing")

        self.meta = ModelMeta.from_file(model_dir / "model_meta.json")
        self.version = self.meta.version

        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=self.meta.max_length)
        if self.meta.pad_to_max_length:
            self.tokenizer.enable_padding(length=self.meta.max_length)
        else:
            self.tokenizer.no_padding()

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = intra_op_threads
        opts.inter_op_num_threads = inter_op_threads
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            str(model_dir / "model.onnx"), sess_options=opts, providers=["CPUExecutionProvider"]
        )

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
        elapsed = (time.perf_counter() - start) * 1000

        if logits.shape[-1] != len(self.meta.labels):
            raise ValueError(f"model output has {logits.shape[-1]} logits but meta lists {len(self.meta.labels)} labels")
        if self.meta.activation == "softmax":
            exp = np.exp(logits - logits.max())
            probs = exp / exp.sum()
        else:
            probs = 1.0 / (1.0 + np.exp(-logits))
        label_scores = {lbl: float(p) for lbl, p in zip(self.meta.labels, probs)}
        return ScoreResult(
            risk_score=_risk_from(label_scores, self.meta.safe_label, self.meta.activation),
            label=max(label_scores, key=label_scores.get),
            label_scores=label_scores,
            model_version=self.version,
            inference_ms=elapsed,
        )


def _risk_from(label_scores: dict[str, float], safe_label: str, activation: str) -> float:
    if len(label_scores) == 1:                      # single-logit binary model: score is P(violation)
        return next(iter(label_scores.values()))
    if activation == "softmax":
        return 1.0 - label_scores[safe_label]
    # sigmoid: independent per-label probabilities -> worst violation label
    return max(p for lbl, p in label_scores.items() if lbl != safe_label)


class StubScorer:
    """Dev-only stand-in until Intern 1 ships a model. Never use in production."""

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
