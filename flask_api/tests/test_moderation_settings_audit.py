"""An audit of the moderation-settings paths, kept as tests.

These were written by going through the settings endpoints looking for defects rather than to
cover a change, so each one states what it is checking for and why it would matter. Where the
behaviour turned out to be correct the test stays, because that is what stops it regressing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.admin_auth import restore_levels  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.levels import ModerationLevels  # noqa: E402

EMAIL, PW = "settings_audit@example.com", "AdminTestPass123"
HDR = {"X-Requested-With": "bonc-admin"}


@pytest.fixture
def app():
    a = create_app(load_settings())
    a.extensions["admin"].store.create_admin(EMAIL, "scrypt:32768:8:1$dummy$dummy")
    a.extensions["admin"].store.set_password(EMAIL, generate_password_hash(PW))
    return a


@pytest.fixture
def client(app):
    c = app.test_client()
    assert c.post("/v1/admin/auth/login", json={"email": EMAIL, "password": PW}).status_code == 200
    return c


def set_page(client, ct, **body):
    return client.put(f"/v1/admin/moderation-levels/{ct}", json=body, headers=HDR)


def set_all(client, **body):
    return client.put("/v1/admin/moderation-settings", json=body, headers=HDR)


# ---- what a restart keeps ---------------------------------------------------------------

def test_a_named_level_chosen_after_a_raw_number_survives_a_restart(app, client):
    """`remember` merges each change into one record per surface and never clears a field, so
    a surface set to 0.62 and then to `strict` is stored carrying BOTH. restore_levels replays
    them together, and set_feature applies the level first and the number second - a raw
    reject_min always wins - so the restart would come up at 0.62 and the admin's last choice
    would be silently undone.
    """
    set_page(client, "article", reject_min=0.62)
    set_page(client, "article", level="strict")
    live = app.extensions["moderation"].levels.for_type("article")
    assert live.level == "strict"

    fresh = ModerationLevels.from_yaml(app.extensions["moderation"].levels.source)
    restore_levels(fresh, app.extensions["admin"].store)
    after = fresh.for_type("article")
    assert after.level == "strict", (
        f"a restart reverted the admin's last choice to {after.thresholds}")
    assert after.thresholds == live.thresholds


def test_a_raw_number_chosen_after_a_named_level_survives_a_restart(app, client):
    """The same round trip the other way, which has always worked - a number wins over a
    level in both places - so it pins the asymmetry rather than assuming it."""
    set_page(client, "video", level="strict")
    set_page(client, "video", reject_min=0.62)
    live = app.extensions["moderation"].levels.for_type("video")

    fresh = ModerationLevels.from_yaml(app.extensions["moderation"].levels.source)
    restore_levels(fresh, app.extensions["admin"].store)
    assert fresh.for_type("video").thresholds == live.thresholds


def test_switching_a_surface_off_and_on_keeps_its_level(client, app):
    """A disabled feature keeps its bar so the toggle is lossless."""
    set_page(client, "request", level="strict")
    set_page(client, "request", enabled=False)
    set_page(client, "request", enabled=True)
    p = app.extensions["moderation"].levels.for_type("request")
    assert p.enabled is True and p.level == "strict"


# ---- what the endpoints refuse -----------------------------------------------------------

@pytest.mark.parametrize("bad", [-0.1, 1.5, "abc", [0.5]])
def test_a_rating_outside_0_to_1_is_refused(client, bad):
    assert set_page(client, "proposal", reject_min=bad).status_code == 400


@pytest.mark.parametrize("ok", [0, 1, 0.5])
def test_the_ends_of_the_range_are_accepted(client, ok):
    assert set_page(client, "proposal", reject_min=ok).status_code == 200


def test_an_unknown_level_is_refused(client):
    assert set_page(client, "proposal", level="super_strict").status_code == 400


def test_an_unknown_surface_is_refused(client):
    """A typo must not create a setting that looks saved and is consulted by nothing."""
    assert set_page(client, "articles", level="strict").status_code == 404


def test_an_unknown_field_is_refused(client):
    assert set_page(client, "article", lvl="strict").status_code == 400


def test_an_empty_body_is_refused(client):
    assert set_page(client, "article").status_code == 400


@pytest.mark.parametrize("body", [{"enabled": True}, {"level": "strict"},
                                  {"enabled": "yes", "level": "strict"},
                                  {"enabled": True, "level": "nope"}])
def test_the_all_at_once_endpoint_needs_both_fields_and_a_real_level(client, body):
    assert set_all(client, **body).status_code == 400


# ---- the config file ---------------------------------------------------------------------

def test_a_surface_the_pipeline_does_not_know_is_kept_out_of_the_table(tmp_path):
    """An entry for `articles` (the plural) is consulted by nothing - for_type is only ever
    called with a real ContentType - so it reads as a configured surface while moderating
    nothing. The endpoint rejects it; the file has to say something about it too."""
    import yaml
    p = tmp_path / "levels.yaml"
    p.write_text(yaml.safe_dump({
        "version": 1,
        "features": {"articles": {"enabled": False, "level": "strict"},
                     "article": {"enabled": True, "level": "strict"}}}), encoding="utf-8")
    ml = ModerationLevels.from_yaml(p)
    assert ml.for_type("article").level == "strict"
    assert "articles" not in ml.as_dict()["features"], \
        "a misspelled surface was loaded and will moderate nothing"


def test_a_malformed_level_does_not_take_the_whole_file_down(tmp_path):
    """A config problem must never be the reason content stops being checked."""
    import yaml
    p = tmp_path / "levels.yaml"
    p.write_text(yaml.safe_dump({
        "version": 1,
        "levels": {"broken": {"allow_max": "nonsense"}},
        "features": {"article": {"level": "strict"}}}), encoding="utf-8")
    ml = ModerationLevels.from_yaml(p)
    assert ml.for_type("article").level == "strict"
    assert ml.for_type("video").enabled is True


def test_a_missing_file_moderates_everything_rather_than_nothing(tmp_path):
    ml = ModerationLevels.from_yaml(tmp_path / "nope.yaml")
    assert ml.for_type("article").enabled is True
