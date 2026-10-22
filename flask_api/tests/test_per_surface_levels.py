"""A moderation level per page, reachable from the dashboard.

The server has taken a level per content type from the start: `/v1/admin/moderation-levels`
reads the table, `PUT /v1/admin/moderation-levels/<content_type>` changes one surface, and
`_settings_view` has always returned a `surfaces` block. Nothing in the dashboard called any
of it - the only control was the all-at-once endpoint - so per-page settings existed and
could be reached by nothing but a hand-edited YAML file.

These pin the behaviour the per-page UI relies on, over HTTP and as an admin:

  * one page can be set without disturbing the others
  * the all-at-once save deliberately overwrites them, which is why the UI sends it FIRST
  * `surfaces` reports what each page is actually at, and `mixed` says whether they agree
  * a page can be switched off on its own

The order one is the one worth keeping: sending the per-page changes before the
everything-change silently discarded whichever page the admin had just set.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.config import load_settings  # noqa: E402

EMAIL, PW = "levels_admin@example.com", "AdminTestPass123"


@pytest.fixture
def client(tmp_path):
    settings = load_settings()
    app = create_app(settings)
    admin = app.extensions["admin"]
    admin.store.create_admin(EMAIL, "scrypt:32768:8:1$dummy$dummy")
    admin.store.set_password(EMAIL, generate_password_hash(PW))
    c = app.test_client()
    assert c.post("/v1/admin/auth/login", json={"email": EMAIL, "password": PW}).status_code == 200
    return c


def settings_view(c) -> dict:
    res = c.get("/v1/admin/moderation-settings")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()


def set_page(c, ct, **body):
    return c.put(f"/v1/admin/moderation-levels/{ct}", json=body,
                 headers={"X-Requested-With": "bonc-admin"})


def set_everything(c, **body):
    return c.put("/v1/admin/moderation-settings", json=body,
                 headers={"X-Requested-With": "bonc-admin"})


# ---- one page at a time ---------------------------------------------------------------

def test_setting_one_page_leaves_the_rest_alone(client):
    assert set_everything(client, enabled=True, level="medium").status_code == 200
    assert set_page(client, "article", level="strict").status_code == 200

    surfaces = settings_view(client)["surfaces"]
    assert surfaces["article"]["level"] == "strict"
    for other in ("video", "request", "proposal", "business_profile"):
        assert surfaces[other]["level"] == "medium", other


def test_the_view_reports_that_pages_disagree(client):
    set_everything(client, enabled=True, level="medium")
    assert settings_view(client)["mixed"] is False
    set_page(client, "article", level="strict")
    view = settings_view(client)
    assert view["mixed"] is True
    # With pages disagreeing there is no single level to show, and showing one of them would
    # misreport the other eight.
    assert view["level"] is None


def test_a_single_page_can_be_switched_off(client):
    set_everything(client, enabled=True, level="medium")
    assert set_page(client, "video", enabled=False).status_code == 200
    surfaces = settings_view(client)["surfaces"]
    assert surfaces["video"]["enabled"] is False
    assert surfaces["article"]["enabled"] is True
    assert settings_view(client)["enabled"] is False     # not every page is on any more


def test_a_level_set_while_a_page_is_off_is_kept_for_when_it_returns(client):
    """Switching off and on again must be lossless, or an admin's level quietly disappears."""
    set_everything(client, enabled=True, level="medium")
    set_page(client, "request", level="strict")
    set_page(client, "request", enabled=False)
    set_page(client, "request", enabled=True)
    assert settings_view(client)["surfaces"]["request"]["level"] == "strict"


# ---- the ordering the UI depends on ---------------------------------------------------

def test_the_everything_save_overwrites_the_per_page_ones(client):
    """Not a bug - "every page" means every page. It is the reason the dashboard sends the
    everything-change FIRST and the per-page ones after; the other order threw away whichever
    page the admin had just set."""
    set_page(client, "article", level="strict")
    set_everything(client, enabled=True, level="lenient")
    assert settings_view(client)["surfaces"]["article"]["level"] == "lenient"


def test_everything_then_pages_keeps_both(client):
    """The order the dashboard actually sends, and the result an admin expects from it."""
    set_everything(client, enabled=True, level="lenient")
    set_page(client, "article", level="strict")
    set_page(client, "video", enabled=False)

    surfaces = settings_view(client)["surfaces"]
    assert surfaces["article"]["level"] == "strict"
    assert surfaces["video"]["enabled"] is False
    assert surfaces["proposal"]["level"] == "lenient"


# ---- what the chips show --------------------------------------------------------------

def test_every_dashboard_surface_is_reported(client):
    """The chips are built from this list. A surface missing here is a page whose setting
    nobody can see or reach, which is the state the whole feature is fixing."""
    surfaces = settings_view(client)["surfaces"]
    for ct in ("business_profile", "video", "article", "request", "proposal",
               "business_proposal", "post", "advertisement", "product_listing"):
        assert ct in surfaces, ct


def test_an_unknown_page_is_refused_rather_than_silently_stored(client):
    """A typo must not create a setting that looks saved and moderates nothing."""
    res = set_page(client, "articles", level="strict")   # note the plural
    assert res.status_code in (400, 404), res.get_data(as_text=True)
    assert "articles" not in settings_view(client)["surfaces"]


def test_an_unknown_level_is_refused(client):
    res = set_page(client, "article", level="very_strict")
    assert res.status_code == 400
    assert settings_view(client)["surfaces"]["article"].get("level") != "very_strict"
