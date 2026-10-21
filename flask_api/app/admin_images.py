"""Admin endpoints for image policy management, categories, dataset images, and model fine-tuning.

Allows administrators to:
  * View and filter stored images by category and allow/block policy.
  * Add custom categories and upload new images into categories.
  * Select/deselect images and toggle what should be allowed vs blocked.
  * Fine-tune the vision model (probe & thresholds) for business needs (e.g. child products).
  * Apply business presets (e.g. Baby & Child Products / Mamy Poko Pants vs Standard).
"""
from __future__ import annotations

import io
import logging
import mimetypes
import os
import time
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file
from werkzeug.utils import secure_filename

from app.admin_auth import check_admin
from app.image_trainer import ImageTrainer
from app.system_store import SystemStore

log = logging.getLogger("app.admin_images")

admin_images_bp = Blueprint("admin_images", __name__)


def _store() -> SystemStore:
    return current_app.extensions["system_store"]


def _trainer() -> ImageTrainer:
    store = _store()
    visual = current_app.extensions["moderation"].settings.visual
    return ImageTrainer(store, visual_check=visual)


def _err(status: int, code: str, message: str, **extra):
    resp = jsonify({"error": code, "message": message, **extra})
    resp.status_code = status
    return resp


# ---- Categories ---------------------------------------------------------------------

@admin_images_bp.get("/v1/admin/images/categories")
def list_categories():
    chk = check_admin()
    if chk is not None:
        return chk
    store = _store()
    return jsonify({
        "categories": store.list_categories(),
        "active_mode": store.get_setting("active_policy_mode", "standard"),
    })


@admin_images_bp.post("/v1/admin/images/categories")
def add_category():
    chk = check_admin()
    if chk is not None:
        return chk
    body = request.get_json(silent=True) or {}
    name = str(body.get("name", "")).strip()
    display_name = str(body.get("display_name", "")).strip() or name.capitalize()
    default_action = str(body.get("default_action", "allow")).strip().lower()
    description = str(body.get("description", "")).strip()

    if not name:
        return _err(400, "invalid_input", "Category 'name' is required.")

    store = _store()
    try:
        cat = store.add_category(name, display_name, default_action, description)
        return jsonify({"category": cat, "message": f"Category '{cat['name']}' created."}), 201
    except ValueError as e:
        return _err(409, "category_exists", str(e))


@admin_images_bp.put("/v1/admin/images/categories/<name>")
def update_category(name: str):
    chk = check_admin()
    if chk is not None:
        return chk
    body = request.get_json(silent=True) or {}
    store = _store()

    # Bulk update action if requested
    if "action" in body:
        act = str(body["action"]).lower()
        if act not in ("allow", "block"):
            return _err(400, "invalid_action", "Action must be 'allow' or 'block'.")
        count = store.set_category_policy(name, act)
        return jsonify({
            "message": f"Set all {count} images in '{name}' to {act.upper()}.",
            "category": store.get_category(name),
        })

    try:
        cat = store.update_category(
            name,
            display_name=body.get("display_name"),
            default_action=body.get("default_action"),
            description=body.get("description"),
        )
        return jsonify({"category": cat, "message": f"Category '{name}' updated."})
    except ValueError as e:
        return _err(404, "not_found", str(e))


# ---- Images Listing & File Serving --------------------------------------------------

@admin_images_bp.get("/v1/admin/images")
def list_images():
    chk = check_admin()
    if chk is not None:
        return chk
    store = _store()
    category = request.args.get("category")
    action = request.args.get("action")
    is_selected = request.args.get("selected")
    search = request.args.get("search")
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 24))

    sel_int = int(is_selected) if is_selected in ("0", "1") else None
    data = store.list_images(
        category=category,
        action=action,
        is_selected=sel_int,
        search=search,
        page=page,
        limit=limit,
    )
    data["active_policy_mode"] = store.get_setting("active_policy_mode", "standard")
    return jsonify(data)


@admin_images_bp.get("/v1/admin/images/<int:image_id>/file")
def get_image_file(image_id: int):
    chk = check_admin()
    if chk is not None:
        return chk
    store = _store()
    img = store.get_image(image_id)
    if not img:
        return _err(404, "not_found", "Image not found.")

    trainer = _trainer()
    p = trainer.resolve_image_path(img["file_path"])
    if not p or not p.exists():
        return _err(404, "file_missing", f"Image file not found on disk: {img['file_path']}")

    mime = img.get("mime_type") or mimetypes.guess_type(str(p))[0] or "image/jpeg"
    return send_file(p, mimetype=mime, max_age=3600)


# ---- Image Upload & Batch Changes ---------------------------------------------------

@admin_images_bp.post("/v1/admin/images/upload")
def upload_image():
    chk = check_admin()
    if chk is not None:
        return chk
    if "file" not in request.files:
        return _err(400, "no_file", "Upload a file in the 'file' multipart field.")

    file = request.files["file"]
    if not file.filename:
        return _err(400, "empty_filename", "File has no filename.")

    category = request.form.get("category", "safe").strip().lower()
    action = request.form.get("action", "allow").strip().lower()
    if action not in ("allow", "block"):
        action = "allow"
    description = request.form.get("description", "").strip()

    # Save to data/uploads
    uploads_dir = Path(current_app.root_path).parent / "data" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    safe_name = f"{int(time.time())}_{secure_filename(file.filename)}"
    dest = uploads_dir / safe_name
    file.save(dest)

    store = _store()
    trainer = _trainer()
    emb = trainer.compute_embedding(dest)
    emb_bytes = emb.tobytes() if emb is not None else None

    mime = file.content_type or mimetypes.guess_type(str(dest))[0] or "image/jpeg"
    rel_path = f"data/uploads/{safe_name}"

    record = store.add_image(
        filename=file.filename,
        file_path=rel_path,
        category=category,
        action=action,
        is_selected=1,
        source="uploaded",
        description=description,
        mime_type=mime,
        embedding=emb_bytes,
    )

    # ONE UPLOAD, THREE CHANNELS. They reach different pictures and they arrive at different
    # speeds, so teaching all three from a single action is what makes an upload worth making:
    #
    #   exemplar   the picture itself. Catches lookalikes from the next upload onward.
    #   phrasing   what the admin says it IS. Generalises far past this one picture, and is
    #              the only channel that can, because it scores in CLIP's text space.
    #   probe      is_selected above. Learns from the accumulated set on the next retrain,
    #              which is where a visually diverse subject like `weapon` is eventually won.
    #
    # Measured at matched false-positive rates, exemplar and phrasing catch 82.7% and 79.1% of
    # held-out wedding photographs on their own and 93.3% together - they miss different ones.
    teach = str(request.form.get("teach", "")).strip().lower() in ("1", "true", "yes", "on")
    taught: dict = {}
    if teach and record:
        from app.admin_policy import (_export_exemplars, _paths, _read, add_phrasing,
                                      find_conflicts)
        rid = record.get("id")
        phrasing = request.form.get("phrasing", "").strip() or description
        label = request.form.get("policy_label", "").strip().lower() or category
        resolve = str(request.form.get("resolve", "")).strip().lower() or None

        # CHECK BEFORE APPLYING ANYTHING. The first cut set the exemplar first and only then
        # asked add_phrasing, which detected the clash, declined, and was reported inside a
        # 201. The picture was therefore refused by the exemplar while the admin's own
        # allowing rule stood untouched and nothing asked them - the exact bug this was meant
        # to prevent, arriving through the door the fix did not cover.
        policy_path, _d, model_dir = _paths()
        conflicts = []
        if phrasing and resolve is None:
            conflicts = find_conflicts(_read(policy_path), label, [phrasing],
                                       unsafe=(action == "block"), model_dir=model_dir)
        if conflicts:
            # The file is kept and the record stands - the admin did upload it, and making
            # them pick it again to answer a question would be its own small insult. Only the
            # TEACHING waits, and the dialog resolves it against this image id.
            return jsonify({
                "success": True, "image": record,
                "taught": {"conflict": True, "conflicts": conflicts,
                           "pending": {"image_id": rid, "label": label, "phrasing": phrasing,
                                       "unsafe": action == "block", "category": category,
                                       "mode": action}},
                "message": f"Uploaded '{file.filename}', but the description contradicts an "
                           f"existing rule - nothing has been taught yet.",
            }), 201

        if emb_bytes and rid:
            store.set_exemplars([rid], is_exemplar=True, action=action)
            taught["exemplar"] = _export_exemplars(model_dir)
        else:
            taught["exemplar"] = {"skipped": "no embedding could be computed for this file"}
        if phrasing:
            taught["phrasing"] = add_phrasing(label, phrasing, unsafe=(action == "block"),
                                              category=category, resolve=resolve)
        else:
            taught["phrasing"] = {"added": False, "reason": "no description given"}
        taught["probe"] = {"selected_for_next_retrain": True}

    return jsonify({"success": True, "image": record, "taught": taught or None,
                    "message": f"Uploaded '{file.filename}' to '{category}'."}), 201


@admin_images_bp.put("/v1/admin/images/batch")
def batch_update_images():
    chk = check_admin()
    if chk is not None:
        return chk
    body = request.get_json(silent=True) or {}
    store = _store()

    # Bulk select / deselect, under the same filter the grid is showing. Taking only the
    # category made the button act on pictures the admin could not see: searching "gym" shows
    # 64 images across two categories, and Deselect All deselected a whole category instead.
    # The retrain that followed was then measuring a change nobody had made.
    if "select_all" in body:
        sel = bool(body["select_all"])
        cat = body.get("category")
        count = store.set_selection_bulk(sel, category=cat, search=body.get("search"),
                                         action=body.get("action"))
        where = []
        if cat and str(cat).lower() != "all":
            where.append(f"category '{cat}'")
        if body.get("action") and str(body["action"]).lower() != "all":
            where.append(f"{str(body['action']).lower()} images")
        if str(body.get("search") or "").strip():
            where.append(f'matching "{str(body["search"]).strip()}"')
        # Say WHAT was touched, not just how many. "Deselected 72 images" and "Deselected 72
        # images in category physique" read the same until the two sets differ, which is
        # exactly when the admin needs to be told.
        scope = (" in " + ", ".join(where)) if where else " (everything)"
        return jsonify({"success": True, "updated": count,
                        "message": f"{'Selected' if sel else 'Deselected'} {count} images{scope}."})

    # Bulk action change
    if "bulk_action" in body and "category" in body:
        act = str(body["bulk_action"]).lower()
        cat = str(body["category"]).lower()
        count = store.set_category_policy(cat, act)
        return jsonify({"success": True, "updated": count, "message": f"Set category '{cat}' to {act.upper()}."})

    updates = body.get("updates") or []
    if not isinstance(updates, list):
        return _err(400, "invalid_updates", "'updates' must be an array of image modifications.")

    count = store.batch_update_images(updates)
    return jsonify({"success": True, "updated": count, "message": f"Saved changes for {count} images."})


# ---- Training & Presets -------------------------------------------------------------

@admin_images_bp.post("/v1/admin/images/train")
def train_model():
    chk = check_admin()
    if chk is not None:
        return chk
    body = request.get_json(silent=True) or {}
    c_param = float(body.get("C", 1.0))
    preset = body.get("preset")

    trainer = _trainer()
    try:
        result = trainer.train_probe(c_param=c_param, preset=preset)
        return jsonify(result)
    except Exception as e:
        log.exception("Training failed")
        return _err(500, "training_error", str(e))


@admin_images_bp.post("/v1/admin/images/preset")
def apply_preset():
    chk = check_admin()
    if chk is not None:
        return chk
    body = request.get_json(silent=True) or {}
    preset = str(body.get("preset", "")).strip().lower()
    if not preset:
        return _err(400, "missing_preset", "Provide 'preset' (e.g. 'child_products' or 'standard').")

    trainer = _trainer()
    try:
        result = trainer.apply_preset(preset)
        return jsonify(result)
    except Exception as e:
        log.exception("Applying preset failed")
        return _err(500, "preset_error", str(e))


@admin_images_bp.get("/v1/admin/images/history")
def get_training_history():
    chk = check_admin()
    if chk is not None:
        return chk
    store = _store()
    history = store.list_training_history(limit=10)
    return jsonify({"history": history, "active_policy_mode": store.get_setting("active_policy_mode", "standard")})


@admin_images_bp.post("/v1/admin/images/load-all")
def load_all_images():
    chk = check_admin()
    if chk is not None:
        return chk
    store = _store()
    try:
        res = store.load_all_dataset_images()
        return jsonify({
            "success": True,
            "inserted": res["inserted"],
            "total": res["total"],
            "message": f"Loaded all images from disk into database. Total images: {res['total']}."
        })
    except Exception as e:
        log.exception("Load all images failed")
        return _err(500, "load_error", str(e))
