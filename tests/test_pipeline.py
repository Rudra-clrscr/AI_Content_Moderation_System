"""
Automated integration tests for Video-to-Transcript Data Pipeline.
"""

import sys
import unittest
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

from pipeline.config import PipelineConfig
from pipeline.models import PipelineResult, VideoMetadata
from pipeline.orchestrator import VideoTranscriptionPipeline
from pipeline.stages.ingestion import VideoIngestionStage, NoAudioStreamError, UnsupportedFormatError
from pipeline.stages.extraction import AudioExtractionStage
from pipeline.stages.transcription import TranscriptionStage
from pipeline.stages.sink import TranscriptSinkStage


class TestVideoTranscriptionPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_video = Path("sample_test_video.mp4").resolve()
        if not cls.test_video.exists():
            from generate_sample_video import generate_sample_video
            generate_sample_video(str(cls.test_video))

    def test_01_ingestion_stage(self):
        stage = VideoIngestionStage()
        meta = stage.process(self.test_video)

        self.assertIsInstance(meta, VideoMetadata)
        self.assertEqual(meta.file_name, "sample_test_video.mp4")
        self.assertTrue(meta.has_audio)
        self.assertGreater(meta.duration_seconds, 0)
        self.assertGreater(meta.file_size_bytes, 0)

    def test_02_audio_extraction_stage(self):
        stage = AudioExtractionStage()
        audio_path = stage.process(self.test_video)

        try:
            self.assertTrue(audio_path.exists())
            self.assertTrue(audio_path.name.endswith(".wav"))
            self.assertGreater(audio_path.stat().st_size, 1000)
        finally:
            if audio_path.exists():
                audio_path.unlink()

    def test_03_end_to_end_pipeline(self):
        output_dir = Path("test_output_transcripts")
        config = PipelineConfig(
            model_size="tiny",
            device="cpu",
            output_dir=output_dir,
            output_formats={"txt", "srt", "vtt", "json"},
            word_timestamps=True,
            keep_extracted_audio=False,
        )

        pipeline = VideoTranscriptionPipeline(config=config)
        result = pipeline.process(self.test_video)

        self.assertIsInstance(result, PipelineResult)
        self.assertEqual(result.detected_language, "en")
        self.assertGreater(len(result.segments), 0)
        self.assertIn("video", result.full_text.lower())

        # Verify exported files
        self.assertTrue((output_dir / "sample_test_video.txt").exists())
        self.assertTrue((output_dir / "sample_test_video.srt").exists())
        self.assertTrue((output_dir / "sample_test_video.vtt").exists())
        self.assertTrue((output_dir / "sample_test_video.json").exists())

        # Clean up test output
        for f in output_dir.glob("sample_test_video.*"):
            f.unlink()
        if output_dir.exists():
            output_dir.rmdir()


if __name__ == "__main__":
    unittest.main()
