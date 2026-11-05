"""Setting post, article and job to different levels in one call.

`PUT /v1/admin/moderation-settings` used to take only `{enabled, level}` and write it to every
surface. That was the one call that changed more than one surface, so an admin who had set
post, article and job apart and then used it lost all three: the live server was found with
every surface at `level_8` and `mixed: false`, which is exactly what that call leaves behind.

It now also takes `surfaces`, which changes only the surfaces it names.
"""
import io
from pathlib import Path

import pytest
import yaml
from werkzeug.security import generate_password_hash

from app import create_app
from app.admin_auth import ALL_SURFACES
from app.admin_store import AdminStore
from tests.conftest import FixedScorer

EMAIL, PW = "levels@example.com", "Correct-horse-9"
HDR = {"X-Requested-With": "bonc-admin"}
SPEC = Path(__file__).resolve().parents[2] / "docs" / "openapi.yaml"


@pytest.fixture
def scorer():
    return FixedScorer(0.1)


def make_app(make_settings, scorer, sink, store):
    app = create_app(make_settings(admin_token=None), scorer=scorer, sink=sink,
                     admin_store=store, mail_async=False)
    if not store.get_admin(EMAIL):
        store.create_admin(EMAIL, generate_password_hash(PW))
    return app


def login(app):
    c = app.test_client()
    assert c.post("/v1/admin/auth/login", json={"email": EMAIL, "password": PW}).status_code == 200
    return c


@pytest.fixture
def app(make_settings, scorer, sink):
    return make_app(make_settings, scorer, sink, AdminStore(":memory:"))


@pytest.fixture
def admin(app):
    return login(app)


def put(c, body):
    return c.put("/v1/admin/moderation-settings", json=body, headers=HDR)


def view(c):
    return c.get("/v1/admin/moderation-settings").get_json()


def decision(c, ctype):
    r = c.post("/v1/moderate", json={"content": "hello there", "content_type": ctype})
    return r.get_json()["Data"]["decision"]


THREE = {"post": "lenient", "article": "medium", "job": "strict"}


# ---- the request this exists for ---------------------------------------------------------

def test_three_surfaces_get_three_levels(admin):
    r = put(admin, {"surfaces": THREE})
    assert r.status_code == 200, r.get_data(as_text=True)
    surfaces = r.get_json()["surfaces"]
    assert {ct: surfaces[ct]["level"] for ct in THREE} == THREE
    assert r.get_json()["mixed"] is True
    assert r.get_json() == view(admin)            # the reply is what the GET says, not a guess


def test_each_level_is_the_one_moderation_actually_uses(admin, scorer):
    put(admin, {"surfaces": THREE})
    scorer.risk = 0.60                             # under lenient 0.85, over medium 0.50
    assert decision(admin, "post") == "approved"
    assert decision(admin, "article") == "reject"
    scorer.risk = 0.40                             # under medium 0.50, over strict 0.25
    assert decision(admin, "article") == "approved"
    assert decision(admin, "job") == "reject"


def test_surfaces_not_named_are_left_alone(admin):
    """The whole point of the new shape: it is not the everything-setting."""
    put(admin, {"enabled": True, "level": "level_8"})
    put(admin, {"surfaces": {"post": "lenient"}})
    surfaces = view(admin)["surfaces"]
    assert surfaces["post"]["level"] == "lenient"
    assert all(surfaces[ct]["level"] == "level_8" for ct in ALL_SURFACES if ct != "post")


def test_a_second_call_does_not_undo_the_first(admin):
    put(admin, {"surfaces": {"post": "lenient"}})
    put(admin, {"surfaces": {"job": "strict"}})
    surfaces = view(admin)["surfaces"]
    assert surfaces["post"]["level"] == "lenient"
    assert surfaces["job"]["level"] == "strict"


def test_everything_then_the_named_ones_on_top(admin):
    r = put(admin, {"enabled": True, "level": "strict", "surfaces": {"post": "lenient"}})
    surfaces = r.get_json()["surfaces"]
    assert surfaces["post"]["level"] == "lenient"
    assert all(surfaces[ct]["level"] == "strict" for ct in ALL_SURFACES if ct != "post")


def test_the_object_form_can_switch_one_surface_off_and_keep_its_level(admin, scorer):
    put(admin, {"surfaces": {"job": "strict"}})
    r = put(admin, {"surfaces": {"job": {"enabled": False}}})
    assert r.get_json()["surfaces"]["job"] == {"enabled": False, "level": "strict"}
    scorer.risk = 0.99
    assert decision(admin, "job") == "approved"    # off means not moderated
    assert decision(admin, "post") == "reject"     # and only job is off
    put(admin, {"surfaces": {"job": {"enabled": True}}})
    assert view(admin)["surfaces"]["job"]["level"] == "strict"


def test_a_level_name_alone_leaves_the_toggle_as_it_was(admin):
    """Same as the per-surface PUT: `{"level": ...}` there does not switch a surface on."""
    put(admin, {"surfaces": {"video": {"enabled": False}}})
    put(admin, {"surfaces": {"video": "lenient"}})
    assert view(admin)["surfaces"]["video"]["enabled"] is False


def test_it_survives_a_restart(make_settings, scorer, sink, tmp_path):
    db = tmp_path / "admin.db"
    put(login(make_app(make_settings, scorer, sink, AdminStore(db))), {"surfaces": THREE})
    again = login(make_app(make_settings, scorer, sink, AdminStore(db)))    # "restart"
    surfaces = view(again)["surfaces"]
    assert {ct: surfaces[ct]["level"] for ct in THREE} == THREE


def test_a_raw_number_set_per_surface_is_replaced_not_kept_beside_the_level(
        make_settings, scorer, sink, tmp_path):
    """The saved record must not carry the old 0.62 next to the new name, or the restart
    replays both and the number wins (see `remember`)."""
    db = tmp_path / "admin.db"
    c = login(make_app(make_settings, scorer, sink, AdminStore(db)))
    c.put("/v1/admin/moderation-levels/article", json={"reject_min": 0.62}, headers=HDR)
    put(c, {"surfaces": {"article": "strict"}})
    again = login(make_app(make_settings, scorer, sink, AdminStore(db)))
    assert view(again)["surfaces"]["article"]["level"] == "strict"


# ---- what is refused, and that refusing changes nothing ----------------------------------

@pytest.mark.parametrize("body,code", [
    ({"surfaces": {}}, "invalid_request"),
    ({"surfaces": None}, "invalid_request"),
    ({"surfaces": ["post"]}, "invalid_request"),
    ({"surfaces": {"post": "super_strict"}}, "invalid_request"),
    ({"surfaces": {"post": {}}}, "invalid_request"),
    ({"surfaces": {"post": {"level": "strict", "reject_min": 0.6}}}, "invalid_request"),
    ({"surfaces": {"post": {"enabled": "no"}}}, "invalid_request"),
    ({"surfaces": {"post": 0.5}}, "invalid_request"),
    ({"surfaces": {"articles": "strict"}}, "no_such_surface"),
    ({"level": "strict", "surfaces": {"post": "lenient"}}, "invalid_request"),
    ({"surfaces": {"post": "lenient"}, "extra": 1}, "invalid_request"),
])
def test_a_bad_body_is_refused(admin, body, code):
    r = put(admin, body)
    assert r.status_code == 400
    assert r.get_json()["Data"]["code"] == code


def test_one_bad_entry_refuses_the_good_ones_too(admin):
    """Checked whole before anything is applied: no half-saved request."""
    before = view(admin)
    r = put(admin, {"surfaces": {"post": "lenient", "articles": "strict", "job": "strict"}})
    assert r.status_code == 400
    assert r.get_json()["Data"]["surfaces"] == ["articles"]
    assert view(admin) == before


def test_a_failed_save_puts_every_surface_back_exactly(app, admin, monkeypatch):
    """Including a raw rating, which the old rollback - replaying the saved record's level -
    could not put back."""
    admin.put("/v1/admin/moderation-levels/article", json={"reject_min": 0.62}, headers=HDR)
    put(admin, {"surfaces": {"post": "lenient"}})
    before = view(admin)

    def boom(_data):
        raise OSError("disk full")
    monkeypatch.setattr(app.extensions["admin"].store, "save_settings", boom)
    for body in ({"surfaces": THREE}, {"enabled": False, "level": "strict"}):
        r = put(admin, body)
        assert r.status_code == 500 and r.get_json()["Data"]["code"] == "save_failed"
        assert view(admin) == before


# ---- the Swagger page ----------------------------------------------------------------------

def _examples():
    spec = yaml.safe_load(io.open(SPEC, encoding="utf-8"))
    put_op = spec["paths"]["/v1/admin/moderation-settings"]["put"]
    return put_op["requestBody"]["content"]["application/json"]["examples"]


@pytest.mark.parametrize("name", list(_examples()))
def test_every_example_on_the_page_is_accepted(admin, name):
    assert put(admin, _examples()[name]["value"]).status_code == 200


def test_the_example_try_it_out_starts_with_changes_only_the_surfaces_it_names():
    """Swagger UI fills the body with the first example. If that were the everything-setting,
    clicking Execute without reading would flatten every surface - the original problem."""
    first = next(iter(_examples().values()))["value"]
    assert set(first) == {"surfaces"}
