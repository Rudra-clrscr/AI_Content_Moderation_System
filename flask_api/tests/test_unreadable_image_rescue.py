"""A picture the visual check CLEARED must not be refused over writing it could not read.

THE BUG THIS COMES FROM. Three ordinary uploads came back one after another with "there is
writing in this image that could not be read, so it cannot be checked. Upload a clearer
picture, or put the words in the post itself." The writing was not what had stopped them.

app/api.py refuses an image whose words OCR cannot read, unless the picture itself has been
looked at and cleared - the rescue that exists because refusing on unreadable writing alone was
turning away 13% of real listings over logos, number plates and watermarks. The condition read:

    seen_and_clear = (allow_unreadable_image_when_seen and looked.checked
                      and not looked.unsafe)

`decides_reject` had already said no three lines above - that IS cleared. `not looked.unsafe`
re-derived a much stricter test, and VisualResult.unsafe is not a verdict: it is true whenever
the BEST-MATCHING label happens to be an unsafe one, at any confidence, with no bar applied.
It answers True for a blank white image (top label `unrest` at 0.07), and measured over the
3,496 stored pictures it is true for 214 that decides_reject clears - 9.5% of everything filed
under `safe`, 17.2% of `minor`.

So the rescue was switched off for about one picture in ten, and those authors were told to
upload a clearer picture when nothing about their picture was unclear.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.clip import VisualResult  # noqa: E402
from app.media import MediaLimits  # noqa: E402
from app.model import ScoreResult  # noqa: E402
from app.ocr import PageText  # noqa: E402

from tests.test_ocr_readability import CONFIG, GarbageOcr  # noqa: E402


class StubVisual:
    """The real check's two answers, chosen separately - which is the whole point here.

    `result` is what it SAW and `reject` is whether it ACTS, and the bug was reading the first
    as if it were the second.
    """

    def __init__(self, result: VisualResult, reject: bool = False):
        self.result, self.reject = result, reject

    def classify(self, image):
        return self.result

    def decides_reject(self, result):
        return self.reject

    @property
    def status(self):
        return {"enabled": True, "ready": True, "provider": "Stub"}


class QuietScorer:
    version = "q-1"

    def score(self, text):
        return ScoreResult(0.01, "safe", {"safe": 0.99, "unsafe": 0.01}, self.version, 1.0)


def png(colour=(200, 200, 200)) -> bytes:
    Image = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    Image.new("RGB", (60, 30), colour).save(buf, "PNG")
    return buf.getvalue()


def upload(client, name="listing.png"):
    return client.post("/v1/moderate/media",
                       data={"file": (io.BytesIO(png()), name)},
                       content_type="multipart/form-data")


def build(make_settings, looked, reject=False, **media):
    """An upload whose writing OCR cannot read, and a visual check that saw `looked`."""
    pytest.importorskip("PIL")
    media.setdefault("allow_unreadable_image_when_seen", True)
    settings = make_settings(ocr=GarbageOcr(config=CONFIG), media=MediaLimits(**media),
                             visual=StubVisual(looked, reject))
    return create_app(settings, scorer=QuietScorer()).test_client()


# The shape of the bug: cleared, but the top label happens to be an unsafe one. On this corpus
# that is 214 of 3,496 pictures, and every one of them was refused over its writing.
CLEARED_BUT_TOP_LABEL_UNSAFE = VisualResult(
    checked=True, label="minor", category="minors", score=0.31, unsafe_score=0.44)

PLAINLY_SAFE = VisualResult(checked=True, label="safe", category="", score=0.88)


# ---- the bug itself ------------------------------------------------------------------

def test_a_cleared_picture_publishes_even_when_its_top_label_is_an_unsafe_one():
    """The three refused uploads. decides_reject cleared it, so the writing is the only thing
    left unknown and the rescue applies."""
    assert CLEARED_BUT_TOP_LABEL_UNSAFE.unsafe is True, "the fixture must have the bug's shape"


def test_the_refusal_no_longer_fires_on_a_cleared_picture(make_settings):
    client = build(make_settings, CLEARED_BUT_TOP_LABEL_UNSAFE, reject=False)
    r = upload(client)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["decision"] == "allow"


def test_a_plainly_safe_picture_was_never_affected_and_still_publishes(make_settings):
    """The half of the rescue that always worked, pinned so the fix did not narrow it."""
    r = upload(build(make_settings, PLAINLY_SAFE, reject=False))
    assert r.status_code == 200
    assert r.get_json()["decision"] == "allow"


def test_publishing_it_never_claims_the_writing_was_checked(make_settings):
    """The narrow claim the rescue is allowed to make: we looked at the picture, we could not
    read the words. Never a clean result."""
    body = upload(build(make_settings, CLEARED_BUT_TOP_LABEL_UNSAFE)).get_json()
    assert body["media"]["checked"] is False
    assert body["media"]["text_readable"] is False
    assert body["media"]["text_found"] is True
    assert body["media"]["visual_content_checked"] is True   # the PICTURE was looked at


# ---- and must not have opened anything ------------------------------------------------

def test_a_picture_the_check_refuses_is_still_refused(make_settings):
    """The rescue must not reach a picture decides_reject acts on - that refusal happens
    before any of this and is about the picture, not its writing."""
    r = upload(build(make_settings, CLEARED_BUT_TOP_LABEL_UNSAFE, reject=True))
    assert r.get_json()["decision"] == "reject"


def test_a_picture_nothing_looked_at_is_still_refused(make_settings):
    """The hole the whole branch exists to close: `checked` false means nobody looked, and
    unreadable writing on an unexamined picture must not publish."""
    r = upload(build(make_settings, VisualResult(checked=False)))
    assert r.status_code == 422
    assert r.get_json()["error"]["code"] == "media_unreadable"


def test_the_rescue_can_still_be_switched_off(make_settings):
    """allow_unreadable_image_when_seen is the operator's switch and still governs."""
    r = upload(build(make_settings, PLAINLY_SAFE, allow_unreadable_image_when_seen=False))
    assert r.status_code == 422
    assert r.get_json()["error"]["code"] == "media_unreadable"


def test_readable_writing_is_still_read_and_judged(make_settings):
    """Nothing here touches the ordinary path: when OCR CAN read the words they are moderated,
    cleared picture or not."""
    client = build(make_settings, CLEARED_BUT_TOP_LABEL_UNSAFE)
    client.application.extensions["moderation"].settings.ocr.page = \
        PageText("a perfectly ordinary caption about a sofa", 0.98, 1.0, 1)
    body = upload(client).get_json()
    assert body["media"]["text_readable"] is True
    # `checked: false` is the unreadable branch's own narrow claim; the readable path never
    # sets it, because the words really were read and judged.
    assert "checked" not in body["media"]
    assert body["decision"] == "allow"
