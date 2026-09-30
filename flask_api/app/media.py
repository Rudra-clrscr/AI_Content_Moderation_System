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
project. The default is to refuse, because accepting an unchecked video is the same hole as
accepting an unchecked scan. `media.allow_unchecked_video` exists for deployments that would
rather take the risk, and it is named so that turning it on is a decision someone makes on
purpose.

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


def image_text(data: bytes, ocr, limits: MediaLimits) -> tuple[str, float]:
    """Text OCR can read out of an image, and its mean confidence. ("", 0.0) when there is
    none — which is normal for a photograph and is not, on its own, a reason to refuse."""
    if ocr is None or not ocr.enabled:
        return "", 0.0
    try:
        import io

        import numpy as np
        from PIL import Image
    except ImportError:
        log.warning("image OCR unavailable: Pillow or numpy missing")
        return "", 0.0

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
    except Exception as exc:
        raise MediaError("media_unreadable", f"the image could not be decoded ({type(exc).__name__})")

    try:
        result = ocr.read_image(array)
    except Exception:
        log.exception("OCR failed on an image upload")
        return "", 0.0
    return result.text, result.confidence
