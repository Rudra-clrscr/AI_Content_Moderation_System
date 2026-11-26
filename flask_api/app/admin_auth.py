"""Who may use the admin endpoints, and the saved moderation settings.

THERE IS NO LOGIN HERE ANY MORE. Admins sign in on the BONC .NET side; this service only
checks what they bring (see check_admin):

    Authorization: Bearer <token>   the admin's BONC sign-in, verified by the .NET endpoint
                                    at BONC_AUTH_VERIFY_URL (BONC_AUTH_STYLE says which kind)
    X-Admin-Token: <ADMIN_TOKEN>    for scripts and ops, when ADMIN_TOKEN is set
    AUTH_DISABLED=1                 no check at all

The email/password accounts, sessions and emailed reset codes that used to live here were
removed once the .NET sign-in became the way in.

Routes (all JSON):
    GET  /v1/admin/auth/me               who the request is authenticated as
    GET  /v1/admin/moderation-settings
    PUT  /v1/admin/moderation-settings   {enabled, level}  applied to EVERY content type
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
import time
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request

from app.admin_store import AdminStore
from app.pipeline import ContentType

log = logging.getLogger("app.admin")
admin_bp = Blueprint("admin_auth", __name__)

# A log export is built whole in memory before it is sent, so this is the cap that keeps an
# operator who has cleared nothing from discovering the limit as a dead server. At the widest
# columns a row is a few hundred bytes, so this is tens of MB in the worst case; the file says
# when it bit, and the filters on the screen are how you get the rest.
MAX_EXPORT_ROWS = 50_000

# One value per content type the pipeline knows, so no surface is forgotten.
ALL_SURFACES = [c.value for c in ContentType]


class Admin:
    """Per-app state: the store, and the lock that serialises saved-settings writes."""

    def __init__(self, store: AdminStore):
        self.store = store
        self.settings_lock = threading.Lock()


# ---- setup ---------------------------------------------------------------------------

def default_db_path() -> str:
    return os.environ.get("ADMIN_DB_PATH") or str(Path(__file__).resolve().parent.parent / "data" / "admin.db")


def init_admin(app, store: AdminStore | None = None) -> Admin:
    admin = Admin(store or AdminStore(default_db_path()))
    app.extensions["admin"] = admin
    settings = app.extensions["moderation"].settings
    app.extensions["bonc_auth"] = bonc_verifier(settings)
    app.register_blueprint(admin_bp)
    if settings.admin_cors_origins:
        app.after_request(_admin_cors)
        log.info("admin endpoints accept browser calls from %s",
                 ", ".join(settings.admin_cors_origins))
    return admin


# ---- CORS for the BONC admin frontend --------------------------------------------------
# Only /v1/admin/*, only the listed origins, and no credentials: the frontend sends the BONC
# token in Authorization, so there is no cookie for a browser to attach on its own. Flask
# answers the preflight OPTIONS itself (the views never run, so check_admin is not asked),
# and this hook adds the headers to that answer and to every real response - errors included,
# or the frontend could not read why a call was refused.
_CORS_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
_CORS_HEADERS = "Authorization, Content-Type"
_CORS_EXPOSE = "Content-Disposition, X-Export-Rows, X-Export-Total, Retry-After"


def _admin_cors(resp):
    if not request.path.startswith("/v1/admin/"):
        return resp
    origin = (request.headers.get("Origin") or "").rstrip("/").lower()
    resp.vary.add("Origin")
    if origin and origin in current_app.extensions["moderation"].settings.admin_cors_origins:
        resp.headers["Access-Control-Allow-Origin"] = request.headers["Origin"].rstrip("/")
        resp.headers["Access-Control-Allow-Methods"] = _CORS_METHODS
        resp.headers["Access-Control-Allow-Headers"] = _CORS_HEADERS
        resp.headers["Access-Control-Expose-Headers"] = _CORS_EXPOSE
        resp.headers["Access-Control-Max-Age"] = "600"
    return resp


def restore_levels(levels, store: AdminStore) -> None:
    """Re-apply what the admin saved, so a restart changes nothing."""
    for ctype, rec in store.load_settings().get("features", {}).items():
        try:
            levels.set_feature(ctype, enabled=rec.get("enabled"), level=rec.get("level"),
                               reject_min=rec.get("reject_min"), allow_max=rec.get("allow_max"))
        except (ValueError, TypeError):
            log.error("saved moderation setting for %s is no longer valid; skipped", ctype)


# A named level and a raw rating are two ways of saying the same thing, and set_feature
# treats a raw one as replacing the name ("a number was given; it is no longer named").
# The saved record has to say the same, or the two disagree after a restart.
_SUPERSEDES = {"level": ("reject_min", "allow_max"),
               "reject_min": ("level",), "allow_max": ("level",)}


def remember(ctype: str, body: dict) -> None:
    """Save the fields an admin just changed for one surface.

    Merging without clearing kept both ways of saying it. A surface set to 0.62 and then to
    `strict` was stored carrying the level AND the old number; restore_levels replays them
    together, set_feature applies the level first and the number second, and the restart came
    up at 0.62 - the admin's last choice silently undone, with nothing on screen to say so.
    """
    a: Admin = current_app.extensions["admin"]
    with a.settings_lock:
        data = a.store.load_settings()
        rec = data.setdefault("features", {}).setdefault(ctype, {})
        incoming = {k: v for k, v in body.items() if v is not None}
        for key in incoming:
            for dead in _SUPERSEDES.get(key, ()):
                rec.pop(dead, None)
        rec.update(incoming)
        a.store.save_settings(data)


def forget(ctype: str) -> None:
    """Remove the saved override for one surface so it reverts to default."""
    a: Admin = current_app.extensions["admin"]
    with a.settings_lock:
        data = a.store.load_settings()
        if "features" in data and ctype in data["features"]:
            data["features"].pop(ctype, None)
            a.store.save_settings(data)


# ---- who is calling -------------------------------------------------------------------

def _err(status: int, code: str, message: str, **extra):
    # The company envelope: three keys, with the machine-readable code in `Data`. The flat
    # `{"error": "<code>", "message": ...}` pair these routes used to send is gone, and so is
    # the long-standing split where `error` was a bare string here and an object in
    # `api._error` - there is one shape now, and `Data.code` is it.
    resp = jsonify({
        "Success": False,
        "Message": message,
        "Data": {"code": code, "message": message, **extra},
    })
    resp.status_code = status
    return resp


BONC_AUTH_STYLES = ("verify-admin", "user-detail")


def bonc_verifier(settings):
    """The .NET admin-token verifier, or None when BONC_AUTH_VERIFY_URL is unset.

    BONC_AUTH_STYLE picks which .NET endpoint the URL is:
        verify-admin  a purpose-built verifier answering {adminId, role, sessionId} / 401 / 403
                      (the bonc-auth package's contract)
        user-detail   the existing GET /api/userService/getUserDetail; this service reads the
                      roles itself (UserDetailAuth)

    Built once at startup, so a bad URL (plain http to a remote host, say) stops the server
    with the reason instead of failing every admin request later. A missing package stops it
    too: a deployment configured to trust the .NET sign-in must not quietly come up
    without it.
    """
    url = getattr(settings, "bonc_auth_verify_url", None)
    if not url:
        return None
    style = getattr(settings, "bonc_auth_style", "verify-admin")
    if style not in BONC_AUTH_STYLES:
        raise ValueError(f"BONC_AUTH_STYLE must be one of {', '.join(BONC_AUTH_STYLES)}, "
                         f"not {style!r}")
    try:
        from bonc_auth import BoncAuth
    except ImportError as exc:
        raise RuntimeError("BONC_AUTH_VERIFY_URL is set but the bonc-auth package is not "
                           "installed: pip install -r requirements.txt") from exc
    if style == "user-detail":
        roles = tuple(getattr(settings, "bonc_auth_admin_roles", ()) or ())
        if not roles:
            raise ValueError("BONC_AUTH_STYLE=user-detail needs BONC_AUTH_ADMIN_ROLES "
                             "(e.g. Admin,SuperAdmin): with none, nobody could ever get in")
        verifier = UserDetailAuth(url, admin_roles=roles, timeout=settings.bonc_auth_timeout)
    else:
        verifier = BoncAuth(url, method=settings.bonc_auth_method,
                            timeout=settings.bonc_auth_timeout)
    ttl = float(getattr(settings, "bonc_auth_cache_seconds", 0) or 0)
    log.info("admin endpoints accept BONC bearer tokens, verified at %s (%s)%s", url, style,
             f", accepted tokens trusted for {ttl:g} s" if ttl else "")
    return CachedVerifier(verifier, ttl) if ttl > 0 else verifier


def _jwt_claims(token: str) -> dict:
    """The payload of a JWT, NOT signature-checked - .NET does that on the same request.

    Read only to learn which user the token claims to be, so that user's details can be
    asked for and the answer compared with it. Nothing here is trusted on its own.
    """
    import base64
    import json
    parts = token.split(".")
    if len(parts) != 3 or len(token) > 8192:
        return {}
    try:
        raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        claims = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _role_names(detail: dict) -> list[str]:
    """Every role the .NET user record names. `UserRoles` is a string ("Basic", or several
    joined with commas); `RoleNames`/`RoleName` are read too in case a build fills those."""
    import re
    out: list[str] = []
    for key in ("UserRoles", "RoleNames", "RoleName"):
        v = detail.get(key)
        items = v if isinstance(v, list) else [v]
        for item in items:
            if isinstance(item, str):
                out += [r.strip() for r in re.split(r"[,;|]", item) if r.strip()]
    return out


class UserDetailAuth:
    """Admin check built on the .NET API that exists today:

        GET /api/userService/getUserDetail?userRowId=<the token's UserRowId>
        Authorization: Bearer <token>

    .NET checks the token (401 when it is missing, invalid or expired) and returns the user
    record, wrapped in its {Success, Data} envelope. This service then decides: an active
    user whose roles include one of `admin_roles` is an admin; anyone else signed in is a 403.

    The user asked about is the one the token itself names, and the answer must be that same
    user - so a .NET quirk that returned some other record (an admin's, say) is a 503, never
    someone else's access. Same exceptions as bonc-auth's BoncAuth, so _bonc_admin and
    CachedVerifier treat the two alike.
    """

    def __init__(self, verify_url: str, *, admin_roles, timeout: float = 5.0):
        from bonc_auth import BoncAuth
        BoncAuth(verify_url, timeout=timeout)       # same URL rules: absolute, HTTPS off-box
        self.verify_url, self.timeout = verify_url, timeout
        self.admin_roles = {r.strip().lower(): r.strip() for r in admin_roles if r.strip()}

    def verify(self, token: str) -> dict:
        import httpx
        from bonc_auth import AuthServiceUnavailable, Forbidden, InvalidToken

        if not token:
            raise InvalidToken("Bearer token is empty.")
        user_id = str(_jwt_claims(token).get("UserRowId") or "").strip()
        if not user_id or len(user_id) > 128:
            raise InvalidToken("Not a BONC sign-in token (no UserRowId claim).")
        try:
            resp = httpx.get(self.verify_url, params={"userRowId": user_id},
                             headers={"Accept": "application/json",
                                      "Authorization": f"Bearer {token}"},
                             timeout=self.timeout, follow_redirects=False)
        except httpx.RequestError as exc:
            raise AuthServiceUnavailable("Could not reach BONC authentication service.") from exc
        if resp.status_code == 401:
            raise InvalidToken("Token is invalid, expired, or the session is no longer active.")
        if resp.status_code == 403:
            raise Forbidden("Admin access is required.")
        if resp.status_code != 200:
            raise AuthServiceUnavailable(f"BONC getUserDetail returned HTTP {resp.status_code}.")
        try:
            body = resp.json()
        except ValueError as exc:
            raise AuthServiceUnavailable("BONC returned an invalid JSON response.") from exc
        if not isinstance(body, dict):
            raise AuthServiceUnavailable("BONC getUserDetail did not return a JSON object.")
        if body.get("Success") is False:
            raise AuthServiceUnavailable(
                f"BONC getUserDetail said Success=false: {str(body.get('Message'))[:200]}")
        detail = body["Data"] if isinstance(body.get("Data"), dict) else body
        if str(detail.get("UserRowId") or "").strip().lower() != user_id.lower():
            raise AuthServiceUnavailable(
                "BONC getUserDetail answered for a different user than the token names.")
        if detail.get("IsActive") is False:
            raise InvalidToken("This BONC account is deactivated.")
        roles = [self.admin_roles[r.lower()] for r in _role_names(detail)
                 if r.lower() in self.admin_roles]
        if not roles:
            raise Forbidden("Admin access is required.")
        return {"adminId": str(detail["UserRowId"]), "role": roles[0], "sessionId": None}


class CachedVerifier:
    """Remembers tokens .NET accepted, for `ttl` seconds, so a page of calls costs one check.

    ONLY ACCEPTANCES ARE KEPT. A refusal (401/403) or a failure is asked again every time:
    caching a 503 would hold an outage open after .NET recovers, and caching a 401 would lock
    out an admin who has just signed in again with the same token. Keys are SHA-256 of the
    token, so the raw token is never held. Per process - each gunicorn worker keeps its own.
    """

    MAX_ENTRIES = 2000

    def __init__(self, inner, ttl: float, clock=time.monotonic):
        self.inner, self.ttl, self._clock = inner, ttl, clock
        self._seen: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def verify(self, token: str) -> dict:
        key = hashlib.sha256(token.encode()).hexdigest()
        now = self._clock()
        with self._lock:
            hit = self._seen.get(key)
            if hit and hit[0] > now:
                return dict(hit[1])
        who = self.inner.verify(token)            # raises on refusal: nothing is stored
        with self._lock:
            if len(self._seen) >= self.MAX_ENTRIES:
                self._seen = {k: v for k, v in self._seen.items() if v[0] > now}
                if len(self._seen) >= self.MAX_ENTRIES:
                    self._seen.clear()
            self._seen[key] = (now + self.ttl, dict(who))
        return dict(who)


def _bonc_admin(verifier):
    """Verify the request's bearer token with .NET. None if it is a live admin session.

    On success the verified identity is in `g.admin` ({"adminId", "role", "sessionId"}).
    Fails closed: an unreachable or confused verifier is a 503, never a pass.
    """
    from bonc_auth import AuthServiceUnavailable, Forbidden, InvalidToken
    from bonc_auth.client import extract_bearer_token
    try:
        g.admin = verifier.verify(extract_bearer_token(request.headers.get("Authorization")))
    except InvalidToken:
        return _err(401, "unauthorized",
                    "Your BONC admin session is invalid or has ended. Sign in again.")
    except Forbidden:
        return _err(403, "not_admin", "This BONC account does not have admin access.")
    except AuthServiceUnavailable as exc:
        log.error("BONC admin verification unavailable: %s", exc)
        return _err(503, "auth_unavailable",
                    "Admin sign-in could not be checked just now. Try again shortly.")
    return None


def _actor() -> str:
    """Who is acting, for `created_by` columns: the .NET admin id, or "" for a script."""
    who = getattr(g, "admin", None)
    return str(who.get("adminId", "")) if isinstance(who, dict) else ""


def check_admin():
    """None if the caller may use admin endpoints, otherwise a ready-made error response.

    Accepted: a BONC .NET admin token (Authorization: Bearer, when BONC_AUTH_VERIFY_URL is
    set), or for scripts/ops the X-Admin-Token header (when ADMIN_TOKEN is set) - or anyone
    at all when AUTH_DISABLED is on (see Settings.auth_disabled).

    A request that brings a bearer token is judged on that token alone: a rejected one is
    refused, not retried against X-Admin-Token, so the .NET answer is the answer. Neither
    credential carries any risk of CSRF - a browser never attaches either on its own.
    """
    settings = current_app.extensions["moderation"].settings
    if settings.auth_disabled:
        return None
    verifier = current_app.extensions.get("bonc_auth")
    if verifier is not None and request.headers.get("Authorization", "").strip():
        return _bonc_admin(verifier)
    token = settings.admin_token
    if token and hmac.compare_digest(request.headers.get("X-Admin-Token", ""), token):
        return None
    if verifier is None and not token:
        return _err(404, "not_found", "admin endpoints are disabled: set BONC_AUTH_VERIFY_URL "
                                      "(the .NET admin sign-in) or ADMIN_TOKEN")
    return _err(401, "unauthorized", "Sign in to BONC as an admin to use this.")


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@admin_bp.get("/v1/admin/auth/me")
def me():
    """Who this request is authenticated as. The quickest end-to-end check for the .NET side."""
    denied = check_admin()
    if denied:
        return denied
    who = getattr(g, "admin", None)
    if isinstance(who, dict):
        return jsonify({"admin_id": who.get("adminId"), "role": who.get("role"),
                        "session_id": who.get("sessionId"), "via": "bonc"}), 200
    via = "open" if current_app.extensions["moderation"].settings.auth_disabled else "admin_token"
    return jsonify({"admin_id": None, "role": None, "session_id": None, "via": via}), 200


# ---- moderation settings (one setting, every content type) -----------------------------

def _settings_view(levels) -> dict:
    pol = {t: levels.for_type(t) for t in ALL_SURFACES}
    enabled = [p.enabled for p in pol.values()]
    shown = [p.level for p in pol.values() if p.level]
    surfaces = {}
    for t in ALL_SURFACES:
        p = levels.for_type(t)
        d = p.as_dict()
        if not p.enabled and p.level:
            d["level"] = p.level
        surfaces[t] = d
    return {
        "enabled": all(enabled),
        "level": shown[0] if shown and len(set(shown)) == 1 and len(shown) == len(pol) else None,
        "mixed": len(set(enabled)) > 1 or len(set(p.level for p in pol.values())) > 1,
        "levels": sorted(levels.as_dict()["levels"]),
        "surfaces": surfaces,
    }


@admin_bp.get("/v1/admin/moderation-settings")
def get_settings():
    denied = check_admin()
    if denied:
        return denied
    return jsonify(_settings_view(current_app.extensions["moderation"].levels)), 200


@admin_bp.put("/v1/admin/moderation-settings")
def put_settings():
    denied = check_admin()
    if denied:
        return denied
    data = _body()
    levels = current_app.extensions["moderation"].levels
    if set(data) - {"enabled", "level"} or not isinstance(data.get("enabled"), bool) \
            or data.get("level") not in levels.as_dict()["levels"]:
        return _err(400, "invalid_request",
                    f"send {{enabled: true|false, level: one of {sorted(levels.as_dict()['levels'])}}}")
    a: Admin = current_app.extensions["admin"]
    with a.settings_lock:                       # two admins saving at once cannot interleave
        previous = a.store.load_settings()
        for t in ALL_SURFACES:
            levels.set_feature(t, enabled=data["enabled"], level=data["level"])
        try:
            a.store.save_settings({"features": {t: {"enabled": data["enabled"], "level": data["level"]}
                                                for t in ALL_SURFACES}})
        except Exception:
            log.exception("could not save moderation settings; rolling back")
            for t in ALL_SURFACES:               # keep memory and disk telling the same story
                rec = previous.get("features", {}).get(t, {})
                levels.set_feature(t, enabled=rec.get("enabled", True), level=rec.get("level"))
            return _err(500, "save_failed", "Could not save the setting, so nothing was changed.")
    log.warning("moderation set by admin: enabled=%s level=%s for %s", data["enabled"], data["level"],
                ", ".join(ALL_SURFACES))
    return jsonify(_settings_view(levels)), 200


# ---- moderation logs & audit trail endpoints ------------------------------------------

def _when(raw) -> float | None:
    """A time bound from the query string: epoch seconds, or an ISO date / date-time.

    A bare date is the start of that day in UTC; the screen sends full ISO times built from
    the admin's own clock, so a range picked in IST means IST.
    """
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        pass
    from datetime import datetime, timezone
    try:
        d = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def _log_filters() -> dict:
    """The filters the log screen sends, shared by the list and the export."""
    return {"decision": request.args.get("decision"),
            "content_type": request.args.get("content_type"),
            "severity": request.args.get("severity"),
            "query": request.args.get("q"),
            "modality": request.args.get("modality"),
            "since": _when(request.args.get("since")),
            "until": _when(request.args.get("until")),
            "user": request.args.get("user")}


@admin_bp.get("/v1/admin/moderation-logs")
def get_moderation_logs():
    denied = check_admin()
    if denied:
        return denied
    a: Admin = current_app.extensions["admin"]
    try:
        limit = min(200, max(1, int(request.args.get("limit", 50))))
    except (ValueError, TypeError):
        limit = 50
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (ValueError, TypeError):
        offset = 0

    try:
        offender_min = max(1, int(request.args.get("offender_min", 3)))
    except (ValueError, TypeError):
        offender_min = 3
    try:
        offender_rate = min(1.0, max(0.0, float(request.args.get("offender_rate", 0.5))))
    except (ValueError, TypeError):
        offender_rate = 0.5

    logs_data = a.store.get_moderation_logs(
        **_log_filters(), limit=limit, offset=offset,
        offender_min=offender_min, offender_rate=offender_rate)
    blocked = a.store.blocked_members()
    logs_data["blocked_members"] = sorted(blocked)
    for u in logs_data["repeat_offenders"]["users"]:
        u["blocked"] = u["user_id"] in blocked
    return jsonify(logs_data), 200


def _offender_rule() -> tuple[int, float]:
    """The two-part offender rule from the query string: `min_rejects` and `min_rate`.

    `offender_min` / `offender_rate` are accepted too - the names the log list uses - so one
    link can carry the same rule to either endpoint. Anything malformed falls back to 3 / 0.5.
    """
    def pick(*names):
        for n in names:
            if request.args.get(n) not in (None, ""):
                return request.args.get(n)
        return None
    try:
        n = max(1, int(pick("min_rejects", "offender_min") or 3))
    except (ValueError, TypeError):
        n = 3
    try:
        r = min(1.0, max(0.0, float(pick("min_rate", "offender_rate") or 0.5)))
    except (ValueError, TypeError):
        r = 0.5
    return n, r


def _offender_window() -> tuple[float | None, float | None]:
    """`since`/`until` (epoch or ISO), or `hours` back from now as a shorthand."""
    since, until = _when(request.args.get("since")), _when(request.args.get("until"))
    try:
        hours = float(request.args.get("hours")) if request.args.get("hours") else None
    except ValueError:
        hours = None
    if since is None and hours and hours > 0:
        since = time.time() - hours * 3600
    return since, until


@admin_bp.get("/v1/admin/moderation-logs/repeat-offenders")
def list_repeat_offenders():
    """Members whose submissions are refused too often in a window.

    The same list the log screen highlights, on its own, for a caller that wants only the
    members - the portal, say, deciding whom to send for manual review.
    """
    denied = check_admin()
    if denied:
        return denied
    a: Admin = current_app.extensions["admin"]
    n, r = _offender_rule()
    since, until = _offender_window()
    try:
        limit = min(500, max(1, int(request.args.get("limit", 50))))
    except (ValueError, TypeError):
        limit = 50
    res = a.store.repeat_offenders(since=since, until=until, min_rejects=n, min_rate=r,
                                   content_type=request.args.get("content_type") or None,
                                   modality=request.args.get("modality") or None,
                                   limit=limit)
    res["window"] = {"since": since, "until": until}
    blocked = a.store.blocked_members()
    for u in res["users"]:
        u["blocked"] = u["user_id"] in blocked
    return jsonify(res), 200


@admin_bp.get("/v1/admin/moderation-logs/repeat-offenders/<path:user_id>")
def get_member_record(user_id: str):
    """One member's record and whether it makes them a repeat offender - yes or no, with
    the numbers behind it."""
    denied = check_admin()
    if denied:
        return denied
    a: Admin = current_app.extensions["admin"]
    user_id = user_id.strip()[:128]
    if not user_id or user_id == "anonymous":
        return _err(400, "bad_user", "Give a member id; anonymous submissions cannot be counted.")
    n, r = _offender_rule()
    since, until = _offender_window()
    res = a.store.member_record(user_id, since=since, until=until, min_rejects=n, min_rate=r)
    res["window"] = {"since": since, "until": until}
    res["blocked"] = user_id in a.store.blocked_members()
    return jsonify(res), 200


@admin_bp.get("/v1/admin/moderation-logs/export")
def export_moderation_logs():
    """Download the moderation log as text, Word or Excel.

    Takes the SAME filter arguments as the list endpoint and applies them through the same
    builder, because the button sits on a filtered screen and "export" there can only sensibly
    mean "what I am looking at". It is not paginated, though: the screen shows fifteen rows and
    nobody wants fifteen rows in a spreadsheet, so this walks the whole matching set up to a
    cap and the file records both numbers about itself.
    """
    denied = check_admin()
    if denied:
        return denied
    from app.log_export import MEDIA_TYPES, render

    fmt = (request.args.get("format") or "txt").strip().lower()
    if fmt in ("excel", "xls"):
        fmt = "xlsx"
    if fmt in ("word", "docx"):
        fmt = "doc"
    if fmt not in MEDIA_TYPES:
        return _err(400, "bad_format",
                    f"format must be one of: {', '.join(sorted(MEDIA_TYPES))}.")

    a: Admin = current_app.extensions["admin"]
    filters = _log_filters()
    try:
        cap = min(MAX_EXPORT_ROWS, max(1, int(request.args.get("max_rows", MAX_EXPORT_ROWS))))
    except (TypeError, ValueError):
        cap = MAX_EXPORT_ROWS

    total = a.store.count_moderation_logs(**filters)
    rows = a.store.export_moderation_logs(limit=cap, **filters)

    try:
        data, media_type, name = render(
            fmt, rows, a.store.EXPORT_COLUMNS, total,
            truncated=total > len(rows),
            # Reported as the admin set them on screen, not as the query names them.
            filters={"decision": filters["decision"], "surface": filters["content_type"],
                     "severity": filters["severity"], "search": filters["query"],
                     "model": filters["modality"], "user": filters["user"],
                     "from": request.args.get("since"), "to": request.args.get("until")})
    except ImportError:
        # Only the workbook has a dependency, and it is declared. Say which format is missing
        # rather than failing the button, because the other two still work.
        log.exception("the Excel export could not be built")
        return _err(503, "export_unavailable",
                    "Excel export needs the openpyxl package on the server "
                    "(pip install openpyxl). Text and Word exports still work.")

    resp = current_app.response_class(data, mimetype=media_type)
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    resp.headers["Content-Length"] = str(len(data))
    # An audit export is a point-in-time copy of a table that is still being written to.
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Export-Rows"] = str(len(rows))
    resp.headers["X-Export-Total"] = str(total)
    return resp


@admin_bp.get("/v1/admin/moderation-logs/<request_id>")
def get_moderation_log_detail(request_id: str):
    denied = check_admin()
    if denied:
        return denied
    a: Admin = current_app.extensions["admin"]
    item = a.store.get_moderation_log_by_request_id(request_id)
    if not item:
        return _err(404, "not_found", f"No moderation log found for request {request_id}")
    return jsonify(item), 200


# ---- an admin overturning a decision -----------------------------------------------------
# The .NET side owns the content and does the publishing; what this service owns is the
# record. So an override here changes the audit log - the verdict the screens, the counts and
# the repeat-offender rule see - and answers with the verdict in the platform's own words
# (`Action`), for .NET to act on. It does not publish anything itself.

# The platform's words (and the pipeline's own) for the two verdicts an admin can give.
_OVERRIDE_WORDS = {"approved": "allow", "approve": "allow", "allow": "allow",
                   "allowed": "allow", "accept": "allow",
                   "reject": "reject", "rejected": "reject"}
_PLATFORM_WORD = {"allow": "approved", "reject": "reject", "revise": "revise"}


def _override_view(row: dict) -> dict:
    said = str(row.get("decision") or "").lower()
    model = row.get("original_decision")
    action = _PLATFORM_WORD.get(said, said)
    return {
        "request_id": row.get("request_id"),
        "content_type": row.get("content_type"),
        "content_id": row.get("content_id"),
        "user_id": row.get("user_id"),
        "Action": action,            # what .NET acts on: "approved" | "reject"
        "decision": action,
        "overridden": bool(model),
        "model_decision": _PLATFORM_WORD.get(str(model or said).lower(), model or said),
        "overridden_by": row.get("overridden_by") if model else None,
        "overridden_at": row.get("overridden_at") if model else None,
        "reason": row.get("override_reason") if model else None,
    }


def _apply_override(request_id: str):
    a: Admin = current_app.extensions["admin"]
    body = _body()
    raw = str(body.get("decision") or body.get("Action") or "").strip().lower()
    decision = _OVERRIDE_WORDS.get(raw)
    if decision is None:
        return _err(400, "bad_decision",
                    "Send decision: 'approved' (publish it) or 'reject' (keep it refused).")
    reason = str(body.get("reason") or body.get("Note") or "").strip()[:1000]
    row = a.store.override_decision(request_id, decision, by=_actor(), reason=reason)
    if row is None:
        return _err(404, "not_found", f"No moderation decision was recorded for {request_id}.")
    view = _override_view(row)
    log.warning("admin %s overrode %s (%s %s): model said %s, now %s%s",
                _actor() or "(admin token)", request_id, row.get("content_type"),
                row.get("content_id") or "-", view["model_decision"], view["Action"],
                f" - {reason}" if reason else "")
    return jsonify(view), 200


@admin_bp.post("/v1/admin/moderation-logs/<request_id>/override")
def override_decision(request_id: str):
    """Overturn one decision: {"decision": "approved" | "reject", "reason": "..."}."""
    denied = check_admin()
    if denied:
        return denied
    return _apply_override(request_id)


@admin_bp.post("/v1/admin/moderation-logs/override")
def override_decision_by_content():
    """The same, found by the platform's own ids when it did not keep the request_id:
    {"content_type": "article", "content_id": "123", "decision": ..., "reason": ...}.
    Applies to the newest decision recorded for that content."""
    denied = check_admin()
    if denied:
        return denied
    body = _body()
    ctype = str(body.get("content_type") or "").strip()
    cid = str(body.get("content_id") or "").strip()
    if not ctype or not cid:
        return _err(400, "missing_content",
                    "Give content_type and content_id, or use "
                    "/v1/admin/moderation-logs/<request_id>/override.")
    a: Admin = current_app.extensions["admin"]
    request_id = a.store.find_request_id(ctype, cid)
    if request_id is None:
        return _err(404, "not_found", f"No moderation decision was recorded for {ctype} {cid}.")
    return _apply_override(request_id)


@admin_bp.delete("/v1/admin/moderation-logs/<request_id>/override")
def clear_override(request_id: str):
    """Undo an override: the model's verdict stands again."""
    denied = check_admin()
    if denied:
        return denied
    a: Admin = current_app.extensions["admin"]
    row = a.store.clear_override(request_id)
    if row is None:
        return _err(404, "not_found", f"No moderation decision was recorded for {request_id}.")
    log.warning("admin %s cleared the override on %s", _actor() or "(admin token)", request_id)
    return jsonify(_override_view(row)), 200


# ---- blocked addresses ----------------------------------------------------------------
# WHAT THIS IS AND IS NOT. Blocking an address stops casual repetition from one connection -
# the same person posting the same thing eight times - and that is worth having. It is not an
# identity control: a mobile connection changes address by switching to cellular, and a VPN
# changes it by clicking. That is also why it cuts the other way, and why the scope matters:
# a /24 can be one household or one office behind a single NAT, so refusing a network can
# refuse people who did nothing. The screen says which scope is being used, and the log keeps
# the reason, because the person undoing this months later will not be the one who did it.

@admin_bp.get("/v1/admin/blocked-clients")
def list_blocked_clients():
    denied = check_admin()
    if denied:
        return denied
    a: Admin = current_app.extensions["admin"]
    return jsonify({"blocked": a.store.list_blocked_clients()}), 200


@admin_bp.post("/v1/admin/blocked-clients")
def add_blocked_client():
    denied = check_admin()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    value = str(body.get("value", "")).strip()
    scope = str(body.get("scope", "ip")).strip().lower()
    if not value:
        return _err(400, "missing_value", "Give the address or member id to block.")
    if scope not in ("ip", "network", "member"):
        return _err(400, "bad_scope", "scope must be 'ip', 'network' or 'member'.")

    a: Admin = current_app.extensions["admin"]
    who = _actor()
    reason = str(body.get("reason", "")).strip()
    if scope == "member":
        # A member id is the portal's own, and this service has no user table to check it
        # against - so only the shapes that can never name one member are refused.
        if value.lower() == "anonymous" or len(value) > 128:
            return _err(400, "bad_member",
                        "Give a member id; anonymous submissions cannot be blocked by member.")
        row = a.store.block_client(value, "member", reason=reason,
                                   created_by=who)
        log.info("admin blocked member %s", value)
        return jsonify({"blocked": row, "message": f"Blocked member {value}."}), 201

    # Validated here so a typo becomes a message rather than a row that silently blocks
    # nothing and is never noticed. A network is stored in the same normal form the resolver
    # produces, or the two would never compare equal.
    from app import client_address
    if scope == "ip":
        if not client_address.network_of(value):
            return _err(400, "bad_address", f"{value!r} is not an IP address.")
    else:
        net = client_address.network_of(value.split("/")[0])
        if not net:
            return _err(400, "bad_address", f"{value!r} is not an address or network.")
        value = net

    # REFUSING YOUR OWN CONNECTION IS A LOCKOUT, and the screen that would undo it is behind
    # the block you just made. Caught here rather than explained in a tooltip nobody reads.
    me = client_address.from_request(
        request, current_app.extensions["moderation"].settings.trust_proxy_hops)
    if value in (me.get("ip"), me.get("network")):
        return _err(409, "would_block_self",
                    f"That is your own connection ({me.get('ip')}). Blocking it would stop "
                    "your own submissions; the dashboard would still work, but check the "
                    "address before going ahead.")

    # A script on X-Admin-Token has no admin id, so `who` is blank - the block still stands.
    row = a.store.block_client(value, scope, reason=reason,
                               created_by=who)
    log.info("admin blocked %s %s", scope, value)
    return jsonify({"blocked": row,
                    "message": f"Blocked {scope} {value}."}), 201


@admin_bp.delete("/v1/admin/blocked-clients/<path:value>")
def remove_blocked_client(value: str):
    denied = check_admin()
    if denied:
        return denied
    a: Admin = current_app.extensions["admin"]
    n = a.store.unblock_client(value, request.args.get("scope"))
    if not n:
        return _err(404, "not_blocked", f"{value!r} is not blocked.")
    log.info("admin unblocked %s", value)
    return jsonify({"unblocked": True, "count": n, "message": f"Unblocked {value}."}), 200


@admin_bp.delete("/v1/admin/moderation-logs")
def clear_moderation_logs():
    denied = check_admin()
    if denied:
        return denied
    a: Admin = current_app.extensions["admin"]
    count = a.store.clear_moderation_logs()
    return jsonify({"cleared": True, "count": count}), 200
