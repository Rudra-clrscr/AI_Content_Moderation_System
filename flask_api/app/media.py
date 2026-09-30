"""What an uploaded file is, and what we are able to say about it.

The article editor lets an author attach an image, a video or a PDF. Anything attached to a
post is published with it, so anything we cannot read is a way round the moderator: write a
clean article, put the scam in the picture. Each kind gets the strictest rule we can honestly
support, and the rules differ because what we can read differs.

**PDF** — text, with OCR for scanned pages (app/pdf.py). A page we cannot read is refused.

**Image** — OCR only. That catches the real bypass, a screenshot of a scam, which is how this
content usually arrives. It does **not** look at the picture: there is no classifier here for
nudity, violence or counterfeit goods, so a photograph of anything at all passes as long as it
carries no harmful words. An image with no text is therefore allowed — unlike a blank PDF page,
a product photo with no writing on it is completely normal, and refusing those would break the
feature for every honest seller. `visual_content_checked: false` says so in the response
rather than leaving the caller to assume otherwise.

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

    def __post_init__(self) -> None:
        if min(self.max_bytes, self.max_pixels) < 1:
            raise ValueError("media limits must be positive")


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


def image_text(data: bytes, ocr, limits: MediaLimits) -> ImageText:
    """What OCR can read out of an image. An empty result is normal for a photograph and is
    not, on its own, a reason to refuse one."""
    if ocr is None or not ocr.enabled:
        return ImageText("", 0.0)
    try:
        import io

        import numpy as np
        from PIL import Image
    except ImportError:
        log.warning("image OCR unavailable: Pillow or numpy missing")
        return ImageText("", 0.0)

    try:
        Image.MAX_IMAGE_PIXELS = limits.max_pixels          # Pillow's own bomb guard
        with Image.open(io.BytesIO(data)) as img:
            if img.width * img.height > limits.max_pixels:
                raise MediaError("media_too_large",
                                 f"the image is {img.width}x{img.height}, over the "
                                 f"{limits.max_pixels} pixel limit")
            frame = img.convert("RGB")
            array = np.asarray(frame)
    except MediaError:
        raise
    except Exception:
        log.warning("image could not be decoded", exc_info=True)   # the name is for the log, not the author
        raise MediaError("media_unreadable", "this image file appears to be damaged and could not "
                                             "be opened. Try saving it again, or upload a different one.")

    try:
        page = ocr.read_image(array)
    except Exception:
        log.exception("OCR failed on an image upload")
        # OCR fell over rather than reporting on the picture, so nothing is known about it.
        # Reporting "no text" here would publish it as checked.
        return ImageText("", 0.0, failed=True)
    return ImageText(page.text, page.confidence, page.boxes,
                     has_text=page.has_text, readable=ocr.config.reads_as_text(page))
