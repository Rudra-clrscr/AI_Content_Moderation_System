"""Image Policy Rules and Image Fine-Tuning are two views of ONE list of subjects.

THE BUG THIS COMES FROM. A rule named `toy` scores the pictures filed under the category `toy`
(app/modra_image.policy_category relies on the folder names being the label names), yet the two
screens kept separate lists and never told each other anything:

  * a subject created on one screen could be created again on the other, and "New rule" with
    an existing rule's name silently replaced that rule's phrasings;
  * "Stop refusing" changed the rule and left its pictures marked BLOCK, and ALLOW ALL changed
    the pictures and left the rule refusing - on the live data, the `weapon` rule refused
    while all 500 of its pictures were marked ALLOW, so the next retrain would have taught the
    probe that weapons are safe.

Also covers "Test an image", which runs one picture through the live visual decision.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.system_store import SystemStore  # noqa: E402

TOKEN = "test-admin-token"   # the ADMIN_TOKEN these tests run under
POLICY = ROOT / "config" / "visual_policy.yaml"
ENCODER = ROOT / "models" / "img_v1" / "text_model.onnx"
needs_encoder = pytest.mark.skipif(
    not ENCODER.exists(), reason="rebuilding prompts.npz needs the 254 MB text encoder")


@pytest.fixture
def app(tmp_path):
    store = SystemStore(path=tmp_path / "sync.db", images_dir=tmp_path / "no-images")
    app = create_app(load_settings(), system_store=store)
    app.extensions["moderation"].settings.admin_token = TOKEN
    return app


@pytest.fixture
def client(app):
    c = app.test_client()
    c.environ_base["HTTP_X_ADMIN_TOKEN"] = TOKEN
    return c


def _rules() -> dict:
    return yaml.safe_load(POLICY.read_text(encoding="utf-8")).get("labels") or {}


def _png(color=(200, 200, 200)) -> io.BytesIO:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (96, 72), color).save(buf, "PNG")
    buf.seek(0)
    return buf


# ---- names ---------------------------------------------------------------------------

def test_new_rule_does_not_replace_an_existing_rule(client):
    name = sorted(_rules())[0]
    before = POLICY.read_bytes()
    r = client.put(f"/v1/admin/policy/labels/{name}",
                   json={"create": True, "unsafe": True, "prompts": ["a photo of something"]})
    assert r.status_code == 409
    assert r.get_json()["Data"]["code"] == "rule_exists"
    assert POLICY.read_bytes() == before


def test_new_category_refuses_the_name_of_a_rule(client, app):
    name = next(iter(sorted(_rules())))
    r = client.post("/v1/admin/images/categories",
                    json={"name": name, "default_action": "allow"})
    assert r.status_code == 409
    assert r.get_json()["Data"]["code"] == "category_exists"
    # It is listed as the category it already is, not left missing.
    assert app.extensions["system_store"].get_category(name) is not None


def test_new_rule_on_an_existing_category_asks_first(client, app):
    app.extensions["system_store"].add_category("kite_flying", "Kite flying", "block")
    before = POLICY.read_bytes()
    r = client.put("/v1/admin/policy/labels/kite_flying",
                   json={"create": True, "unsafe": True, "prompts": ["a photo of a kite"]})
    assert r.status_code == 409
    d = r.get_json()["Data"]
    assert d["code"] == "category_exists" and d["category"]["name"] == "kite_flying"
    assert POLICY.read_bytes() == before


# ---- verdicts ------------------------------------------------------------------------

def test_disagreement_is_reported_and_can_be_settled_toward_the_rule(client, app):
    store = app.extensions["system_store"]
    refusing = [n for n, e in _rules().items() if (e or {}).get("unsafe", True)
                and store.get_category(n) is not None]
    if not refusing:
        pytest.skip("no refusing rule shares a name with a default category")
    name = refusing[0]
    store.set_category_policy(name, "allow")          # the pictures, behind the rule's back

    s = client.get("/v1/admin/policy/sync").get_json()
    assert name in s["disagreeing"]

    r = client.post(f"/v1/admin/policy/sync/{name}", json={"keep": "rule"})
    assert r.status_code == 200, r.get_json()
    assert store.get_category(name)["default_action"] == "block"
    assert name not in r.get_json()["sync"]["disagreeing"]


@needs_encoder
def test_rule_and_pictures_follow_each_other(client, app):
    store = app.extensions["system_store"]
    r = client.put("/v1/admin/policy/labels/kite_flying",
                   json={"create": True, "unsafe": True,
                         "prompts": ["a photo of a kite flying in the sky"]})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["fine_tuning"]["created"] is True
    assert store.get_category("kite_flying")["default_action"] == "block"

    # ALLOW ALL on the Fine-Tuning screen turns the rule to allow.
    r = client.put("/v1/admin/images/categories/kite_flying", json={"action": "allow"})
    assert r.status_code == 200
    assert r.get_json()["policy_rule"]["changed"] is True
    assert _rules()["kite_flying"]["unsafe"] is False

    # And Start refusing on the rule moves the category back.
    r = client.put("/v1/admin/policy/labels/kite_flying",
                   json={"unsafe": True, "prompts": ["a photo of a kite flying in the sky"]})
    assert r.status_code == 200
    assert store.get_category("kite_flying")["default_action"] == "block"


# ---- test an image -------------------------------------------------------------------

def test_test_image_needs_a_file(client):
    assert client.post("/v1/admin/policy/test-image", data={},
                       content_type="multipart/form-data").status_code == 400


def test_test_image_rejects_a_file_that_is_not_a_picture(client, app):
    if not app.extensions["moderation"].settings.visual.ready:
        pytest.skip("visual model not installed")
    r = client.post("/v1/admin/policy/test-image",
                    data={"file": (io.BytesIO(b"not an image at all"), "x.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 422


def test_test_image_reports_the_live_decision(client, app):
    visual = app.extensions["moderation"].settings.visual
    if not visual.ready:
        pytest.skip("visual model not installed")
    log_rows = app.extensions["admin"].store.count_moderation_logs()
    r = client.post("/v1/admin/policy/test-image",
                    data={"file": (_png(), "grey.png")}, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["verdict"] in ("refuse", "publish")
    assert {l["name"] for l in d["labels"]} == set(visual._labels)
    # The same verdict the upload path reaches, and nothing written to the audit log.
    from PIL import Image
    same = visual.decides_reject(visual.classify(Image.open(_png())))
    assert (d["verdict"] == "refuse") == bool(same)
    assert app.extensions["admin"].store.count_moderation_logs() == log_rows
