"""GPU scorer for offline evaluation: the v4 checkpoint behind the API's Scorer protocol.

    from torch_scorer import TorchScorer
    client = create_app(settings, scorer=TorchScorer("D:/AI/bonc-v4/ckpt", bundle)).test_client()

Why this exists: sweeping thresholds runs the whole pipeline over hundreds of texts several
times, and on CPU that is minutes per pass. onnxruntime-gpu can't be used here (its CUDA
provider wants the CUDA 13 toolkit; this machine has torch's 12.4 runtime), so offline runs
go through the PyTorch checkpoint on the GPU instead, with the same temperature the ONNX
graph has baked in.

**The deployed service is unaffected**: it always loads the ONNX bundle on CPU
(contracts/model_bundle.md). This is a measurement tool, and `verify_against_onnx` checks
that it agrees with the shipped bundle before its numbers are used for anything.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "flask_api"))

from app.model import ScoreResult, _risk_from   # noqa: E402


class TorchScorer:
    """Same interface as OnnxScorer, running on the GPU. Batch-invariant: padding is masked."""

    def __init__(self, ckpt: str | Path, bundle: str | Path, device: str = "cuda", max_length: int = 256):
        meta = json.loads((Path(bundle) / "model_meta.json").read_text(encoding="utf-8"))
        self.meta = meta
        self.version = meta["version"] + "+torch"
        self.labels = meta["labels"]
        self.safe_label = meta["safe_label"]
        self.label_weights = meta.get("label_weights")
        self.temperature = meta["temperature"]
        self.max_length = meta.get("max_length", max_length)
        self.batch_invariant = True
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")
        self.tok = AutoTokenizer.from_pretrained(ckpt)
        self.model = AutoModelForSequenceClassification.from_pretrained(ckpt).to(self.device).eval()

    @torch.no_grad()
    def _logits(self, texts: list[str]) -> np.ndarray:
        enc = self.tok(list(texts), truncation=True, max_length=self.max_length,
                       padding=True, return_tensors="pt").to(self.device)
        out = self.model(input_ids=enc["input_ids"], attention_mask=enc["attention_mask"]).logits
        return (out.float() / self.temperature).cpu().numpy()

    def _result(self, logits: np.ndarray, ms: float) -> ScoreResult:
        exp = np.exp(logits - logits.max())
        probs = exp / exp.sum()
        label_scores = {l: float(p) for l, p in zip(self.labels, probs)}
        return ScoreResult(_risk_from(label_scores, self.safe_label, "softmax", self.label_weights),
                           max(label_scores, key=label_scores.get), label_scores, self.version, ms)

    def score(self, text: str) -> ScoreResult:
        t0 = time.perf_counter()
        logits = self._logits([text])[0]
        return self._result(logits, (time.perf_counter() - t0) * 1000)

    def score_batch(self, texts: list[str], max_batch: int = 32) -> list[ScoreResult]:
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        results: list[ScoreResult | None] = [None] * len(texts)
        for b in range(0, len(order), max_batch):
            idx = order[b:b + max_batch]
            t0 = time.perf_counter()
            logits = self._logits([texts[i] for i in idx])
            per = (time.perf_counter() - t0) * 1000 / len(idx)
            for row, i in enumerate(idx):
                results[i] = self._result(logits[row], per)
        return results  # type: ignore[return-value]


def make_scorer(device: str, bundle: str | Path, ckpt: str | Path = "D:/AI/bonc-v4/ckpt", **onnx_kwargs):
    """The scorer an offline script should use. "cpu" is the real shipped path (ONNX on CPU);
    "cuda" is the fast stand-in for sweeps, ~12x quicker on batched spans."""
    if device == "cuda":
        return TorchScorer(ckpt, bundle)
    from app.model import OnnxScorer
    return OnnxScorer(bundle, **onnx_kwargs)


def verify_against_onnx(scorer: "TorchScorer", bundle: str | Path, texts: list[str], boundary: float = 0.5) -> dict:
    """Compare this scorer with the bundle the service actually ships, on CPU."""
    from app.model import OnnxScorer
    onnx = OnnxScorer(bundle)
    a = np.array([r.risk_score for r in scorer.score_batch(texts)])
    b = np.array([onnx.score(t).risk_score for t in texts])
    return {"n": len(texts), "max_abs_diff": float(np.abs(a - b).max()),
            "decisions_agree": int(((a > boundary) == (b > boundary)).sum())}


if __name__ == "__main__":
    import pandas as pd
    here = Path(__file__).resolve().parent
    bundle = here.parent / "flask_api" / "models" / "v4"
    texts = pd.read_csv(here / "eval_handwritten.csv")["text"].tolist()
    s = TorchScorer("D:/AI/bonc-v4/ckpt", bundle)
    print(f"device {s.device}")
    for name, fn in (("one at a time", lambda: [s.score(t) for t in texts]),
                     ("batched", lambda: s.score_batch(texts))):
        fn()
        t0 = time.perf_counter()
        fn()
        print(f"   {name:15s} {(time.perf_counter() - t0) / len(texts) * 1000:6.2f} ms per text")
    print("vs the shipped ONNX bundle on CPU:", verify_against_onnx(s, bundle, texts))
