"""Train a linear probe over the image encoder's embeddings, from BONC's own labelled pictures.

    python training/train_image_probe.py --encoder flask_api/models/img_v1
    python training/train_image_probe.py --encoder flask_api/models/img_v2 --out flask_api/models/img_v1/probe.npz

The zero-shot policy in `config/visual_policy.yaml` is a guess at English wordings. It works -
77% of weapons, 93% of gore at the shipped threshold - but every improvement has had to be
bought by editing prompts and re-measuring, and three times a change that read well cost recall
somewhere else. None of it learns anything from the 1,100-odd labelled pictures sitting in
`images/`.

This does. The encoder is frozen and its embeddings cached; a multinomial logistic regression
is fitted on top. That is the same shape as Layer 1.5's triage filter - a linear model over
frozen features - and it serves the same way: training needs scikit-learn, serving needs one
matrix multiply in numpy, so nothing new reaches the Flask app.

**The split is by source directory, not by picture.** `images/flag` is 300 frames from a
handful of weapon-detection videos, so neighbouring frames are near-duplicates; splitting at
random would put a frame in train and its neighbour in test and report a number that means
nothing. Grouping by `sequence_group` from labels.csv where it exists, and by file prefix
otherwise, keeps related frames on one side.

`non_graphic` is left out of training entirely: its own README says it contains weapons and
fighting, so it is neither a clean positive nor a clean negative.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "flask_api"))
sys.path.insert(0, str(ROOT / "flask_api" / "scripts"))

# .avif included: Unsplash and other stock sources serve it by default now, and leaving it out
# silently skipped 73 supplied images - the run came back with byte-identical metrics and no
# warning, which is the worst way for a dataset to shrink.
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".gif", ".tif", ".tiff"}

# directory -> class. The classes are the policy's own labels, so a probe can stand in for the
# prompts without anything downstream changing.
SETS = {
    # Weapons from two very different sources on purpose: `flag` is 300 frames from a handful
    # of CCTV-style clips (easy, near-duplicate), `weapons` is 200 varied stills including
    # lathis, talwars and sickles. The second is what generalisation actually looks like.
    "flag": "weapon",
    "weapons": "weapon",
    "graphic_injury_gore_500/images/graphic_injury_gore": "gore",
    # Confrontation, fighting and menace share a policy category (violence_or_unrest) and are
    # pooled into one class.
    #
    # `violence` and `threats` USED TO FEED THIS CLASS and no longer do. Both were audited
    # picture by picture (docs/image_sets.md): `threats` is about twenty photographs of metal
    # bands on stage plus manuscripts, vigils and a wild boar, and `violence` is mostly boxing,
    # wrestling and MMA with an FBI award handshake and a meerkat. Roughly 12% and 17% are the
    # thing they are named for. Pooling them in bought 233 rows of mislabelled training data
    # and would teach this class that a metal gig is a riot.
    #
    # `unrest_real` replaces them: 84 Indian press photographs of police-protester
    # confrontation, supplied rather than scraped, which the prompts already score at 99%.
    "unrest": "unrest",
    "unrest_real": "unrest",
    "romance": "romantic",
    "revealing": "revealing",
    # The two sides of the policy's distinction, for the first time: 135 pictures OF a child
    # against 167 where children are merely present. If `minor` is learnable at all, it is
    # learnable from this contrast and not from the unsafe class alone.
    # Kept as safe: children present but not the subject is explicitly allowed, and these
    # broaden the safe class. images/minors (child portraits) has no class and is skipped -
    # the `minor` label is carried by its prompts.
    # Both sides of the policy's distinction. `minors` is pictures OF a child, which is what
    # the rule refuses; `minors_incidental` is children merely present, which it allows, and
    # those stay in `safe` so the contrast is in the training data rather than assumed.
    "minors": "minor",
    # The labels added after the first probe round, now that they have supplied data behind
    # them. `physique` and `wedding` score 99% and 100% on prompts alone, so a class here can
    # only ever add false positives - they are mapped so --classes can TEST that rather than
    # assert it. `sexual` has 37 images of one genre, which is thin for a class.
    "gym_neg": "physique",
    "gym_positive": "safe",
    "weddings": "wedding",
    "nudity": "sexual",
    # `manhandling` has NO training data and cannot be a class. Its only sources were
    # images/violence and images/threats, and the audit found those are ~17% and ~12% the
    # thing they are named for - mostly boxing, wrestling and photographs of metal bands on
    # stage. A class fitted on them would learn that a gig is an assault. It stays with its 14
    # prompts, which catch 99% of real press photography of confrontation.
    "minors_incidental": "safe",
    "allow": "safe",
    "BONC_Positive": "safe",
    "body_parts": "safe",
    "crowds_safe": "safe",
}
# THE CLASSES, AND A CORRECTION.
#
# This list said `["safe", "weapon", "gore"]` while the shipped models/img_v1/probe.npz held
# FOUR - safe, weapon, gore, minor. A `minor` experiment trained the 4-class file at 10:06 and
# this line was edited back to three at 10:07 without re-running it, so the artifact and its
# trainer disagreed for weeks. Anyone who retrained got a materially different model and no
# warning. `--classes` and the check in main() exist so that cannot happen again quietly.
#
# The older comment here said `minor` "still fails", measured as a standalone detector: recall
# 82% at precision 62%, refusing 39 to 62 of the 167 incidental images. That measurement was
# right and the conclusion drawn from it was wrong, because it asked the wrong question. The
# probe does not decide a label on its own - `decides_reject` refuses when EITHER the prompts
# or `1 - P(safe)` crosses its line. Measured that way, the accidental fourth class is worth:
#
#     set          4-class   without `minor`
#     minors         96%          67%
#     revealing      69%          44%
#     nudity         95%          73%
#     body_parts     13% refused  10% refused
#
# A class trained on child portraits against incidental children generalises to "a human body
# fills the frame", which is why it lifts `revealing` and `nudity` by twenty points - something
# a child detector has no business doing. It earns its place; it is just not doing the job its
# name suggests.
#
# `revealing` was then added as a fifth, and it is the one that earns its place on its own
# merits: 86% recall at 81% precision out-of-fold, the strongest class here after gore. It
# exists because cleaning the explicit images out of `minors` cost 12 points of `revealing`
# recall - that recall had been coming from contaminated training data - and this is the
# honest way to get it back. Measured, 4 classes -> 5:
#
#     revealing 57% -> 97%   nudity 92% -> 100%   minors 95% -> 97%
#     weapons   83% -> 86%   gore   99% -> 100%   romance 61% -> 64%
#     cost: body_parts 13 -> 20 refused, crowds_safe 8 -> 12, incidental 43 -> 48
#
# `unrest` and `romantic` were tried alongside it (7 classes) and are NOT here: at the shipped
# threshold they bought 6 points of weapons and cost crowds_safe 10 -> 43 and body_parts
# 17 -> 39. `romantic` precision is 57%. At every matched false-positive rate the smaller probe
# won. `wedding` and `physique` are absent too - the prompts score them 100% and 99%, so a
# probe class could only add false positives.
#
# Note the softmax dilution: the unsafe reading is 1 - P(safe), so adding classes lowers
# P(safe) and raises that reading for every picture. Thresholds are NOT comparable across class
# counts, and `probe_reject_min` has to be re-swept whenever this list changes.
CLASSES = ["safe", "weapon", "gore", "minor", "revealing"]


def group_of(path: Path, groups: dict[str, str]) -> str:
    """Which cluster of near-duplicates this picture belongs to."""
    if path.name in groups:
        return groups[path.name]
    stem = path.stem
    # "pistol_pistol_0441" -> "pistol_pistol"; "knife_ABsframe00214" -> "knife_ABsframe"
    cut = stem.rstrip("0123456789")
    return cut or stem


def load_groups(images: Path) -> dict[str, str]:
    """Clustering columns from any labels.csv, kept only where they actually cluster.

    `sequence_group` in the weapons CSV is unique per row ("pistol_0441"), so using it as-is
    gives one group per picture and the grouped split degenerates into a random one - which is
    how near-duplicate video frames end up on both sides and the score comes back flattering.
    A column earns its place only if it maps several files to the same value.
    """
    out: dict[str, str] = {}
    for csv_path in images.rglob("labels.csv"):
        try:
            with csv_path.open(encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
        except Exception:
            continue
        for column in ("sequence_group", "subject"):
            pairs = [(Path(r.get("image_path", "")).name, r.get(column) or "") for r in rows]
            pairs = [(n, g) for n, g in pairs if n and g]
            if not pairs:
                continue
            if len(set(g for _, g in pairs)) >= 0.9 * len(pairs):
                continue                      # as good as unique: not a grouping
            out.update(dict(pairs))
            break
    return out


def embed_all(encoder: Path, images: Path, device: str):
    """(X, y, groups, paths) - embeddings and labels for every picture in SETS."""
    import numpy as np
    from PIL import Image

    from app.clip import VisualCheck, VisualConfig
    from eval_clip_images import image_files

    # Absolute: VisualCheck resolves a relative model_dir against the app root, which would
    # turn "flask_api/models/img_v1" into "flask_api/flask_api/models/img_v1".
    check = VisualCheck(VisualConfig(enabled=True, model_dir=str(encoder.resolve()), device=device),
                        root=ROOT / "flask_api")
    if not check.ready:
        raise SystemExit(f"encoder not loadable: {check.status.get('reason')}")
    print(f"encoder {encoder.name} on {check.status['provider']}")

    seq = load_groups(images)
    X, y, g, paths = [], [], [], []
    for rel, label in SETS.items():
        if label not in CLASSES:
            continue
        folder = images / rel
        if not folder.is_dir():
            print(f"  {rel}: missing, skipped")
            continue
        # Recursive, by bytes, de-duplicated - the same walk eval_clip_images.py uses, imported
        # rather than re-implemented because the two drifting apart is how this file came to
        # train on a different set of pictures than the one being measured.
        #
        # iterdir() + a suffix filter stood here and did two things quietly. It MISSED every
        # foldered set: images/weddings holds its 205 pictures in `wedding image/dedwe/` and
        # friends, so this read 0 and the run crashed on an empty class. And it de-duplicated
        # nothing, so the 73 files in minors/kid_neg that are byte-identical to pictures
        # already in minors/ were trained on twice - a tenth of that class voting double.
        files = image_files(folder)
        for p in files:
            try:
                with Image.open(p) as im:
                    im.load()
                    emb = check._session.run([check._image_output],
                                             {"pixel_values": check.preprocess(im)})[0][0]
            except Exception:
                continue
            X.append(emb)
            y.append(CLASSES.index(label))
            g.append(f"{rel}:{group_of(p, seq)}")
            paths.append(p)
        print(f"  {rel:46} {len(files):4} -> {label}")
    X = np.asarray(X, dtype=np.float32)
    X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-12)
    return X, np.asarray(y), np.asarray(g), paths


def main() -> int:
    global CLASSES                      # --classes rebinds it; see the block after parse_args
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--encoder", type=Path, default=ROOT / "flask_api" / "models" / "img_v1")
    ap.add_argument("--images", type=Path, default=ROOT / "images")
    ap.add_argument("--out", type=Path, default=None, help="default: <encoder>/probe.npz")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--C", type=float, default=1.0, help="inverse regularisation strength")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--classes", default=",".join(CLASSES),
                    help="comma-separated, must include `safe`. Default is what ships; try "
                         "adding unrest,romantic,revealing and compare before believing it.")
    ap.add_argument("--class-weight", choices=["balanced", "none"], default="balanced",
                    help="balanced upweights small classes, which with seven of them makes the "
                         "smallest ones aggressive enough to refuse ordinary content")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Rebind before embed_all, which filters SETS against CLASSES. Done here rather than
    # threading a parameter through nine call sites, and done LOUDLY: the artifact and this
    # list silently disagreeing for weeks is the reason the flag exists.
    chosen = [c.strip() for c in args.classes.split(",") if c.strip()]
    if "safe" not in chosen:
        print("--classes must include `safe`: the unsafe reading is 1 - P(safe)", file=sys.stderr)
        return 2
    unknown = [c for c in chosen if c not in set(SETS.values())]
    if unknown:
        print(f"no training data mapped to {unknown} in SETS - nothing would be learned",
              file=sys.stderr)
        return 2
    if chosen != CLASSES:
        print(f"classes overridden: {CLASSES} -> {chosen}")
    CLASSES = chosen
    print(f"training a {len(CLASSES)}-class probe: {CLASSES}")

    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import GroupKFold

    X, y, groups, paths = embed_all(args.encoder, args.images, args.device)
    print(f"\n{len(X)} embeddings, {X.shape[1]}-d, "
          f"{len(set(groups))} groups, classes {dict(zip(*np.unique(y, return_counts=True)))}")

    # A class with no rows makes LogisticRegression learn k-1 classes while everything
    # downstream indexes k, which surfaces as an opaque broadcast error 200 lines later.
    present = set(np.unique(y).tolist())
    empty = [c for i, c in enumerate(CLASSES) if i not in present]
    if empty:
        print(f"\nno training images found for {empty} - every set mapped to them in SETS is "
              f"missing or unreadable. Drop them from --classes, or fix the folders.",
              file=sys.stderr)
        return 2

    # Grouped cross-validation: the honest estimate, because neighbouring video frames would
    # otherwise appear on both sides and flatter the model.
    folds = min(args.folds, len(set(groups)))
    cv = GroupKFold(n_splits=folds)
    oof = np.zeros((len(X), len(CLASSES)), dtype=np.float32)
    for tr, te in cv.split(X, y, groups):
        clf = LogisticRegression(C=args.C, max_iter=3000,
                                 class_weight=None if args.class_weight == "none" else "balanced")
        clf.fit(X[tr], y[tr])
        # Scatter by clf.classes_ rather than assuming predict_proba returns one column per
        # CLASS. A grouped split can put every row of a small class in one fold - `sexual` is
        # 37 pictures and they share a subject group - and then that fold trains on k-1
        # classes and returns k-1 columns. Assigning that straight into oof[te] raised an
        # opaque broadcast error that said nothing about which class was missing.
        oof[np.ix_(te, clf.classes_)] = clf.predict_proba(X[te])

    pred = oof.argmax(1)
    print(f"\ngrouped {folds}-fold cross-validation (out-of-fold):")
    print(f"{'class':8} {'n':>5} {'recall':>8} {'precision':>10}")
    for i, name in enumerate(CLASSES):
        mask = y == i
        rec = (pred[mask] == i).mean() if mask.any() else float("nan")
        sel = pred == i
        prec = (y[sel] == i).mean() if sel.any() else float("nan")
        print(f"{name:8} {int(mask.sum()):5} {rec:8.1%} {prec:10.1%}")
    print(f"{'overall':8} {len(y):5} {(pred == y).mean():8.1%}")

    # The number that compares with the zero-shot table: unsafe = 1 - P(safe).
    unsafe = 1.0 - oof[:, CLASSES.index("safe")]
    print(f"\nthreshold sweep (unsafe = 1 - P(safe), out-of-fold):")
    print(f"{'thr':>6} {'weapons':>9} {'gore':>9} {'safe refused':>14}")
    for thr in (0.5, 0.7, 0.8, 0.9, 0.95, 0.97, 0.99):
        w = ((unsafe >= thr) & (y == CLASSES.index("weapon"))).sum()
        gr = ((unsafe >= thr) & (y == CLASSES.index("gore"))).sum()
        fp = ((unsafe >= thr) & (y == CLASSES.index("safe"))).sum()
        nw = (y == CLASSES.index("weapon")).sum()
        ng = (y == CLASSES.index("gore")).sum()
        ns = (y == CLASSES.index("safe")).sum()
        print(f"{thr:6.2f} {w:4}/{nw:<4}{w/nw:5.0%} {gr:4}/{ng:<4}{gr/ng:5.0%} "
              f"{fp:5}/{ns:<5}{fp/ns:6.1%}")

    # The number that actually decides deployment: prompts and probe together, with the probe's
    # OUT-OF-FOLD score. Using the fitted model here would be measuring memorisation - it has
    # seen every one of these pictures - and on the weapons set that reads 97% against a true
    # 73%, because 300 frames from a few videos are easy to memorise and hard to generalise.
    print("\ncombined decision (prompt softmax OR out-of-fold probe), as served:")
    from PIL import Image
    from app.clip import VisualCheck, VisualConfig

    chk = VisualCheck(VisualConfig(enabled=True, model_dir=str(args.encoder.resolve()),
                                   device=args.device), root=ROOT / "flask_api")
    prompt_unsafe = np.zeros(len(paths), dtype=np.float32)
    for i, path in enumerate(paths):
        with Image.open(path) as im:
            prompt_unsafe[i] = chk.classify(im).unsafe_score

    print(f"{'prompt':>7} {'probe':>7} {'weapons':>11} {'gore':>11} {'safe refused':>14}")
    for pt in (0.90,):
        for qt in (0.50, 0.60, 0.70, 0.80, 0.90):
            hit = (prompt_unsafe >= pt) | (unsafe >= qt)
            row = []
            for cls in ("weapon", "gore", "safe"):
                m = y == CLASSES.index(cls)
                row.append((int((hit & m).sum()), int(m.sum())))
            (w, nw), (g, ng), (f, nf) = row
            # Which safe set the false positives come from. BONC_Positive is the only one drawn
            # from real member uploads, so a refusal there costs far more than one on an
            # adversarial object set, and the aggregate hides that.
            safe_mask = y == CLASSES.index("safe")
            where = {}
            for i in np.where(hit & safe_mask)[0]:
                src = groups[i].split(":", 1)[0]
                where[src] = where.get(src, 0) + 1
            detail = " ".join(f"{k}={v}" for k, v in sorted(where.items())) or "-"
            print(f"{pt:7.2f} {qt:7.2f} {w:4}/{nw:<4}{w/nw:4.0%} {g:4}/{ng:<4}{g/ng:4.0%} "
                  f"{f:5}/{nf:<5}{f/nf:5.1%}  {detail}")

    # Final model on everything, for serving.
    clf = LogisticRegression(C=args.C, max_iter=3000,
                                 class_weight=None if args.class_weight == "none" else "balanced")
    clf.fit(X, y)
    out = args.out or (args.encoder / "probe.npz")
    np.savez(out, W=clf.coef_.astype(np.float32), b=clf.intercept_.astype(np.float32),
             classes=np.array(CLASSES), dim=np.array(X.shape[1], dtype=np.int32),
             encoder=np.array(args.encoder.name))
    meta = {"classes": CLASSES, "dim": int(X.shape[1]), "n_train": int(len(X)),
            "n_groups": int(len(set(groups))), "C": args.C,
            "cv_accuracy": float((pred == y).mean())}
    print(f"\nwrote {out} ({out.stat().st_size / 1024:.0f} KB)")
    print(json.dumps(meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
