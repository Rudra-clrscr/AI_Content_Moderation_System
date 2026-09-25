"""Threshold routing: risk score -> allow / review / reject."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    ALLOW = "allow"
    REVIEW = "review"
    REJECT = "reject"

    @property
    def severity(self) -> int:
        return _SEVERITY[self]


_SEVERITY = {Decision.ALLOW: 0, Decision.REVIEW: 1, Decision.REJECT: 2}


@dataclass(frozen=True)
class Thresholds:
    """risk <= allow_max -> ALLOW; risk >= reject_min -> REJECT; otherwise REVIEW.

    Equivalently, on a safety scale (1 - risk): safety >= 1 - allow_max is safe,
    safety <= 1 - reject_min is rejected.
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
    """Map a risk score to a decision. `floor` lets a gate "flag" force at least REVIEW."""
    if score >= thresholds.reject_min:
        decision = Decision.REJECT
    elif score <= thresholds.allow_max:
        decision = Decision.ALLOW
    else:
        decision = Decision.REVIEW
    return max(decision, floor, key=lambda d: d.severity)
