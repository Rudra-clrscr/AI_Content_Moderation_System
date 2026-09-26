"""Download the ML team's model bundle into models/current (gitignored).

    python scripts/fetch_model.py                       # latest from main
    python scripts/fetch_model.py --ref <commit-or-tag> # pin a specific release
    python scripts/fetch_model.py --dest models/v2      # stage elsewhere, then reload

Source: https://github.com/Gupta35251/BONC. The .onnx file is stored with
Git LFS, so it's fetched from GitHub's LFS media endpoint, not raw.
Files are downloaded to a temp name and only moved into place once all of
them succeed, so an interrupted download never leaves a half-written bundle.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPO = "Gupta35251/BONC"
FILES = {  # file -> served via LFS?
    "model_meta.json": False,
    "tokenizer.json": False,
    "model_int8.onnx": True,
}


def url_for(repo: str, ref: str, name: str, lfs: bool) -> str:
    host = "media.githubusercontent.com/media" if lfs else "raw.githubusercontent.com"
    return f"https://{host}/{repo}/{ref}/{name}"


def download(url: str, dest: Path) -> int:
    with urllib.request.urlopen(url, timeout=60) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out, length=1 << 20)
    return dest.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--ref", default="main")
    ap.add_argument("--dest", default="models/current")
    args = ap.parse_args()

    dest = Path(args.dest) if Path(args.dest).is_absolute() else ROOT / args.dest
    dest.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        tmp = Path(tmp)
        for name, lfs in FILES.items():
            url = url_for(args.repo, args.ref, name, lfs)
            print(f"downloading {url}")
            size = download(url, tmp / name)
            if name.endswith(".onnx") and size < 1024 and (tmp / name).read_bytes().startswith(b"version https://git-lfs"):
                print(f"error: got a Git LFS pointer instead of the model for {name}", file=sys.stderr)
                return 1
            print(f"  {size / 1e6:.1f} MB")

        meta = json.loads((tmp / "model_meta.json").read_text(encoding="utf-8"))
        dest.mkdir(exist_ok=True)
        for name in FILES:
            (tmp / name).replace(dest / name)

    print(f"model {meta.get('version')} ready in {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
