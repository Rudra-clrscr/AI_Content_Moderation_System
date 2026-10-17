"""
Configuration dataclass and options for the Video-to-Transcript Data Pipeline.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Literal, Optional, Set
import os

# Disable HF symlinks warning on Windows
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"


ModelSize = Literal["tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en", "large-v3"]
ComputeDevice = Literal["auto", "cpu", "cuda"]
ComputeType = Literal["auto", "int8", "int8_float16", "float16", "float32", "default"]
OutputFormat = Literal["txt", "srt", "vtt", "json"]


@dataclass
class PipelineConfig:
    """
    Configuration settings for VideoTranscriptionPipeline.
    """
    # Whisper Model Options
    model_size: ModelSize = "base"
    device: ComputeDevice = "auto"
    compute_type: ComputeType = "auto"
    cpu_threads: int = 4
    num_workers: int = 1

    # Transcription Options
    language: Optional[str] = None  # None / "auto" triggers Whisper language detection
    vad_filter: bool = True  # Silero VAD to filter out non-speech segments
    word_timestamps: bool = True  # Generate word-level timestamps
    initial_prompt: Optional[str] = None  # Context prompt to guide vocabulary/spelling

    # Audio Extraction Options
    audio_sample_rate: int = 16000  # 16kHz required by Whisper
    audio_channels: int = 1         # Mono
    keep_extracted_audio: bool = False  # Keep intermediate .wav file after completion

    # Sink / Output Options
    output_dir: Path = field(default_factory=lambda: Path("./transcripts"))
    output_formats: Set[OutputFormat] = field(
        default_factory=lambda: {"txt", "srt", "vtt", "json"}
    )
    temp_dir: Optional[Path] = None  # Custom temp directory, or system temp if None

    def resolved_device(self) -> str:
        """Resolve 'auto' device to 'cuda' (if torch/ctranslate2 cuda available) or 'cpu'."""
        if self.device != "auto":
            return self.device
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                return "cuda"
        except Exception:
            pass
        return "cpu"

    def resolved_compute_type(self) -> str:
        """Resolve compute type based on device."""
        if self.compute_type != "auto":
            return self.compute_type
        device = self.resolved_device()
        if device == "cuda":
            return "float16"
        return "int8"
