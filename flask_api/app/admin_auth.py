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


def remember(ctype: str, body: dict) -> None:
    """Save the fields an admin just changed for one surface."""
    a: Admin = current_app.extensions["admin"]
    with a.settings_lock:
        data = a.store.load_settings()
        rec = data.setdefault("features", {}).setdefault(ctype, {})
        rec.update({k: v for k, v in body.items() if v is not None})
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
    return {
        "enabled": all(enabled),
        "level": shown[0] if shown and len(set(shown)) == 1 and len(shown) == len(pol) else None,
        "mixed": len(set(enabled)) > 1 or len(set(p.level for p in pol.values())) > 1,
        "levels": sorted(levels.as_dict()["levels"]),
        "surfaces": {t: levels.for_type(t).as_dict() for t in ALL_SURFACES},
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
