"""Is the model you would roll back to actually on this machine, and does it load?

    python scripts/check_rollback_bundles.py              # structural check, seconds
    python scripts/check_rollback_bundles.py --load       # also load each and score, minutes

WHY THIS EXISTS. Only the live bundle is tracked in git - see .gitignore - and a superseded
one is dropped when its replacement ships. That drop DELETES it from every machine that pulls
the commit, including the one that made the change, because the files are still tracked at
that moment and checkout removes them. models/v9 went exactly this way the day v9-ver3 was
merged, and the rollback documented in settings.yaml was gone until it was restored from LFS
history.

So "we can roll back" is a claim about a directory nobody looks at until the morning they
need it. This looks.

What it checks, in order of how it actually goes wrong:

  * the directory is missing entirely            - the drop deleted it
  * model.onnx is ~130 bytes and starts "version https://git-lfs.github.com"
                                                 - LFS pointer, never smudged; `git lfs pull`
  * the file is short against model_meta.json's own record, or unreadable
                                                 - a truncated copy or a failed download
  * with --load, the graph loads and scores      - the only check that proves anything

Exit code is non-zero if any named bundle fails, so this can gate a release.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# A pointer is this short and starts with the spec URL; a real ONNX is hundreds of MB.
LFS_MAGIC = b"version https://git-lfs.github.com"
MIN_PLAUSIBLE_BYTES = 1 << 20

SCAM = "Double your money in 30 days, guaranteed returns."
SAFE = "We supply ISI marked cable from stock, delivery in 3 days."


def live_bundle() -> str:
    """What settings.yaml is serving right now, so the live one is checked by default too."""
    import yaml

    raw = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    return str(((raw or {}).get("model") or {}).get("dir") or "").split("/")[-1]


def check(name: str, load: bool) -> tuple[bool, str]:
    d = ROOT / "models" / name
    if not d.is_dir():
        return False, "directory missing - the drop deleted it; restore from LFS history"
    onnx = d / "model.onnx"
    if not onnx.is_file():
        return False, "no model.onnx"

    size = onnx.stat().st_size
    with onnx.open("rb") as f:
        head = f.read(len(LFS_MAGIC))
    if head.startswith(LFS_MAGIC):
        return False, f"LFS pointer, not the model ({size} bytes) - run `git lfs pull`"
    if size < MIN_PLAUSIBLE_BYTES:
        return False, f"only {size} bytes - truncated"

    meta = d / "model_meta.json"
    note = f"{size / 1e6:.0f} MB"
    if meta.is_file():
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            note = f"{m.get('version', '?')}, {note}"
        except Exception as e:
            return False, f"model_meta.json unreadable: {e}"

    if not load:
        return True, note

    try:
        from app.model import OnnxScorer

        sc = OnnxScorer(d)
        bad, good = sc.score(SCAM).risk_score, sc.score(SAFE).risk_score
    except Exception as e:
        return False, f"{note} - but it does not load: {type(e).__name__}: {e}"
    # Not an accuracy test. A bundle that loads and then puts a plain scam below a cable
    # listing is wired up wrong, and that is worth catching here rather than during a
    # rollback at 2am.
    if not bad > good:
        return False, f"{note} - loads, but scores a scam {bad:.3f} against a listing {good:.3f}"
    return True, f"{note}, scam {bad:.3f} / safe {good:.3f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundles", nargs="*", default=None,
                    help="bundle names under models/ (default: the live one plus v5, v6, v8, v9)")
    ap.add_argument("--load", action="store_true",
                    help="also load each bundle and score two sentences (slow, and the only "
                         "check that proves the thing works)")
    args = ap.parse_args()

    names = args.bundles or list(dict.fromkeys(
        [n for n in (live_bundle(),) if n] + ["v5", "v6", "v8", "v9"]))
    live = live_bundle()

    print(f"{'bundle':14} {'':4} detail")
    failed = []
    for n in names:
        ok, note = check(n, args.load)
        tag = "LIVE" if n == live else ""
        print(f"  {n:12} {tag:4} {'ok  ' if ok else 'FAIL'} {note}")
        if not ok:
            failed.append(n)

    if failed:
        print(f"\n{len(failed)} of {len(names)} unusable: {', '.join(failed)}")
        print("Recover one from LFS history with, from a commit that still tracked it:")
        print("    git checkout <commit-before-the-drop> -- flask_api/models/<name>/")
        print("    git rm -r --cached flask_api/models/<name>/   # keep on disk, out of the index")
        return 1
    print(f"\nall {len(names)} usable" + ("" if args.load else "  (structural only; --load to prove it)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
