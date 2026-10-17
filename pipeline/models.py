"""
Data models and representations for the Video Transcription Pipeline.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class VideoMetadata:
    """Metadata describing the ingested video file."""
    file_path: str
    file_name: str
    file_size_bytes: int
    duration_seconds: float
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    has_audio: bool = True

    @property
    def file_size_mb(self) -> float:
        return round(self.file_size_bytes / (1024 * 1024), 2)

    @property
    def formatted_duration(self) -> str:
        mins, secs = divmod(int(self.duration_seconds), 60)
        hrs, mins = divmod(mins, 60)
        if hrs > 0:
            return f"{hrs:02d}:{mins:02d}:{secs:02d}"
        return f"{mins:02d}:{secs:02d}"


@dataclass
class WordSegment:
    """Word-level timing and confidence."""
    word: str
    start: float
    end: float
    probability: float


@dataclass
class TranscriptSegment:
    """Segment / sentence within the transcribed audio."""
    id: int
    start: float
    end: float
    text: str
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    words: List[WordSegment] = field(default_factory=list)

    @property
    def formatted_time_range(self) -> str:
        return f"{format_timestamp(self.start)} --> {format_timestamp(self.end)}"


@dataclass
class PipelineResult:
    """Comprehensive result of the transcription pipeline execution."""
    video_metadata: VideoMetadata
    detected_language: str
    language_probability: float
    full_text: str
    segments: List[TranscriptSegment]
    exported_files: Dict[str, str] = field(default_factory=dict)
    processing_time_seconds: float = 0.0
    extracted_audio_path: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def total_words(self) -> int:
        return len(self.full_text.split())

    def to_dict(self) -> Dict[str, Any]:
        """Convert result into a serializable dictionary."""
        return {
            "metadata": asdict(self.video_metadata),
            "detected_language": self.detected_language,
            "language_probability": round(self.language_probability, 4),
            "processing_time_seconds": round(self.processing_time_seconds, 2),
            "created_at": self.created_at,
            "total_words": self.total_words,
            "full_text": self.full_text,
            "exported_files": self.exported_files,
            "segments": [
                {
                    "id": seg.id,
                    "start": round(seg.start, 3),
                    "end": round(seg.end, 3),
                    "text": seg.text,
                    "avg_logprob": round(seg.avg_logprob, 3),
                    "no_speech_prob": round(seg.no_speech_prob, 3),
                    "words": [asdict(w) for w in seg.words],
                }
                for seg in self.segments
            ],
        }


def format_timestamp(seconds: float, decimal_separator: str = ",") -> str:
    """
    Format seconds into HH:MM:SS,mmm or HH:MM:SS.mmm
    """
    assert seconds >= 0, "non-negative timestamp expected"
    milliseconds = round(seconds * 1000.0)

    hours = milliseconds // 3_600_000
    milliseconds -= hours * 3_600_000

    minutes = milliseconds // 60_000
    milliseconds -= minutes * 60_000

    secs = milliseconds // 1_000
    milliseconds -= secs * 1_000

    return f"{hours:02d}:{minutes:02d}:{secs:02d}{decimal_separator}{milliseconds:03d}"
