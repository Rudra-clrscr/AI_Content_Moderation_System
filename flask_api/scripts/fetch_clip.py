"""Download the CLIP image model into models/img_v1, verified against pinned SHA256s.

    python scripts/fetch_clip.py                      # the serving file (352 MB)
    python scripts/fetch_clip.py --with-text-encoder  # also the 254 MB text encoder
    python scripts/fetch_clip.py --dest models/img_v1

This is the image bundle. It is a **separate model from the text moderation model** in
`models/v5`: that one scores words, this one scores pictures, they version apart, and neither
stands in for the other. Rolling one back does not touch the other.

Fetched **here, once**, never at request time: the service must not depend on huggingface.co
being up to moderate an upload, and an air-gapped deployment copies `models/img_v1/` across
with the rest. Each file goes to a temp name, is checked against the hash below, and is only
moved into place once it verifies — so a re-uploaded or truncated file is caught here rather
than becoming a model that silently scores differently.

Two files are needed to serve, and the second is built, not downloaded:

    vision_model.onnx   this script
    prompts.npz         python scripts/build_clip_prompts.py   (needs --with-text-encoder once)

The text encoder is only needed to build `prompts.npz` from `config/visual_policy.yaml`, which
is an offline step. A serving host does not need it, and `--with-text-encoder` exists for the
host that edits the policy.

Weights: openai/clip-vit-base-patch32, ONNX export by Xenova, MIT licence.
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
DEFAULT_DEST = ROOT / "models" / "img_v1"
REPO = "Xenova/clip-vit-base-patch32"
# Pinned to a revision, not to `main`: a model repo can be updated under you, and a different
# vision encoder would move every score in config/visual_policy.yaml without anything failing.
REVISION = "d15189d7028b43f1d3e65039190477f6af591c2a"
BASE = f"https://huggingface.co/{REPO}/resolve/{REVISION}"

# name -> (path in the repo, sha256, bytes, needed to serve?)
FILES: dict[str, tuple[str, str, int, bool]] = {
    "vision_model.onnx": (
        "onnx/vision_model.onnx",
        "fd6e1402a588279d1723c7534d4bcba5bc0b14b47dfab0e46f8c47b8270d7d40", 351_685_709, True),
    "text_model.onnx": (
        "onnx/text_model.onnx",
        "3f6571f5bad13a97c469c1622e1cfc4d9aef78b79fdbfcff804ca357bfada8cc", 254_058_553, False),
    "tokenizer.json": (
        "tokenizer.json",
        "f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd", 2_224_119, False),
}
SERVING = [n for n, (_, _, _, serve) in FILES.items() if serve]
BUILD_ONLY = [n for n, (_, _, _, serve) in FILES.items() if not serve]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(name: str, dest: Path) -> bool:
    repo_path, want, size, _ = FILES[name]
    out = dest / name
    if out.exists():
        if sha256_file(out) == want:
            print(f"  {name}: already here and verified")
            return True
        print(f"  {name}: present but the hash does not match — re-downloading", file=sys.stderr)

    url = f"{BASE}/{repo_path}"
    dest.mkdir(parents=True, exist_ok=True)
    print(f"  {name}: {size / 1e6:.0f} MB from {url}")
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
        print()
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        if tmp and tmp.exists():
            tmp.unlink()
        print(f"  {name}: download failed: {e}", file=sys.stderr)
        return False

    got = sha256_file(tmp)
    if got != want:
        tmp.unlink()
        # Never move a file we cannot vouch for into the model directory: a wrong vision
        # encoder scores every picture differently and nothing else would notice.
        print(f"  {name}: SHA256 mismatch\n    expected {want}\n    got      {got}", file=sys.stderr)
        return False
    shutil.move(str(tmp), out)
    print(f"  {name}: verified")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--with-text-encoder", action="store_true",
                    help="also fetch the text encoder and tokenizer, needed only to run "
                         "scripts/build_clip_prompts.py after editing the policy file")
    ap.add_argument("--verify", action="store_true", help="check what is there and download nothing")
    args = ap.parse_args()

    wanted = SERVING + (BUILD_ONLY if args.with_text_encoder else [])

    if args.verify:
        ok = True
        for name in FILES:
            path = args.dest / name
            if not path.exists():
                need = "needed to serve" if FILES[name][3] else "only needed to build prompts"
                print(f"  {name}: missing ({need})")
                ok = ok and not FILES[name][3]
                continue
            good = sha256_file(path) == FILES[name][1]
            print(f"  {name}: {'verified' if good else 'HASH MISMATCH'}")
            ok = ok and good
        return 0 if ok else 1

    print(f"{REPO} @ {REVISION[:12]} -> {args.dest}")
    failed = [name for name in wanted if not download(name, args.dest)]
    if failed:
        print(f"\nfailed: {', '.join(failed)}", file=sys.stderr)
        return 1

    print(f"\n{len(wanted)} file(s) in {args.dest}")
    if not (args.dest / "prompts.npz").exists():
        print("\nNext: python scripts/build_clip_prompts.py"
              + ("" if args.with_text_encoder else
                 "\n  (needs the text encoder: re-run this with --with-text-encoder)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
