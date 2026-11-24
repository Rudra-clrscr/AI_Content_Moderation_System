"""BONC_AUTH_VERIFY_URL: admin endpoints accept the .NET backend's admin bearer token.

Exercised against a real HTTP verifier on 127.0.0.1 through the real bonc-auth package, so
what is tested is the wire contract the .NET team has to meet: 200 + adminId for a live admin
session, 401 for a bad or ended one, 403 for a signed-in non-admin.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip("bonc_auth")

from app import create_app  # noqa: E402

ADMIN = {"adminId": "dotnet-admin-7", "role": "Admin", "sessionId": "sess-1"}


class _Verifier(BaseHTTPRequestHandler):
    calls: dict = {}      # token -> how many times .NET was asked about it

    def do_GET(self):  # noqa: N802 - http.server's naming
        token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
        _Verifier.calls[token] = _Verifier.calls.get(token, 0) + 1
        status, body = {"good": (200, ADMIN), "member": (403, {}),
                        "broken": (500, {})}.get(token, (401, {}))
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def verify_url():
    srv = HTTPServer(("127.0.0.1", 0), _Verifier)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/api/auth/verify-admin"
    srv.shutdown()


def _client(make_settings, **kw):
    return create_app(settings=make_settings(**kw)).test_client()


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_a_live_dotnet_admin_token_opens_every_admin_blueprint(make_settings, verify_url):
    c = _client(make_settings, bonc_auth_verify_url=verify_url)
    # One route from each of the three admin blueprints.
    assert c.get("/v1/admin/moderation-levels", headers=_bearer("good")).status_code == 200
    assert c.get("/v1/admin/images/categories", headers=_bearer("good")).status_code == 200
    assert c.get("/v1/admin/policy/labels", headers=_bearer("good")).status_code == 200
    # Writes too: a bearer token is never attached by a browser on its own, so there is
    # nothing for a CSRF check to protect.
    r = c.put("/v1/admin/moderation-levels/job", json={"level": "lenient"},
              headers=_bearer("good"))
    assert r.status_code == 200, r.get_json()


def test_bad_tokens_are_refused_with_the_reason(make_settings, verify_url):
    c = _client(make_settings, bonc_auth_verify_url=verify_url)
    r = c.get("/v1/admin/moderation-levels", headers=_bearer("expired"))
    assert r.status_code == 401 and r.get_json()["Data"]["code"] == "unauthorized"
    r = c.get("/v1/admin/moderation-levels", headers=_bearer("member"))
    assert r.status_code == 403 and r.get_json()["Data"]["code"] == "not_admin"
    r = c.get("/v1/admin/moderation-levels", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401


def test_it_fails_closed_when_dotnet_misbehaves_or_is_down(make_settings, verify_url):
    c = _client(make_settings, bonc_auth_verify_url=verify_url)
    r = c.get("/v1/admin/moderation-levels", headers=_bearer("broken"))
    assert r.status_code == 503 and r.get_json()["Data"]["code"] == "auth_unavailable"
    down = _client(make_settings, bonc_auth_verify_url="http://127.0.0.1:9/verify",
                   bonc_auth_timeout=1.0)
    assert down.get("/v1/admin/moderation-levels",
                    headers=_bearer("good")).status_code == 503


def test_a_rejected_token_is_not_rescued_by_the_admin_token(make_settings, verify_url):
    c = _client(make_settings, bonc_auth_verify_url=verify_url)
    r = c.get("/v1/admin/moderation-levels",
              headers={**_bearer("expired"), "X-Admin-Token": "secret"})
    assert r.status_code == 401
    # Without a bearer header, the ops token still works.
    assert c.get("/v1/admin/moderation-levels",
                 headers={"X-Admin-Token": "secret"}).status_code == 200


def test_me_reports_the_dotnet_identity(make_settings, verify_url):
    c = _client(make_settings, bonc_auth_verify_url=verify_url)
    body = c.get("/v1/admin/auth/me", headers=_bearer("good")).get_json()
    assert body == {"admin_id": "dotnet-admin-7", "role": "Admin",
                    "session_id": "sess-1", "via": "bonc"}


def test_blocks_record_the_dotnet_admin_who_made_them(make_settings, verify_url):
    c = _client(make_settings, bonc_auth_verify_url=verify_url)
    r = c.post("/v1/admin/blocked-clients", json={"value": "member-9", "scope": "member"},
               headers=_bearer("good"))
    assert r.status_code == 201
    assert r.get_json()["blocked"]["created_by"] == "dotnet-admin-7"


def test_unset_changes_nothing_and_ready_says_which(make_settings, verify_url):
    c = _client(make_settings)
    # A bearer header means nothing without the verifier: still the old 401.
    assert c.get("/v1/admin/moderation-levels", headers=_bearer("good")).status_code == 401
    assert c.get("/ready").get_json()["Data"]["admin_verifier"] == "local"
    on = _client(make_settings, bonc_auth_verify_url=verify_url)
    assert on.get("/ready").get_json()["Data"]["admin_verifier"] == "bonc"


def test_auth_disabled_still_wins(make_settings, verify_url):
    c = _client(make_settings, bonc_auth_verify_url=verify_url, auth_disabled=True)
    assert c.get("/v1/admin/moderation-levels", headers=_bearer("expired")).status_code == 200


def test_a_plain_http_remote_verifier_is_refused_at_startup(make_settings):
    with pytest.raises(ValueError, match="HTTPS"):
        create_app(settings=make_settings(bonc_auth_verify_url="http://example.com/verify"))


# ---- caching -------------------------------------------------------------------------------

def _asked(token):
    return _Verifier.calls.get(token, 0)


def test_an_accepted_token_is_checked_once_per_window(make_settings, verify_url):
    """A dashboard page is ~25 admin calls; it must not be ~25 round trips to .NET."""
    c = _client(make_settings, bonc_auth_verify_url=verify_url, bonc_auth_cache_seconds=60)
    before = _asked("good")
    for _ in range(5):
        assert c.get("/v1/admin/moderation-levels", headers=_bearer("good")).status_code == 200
    assert _asked("good") - before == 1


def test_the_window_ends_and_dotnet_is_asked_again(make_settings, verify_url):
    from app.admin_auth import CachedVerifier
    app = create_app(settings=make_settings(bonc_auth_verify_url=verify_url,
                                            bonc_auth_cache_seconds=60))
    cached = app.extensions["bonc_auth"]
    assert isinstance(cached, CachedVerifier)
    now = [1000.0]
    cached._clock = lambda: now[0]
    c = app.test_client()
    before = _asked("good")
    c.get("/v1/admin/moderation-levels", headers=_bearer("good"))
    now[0] += 59
    c.get("/v1/admin/moderation-levels", headers=_bearer("good"))
    assert _asked("good") - before == 1
    now[0] += 2                                   # past the 60 s window
    c.get("/v1/admin/moderation-levels", headers=_bearer("good"))
    assert _asked("good") - before == 2


def test_refusals_and_failures_are_never_cached(make_settings, verify_url):
    c = _client(make_settings, bonc_auth_verify_url=verify_url, bonc_auth_cache_seconds=60)
    for token in ("expired", "member", "broken"):
        before = _asked(token)
        c.get("/v1/admin/moderation-levels", headers=_bearer(token))
        c.get("/v1/admin/moderation-levels", headers=_bearer(token))
        assert _asked(token) - before == 2, token


def test_zero_checks_every_request(make_settings, verify_url):
    c = _client(make_settings, bonc_auth_verify_url=verify_url, bonc_auth_cache_seconds=0)
    before = _asked("good")
    c.get("/v1/admin/moderation-levels", headers=_bearer("good"))
    c.get("/v1/admin/moderation-levels", headers=_bearer("good"))
    assert _asked("good") - before == 2


def test_the_cache_hands_out_copies(make_settings, verify_url):
    """g.admin is per request; a view that edits it must not edit the next request's."""
    from app.admin_auth import CachedVerifier

    class Inner:
        def verify(self, token):
            return {"adminId": "a1"}
    v = CachedVerifier(Inner(), 60)
    v.verify("t")["adminId"] = "tampered"
    assert v.verify("t")["adminId"] == "a1"


# ---- CORS ----------------------------------------------------------------------------------

FRONT = "https://admin.boncnetwork.com"


def _preflight(c, path="/v1/admin/moderation-levels/job", origin=FRONT):
    return c.options(path, headers={"Origin": origin, "Access-Control-Request-Method": "PUT",
                                    "Access-Control-Request-Headers": "authorization,content-type"})


def test_the_listed_frontend_may_call_admin_endpoints(make_settings, verify_url):
    c = _client(make_settings, bonc_auth_verify_url=verify_url, admin_cors_origins=(FRONT,))
    pre = _preflight(c)
    assert pre.status_code == 200
    assert pre.headers["Access-Control-Allow-Origin"] == FRONT
    assert "PUT" in pre.headers["Access-Control-Allow-Methods"]
    assert "Authorization" in pre.headers["Access-Control-Allow-Headers"]
    assert "Access-Control-Allow-Credentials" not in pre.headers
    r = c.get("/v1/admin/moderation-levels", headers={**_bearer("good"), "Origin": FRONT})
    assert r.status_code == 200 and r.headers["Access-Control-Allow-Origin"] == FRONT
    assert "Origin" in r.headers["Vary"]
    # A refusal is readable too, or the frontend cannot tell "signed out" from "down".
    r = c.get("/v1/admin/moderation-levels", headers={**_bearer("expired"), "Origin": FRONT})
    assert r.status_code == 401 and r.headers["Access-Control-Allow-Origin"] == FRONT


def test_other_origins_and_other_endpoints_get_nothing(make_settings, verify_url):
    c = _client(make_settings, bonc_auth_verify_url=verify_url, admin_cors_origins=(FRONT,))
    assert "Access-Control-Allow-Origin" not in _preflight(c, origin="https://evil.example").headers
    r = c.post("/v1/moderate", json={"content": "hello there", "content_type": "post"},
               headers={"Origin": FRONT})
    assert "Access-Control-Allow-Origin" not in r.headers


def test_no_cors_unless_configured(make_settings, verify_url):
    c = _client(make_settings, bonc_auth_verify_url=verify_url)
    assert "Access-Control-Allow-Origin" not in _preflight(c).headers


def test_cors_origins_are_parsed_strictly():
    from app.config import _cors_origins
    assert _cors_origins(" https://A.example.com/ , http://localhost:3000") == (
        "https://a.example.com", "http://localhost:3000")
    assert _cors_origins("") == ()
    for bad in ("*", "admin.boncnetwork.com", "https://admin.boncnetwork.com/login"):
        with pytest.raises(ValueError):
            _cors_origins(bad)
