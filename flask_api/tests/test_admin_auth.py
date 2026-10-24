"""Admin login, password recovery and saved moderation settings.

Email delivery is MOCKED here (FakeMailer, and a patched urlopen for the Brevo request shape).
A real send to a real inbox is a manual check: see docs in ADMIN_SETUP.md.
"""
import re
import time
import urllib.request

import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.admin_auth import ALL_SURFACES, BrevoMailer, MailError
from app.admin_store import AdminStore
from tests.conftest import FixedScorer

EMAIL, PW = "boss@example.com", "Correct-horse-9"
HDR = {"X-Requested-With": "bonc-admin"}


class FakeMailer:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, text):
        self.sent.append((to, subject, text))

    @property
    def last_code(self):
        return re.search(r"\b(\d{6})\b", self.sent[-1][2]).group(1)


@pytest.fixture
def mailer():
    return FakeMailer()


@pytest.fixture
def scorer():
    return FixedScorer(0.1)


@pytest.fixture
def app(make_settings, scorer, sink, mailer):
    app = create_app(make_settings(admin_token=None), scorer=scorer, sink=sink,
                     admin_store=AdminStore(":memory:"), mailer=mailer, mail_async=False)
    app.extensions["admin"].store.create_admin(EMAIL, generate_password_hash(PW))
    return app


@pytest.fixture
def anon(app):
    return app.test_client()


@pytest.fixture
def admin(app):
    c = app.test_client()
    assert c.post("/v1/admin/auth/login", json={"email": EMAIL, "password": PW}).status_code == 200
    return c


def moderate(c, ctype, text="hello there"):
    r = c.post("/v1/moderate", json={"content": text, "content_type": ctype})
    return r


# ---- login -------------------------------------------------------------------------

def test_valid_login_and_me_and_logout(anon):
    assert anon.get("/v1/admin/auth/me").status_code == 401
    r = anon.post("/v1/admin/auth/login", json={"email": EMAIL.upper(), "password": PW})
    assert r.status_code == 200 and r.get_json()["email"] == EMAIL
    assert anon.get("/v1/admin/auth/me").status_code == 200
    anon.post("/v1/admin/auth/logout")
    assert anon.get("/v1/admin/auth/me").status_code == 401


def test_bad_credentials_rejected_with_one_message(anon):
    wrong_pw = anon.post("/v1/admin/auth/login", json={"email": EMAIL, "password": "nope-nope-1"})
    no_user = anon.post("/v1/admin/auth/login", json={"email": "who@example.com", "password": PW})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.get_json() == no_user.get_json()          # cannot tell which part was wrong


def test_password_never_stored_in_plaintext(app):
    row = app.extensions["admin"].store.get_admin(EMAIL)
    assert PW not in row["password_hash"] and row["password_hash"].startswith(("scrypt:", "pbkdf2:"))


def test_login_is_rate_limited(anon):
    codes = [anon.post("/v1/admin/auth/login", json={"email": EMAIL, "password": f"wrong-pass-{i}"}).status_code
             for i in range(7)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert anon.post("/v1/admin/auth/login", json={"email": EMAIL, "password": PW}).status_code == 429


def test_session_expires_when_idle(admin, app):
    with admin.session_transaction() as s:
        s["ts"] = time.time() - 31 * 60
    assert admin.get("/v1/admin/auth/me").status_code == 401


# ---- endpoints are closed to everyone else ---------------------------------------------

def test_unauthenticated_cannot_use_admin_endpoints(anon):
    assert anon.get("/v1/admin/moderation-settings").status_code == 401
    assert anon.put("/v1/admin/moderation-settings", json={"enabled": False, "level": "medium"},
                    headers=HDR).status_code == 401
    assert anon.get("/v1/admin/moderation-levels").status_code == 401
    assert anon.put("/v1/admin/moderation-levels/article", json={"enabled": False}).status_code == 401


def test_session_changes_need_the_csrf_header(admin):
    r = admin.put("/v1/admin/moderation-settings", json={"enabled": False, "level": "medium"})
    assert r.status_code == 403
    assert admin.get("/v1/admin/moderation-settings").get_json()["enabled"] is True


# ---- settings: saved, enforced, restart-proof ------------------------------------------

def test_settings_load_what_is_actually_saved(admin):
    j = admin.get("/v1/admin/moderation-settings").get_json()
    # The three named levels AND the ten cumulative ones from config/moderation_levels.yaml.
    # Asserted as a subset rather than an exact list: the levels are config, so a test that
    # pins the whole list fails every time a level is added, which is what it did when the
    # ten-level heat bar landed. What matters is that the endpoint reports what is configured
    # and that nothing silently drops out of it.
    assert j["enabled"] is True
    assert {"lenient", "medium", "strict"} <= set(j["levels"])
    assert {f"level_{n}" for n in range(1, 11)} <= set(j["levels"])
    assert set(j["surfaces"]) == set(ALL_SURFACES)
    assert {"article", "business_profile", "video", "request", "proposal", "business_proposal"} <= set(ALL_SURFACES)


def test_bad_settings_bodies_change_nothing(admin):
    for body in ({"enabled": "false", "level": "medium"}, {"enabled": False, "level": "balanced"},
                 {"enabled": False}, {"enabled": False, "level": "medium", "extra": 1}, {}):
        assert admin.put("/v1/admin/moderation-settings", json=body, headers=HDR).status_code == 400
    assert admin.get("/v1/admin/moderation-settings").get_json()["enabled"] is True


@pytest.mark.parametrize("ctype", ALL_SURFACES)
def test_off_publishes_and_on_moderates_every_surface(admin, scorer, ctype):
    scorer.risk = 0.99
    assert moderate(admin, ctype).get_json()["decision"] == "reject"
    admin.put("/v1/admin/moderation-settings", json={"enabled": False, "level": "strict"}, headers=HDR)
    off = moderate(admin, ctype).get_json()
    assert off["decision"] == "allow" and off.get("decided_by") == "disabled"
    admin.put("/v1/admin/moderation-settings", json={"enabled": True, "level": "strict"}, headers=HDR)
    assert moderate(admin, ctype).get_json()["decision"] == "reject"


@pytest.mark.parametrize("ctype", ALL_SURFACES)
def test_each_level_is_enforced_on_every_surface(admin, scorer, ctype):
    def decide(level, risk):
        admin.put("/v1/admin/moderation-settings", json={"enabled": True, "level": level}, headers=HDR)
        scorer.risk = risk
        return moderate(admin, ctype).get_json()["decision"]
    assert decide("lenient", 0.60) == "allow"       # lenient bar 0.85
    assert decide("medium", 0.60) == "reject"       # medium bar 0.50
    assert decide("medium", 0.40) == "allow"
    assert decide("strict", 0.40) == "reject"       # strict bar 0.25
    assert decide("lenient", 0.99) == "reject"      # even lenient refuses clear abuse


def test_off_does_not_switch_off_authentication_or_validation(admin, anon):
    admin.put("/v1/admin/moderation-settings", json={"enabled": False, "level": "medium"}, headers=HDR)
    assert anon.get("/v1/admin/moderation-settings").status_code == 401
    assert anon.post("/v1/moderate", json={"content": "", "content_type": "article"}).status_code == 400


def test_rejected_and_revise_are_never_allow_when_on(admin, scorer, make_settings):
    scorer.risk = 0.9
    assert moderate(admin, "article").get_json()["decision"] in ("reject", "revise")
    r = moderate(admin, "article", "badword here").get_json()      # the gate's own block rule
    assert r["decision"] == "reject"
    r = moderate(admin, "proposal", "you can pay in crypto").get_json()   # the gate's revise rule
    assert r["decision"] in ("revise", "reject")


def test_moderator_failure_never_allows(make_settings, sink, mailer):
    class Boom:
        version = "boom"
        def score(self, text):
            raise RuntimeError("model down")
    app = create_app(make_settings(), scorer=Boom(), sink=sink, admin_store=AdminStore(":memory:"),
                     mailer=mailer, mail_async=False)
    r = moderate(app.test_client(), "article")
    assert r.status_code >= 500 and "allow" not in r.get_data(as_text=True).lower().replace("allowed", "")


def test_settings_survive_a_restart(make_settings, sink, mailer, tmp_path, scorer):
    db = tmp_path / "admin.db"
    app1 = create_app(make_settings(admin_token=None), scorer=scorer, sink=sink,
                      admin_store=AdminStore(db), mailer=mailer, mail_async=False)
    app1.extensions["admin"].store.create_admin(EMAIL, generate_password_hash(PW))
    c = app1.test_client()
    c.post("/v1/admin/auth/login", json={"email": EMAIL, "password": PW})
    c.put("/v1/admin/moderation-settings", json={"enabled": True, "level": "strict"}, headers=HDR)

    app2 = create_app(make_settings(admin_token=None), scorer=scorer, sink=sink,
                      admin_store=AdminStore(db), mailer=mailer, mail_async=False)   # "restart"
    scorer.risk = 0.40
    assert moderate(app2.test_client(), "video").get_json()["decision"] == "reject"   # strict still on
    c.put("/v1/admin/moderation-settings", json={"enabled": False, "level": "strict"}, headers=HDR)
    app3 = create_app(make_settings(admin_token=None), scorer=scorer, sink=sink,
                      admin_store=AdminStore(db), mailer=mailer, mail_async=False)
    scorer.risk = 0.99
    assert moderate(app3.test_client(), "video").get_json()["decision"] == "allow"    # OFF still off


def test_old_per_surface_endpoint_is_saved_too(make_settings, sink, mailer, tmp_path, scorer):
    db = tmp_path / "a.db"
    mk = lambda: create_app(make_settings(), scorer=scorer, sink=sink, admin_store=AdminStore(db),
                            mailer=mailer, mail_async=False)
    c = mk().test_client()
    assert c.put("/v1/admin/moderation-levels/article", json={"enabled": False},
                 headers={"X-Admin-Token": "secret"}).status_code == 200
    scorer.risk = 0.99
    assert moderate(mk().test_client(), "article").get_json()["decision"] == "allow"
    assert moderate(mk().test_client(), "video").get_json()["decision"] == "reject"


# ---- forgot password ------------------------------------------------------------------

def forgot(c, email=EMAIL):
    return c.post("/v1/admin/auth/password/forgot", json={"email": email})


def test_forgot_reply_is_identical_for_known_and_unknown(anon, mailer):
    a, b = forgot(anon), forgot(anon, "stranger@example.com")
    assert a.status_code == b.status_code == 200 and a.get_json() == b.get_json()
    assert [m[0] for m in mailer.sent] == [EMAIL]               # only the real admin got mail


def test_code_is_six_digits_hashed_and_expires(anon, mailer, app):
    forgot(anon)
    code = mailer.last_code
    row = app.extensions["admin"].store.get_code(EMAIL)
    assert re.fullmatch(r"\d{6}", code) and code not in row["code_hash"]
    app.extensions["admin"].store._mem.execute("UPDATE reset_code SET expires_at = 1")
    r = anon.post("/v1/admin/auth/password/verify", json={"email": EMAIL, "code": code})
    assert r.status_code == 400 and "expired" in r.get_json()["message"]


def test_wrong_code_then_locked_after_five(anon, mailer):
    forgot(anon)
    real = mailer.last_code
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        assert anon.post("/v1/admin/auth/password/verify", json={"email": EMAIL, "code": wrong}).status_code == 400
    r = anon.post("/v1/admin/auth/password/verify", json={"email": EMAIL, "code": real})
    assert r.status_code == 400                                  # locked: even the right code fails now


def test_new_code_cancels_the_old_one_and_resend_is_throttled(anon, mailer, app):
    forgot(anon)
    first = mailer.last_code
    forgot(anon)                                                 # inside the cooldown: no second email
    assert len(mailer.sent) == 1
    app.extensions["admin"].store._mem.execute("UPDATE reset_code SET created_at = 1")
    forgot(anon)
    assert len(mailer.sent) == 2
    second = mailer.last_code
    if first != second:
        assert anon.post("/v1/admin/auth/password/verify", json={"email": EMAIL, "code": first}).status_code == 400
    assert anon.post("/v1/admin/auth/password/verify", json={"email": EMAIL, "code": second}).status_code == 200


def test_forgot_is_rate_limited_without_revealing_anything(anon, mailer, app):
    for _ in range(8):
        app.extensions["admin"].store._mem.execute("UPDATE reset_code SET created_at = 1")
        assert forgot(anon).status_code == 200
    assert len(mailer.sent) == 5                                 # capped at 5 an hour per email


# ---- full recovery --------------------------------------------------------------------

def recover(c, mailer, new_pw):
    forgot(c)
    v = c.post("/v1/admin/auth/password/verify", json={"email": EMAIL, "code": mailer.last_code})
    assert v.status_code == 200
    return v.get_json()["reset_token"], c.post


def test_full_reset_new_password_works_old_one_does_not(anon, mailer, admin):
    NEW = "Brand-new-pass-42"
    token, _ = recover(anon, mailer, NEW)
    r = anon.post("/v1/admin/auth/password/reset", json={"email": EMAIL, "reset_token": token, "new_password": NEW})
    assert r.status_code == 200
    assert anon.post("/v1/admin/auth/login", json={"email": EMAIL, "password": PW}).status_code == 401
    assert anon.post("/v1/admin/auth/login", json={"email": EMAIL, "password": NEW}).status_code == 200
    assert admin.get("/v1/admin/auth/me").status_code == 401     # the session open before the reset died


def test_reset_token_and_code_are_single_use(anon, mailer):
    token, _ = recover(anon, mailer, "x")
    body = {"email": EMAIL, "reset_token": token, "new_password": "Another-pass-77"}
    assert anon.post("/v1/admin/auth/password/reset", json=body).status_code == 200
    assert anon.post("/v1/admin/auth/password/reset", json=body).status_code == 400
    assert anon.post("/v1/admin/auth/password/verify",
                     json={"email": EMAIL, "code": mailer.last_code}).status_code == 400


def test_reset_needs_the_verified_token_and_a_strong_password(anon, mailer):
    forgot(anon)
    body = {"email": EMAIL, "reset_token": "guess", "new_password": "Another-pass-77"}
    assert anon.post("/v1/admin/auth/password/reset", json=body).status_code == 400   # code not yet verified
    token, _ = recover(anon, mailer, "x")
    for weak in ("short1", "onlyletterslong", "1234567890123", EMAIL):
        r = anon.post("/v1/admin/auth/password/reset", json={"email": EMAIL, "reset_token": token, "new_password": weak})
        assert r.status_code == 400
    assert anon.post("/v1/admin/auth/login", json={"email": EMAIL, "password": PW}).status_code == 200


def test_no_secrets_in_logs(anon, mailer, caplog):
    caplog.set_level("DEBUG")
    forgot(anon)
    code = mailer.last_code
    anon.post("/v1/admin/auth/login", json={"email": EMAIL, "password": "wrong-pass-1"})
    assert code not in caplog.text and "wrong-pass-1" not in caplog.text and PW not in caplog.text


# ---- the real email provider's request shape (network call mocked) -----------------------

def test_brevo_request_shape(monkeypatch):
    seen = {}

    class Resp:
        status = 201
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout):
        seen.update(url=req.full_url, key=req.get_header("Api-key"), body=req.data, timeout=timeout)
        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    BrevoMailer("KEY123", "sender@example.com").send("to@example.com", "Subj", "Code 123456")
    assert seen["url"] == "https://api.brevo.com/v3/smtp/email" and seen["key"] == "KEY123"
    assert b"to@example.com" in seen["body"] and b"sender@example.com" in seen["body"] and seen["timeout"] == 10


def test_provider_failure_is_reported_but_not_leaked(monkeypatch, make_settings, scorer, sink):
    class Failing:
        def send(self, *a):
            raise MailError("email provider answered 401")
    app = create_app(make_settings(admin_token=None), scorer=scorer, sink=sink, admin_store=AdminStore(":memory:"),
                     mailer=Failing(), mail_async=False)
    app.extensions["admin"].store.create_admin(EMAIL, generate_password_hash(PW))
    r = forgot(app.test_client())
    assert r.status_code == 200 and "401" not in r.get_data(as_text=True)


# ---- moderation logs & audit trail in admin dashboard ---------------------------------

def test_moderation_logs_requires_admin(app):
    anon = app.test_client()
    assert anon.get("/v1/admin/moderation-logs").status_code == 401
    assert anon.delete("/v1/admin/moderation-logs").status_code == 401


def test_moderation_logs_records_and_filters_events(admin, make_settings, scorer, sink):
    # Post some content to /v1/moderate
    post_res = admin.post("/v1/moderate", json={
        "content": "Professional business profile text for engineering firm",
        "content_type": "business_profile",
        "content_id": "alice_developer",
    })
    assert post_res.status_code == 200

    # Query moderation logs
    logs_res = admin.get("/v1/admin/moderation-logs")
    assert logs_res.status_code == 200
    data = logs_res.get_json()
    assert "logs" in data
    assert "stats" in data
    assert data["total"] >= 1

    # Find the logged event
    event = next((e for e in data["logs"] if e.get("content_id") == "alice_developer"), None)
    assert event is not None
    assert event["user_id"] == "alice_developer"
    assert event["content_type"] == "business_profile"
    assert event["decision"] == "allow"
    assert event["severity"] in ("low", "none")

    # Detail lookup by request_id
    detail_res = admin.get(f"/v1/admin/moderation-logs/{event['request_id']}")
    assert detail_res.status_code == 200
    detail = detail_res.get_json()
    assert detail["request_id"] == event["request_id"]
    assert "raw_result" in detail

    # Filter by surface
    filtered = admin.get("/v1/admin/moderation-logs?content_type=business_profile").get_json()
    assert any(e["content_id"] == "alice_developer" for e in filtered["logs"])

    # Search by user
    searched = admin.get("/v1/admin/moderation-logs?q=alice_developer").get_json()
    assert any(e["user_id"] == "alice_developer" for e in searched["logs"])

    # Clear logs
    del_res = admin.delete("/v1/admin/moderation-logs", headers=HDR)
    assert del_res.status_code == 200
    assert admin.get("/v1/admin/moderation-logs").get_json()["total"] == 0
