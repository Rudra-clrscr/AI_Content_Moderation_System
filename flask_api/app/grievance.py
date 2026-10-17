"""The public-surface grievance check: a second model, asked a different question.

The main model (app/model.py) answers "is this text harmful anywhere". It never sees the
surface, and it must not: a member chasing a damaged consignment through a proposal is the
platform working, and an earlier round that refused that language made the portal useless for
disputes. So the main model's answer for "nobody has replied about my refund in two weeks" is
and stays ALLOW.

This model answers "would this be a customer-support complaint or an emotional outcry if it
were PUBLISHED TO THE NETWORK". The same sentence, a different question, and the two can only
coexist because this one is consulted on the public surfaces only.

WHY THIS IS NOT ANOTHER GATE RULE. It was, for a while: config/gate_patterns.yaml carries
`complaint.public_grievance` with fifteen patterns, and it caught the sentences someone had
thought of. Two submissions in two days went through it - one about a company's delivery
system, one about a driver leaving a parcel in the rain - because a pattern list only holds
the shapes already seen. The rule stays as a floor; this is the part that generalises.

UNKNOWN SURFACES ARE NOT CHECKED, and that is the opposite of what gate.py does with them.
A gate rule is narrow and auditable, so running it on a surface nobody named costs little. A
classifier refuses a whole genre, so running it where nobody chose it would refuse dispute
traffic on a surface that was never reviewed - exactly the regression this design exists to
prevent. A new content_type therefore gets the main model only, until someone adds it here.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_SURFACES = ("article", "post", "advertisement")


class GrievanceCheck:
    """Loaded bundle, threshold and surfaces. `enabled` is False until a bundle loads.

    A missing or unusable bundle disables the check and logs why. The service must keep
    working - with the gate rule as the floor it had before this existed - rather than fail
    to start because a model file was not copied.
    """

    def __init__(self, enabled: bool = False, threshold: float = 0.5,
                 surfaces: tuple[str, ...] = DEFAULT_SURFACES, version: str = "none",
                 scorer=None, weights=None, intercept: float = 0.0, meta: dict | None = None):
        self.enabled = enabled
        self.threshold = threshold
        self.surfaces = frozenset(surfaces)
        self.version = version
        self.scorer = scorer          # an app.model.Scorer, when the bundle is a transformer
        self.weights = weights        # hashed-ngram weights, when it is the linear bundle
        self.intercept = intercept
        self.meta = meta or {}
        self._np = None
        if weights is not None:
            import numpy as np
            self._np = np

    # ---- loading ---------------------------------------------------------------------

    @classmethod
    def load(cls, model_dir: str | Path, enabled: bool = True,
             threshold: float | None = None, surfaces: tuple[str, ...] | None = None,
             **model_kwargs) -> "GrievanceCheck":
        """Load whichever bundle is in `model_dir`.

        Two shapes are accepted on purpose. The linear one (grievance.npz) is 1 MB and costs
        ~0.1 ms; the transformer one (an ONNX bundle, exactly like models/v6) is larger and
        reads sentences the linear model cannot. Which one is present is an operational
        choice, so serving should not need a code change to switch.
        """
        if not enabled:
            return cls()
        model_dir = Path(model_dir)
        try:
            meta = json.loads((model_dir / "grievance_meta.json").read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("grievance check disabled: no readable bundle in %s (%s: %s). The "
                        "gate rule complaint.public_grievance is then the only cover. The "
                        "DeBERTa bundle is gitignored (371 MB); either rebuild it - see "
                        ".gitignore for the four commands - or point grievance.dir at "
                        "models/grievance-linear-v1, which is tracked.",
                        model_dir, type(exc).__name__, exc)
            return cls()

        chosen = float(meta["threshold"] if threshold is None else threshold)
        surf = tuple(surfaces or meta.get("surfaces") or DEFAULT_SURFACES)
        try:
            if (model_dir / "model.onnx").exists() or meta.get("backend") == "onnx":
                from app.model import ModelRegistry
                reg = ModelRegistry()
                reg.load("onnx", model_dir, **model_kwargs)
                check = cls(True, chosen, surf, meta.get("version", "grievance"),
                            scorer=reg, meta=meta)
            else:
                import numpy as np
                from app.triage import FEATURE_BITS, FEATURE_VERSION
                if meta.get("feature_version") != FEATURE_VERSION:
                    raise ValueError(f"bundle was built with feature_version "
                                     f"{meta.get('feature_version')}, this code is {FEATURE_VERSION}; retrain it")
                if meta.get("feature_bits") != FEATURE_BITS:
                    raise ValueError(f"bundle has {meta.get('feature_bits')} feature bits, "
                                     f"this code has {FEATURE_BITS}")
                data = np.load(model_dir / "grievance.npz")
                check = cls(True, chosen, surf, meta.get("version", "grievance"),
                            weights=data["weights"].astype(np.float32),
                            intercept=float(data["intercept"]), meta=meta)
        except Exception as exc:
            log.warning("grievance check disabled: %s: %s (the gate rule is the only cover "
                        "for public complaints)", type(exc).__name__, exc)
            return cls()

        hw = (meta.get("metrics") or {}).get("eval_grievance_handwritten_2") or {}
        log.info("grievance check %s loaded (threshold %.2f, surfaces %s, hand-written recall %s)",
                 check.version, check.threshold, ", ".join(sorted(check.surfaces)),
                 f"{hw['recall']:.1%}" if "recall" in hw else "unmeasured")
        return check

    # ---- use -------------------------------------------------------------------------

    def applies_to(self, content_type: str | None) -> bool:
        """Only the surfaces named. A surface this does not list - including one nobody has
        heard of - is not checked. See the module docstring for why that differs from the
        gate's answer to the same question."""
        return self.enabled and content_type is not None and content_type in self.surfaces

    def probability(self, text: str) -> float:
        """P(this is a complaint or an outcry published where it does not belong)."""
        if self.scorer is not None:
            return float(self.scorer.score(text).risk_score)
        idx, val = _hash(text)
        if not idx:
            return 0.0
        np = self._np
        z = float(np.dot(self.weights[np.asarray(idx, dtype=np.int64)],
                         np.asarray(val, dtype=np.float32))) + self.intercept
        return 1.0 / (1.0 + np.exp(-z)) if z > -30 else 0.0

    def check(self, text: str, content_type: str | None) -> tuple[bool, float | None]:
        """(refuse this on this surface, score). Score is None when the check did not run,
        so a caller can tell "looked and found nothing" from "never looked" - the same
        distinction `visual_content_checked` draws for pictures."""
        if not self.applies_to(content_type):
            return False, None
        score = self.probability(text)
        return score >= self.threshold, score


def _hash(text: str):
    from app.triage import hash_features
    return hash_features(text)
