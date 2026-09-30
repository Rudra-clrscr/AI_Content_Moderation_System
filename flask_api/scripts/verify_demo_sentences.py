"""Check that every demo sentence still gets the decision the demo guide promises.

Run this after changing the model, thresholds or gate rules, with the server running:

    python scripts/verify_demo_sentences.py
    python scripts/verify_demo_sentences.py --url http://127.0.0.1:8080

It reads the sentence bank in DEMO_GUIDE.md (section 4) and the example buttons in
app/static/demo.html, sends each sentence to /v1/moderate, and compares:
  - the decision (allow / revise / reject) written in the row's last column
  - the deciding layer, when the row names one ("gate", "targeted")
  - the gate rule, when the row names one (e.g. `scam.advance_fee`)
  - the highlighted text, when the row says highlights "..."

Exits with status 1 if anything differs, so the guide can be fixed before a demo.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "DEMO_GUIDE.md"
DEMO_PAGE = ROOT / "app" / "static" / "demo.html"
TYPES = {"product listing": "product_listing", "business profile": "business_profile",
         "post": "post", "advertisement": "advertisement", "article": "article"}
RULE_ID = re.compile(r"`([a-z]+\.[a-z_]+)`")


def guide_cases() -> list[dict]:
    text = GUIDE.read_text(encoding="utf-8")
    bank = text[text.index("## 4. Sentence bank"):text.index("## 5.")]
    cases = []
    for line in bank.splitlines():
        if not line.startswith("| ") or line.startswith("| Sentence") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        last = cells[-1]
        rule = RULE_ID.search(last)
        words = RULE_ID.sub("", last)  # keywords only outside rule ids ("abuse.targeted" isn't "targeted")
        decision = re.search(r"\b(allow|revise|reject)\b", words)
        case = {
            "source": "guide",
            "text": cells[0],
            "type": next((TYPES[c.lower()] for c in cells if c.lower() in TYPES), "post"),
            "decision": decision.group(1) if decision else ("reject" if rule else None),
            "decided_by": ("targeted" if re.search(r"\btargeted\b", words) else
                           "sentence" if re.search(r"· sentence\b", words) else
                           "gate" if (re.search(r"\bby gate\b", words) or (rule and not decision)) else None),
            "rule": rule.group(1) if rule else None,
            # highlights "Earn guaranteed income…" -> an issue's text must start with this
            "highlight": (h.group(1).rstrip("…").strip()
                          if (h := re.search(r'highlights "([^"]+)"', words)) else None),
        }
        if case["decision"]:
            cases.append(case)
    return cases


def page_cases() -> list[dict]:
    js = DEMO_PAGE.read_text(encoding="utf-8")
    block = js[js.index("const EXAMPLES"):js.index("];", js.index("const EXAMPLES"))]
    found = re.findall(r'tag: "(\w+)", note: "([^"]*)", type: "(\w+)",\s*text: "((?:[^"\\]|\\.)*)"', block)
    return [{"source": f"page #{i}", "text": json.loads(f'"{text}"'), "type": typ, "decision": tag,
             # "gate: …" / "gate block" = decided by the gate; "gate flag …" only raises the floor
             "decided_by": ("targeted" if note.startswith("targeted")
                            else "sentence" if note.startswith("sentence")
                            else "gate" if note.startswith(("gate:", "gate block")) else None),
             "rule": None, "highlight": None}
            for i, (tag, note, typ, text) in enumerate(found, 1)]


def moderate(url: str, text: str, ctype: str) -> dict:
    req = urllib.request.Request(f"{url}/v1/moderate", json.dumps({"content": text, "content_type": ctype}).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    args = ap.parse_args()

    try:
        ready = json.load(urllib.request.urlopen(f"{args.url}/ready", timeout=10))
    except Exception as exc:
        print(f"server not reachable at {args.url}: {exc}", file=sys.stderr)
        return 1
    print(f"model {ready.get('model_version')} · gate rules {ready.get('gate_rules')} · thresholds {ready.get('thresholds')}\n")

    cases = page_cases() + guide_cases()
    failures = 0
    for c in cases:
        r = moderate(args.url, c["text"], c["type"])
        problems = []
        if r["decision"] != c["decision"]:
            problems.append(f"decision {r['decision']} (expected {c['decision']})")
        if c["decided_by"] and r["decided_by"] != c["decided_by"]:
            problems.append(f"decided_by {r['decided_by']} (expected {c['decided_by']})")
        if c["highlight"]:
            marked = [c["text"][i["start"]:i["end"]] for i in (r.get("feedback") or {}).get("issues", [])]
            if not any(m.startswith(c["highlight"]) for m in marked):
                problems.append(f"highlight {marked} (expected one starting {c['highlight']!r})")
        if c["rule"] and c["rule"] not in [m["rule_id"] for m in r["gate_matches"]]:
            problems.append(f"rule {c['rule']} did not match")
        failures += bool(problems)
        risk = "  -  " if r["risk_score"] is None else f"{r['risk_score']:.3f}"
        print(f"{'FAIL' if problems else 'ok  '} {c['source']:8s} {r['decision']:7s} {r['decided_by']:8s} {risk} | "
              f"{c['text'][:70]}{'  <- ' + '; '.join(problems) if problems else ''}")

    print(f"\n{len(cases)} sentences checked, {failures} mismatches")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
