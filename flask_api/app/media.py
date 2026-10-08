"""What an uploaded file is, and what we are able to say about it.

The article editor lets an author attach an image, a video or a PDF. Anything attached to a
post is published with it, so anything we cannot read is a way round the moderator: write a
clean article, put the scam in the picture. Each kind gets the strictest rule we can honestly
support, and the rules differ because what we can read differs.

**PDF** — text, with OCR for scanned pages (app/pdf.py). A page we cannot read is refused.

**Image** — two passes over the same decoded picture. OCR reads any writing, which catches
the common bypass of putting the scam in a screenshot; and the CLIP visual check (app/clip.py)
scores the picture itself against the written policy in `config/visual_policy.yaml`, which
catches what no amount of reading would — a weapon listing or a gore photograph with no words
on it at all. An image with no text and nothing the visual check objects to is allowed: unlike
a blank PDF page, a product photo with no writing on it is completely normal, and refusing
those would break the feature for every honest seller.

`visual_content_checked` says which of those two actually happened. It is `false` wherever the
visual check is off or its model is missing, and the caller must not read that as clean.

**Video** — nothing. Reading it would mean decoding frames and audio, which is a different
project. The code default is to refuse, because accepting an unchecked video is the same hole
as accepting an unchecked scan. The shipped config turns `media.allow_unchecked_video` on:
until there is a visual model, refusing would close the Videos tab altogether, so footage is
published with `checked: false` on the record instead of being silently passed as clean. The
flag is named so that either setting is a decision someone makes on purpose.

The kind is decided by the file's leading bytes, never by its name: a `.png` that is really a
PDF should be treated as a PDF, and an executable renamed `.jpg` should be refused.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# (kind, offset, magic). Checked in order; the first match wins.
_SIGNATURES: tuple[tuple[str, int, bytes], ...] = (
    ("pdf", 0, b"%PDF"),
    ("image", 0, b"\x89PNG\r\n\x1a\n"),
    ("image", 0, b"\xff\xd8\xff"),              # JPEG
    ("image", 0, b"GIF87a"),
    ("image", 0, b"GIF89a"),
    ("image", 0, b"BM"),                        # BMP
    ("image", 0, b"II*\x00"),                   # TIFF little-endian
    ("image", 0, b"MM\x00*"),                   # TIFF big-endian
    ("video", 0, b"\x1a\x45\xdf\xa3"),          # Matroska / WebM
    ("video", 0, b"OggS"),
    ("video", 4, b"ftyp"),                      # MP4 / MOV / 3GP
    ("video", 4, b"moov"),
)


class MediaError(Exception):
    """The upload cannot be moderated, and the author should be told why."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


@dataclass(frozen=True)
class MediaLimits:
    enabled: bool = True
    max_bytes: int = 15_000_000        # one attachment
    max_pixels: int = 40_000_000       # decoded image size, against decompression bombs
    allow_unchecked_video: bool = False
    # An image with writing OCR could not read. Refusing is the fail-closed choice and matches
    # the PDF rule (pdf.reject_unreadable_pages): a picture whose words nobody read is exactly
    # how a scam arrives. Turn it on to publish those anyway - they are then recorded with
    # checked: false, never as a clean result.
    allow_unreadable_image: bool = False
    # An image whose writing OCR could not read, but whose PICTURE the visual check looked at
    # and cleared. Refusing those was right while nothing inspected the picture; now that
    # app/clip.py does, it refuses 13% of real listings over logos and number plates. What is
    # still unchecked is the WORDS, so this is only ever reached when the visual check actually
    # ran and found nothing - never when it is off or missing.
    allow_unreadable_image_when_seen: bool = True
    # Text pulled out of a picture is held to a higher bar than text somebody typed. OCR of a
    # shopfront returns word-salad ("TURNKEY MWRULTANTS A SOLUTIONS"), and the model was
    # trained on prose, so it scores that noise unreliably. Measured on 63 real BONC listings
    # and the scam flyers: legitimate signage lands at 0.78-0.976, scam text at 0.9997.
    ocr_text_reject_min: float = 0.99

    def __post_init__(self) -> None:
        if min(self.max_bytes, self.max_pixels) < 1:
            raise ValueError("media limits must be positive")
        if not 0.0 <= self.ocr_text_reject_min <= 1.0:
            raise ValueError("media.ocr_text_reject_min must be in [0, 1]")


def detect_kind(data: bytes, filename: str = "") -> str:
    """"pdf" | "image" | "video" | "unknown", from the file's own bytes."""
    head = data[:64]
    for kind, offset, magic in _SIGNATURES:
        if head[offset:offset + len(magic)] == magic:
            return kind
    if head[:4] == b"RIFF" and len(head) >= 12:     # RIFF containers: WEBP is an image, AVI a video
        return {b"WEBP": "image", b"AVI ": "video"}.get(head[8:12], "unknown")
    return "unknown"


@dataclass(frozen=True)
class ImageText:
    """What OCR could make of a picture.

    The three outcomes are deliberately distinct, because the right answer differs for each:

    * `not has_text` — no writing in the picture. Ordinary for a product photo, so it is
      allowed, with `visual_content_checked: false` saying the picture itself wasn't judged.
    * `has_text and readable` — the writing was read, and it is moderated like any other text.
    * `has_text and not readable` — there is writing that OCR could not read, so nothing about
      it has been checked. Reporting this as an allow is how a threat in Devanagari published
      with a clean result; see `media.allow_unreadable_image`.
    """

    text: str
    confidence: float
    boxes: int = 0
    has_text: bool = False
    readable: bool = False
    # OCR itself failed, so we know nothing about the picture either way. Distinct from
    # unreadable writing, because the author can act on that and not on this.
    failed: bool = False


@dataclass(frozen=True)
class ImageRead:
    """Everything we were able to make of one picture: its words, and the picture itself."""

    text: "ImageText"
    visual: "object"          # app.clip.VisualResult; typed loosely to keep clip.py optional


def read_image(data: bytes, ocr, limits: MediaLimits, visual=None) -> ImageRead:
    """Decode the picture once, then both read it and look at it.

    Decoding is the expensive part and the part that can blow up on a hostile file, so it
    happens here rather than twice in the two checkers. A failure to decode is an error about
    the file; a failure in either checker is recorded on that checker's own result, because the
    caller has to tell "nothing here" apart from "nobody looked".
    """
    frame = _decode(data, limits)
    text = _ocr_image(frame, ocr)
    looked = _visual_image(frame, visual)
    return ImageRead(text=text, visual=looked)


def _decode(data: bytes, limits: MediaLimits):
    """Bytes to a PIL RGB image, with the decompression-bomb guards."""
    try:
        import io

        from PIL import Image
    except ImportError:
        log.warning("image checks unavailable: Pillow is missing")
        return None
    try:
        Image.MAX_IMAGE_PIXELS = limits.max_pixels          # Pillow's own bomb guard
        with Image.open(io.BytesIO(data)) as img:
            if img.width * img.height > limits.max_pixels:
                raise MediaError("media_too_large",
                                 f"the image is {img.width}x{img.height}, over the "
                                 f"{limits.max_pixels} pixel limit")
            return img.convert("RGB")
    except MediaError:
        raise
    except Image.DecompressionBombError:
        # Pillow's guard fires inside open(), before the explicit check above gets to run. Same
        # refusal either way, but the author is told their picture is too big rather than that
        # it is damaged - one of those they can act on.
        raise MediaError("media_too_large",
                         f"the image is larger than the {limits.max_pixels} pixel limit")
    except Exception:
        log.warning("image could not be decoded", exc_info=True)   # the name is for the log, not the author
        raise MediaError("media_unreadable", "this image file appears to be damaged and could not "
                                             "be opened. Try saving it again, or upload a different one.")


def _ocr_image(frame, ocr) -> "ImageText":
    if frame is None or ocr is None or not ocr.enabled:
        return ImageText("", 0.0)
    try:
        import numpy as np
    except ImportError:
        log.warning("image OCR unavailable: numpy is missing")
        return ImageText("", 0.0)
    try:
        page = ocr.read_image(np.asarray(frame))
    except Exception:
        log.exception("OCR failed on an image upload")
        # OCR fell over rather than reporting on the picture, so nothing is known about it.
        # Reporting "no text" here would publish it as checked.
        return ImageText("", 0.0, failed=True)
    return ImageText(page.text, page.confidence, page.boxes,
                     has_text=page.has_text, readable=ocr.config.reads_as_text(page))


def _visual_image(frame, visual):
    from app.clip import VisualResult

    if frame is None or visual is None:
        return VisualResult(checked=False)
    return visual.classify(frame)


def image_text(data: bytes, ocr, limits: MediaLimits) -> ImageText:
    """What OCR can read out of an image, and nothing about the picture itself.

    Kept for callers that only want the words. `read_image` is the one the API uses.
    """
    return _ocr_image(_decode(data, limits), ocr)
