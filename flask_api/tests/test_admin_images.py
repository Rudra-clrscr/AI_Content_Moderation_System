"""Tests for image policy database, category management, batch updates, and fine-tuning."""
import io
import json
import pytest
from pathlib import Path

from app import create_app
from app.config import load_settings
from app.system_store import SystemStore

EMAIL, PW = "test_image_admin@example.com", "AdminTestPass123"
HDR = {"X-Requested-With": "bonc-admin"}


@pytest.fixture
def test_app(tmp_path):
    settings = load_settings()
    db_path = tmp_path / "test_system.db"
    store = SystemStore(path=db_path)

    app = create_app(settings, system_store=store)
    admin = app.extensions["admin"]
    admin.store.create_admin(EMAIL, "scrypt:32768:8:1$dummy$dummy")
    # Set known password
    from werkzeug.security import generate_password_hash
    admin.store.set_password(EMAIL, generate_password_hash(PW))
    return app


@pytest.fixture
def client(test_app):
    c = test_app.test_client()
    login_res = c.post("/v1/admin/auth/login", json={"email": EMAIL, "password": PW})
    assert login_res.status_code == 200
    return c


def test_list_categories(client):
    res = client.get("/v1/admin/images/categories")
    assert res.status_code == 200
    data = res.get_json()
    assert "categories" in data
    cats = {c["name"] for c in data["categories"]}
    assert "minor" in cats
    assert "safe" in cats
    assert "weapon" in cats
    assert "child_products" in cats


def test_add_custom_category(client):
    res = client.post("/v1/admin/images/categories", json={
        "name": "pediatric_health",
        "display_name": "Pediatric Health & Care",
        "default_action": "allow",
        "description": "Healthcare products for infants and children"
    }, headers=HDR)
    assert res.status_code == 201
    cat = res.get_json()["category"]
    assert cat["name"] == "pediatric_health"
    assert cat["default_action"] == "allow"

    # Verify category appears in list
    cats_res = client.get("/v1/admin/images/categories")
    names = [c["name"] for c in cats_res.get_json()["categories"]]
    assert "pediatric_health" in names


def test_list_images_and_filtering(client):
    res = client.get("/v1/admin/images?limit=10")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["items"]) <= 10
    assert "stats" in data
    assert data["stats"]["total"] >= 10

    # Filter by category
    res_minor = client.get("/v1/admin/images?category=minor")
    assert res_minor.status_code == 200
    data_minor = res_minor.get_json()
    for item in data_minor["items"]:
        assert item["category"] == "minor"


def test_batch_update_images(client):
    list_res = client.get("/v1/admin/images?limit=5")
    items = list_res.get_json()["items"]
    assert len(items) >= 2

    id1, id2 = items[0]["id"], items[1]["id"]
    res = client.put("/v1/admin/images/batch", json={
        "updates": [
            {"id": id1, "action": "allow", "is_selected": 1},
            {"id": id2, "action": "block", "is_selected": 0},
        ]
    }, headers=HDR)
    assert res.status_code == 200
    assert res.get_json()["updated"] == 2

    # Verify changes
    chk1 = client.get("/v1/admin/images?limit=50").get_json()["items"]
    by_id = {it["id"]: it for it in chk1}
    assert by_id[id1]["action"] == "allow"
    assert by_id[id1]["is_selected"] == 1
    assert by_id[id2]["action"] == "block"
    assert by_id[id2]["is_selected"] == 0


def test_bulk_category_policy_update(client):
    # Set all minor images to allow
    res = client.put("/v1/admin/images/categories/minor", json={"action": "allow"}, headers=HDR)
    assert res.status_code == 200

    minors = client.get("/v1/admin/images?category=minor").get_json()["items"]
    for m in minors:
        assert m["action"] == "allow"

    # Set back to block
    res2 = client.put("/v1/admin/images/categories/minor", json={"action": "block"}, headers=HDR)
    assert res2.status_code == 200
    minors2 = client.get("/v1/admin/images?category=minor").get_json()["items"]
    for m in minors2:
        assert m["action"] == "block"


def test_upload_image_to_category(client):
    # Create simple 10x10 png image
    from PIL import Image
    im = Image.new("RGB", (10, 10), color="blue")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    buf.seek(0)

    res = client.post("/v1/admin/images/upload", data={
        "file": (buf, "test_diaper_sample.png"),
        "category": "child_products",
        "action": "allow",
        "description": "Sample diaper image for testing"
    }, content_type="multipart/form-data", headers=HDR)
    assert res.status_code == 201
    img = res.get_json()["image"]
    assert img["filename"] == "test_diaper_sample.png"
    assert img["category"] == "child_products"
    assert img["action"] == "allow"

    # Test file download
    file_res = client.get(f"/v1/admin/images/{img['id']}/file")
    assert file_res.status_code == 200
    assert file_res.content_type.startswith("image/")


def test_apply_presets_and_fine_tune(client):
    # Test applying child_products preset
    res = client.post("/v1/admin/images/preset", json={"preset": "child_products"}, headers=HDR)
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "ALLOWED" in data["minor_policy"]

    # Verify history
    hist = client.get("/v1/admin/images/history").get_json()
    assert len(hist["history"]) >= 1
    assert hist["active_policy_mode"] == "child_products"

    # Test applying standard preset
    res_std = client.post("/v1/admin/images/preset", json={"preset": "standard"}, headers=HDR)
    assert res_std.status_code == 200
    assert "BLOCKED" in res_std.get_json()["minor_policy"]
