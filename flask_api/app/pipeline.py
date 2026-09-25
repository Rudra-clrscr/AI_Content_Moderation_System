"""Gate -> model -> routing. Shared by the sync endpoint and the async Celery worker,
so both paths produce an identical result payload (contracts/moderation_result.md)."""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from app.gate import Gate, GateResult
from app.model import ModelRegistry
from app.routing import Decision, Thresholds, route

log = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"


class ContentType(str, Enum):
    BUSINESS_PROFILE = "business_profile"
    PRODUCT_LISTING = "product_listing"
    POST = "post"
    ADVERTISEMENT = "advertisement"


class Stage(str, Enum):
    GATE = "gate"     # decided by Layer 1, model skipped
    MODEL = "model"   # decided by model score (possibly floored by a gate flag)


@dataclass(frozen=True)
class ModerationRequest:
    content: str
    content_type: ContentType
    content_id: str | None = None
    request_id: str = ""

    def __post_init__(self) -> None:
        if not self.request_id:
            object.__setattr__(self, "request_id", str(uuid.uuid4()))

    def as_dict(self) -> dict:
        return {"content": self.content, "content_type": self.content_type.value,
                "content_id": self.content_id, "request_id": self.request_id}

    @classmethod
    def from_dict(cls, d: dict) -> "ModerationRequest":
        return cls(d["content"], ContentType(d["content_type"]), d.get("content_id"), d["request_id"])


class Pipeline:
    def __init__(self, gate: Gate, models: ModelRegistry, thresholds: Thresholds, latency_budget_ms: float = 30.0):
        self.gate = gate
        self.models = models
        self.thresholds = thresholds
        self.latency_budget_ms = latency_budget_ms

    def run_gate(self, req: ModerationRequest) -> tuple[GateResult, float]:
        t0 = time.perf_counter()
        result = self.gate.check(req.content)
        return result, (time.perf_counter() - t0) * 1000

    def gate_only_result(self, req: ModerationRequest, gate: GateResult, gate_ms: float) -> dict:
        """Final result for content blocked by Layer 1 (no model call)."""
        return self._build(req, Decision.REJECT, Stage.GATE, gate, None, gate_ms, None)

    def moderate(self, req: ModerationRequest, gate: GateResult | None = None, gate_ms: float = 0.0) -> dict:
        t0 = time.perf_counter()
        if gate is None:
            gate, gate_ms = self.run_gate(req)
        if gate.blocked:
            return self.gate_only_result(req, gate, gate_ms)

        scored = self.models.score(req.content)
        if scored.inference_ms > self.latency_budget_ms:
            log.warning("inference %.1f ms over %.0f ms budget (request %s)",
                        scored.inference_ms, self.latency_budget_ms, req.request_id)
        floor = Decision.REVIEW if gate.flagged else Decision.ALLOW
        decision = route(scored.risk_score, self.thresholds, floor=floor)
        total = gate_ms + (time.perf_counter() - t0) * 1000
        return self._build(req, decision, Stage.MODEL, gate, scored, gate_ms, total)

    def _build(self, req, decision, stage, gate, scored, gate_ms, total_ms) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "request_id": req.request_id,
            "status": "completed",
            "content_id": req.content_id,
            "content_type": req.content_type.value,
            "content": req.content,
            "content_sha256": hashlib.sha256(req.content.encode("utf-8")).hexdigest(),
            "decision": decision.value,
            "decided_by": stage.value,
            "risk_score": None if scored is None else round(scored.risk_score, 6),
            "predicted_label": None if scored is None else scored.label,
            "label_scores": None if scored is None else {k: round(v, 6) for k, v in scored.label_scores.items()},
            "gate_matches": [m.as_dict() for m in gate.matches],
            "gate_version": self.gate.version,
            "model_version": None if scored is None else scored.model_version,
            "thresholds": self.thresholds.as_dict(),
            "latency_ms": {
                "gate": round(gate_ms, 3),
                "inference": None if scored is None else round(scored.inference_ms, 3),
                "total": round(total_ms if total_ms is not None else gate_ms, 3),
            },
            "decided_at": datetime.now(timezone.utc).isoformat(),
        }
