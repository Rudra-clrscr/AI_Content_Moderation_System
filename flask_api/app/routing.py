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
    """score < allow_below -> ALLOW; score >= reject_at -> REJECT; otherwise REVIEW."""

    allow_below: float
    reject_at: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.allow_below <= self.reject_at <= 1.0:
            raise ValueError(
                f"need 0 <= allow_below <= reject_at <= 1, got "
                f"allow_below={self.allow_below}, reject_at={self.reject_at}"
            )

    def as_dict(self) -> dict[str, float]:
        return {"allow_below": self.allow_below, "reject_at": self.reject_at}


def route(score: float, thresholds: Thresholds, *, floor: Decision = Decision.ALLOW) -> Decision:
    """Map a risk score to a decision. `floor` lets a gate "flag" force at least REVIEW."""
    if score >= thresholds.reject_at:
        decision = Decision.REJECT
    elif score >= thresholds.allow_below:
        decision = Decision.REVIEW
    else:
        decision = Decision.ALLOW
    return max(decision, floor, key=lambda d: d.severity)
