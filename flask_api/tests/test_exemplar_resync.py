"""Changing a marked picture's verdict in the grid must move the LIVE lookalike rule with it.

THE BUG THIS COMES FROM. An admin marked a picture "refuse like this", changed their mind,
switched the card to ALLOW, and saved. The card relabelled itself "Live rule: allowing
lookalikes" and the database agreed - and the next upload that looked like it was refused
anyway, with no rule in visual_policy.yaml naming anything of the sort.

An exemplar's direction is not stored next to the mark. It IS `image_policy.action`, the same
column the grid's Allow/Block toggle writes, and the live check reads it from a baked
exemplars.npz rather than the database. Only the two endpoints that MARK exemplars re-exported
that file, so the grid's own toggle, the category ALLOW ALL / BLOCK ALL buttons, and a batch
that moves a picture between categories all wrote the column and left the file alone. The card
was never lying about the database - it was reading the column that had changed, printed next
to a file that had not.

The category half matters for the same reason: a withdrawn category silences its exemplars
(see test_withdrawn_categories), and that test is on the category baked into the npz.
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import numpy as np
import pytest
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.system_store import SystemStore  # noqa: E402

EMAIL, PW = "exemplar_resync@example.com", "AdminTestPass123"
HDR = {"X-Requested-With": "bonc-admin"}


def _vec(*xs) -> bytes:
    """A unit embedding, stored exactly as compute_embedding stores one."""
    v = np.asarray(xs, dtype=np.float32)
    return (v / np.linalg.norm(v)).astype(np.float32).tobytes()


@pytest.fixture
def app(tmp_path):
    store = SystemStore(path=tmp_path / "resync.db", images_dir=tmp_path / "no-images")
    app = create_app(load_settings(), system_store=store)

    # The export writes models/img_v1/exemplars.npz and reloads it into the live matcher.
    # Pointed at tmp_path so the test cannot rewrite the shipped bundle, which is the one
    # the rest of the suite scores against.
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    visual = app.extensions["moderation"].settings.visual
    # VisualConfig is frozen and VisualCheck.model_dir is derived from it, so the redirect
    # goes through a replaced config. Absolute, so it is not resolved against visual.root.
    visual.config = dataclasses.replace(visual.config, model_dir=str(model_dir))

    admin = app.extensions["admin"]
    admin.store.create_admin(EMAIL, "scrypt:32768:8:1$dummy$dummy")
    admin.store.set_password(EMAIL, generate_password_hash(PW))
    app.config["_model_dir"] = model_dir
    return app


@pytest.fixture
def client(app):
    c = app.test_client()
    assert c.post("/v1/admin/auth/login", json={"email": EMAIL, "password": PW}).status_code == 200
    return c


@pytest.fixture
def marked(app, client):
    """One picture, marked "refuse things like this" through the endpoint that marks them."""
    store = app.extensions["system_store"]
    row = store.add_image(filename="rainbow.jpg", file_path="data/uploads/rainbow.jpg",
                          category="safe", action="block", is_selected=1,
                          embedding=_vec(1.0, 0.0, 0.0))
    res = client.put("/v1/admin/policy/exemplars",
                     json={"ids": [row["id"]], "mode": "block"}, headers=HDR)
    assert res.status_code == 200, res.get_json()
    assert _live(app)["actions"] == ["block"]
    return row["id"]


def _live(app) -> dict:
    """What the live check would match against: the baked file, not the database."""
    p = app.config["_model_dir"] / "exemplars.npz"
    assert p.exists(), "nothing was ever exported"
    with np.load(p, allow_pickle=False) as d:
        return {"actions": [str(a) for a in d["actions"]],
                "categories": [str(c) for c in d["categories"]],
                "ids": [int(i) for i in d["ids"]]}


# ---- the bug itself ------------------------------------------------------------------

def test_switching_a_marked_card_to_allow_moves_the_live_rule(app, client, marked):
    """The reported bug, in one assertion. Refusing -> allowing, through the grid's own
    toggle and Submit Changes, must reach the file the live check reads."""
    res = client.put("/v1/admin/images/batch",
                     json={"updates": [{"id": marked, "action": "allow"}]}, headers=HDR)
    assert res.status_code == 200, res.get_json()
    assert _live(app)["actions"] == ["allow"]


def test_the_response_says_the_live_rule_moved(app, client, marked):
    """Everything else in the grid takes effect on the next RETRAIN. This one takes effect on
    the next upload, so saying so is the difference between the admin testing it now and
    waiting for a retrain that was never needed."""
    res = client.put("/v1/admin/images/batch",
                     json={"updates": [{"id": marked, "action": "allow"}]}, headers=HDR)
    body = res.get_json()
    assert body["exemplars"]["allowing"] == 1
    assert body["exemplars"]["refusing"] == 0
    assert "lookalike" in body["message"].lower()


def test_category_allow_all_moves_its_marked_pictures(app, client, marked):
    """ALLOW ALL on a category goes through a different endpoint and had the same hole."""
    res = client.put("/v1/admin/images/categories/safe", json={"action": "allow"}, headers=HDR)
    assert res.status_code == 200, res.get_json()
    assert _live(app)["actions"] == ["allow"]


def test_bulk_action_on_the_batch_endpoint_moves_its_marked_pictures(app, client, marked):
    res = client.put("/v1/admin/images/batch",
                     json={"bulk_action": "allow", "category": "safe"}, headers=HDR)
    assert res.status_code == 200, res.get_json()
    assert _live(app)["actions"] == ["allow"]


def test_moving_a_marked_picture_to_another_category_is_rebaked(app, client, marked):
    """The baked category is what the withdrawn-category check tests, so a stale one means a
    mark that cannot be silenced by withdrawing the category it now sits in."""
    res = client.put("/v1/admin/images/batch",
                     json={"updates": [{"id": marked, "category": "weapon"}]}, headers=HDR)
    assert res.status_code == 200, res.get_json()
    assert _live(app)["categories"] == ["weapon"]


# ---- and must not fire for everything else -------------------------------------------

def test_saving_train_checkboxes_does_not_rebake(app, client, marked):
    """`is_selected` is the next retrain's business. Re-exporting on it would reload the live
    matcher on every checkbox in a 24-card grid for a change it cannot see."""
    before = (app.config["_model_dir"] / "exemplars.npz").stat().st_mtime_ns
    res = client.put("/v1/admin/images/batch",
                     json={"updates": [{"id": marked, "is_selected": 0}]}, headers=HDR)
    assert res.status_code == 200
    assert res.get_json()["exemplars"] is None
    assert (app.config["_model_dir"] / "exemplars.npz").stat().st_mtime_ns == before


def test_changing_an_unmarked_picture_does_not_rebake(app, client, marked):
    """Flipping an ordinary picture to ALLOW says nothing about any live rule."""
    store = app.extensions["system_store"]
    other = store.add_image(filename="desk.jpg", file_path="data/uploads/desk.jpg",
                            category="safe", action="block", is_selected=1,
                            embedding=_vec(0.0, 1.0, 0.0))
    res = client.put("/v1/admin/images/batch",
                     json={"updates": [{"id": other["id"], "action": "allow"}]}, headers=HDR)
    assert res.status_code == 200
    assert res.get_json()["exemplars"] is None
    assert _live(app)["actions"] == ["block"]
