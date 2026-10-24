"""What an uploaded video SAYS, as English text the ordinary text pipeline can judge.

Before this, a video was published unwatched (`media.allow_unchecked_video`). Reading frames is
a different project; reading the audio is not, because speech turns into the one thing this
service already moderates well: text. The chain is

    upload -> temp file -> probe (has audio? how long?) -> ffmpeg 16 kHz mono WAV
           -> Faster-Whisper (English text, translated if another language is spoken)
           -> POST /v1/moderate pipeline  (gate + DeBERTa + the admin's level for the surface)

so a video is judged by exactly the rules a typed article is, at exactly the bar the admin set
for that surface. Nothing here decides allow/reject: it only produces the text, and the API
hands that to `_moderate`.

The stages are the project's own (`pipeline/`, the offline transcription pipeline that already
shipped with `POST /v1/media/transcribe`), not a second copy.

THREE WAYS IT CAN NOT PRODUCE TEXT, and each is a different answer:

* `VideoUnavailable` - the check cannot run AT ALL (a package or the Whisper model is missing).
  The service behaves exactly as it did before this existed, i.e. `media.allow_unchecked_video`
  decides. A deployment that never installed faster-whisper must keep working.
* `VideoFailed` - it was running and broke. Refused with a retry message, like OCR falling
  over: publishing something nobody listened to is the hole this closes, and the author did
  nothing wrong.
* `MediaError` - the file is the problem (damaged, longer than the limit). The author is told.

A video with no audio track, or with no speech in it, yields empty text: there is nothing
spoken to judge, so it is allowed - and the result says `visual_content_checked: false`,
because the PICTURES were never looked at and nobody may read an allow as "we watched it".
"""
from __future__ import annotations

import logging
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.media import MediaError

log = logging.getLogger(__name__)


class VideoUnavailable(Exception):
    """The audio check cannot run on this machine (missing package or model)."""


class VideoFailed(Exception):
    """The audio check was running and failed; the video could not be checked just now."""


@dataclass(frozen=True)
class VideoConfig:
    enabled: bool = False                # off in code: the model is not in git. settings.yaml turns it on.
    max_bytes: int = 100_000_000         # one video file
    max_seconds: float = 600.0           # longer is refused, never half-listened-to
    model_size: str = "small"            # multilingual: "*.en" models cannot translate other languages
    device: str = "auto"                 # auto | cpu | cuda
    compute_type: str = "auto"
    cpu_threads: int = 4
    vad_filter: bool = True              # skip silence/music: fewer invented words
    max_concurrent: int = 1              # transcriptions at once; each one uses all the CPU it is given
    ffmpeg_timeout: float = 120.0        # seconds the audio extraction may run

    def __post_init__(self) -> None:
        if self.max_bytes < 1 or self.max_seconds <= 0 or self.max_concurrent < 1:
            raise ValueError("video limits must be positive")
        if self.model_size.endswith(".en"):
            raise ValueError("video.model_size must be multilingual (not '*.en'): speech in "
                             "another language could not be translated to English")


@dataclass(frozen=True)
class Speech:
    """The result of listening to one video."""

    text: str                            # English; "" when nothing was said
    language: str | None
    language_probability: float
    translated: bool                     # True when the speech was not English and was translated
    duration_seconds: float
    has_audio: bool
    segments: tuple[dict, ...] = ()      # [{start, end, char_start, char_end}] into `text`
    processing_seconds: float = 0.0
    # What was actually SAID, in its own language, when `text` is a translation. Kept because a
    # translation can drop or soften an abusive word; the API checks this text against the gate too.
    source_text: str = ""

    @property
    def has_speech(self) -> bool:
        return bool(self.text.strip())

    def as_dict(self) -> dict:
        return {
            "audio_found": self.has_audio,
            "speech_found": self.has_speech,
            "language": self.language,
            "language_probability": round(self.language_probability, 4),
            "translated_to_english": self.translated,
            "duration_seconds": round(self.duration_seconds, 2),
            "transcript": self.text,
            "original_transcript": self.source_text,
            # Lets the author find a flagged sentence in the footage: issues carry offsets into
            # `transcript`, and this maps each offset range to a time range.
            "segments": [dict(s) for s in self.segments],
            "processing_seconds": round(self.processing_seconds, 2),
        }


# (language, probability, segments, text, translated[, source_text]). Injectable so tests need no model.
Transcriber = Callable[[Path], "tuple[str, float, list, str, bool]"]

_RETRY_UNAVAILABLE_AFTER = 300.0     # a missing model is re-tried every 5 minutes, not on every upload


def _suffix(data: bytes) -> str:
    """A file extension from the bytes. The upload's own name is never trusted or used."""
    head = data[:16]
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm"
    if head[:4] == b"OggS":
        return ".ogg"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return ".avi"
    return ".mp4"


class VideoCheck:
    """Loaded configuration plus (lazily) the Whisper model. `enabled` is False until configured."""

    def __init__(self, config: VideoConfig | None = None, transcriber: Transcriber | None = None):
        self.config = config or VideoConfig()
        self._injected = transcriber
        self._stages = None
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(self.config.max_concurrent)
        self._unavailable: tuple[float, str] | None = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def status(self) -> dict:
        """For /ready: whether spoken words are actually being checked, readable from outside."""
        reason = self._unavailable[1] if self._unavailable else None
        return {"enabled": self.enabled, "model": self.config.model_size,
                "ready": self.enabled and self._stages is not None,
                "unavailable_reason": reason}

    # ---- loading ----------------------------------------------------------------------

    def warm(self) -> None:
        """Load Whisper now, so the first upload does not pay for (or time out during) a
        download of several hundred MB. Called once at start-up from wsgi.py; failure is
        already logged by `_load` and leaves the check unavailable, never crashes the server."""
        try:
            self._load()
        except (VideoUnavailable, VideoFailed):
            pass

    def _load(self):
        if self._stages is not None:                        # the normal case: nothing to lock
            return self._stages
        # Someone is already loading the model (the start-up warm-up, or another upload). An
        # upload must not wait minutes for it, and must not slip through unchecked either:
        # it is told to retry.
        if not self._lock.acquire(blocking=False):
            raise VideoFailed("the video check is still starting up")
        try:
            if self._stages is not None:
                return self._stages
            if self._unavailable and time.monotonic() - self._unavailable[0] < _RETRY_UNAVAILABLE_AFTER:
                raise VideoUnavailable(self._unavailable[1])
            try:
                root = Path(__file__).resolve().parents[2]          # the repo root holds pipeline/
                if str(root) not in sys.path:
                    sys.path.insert(0, str(root))
                from pipeline.config import PipelineConfig
                from pipeline.stages.extraction import AudioExtractionStage
                from pipeline.stages.ingestion import VideoIngestionStage
                from pipeline.stages.transcription import TranscriptionStage

                cfg = PipelineConfig(
                    model_size=self.config.model_size, device=self.config.device,
                    compute_type=self.config.compute_type, cpu_threads=self.config.cpu_threads,
                    vad_filter=self.config.vad_filter, word_timestamps=False,
                    keep_extracted_audio=False,
                )
                extraction = AudioExtractionStage(
                    sample_rate=cfg.audio_sample_rate, channels=cfg.audio_channels,
                    # +1s so a file that LIES about its length is caught by the length check
                    # below rather than silently cut off at the limit.
                    max_seconds=self.config.max_seconds + 1, timeout=self.config.ffmpeg_timeout)
                transcription = TranscriptionStage(config=cfg)
                if self._injected is None:
                    _ = transcription.model            # loads (or downloads) Whisper now, once
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                self._unavailable = (time.monotonic(), reason)
                log.warning("video audio check unavailable (%s). Videos are handled by "
                            "media.allow_unchecked_video until this is fixed. Install the "
                            "packages in requirements.txt and let the Whisper model download.", reason)
                raise VideoUnavailable(reason) from exc
            self._stages = (VideoIngestionStage(), extraction, transcription)
            self._unavailable = None
            log.info("video audio check ready (whisper %s)", self.config.model_size)
            return self._stages
        finally:
            self._lock.release()

    # ---- use --------------------------------------------------------------------------

    def check(self, data: bytes) -> Speech:
        """Listen to one uploaded video. Raises VideoUnavailable / VideoFailed / MediaError."""
        ingestion, extraction, transcription = self._load()
        from pipeline.stages.extraction import AudioExtractionError
        from pipeline.stages.ingestion import IngestionError, NoAudioStreamError

        started = time.perf_counter()
        workdir = Path(tempfile.mkdtemp(prefix="bonc_video_"))
        try:
            video = workdir / f"upload{_suffix(data)}"
            video.write_bytes(data)

            try:
                meta = ingestion.process(video)
            except NoAudioStreamError:
                return Speech("", None, 0.0, False, 0.0, has_audio=False,
                              processing_seconds=time.perf_counter() - started)
            except IngestionError as exc:
                log.warning("video could not be probed: %s", exc)
                raise MediaError("media_unreadable", "this video file appears to be damaged or is "
                                 "in a format that could not be opened. Try exporting it again "
                                 "as MP4.") from None
            if meta.duration_seconds > self.config.max_seconds:
                raise self._too_long(meta.duration_seconds)

            try:
                wav = extraction.extract(video, workdir / "audio.wav")
            except AudioExtractionError as exc:
                log.warning("audio could not be extracted: %s", exc)
                raise MediaError("media_unreadable", "the sound in this video could not be read. "
                                 "Try exporting it again as MP4.") from None
            # 16 kHz mono 16-bit = 32,000 bytes a second. Measured on the real audio, because the
            # container's own claim about its length is the author's to make.
            audio_seconds = max(0.0, (wav.stat().st_size - 44) / 32000.0)
            if audio_seconds > self.config.max_seconds + 0.5:
                raise self._too_long(audio_seconds)

            try:
                with self._slots:
                    language, probability, segments, text, translated, *rest = self._transcribe(
                        transcription, wav)
                    source_text = rest[0] if rest else ""
            except Exception as exc:
                log.exception("transcription failed")
                raise VideoFailed(str(exc)) from exc

            text, mapped = _join(segments, text)
            return Speech(text, language, float(probability or 0.0), bool(translated),
                          max(meta.duration_seconds, audio_seconds), True, tuple(mapped),
                          time.perf_counter() - started, (source_text or "").strip())
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _transcribe(self, transcription, wav: Path):
        if self._injected is not None:
            return self._injected(wav)
        return transcription.transcribe_english(wav)

    def _too_long(self, seconds: float) -> MediaError:
        return MediaError("media_too_long",
                          f"this video is {seconds / 60:.1f} minutes long; the limit is "
                          f"{self.config.max_seconds / 60:.0f} minutes. A video cannot be "
                          f"published unless all of it can be checked.")


def _join(segments, text: str) -> tuple[str, list[dict]]:
    """Rebuild the transcript from the segments, recording where each one landed."""
    parts: list[str] = []
    mapped: list[dict] = []
    pos = 0
    for seg in segments:
        t = (getattr(seg, "text", "") or "").strip()
        if not t:
            continue
        if parts:
            pos += 1                                  # the joining space
        mapped.append({"start": round(float(seg.start), 2), "end": round(float(seg.end), 2),
                       "char_start": pos, "char_end": pos + len(t)})
        parts.append(t)
        pos += len(t)
    return (" ".join(parts) if parts else (text or "").strip()), mapped
