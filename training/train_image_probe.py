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

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

# directory -> class. The classes are the policy's own labels, so a probe can stand in for the
# prompts without anything downstream changing.
SETS = {
    "flag": "weapon",
    "graphic_injury_gore_500/images/graphic_injury_gore": "gore",
    "allow": "safe",
    "BONC_Positive": "safe",
    "body_parts": "safe",
}
CLASSES = ["safe", "weapon", "gore"]


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
        folder = images / rel
        if not folder.is_dir():
            print(f"  {rel}: missing, skipped")
            continue
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() in EXTS)
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
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--encoder", type=Path, default=ROOT / "flask_api" / "models" / "img_v1")
    ap.add_argument("--images", type=Path, default=ROOT / "images")
    ap.add_argument("--out", type=Path, default=None, help="default: <encoder>/probe.npz")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--C", type=float, default=1.0, help="inverse regularisation strength")
    ap.add_argument("--folds", type=int, default=5)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import GroupKFold

    X, y, groups, paths = embed_all(args.encoder, args.images, args.device)
    print(f"\n{len(X)} embeddings, {X.shape[1]}-d, "
          f"{len(set(groups))} groups, classes {dict(zip(*np.unique(y, return_counts=True)))}")

    # Grouped cross-validation: the honest estimate, because neighbouring video frames would
    # otherwise appear on both sides and flatter the model.
    folds = min(args.folds, len(set(groups)))
    cv = GroupKFold(n_splits=folds)
    oof = np.zeros((len(X), len(CLASSES)), dtype=np.float32)
    for tr, te in cv.split(X, y, groups):
        clf = LogisticRegression(C=args.C, max_iter=3000, class_weight="balanced")
        clf.fit(X[tr], y[tr])
        oof[te] = clf.predict_proba(X[te])

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
    clf = LogisticRegression(C=args.C, max_iter=3000, class_weight="balanced")
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
