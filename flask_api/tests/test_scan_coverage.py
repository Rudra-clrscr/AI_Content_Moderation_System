"""Nothing that reaches the pipeline may go unscored.

The sentence scan is the only thing that sees past the model's 256-token window, so anything it
skips is checked by nothing at all. Two ways that happened:

* `is_scannable_sentence` (added to filter out erratic scores from logo lists and catalogue
  headings) was used to DROP spans from the scan. A counterfeit list at the end of a long
  article then published clean: the whole-text score read 0.000226 because it could not see
  that far. Such spans are now scored and marked `low_syntax`; they simply may not escalate on
  a middling score.
* `sentence_scan.max_sentences` was 400, sized for `limits.max_chars` (25,000). PDFs now reach
  the pipeline with up to `pdf.max_chars` (100,000) characters, leaving roughly a thousand
  spans past the cap.
"""
import pytest

from app import create_app
from app.config import PROJECT_ROOT, load_settings
from app.model import ScoreResult
from app.targeted import SentenceScan, is_scannable_sentence

# A row of brand names: no verb, no function words - what the syntax filter is meant to catch.
NOISE = "Rolex, Gucci, Nike, Adidas, Omega, first copy, master quality, cheap rate."
CLEAN_SENTENCE = "We weave cotton saris for wholesalers across Gujarat every week. "


class RiskyNoiseScorer:
    """Scores the brand list high, but only when it is scored ON ITS OWN.

    The length guard matters: the whole text contains the list too, and if the whole-text score
    were high the post would reject by "model" and never exercise the scan at all - which is the
    thing under test."""
    version = "noise-1"

    def __init__(self, risk=0.99):
        self.risk = risk

    def score(self, text):
        r = self.risk if ("rolex" in text.lower() and len(text) < 90) else 0.0
        return ScoreResult(r, "unsafe" if r > 0.5 else "safe", {"safe": 1 - r, "unsafe": r},
                           self.version, 1.0)


def post(client, content):
    return client.post("/v1/moderate", json={"content": content, "content_type": "article"}).get_json()


def test_the_filter_still_recognises_noise():
    assert is_scannable_sentence(NOISE) is False
    assert is_scannable_sentence("We can get your product BIS certified without testing.") is True


def test_low_syntax_span_is_scored_not_dropped(make_settings):
    client = create_app(make_settings(max_chars=50_000), scorer=RiskyNoiseScorer()).test_client()
    body = post(client, CLEAN_SENTENCE * 3 + NOISE)
    scored = [s for s in body["sentence_scores"] if s.get("low_syntax")]
    assert scored, "the brand list must still be scored, just marked low_syntax"


def test_low_syntax_span_still_rejects_when_the_score_leaves_no_doubt(make_settings):
    """The regression: this published clean when such spans were dropped from the scan."""
    client = create_app(make_settings(max_chars=50_000), scorer=RiskyNoiseScorer(0.99)).test_client()
    body = post(client, CLEAN_SENTENCE * 3 + NOISE)
    assert body["decision"] == "reject"
    assert body["decided_by"] == "sentence"


def test_low_syntax_span_does_not_escalate_on_a_middling_score(make_settings):
    """Why the filter was added: a fragment scoring in the middle band is noise, not a verdict."""
    client = create_app(make_settings(max_chars=50_000), scorer=RiskyNoiseScorer(0.72)).test_client()
    body = post(client, CLEAN_SENTENCE * 3 + NOISE)
    assert body["decision"] == "allow"
    [entry] = [s for s in body["sentence_scores"] if s.get("low_syntax")]
    assert entry["risk_score"] == pytest.approx(0.72)      # recorded, but ignored


def test_an_ordinary_sentence_still_escalates_in_the_middle_band(make_settings):
    """The low_syntax exemption must not quietly disarm the ordinary revise path.

    0.5 sits inside the fixture's middle band (Thresholds(0.3, 0.7))."""
    class MiddlingScorer:
        version = "m-1"

        def score(self, text):
            r = 0.5 if ("bis certified" in text.lower() and len(text) < 90) else 0.0
            return ScoreResult(r, "x", {"x": r}, self.version, 1.0)

    client = create_app(make_settings(max_chars=50_000), scorer=MiddlingScorer()).test_client()
    body = post(client, CLEAN_SENTENCE * 3 + "We get your product BIS certified without testing.")
    assert body["decision"] == "revise" and body["decided_by"] == "sentence"


def test_the_scan_cap_covers_the_longest_text_that_can_reach_it():
    """`api.py` sends a PDF's text under pdf.max_chars, so the cap must cover that, not the
    smaller limits.max_chars. 62 chars per span is a deliberately conservative sentence length."""
    s = load_settings(PROJECT_ROOT / "config" / "settings.yaml")
    longest = max(s.max_chars, s.pdf.max_chars)
    needed = longest / 62
    assert s.sentence_scan.max_sentences >= needed, (
        f"sentence_scan.max_sentences ({s.sentence_scan.max_sentences}) does not cover "
        f"{longest} characters (~{needed:.0f} spans); text past the cap is scored by nothing")


def test_low_syntax_reject_min_is_validated():
    with pytest.raises(ValueError):
        SentenceScan(low_syntax_reject_min=0.0)
    with pytest.raises(ValueError):
        SentenceScan(low_syntax_reject_min=1.5)
