"""
Configuration dataclass and options for the Video-to-Transcript Data Pipeline.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Literal, Optional, Set
import os
import sys

# Disable HF symlinks warning on Windows
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"


def setup_windows_cuda_dlls() -> bool:
    """Ensure Windows dynamic loader can find cuBLAS / cuDNN from torch or nvidia packages."""
    if sys.platform != "win32":
        return True
    import site
    import ctypes
    dirs_to_check = []
    try:
        for sp in site.getsitepackages():
            dirs_to_check.append(os.path.join(sp, "torch", "lib"))
            nvidia_base = os.path.join(sp, "nvidia")
            if os.path.isdir(nvidia_base):
                for sub in os.listdir(nvidia_base):
                    dirs_to_check.append(os.path.join(nvidia_base, sub, "bin"))
    except Exception:
        pass
    for d in dirs_to_check:
        if os.path.isdir(d):
            try:
                os.add_dll_directory(d)
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            except Exception:
                pass
            for dll_name in ("cublas64_12.dll", "cublasLt64_12.dll", "cudart64_12.dll"):
                dll_path = os.path.join(d, dll_name)
                if os.path.exists(dll_path):
                    try:
                        ctypes.CDLL(dll_path)
                    except Exception:
                        pass
    return True


def is_cuda_available() -> bool:
    """Check if ctranslate2 can actually execute on CUDA with functional cuBLAS DLLs."""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() <= 0:
            return False
        setup_windows_cuda_dlls()
        if sys.platform == "win32":
            import ctypes
            try:
                ctypes.CDLL("cublas64_12.dll")
            except Exception:
                return False
        return True
    except Exception:
        return False


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
    cpu_threads: int = field(default_factory=lambda: min(8, max(4, os.cpu_count() or 4)))
    num_workers: int = 1

    # Transcription Options
    language: Optional[str] = None  # None / "auto" triggers Whisper language detection
    task: Literal["transcribe", "translate"] = "transcribe"  # "translate" = speech in any language -> English text
    beam_size: int = 1  # 1 = greedy decoding (fastest), >1 = beam search
    vad_filter: bool = True  # Silero VAD to filter out non-speech segments
    word_timestamps: bool = True  # Generate word-level timestamps
    initial_prompt: Optional[str] = None  # Context prompt to guide vocabulary/spelling

    # Anti-Hallucination & Decoder Guardrails
    condition_on_previous_text: bool = False  # False breaks the hallucination feedback loop across 30s chunks
    no_speech_threshold: float = 0.6  # Probability threshold to consider a segment silent/non-speech
    compression_ratio_threshold: float = 2.4  # Discard repetitive loops with high zlib compression ratio
    log_prob_threshold: Optional[float] = -1.0  # Filter out low-confidence decoder output
    hallucination_silence_threshold: Optional[float] = 2.0  # Skip silent periods when hallucination is suspected
    repetition_penalty: float = 1.0  # Repetition penalty parameter

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
        """Resolve 'auto' device to 'cuda' (if torch/ctranslate2 cuda available and functional) or 'cpu'."""
        if self.device != "auto":
            return self.device
        if is_cuda_available():
            return "cuda"
        return "cpu"

    def resolved_compute_type(self) -> str:
        """Resolve compute type based on device."""
        if self.compute_type != "auto":
            return self.compute_type
        device = self.resolved_device()
        if device == "cuda":
            return "float16"
        return "int8"
