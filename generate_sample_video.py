"""
Utility script to generate a synthetic test video with real spoken speech
to validate the video transcription pipeline.
"""

from pathlib import Path
import subprocess
import sys
import imageio_ffmpeg

def generate_sample_video(
    output_mp4: str = "sample_test_video.mp4",
    speech_text: str = "Welcome to the video transcription data pipeline. This video demonstrates automated speech extraction and transcription using local AI models.",
):
    out_path = Path(output_mp4).resolve()
    temp_wav = out_path.parent / "temp_synth_speech.wav"
    ps_script_file = out_path.parent / "synth_script.ps1"

    print(f"Generating spoken audio for test video...")
    ps_content = f"""
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SetOutputToWaveFile('{str(temp_wav).replace('\\', '\\\\')}')
$synth.Speak('{speech_text}')
$synth.Dispose()
"""
    ps_script_file.write_text(ps_content, encoding="utf-8")

    try:
        # Run PowerShell script to synthesize speech WAV
        res = subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ps_script_file)],
            capture_output=True,
            text=True,
            check=True,
        )

        if not temp_wav.exists() or temp_wav.stat().st_size == 0:
            raise RuntimeError(f"Failed to generate speech WAV: {res.stderr}")

        print(f"Synthesized audio created ({temp_wav.stat().st_size} bytes).")

        # Now mux video testsrc + audio into an MP4 video using ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        print(f"Muxing test video pattern with audio into '{out_path.name}'...")
        mux_cmd = [
            ffmpeg_exe,
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-f", "lavfi",
            "-i", "testsrc=size=640x360:rate=24",
            "-i", str(temp_wav),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-shortest",
            str(out_path),
        ]
        res_mux = subprocess.run(mux_cmd, capture_output=True, text=True, check=True)

        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError(f"Failed to generate MP4: {res_mux.stderr}")

        print(f"Successfully generated sample video: {out_path} ({out_path.stat().st_size} bytes)")
        return out_path

    finally:
        # Cleanup temp generator files
        if ps_script_file.exists():
            ps_script_file.unlink()
        if temp_wav.exists():
            temp_wav.unlink()

if __name__ == "__main__":
    generate_sample_video()
