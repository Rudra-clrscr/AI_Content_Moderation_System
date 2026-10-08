"""Measure the `sexual` label against a reference classifier, on real pictures.

    python scripts/fetch_nsfw_reference.py        # once
    python scripts/eval_sexual_label.py --images ../images

The `sexual` label in `config/visual_policy.yaml` has measured false positives and **unmeasured
recall**: `images/body_parts` shows it does not refuse athletes or physiotherapy, but nothing
shows it catches what it is named for. Closing that by collecting pornography is not an option
(see `docs/measuring_the_sexual_label.md`).

This is the other route. A purpose-trained NSFW classifier
(`onnx-community/nsfw_image_detection-ONNX`, the ONNX export of Falconsai's ViT, Apache 2.0)
scores the same pictures, and the two are compared. Where the reference says NSFW and the
`sexual` label does not, that is a **miss** — and a miss on pictures BONC actually receives,
which is worth more than recall on a corpus of professional pornography no B2B marketplace
sees. Where the `sexual` label fires and the reference does not, that is a false positive.

Neither is ground truth. The reference is a second opinion with its own error rate, so what
this produces is an agreement rate and a list of disagreements to look at — not a score to
quote. Read the disagreements; that is the point of the script.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.clip import VisualCheck, VisualConfig  # noqa: E402

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
# The reference ViT's own preprocessing, from its preprocessor_config.json. Not CLIP's.
REF_SIZE = 224
REF_MEAN = REF_STD = 0.5


class Reference:
    """The NSFW classifier, as a second opinion. Returns P(nsfw)."""

    def __init__(self, model_dir: Path, device: str = "auto"):
        import numpy as np
        import onnxruntime as ort

        from app.clip import _preload_gpu_dlls, _providers

        self.np = np
        model = model_dir / "model.onnx"
        if not model.exists():
            raise SystemExit(f"missing {model} - run scripts/fetch_nsfw_reference.py first")
        _preload_gpu_dlls(ort, device)
        self.session = ort.InferenceSession(
            str(model), providers=_providers(device, ort.get_available_providers()))
        self.input = self.session.get_inputs()[0].name
        self.provider = self.session.get_providers()[0]

    def score(self, image) -> float:
        from PIL import Image

        np = self.np
        img = image.convert("RGB").resize((REF_SIZE, REF_SIZE), Image.BILINEAR)
        arr = (np.asarray(img, dtype=np.float32) / 255.0 - REF_MEAN) / REF_STD
        logits = self.session.run(None, {self.input: arr.transpose(2, 0, 1)[None]})[0][0]
        e = np.exp(logits - logits.max())
        return float((e / e.sum())[1])          # id2label: {0: normal, 1: nsfw}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", type=Path, default=ROOT.parent / "images")
    ap.add_argument("--sets", nargs="*", default=None,
                    help="subdirectories to score (default: every one that has images)")
    ap.add_argument("--model-dir", type=Path, default=ROOT / "models" / "img_v1")
    ap.add_argument("--ref-dir", type=Path, default=ROOT / "models" / "nsfw_ref")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--ref-threshold", type=float, default=0.5,
                    help="P(nsfw) at or above this is the reference calling it NSFW")
    ap.add_argument("--dump", type=int, default=0, help="copy this many disagreements out")
    ap.add_argument("--dump-dir", type=Path, default=None)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from PIL import Image

    check = VisualCheck(VisualConfig(enabled=True, model_dir=str(args.model_dir),
                                     device=args.device), root=ROOT)
    if not check.ready:
        print(f"visual check unavailable: {check.status.get('reason')}", file=sys.stderr)
        return 2
    ref = Reference(args.ref_dir, args.device)
    print(f"policy on {check.status['provider']}, reference on {ref.provider}")
    print(f"reference calls NSFW at P >= {args.ref_threshold}, "
          f"policy at sexual >= {check.config.reject_min}\n")

    folders = ([args.images / s for s in args.sets] if args.sets
               else sorted(p for p in args.images.iterdir() if p.is_dir()))
    rows = []
    for folder in folders:
        paths = sorted(p for p in folder.rglob("*") if p.suffix.lower() in EXTS)
        if not paths:
            continue
        for path in paths:
            try:
                with Image.open(path) as im:
                    im.load()
                    policy = check.classify(im)
                    p_nsfw = ref.score(im)
            except Exception:
                continue
            rows.append({"set": folder.name, "path": path,
                         "sexual": policy.scores.get("sexual", 0.0),
                         "label": policy.label, "unsafe": policy.unsafe_score,
                         "ref": p_nsfw})
        print(f"  {folder.name:22} {len(paths):5} images")

    if not rows:
        print("no images found", file=sys.stderr)
        return 2

    thr = check.config.reject_min
    both = sum(1 for r in rows if r["ref"] >= args.ref_threshold and r["sexual"] >= thr)
    miss = [r for r in rows if r["ref"] >= args.ref_threshold and r["sexual"] < thr]
    false_pos = [r for r in rows if r["ref"] < args.ref_threshold and r["sexual"] >= thr]
    neither = len(rows) - both - len(miss) - len(false_pos)

    # The reference answers "is this NSFW", which it reads broadly: it calls bloody injury NSFW
    # too. So "did the `sexual` label fire" is the wrong comparison on its own - the picture may
    # be refused under `gore` instead, and refused is refused. Both numbers are reported: the
    # label-specific one, and the one that says whether the upload actually got through.
    ref_nsfw = [r for r in rows if r["ref"] >= args.ref_threshold]
    refused = [r for r in ref_nsfw if r["unsafe"] >= thr]
    got_through = [r for r in ref_nsfw if r["unsafe"] < thr]

    print(f"\n{len(rows)} images\n")
    print(f"{'':28}{'reference: NSFW':>18}{'reference: normal':>20}")
    print(f"{'policy: sexual fires':28}{both:>18}{len(false_pos):>20}")
    print(f"{'policy: it does not':28}{len(miss):>18}{neither:>20}")
    agree = (both + neither) / len(rows)
    print(f"\nagreement {agree:.2%}")
    flagged_by_ref = both + len(miss)
    if flagged_by_ref:
        print(f"the reference calls {flagged_by_ref} NSFW; the policy agrees on "
              f"{both} of them ({both / flagged_by_ref:.0%})")
    else:
        print("the reference called nothing NSFW in these sets, so recall is still unmeasured "
              "here - this says the sets are clean, not that the label works")

    if ref_nsfw:
        print(f"\nof the {len(ref_nsfw)} the reference calls NSFW, the policy REFUSES "
              f"{len(refused)} ({len(refused)/len(ref_nsfw):.0%}) under some label:")
        by_label: dict[str, int] = {}
        for r in refused:
            by_label[r["label"]] = by_label.get(r["label"], 0) + 1
        for label, count in sorted(by_label.items(), key=lambda kv: -kv[1]):
            print(f"    {count:4}  as {label}")
        if got_through:
            print(f"  {len(got_through)} got through entirely:")
            for r in sorted(got_through, key=lambda x: -x["ref"])[:10]:
                print(f"    ref {r['ref']:.3f}  unsafe {r['unsafe']:.3f}  top={r['label']:7} "
                      f"[{r['set']}] {r['path'].name[:40]}")

    for name, items, note in (
        ("SEXUAL-LABEL MISSES (reference says NSFW, `sexual` does not — may be caught as gore)",
         miss, "which label caught it is in `top=`"),
        ("FALSE POSITIVES (policy fires, reference says normal)", false_pos, ""),
    ):
        if not items:
            continue
        print(f"\n{name}: {len(items)}  {note}")
        for r in sorted(items, key=lambda x: -x["ref"])[:15]:
            print(f"  ref {r['ref']:.3f}  sexual {r['sexual']:.3f}  "
                  f"top={r['label']:7} [{r['set']}] {r['path'].name[:40]}")

    # Where the reference sits on the sets as a whole, which also says something about it.
    print("\nreference P(nsfw) by set:")
    by_set: dict[str, list[float]] = {}
    for r in rows:
        by_set.setdefault(r["set"], []).append(r["ref"])
    for name, vals in sorted(by_set.items()):
        vals.sort()
        over = sum(1 for v in vals if v >= args.ref_threshold)
        print(f"  {name:22} n={len(vals):4}  p50 {vals[len(vals)//2]:.3f}  "
              f"max {vals[-1]:.3f}  called NSFW {over:4}")

    if args.dump and (miss or false_pos):
        out = args.dump_dir or (ROOT / "scripts" / "sexual_label_disagreements")
        shutil.rmtree(out, ignore_errors=True)
        for name, items in (("miss", miss), ("false_positive", false_pos)):
            folder = out / name
            folder.mkdir(parents=True, exist_ok=True)
            for r in sorted(items, key=lambda x: -x["ref"])[: args.dump]:
                shutil.copy(r["path"], folder / f"ref{r['ref']:.3f}_sex{r['sexual']:.3f}_{r['path'].name}")
        print(f"\ndisagreements written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
