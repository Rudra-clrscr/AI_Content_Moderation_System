"""Automated tests for the Unified Moderation API (`POST /v1/moderate/unified`).

Verifies the wrapper behavior across all 12 scenarios:
1. Text only -> DeBERTa called; image model not called.
2. Image only -> Image model called; DeBERTa not called.
3. Both text and image -> Both models called.
4. Neither text nor image -> HTTP 400.
5. Safe text + safe image -> Overall allow.
6. Unsafe text + safe image -> Overall reject.
7. Safe text + unsafe image -> Overall reject.
8. Unsafe text + unsafe image -> Overall reject.
9. Image API failure -> Correct error handling; never silently allow.
10. Text API failure -> Correct error handling; never silently allow.
11. Invalid or unsupported file -> Correct validation error.
12. Existing individual APIs continue to work as before.
"""
import io
import pytest

from app import create_app
from app.clip import VisualConfig, VisualResult
from app.model import ModelNotReady, ScoreResult
from tests.conftest import FixedScorer


def png(colour=(200, 200, 200), size=(64, 64)) -> bytes:
    Image = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


class FakeVisual:
    """Mock visual check for testing."""

    def __init__(self, result: VisualResult, reject: bool = False, config=None):
        self.result = result
        self.reject = reject
        self.calls = 0
        self.config = config or VisualConfig(enabled=True)

    def classify(self, image):
        self.calls += 1
        return self.result

    def decides_reject(self, result):
        return self.reject

    @property
    def status(self):
        return {"enabled": True, "ready": True, "provider": "FakeProvider"}


class FailingScorer:
    """Scorer that fails with ModelNotReady."""
    version = "failing-1"

    def score(self, text):
        raise ModelNotReady("DeBERTa weights not loaded")


def build_app(make_settings, scorer=None, visual=None, sink=None, **kw):
    scorer = scorer or FixedScorer(0.1)
    s = make_settings(**kw)
    if visual is not None:
        s.visual = visual
    return create_app(s, scorer=scorer, sink=sink).test_client()


# --------------------------------------------------------------------------------
# Scenario 1: Text only -> DeBERTa called; image model not called
# --------------------------------------------------------------------------------

def test_1_text_only_calls_deberta_not_image_model(make_settings, sink):
    scorer = FixedScorer(0.1)
    visual = FakeVisual(VisualResult(checked=True, label="safe", score=0.99), reject=False)
    client = build_app(make_settings, scorer=scorer, visual=visual, sink=sink)

    # Post JSON
    res = client.post("/v1/moderate/unified", json={
        "content": "Fresh organic basmati rice available for export.",
        "content_type": "product_listing",
        "content_id": "item-101"
    })

    assert res.status_code == 200
    body = res.get_json()
    assert body["Success"] is True
    data = body["Data"]

    # Verify DeBERTa was called and image model was NOT called
    assert scorer.calls == 1
    assert visual.calls == 0

    # Verify checks dictionary
    assert data["checks"]["text"]["executed"] is True
    assert data["checks"]["image"]["executed"] is False
    assert data["checks"]["text"]["decision"] == "approved"
    assert data["checks"]["image"]["decision"] is None
    assert data["checks"]["image"]["result"] is None

    # Overall decision
    assert data["decision"] == "approved"
    assert data["Action"] == "approved"
    assert data["legacy_decision"] == "allow"


def test_1_text_only_multipart_form(make_settings, sink):
    scorer = FixedScorer(0.1)
    visual = FakeVisual(VisualResult(checked=True, label="safe", score=0.99), reject=False)
    client = build_app(make_settings, scorer=scorer, visual=visual, sink=sink)

    # Post multipart form data without file
    res = client.post("/v1/moderate/unified", data={
        "content": "Text via multipart form data.",
        "content_type": "article"
    }, content_type="multipart/form-data")

    assert res.status_code == 200
    data = res.get_json()["Data"]
    assert scorer.calls == 1
    assert visual.calls == 0
    assert data["checks"]["text"]["executed"] is True
    assert data["checks"]["image"]["executed"] is False


# --------------------------------------------------------------------------------
# Scenario 2: Image only -> Image model called; DeBERTa not called
# --------------------------------------------------------------------------------

def test_2_image_only_calls_image_model_not_deberta(make_settings, sink):
    scorer = FixedScorer(0.1)
    visual = FakeVisual(VisualResult(checked=True, label="safe", score=0.99, scores={"safe": 0.99}), reject=False)
    client = build_app(make_settings, scorer=scorer, visual=visual, sink=sink)

    img_bytes = png()
    res = client.post("/v1/moderate/unified", data={
        "file": (io.BytesIO(img_bytes), "sample.png"),
        "content_type": "article",
        "content_id": "img-202"
    }, content_type="multipart/form-data")

    assert res.status_code == 200
    body = res.get_json()
    assert body["Success"] is True
    data = body["Data"]

    # Verify image model was called and DeBERTa was NOT called
    assert visual.calls == 1
    assert scorer.calls == 0

    # Verify checks dictionary
    assert data["checks"]["text"]["executed"] is False
    assert data["checks"]["image"]["executed"] is True
    assert data["checks"]["text"]["decision"] is None
    assert data["checks"]["text"]["result"] is None
    assert data["checks"]["image"]["decision"] == "approved"

    # Overall decision
    assert data["decision"] == "approved"
    assert data["Action"] == "approved"
    assert data["media"]["visual_content_checked"] is True


# --------------------------------------------------------------------------------
# Scenario 3: Both text and image -> Both models called
# --------------------------------------------------------------------------------

def test_3_both_text_and_image_calls_both_models(make_settings, sink):
    scorer = FixedScorer(0.1)
    visual = FakeVisual(VisualResult(checked=True, label="safe", score=0.99), reject=False)
    client = build_app(make_settings, scorer=scorer, visual=visual, sink=sink)

    res = client.post("/v1/moderate/unified", data={
        "content": "A high-quality agricultural exporter catalog.",
        "file": (io.BytesIO(png()), "catalog.png"),
        "content_type": "article",
        "content_id": "art-303"
    }, content_type="multipart/form-data")

    assert res.status_code == 200
    data = res.get_json()["Data"]

    # Both models called
    assert scorer.calls == 1
    assert visual.calls == 1

    # Checks executed flags
    assert data["checks"]["text"]["executed"] is True
    assert data["checks"]["image"]["executed"] is True
    assert data["checks"]["text"]["result"] is not None
    assert data["checks"]["image"]["result"] is not None


# --------------------------------------------------------------------------------
# Scenario 4: Neither text nor image -> HTTP 400
# --------------------------------------------------------------------------------

def test_4_neither_text_nor_image_returns_400(client):
    # Empty JSON
    r1 = client.post("/v1/moderate/unified", json={})
    assert r1.status_code == 400
    body1 = r1.get_json()
    assert body1["Success"] is False
    assert body1["Data"]["code"] == "invalid_request"

    # Empty multipart form
    r2 = client.post("/v1/moderate/unified", data={}, content_type="multipart/form-data")
    assert r2.status_code == 400
    assert r2.get_json()["Data"]["code"] == "invalid_request"


def test_4_empty_text_explicitly_supplied_returns_400(client):
    # Empty string in JSON
    r1 = client.post("/v1/moderate/unified", json={"content": "   "})
    assert r1.status_code == 400
    assert r1.get_json()["Data"]["code"] == "invalid_content"

    # Blank text and no file: nothing to check.
    r2 = client.post("/v1/moderate/unified", data={"content": "   ", "Title": ""},
                     content_type="multipart/form-data")
    assert r2.status_code == 400
    assert r2.get_json()["Data"]["code"] == "invalid_content"


def test_blank_text_boxes_beside_a_picture_are_an_empty_caption(client):
    """Exactly what Swagger sends with "Send empty value" ticked: every text box as an empty
    string, plus the picture. That is a photo-only post, not a request that sent `content`
    and the parts together - which is what it was refused as."""
    r = client.post("/v1/moderate/unified", data={
        "content": "", "Title": "", "Body": "  ", "Hashtags": "",
        "file": (io.BytesIO(png()), "pic.png"), "content_type": "post",
    }, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    checks = r.get_json()["Data"]["checks"]
    assert checks["text"]["executed"] is False
    assert checks["image"]["executed"] is True


def test_blank_parts_beside_content_are_not_a_conflict(client):
    r = client.post("/v1/moderate/unified", data={
        "content": "Shipping two containers of rice.", "Title": "", "Body": "", "Hashtags": "",
    }, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["Data"]["content"] == "Shipping two containers of rice."


# --------------------------------------------------------------------------------
# Scenario 5: Safe text + safe image -> Overall allow
# --------------------------------------------------------------------------------

def test_5_safe_text_and_safe_image_overall_allow(make_settings, sink):
    scorer = FixedScorer(0.05)  # safe
    visual = FakeVisual(VisualResult(checked=True, label="safe", score=0.99), reject=False)
    client = build_app(make_settings, scorer=scorer, visual=visual, sink=sink)

    res = client.post("/v1/moderate/unified", data={
        "content": "Solar panels for clean industrial energy.",
        "file": (io.BytesIO(png()), "solar.png"),
        "content_type": "article"
    }, content_type="multipart/form-data")

    assert res.status_code == 200
    body = res.get_json()
    assert body["Success"] is True
    data = body["Data"]

    assert data["decision"] == "approved"
    assert data["Action"] == "approved"
    assert data["legacy_decision"] == "allow"
    assert data["checks"]["text"]["decision"] == "approved"
    assert data["checks"]["image"]["decision"] == "approved"
    assert body["Message"] == "Content approved for publication."


# --------------------------------------------------------------------------------
# Scenario 6: Unsafe text + safe image -> Overall reject
# --------------------------------------------------------------------------------

def test_6_unsafe_text_and_safe_image_overall_reject_model(make_settings, sink):
    scorer = FixedScorer(0.95)  # unsafe score above reject threshold
    visual = FakeVisual(VisualResult(checked=True, label="safe", score=0.99), reject=False)
    client = build_app(make_settings, scorer=scorer, visual=visual, sink=sink)

    res = client.post("/v1/moderate/unified", data={
        "content": "Unsafe text that scores high risk.",
        "file": (io.BytesIO(png()), "safe.png"),
        "content_type": "article"
    }, content_type="multipart/form-data")

    assert res.status_code == 200
    data = res.get_json()["Data"]

    assert data["decision"] == "reject"
    assert data["Action"] == "reject"
    assert data["legacy_decision"] == "reject"
    assert data["checks"]["text"]["decision"] == "reject"
    assert data["checks"]["image"]["decision"] == "approved"


def test_6_unsafe_text_gate_block_and_safe_image(make_settings, sink):
    # Content has "badword" which matches test gate rule
    scorer = FixedScorer(0.1)
    visual = FakeVisual(VisualResult(checked=True, label="safe", score=0.99), reject=False)
    client = build_app(make_settings, scorer=scorer, visual=visual, sink=sink)

    res = client.post("/v1/moderate/unified", data={
        "content": "Check out this badword forbidden phrase.",
        "file": (io.BytesIO(png()), "safe.png"),
        "content_type": "article"
    }, content_type="multipart/form-data")

    assert res.status_code == 200
    data = res.get_json()["Data"]

    assert data["decision"] == "reject"
    assert data["Action"] == "reject"
    assert data["checks"]["text"]["decision"] == "reject"
    assert data["checks"]["image"]["decision"] == "approved"
    assert len(data["gate_matches"]) >= 1


# --------------------------------------------------------------------------------
# Scenario 7: Safe text + unsafe image -> Overall reject
# --------------------------------------------------------------------------------

def test_7_safe_text_and_unsafe_image_overall_reject(make_settings, sink):
    scorer = FixedScorer(0.05)  # safe text
    # visual check flags weapon
    visual_result = VisualResult(checked=True, label="weapon", category="weapons", score=0.98,
                                 unsafe_score=0.98, scores={"weapon": 0.98})
    visual = FakeVisual(visual_result, reject=True)
    client = build_app(make_settings, scorer=scorer, visual=visual, sink=sink)

    res = client.post("/v1/moderate/unified", data={
        "content": "Completely benign text about gardening equipment.",
        "file": (io.BytesIO(png()), "weapon.png"),
        "content_type": "article"
    }, content_type="multipart/form-data")

    assert res.status_code == 200
    data = res.get_json()["Data"]

    assert data["decision"] == "reject"
    assert data["Action"] == "reject"
    assert data["legacy_decision"] == "reject"
    assert data["checks"]["text"]["decision"] == "approved"
    assert data["checks"]["image"]["decision"] == "reject"


# --------------------------------------------------------------------------------
# Scenario 8: Unsafe text + unsafe image -> Overall reject
# --------------------------------------------------------------------------------

def test_8_unsafe_text_and_unsafe_image_overall_reject(make_settings, sink):
    scorer = FixedScorer(0.92)  # unsafe text
    visual_result = VisualResult(checked=True, label="gore", category="violence", score=0.96,
                                 unsafe_score=0.96, scores={"gore": 0.96})
    visual = FakeVisual(visual_result, reject=True)
    client = build_app(make_settings, scorer=scorer, visual=visual, sink=sink)

    res = client.post("/v1/moderate/unified", data={
        "content": "Dangerous scam and phishing content.",
        "file": (io.BytesIO(png()), "violent.png"),
        "content_type": "article"
    }, content_type="multipart/form-data")

    assert res.status_code == 200
    data = res.get_json()["Data"]

    assert data["decision"] == "reject"
    assert data["Action"] == "reject"
    assert data["legacy_decision"] == "reject"
    assert data["checks"]["text"]["decision"] == "reject"
    assert data["checks"]["image"]["decision"] == "reject"


# --------------------------------------------------------------------------------
# Scenario 9: Image API failure -> Correct error handling; never silently allow
# --------------------------------------------------------------------------------

def test_9_image_failure_returns_503_and_never_allows(make_settings, sink):
    scorer = FixedScorer(0.05)  # safe text
    visual = FakeVisual(VisualResult(checked=False, failed=True))  # visual check threw
    client = build_app(make_settings, scorer=scorer, visual=visual, sink=sink)

    res = client.post("/v1/moderate/unified", data={
        "content": "Safe article text.",
        "file": (io.BytesIO(png()), "damaged.png"),
        "content_type": "article"
    }, content_type="multipart/form-data")

    assert res.status_code == 503
    body = res.get_json()
    assert body["Success"] is False
    assert body["Data"]["code"] == "media_not_checkable"


# --------------------------------------------------------------------------------
# Scenario 10: Text API failure -> Correct error handling; never silently allow
# --------------------------------------------------------------------------------

def test_10_text_failure_returns_503_and_never_allows(make_settings, sink):
    visual = FakeVisual(VisualResult(checked=True, label="safe", score=0.99), reject=False)
    client = build_app(make_settings, scorer=FailingScorer(), visual=visual, sink=sink)

    res = client.post("/v1/moderate/unified", data={
        "content": "Some article content.",
        "file": (io.BytesIO(png()), "sample.png"),
        "content_type": "article"
    }, content_type="multipart/form-data")

    assert res.status_code == 503
    body = res.get_json()
    assert body["Success"] is False
    assert body["Data"]["code"] == "model_not_ready"


# --------------------------------------------------------------------------------
# Scenario 11: Invalid or unsupported file -> Correct validation error
# --------------------------------------------------------------------------------

def test_11_unsupported_file_type_returns_422(client):
    # Upload an executable header
    res = client.post("/v1/moderate/unified", data={
        "content": "Here is an executable file.",
        "file": (io.BytesIO(b"MZ\x90\x00\x03\x00"), "program.exe"),
        "content_type": "article"
    }, content_type="multipart/form-data")

    assert res.status_code == 422
    body = res.get_json()
    assert body["Success"] is False
    assert body["Data"]["code"] == "media_unsupported"


def test_11_oversized_file_returns_413(make_settings, sink):
    from app.media import MediaLimits
    s = make_settings()
    s.media = MediaLimits(max_bytes=100)
    client = create_app(s, scorer=FixedScorer(0.1), sink=sink).test_client()

    res = client.post("/v1/moderate/unified", data={
        "file": (io.BytesIO(b"A" * 200), "huge.png"),
        "content_type": "article"
    }, content_type="multipart/form-data")

    assert res.status_code == 413
    assert res.get_json()["Data"]["code"] == "media_too_large"


# --------------------------------------------------------------------------------
# Scenario 12: Existing individual APIs continue to work as before
# --------------------------------------------------------------------------------

def test_12_existing_text_api_works_as_before(client):
    res = client.post("/v1/moderate", json={
        "content": "Industrial water pump supply.",
        "content_type": "product_listing"
    })
    assert res.status_code == 200
    body = res.get_json()
    assert body["Success"] is True
    assert body["Data"]["Action"] == "approved"


def test_12_existing_media_api_works_as_before(make_settings, sink):
    visual = FakeVisual(VisualResult(checked=True, label="safe", score=0.99), reject=False)
    client = build_app(make_settings, visual=visual, sink=sink)

    res = client.post("/v1/moderate/media", data={
        "file": (io.BytesIO(png()), "test.png"),
        "content_type": "article"
    }, content_type="multipart/form-data")

    assert res.status_code == 200
    body = res.get_json()
    assert body["Success"] is True
    assert body["Data"]["Action"] == "approved"
    assert body["Data"]["media"]["visual_content_checked"] is True


# --------------------------------------------------------------------------------
# Additional Security & Validation Tests for Unified API
# --------------------------------------------------------------------------------

def test_unified_service_token_enforcement(make_settings):
    token = "secret-unified-token"
    client = build_app(make_settings, service_token=token)

    # Without token -> 401
    r_unauth = client.post("/v1/moderate/unified", json={"content": "Checking token"})
    assert r_unauth.status_code == 401
    assert r_unauth.get_json()["Data"]["code"] == "unauthorized"

    # With valid token -> 200
    r_auth = client.post("/v1/moderate/unified", json={"content": "Checking token"},
                         headers={"Authorization": f"Bearer {token}"})
    assert r_auth.status_code == 200


def test_unified_invalid_content_type_rejected(client):
    res = client.post("/v1/moderate/unified", json={
        "content": "Valid text",
        "content_type": "unregistered_surface"
    })
    assert res.status_code == 400
    assert res.get_json()["Data"]["code"] == "invalid_content_type"


def test_unified_revise_decision_handling(make_settings, sink):
    # When text is flagged for revision by gate rule
    scorer = FixedScorer(0.1)
    visual = FakeVisual(VisualResult(checked=True, label="safe", score=0.99), reject=False)
    client = build_app(make_settings, scorer=scorer, visual=visual, sink=sink)

    # "pay in crypto" triggers test.revise_payment gate rule
    res = client.post("/v1/moderate/unified", data={
        "content": "Please pay by crypto for the transaction.",
        "file": (io.BytesIO(png()), "safe.png"),
        "content_type": "article"
    }, content_type="multipart/form-data")

    assert res.status_code == 200
    data = res.get_json()["Data"]
    assert data["decision"] == "revise"
    assert data["Action"] == "reject"
    assert data["legacy_decision"] == "revise"



# --------------------------------------------------------------------------------
# An approval must not claim a check that never ran - including the combined one.
# --------------------------------------------------------------------------------

def test_combined_approval_does_not_claim_an_inspection_that_did_not_happen(make_settings, sink):
    """The both-halves path had its own hard-coded "Content approved for publication.".

    Cases 1 and 2 ask `_approved_message` what an approval is allowed to say; case 3 used to
    assert it. So with moderation switched off for the surface, the same content came back
    honest from /v1/moderate and "approved for publication" from /v1/moderate/unified - the
    one claim the rest of this service takes care never to make.
    """
    c = build_app(make_settings, sink=sink, admin_token="secret")
    assert c.put("/v1/admin/moderation-settings", json={"enabled": False, "level": "strict"},
                 headers={"X-Admin-Token": "secret"}).status_code == 200

    res = c.post("/v1/moderate/unified",
                 data={"content": "Bulk cotton yarn supplier, FSSAI certified.",
                       "content_type": "post",
                       "file": (io.BytesIO(png()), "sample.png")},
                 content_type="multipart/form-data")
    assert res.status_code == 200
    body = res.get_json()
    assert body["Data"]["Action"] == "approved"
    assert body["Message"] == ("Published without being checked: "
                               "moderation is switched off for this surface.")

    # And the combined payload carries what made that true. Without `decided_by` the honesty
    # check upstream has nothing to read, which is how the hard-coded message survived.
    assert body["Data"]["decided_by"] == "disabled"
    assert body["Data"]["checks"]["text"]["result"]["decided_by"] == "disabled"

    # The plain endpoint has always said it; the two must not disagree about the same content.
    plain = c.post("/v1/moderate", json={"content": "Bulk cotton yarn supplier, FSSAI certified.",
                                         "content_type": "post"}).get_json()
    assert plain["Message"] == body["Message"]
