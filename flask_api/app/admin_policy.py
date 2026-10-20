"""Write and edit the visual policy from the admin dashboard, with a default always available.

WHAT A POLICY LABEL IS. config/visual_policy.yaml is the whole of the image policy: CLIP
scores a picture against written phrasings rather than against trained classes, so adding
`weapon` with a few wordings is the entire act of teaching the system to refuse weapons. No
retraining, no labelled dataset. That is the property worth exposing to an admin, and until
now the only way to use it was to edit a file on the server.

WHY THIS IS NOT JUST A FILE WRITE. The thing actually scored against is models/img_v1/
prompts.npz - one L2-normalised vector per phrasing - which scripts/build_clip_prompts.py
bakes from the policy offline. It is offline on purpose: the text encoder is 254 MB and
app/clip.py deliberately never loads it, so the serving process carries the vision encoder and
nothing else. Editing the YAML alone changes nothing; the npz has to be rebuilt, and it stamps
the policy file's hash so a stale one is caught rather than quietly scored against.

So a save here is three steps, and all three have to succeed or none of them count:

    1. write the YAML
    2. rebuild prompts.npz  (a subprocess, so the 254 MB encoder lives and dies outside the
                             web process - the architecture's whole point)
    3. VisualCheck.reload_prompts()  (swap the new labels, categories, thresholds and
                                      embeddings in together, under the lock)

Step 2 can fail on a bad phrasing and step 3 on a corrupt npz, and a policy file that no
longer matches what is being scored is worse than a rejected edit - the dashboard would show
one policy and the model would apply another. Every write is therefore staged: the current
YAML is copied aside first and restored if any step fails, so a failed save leaves the system
exactly where it was.

THE DEFAULT, WHICH THE PRODUCT OWNER ASKED FOR EXPLICITLY. config/visual_policy.default.yaml
is written once, from the policy as it shipped, before the first edit is ever applied - the
same device image_trainer.py uses for probe_backup_default.npz, and for the same reason: the
only moment a pristine copy can be taken is before anybody changes anything. `POST
/v1/admin/policy/reset` restores it. It is created from whatever is on disk at first write, so
it must exist in the repo for a fresh clone to have a true default; see the note in
flask_api/config/.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml
from flask import Blueprint, current_app, jsonify, request

from app.admin_auth import check_admin

log = logging.getLogger("app.admin_policy")

admin_policy_bp = Blueprint("admin_policy", __name__)

# A label name has to be a plain identifier: it becomes a key in the YAML, a column in the
# npz, and a field in every moderation result that reports a visual match.
_NAME_OK = set("abcdefghijklmnopqrstuvwxyz0123456789_")
MAX_PROMPTS = 24
MAX_PROMPT_CHARS = 200
BUILD_TIMEOUT_S = 600


def _err(status: int, code: str, message: str, **extra):
    resp = jsonify({"error": code, "message": message, **extra})
    resp.status_code = status
    return resp


def _paths() -> tuple[Path, Path, Path]:
    """(policy file, default copy, model dir) for the live visual check."""
    visual = current_app.extensions["moderation"].settings.visual
    policy = Path(visual.root) / "config" / "visual_policy.yaml"
    return policy, policy.with_name("visual_policy.default.yaml"), Path(visual.model_dir)


def _family(model_dir: Path) -> str:
    """Which encoder family the LIVE bundle is, read from its own model_meta.json.

    build_clip_prompts.py defaults to --family clip, and img_v1 is CLIP, so hardcoding the
    default would work today and break silently the day models/img_v2 (SigLIP 2) goes live:
    the embeddings would be built by a different text tower than the vision tower scoring
    against them, and every picture would be judged on noise while the service looked healthy.
    Derived, so it stays right without anyone remembering this.
    """
    import json
    meta = model_dir / "model_meta.json"
    try:
        base = str(json.loads(meta.read_text(encoding="utf-8")).get("base_model", ""))
    except Exception:
        return "clip"
    return "siglip2" if "siglip" in base.lower() else "clip"


def _ensure_default(policy: Path, default: Path) -> None:
    """Take the pristine copy before the first edit, never after. See the module docstring."""
    if policy.exists() and not default.exists():
        try:
            shutil.copy2(policy, default)
            log.info("saved the shipped visual policy to %s", default.name)
        except Exception:
            log.warning("could not save a default copy of %s", policy, exc_info=True)


def snapshot_default_policy(settings) -> None:
    """Called once at startup, outside a request, so the default is taken before any edit.

    _ensure_default also runs on the first write, which would be enough if the server were
    always started before anyone edited the file by hand. It is not: an operator editing
    visual_policy.yaml on disk and restarting would otherwise have that edit become the
    "default" the dashboard offers to restore. Taking it at startup narrows that to the first
    boot after this ships, which is the earliest moment available.
    """
    try:
        policy = Path(settings.visual.root) / "config" / "visual_policy.yaml"
        _ensure_default(policy, policy.with_name("visual_policy.default.yaml"))
    except Exception:                                           # pragma: no cover
        log.warning("could not snapshot the default visual policy", exc_info=True)


def _read(policy: Path) -> dict:
    if not policy.exists():
        return {"version": 1, "labels": {}}
    return yaml.safe_load(policy.read_text(encoding="utf-8")) or {"version": 1, "labels": {}}


def _validate(name: str, body: dict) -> tuple[str, dict] | tuple[None, object]:
    name = str(name or "").strip().lower()
    if not name or not set(name) <= _NAME_OK:
        return None, _err(400, "bad_name", "A label name may use a-z, 0-9 and _ only.")
    prompts = body.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        return None, _err(400, "missing_prompts",
                          "Give at least one phrasing, e.g. 'a photo of a handgun'.")
    cleaned = []
    for p in prompts:
        p = str(p or "").strip()
        if not p:
            continue
        if len(p) > MAX_PROMPT_CHARS:
            return None, _err(400, "prompt_too_long",
                              f"A phrasing may be at most {MAX_PROMPT_CHARS} characters.")
        cleaned.append(p)
    if not cleaned:
        return None, _err(400, "missing_prompts", "Give at least one non-empty phrasing.")
    if len(cleaned) > MAX_PROMPTS:
        return None, _err(400, "too_many_prompts",
                          f"At most {MAX_PROMPTS} phrasings per label.")
    entry: dict = {"unsafe": bool(body.get("unsafe", True)), "prompts": cleaned}
    if entry["unsafe"]:
        entry["category"] = str(body.get("category") or name).strip().lower()
    rmin = body.get("reject_min")
    if rmin is not None and rmin != "":
        try:
            v = float(rmin)
        except (TypeError, ValueError):
            return None, _err(400, "bad_threshold", "reject_min must be a number in 0..1.")
        if not 0.0 <= v <= 1.0:
            return None, _err(400, "bad_threshold", "reject_min must be between 0 and 1.")
        entry["reject_min"] = v
    return name, entry


def _apply(policy: Path, doc: dict, model_dir: Path) -> tuple[bool, str]:
    """Write, rebuild, reload - or put everything back. See the module docstring."""
    staged = policy.with_suffix(".yaml.staged")
    try:
        shutil.copy2(policy, staged)
    except Exception:
        return False, "could not stage the current policy; nothing was changed"
    try:
        policy.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
                          encoding="utf-8")
        script = Path(current_app.extensions["moderation"].settings.visual.root) / "scripts" / "build_clip_prompts.py"
        if not script.exists():
            raise RuntimeError(f"{script} is missing, so prompts.npz cannot be rebuilt")
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, str(script), "--policy", str(policy),
             "--model-dir", str(model_dir), "--family", _family(model_dir)],
            capture_output=True, text=True, timeout=BUILD_TIMEOUT_S, cwd=str(script.parent.parent))
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "").strip()[-400:]
                               or "build_clip_prompts.py failed")
        log.info("rebuilt prompts.npz in %.1fs", time.time() - t0)
        visual = current_app.extensions["moderation"].settings.visual
        if not visual.reload_prompts():
            raise RuntimeError("the new prompts.npz could not be loaded; policy rolled back")
    except Exception as exc:
        try:
            shutil.copy2(staged, policy)
            current_app.extensions["moderation"].settings.visual.reload_prompts()
        except Exception:
            log.exception("ROLLBACK FAILED - %s may not match prompts.npz", policy)
        return False, str(exc)
    finally:
        staged.unlink(missing_ok=True)
    return True, "ok"


# ---- read ------------------------------------------------------------------------------

@admin_policy_bp.get("/v1/admin/policy/labels")
def list_labels():
    chk = check_admin()
    if chk is not None:
        return chk
    policy, default, _ = _paths()
    doc = _read(policy)
    visual = current_app.extensions["moderation"].settings.visual
    live = sorted(set(getattr(visual, "_labels", []) or []))
    return jsonify({
        "labels": doc.get("labels") or {},
        "version": doc.get("version", 1),
        # What the MODEL is scoring right now, which is prompts.npz and not this file. They
        # agree after a successful save and differ if someone edited the YAML by hand without
        # rebuilding - worth showing rather than assuming.
        "live_labels": live,
        "in_sync": sorted((doc.get("labels") or {}).keys()) == live,
        "default_available": default.exists(),
    })


# ---- write -----------------------------------------------------------------------------

@admin_policy_bp.put("/v1/admin/policy/labels/<name>")
def upsert_label(name: str):
    chk = check_admin()
    if chk is not None:
        return chk
    body = request.get_json(silent=True) or {}
    key, entry = _validate(name, body)
    if key is None:
        return entry
    policy, default, model_dir = _paths()
    _ensure_default(policy, default)
    doc = _read(policy)
    doc.setdefault("labels", {})
    existed = key in doc["labels"]
    doc["labels"][key] = entry
    ok, msg = _apply(policy, doc, model_dir)
    if not ok:
        return _err(500, "policy_build_failed", msg)
    return jsonify({"status": "updated" if existed else "created", "label": key,
                    "entry": entry, "live_labels": sorted(set(
                        current_app.extensions["moderation"].settings.visual._labels))})


@admin_policy_bp.delete("/v1/admin/policy/labels/<name>")
def delete_label(name: str):
    chk = check_admin()
    if chk is not None:
        return chk
    key = str(name or "").strip().lower()
    policy, default, model_dir = _paths()
    doc = _read(policy)
    if key not in (doc.get("labels") or {}):
        return _err(404, "no_such_label", f"No policy label named {key!r}.")
    safe_left = [n for n, e in doc["labels"].items()
                 if n != key and not (e or {}).get("unsafe", True)]
    if not safe_left:
        # The safe prompts are what the unsafe ones are judged against - the policy file says
        # so in as many words. Removing the last one makes every ordinary photograph compete
        # only against violations, and the check starts refusing catalogues.
        return _err(400, "last_safe_label",
                    "At least one safe label must remain: the unsafe ones are scored against "
                    "them, and with none left ordinary photographs look suspicious.")
    _ensure_default(policy, default)
    doc["labels"].pop(key)
    ok, msg = _apply(policy, doc, model_dir)
    if not ok:
        return _err(500, "policy_build_failed", msg)
    return jsonify({"status": "deleted", "label": key})


@admin_policy_bp.post("/v1/admin/policy/reset")
def reset_policy():
    """Back to the policy as it shipped. The option the product owner asked always to exist."""
    chk = check_admin()
    if chk is not None:
        return chk
    policy, default, model_dir = _paths()
    if not default.exists():
        return _err(409, "no_default",
                    "No default copy exists yet. One is taken automatically before the first "
                    "edit, so there is nothing to go back to until the policy is changed.")
    ok, msg = _apply(policy, _read(default), model_dir)
    if not ok:
        return _err(500, "policy_build_failed", msg)
    return jsonify({"status": "reset_to_default",
                    "labels": sorted((_read(policy).get("labels") or {}).keys())})
