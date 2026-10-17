"""
Video Ingestion & Validation Stage.
"""

from pathlib import Path
from typing import Optional, Set
import os
import av

from pipeline.models import VideoMetadata


SUPPORTED_VIDEO_EXTENSIONS: Set[str] = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv",
    ".wmv", ".m4v", ".ts", ".mpeg", ".mpg", ".3gp"
}

SUPPORTED_AUDIO_EXTENSIONS: Set[str] = {
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".wma", ".opus"
}

SUPPORTED_EXTENSIONS = SUPPORTED_VIDEO_EXTENSIONS | SUPPORTED_AUDIO_EXTENSIONS


class IngestionError(Exception):
    """Raised when video file cannot be ingested or validated."""
    pass


class UnsupportedFormatError(IngestionError):
    """Raised when file extension or format is unsupported."""
    pass


class NoAudioStreamError(IngestionError):
    """Raised when video file contains no audio track."""
    pass


class VideoIngestionStage:
    """
    Ingests and inspects input video files, extracting technical metadata
    and verifying audio stream presence before downstream processing.
    """

    def __init__(self, allowed_extensions: Optional[Set[str]] = None):
        self.allowed_extensions = allowed_extensions or SUPPORTED_EXTENSIONS

    def validate(self, file_path: Path | str) -> Path:
        """Validate that file exists, is accessible, and has an allowed format."""
        path = Path(file_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Input file not found at: {path}")
        if not path.is_file():
            raise IngestionError(f"Target path is not a file: {path}")

        ext = path.suffix.lower()
        if ext not in self.allowed_extensions:
            allowed_list = ", ".join(sorted(self.allowed_extensions))
            raise UnsupportedFormatError(
                f"Unsupported file format '{ext}'. Supported formats: {allowed_list}"
            )

        if path.stat().st_size == 0:
            raise IngestionError(f"Input file is empty (0 bytes): {path}")

        return path

    def extract_metadata(self, file_path: Path | str) -> VideoMetadata:
        """
        Probe media file using PyAV to extract technical metadata.
        """
        path = self.validate(file_path)
        file_size = path.stat().st_size

        try:
            with av.open(str(path)) as container:
                # Duration is in container time_base or microseconds
                duration_sec = 0.0
                if container.duration is not None:
                    duration_sec = float(container.duration) / float(av.time_base)

                video_streams = container.streams.video
                audio_streams = container.streams.audio

                video_codec = None
                width = None
                height = None
                fps = None

                if video_streams:
                    v_stream = video_streams[0]
                    video_codec = v_stream.codec_context.name if v_stream.codec_context else None
                    width = v_stream.width
                    height = v_stream.height
                    if v_stream.average_rate:
                        fps = float(v_stream.average_rate)
                    if duration_sec <= 0 and v_stream.duration and v_stream.time_base:
                        duration_sec = float(v_stream.duration * v_stream.time_base)

                has_audio = len(audio_streams) > 0
                audio_codec = None
                if audio_streams:
                    a_stream = audio_streams[0]
                    audio_codec = a_stream.codec_context.name if a_stream.codec_context else None
                    if duration_sec <= 0 and a_stream.duration and a_stream.time_base:
                        duration_sec = float(a_stream.duration * a_stream.time_base)

                if not has_audio:
                    raise NoAudioStreamError(
                        f"Video file '{path.name}' has no audio stream to transcribe."
                    )

                return VideoMetadata(
                    file_path=str(path),
                    file_name=path.name,
                    file_size_bytes=file_size,
                    duration_seconds=duration_sec,
                    video_codec=video_codec,
                    audio_codec=audio_codec,
                    width=width,
                    height=height,
                    fps=fps,
                    has_audio=has_audio,
                )
        except NoAudioStreamError:
            raise
        except Exception as e:
            raise IngestionError(f"Failed to inspect video metadata for '{path.name}': {e}") from e

    def process(self, file_path: Path | str) -> VideoMetadata:
        """Execute ingestion stage."""
        return self.extract_metadata(file_path)
