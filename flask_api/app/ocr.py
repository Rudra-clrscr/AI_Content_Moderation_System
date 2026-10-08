"""OCR for PDF pages that hold no extractable text.

Without this, a scanned page is refused (app/pdf.py): text nobody can read can't be checked,
and approving it would let a screenshot of a scam through. OCR turns some of those refusals
into real decisions — it widens what we can *accept*, it does not make the system safer.

RapidOCR is PaddleOCR's models exported to ONNX and run through onnxruntime, which is already
this service's only inference runtime: no PyTorch, no Paddle, no system binary, and the same
CPU tuning and offline-bundle discipline as the moderation model. Apache 2.0, and the weights
ship inside the package, so nothing is downloaded at run time. Measured on rendered flyers:
~0.5 s per page on CPU, recovering the text intact through low resolution, 7-degree skew and
sensor noise.

Pages are rasterised with pypdfium2 rather than pulled out as embedded images. Scans arrive in
awkward encodings (CCITT G4, JBIG2) that image extractors often refuse, and a decode failure
here would refuse a legitimate document; rendering also catches pages whose "text" is really
vector outlines, which no extractor can read.

**It still fails closed.** OCR that comes back with too little text, or with low confidence,
leaves the page unreadable and the upload is refused as before. Anyone can blur an image until
OCR gives up, so this must never become a way to wave a page through.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OcrConfig:
    enabled: bool = True
    max_pages: int = 60          # pages per upload; ~0.5 s each, bounds catalogs
    dpi: int = 200               # 200 dpi is enough for body text and keeps pages ~2 MP
    max_pixels: int = 4_000_000  # hard cap per page, whatever the dpi works out to
    min_chars: int = 20          # below this the page is still unreadable
    min_confidence: float = 0.5  # mean recognition confidence below this is not trusted
    # Detection finds where the writing is; recognition turns it into characters. When the
    # recognition model doesn't cover the script, detection still finds every line and
    # recognition returns near-nothing at a plausible-looking confidence. Measured on rendered
    # 48pt text, characters recovered per detected line:
    #   English  "Cotton saris wholesale" / "Surat, Gujarat - since 1994"  25-28 chars, conf 0.99
    #   Hindi    "सूती साड़ियाँ थोक में"                                        4 chars ("TURT"), conf 0.76
    #   Hindi    "तुम सब बेवकूफ हो, मर जाओ / मैं तुम्हें जान से मार दूंगा"          1.5 chars ("上 亚"),  conf 0.62
    # The Hindi threat cleared min_confidence, so confidence alone cannot catch it and the
    # picture was reported as read and clean. A line OCR really read comes back as a word or
    # more, so the yield per line is the signal.
    min_chars_per_box: int = 6
    # ...except that genuinely short signage ("SALE", "95/kg") is one line of few characters.
    # Recognition that is this sure of itself is trusted however little it returned.
    trust_short_confidence: float = 0.90
    # A recognition model for a script the package doesn't bundle (it ships Latin and Chinese).
    # `scripts/fetch_ocr_langs.py devanagari` downloads one into models/ocr; the path is local
    # so nothing is fetched while serving a request. Both are needed together: the path supplies
    # the weights, the language tells RapidOCR which pre/post-processing they expect.
    rec_model_path: str = ""
    rec_lang: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("ocr.min_confidence must be in [0, 1]")
        if not 0.0 <= self.trust_short_confidence <= 1.0:
            raise ValueError("ocr.trust_short_confidence must be in [0, 1]")
        if min(self.max_pages, self.dpi, self.max_pixels, self.min_chars, self.min_chars_per_box) < 1:
            raise ValueError("ocr limits must be positive")

    def reads_as_text(self, page: "PageText") -> bool:
        """Do we trust what OCR recovered, or did it only prove there is writing it can't read?

        False means the image demonstrably carries text that was not read. That is not the same
        as an image with no text in it (`PageText.has_text`), and it must never be reported as
        checked: a threat in Devanagari would otherwise publish with a clean result.
        """
        chars = len(page.text.strip())
        if not chars or page.confidence < self.min_confidence:
            return False
        if page.confidence >= self.trust_short_confidence:
            return True
        return chars >= self.min_chars_per_box * max(page.boxes, 1)


@dataclass(frozen=True)
class PageText:
    text: str
    confidence: float
    ms: float
    boxes: int = 0               # text lines detection found, whether or not they were recognised

    @property
    def has_text(self) -> bool:
        """Whether there is writing in the image at all, read or not."""
        return bool(self.text.strip()) or self.boxes > 0


class OcrEngine:
    """Lazily loaded RapidOCR. `available` stays False if the optional packages aren't installed,
    and the service then behaves exactly as it did before OCR existed."""

    def __init__(self, config: OcrConfig | None = None):
        self.config = config or OcrConfig()
        self._engine = None
        self._lock = threading.Lock()
        self._failed = False

    @property
    def enabled(self) -> bool:
        return self.config.enabled and not self._failed

    def _load(self):
        if self._engine is None:
            from rapidocr import RapidOCR
            self._engine = RapidOCR(params=self._params())
            log.info("OCR engine loaded (RapidOCR on onnxruntime)%s",
                     f", {self.config.rec_lang} recognition" if self.config.rec_lang else "")
        return self._engine

    def _params(self) -> dict | None:
        """RapidOCR overrides, or None for the models bundled with the package."""
        path, lang = self.config.rec_model_path, self.config.rec_lang
        if not path or not lang:
            if path or lang:
                log.warning("ocr.rec_model_path and ocr.rec_lang must be set together; "
                            "using the bundled recognition model")
            return None
        from pathlib import Path as _Path
        if not _Path(path).exists():
            log.error("ocr.rec_model_path %s does not exist; using the bundled recognition "
                      "model (run scripts/fetch_ocr_langs.py %s)", path, lang)
            return None
        # engine_type/ocr_version are left alone: RapidOCR reads the character list out of the
        # .onnx itself, so the weights and the language are all it needs from us.
        return {"Rec.model_path": str(path), "Rec.lang_type": lang}

    def read_pages(self, pdf_bytes: bytes, page_indexes: list[int]) -> dict[int, PageText]:
        """OCR the given 0-based pages of a PDF. Returns only the pages it managed to read;
        anything missing from the result stays unreadable and is refused upstream."""
        if not self.enabled or not page_indexes:
            return {}
        wanted = page_indexes[: self.config.max_pages]
        out: dict[int, PageText] = {}
        try:
            import numpy as np
            import pypdfium2 as pdfium
            with self._lock:                      # one page at a time: bounded CPU, and the
                engine = self._load()             # wrapper does its own preprocessing
                doc = pdfium.PdfDocument(pdf_bytes)
                try:
                    for index in wanted:
                        try:
                            out[index] = self._read_page(doc, index, engine, np)
                        except Exception:
                            log.warning("OCR failed on page %d", index + 1, exc_info=True)
                finally:
                    doc.close()
        except ImportError as exc:
            log.warning("OCR disabled: %s (install rapidocr and pypdfium2 to enable it)", exc)
            self._failed = True
            return {}
        except Exception:
            log.exception("OCR could not run; pages stay unreadable")
            return out
        # A page has to clear the absolute floor AND look like text that was really read, so a
        # scan in a script the recognition model doesn't cover stays unreadable (and is refused
        # upstream) instead of passing as a page with a few characters on it.
        return {i: p for i, p in out.items()
                if len(p.text) >= self.config.min_chars and self.config.reads_as_text(p)}

    def read_rendered(self, pages: dict[int, "object"]) -> dict[int, PageText]:
        """OCR pages somebody else has already rendered.

        `read_pages` renders and reads in one go, which is all OCR needs on its own. But a
        scanned page also has to be *looked at* by the visual check, and rendering a page is
        the expensive part - ~100 ms before either of them does any work. So the PDF path
        renders once and hands the same bitmaps to both (app/pdf.py), and this is the half of
        `read_pages` that takes them.
        """
        if not self.enabled or not pages:
            return {}
        out: dict[int, PageText] = {}
        with self._lock:
            try:
                engine = self._load()
            except Exception:
                log.exception("OCR could not run; pages stay unreadable")
                return {}
            for index, image in pages.items():
                t0 = time.perf_counter()
                try:
                    out[index] = self._to_page_text(engine(image), (time.perf_counter() - t0) * 1000)
                except Exception:
                    log.warning("OCR failed on page %d", index + 1, exc_info=True)
        return {i: p for i, p in out.items()
                if len(p.text) >= self.config.min_chars and self.config.reads_as_text(p)}

    def read_image(self, array) -> PageText:
        """OCR an already-decoded RGB image (app/media.py hands us attachments this way)."""
        if not self.enabled:
            return PageText("", 0.0, 0.0)
        t0 = time.perf_counter()
        with self._lock:
            try:
                result = self._load()(array)
            except ImportError as exc:
                log.warning("OCR disabled: %s", exc)
                self._failed = True
                return PageText("", 0.0, 0.0)
        return self._to_page_text(result, (time.perf_counter() - t0) * 1000)

    @staticmethod
    def _to_page_text(result, ms: float) -> PageText:
        texts = list(getattr(result, "txts", None) or [])
        scores = [float(s) for s in (getattr(result, "scores", None) or [])]
        boxes = getattr(result, "boxes", None)
        return PageText(" ".join(t.strip() for t in texts if t.strip()),
                        float(sum(scores) / len(scores)) if scores else 0.0, ms,
                        len(boxes) if boxes is not None else len(texts))

    def _read_page(self, doc, index: int, engine, np) -> PageText:
        page = doc[index]
        width, height = page.get_size()                        # points (1/72 inch)
        scale = self.config.dpi / 72.0
        pixels = (width * scale) * (height * scale)
        if pixels > self.config.max_pixels:                    # keep a hostile page size bounded
            scale *= (self.config.max_pixels / pixels) ** 0.5
        t0 = time.perf_counter()
        bitmap = page.render(scale=scale)
        image = np.asarray(bitmap.to_pil().convert("RGB"))
        return self._to_page_text(engine(image), (time.perf_counter() - t0) * 1000)
