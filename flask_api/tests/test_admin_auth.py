"""Who may reach the admin endpoints, and the saved moderation settings.

There is no admin login on this service any more (admins sign in on BONC; see
test_bonc_auth.py for the .NET token). These tests use the ops token, X-Admin-Token.
"""
import pytest

from app import create_app
from app.admin_auth import ALL_SURFACES
from app.admin_store import AdminStore
from tests.conftest import FixedScorer

TOKEN = "secret"      # make_settings' admin_token


@pytest.fixture
def scorer():
    return FixedScorer(0.1)


@pytest.fixture
def app(make_settings, scorer, sink):
    return create_app(make_settings(), scorer=scorer, sink=sink, admin_store=AdminStore(":memory:"))


@pytest.fixture
def anon(app):
    return app.test_client()


@pytest.fixture
def admin(app):
    c = app.test_client()
    c.environ_base["HTTP_X_ADMIN_TOKEN"] = TOKEN
    return c


def moderate(c, ctype, text="hello there"):
    r = c.post("/v1/moderate", json={"content": text, "content_type": ctype})
    return r


# ---- endpoints are closed to everyone else ---------------------------------------------

def test_unauthenticated_cannot_use_admin_endpoints(anon):
    assert anon.get("/v1/admin/auth/me").status_code == 401
    assert anon.get("/v1/admin/moderation-settings").status_code == 401
    assert anon.put("/v1/admin/moderation-settings", json={"enabled": False, "level": "medium"}).status_code == 401
    assert anon.get("/v1/admin/moderation-levels").status_code == 401
    assert anon.put("/v1/admin/moderation-levels/article", json={"enabled": False}).status_code == 401


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
        assert admin.put("/v1/admin/moderation-settings", json=body).status_code == 400
    assert admin.get("/v1/admin/moderation-settings").get_json()["enabled"] is True


@pytest.mark.parametrize("ctype", ALL_SURFACES)
def test_off_publishes_and_on_moderates_every_surface(admin, scorer, ctype):
    scorer.risk = 0.99
    assert moderate(admin, ctype).get_json()["Data"]["decision"] == "reject"
    admin.put("/v1/admin/moderation-settings", json={"enabled": False, "level": "strict"})
    off = moderate(admin, ctype).get_json()["Data"]
    assert off["decision"] == "approved" and off.get("decided_by") == "disabled"
    admin.put("/v1/admin/moderation-settings", json={"enabled": True, "level": "strict"})
    assert moderate(admin, ctype).get_json()["Data"]["decision"] == "reject"


@pytest.mark.parametrize("ctype", ALL_SURFACES)
def test_each_level_is_enforced_on_every_surface(admin, scorer, ctype):
    def decide(level, risk):
        admin.put("/v1/admin/moderation-settings", json={"enabled": True, "level": level})
        scorer.risk = risk
        return moderate(admin, ctype).get_json()["Data"]["decision"]
    assert decide("lenient", 0.60) == "approved"       # lenient bar 0.85
    assert decide("medium", 0.60) == "reject"       # medium bar 0.50
    assert decide("medium", 0.40) == "approved"
    assert decide("strict", 0.40) == "reject"       # strict bar 0.25
    assert decide("lenient", 0.99) == "reject"      # even lenient refuses clear abuse


def test_off_does_not_switch_off_authentication_or_validation(admin, anon):
    admin.put("/v1/admin/moderation-settings", json={"enabled": False, "level": "medium"})
    assert anon.get("/v1/admin/moderation-settings").status_code == 401
    assert anon.post("/v1/moderate", json={"content": "", "content_type": "article"}).status_code == 400


def test_rejected_and_revise_are_never_allow_when_on(admin, scorer, make_settings):
    scorer.risk = 0.9
    assert moderate(admin, "article").get_json()["Data"]["decision"] in ("reject", "revise")
    r = moderate(admin, "article", "badword here").get_json()["Data"]      # the gate's own block rule
    assert r["decision"] == "reject"
    r = moderate(admin, "proposal", "you can pay in crypto").get_json()["Data"]   # the gate's revise rule
    assert r["decision"] in ("revise", "reject")


def test_moderator_failure_never_allows(make_settings, sink):
    class Boom:
        version = "boom"
        def score(self, text):
            raise RuntimeError("model down")
    app = create_app(make_settings(), scorer=Boom(), sink=sink, admin_store=AdminStore(":memory:"))
    r = moderate(app.test_client(), "article")
    assert r.status_code >= 500 and "allow" not in r.get_data(as_text=True).lower().replace("allowed", "")


def test_settings_survive_a_restart(make_settings, sink, tmp_path, scorer):
    db = tmp_path / "admin.db"
    mk = lambda: create_app(make_settings(), scorer=scorer, sink=sink, admin_store=AdminStore(db))
    c = mk().test_client()
    c.environ_base["HTTP_X_ADMIN_TOKEN"] = TOKEN
    c.put("/v1/admin/moderation-settings", json={"enabled": True, "level": "strict"})

    scorer.risk = 0.40
    assert moderate(mk().test_client(), "video").get_json()["Data"]["decision"] == "reject"   # strict still on
    c.put("/v1/admin/moderation-settings", json={"enabled": False, "level": "strict"})
    scorer.risk = 0.99
    assert moderate(mk().test_client(), "video").get_json()["Data"]["decision"] == "approved"    # OFF still off


def test_old_per_surface_endpoint_is_saved_too(make_settings, sink, tmp_path, scorer):
    db = tmp_path / "a.db"
    mk = lambda: create_app(make_settings(), scorer=scorer, sink=sink, admin_store=AdminStore(db))
    c = mk().test_client()
    assert c.put("/v1/admin/moderation-levels/article", json={"enabled": False},
                 headers={"X-Admin-Token": TOKEN}).status_code == 200
    scorer.risk = 0.99
    assert moderate(mk().test_client(), "article").get_json()["Data"]["decision"] == "approved"
    assert moderate(mk().test_client(), "video").get_json()["Data"]["decision"] == "reject"


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
    # "alice_developer" is this event's content_id, and a content id is not an author -
    # asserting it came back as user_id is what let the dashboard show one as the other.
    assert event["user_id"] == "anonymous"
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

    # Free-text search covers content_id as well as user_id, so the event is still found
    # by this term - as its content_id, which is what it actually is.
    searched = admin.get("/v1/admin/moderation-logs?q=alice_developer").get_json()
    assert any(e["content_id"] == "alice_developer" for e in searched["logs"])

    # Clear logs
    del_res = admin.delete("/v1/admin/moderation-logs")
    assert del_res.status_code == 200
    assert admin.get("/v1/admin/moderation-logs").get_json()["total"] == 0


def test_a_wrong_admin_token_is_refused(anon):
    assert anon.get("/v1/admin/moderation-settings",
                    headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_admin_endpoints_are_disabled_with_no_way_in(make_settings, scorer, sink):
    """No BONC verifier and no ADMIN_TOKEN: nothing could ever be accepted, so say disabled."""
    app = create_app(make_settings(admin_token=None), scorer=scorer, sink=sink,
                     admin_store=AdminStore(":memory:"))
    r = app.test_client().get("/v1/admin/moderation-settings")
    assert r.status_code == 404 and "BONC_AUTH_VERIFY_URL" in r.get_json()["Message"]


def test_me_says_how_the_caller_got_in(admin):
    assert admin.get("/v1/admin/auth/me").get_json()["via"] == "admin_token"


def test_the_old_login_routes_are_gone(anon):
    for path in ("/v1/admin/auth/login", "/v1/admin/auth/logout",
                 "/v1/admin/auth/password/forgot", "/v1/admin/auth/password/verify",
                 "/v1/admin/auth/password/reset"):
        assert anon.post(path, json={}).status_code in (404, 405), path
