"""Fetch the image files a labels.csv describes, verifying each against its recorded SHA256.

    python scripts/fetch_label_images.py ../images/labels.csv

The image datasets under `images/` are third-party photographs under their own licences, so
like `res/` they are not redistributed in this repo - only the CSV is, and it records for every
row where the file came from (`source_url`), what it should hash to (`sha256`), and under what
licence. This script turns that back into files on disk, and refuses anything whose bytes do
not match, so a moved or re-encoded upstream file is caught rather than quietly changing what
the evaluation is measuring.

Paths in `image_path` are resolved against `--root`, which defaults to the CSV's own directory.
That suits a CSV shipped inside its dataset folder; a CSV whose paths are written relative to
somewhere else needs the root saying, e.g. `--root .` for `images/labels.csv`, whose paths
already begin with `images/`. Files already present and matching are left alone, so the script
can be re-run to fill in whatever is missing.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UA = "bonc-moderation-dataset-fetch/1.0"
TIMEOUT = 60


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_one(row: dict, root: Path, retries: int = 3) -> tuple[str, str]:
    """Returns (status, detail). Status is "ok", "present", "skip" or "fail"."""
    rel = (row.get("image_path") or "").strip()
    url = (row.get("source_url") or "").strip()
    want = (row.get("sha256") or "").strip().lower()
    if not rel or not url:
        return "skip", f"{row.get('image_id', '?')}: no image_path or source_url"

    dest = root / rel
    if dest.exists():
        if not want or sha256(dest.read_bytes()) == want:
            return "present", rel
        return "fail", f"{rel}: on disk but the bytes do not match its recorded sha256"

    last = ""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = resp.read()
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last = f"{type(e).__name__}: {e}"
            continue
        if want and sha256(data) != want:
            # Not a transient failure: the upstream file is not the one this row describes.
            return "fail", f"{rel}: downloaded bytes do not match the recorded sha256"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return "ok", rel
    return "fail", f"{rel}: {last}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path", type=Path, help="a labels.csv with image_path, source_url, sha256")
    ap.add_argument("--root", type=Path, default=None,
                    help="resolve image_path against this directory (default: the CSV's own)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="stop after this many rows (0 = all)")
    args = ap.parse_args()

    if not args.csv_path.exists():
        print(f"no such file: {args.csv_path}", file=sys.stderr)
        return 2
    root = (args.root or args.csv_path.resolve().parent).resolve()
    with args.csv_path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if args.limit:
        rows = rows[: args.limit]
    print(f"{len(rows)} rows from {args.csv_path}, into {root}")

    counts: dict[str, int] = {}
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, (status, detail) in enumerate(pool.map(lambda r: fetch_one(r, root), rows), 1):
            counts[status] = counts.get(status, 0) + 1
            if status == "fail":
                failures.append(detail)
            if i % 50 == 0 or i == len(rows):
                print(f"  {i}/{len(rows)}  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    for f in failures[:20]:
        print("  FAIL", f, file=sys.stderr)
    if len(failures) > 20:
        print(f"  ... and {len(failures) - 20} more", file=sys.stderr)
    # A dataset with holes still evaluates, it just evaluates less - so say so and carry on.
    print(f"\n{counts.get('ok', 0)} downloaded, {counts.get('present', 0)} already there, "
          f"{len(failures)} failed, {counts.get('skip', 0)} skipped")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
