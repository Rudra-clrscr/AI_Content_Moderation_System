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
    # Only an unsafe label has anything to refuse at, and build_clip_prompts.py reads
    # reject_min only inside `if spec.get("unsafe")`. A bar left on a label that has just been
    # set to allow is therefore dead config that reads as a live setting - which is what
    # flipping `physique` to allow produced: `unsafe: false` with `reject_min: 0.55` under it,
    # ignored by the build and confusing to everyone after.
    rmin = body.get("reject_min") if entry["unsafe"] else None
    if rmin is not None and rmin != "":
        try:
            v = float(rmin)
        except (TypeError, ValueError):
            return None, _err(400, "bad_threshold", "reject_min must be a number in 0..1.")
        if not 0.0 <= v <= 1.0:
            return None, _err(400, "bad_threshold", "reject_min must be between 0 and 1.")
        entry["reject_min"] = v
    return name, entry


# ---- editing the policy file without destroying it -------------------------------------
# config/visual_policy.yaml is 484 lines of which 249 are comments, and they are the
# reasoning: why `minor` sits at 0.85 when `violence` sits at 0.57, which image set each bar
# was measured on, what the `safe` list is for. yaml.safe_dump round-trips the data and throws
# every line of it away - the first version of this feature did exactly that and silently cut
# the file from 484 lines to 176.
#
# So the file is edited as TEXT, in place, one operation at a time. Nothing is re-rendered:
# adding a phrasing inserts a line, flipping a verdict replaces one, a new rule is appended
# and a deleted one is cut. Every byte nobody asked to change is left alone, including the
# comments belonging to rules an admin never touched.

def _label_span(lines: list[str], label: str) -> tuple[int, int, str] | None:
    """(first line, line after last, indent) of a label's block, or None.

    A block runs from its `name:` line to the next key at the same indent. Comment lines
    immediately ABOVE the key belong to it - that is where this file documents a rule - so
    they are included, and a deletion takes them with it rather than leaving an explanation
    for something that is gone.
    """
    start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if stripped.split(":")[0].strip() == label and stripped.endswith(":") or \
           stripped.startswith(label + ":"):
            if start is None and indent >= 2:
                start = i
                own_indent = indent
                break
    if start is None:
        return None
    j = start
    while j > 0 and lines[j - 1].strip().startswith("#"):
        j -= 1
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and not line.strip().startswith("#"):
            if len(line) - len(line.lstrip()) <= own_indent:
                break
        end += 1
    # Trailing comments that introduce the NEXT rule stay with the next rule.
    while end > start + 1 and lines[end - 1].strip().startswith("#"):
        end -= 1
    return j, end, " " * own_indent


def _prompts_insert_at(lines: list[str], start: int, end: int) -> tuple[int, str] | None:
    """Where to put a new phrasing inside a block, and the bullet indent to use."""
    last, indent = None, None
    for i in range(start, end):
        stripped = lines[i].strip()
        if stripped.startswith("- "):
            last = i
            indent = " " * (len(lines[i]) - len(lines[i].lstrip()))
        elif stripped.startswith("prompts:") and indent is None:
            indent = " " * (len(lines[i]) - len(lines[i].lstrip()) + 2)
            last = i
    if last is None:
        return None
    return last + 1, indent or "      "


def edit_policy_text(text: str, label: str, *, add_prompt: str | None = None,
                     remove_prompt: str | None = None, set_unsafe: bool | None = None,
                     set_field: tuple[str, object] | None = None,
                     remove_field: str | None = None, delete: bool = False,
                     new_entry: dict | None = None) -> str:
    """One surgical change to the policy file, preserving every comment."""
    lines = text.splitlines()
    span = _label_span(lines, label)

    if delete:
        if not span:
            return text
        a, b, _ = span
        return "\n".join(lines[:a] + lines[b:]) + "\n"

    if span is None:
        if new_entry is None:
            return text
        block = [f"  {label}:"]
        block.append(f"    unsafe: {'true' if new_entry.get('unsafe', True) else 'false'}")
        if new_entry.get("unsafe", True) and new_entry.get("category"):
            block.append(f"    category: {new_entry['category']}")
        if new_entry.get("reject_min") is not None:
            block.append(f"    reject_min: {new_entry['reject_min']}")
        block.append("    prompts:")
        for p in new_entry.get("prompts") or []:
            block.append(f"      - {_scalar(p)}")
        tail = "" if text.endswith("\n") else "\n"
        return text + tail + "\n".join(block) + "\n"

    a, b, indent = span
    if set_unsafe is not None:
        for i in range(a, b):
            if lines[i].strip().startswith("unsafe:"):
                pad = " " * (len(lines[i]) - len(lines[i].lstrip()))
                lines[i] = f"{pad}unsafe: {'true' if set_unsafe else 'false'}"
                break
    if remove_prompt is not None:
        target = _norm_phrase(remove_prompt)
        for i in range(a, b):
            st = lines[i].strip()
            if st.startswith("- ") and _norm_phrase(st[2:].strip().strip("'\"")) == target:
                del lines[i]
                b -= 1
                break
    if remove_field is not None:
        # Cuts the whole line, trailing comment included, AND the comment lines that continue
        # it. In this file a measured threshold carries its workings underneath, aligned past
        # the value:
        #
        #     reject_min: 0.55   # gym_neg p25 0.78 / p50 0.90. No real BONC listing
        #                        # scores above 0.25 on this label, so the bar is ...
        #
        # Removing only the first line leaves five lines explaining a setting that is gone.
        # The continuation is told apart from the comment that introduces the NEXT field by
        # indent: continuations are aligned past the key, introductions sit at the key's own
        # indent.
        for i in range(a, b):
            if lines[i].strip().startswith(remove_field + ":"):
                field_indent = len(lines[i]) - len(lines[i].lstrip())
                end = i + 1
                while end < b:
                    nxt = lines[end]
                    if not nxt.strip().startswith("#"):
                        break
                    if len(nxt) - len(nxt.lstrip()) <= field_indent:
                        break
                    end += 1
                del lines[i:end]
                b -= (end - i)
                break
    if set_field is not None:
        key, val = set_field
        rendered = ("true" if val else "false") if isinstance(val, bool) else str(val)
        done = False
        for i in range(a, b):
            if lines[i].strip().startswith(key + ":"):
                pad = " " * (len(lines[i]) - len(lines[i].lstrip()))
                # Keep any trailing comment on the line: on reject_min that comment IS the
                # measurement the number came from, and dropping it leaves a bare threshold
                # nobody can defend.
                tail = ""
                if "#" in lines[i]:
                    tail = "   " + lines[i][lines[i].index("#"):]
                lines[i] = f"{pad}{key}: {rendered}{tail}"
                done = True
                break
        if not done and val is not None:
            lines.insert(a + 1, f"{indent}  {key}: {rendered}")
            b += 1
    if add_prompt:
        where = _prompts_insert_at(lines, a, b)
        if where is None:
            pad = indent + "  "
            lines[b:b] = [f"{pad}prompts:", f"{pad}  - {_scalar(add_prompt)}"]
        else:
            at, bullet = where
            lines[at:at] = [f"{bullet}- {_scalar(add_prompt)}"]
    return "\n".join(lines) + "\n"


def _scalar(s: str) -> str:
    """Quote only when YAML would otherwise misread it, so the file keeps its plain look."""
    s = str(s)
    if s != s.strip() or not s:
        return repr(s)
    if s[0] in "-?:,[]{}#&*!|>'\"%@`" or ": " in s or s.endswith(":"):
        return "'" + s.replace("'", "''") + "'"
    return s


def _render_preserving(old_text: str, old_doc: dict, new_doc: dict) -> str:
    """Write `new_doc` by editing `old_text` only where the two differ.

    The callers all build a whole document and hand it over, which is the clearest way to
    express an intent, and yaml.safe_dump is the obvious way to write one back. It is also
    how 249 comment lines were destroyed on the first save: dump round-trips the data and
    nothing else. Rather than make every caller speak in edits, the difference is worked out
    here and applied as edits, so a save touches the lines it means to and no others.

    Anything this cannot express falls through to safe_dump with a warning, because a policy
    that refuses to save is worse than one that saves plainly - but the fallback is loud, so
    it is a bug report rather than a silent loss.
    """
    text = old_text
    old_labels = (old_doc.get("labels") or {})
    new_labels = (new_doc.get("labels") or {})

    for name in list(old_labels):
        if name not in new_labels:
            text = edit_policy_text(text, name, delete=True)

    for name, entry in new_labels.items():
        if name not in old_labels:
            text = edit_policy_text(text, name, new_entry=entry)
            continue
        before, after = old_labels[name] or {}, entry or {}
        if bool(before.get("unsafe", True)) != bool(after.get("unsafe", True)):
            text = edit_policy_text(text, name, set_unsafe=bool(after.get("unsafe", True)))
        for key in ("category", "reject_min"):
            if before.get(key) == after.get(key):
                continue
            if after.get(key) is None:
                # Gone, not merely unchanged. Without this the differ could only ever add or
                # overwrite a field, so a label flipped to allow kept the bar and the category
                # it no longer has any use for.
                text = edit_policy_text(text, name, remove_field=key)
            else:
                text = edit_policy_text(text, name, set_field=(key, after[key]))
        old_p = list(before.get("prompts") or [])
        new_p = list(after.get("prompts") or [])
        for p in old_p:
            if not any(_norm_phrase(p) == _norm_phrase(q) for q in new_p):
                text = edit_policy_text(text, name, remove_prompt=p)
        for p in new_p:
            if not any(_norm_phrase(p) == _norm_phrase(q) for q in old_p):
                text = edit_policy_text(text, name, add_prompt=p)

    try:
        check = yaml.safe_load(text) or {}
        if (check.get("labels") or {}).keys() != new_labels.keys():
            raise ValueError("label set does not match after editing")
        for k, v in new_labels.items():
            got = (check["labels"].get(k) or {})
            if [_norm_phrase(x) for x in (got.get("prompts") or [])] != \
               [_norm_phrase(x) for x in (v.get("prompts") or [])]:
                raise ValueError(f"prompts for {k!r} do not match after editing")
            if bool(got.get("unsafe", True)) != bool(v.get("unsafe", True)):
                raise ValueError(f"unsafe for {k!r} does not match after editing")
    except Exception as exc:
        log.warning("could not edit the policy in place (%s) - rewriting it, which loses the "
                    "comments. This is a bug worth reporting.", exc)
        return yaml.safe_dump(new_doc, sort_keys=False, allow_unicode=True)
    return text


def _apply(policy: Path, doc: dict, model_dir: Path) -> tuple[bool, str]:
    """Write, rebuild, reload - or put everything back. See the module docstring."""
    staged = policy.with_suffix(".yaml.staged")
    try:
        shutil.copy2(policy, staged)
    except Exception:
        return False, "could not stage the current policy; nothing was changed"
    try:
        before_text = staged.read_text(encoding="utf-8")
        # An explicit LF newline, because the default rewrites every line ending to the
        # platform's on Windows - turning a one-line change into a 484-line diff and burying
        # what the admin actually did.
        with policy.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(_render_preserving(before_text, yaml.safe_load(before_text) or {}, doc))
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
        # The categories that HAVE a readable name, so the editor can offer them instead of
        # letting the field default to the rule name. That default is how `abuses` was minted
        # next to the `abuse` that already meant the same thing, and nothing on the screen
        # said the two were different or that one of them had no name for the author.
        "named_categories": _named_categories(),
        "balance": _prompt_balance(doc),
    })


def _feedback():
    """The live author-facing wording, loaded from config/feedback_messages.yaml at startup."""
    return current_app.extensions["moderation"].pipeline.feedback


def _named_categories() -> dict:
    """{category: readable name} from config/feedback_messages.yaml, for the rule editor."""
    return dict(_feedback().categories or {})


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

    # ONLY ASK ABOUT WHAT THIS EDIT INTRODUCES. Checking every wording on every save meant
    # four of the fifteen shipped rules - minor, romantic, unrest and safe - could not be
    # edited at all: they overlap the `safe` rule closely enough to trip the similarity bar,
    # they have shipped that way from the start, and changing one rule's reject_min asked the
    # admin to settle a contradiction they had not made and could only clear by deleting a
    # wording they had not written.
    #
    # A contradiction already in the file is not news. What is news is an edit that creates
    # one, which is either a wording being ADDED, or a verdict being FLIPPED - and a flip
    # changes the meaning of every wording the rule already holds, so that case checks them
    # all. A brand new rule is all-new by definition.
    previous = (doc["labels"].get(key) or {}) if existed else None
    check_prompts = prompts_this_edit_introduces(previous, entry)
    conflicts = find_conflicts(doc, key, check_prompts, entry["unsafe"], model_dir)
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
                        current_app.extensions["moderation"].settings.visual._labels)),
                    # What this save means for the author and for the rest of the policy -
                    # both of which are decided here and reported nowhere else.
                    "consequences": _consequences(doc, key, _feedback())})


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


# How alike two wordings must be to count as the same rule. Measured against
# "a photo of rainbow": the ways a person rephrases it - "a photo of a rainbow", "a rainbow
# photo", "rainbow", "a photo of a rainbow in the sky" - score 0.923 to 0.990, while unrelated
# subjects (a handgun, a wedding, a baby, steel pipes) score 0.743 to 0.844. Nothing lands
# between 0.845 and 0.922, so the bar goes in that gap rather than being picked by feel.
PHRASE_SAME_MIN = 0.88


def _semantic_clashes(doc: dict, label: str, prompts: list[str], unsafe: bool,
                      model_dir: Path) -> list[dict]:
    """Conflicts between wordings that MEAN the same thing without being the same string.

    The first version compared normalised text, which caught "A photo of Rainbow." against
    "a photo of rainbow" and nothing else. That is not how the clash arises: an admin writes
    an allowing rule, uploads a picture weeks later and describes it afresh, and the two
    sentences agree in meaning and differ in wording. Measured, one extra article was enough
    to slip through - "a photo of a rainbow" against "a photo of rainbow" went undetected -
    so the guard existed and never fired on the case it was built for.

    Compared in CLIP's own text space, because that is the space the rules are scored in: two
    wordings the encoder places together WILL match the same pictures, whatever a human thinks
    of them. The existing wordings are already encoded in prompts.npz, so this costs one
    subprocess for the new sentences rather than re-encoding the whole policy.
    """
    import numpy as np
    npz = model_dir / "prompts.npz"
    script = Path(current_app.extensions["moderation"].settings.visual.root) / "scripts" / "encode_prompts.py"
    if not npz.exists() or not script.exists():
        return []
    try:
        data = np.load(npz, allow_pickle=False)
        baked = data["embeds"].astype(np.float32)
        baked_prompts = [str(x) for x in data["prompts"]]
        baked_labels = [str(x) for x in data["labels"]]
        proc = subprocess.run([sys.executable, str(script), "--model-dir", str(model_dir)],
                              input=json.dumps(list(prompts)), capture_output=True, text=True,
                              timeout=BUILD_TIMEOUT_S, cwd=str(script.parent.parent))
        if proc.returncode != 0:
            log.warning("conflict check could not encode: %s", (proc.stderr or "")[-200:])
            return []
        new = np.asarray(json.loads(proc.stdout)["embeds"], dtype=np.float32)
    except Exception:
        log.exception("semantic conflict check failed - falling back to exact matching only")
        return []
    if new.shape[1] != baked.shape[1]:
        return []

    verdict_of = {k: ("refuse" if (e or {}).get("unsafe", True) else "allow")
                  for k, e in (doc.get("labels") or {}).items()}
    mine = "refuse" if unsafe else "allow"
    out, seen = [], set()
    sims = new @ baked.T
    for i, phrase in enumerate(prompts):
        for j in np.argsort(-sims[i]):
            s = float(sims[i, j])
            if s < PHRASE_SAME_MIN:
                break
            other = baked_labels[j]
            if other == label or verdict_of.get(other, "refuse") == mine:
                continue
            key = (phrase, other, baked_prompts[j])
            if key in seen:
                continue
            seen.add(key)
            out.append({"phrasing": baked_prompts[j], "your_wording": phrase,
                        "other_label": other, "other_verdict": verdict_of.get(other, "refuse"),
                        "this_verdict": mine, "similarity": round(s, 4)})
    return out


def prompts_this_edit_introduces(previous: dict | None, entry: dict) -> list[str]:
    """The wordings this save is actually putting into a new position, and so the only ones
    worth asking the admin about.

    Checking every wording on every save meant four of the fifteen shipped rules - minor,
    romantic, unrest and safe - could not be edited at all. They overlap the `safe` rule
    closely enough to trip the similarity bar, they have shipped that way from the start, and
    so changing one rule's reject_min asked the admin to settle a contradiction they had not
    made and could only clear by deleting a wording they had not written. A contradiction
    already sitting in the file is not news.

    What IS news is an edit that creates one:

      * a rule being created      - every wording in it is new
      * a verdict being flipped   - every wording it already holds now means the opposite,
                                    which is exactly the "Stop refusing" press
      * a wording being added     - just that wording

    `previous` is None for a rule that does not exist yet.
    """
    if previous is None:
        return list(entry.get("prompts") or [])
    if bool(previous.get("unsafe", True)) != bool(entry.get("unsafe", True)):
        return list(entry.get("prompts") or [])
    had = {_norm_phrase(p) for p in (previous.get("prompts") or [])}
    return [p for p in (entry.get("prompts") or []) if _norm_phrase(p) not in had]


def find_conflicts(doc: dict, label: str, prompts: list[str], unsafe: bool,
                   model_dir: Path | None = None) -> list[dict]:
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
    # Meaning first, since that is the case this exists for; identical strings are a subset
    # the encoder also catches, but the exact pass below costs nothing and still runs when the
    # encoder is unavailable.
    if model_dir is not None:
        out.extend(_semantic_clashes(doc, label, [p for p in prompts if _norm_phrase(p)],
                                     unsafe, model_dir))
    already = {(c["other_label"], _norm_phrase(c["phrasing"])) for c in out}
    for other, entry in (doc.get("labels") or {}).items():
        if other == label or not isinstance(entry, dict):
            continue
        if bool(entry.get("unsafe", True)) == bool(unsafe):
            continue
        for p in (entry.get("prompts") or []):
            if _norm_phrase(p) in want and (other, _norm_phrase(p)) not in already:
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
    conflicts = find_conflicts(doc, key, [phrasing], unsafe, model_dir)
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
            "phrasings": len(prompts), "error": None if ok else msg,
            "consequences": _consequences(doc, key, _feedback()) if ok else None}


def _near_categories(category: str, named: dict) -> dict:
    """Curated categories that look like the same idea as `category`, {slug: readable name}.

    Deliberately crude - singular/plural and separator differences only. The job is to catch a
    category being minted next to one that already exists, and anything cleverer would start
    offering `violence_or_unrest` for `unrest` and teaching admins to click past the question.
    """
    def key(c: str) -> str:
        c = re.sub(r"[^a-z0-9]+", "", str(c).lower())
        if c.endswith("ies"):          # parties -> party
            return c[:-3] + "y"
        if c.endswith("s") and not c.endswith("ss"):   # abuses -> abuse, weapons -> weapon
            return c[:-1]
        return c

    k = key(category)
    if not k:
        return {}
    return {c: n for c, n in named.items() if c != category and key(c) == k}


def _prompt_balance(doc: dict) -> dict:
    """How many refusing phrasings the policy holds against how many allowing ones.

    A property of the whole file, not of any one rule, and the least visible thing about it.
    Scores are a softmax over every phrasing, so the allowing list is what the refusing ones
    are measured against - the policy file says so in as many words - and adding refusing
    wordings without adding allowing ones raises the unsafe score of every ORDINARY
    photograph. The shipped policy sits at 82 against 81, a margin that one new rule erases,
    and four rules have been added here since. It surfaces as "the checker started refusing
    catalogues", weeks after the save that caused it, which is why this is reported on the
    screen that shows the policy and not only in the reply to a save.
    """
    labels = doc.get("labels") or {}
    safe_n = sum(len((e or {}).get("prompts") or [])
                 for e in labels.values() if not (e or {}).get("unsafe"))
    unsafe_n = sum(len((e or {}).get("prompts") or [])
                   for e in labels.values() if (e or {}).get("unsafe"))
    out = {"safe_prompts": safe_n, "unsafe_prompts": unsafe_n,
           "safe_margin": safe_n - unsafe_n}
    if safe_n < unsafe_n:
        out["warning"] = (
            f"There are now {unsafe_n} refusing phrasings against {safe_n} allowing ones. Every "
            "score is a softmax over all of them, so the allowing list is what the refusing "
            "ones are measured against, and with fewer of them ordinary photographs score "
            "higher as unsafe. Worth knowing rather than acting on blindly: the refusals you "
            "actually see are decided first by each rule's own bar and by the probe, and this "
            "total only decides the ones they leave over. Measured at 88 against 85, adding "
            "twelve allowing phrasings moved the evaluation by four fewer wrong refusals and "
            "eight fewer correct ones - so measure with scripts/eval_clip_images.py before and "
            "after rather than adding wordings until the number goes green.")
    return out


def _consequences(doc: dict, label: str, fb) -> dict:
    """What a saved rule means for the author and for the rest of the policy.

    THE STEP THAT WAS MISSING. Creating a rule here writes config/visual_policy.yaml and
    rebuilds prompts.npz, and it is live from that moment. But the NAME the author is told is
    in a different file - config/feedback_messages.yaml - that nothing in this flow touches, so
    a new category arrives with no readable name and the refusal reads "It appears to break
    BONC's community policies on abuses". Three of the four rules admins have added here were
    noticed and hand-named later; `abuses` was not, and the only thing that ever reported it
    was a unit test, days afterwards, with nothing connecting it to the action that caused it.

    So the save says what it did. `author_sees` is the exact phrase that will be published,
    and `category_curated` is false when it is the derived fallback rather than a chosen name -
    which is the admin's cue either to reuse an existing category or to ask for a real name.

    `safe_prompts` / `unsafe_prompts` are here for the other invisible consequence. Scores are
    a softmax over every phrasing in the file, so the safe list is what the unsafe ones are
    judged against: adding unsafe wordings without adding safe ones raises the unsafe score of
    every ordinary photograph, and the shipped policy sits at 82 against 81 - a margin one rule
    erases. It arrives as "the checker started refusing catalogues", long after the save.
    """
    labels = doc.get("labels") or {}
    entry = labels.get(label) or {}
    out = _prompt_balance(doc)
    out["label"] = label
    if not entry.get("unsafe"):
        return out
    category = str(entry.get("category") or label)
    curated = bool((fb.categories or {}).get(category))
    out["author_sees"] = fb.category_name(category)
    out["category"] = category
    out["category_curated"] = curated
    if not curated:
        # DID YOU MEAN THE ONE THAT ALREADY EXISTS? `abuses` was minted beside the `abuse`
        # that already meant "abusive language", because the editor's category box defaulted
        # to the rule name and nothing compared the two. A slug-level near-match is not proof
        # of a mistake - `minor` and `minors` are genuinely one idea, `parties` and `party`
        # too - but it is exactly the moment to ask, and the only moment anyone can answer.
        near = _near_categories(category, fb.categories or {})
        if near:
            out["similar_categories"] = near
            out["naming_hint"] = (
                f"The category {category!r} is new and has no readable name, so an author "
                f"refused by this rule is told \"...policies on {out['author_sees']}\". "
                + "Did you mean " + ", ".join(f"{k!r} ({v})" for k, v in near.items())
                + "? Reusing it gives the author the written name; keeping this one needs a "
                  "name added under `categories:` in config/feedback_messages.yaml.")
            return out
        out["naming_hint"] = (
            f"No readable name is set for the category {category!r}, so an author refused by "
            f"this rule is told \"...policies on {out['author_sees']}\". Point the rule at an "
            "existing category, or add a name under `categories:` in "
            "config/feedback_messages.yaml (it is read at startup).")
    return out


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

    # WHAT ELSE THIS WOULD CATCH. An exemplar generalises by appearance, and appearance does
    # not respect the categories a person has in mind: a rainbow marked "refuse things like
    # this" scored 0.828 against a cloud photograph, because both are soft-coloured sky, and
    # the cloud was refused with nothing in the policy naming clouds. Deselecting it and
    # retraining the probe could not help - the probe was not the one objecting.
    #
    # So the reach is measured and shown before the mark is applied. Grouped by the category
    # the matched pictures are filed under, because the surprise is never "it caught more"
    # but "it caught THOSE".
    if body.get("preview"):
        rows = _rows_by_id(store, ids)
        if not rows:
            return _err(404, "no_such_image", "None of those image ids are stored.")
        visual = current_app.extensions["moderation"].settings.visual
        bar = float(visual.config.exemplar_reject_min)
        import numpy as np
        seeds = []
        for r in rows:
            v = np.frombuffer(r["embedding"], dtype=np.float32)
            if v.size:
                seeds.append(v / max(float(np.linalg.norm(v)), 1e-12))
        if not seeds:
            return _err(409, "no_embeddings", "Those pictures have no embedding to compare.")
        S = np.stack(seeds)
        others, meta = [], []
        for r in store.all_embeddings(limit=20000):
            if r["id"] in set(ids):
                continue
            v = np.frombuffer(r["embedding"], dtype=np.float32)
            if v.size == S.shape[1]:
                others.append(v / max(float(np.linalg.norm(v)), 1e-12))
                meta.append(r)
        if not others:
            return jsonify({"preview": True, "would_match": 0, "by_category": {}, "top": []})
        sims = (np.stack(others) @ S.T).max(axis=1)
        hit = np.where(sims >= bar)[0]
        by_cat: dict[str, int] = {}
        for i in hit:
            c = meta[i].get("category") or "?"
            by_cat[c] = by_cat.get(c, 0) + 1
        order = hit[np.argsort(-sims[hit])][:12]
        own = {r.get("category") for r in rows}
        return jsonify({
            "preview": True, "mode": mode, "threshold": bar,
            "would_match": int(len(hit)),
            "by_category": dict(sorted(by_cat.items(), key=lambda kv: -kv[1])),
            # Categories other than the marked picture's own: this is the list that surprises.
            "other_categories": {k: n for k, n in by_cat.items() if k not in own},
            "top": [{"id": meta[i]["id"], "filename": meta[i].get("filename"),
                     "category": meta[i].get("category"),
                     "score": round(float(sims[i]), 4)} for i in order],
        })

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
