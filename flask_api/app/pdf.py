"""PDF text extraction for moderation.

A PDF is moderated by pulling its text out and running it through the ordinary pipeline, so a
brochure or spec sheet is judged by the same rules as a pasted post. Everything interesting
here is about the ways that can go wrong.

**Pages we cannot read must not pass.** A PDF whose pages are scanned images yields no text, and
a moderator that "checks" empty text allows anything. So a page holding images but (almost) no
extractable text is counted as *unreadable*, and the upload is rejected naming those pages. This
is the one rule worth keeping if the rest is rewritten: without it, uploading a screenshot of a
scam is enough to defeat the whole system.

**OCR (app/ocr.py) is tried on exactly those pages**, and only widens what can be accepted: a
page it reads becomes ordinary text and is judged normally, while a page it cannot read stays
unreadable and is still refused. Blurring an image until OCR gives up therefore buys nothing.

**Wrapped lines are rejoined.** PDFs break paragraphs at every visual line. The sentence scan
splits on newlines, so raw extracted text would arrive as dozens of fragments and each sentence
would be judged without its context. `_unwrap` puts a paragraph back into one line when a line
neither ends a sentence nor is followed by something that looks like a new one.

**Everything is bounded.** File size, page count and extracted characters all have limits, and
pypdf is only ever asked for text — no JavaScript, no embedded files, no network.
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ocr import OcrEngine

log = logging.getLogger(__name__)

_SPACES = re.compile(r"[ \t ]+")
_HARD_WRAP = re.compile(r"[.!?:;]['\")\]]?$")     # a line that already ends a sentence
_STARTS_NEW = re.compile(r"^\s*(?:[-*•\d]|[A-Z][A-Za-z]*\s*:)")   # bullet, number, "Heading:"
_HYPHEN_WRAP = re.compile(r"(\w)-$")


class PdfError(Exception):
    """Extraction failed in a way the caller should report to the author."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


@dataclass(frozen=True)
class PdfLimits:
    enabled: bool = True
    max_bytes: int = 25_000_000          # 25 MB
    max_pages: int = 100
    max_chars: int = 100_000             # extracted text; longer documents are rejected, not truncated
    min_chars_per_page: int = 20         # below this, a page with images is treated as unreadable
    reject_unreadable_pages: bool = True  # False: moderate what was extracted and report the rest

    def __post_init__(self) -> None:
        if min(self.max_bytes, self.max_pages, self.max_chars) < 1:
            raise ValueError("pdf limits must be positive")


@dataclass(frozen=True)
class Page:
    page: int          # 1-based, as a reader sees it
    start: int         # offsets into the extracted text
    end: int
    chars: int
    images: int
    readable: bool
    source: str = "text"          # "text" from the PDF, or "ocr" when it was read off the pixels
    confidence: float | None = None   # OCR only

    def as_dict(self) -> dict:
        d = {"page": self.page, "start": self.start, "end": self.end, "chars": self.chars}
        if self.source != "text":
            d["source"] = self.source
            d["confidence"] = round(self.confidence or 0.0, 4)
        if not self.readable:
            d["readable"] = False
        return d


@dataclass(frozen=True)
class PageVisual:
    """What the visual check made of one page's pixels."""

    page: int                 # 1-based, as a reader sees it
    label: str
    category: str             # "" when the best-matching label is a safe one
    score: float
    unsafe_score: float

    def as_dict(self) -> dict:
        d = {"page": self.page, "label": self.label, "score": self.score,
             "unsafe_score": self.unsafe_score}
        if self.category:
            d["category"] = self.category
        return d


def _classify_page(visual, image):
    """Score one rendered page. Never raises: a page the visual check chokes on is reported as
    not looked at, which is what `visual_pages` omitting it means."""
    try:
        from PIL import Image

        return visual.classify(Image.fromarray(image))
    except Exception:
        log.warning("visual check failed on a PDF page", exc_info=True)
        return None


@dataclass(frozen=True)
class Extracted:
    text: str
    pages: tuple[Page, ...]
    page_count: int
    unreadable_pages: tuple[int, ...] = ()
    producer: str | None = field(default=None)
    # Pages whose pixels were scored (app/clip.py), and the subset the check objects to. Empty
    # when the visual check is off or no page carries images - which is NOT the same as every
    # page being clean, so `visual_checked_pages` says which were actually looked at.
    visual_pages: tuple[PageVisual, ...] = ()
    unsafe_visual: tuple[PageVisual, ...] = ()

    def as_dict(self) -> dict:
        d = {"page_count": self.page_count, "extracted_chars": len(self.text),
             "pages": [p.as_dict() for p in self.pages],
             "unreadable_pages": list(self.unreadable_pages)}
        ocr_pages = [p.page for p in self.pages if p.source == "ocr"]
        if ocr_pages:
            d["ocr_pages"] = ocr_pages
        if self.visual_pages:
            d["visual_checked_pages"] = [v.page for v in self.visual_pages]
            d["visual"] = [v.as_dict() for v in self.visual_pages if v.category]
        return d

    def page_of(self, offset: int) -> int | None:
        """Which page a character offset came from, so feedback can be shown on the right page."""
        for p in self.pages:
            if p.start <= offset < p.end:
                return p.page
        return None


def _unwrap(text: str) -> str:
    """Rejoin lines that a PDF broke mid-sentence, keeping real paragraph breaks."""
    out: list[str] = []
    for raw in text.split("\n"):
        line = _SPACES.sub(" ", raw).strip()
        if not line:
            out.append("")
            continue
        if out and out[-1] and not _HARD_WRAP.search(out[-1]) and not _STARTS_NEW.match(line):
            joined = _HYPHEN_WRAP.sub(r"\1", out[-1])          # "manu-\nfacturer" -> "manufacturer"
            out[-1] = joined + ("" if joined != out[-1] else " ") + line
        else:
            out.append(line)
    # collapse runs of blank lines to one blank line
    lines, blank = [], False
    for line in out:
        if line:
            lines.append(line)
            blank = False
        elif not blank and lines:
            lines.append("")
            blank = True
    return "\n".join(lines).strip()


def _image_count(page) -> int:
    """Image XObjects on the page, without decoding them (decoding a hostile image is a risk
    we don't need to take: the count alone tells us the page carries pixels, not text)."""
    try:
        resources = page.get("/Resources")
        resources = resources.get_object() if hasattr(resources, "get_object") else resources
        xobjects = (resources or {}).get("/XObject")
        xobjects = xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
        if not xobjects:
            return 0
        count = 0
        for ref in xobjects.values():
            obj = ref.get_object() if hasattr(ref, "get_object") else ref
            if (obj or {}).get("/Subtype") == "/Image":
                count += 1
        return count
    except Exception:       # a malformed resource dict shouldn't fail the upload
        return 0


def render_pages(data: bytes, indexes: list[int], dpi: int = 200,
                 max_pixels: int = 4_000_000) -> dict[int, "object"]:
    """Rasterise the given 0-based pages to RGB arrays.

    Rendering is the expensive step - around 100 ms a page before anything looks at the result -
    and a scanned page needs to be read AND looked at. So it happens once, here, and the array
    goes to both the OCR engine and the visual check. Pages that fail to render are left out
    rather than raising: one bad page should not take the whole document down, and a page
    nobody could render carries no text either, so it stays unreadable and is refused upstream.
    """
    try:
        import numpy as np
        import pypdfium2 as pdfium
    except ImportError as exc:
        log.warning("cannot rasterise PDF pages: %s", exc)
        return {}

    out: dict[int, object] = {}
    try:
        doc = pdfium.PdfDocument(data)
    except Exception:
        log.warning("PDF could not be opened for rendering", exc_info=True)
        return {}
    try:
        for index in indexes:
            try:
                page = doc[index]
                width, height = page.get_size()                 # points (1/72 inch)
                scale = dpi / 72.0
                pixels = (width * scale) * (height * scale)
                if pixels > max_pixels:                         # bound a hostile page size
                    scale *= (max_pixels / pixels) ** 0.5
                out[index] = np.asarray(page.render(scale=scale).to_pil().convert("RGB"))
            except Exception:
                log.warning("page %d could not be rendered", index + 1, exc_info=True)
    finally:
        doc.close()
    return out


def extract(data: bytes, limits: PdfLimits | None = None, filename: str = "",
            ocr: "OcrEngine | None" = None, visual: "object | None" = None) -> Extracted:
    """Text of `data`, or PdfError. See the module docstring for the rules.

    `ocr` (app/ocr.py) is consulted only for pages that carry no extractable text, and only
    ever adds text — a page it cannot read is still refused.

    `visual` (app/clip.py) looks at the pages that carry pixels. A scanned PDF is a stack of
    photographs, so without this a weapon or a gore photograph published simply by being put
    in a PDF instead of attached as an image — the same hole the visual check closes for
    attachments, one container along. Pages of pure text have nothing to look at and are
    skipped.
    """
    limits = limits or PdfLimits()
    if len(data) > limits.max_bytes:
        raise PdfError("pdf_too_large", f"the file is larger than {limits.max_bytes} bytes",
                       size=len(data), limit=limits.max_bytes)
    if not data[:1024].lstrip().startswith(b"%PDF"):
        raise PdfError("pdf_invalid", "this does not look like a PDF file")

    try:
        from pypdf import PdfReader
    except ImportError as exc:                                  # pragma: no cover
        raise PdfError("pdf_unavailable", "PDF support is not installed on the server") from exc

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and reader.decrypt("") == 0:     # 0 = the empty password didn't work
            raise PdfError("pdf_encrypted", "the PDF is password protected; upload an unprotected copy")
        pages = reader.pages
        page_count = len(pages)
    except PdfError:
        raise
    except Exception:
        # The parser's own exception name ("PdfStreamError") was being shown to the author,
        # who can do nothing with it. It goes to the log, where someone can.
        log.warning("PDF could not be parsed", exc_info=True)
        raise PdfError("pdf_unreadable", "this PDF appears to be damaged and could not be opened. "
                                         "Try saving it again from the program that made it.")

    if page_count == 0:
        raise PdfError("pdf_empty", "the PDF has no pages")
    if page_count > limits.max_pages:
        raise PdfError("pdf_too_many_pages", f"the PDF has {page_count} pages, the limit is {limits.max_pages}",
                       pages=page_count, limit=limits.max_pages)

    # Pass 1: the text the PDF carries.
    visual_pages: list[PageVisual] = []
    unsafe_visual: list[PageVisual] = []
    bodies: list[str] = []
    image_counts: list[int] = []
    sources: list[str] = []
    confidences: list[float | None] = []
    for i, page in enumerate(pages, start=1):
        try:
            raw = page.extract_text() or ""
        except Exception:
            log.warning("page %d of %r could not be extracted", i, filename)
            raw = ""
        bodies.append(_unwrap(raw))
        image_counts.append(_image_count(page))
        sources.append("text")
        confidences.append(None)

    def is_readable(n: int) -> bool:
        return len(bodies[n]) >= limits.min_chars_per_page or image_counts[n] == 0

    # Pass 2: the pixels. Two different questions get asked of the same rendered page, so it
    # is rasterised once and handed to both.
    #
    #   OCR      only pages that carry no extractable text - it can only ever ADD text, and a
    #            page it cannot read stays unreadable and is refused below.
    #   visual   every page that carries images, read or not. A scanned invoice is a photograph
    #            and so is a scanned catalogue of weapons; the text on it says nothing about
    #            what the picture shows.
    blind = [n for n in range(len(bodies)) if not is_readable(n)]
    want_ocr = bool(blind) and ocr is not None and ocr.enabled
    looking = visual is not None and getattr(visual, "ready", False)
    pictured = [n for n in range(len(bodies)) if image_counts[n] > 0] if looking else []

    cap = ocr.config.max_pages if ocr is not None else 60
    to_render = sorted(set((blind if want_ocr else []) + pictured))[:cap]
    rendered = render_pages(data, to_render,
                            dpi=ocr.config.dpi if ocr is not None else 200,
                            max_pixels=ocr.config.max_pixels if ocr is not None else 4_000_000) \
        if to_render else {}

    if want_ocr and rendered:
        for n, read in ocr.read_rendered({i: rendered[i] for i in blind if i in rendered}).items():
            bodies[n] = _unwrap(read.text)
            sources[n] = "ocr"
            confidences[n] = read.confidence
            log.info("page %d of %r read by OCR: %d chars, confidence %.2f, %.0f ms",
                     n + 1, filename, len(bodies[n]), read.confidence, read.ms)

    for n in pictured:
        if n not in rendered:
            continue
        looked = _classify_page(visual, rendered[n])
        if looked is None:
            continue
        visual_pages.append(PageVisual(n + 1, looked.label, looked.category,
                                       round(looked.score, 4), round(looked.unsafe_score, 4)))
        if visual.decides_reject(looked):
            unsafe_visual.append(visual_pages[-1])
            log.info("page %d of %r: visual check says %s (%.3f)",
                     n + 1, filename, looked.label, looked.score)

    # Pass 3: lay the pages out and check what is still unreadable.
    spans: list[Page] = []
    unreadable: list[int] = []
    total = 0
    for n, body in enumerate(bodies):
        readable = is_readable(n)
        if not readable:
            unreadable.append(n + 1)
        start = total + (2 if n else 0)               # pages are joined by a blank line
        total = start + len(body)
        if total > limits.max_chars:
            raise PdfError("pdf_text_too_long",
                           f"the PDF holds more than {limits.max_chars} characters of text; "
                           "split it into smaller documents", limit=limits.max_chars)
        spans.append(Page(n + 1, start, start + len(body), len(body), image_counts[n],
                          readable, sources[n], confidences[n]))

    text = "\n\n".join(bodies).strip()
    if unreadable and limits.reject_unreadable_pages:
        raise PdfError("pdf_text_not_extractable",
                       f"no text could be read from page(s) {', '.join(map(str, unreadable))}. "
                       "These look like scanned images, and content we cannot read cannot be checked. "
                       "Upload a PDF with selectable text.", unreadable_pages=unreadable)
    if not text.strip():
        raise PdfError("pdf_text_not_extractable",
                       "no text could be read from this PDF, so it cannot be checked. "
                       "Upload a PDF with selectable text.", unreadable_pages=unreadable or [1])

    return Extracted(text, tuple(spans), page_count, tuple(unreadable),
                     visual_pages=tuple(visual_pages), unsafe_visual=tuple(unsafe_visual))
