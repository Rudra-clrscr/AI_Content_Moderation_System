"""
Pipeline processing stages.
"""

from pipeline.stages.ingestion import VideoIngestionStage
from pipeline.stages.extraction import AudioExtractionStage
from pipeline.stages.transcription import TranscriptionStage
from pipeline.stages.sink import TranscriptSinkStage

__all__ = [
    "VideoIngestionStage",
    "AudioExtractionStage",
    "TranscriptionStage",
    "TranscriptSinkStage",
]
