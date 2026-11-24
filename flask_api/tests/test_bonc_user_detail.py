"""BONC_AUTH_STYLE=user-detail: admin tokens checked through .NET's existing getUserDetail.

A fake getUserDetail on 127.0.0.1 answers the way the real one does (the {Success, Data}
envelope; a real HTTP 401 with {"Success": false, "Message": "Invalid token"} for a bad
token). The tokens are made-up JWTs: only their payload is read on this side, and the fake
server plays the part of .NET checking the signature.
"""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("bonc_auth")

from app import create_app  # noqa: E402


def _jwt(claims: dict) -> str:
    def enc(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'HS256', 'typ': 'JWT'})}.{enc(claims)}.fake-signature"


ADMIN_ID = "11111111-aaaa-4bbb-8ccc-000000000001"
USERS = {   # UserRowId -> the record .NET holds
    ADMIN_ID: {"UserRowId": ADMIN_ID, "UserRoles": "Admin", "IsActive": True},
    "u-super": {"UserRowId": "u-super", "UserRoles": "Basic, SuperAdmin", "IsActive": True},
    "u-basic": {"UserRowId": "u-basic", "UserRoles": "Basic", "IsActive": True},
    "u-gone": {"UserRowId": "u-gone", "UserRoles": "Admin", "IsActive": False},
    "u-bare": {"UserRowId": "u-bare", "UserRoles": "Admin", "IsActive": True},
}
TOK = {name: _jwt({"UserRowId": uid, "exp": 9999999999}) for name, uid in
       [("admin", ADMIN_ID), ("super", "u-super"), ("basic", "u-basic"), ("gone", "u-gone"),
        ("bare", "u-bare"), ("swap", "u-swap"), ("falsy", "u-falsy"), ("boom", "u-boom")]}
REVOKED = _jwt({"UserRowId": ADMIN_ID, "exp": 1})


class _GetUserDetail(BaseHTTPRequestHandler):
    seen: list = []           # (token, userRowId) per call

    def do_GET(self):  # noqa: N802
        token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
        uid = parse_qs(urlparse(self.path).query).get("userRowId", [""])[0]
        _GetUserDetail.seen.append((token, uid))
        if token not in TOK.values():               # .NET: bad signature / expired / missing
            return self._send(401, {"Success": False, "Message": "Invalid token"})
        if uid == "u-swap":                         # a .NET bug: someone else's record
            return self._send(200, {"Success": True, "Data": USERS[ADMIN_ID]})
        if uid == "u-falsy":
            return self._send(200, {"Success": False, "Message": "User not found"})
        if uid == "u-boom":
            return self._send(409, "conflict")
        if uid == "u-bare":                         # the DTO without the envelope
            return self._send(200, USERS[uid])
        return self._send(200, {"Success": True, "Message": "", "Data": USERS[uid]})

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def detail_url():
    srv = HTTPServer(("127.0.0.1", 0), _GetUserDetail)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/api/userService/getUserDetail"
    srv.shutdown()


@pytest.fixture
def c(make_settings, detail_url):
    return create_app(settings=make_settings(
        bonc_auth_verify_url=detail_url, bonc_auth_style="user-detail",
        bonc_auth_cache_seconds=0)).test_client()


def _get(c, token, path="/v1/admin/moderation-levels"):
    return c.get(path, headers={"Authorization": f"Bearer {token}"})


def _code(r):
    return r.get_json()["Data"]["code"]


def test_an_admin_gets_in_and_me_names_them(c):
    assert _get(c, TOK["admin"]).status_code == 200
    assert _get(c, TOK["admin"], "/v1/admin/auth/me").get_json() == {
        "admin_id": ADMIN_ID, "role": "Admin", "session_id": None, "via": "bonc"}


def test_the_user_asked_about_is_the_one_the_token_names(c):
    _GetUserDetail.seen.clear()
    _get(c, TOK["admin"])
    assert _GetUserDetail.seen == [(TOK["admin"], ADMIN_ID)]


def test_any_listed_role_among_several_counts(c):
    r = _get(c, TOK["super"], "/v1/admin/auth/me")
    assert r.status_code == 200 and r.get_json()["role"] == "SuperAdmin"


def test_a_signed_in_member_is_403(c):
    r = _get(c, TOK["basic"])
    assert r.status_code == 403 and _code(r) == "not_admin"


def test_a_bad_expired_or_foreign_token_is_401(c):
    assert _code(_get(c, REVOKED)) == "unauthorized"            # .NET says 401
    _GetUserDetail.seen.clear()
    for junk in ("not-a-jwt", _jwt({"sub": "x"})):              # no UserRowId: never sent
        r = _get(c, junk)
        assert r.status_code == 401 and _code(r) == "unauthorized"
    assert _GetUserDetail.seen == []


def test_a_deactivated_admin_is_401(c):
    assert _get(c, TOK["gone"]).status_code == 401


def test_the_bare_dto_is_understood_too(c):
    assert _get(c, TOK["bare"]).status_code == 200


def test_someone_elses_record_is_never_access(c):
    """If .NET ever answered with another user's record, that must not lend their role."""
    r = _get(c, TOK["swap"])
    assert r.status_code == 503 and _code(r) == "auth_unavailable"


def test_odd_answers_fail_closed(c, make_settings):
    assert _get(c, TOK["falsy"]).status_code == 503              # 200 + Success:false
    assert _get(c, TOK["boom"]).status_code == 503               # 409
    down = create_app(settings=make_settings(
        bonc_auth_verify_url="http://127.0.0.1:9/api/userService/getUserDetail",
        bonc_auth_style="user-detail", bonc_auth_timeout=1.0)).test_client()
    assert _get(down, TOK["admin"]).status_code == 503


def test_blocks_record_the_admins_user_row_id(c):
    r = c.post("/v1/admin/blocked-clients", json={"value": "member-9", "scope": "member"},
               headers={"Authorization": f"Bearer {TOK['admin']}"})
    assert r.status_code == 201 and r.get_json()["blocked"]["created_by"] == ADMIN_ID


def test_the_role_list_is_configurable(make_settings, detail_url):
    c = create_app(settings=make_settings(
        bonc_auth_verify_url=detail_url, bonc_auth_style="user-detail",
        bonc_auth_admin_roles=("SuperAdmin",))).test_client()
    assert _get(c, TOK["admin"]).status_code == 403
    assert _get(c, TOK["super"]).status_code == 200


def test_acceptances_are_cached_like_the_other_style(make_settings, detail_url):
    c = create_app(settings=make_settings(
        bonc_auth_verify_url=detail_url, bonc_auth_style="user-detail",
        bonc_auth_cache_seconds=60)).test_client()
    _GetUserDetail.seen.clear()
    for _ in range(4):
        assert _get(c, TOK["admin"]).status_code == 200
    assert len(_GetUserDetail.seen) == 1


def test_bad_configuration_stops_startup(make_settings, detail_url):
    with pytest.raises(ValueError, match="BONC_AUTH_STYLE"):
        create_app(settings=make_settings(bonc_auth_verify_url=detail_url,
                                          bonc_auth_style="guess"))
    with pytest.raises(ValueError, match="BONC_AUTH_ADMIN_ROLES"):
        create_app(settings=make_settings(bonc_auth_verify_url=detail_url,
                                          bonc_auth_style="user-detail",
                                          bonc_auth_admin_roles=()))
    with pytest.raises(ValueError, match="HTTPS"):
        create_app(settings=make_settings(
            bonc_auth_verify_url="http://api.example.com/api/userService/getUserDetail",
            bonc_auth_style="user-detail"))


def test_env_parsing(monkeypatch):
    from app.config import load_settings
    monkeypatch.setenv("BONC_AUTH_STYLE", " User-Detail ")
    monkeypatch.setenv("BONC_AUTH_ADMIN_ROLES", "Admin, SuperAdmin ,")
    s = load_settings()
    assert s.bonc_auth_style == "user-detail"
    assert s.bonc_auth_admin_roles == ("Admin", "SuperAdmin")
