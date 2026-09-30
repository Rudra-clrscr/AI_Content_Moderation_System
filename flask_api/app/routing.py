"""Threshold routing: risk score -> allow / revise / reject.

There is no human-review step. The middle band asks the *author* to revise:
the response highlights what to change (see app/feedback.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    ALLOW = "allow"
    REVISE = "revise"   # not published yet: the author is shown what to fix and can resubmit
    REJECT = "reject"

    @property
    def severity(self) -> int:
        return _SEVERITY[self]


_SEVERITY = {Decision.ALLOW: 0, Decision.REVISE: 1, Decision.REJECT: 2}


def most_severe(*decisions: Decision) -> Decision:
    return max(decisions, key=lambda d: d.severity)


@dataclass(frozen=True)
class Thresholds:
    """risk <= allow_max -> ALLOW; risk >= reject_min -> REJECT; otherwise REVISE.

    Equivalently, on a safety scale (1 - risk): safety >= 1 - allow_max is safe,
    safety <= 1 - reject_min is rejected.

    With allow_max == reject_min there is no revise band: a single boundary where
    risk <= boundary is allowed and risk > boundary is rejected (the allow side is
    inclusive). bonc-v4 uses 0.5: its risk is a calibrated P(unsafe).
    """

    allow_max: float
    reject_min: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.allow_max <= self.reject_min <= 1.0:
            raise ValueError(
                f"need 0 <= allow_max <= reject_min <= 1, got "
                f"allow_max={self.allow_max}, reject_min={self.reject_min}"
            )

    def as_dict(self) -> dict[str, float]:
        return {"allow_max": self.allow_max, "reject_min": self.reject_min}


def route(score: float, thresholds: Thresholds, *, floor: Decision = Decision.ALLOW) -> Decision:
    """Map a risk score to a decision. `floor` lets a gate "revise" rule force at least REVISE."""
    if score <= thresholds.allow_max:       # checked first: with one boundary, the boundary itself is allowed
        decision = Decision.ALLOW
    elif score >= thresholds.reject_min:
        decision = Decision.REJECT
    else:
        decision = Decision.REVISE
    return most_severe(decision, floor)
