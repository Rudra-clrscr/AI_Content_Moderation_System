"""Layer 1.5: a linear pre-filter that keeps most spans away from the DeBERTa model.

The sentence scan scores every sentence of a post on its own, and long sentences in word
windows too (app/targeted.py). For a clean 40-sentence article that is 40+ model calls,
and nearly all of them come back "safe". This filter is a logistic regression over hashed
word and character n-grams: ~0.1 ms per span, no tokenizer and no ONNX session. Spans it
is confident about never reach the model.

It is deliberately one-sided. The threshold is chosen on held-out spans so that it clears
only text the DeBERTa model would also have allowed; `training/train_triage.py` picks it
and reports the misses. Anything the filter is unsure about goes to the model exactly as
before, so it can only cost recall if the threshold is set too high, never by design. It
also never sees the whole-text score, the targeted check or the word-by-word scan: those
stay on the full model.

Character n-grams are hashed as well as words, so obfuscated spellings ("f1rst c0py")
still resemble the words they imitate, and a span whose de-obfuscated reading differs is
checked twice (see Pipeline.moderate).

The feature hashing here is the contract between training and serving: change it and the
saved weights stop meaning anything, so `triage_meta.json` records FEATURE_VERSION and the
loader refuses a bundle that was built with a different one.
"""
from __future__ import annotations

import json
import logging
import re
import zlib
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)

FEATURE_VERSION = 1      # bump whenever hash_features changes; old weight files are then rejected
FEATURE_BITS = 18        # 262,144 buckets -> 1 MB of float32 weights
_MASK = (1 << FEATURE_BITS) - 1
_SPACES = re.compile(r"\s+")
CHAR_NGRAMS = (3, 4, 5)


def _bucket(token: str) -> int:
    return zlib.crc32(token.encode("utf-8")) & _MASK


def hash_features(text: str) -> tuple[list[int], list[float]]:
    """(bucket, weight) pairs for one span: word 1-2 grams and character 3-5 grams,
    counted and L2-normalised so long and short spans are on the same scale."""
    norm = _SPACES.sub(" ", text.lower()).strip()
    counts: Counter[int] = Counter()
    words = norm.split(" ") if norm else []
    previous = None
    for word in words:
        counts[_bucket("w:" + word)] += 1
        if previous is not None:
            counts[_bucket("w:" + previous + " " + word)] += 1
        previous = word
    padded = " " + norm + " "
    for n in CHAR_NGRAMS:
        for i in range(len(padded) - n + 1):
            counts[_bucket("c:" + padded[i:i + n])] += 1
    if not counts:
        return [], []
    values = list(counts.values())
    scale = sum(v * v for v in values) ** 0.5
    return list(counts.keys()), [v / scale for v in values]


class TriageFilter:
    """Loaded weights plus the clearing threshold. `enabled` is False until a bundle loads."""

    def __init__(self, enabled: bool = False, threshold: float = 0.0, version: str = "none",
                 weights=None, intercept: float = 0.0, meta: dict | None = None):
        self.enabled = enabled
        self.threshold = threshold
        self.version = version
        self.weights = weights
        self.intercept = intercept
        self.meta = meta or {}
        self._np = None
        if weights is not None:
            import numpy as np
            self._np = np

    @classmethod
    def load(cls, model_dir: str | Path, threshold: float | None = None) -> "TriageFilter":
        """Load a bundle. A missing or unusable bundle disables the filter and logs why:
        the service must keep working (just slower) rather than fail to start."""
        model_dir = Path(model_dir)
        try:
            import numpy as np
            meta = json.loads((model_dir / "triage_meta.json").read_text(encoding="utf-8"))
            if meta.get("feature_version") != FEATURE_VERSION:
                raise ValueError(f"triage bundle was built with feature_version "
                                 f"{meta.get('feature_version')}, this code is {FEATURE_VERSION}; retrain it")
            if meta.get("feature_bits") != FEATURE_BITS:
                raise ValueError(f"triage bundle has {meta.get('feature_bits')} feature bits, this code has {FEATURE_BITS}")
            data = np.load(model_dir / "triage.npz")
            weights = data["weights"].astype(np.float32)
            if weights.shape != (1 << FEATURE_BITS,):
                raise ValueError(f"triage weights have shape {weights.shape}, expected {(1 << FEATURE_BITS,)}")
        except Exception as exc:
            log.warning("triage pre-filter disabled: %s: %s (every span goes to the model)",
                        type(exc).__name__, exc)
            return cls()
        chosen = meta["threshold"] if threshold is None else threshold
        f = cls(True, float(chosen), meta.get("version", "triage"), weights, float(data["intercept"]), meta)
        log.info("triage pre-filter %s loaded (threshold %.4f, expected skip rate %s)",
                 f.version, f.threshold, meta.get("skip_rate"))
        return f

    def probability(self, text: str) -> float:
        """P(this span is worth sending to the model), from the linear model."""
        idx, val = hash_features(text)
        if not idx:
            return 0.0
        np = self._np
        z = float(np.dot(self.weights[np.asarray(idx, dtype=np.int64)],
                         np.asarray(val, dtype=np.float32))) + self.intercept
        return 1.0 / (1.0 + np.exp(-z)) if z > -30 else 0.0

    def clears(self, *texts: str) -> tuple[bool, float]:
        """(may skip the model, score). A span is cleared only if every reading of it
        — the author's text and its de-obfuscated form — is below the threshold."""
        score = max(self.probability(t) for t in texts)
        return score < self.threshold, score
