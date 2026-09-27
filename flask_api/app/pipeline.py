"""Gate -> model -> routing -> author feedback. Shared by the sync endpoint and the
async Celery worker, so both paths produce an identical result payload
(contracts/moderation_result.md)."""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from app.feedback import Feedback, Issue
from app.gate import Gate, GateResult
from app.model import ModelRegistry, ScoreResult
from app.routing import Decision, Thresholds, most_severe, route
from app.targeted import SentenceScan, TargetedAbuse, sentence_spans

log = logging.getLogger(__name__)

SCHEMA_VERSION = "1.3"  # 1.1: targeted_segments; 1.2: "revise" + feedback; 1.3: sentence_scores, decided_by "sentence"


class ContentType(str, Enum):
    BUSINESS_PROFILE = "business_profile"
    PRODUCT_LISTING = "product_listing"
    POST = "post"
    ADVERTISEMENT = "advertisement"


class Stage(str, Enum):
    GATE = "gate"          # decided by a Layer 1 rule (block, or a "revise" rule the model would have allowed)
    MODEL = "model"        # decided by the whole-text model score
    TARGETED = "targeted"  # decided by a sentence aimed at someone, scored separately
    SENTENCE = "sentence"  # one sentence scored at reject level on its own (e.g. a scam tacked onto a listing)


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


class _Scorer:
    """Per-request memo so no span of text is scored twice; tracks total inference time."""

    def __init__(self, models: ModelRegistry):
        self.models = models
        self.cache: dict[str, ScoreResult] = {}
        self.inference_ms = 0.0

    def __call__(self, text: str) -> ScoreResult:
        if text not in self.cache:
            result = self.models.score(text)
            self.inference_ms += result.inference_ms
            self.cache[text] = result
        return self.cache[text]


class Pipeline:
    def __init__(self, gate: Gate, models: ModelRegistry, thresholds: Thresholds, latency_budget_ms: float = 30.0,
                 targeted: TargetedAbuse | None = None, feedback: Feedback | None = None,
                 sentence_scan: SentenceScan | None = None):
        self.gate = gate
        self.models = models
        self.thresholds = thresholds
        self.latency_budget_ms = latency_budget_ms
        self.targeted = targeted or TargetedAbuse(enabled=False)
        self.feedback = feedback or Feedback()
        self.sentence_scan = sentence_scan or SentenceScan(enabled=False)

    def run_gate(self, req: ModerationRequest) -> tuple[GateResult, float]:
        t0 = time.perf_counter()
        result = self.gate.check(req.content)
        return result, (time.perf_counter() - t0) * 1000

    def gate_only_result(self, req: ModerationRequest, gate: GateResult, gate_ms: float) -> dict:
        """Final result for content blocked by Layer 1 (no model call)."""
        fb = self.feedback.build(Decision.REJECT, gate, [])
        return self._build(req, Decision.REJECT, Stage.GATE, gate, None, gate_ms, None, [], [], None, fb)

    def moderate(self, req: ModerationRequest, gate: GateResult | None = None, gate_ms: float = 0.0) -> dict:
        t0 = time.perf_counter()
        if gate is None:
            gate, gate_ms = self.run_gate(req)
        if gate.blocked:
            return self.gate_only_result(req, gate, gate_ms)

        score = _Scorer(self.models)
        scored = score(req.content)
        if scored.inference_ms > self.latency_budget_ms:
            log.warning("inference %.1f ms over %.0f ms budget (request %s)",
                        scored.inference_ms, self.latency_budget_ms, req.request_id)

        model_decision = route(scored.risk_score, self.thresholds)
        decision = most_severe(model_decision, Decision.REVISE if gate.needs_revision else Decision.ALLOW)
        stage = Stage.MODEL if decision is model_decision else Stage.GATE
        model_issues: list[Issue] = []

        # Targeted check: a sentence aimed at someone can escalate to revise or reject.
        segments: list[dict] = []
        if self.targeted.enabled and decision is not Decision.REJECT:
            for start, end in self.targeted.segments(req.content):
                seg = score(req.content[start:end])
                segments.append({"start": start, "end": end,
                                 "risk_score": round(seg.risk_score, 6), "predicted_label": seg.label})
                seg_decision = route(seg.risk_score, self.thresholds)
                if seg_decision is Decision.ALLOW:
                    continue
                issue = Issue(start, end, "model", self.feedback.model_targeted, risk_score=seg.risk_score)
                if seg_decision is Decision.REJECT:
                    decision, stage, model_issues = Decision.REJECT, Stage.TARGETED, [issue]
                    break
                model_issues.append(issue)
                if decision is Decision.ALLOW:
                    decision, stage = Decision.REVISE, Stage.TARGETED

        # Sentence scan: any single sentence at reject level rejects the post.
        sentence_scores: list[dict] = []
        spans = sentence_spans(req.content)
        if self.sentence_scan.enabled and decision is not Decision.REJECT and len(spans) >= 2:
            for start, end in spans[: self.sentence_scan.max_sentences]:
                risk = score(req.content[start:end]).risk_score
                sentence_scores.append({"start": start, "end": end, "risk_score": round(risk, 6)})
            worst = max(sentence_scores, key=lambda x: x["risk_score"])
            if worst["risk_score"] >= self.thresholds.reject_min:
                decision, stage = Decision.REJECT, Stage.SENTENCE
                model_issues = [Issue(worst["start"], worst["end"], "model", self.feedback.model_sentence,
                                      risk_score=worst["risk_score"])]

        # Show the author which sentences the model objects to.
        if stage is Stage.MODEL and (decision is Decision.REVISE
                                     or (decision is Decision.REJECT and self.feedback.highlight_on_reject)):
            # Skip sentences the targeted check already highlighted (same text, more specific message).
            model_issues += [i for i in self._sentence_issues(req.content, scored, score)
                             if not any(i.start < t.end and t.start < i.end for t in model_issues)]

        fb = self.feedback.build(decision, gate, model_issues)
        total = gate_ms + (time.perf_counter() - t0) * 1000
        return self._build(req, decision, stage, gate, scored, gate_ms, total, segments, sentence_scores,
                           score.inference_ms, fb)

    def _sentence_issues(self, content: str, whole: ScoreResult, score: _Scorer) -> list[Issue]:
        spans = sentence_spans(content)
        if len(spans) <= 1:
            s, e = spans[0] if spans else (0, len(content))
            return [Issue(s, e, "model", self.feedback.model_sentence, risk_score=whole.risk_score)]
        scored = [(s, e, score(content[s:e]).risk_score) for s, e in spans[: self.feedback.max_highlight_sentences]]
        flagged = [x for x in scored if x[2] > self.thresholds.allow_max]
        # If no single sentence stands out, the combination is the problem: point at the riskiest one.
        chosen = flagged or [max(scored, key=lambda x: x[2])]
        return [Issue(s, e, "model", self.feedback.model_sentence, risk_score=r) for s, e, r in chosen]

    def _build(self, req, decision, stage, gate, scored, gate_ms, total_ms, segments, sentence_scores,
               inference_ms, feedback) -> dict:
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
            # Offsets into `content` (text itself isn't repeated, so logs stay content-free).
            "targeted_segments": segments,
            # Every sentence scored on its own (offsets + risk), when the post has 2+ sentences.
            "sentence_scores": sentence_scores,
            # Author-facing: what to fix and where (null when allowed). Offsets into `content`.
            "feedback": feedback,
            "gate_version": self.gate.version,
            "model_version": None if scored is None else scored.model_version,
            "thresholds": self.thresholds.as_dict(),
            "latency_ms": {
                "gate": round(gate_ms, 3),
                "inference": None if inference_ms is None else round(inference_ms, 3),
                "total": round(total_ms if total_ms is not None else gate_ms, 3),
            },
            "decided_at": datetime.now(timezone.utc).isoformat(),
        }
