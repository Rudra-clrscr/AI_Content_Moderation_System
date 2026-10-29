"""Admin login, password recovery by emailed code, and the saved moderation settings.

Routes (all JSON):
    POST /v1/admin/auth/login            {email, password}
    POST /v1/admin/auth/logout
    GET  /v1/admin/auth/me
    POST /v1/admin/auth/password/forgot  {email}                          always the same reply
    POST /v1/admin/auth/password/verify  {email, code}                    -> {reset_token}
    POST /v1/admin/auth/password/reset   {email, reset_token, new_password}
    GET  /v1/admin/moderation-settings
    PUT  /v1/admin/moderation-settings   {enabled, level}  applied to EVERY content type

Nothing here registers an administrator over HTTP. Admins are created from the command line
(python -m app.admin_cli create-admin), so being able to reach the site is never enough.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from app.admin_store import AdminStore
from app.pipeline import ContentType

log = logging.getLogger("app.admin")
admin_bp = Blueprint("admin_auth", __name__)

OTP_TTL = 10 * 60            # a code works for 10 minutes
TOKEN_TTL = 10 * 60          # after the code is accepted, the reset form is good for 10 minutes
OTP_MAX_ATTEMPTS = 5         # wrong guesses allowed per code
RESEND_COOLDOWN = 60         # seconds between codes for one email
SESSION_IDLE = 30 * 60       # log out after 30 minutes of no activity
MIN_PASSWORD = 10
CSRF_HEADER = ("X-Requested-With", "bonc-admin")
# A log export is built whole in memory before it is sent, so this is the cap that keeps an
# operator who has cleared nothing from discovering the limit as a dead server. At the widest
# columns a row is a few hundred bytes, so this is tens of MB in the worst case; the file says
# when it bit, and the filters on the screen are how you get the rest.
MAX_EXPORT_ROWS = 50_000

# One value per content type the pipeline knows, so no surface is forgotten.
ALL_SURFACES = [c.value for c in ContentType]

_DUMMY_HASH = generate_password_hash(secrets.token_hex(8))   # makes "no such email" take as long


class Admin:
    """Per-app state: the store, the mailer and the rate limiter."""

    def __init__(self, store: AdminStore, mailer, mail_async: bool = True):
        self.store, self.mailer, self.mail_async = store, mailer, mail_async
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()
        self.settings_lock = threading.Lock()

    def limited(self, key: str, limit: int, window: float) -> int:
        """0 if allowed (and counts the hit); otherwise seconds to wait. In-memory, per process."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return int(window - (now - q[0])) + 1
            q.append(now)
            return 0


# ---- email ---------------------------------------------------------------------------

class MailError(RuntimeError):
    pass


class BrevoMailer:
    """Sends through Brevo's transactional email API (free plan: 300 emails a day).

    The sender address must be verified in the Brevo dashboard first. The API key is read
    from EMAIL_PROVIDER_API_KEY and is never sent to the browser or written to a log.
    """
    URL = "https://api.brevo.com/v3/smtp/email"

    def __init__(self, api_key: str, sender_email: str, sender_name: str = "BONC Network"):
        self.api_key, self.sender_email, self.sender_name = api_key, sender_email, sender_name

    def send(self, to: str, subject: str, text: str) -> None:
        body = json.dumps({"sender": {"email": self.sender_email, "name": self.sender_name},
                           "to": [{"email": to}], "subject": subject, "textContent": text}).encode()
        req = urllib.request.Request(self.URL, data=body, method="POST", headers={
            "api-key": self.api_key, "content-type": "application/json", "accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status >= 300:
                    raise MailError(f"email provider answered {resp.status}")
        except urllib.error.HTTPError as exc:
            raise MailError(f"email provider answered {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MailError(f"email provider unreachable: {type(exc).__name__}") from None


def mailer_from_env():
    key, sender = os.environ.get("EMAIL_PROVIDER_API_KEY", ""), os.environ.get("EMAIL_FROM", "")
    if not key or not sender:
        return None
    return BrevoMailer(key, sender, os.environ.get("EMAIL_FROM_NAME", "BONC Network"))


# ---- setup ---------------------------------------------------------------------------

def default_db_path() -> str:
    return os.environ.get("ADMIN_DB_PATH") or str(Path(__file__).resolve().parent.parent / "data" / "admin.db")


def init_admin(app, store: AdminStore | None = None, mailer=None, mail_async: bool = True) -> Admin:
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        secret = secrets.token_hex(32)
        log.warning("SECRET_KEY is not set: admin logins will end whenever the server restarts. "
                    "Set it in .env (see .env.example).")
    app.secret_key = secret
    app.config.update(
        SESSION_COOKIE_NAME="bonc_admin", SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
    )
    admin = Admin(store or AdminStore(default_db_path()), mailer or mailer_from_env(), mail_async)
    app.extensions["admin"] = admin
    app.register_blueprint(admin_bp)
    return admin


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
    resp = jsonify({"error": code, "message": message, **extra})
    resp.status_code = status
    return resp


def _session_admin():
    a: Admin = current_app.extensions["admin"]
    aid, sv, ts = session.get("aid"), session.get("sv"), session.get("ts", 0)
    if not aid:
        return None
    row = a.store.get_admin_by_id(aid)
    if row is None or row["session_version"] != sv or time.time() - ts > SESSION_IDLE:
        session.clear()
        return None
    session["ts"] = time.time()              # activity keeps the login alive
    session.permanent = True
    return row


def check_admin():
    """None if the caller may use admin endpoints, otherwise a ready-made error response.

    Accepted: a logged-in admin session, or (for scripts/ops) the X-Admin-Token header.
    """
    row = _session_admin()
    if row is not None:
        if request.method not in ("GET", "HEAD", "OPTIONS") \
                and request.headers.get(CSRF_HEADER[0]) != CSRF_HEADER[1]:
            return _err(403, "forbidden", "missing request header")
        return None
    token = current_app.extensions["moderation"].settings.admin_token
    admin: Admin = current_app.extensions["admin"]
    if token and hmac.compare_digest(request.headers.get("X-Admin-Token", ""), token):
        return None
    if not token and admin.store.admin_count() == 0:
        return _err(404, "not_found", "admin endpoints disabled (no admin created, ADMIN_TOKEN unset)")
    return _err(401, "unauthorized", "please log in")


# ---- validation -----------------------------------------------------------------------

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _email(value) -> str | None:
    value = value.strip().lower() if isinstance(value, str) else ""
    return value if value and len(value) <= 320 and _EMAIL.match(value) else None


def password_problem(pw: str, email: str = "") -> str | None:
    if not isinstance(pw, str) or len(pw) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if len(pw) > 200:
        return "That password is too long."
    if not (re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)):
        return "Use both letters and numbers."
    if email and pw.lower() == email.lower():
        return "The password can't be your email address."
    return None


def _hash_secret(value: str) -> str:
    return hmac.new(current_app.secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def _ip() -> str:
    return request.remote_addr or "?"


# ---- login / logout / me --------------------------------------------------------------

@admin_bp.post("/v1/admin/auth/login")
def login():
    a: Admin = current_app.extensions["admin"]
    data = _body()
    email, password = _email(data.get("email")), data.get("password")
    if email is None or not isinstance(password, str) or not password:
        return _err(400, "invalid_request", "Enter your email address and password.")
    wait = max(a.limited(f"login-ip:{_ip()}", 20, 900), a.limited(f"login-email:{email}", 5, 900))
    if wait:
        resp = _err(429, "rate_limited", f"Too many attempts. Try again in {wait // 60 + 1} minute(s).")
        resp.headers["Retry-After"] = str(wait)
        return resp
    row = a.store.get_admin(email)
    ok = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, password)
    if not (row and ok):
        log.warning("failed admin login from %s", _ip())
        return _err(401, "invalid_credentials", "Incorrect email or password.")
    session.clear()
    session.update(aid=row["id"], sv=row["session_version"], ts=time.time())
    session.permanent = True
    return jsonify({"email": row["email"]}), 200


@admin_bp.post("/v1/admin/auth/logout")
def logout():
    session.clear()
    return jsonify({"ok": True}), 200


@admin_bp.get("/v1/admin/auth/me")
def me():
    row = _session_admin()
    if row is None:
        return _err(401, "unauthorized", "please log in")
    return jsonify({"email": row["email"]}), 200


# ---- password recovery ----------------------------------------------------------------

GENERIC_FORGOT = {"ok": True, "message": "If that email belongs to an administrator, "
                                          "we have sent a 6-digit code to it."}


def _deliver(a: Admin, email: str, code: str) -> None:
    try:
        if a.mailer is None:
            log.error("password reset requested but email is not configured "
                      "(EMAIL_PROVIDER_API_KEY / EMAIL_FROM); no code was sent")
            return
        a.mailer.send(email, "Your BONC admin verification code",
                      f"Your BONC admin verification code is {code}.\n\n"
                      f"It works for {OTP_TTL // 60} minutes and only once. "
                      "If you did not ask for it, ignore this email: your password has not changed.")
    except MailError as exc:
        log.error("could not send reset email: %s", exc)       # never log the code


@admin_bp.post("/v1/admin/auth/password/forgot")
def forgot():
    a: Admin = current_app.extensions["admin"]
    email = _email(_body().get("email"))
    if email is None:
        return _err(400, "invalid_request", "Enter a valid email address.")
    if a.limited(f"forgot-ip:{_ip()}", 10, 3600) or a.limited(f"forgot-email:{email}", 5, 3600):
        return jsonify(GENERIC_FORGOT), 200      # same reply: a limit must not reveal anything
    if a.store.get_admin(email) is not None:
        existing = a.store.get_code(email)
        if existing is None or time.time() - existing["created_at"] >= RESEND_COOLDOWN:
            code = f"{secrets.randbelow(10**6):06d}"
            a.store.put_code(email, _hash_secret(f"{email}:{code}"), OTP_TTL)   # replaces older codes
            if a.mail_async:
                threading.Thread(target=_deliver, args=(a, email, code), daemon=True).start()
            else:
                _deliver(a, email, code)
    return jsonify(GENERIC_FORGOT), 200


@admin_bp.post("/v1/admin/auth/password/verify")
def verify():
    a: Admin = current_app.extensions["admin"]
    data = _body()
    email, code = _email(data.get("email")), data.get("code")
    if email is None or not isinstance(code, str) or not re.fullmatch(r"\d{6}", code.strip()):
        return _err(400, "invalid_request", "Enter the 6-digit code.")
    if a.limited(f"verify-ip:{_ip()}", 30, 900):
        return _err(429, "rate_limited", "Too many attempts. Wait a few minutes.")
    token = secrets.token_urlsafe(32)
    result = a.store.verify_code(email, _hash_secret(f"{email}:{code.strip()}"), OTP_MAX_ATTEMPTS,
                                 _hash_secret(token), TOKEN_TTL)
    if result == "ok":
        return jsonify({"reset_token": token}), 200
    msg = {"expired": "That code has expired. Request a new one.",
           "locked": "Too many wrong codes. Request a new one."}.get(result, "That code is not correct.")
    return _err(400, "invalid_code", msg)


@admin_bp.post("/v1/admin/auth/password/reset")
def reset():
    a: Admin = current_app.extensions["admin"]
    data = _body()
    email, token, new = _email(data.get("email")), data.get("reset_token"), data.get("new_password")
    if email is None or not isinstance(token, str) or not token:
        return _err(400, "invalid_request", "Start again from Forgot password.")
    problem = password_problem(new, email)
    if problem:
        return _err(400, "weak_password", problem)
    if a.limited(f"reset-ip:{_ip()}", 20, 900):
        return _err(429, "rate_limited", "Too many attempts. Wait a few minutes.")
    if not a.store.reset_password(email, _hash_secret(token), generate_password_hash(new)):
        return _err(400, "invalid_token", "This reset link has expired. Start again from Forgot password.")
    session.clear()
    return jsonify({"ok": True, "message": "Password changed. You can log in now."}), 200


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

@admin_bp.get("/v1/admin/moderation-logs")
def get_moderation_logs():
    denied = check_admin()
    if denied:
        return denied
    a: Admin = current_app.extensions["admin"]
    decision = request.args.get("decision")
    content_type = request.args.get("content_type")
    severity = request.args.get("severity")
    query = request.args.get("q")
    try:
        limit = min(200, max(1, int(request.args.get("limit", 50))))
    except (ValueError, TypeError):
        limit = 50
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (ValueError, TypeError):
        offset = 0

    logs_data = a.store.get_moderation_logs(
        decision=decision,
        content_type=content_type,
        severity=severity,
        query=query,
        limit=limit,
        offset=offset,
    )
    return jsonify(logs_data), 200


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
    filters = {"decision": request.args.get("decision"),
               "content_type": request.args.get("content_type"),
               "severity": request.args.get("severity"),
               "query": request.args.get("q")}
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
                     "severity": filters["severity"], "search": filters["query"]})
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
        return _err(400, "missing_value", "Give the address to block.")
    if scope not in ("ip", "network"):
        return _err(400, "bad_scope", "scope must be 'ip' or 'network'.")

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

    a: Admin = current_app.extensions["admin"]
    # A script authenticating with X-Admin-Token has no session row, so this records nothing
    # rather than failing - the block is still the right thing to make.
    who = _session_admin()
    row = a.store.block_client(value, scope, reason=str(body.get("reason", "")).strip(),
                               created_by=who["email"] if who else "")
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
