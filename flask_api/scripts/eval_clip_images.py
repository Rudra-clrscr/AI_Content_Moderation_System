"""Measure the visual check against the sample images, and pick its threshold.

    python scripts/eval_clip_images.py --images ../images

The policy in `config/visual_policy.yaml` is written in English, not learned from BONC's
pictures, so nothing about it is self-evident: a wording that sounds right can miss half a
class, and a thin `safe` list can make ordinary product photos score as weapons. This prints
what actually happens on the sample sets, sweeps `visual.reject_min`, and names the pictures it
gets most wrong so they can be looked at.

The sets it knows about, from `images/`:

    flag/                                   300 weapons (pistol, knife)        -> expect unsafe
    allow/                                  200 safe objects (phone, purse...) -> expect safe
    graphic_injury_gore_500/.../graphic_injury_gore  300 gore                  -> expect unsafe
    graphic_injury_gore_500/.../non_graphic         200 "not gore"             -> see below
    BONC_Positive/                           63 real BONC listing pictures     -> expect safe

`non_graphic` is reported but is NOT a false-positive set: its own README says it deliberately
contains weapons, smoking and non-graphic fighting, so "not gore" is not "allowed on BONC".
Scoring it as unsafe is often correct. The false-positive number that matters is BONC_Positive,
which is the only set drawn from what members actually upload.

`--dump N` writes the N worst-scoring images of each set to a folder so they can be eyeballed;
a threshold is not worth trusting until someone has looked at what sits either side of it.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.clip import VisualCheck, VisualConfig  # noqa: E402

# .avif included: Unsplash and other stock sources serve it by default now, and leaving it out
# silently skipped 73 supplied images - the run came back with byte-identical metrics and no
# warning, which is the worst way for a dataset to shrink.
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".gif", ".tif", ".tiff"}


def find_sets(images: Path) -> list[tuple[str, Path, bool]]:
    """(name, directory, expected_unsafe). Missing directories are skipped, not invented."""
    gore = images / "graphic_injury_gore_500" / "images"
    candidates = [
        ("flag (weapons)", images / "flag", True),
        ("allow (safe objects)", images / "allow", False),
        ("gore", gore / "graphic_injury_gore", True),
        ("non_graphic", gore / "non_graphic", None),      # None: not a clean expectation
        ("BONC_Positive", images / "BONC_Positive", False),
        # Human bodies, skin and anatomy: hands, athletes, physiotherapy, swimming, medical
        # diagrams. Collected with scripts/collect_commons_images.py. These exist to measure
        # the `sexual` label, which has no positive samples behind it - so what is measured is
        # the side that would actually hurt BONC, a clinic or a sportswear seller being refused.
        ("body_parts", images / "body_parts", False),
        # The positive side of the `sexual` label. Not in the repo and not collectable by
        # scraping - see docs/measuring_the_sexual_label.md for the two routes that work.
        # Drop a licensed set in here and it is measured like any other; absent, it is skipped.
        ("sexual_positive", images / "sexual_positive", True),
        # Confrontation, and the ordinary crowds that look like it. The second set is the one
        # that decides whether this label is affordable: a trade fair, a wedding procession and
        # a temple festival are all dense crowds, several with banners and raised hands.
        ("unrest", images / "unrest", True),
        ("crowds_safe", images / "crowds_safe", False),
        # Collected for the labels added after the first round. Every one of these should be
        # refused on a trade platform; crowds_safe and body_parts are what they must not catch.
        ("weapons (varied)", images / "weapons", True),
        ("violence", images / "violence", True),
        ("threats", images / "threats", True),
        ("romance", images / "romance", True),
        ("revealing", images / "revealing", True),
        ("minors", images / "minors", True),
        # Children present but NOT the subject: graduations, school events with parents, family
        # firm team photos. The `minor` rule allows these on purpose, so this is the set that
        # prices it - and it was the one set the probe experiments used but this script did not,
        # which left the headline cost of that label unmeasurable from here.
        ("minors_incidental", images / "minors_incidental", False),
        # Artistic nude photography, supplied rather than collected. This is the positive set
        # the `sexual` label never had - its recall was unmeasured until these arrived.
        ("nudity", images / "nudity", True),
        # Indian press photography of police/protester confrontation, supplied rather than
        # collected. It scores a median 0.995 against the Commons `violence` set's 0.568 - the
        # model handles the real distribution far better than my collected sets suggested, and
        # this is the one to trust when the two disagree.
        ("unrest_real", images / "unrest_real", True),
        # Gym pictures, supplied, split by the member's INTENT rather than by what is in frame.
        # gym_positive is the gym as a business - adverts, franchise promos, equipment product
        # shots, facility interiors. gym_neg is the same gym with the body as the subject -
        # mirror selfies, flexing, a bodybuilder posing. Several gym_neg images are fully
        # clothed and several gym_positive ones show a muscular model in a vest, so skin does
        # not separate them and neither does the room. This is the same framing distinction
        # `minor` draws, and the hardest pair of sets in here.
        ("gym_neg (physique display)", images / "gym_neg", True),
        ("gym_positive (gym business)", images / "gym_positive", False),
    ]
    return [(n, d, e) for n, d, e in candidates if d.is_dir()]


def score_dir(check: VisualCheck, directory: Path,
              limit: int = 0) -> list[tuple[Path, float, str, dict, bool]]:
    from PIL import Image

    out = []
    paths = sorted(p for p in directory.iterdir() if p.suffix.lower() in EXTS)
    for path in (paths[:limit] if limit else paths):
        try:
            with Image.open(path) as im:
                result = check.classify(im)
        except Exception as e:                      # a corrupt sample must not stop the sweep
            print(f"  skipped {path.name}: {type(e).__name__}", file=sys.stderr)
            continue
        if not result.checked:
            raise SystemExit("the visual check is not loaded - is visual.enabled on, and have "
                             "fetch_clip.py and build_clip_prompts.py been run?")
        # The shipped verdict travels with the score. The sweep below moves one global bar,
        # which is not how the service decides any more - each label has its own, and
        # `decides_reject` is the only thing that knows all of them.
        out.append((path, result.unsafe_score, result.label, result.scores,
                    check.decides_reject(result)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", type=Path, default=ROOT.parent / "images")
    ap.add_argument("--model-dir", type=Path, default=ROOT / "models" / "img_v1")
    ap.add_argument("--device", default="auto", help="auto | cuda | directml | cpu")
    ap.add_argument("--limit", type=int, default=0, help="at most this many images per set")
    ap.add_argument("--dump", type=int, default=0, help="write this many worst cases per set")
    ap.add_argument("--dump-dir", type=Path, default=None)
    args = ap.parse_args()

    if not args.images.is_dir():
        print(f"no images directory at {args.images}", file=sys.stderr)
        return 2
    sets = find_sets(args.images)
    if not sets:
        print(f"no known image sets under {args.images}", file=sys.stderr)
        return 2

    check = VisualCheck(VisualConfig(enabled=True, model_dir=str(args.model_dir),
                                     device=args.device), root=ROOT)
    if not check.ready:
        print(f"visual check unavailable: {check.status.get('reason')}", file=sys.stderr)
        return 2
    print(f"running on {check.status['provider']}")

    results: dict[str, tuple[list, bool | None]] = {}
    t0 = time.time()
    total = 0
    for name, directory, expected in sets:
        scored = score_dir(check, directory, args.limit)
        results[name] = (scored, expected)
        total += len(scored)
    elapsed = time.time() - t0
    device = check.status["provider"].replace("ExecutionProvider", "")
    print(f"\n{total} images in {elapsed:.1f}s "
          f"({total / max(elapsed, 1e-9):.1f}/s on {device})\n")

    # ---- what each set looks like ----
    print(f"{'set':24} {'n':>5}  {'expect':>7}  {'mean':>6} {'p50':>6} {'p90':>6}  top label")
    for name, (scored, expected) in results.items():
        if not scored:
            continue
        vals = sorted(s for _, s, _, _, _ in scored)
        n = len(vals)
        counts: dict[str, int] = {}
        for _, _, label, _, _ in scored:
            counts[label] = counts.get(label, 0) + 1
        top = ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:3])
        want = {True: "unsafe", False: "safe", None: "mixed"}[expected]
        print(f"{name:24} {n:5}  {want:>7}  {sum(vals)/n:6.3f} {vals[n//2]:6.3f} "
              f"{vals[min(n-1, int(n*0.9))]:6.3f}  {top}")

    # ---- the sweep ----
    # Recall over the sets that should be caught, against false positives on the one set drawn
    # from real BONC uploads. non_graphic is excluded from both: it is neither.
    catch = [n for n, (_, e) in results.items() if e is True]
    clean = [n for n, (_, e) in results.items() if e is False]
    print(f"\nthreshold sweep   caught: {', '.join(catch)}   false positives: {', '.join(clean)}")
    print(f"{'reject_min':>10} " + "".join(f"{n.split()[0]:>16}" for n in catch + clean))
    best = None
    # Sigmoid bundles (SigLIP) live far lower than softmax ones: a label is scored on its own
    # account, so an unambiguous match sits around 0.3-0.6 rather than winning a 0.99 softmax.
    # The sweep has to cover both or it measures nothing for half the models.
    sigmoid = check.status.get("scoring") == "sigmoid"
    steps = ((0.01, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.70) if sigmoid
             else (0.50, 0.70, 0.80, 0.90, 0.95, 0.97, 0.99, 0.995, 0.999))
    for thr in steps:
        row = []
        rates = {}
        for name in catch + clean:
            scored, _ = results[name]
            if not scored:
                row.append(f"{'-':>16}")
                continue
            hits = sum(1 for _, s, _, _, _ in scored if s >= thr)
            rates[name] = hits / len(scored)
            row.append(f"{hits:>6}/{len(scored):<4} {rates[name]:>4.0%}")
        fp = max((rates.get(n, 0.0) for n in clean), default=0.0)
        rec = min((rates.get(n, 0.0) for n in catch), default=0.0)
        print(f"{thr:>10.3f} " + "".join(row) + f"   recall {rec:.0%}  worst FP {fp:.1%}")
        if best is None or (fp <= 0.01 and rec > best[1]):
            if fp <= 0.01:
                best = (thr, rec, fp)
    if best:
        print(f"\nhighest recall with at most 1% false positives on real uploads: "
              f"reject_min = {best[0]:.3f}  (recall {best[1]:.0%}, FP {best[2]:.1%})")

    # ---- what the service would actually do ----
    # The sweep above moves one bar across every label. Production stopped doing that when the
    # per-label `reject_min` landed, so a row of it describes a configuration nothing runs.
    # This is the shipped rule: each label at its own bar, the combined unsafe score underneath
    # it, and the probe alongside. It is where the figures in config/visual_policy.yaml and
    # contracts/moderation_result.md come from.
    print("\nSHIPPED DECISION (per-label bars + probe)")
    print(f"{'set':24} {'n':>5}  {'expect':>7}  {'refused':>12}")
    for name, (scored, expected) in results.items():
        if not scored:
            continue
        rej = sum(1 for *_, r in scored if r)
        want = {True: "unsafe", False: "safe", None: "mixed"}[expected]
        flag = "" if expected is None else ("  <- recall" if expected else "  <- refusals")
        print(f"{name:24} {len(scored):5}  {want:>7}  {rej:5}/{len(scored):<4} "
              f"{rej / len(scored):>4.0%}{flag}")

    # ---- the ones to look at ----
    if args.dump:
        dump_dir = args.dump_dir or (ROOT / "scripts" / "clip_eval_dump")
        shutil.rmtree(dump_dir, ignore_errors=True)
        for name, (scored, expected) in results.items():
            if expected is None or not scored:
                continue
            # Worst = most wrong: lowest score for sets that should be caught, highest for the
            # sets that should pass.
            worst = sorted(scored, key=lambda r: r[1], reverse=(expected is False))[: args.dump]
            folder = dump_dir / name.split()[0]
            folder.mkdir(parents=True, exist_ok=True)
            for path, score, label, _, _ in worst:
                shutil.copy(path, folder / f"{score:.3f}_{label}_{path.name}")
        print(f"\nworst cases written to {dump_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
