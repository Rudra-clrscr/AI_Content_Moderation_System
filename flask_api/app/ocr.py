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
    max_pages: int = 10          # pages per upload; OCR is ~0.5 s each, so this bounds the request
    dpi: int = 200               # 200 dpi is enough for body text and keeps pages ~2 MP
    max_pixels: int = 4_000_000  # hard cap per page, whatever the dpi works out to
    min_chars: int = 20          # below this the page is still unreadable
    min_confidence: float = 0.5  # mean recognition confidence below this is not trusted

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("ocr.min_confidence must be in [0, 1]")
        if min(self.max_pages, self.dpi, self.max_pixels, self.min_chars) < 1:
            raise ValueError("ocr limits must be positive")


@dataclass(frozen=True)
class PageText:
    text: str
    confidence: float
    ms: float


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
            self._engine = RapidOCR()
            log.info("OCR engine loaded (RapidOCR on onnxruntime)")
        return self._engine

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
        return {i: p for i, p in out.items()
                if len(p.text) >= self.config.min_chars and p.confidence >= self.config.min_confidence}

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
        result = engine(image)
        texts = list(getattr(result, "txts", None) or [])
        scores = [float(s) for s in (getattr(result, "scores", None) or [])]
        return PageText(" ".join(t.strip() for t in texts if t.strip()),
                        float(sum(scores) / len(scores)) if scores else 0.0,
                        (time.perf_counter() - t0) * 1000)
