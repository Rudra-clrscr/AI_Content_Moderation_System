"""
Video Transcription Pipeline Orchestrator.
Coordinates the end-to-end data pipeline:
Ingestion -> Audio Extraction -> Speech Recognition -> Format Export.
"""

from pathlib import Path
from typing import Callable, Dict, List, Optional
import os
import time

from pipeline.config import PipelineConfig
from pipeline.models import PipelineResult, TranscriptSegment, VideoMetadata
from pipeline.stages.ingestion import VideoIngestionStage
from pipeline.stages.extraction import AudioExtractionStage
from pipeline.stages.transcription import TranscriptionStage
from pipeline.stages.sink import TranscriptSinkStage


StageCallback = Callable[[str, Dict], None]


class VideoTranscriptionPipeline:
    """
    Main orchestrator for video transcription data pipelines.
    Provides single-video and batch execution with progress hooks.
    """

    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        on_stage_start: Optional[StageCallback] = None,
        on_stage_complete: Optional[StageCallback] = None,
    ):
        self.config = config or PipelineConfig()
        self.on_stage_start = on_stage_start
        self.on_stage_complete = on_stage_complete

        # Instantiate pipeline stages
        self.ingestion_stage = VideoIngestionStage()
        self.extraction_stage = AudioExtractionStage(
            sample_rate=self.config.audio_sample_rate,
            channels=self.config.audio_channels,
            temp_dir=self.config.temp_dir,
        )
        self.transcription_stage = TranscriptionStage(config=self.config)
        self.sink_stage = TranscriptSinkStage(
            output_dir=self.config.output_dir,
            formats=self.config.output_formats,
        )

    def _notify_start(self, stage: str, **kwargs):
        if self.on_stage_start:
            self.on_stage_start(stage, kwargs)

    def _notify_complete(self, stage: str, **kwargs):
        if self.on_stage_complete:
            self.on_stage_complete(stage, kwargs)

    def process(
        self,
        video_path: Path | str,
        on_segment_callback: Optional[Callable[[TranscriptSegment, float], None]] = None,
    ) -> PipelineResult:
        """
        Execute end-to-end transcription pipeline for a single video.
        """
        start_time = time.time()
        video_p = Path(video_path).resolve()
        audio_path: Optional[Path] = None

        try:
            # 1. INGESTION & VALIDATION
            self._notify_start("ingestion", file=str(video_p))
            metadata: VideoMetadata = self.ingestion_stage.process(video_p)
            self._notify_complete("ingestion", metadata=metadata)

            # 2. AUDIO EXTRACTION
            self._notify_start("extraction", video=str(video_p))
            audio_path = self.extraction_stage.process(video_p)
            self._notify_complete("extraction", audio_path=str(audio_path))

            # 3. SPEECH TRANSCRIPTION
            self._notify_start("transcription", audio=str(audio_path), model=self.config.model_size)
            (
                detected_lang,
                lang_prob,
                segments,
                full_text,
            ) = self.transcription_stage.process(
                audio_path, on_segment_callback=on_segment_callback
            )
            self._notify_complete(
                "transcription",
                language=detected_lang,
                segments_count=len(segments),
            )

            # 4. SINK / EXPORT FORMATS
            elapsed_total = time.time() - start_time
            result = PipelineResult(
                video_metadata=metadata,
                detected_language=detected_lang,
                language_probability=lang_prob,
                full_text=full_text,
                segments=segments,
                processing_time_seconds=elapsed_total,
                extracted_audio_path=str(audio_path) if self.config.keep_extracted_audio else None,
            )

            self._notify_start("sink", output_dir=str(self.config.output_dir))
            exported_files = self.sink_stage.export(result, base_name=video_p.stem)
            self._notify_complete("sink", exported_files=exported_files)

            return result

        finally:
            # Clean up intermediate extracted audio if not configured to keep
            if audio_path and not self.config.keep_extracted_audio:
                try:
                    if audio_path.exists():
                        audio_path.unlink()
                except OSError:
                    pass

    def process_batch(
        self,
        video_paths: List[Path | str],
        continue_on_error: bool = True,
        on_progress: Optional[Callable[[int, int, Path, Optional[PipelineResult], Optional[Exception]], None]] = None,
    ) -> List[PipelineResult]:
        """
        Process multiple video files sequentially.
        """
        results: List[PipelineResult] = []
        total = len(video_paths)

        for index, item in enumerate(video_paths, start=1):
            v_path = Path(item)
            try:
                res = self.process(v_path)
                results.append(res)
                if on_progress:
                    on_progress(index, total, v_path, res, None)
            except Exception as exc:
                if on_progress:
                    on_progress(index, total, v_path, None, exc)
                if not continue_on_error:
                    raise

        return results
