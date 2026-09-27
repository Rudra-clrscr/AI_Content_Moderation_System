import pytest
import yaml

from app import create_app
from app.config import PROJECT_ROOT
from app.feedback import Feedback
from app.gate import Gate, normalize_with_map
from app.model import ScoreResult
from app.targeted import TargetedAbuse, sentence_spans


class KeywordScorer:
    """Risk by keyword, so tests control which sentence looks bad."""
    version = "kw-1"

    def __init__(self, rules=None, default=0.1):
        self.rules = rules or {}
        self.default = default
        self.calls = []

    def score(self, text):
        self.calls.append(text)
        risk = max([r for kw, r in self.rules.items() if kw in text.lower()], default=self.default)
        return ScoreResult(risk, "x", {"x": risk}, self.version, 1.0)


def post(client, content, ctype="post"):
    return client.post("/v1/moderate", json={"content": content, "content_type": ctype}).get_json()


def app_with(make_settings, scorer, **overrides):
    return create_app(make_settings(**overrides), scorer=scorer).test_client()


def marked(text, issues):
    return [text[i["start"]:i["end"]] for i in issues]


# ---- offsets back to the original text ------------------------------------------

def test_normalize_map_points_back_to_original():
    text = "  Ｈｅｌｌｏ​   WORLD  "
    norm, index = normalize_with_map(text)
    assert norm == "hello world"
    assert "".join(text[i] for i in index) == "Ｈｅｌｌｏ WORLD"


def test_casefold_expansion_keeps_map_aligned():
    norm, index = normalize_with_map("Straße")
    assert norm == "strasse" and len(index) == len(norm) and index[-1] == 5


def test_gate_spans_are_in_original_text(gate_file):
    g = Gate.from_yaml(gate_file)
    text = "Buy now: DOUBLE     your MONEY today"
    m = g.check(text).matches[0]
    assert [text[s:e] for s, e in m.spans] == ["DOUBLE     your MONEY"]


def test_sentence_spans_are_trimmed():
    text = "  First one.   Second, 2.5 kg!  "
    assert [text[s:e] for s, e in sentence_spans(text)] == ["First one", "Second, 2.5 kg"]


# ---- revise --------------------------------------------------------------------

def test_revise_from_gate_rule_highlights_match(make_settings):
    text = "Steel valves in stock. You can pay in crypto or by card."
    body = post(app_with(make_settings, KeywordScorer()), text)
    assert body["decision"] == "revise" and body["decided_by"] == "gate"
    fb = body["feedback"]
    assert fb["title"] and fb["message"]
    assert marked(text, fb["issues"]) == ["pay in crypto"]
    assert fb["issues"][0]["rule_id"] == "test.revise_payment" and fb["issues"][0]["source"] == "rule"


def test_revise_from_model_single_sentence_highlights_it(make_settings):
    text = "  Earn big from home, message us  "
    body = post(app_with(make_settings, KeywordScorer(default=0.5)), text)
    assert body["decision"] == "revise" and body["decided_by"] == "model"
    assert marked(text, body["feedback"]["issues"]) == ["Earn big from home, message us"]


def test_revise_from_model_highlights_only_the_bad_sentence(make_settings):
    text = "Quality cotton yarn. Guaranteed profit, no questions asked. Ships from Surat."
    scorer = KeywordScorer({"guaranteed profit": 0.6, "cotton": 0.5})
    scorer.rules["quality cotton yarn. guaranteed"] = 0.5   # whole text is borderline
    body = post(app_with(make_settings, scorer), text)
    assert body["decision"] == "revise"
    issues = body["feedback"]["issues"]
    assert marked(text, issues) == ["Quality cotton yarn", "Guaranteed profit, no questions asked"]
    assert all(i["source"] == "model" and "risk_score" in i for i in issues)


def test_when_no_sentence_stands_out_the_riskiest_is_shown(make_settings):
    text = "Part one here. Part two is odd. Part three."
    scorer = KeywordScorer({"part one here. part two": 0.5, "odd": 0.2})
    body = post(app_with(make_settings, scorer), text)
    assert body["decision"] == "revise"
    assert marked(text, body["feedback"]["issues"]) == ["Part two is odd"]


def test_highlight_scoring_is_capped(make_settings, tmp_path):
    msgs = tmp_path / "fb.yaml"
    msgs.write_text("max_highlight_sentences: 2\n")
    text = ". ".join(f"Sentence number {i}" for i in range(6))
    scorer = KeywordScorer(default=0.5)
    body = post(app_with(make_settings, scorer, feedback_messages_file=msgs), text)
    assert body["decision"] == "revise"
    assert len(scorer.calls) == 1 + 2    # whole text + 2 sentences


def test_targeted_sentence_in_middle_band_asks_for_revision(make_settings):
    text = "Great valves and fast delivery. But he is a shady operator."

    class SegmentOnly(KeywordScorer):   # only the standalone sentence looks borderline
        def score(self, t):
            self.calls.append(t)
            risk = 0.5 if t.startswith("he is a shady") else 0.1
            return ScoreResult(risk, "x", {"x": risk}, self.version, 1.0)

    scorer = SegmentOnly()
    body = post(app_with(make_settings, scorer), text)
    assert body["decision"] == "revise" and body["decided_by"] == "targeted"
    assert marked(text, body["feedback"]["issues"]) == ["he is a shady operator"]


# ---- reject and allow ----------------------------------------------------------

def test_reject_names_policy_area_without_highlights(make_settings):
    body = post(app_with(make_settings, KeywordScorer()), "double your money now")
    fb = body["feedback"]
    assert body["decision"] == "reject" and fb["categories"] == ["fraud"]
    assert "fraud" in fb["message"] and fb["issues"] == []


def test_model_reject_uses_generic_message(make_settings):
    body = post(app_with(make_settings, KeywordScorer(default=0.95)), "some text")
    fb = body["feedback"]
    assert body["decision"] == "reject" and fb["categories"] == [] and fb["issues"] == []
    assert "{categories}" not in fb["message"]


def test_highlight_on_reject_can_be_enabled(make_settings, tmp_path):
    msgs = tmp_path / "fb.yaml"
    msgs.write_text("highlight_on_reject: true\n")
    text = "Please double your money now"
    body = post(app_with(make_settings, KeywordScorer(), feedback_messages_file=msgs), text)
    assert marked(text, body["feedback"]["issues"]) == ["double your money"]


def test_allow_has_no_feedback(make_settings):
    assert post(app_with(make_settings, KeywordScorer()), "Steel valves in stock")["feedback"] is None


def test_feedback_offsets_only_no_text_in_issues(make_settings):
    text = "You can pay in crypto here"
    issues = post(app_with(make_settings, KeywordScorer()), text)["feedback"]["issues"]
    assert "crypto" not in str(issues)


# ---- shipped configuration -----------------------------------------------------

def test_every_revise_rule_has_an_instruction():
    rules = yaml.safe_load((PROJECT_ROOT / "config" / "gate_patterns.yaml").read_text(encoding="utf-8"))["rules"]
    fb = Feedback.from_yaml(PROJECT_ROOT / "config" / "feedback_messages.yaml")
    missing = [r["id"] for r in rules if r.get("action") == "revise" and r["id"] not in fb.rule_messages]
    assert missing == []


def test_shipped_messages_load():
    fb = Feedback.from_yaml(PROJECT_ROOT / "config" / "feedback_messages.yaml")
    assert fb.highlight_on_reject is False and fb.max_highlight_sentences >= 1
    assert "{categories}" in fb.reject_message


def test_targeted_disabled_still_gives_model_feedback(make_settings):
    s = make_settings(targeted=TargetedAbuse(enabled=False))
    body = post(create_app(s, scorer=KeywordScorer(default=0.5)).test_client(), "He is odd")
    assert body["decision"] == "revise" and body["feedback"]["issues"]


def test_sentence_highlighted_once_when_targeted_check_already_marked_it(make_settings):
    text = "Our team packs every order. They use odd cartons for shipments."
    scorer = KeywordScorer({"odd": 0.5})   # whole text and the 'They…' sentence are both borderline
    issues = post(app_with(make_settings, scorer), text)["feedback"]["issues"]
    spans = [(i["start"], i["end"]) for i in issues]
    assert len(spans) == len(set(spans))
    assert marked(text, issues) == ["They use odd cartons for shipments"]
