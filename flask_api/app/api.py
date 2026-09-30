"""HTTP endpoints. Request/response shapes are documented in contracts/moderation_result.md."""
from __future__ import annotations

import hmac
import logging
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from app.model import ModelNotReady
from app.pdf import PdfError
from app.pdf import extract as pdf_extract
from app.pipeline import ContentType, ModerationRequest

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


def _moderate(req: ModerationRequest, extra: dict | None = None):
    """Gate, then queue or score. Shared by the JSON and PDF endpoints so a PDF is judged by
    exactly the same rules. `extra` is merged into the response (the PDF page map)."""
    svc = _svc()
    # Layer 1 always runs inline — it's cheap, and a block needs no model or queue.
    gate, gate_ms = svc.pipeline.run_gate(req)
    if gate.blocked:
        result = svc.pipeline.gate_only_result(req, gate, gate_ms)
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
        result = svc.pipeline.moderate(req, gate=gate, gate_ms=gate_ms)
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
def moderate_pdf():
    """Moderate an uploaded PDF: extract its text, then run the ordinary pipeline.

    multipart/form-data: `file` (required), `content_type` (default "article"), `content_id`.
    The result is the usual payload plus a `pdf` block mapping character offsets to pages, so
    the client can show each issue on the page it came from. A PDF whose pages can't be read
    as text is rejected rather than allowed — see app/pdf.py.
    """
    svc = _svc()
    limits = svc.settings.pdf
    if not limits.enabled:
        return _error(404, "not_found", "PDF moderation is disabled")

    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return _error(400, "invalid_file", "attach a PDF as the 'file' field of a multipart form")

    try:
        content_type = ContentType(request.form.get("content_type", ContentType.ARTICLE.value))
    except ValueError:
        return _error(400, "invalid_content_type", "unknown 'content_type'", allowed=_CONTENT_TYPES)
    content_id = request.form.get("content_id") or None

    data = upload.read(limits.max_bytes + 1)
    if len(data) > limits.max_bytes:
        return _error(413, "pdf_too_large", f"the file is larger than {limits.max_bytes} bytes",
                      limit=limits.max_bytes)

    try:
        extracted = pdf_extract(data, limits, upload.filename, svc.settings.ocr)
    except PdfError as exc:
        status = 413 if exc.code in ("pdf_too_large", "pdf_too_many_pages", "pdf_text_too_long") else 422
        return _error(status, exc.code, exc.message, **exc.extra)

    if len(extracted.text) > svc.settings.max_chars:
        return _error(413, "content_too_long",
                      f"the PDF's text is {len(extracted.text)} characters, over the "
                      f"{svc.settings.max_chars} character limit")

    req = ModerationRequest(extracted.text, content_type, content_id)
    return _moderate(req, {"pdf": {"filename": upload.filename, **extracted.as_dict()}})


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
