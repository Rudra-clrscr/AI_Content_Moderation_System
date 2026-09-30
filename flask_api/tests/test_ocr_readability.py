"""Did OCR read the picture, or only prove there is writing it can't read?

Detection finds where the words are; recognition turns them into characters. RapidOCR bundles
a Latin and a Chinese recognition model, so a Hindi picture comes back with every line located
and near-nothing recognised. QA 2026-09-30 posted a PNG reading "तुम सब बेवकूफ हो, मर जाओ /
मैं तुम्हें जान से मार दूंगा" and got `decision: allow` with the extracted text "aaohh y" — the
threat published, and the record said the attachment was clean.

Confidence alone cannot catch it. Measured here on rendered 48pt text, per detected line:

    English  "Cotton saris wholesale" / "Surat, Gujarat - since 1994"   25-28 chars, conf 0.99
    Hindi    "सूती साड़ियाँ थोक में"                                       4 chars, conf 0.76
    Hindi    "तुम सब बेवकूफ हो, मर जाओ / मैं तुम्हें जान से मार दूंगा"       1.5 chars, conf 0.62

Both Hindi cases clear ocr.min_confidence (0.5). The yield per detected line is what separates
them, and `trust_short_confidence` keeps genuinely short signage ("SALE") readable.
"""
import io

import pytest

from app import create_app
from app.media import MediaLimits
from app.model import ScoreResult
from app.ocr import OcrConfig, PageText

CONFIG = OcrConfig(enabled=True)


# --------------------------------------------------------------- the rule itself

@pytest.mark.parametrize("page,expected,why", [
    (PageText("Cotton saris wholesale Surat, Gujarat - since 1994", 0.990, 1.0, 2), True,
     "English, read cleanly"),
    (PageText("上 亚", 0.623, 1.0, 2), False,
     "Hindi threat through a Latin recogniser: 1.5 chars a line"),
    (PageText("TURT", 0.760, 1.0, 1), False,
     "Hindi signage through a Latin recogniser: 4 chars, not confident"),
    (PageText("SALE", 0.995, 1.0, 1), True,
     "genuinely short signage, recognised with near-certainty"),
    (PageText("", 0.0, 1.0, 0), False,
     "nothing recovered at all"),
    (PageText("a plausible line of text here", 0.41, 1.0, 1), False,
     "below the confidence floor however much it returned"),
])
def test_reads_as_text(page, expected, why):
    assert CONFIG.reads_as_text(page) is expected, why


@pytest.mark.parametrize("page,expected", [
    (PageText("", 0.0, 1.0, 0), False),      # a photo with nothing written on it
    (PageText("", 0.0, 1.0, 3), True),       # lines found, none recognised
    (PageText("SALE", 0.99, 1.0, 1), True),
])
def test_has_text_separates_no_writing_from_unread_writing(page, expected):
    assert page.has_text is expected


def test_trust_short_confidence_is_validated():
    with pytest.raises(ValueError):
        OcrConfig(trust_short_confidence=1.4)


# --------------------------------------------------------------- through the endpoint

class GarbageOcr:
    """OCR as it behaved on the reported Devanagari PNG: two lines found, three characters back."""

    def __init__(self, page=None, config=CONFIG):
        self.config = config
        self.page = page or PageText("上 亚", 0.623, 12.0, 2)

    enabled = True

    def read_image(self, array):
        return self.page


class KeywordScorer:
    version = "k-1"

    def score(self, text):
        r = 0.99 if "burn" in text.lower() else 0.01
        return ScoreResult(r, "unsafe" if r > 0.5 else "safe", {"safe": 1 - r, "unsafe": r}, self.version, 1.0)


def png(colour=(200, 200, 200)) -> bytes:
    Image = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    Image.new("RGB", (60, 30), colour).save(buf, "PNG")
    return buf.getvalue()


def upload(client, data, name="flyer.png"):
    return client.post("/v1/moderate/media", data={"file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data")


def build(make_settings, **media):
    pytest.importorskip("PIL")
    settings = make_settings(ocr=GarbageOcr(), media=MediaLimits(**media))
    return create_app(settings, scorer=KeywordScorer()).test_client()


def test_unreadable_image_is_refused_not_allowed(make_settings):
    """The reported bug. An allow here publishes a threat nobody read."""
    r = upload(build(make_settings), png())
    assert r.status_code == 422
    body = r.get_json()
    assert body["error"]["code"] == "media_unreadable"
    assert body["error"]["text_readable"] is False


def test_the_refusal_message_tells_the_author_what_to_do(make_settings):
    message = upload(build(make_settings), png()).get_json()["error"]["message"]
    assert "could not be read" in message
    assert "clearer picture" in message


def test_unreadable_image_can_be_published_but_never_as_checked(make_settings):
    """With the flag on, the upload goes through - and the record says it wasn't checked,
    the same way unwatched video does. It must not look like a clean result."""
    r = upload(build(make_settings, allow_unreadable_image=True), png())
    assert r.status_code == 200
    media = r.get_json()["media"]
    assert media["checked"] is False
    assert media["text_readable"] is False
    assert media["text_found"] is True                 # there IS writing, we just can't read it
    assert media["visual_content_checked"] is False


def test_a_photo_with_no_writing_is_still_allowed(make_settings):
    """The asymmetry is deliberate: no text at all is ordinary for a product photo."""
    client = build(make_settings)
    client.application.extensions["moderation"].settings.ocr.page = PageText("", 0.0, 1.0, 0)
    body = upload(client, png()).get_json()
    assert body["decision"] == "allow"
    assert body["media"]["text_found"] is False


def test_readable_text_in_an_image_is_moderated_normally(make_settings):
    client = build(make_settings)
    client.application.extensions["moderation"].settings.ocr.page = \
        PageText("We will burn your shop down, we know where you live", 0.98, 1.0, 1)
    body = upload(client, png()).get_json()
    assert body["decision"] == "reject"
    assert body["media"]["text_readable"] is True


class BrokenOcr:
    """OCR that raises. The picture is still refused, but for the right reason."""
    enabled = True
    config = CONFIG

    def read_image(self, array):
        raise RuntimeError("engine exploded")


def test_ocr_failure_is_refused_as_our_problem_not_the_authors(make_settings):
    pytest.importorskip("PIL")
    settings = make_settings(ocr=BrokenOcr(), media=MediaLimits())
    client = create_app(settings, scorer=KeywordScorer()).test_client()
    r = upload(client, png())
    assert r.status_code == 503                       # retryable, not a verdict on the file
    error = r.get_json()["error"]
    assert error["code"] == "media_not_checkable"
    assert "try again" in error["message"].lower()
    assert "could not be read" not in error["message"]   # we never saw whether there was text
