"""The moderation rating an author is shown with the feedback.

Senior review (2026-10-04): tell the author how bad it is, not just that it was refused. The
rating has to describe what actually decided — a paragraph that scores 0.1 as a whole but holds
one sentence at 0.99 is not a "low" finding — and it must never become a way to publish anyway:
content the moderator objects to is not published, whatever it is rated.
"""
import pytest

from app import create_app
from app.feedback import severity_of
from app.model import ScoreResult
from app.pipeline import SCHEMA_VERSION
from app.routing import Decision


class KeywordScorer:
    """Risk by keyword, so a test controls which sentence looks bad."""
    version = "kw-1"

    def __init__(self, rules=None, default=0.1, max_len=10_000):
        # max_len scopes the keywords to short spans, so a paragraph can score low as a whole
        # while one sentence inside it scores high - which is the case the scan exists for.
        self.rules, self.default, self.max_len = rules or {}, default, max_len

    def score(self, text):
        risk = self.default
        if len(text) <= self.max_len:
            risk = max([r for kw, r in self.rules.items() if kw in text.lower()], default=risk)
        return ScoreResult(risk, "x", {"x": risk}, self.version, 1.0)


def post(client, content, ctype="post"):
    return client.post("/v1/moderate", json={"content": content, "content_type": ctype}).get_json()


def app_with(make_settings, scorer, **overrides):
    return create_app(make_settings(**overrides), scorer=scorer).test_client()


# ---- the bands --------------------------------------------------------------------

@pytest.mark.parametrize("risk, expected", [
    (0.0, "low"), (0.49, "low"), (0.50, "medium"), (0.79, "medium"), (0.80, "high"), (1.0, "high"),
])
def test_bands_read_off_the_risk_score(risk, expected):
    assert severity_of(risk, Decision.REJECT) == expected


def test_no_model_score_is_read_from_the_decision():
    # A gate rule decided on its own, so there is nothing to band.
    assert severity_of(None, Decision.REJECT) == "high"
    assert severity_of(None, Decision.REVISE) == "medium"


# ---- the rating in the response ---------------------------------------------------

def test_allowed_content_has_no_feedback_and_so_no_rating(make_settings, scorer):
    client = app_with(make_settings, scorer)
    assert post(client, "Premium basmati rice exporter, FSSAI certified.")["feedback"] is None


@pytest.mark.parametrize("risk, expected", [(0.95, "high"), (0.75, "medium")])
def test_rejected_content_is_rated_from_its_score(make_settings, risk, expected):
    client = app_with(make_settings, KeywordScorer(default=risk))
    fb = post(client, "Some text the model does not like at all.")["feedback"]
    assert fb["severity"] == expected


def test_revise_band_can_be_rated_low(make_settings):
    # Thresholds(0.3, 0.7): 0.35 is the middle band, and 0.35 is under the "medium" floor.
    client = app_with(make_settings, KeywordScorer(default=0.35))
    fb = post(client, "Mildly odd text. It reads a little strangely.")["feedback"]
    assert fb["severity"] == "low"


def test_gate_block_is_rated_high_with_no_model_score(client):
    body = post(client, "This contains badword and should be blocked.")
    assert body["decision"] == "reject"
    assert body["risk_score"] is None          # nothing was scored; the rule decided
    assert body["feedback"]["severity"] == "high"


def test_gate_revise_rule_is_not_rated_by_the_model_score(client):
    """A deterministic policy hit on text the model is happy with.

    The model's 0.1 would read as "low" and understate it: the rule, not the model, decided.
    """
    body = post(client, "Great rates on cotton. You can pay in crypto if that is easier.")
    assert body["decision"] == "revise" and body["decided_by"] == "gate"
    assert body["risk_score"] == pytest.approx(0.1)
    assert body["feedback"]["severity"] == "medium"


def test_one_bad_sentence_is_not_rated_by_the_whole_text(make_settings):
    """The signal is what the scan found, not what the paragraph averaged out to."""
    scorer = KeywordScorer({"settle it outside": 0.99}, default=0.05, max_len=90)
    client = app_with(make_settings, scorer)
    text = ("We export cotton saris from Surat at wholesale rates. "
            "Our order book is open for the festive season. "
            "Pay us directly and we will settle it outside the platform this once.")
    body = post(client, text)
    assert body["decision"] == "reject" and body["decided_by"] == "sentence"
    assert body["risk_score"] < 0.3                      # the paragraph as a whole looks fine
    assert body["feedback"]["severity"] == "high"        # but the rating reports the sentence


# ---- the rating is a label, never a licence ---------------------------------------

@pytest.mark.parametrize("risk", [0.35, 0.75, 0.95])
def test_a_rating_never_turns_a_decision_into_an_allow(make_settings, risk):
    """Whatever it is rated, content the moderator objects to is still not published.

    The rating exists so the author knows how far off they were. There is no "post anyway", so
    nothing in the payload may read as permission to publish it.
    """
    client = app_with(make_settings, KeywordScorer(default=risk))
    body = post(client, "Some text the model does not like.")
    assert body["decision"] != "allow"
    assert body["feedback"]["severity"] in ("low", "medium", "high")
    assert not any("proceed" in k or "override" in k for k in body["feedback"])


def test_rating_does_not_disturb_the_rest_of_the_payload(client):
    """A caller that ignores `severity` sees exactly what it saw before."""
    body = post(client, "This contains badword and should be blocked.")
    fb = body["feedback"]
    assert set(fb) == {"title", "message", "severity", "categories", "issues"}
    assert fb["title"] and fb["message"] and fb["categories"] == ["hate"]


def test_schema_version_announces_the_new_field():
    assert SCHEMA_VERSION == "1.7"
