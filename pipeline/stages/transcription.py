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
            
            try:
                self._model = WhisperModel(
                    self.config.model_size,
                    device=device,
                    compute_type=compute_type,
                    cpu_threads=self.config.cpu_threads,
                    num_workers=self.config.num_workers,
                )
            except Exception as e:
                if device == "cuda":
                    self._model = WhisperModel(
                        self.config.model_size,
                        device="cpu",
                        compute_type="int8",
                        cpu_threads=self.config.cpu_threads,
                        num_workers=self.config.num_workers,
                    )
                else:
                    raise
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
                task=self.config.task,
                beam_size=self.config.beam_size,
                vad_filter=self.config.vad_filter,
                word_timestamps=self.config.word_timestamps,
                initial_prompt=self.config.initial_prompt,
                condition_on_previous_text=self.config.condition_on_previous_text,
                no_speech_threshold=self.config.no_speech_threshold,
                compression_ratio_threshold=self.config.compression_ratio_threshold,
                log_prob_threshold=self.config.log_prob_threshold,
                hallucination_silence_threshold=self.config.hallucination_silence_threshold,
                repetition_penalty=self.config.repetition_penalty,
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
            err_msg = str(e).lower()
            if "cublas" in err_msg or "cuda" in err_msg or "cudnn" in err_msg:
                # CUDA library load failed during execution, fallback to CPU and retry
                self._model = WhisperModel(
                    self.config.model_size,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=self.config.cpu_threads,
                    num_workers=self.config.num_workers,
                )
                return self.transcribe(audio_path, on_segment_callback=on_segment_callback)
            raise TranscriptionError(f"Transcription failed for '{audio_p.name}': {e}") from e

    def transcribe_english(
        self,
        audio_path: Path | str,
        on_segment_callback: Optional[Callable[[TranscriptSegment, float], None]] = None,
    ) -> Tuple[str, float, List[TranscriptSegment], str, bool, str]:
        """English text for the speech in `audio_path`, whatever language is spoken.

        Returns (detected_language, language_probability, segments, full_text, translated,
        source_text). `source_text` is what was actually said, in the original language, and is only
        filled when the speech was translated: a translation can soften or drop an abusive word, so
        the original wording is kept for a safety check of its own.

        Whisper detects the language before it yields the first segment, so the language is
        read first and the segments only after we know which job to run: English speech is
        transcribed as it was said; any other language is re-run as Whisper's `translate`
        task. The first run's lazy generator is dropped unread, so the extra cost is only the
        language detection. Needs a multilingual model (not "*.en").
        """
        audio_p = Path(audio_path).resolve()
        if not audio_p.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_p}")
        lang_cfg = self.config.language
        if lang_cfg and lang_cfg.lower() in ("auto", "none"):
            lang_cfg = None

        def run(task: str, language: Optional[str]):
            return self.model.transcribe(
                str(audio_p),
                language=language,
                task=task,
                beam_size=self.config.beam_size,
                vad_filter=self.config.vad_filter,
                word_timestamps=False,
                initial_prompt=self.config.initial_prompt,
                condition_on_previous_text=self.config.condition_on_previous_text,
                no_speech_threshold=self.config.no_speech_threshold,
                compression_ratio_threshold=self.config.compression_ratio_threshold,
                log_prob_threshold=self.config.log_prob_threshold,
                hallucination_silence_threshold=self.config.hallucination_silence_threshold,
                repetition_penalty=self.config.repetition_penalty,
            )

        try:
            segments_iter, info = run("transcribe", lang_cfg)
            language, probability, translated = info.language, info.language_probability, False
            source_text = ""
            if language != "en":
                # Read the speech as it was said FIRST: this is the text that still contains
                # the abusive word, before translation has a chance to clean it up.
                source_text = " ".join(
                    t for t in (seg.text.strip() for seg in segments_iter) if t)
                segments_iter, info = run("translate", language)
                translated = True
            total = info.duration
            segments: List[TranscriptSegment] = []
            parts: List[str] = []
            for idx, seg in enumerate(segments_iter, start=1):
                text = seg.text.strip()
                ts = TranscriptSegment(
                    id=idx, start=seg.start, end=seg.end, text=text,
                    avg_logprob=getattr(seg, "avg_logprob", 0.0),
                    no_speech_prob=getattr(seg, "no_speech_prob", 0.0), words=[],
                )
                segments.append(ts)
                if text:
                    parts.append(text)
                if on_segment_callback:
                    on_segment_callback(ts, min(1.0, seg.end / total) if total > 0 else 0.0)
            return language, probability, segments, " ".join(parts), translated, source_text
        except Exception as e:
            err_msg = str(e).lower()
            if "cublas" in err_msg or "cuda" in err_msg or "cudnn" in err_msg:
                self._model = WhisperModel(
                    self.config.model_size, device="cpu", compute_type="int8",
                    cpu_threads=self.config.cpu_threads, num_workers=self.config.num_workers,
                )
                return self.transcribe_english(audio_path, on_segment_callback=on_segment_callback)
            raise TranscriptionError(f"Transcription failed for '{audio_p.name}': {e}") from e

    def process(
        self,
        audio_path: Path | str,
        on_segment_callback: Optional[Callable[[TranscriptSegment, float], None]] = None,
    ) -> Tuple[str, float, List[TranscriptSegment], str]:
        """Execute transcription stage."""
        return self.transcribe(audio_path, on_segment_callback=on_segment_callback)
