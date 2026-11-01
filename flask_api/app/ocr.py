"""OCR for writing inside an uploaded picture.

Without this, the scam just moves into the image: a flyer, a screenshot or a shopfront photo
carries its words as pixels, and text nobody read cannot be checked. OCR turns some of those
into real decisions — it widens what we can *accept*, it does not make the system safer.

RapidOCR is PaddleOCR's models exported to ONNX and run through onnxruntime, which is already
this service's only inference runtime: no PyTorch, no Paddle, no system binary, and the same
CPU tuning and offline-bundle discipline as the moderation model. Apache 2.0, and the weights
ship inside the package, so nothing is downloaded at run time. Measured on rendered flyers:
~0.5 s per picture on CPU, recovering the text intact through low resolution, 7-degree skew
and sensor noise.

**It fails closed.** OCR that comes back with too little text, or with low confidence, leaves
the writing unread, and `media.allow_unreadable_image` decides what happens then — never a
clean result. Anyone can blur an image until OCR gives up, so this must never become a way to
wave a picture through.

This module used to render and read PDF pages as well (`read_pages`, `read_rendered`). Both
went when app/pdf.py did; the page-rendering half is in private_notes/pdf_on_hold/ and in the
history of that commit, to come back with it.
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
    min_chars: int = 20          # below this the picture's writing is still unread
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
        if min(self.min_chars, self.min_chars_per_box) < 1:
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
