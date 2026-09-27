"""Compare two model bundles (or two calibrations) end to end: gate + model + sentence checks + routing.

    python scripts/compare_models.py models/v1 models/v3
    python scripts/compare_models.py models/v3 models/v3 --old-review-weight 0.5 --new-review-weight 0.25

Runs a fixed set of legitimate and harmful B2B texts through the full pipeline
with each side and reports:

  * correct decisions (legitimate -> allow; harmful -> revise or reject)
  * Six Sigma quality: defects, DPMO (defects per million opportunities) and the
    sigma level (with the conventional 1.5-sigma shift), plus a 95% Wilson
    interval on the defect rate, which shows how much a small test set can tell you
  * McNemar's exact test: are the two sides' error patterns really different,
    or could the difference be chance? (p < 0.05 = significant)
  * every text whose decision changed

Use it before switching `model.dir` or `label_weights`, together with
scripts/verify_demo_sentences.py for the demo guide.
"""
import argparse
import logging
import math
import sys
from statistics import NormalDist
from collections import Counter
from pathlib import Path

logging.disable(logging.WARNING)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.sinks import MemorySink  # noqa: E402

LEGIT = [
    "Family-run textile mill in Surat producing cotton and viscose fabrics since 1998. ISO 9001 certified.",
    "Organic turmeric powder, 25 kg bags, lab tested for purity. Minimum order 500 kg.",
    "We are attending the Delhi Trade Fair next week. Visit our stall to see our new product range.",
    "Logistics company offering warehousing and last-mile delivery services in Pune and Mumbai.",
    "We are an ISO 9001 certified manufacturer of copper wires and cables, supplying clients across India since 2005.",
    "Our sales manager Priya is on leave this week. She will be back on Monday to handle your orders.",
    "We met Mr. Sharma at the expo. He showed us their new range of solar inverters.",
    "Ordered LED lights last month. They arrived damaged, but their support team replaced them quickly.",
    "The packaging was poor. They need to improve how they pack fragile items.",
    "Delivery was late by two weeks. He did not answer calls and we lost a client because of it.",
    "Their prices are higher than competitors, but the quality justifies it. Recommended.",
    "Contact our founder directly. He handles all bulk enquiries personally.",
    "Quality was not as described. They sent the wrong grade of steel and refused a refund.",
    "Family business since 1985. His sons now run the export division and their team is growing.",
    "Ask for Meena in accounts. She can share GST invoices and bank details for payment.",
    "We supply cotton yarn to exporters. They trust us because we never compromise on quality.",
    "Cotton bedsheets in king and queen sizes, 300 thread count. Bulk orders welcome.",
    "High quality PVC pipes, ISI marked, available in all sizes. Bulk orders welcome.",
    "LED panel lights, 12W to 48W, BIS certified. Test certificates provided with every order.",
    "Solar panels 330W mono PERC, 25 year warranty. Installation support available.",
    "Ceramic floor tiles, 600x600 mm, glossy and matt finish. Samples available on request.",
    "Fresh alphonso mangoes from Ratnagiri, packed in 5 kg boxes. Bulk orders welcome.",
    "Get 70% off on all branded goods this week only, hurry before stock runs out!",
    "Great supplier. They delivered 500 units on time and the quality was excellent.",
    "Industrial stainless steel valves, sizes 15mm to 300mm. Test certificates provided with every order.",
    "Premium basmati rice exporter, 20 years in business, FSSAI certified. Bulk orders welcome.",
    "He found the test results disturbing and recalled the batch.",
    "They handle toxic chemicals safely and follow all regulations.",
    "Their prices are shamelessly low this week, grab the deal.",
    "Industrial vacuum cleaner, 2000W motor sucks up dust and water. Warranty provided with every order.",
    "Hiring sales representatives in Mumbai, commission plus fixed salary. Apply via our careers page.",
    "We manufacture CNC-machined aluminium parts for automotive clients across Europe and Asia.",
]
HARMFUL = [
    "Looking for distributors in Gujarat. Very high margins, no investment needed, just register with a small fee.",
    "Work from home and earn big, message us for details",
    "Your account will be suspended. Share your password and card number to verify immediately.",
    "Urgent requirement: pay registration charges to get guaranteed government tender approval.",
    "Limited stock! Contact us on WhatsApp for wholesale prices, full payment in advance only.",
    "Good prices and quick replies from the sales team. I hope she dies in an accident soon.",
    "Cheap replica branded watches, first copy, cash on delivery",
    "Send your bank details and OTP to claim your prize money today",
    "Earn 50000 per week from home!!! No experience needed, limited slots, DM now",
    "Congratulations! You have won a lottery of 10 lakh rupees. Pay the processing charges to receive it.",
    "You people are idiots and I will destroy your business",
    "High quality PVC pipes, ISI marked, available in all sizes. Earn 50000 per week from home, no experience needed.",
    "LED panel lights, 12W to 48W, BIS certified. Test certificates provided with every order. Work from home and earn big, message us for details.",
    "Solar panels 330W mono PERC, 25 year warranty. Limited slots for dealers, register now with a small fee.",
    "Excellent packaging and prompt delivery on every order. They should all be burned alive along with their shop.",
    "his thinking and mindset disturbing",
    "She will regret the day she crossed me.",
    "Cotton bedsheets in king and queen sizes, 300 thread count. Our competitor sells fake products, do not trust them.",
]



# Articles: title + short body (titles are fragments; v3 scores them "unsure")
LEGIT += [
    "About our yarn business\n\nCotton yarn exporters since 1998. We ship to Europe and Asia.",
    "Draft about solar panels\n\nSolar panels 330W mono PERC. They come with a 25 year warranty.",
    "How we scaled a family textile mill\n\nWe started with four looms in 1998. Today we run sixty.",
    "Case study: cutting logistics costs\n\nWe moved to consolidated shipments. Delivery times improved from twelve days to eight.",
]

def run(model_dir, review_weight=None):
    s = load_settings()
    s.model_dir = Path(model_dir) if Path(model_dir).is_absolute() else ROOT / model_dir
    if review_weight is not None:
        s.label_weights = {"safe": 0.0, "review": review_weight, "reject": 1.0}
    c = create_app(s, sink=MemorySink()).test_client()
    return {t: c.post("/v1/moderate", json={"content": t, "content_type": "post"}).get_json() for t in LEGIT + HARMFUL}


def correct(text, decision):
    return decision == "allow" if text in LEGIT else decision in ("revise", "reject")


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def sigma_level(defect_rate):
    """Six Sigma convention: long-term yield -> z, plus the 1.5-sigma shift."""
    if defect_rate <= 0:
        return float("inf")
    return NormalDist().inv_cdf(1 - defect_rate) + 1.5


def six_sigma(results):
    texts = LEGIT + HARMFUL
    defects = sum(not correct(t, results[t]["decision"]) for t in texts)
    rate = defects / len(texts)
    lo, hi = wilson(defects, len(texts))
    return defects, rate, lo, hi


def mcnemar(a, b):
    """Exact McNemar test on paired correctness. Returns (b_only_wrong, a_only_wrong, p_value)."""
    texts = LEGIT + HARMFUL
    new_worse = sum(correct(t, a[t]["decision"]) and not correct(t, b[t]["decision"]) for t in texts)
    new_better = sum(not correct(t, a[t]["decision"]) and correct(t, b[t]["decision"]) for t in texts)
    n = new_worse + new_better
    if n == 0:
        return new_worse, new_better, 1.0
    k = min(new_worse, new_better)
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)
    return new_worse, new_better, p


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old_model_dir")
    ap.add_argument("new_model_dir")
    ap.add_argument("--old-review-weight", type=float)
    ap.add_argument("--new-review-weight", type=float)
    args = ap.parse_args()

    a = run(args.old_model_dir, args.old_review_weight)
    b = run(args.new_model_dir, args.new_review_weight)

    for name, texts, good in (("LEGIT (want allow)", LEGIT, {"allow"}),
                              ("HARMFUL (want revise/reject)", HARMFUL, {"revise", "reject"})):
        ca, cb = Counter(a[t]["decision"] for t in texts), Counter(b[t]["decision"] for t in texts)
        print(f"\n== {name}: {len(texts)} texts")
        print(f"   old: {dict(ca)}   correct {sum(a[t]['decision'] in good for t in texts)}/{len(texts)}")
        print(f"   new: {dict(cb)}   correct {sum(b[t]['decision'] in good for t in texts)}/{len(texts)}")
        for t in texts:
            if a[t]["decision"] != b[t]["decision"]:
                ra, rb = a[t]["risk_score"], b[t]["risk_score"]
                ra = "-" if ra is None else f"{ra:.3f}"
                rb = "-" if rb is None else f"{rb:.3f}"
                print(f"   changed: old {a[t]['decision']}({ra}) -> new {b[t]['decision']}({rb}) | {t[:70]!r}")

    n = len(LEGIT) + len(HARMFUL)
    print(f"\n== Six Sigma view ({n} opportunities: one decision per text)")
    for label, res in (("old", a), ("new", b)):
        d, rate, lo, hi = six_sigma(res)
        print(f"   {label}: {d} defects, DPMO {rate * 1e6:,.0f}, sigma level {sigma_level(rate):.2f} "
              f"(95% interval on defect rate {lo:.1%}-{hi:.1%} -> sigma {sigma_level(hi):.2f}-{sigma_level(lo):.2f})")
    print("   Six Sigma (3.4 DPMO) needs ~1,000,000 labelled decisions to even measure; "
          "a test set this size can only separate large differences.")

    worse, better, p = mcnemar(a, b)
    verdict = "significant (p < 0.05)" if p < 0.05 else "NOT significant (could be chance)"
    print(f"\n== McNemar exact test (paired): new wrong where old right: {worse}, new right where old wrong: {better}")
    print(f"   p-value = {p:.4f} -> {verdict}")


if __name__ == "__main__":
    main()
