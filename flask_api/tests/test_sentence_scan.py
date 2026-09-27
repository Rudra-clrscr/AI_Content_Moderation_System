import pytest

from app import create_app
from app.model import ScoreResult
from app.targeted import SentenceScan, TargetedAbuse


class ShortSentenceScorer:
    """The whole post scores low; any short text containing 'earn' scores `risk`."""
    version = "s-1"

    def __init__(self, risk=0.95):
        self.risk = risk
        self.calls = []

    def score(self, text):
        self.calls.append(text)
        r = self.risk if ("earn" in text.lower() and len(text) < 45) else 0.01
        return ScoreResult(r, "x", {"x": r}, self.version, 1.0)


def post(client, content):
    return client.post("/v1/moderate", json={"content": content, "content_type": "post"}).get_json()


TEXT = "Handmade leather wallets and belts. Earn 50000 per week from home."


def test_reject_level_sentence_rejects_whole_post(make_settings):
    body = post(create_app(make_settings(), scorer=ShortSentenceScorer()).test_client(), TEXT)
    assert body["decision"] == "reject" and body["decided_by"] == "sentence"
    assert body["risk_score"] == 0.01                      # whole text alone would be allowed
    worst = max(body["sentence_scores"], key=lambda x: x["risk_score"])
    assert TEXT[worst["start"]:worst["end"]] == "Earn 50000 per week from home"
    assert body["feedback"]["issues"] == []                # rejects don't highlight by default


def test_middle_band_sentence_asks_for_revision(make_settings):
    body = post(create_app(make_settings(), scorer=ShortSentenceScorer(risk=0.5)).test_client(), TEXT)
    assert body["decision"] == "revise" and body["decided_by"] == "sentence"
    issues = body["feedback"]["issues"]
    assert [TEXT[i["start"]:i["end"]] for i in issues] == ["Earn 50000 per week from home"]


def test_middle_band_revise_can_be_turned_off(make_settings):
    s = make_settings(sentence_scan=SentenceScan(revise_on_middle=False))
    body = post(create_app(s, scorer=ShortSentenceScorer(risk=0.5)).test_client(), TEXT)
    assert body["decision"] == "allow" and len(body["sentence_scores"]) == 2


def test_single_sentence_post_is_not_rescanned(make_settings):
    scorer = ShortSentenceScorer()
    body = post(create_app(make_settings(), scorer=scorer).test_client(), "Handmade leather wallets")
    assert body["sentence_scores"] == [] and len(scorer.calls) == 1


def test_scan_is_capped(make_settings):
    scorer = ShortSentenceScorer()
    text = ". ".join(f"Line {i} is fine" for i in range(10))
    s = make_settings(sentence_scan=SentenceScan(max_sentences=3), targeted=TargetedAbuse(enabled=False))
    body = post(create_app(s, scorer=scorer).test_client(), text)
    assert len(body["sentence_scores"]) == 3 and len(scorer.calls) == 1 + 3


def test_scan_can_be_disabled(make_settings):
    s = make_settings(sentence_scan=SentenceScan(enabled=False))
    body = post(create_app(s, scorer=ShortSentenceScorer()).test_client(), TEXT)
    assert body["decision"] == "allow" and body["sentence_scores"] == []


def test_skipped_when_already_rejected(make_settings):
    class High(ShortSentenceScorer):
        def score(self, text):
            self.calls.append(text)
            return ScoreResult(0.9, "x", {"x": 0.9}, self.version, 1.0)
    scorer = High()
    body = post(create_app(make_settings(), scorer=scorer).test_client(), TEXT)
    assert body["decided_by"] == "model" and body["sentence_scores"] == [] and len(scorer.calls) == 1


def test_sentences_are_scored_once(make_settings):
    """Scan + highlight + targeted checks share one cache per request."""
    scorer = ShortSentenceScorer(risk=0.5)   # middle band -> revise path also highlights sentences
    text = "Good wallets. They earn praise. Ships fast."
    post(create_app(make_settings(), scorer=scorer).test_client(), text)
    assert len(scorer.calls) == len(set(scorer.calls))


def test_invalid_max_sentences():
    with pytest.raises(ValueError):
        SentenceScan(max_sentences=0)




def test_harmful_sentence_deep_in_long_article_is_caught(make_settings):
    filler = " ".join(f"Paragraph {i} describes our cotton mill and its quality checks." for i in range(40))
    text = filler + " Earn 50000 per week from home. " + filler
    s = make_settings(max_chars=50_000)
    body = post(create_app(s, scorer=ShortSentenceScorer()).test_client(), text)
    assert body["decision"] == "reject" and body["decided_by"] == "sentence"
    assert len(body["sentence_scores"]) == 81          # every sentence, not just the first few
