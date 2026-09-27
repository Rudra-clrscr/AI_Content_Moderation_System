"""Author-facing feedback: what to fix, and where.

Instead of a human-review queue, content in the middle band is returned to its
author (like a "before you post" prompt): the response lists each problem with
its character span in the author's text and an instruction, so the client can
highlight it and the author can edit and resubmit.

Wording comes from config/feedback_messages.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.gate import GateResult
from app.routing import Decision


@dataclass(frozen=True)
class Issue:
    start: int
    end: int
    source: str                     # "rule" or "model"
    message: str
    category: str | None = None
    rule_id: str | None = None
    risk_score: float | None = None

    def as_dict(self) -> dict:
        d = {"start": self.start, "end": self.end, "source": self.source, "message": self.message}
        if self.rule_id:
            d["rule_id"] = self.rule_id
        if self.category:
            d["category"] = self.category
        if self.risk_score is not None:
            d["risk_score"] = round(self.risk_score, 6)
        return d


@dataclass
class Feedback:
    revise_title: str = "Your post needs a few changes"
    revise_message: str = "Some parts of your post may go against our community policies. Edit the highlighted parts and post again."
    reject_title: str = "Your post can't be published"
    reject_message: str = "It appears to break our community policies on {categories}."
    reject_message_generic: str = "It appears to break our community policies."
    highlight_on_reject: bool = False
    max_highlight_sentences: int = 5
    categories: dict[str, str] = field(default_factory=dict)
    rule_messages: dict[str, str] = field(default_factory=dict)
    model_sentence: str = "This sentence may go against our community policies. Rephrase it."
    model_targeted: str = "This sentence about another person or business may come across as hostile. Rephrase it."

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Feedback":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        rev, rej, model = raw.get("revise", {}), raw.get("reject", {}), raw.get("model", {})
        d = cls()
        return cls(
            revise_title=rev.get("title", d.revise_title),
            revise_message=rev.get("message", d.revise_message),
            reject_title=rej.get("title", d.reject_title),
            reject_message=rej.get("message", d.reject_message),
            reject_message_generic=rej.get("message_generic", d.reject_message_generic),
            highlight_on_reject=bool(raw.get("highlight_on_reject", False)),
            max_highlight_sentences=int(raw.get("max_highlight_sentences", 5)),
            categories=raw.get("categories") or {},
            rule_messages=raw.get("rules") or {},
            model_sentence=model.get("sentence", d.model_sentence),
            model_targeted=model.get("targeted", d.model_targeted),
        )

    def rule_issues(self, gate: GateResult, actions: tuple[str, ...]) -> list[Issue]:
        return [Issue(s, e, "rule", self.rule_messages.get(m.rule_id, "Edit this part of your post."),
                      m.category, m.rule_id)
                for m in gate.matches if m.action in actions for s, e in m.spans]

    def build(self, decision: Decision, gate: GateResult, model_issues: list[Issue]) -> dict | None:
        if decision is Decision.ALLOW:
            return None

        if decision is Decision.REVISE:
            issues = self.rule_issues(gate, ("revise",)) + model_issues
            return {"title": self.revise_title, "message": self.revise_message,
                    "issues": [i.as_dict() for i in sorted(issues, key=lambda i: (i.start, i.end))]}

        # REJECT: name the policy areas; highlight only if configured to.
        blocking = [m for m in gate.matches if m.action == "block"]
        names = list(dict.fromkeys(self.categories.get(m.category, m.category) for m in blocking))
        message = (self.reject_message.format(categories=_join(names)) if names else self.reject_message_generic)
        issues = (self.rule_issues(gate, ("block", "revise")) + model_issues) if self.highlight_on_reject else []
        return {"title": self.reject_title, "message": message,
                "categories": list(dict.fromkeys(m.category for m in blocking)),
                "issues": [i.as_dict() for i in sorted(issues, key=lambda i: (i.start, i.end))]}


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
