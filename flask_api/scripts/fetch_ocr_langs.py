"""Download a RapidOCR recognition model for a script the shipped package doesn't cover.

    python scripts/fetch_ocr_langs.py devanagari      # Hindi, Marathi, Nepali
    python scripts/fetch_ocr_langs.py --list          # what's available
    python scripts/fetch_ocr_langs.py devanagari --dest models/ocr

RapidOCR bundles a Latin and a Chinese recognition model only. Detection finds the writing in
any script, so a Hindi picture comes back with every line located and near-nothing recognised
("तुम सब बेवकूफ हो" read as "上 亚"). app/ocr.py now calls that unreadable rather than clean, so
until the Devanagari model is in place those uploads are refused instead of published — correct,
but it refuses ordinary Hindi signage too. This script closes that gap.

Models are fetched **here, once**, not at request time: the service must not depend on
modelscope.cn being up to moderate an upload, and an air-gapped deployment copies
`models/ocr/` across with the rest of the bundle. Each file is checked against the SHA256
RapidOCR itself records, downloaded to a temp name, and only moved into place once it verifies.

Then point the service at it (config/settings.yaml, under pdf.ocr):

    rec_model_path: models/ocr/devanagari_rec.onnx
    rec_lang: devanagari

The ONNX recognition models carry their own character list, so no dictionary file is needed.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = ROOT / "models" / "ocr"
# RapidOCR's own manifest of model URLs and hashes, so this script has no list of its own to
# drift out of date. Installed with the package.
MANIFEST = "rapidocr/default_models.yaml"


def manifest() -> dict:
    import yaml

    for parent in sys.path:
        path = Path(parent) / MANIFEST
        if path.exists():
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raise SystemExit(f"could not find {MANIFEST}; is rapidocr installed in this environment?")


def recognizers(raw: dict) -> dict[str, tuple[str, str, str]]:
    """{lang: (version, url, sha256)} for every recognition model the manifest lists."""
    out: dict[str, tuple[str, str, str]] = {}
    for version, tasks in (raw.get("onnxruntime") or {}).items():
        for name, spec in ((tasks or {}).get("rec") or {}).items():
            url, sha = spec.get("model_dir", ""), spec.get("SHA256", "")
            if not url:
                continue
            # "devanagari_PP-OCRv4_rec_mobile" -> "devanagari"
            lang = name.split("_")[0]
            # Prefer the newest version of a language when several are listed.
            if lang not in out or out[lang][0] < version:
                out[lang] = (version, url, sha)
    return out


def download(url: str, dest: Path, sha256: str) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False, suffix=".part") as tmp:
        staged = Path(tmp.name)
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                while chunk := resp.read(1 << 20):
                    digest.update(chunk)
                    tmp.write(chunk)
        except Exception:
            tmp.close()
            staged.unlink(missing_ok=True)
            raise
    if sha256 and digest.hexdigest() != sha256:
        staged.unlink(missing_ok=True)
        raise SystemExit(f"checksum mismatch for {url}\n  expected {sha256}\n  got      {digest.hexdigest()}")
    shutil.move(str(staged), dest)
    return dest.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lang", nargs="*", help="script to fetch, e.g. devanagari")
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--list", action="store_true", help="list the available scripts and exit")
    args = ap.parse_args()

    available = recognizers(manifest())
    if args.list or not args.lang:
        print(f"{len(available)} recognition models available:")
        for lang, (version, _, _) in sorted(available.items()):
            print(f"  {lang:15} {version}")
        return 0

    unknown = [lang for lang in args.lang if lang not in available]
    if unknown:
        print(f"unknown script(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"available: {', '.join(sorted(available))}", file=sys.stderr)
        return 2

    for lang in args.lang:
        version, url, sha = available[lang]
        dest = args.dest / f"{lang}_rec.onnx"
        print(f"{lang} ({version}) -> {dest}")
        size = download(url, dest, sha)
        print(f"  {size:,} bytes, sha256 verified")

    rel = (args.dest / f"{args.lang[0]}_rec.onnx").relative_to(ROOT).as_posix()
    print("\nNow set these under pdf.ocr in config/settings.yaml:")
    print(f"    rec_model_path: {rel}")
    print(f"    rec_lang: {args.lang[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
