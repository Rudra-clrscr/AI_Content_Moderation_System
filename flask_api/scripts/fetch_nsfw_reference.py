"""Download the NSFW reference classifier, verified against pinned SHA256s.

    python scripts/fetch_nsfw_reference.py
    python scripts/fetch_nsfw_reference.py --verify

This is **not** part of the moderation service. It is a second opinion used by
`scripts/eval_sexual_label.py` to measure the `sexual` label's recall on real pictures, because
the alternative - collecting pornography to test against - is not one worth taking (see
`docs/measuring_the_sexual_label.md`). Nothing in `app/` loads it, and the service behaves
identically whether or not it is present.

Weights: Falconsai/nsfw_image_detection, a ViT-base fine-tuned for NSFW classification, Apache
2.0, so it is usable commercially if it ever earns promotion from referee to player. This is
the ONNX export, so it runs on the same onnxruntime as everything else and needs no PyTorch.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = ROOT / "models" / "nsfw_ref"
REPO = "onnx-community/nsfw_image_detection-ONNX"
# Pinned: a model repo can be updated underneath you, and a different referee would silently
# change every number this produces.
REVISION = "main"
BASE = f"https://huggingface.co/{REPO}/resolve/{REVISION}"

FILES: dict[str, tuple[str, str, int]] = {
    "model.onnx": ("onnx/model.onnx",
                   "a4316a4fb750169ac4fcabaabee1fcbd982b0ee8c0cc63fe3e944954bb9a7d9c", 343_385_088),
    "config.json": ("config.json",
                    "0e01783cf842a0acaa6b3b6594b941bee4d39a1918756b27d23f503878889696", 0),
    "preprocessor_config.json": ("preprocessor_config.json",
                                 "ae9bb157b9629887cc74913a4e7c12c9308f374f0930e8072320e8f2e1583c5e", 0),
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(name: str, dest: Path) -> bool:
    repo_path, want, size = FILES[name]
    out = dest / name
    if out.exists() and sha256_file(out) == want:
        print(f"  {name}: already here and verified")
        return True

    url = f"{BASE}/{repo_path}"
    dest.mkdir(parents=True, exist_ok=True)
    print(f"  {name}: {size / 1e6:.0f} MB" if size else f"  {name}")
    tmp = None
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            with tempfile.NamedTemporaryFile(dir=dest, delete=False, suffix=".part") as fh:
                tmp = Path(fh.name)
                read = 0
                while chunk := resp.read(1 << 20):
                    fh.write(chunk)
                    read += len(chunk)
                    if size:
                        print(f"\r    {read / 1e6:7.0f} / {size / 1e6:.0f} MB", end="", flush=True)
        if size:
            print()
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        if tmp and tmp.exists():
            tmp.unlink()
        print(f"  {name}: download failed: {e}", file=sys.stderr)
        return False

    got = sha256_file(tmp)
    if got != want:
        tmp.unlink()
        print(f"  {name}: SHA256 mismatch\n    expected {want}\n    got      {got}", file=sys.stderr)
        return False
    shutil.move(str(tmp), out)
    print(f"  {name}: verified")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--verify", action="store_true", help="check what is there, download nothing")
    args = ap.parse_args()

    if args.verify:
        ok = True
        for name, (_, want, _) in FILES.items():
            path = args.dest / name
            if not path.exists():
                print(f"  {name}: missing")
                ok = False
                continue
            good = sha256_file(path) == want
            print(f"  {name}: {'verified' if good else 'HASH MISMATCH'}")
            ok = ok and good
        return 0 if ok else 1

    print(f"{REPO} -> {args.dest}")
    failed = [n for n in FILES if not download(n, args.dest)]
    if failed:
        print(f"\nfailed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"\nNext: python scripts/eval_sexual_label.py --images ../images")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
