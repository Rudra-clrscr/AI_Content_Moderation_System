"""
Audio Extraction Stage.
Extracts and normalizes the audio stream from a video into a 16kHz mono WAV file.
"""

from pathlib import Path
from typing import Optional
import os
import subprocess
import tempfile
import time
import imageio_ffmpeg


class AudioExtractionError(Exception):
    """Raised when audio extraction from video fails."""
    pass


class AudioExtractionStage:
    """
    Extracts the audio track from video files and converts it into
    standard 16kHz mono PCM 16-bit WAV for optimal ASR performance.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        temp_dir: Optional[Path] = None,
        max_seconds: Optional[float] = None,
        timeout: Optional[float] = None,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.temp_dir = temp_dir
        self.max_seconds = max_seconds   # None = whole track (the CLI); the web service sets a cap
        self.timeout = timeout           # seconds ffmpeg may run; None = no limit
        self.ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

    def get_output_audio_path(self, video_path: Path) -> Path:
        """Generate a path for the extracted audio file."""
        if self.temp_dir:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            return self.temp_dir / f"{video_path.stem}_audio.wav"
        
        # Use system temp directory
        temp_dir = Path(tempfile.gettempdir()) / "video_transcripts_audio"
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir / f"{video_path.stem}_{int(time.time())}.wav"

    def extract(self, video_path: Path | str, target_audio_path: Optional[Path | str] = None) -> Path:
        """
        Extract audio from video to target_audio_path or auto-generated WAV.
        """
        video_p = Path(video_path).resolve()
        if not video_p.exists():
            raise FileNotFoundError(f"Video file does not exist: {video_p}")

        out_audio_p = (
            Path(target_audio_path).resolve()
            if target_audio_path
            else self.get_output_audio_path(video_p)
        )
        out_audio_p.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.ffmpeg_exe,
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(video_p),
            *(["-t", str(self.max_seconds)] if self.max_seconds else []),
            "-vn",                      # Disable video recording
            "-acodec", "pcm_s16le",     # 16-bit uncompressed PCM
            "-ar", str(self.sample_rate), # 16000 Hz
            "-ac", str(self.channels),  # 1 channel (mono)
            "-y",                       # Overwrite output without asking
            str(out_audio_p),
        ]

        try:
            start_t = time.time()
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=self.timeout,
            )
            elapsed = time.time() - start_t

            if result.returncode != 0:
                raise AudioExtractionError(
                    f"FFmpeg audio extraction failed (code {result.returncode}): {result.stderr.strip()}"
                )

            if not out_audio_p.exists() or out_audio_p.stat().st_size == 0:
                raise AudioExtractionError(
                    f"Extracted audio file is missing or 0 bytes: {out_audio_p}"
                )

            return out_audio_p

        except subprocess.TimeoutExpired:
            raise AudioExtractionError(f"Audio extraction took longer than {self.timeout}s")
        except Exception as e:
            if not isinstance(e, AudioExtractionError):
                raise AudioExtractionError(f"Error during audio extraction: {e}") from e
            raise

    def process(self, video_path: Path | str, target_audio_path: Optional[Path | str] = None) -> Path:
        """Execute audio extraction stage."""
        return self.extract(video_path, target_audio_path)
