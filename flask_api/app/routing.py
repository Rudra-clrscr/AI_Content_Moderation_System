"""Threshold routing: risk score -> allow / reject.

There is no human-review outcome: every item is decided automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    ALLOW = "allow"
    REJECT = "reject"


@dataclass(frozen=True)
class Thresholds:
    """risk >= reject_min -> REJECT; otherwise ALLOW.

    Equivalently, on the team's safety scale (1 - risk): safety <= 1 - reject_min is rejected.
    """

    reject_min: float

    def __post_init__(self) -> None:
        if not 0.0 < self.reject_min <= 1.0:
            raise ValueError(f"need 0 < reject_min <= 1, got reject_min={self.reject_min}")

    def as_dict(self) -> dict[str, float]:
        return {"reject_min": self.reject_min}


def route(score: float, thresholds: Thresholds) -> Decision:
    return Decision.REJECT if score >= thresholds.reject_min else Decision.ALLOW
