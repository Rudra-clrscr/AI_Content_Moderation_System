"""HTTP endpoints. Request/response shapes are documented in contracts/moderation_result.md."""
from __future__ import annotations

import hmac
import logging
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from app.gate import GateMatch, GateResult
from app.media import MediaError, detect_kind, read_image
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
    pipeline = svc.pipeline if thresholds is None else svc.pipeline.with_thresholds(thresholds)
    # Layer 1 always runs inline — it's cheap, and a block needs no model or queue.
    gate, gate_ms = pipeline.run_gate(req)
    if gate.blocked:
        result = pipeline.gate_only_result(req, gate, gate_ms)
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
            **(extra or {}),
        }), 202

    try:
        result = pipeline.moderate(req, gate=gate, gate_ms=gate_ms)
    except ModelNotReady as exc:
        return _error(503, "model_not_ready", str(exc))
    except Exception:
        log.exception("inference failed for request %s", req.request_id)
        return _error(500, "inference_failed", "model inference failed", request_id=req.request_id)
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
      * **Video** — nothing can be read. The shipped config sets `media.allow_unchecked_video`,
        so it is allowed with `checked: false`; turn the flag off to refuse video instead.

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

    cap = max(pdf_limits.max_bytes, media.max_bytes)
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
            extracted = pdf_extract(data, pdf_limits, upload.filename, svc.settings.ocr)
        except PdfError as exc:
            status = 413 if exc.code in ("pdf_too_large", "pdf_too_many_pages", "pdf_text_too_long") else 422
            return _error(status, exc.code, exc.message, **exc.extra)
        text = extracted.text
        block = {**block, **extracted.as_dict()}
        extra = {"pdf": block, "media": block}          # "pdf" kept for the original contract

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


def _visual_reject(filename: str, looked, content_type: ContentType,
                   content_id: str | None, extra: dict):
    """A refusal decided by the picture rather than by any text.

    Built as a gate block so it is indistinguishable downstream from a blocked phrase: it gets
    the configured feedback wording for its category, an audit row through the usual sink, and
    the same shape of result a caller already handles. The "content" is the filename, because
    there is no text to quote - what was wrong is in `media.visual`.
    """
    svc = _svc()
    req = ModerationRequest(f"[{filename}]", content_type, content_id)
    match = GateMatch(rule_id=f"visual.{looked.label}", category=looked.category,
                      action="block", spans=())
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
    }
    if svc.settings.mode == "sync":
        body.update(model_ready=svc.models.ready, model_version=svc.models.version,
                    model_backend=svc.settings.model_backend, model_error=svc.models.last_error)
        if not svc.models.ready:
            return jsonify({"status": "not_ready", **body}), 503
    return jsonify({"status": "ready", **body}), 200


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
