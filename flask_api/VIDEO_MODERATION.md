# Video moderation (audio)

When someone uploads a video, the service listens to it, turns the speech into English text and
judges that text with the **same pipeline as a typed article** (gate rules + DeBERTa). If the speech
is harmful the video is blocked; otherwise it is allowed.

```
video -> audio (ffmpeg) -> English text (Faster-Whisper) -> gate + DeBERTa -> allow / revise / reject
```

## Obeys the Admin setting
* Admin **OFF** for that surface: nothing is listened to; the video is published and recorded as
  `decided_by: disabled`.
* Admin **ON**: the speech is judged at **Lenient / Medium / Strict** exactly like text on that surface.
* The surface comes from the upload's `content_type` (`video` on the Videos tab, `article` for an
  attachment on an article), so each follows its own Admin setting.

## What is and is not checked
| | |
|---|---|
| Spoken words | yes, any language (non-English is translated to English first) |
| The pictures / frames | **no**. Every result says `visual_content_checked: false` |
| No audio track, or no speech | allowed (nothing was said) |
| Longer than `max_seconds` (10 min) | refused, never half-listened-to |
| Damaged file | refused with a reason |
| The check crashes | refused with "try again" (503). Never published |

## Setup
```powershell
cd flask_api
pip install -r requirements.txt          # adds faster-whisper, av, imageio-ffmpeg
python -m flask --app wsgi run --port 5000
```
The Whisper model (`small`, about 470 MB) downloads **once, at start-up**. Watch the log for
`video audio check ready`. `GET /ready` shows `"video": {"ready": true}`.

**Until it says ready, videos are NOT being checked.** If the model is missing (no internet, not yet
downloaded) the check turns itself off and `media.allow_unchecked_video` decides. The shipped config
allows video through with `checked: false`, so the Videos tab keeps working. When you are happy it
loads, set this in `config/settings.yaml` so a missing model blocks videos instead of letting them by:

```yaml
media:
  allow_unchecked_video: false
```

## Settings (`config/settings.yaml`, `media.video`)
`enabled`, `max_bytes` (100 MB), `max_seconds` (600), `model_size` (`small`; `tiny`/`base` are faster
and miss more words; must not end in `.en`), `device` (`auto`/`cpu`/`cuda`), `cpu_threads`,
`vad_filter`, `max_concurrent`, `ffmpeg_timeout`. Env: `VIDEO_CHECK=0` (turn off),
`VIDEO_ASR_MODEL`, `VIDEO_ASR_DEVICE`.

## Hindi and other languages
The DeBERTa model is English-centric, and a translation can clean up a curse word. So for non-English
speech the service keeps **both** texts: the English translation goes to the model as before, and the
words **as spoken** (`original_transcript`) are also checked against the gate's Hindi/Hinglish rules
(Devanagari and Roman script). A block-level match refuses the video. Costs one extra transcription
pass for non-English videos only.

## Honest limits
* Abuse in a language the gate has no rules for (and that the English translation hides) is not caught.
  Whisper sometimes labels Hindi speech as **Urdu** and writes Arabic script, which the gate does not
  list. Add those terms to `config/gate_patterns.yaml` if you see it.
* Whisper makes mistakes on accents, noise and slang, and translation can soften a threat. A wrong
  transcript means a wrong decision. `small` is a compromise; `medium`/`large-v3` are better on a GPU.
* Speech only: a silent video with an offensive picture is allowed.
* One video is transcribed at a time per server process (`max_concurrent`); a 10-minute video can take
  minutes on a CPU. The request stays open meanwhile.
* The audit row stores the transcript, like it stores typed text.
* With `gunicorn -w 4` every worker loads its own copy of the model.
