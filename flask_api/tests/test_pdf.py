"""PDF upload and extraction.

The PDFs here are built byte by byte rather than with a writer library, so a test can produce
exactly the awkward shapes that matter: a page of images with no text, a paragraph broken
across visual lines, an encrypted file.
"""
import io
import zlib

import pytest

from app import create_app
from app.model import ScoreResult
from app.pdf import PdfError, PdfLimits, _unwrap, extract


# ---- a minimal PDF writer, enough for pypdf to read ----------------------------

def _escape(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def make_pdf(pages: list[str], with_image: set[int] = frozenset()) -> bytes:
    """A PDF where page i shows `pages[i]`. Pages listed in `with_image` also carry an image
    XObject, which is how a scanned page looks: pixels, no text.

    Objects 1-3 are the catalog, the page tree and the font; page objects are numbered from 4.
    """
    objects: dict[int, bytes] = {}
    page_numbers: list[int] = []
    number = 4

    for i, text in enumerate(pages):
        stream = "BT /F1 12 Tf 72 720 Td 14 TL\n"
        for line in text.split("\n"):
            stream += f"({_escape(line)}) Tj T*\n"
        stream += "ET"
        raw = stream.encode("latin-1")
        content_num, number = number, number + 1
        objects[content_num] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(raw), raw)

        resources = "<< /Font << /F1 3 0 R >>"
        if i in with_image:
            img = zlib.compress(b"\x00" * 300)
            image_num, number = number, number + 1
            objects[image_num] = (b"<< /Type /XObject /Subtype /Image /Width 10 /Height 10 "
                                  b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
                                  b"/Length %d >>\nstream\n%s\nendstream" % (len(img), img))
            resources += f" /XObject << /Im0 {image_num} 0 R >>"
        resources += " >>"

        page_num, number = number, number + 1
        objects[page_num] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                             f"/Contents {content_num} 0 R /Resources {resources} >>").encode("latin-1")
        page_numbers.append(page_num)

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = (f"<< /Type /Pages /Kids [{' '.join(f'{n} 0 R' for n in page_numbers)}] "
                  f"/Count {len(page_numbers)} >>").encode("latin-1")
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for n in sorted(objects):
        offsets[n] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (n, objects[n])
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for n in sorted(objects):
        out += b"%010d 00000 n \n" % offsets[n]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref_at)
    return bytes(out)


def make_scanned_pdf(lines: list[str], scale: float = 1.0, angle: int = 0) -> bytes:
    """A PDF whose only page is a picture of the text — what a scan or a screenshot looks like.
    Skipped if Pillow isn't installed; the OCR tests need it to make something to read."""
    PIL = pytest.importorskip("PIL")
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1240, 700), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 34)
    except OSError:                                  # pragma: no cover - font layout varies by host
        font = ImageFont.load_default()
    for i, line in enumerate(lines):
        draw.text((70, 70 + i * 60), line, fill=(20, 20, 20), font=font)
    if angle:
        img = img.rotate(angle, expand=True, fillcolor="white")
    if scale != 1.0:
        small = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        img = small.resize((1240, 700), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return _pdf_with_image(buf.getvalue(), img.width, img.height)


def _pdf_with_image(jpeg: bytes, width: int, height: int) -> bytes:
    """A one-page PDF whose whole page is `jpeg`, and which carries no text at all.

    The page is sized to the image's aspect ratio. Filling a portrait page with a landscape
    scan stretches the glyphs badly enough that detection stops finding them — which is a
    property of the fixture, not of the OCR, and cost an hour to notice.
    """
    page_w = 612.0
    page_h = round(612.0 * height / width, 2)
    content = f"q {page_w} 0 0 {page_h} 0 0 cm /Im0 Do Q".encode()
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w} {page_h}] /Contents 4 0 R "
            f"/Resources << /XObject << /Im0 5 0 R >> >> >>").encode(),
        4: b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        5: (b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB "
            b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n%s\nendstream"
            % (width, height, len(jpeg), jpeg)),
    }
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for n in sorted(objects):
        offsets[n] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (n, objects[n])
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for n in sorted(objects):
        out += b"%010d 00000 n \n" % offsets[n]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref_at)
    return bytes(out)


def test_the_test_pdf_is_readable():
    e = extract(make_pdf(["Cotton bedsheets in king and queen sizes."]))
    assert "Cotton bedsheets" in e.text and e.page_count == 1


# ---- extraction ----------------------------------------------------------------

def test_pages_are_mapped_to_offsets():
    e = extract(make_pdf(["First page about cotton yarn.", "Second page about delivery terms."]))
    assert e.page_count == 2 and len(e.pages) == 2
    for p in e.pages:
        assert e.text[p.start:p.end].strip()
    assert e.page_of(e.pages[1].start) == 2
    assert "First page" in e.text[e.pages[0].start:e.pages[0].end]


def test_wrapped_lines_are_rejoined_into_sentences():
    """Raw PDF text breaks a paragraph at every visual line; the sentence scan splits on
    newlines, so without this each fragment would be judged on its own."""
    e = extract(make_pdf(["We manufacture cotton yarn for\nexporters across India and we\nship within seven days."]))
    assert "cotton yarn for exporters across India and we ship within seven days." in e.text


def test_hyphenated_word_across_lines_is_joined():
    assert _unwrap("We are a manu-\nfacturer of pipes") == "We are a manufacturer of pipes"


def test_real_paragraph_breaks_are_kept():
    assert _unwrap("First paragraph ends here.\nSecond one starts.") == \
        "First paragraph ends here.\nSecond one starts."


def test_bullets_are_not_joined():
    assert _unwrap("We supply\n- cotton yarn\n- viscose") == "We supply\n- cotton yarn\n- viscose"


# ---- failing closed -------------------------------------------------------------

def test_image_only_page_is_rejected_not_allowed():
    """The security property: a scanned page yields no text, and text we cannot read cannot be
    checked. Allowing it would let a screenshot of a scam through untouched."""
    with pytest.raises(PdfError) as exc:
        extract(make_pdf(["Genuine cotton yarn listing with plenty of text on it.", " "], with_image={1}))
    assert exc.value.code == "pdf_text_not_extractable" and exc.value.extra["unreadable_pages"] == [2]


def test_image_page_may_be_allowed_when_configured():
    e = extract(make_pdf(["Genuine cotton yarn listing with plenty of text.", " "], with_image={1}),
                PdfLimits(reject_unreadable_pages=False))
    assert e.unreadable_pages == (2,) and "cotton yarn" in e.text


def test_blank_page_without_images_is_not_unreadable():
    e = extract(make_pdf(["Plenty of readable text about cotton yarn here.", " "]))
    assert e.unreadable_pages == ()


def test_empty_pdf_text_is_rejected():
    with pytest.raises(PdfError, match="cannot be checked"):
        extract(make_pdf([" "]))


def test_not_a_pdf():
    with pytest.raises(PdfError) as exc:
        extract(b"MZ\x90\x00 this is an executable")
    assert exc.value.code == "pdf_invalid"


def test_corrupt_pdf():
    with pytest.raises(PdfError) as exc:
        extract(b"%PDF-1.4\nnot really a pdf at all")
    assert exc.value.code in ("pdf_unreadable", "pdf_text_not_extractable")


def test_corrupt_pdf_message_names_no_internals():
    """The author was being shown "PdfStreamError", which tells them nothing they can act on.
    The exception name belongs in the log; the message has to say what to do instead."""
    with pytest.raises(PdfError) as exc:
        extract(b"%PDF-1.4\nnot really a pdf at all")
    message = exc.value.message
    assert "Error" not in message and "Exception" not in message
    assert "(" not in message                     # no parenthesised class name
    assert "Try saving it again" in message       # says what the author can do about it


def test_limits_are_enforced():
    pdf = make_pdf(["Cotton yarn and bedsheets in all sizes."] * 3)
    with pytest.raises(PdfError) as exc:
        extract(pdf, PdfLimits(max_bytes=10))
    assert exc.value.code == "pdf_too_large"
    with pytest.raises(PdfError) as exc:
        extract(pdf, PdfLimits(max_pages=2))
    assert exc.value.code == "pdf_too_many_pages"
    with pytest.raises(PdfError) as exc:
        extract(pdf, PdfLimits(max_chars=10))
    assert exc.value.code == "pdf_text_too_long"


# ---- the endpoint ----------------------------------------------------------------

class TextScorer:
    version = "p-1"

    def __init__(self, risk_for="earn"):
        self.risk_for = risk_for

    def score(self, text):
        r = 0.95 if (self.risk_for in text.lower() and len(text) < 120) else 0.01
        return ScoreResult(r, "x", {"x": r}, self.version, 1.0)


def client(make_settings, **kw):
    return create_app(make_settings(max_chars=50_000, **kw), scorer=TextScorer()).test_client()


def upload(c, data, name="brochure.pdf", **form):
    return c.post("/v1/moderate/pdf", data={"file": (io.BytesIO(data), name), **form},
                  content_type="multipart/form-data")


def test_clean_pdf_is_allowed(make_settings):
    r = upload(client(make_settings), make_pdf(["Cotton bedsheets in king and queen sizes. Bulk orders welcome."]))
    body = r.get_json()
    assert r.status_code == 200 and body["decision"] == "allow"
    assert body["pdf"]["filename"] == "brochure.pdf" and body["pdf"]["page_count"] == 1
    assert body["content_type"] == "article"          # the default for an uploaded document


def test_scam_inside_a_pdf_is_rejected_and_located_on_its_page(make_settings):
    pdf = make_pdf(["Cotton bedsheets in king and queen sizes. Bulk orders welcome.",
                    "Earn 50000 per week from home. No experience needed."])
    body = upload(client(make_settings), pdf).get_json()
    assert body["decision"] == "reject"
    pages = {p["page"]: (p["start"], p["end"]) for p in body["pdf"]["pages"]}
    issue = body["feedback"]["issues"][0]
    assert pages[2][0] <= issue["start"] < pages[2][1], "the issue should fall on page 2"


def test_image_only_pdf_is_refused_by_the_endpoint(make_settings):
    r = upload(client(make_settings), make_pdf(["Plenty of readable text about cotton yarn.", " "], with_image={1}))
    assert r.status_code == 422
    assert r.get_json()["error"]["code"] == "pdf_text_not_extractable"
    assert r.get_json()["error"]["unreadable_pages"] == [2]


def test_missing_file(make_settings):
    r = client(make_settings).post("/v1/moderate/pdf", data={}, content_type="multipart/form-data")
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "invalid_file"


def test_unsupported_upload(make_settings):
    r = upload(client(make_settings), b"just a text file", name="notes.txt")
    assert r.status_code == 422 and r.get_json()["error"]["code"] == "media_unsupported"


def test_bad_content_type(make_settings):
    r = upload(client(make_settings), make_pdf(["Cotton bedsheets for wholesale buyers."]), content_type="banana")
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "invalid_content_type"


def test_oversized_pdf(make_settings):
    s = make_settings(max_chars=50_000, pdf=PdfLimits(max_bytes=200))
    c = create_app(s, scorer=TextScorer()).test_client()
    r = upload(c, make_pdf(["Cotton bedsheets in king and queen sizes. Bulk orders welcome."]))
    assert r.status_code == 413 and r.get_json()["error"]["code"] == "pdf_too_large"


def test_endpoint_can_be_disabled(make_settings):
    s = make_settings(max_chars=50_000, pdf=PdfLimits(enabled=False))
    c = create_app(s, scorer=TextScorer()).test_client()
    assert upload(c, make_pdf(["Cotton bedsheets."])).status_code == 404


SCAM_FLYER = ["Sunrise Polymers - Dealer Programme",
              "Become a distributor in your district today.",
              "Earn 50000 per week from home with no investment.",
              "Just pay a small registration fee to reserve your territory."]


@pytest.fixture(scope="module")
def ocr():
    """The real engine. Skipped where rapidocr/pypdfium2 aren't installed, so the suite still
    runs on a machine without the optional OCR packages."""
    pytest.importorskip("rapidocr")
    pytest.importorskip("pypdfium2")
    from app.ocr import OcrConfig, OcrEngine
    engine = OcrEngine(OcrConfig(enabled=True))
    if not engine.read_pages(make_scanned_pdf(["Cotton bedsheets in king and queen sizes."]), [0]):
        pytest.skip("OCR engine could not read a plain rendered page on this host")
    return engine


def test_scanned_page_is_read_by_ocr_instead_of_refused(ocr):
    """Without OCR this PDF is refused; with it the page becomes real, checkable text."""
    pdf = make_scanned_pdf(SCAM_FLYER)
    with pytest.raises(PdfError):                       # the behaviour when OCR is off
        extract(pdf)
    e = extract(pdf, ocr=ocr)
    assert e.unreadable_pages == () and e.pages[0].source == "ocr"
    assert e.pages[0].confidence > 0.5
    assert "50000" in e.text and "registration fee" in e.text.lower()


@pytest.mark.parametrize("scale,angle", [(1.0, 0), (0.45, 0), (1.0, 7), (0.6, 4)])
def test_ocr_survives_degraded_scans(ocr, scale, angle):
    """Low resolution, skew and rescaling are what a phone photo of a flyer looks like."""
    e = extract(make_scanned_pdf(SCAM_FLYER, scale=scale, angle=angle), ocr=ocr)
    assert "50000" in e.text


def test_ocr_result_still_reaches_a_decision(make_settings, ocr):
    s = make_settings(max_chars=50_000, ocr=ocr)
    c = create_app(s, scorer=TextScorer(risk_for="50000")).test_client()
    body = upload(c, make_scanned_pdf(SCAM_FLYER), name="flyer.pdf").get_json()
    assert body["decision"] == "reject"
    assert body["pdf"]["ocr_pages"] == [1]
    assert body["pdf"]["pages"][0]["source"] == "ocr"


def test_blank_scan_is_still_refused(ocr):
    """OCR must not become a way through: a page it can't read stays unreadable."""
    blank = _pdf_with_image(_grey_jpeg(), 600, 400)
    with pytest.raises(PdfError) as exc:
        extract(blank, ocr=ocr)
    assert exc.value.code == "pdf_text_not_extractable"


def _grey_jpeg() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (600, 400), (200, 200, 200)).save(buf, "JPEG")
    return buf.getvalue()


def test_ocr_is_skipped_when_disabled(ocr):
    from app.ocr import OcrConfig, OcrEngine
    off = OcrEngine(OcrConfig(enabled=False))
    with pytest.raises(PdfError):
        extract(make_scanned_pdf(SCAM_FLYER), ocr=off)


def test_low_confidence_pages_are_not_trusted(ocr):
    """Raising the confidence floor above what the engine reports must refuse the page, not
    accept a half-read one."""
    from app.ocr import OcrConfig, OcrEngine
    strict = OcrEngine(OcrConfig(enabled=True, min_confidence=0.999999))
    with pytest.raises(PdfError):
        extract(make_scanned_pdf(SCAM_FLYER), ocr=strict)


def test_missing_ocr_packages_degrade_to_refusal(monkeypatch):
    """On a host without rapidocr the service behaves exactly as it did before OCR existed."""
    import builtins
    from app.ocr import OcrConfig, OcrEngine
    real_import = builtins.__import__

    def no_rapidocr(name, *a, **kw):
        if name.startswith(("rapidocr", "pypdfium2")):
            raise ImportError(f"no module named {name}")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_rapidocr)
    engine = OcrEngine(OcrConfig(enabled=True))
    assert engine.read_pages(b"%PDF-1.4", [0]) == {}
    assert not engine.enabled            # it disables itself rather than failing every upload


def test_gate_block_in_a_pdf_skips_the_model(make_settings):
    body = upload(client(make_settings), make_pdf(["Invest now and double your money in 7 days."])).get_json()
    assert body["decision"] == "reject" and body["decided_by"] == "gate"
    assert body["pdf"]["page_count"] == 1             # the pdf block is present even on a gate block
