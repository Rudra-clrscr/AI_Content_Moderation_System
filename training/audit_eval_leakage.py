"""Which evaluation sentences are also training frames, and what that costs the numbers.

    python training/audit_eval_leakage.py                  # the audit, exits 1 if anything leaks
    python training/audit_eval_leakage.py --measure \\
        --ckpt D:/AI/bonc-grievance-v8inc/ckpt             # also price the leak in points

WHY THIS EXISTS. training/README.md says the eval_*.csv sets are "hand-written held-out sets,
never trained on", and the whole measurement discipline in this repo rests on that sentence:
the generated test split is reported and ignored precisely because a generator makes its own
held-out rows separable. The hand-written sets are supposed to be the part that cannot be
gamed.

They are not currently held out. generate_support_complaints.py's round 4 was written FROM
accept_system.py's false positives, and it was written by COPYING the failing sentences into
NEGATIVE_FRAMES rather than by writing frames for the families they belong to. So the eval row
and the training frame are the same string, and every number measured on those rows is a
memorisation score wearing a generalisation label.

WHAT "AT THE SOURCE" MEANS, AND WHY THIS SCRIPT DOES NOT PICK A SIDE.

There are two ways to separate them and they are not equivalent:

  drop the frame     The eval row keeps its provenance - written by hand, before the model,
                     by someone who was not looking at the training data. That provenance
                     cannot be rebuilt once spent, which argues for protecting it. The cost is
                     that the family loses its most representative training examples, and
                     round 4 of generate_workplace_grievances.py measured that cost: the
                     families it was meant to teach did not move.

  rewrite the row    The generator keeps its frames, and a fresh paraphrase of the same
                     phenomenon goes in the eval set. The cost is provenance: the new row was
                     written after the training data, by whoever is doing the rewriting, and
                     that is a weaker guarantee than the original had.

The defensible answer is BOTH, applied per family rather than per row: write new frames for
the family so training still teaches it, and leave the eval rows alone so the measurement
keeps its provenance. That is work on the generator, not a flag on this script. What this
script does is make the problem visible, keep it visible, and price it.

PRIOR ART, WHICH EXISTED AND WAS NOT ENOUGH. synth_data.py already reads every eval_*.csv
into `_EVAL_KEYS` and drops generated rows that match - the right idea, implemented in one
generator, three years of frames ago. Nothing carried it to the eight other generators, and
nothing measured whether it was working. One generator defending itself is not a guarantee;
this script and flask_api/tests/test_eval_leakage.py are the guarantee.

HOW THE FRAMES ARE FOUND. Every generator module is imported and every module-level list of
strings is read, which is generic on purpose: a new generator, or a new frame list in an old
one, is covered without this file being edited. Entries containing a `{` slot are skipped,
because those are templates rather than sentences and cannot match an eval row verbatim. The
comparison is on alphanumerics and single spaces, so punctuation and case cannot hide a
duplicate - which is what a near-duplicate varies first.
"""
from __future__ import annotations

import argparse
import csv
import importlib
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "flask_api"))

# Every generator in training/. Imported by name rather than globbed so that adding one is a
# deliberate line in this file - a generator nobody audited is the situation being fixed.
GENERATORS = (
    "synth_data",
    "generate_support_complaints",
    "generate_workplace_grievances",
    "generate_public_callouts",
    "generate_implied_threats",
    "generate_policy_rows",
    "generate_hinglish_and_idioms",
    "generate_public_grievances",
    "generate_v6_gaps",
)


def norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", str(t).lower())).strip()


def frames_of(module_name: str) -> dict[str, list[str]]:
    """Module-level lists of strings, by attribute name. Slotted templates are skipped."""
    try:
        mod = importlib.import_module(module_name)
    except Exception as exc:
        print(f"  (could not import {module_name}: {type(exc).__name__}: {exc})",
              file=sys.stderr)
        return {}
    out = {}
    for attr in dir(mod):
        # UPPER_CASE only. Frame lists in this repo are module constants, and the exception
        # that proves the rule is synth_data.row - a loop variable that escaped to module
        # scope from synth_data's OWN leakage guard, which reads every eval_*.csv into
        # _EVAL_KEYS so it can drop any generated row matching one. Reporting that as a leak
        # source accused the one generator already doing this right, and sent whoever read
        # the report looking for a frame list that does not exist.
        if attr.startswith("_") or not attr.isupper():
            continue
        val = getattr(mod, attr)
        if isinstance(val, (list, tuple)) and val and all(isinstance(x, str) for x in val):
            literal = [x for x in val if x and "{" not in x]
            if literal:
                out[attr] = literal
    return out


def eval_rows(eval_dir: Path) -> dict[str, list[dict]]:
    out = {}
    for path in sorted(eval_dir.glob("eval_*.csv")):
        try:
            with path.open(encoding="utf-8") as fh:
                rows = [r for r in csv.DictReader(fh) if r.get("text")]
            if rows:
                out[path.name] = rows
        except Exception as exc:                                        # pragma: no cover
            print(f"  (could not read {path.name}: {exc})", file=sys.stderr)
    return out


def audit(eval_dir: Path) -> tuple[dict, dict]:
    """Returns (per-eval-file leak detail, frame -> where it came from)."""
    origin: dict[str, list[str]] = {}
    seen_lists: dict[int, str] = {}
    for mod in GENERATORS:
        for attr, literals in frames_of(mod).items():
            # A generator that IMPORTS another's frame list re-exports the same object
            # (generate_workplace_grievances.SUPPLIER_NEGATIVE_FRAMES is
            # generate_support_complaints.NEGATIVE_FRAMES). Attributing the leak to both
            # would invent a second source and send whoever fixes it to the wrong file.
            ident = id(getattr(importlib.import_module(mod), attr))
            if ident in seen_lists:
                continue
            seen_lists[ident] = f"{mod}.{attr}"
            for frame in literals:
                origin.setdefault(norm(frame), []).append(f"{mod}.{attr}")

    detail = {}
    for name, rows in eval_rows(eval_dir).items():
        leaked = []
        for i, r in enumerate(rows):
            key = norm(r["text"])
            if key in origin:
                leaked.append({"row": i, "text": r["text"], "label": r.get("label", ""),
                               "note": r.get("note", "") or r.get("group", ""),
                               "from": sorted(set(origin[key]))})
        detail[name] = {"n": len(rows), "leaked": leaked}
    return detail, origin


def measure(ckpt: str, bundle: str, device: str, detail: dict, threshold: float) -> None:
    """Score the leaked rows and the clean rows separately, per eval file.

    This is the number the audit exists to produce. If a model scores the leaked rows well and
    the clean rows of the same file badly, the published figure for that file is an average of
    a memorisation score and a generalisation score, and only the second one predicts what
    happens to text nobody has seen.
    """
    import numpy as np
    from torch_scorer import TorchScorer
    scorer = TorchScorer(ckpt, bundle, device=device)

    print(f"\n{'=' * 78}\nLEAKED vs CLEAN, scored on {ckpt} at threshold {threshold}")
    print("accuracy on each half of each file. A gap is the memorisation premium.\n")
    print(f"  {'eval file':34} {'leaked':>16} {'clean':>16}")
    print(f"  {'':34} {'n   accuracy':>16} {'n   accuracy':>16}")
    print("  " + "-" * 68)
    tot = {"leaked_n": 0, "leaked_ok": 0, "clean_n": 0, "clean_ok": 0}
    for name, d in sorted(detail.items()):
        if not d["leaked"]:
            continue
        with open(HERE / name, encoding="utf-8") as fh:
            rows = [r for r in csv.DictReader(fh) if r.get("text")]
        leaked_idx = {x["row"] for x in d["leaked"]}
        halves = {}
        for half, idx in (("leaked", leaked_idx),
                          ("clean", set(range(len(rows))) - leaked_idx)):
            sub = [rows[i] for i in sorted(idx)]
            if not sub:
                halves[half] = (0, float("nan"))
                continue
            p = np.array([scorer.score(r["text"]).risk_score for r in sub])
            y = np.array([int(r["label"]) for r in sub])
            ok = float((((p >= threshold).astype(int)) == y).mean())
            halves[half] = (len(sub), ok)
            tot[f"{half}_n"] += len(sub)
            tot[f"{half}_ok"] += int(round(ok * len(sub)))
        (ln, la), (cn, ca) = halves["leaked"], halves["clean"]
        print(f"  {name:34} {ln:4} {la:10.1%} {cn:5} {ca:10.1%}")
    if tot["leaked_n"] and tot["clean_n"]:
        la = tot["leaked_ok"] / tot["leaked_n"]
        ca = tot["clean_ok"] / tot["clean_n"]
        print("  " + "-" * 68)
        print(f"  {'ALL FILES WITH A LEAK':34} {tot['leaked_n']:4} {la:10.1%} "
              f"{tot['clean_n']:5} {ca:10.1%}")
        print(f"\n  memorisation premium: {la - ca:+.1%} "
              f"({la:.1%} on sentences that are also training frames, "
              f"{ca:.1%} on sentences that are not)")


def scan_datasets(res_dir: Path, eval_dir: Path, limit_mb: float) -> dict:
    """The same question asked of the ACTUAL training data, not of the frame lists.

    Reading module-level lists under-reports, and finding out why was instructive: the
    sentence "Payment clear ho gaya hai, invoice ki copy attach kar di hai" is in
    eval_public_callouts.csv and in the training data, but it is not in any named frame list -
    it arrives as a composed row, and the only reason the frame scan saw it at all is that a
    loop variable called `row` leaked to module scope in synth_data.py. A generator that
    builds its sentences in a function is invisible to that scan.

    So this streams every res/*.csv and asks the only question that actually matters: does
    this exact evaluation sentence appear in the corpus the model trains on. It is slower and
    it is the authoritative answer.
    """
    want = {}
    for path in sorted(eval_dir.glob("eval_*.csv")):
        with path.open(encoding="utf-8") as fh:
            for i, row in enumerate(csv.DictReader(fh)):
                if row.get("text"):
                    want.setdefault(norm(row["text"]), []).append((path.name, i, row.get("label", "")))
    hits: dict[str, set[str]] = {}
    for path in sorted(res_dir.glob("*.csv")):
        mb = path.stat().st_size / 1e6
        if mb > limit_mb:
            print(f"  (skipping {path.name}, {mb:.0f} MB > --limit-mb {limit_mb:.0f})")
            continue
        n = 0
        try:
            with path.open(encoding="utf-8", errors="replace", newline="") as fh:
                reader = csv.DictReader(fh)
                if not reader.fieldnames or "text" not in reader.fieldnames:
                    continue
                for row in reader:
                    t = row.get("text")
                    if not t:
                        continue
                    key = norm(t)
                    if key in want:
                        hits.setdefault(key, set()).add(path.name)
                        n += 1
        except Exception as exc:                                        # pragma: no cover
            print(f"  (could not scan {path.name}: {type(exc).__name__}: {exc})", file=sys.stderr)
            continue
        print(f"  scanned {path.name:34} {mb:7.1f} MB  {n:6,} rows match an eval sentence")
    return {k: (want[k], sorted(v)) for k, v in hits.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval-dir", type=Path, default=HERE)
    ap.add_argument("--show", type=int, default=8, help="leaked rows to print per file")
    ap.add_argument("--measure", action="store_true",
                    help="also score the leaked and clean halves separately")
    ap.add_argument("--ckpt", default="D:/AI/bonc-grievance-v8inc/ckpt")
    ap.add_argument("--bundle", default=str(ROOT / "flask_api" / "models" / "grievance-v7"))
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--threshold", type=float, default=0.978)
    ap.add_argument("--datasets", action="store_true",
                    help="also scan res/*.csv - the actual training corpora - which is the "
                         "authoritative check; the frame scan under-reports composed rows")
    ap.add_argument("--res-dir", type=Path, default=ROOT / "res")
    ap.add_argument("--limit-mb", type=float, default=300.0)
    args = ap.parse_args()

    detail, origin = audit(args.eval_dir)
    print(f"{len(origin):,} literal frames across {len(GENERATORS)} generators, "
          f"checked against {len(detail)} eval files\n")

    total_leaked = total_rows = 0
    for name, d in sorted(detail.items()):
        total_rows += d["n"]
        total_leaked += len(d["leaked"])
        flag = "LEAK" if d["leaked"] else "  ok"
        print(f"  {flag}  {name:36} {len(d['leaked']):3} of {d['n']:4} rows are training frames")
        for x in d["leaked"][: args.show]:
            print(f"          label={x['label']}  {x['text'][:64]}")
            print(f"              from {', '.join(x['from'])}")
        if len(d["leaked"]) > args.show:
            print(f"          ... and {len(d['leaked']) - args.show} more")

    print(f"\n{total_leaked} of {total_rows} evaluation sentences ({total_leaked / max(total_rows, 1):.1%}) "
          f"are also training frames.")

    corpus_leaked = 0
    if args.datasets:
        print(f"\n{'=' * 78}\nSCANNING THE ACTUAL TRAINING CORPORA in {args.res_dir}\n")
        found = scan_datasets(args.res_dir, args.eval_dir, args.limit_mb)
        corpus_leaked = len(found)
        by_file: dict[str, int] = {}
        for key, (where, corpora) in found.items():
            for name, _i, _l in where:
                by_file[name] = by_file.get(name, 0) + 1
        print(f"\n  {corpus_leaked} distinct eval sentences appear verbatim in res/*.csv:")
        for name, n in sorted(by_file.items(), key=lambda kv: -kv[1]):
            print(f"    {n:4}  {name}")

    if args.measure and total_leaked:
        measure(args.ckpt, args.bundle, args.device, detail, args.threshold)

    if total_leaked or corpus_leaked:
        print("\nEvery number measured on those rows is a memorisation score. Fix at the "
              "source:\nwrite new frames for the family and leave the eval rows alone - see "
              "the docstring.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
