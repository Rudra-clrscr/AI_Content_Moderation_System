"""HTTP endpoints. Request/response shapes are documented in contracts/moderation_result.md."""
from __future__ import annotations

import hmac
import logging
import re
from pathlib import Path

from flask import Blueprint, Response, current_app, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from app import client_address
from app.gate import GateMatch, GateResult
from app.media import MediaError, detect_kind, read_image
from app.model import ModelNotReady
from app.pipeline import SCHEMA_VERSION, ContentType, ModerationRequest, Stage
from app.routing import Decision, Thresholds

log = logging.getLogger(__name__)
bp = Blueprint("moderation", __name__)

_CONTENT_TYPES = [c.value for c in ContentType]


def _svc():
    return current_app.extensions["moderation"]


def _envelope(data, message: str, success: bool = True) -> dict:
    """The company's `ApiResponse<T>`: `Success`, `Message`, `Data`, and nothing else.

    Every one of the 23 `*ApiResponse` schemas in the company specification carries exactly
    these three properties and declares `additionalProperties: false`, so the payload belongs
    in `Data` and nowhere else.

    An earlier version of this envelope also added a `Type` field and repeated the whole
    payload alongside `Data`. `Type` is not in the company contract at any point, and the
    repeated copy made every response invalid against it, so both are gone: `Data` is the
    payload. Callers read `Data`.
    """
    return {"Success": success, "Message": message, "Data": data}


def _error(status: int, code: str, message: str, **extra):
    """A refusal, in the same envelope as everything else.

    `Success: false` and `Message` are what a .NET `HttpClient` branches on. `Data.code` is
    the machine-readable name of the refusal, for a caller that has to tell one from another -
    it is stable, where `Message` is prose and may be reworded. There is no top-level `error`
    key any more; the company envelope has no room for one.
    """
    return jsonify(_envelope({"code": code, "message": message, **extra},
                             message, success=False)), status


def _format_moderation_data(result: dict) -> dict:
    raw = result.get("decision", "allow")
    is_approved = (raw in ("allow", "approved"))
    # The .NET server operates on two responses: "approved" and "reject"
    action = "approved" if is_approved else "reject"

    data = dict(result)
    data["Action"] = action
    data["Decision"] = action
    data["action"] = action
    data["verdict"] = action
    data["approval_status"] = action
    data["legacy_decision"] = raw
    # "approved" when allowed; retain "revise" if internal pipeline produced a revise band
    data["decision"] = "approved" if is_approved else raw
    if "status" not in data:
        data["status"] = "completed"

    fb = result.get("feedback")
    if fb and isinstance(fb, dict):
        data["Note"] = fb.get("message") or fb.get("title")
    elif not is_approved:
        matches = result.get("gate_matches") or []
        if matches:
            data["Note"] = f"Violation of policy rule: {matches[0].get('rule_id', 'content_policy')}"
        else:
            data["Note"] = f"Content rejected by moderation risk score: {result.get('risk_score', 1.0)}"
    else:
        data["Note"] = None

    return data


def _approved_message(data: dict) -> str:
    """What an approval is allowed to claim.

    "Content approved for publication" has to mean something looked and found nothing. Two
    paths publish without that: a surface an admin switched moderation off for, and a file
    nothing could read - a PDF, a video, writing OCR could not make out. The rest of this
    service takes care that neither can be read as a clean result (`decided_by: "disabled"`,
    `risk_score: null`, `media.checked: false`), and the envelope's own sentence - the one
    line a .NET caller is most likely to log or show an operator - must not undo that.
    """
    if data.get("decided_by") == "disabled":
        return "Published without being checked: moderation is switched off for this surface."
    media = data.get("media")
    if isinstance(media, dict) and media.get("checked") is False:
        return "Published, but nothing in this file could be read or checked."
    n = data.get("attachments_not_checked") or 0
    if n:
        # The text passed. The pictures that came with it were never looked at, and this is
        # the one line a caller is most likely to log or show an operator.
        return (f"Text approved for publication, but {n} "
                f"attachment{'' if n == 1 else 's'} on this submission "
                f"{'was' if n == 1 else 'were'} not checked - send each to "
                f"POST /v1/moderate/media.")
    return "Content approved for publication."


def _api_response(result: dict, message: str | None = None, status: int = 200, extra: dict | None = None):
    merged = {**result, **(extra or {})}
    data = _format_moderation_data(merged)
    is_approved = (data["Action"] == "approved")
    if message:
        msg = message
    elif is_approved:
        msg = _approved_message(data)
    else:
        msg = data.get("Note") or "Content rejected by moderation policy."

    return jsonify(_envelope(data, msg)), status


# The endpoints a member submits through. Listed rather than matched on a path prefix because
# the admin screens are on this blueprint too, and an operator who blocks their own office
# network must not thereby lock themselves out of the dashboard that would undo it.
#
# Built from bp.name rather than written out: Flask keys endpoints as "<blueprint>.<function>",
# and a hardcoded prefix that does not match turns every check below into a silent no-op -
# blocking that accepts every submission and reports nothing wrong.
SUBMISSION_ENDPOINTS = {f"{bp.name}.{fn}" for fn in
                        ("moderate", "moderate_media", "moderate_unified", "transcribe_media_file")}

# What the bearer token gates: the submissions, plus the status lookup, which hands back a
# whole moderation result to anyone who can guess a request id. NOT the probes - a load
# balancer has to reach /health and /ready without a secret - and not the admin screens,
# which have their own session-or-X-Admin-Token boundary in admin_auth.check_admin.
SERVICE_ENDPOINTS = SUBMISSION_ENDPOINTS | {f"{bp.name}.moderation_status"}


_FORWARDED_WARNED = False


def _warn_once_about_forwarded(hops: int, client: dict) -> None:
    """Say so when requests arrive forwarded but nothing is configured to trust them.

    This is the shape of the one configuration mistake that matters here: a proxy is put in
    front and `trust_proxy_hops` is left at 0, so every audit row records the proxy's address
    instead of the author's. Nothing fails - the log just quietly fills with one address, and
    it is noticed when somebody tries to block a member and refuses the whole site.

    Only the hops=0 direction is worth a warning. A header arriving with hops already set is
    the normal case, and a header that never arrives while hops is set resolves to the socket
    address anyway, which is the conservative answer rather than a wrong one.

    Once per process, because this runs on every request and an operator who has read it once
    does not need it on the next hundred thousand.
    """
    global _FORWARDED_WARNED
    if _FORWARDED_WARNED or hops > 0 or not client.get("forwarded"):
        return
    _FORWARDED_WARNED = True
    log.warning(
        "requests are arriving with X-Forwarded-For (%s) but trust_proxy_hops is 0, so the "
        "address recorded for every decision is %s - the proxy, not the author. Set "
        "trust_proxy_hops in config/settings.yaml (or TRUST_PROXY_HOPS) to the number of "
        "proxies in front of this service. Left as it is, the moderation log attributes "
        "everything to one address and blocking it refuses everybody.",
        client.get("forwarded"), client.get("ip") or "?")


@bp.before_request
def _resolve_client():
    """Work out who is calling, and turn them away if an operator has refused them.

    Done once per request rather than at each of the six places a result is recorded, so a new
    endpoint cannot quietly start writing audit rows with no address in them.
    """
    svc = current_app.extensions["moderation"]
    g.client = client_address.from_request(request, svc.settings.trust_proxy_hops)
    _warn_once_about_forwarded(svc.settings.trust_proxy_hops, g.client)
    # Authentication first: an unauthenticated caller is turned away before the blocklist,
    # which is a moderation convenience and says so about itself below.
    if request.endpoint in SERVICE_ENDPOINTS:
        refusal = _require_service_token()
        if refusal is not None:
            return refusal
    if request.endpoint not in SUBMISSION_ENDPOINTS:
        return None
    try:
        hit = current_app.extensions["admin"].store.blocked_entry(
            g.client.get("ip"), g.client.get("network"))
    except Exception:
        # The blocklist is a moderation convenience, not an authentication boundary. If it
        # cannot be read, submissions carry on and the failure is in the log, because the
        # alternative is the whole service going down with the admin database.
        log.exception("could not read the blocklist")
        return None
    if hit:
        # 403 and a flat sentence. Naming the address or the scope would tell someone probing
        # it exactly how wide the block is and which address to come back from.
        return _error(403, "client_blocked",
                      "submissions from this connection are not being accepted.")
    return None


def _require_service_token():
    """None if the caller may call the moderation endpoints, else a refusal.

    A bearer token in `Authorization`, which is the scheme, header and spelling the company
    API already declares (`Bearer`, `apiKey` in `Authorization`), so the .NET side reuses the
    handler it has rather than learning a second convention for this one service.

    WITH `SERVICE_TOKEN` UNSET THESE ENDPOINTS ARE OPEN. Every local setup and the whole test
    suite rely on that, so it stays - but it is a hole to close on deployment rather than a
    default to lean on, and it is the one setting that matters now that this service answers
    on its own host instead of beside the caller. `/ready` reports `service_auth` so a deploy
    check can assert it came out `enforced` rather than discover it did not.
    """
    if _svc().settings.auth_disabled:
        return None
    token = _svc().settings.service_token
    if not token:
        return None
    scheme, _, value = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(value.strip(), token):
        # No detail. Which half was wrong is the one thing worth knowing to someone guessing.
        return _error(401, "unauthorized",
                      "a valid bearer token is required to call this endpoint")
    return None


def _emit(result: dict) -> dict:
    """Record a decision, with the address it came from attached.

    Stamped onto the copy the SINK gets and not onto the response: it is the caller's own
    address, so returning it tells them nothing they did not know, and it would become part of
    the public API's shape for the sake of an audit column.
    """
    svc = current_app.extensions["moderation"]
    svc.sink.emit({**result, "client": getattr(g, "client", None) or {}})
    return result


@bp.errorhandler(HTTPException)
def _http_error(exc: HTTPException):
    return _error(exc.code or 500, exc.name.lower().replace(" ", "_"), exc.description or exc.name)


# The composer's fields, in the order it shows them, and the separator between them. Both
# match buildArticleContent in app/static/articles/moderation-client.js on purpose: a caller
# that joins its own form and a caller that sends the parts must produce the same string, or
# the same article scores two different ways depending on which door it came in.
# Every box a member can type prose into, in the order a reader meets them. Most surfaces
# fill three; a job posting fills most of them, and each is optional, so a form that has no
# `requirements` simply contributes nothing to the join.
PART_FIELDS = ("title", "company", "body", "responsibilities", "requirements",
               "qualification", "key_skills", "benefits", "hashtags")
PART_SEP = "\n\n"


def hashtag_words(tag: str) -> str:
    """`#dripIrrigation` -> `drip Irrigation`.

    The model scores words. A run-together tag is one unknown token and scores nothing, so a
    tag is worth moderating only once its words are recoverable - the difference between
    scoring `#firstCopyRolex` 0.000 and scoring `first copy rolex` for what it says.
    """
    t = re.sub(r"^#", "", str(tag))
    t = re.sub(r"_+", " ", t)
    t = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", t)
    t = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _normalise_hashtags(raw) -> str:
    """A list of tags, or the one comma-separated string the company's own DTOs carry."""
    if raw is None:
        return ""
    tags = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
    return " ".join(w for w in (hashtag_words(t) for t in tags) if w)


def join_parts(parts: dict) -> tuple[str, dict]:
    """Join the composer's fields into the one string the model sees.

    Returns the content and, beside it, where each field landed in it - so a caller that
    sent the parts never has to work out for itself which field a `feedback.issues` offset
    fell in. That sum was being done by hand, differently, by every surface that called in,
    and getting it wrong highlights the wrong box rather than failing loudly.
    """
    content, fields = "", {}
    for name in PART_FIELDS:
        value = (_normalise_hashtags(parts.get(name)) if name == "hashtags"
                 else str(parts.get(name) or "")).strip()
        if not value:
            continue
        if content:
            content += PART_SEP
        fields[name] = [len(content), len(content) + len(value)]
        content += value
    return content, fields


# The keys a forwarded `CreateArticleDto` / `CreatePostDTO` carries its attachments under.
# This endpoint reads text and cannot look at any of them.
ATTACHMENT_FIELDS = ("Medias", "medias", "MediaPaths", "media_paths",
                     "PostMedias", "post_medias", "ThumbnailUrl", "thumbnail_url",
                     "PdfBase64", "pdf_base64")


def _count_attachments(body: dict) -> int:
    """How many attachments came with a request that cannot check a single one.

    A .NET caller forwarding its own DTO sends the whole thing, pictures included. Reading
    the text out of it and answering `approved` would be the exact claim this service takes
    care never to make: that something was checked when nothing looked at it. So they are
    counted and said out loud rather than dropped on the floor.
    """
    total = 0
    for name in ATTACHMENT_FIELDS:
        value = body.get(name)
        if isinstance(value, (list, tuple)):
            total += len([v for v in value if v])
        elif isinstance(value, str) and value.strip():
            total += 1
    return total


# What each company DTO calls the member-written text, so one can be forwarded whole.
# The article DTOs say `Body`; `CreatePostDTO` says `TextContent`; `CreateGroupPostDto` says
# `Content`. A name missing from here is not an error anyone sees - the field is simply
# dropped and the text goes unmoderated - which is why the list is checked against the
# company specification by tests/test_dto_field_coverage.py rather than kept by hand.
PART_ALIASES = {
    "title": ("title", "Title"),
    "company": ("company", "CompanyName", "company_name"),
    "body": ("body", "Body", "TextContent", "text_content", "Content",
             "Description", "description"),
    "responsibilities": ("responsibilities", "Responsibilities"),
    "requirements": ("requirements", "Requirements"),
    "qualification": ("qualification", "Qualification"),
    "key_skills": ("key_skills", "KeySkills"),
    "benefits": ("benefits", "Benefits"),
    "hashtags": ("hashtags", "Hashtags"),
}


def _read_parts(body: dict) -> dict:
    """The parts as sent, under any of the names the company's own DTOs give them.

    A .NET caller forwarding its own DTO is the likeliest way to reach this endpoint, and
    before it took these names it was also the likeliest way to be silently ignored.
    """
    out = {}
    for name in PART_FIELDS:
        value = None
        for alias in PART_ALIASES[name]:
            if body.get(alias) is not None:
                value = body[alias]
                break
        out[name] = value
    return out


def _parse_request() -> ModerationRequest | tuple:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error(400, "invalid_json", "body must be a JSON object")

    parts = _read_parts(body)
    sent = sorted(n for n, v in parts.items() if v is not None)
    content = body.get("content")

    if sent and content is not None:
        # Choosing one silently is the failure this endpoint exists to stop. Name both.
        return _error(400, "invalid_request",
                      f"send either 'content' or the parts {sent}, not both", parts=sent)

    fields = {}
    if sent:
        content, fields = join_parts(parts)
        if not content.strip():
            return _error(400, "invalid_content",
                          f"the parts {sent} are all empty; there is nothing to check",
                          parts=sent)

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

    req = ModerationRequest(content, content_type, None if content_id is None else str(content_id))
    # Where each part landed. Offsets into `content` only help a caller who knows how
    # `content` was built, and when the service built it, that caller does not.
    #
    # ModerationRequest is frozen, and rightly so - what gets scored should not change
    # under the pipeline. This is bookkeeping about the request rather than part of it,
    # so it rides alongside and stays out of as_dict().
    object.__setattr__(req, "fields", fields)
    object.__setattr__(req, "unchecked_attachments", _count_attachments(body))
    return req


def _execute_moderation(req: ModerationRequest,
                        thresholds: "Thresholds | None" = None) -> tuple[dict | None, tuple | None]:
    svc = _svc()
    policy = svc.levels.for_type(req.content_type.value)
    if not policy.enabled:
        result = {**svc.pipeline.not_moderated_result(req), "moderation": policy.as_dict()}
        _emit(result)
        return result, None

    effective = thresholds or policy.thresholds
    pipeline = svc.pipeline if effective is None else svc.pipeline.with_thresholds(effective)

    def stamp(result: dict) -> dict:
        return {**result, "moderation": policy.as_dict()}

    gate, gate_ms = pipeline.run_gate(req)
    if gate.blocked:
        result = stamp(pipeline.gate_only_result(req, gate, gate_ms))
        _emit(result)
        return result, None

    if svc.settings.mode == "async":
        try:
            svc.enqueue(req)
        except Exception:
            log.exception("enqueue failed for request %s", req.request_id)
            return None, _error(503, "queue_unavailable", "moderation queue unavailable, retry later")
        async_data = {
            "request_id": req.request_id,
            "status": "pending",
            "Action": "pending",
            "content_id": req.content_id,
            "content_type": req.content_type.value,
            "status_url": f"/v1/moderate/{req.request_id}",
            "moderation": policy.as_dict(),
        }
        return None, (jsonify(_envelope(async_data,
                                        "Content queued for asynchronous moderation.")), 202)

    try:
        result = pipeline.moderate(req, gate=gate, gate_ms=gate_ms)
    except ModelNotReady as exc:
        return None, _error(503, "model_not_ready", str(exc))
    except Exception:
        log.exception("inference failed for request %s", req.request_id)
        return None, _error(500, "inference_failed", "model inference failed", request_id=req.request_id)
    result = stamp(result)
    _emit(result)
    return result, None


def _moderate(req: ModerationRequest, extra: dict | None = None,
              thresholds: "Thresholds | None" = None):
    res, err = _execute_moderation(req, thresholds)
    if err is not None:
        return err
    return _api_response(res, extra=extra)


@bp.post("/v1/moderate")
def moderate():
    req = _parse_request()
    if isinstance(req, tuple):
        return req
    # Reported only when the service did the joining. A caller that sent its own
    # `content` already knows where everything is.
    extra = {}
    fields = getattr(req, "fields", None)
    if fields:
        extra["fields"] = fields
    unchecked = getattr(req, "unchecked_attachments", 0)
    if unchecked:
        extra["attachments_not_checked"] = unchecked
    return _moderate(req, extra=extra or None)


def _execute_media_moderation(upload, content_type: ContentType,
                             content_id: str | None) -> tuple[dict | None, tuple | None]:
    svc = _svc()
    media = svc.settings.media

    if upload is None or not upload.filename:
        return None, _error(400, "invalid_file", "attach a file as the 'file' field of a multipart form")

    try:
        upload.seek(0)
    except Exception:
        pass

    data = upload.read(media.max_bytes + 1)
    if len(data) > media.max_bytes:
        return None, _error(413, "media_too_large", f"the file is larger than {media.max_bytes} bytes",
                            limit=media.max_bytes)

    kind = detect_kind(data, upload.filename)
    block = {"filename": upload.filename, "kind": kind}

    if kind == "pdf":
        if not media.allow_unchecked_pdf:
            return None, _error(422, "media_not_checkable",
                                "PDFs cannot be checked by this service at the moment, so this one "
                                "cannot be published. Paste the text, or attach an image instead.",
                                kind=kind)
        block.update(text_found=False, visual_content_checked=False, checked=False)
        allowed = _allowed_without_text(upload.filename, content_type, content_id)
        return {**allowed, "media": block}, None

    elif kind == "image":
        try:
            seen = read_image(data, svc.settings.ocr, media, svc.settings.visual)
        except MediaError as exc:
            return None, _error(413 if exc.code == "media_too_large" else 422, exc.code, exc.message, **exc.extra)
        read, looked = seen.text, seen.visual
        text = read.text

        block.update(text_found=read.has_text, ocr_confidence=round(read.confidence, 4),
                     text_readable=read.readable, **looked.as_dict())
        extra = {"media": block}
        if looked.failed:
            return None, _error(503, "media_not_checkable",
                                "this image could not be checked just now. Please try again in a "
                                "moment.", kind=kind)
        if svc.settings.visual.decides_reject(looked):
            res = _build_visual_reject_result(upload.filename, looked, content_type, content_id, extra)
            return res, None
        if read.failed:
            return None, _error(503, "media_not_checkable",
                                "this image could not be checked just now. Please try again in a "
                                "moment.", kind=kind)
        if read.has_text and not read.readable:
            seen_and_clear = media.allow_unreadable_image_when_seen and looked.checked
            if not (media.allow_unreadable_image or seen_and_clear):
                return None, _error(422, "media_unreadable",
                                    "there is writing in this image that could not be read, so it "
                                    "cannot be checked. Upload a clearer picture, or put the words "
                                    "in the post itself.", kind=kind, text_readable=False)
            block.update(checked=False)
            allowed = _allowed_without_text(upload.filename, content_type, content_id)
            return {**allowed, "media": block}, None
        if not text.strip():
            allowed = _allowed_without_text(upload.filename, content_type, content_id)
            return {**allowed, "media": block}, None

    elif kind == "video":
        if not media.allow_unchecked_video:
            return None, _error(422, "media_not_checkable",
                                "video cannot be checked by this service, so it cannot be published. "
                                "Upload a still image instead.", kind=kind)
        block.update(text_found=False, visual_content_checked=False, checked=False)
        allowed = _allowed_without_text(upload.filename, content_type, content_id)
        return {**allowed, "media": block}, None

    else:
        return None, _error(422, "media_unsupported",
                            "this file type cannot be checked. Attach an image, a video or a PDF.",
                            kind=kind)

    char_limit = svc.settings.max_chars
    if len(text) > char_limit:
        return None, _error(413, "content_too_long",
                            f"the file's text is {len(text)} characters, over the "
                            f"{char_limit} character limit")

    thresholds = None
    if kind == "image":
        thresholds = Thresholds(allow_max=media.ocr_text_reject_min,
                                reject_min=media.ocr_text_reject_min)
    res, err = _execute_moderation(ModerationRequest(text, content_type, content_id), thresholds)
    if err is not None:
        return None, err
    return {**res, "media": block}, None


@bp.post("/v1/moderate/media")
def moderate_media():
    """Moderate one uploaded file: read whatever text it carries, then run the ordinary pipeline.

    multipart/form-data: `file` (required), `content_type` (default "article"), `content_id`.
    Post one file per request — each gets its own decision and its own audit row.

    What can be read differs by kind, and so does the rule (see app/media.py):

      * **Image** — two passes over the same picture. OCR reads any writing, which catches
        the bypass of putting the scam in a screenshot; the CLIP visual check (app/clip.py)
        scores the picture itself against `config/visual_policy.yaml`, which catches a weapon
        or gore photograph carrying no words at all. With the visual check off or its model
        missing, the result says `visual_content_checked: false` and behaves as it used to.
      * **PDF** — not checked. Extraction was taken out while the scanned-page path is
        reworked, so `media.allow_unchecked_pdf` decides, exactly as it does for video:
        publish it and record `checked: false`, or refuse it.
      * **Video** — not checked. The audio-transcription path was removed along with its
        Whisper dependency, so `media.allow_unchecked_video` decides: refuse it, or publish it
        and record `checked: false` so nothing reads the allow as "we looked at it".
    """
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return _error(400, "invalid_file", "attach a file as the 'file' field of a multipart form")

    try:
        content_type = ContentType(request.form.get("content_type", ContentType.ARTICLE.value))
    except ValueError:
        return _error(400, "invalid_content_type", "unknown 'content_type'", allowed=_CONTENT_TYPES)
    content_id = request.form.get("content_id") or None

    res, err = _execute_media_moderation(upload, content_type, content_id)
    if err is not None:
        return err
    return _api_response(res)


@bp.post("/v1/moderate/unified")
def moderate_unified():
    """Unified moderation endpoint: intelligently moderates text, image/media, or both.

    Accepts multipart/form-data or application/json.
      - If only text is provided: runs ONLY text moderation (DeBERTa pipeline).
      - If only image/media is provided: runs ONLY image moderation (visual check / OCR).
      - If both are provided: runs BOTH moderation checks and combines the results.
        If either check rejects, the final combined decision is reject.
    """
    body = request.get_json(silent=True) or {}
    form = request.form or {}
    files = request.files or {}

    # Extract text content. The parts come first: an article arriving here is the one with a
    # picture attached, so this is the endpoint most likely to be handed a whole DTO, and
    # dropping its title because `content` was absent is the failure /v1/moderate already had.
    unified_fields = {}
    source = {**body, **form}

    # Extract upload file
    upload = files.get("file") or files.get("image") or files.get("media")
    has_file = (upload is not None and bool(upload.filename))

    def _blank(v) -> bool:
        if v is None:
            return True
        if isinstance(v, str):
            return not v.strip()
        if isinstance(v, (list, tuple)):
            return not any(str(x).strip() for x in v if x is not None)
        return False

    # A BLANK FIELD IS NOT A SENT FIELD. Swagger's "Send empty value" boxes, and a .NET form
    # serialising every property, both send `Title=` and `content=` for boxes nobody typed in.
    # Counting those as sent refused a picture-only request for "sending content and the parts
    # together" when neither held a word.
    parts = {k: (None if _blank(v) else v) for k, v in _read_parts(source).items()}
    sent = sorted(n for n, v in parts.items() if v is not None)
    raw_content, content_key = None, None
    for k in ("content", "text"):
        if k in form or k in body:
            raw_content, content_key = (form.get(k) if k in form else body.get(k)), k
            break
    content_given = content_key is not None and not _blank(raw_content)

    if sent and content_given:
        # The same rule as /v1/moderate. Taking the parts and quietly dropping `content` meant
        # a Title typed beside the text in the Swagger form threw the text away unscored.
        return _error(400, "invalid_request",
                      f"send either 'content' or the parts {sent}, not both - put the text "
                      f"in 'Body' alongside 'Title'", parts=sent)

    text_provided = False
    content = None
    if sent:
        content, unified_fields = join_parts(parts)
        text_provided = True
    elif content_given:
        if not isinstance(raw_content, str):
            return _error(400, "invalid_content", "'content' must be a non-empty string")
        content = raw_content
        text_provided = True
    elif not has_file and (content_key is not None
                           or any(a in source for al in PART_ALIASES.values() for a in al)):
        # Text boxes were sent, all blank, and there is no picture: nothing at all to check.
        # With a picture the blank boxes are simply an empty caption - a photo-only post.
        return _error(400, "invalid_content", "'content' must be a non-empty string")

    if text_provided:
        max_chars = _svc().settings.max_chars
        if len(content) > max_chars:
            return _error(413, "content_too_long", f"'content' exceeds {max_chars} characters")

    if not text_provided and not has_file:
        return _error(400, "invalid_request", "At least one of 'content' (text) or 'file' (image/media) must be provided")

    raw_ct = form.get("content_type") or body.get("content_type")
    if raw_ct:
        try:
            content_type = ContentType(raw_ct)
        except ValueError:
            return _error(400, "invalid_content_type", "unknown 'content_type'", allowed=_CONTENT_TYPES)
    else:
        content_type = ContentType.ARTICLE

    raw_cid = form.get("content_id") if "content_id" in form else body.get("content_id")
    if raw_cid is not None:
        if not isinstance(raw_cid, (str, int)):
            return _error(400, "invalid_content_id", "'content_id' must be a string or integer")
        content_id = str(raw_cid)
    else:
        content_id = None

    # Case 1: Only text is provided
    if text_provided and not has_file:
        req = ModerationRequest(content, content_type, content_id)
        res, err = _execute_moderation(req)
        if err is not None:
            return err
        text_data = _format_moderation_data(res)
        checks = {
            "text": {
                "executed": True,
                "decision": text_data.get("decision"),
                "result": text_data,
            },
            "image": {
                "executed": False,
                "decision": None,
                "result": None,
            },
        }
        unified_data = dict(text_data)
        unified_data["checks"] = checks
        if unified_fields:
            unified_data["fields"] = unified_fields
        msg = _approved_message(unified_data) if unified_data["Action"] == "approved" else (
            unified_data.get("Note") or "Content rejected by moderation policy."
        )
        return jsonify(_envelope(unified_data, msg)), 200

    # Case 2: Only image is provided
    if has_file and not text_provided:
        res, err = _execute_media_moderation(upload, content_type, content_id)
        if err is not None:
            return err
        image_data = _format_moderation_data(res)
        checks = {
            "text": {
                "executed": False,
                "decision": None,
                "result": None,
            },
            "image": {
                "executed": True,
                "decision": image_data.get("decision"),
                "result": image_data,
            },
        }
        unified_data = dict(image_data)
        unified_data["checks"] = checks
        msg = _approved_message(unified_data) if unified_data["Action"] == "approved" else (
            image_data.get("Note") or "Content rejected by moderation policy."
        )
        return jsonify(_envelope(unified_data, msg)), 200

    # Case 3: Both text and image are provided
    req = ModerationRequest(content, content_type, content_id)
    text_res, text_err = _execute_moderation(req)
    if text_err is not None:
        return text_err

    image_res, image_err = _execute_media_moderation(upload, content_type, content_id)
    if image_err is not None:
        return image_err

    text_data = _format_moderation_data(text_res)
    image_data = _format_moderation_data(image_res)

    text_dec = text_data.get("decision", "approved")
    image_dec = image_data.get("decision", "approved")
    text_raw = text_data.get("legacy_decision", text_dec)
    image_raw = image_data.get("legacy_decision", image_dec)

    text_rejected = (text_dec == "reject" or text_raw == "reject")
    image_rejected = (image_dec == "reject" or image_raw == "reject")
    text_revise = (text_dec == "revise" or text_raw == "revise")
    image_revise = (image_dec == "revise" or image_raw == "revise")

    if text_rejected or image_rejected:
        final_decision = "reject"
        final_action = "reject"
        final_legacy = "reject"
    elif text_revise or image_revise:
        final_decision = "revise"
        final_action = "reject"
        final_legacy = "revise"
    else:
        final_decision = "approved"
        final_action = "approved"
        final_legacy = "allow"

    notes = []
    if text_rejected or text_revise:
        notes.append(text_data.get("Note") or "Text violates moderation policy.")
    if image_rejected or image_revise:
        notes.append(image_data.get("Note") or "Image violates moderation policy.")
    note = " | ".join(notes) if notes else None

    # If EITHER half is one that nothing actually looked at, the combined result is too.
    # `_approved_message` reads `decided_by` to tell an approval that was inspected from one
    # that was simply published, and an approval here must not claim an inspection that half
    # of it never got. `checks` below keeps the per-half truth.
    decided = [text_data.get("decided_by"), image_data.get("decided_by")]
    combined_decided_by = "disabled" if "disabled" in decided else (decided[0] or decided[1])

    gate_matches = (text_data.get("gate_matches") or []) + (image_data.get("gate_matches") or [])
    scores = [s for s in (text_data.get("risk_score"), image_data.get("risk_score")) if s is not None]
    combined_risk = max(scores) if scores else None

    checks = {
        "text": {
            "executed": True,
            "decision": text_dec,
            "result": text_data,
        },
        "image": {
            "executed": True,
            "decision": image_dec,
            "result": image_data,
        },
    }

    unified_data = {
        "schema_version": SCHEMA_VERSION,
        "request_id": text_data.get("request_id") or image_data.get("request_id"),
        "status": "completed",
        "content_id": content_id,
        "content_type": content_type.value,
        "content": content,
        "decision": final_decision,
        "decided_by": combined_decided_by,
        **({"fields": unified_fields} if unified_fields else {}),
        "Action": final_action,
        "Decision": final_action,
        "action": final_action,
        "verdict": final_action,
        "approval_status": final_action,
        "legacy_decision": final_legacy,
        "risk_score": combined_risk,
        "media": image_data.get("media"),
        "gate_matches": gate_matches,
        "feedback": text_data.get("feedback") or image_data.get("feedback"),
        "Note": note,
        "checks": checks,
        "moderation": text_data.get("moderation") or image_data.get("moderation"),
        "decided_at": text_data.get("decided_at") or image_data.get("decided_at"),
    }

    msg = _approved_message(unified_data) if final_action == "approved" else (
        note or "Content rejected by moderation policy."
    )
    return jsonify(_envelope(unified_data, msg)), 200


def _build_visual_reject_result(filename: str, looked, content_type: ContentType,
                               content_id: str | None, extra: dict, page: int | None = None) -> dict:
    svc = _svc()
    where = f"[{filename}]" if page is None else f"[{filename}, page {page}]"
    req = ModerationRequest(where, content_type, content_id)
    try:
        cause = looked.refused_by(svc.settings.visual.config)
    except Exception:
        cause = None
    if cause == "exemplar":
        m = getattr(looked, "exemplar_match", None) or {}
        rule_id = f"visual.exemplar#{m.get('id')}" if m.get("id") else "visual.exemplar"
    elif cause == "probe":
        rule_id = f"visual.probe.{looked.label}"
    else:
        rule_id = f"visual.{looked.label}"
    category = getattr(looked, "category", "")
    if cause == "exemplar":
        marked = (getattr(looked, "exemplar_match", None) or {}).get("category") or ""
        try:
            category = svc.settings.visual.policy_category(marked)
        except Exception:
            log.exception("could not translate exemplar category %r", marked)
            category = ""
    match = GateMatch(rule_id=rule_id, category=category, action="block", spans=())
    result = svc.pipeline.gate_only_result(req, GateResult((match,)), 0.0)
    _emit(result)
    return {**result, **(extra or {})}


def _visual_reject(filename: str, looked, content_type: ContentType,
                   content_id: str | None, extra: dict, page: int | None = None):
    res = _build_visual_reject_result(filename, looked, content_type, content_id, extra, page)
    return _api_response(res)



def _allowed_without_text(filename: str, content_type: ContentType, content_id: str | None) -> dict:
    """An allow for a file that carries no text to judge. Recorded like any other decision so
    the audit trail shows what was published and that nothing could be read in it."""
    svc = _svc()
    req = ModerationRequest(f"[{filename}]", content_type, content_id)
    gate, gate_ms = svc.pipeline.run_gate(req)
    result = svc.pipeline.gate_only_result(req, gate, gate_ms) if gate.blocked else \
        svc.pipeline._build(req, Decision.ALLOW, Stage.MODEL, gate, None, gate_ms, gate_ms, [], [], None, None, [])
    _emit(result)
    return result


@bp.get("/v1/moderate/<request_id>")
def moderation_status(request_id: str):
    svc = _svc()
    if svc.fetch_result is None:
        return _error(404, "not_supported", "status lookup is only available in async mode")
    result = svc.fetch_result(request_id)
    if not isinstance(result, dict):
        return _error(500, "invalid_status", "unexpected result from status lookup")
    if result.get("status") == "completed":
        return _api_response(result)
    return jsonify(_envelope(result,
                             f"Moderation status: {result.get('status', 'pending')}")), 200


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


# The spec is documentation, so it lives in docs/ beside API_REFERENCE.md rather than being
# copied in here - two copies of an endpoint list drift, and the stale one is always the one
# somebody tests against. A deploy that ships flask_api/ alone therefore has no spec, which is
# why the handler below says so plainly instead of returning Flask's 404 page.
_SPEC_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


@bp.get("/openapi.yaml")
def openapi_spec():
    """The OpenAPI document. Served by default; API_DOCS=0 turns it off."""
    if not _svc().settings.api_docs:
        return _error(404, "not_found", "API docs are turned off (API_DOCS=0)")
    if not (_SPEC_DIR / "openapi.yaml").exists():
        return _error(404, "not_found",
                      f"no spec at {_SPEC_DIR / 'openapi.yaml'}; it ships in the repository's "
                      "docs/ directory, which a flask_api-only deployment does not carry")
    if _svc().settings.auth_disabled:
        # No lock icons and no Authorize button for checks that are not there. The file on
        # disk keeps its security declarations for when authentication goes back on.
        return Response(_spec_without_auth(), mimetype="application/yaml")
    return send_from_directory(_SPEC_DIR, "openapi.yaml", mimetype="application/yaml")


def _spec_without_auth() -> str:
    import yaml

    spec = yaml.safe_load((_SPEC_DIR / "openapi.yaml").read_text(encoding="utf-8"))
    spec["security"] = []
    (spec.get("components") or {}).pop("securitySchemes", None)
    for ops in (spec.get("paths") or {}).values():
        for op in (ops or {}).values():
            if isinstance(op, dict):
                op.pop("security", None)
    info = spec.setdefault("info", {})
    info["description"] = ("> **Authentication is switched off on this server** (`AUTH_DISABLED=1`). "
                           "Every endpoint, admin ones included, answers without a token or a "
                           "login.\n\n" + str(info.get("description") or ""))
    return yaml.safe_dump(spec, sort_keys=False, allow_unicode=True)


@bp.get("/docs")
def api_docs():
    """Swagger UI, served from this app so "Try it out" is a SAME-ORIGIN request.

    This service sends no CORS headers, and adding them to reach a Swagger UI hosted
    somewhere else would mean relaxing the browser's protection on every endpoint - on the
    unauthenticated submission endpoints too - for the sake of a developer convenience.
    Serving the page from here costs one route and leaves that boundary alone.
    """
    if not _svc().settings.api_docs:
        return _error(404, "not_found", "API docs are turned off (API_DOCS=0)")
    return send_from_directory(Path(__file__).parent / "static", "docs.html")


@bp.get("/health")
def health():
    return jsonify(_envelope({"status": "ok"}, "Service is healthy.")), 200


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
        # Which surfaces are currently unmoderated. An operator should be able to read that
        # from outside rather than infer it from what gets published.
        "moderation_disabled_for": svc.levels.disabled_features(),
        # Whether the submission endpoints are behind the bearer token. Reported because the
        # service fails OPEN when SERVICE_TOKEN is unset, and a deploy that forgot it looks
        # exactly like a deploy that did not - from the outside, until someone finds it.
        "service_auth": "open" if svc.settings.auth_disabled or not svc.settings.service_token
                        else "enforced",
        "admin_auth": "open" if svc.settings.auth_disabled else "enforced",
    }
    if svc.settings.mode == "sync":
        body.update(model_ready=svc.models.ready, model_version=svc.models.version,
                    model_backend=svc.settings.model_backend, model_error=svc.models.last_error)
        if not svc.models.ready:
            return jsonify(_envelope({"status": "not_ready", **body},
                                     "Service models not ready.", success=False)), 503
    return jsonify(_envelope({"status": "ready", **body}, "Service is ready.")), 200


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
    # A SURFACE THAT DOES NOT EXIST IS A 404, not a 400: the difference is "there is no such
    # page" against "that value is wrong", and an admin who typed `articles` needs the first.
    # Checked before set_feature so the two cases keep distinct codes.
    levels = _svc().levels
    if content_type not in levels.known_surfaces():
        return _error(404, "no_such_surface",
                      f"no such surface {content_type!r}; expected one of "
                      f"{', '.join(sorted(levels.known_surfaces()))}")
    try:
        policy = levels.set_feature(
            content_type, enabled=body.get("enabled"), level=body.get("level"),
            reject_min=body.get("reject_min"), allow_max=body.get("allow_max"))
    except (ValueError, TypeError) as exc:
        return _error(400, "invalid_request", str(exc))
    from app.admin_auth import remember
    remember(content_type, body)           # saved, so a restart keeps it
    log.warning("moderation level changed by admin: %s -> %s", content_type, policy.as_dict())
    return jsonify({"content_type": content_type, **policy.as_dict()}), 200


@bp.delete("/v1/admin/moderation-levels/<content_type>")
def clear_moderation_level(content_type: str):
    """Drop one surface's override so it follows the default again.

    Not the same as setting it back to whatever the default happens to be: that freezes a copy
    which stops tracking a later change to the default. An admin undoing an experiment means
    "follow the default", and until this existed the only way to express it was to guess the
    default's current value and type it in.
    """
    denied = _require_admin()
    if denied:
        return denied
    try:
        cleared = _svc().levels.clear_feature(content_type)
    except ValueError as exc:
        return _error(404, "no_such_surface", str(exc))
    from app.admin_auth import forget
    forget(content_type)                   # so a restart does not bring it back
    log.warning("moderation level cleared by admin: %s", content_type)
    return jsonify({"content_type": content_type, "cleared": True,
                    "had_override": cleared,
                    **_svc().levels.for_type(content_type).as_dict()}), 200


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

