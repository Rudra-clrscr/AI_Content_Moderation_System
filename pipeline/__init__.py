"""
Video-to-Transcript Data Pipeline Package
"""

from pipeline.config import PipelineConfig
from pipeline.models import PipelineResult, TranscriptSegment, VideoMetadata, WordSegment
from pipeline.orchestrator import VideoTranscriptionPipeline

__all__ = [
    "VideoTranscriptionPipeline",
    "PipelineConfig",
    "PipelineResult",
    "VideoMetadata",
    "TranscriptSegment",
    "WordSegment",
]

__version__ = "1.0.0"
