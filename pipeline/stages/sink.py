"""
Transcript Sink & Exporter Stage.
Formats and writes transcription outputs into TXT, SRT, VTT, and JSON.
"""

from pathlib import Path
from typing import Dict, List, Set
import json

from pipeline.models import PipelineResult, TranscriptSegment, VideoMetadata, format_timestamp


class TranscriptSinkStage:
    """
    Handles formatting and writing transcript outputs to multiple standard formats.
    """

    def __init__(self, output_dir: Path | str = "./transcripts", formats: Set[str] | None = None):
        self.output_dir = Path(output_dir).resolve()
        self.formats = formats or {"txt", "srt", "vtt", "json"}

    @staticmethod
    def to_txt(full_text: str, segments: List[TranscriptSegment], include_timestamps: bool = False) -> str:
        """Format as plain text, optionally with timestamps."""
        if not include_timestamps:
            return full_text.strip() + "\n"
        
        lines = []
        for seg in segments:
            lines.append(f"[{seg.formatted_time_range}] {seg.text}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def to_srt(segments: List[TranscriptSegment]) -> str:
        """Format segments as standard SubRip (.srt) subtitles."""
        srt_blocks = []
        for seg in segments:
            start_ts = format_timestamp(seg.start, decimal_separator=",")
            end_ts = format_timestamp(seg.end, decimal_separator=",")
            srt_blocks.append(f"{seg.id}\n{start_ts} --> {end_ts}\n{seg.text}\n")
        return "\n".join(srt_blocks)

    @staticmethod
    def to_vtt(segments: List[TranscriptSegment]) -> str:
        """Format segments as WebVTT (.vtt) subtitles."""
        vtt_lines = ["WEBVTT\n"]
        for seg in segments:
            start_ts = format_timestamp(seg.start, decimal_separator=".")
            end_ts = format_timestamp(seg.end, decimal_separator=".")
            vtt_lines.append(f"{seg.id}\n{start_ts} --> {end_ts}\n{seg.text}\n")
        return "\n".join(vtt_lines)

    @staticmethod
    def to_json(result: PipelineResult) -> str:
        """Format complete pipeline result as pretty-printed JSON."""
        return json.dumps(result.to_dict(), indent=2, ensure_ascii=False)

    def export(self, result: PipelineResult, base_name: str) -> Dict[str, str]:
        """
        Write files for all configured formats.
        Returns dictionary mapping format extension to exported file path.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        exported: Dict[str, str] = {}

        # 1. Plain text (.txt)
        if "txt" in self.formats:
            txt_path = self.output_dir / f"{base_name}.txt"
            txt_content = self.to_txt(result.full_text, result.segments)
            txt_path.write_text(txt_content, encoding="utf-8")
            exported["txt"] = str(txt_path)

        # 2. SubRip Subtitles (.srt)
        if "srt" in self.formats:
            srt_path = self.output_dir / f"{base_name}.srt"
            srt_content = self.to_srt(result.segments)
            srt_path.write_text(srt_content, encoding="utf-8")
            exported["srt"] = str(srt_path)

        # 3. WebVTT (.vtt)
        if "vtt" in self.formats:
            vtt_path = self.output_dir / f"{base_name}.vtt"
            vtt_content = self.to_vtt(result.segments)
            vtt_path.write_text(vtt_content, encoding="utf-8")
            exported["vtt"] = str(vtt_path)

        # 4. JSON (.json)
        if "json" in self.formats:
            json_path = self.output_dir / f"{base_name}.json"
            # Populate exported_files inside result dict before writing json
            result.exported_files = exported
            json_content = self.to_json(result)
            json_path.write_text(json_content, encoding="utf-8")
            exported["json"] = str(json_path)

        result.exported_files = exported
        return exported
