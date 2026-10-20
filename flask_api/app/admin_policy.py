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

import json
import logging
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
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

    # Refusing and allowing the same sentence are both coherent policies, and only the admin
    # knows which they meant - so this stops and asks instead of letting write order decide.
    resolve = str(body.get("resolve", "")).strip().lower() or None
    conflicts = find_conflicts(doc, key, entry["prompts"], entry["unsafe"])
    if conflicts and resolve not in ("refuse", "allow"):
        return _err(409, "policy_conflict",
                    "This wording is already used by a rule with the opposite verdict. "
                    "Choose which one should stand.",
                    conflicts=conflicts,
                    this_verdict="refuse" if entry["unsafe"] else "allow")
    if conflicts:
        if resolve == ("refuse" if entry["unsafe"] else "allow"):
            _resolve_conflicts(doc, conflicts, keep=resolve)
        else:
            # The existing rule wins, so the clashing phrasings are dropped from THIS one.
            entry["prompts"] = [p for p in entry["prompts"]
                                if not any(_norm_phrase(p) == _norm_phrase(c["phrasing"])
                                           for c in conflicts)]
            if not entry["prompts"]:
                return _err(409, "policy_conflict",
                            "Every wording in this rule is already used by a rule with the "
                            "opposite verdict, so keeping those leaves this rule with nothing.",
                            conflicts=conflicts)

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


def _norm_phrase(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", str(s).lower())).strip()


def find_conflicts(doc: dict, label: str, prompts: list[str], unsafe: bool) -> list[dict]:
    """Phrasings that would mean REFUSE in one rule and ALLOW in another.

    This is reachable in normal use and silent when it happens. An admin writes an allowing
    rule "a photo of rainbow", then uploads a rainbow photograph, describes it the same way
    and marks it refuse - and the identical sentence now lives in a refusing rule and an
    allowing rule at once. Both score the same picture, the softmax splits between them, and
    which one wins depends on the other labels in the file rather than on anything anyone
    decided. The admin sees a rainbow refused and no reason why.

    The clash is not resolvable by the system: refusing and allowing the same thing are both
    coherent policies and only the admin knows which they meant. So this reports it and the
    caller asks, rather than picking by write order - which is what happened before, and
    picked whichever rule was edited second.

    Comparison is on alphanumerics and single spaces, so "A photo of a Rainbow." and
    "a photo of a rainbow" count as the same sentence. Near-duplicates that differ in wording
    are left alone: catching those needs a similarity threshold, and a false alarm here blocks
    a legitimate edit.
    """
    want = {_norm_phrase(p) for p in prompts if _norm_phrase(p)}
    if not want:
        return []
    out = []
    for other, entry in (doc.get("labels") or {}).items():
        if other == label or not isinstance(entry, dict):
            continue
        if bool(entry.get("unsafe", True)) == bool(unsafe):
            continue
        for p in (entry.get("prompts") or []):
            if _norm_phrase(p) in want:
                out.append({
                    "phrasing": p, "other_label": other,
                    "other_verdict": "refuse" if entry.get("unsafe", True) else "allow",
                    "this_verdict": "refuse" if unsafe else "allow",
                })
    return out


def _resolve_conflicts(doc: dict, conflicts: list[dict], keep: str) -> None:
    """Apply the admin's choice by removing the losing copy of the phrasing.

    `keep` is the verdict that survives. The phrasing is dropped from every rule holding the
    other verdict - not the rule itself, which may carry other phrasings that are still wanted.
    A rule left with no phrasings at all is removed, because a rule that matches nothing is a
    row in the policy file pretending to be a decision.
    """
    for c in conflicts:
        if c["other_verdict"] == keep:
            continue
        entry = (doc.get("labels") or {}).get(c["other_label"])
        if not isinstance(entry, dict):
            continue
        entry["prompts"] = [p for p in (entry.get("prompts") or [])
                            if _norm_phrase(p) != _norm_phrase(c["phrasing"])]
        if not entry["prompts"]:
            doc["labels"].pop(c["other_label"], None)
            log.info("policy: rule %r removed, its only phrasing lost the conflict",
                     c["other_label"])


def add_phrasing(label: str, phrasing: str, unsafe: bool = True,
                 category: str | None = None, resolve: str | None = None) -> dict:
    """Add one phrasing to a policy rule, creating the rule if it does not exist yet.

    Called when an admin uploads a picture and says what it is. That sentence is worth more
    than a caption: it is a zero-shot rule, and it generalises where the picture itself cannot.
    Measured on this corpus at matched false-positive rates, one wedding photograph as an
    exemplar catches 82.7% of other wedding photographs and the sentence "a photo of a wedding
    ceremony" catches 79.1% - but they miss DIFFERENT ones, and together they catch 93.3%. On
    weapons, where a knife and a rifle look nothing alike, one picture catches 8.0% and the
    sentence 13.6%, together 21.4%.

    An existing rule only ever GAINS a phrasing here; its unsafe flag is left alone. Flipping
    a live rule from "refusing" to "allowed" as a side effect of someone uploading a picture
    would be a policy change nobody asked for, made by a form that looks like a file picker.
    """
    policy, default, model_dir = _paths()
    _ensure_default(policy, default)
    doc = _read(policy)
    doc.setdefault("labels", {})
    key = str(label).strip().lower()
    existed = key in doc["labels"]
    entry = doc["labels"].get(key) or {"unsafe": unsafe, "prompts": []}
    prompts = list(entry.get("prompts") or [])
    phrasing = str(phrasing).strip()
    if not phrasing:
        return {"added": False, "reason": "empty"}
    if any(p.strip().lower() == phrasing.lower() for p in prompts):
        return {"added": False, "reason": "already there", "label": key}
    if len(prompts) >= MAX_PROMPTS:
        return {"added": False, "reason": f"rule already has {MAX_PROMPTS} phrasings",
                "label": key}
    conflicts = find_conflicts(doc, key, [phrasing], unsafe)
    if conflicts and resolve not in ("refuse", "allow"):
        return {"added": False, "reason": "conflict", "label": key, "conflicts": conflicts}
    if conflicts:
        if resolve == ("refuse" if unsafe else "allow"):
            _resolve_conflicts(doc, conflicts, keep=resolve)
        else:
            # The admin chose the OTHER verdict, so this phrasing is simply not added and the
            # existing rule stands. Reported as resolved rather than failed: the decision was
            # made and acted on, it just went the other way.
            return {"added": False, "reason": "resolved_in_favour_of_existing",
                    "label": key, "kept": resolve, "conflicts": conflicts}
    prompts.append(phrasing)
    entry["prompts"] = prompts
    if not existed:
        entry["unsafe"] = bool(unsafe)
        if unsafe:
            entry["category"] = (category or key).strip().lower()
    doc["labels"][key] = entry
    ok, msg = _apply(policy, doc, model_dir)
    return {"added": bool(ok), "label": key, "created_rule": not existed,
            "phrasings": len(prompts), "error": None if ok else msg}


def _rows_by_id(store, ids: list[int]) -> list[dict]:
    """The stored rows for these ids, with their embeddings."""
    want = set(ids)
    return [r for r in store.all_embeddings(limit=20000) if r["id"] in want]


def _written_verdict(visual, img) -> tuple[str | None, str | None, float | None]:
    """What the WRITTEN rules say about this picture: ("refuse"|"allow", label, score).

    Scored through the same softmax the live check uses, so the number is the one that would
    appear in a moderation result rather than a raw similarity. Returns (None, None, None)
    when nothing is confident enough to be worth contradicting - see the caller for why a
    weak match must not raise a warning.
    """
    try:
        import numpy as np
        v = np.frombuffer(img["embedding"], dtype=np.float32)
        if v.size == 0 or visual._prompt_embeds is None:
            return None, None, None
        v = v / max(float(np.linalg.norm(v)), 1e-12)
        if v.shape[0] != visual._prompt_embeds.shape[1]:
            return None, None, None
        sims = visual._prompt_embeds @ v
        labels = np.asarray(visual._labels)
        names = sorted(set(visual._labels))
        best = np.array([sims[labels == n].max() for n in names], dtype=np.float32)
        z = best * getattr(visual, "_scale", 100.0) + getattr(visual, "_bias", 0.0)
        p = np.exp(z - z.max()); p = p / p.sum()
        top = names[int(p.argmax())]
        score = float(p.max())
        unsafe = top in visual._categories
        bar = visual._thresholds.get(top, visual.config.reject_min)
        if unsafe and score >= bar:
            return "block", top, score
        if not unsafe and score >= bar:
            return "allow", top, score
        return None, None, None
    except Exception:
        log.exception("could not read the written verdict for image %s", img.get("id"))
        return None, None, None


def _export_exemplars(model_dir: Path) -> dict:
    """Write models/img_v1/exemplars.npz from the marked pictures, then reload it live.

    A file rather than a database read inside app/clip.py, for the same reason prompts.npz is
    a file: the serving layer owns the vision encoder and two npz files and knows nothing
    about data/system.db, which belongs to the admin side. Keeping that line means the
    moderation path has no dependency on the dashboard being installed, configured, or even
    present.
    """
    store = current_app.extensions["system_store"]
    rows = store.list_exemplars()
    vecs, actions, cats, ids = [], [], [], []
    for r in rows:
        try:
            v = np.frombuffer(r["embedding"], dtype=np.float32)
        except Exception:
            continue
        if v.size == 0:
            continue
        vecs.append(v)
        actions.append(str(r.get("action") or "block"))
        cats.append(str(r.get("category") or ""))
        ids.append(int(r["id"]))
    out = model_dir / "exemplars.npz"
    if vecs:
        width = max(v.shape[0] for v in vecs)
        keep = [i for i, v in enumerate(vecs) if v.shape[0] == width]
        np.savez_compressed(
            out, embeds=np.stack([vecs[i] for i in keep]).astype(np.float32),
            actions=np.array([actions[i] for i in keep]),
            categories=np.array([cats[i] for i in keep]),
            ids=np.array([ids[i] for i in keep], dtype=np.int64))
    else:
        # No exemplars left is a legitimate state - the admin removed the last one - and it
        # has to be written, not skipped, or the live check keeps matching against a file
        # describing pictures nobody marked any more.
        np.savez_compressed(out, embeds=np.zeros((0, 1), dtype=np.float32),
                            actions=np.array([]), categories=np.array([]),
                            ids=np.array([], dtype=np.int64))
    ok = current_app.extensions["moderation"].settings.visual.reload_exemplars()
    n_block = sum(1 for a in actions if a == "block")
    return {"exemplars": len(ids), "refusing": n_block, "allowing": len(ids) - n_block,
            "reloaded": bool(ok)}


@admin_policy_bp.post("/v1/admin/policy/labels/<name>/phrasings")
def add_phrasing_endpoint(name: str):
    """Add ONE phrasing to a rule, optionally resolving a conflict.

    Separate from the full PUT because that replaces a rule's whole prompt list, and the two
    callers that need this - the upload form and the conflict dialog it raises - are adding a
    single sentence to whatever is already there. Going through PUT would mean the client
    reconstructing the existing wordings and sending them back, which is a race with anyone
    else editing and a way to lose phrasings by accident.
    """
    chk = check_admin()
    if chk is not None:
        return chk
    body = request.get_json(silent=True) or {}
    phrasing = str(body.get("phrasing", "")).strip()
    if not phrasing:
        return _err(400, "missing_phrasing", "Give the phrasing to add.")
    res = add_phrasing(str(name), phrasing,
                       unsafe=bool(body.get("unsafe", True)),
                       category=body.get("category"),
                       resolve=str(body.get("resolve", "")).strip().lower() or None)
    if res.get("reason") == "conflict":
        return _err(409, "policy_conflict",
                    "This wording is already used by a rule with the opposite verdict. "
                    "Choose which one should stand.",
                    conflicts=res.get("conflicts", []),
                    this_verdict="refuse" if body.get("unsafe", True) else "allow")
    if not res.get("added") and res.get("error"):
        return _err(500, "policy_build_failed", res["error"])
    return jsonify(res)


@admin_policy_bp.get("/v1/admin/policy/exemplars")
def list_exemplars():
    chk = check_admin()
    if chk is not None:
        return chk
    store = current_app.extensions["system_store"]
    rows = [{k: v for k, v in r.items() if k != "embedding"} for r in store.list_exemplars()]
    visual = current_app.extensions["moderation"].settings.visual
    return jsonify({"exemplars": rows, "count": len(rows),
                    "reject_min": visual.config.exemplar_reject_min})


@admin_policy_bp.put("/v1/admin/policy/exemplars")
def set_exemplars():
    """Point at pictures: "refuse things like this", "allow things like this", or neither.

    This is the half of policy an admin actually has. Writing phrasings is a skill; pointing
    at a picture they just looked at is not, and it is how the request arrives - somebody
    uploads an image and wants it refused, or changes their mind about one that is being
    refused now. It takes effect on the next upload: no retraining, no dataset, and undoing it
    is clearing the same flag.
    """
    chk = check_admin()
    if chk is not None:
        return chk
    body = request.get_json(silent=True) or {}
    ids = body.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return _err(400, "missing_ids", "Give the image ids to mark.")
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return _err(400, "bad_ids", "Image ids must be numbers.")
    mode = str(body.get("mode", "")).strip().lower()
    if mode not in ("block", "allow", "clear"):
        return _err(400, "bad_mode", "mode must be 'block', 'allow' or 'clear'.")

    store = current_app.extensions["system_store"]
    resolve = str(body.get("resolve", "")).strip().lower() or None

    # THE OTHER DIRECTION OF THE SAME CLASH. A picture marked "refuse things like this" whose
    # written rules already publish it - or the reverse - is the same contradiction as two
    # rules disagreeing, arriving through the other door. It is quieter and worse: the
    # exemplar is checked first, so it wins, and the admin sees their own allowing rule being
    # ignored with nothing in the policy file to explain it.
    #
    # Only flagged when the written verdict is CONFIDENT. Every picture matches some label
    # weakly, and warning on a 0.3 means warning on everything, which trains people to click
    # past the one that mattered.
    clashes = []
    if mode in ("block", "allow") and resolve is None:
        visual = current_app.extensions["moderation"].settings.visual
        if visual.ready:
            for img in _rows_by_id(store, ids):
                verdict, label, score = _written_verdict(visual, img)
                if verdict and verdict != mode and score is not None:
                    clashes.append({"id": img["id"], "filename": img.get("filename"),
                                    "written_rule": label, "written_verdict": verdict,
                                    "score": round(float(score), 4), "you_chose": mode})
    if clashes:
        return _err(409, "exemplar_conflict",
                    "The written rules already decide these pictures the other way. Marking "
                    "them here overrides that silently, because exemplars are checked first. "
                    "Choose which should stand.",
                    conflicts=clashes)

    changed = store.set_exemplars(ids, is_exemplar=(mode != "clear"),
                                  action=None if mode == "clear" else mode)
    _, _, model_dir = _paths()
    state = _export_exemplars(model_dir)
    return jsonify({"status": "ok", "mode": mode, "changed": changed,
                    "resolved": resolve, **state})


@admin_policy_bp.post("/v1/admin/policy/preview")
def preview_label():
    """Score candidate phrasings against the stored images WITHOUT changing anything.

    This is the part that makes writing policy in words usable rather than a guess. A rule is
    only as good as its wordings, and until now the only way to find out whether "a photo of
    fireworks exploding" catches what you meant was to apply it and watch the moderation queue.
    Here the admin sees, before committing, which of BONC's own pictures the words match and
    how strongly - and, for an existing rule, which pictures it is already matching that they
    might not expect.

    It scores against the embeddings already in system.db, so there is no inference over
    images at all: one subprocess to encode a handful of phrasings, then a dot product.

    THESE NUMBERS ARE NOT THE LIVE SCORES AND MUST NOT BE READ AS A THRESHOLD. They are raw
    cosine similarities, which for CLIP sit in a narrow band - measured here, a wedding
    photograph against "a photo of a wedding ceremony" is 0.32 while the same picture scores
    0.96 through classify(). The live check softmaxes across every label, which concentrates
    almost all the mass on the winner; a raw similarity does not. So `over_threshold` is
    returned only when the caller asks for it, and the UI presents this as a RANKING - which
    of your pictures these words pick out, and whether they cluster on the subject you meant -
    rather than as a prediction of reject_min.

    Ranking is the right question anyway. A wording is good when it separates: "a photo of a
    wedding ceremony" puts five wedding photographs at the top, while "a photo of fireworks
    exploding" returns gore, romance and a safe listing at nearly identical scores, which is
    exactly how it looks when nothing in the set matches. That contrast is visible in the
    order and invisible in any single number.
    """
    chk = check_admin()
    if chk is not None:
        return chk
    body = request.get_json(silent=True) or {}
    prompts = [str(p).strip() for p in (body.get("prompts") or []) if str(p).strip()]
    if not prompts:
        return _err(400, "missing_prompts", "Give at least one phrasing to try.")
    if len(prompts) > MAX_PROMPTS:
        return _err(400, "too_many_prompts", f"At most {MAX_PROMPTS} phrasings.")
    limit = max(1, min(int(body.get("limit", 12) or 12), 48))

    _, _, model_dir = _paths()
    root = Path(current_app.extensions["moderation"].settings.visual.root)
    script = root / "scripts" / "encode_prompts.py"
    if not script.exists():
        return _err(500, "no_encoder", f"{script} is missing.")
    try:
        proc = subprocess.run([sys.executable, str(script), "--model-dir", str(model_dir)],
                              input=json.dumps(prompts), capture_output=True, text=True,
                              timeout=BUILD_TIMEOUT_S, cwd=str(root))
        if proc.returncode != 0:
            return _err(500, "encode_failed",
                        (proc.stderr or proc.stdout or "").strip()[-300:] or "encoding failed")
        embeds = np.asarray(json.loads(proc.stdout)["embeds"], dtype=np.float32)
    except Exception as exc:
        log.exception("policy preview failed")
        return _err(500, "encode_failed", str(exc))

    store = current_app.extensions["system_store"]
    rows = store.list_images(page=1, limit=5000).get("items", [])
    ids = [r["id"] for r in rows if r.get("has_embedding")]
    if not ids:
        return _err(409, "no_embeddings",
                    "No stored image has an embedding yet. Run the image loader first so "
                    "there is something to try these words against.")

    # Pull the vectors for exactly those rows. list_images omits the blob by design (it is
    # 2 KB a row), so they are fetched here rather than widening that query for every caller.
    vecs, keep = [], []
    for r in store.get_selected_for_training() if body.get("selected_only") else store.all_embeddings():
        try:
            v = np.frombuffer(r["embedding"], dtype=np.float32)
        except Exception:
            continue
        if v.shape[0] == embeds.shape[1]:
            vecs.append(v)
            keep.append(r)
    if not vecs:
        return _err(409, "no_embeddings", "No stored embedding matches this model's dimension.")

    M = np.stack(vecs)
    M = M / np.maximum(np.linalg.norm(M, axis=1, keepdims=True), 1e-12)
    # Best-matching phrasing per picture, which is how the live check ensembles them too.
    sims = (M @ embeds.T).max(axis=1)
    order = np.argsort(-sims)[:limit]
    matches = [{
        "id": keep[i]["id"], "filename": keep[i].get("filename"),
        "category": keep[i].get("category"), "action": keep[i].get("action"),
        "score": round(float(sims[i]), 4),
    } for i in order]
    thresh = body.get("reject_min")
    over = None
    if thresh not in (None, ""):
        try:
            over = int((sims >= float(thresh)).sum())
        except (TypeError, ValueError):
            over = None
    return jsonify({
        "scored": int(len(sims)), "prompts": prompts, "top": matches,
        "over_threshold": over,
        "note": ("Cosine similarity against stored images. The live check softmaxes over every "
                 "rule, so a matching picture usually scores HIGHER in production than here."),
    })


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
