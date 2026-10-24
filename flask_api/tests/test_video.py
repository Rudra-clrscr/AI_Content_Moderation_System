"""Video moderation: the speech in an uploaded video is judged like typed text.

Two layers are tested, and the split is deliberate.

* The API layer uses a stand-in for the listening step (`FakeVideo`), so every rule about what
  happens to the words - the admin's ON/OFF and level, the gate, the model, fail-closed - is
  pinned without needing Whisper or a video file. These are the tests that say "a video whose
  speech is harmful is never published".
* The listening step itself (`VideoCheck.check`) is run on REAL generated video files through
  the real probe and ffmpeg extraction, with only the speech-to-text swapped for a stub,
  because Whisper's model is a download. What Whisper itself hears is NOT tested here.
"""
import io
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import create_app
from app.config import Settings, load_settings
from app.media import MediaError, MediaLimits
from app.model import ScoreResult
from app.video import (Speech, VideoCheck, VideoConfig, VideoFailed, VideoUnavailable, _join)

ADMIN = {"X-Admin-Token": "secret"}


def mp4_bytes() -> bytes:
    """Bytes that read as video. FakeVideo never decodes them."""
    return b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"\x00" * 200


class SpeechScorer:
    """Stands in for the DeBERTa model: what the words say decides the score."""
    version = "v-1"

    def __init__(self):
        self.calls = 0

    def score(self, text):
        self.calls += 1
        t = text.lower()
        r = 0.97 if "kill" in t else 0.60 if "rude" in t else 0.02
        return ScoreResult(r, "x", {"x": r}, self.version, 1.0)


class FakeVideo(VideoCheck):
    """A VideoCheck whose listening step is scripted: `heard` is a Speech or an exception."""

    def __init__(self, heard, config=None):
        super().__init__(config or VideoConfig(enabled=True))
        self.heard = heard
        self.calls = 0

    def check(self, data):
        self.calls += 1
        if isinstance(self.heard, Exception):
            raise self.heard
        return self.heard


def said(text, **kw) -> Speech:
    return Speech(text, "en", 0.99, False, 12.0, True,
                  ({"start": 0.0, "end": 4.0, "char_start": 0, "char_end": len(text)},), 1.0)


@pytest.fixture
def scorer():
    return SpeechScorer()


def build(make_settings, scorer, sink, heard, **settings_kw):
    video = FakeVideo(heard)
    app = create_app(make_settings(max_chars=50_000, video=video, **settings_kw),
                     scorer=scorer, sink=sink)
    return app.test_client(), video


def upload(c, name="clip.mp4", data=None, content_type="video"):
    return c.post("/v1/moderate/media",
                  data={"file": (io.BytesIO(data or mp4_bytes()), name), "content_type": content_type},
                  content_type="multipart/form-data")


def set_level(c, **body):
    r = c.put("/v1/admin/moderation-levels/video", json=body, headers=ADMIN)
    assert r.status_code == 200, r.get_json()


# ---- the core promise: harmful speech is blocked, clean speech is allowed -----------------

def test_clean_speech_is_allowed_and_the_transcript_is_returned(make_settings, scorer, sink):
    c, video = build(make_settings, scorer, sink, said("We ship steel pipes across Gujarat."))
    body = upload(c).get_json()
    assert body["decision"] == "allow" and scorer.calls == 1
    m = body["media"]
    assert m["kind"] == "video" and m["audio_checked"] is True and m["speech_found"] is True
    assert m["transcript"] == "We ship steel pipes across Gujarat."
    assert m["language"] == "en" and m["translated_to_english"] is False


def test_harmful_speech_is_rejected_by_the_text_model(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, said("I will kill you and burn your warehouse."))
    body = upload(c).get_json()
    assert body["decision"] == "reject" and body["decided_by"] == "model"
    assert body["feedback"]["issues"]               # the author is told what was said


def test_speech_that_trips_a_gate_rule_is_blocked_before_the_model(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, said("this is a badword in a video"))
    body = upload(c).get_json()
    assert body["decision"] == "reject" and body["decided_by"] == "gate" and scorer.calls == 0


def test_speech_needing_a_revision_is_not_allowed(make_settings, scorer, sink):
    """A gate `revise` rule: not published. Allowed would be the bug."""
    c, _ = build(make_settings, scorer, sink, said("you can pay in crypto for this"))
    assert upload(c).get_json()["decision"] in ("revise", "reject")


def test_the_decision_is_recorded_like_any_other(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, said("I will kill you."))
    upload(c)
    assert len(sink.results) == 1 and sink.results[0]["decision"] == "reject"
    # Same as typed text: the audit row keeps the content it judged - here, the transcript.
    assert sink.results[0]["content"] == "I will kill you." and sink.results[0]["content_type"] == "video"


def test_attachments_on_an_article_use_the_articles_setting(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, said("I will kill you."))
    c.put("/v1/admin/moderation-levels/article", json={"enabled": False}, headers=ADMIN)
    assert upload(c, content_type="article").get_json()["decided_by"] == "disabled"
    assert upload(c, content_type="video").get_json()["decision"] == "reject"   # Videos is still ON


# ---- the admin's setting is obeyed -----------------------------------------------------

def test_admin_off_publishes_without_listening(make_settings, scorer, sink):
    c, video = build(make_settings, scorer, sink, said("I will kill you."))
    set_level(c, enabled=False)
    body = upload(c).get_json()
    assert body["decision"] == "allow" and body["decided_by"] == "disabled"
    assert video.calls == 0 and scorer.calls == 0              # no CPU spent on a video nobody judges
    assert body["media"]["checked"] is False and body["media"]["moderation_enabled"] is False


def test_turning_it_back_on_moderates_again(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, said("I will kill you."))
    set_level(c, enabled=False)
    assert upload(c).get_json()["decision"] == "allow"
    set_level(c, enabled=True)
    assert upload(c).get_json()["decision"] == "reject"


def test_each_level_changes_the_bar_for_a_borderline_video(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, said("The foreman was rude about it."))   # scores 0.60
    set_level(c, enabled=True, level="lenient")
    assert upload(c).get_json()["decision"] == "allow"
    set_level(c, level="medium")
    assert upload(c).get_json()["decision"] == "reject"
    set_level(c, level="strict")
    assert upload(c).get_json()["decision"] == "reject"


def test_lenient_still_refuses_clear_abuse(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, said("I will kill you."))
    set_level(c, enabled=True, level="lenient")
    assert upload(c).get_json()["decision"] == "reject"


# ---- fail closed ------------------------------------------------------------------------

def test_a_failure_while_listening_never_publishes(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, VideoFailed("whisper crashed"))
    r = upload(c)
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "media_not_checkable"
    assert sink.results == [] and scorer.calls == 0


def test_a_model_failure_after_listening_never_publishes(make_settings, sink):
    class Boom:
        version = "boom"
        def score(self, text):
            raise RuntimeError("model down")
    c, _ = build(make_settings, Boom(), sink, said("hello there"))
    r = upload(c)
    assert r.status_code >= 500 and "allow" not in r.get_data(as_text=True)


def test_a_damaged_video_is_refused_with_a_reason(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, MediaError("media_unreadable", "this video is damaged"))
    r = upload(c)
    assert r.status_code == 422 and r.get_json()["error"]["code"] == "media_unreadable"


def test_a_video_over_the_length_limit_is_refused_not_half_checked(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, MediaError("media_too_long", "too long"))
    r = upload(c)
    assert r.status_code == 413 and r.get_json()["error"]["code"] == "media_too_long"


def test_an_oversized_file_is_refused_before_anything_runs(make_settings, scorer, sink):
    video = FakeVideo(said("hi"), VideoConfig(enabled=True, max_bytes=100))
    app = create_app(make_settings(max_chars=50_000, video=video), scorer=scorer, sink=sink)
    r = upload(app.test_client(), data=mp4_bytes())
    assert r.status_code == 413 and video.calls == 0


# ---- nothing to listen to -----------------------------------------------------------------

def test_a_video_with_no_speech_is_allowed_and_says_the_picture_was_not_seen(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, Speech("", None, 0.0, False, 30.0, True))
    body = upload(c).get_json()
    assert body["decision"] == "allow" and scorer.calls == 0
    assert body["media"]["speech_found"] is False and body["media"]["audio_checked"] is True
    assert body["media"]["visual_content_checked"] is False


def test_a_video_with_no_audio_track_is_allowed_and_says_so(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, Speech("", None, 0.0, False, 0.0, False))
    body = upload(c).get_json()
    assert body["decision"] == "allow" and body["media"]["audio_found"] is False


def test_every_video_result_says_the_pictures_were_not_inspected(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, said("Nothing wrong here."))
    assert upload(c).get_json()["media"]["visual_content_checked"] is False


# ---- a translation must not launder abuse: the words as SPOKEN are gate-checked too ----------------

def translated(english, original):
    return Speech(english, "hi", 0.95, True, 8.0, True,
                  ({"start": 0.0, "end": 3.0, "char_start": 0, "char_end": len(english)},), 1.0, original)


def test_abuse_lost_in_translation_is_still_caught_from_the_original_words(make_settings, scorer, sink):
    """Whisper turned the curse into clean English; the gate still sees it in the Hindi."""
    c, _ = build(make_settings, scorer, sink, translated("Where is my order?", "मेरा order कहाँ है badword"))
    body = upload(c).get_json()
    assert body["decision"] == "reject" and body["decided_by"] == "gate"
    assert body["gate_matches"][0]["rule_id"] == "test.block_term"
    assert scorer.calls == 0                                   # refused before the English was scored
    assert body["media"]["translated_to_english"] is True


def test_clean_translated_speech_is_allowed(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, translated("Where is my order?", "मेरा ऑर्डर कहाँ है?"))
    body = upload(c).get_json()
    assert body["decision"] == "allow" and body["media"]["original_transcript"] == "मेरा ऑर्डर कहाँ है?"


def test_harm_in_the_english_translation_is_still_caught_by_the_model(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, translated("I will kill you.", "मैं तुम्हें मार दूँगा"))
    assert upload(c).get_json()["decision"] == "reject"


def test_admin_off_skips_the_original_words_check_too(make_settings, scorer, sink):
    c, video = build(make_settings, scorer, sink, translated("Where is my order?", "badword"))
    set_level(c, enabled=False)
    body = upload(c).get_json()
    assert body["decision"] == "allow" and body["decided_by"] == "disabled" and video.calls == 0


# ---- a machine without the model behaves as before -----------------------------------------

def test_unavailable_check_falls_back_to_the_unchecked_policy(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, VideoUnavailable("no model"),
                 media=MediaLimits(allow_unchecked_video=True))
    body = upload(c).get_json()
    assert body["decision"] == "allow" and body["media"]["checked"] is False


def test_unavailable_check_refuses_when_unchecked_video_is_not_allowed(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, VideoUnavailable("no model"),
                 media=MediaLimits(allow_unchecked_video=False))
    r = upload(c)
    assert r.status_code == 422 and r.get_json()["error"]["code"] == "media_not_checkable"


def test_with_the_check_off_video_is_handled_exactly_as_before(make_settings, scorer, sink):
    app = create_app(make_settings(media=MediaLimits(allow_unchecked_video=True)), scorer=scorer, sink=sink)
    body = upload(app.test_client()).get_json()
    assert body["decision"] == "allow" and body["media"]["checked"] is False
    assert "audio_checked" not in body["media"]


# ---- settings ---------------------------------------------------------------------------------

def test_shipped_config_listens_to_video_and_code_default_does_not():
    assert load_settings().video.enabled is True
    assert Settings().video.enabled is False


def test_an_english_only_whisper_model_is_refused_at_load():
    with pytest.raises(ValueError, match="multilingual"):
        VideoConfig(model_size="small.en")


def test_ready_reports_the_video_check(make_settings, scorer, sink):
    c, _ = build(make_settings, scorer, sink, said("hi"))
    assert c.get("/ready").get_json()["video"]["enabled"] is True


def test_join_maps_every_sentence_to_a_time_range():
    segs = [SimpleNamespace(start=0.0, end=2.5, text=" Hello there."),
            SimpleNamespace(start=2.5, end=5.0, text=""),
            SimpleNamespace(start=5.0, end=7.0, text="Second line. ")]
    text, mapped = _join(segs, "")
    assert text == "Hello there. Second line."
    assert [text[m["char_start"]:m["char_end"]] for m in mapped] == ["Hello there.", "Second line."]
    assert mapped[1]["start"] == 5.0


# ---- start-up: the model loads once, and an upload never waits for (or sneaks past) a download ----

def test_an_upload_during_model_loading_is_told_to_retry_not_published(make_settings, scorer, sink):
    video = VideoCheck(VideoConfig(enabled=True))
    app = create_app(make_settings(max_chars=50_000, video=video), scorer=scorer, sink=sink)
    video._lock.acquire()                              # the warm-up is in the middle of loading
    try:
        r = upload(app.test_client())
    finally:
        video._lock.release()
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "media_not_checkable"
    assert sink.results == []


def test_warm_up_never_raises_even_when_the_model_is_missing():
    video = VideoCheck(VideoConfig(enabled=True, model_size="no-such-model-size"))
    video.warm()                                       # logs, and the check stays unavailable
    assert video.status["ready"] is False


# ---- the listening step on REAL video files (Whisper itself is stubbed) -----------------------

av = pytest.importorskip("av")
imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")
pytest.importorskip("faster_whisper")


def make_video(path: Path, seconds: int = 3, audio: bool = True) -> bytes:
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate=10"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(cmd, check=True)
    return path.read_bytes()


def stub_transcriber(language="en", translated=False, text="Hello from the video."):
    def run(wav: Path):
        assert wav.exists() and wav.stat().st_size > 44        # real extracted audio reached us
        seg = SimpleNamespace(start=0.0, end=1.5, text=text)
        return language, 0.97, [seg], text, translated
    return run


def real(**kw) -> VideoCheck:
    transcriber = kw.pop("transcriber", stub_transcriber())
    return VideoCheck(VideoConfig(enabled=True, **kw), transcriber=transcriber)


def test_real_video_audio_is_extracted_and_its_text_mapped(tmp_path):
    speech = real().check(make_video(tmp_path / "a.mp4"))
    assert speech.has_audio and speech.has_speech
    assert speech.text == "Hello from the video." and speech.language == "en"
    assert 2.0 < speech.duration_seconds < 4.5
    assert speech.segments[0]["char_end"] == len("Hello from the video.")


def test_translated_speech_is_flagged(tmp_path):
    speech = real(transcriber=stub_transcriber("hi", True, "Where is my order?")).check(
        make_video(tmp_path / "a.mp4"))
    assert speech.language == "hi" and speech.translated is True


def test_real_video_without_audio_reports_no_audio(tmp_path):
    speech = real().check(make_video(tmp_path / "silent.mp4", audio=False))
    assert speech.has_audio is False and speech.text == ""


def test_real_video_longer_than_the_limit_is_refused(tmp_path):
    with pytest.raises(MediaError) as exc:
        real(max_seconds=1).check(make_video(tmp_path / "long.mp4", seconds=4))
    assert exc.value.code == "media_too_long"


def test_bytes_that_are_not_a_video_are_refused_as_unreadable():
    with pytest.raises(MediaError) as exc:
        real().check(mp4_bytes())
    assert exc.value.code == "media_unreadable"


def test_a_transcriber_crash_becomes_video_failed(tmp_path):
    def boom(wav):
        raise RuntimeError("out of memory")
    with pytest.raises(VideoFailed):
        real(transcriber=boom).check(make_video(tmp_path / "a.mp4"))


def test_temp_files_are_removed_even_when_it_fails(tmp_path):
    import tempfile
    before = set(Path(tempfile.gettempdir()).glob("bonc_video_*"))
    with pytest.raises(MediaError):
        real().check(mp4_bytes())
    assert set(Path(tempfile.gettempdir()).glob("bonc_video_*")) == before


# ---- English output: the language is read first, then the right job is run ---------------------

def _stage(language):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from pipeline.config import PipelineConfig
    from pipeline.stages.transcription import TranscriptionStage

    calls = []

    class FakeModel:
        def transcribe(self, path, **kw):
            calls.append(kw)
            info = SimpleNamespace(language=language, language_probability=0.9, duration=3.0)
            said = " Where is my refund?" if language == "en" or kw["task"] == "translate" \
                else " मेरा रिफंड कहाँ है?"
            seg = SimpleNamespace(start=0.0, end=1.0, text=said, avg_logprob=-0.2, no_speech_prob=0.01)
            return iter([seg]), info

    stage = TranscriptionStage(PipelineConfig(model_size="small"))
    stage._model = FakeModel()
    return stage, calls


def test_english_speech_is_transcribed_not_translated(tmp_path):
    stage, calls = _stage("en")
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF" + b"\0" * 100)
    lang, prob, segs, text, translated, source = stage.transcribe_english(wav)
    assert (lang, translated, text, source) == ("en", False, "Where is my refund?", "")
    assert [c["task"] for c in calls] == ["transcribe"]


def test_other_languages_are_translated_to_english(tmp_path):
    stage, calls = _stage("hi")
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF" + b"\0" * 100)
    lang, prob, segs, text, translated, source = stage.transcribe_english(wav)
    assert (lang, translated) == ("hi", True)
    assert text == "Where is my refund?"                 # the English that goes to the model
    assert source == "मेरा रिफंड कहाँ है?"                # and the words as they were spoken
    assert [c["task"] for c in calls] == ["transcribe", "translate"]
    assert calls[1]["language"] == "hi"                 # no second language detection


def test_real_file_through_the_real_endpoint_blocks_harmful_speech_and_allows_clean(
        make_settings, scorer, sink, tmp_path):
    """The whole chain except Whisper: real mp4 -> real probe + ffmpeg -> stub speech -> real API."""
    data = make_video(tmp_path / "a.mp4")
    for spoken, expected in (("We deliver steel pipes on time.", "allow"), ("I will kill you.", "reject")):
        video = real(transcriber=stub_transcriber(text=spoken))
        app = create_app(make_settings(max_chars=50_000, video=video), scorer=scorer, sink=sink)
        body = upload(app.test_client(), data=data).get_json()
        assert body["decision"] == expected, spoken
        assert body["media"]["transcript"] == spoken and body["media"]["audio_checked"] is True
