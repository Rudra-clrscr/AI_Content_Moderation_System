"""Are BOTH models actually serving on this host? Exit non-zero if not.

    python scripts/check_deploy.py                        # files on disk
    python scripts/check_deploy.py --url http://127.0.0.1:8000   # ...and the running service

WHY THIS EXISTS, and it is the single thing most worth running on a new host.

The text model arrives with the repository: models/v9-ver3 is tracked, so a clone plus
`git lfs pull` has it, and if it is still a 130-byte LFS pointer the service fails loudly at
startup. That half looks after itself.

The image model does not. Only four small files of models/img_v1 are in git - the prompts, the
probe and the metadata. `vision_model.onnx` is 352 MB and is fetched on the host by
scripts/fetch_clip.py. Miss that step and app/clip.py does what it is designed to do: it turns
the visual check OFF rather than taking the service down, because a missing model should not
stop text moderation working.

The result, measured on a host with the file removed:

    /ready            ->  status: "ready",  model_ready: true
    visual            ->  {"enabled": true, "ready": false, "reason": "...not in models/img_v1"}
    an image upload   ->  200 allow, visual_content_checked: false

So the service comes up green, a load balancer health check passes, text moderation works, and
every picture publishes unexamined. Nothing in the logs after the first warning, and nothing
the dashboard shows. `visual.ready` is nested two levels inside /ready and nobody reads it on
deployment day.

This reads it. Run it as the last step of a deploy, and after any change to the model files.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LFS_MAGIC = b"version https://git-lfs.github.com"
MIN_PLAUSIBLE = 1 << 20


def _settings() -> dict:
    import yaml
    return yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")) or {}


def _file(path: Path, what: str) -> tuple[bool, str]:
    if not path.is_file():
        return False, f"missing: {path.relative_to(ROOT)}"
    size = path.stat().st_size
    with path.open("rb") as f:
        if f.read(len(LFS_MAGIC)).startswith(LFS_MAGIC):
            return False, (f"{path.relative_to(ROOT)} is a {size}-byte LFS pointer, not the "
                           f"model - run `git lfs pull`")
    if size < MIN_PLAUSIBLE:
        return False, f"{path.relative_to(ROOT)} is only {size} bytes - truncated"
    return True, f"{what} {size / 1e6:.0f} MB"


def check_files() -> list[tuple[str, bool, str]]:
    s = _settings()
    out = []

    text_dir = ROOT / str((s.get("model") or {}).get("dir") or "models/v9-ver3")
    out.append(("text model", *_file(text_dir / "model.onnx", text_dir.name)))

    visual = ((s.get("media") or {}).get("visual") or {})
    if not visual.get("enabled", True):
        out.append(("image model", True, "visual.enabled is false - not required"))
        return out

    vdir = ROOT / str(visual.get("model_dir") or "models/img_v1")
    out.append(("image model", *_file(vdir / "vision_model.onnx", vdir.name)))
    # Built by build_clip_prompts.py and committed, so this is normally present - but it is
    # half of what the check needs to run and is worth naming when it is not.
    prompts = vdir / "prompts.npz"
    out.append(("image prompts", prompts.is_file(),
                "prompts.npz" if prompts.is_file()
                else "missing prompts.npz - run scripts/build_clip_prompts.py"))
    return out


def check_service(url: str) -> list[tuple[str, bool, str]]:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/ready", timeout=30) as r:
            d = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return [("service", False, f"cannot read {url}/ready: {e}")]

    out = [("service", d.get("status") == "ready", f"status={d.get('status')}")]
    out.append(("text model live", bool(d.get("model_ready")),
                f"{d.get('model_version')}" + (f" - {d['model_error']}" if d.get("model_error") else "")))

    vis = d.get("visual") or {}
    if not vis.get("enabled", True):
        out.append(("image model live", True, "visual.enabled is false"))
    else:
        # THE LINE THIS SCRIPT IS FOR. status says "ready" whether or not this is true.
        out.append(("image model live", bool(vis.get("ready")),
                    vis.get("provider") or vis.get("reason") or "not ready"))

    vid = d.get("video") or {}
    if vid.get("enabled"):
        # Whisper downloads on first start and warms in a background thread, so "not yet" is
        # normal for the first few minutes and is not a deployment failure.
        out.append(("video asr (warming is normal)", True,
                    "ready" if vid.get("ready") else "still warming - video uploads retry until it is"))

    off = d.get("moderation_disabled_for") or []
    if off:
        out.append(("surfaces with moderation OFF", False, ", ".join(off)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="also check a running service, e.g. http://127.0.0.1:8000")
    args = ap.parse_args()

    rows = check_files()
    if args.url:
        rows += check_service(args.url)

    bad = 0
    for name, ok, note in rows:
        print(f"  {'ok  ' if ok else 'FAIL'} {name:32} {note}")
        bad += 0 if ok else 1

    if bad:
        print(f"\n{bad} check(s) failed. The image model is the one that fails quietly:")
        print("    python scripts/fetch_clip.py          # vision_model.onnx, 352 MB")
        print("    python scripts/build_clip_prompts.py  # only if prompts.npz is missing")
        print("    git lfs pull                          # if a model is an LFS pointer")
        print("Until then the service answers /ready with status 'ready' and publishes every")
        print("picture unexamined (visual_content_checked: false).")
        return 1
    print("\nboth models are serving" + ("" if args.url else "  (files only; --url to check the running service)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
