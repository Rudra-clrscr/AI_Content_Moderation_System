"""
Command-Line Interface (CLI) for the Video-to-Transcript Data Pipeline.
Features rich visual output, batch processing, interactive file selection, and progress monitoring.
"""

import argparse
from pathlib import Path
import sys
import time
from typing import List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.theme import Theme

from pipeline.config import PipelineConfig
from pipeline.models import PipelineResult, TranscriptSegment
from pipeline.orchestrator import VideoTranscriptionPipeline
from pipeline.stages.ingestion import SUPPORTED_EXTENSIONS

# Ensure UTF-8 output handling on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

custom_theme = Theme({
    "info": "cyan",
    "success": "bold green",
    "warning": "yellow",
    "error": "bold red",
    "highlight": "bold magenta",
})

console = Console(theme=custom_theme, highlight=False)


def print_banner():
    banner = """
  Video-to-Transcript Data Pipeline
  Extract speech transcripts, timestamps, and subtitles from video files
    """
    console.print(Panel(banner.strip(), border_style="cyan"))



def display_metadata_table(metadata):
    table = Table(title="Video Stream Metadata", show_header=True, header_style="bold cyan")
    table.add_column("Property", style="dim")
    table.add_column("Value", style="bold white")

    table.add_row("File Name", metadata.file_name)
    table.add_row("File Size", f"{metadata.file_size_mb} MB")
    table.add_row("Duration", f"{metadata.formatted_duration} ({metadata.duration_seconds:.1f}s)")
    if metadata.width and metadata.height:
        table.add_row("Resolution", f"{metadata.width}x{metadata.height}")
    if metadata.fps:
        table.add_row("Frame Rate", f"{metadata.fps:.2f} fps")
    table.add_row("Video Codec", metadata.video_codec or "N/A")
    table.add_row("Audio Codec", metadata.audio_codec or "N/A")

    console.print(table)


def display_results_summary(result: PipelineResult):
    console.print("\n[bold green][OK] Pipeline Execution Completed Successfully[/bold green]\n")

    summary_table = Table(title="Transcription Summary", show_header=True, header_style="bold magenta")
    summary_table.add_column("Metric", style="dim")
    summary_table.add_column("Value", style="bold white")

    summary_table.add_row("Detected Language", f"{result.detected_language.upper()} ({result.language_probability * 100:.1f}%)")
    summary_table.add_row("Total Segments", str(len(result.segments)))
    summary_table.add_row("Total Spoken Words", str(result.total_words))
    summary_table.add_row("Processing Time", f"{result.processing_time_seconds:.2f}s")
    console.print(summary_table)

    # Exported Files Table
    files_table = Table(title="Generated Transcript Artifacts", show_header=True, header_style="bold green")
    files_table.add_column("Format", style="cyan", width=10)
    files_table.add_column("Destination Path", style="dim white")

    for fmt, path_str in result.exported_files.items():
        files_table.add_row(f".{fmt.upper()}", path_str)

    console.print(files_table)

    # Transcript Preview Card
    preview_text = result.full_text[:300] + ("..." if len(result.full_text) > 300 else "")
    console.print(Panel(preview_text or "[dim]No speech detected.[/dim]", title="Transcript Preview", border_style="green"))


def collect_input_files(target_path: Path) -> List[Path]:
    """Resolve file path or scan directory for video files."""
    if target_path.is_file():
        return [target_path]
    
    if target_path.is_dir():
        files = []
        for ext in SUPPORTED_EXTENSIONS:
            files.extend(list(target_path.glob(f"*{ext}")))
            files.extend(list(target_path.glob(f"*{ext.upper()}")))
        return sorted(list(set(files)))
    
    return []


def parse_args():
    parser = argparse.ArgumentParser(
        description="Local AI Video-to-Transcript Data Pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-i", "--input",
        type=str,
        default=None,
        help="Path to an input video file or a directory containing videos to batch process.",
    )
    parser.add_argument(
        "-o", "--output-dir",
        type=str,
        default="./transcripts",
        help="Directory to save generated transcript files (.txt, .srt, .vtt, .json).",
    )
    parser.add_argument(
        "-m", "--model",
        type=str,
        default="base",
        choices=["tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en", "large-v3"],
        help="Whisper model size to use for transcription.",
    )
    parser.add_argument(
        "-l", "--language",
        type=str,
        default=None,
        help="Language code (e.g., 'en', 'es', 'fr', 'de'). Defaults to automatic language detection.",
    )
    parser.add_argument(
        "-d", "--device",
        type=str,
        default="auto",
        choices=["auto", "cpu", "cuda"],
        help="Device to run inference on.",
    )
    parser.add_argument(
        "-f", "--format",
        type=str,
        default="all",
        help="Comma-separated output formats: txt, srt, vtt, json, or 'all'.",
    )
    parser.add_argument(
        "--word-timestamps",
        action="store_true",
        default=True,
        help="Generate word-level timestamps in the structured JSON output.",
    )
    parser.add_argument(
        "--keep-audio",
        action="store_true",
        default=False,
        help="Preserve the extracted intermediate 16kHz WAV audio file.",
    )

    return parser.parse_args()


def run_cli():
    print_banner()
    args = parse_args()

    # Determine input path
    raw_input = args.input
    if not raw_input:
        console.print("[bold yellow]? No input file specified.[/bold yellow]")
        raw_input = console.input("  Please drag-and-drop or enter the video file path: ").strip().strip('"').strip("'")
        if not raw_input:
            console.print("[red]No video path provided. Exiting.[/red]")
            sys.exit(1)

    input_path = Path(raw_input).resolve()
    if not input_path.exists():
        console.print(f"[bold red]Error:[/bold red] Specified path does not exist: {input_path}")
        sys.exit(1)

    video_files = collect_input_files(input_path)
    if not video_files:
        console.print(f"[bold red]Error:[/bold red] No supported video or audio files found at {input_path}")
        sys.exit(1)

    # Parse formats
    if args.format.lower() == "all":
        formats = {"txt", "srt", "vtt", "json"}
    else:
        formats = {fmt.strip().lower() for fmt in args.format.split(",") if fmt.strip()}

    config = PipelineConfig(
        model_size=args.model,
        device=args.device,
        language=args.language,
        output_dir=Path(args.output_dir),
        output_formats=formats,
        word_timestamps=args.word_timestamps,
        keep_extracted_audio=args.keep_audio,
    )

    console.print(
        f"[dim]Initialized pipeline with model: [bold cyan]{config.model_size}[/bold cyan] "
        f"| device: [bold cyan]{config.resolved_device()}[/bold cyan] "
        f"| compute: [bold cyan]{config.resolved_compute_type()}[/bold cyan][/dim]\n"
    )

    pipeline = VideoTranscriptionPipeline(config=config)

    # Process files
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        for idx, video_file in enumerate(video_files, start=1):
            task_id = progress.add_task(f"Processing ({idx}/{len(video_files)}) {video_file.name}", total=100)

            def on_stage_start(stage: str, details: dict):
                stage_labels = {
                    "ingestion": "Ingesting and inspecting video stream...",
                    "extraction": "Extracting audio with FFmpeg (16kHz mono)...",
                    "transcription": f"Transcribing audio with Whisper [{config.model_size}]...",
                    "sink": "Writing subtitle and transcript files...",
                }
                progress.update(task_id, description=f"[{video_file.name}] {stage_labels.get(stage, stage)}")

            def on_segment(seg: TranscriptSegment, pct: float):
                progress.update(task_id, completed=int(pct * 90))

            pipeline.on_stage_start = on_stage_start

            try:
                result = pipeline.process(video_file, on_segment_callback=on_segment)
                progress.update(task_id, completed=100, description=f"Completed {video_file.name}")
                display_metadata_table(result.video_metadata)
                display_results_summary(result)
            except Exception as e:
                progress.update(task_id, description=f"[red]Failed {video_file.name}[/red]")
                console.print(f"[bold red]Error processing '{video_file.name}':[/bold red] {e}")


if __name__ == "__main__":
    run_cli()
