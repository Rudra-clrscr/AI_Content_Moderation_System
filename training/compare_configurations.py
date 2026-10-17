"""Which SYSTEM does the job: a text model, or a text model plus the grievance check?

    python training/compare_configurations.py

The question this answers is the one that was actually asked at the start: v5 behaves well, and
what needs adding is refusal of emotional outcry and customer complaints. There are two ways to
get there and they are not equivalent.

  A  v5 alone                 the baseline that was working
  B  v6 alone                 v5 retrained with callouts and implied threats folded in
  C  v5 + grievance model     v5 untouched, a second model consulted on PUBLIC surfaces only
  D  v6 + grievance model     what is deployed on this branch right now

B is the approach that changes the model everyone depends on. It bought implied_threats
17.5% -> 59.2% and public_callouts 5.1% -> 59.0%, and regression_check.py shows it also lost
six texts v5 decided correctly, including OTP phishing at 0.950 -> 0.159.

C leaves v5 exactly as it is and puts the new requirement in a model that is only ever asked
about article, post and advertisement - so nothing a proposal or a listing does can change.

This runs all four through the REAL pipeline (gate, model, scans, routing, grievance), on the
article surface, and reports each on: the emotional and complaint genre that was asked for,
the policy categories, and the six texts v6 regressed on.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# The six regressions regression_check.py found between v5 and v6. label 1 = must be refused.
V6_REGRESSIONS = [
    ("Expired medicines repacked with fresh dates, very cheap for bulk buyers.", 1, "counterfeit"),
    ("Register now with a refundable deposit of Rs 7,500 and get guaranteed monthly orders.", 1, "registration_fee"),
    ("Pay, or we come and take the value in goods.", 1, "implied_threat"),
    ("send me otp fast otherwise your account will closed", 1, "otp_phishing"),
    ("Aapke yahan se maal mangwa kar dekha, theek thaak nikla.", 0, "hinglish_review"),
    ("visit shreetextiles.co.in for details", 0, "contact_fragment"),
]


def load_set(path: Path):
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    return [(r["text"], int(r["label"]), r.get("group") or r.get("note") or "") for r in rows]


def run(model_dir: str, grievance: bool, cases, surface: str):
    """Fresh app per configuration: settings are read once at import, so the env has to be set
    before create_app and the module state has to be dropped between runs."""
    os.environ["MODEL_DIR"] = model_dir
    os.environ["GRIEVANCE_CHECK"] = "1" if grievance else "0"
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]
    sys.path.insert(0, str(ROOT / "flask_api"))
    from app import create_app
    client = create_app().test_client()
    out = []
    for text, label, group in cases:
        r = client.post("/v1/moderate", json={"content": text, "content_type": surface,
                                              "content_id": "c"}).get_json()
        refused = r["decision"] != "allow"
        out.append((refused == (label == 1), refused, r.get("decided_by"), label, group, text))
    return out


def summarise(name: str, results, cases) -> dict:
    pos = [r for r in results if r[3] == 1]
    neg = [r for r in results if r[3] == 0]
    return {
        "name": name,
        "caught": sum(r[0] for r in pos) / max(len(pos), 1),
        "allowed": sum(r[0] for r in neg) / max(len(neg), 1),
        "correct": sum(r[0] for r in results) / max(len(results), 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--surface", default="article")
    args = ap.parse_args()

    suites = {
        "emotional + complaints (hand-written, clean)":
            load_set(HERE / "eval_grievance_handwritten_2.csv"),
        "emotional + complaints (hand-written, set 1)":
            load_set(HERE / "eval_grievance_handwritten.csv"),
        "public callouts + outcry (held out)":
            load_set(HERE / "eval_public_callouts.csv"),
        "policy categories (hand-written)":
            load_set(HERE / "eval_policy_categories.csv"),
        "general held-out (handwritten)":
            load_set(HERE / "eval_handwritten.csv"),
        "the six v6 regressions":
            V6_REGRESSIONS,
    }

    configs = [("A  v5 alone", "models/v5", False),
               ("B  v6 alone", "models/v6", False),
               ("C  v5 + grievance", "models/v5", True),
               ("D  v6 + grievance", "models/v6", True)]

    print(f"\nsurface: {args.surface}\n")
    table = {}
    for label, model_dir, grievance in configs:
        table[label] = {}
        for suite, cases in suites.items():
            res = run(model_dir, grievance, cases, args.surface)
            table[label][suite] = summarise(label, res, cases)
            if suite == "the six v6 regressions":
                table[label]["_detail"] = res

    for suite in suites:
        print(f"== {suite}")
        print(f"   {'':22}{'refused correctly':>20}{'published correctly':>22}{'overall':>10}")
        for label, _, _ in configs:
            s = table[label][suite]
            print(f"   {label:<22}{s['caught']:>19.1%}{s['allowed']:>22.1%}{s['correct']:>10.1%}")
        print()

    print("== the six v6 regressions, text by text " + "-" * 36)
    for i, (text, label, group) in enumerate(V6_REGRESSIONS):
        want = "refuse" if label == 1 else "publish"
        print(f"\n  [{group}] want {want}: {text[:66]}")
        for cfg, _, _ in configs:
            ok, refused, by, *_ = table[cfg]["_detail"][i]
            print(f"     {cfg:<22} {'refused' if refused else 'published':<10} "
                  f"by {str(by):<10} {'OK' if ok else 'WRONG'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
