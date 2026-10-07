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
