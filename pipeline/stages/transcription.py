"""
Speech-to-Text Transcription Stage powered by Faster-Whisper.
"""

from pathlib import Path
from typing import Callable, List, Optional, Tuple
import os
import time

from faster_whisper import WhisperModel

from pipeline.config import PipelineConfig
from pipeline.models import TranscriptSegment, WordSegment


class TranscriptionError(Exception):
    """Raised when audio transcription fails."""
    pass


class TranscriptionStage:
    """
    Executes Automatic Speech Recognition (ASR) on extracted audio files
    using local Whisper models.
    """

    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()
        self._model: Optional[WhisperModel] = None

    @property
    def model(self) -> WhisperModel:
        """Lazy load and cache WhisperModel."""
        if self._model is None:
            device = self.config.resolved_device()
            compute_type = self.config.resolved_compute_type()
            
            self._model = WhisperModel(
                self.config.model_size,
                device=device,
                compute_type=compute_type,
                cpu_threads=self.config.cpu_threads,
                num_workers=self.config.num_workers,
            )
        return self._model

    def transcribe(
        self,
        audio_path: Path | str,
        on_segment_callback: Optional[Callable[[TranscriptSegment, float], None]] = None,
    ) -> Tuple[str, float, List[TranscriptSegment], str]:
        """
        Transcribes the given audio file.
        Returns:
            Tuple of (detected_language, language_probability, segments, full_text)
        """
        audio_p = Path(audio_path).resolve()
        if not audio_p.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_p}")

        try:
            # Language argument: None or 'auto' means auto-detect
            lang = self.config.language
            if lang and lang.lower() in ("auto", "none"):
                lang = None

            segments_iter, info = self.model.transcribe(
                str(audio_p),
                language=lang,
                vad_filter=self.config.vad_filter,
                word_timestamps=self.config.word_timestamps,
                initial_prompt=self.config.initial_prompt,
            )

            detected_language = info.language
            language_probability = info.language_probability
            total_duration = info.duration

            segments: List[TranscriptSegment] = []
            text_parts: List[str] = []

            for idx, seg in enumerate(segments_iter, start=1):
                words: List[WordSegment] = []
                if seg.words:
                    for w in seg.words:
                        words.append(
                            WordSegment(
                                word=w.word,
                                start=w.start,
                                end=w.end,
                                probability=getattr(w, "probability", 0.0),
                            )
                        )

                clean_text = seg.text.strip()
                trans_seg = TranscriptSegment(
                    id=idx,
                    start=seg.start,
                    end=seg.end,
                    text=clean_text,
                    avg_logprob=getattr(seg, "avg_logprob", 0.0),
                    no_speech_prob=getattr(seg, "no_speech_prob", 0.0),
                    words=words,
                )
                segments.append(trans_seg)
                if clean_text:
                    text_parts.append(clean_text)

                if on_segment_callback:
                    # Provide progress fraction if total_duration > 0
                    progress = min(1.0, seg.end / total_duration) if total_duration > 0 else 0.0
                    on_segment_callback(trans_seg, progress)

            full_text = " ".join(text_parts)
            return detected_language, language_probability, segments, full_text

        except Exception as e:
            raise TranscriptionError(f"Transcription failed for '{audio_p.name}': {e}") from e

    def process(
        self,
        audio_path: Path | str,
        on_segment_callback: Optional[Callable[[TranscriptSegment, float], None]] = None,
    ) -> Tuple[str, float, List[TranscriptSegment], str]:
        """Execute transcription stage."""
        return self.transcribe(audio_path, on_segment_callback=on_segment_callback)
