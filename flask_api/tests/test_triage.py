import json

import numpy as np
import pytest

from app import create_app
from app.model import ScoreResult
from app.targeted import TargetedAbuse, WordScan
from app.triage import FEATURE_BITS, FEATURE_VERSION, TriageFilter, _bucket, hash_features


def make_triage(risky_words=(), threshold=0.5, intercept=-8.0):
    """A real filter with hand-set weights, so the tests exercise the actual hashing path."""
    weights = np.zeros(1 << FEATURE_BITS, dtype=np.float32)
    for word in risky_words:
        weights[_bucket("w:" + word)] = 1000.0
    return TriageFilter(True, threshold, "test", weights, intercept)


# ---- features ------------------------------------------------------------------

def test_features_are_deterministic_and_normalised():
    idx, val = hash_features("Earn 50000 per week")
    assert (idx, val) == hash_features("Earn 50000 per week")
    assert float(np.linalg.norm(val)) == pytest.approx(1.0)


def test_case_and_whitespace_do_not_change_features():
    assert hash_features("Earn  50000\nper week") == hash_features("earn 50000 per week")


def test_empty_text_has_no_features():
    assert hash_features("   ") == ([], [])


def test_similar_spellings_share_character_features():
    """Char n-grams are why an obfuscated word still looks like the word it imitates."""
    plain = set(hash_features("first copy watches")[0])
    leet = set(hash_features("f1rst c0py watches")[0])
    assert len(plain & leet) > 10


# ---- loading -------------------------------------------------------------------

def test_disabled_by_default():
    f = TriageFilter()
    assert not f.enabled


def test_missing_bundle_disables_instead_of_raising(tmp_path):
    assert not TriageFilter.load(tmp_path / "nope").enabled


def test_feature_version_mismatch_is_refused(tmp_path):
    (tmp_path / "triage_meta.json").write_text(json.dumps(
        {"feature_version": FEATURE_VERSION + 1, "feature_bits": FEATURE_BITS, "threshold": 0.1}))
    np.savez(tmp_path / "triage.npz", weights=np.zeros(1 << FEATURE_BITS, dtype=np.float32), intercept=np.float32(0))
    assert not TriageFilter.load(tmp_path).enabled


def test_wrong_weight_shape_is_refused(tmp_path):
    (tmp_path / "triage_meta.json").write_text(json.dumps(
        {"feature_version": FEATURE_VERSION, "feature_bits": FEATURE_BITS, "threshold": 0.1}))
    np.savez(tmp_path / "triage.npz", weights=np.zeros(128, dtype=np.float32), intercept=np.float32(0))
    assert not TriageFilter.load(tmp_path).enabled


def test_roundtrip_load_uses_the_bundles_threshold(tmp_path):
    (tmp_path / "triage_meta.json").write_text(json.dumps(
        {"feature_version": FEATURE_VERSION, "feature_bits": FEATURE_BITS, "threshold": 0.25, "version": "t-1"}))
    np.savez(tmp_path / "triage.npz", weights=np.zeros(1 << FEATURE_BITS, dtype=np.float32), intercept=np.float32(-5))
    f = TriageFilter.load(tmp_path)
    assert f.enabled and f.threshold == 0.25 and f.version == "t-1"
    assert TriageFilter.load(tmp_path, threshold=0.9).threshold == 0.9   # operator override wins


def test_clears_uses_the_riskiest_reading():
    f = make_triage(["earn"])
    assert f.clears("Cotton towels in all sizes")[0]
    assert not f.clears("Earn 50000 per week")[0]
    # the plain text looks fine on its own, but its de-obfuscated reading does not
    assert not f.clears("E4rn 50000 per week", "Earn 50000 per week")[0]


# ---- in the pipeline -----------------------------------------------------------

class CountingScorer:
    """Risky only for a short span containing the keyword, so the whole text still scores low
    and the sentence scan is what decides — the path the filter sits in front of."""
    version = "c-1"

    def __init__(self, risk_for="earn"):
        self.risk_for = risk_for
        self.calls = []

    def score(self, text):
        self.calls.append(text)
        r = 0.95 if (self.risk_for in text.lower() and len(text) < 45) else 0.01
        return ScoreResult(r, "x", {"x": r}, self.version, 1.0)


TEXT = "Handmade leather wallets. Belts in all sizes. Earn 50000 per week from home."


def post(client, content=TEXT):
    return client.post("/v1/moderate", json={"content": content, "content_type": "post"}).get_json()


def app_with(make_settings, triage, scorer, **kw):
    s = make_settings(triage=triage, word_scan=WordScan(enabled=False),
                      targeted=TargetedAbuse(enabled=False), **kw)
    return create_app(s, scorer=scorer).test_client()


def test_cleared_spans_are_not_sent_to_the_model(make_settings):
    scorer = CountingScorer()
    body = post(app_with(make_settings, make_triage(["earn"]), scorer))
    scanned = [x for x in body["sentence_scores"] if x.get("by") != "prefilter"]
    cleared = [x for x in body["sentence_scores"] if x.get("by") == "prefilter"]
    assert [TEXT[x["start"]:x["end"]] for x in cleared] == ["Handmade leather wallets", "Belts in all sizes"]
    assert [TEXT[x["start"]:x["end"]] for x in scanned] == ["Earn 50000 per week from home"]
    # whole text + the one span the filter would not clear; the two clean sentences cost nothing
    assert scorer.calls == [TEXT, "Earn 50000 per week from home"]


def test_the_decision_is_the_same_with_and_without_the_filter(make_settings):
    off = post(app_with(make_settings, TriageFilter(), CountingScorer()))
    on = post(app_with(make_settings, make_triage(["earn"]), CountingScorer()))
    assert off["decision"] == on["decision"] == "reject"
    assert off["decided_by"] == on["decided_by"] == "sentence"


def test_every_span_is_listed_even_when_cleared(make_settings):
    body = post(app_with(make_settings, make_triage(["earn"]), CountingScorer()))
    assert len(body["sentence_scores"]) == 3
    assert [x["start"] for x in body["sentence_scores"]] == sorted(x["start"] for x in body["sentence_scores"])


def test_a_cleared_span_can_never_escalate(make_settings):
    """Even if the filter is misconfigured with a threshold above the reject line, a span it
    cleared carries the filter's score and must not be read as a model risk."""
    triage = make_triage([], threshold=0.99, intercept=6.0)   # clears nothing: score ~0.997 >= 0.99
    scorer = CountingScorer()
    body = post(app_with(make_settings, triage, scorer))
    assert all(x.get("by") != "prefilter" for x in body["sentence_scores"])
    assert body["decision"] == "reject"


def test_filter_off_scores_every_span(make_settings):
    scorer = CountingScorer()
    body = post(app_with(make_settings, TriageFilter(), scorer))
    assert all("by" not in x for x in body["sentence_scores"])
    assert len(scorer.calls) == 4          # whole text + 3 sentences


def test_leetspeak_span_is_not_cleared_on_the_plain_reading(make_settings):
    text = "Handmade leather wallets. E4rn 50000 per week from home."
    scorer = CountingScorer()
    body = post(app_with(make_settings, make_triage(["earn"]), scorer), text)
    cleared = [text[x["start"]:x["end"]] for x in body["sentence_scores"] if x.get("by") == "prefilter"]
    assert cleared == ["Handmade leather wallets"]
    assert "E4rn 50000 per week from home" in scorer.calls or "Earn 50000 per week from home" in scorer.calls


def test_shipped_bundle_loads_and_matches_this_code():
    """The bundle in models/ must be usable by the code in this checkout."""
    from app.config import PROJECT_ROOT
    bundle = PROJECT_ROOT / "models" / "triage-v1"
    if not (bundle / "triage.npz").exists():
        pytest.skip("triage bundle not present (train it with training/train_triage.py)")
    f = TriageFilter.load(bundle)
    assert f.enabled, "shipped bundle failed to load; check feature_version"
    assert 0 < f.threshold < 0.5, f.threshold
    assert f.clears("Cotton bedsheets in king and queen sizes, 300 thread count")[0]
    assert not f.clears("Earn 50000 per week from home, no experience needed")[0]
