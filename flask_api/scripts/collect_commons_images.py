"""Collect a labelled image set from Wikimedia Commons, with provenance for every file.

    python scripts/collect_commons_images.py body_parts --limit 100
    python scripts/collect_commons_images.py --list

Written for the sets this project cannot measure without: pictures that *should* pass but sit
close to an unsafe label. The `sexual` label in config/visual_policy.yaml has no sample images
behind it, so nothing is known about how it behaves on the legitimate human-body pictures a B2B
marketplace really carries - a dermatology clinic, a physiotherapist, a sportswear seller, a
medical equipment supplier. Those are the false positives that would hurt, and they are
collectable without going anywhere near explicit content.

Commons is used because every file carries a licence and a stable URL, so the output is a
labels.csv in the same shape as the one the weapons set ships with: image_path, source_url,
licence, dimensions and sha256 for each row. That makes the set re-fetchable by
`scripts/fetch_label_images.py` without redistributing the pictures themselves.

**This is not a way to collect explicit imagery.** Every search term is a specific benign
subject, results whose title or categories hint at nudity are dropped (`_EXCLUDE`), and the
point of the set is the pass side, not the fail side. A positive set for a sexual-content
label needs a vetted, licensed dataset obtained through its own agreement - not a scrape.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://commons.wikimedia.org/w/api.php"
UA = "bonc-moderation-research/1.0 (content moderation evaluation; contact: via repository)"

# Subjects whose pictures are ordinary, carry visible skin or human anatomy, and would be
# listed on a B2B marketplace by a clinic, a therapist or a clothing supplier. These are what
# a nudity prompt is most likely to trip over by mistake.
SETS: dict[str, list[str]] = {
    "body_parts": [
        "human hand", "human arm", "human leg", "human foot", "human knee", "human elbow",
        "human shoulder", "human back muscles", "human skin texture", "dermatology skin",
        "physiotherapy treatment", "massage therapy", "sports massage", "athlete running",
        "swimming competition", "gymnastics athlete", "weightlifting athlete", "yoga pose",
        "tattoo on arm", "medical bandage", "prosthetic limb", "anatomical model human",
        "surgical glove hand", "manicure hand", "barefoot sand", "boxing training",
        "swimwear product", "sportswear clothing", "wetsuit diving", "medical examination",
        "hand holding tool", "worker gloves hands", "nurse patient care", "dentist patient",
        "physical therapy exercise", "running track race", "cycling race athlete",
        "football player match", "cricket player batting", "swimming pool lane",
        "beach volleyball player", "martial arts training", "dance performance stage",
        "human face portrait", "hairdresser salon", "spa treatment room",
        "orthopedic brace leg", "walking stick hand", "wheelchair user", "first aid training",
    ],
}

# A result is dropped if any of these appear in its title or categories. The subjects above do
# not need them, so a hit means the search drifted somewhere it should not go.
_EXCLUDE = re.compile(
    r"nude|nudity|naked|topless|erotic|porn|sex|genital|penis|vagina|breast|nipple|buttock|"
    r"lingerie|underwear|bdsm|fetish|strip|bikini.?model|glamour|autopsy|corpse|cadaver|"
    r"wound|blood|injur|surgery|amputat|autops",
    re.I)

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def api(params: dict) -> dict:
    params = {**params, "format": "json", "formatversion": "2"}
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def search(term: str, want: int) -> list[dict]:
    """Image files matching `term`, with their licence and dimensions."""
    try:
        found = api({"action": "query", "generator": "search",
                     "gsrsearch": f'filetype:bitmap "{term}"', "gsrnamespace": "6",
                     "gsrlimit": str(min(want * 3, 50)),
                     "prop": "imageinfo|categories", "cllimit": "20",
                     # Thumbnails, not originals. upload.wikimedia.org rate-limits bulk access
                     # to full-resolution files and its own 429 asks for thumbs instead - and
                     # 1024px is already far more than CLIP needs, since it resizes to 224.
                     "iiurlwidth": "1024",
                     "iiprop": "url|size|extmetadata"})
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        print(f"  {term!r}: search failed ({type(e).__name__})", file=sys.stderr)
        return []

    out = []
    for page in (found.get("query", {}) or {}).get("pages", []) or []:
        title = page.get("title", "")
        cats = " ".join(c.get("title", "") for c in page.get("categories", []) or [])
        if _EXCLUDE.search(title) or _EXCLUDE.search(cats):
            continue
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("thumburl") or info.get("url", "")
        origin = info.get("url", "")
        if Path(urllib.parse.urlparse(origin).path).suffix.lower() not in ALLOWED_EXT:
            continue
        meta = info.get("extmetadata", {}) or {}
        out.append({
            "title": title,
            "url": url,
            "origin": origin,
            "width": info.get("width", 0),
            "height": info.get("height", 0),
            "licence": (meta.get("LicenseShortName", {}) or {}).get("value", "unknown"),
            "artist": re.sub(r"<[^>]+>", "", (meta.get("Artist", {}) or {}).get("value", ""))[:120],
            "term": term,
        })
    return out


def download(url: str, retries: int = 3) -> bytes | None:
    """Fetch one file, backing off on a 429. Wikimedia throttles bulk access, and hammering it
    after being told to slow down is how an IP ends up blocked for everyone behind it."""
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            print(f"  download failed: HTTP {e.code}", file=sys.stderr)
            return None
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            print(f"  download failed: {type(e).__name__}", file=sys.stderr)
            return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("set_name", nargs="?", choices=sorted(SETS), help="which set to collect")
    ap.add_argument("--limit", type=int, default=100, help="how many images (default 100)")
    ap.add_argument("--out", type=Path, default=ROOT.parent / "images")
    ap.add_argument("--list", action="store_true", help="show the sets and their subjects")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.list or not args.set_name:
        for name, terms in SETS.items():
            print(f"{name}  ({len(terms)} subjects)")
            for t in terms:
                print(f"    {t}")
        return 0

    terms = SETS[args.set_name]
    folder = args.out / args.set_name
    folder.mkdir(parents=True, exist_ok=True)
    per_term = max(2, args.limit // len(terms) + 1)

    # Carry forward whatever a previous run collected. Rewriting labels.csv from scratch
    # orphans the files already on disk - they keep their pixels and lose their licence, which
    # for CC material is not a detail. Hashes already present are skipped, so re-running tops
    # the set up instead of duplicating it.
    csv_path = folder / "labels.csv"
    rows: list[dict] = []
    if csv_path.exists():
        with csv_path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        print(f"carrying forward {len(rows)} rows already in {csv_path.name}")
    seen_hashes: set[str] = {r.get("sha256", "") for r in rows}
    print(f"collecting up to {args.limit} images into {folder}")
    for term in terms:
        if len(rows) >= args.limit:
            break
        hits = search(term, per_term)
        taken = 0
        for hit in hits:
            if taken >= per_term or len(rows) >= args.limit:
                break
            data = download(hit["url"])
            if not data or len(data) > 15_000_000:
                continue
            digest = hashlib.sha256(data).hexdigest()
            if digest in seen_hashes:            # Commons mirrors the same file under names
                continue
            ext = Path(urllib.parse.urlparse(hit["url"]).path).suffix.lower()
            name = f"{digest[:16]}{ext}"
            (folder / name).write_bytes(data)
            seen_hashes.add(digest)
            rows.append({
                "image_id": digest[:16],
                "image_path": f"{args.set_name}/{name}",
                "label": "allow",
                "category": args.set_name,
                "subject": hit["term"],
                "source_url": hit["url"],
                "source_file": hit["origin"],
                "source_title": hit["title"],
                "source_dataset": "Wikimedia Commons",
                "license": hit["licence"],
                "attribution": hit["artist"],
                "width": hit["width"],
                "height": hit["height"],
                "sha256": digest,
                "review_status": "search_terms_benign_not_individually_reviewed",
            })
            taken += 1
            time.sleep(0.2)                       # be a good citizen of someone else's API
        print(f"  {term:28} {taken:3} kept   (total {len(rows)})")

    if not rows:
        print("nothing collected", file=sys.stderr)
        return 1

    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n{len(rows)} images, provenance in {csv_path}")
    licences: dict[str, int] = {}
    for r in rows:
        licences[r["license"]] = licences.get(r["license"], 0) + 1
    for lic, count in sorted(licences.items(), key=lambda kv: -kv[1]):
        print(f"  {count:3}  {lic}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
