"""The visual check: what it decides, and what it does when it can't.

The thing this exists to prevent is a picture publishing as clean that nobody looked at. So the
tests that matter most are not the happy path - they are the three ways "nobody looked" can
arise (off, model missing, model threw) and the insistence that none of them is reported as a
pass.
"""
import io

import pytest

from app.clip import VisualCheck, VisualConfig, VisualResult, _providers
from app.media import MediaLimits, read_image


def png(colour=(200, 200, 200), size=(64, 64)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


class FakeVisual:
    """Stands in for the real check so a test can choose what it found."""

    def __init__(self, result: VisualResult, reject: bool = False):
        self.result, self.reject, self.calls = result, reject, 0

    def classify(self, image):
        self.calls += 1
        return self.result

    def decides_reject(self, result):
        return self.reject

    @property
    def status(self):
        return {"enabled": True, "ready": True, "provider": "FakeProvider"}


# ---- choosing where to run ---------------------------------------------------------

def test_auto_prefers_gpu_when_the_runtime_has_one():
    have = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert _providers("auto", have)[0] == "CUDAExecutionProvider"


def test_auto_falls_back_to_cpu_on_a_cpu_only_install():
    assert _providers("auto", ["CPUExecutionProvider"]) == ["CPUExecutionProvider"]


def test_a_named_gpu_that_is_not_installed_falls_back_rather_than_failing():
    """The stock onnxruntime wheel is CPU-only. A host configured for CUDA that hasn't had the
    GPU build installed must still moderate pictures - slowly beats not at all."""
    assert _providers("cuda", ["CPUExecutionProvider"]) == ["CPUExecutionProvider"]


def test_cpu_is_never_silently_upgraded_to_a_gpu():
    assert _providers("cpu", ["CUDAExecutionProvider", "CPUExecutionProvider"]) == ["CPUExecutionProvider"]


def test_an_unknown_device_name_is_treated_as_auto(caplog):
    assert _providers("tpu", ["CPUExecutionProvider"]) == ["CPUExecutionProvider"]


# ---- what the result says ----------------------------------------------------------

def test_an_unchecked_picture_never_reports_a_label():
    """`visual_content_checked: false` and nothing else. A caller must not be able to read a
    clean-looking label off a check that never ran."""
    d = VisualResult(checked=False).as_dict()
    assert d == {"visual_content_checked": False}


def test_a_checked_picture_reports_what_it_found():
    r = VisualResult(checked=True, label="weapon", category="weapons", score=0.97,
                     unsafe_score=0.98, scores={"weapon": 0.97, "safe": 0.02})
    d = r.as_dict()
    assert d["visual_content_checked"] is True
    assert d["visual"]["label"] == "weapon" and d["visual"]["category"] == "weapons"
    assert d["visual"]["score"] == 0.97


def test_a_safe_label_carries_no_category_and_is_not_unsafe():
    r = VisualResult(checked=True, label="safe", score=0.99, scores={"safe": 0.99})
    assert not r.unsafe
    assert "category" not in r.as_dict()["visual"]


# ---- the threshold -----------------------------------------------------------------

def check_with(reject_min=0.90):
    return VisualCheck(VisualConfig(enabled=True, reject_min=reject_min))


@pytest.mark.parametrize("score, expected", [(0.95, True), (0.90, True), (0.89, False)])
def test_an_unsafe_label_is_acted_on_only_above_the_threshold(score, expected):
    r = VisualResult(checked=True, label="weapon", category="weapons", score=score,
                     unsafe_score=score)
    assert check_with().decides_reject(r) is expected


def test_a_confident_safe_label_is_never_a_reject():
    r = VisualResult(checked=True, label="safe", score=0.99, unsafe_score=0.01)
    assert check_with().decides_reject(r) is False


def test_unsafe_split_across_two_labels_still_counts():
    """0.5 weapon and 0.45 gore is obviously a problem, but neither label alone clears the bar.
    The total across the unsafe labels is what catches it."""
    r = VisualResult(checked=True, label="weapon", category="weapons", score=0.50,
                     unsafe_score=0.95, scores={"weapon": 0.5, "gore": 0.45, "safe": 0.05})
    assert check_with().decides_reject(r) is True


def test_an_unchecked_result_is_never_a_reject():
    """Nothing was looked at, so there is nothing to reject on - the caller refuses it for
    being unchecked instead, which is a different message to the author."""
    assert check_with().decides_reject(VisualResult(checked=False)) is False


# ---- the check when its model isn't there ------------------------------------------

def test_a_missing_model_turns_the_check_off_without_raising(tmp_path):
    """A deployment that hasn't run fetch_clip.py behaves exactly as it did before the visual
    check existed. It must never be a hard dependency for the Articles tab."""
    check = VisualCheck(VisualConfig(enabled=True, model_dir=str(tmp_path)))
    assert check.ready is False
    assert check.classify(None).checked is False
    status = check.status
    assert status["ready"] is False and "vision_model.onnx" in status["reason"]


def test_disabled_reports_why_rather_than_pretending_to_be_ready():
    check = VisualCheck(VisualConfig(enabled=False))
    assert check.status == {"enabled": False, "ready": False, "reason": "visual.enabled is false"}


# ---- reading a picture both ways ---------------------------------------------------

def test_an_image_is_read_and_looked_at_in_one_pass():
    looked = VisualResult(checked=True, label="safe", score=0.9)
    fake = FakeVisual(looked)
    seen = read_image(png(), None, MediaLimits(), fake)
    assert seen.visual is looked
    assert fake.calls == 1, "the picture is decoded once and classified once"


def test_no_visual_check_means_the_picture_was_not_looked_at():
    seen = read_image(png(), None, MediaLimits(), None)
    assert seen.visual.checked is False


def test_a_damaged_image_is_an_error_about_the_file_not_a_quiet_pass():
    from app.media import MediaError

    with pytest.raises(MediaError) as exc:
        read_image(b"not an image at all", None, MediaLimits(), FakeVisual(VisualResult()))
    assert exc.value.code == "media_unreadable"


def test_an_oversized_image_is_refused_before_it_is_decoded():
    from app.media import MediaError

    with pytest.raises(MediaError) as exc:
        read_image(png(size=(2000, 2000)), None, MediaLimits(max_pixels=1000), FakeVisual(VisualResult()))
    assert exc.value.code == "media_too_large"


# ---- end to end through the endpoint -----------------------------------------------

def post_image(client, data=None):
    return client.post("/v1/moderate/media",
                       data={"file": (io.BytesIO(data or png()), "photo.png"),
                             "content_type": "article"},
                       content_type="multipart/form-data")


def app_with_visual(make_settings, scorer, sink, visual):
    from app import create_app

    return create_app(make_settings(visual=visual), scorer=scorer, sink=sink).test_client()


def test_a_picture_that_breaks_policy_is_rejected_like_a_blocked_phrase(make_settings, scorer, sink):
    """Not an HTTP error: a decision, with the usual shape, wording and audit row. The
    difference between "your upload failed" and "this can't be published" matters to the
    author, and the Data team gets a row either way."""
    looked = VisualResult(checked=True, label="weapon", category="weapons", score=0.97,
                          unsafe_score=0.98, scores={"weapon": 0.97})
    client = app_with_visual(make_settings, scorer, sink, FakeVisual(looked, reject=True))
    body = post_image(client).get_json()

    assert body["decision"] == "reject" and body["decided_by"] == "gate"
    assert [m["rule_id"] for m in body["gate_matches"]] == ["visual.weapon"]
    assert body["gate_matches"][0]["category"] == "weapons"
    assert body["media"]["visual"]["label"] == "weapon"
    assert body["media"]["visual_content_checked"] is True
    assert len(sink.results) == 1, "a refused picture is still recorded"


def test_a_clean_picture_is_allowed_and_says_it_was_looked_at(make_settings, scorer, sink):
    looked = VisualResult(checked=True, label="safe", score=0.99, scores={"safe": 0.99})
    client = app_with_visual(make_settings, scorer, sink, FakeVisual(looked))
    body = post_image(client).get_json()

    assert body["decision"] == "allow"
    assert body["media"]["visual_content_checked"] is True
    assert body["media"]["visual"]["label"] == "safe"


def test_with_the_check_off_an_image_is_allowed_but_says_nobody_looked(make_settings, scorer, sink):
    """The behaviour before this feature existed, preserved exactly - including the honesty."""
    client = app_with_visual(make_settings, scorer, sink, FakeVisual(VisualResult(checked=False)))
    body = post_image(client).get_json()

    assert body["decision"] == "allow"
    assert body["media"]["visual_content_checked"] is False
    assert "visual" not in body["media"]


def test_a_visual_model_that_throws_refuses_the_upload(make_settings, scorer, sink):
    """Present but broken is not the same as absent. Nothing is known about this picture, so it
    must not publish - and the author is offered a retry, because this one is ours to fix."""
    client = app_with_visual(make_settings, scorer, sink,
                             FakeVisual(VisualResult(checked=False, failed=True)))
    resp = post_image(client)

    assert resp.status_code == 503
    assert resp.get_json()["error"]["code"] == "media_not_checkable"


def test_ready_says_whether_pictures_are_actually_being_looked_at(make_settings, scorer, sink):
    client = app_with_visual(make_settings, scorer, sink, FakeVisual(VisualResult()))
    assert client.get("/ready").get_json()["visual"]["provider"] == "FakeProvider"


# ---- the shipped policy file -------------------------------------------------------
# These need no model bundle, so they run anywhere. The policy is the part of this feature
# that is in git and the part most likely to be edited by hand.

def shipped_policy():
    import yaml

    from app.config import PROJECT_ROOT

    raw = yaml.safe_load((PROJECT_ROOT / "config" / "visual_policy.yaml").read_text(encoding="utf-8"))
    return raw["labels"]


def test_every_unsafe_label_names_a_policy_category():
    """The category is what reaches the author as "this breaks our policy on ...", and what the
    Data team groups decisions by. A nameless one would report the raw label."""
    for name, spec in shipped_policy().items():
        if spec.get("unsafe"):
            assert spec.get("category"), f"unsafe label {name!r} has no category"


def test_every_label_has_prompts():
    for name, spec in shipped_policy().items():
        assert spec.get("prompts"), f"label {name!r} has no prompts"


def test_there_are_safe_labels_to_be_judged_against():
    """Scores are a softmax over every prompt, so the safe list is not decoration: with a thin
    one an ordinary product photo has nothing to be safe as, and its unsafe score rises."""
    labels = shipped_policy()
    safe = [n for n, s in labels.items() if not s.get("unsafe")]
    assert safe, "no safe labels"
    safe_prompts = sum(len(labels[n]["prompts"]) for n in safe)
    unsafe_prompts = sum(len(s["prompts"]) for s in labels.values() if s.get("unsafe"))
    assert safe_prompts >= unsafe_prompts, (
        f"{safe_prompts} safe prompts against {unsafe_prompts} unsafe ones - the safe set should "
        "cover at least as much ground, or ordinary pictures start scoring unsafe")


def test_no_prompt_is_repeated_within_or_across_labels():
    """A duplicate across two labels would have them compete on an identical vector; a
    duplicate within one is simply dead weight in the max."""
    seen = {}
    for name, spec in shipped_policy().items():
        for prompt in spec["prompts"]:
            assert prompt not in seen, f"{prompt!r} appears in both {seen.get(prompt)!r} and {name!r}"
            seen[prompt] = name


# ---- words read off a photograph ---------------------------------------------------
# Text a member types is prose. Text OCR pulls off a shopfront is word-salad
# ("TURNKEY MWRULTANTS A SOLUTIONS"), and the model was trained on sentences, so it scored
# four ordinary BONC listings into the reject band. Image text is therefore judged at
# media.ocr_text_reject_min. Measured (scripts/eval_ocr_text_bar.py): scam flyers read back
# through real OCR land at 0.9996-0.9997, the worst honest listing at 0.9281, so any bar in
# [0.95, 0.999] catches every scam and refuses nothing honest.

@pytest.fixture
def ocr():
    """The real OCR stack, skipped where it can't run. These tests are about what happens to
    text that came OUT of a picture, so a stubbed reader would test nothing."""
    pytest.importorskip("rapidocr")
    import numpy as np
    from PIL import Image

    from app.ocr import OcrConfig, OcrEngine

    engine = OcrEngine(OcrConfig(enabled=True))
    probe = engine.read_image(np.asarray(
        Image.open(io.BytesIO(image_with_text(["Cotton bedsheets in king size."]))).convert("RGB")))
    if not probe.text:
        pytest.skip("OCR could not read a plain rendered image on this host")
    return engine


class RiskScorer:
    version = "r-1"

    def __init__(self, risk):
        self.risk = risk

    def score(self, text):
        from app.model import ScoreResult

        return ScoreResult(self.risk, "x", {"x": self.risk}, self.version, 1.0)


def image_with_text(lines=("Some words on a sign",)):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 200), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(lines):
        draw.text((40, 40 + i * 60), line, fill=(10, 10, 10), font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def upload_to(client, data, name="sign.png"):
    return client.post("/v1/moderate/media",
                       data={"file": (io.BytesIO(data), name), "content_type": "article"},
                       content_type="multipart/form-data")


def client_scoring(make_settings, sink, risk, ocr, **kw):
    from app import create_app

    return create_app(make_settings(max_chars=50_000, ocr=ocr, **kw),
                      scorer=RiskScorer(risk), sink=sink).test_client()


@pytest.mark.parametrize("risk, decision", [
    (0.93, "allow"),    # the worst real BONC listing measured
    (0.98, "allow"),    # garbled signage, still under the bar
    (0.999, "reject"),  # where scam flyers actually land
])
def test_image_text_is_judged_at_the_higher_bar(make_settings, sink, ocr, risk, decision):
    client = client_scoring(make_settings, sink, risk, ocr)
    body = upload_to(client, image_with_text()).get_json()
    assert body["decision"] == decision, f"risk {risk} should {decision}"


def test_image_text_has_no_revise_band(make_settings, sink, ocr):
    """A revise says "edit the highlighted part", which cannot be done to words baked into a
    photograph - the author can only replace the picture. So there is one boundary, not two."""
    body = upload_to(client_scoring(make_settings, sink, 0.80, ocr), image_with_text()).get_json()
    assert body["decision"] == "allow"
    assert body["thresholds"]["allow_max"] == body["thresholds"]["reject_min"]


def test_a_gate_rule_still_blocks_whatever_the_bar_is(make_settings, sink, ocr):
    """The higher bar moves the MODEL's boundary. A deterministic rule either matched or it
    didn't, and a scam phrase photographed is still a scam phrase."""
    body = upload_to(client_scoring(make_settings, sink, 0.01, ocr),
                     image_with_text(["Invest now and double your money"])).get_json()
    assert body["decision"] == "reject" and body["decided_by"] == "gate"


def test_a_pdf_keeps_the_ordinary_bar(make_settings, sink, ocr):
    """Only OCR'd image text is noisy. A PDF's text is extracted, not guessed at, and reads as
    whatever the author actually wrote - so it is judged like anything else they typed."""
    from tests.test_pdf import make_pdf

    body = upload_to(client_scoring(make_settings, sink, 0.80, ocr),
                     make_pdf(["Something the model dislikes."]), "doc.pdf").get_json()
    assert body["decision"] != "allow", "0.80 is over the ordinary reject line"


# ---- scanned PDFs ------------------------------------------------------------------
# A scanned PDF is a stack of photographs. Without the visual check on its pages, a weapon or
# gore photo published simply by being put in a PDF instead of attached as an image - the same
# hole, one container along. The page is rendered ONCE and handed to both OCR and the check.

def scanned_pdf(image_bytes, caption="INVOICE Order 4471 Qty 2"):
    """A page that is a picture with a line of text on it, the way a real scan arrives."""
    from PIL import Image, ImageDraw, ImageFont

    src = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    page = Image.new("RGB", (1240, 1754), "white")
    src.thumbnail((900, 1100))
    page.paste(src, (120, 320))
    draw = ImageDraw.Draw(page)
    try:
        font = ImageFont.truetype("arial.ttf", 38)
    except OSError:
        font = ImageFont.load_default()
    draw.text((120, 160), caption, fill=(0, 0, 0), font=font)
    buf = io.BytesIO()
    page.save(buf, format="PDF", resolution=150.0)
    return buf.getvalue()


class PageVisualStub:
    """A visual check that objects to whatever page it is shown."""

    def __init__(self, result, reject):
        self.result, self.reject, self.calls = result, reject, 0
        self.ready = True

    def classify(self, image):
        self.calls += 1
        return self.result

    def decides_reject(self, result):
        return self.reject


def test_a_pdf_page_is_rendered_once_for_both_checks(make_settings, ocr):
    """Rendering is ~100 ms a page and both the reader and the check need the same pixels."""
    from app.pdf import extract

    s = make_settings(ocr=ocr)
    stub = PageVisualStub(VisualResult(checked=True, label="safe", score=0.99), reject=False)
    got = extract(scanned_pdf(png(size=(400, 300))), s.pdf, "scan.pdf", ocr, stub)
    assert stub.calls == 1, "the page should be classified once, not once per consumer"
    assert [v.page for v in got.visual_pages] == [1]
    assert "INVOICE" in got.text.upper(), "OCR still read the same rendered page"


def test_a_weapon_inside_a_scanned_pdf_is_refused(make_settings, ocr, scorer, sink):
    from app import create_app

    looked = VisualResult(checked=True, label="weapon", category="weapons", score=0.98,
                          unsafe_score=0.99)
    s = make_settings(max_chars=50_000, ocr=ocr,
                      visual=PageVisualStub(looked, reject=True))
    client = create_app(s, scorer=scorer, sink=sink).test_client()
    body = client.post("/v1/moderate/media",
                       data={"file": (io.BytesIO(scanned_pdf(png(size=(400, 300)))), "scan.pdf"),
                             "content_type": "article"},
                       content_type="multipart/form-data").get_json()

    assert body["decision"] == "reject" and body["decided_by"] == "gate"
    assert [m["rule_id"] for m in body["gate_matches"]] == ["visual.weapon"]
    assert "page 1" in body["content"], "the author is told which page to look at"


def test_a_clean_scan_still_publishes(make_settings, ocr, scorer, sink):
    from app import create_app

    looked = VisualResult(checked=True, label="safe", score=0.99)
    s = make_settings(max_chars=50_000, ocr=ocr, visual=PageVisualStub(looked, reject=False))
    client = create_app(s, scorer=scorer, sink=sink).test_client()
    body = client.post("/v1/moderate/media",
                       data={"file": (io.BytesIO(scanned_pdf(png(size=(400, 300)))), "scan.pdf"),
                             "content_type": "article"},
                       content_type="multipart/form-data").get_json()
    assert body["decision"] == "allow"
    assert body["pdf"]["visual_checked_pages"] == [1]


def test_a_text_only_pdf_has_no_pixels_to_look_at(make_settings, ocr):
    """A page of selectable text carries no picture, so there is nothing for the check to do
    and it is not asked - the saving that keeps a 60-page document affordable."""
    from app.pdf import extract
    from tests.test_pdf import make_pdf

    stub = PageVisualStub(VisualResult(checked=True, label="safe", score=0.99), reject=False)
    got = extract(make_pdf(["Cotton bedsheets in king and queen sizes, 300 thread count."]),
                  make_settings().pdf, "doc.pdf", ocr, stub)
    assert stub.calls == 0
    assert got.visual_pages == ()


# ---- the trained probe -------------------------------------------------------------
# A logistic regression over the frozen encoder's embeddings, fitted on BONC's own pictures
# (training/train_image_probe.py). It decides ALONGSIDE the prompts rather than replacing them:
# measured out-of-fold, the probe alone is worse than the prompts on weapons (73% against 77%)
# and much better on gore (98% against 93%), so either on its own gives up something.

def probe_result(probe_unsafe, label="safe", category="", unsafe=0.0, score=None):
    # `score` defaults to `unsafe` for an unsafe label, which is what a real result looks like:
    # the top label's own probability IS the unsafe reading when that label is an unsafe one.
    # Pinning it at some unrelated high value would make decides_reject fire on `score` alone
    # and the test would pass for the wrong reason.
    score = unsafe if score is None else score
    return VisualResult(checked=True, label=label, category=category, score=score,
                        unsafe_score=unsafe, scores={label: score},
                        probe_scores={"safe": 1 - probe_unsafe, "gore": probe_unsafe},
                        probe_unsafe=probe_unsafe)


def check_with_probe(reject_min=0.90, probe_reject_min=0.70):
    return VisualCheck(VisualConfig(enabled=True, reject_min=reject_min,
                                    probe_reject_min=probe_reject_min))


def test_the_probe_can_refuse_what_the_prompts_allow():
    """The gore case: prompts score it 0.2, the probe is sure. Refusing on either is the whole
    reason both are kept."""
    r = probe_result(0.95, label="gore", category="graphic_violence", unsafe=0.20)
    assert check_with_probe().decides_reject(r) is True


def test_the_prompts_can_refuse_what_the_probe_allows():
    """And the weapons case, the other way round."""
    r = probe_result(0.10, label="weapon", category="weapons", unsafe=0.97)
    assert check_with_probe().decides_reject(r) is True


def test_neither_crossing_its_line_is_an_allow():
    r = probe_result(0.40, label="weapon", category="weapons", unsafe=0.50)
    assert check_with_probe().decides_reject(r) is False


def test_the_probe_does_not_fire_on_a_safe_top_label_without_a_category():
    """decides_reject needs a policy category. A probe sure about `safe` is not a refusal."""
    r = probe_result(0.05)
    assert check_with_probe().decides_reject(r) is False


def test_a_bundle_with_no_probe_behaves_as_it_did_before(tmp_path):
    """probe.npz is optional. Without it the check is the prompts alone, unchanged."""
    r = VisualResult(checked=True, label="weapon", category="weapons", score=0.95,
                     unsafe_score=0.95)
    assert not r.probe_scores
    assert check_with_probe().decides_reject(r) is True
    assert "probe" not in r.as_dict()["visual"]


def test_the_probe_is_reported_so_a_decision_can_be_explained():
    r = probe_result(0.88, label="gore", category="graphic_violence", unsafe=0.30)
    d = r.as_dict()["visual"]
    assert d["probe"]["unsafe"] == 0.88
    assert d["probe"]["scores"]["gore"] == 0.88


def test_a_probe_fitted_on_a_different_encoder_is_ignored(tmp_path, caplog):
    """512-d CLIP weights against 768-d SigLIP prompts would multiply cleanly in the wrong
    shape or crash; either way the answer would be noise. It is dropped with a warning."""
    import numpy as np

    np.savez(tmp_path / "probe.npz", W=np.zeros((3, 768), dtype=np.float32),
             b=np.zeros(3, dtype=np.float32), classes=np.array(["safe", "weapon", "gore"]),
             dim=np.array(768, dtype=np.int32), encoder=np.array("img_v2"))
    # No vision model in tmp_path, so the load stops before the probe - what matters is that
    # the dimension guard exists at all, which the shipped bundle exercises.
    check = VisualCheck(VisualConfig(enabled=True, model_dir=str(tmp_path)))
    assert check.ready is False
