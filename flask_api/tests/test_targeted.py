import pytest

from app import create_app
from app.gate import Gate
from app.model import ScoreResult
from app.targeted import SentenceScan, TargetedAbuse
from tests.conftest import FixedScorer


# ---- deterministic rule (kind: targeted / words) -------------------------------

TARGETED_YAML = """
rules:
  - id: t.targeted
    category: abuse
    action: block
    kind: targeted
    subjects: [he, she, they, them, their]
    terms: [idiot, go to hell, "s&m"]
    terms_file: lists/extra.txt
    exclude_file: lists/exclude.txt
  - id: t.words
    category: profanity
    action: flag
    kind: words
    terms_file: lists/extra.txt
    exclude_file: lists/exclude.txt
    exclude: [sucks]
"""


@pytest.fixture
def gate(tmp_path):
    (tmp_path / "lists").mkdir()
    (tmp_path / "lists" / "extra.txt").write_text("# comment\nclown\nsucks\nflange\nbig mouth\n")
    (tmp_path / "lists" / "exclude.txt").write_text("flange\n")
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "gate.yaml").write_text(TARGETED_YAML)
    return Gate.from_yaml(cfg / "gate.yaml")


def ids(result):
    return [m.rule_id for m in result.matches]


@pytest.mark.parametrize("text", [
    "They are idiots",                                # plural of a term
    "He is a clown",                                  # term from file
    "She's such an idiot",                            # contraction on the subject
    "She’s such an idiot",                       # curly apostrophe
    "They told us to go to hell",                     # phrase
    "HE IS AN ｉｄｉｏｔ",        # case + full-width letters
    "Nice prices. But their manager is an idiot!",    # second sentence
    "He sucks",                                       # excluded only from the words rule
])
def test_targeted_blocks(gate, text):
    r = gate.check(text)
    assert r.blocked and ids(r) == ["t.targeted"]


@pytest.mark.parametrize("text", [
    "Idiot. He sells good valves",           # term before the subject, other sentence
    "The idiot-proof design, they say",      # hyphenated compound is one token
    "Theyidiot",                             # no word boundary
    "Idiots everywhere",                     # no subject
    "He supplies a flange",                  # excluded file term
    "The theme is clowning",                 # 'the'/'theme' aren't subjects; 'clowning' isn't 'clown'
])
def test_targeted_does_not_block(gate, text):
    assert not gate.check(text).blocked


def test_words_rule_flags_undirected_profanity(gate):
    r = gate.check("What a clown show")
    assert r.flagged and not r.blocked and ids(r) == ["t.words"]


def test_words_rule_respects_its_own_exclude(gate):
    assert not gate.check("This vacuum sucks up dust").matches


def test_multiword_file_term(gate):
    assert gate.check("She has a big mouth").blocked


def test_symbol_term_uses_regex_fallback(gate):
    assert gate.check("They are into s&m").blocked
    assert not gate.check("s&m. They sell pipes").blocked


def test_targeted_rule_needs_subjects(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("rules:\n  - id: x\n    kind: targeted\n    terms: [a]\n")
    with pytest.raises(ValueError, match="subjects"):
        Gate.from_yaml(p)


def test_unknown_kind_rejected(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("rules:\n  - id: x\n    kind: fuzzy\n    terms: [a]\n")
    with pytest.raises(ValueError, match="kind"):
        Gate.from_yaml(p)


# ---- segment extraction --------------------------------------------------------

TA = TargetedAbuse()


def seg_texts(text, ta=TA):
    return [text[s:e] for s, e in ta.segments(text)]


def test_segment_runs_from_subject_to_sentence_end():
    text = "Great valves and fast delivery. But he will regret cheating us. Call now."
    assert seg_texts(text) == ["he will regret cheating us"]


def test_whole_text_segment_skipped():
    assert seg_texts("They will pay for this") == []


def test_subject_with_nothing_after_is_skipped():
    assert seg_texts("Good supplier. Do not trust them.") == []


def test_decimal_is_not_a_sentence_break():
    text = "Price is 2.5 kg per bag and they cheat on weight. Order today"
    assert seg_texts(text) == ["they cheat on weight"]


def test_number_at_sentence_end_still_breaks():
    text = "Minimum order is 50. They are cheats and liars. Call us"
    assert seg_texts(text) == ["They are cheats and liars"]


def test_max_segments_caps_inference():
    text = ". ".join(f"Line {i}, he did thing {i}" for i in range(6))
    assert len(TargetedAbuse(max_segments=2).segments(text)) == 2


def test_subject_match_is_whole_word():
    assert seg_texts("There is the theme. Order here.") == []


def test_invalid_max_segments():
    with pytest.raises(ValueError):
        TargetedAbuse(max_segments=0)


# ---- pipeline escalation -------------------------------------------------------

class SegmentScorer:
    """Whole text scores low; a short text containing 'regret' scores reject-level."""
    version = "seg-1"

    def __init__(self):
        self.calls = []

    def score(self, text):
        self.calls.append(text)
        risk = 0.95 if "regret" in text and len(text) < 40 else 0.1
        return ScoreResult(risk, "reject" if risk > 0.5 else "safe", {"safe": 1 - risk, "reject": risk},
                           self.version, 2.0)


def post(client, content):
    return client.post("/v1/moderate", json={"content": content, "content_type": "post"}).get_json()


TEXT = "Great valves and fast delivery from this supplier. But he will regret cheating us."


def test_targeted_segment_escalates_to_reject(make_settings):
    body = post(create_app(make_settings(), scorer=SegmentScorer()).test_client(), TEXT)
    assert body["decision"] == "reject" and body["decided_by"] == "targeted"
    seg = body["targeted_segments"][0]
    assert TEXT[seg["start"]:seg["end"]] == "he will regret cheating us"
    assert seg["risk_score"] == 0.95
    assert body["risk_score"] == 0.1                      # whole-text score is still reported
    assert body["latency_ms"]["inference"] == 4.0         # whole text + one segment
    assert "regret" not in str(body["targeted_segments"])  # offsets only, no text


def test_targeted_check_can_be_disabled(make_settings):
    # The sentence scan would also catch this sentence, so turn both off.
    s = make_settings(targeted=TargetedAbuse(enabled=False), sentence_scan=SentenceScan(enabled=False))
    body = post(create_app(s, scorer=SegmentScorer()).test_client(), TEXT)
    assert body["decision"] == "allow" and body["targeted_segments"] == []


def test_low_segment_score_does_not_change_decision(make_settings):
    text = "Great valves. They deliver on time every week."
    body = post(create_app(make_settings(), scorer=SegmentScorer()).test_client(), text)
    assert body["decision"] == "allow" and body["decided_by"] == "model"
    assert len(body["targeted_segments"]) == 1


def test_skipped_when_whole_text_already_rejected(make_settings):
    scorer = FixedScorer(0.9)
    body = post(create_app(make_settings(), scorer=scorer).test_client(), TEXT)
    assert body["decision"] == "reject" and body["decided_by"] == "model"
    assert scorer.calls == 1 and body["targeted_segments"] == []


def test_gate_flag_does_not_change_decision(make_settings):
    text = "See bit.ly/abc for prices. They deliver on time every week."
    body = post(create_app(make_settings(), scorer=SegmentScorer()).test_client(), text)
    assert body["decision"] == "allow"
