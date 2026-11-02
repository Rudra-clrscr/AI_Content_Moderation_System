"""Attachments on an article: images, PDFs, video and the kind-detection that routes them.

The rules differ by kind on purpose (see app/media.py): an image with no text is allowed,
video is refused unless the deployment says otherwise, and a PDF is now published unread.
These tests pin those differences, because getting them the same way round would either break
honest sellers or open the bypass this whole feature exists to close.
"""
import io

import pytest

from app import create_app
from app.media import MediaLimits, detect_kind
from app.model import ScoreResult


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


def pdf_bytes(line="Cotton bedsheets in king and queen sizes.") -> bytes:
    """A minimal valid PDF. Nothing reads one any more, so it only has to be recognised as a
    PDF by its leading bytes - the full builder went with tests/test_pdf.py."""
    text = f"BT /F1 12 Tf 60 780 Td ({line}) Tj ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R >>",
            "\n".join([f"<< /Length {len(text)} >>", "stream", text, "endstream"])]
    parts, offs = ["%PDF-1.4"], []
    out = len(parts[0]) + 1
    for i, o in enumerate(objs, 1):
        offs.append(out)
        body = f"{i} 0 obj\n{o}\nendobj"
        parts.append(body)
        out += len(body) + 1
    parts.append(f"xref\n0 {len(objs) + 1}")
    parts.append("0000000000 65535 f ")
    parts.extend(f"{o:010d} 00000 n " for o in offs)
    parts.append(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{out}\n%%EOF")
    return ("\n".join(parts) + "\n").encode("latin-1")


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
    assert detect_kind(pdf_bytes(), "photo.png") == "pdf"


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
    assert body["decision"] == "approved"
    assert body["media"]["text_found"] is False
    assert body["media"]["visual_content_checked"] is False


def test_clean_text_in_an_image_is_allowed(make_settings, ocr):
    body = upload(client(make_settings, ocr=ocr),
                  text_image(["Cotton bedsheets in king and queen sizes."]), "sizes.png").get_json()
    assert body["decision"] == "approved" and body["media"]["text_found"] is True


def test_video_is_refused_when_the_flag_is_off(make_settings):
    """The code default, and what a deployment gets back the moment it decides footage must
    not go out unwatched."""
    r = upload(client(make_settings), fake_video(), "clip.mp4")
    assert r.status_code == 422
    assert r.get_json()["error"]["code"] == "media_not_checkable"


def test_video_can_be_allowed_unchecked_when_that_is_the_policy(make_settings):
    c = client(make_settings, media=MediaLimits(allow_unchecked_video=True))
    body = upload(c, fake_video(), "clip.mp4").get_json()
    assert body["decision"] == "approved"
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


def test_a_pdf_is_published_unread(make_settings):
    """PDF checking was taken out while the scanned path is reworked. What is left is the video
    rule with the opposite default: publish it, and say plainly that nothing read it."""
    body = upload(client(make_settings), pdf_bytes(), "cat.pdf").get_json()
    assert body["decision"] == "approved"
    assert body["media"]["kind"] == "pdf"
    # The honesty of the record is the whole of what is left. An allow nothing looked at must
    # never be readable as a clean result.
    assert body["media"]["checked"] is False
    assert body["media"]["visual_content_checked"] is False
    assert body["media"]["text_found"] is False
    # Including in the envelope's summary line, which is all a .NET caller may keep.
    assert body["Message"] == "Published, but nothing in this file could be read or checked."
    # The old contract's page map is gone with the extractor that filled it.
    assert "pdf" not in body


def test_a_pdf_can_be_refused_instead(make_settings):
    c = client(make_settings, media=MediaLimits(allow_unchecked_pdf=False))
    r = upload(c, pdf_bytes(), "cat.pdf")
    assert r.status_code == 422
    assert r.get_json()["error"]["code"] == "media_not_checkable"


def test_the_old_pdf_route_is_gone(make_settings):
    """`/v1/moderate/pdf` was the original path. It went with the extractor; callers use
    /v1/moderate/media, which still accepts a PDF.

    405 rather than 404 because the URL now matches `GET /v1/moderate/<request_id>` - "pdf"
    reads as a request id - and that rule takes no POST. Worth pinning either way: what must
    not happen is a POST there quietly succeeding, or 500ing on a lookup of a thing called
    "pdf"."""
    r = upload(client(make_settings), pdf_bytes(), "cat.pdf", path="/v1/moderate/pdf")
    assert r.status_code == 405


def test_an_unread_pdf_is_still_recorded(make_settings):
    """Publishing it unread is the policy; losing the audit row would not be."""
    from app.sinks import MemorySink
    sink = MemorySink()
    c = create_app(make_settings(max_chars=50_000), scorer=KeywordScorer(), sink=sink).test_client()
    upload(c, pdf_bytes(), "cat.pdf")
    assert len(sink.results) == 1
    assert sink.results[0]["decision"] == "allow"


def test_decisions_for_files_are_recorded(make_settings, ocr):
    """Every attachment gets its own audit row, including the ones with no text."""
    from app.sinks import MemorySink
    sink = MemorySink()
    s = make_settings(max_chars=50_000, ocr=ocr)
    c = create_app(s, scorer=KeywordScorer(), sink=sink).test_client()
    upload(c, png(), "product.png")
    upload(c, text_image(["Earn 50000 per week from home."]), "flyer.png")
    assert [r["decision"] for r in sink.results] == ["allow", "reject"]


# ---- ISO base-media brands ---------------------------------------------------------
# AVIF, HEIC, MP4 and MOV all begin "....ftyp" and are told apart only by the brand that
# follows. Treating the whole family as video published AVIF images completely unchecked:
# media.allow_unchecked_video is on, so a weapon photo saved as .avif skipped OCR and the
# visual check both. Unsplash serves AVIF by default, so this was an ordinary upload.

def ftyp(brand: bytes) -> bytes:
    return b"\x00\x00\x00\x20ftyp" + brand + b"\x00" * 48


@pytest.mark.parametrize("brand, kind", [
    (b"avif", "image"),     # AVIF still
    (b"avis", "image"),     # AVIF sequence
    (b"heic", "image"),
    (b"mif1", "image"),
    (b"isom", "video"),     # ordinary MP4
    (b"mp42", "video"),
    (b"qt  ", "video"),     # QuickTime
    (b"3gp5", "video"),
])
def test_iso_media_brand_decides_image_or_video(brand, kind):
    assert detect_kind(ftyp(brand)) == kind


def test_an_unknown_ftyp_brand_is_treated_as_video():
    """Fail safe for this family: an unrecognised brand is far more likely to be some video
    container than a still, and video has the stricter path."""
    assert detect_kind(ftyp(b"zzzz")) == "video"


def test_a_real_avif_is_moderated_rather_than_waved_through(make_settings, ocr):
    """The hole end to end: before the brand check this returned a video result with
    checked: false and nothing was looked at."""
    pytest.importorskip("PIL")
    from PIL import Image, features
    if not (hasattr(features, "check") and features.check("avif")):
        pytest.skip("Pillow on this host cannot encode AVIF")
    import io as _io
    buf = _io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(buf, format="AVIF")
    data = buf.getvalue()
    assert detect_kind(data) == "image"
    body = upload(client(make_settings, ocr=ocr), data, "photo.avif").get_json()
    assert body["media"]["kind"] == "image"
