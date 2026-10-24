"""HTTP endpoints. Request/response shapes are documented in contracts/moderation_result.md."""
from __future__ import annotations

import hmac
import logging
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from app.gate import GateMatch, GateResult
from app.media import MediaError, detect_kind, read_image
from app.video import VideoFailed, VideoUnavailable
from app.model import ModelNotReady
from app.pdf import PdfError
from app.pdf import extract as pdf_extract
from app.pipeline import ContentType, ModerationRequest, Stage
from app.routing import Decision, Thresholds

log = logging.getLogger(__name__)
bp = Blueprint("moderation", __name__)

_CONTENT_TYPES = [c.value for c in ContentType]


def _svc():
    return current_app.extensions["moderation"]


def _error(status: int, code: str, message: str, **extra):
    return jsonify({"error": {"code": code, "message": message, **extra}}), status


@bp.errorhandler(HTTPException)
def _http_error(exc: HTTPException):
    return _error(exc.code or 500, exc.name.lower().replace(" ", "_"), exc.description or exc.name)


def _parse_request() -> ModerationRequest | tuple:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error(400, "invalid_json", "body must be a JSON object")

    content = body.get("content")
    if not isinstance(content, str) or not content.strip():
        return _error(400, "invalid_content", "'content' must be a non-empty string")
    max_chars = _svc().settings.max_chars
    if len(content) > max_chars:
        return _error(413, "content_too_long", f"'content' exceeds {max_chars} characters")

    try:
        content_type = ContentType(body.get("content_type"))
    except ValueError:
        return _error(400, "invalid_content_type", "unknown 'content_type'", allowed=_CONTENT_TYPES)

    content_id = body.get("content_id")
    if content_id is not None and not isinstance(content_id, (str, int)):
        return _error(400, "invalid_content_id", "'content_id' must be a string or integer")

    return ModerationRequest(content, content_type, None if content_id is None else str(content_id))


def _moderate(req: ModerationRequest, extra: dict | None = None,
              thresholds: "Thresholds | None" = None):
    """Gate, then queue or score. Shared by the JSON and PDF endpoints so a PDF is judged by
    exactly the same rules. `extra` is merged into the response (the PDF page map).

    `thresholds` raises the bar for one request - used for text OCR'd out of a picture, which
    is noisier than anything a member types. The gate is unaffected either way: a rule either
    matched or it didn't, and a scam phrase in a photograph is still a scam phrase.
    """
    svc = _svc()
    # The admin's setting for this surface: whether it is moderated at all, and at what bar.
    policy = svc.levels.for_type(req.content_type.value)
    if not policy.enabled:
        # Nothing runs - not the model, not the gate. Published and recorded as unchecked.
        result = {**svc.pipeline.not_moderated_result(req), "moderation": policy.as_dict()}
        svc.sink.emit(result)
        return jsonify({**result, **(extra or {})}), 200

    # An explicit override wins over the feature's level. The only caller that passes one is
    # text read out of a picture, where the higher bar is a correctness measure against OCR
    # noise rather than a policy preference - applying a `strict` feature bar to word-salad
    # would bring back exactly the false positives that override exists to prevent.
    effective = thresholds or policy.thresholds
    pipeline = svc.pipeline if effective is None else svc.pipeline.with_thresholds(effective)

    def stamp(result: dict) -> dict:
        """Record the level this was judged at ON the result, not merely in the response.

        The sink is what anyone reads months later to ask why something was published. If an
        admin had set this surface to `lenient`, or switched it off, that is the first thing
        they need to see - and merging it into the HTTP body alone would leave the audit row
        silent about it.
        """
        return {**result, "moderation": policy.as_dict()}
    # Layer 1 always runs inline — it's cheap, and a block needs no model or queue.
    gate, gate_ms = pipeline.run_gate(req)
    if gate.blocked:
        result = stamp(pipeline.gate_only_result(req, gate, gate_ms))
        svc.sink.emit(result)
        return jsonify({**result, **(extra or {})}), 200

    if svc.settings.mode == "async":
        try:
            svc.enqueue(req)
        except Exception:
            log.exception("enqueue failed for request %s", req.request_id)
            return _error(503, "queue_unavailable", "moderation queue unavailable, retry later")
        return jsonify({
            "request_id": req.request_id,
            "status": "pending",
            "content_id": req.content_id,
            "content_type": req.content_type.value,
            "status_url": f"/v1/moderate/{req.request_id}",
            "moderation": policy.as_dict(),
            **(extra or {}),
        }), 202

    try:
        result = pipeline.moderate(req, gate=gate, gate_ms=gate_ms)
    except ModelNotReady as exc:
        return _error(503, "model_not_ready", str(exc))
    except Exception:
        log.exception("inference failed for request %s", req.request_id)
        return _error(500, "inference_failed", "model inference failed", request_id=req.request_id)
    result = stamp(result)
    svc.sink.emit(result)
    return jsonify({**result, **(extra or {})}), 200


@bp.post("/v1/moderate")
def moderate():
    req = _parse_request()
    if isinstance(req, tuple):
        return req
    return _moderate(req)


@bp.post("/v1/moderate/pdf")
@bp.post("/v1/moderate/media")
def moderate_media():
    """Moderate one uploaded file: read whatever text it carries, then run the ordinary pipeline.

    multipart/form-data: `file` (required), `content_type` (default "article"), `content_id`.
    Post one file per request — each gets its own decision and its own audit row.

    What can be read differs by kind, and so does the rule (see app/media.py):

      * **PDF** — text, with OCR for scanned pages. A page that can't be read is refused, and
        the result carries a `pdf` block mapping offsets to page numbers.
      * **Image** — two passes over the same picture. OCR reads any writing, which catches
        the bypass of putting the scam in a screenshot; the CLIP visual check (app/clip.py)
        scores the picture itself against `config/visual_policy.yaml`, which catches a weapon
        or gore photograph carrying no words at all. With the visual check off or its model
        missing, the result says `visual_content_checked: false` and behaves as it used to.
      * **Video** — its audio is transcribed to English and judged like typed text, at the
        admin's level for the surface (see app/video.py); the pictures are not examined. If the
        audio check is unavailable, `media.allow_unchecked_video` decides, as it always did.

    `/v1/moderate/pdf` is the original path and still works; it accepts the other kinds too.
    """
    svc = _svc()
    pdf_limits, media = svc.settings.pdf, svc.settings.media
    if not pdf_limits.enabled:
        return _error(404, "not_found", "file moderation is disabled")

    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return _error(400, "invalid_file", "attach a file as the 'file' field of a multipart form")

    try:
        content_type = ContentType(request.form.get("content_type", ContentType.ARTICLE.value))
    except ValueError:
        return _error(400, "invalid_content_type", "unknown 'content_type'", allowed=_CONTENT_TYPES)
    content_id = request.form.get("content_id") or None

    cap = max(pdf_limits.max_bytes, media.max_bytes,
              svc.settings.video.config.max_bytes if svc.settings.video.enabled else 0)
    data = upload.read(cap + 1)
    if len(data) > cap:
        return _error(413, "media_too_large", f"the file is larger than {cap} bytes", limit=cap)

    kind = detect_kind(data, upload.filename)
    block = {"filename": upload.filename, "kind": kind}

    if kind == "pdf":
        if len(data) > pdf_limits.max_bytes:
            return _error(413, "pdf_too_large", f"the file is larger than {pdf_limits.max_bytes} bytes",
                          limit=pdf_limits.max_bytes)
        try:
            extracted = pdf_extract(data, pdf_limits, upload.filename, svc.settings.ocr,
                                    svc.settings.visual)
        except PdfError as exc:
            status = 413 if exc.code in ("pdf_too_large", "pdf_too_many_pages", "pdf_text_too_long") else 422
            return _error(status, exc.code, exc.message, **exc.extra)
        text = extracted.text
        block = {**block, **extracted.as_dict()}
        extra = {"pdf": block, "media": block}          # "pdf" kept for the original contract
        if extracted.unsafe_visual:
            # A scanned PDF is a stack of photographs. Whatever its text says, a page the
            # visual check objects to is refused the same way the picture would be if it had
            # been attached directly - the container must not decide the rule.
            worst = max(extracted.unsafe_visual, key=lambda v: v.score)
            return _visual_reject(upload.filename, worst, content_type, content_id, extra,
                                  rule_id=f"visual.{worst.label}", page=worst.page)

    elif kind == "image":
        if len(data) > media.max_bytes:
            return _error(413, "media_too_large", f"the file is larger than {media.max_bytes} bytes",
                          limit=media.max_bytes)
        try:
            seen = read_image(data, svc.settings.ocr, media, svc.settings.visual)
        except MediaError as exc:
            return _error(413 if exc.code == "media_too_large" else 422, exc.code, exc.message, **exc.extra)
        read, looked = seen.text, seen.visual
        text = read.text
        # A photograph with no writing on it is ordinary, so no text is not a refusal here.
        # `visual_content_checked` then carries the weight: true means the picture itself was
        # scored, false means nobody looked at it and the caller must not read it as clean.
        block.update(text_found=read.has_text, ocr_confidence=round(read.confidence, 4),
                     text_readable=read.readable, **looked.as_dict())
        extra = {"media": block}
        if looked.failed:
            # The visual model is present and threw. Same rule as OCR falling over: refuse,
            # because publishing a picture nothing looked at is the hole this check exists to
            # close, and the author did nothing wrong so offer a retry.
            return _error(503, "media_not_checkable",
                          "this image could not be checked just now. Please try again in a "
                          "moment.", kind=kind)
        if svc.settings.visual.decides_reject(looked):
            # The picture itself breaks policy, whatever the words say. This is a decision about
            # content, not an upload error, so it goes through the pipeline as a gate block:
            # same feedback wording, same audit row, same sink as a blocked sentence.
            return _visual_reject(upload.filename, looked, content_type, content_id, extra)
        if read.failed:
            # The checker broke, which is ours to fix and nothing the author did. Still refused:
            # an image nothing looked at must not publish as clean.
            return _error(503, "media_not_checkable",
                          "this image could not be checked just now. Please try again in a "
                          "moment.", kind=kind)
        if read.has_text and not read.readable:
            # There IS writing here and OCR could not read it, so its WORDS are unchecked.
            # Answering "allow" is how a Devanagari threat once published with a clean result.
            #
            # What has changed is that the picture itself is no longer unexamined: when the
            # visual check ran and cleared it, the only thing left unknown is the writing, and
            # refusing on that alone was turning away 13% of real listings over logos, number
            # plates and watermarks. The record still says checked: false - this is a narrower
            # claim ("we looked at it, we could not read it"), never a clean result.
            seen_and_clear = (media.allow_unreadable_image_when_seen and looked.checked
                              and not looked.unsafe)
            if not (media.allow_unreadable_image or seen_and_clear):
                return _error(422, "media_unreadable",
                              "there is writing in this image that could not be read, so it "
                              "cannot be checked. Upload a clearer picture, or put the words "
                              "in the post itself.", kind=kind, text_readable=False)
            block.update(checked=False)
            return jsonify({**_allowed_without_text(upload.filename, content_type, content_id), **extra}), 200
        if not text.strip():
            return jsonify({**_allowed_without_text(upload.filename, content_type, content_id), **extra}), 200

    elif kind == "video":
        if svc.settings.video.enabled:
            return _moderate_video(svc, data, upload.filename, content_type, content_id, block)
        # The audio check is switched off (or not installed): video is handled exactly as it
        # always was, by media.allow_unchecked_video.
        if not media.allow_unchecked_video:
            return _error(422, "media_not_checkable",
                          "video cannot be checked by this service, so it cannot be published. "
                          "Upload a still image or a PDF instead.", kind=kind)
        block.update(text_found=False, visual_content_checked=False, checked=False)
        return jsonify({**_allowed_without_text(upload.filename, content_type, content_id),
                        "media": block}), 200

    else:
        return _error(422, "media_unsupported",
                      "this file type cannot be checked. Attach an image, a video or a PDF.",
                      kind=kind)

    char_limit = pdf_limits.max_chars if kind == "pdf" else svc.settings.max_chars
    if len(text) > char_limit:
        return _error(413, "content_too_long",
                      f"the file's text is {len(text)} characters, over the "
                      f"{char_limit} character limit")

    # Words read off a photograph are held to a higher bar than words somebody typed. OCR of a
    # shopfront comes back as word-salad and the model, trained on prose, scores that noise
    # unreliably - it put four ordinary BONC listings in the reject band. Measured: legitimate
    # signage 0.78-0.976, scam flyers 0.9997, so the two still separate. PDFs keep the ordinary
    # bar: their text is extracted, not guessed at, and reads as what the author wrote.
    # One boundary, not two. A "revise" verdict asks the author to edit the highlighted part,
    # which is meaningless for words baked into a photograph - their only remedies are to
    # replace the picture or leave it. So image text is allowed below the bar and refused at or
    # above it, with nothing in between.
    thresholds = None
    if kind == "image":
        thresholds = Thresholds(allow_max=media.ocr_text_reject_min,
                                reject_min=media.ocr_text_reject_min)
    return _moderate(ModerationRequest(text, content_type, content_id), extra, thresholds)


def _moderate_video(svc, data: bytes, filename: str, content_type: ContentType,
                    content_id: str | None, block: dict):
    """Listen to a video, then judge what was said exactly as if it had been typed.

    The speech becomes English text (app/video.py) and goes through `_moderate`, so the gate,
    the DeBERTa model, the sink and - above all - the admin's setting for this surface all
    apply with no second code path: ON/OFF, and the lenient/medium/strict bar. Anything other
    than `allow` is not published, which is also what the front end enforces.

    The pictures are NOT examined, and every branch says so (`visual_content_checked: false`).
    """
    media = svc.settings.media
    video = svc.settings.video
    block.update(audio_checked=False, visual_content_checked=False)

    # Admin OFF for this surface: nothing is listened to and nothing is judged. `_moderate`
    # records it as published-unmoderated (`decided_by: disabled`), never as clean. Skipping the
    # listening here also saves the CPU a transcription would have cost.
    if not svc.levels.for_type(content_type.value).enabled:
        block.update(checked=False, moderation_enabled=False)
        return _moderate(ModerationRequest(f"[{filename}]", content_type, content_id), {"media": block})

    if len(data) > video.config.max_bytes:
        return _error(413, "media_too_large", f"the video is larger than {video.config.max_bytes} bytes",
                      limit=video.config.max_bytes)
    try:
        speech = video.check(data)
    except VideoUnavailable:
        # The check cannot run on this machine at all: fall back to the pre-existing policy
        # rather than closing the Videos tab. The record says checked: false.
        if not media.allow_unchecked_video:
            return _error(422, "media_not_checkable",
                          "video cannot be checked by this service, so it cannot be published. "
                          "Upload a still image or a PDF instead.", kind="video")
        block.update(text_found=False, checked=False)
        return jsonify({**_allowed_without_text(filename, content_type, content_id), "media": block}), 200
    except MediaError as exc:
        status = 413 if exc.code in ("media_too_large", "media_too_long") else 422
        return _error(status, exc.code, exc.message, **exc.extra)
    except VideoFailed:
        # It was listening and it broke. Refused, because publishing a video nobody listened to
        # is the hole this closes - and the author did nothing wrong, so offer a retry.
        return _error(503, "media_not_checkable",
                      "this video could not be checked just now. Please try again in a moment.",
                      kind="video")

    block.update(speech.as_dict(), audio_checked=True, checked=True,
                 text_found=speech.has_speech, text_readable=True)
    if not speech.has_speech:
        # No audio track, or no speech in it: nothing was said, so nothing is harmful in what
        # was said. Allowed, and recorded like any other upload with no text to judge.
        return jsonify({**_allowed_without_text(filename, content_type, content_id), "media": block}), 200
    if speech.translated and speech.source_text:
        # The English is a TRANSLATION, and a translation launders abuse: Whisper turns a Hindi
        # curse into something clean, and the text model is English-centric anyway. The gate has
        # Hindi and Hinglish rules (Devanagari and Roman script), so it reads the words as they
        # were SPOKEN as well. Only a block-level match refuses here; everything else still goes
        # to the model below, on the English.
        source = ModerationRequest(speech.source_text, content_type, content_id)
        gate, gate_ms = svc.pipeline.run_gate(source)
        if gate.blocked:
            policy = svc.levels.for_type(content_type.value)
            result = {**svc.pipeline.gate_only_result(source, gate, gate_ms), "moderation": policy.as_dict()}
            svc.sink.emit(result)
            return jsonify({**result, "media": block}), 200
    limit = svc.settings.pdf.max_chars
    if len(speech.text) > limit:
        return _error(413, "content_too_long",
                      f"the video's speech is {len(speech.text)} characters, over the {limit} character limit")
    return _moderate(ModerationRequest(speech.text, content_type, content_id), {"media": block})


def _visual_reject(filename: str, looked, content_type: ContentType,
                   content_id: str | None, extra: dict, rule_id: str = "", page: int | None = None):
    """A refusal decided by the picture rather than by any text.

    Built as a gate block so it is indistinguishable downstream from a blocked phrase: it gets
    the configured feedback wording for its category, an audit row through the usual sink, and
    the same shape of result a caller already handles. The "content" is the filename, because
    there is no text to quote - what was wrong is in `media.visual`.
    """
    svc = _svc()
    # Name the page when there is one, so the author is told where to look in a 40-page scan.
    where = f"[{filename}]" if page is None else f"[{filename}, page {page}]"
    req = ModerationRequest(where, content_type, content_id)
    # Name the channel that actually refused, not the best-matching label. An exemplar
    # refusal was being logged as `visual.safe` - the label with the highest score, which for
    # a cloud refused by a rainbow exemplar was literally `safe` at 0.46. An audit row saying
    # a picture was blocked by the safe rule is worse than no audit row: it sends whoever
    # reads it to the one place the cause is not.
    # Defensive because `looked` and the visual check are both injectable: a stub scorer in a
    # test, or an older bundle's result object, may carry neither. Naming the cause is a
    # diagnostic, and a diagnostic that can break the refusal it describes is worse than none.
    try:
        cause = looked.refused_by(svc.settings.visual.config)
    except Exception:
        cause = None
    if not rule_id:
        if cause == "exemplar":
            m = getattr(looked, "exemplar_match", None) or {}
            # The image id is what makes this actionable: it is the row to go and change.
            rule_id = f"visual.exemplar#{m.get('id')}" if m.get("id") else "visual.exemplar"
        elif cause == "probe":
            rule_id = f"visual.probe.{looked.label}"
        else:
            rule_id = f"visual.{looked.label}"
    # The exemplar's OWN category, not the best-matching label's. A cloud refused by a rainbow
    # exemplar was being filed under `weapons`, because that is what the prompts happened to
    # rank highest once the probe had spoken - a category with no bearing on why it was
    # refused, and the one an operator would chase first.
    category = getattr(looked, "category", "")
    if cause == "exemplar":
        category = (getattr(looked, "exemplar_match", None) or {}).get("category") or category
    match = GateMatch(rule_id=rule_id, category=category, action="block", spans=())
    result = svc.pipeline.gate_only_result(req, GateResult((match,)), 0.0)
    svc.sink.emit(result)
    return jsonify({**result, **extra}), 200


def _allowed_without_text(filename: str, content_type: ContentType, content_id: str | None) -> dict:
    """An allow for a file that carries no text to judge. Recorded like any other decision so
    the audit trail shows what was published and that nothing could be read in it."""
    svc = _svc()
    req = ModerationRequest(f"[{filename}]", content_type, content_id)
    gate, gate_ms = svc.pipeline.run_gate(req)
    result = svc.pipeline.gate_only_result(req, gate, gate_ms) if gate.blocked else \
        svc.pipeline._build(req, Decision.ALLOW, Stage.MODEL, gate, None, gate_ms, gate_ms, [], [], None, None, [])
    svc.sink.emit(result)
    return result


@bp.get("/v1/moderate/<request_id>")
def moderation_status(request_id: str):
    svc = _svc()
    if svc.fetch_result is None:
        return _error(404, "not_supported", "status lookup is only available in async mode")
    result = svc.fetch_result(request_id)
    return jsonify(result), 200


@bp.get("/demo")
def demo_page():
    """Browser demo UI. Enabled with DEMO_PAGE=1; never on in production."""
    if not _svc().settings.demo_page:
        return _error(404, "not_found", "demo page disabled (set DEMO_PAGE=1)")
    return send_from_directory(Path(__file__).parent / "static", "demo.html")


_ARTICLES_DIR = Path(__file__).parent / "static" / "articles"


@bp.get("/articles")
@bp.get("/articles/<path:name>")
def articles_page(name: str = "index.html"):
    """Articles tab + Write-article modal with publish-time moderation (front-end reference).
    Enabled with DEMO_PAGE=1, like /demo."""
    if not _svc().settings.demo_page:
        return _error(404, "not_found", "demo pages disabled (set DEMO_PAGE=1)")
    return send_from_directory(_ARTICLES_DIR, name)


@bp.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


@bp.get("/ready")
def ready():
    svc = _svc()
    body = {
        "mode": svc.settings.mode,
        "gate_version": svc.pipeline.gate.version,
        "gate_rules": len(svc.pipeline.gate.rules),
        "thresholds": svc.settings.thresholds.as_dict(),
        # Whether pictures are actually being looked at. An operator should be able to read
        # this here rather than infer it from a published result - the visual check turns
        # itself off when its model is missing, and that is exactly when you want to know.
        "visual": svc.settings.visual.status,
        "video": svc.settings.video.status,
        # Which surfaces are currently unmoderated. An operator should be able to read that
        # from outside rather than infer it from what gets published.
        "moderation_disabled_for": svc.levels.disabled_features(),
    }
    if svc.settings.mode == "sync":
        body.update(model_ready=svc.models.ready, model_version=svc.models.version,
                    model_backend=svc.settings.model_backend, model_error=svc.models.last_error)
        if not svc.models.ready:
            return jsonify({"status": "not_ready", **body}), 503
    return jsonify({"status": "ready", **body}), 200


def _require_admin():
    """None if the caller is an admin (logged-in session or X-Admin-Token), else an error response."""
    from app.admin_auth import check_admin
    return check_admin()


@bp.get("/v1/admin/moderation-levels")
def get_moderation_levels():
    """The whole table: the named levels, the default, and every configured feature."""
    denied = _require_admin()
    if denied:
        return denied
    return jsonify(_svc().levels.as_dict()), 200


@bp.put("/v1/admin/moderation-levels/<content_type>")
def set_moderation_level(content_type: str):
    """Change one feature, live.

        {"enabled": false}                 the toggle
        {"level": "strict"}                a named level
        {"reject_min": 0.62}               the rating directly, 0 to 1

    `enabled` and the rating are independent: setting a level on a disabled feature stores it
    for when the toggle comes back on. Takes effect on the next request; nothing restarts.
    """
    denied = _require_admin()
    if denied:
        return denied
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not body:
        return _error(400, "invalid_request", "send a JSON object with enabled, level or reject_min")
    known = {"enabled", "level", "reject_min", "allow_max"}
    unknown = set(body) - known
    if unknown:
        return _error(400, "invalid_request", f"unknown fields {sorted(unknown)}; "
                                              f"expected any of {sorted(known)}")
    try:
        policy = _svc().levels.set_feature(
            content_type, enabled=body.get("enabled"), level=body.get("level"),
            reject_min=body.get("reject_min"), allow_max=body.get("allow_max"))
    except (ValueError, TypeError) as exc:
        return _error(400, "invalid_request", str(exc))
    from app.admin_auth import remember
    remember(content_type, body)           # saved, so a restart keeps it
    log.warning("moderation level changed by admin: %s -> %s", content_type, policy.as_dict())
    return jsonify({"content_type": content_type, **policy.as_dict()}), 200


@bp.post("/v1/admin/model/reload")
def reload_model():
    """Hot-swap to the bundle in MODEL_DIR (e.g. after the ML team ships a new version).
    Only swaps if the new model loads and warms up; otherwise the old one stays live."""
    svc = _svc()
    token = svc.settings.admin_token
    supplied = request.headers.get("X-Admin-Token", "")
    if not token:
        return _error(404, "not_found", "admin endpoints disabled (ADMIN_TOKEN unset)")
    if not hmac.compare_digest(supplied, token):
        return _error(401, "unauthorized", "invalid admin token")

    previous = svc.models.version
    s = svc.settings
    try:
        svc.models.load(s.model_backend, s.model_dir, **s.model_kwargs())
    except Exception as exc:
        return _error(500, "reload_failed", f"{type(exc).__name__}: {exc}", active_version=previous)
    return jsonify({"status": "reloaded", "previous_version": previous, "model_version": svc.models.version}), 200


@bp.post("/v1/media/transcribe")
def transcribe_media_file():
    """Transcribes an uploaded video file using the offline Faster-Whisper pipeline.
    Returns detected language, full transcript, word/sentence segments, and exportable formats (txt, srt, vtt, json).
    """
    upload = request.files.get("file")
    if upload is None:
        return _error(400, "missing_file", "multipart field 'file' is required")

    filename = upload.filename or "video.mp4"
    data = upload.read()
    if not data:
        return _error(400, "empty_file", "uploaded file is empty")

    import os
    import sys
    import tempfile
    from pathlib import Path

    # Ensure pipeline package is importable
    root_dir = Path(__file__).resolve().parents[2]
    if str(root_dir) not in sys.path:
        sys.path.insert(0, str(root_dir))

    try:
        from pipeline.config import PipelineConfig
        from pipeline.orchestrator import VideoTranscriptionPipeline
        from pipeline.stages.ingestion import IngestionError, NoAudioStreamError
    except ImportError as exc:
        return _error(500, "transcription_unavailable", f"Pipeline import error: {exc}")

    suffix = Path(filename).suffix or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)

    try:
        config = PipelineConfig(
            model_size="tiny",
            device="auto",
            output_formats={"txt", "srt", "vtt", "json"},
            word_timestamps=True,
            keep_extracted_audio=False,
        )
        pipeline = VideoTranscriptionPipeline(config=config)
        result = pipeline.process(tmp_path)

        sink = pipeline.sink_stage
        txt_content = sink.to_txt(result.full_text, result.segments)
        srt_content = sink.to_srt(result.segments)
        vtt_content = sink.to_vtt(result.segments)
        json_content = sink.to_json(result)

        return jsonify({
            "status": "ok",
            "filename": filename,
            "detected_language": result.detected_language,
            "language_probability": round(result.language_probability, 4),
            "processing_time_seconds": round(result.processing_time_seconds, 2),
            "total_words": result.total_words,
            "full_text": result.full_text,
            "segments": [
                {
                    "id": s.id,
                    "start": round(s.start, 3),
                    "end": round(s.end, 3),
                    "text": s.text,
                    "time_range": s.formatted_time_range,
                }
                for s in result.segments
            ],
            "formats": {
                "txt": txt_content,
                "srt": srt_content,
                "vtt": vtt_content,
                "json": json_content,
            }
        }), 200

    except NoAudioStreamError:
        return _error(422, "no_audio_stream", "The uploaded video does not contain an audio stream to transcribe.")
    except Exception as exc:
        log.exception("Transcription failed: %s", exc)
        return _error(500, "transcription_failed", str(exc))
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass

