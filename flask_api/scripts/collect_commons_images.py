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
    # Weapons from sources other than CCTV video. images/flag is 300 frames from a handful of
    # clips, which is why the probe memorises it (95% on a random split) and generalises poorly
    # (73% when the split is grouped by clip). What is missing is variety: different lighting,
    # framing, backgrounds and weapon types, including the catalogue-style shots a member would
    # actually upload if they were listing one.
    "weapons": [
        "handgun pistol", "revolver firearm", "semi automatic pistol", "assault rifle",
        "hunting rifle", "shotgun firearm", "combat knife", "hunting knife blade",
        "machete blade", "dagger weapon", "person aiming handgun", "person holding rifle",
        "gun shop display", "firearms for sale counter", "pistol on table",
        "rifle leaning against wall", "knife collection display", "switchblade knife",
        "soldier carrying rifle", "police officer with firearm",
        # Not only firearms. A lathi, a talwar and an iron rod are what a weapon listing or a
        # violent photograph actually looks like here, and none of them resemble a pistol.
        "lathi stick police", "bamboo stick weapon", "talwar sword india",
        "kirpan sword", "sickle farm tool", "iron rod weapon", "axe weapon",
        "club blunt weapon", "spear weapon", "crossbow weapon",
    ],

    # Fighting and assault, as distinct from the crowd confrontation `unrest` describes.
    "violence": [
        "two men fighting street", "bar fight brawl", "street brawl crowd",
        "person punching another", "assault attack victim", "wrestling fight real",
        "kicking a person on ground", "beating with stick", "mob attacking person",
        "scuffle between protesters", "hooligans fighting", "boxing knockout punch",
    ],
    # Menace: the weapon or gesture aimed AT someone, rather than merely present.
    "threats": [
        "man pointing gun at camera", "threatening with knife", "raised fist threatening",
        "menacing gesture man", "pointing finger angrily", "intimidating posture man",
        "robber pointing weapon", "threatening gesture crowd", "angry man shouting face",
        "person brandishing weapon",
    ],
    # Couples. The counterweights are in visual_policy.yaml - handshakes, team photos and
    # colleagues greeting, which are two people close together without being a couple.
    "romance": [
        "couple kissing", "romantic couple embrace", "couple holding hands date",
        "engagement proposal couple", "couple on beach sunset", "wedding couple kiss",
        "lovers hugging", "valentine couple", "couple dancing romantic",
        "honeymoon couple",
    ],
    # Fashion and glamour photography: clothed, but posed for display. The term list is
    # deliberately fashion-oriented and _EXCLUDE_SEXUAL still applies, so this collects
    # catalogue and runway material and not explicit content.
    "revealing": [
        "fashion model runway dress", "swimwear fashion shoot", "bikini beach fashion",
        "glamour photography model", "lingerie fashion catalogue", "model posing studio",
        "evening gown model", "fashion week model", "crop top fashion", "beachwear model",
    ],
    # Children. Collected for ONE purpose: to check that the `minor` label actually refuses
    # them, because an unvalidated child-image filter on a live platform is worse than a known
    # broken one. Ordinary freely-licensed photographs, gitignored like every other set here
    # and not redistributed.
    # The CHILD AS SUBJECT, which is what the policy refuses. The first pull used broad terms
    # ("children playing park", "family with children") and collected mostly the incidental
    # case the policy ALLOWS - so the probe's class was noise against a safe set full of the
    # same thing. Those images now live in minors_incidental and count as safe, giving the
    # probe both sides of the distinction for the first time.
    "minors": [
        "child portrait photograph", "baby portrait studio", "toddler portrait",
        "child face close up", "young girl portrait", "young boy portrait",
        "infant portrait photograph", "child headshot", "smiling child portrait",
        "portrait of a schoolboy", "portrait of a schoolgirl", "child looking at camera",
    ],

    # Crowds with anger in them. The motive is not "no politics" - a tariff discussion is
    # ordinary B2B talk - it is that violence and anger should not be promoted on a networking
    # platform. So what is wanted here is confrontation, not every gathering.
    "unrest": [
        "street protest demonstration", "political rally crowd", "riot police clash",
        "angry crowd shouting", "protesters holding placards", "burning effigy protest",
        "labour strike picket line", "tear gas demonstration", "barricade confrontation",
        "mob violence street", "farmers protest march", "anti government demonstration",
        "police baton charge", "burning tyres protest", "crowd throwing stones",
        "student protest university", "trade union demonstration", "sit in protest road",
        "candlelight vigil protest", "placards rally street", "water cannon protesters",
        "riot aftermath burnt", "crowd confrontation police line", "general strike march",
        "demonstration banner city", "protest camp occupation",
    ],
    # Crowds that are NOT angry, and which a careless prompt would refuse: this is the
    # counterweight set, and it matters more than the positives. A trade fair, a queue and a
    # religious procession are all dense crowds, some with banners and raised hands.
    "crowds_safe": [
        "business conference audience", "trade fair exhibition hall", "product launch event",
        "crowded indian market street", "shopping mall crowd", "queue outside shop",
        "team meeting office", "award ceremony stage", "sports stadium crowd cheering",
        "wedding procession india", "religious procession festival", "temple festival crowd",
        "graduation ceremony", "music concert audience", "marathon runners crowd",
        "street food market crowd", "railway station platform crowd", "parade marching band",
        "conference delegates hall", "factory workers assembly line", "office open plan staff",
        "warehouse workers loading", "farmers market stalls", "bus queue commuters",
        "school assembly students", "charity run participants", "cultural dance festival",
        "trade show booth visitors", "job fair candidates", "village fair crowd",
    ],
}

# A result is dropped if any of these appear in its title or categories. The sexual exclusions
# apply everywhere - no set here needs explicit material, so a hit means the search drifted.
_EXCLUDE_SEXUAL = (
    r"nude|nudity|naked|topless|erotic|porn|sex|genital|penis|vagina|breast|nipple|buttock|"
    r"lingerie|underwear|bdsm|fetish|bikini.?model|glamour")
# Graphic injury is excluded only where it would be off-topic. For `unrest` it must NOT be:
# protest and riot photographs are routinely categorised with "violence", "injury" or "blood",
# and filtering on those threw away four fifths of that set on the first run.
_EXCLUDE_GRAPHIC = r"autopsy|corpse|cadaver|wound|blood|injur|surgery|amputat"
_EXCLUDE_BY_SET = {
    # Weapons: drop the diagram/heraldry noise Commons is full of ("coat of arms with sword"),
    # and anything graphic, which belongs to the gore set and would muddy both.
    "weapons": re.compile(_EXCLUDE_SEXUAL + r"|coat of arms|heraldr|diagram|patent|"
                          r"map|stamp|coin|coat-of-arms|coats of arms|" + _EXCLUDE_GRAPHIC, re.I),
    "unrest": re.compile(_EXCLUDE_SEXUAL, re.I),
    # Violence and threats are documentary photographs, routinely categorised with "injury"
    # or "blood" - filtering on those would gut these sets the way it gutted `unrest`.
    "violence": re.compile(_EXCLUDE_SEXUAL, re.I),
    "threats": re.compile(_EXCLUDE_SEXUAL, re.I),
    # Fashion, not explicit material. The sexual exclusions stay on and do the work.
    "revealing": re.compile(_EXCLUDE_SEXUAL, re.I),
    "romance": re.compile(_EXCLUDE_SEXUAL, re.I),
    # Children: the sexual exclusions are non-negotiable here, plus anything medical or
    # graphic, which has no place in a set whose only job is to test a blocklist.
    "minors": re.compile(_EXCLUDE_SEXUAL + "|" + _EXCLUDE_GRAPHIC + r"|malnutrition|refugee camp|"
                         r"war|victim|abuse|orphan", re.I),
    "crowds_safe": re.compile(_EXCLUDE_SEXUAL + r"|riot|protest|clash|violen", re.I),
}
_EXCLUDE = re.compile(_EXCLUDE_SEXUAL + "|" + _EXCLUDE_GRAPHIC, re.I)

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def api(params: dict) -> dict:
    params = {**params, "format": "json", "formatversion": "2"}
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def search(term: str, want: int, exclude=None, phrase: bool = True) -> list[dict]:
    """Image files matching `term`, with their licence and dimensions."""
    try:
        found = api({"action": "query", "generator": "search",
                     "gsrsearch": (f'filetype:bitmap "{term}"' if phrase
                                   else f"filetype:bitmap {term}"), "gsrnamespace": "6",
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
        bad = exclude or _EXCLUDE
        if bad.search(title) or bad.search(cats):
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
        # An exact-phrase search is precise but thin; where a set comes back short the
        # unquoted form finds far more and the per-set exclusions keep it honest.
        bad = _EXCLUDE_BY_SET.get(args.set_name)
        hits = search(term, per_term, bad) or search(term, per_term, bad, phrase=False)
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
