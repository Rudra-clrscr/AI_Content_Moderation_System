# Video-to-Transcript Data Pipeline

A **100% self-hosted, offline, privacy-first** data pipeline that ingests video files, extracts audio, and transcribes speech into formatted text, subtitles (`.srt`, `.vtt`), and structured timestamped JSON using local AI models (**Faster-Whisper** and embedded **FFmpeg**).

No cloud services, no external API keys, and no data leaving your machine.

---

## Architecture Overview

```
                      +-----------------------------+
                      |   User Upload / Ingestion   |
                      | (MP4, MKV, MOV, AVI, WEBM)  |
                      +--------------+--------------+
                                     |
                                     v
                      +-----------------------------+
                      |   Ingestion & Validation    |
                      |   - Format & Codec Check    |
                      |   - Video & Audio Metadata  |
                      |   - Silence / Audio Guard   |
                      +--------------+--------------+
                                     |
                                     v
                      +-----------------------------+
                      |   Audio Extraction Stage    |
                      |   - Embedded FFmpeg Demux   |
                      |   - Standard 16kHz Mono WAV |
                      +--------------+--------------+
                                     |
                                     v
                      +-----------------------------+
                      | Speech Recognition (Whisper)|
                      |   - Local Faster-Whisper    |
                      |   - Voice Activity Detection|
                      |   - Word-level Timestamps   |
                      |   - Auto Language Detection |
                      +--------------+--------------+
                                     |
                                     v
                      +-----------------------------+
                      |    Transcript Sink Stage    |
                      +--------------+--------------+
                                     |
         +----------------+----------+---------+----------------+
         |                |                    |                |
         v                v                    v                v
     Plain Text     SubRip Subtitles        WebVTT        Structured JSON
      (`.txt`)          (`.srt`)           (`.vtt`)          (`.json`)
```

---

## Features

- **100% Self-Hosted & Local**: Runs completely offline on your CPU or GPU.
- **Embedded FFmpeg**: Uses `imageio-ffmpeg`'s bundled FFmpeg executable — zero system configuration or external software installation needed.
- **Multi-Format Export**: Generates `.txt`, `.srt`, `.vtt`, and `.json` simultaneously.
- **Word-Level Precision**: Captures exact start and end timestamps and confidence for every spoken word.
- **Batch Processing**: Point to a single video file or an entire folder of videos.
- **Interactive Drag & Drop**: Run without flags to enter interactive drag-and-drop mode.
- **Voice Activity Detection (VAD)**: Silero VAD filters background noise and silence for faster transcription.
- **Pluggable Python API**: Easily import `VideoTranscriptionPipeline` into any ETL workflow or application.

---

## Quickstart

### 1. Run via Windows Launcher (`run.bat`)

```cmd
# Process a single video file
run.bat -i sample_test_video.mp4

# Run interactively (prompts you to drag-and-drop your video file)
run.bat

# Use a specific model (tiny, base, small, medium, large-v3)
run.bat -i sample_test_video.mp4 -m base

# Process an entire folder of videos
run.bat -i ./my_videos/ -o ./transcripts/
```

### 2. Run via Python CLI

```powershell
.\.venv\Scripts\python.exe run_pipeline.py -i sample_test_video.mp4 -m tiny
```

---

## CLI Options

| Argument | Short | Default | Description |
|---|---|---|---|
| `--input` | `-i` | `None` | Path to video file or folder for batch processing (prompts if omitted). |
| `--output-dir` | `-o` | `./transcripts` | Output folder for generated transcripts. |
| `--model` | `-m` | `base` | Model size (`tiny`, `tiny.en`, `base`, `base.en`, `small`, `small.en`, `medium`, `large-v3`). |
| `--language` | `-l` | `None` (auto) | Language code (e.g., `en`, `es`, `fr`, `de`). Auto-detected if omitted. |
| `--device` | `-d` | `auto` | Execution hardware: `auto`, `cpu`, or `cuda`. |
| `--format` | `-f` | `all` | Comma-separated output formats: `txt`, `srt`, `vtt`, `json`, or `all`. |
| `--word-timestamps` | | `True` | Include word-level timestamps in JSON output. |
| `--keep-audio` | | `False` | Retain the extracted intermediate 16kHz WAV file. |

---

## Python Library Usage

You can import and use the pipeline directly in your Python applications:

```python
from pathlib import Path
from pipeline import VideoTranscriptionPipeline, PipelineConfig

# 1. Configure pipeline options
config = PipelineConfig(
    model_size="base",           # tiny, base, small, medium, large-v3
    device="auto",               # auto, cpu, cuda
    output_dir=Path("./transcripts"),
    output_formats={"txt", "srt", "vtt", "json"},
    word_timestamps=True,
)

# 2. Initialize pipeline
pipeline = VideoTranscriptionPipeline(config=config)

# 3. Process video
result = pipeline.process("sample_test_video.mp4")

# 4. Access structured data
print(f"Language: {result.detected_language} ({result.language_probability * 100:.1f}%)")
print(f"Total Words: {result.total_words}")
print(f"Full Text:\n{result.full_text}\n")

# Access individual segments with timestamps
for segment in result.segments:
    print(f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text}")

# View generated files
print("Exported files:", result.exported_files)
```

### Batch Processing in Python

```python
results = pipeline.process_batch(
    video_paths=["video1.mp4", "video2.mov", "video3.mkv"],
    continue_on_error=True
)
```

---

## Generated Output Formats

### 1. Plain Text (`.txt`)
Contains the continuous transcribed text for easy reading and summarization.

### 2. SubRip Subtitles (`.srt`)
```srt
1
00:00:00,000 --> 00:00:02,800
Welcome to the Video Transcription Data Pipeline.

2
00:00:03,760 --> 00:00:08,900
This video demonstrates automated speech extraction and transcription using local AI models.
```

### 3. WebVTT Subtitles (`.vtt`)
Standard subtitle format ready to drop into HTML5 `<video><track src="..."></video>` elements.

### 4. Structured JSON (`.json`)
```json
{
  "metadata": {
    "file_name": "sample_test_video.mp4",
    "duration_seconds": 9.73,
    "video_codec": "h264",
    "audio_codec": "aac",
    "resolution": "640x360",
    "fps": 24.0
  },
  "detected_language": "en",
  "language_probability": 0.995,
  "total_words": 19,
  "full_text": "Welcome to the Video Transcription Data Pipeline...",
  "segments": [
    {
      "id": 1,
      "start": 0.0,
      "end": 2.8,
      "text": "Welcome to the Video Transcription Data Pipeline.",
      "words": [
        {"word": "Welcome", "start": 0.0, "end": 0.52, "probability": 0.95},
        {"word": "to", "start": 0.52, "end": 0.76, "probability": 0.99}
      ]
    }
  ]
}
```

---

## Generating Test Videos

To generate a sample video with synthesized voice on Windows:
```cmd
run.bat
# or
.\.venv\Scripts\python.exe generate_sample_video.py
```

## Running Automated Tests

```powershell
.\.venv\Scripts\python.exe -m unittest tests/test_pipeline.py
```
