"""Attachments on an article: images, video and the kind-detection that routes them.

The rules differ by kind on purpose (see app/media.py): a PDF page with no text is refused,
an image with no text is allowed, and video is refused unless the deployment says otherwise.
These tests pin those differences, because getting them the same way round would either break
honest sellers or open the bypass this whole feature exists to close.
"""
import io

import pytest

from app import create_app
from app.media import MediaLimits, detect_kind
from app.model import ScoreResult
from tests.test_pdf import make_pdf, make_scanned_pdf


def png(width=40, height=20, colour=(200, 200, 200)) -> bytes:
    Image = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, "PNG")
    return buf.getvalue()


def text_image(lines, fmt="PNG") -> bytes:
    """A picture of some words — how a scam actually arrives as an attachment."""
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1100, 90 + 60 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 34)
    except OSError:                                    # pragma: no cover - host dependent
        font = ImageFont.load_default()
    for i, line in enumerate(lines):
        draw.text((60, 45 + i * 60), line, fill=(20, 20, 20), font=font)
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def fake_video() -> bytes:
    return b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"\x00" * 200


# ---- kind detection is by bytes, never by name ---------------------------------

@pytest.mark.parametrize("data,expected", [
    (b"%PDF-1.4 ...", "pdf"),
    (b"\x89PNG\r\n\x1a\n rest", "image"),
    (b"\xff\xd8\xff\xe0 JFIF", "image"),
    (b"GIF89a....", "image"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image"),
    (b"RIFF\x00\x00\x00\x00AVI LIST", "video"),
    (b"\x1a\x45\xdf\xa3 matroska", "video"),
    (b"\x00\x00\x00 ftypisom", "video"),
    (b"MZ\x90\x00 an executable", "unknown"),
    (b"plain text", "unknown"),
])
def test_detect_kind(data, expected):
    assert detect_kind(data, "anything.bin") == expected


def test_a_pdf_named_png_is_still_a_pdf():
    assert detect_kind(make_pdf(["Cotton bedsheets in all sizes."]), "photo.png") == "pdf"


def test_an_executable_named_jpg_is_refused_as_unknown():
    assert detect_kind(b"MZ\x90\x00\x03", "holiday.jpg") == "unknown"


# ---- the endpoint ---------------------------------------------------------------

class KeywordScorer:
    version = "m-1"

    def score(self, text):
        # 0.999, not an arbitrary "clearly bad" number: text OCR'd out of a picture is judged
        # at media.ocr_text_reject_min, and this stands in for the live model, which scores
        # scam flyers read back through real OCR at 0.9996-0.9997 while the worst honest BONC
        # listing reaches 0.9281 (scripts/eval_ocr_text_bar.py). A stub below the bar would be
        # testing a model that does not exist.
        r = 0.999 if "earn" in text.lower() else 0.01
        return ScoreResult(r, "x", {"x": r}, self.version, 1.0)


def client(make_settings, **kw):
    return create_app(make_settings(max_chars=50_000, **kw), scorer=KeywordScorer()).test_client()


def upload(c, data, name="file.bin", path="/v1/moderate/media", **form):
    return c.post(path, data={"file": (io.BytesIO(data), name), **form},
                  content_type="multipart/form-data")


@pytest.fixture(scope="module")
def ocr():
    pytest.importorskip("rapidocr")
    from app.ocr import OcrConfig, OcrEngine
    engine = OcrEngine(OcrConfig(enabled=True))
    probe = engine.read_image(_as_array(text_image(["Cotton bedsheets in king size."])))
    if not probe.text:
        pytest.skip("OCR could not read a plain rendered image on this host")
    return engine


def _as_array(data: bytes):
    import numpy as np
    from PIL import Image
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))


def test_scam_in_an_attached_image_is_caught(make_settings, ocr):
    """The bypass this exists to close: a clean article with the scam in the picture."""
    body = upload(client(make_settings, ocr=ocr),
                  text_image(["Earn 50000 per week from home.",
                              "Pay a small registration fee to join."]), "flyer.png").get_json()
    assert body["decision"] == "reject"
    assert body["media"]["kind"] == "image" and body["media"]["text_found"] is True


def test_photo_without_text_is_allowed_but_says_it_was_not_looked_at(make_settings, ocr):
    """A product photo carries no words. Refusing those would break the feature for every
    honest seller — but the caller must be able to see the picture was never classified."""
    body = upload(client(make_settings, ocr=ocr), png(), "product.png").get_json()
    assert body["decision"] == "allow"
    assert body["media"]["text_found"] is False
    assert body["media"]["visual_content_checked"] is False


def test_clean_text_in_an_image_is_allowed(make_settings, ocr):
    body = upload(client(make_settings, ocr=ocr),
                  text_image(["Cotton bedsheets in king and queen sizes."]), "sizes.png").get_json()
    assert body["decision"] == "allow" and body["media"]["text_found"] is True


def test_video_is_refused_when_the_flag_is_off(make_settings):
    """The code default, and what a deployment gets back the moment it decides footage must
    not go out unwatched."""
    r = upload(client(make_settings), fake_video(), "clip.mp4")
    assert r.status_code == 422
    assert r.get_json()["error"]["code"] == "media_not_checkable"


def test_video_can_be_allowed_unchecked_when_that_is_the_policy(make_settings):
    c = client(make_settings, media=MediaLimits(allow_unchecked_video=True))
    body = upload(c, fake_video(), "clip.mp4").get_json()
    assert body["decision"] == "allow"
    assert body["media"]["checked"] is False and body["media"]["visual_content_checked"] is False


def test_shipped_policy_publishes_video_unchecked_and_says_so():
    """There is no visual model yet, so the platform publishes footage rather than close the
    Videos tab. Pinned here so the day that changes it is a deliberate edit, not a drift — and
    so nobody reads an `allow` on a video as "we looked at it"."""
    from app.config import load_settings
    assert load_settings().media.allow_unchecked_video is True


def test_unknown_type_is_refused(make_settings):
    r = upload(client(make_settings), b"MZ\x90\x00 executable", "setup.exe")
    assert r.status_code == 422 and r.get_json()["error"]["code"] == "media_unsupported"


def test_oversized_image_is_refused(make_settings):
    c = client(make_settings, media=MediaLimits(max_bytes=50))
    r = upload(c, png(200, 200), "big.png")
    assert r.status_code == 413 and r.get_json()["error"]["code"] == "media_too_large"


def test_pdf_still_works_through_the_media_path(make_settings):
    body = upload(client(make_settings), make_pdf(["Cotton bedsheets in king and queen sizes."]),
                  "cat.pdf").get_json()
    assert body["decision"] == "allow" and body["media"]["kind"] == "pdf"
    assert body["pdf"]["page_count"] == 1          # the original contract's block is still there


def test_the_old_pdf_path_is_unchanged(make_settings):
    body = upload(client(make_settings), make_pdf(["Cotton bedsheets in king and queen sizes."]),
                  "cat.pdf", path="/v1/moderate/pdf").get_json()
    assert body["decision"] == "allow" and body["pdf"]["page_count"] == 1


def test_scanned_pdf_attachment_still_fails_closed(make_settings):
    """The difference that matters: no text in a PDF page is refused, no text in a photo is not."""
    from app.pdf import PdfLimits
    c = client(make_settings, pdf=PdfLimits(), media=MediaLimits())
    r = upload(c, make_scanned_pdf(["Nothing readable"], scale=0.02), "scan.pdf")
    assert r.status_code == 422 and r.get_json()["error"]["code"] == "pdf_text_not_extractable"


def test_decisions_for_files_are_recorded(make_settings, ocr):
    """Every attachment gets its own audit row, including the ones with no text."""
    from app.sinks import MemorySink
    sink = MemorySink()
    s = make_settings(max_chars=50_000, ocr=ocr)
    c = create_app(s, scorer=KeywordScorer(), sink=sink).test_client()
    upload(c, png(), "product.png")
    upload(c, text_image(["Earn 50000 per week from home."]), "flyer.png")
    assert [r["decision"] for r in sink.results] == ["allow", "reject"]
