"""Gate -> model -> routing -> author feedback. Shared by the sync endpoint and the
async Celery worker, so both paths produce an identical result payload
(contracts/moderation_result.md)."""
from __future__ import annotations

import hashlib
import logging
import math
import time
from contextlib import contextmanager
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from app.feedback import Feedback, Issue
from app.gate import Gate, GateResult
from app.model import ModelRegistry, ScoreResult
from app.routing import Decision, Thresholds, most_severe, route
from app.targeted import (SentenceScan, TargetedAbuse, WordScan, deobfuscate, drop_word, scan_spans, word_spans,
                          word_windows)
from app.triage import TriageFilter

log = logging.getLogger(__name__)

SCHEMA_VERSION = "1.5"  # 1.1: targeted_segments; 1.2: "revise" + feedback; 1.3: sentence_scores, decided_by "sentence"
                        # 1.4: word windows in sentence_scores ("kind"), word_scores, issues[].words
                        # 1.5: sentence_scores[].by ("prefilter" = scored by the linear pre-filter, not the model)


class ContentType(str, Enum):
    """The surfaces a member can publish from. One per tab in the dashboard, so a decision can
    be traced back to where it was made and the author is addressed in the right words
    ("Your proposal needs a few changes"). The type does not change how the model scores text;
    it selects the wording in config/feedback_messages.yaml and is stored with the decision.
    """

    BUSINESS_PROFILE = "business_profile"     # Add Business
    PRODUCT_LISTING = "product_listing"
    POST = "post"
    ADVERTISEMENT = "advertisement"
    ARTICLE = "article"                       # Articles
    VIDEO = "video"                           # Videos — the title/description, not the footage
    REQUEST = "request"                       # Requests
    PROPOSAL = "proposal"                     # Proposals
    BUSINESS_PROPOSAL = "business_proposal"   # Business Proposals


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


def _span_scores(content: str, view: str, spans: list[tuple[int, int]], score: "_Scorer") -> list[ScoreResult]:
    """Score each span of `content`; where the de-obfuscated `view` reads differently, score that
    too and keep the riskier result. All texts go to the model in one batch."""
    texts = [content[s:e] for s, e in spans]
    alt = [(i, view[s:e]) for i, (s, e) in enumerate(spans) if view[s:e] != texts[i]]
    results = score.many(texts + [t for _, t in alt])
    best = results[: len(texts)]
    for (i, _), r in zip(alt, results[len(texts):]):
        if r.risk_score > best[i].risk_score:
            best[i] = r
    return best


GREEDY_CANDIDATES = 12   # words tried per greedy round: the ones whose single removal lowered the logit most


class _Stages:
    """Wall-clock per phase of a request, reported as latency_ms.stages.

    Cheap (a perf_counter pair per phase) and always on: without it "the request took 400 ms"
    says nothing about whether to optimise the scan, the word analysis or the model itself.
    """

    def __init__(self) -> None:
        self.ms: dict[str, float] = {}

    @contextmanager
    def time(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.ms[name] = self.ms.get(name, 0.0) + (time.perf_counter() - start) * 1000

    def as_dict(self) -> dict:
        return {k: round(v, 3) for k, v in self.ms.items() if v >= 0.0005}


def _logit(p: float) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


def _without(text: str, words: list[tuple[int, int, str]], drop: set[int]) -> str:
    """`text` with the words at indices `drop` removed (remaining words joined by single spaces)."""
    return " ".join(text[a:b] for j, (a, b, _) in enumerate(words) if j not in drop)


def _non_overlapping(entries: list[dict]) -> list[dict]:
    """Keep sentences over windows, then the riskiest, dropping spans that overlap a kept one."""
    kept: list[dict] = []
    for x in sorted(entries, key=lambda x: ("kind" in x, -x["risk_score"])):
        if not any(x["start"] < k["end"] and k["start"] < x["end"] for k in kept):
            kept.append(x)
    return sorted(kept, key=lambda x: x["start"])


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

    def many(self, texts: list[str]) -> list[ScoreResult]:
        """Score several texts at once (one batched call when the model allows it)."""
        todo = list(dict.fromkeys(t for t in texts if t not in self.cache))
        if todo:
            for text, result in zip(todo, self.models.score_many(todo)):
                self.inference_ms += result.inference_ms
                self.cache[text] = result
        return [self.cache[t] for t in texts]


class Pipeline:
    def __init__(self, gate: Gate, models: ModelRegistry, thresholds: Thresholds, latency_budget_ms: float = 30.0,
                 targeted: TargetedAbuse | None = None, feedback: Feedback | None = None,
                 sentence_scan: SentenceScan | None = None, word_scan: WordScan | None = None,
                 triage: TriageFilter | None = None):
        self.gate = gate
        self.models = models
        self.thresholds = thresholds
        self.latency_budget_ms = latency_budget_ms
        self.targeted = targeted or TargetedAbuse(enabled=False)
        self.feedback = feedback or Feedback()
        self.sentence_scan = sentence_scan or SentenceScan(enabled=False)
        self.word_scan = word_scan or WordScan(enabled=False)
        self.triage = triage or TriageFilter()

    def run_gate(self, req: ModerationRequest) -> tuple[GateResult, float]:
        t0 = time.perf_counter()
        result = self.gate.check(req.content)
        return result, (time.perf_counter() - t0) * 1000

    def gate_only_result(self, req: ModerationRequest, gate: GateResult, gate_ms: float) -> dict:
        """Final result for content blocked by Layer 1 (no model call)."""
        fb = self.feedback.build(Decision.REJECT, gate, [], req.content_type.value)
        return self._build(req, Decision.REJECT, Stage.GATE, gate, None, gate_ms, None, [], [], None, fb, [])

    def moderate(self, req: ModerationRequest, gate: GateResult | None = None, gate_ms: float = 0.0) -> dict:
        t0 = time.perf_counter()
        if gate is None:
            gate, gate_ms = self.run_gate(req)
        if gate.blocked:
            return self.gate_only_result(req, gate, gate_ms)

        stages = _Stages()
        score = _Scorer(self.models)
        # Leetspeak restored word by word ("F1rst c0py" -> "First copy"), same length, so every span
        # is scored on both texts and the riskier reading counts. Identical text when there's none.
        view = deobfuscate(req.content) if self.word_scan.enabled and self.word_scan.deobfuscate else req.content
        with stages.time("whole"):
            scored = score(req.content)
            if view != req.content:
                scored = max(scored, score(view), key=lambda r: r.risk_score)
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
            seg_spans = self.targeted.segments(req.content)
            with stages.time("targeted"):
                seg_results = _span_scores(req.content, view, seg_spans, score)
            for (start, end), seg in zip(seg_spans, seg_results):
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

        # Sentence scan: every sentence (and word windows over long ones) scored on its own.
        # Any one at reject level rejects the post.
        sentence_scores: list[dict] = []
        units = self._scan_units(req.content)
        # Already rejected by the whole-text score: still scan when the author will be shown what to
        # rewrite, so the right sentences are highlighted (not just the first few).
        whole_rejected = decision is Decision.REJECT and stage is Stage.MODEL
        scan = decision is not Decision.REJECT or (whole_rejected and self.feedback.highlight_on_reject)
        highlighted_by_scan = False
        if self.sentence_scan.enabled and scan and units:
            units = units[: self.sentence_scan.max_sentences]
            # Layer 1.5: the linear pre-filter clears obviously-safe spans without a model call.
            # Its scores are recorded as "by": "prefilter" and can never escalate: the threshold
            # is far below allow_max, and the escalation lists below skip them explicitly.
            with stages.time("triage"):
                units, sentence_scores = self._triage(req.content, view, units)
            with stages.time("scan"):
                results = _span_scores(req.content, view, [(s, e) for s, e, _ in units], score)
            for (start, end, kind), r in zip(units, results):
                entry = {"start": start, "end": end, "risk_score": round(r.risk_score, 6)}
                if kind != "sentence":
                    entry["kind"] = kind
                sentence_scores.append(entry)
            sentence_scores.sort(key=lambda x: (x["start"], x["end"]))
            by_model = [x for x in sentence_scores if x.get("by") != "prefilter"]
            rejecting = [x for x in by_model if route(x["risk_score"], self.thresholds) is Decision.REJECT]
            if rejecting:
                if not whole_rejected:
                    decision, stage = Decision.REJECT, Stage.SENTENCE
                model_issues = [Issue(x["start"], x["end"], "model", self.feedback.model_sentence,
                                      risk_score=x["risk_score"]) for x in _non_overlapping(rejecting)]
                highlighted_by_scan = True
            elif self.sentence_scan.revise_on_middle and decision is Decision.ALLOW:
                risky = [x for x in by_model if x["risk_score"] > self.thresholds.allow_max]
                if risky:
                    decision, stage = Decision.REVISE, Stage.SENTENCE
                    model_issues += [Issue(x["start"], x["end"], "model", self.feedback.model_sentence,
                                           risk_score=x["risk_score"]) for x in _non_overlapping(risky)]

        # Show the author which sentences the model objects to (unless the scan already found them).
        if stage is Stage.MODEL and not highlighted_by_scan and (
                decision is Decision.REVISE or (decision is Decision.REJECT and self.feedback.highlight_on_reject)):
            # Skip sentences the targeted check already highlighted (same text, more specific message).
            with stages.time("highlight"):
                found = self._sentence_issues(req.content, view, scored, score)
            model_issues += [i for i in found
                             if not any(i.start < t.end and t.start < i.end for t in model_issues)]

        # Word by word: which words in each flagged span drive its risk (on the de-obfuscated
        # text, so "c0py" is analysed as "copy"; offsets are the same).
        word_scores: list[dict] = []
        if self.word_scan.enabled and decision is not Decision.ALLOW and model_issues:
            with stages.time("words"):
                model_issues = self._attach_words(view, model_issues, score, word_scores)

        fb = self.feedback.build(decision, gate, model_issues, req.content_type.value)
        total = gate_ms + (time.perf_counter() - t0) * 1000
        return self._build(req, decision, stage, gate, scored, gate_ms, total, segments, sentence_scores,
                           score.inference_ms, fb, word_scores, stages)

    def _triage(self, content: str, view: str, units: list[tuple[int, int, str]]
                ) -> tuple[list[tuple[int, int, str]], list[dict]]:
        """Split the scan's spans into (still needs the model, already cleared by the pre-filter).

        A span is only cleared when every reading of it is below the threshold, so leetspeak
        can't walk past the filter. The span is de-obfuscated here rather than relying on
        `view`: the pipeline only builds that when the word scan asks for it, and turning the
        word scan off must not quietly weaken the filter.
        """
        if not self.triage.enabled:
            return units, []
        keep, cleared = [], []
        for start, end, kind in units:
            text = content[start:end]
            readings = {text, view[start:end], deobfuscate(text)}
            ok, score = self.triage.clears(*readings)
            if not ok:
                keep.append((start, end, kind))
                continue
            entry = {"start": start, "end": end, "risk_score": round(score, 6), "by": "prefilter"}
            if kind != "sentence":
                entry["kind"] = kind
            cleared.append(entry)
        return keep, cleared

    def _scan_units(self, content: str) -> list[tuple[int, int, str]]:
        """Spans to score on their own: every sentence (when there are 2+; fragments under 3 words
        merged into a neighbour), plus overlapping word windows over sentences too long to read
        undiluted (when the word scan is on)."""
        spans = scan_spans(content)
        units = [(s, e, "sentence") for s, e in spans] if len(spans) >= 2 else []
        if self.word_scan.enabled:
            ws = self.word_scan
            for s, e in spans:
                units += [(a, b, "window") for a, b in word_windows(content, s, e, ws.window_words, ws.window_stride)]
        return units

    def _shortlist(self, variants: list[tuple[int, int, str]], limit: int) -> list[tuple[int, int, str]]:
        """The words most worth spending a model call on, ranked by the linear pre-filter.

        Removing the word that matters most gives the lowest score, whichever model is asked, and
        the pre-filter answers in ~0.1 ms against ~11 ms for DeBERTa. It only picks the shortlist;
        the model still measures the contributions and decides the trigger words, so a poor
        shortlist costs highlight quality, never correctness. Without the filter loaded this is a
        no-op and every word is measured, as before.
        """
        if len(variants) <= limit:
            return variants
        if not self.triage.enabled:
            return variants[:limit]                             # no filter: measure what the budget allows
        ranked = sorted(variants, key=lambda v: self.triage.probability(v[2]))
        return sorted(ranked[:limit], key=lambda v: v[0])       # back into reading order

    def _attach_words(self, content: str, issues: list[Issue], score: _Scorer, out: list[dict]) -> list[Issue]:
        """Word by word, for each flagged model span.

        * word_scores (`out`): occlusion per word, contribution = risk(span) - risk(span without it).
        * trigger words: the words whose single removal lowers the risk by >= min_contribution; if no
          single word does (harm spread over several words, or a saturated 0.999 score), a greedy
          search removes, one at a time, the word that lowers the risk most (in logit terms, so
          saturated scores still rank words) until the span is back at or under allow_max. That
          smallest set is what the author has to rewrite. If max_triggers words can't bring it
          under, the whole sentence is the problem and no single words are marked.
        """
        ws = self.word_scan
        flagged = [i for i in issues if i.source == "model" and (i.risk_score or 0) > self.thresholds.allow_max]
        flagged.sort(key=lambda i: -(i.risk_score or 0))    # spend the call budget on the worst spans
        # Long spans: analyse the riskiest word window inside them, not all of it (each word
        # removed from a 150-word span means 150 long inferences).
        regions = []
        for issue in flagged[: ws.max_spans]:
            wins = word_windows(content, issue.start, issue.end, ws.window_words, ws.window_stride)
            regions.append((issue, wins or [(issue.start, issue.end)]))
        win_scores = score.many([content[s:e] for _, wins in regions for s, e in wins if len(wins) > 1])
        todo = []   # (issue, region start, region text, [(word start, word end, variant text)])
        k = 0
        for issue, wins in regions:
            if len(wins) > 1:
                best = max(range(len(wins)), key=lambda j: win_scores[k + j].risk_score)
                k += len(wins)
                start, end = wins[best]
            else:
                start, end = wins[0]
            text = content[start:end]
            words = word_spans(text)[: ws.max_words]
            if len(words) < 2:
                continue
            candidates = [(a, b, drop_word(text, a, b)) for a, b in words]
            todo.append((issue, start, text, candidates))
        if not todo:
            return issues
        # Spend the budget span by span, worst first: each span costs one call for its own score
        # plus one per word measured, and a span that can't be afforded at all is dropped rather
        # than measured badly. A single long span cannot eat the whole budget either.
        spent, affordable = 0, []
        for issue, start, text, candidates in todo:
            room = ws.max_calls - spent - 1
            if room < 2:
                break
            keep = self._shortlist(candidates, min(ws.max_candidates, room))
            affordable.append((issue, start, text, keep))
            spent += 1 + len(keep)
        todo = affordable
        if not todo:
            return issues
        bases = score.many([text for _, _, text, _ in todo])
        variants = score.many([v for *_, words in todo for *_, v in words])
        triggers: dict[int, list[int]] = {}          # todo index -> indices of trigger words
        greedy: dict[int, tuple[list[int], float]] = {}   # todo index -> (removed word indices, current risk)
        candidates: dict[int, list[int]] = {}        # greedy search only tries the words that mattered most
        k = 0
        for n, ((issue, origin, _, words), base) in enumerate(zip(todo, bases)):
            risks = [variants[k + j].risk_score for j in range(len(words))]
            k += len(words)
            for (a, b, _), r in zip(words, risks):
                out.append({"start": origin + a, "end": origin + b, "contribution": round(base.risk_score - r, 6)})
            single = [j for j, r in enumerate(risks) if base.risk_score - r >= ws.min_contribution]
            if single:
                triggers[n] = sorted(sorted(single, key=lambda j: risks[j])[: ws.max_triggers])
            else:
                ranked = sorted(range(len(words)), key=lambda j: _logit(risks[j]))
                candidates[n] = ranked[:min(GREEDY_CANDIDATES, ws.max_candidates)]
                greedy[n] = ([ranked[0]], risks[ranked[0]])
        # Greedy rounds, all pending spans in one batched call per round, until the phase's
        # call budget runs out. Stopping early loses highlight detail, never a decision.
        remaining = max(0, ws.max_calls - spent)
        while greedy and remaining > 0:
            pending = {n: (removed, risk) for n, (removed, risk) in greedy.items()
                       if risk > self.thresholds.allow_max and len(removed) < ws.max_triggers}
            for n in set(greedy) - set(pending):
                removed, risk = greedy.pop(n)
                if risk <= self.thresholds.allow_max:
                    triggers[n] = sorted(removed)
            if not pending:
                break
            batch = [(n, j) for n, (removed, _) in pending.items() for j in candidates[n] if j not in removed]
            batch = batch[:remaining]
            remaining -= len(batch)
            if not batch:
                break
            results = score.many([_without(todo[n][2], todo[n][3], set(pending[n][0]) | {j}) for n, j in batch])
            best: dict[int, tuple[int, float]] = {}
            for (n, j), r in zip(batch, results):
                if n not in best or _logit(r.risk_score) < _logit(best[n][1]):
                    best[n] = (j, r.risk_score)
            for n, (j, risk) in best.items():
                greedy[n] = (pending[n][0] + [j], risk)
            for n in set(pending) - set(best):   # no words left to remove
                greedy.pop(n)
        updated: dict[int, Issue] = {}
        for n, idx in triggers.items():
            issue, origin, _, words = todo[n]
            spans = tuple((origin + words[j][0], origin + words[j][1]) for j in idx)
            updated[id(issue)] = Issue(issue.start, issue.end, issue.source, issue.message, issue.category,
                                       issue.rule_id, issue.risk_score, spans)
        return [updated.get(id(i), i) for i in issues]

    def _sentence_issues(self, content: str, view: str, whole: ScoreResult, score: _Scorer) -> list[Issue]:
        spans = scan_spans(content)
        if len(spans) <= 1:
            s, e = spans[0] if spans else (0, len(content))
            return [Issue(s, e, "model", self.feedback.model_sentence, risk_score=whole.risk_score)]
        head = spans[: self.feedback.max_highlight_sentences]
        scored = [(s, e, r.risk_score) for (s, e), r in zip(head, _span_scores(content, view, head, score))]
        flagged = [x for x in scored if x[2] > self.thresholds.allow_max]
        # If no single sentence stands out, the combination is the problem: point at the riskiest one.
        chosen = flagged or [max(scored, key=lambda x: x[2])]
        return [Issue(s, e, "model", self.feedback.model_sentence, risk_score=r) for s, e, r in chosen]

    def _build(self, req, decision, stage, gate, scored, gate_ms, total_ms, segments, sentence_scores,
               inference_ms, feedback, word_scores=(), stages=None) -> dict:
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
            # Every sentence scored on its own (offsets + risk), when the post has 2+ sentences,
            # plus word windows over long sentences ("kind": "window").
            "sentence_scores": sentence_scores,
            # Word by word, for spans the model flagged: how much each word adds to the risk.
            "word_scores": list(word_scores),
            # Author-facing: what to fix and where (null when allowed). Offsets into `content`.
            "feedback": feedback,
            "gate_version": self.gate.version,
            "model_version": None if scored is None else scored.model_version,
            "thresholds": self.thresholds.as_dict(),
            "latency_ms": {
                "gate": round(gate_ms, 3),
                "inference": None if inference_ms is None else round(inference_ms, 3),
                "total": round(total_ms if total_ms is not None else gate_ms, 3),
                # Wall clock per phase, so a slow request says which part was slow.
                "stages": {} if stages is None else stages.as_dict(),
            },
            "decided_at": datetime.now(timezone.utc).isoformat(),
        }
