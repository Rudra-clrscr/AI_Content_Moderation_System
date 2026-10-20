"""Add `public_label` to the eval sets: what the answer is when the text is PUBLISHED.

    python training/relabel_public_surface.py            # writes the column
    python training/relabel_public_surface.py --check    # reports, changes nothing

WHY A SECOND COLUMN AND NOT AN EDIT.

`label` answers "would the main model refuse this anywhere", and for a complaint the answer is
no, deliberately - a member chasing a damaged consignment through a proposal is the platform
working, and an earlier round that refused that language made the portal useless for disputes.
Changing `label` would break that evaluation and quietly move the main model's target.

`public_label` answers the other question: published as an ARTICLE to the whole network, is
this a complaint about a counterparty that BONC refuses? Decided 2026-10-17 on this example:

    "Their LR copy has the wrong destination pin code again."
    article -> refuse        proposal -> publish

Both labels are right. The surface is what separates them, which is the whole design.

WITHOUT THIS, EVERY FALSE-POSITIVE NUMBER FOR THE GRIEVANCE CHECK READS WORSE THAN IT IS.
Measured on grievance-v2: 57 of 274 "legitimate" texts refused, and about half of them are
complaints the policy says to refuse - "Your driver was rude to our staff and we have reported
it", "We are raising a formal complaint about this shipment". Judging a public-surface model
with sets written for private-surface language is a category error.

HOW THE LINES WERE DRAWN, because the groups do not split cleanly and a group-level rule would
be wrong in both directions:

  refused on a public surface
    a complaint about a counterparty, however politely phrased - a defect, a short delivery,
    a late consignment, a charge nobody explained, an unanswered phone
    mild venting about one of those ("honestly disappointed, but these things happen")
    emotional language about being let down

  still published
    PROFESSIONAL CRITICISM, which is the distinction worth keeping: "I disagree with his
    approach to pricing, though he explained his reasoning clearly" is an assessment, not a
    grievance. So is "I would not use them for urgent work, but their standard orders are
    reliable" - a recommendation with a caveat is how members help each other.
    contract and negotiation language: penalties, termination, legal notice, walking away
    descriptions of using the proper channel - arbitration invoked, raised on their portal
    a balanced review where the problem was resolved ("the technician came the next day")
    articles, including ones that mention a setback, because that is what makes them stories

The list below is text, not group names, because `firm_business_language` alone holds both
"We will withhold the final payment until the defects are rectified" (publishes - negotiation)
and "Your driver was rude to our staff" (refused - complaint).
"""
from __future__ import annotations

import argparse
import csv
import glob
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Matched on a distinctive prefix so a small edit to the tail of a row does not silently drop
# it from the list. Every one of these is `label` 0 and `public_label` 1.
REFUSED_WHEN_PUBLISHED = [
    # b2b_negotiation / complaint
    "Frankly the delivery was late and the packaging",
    "The last three consignments were short by 12 kg",
    "Quality has dropped since the factory moved",
    "We are disappointed with the response time",
    "This is the third delay this quarter",
    "The samples did not match the specification",
    "We are escalating this because two emails",
    # dismissive / absurd_about_thing - complaints about a supplier's charges or handling
    "The freight charge they added at the last minute",
    "It is ridiculous that a simple GST correction",
    "Their minimum order quantity is laughable",
    "The warranty terms are nonsense",
    "A pathetic excuse for packaging",
    "The exchange rate they used on the invoice",
    "Honestly the waiting time at their warehouse",
    "We think the revised return policy is ridiculous",
    # dismissive / hinglish_heldout - the two that are complaints
    "Pichle mahine jo order diya tha uska bill",
    "Itne paise lagane ke baad bhi machine chali nahi",
    # handwritten / review - the ones that are complaints rather than balanced reviews
    "The fabric bled colour after the first wash",
    "He promised delivery by Friday and it came",
    "They charged us for pallets we never received",
    "Honestly the worst packaging I have seen",
    "We will not reorder because the grade of paper",
    # implied_threats / firm_business_language - the complaint half of a mixed group
    "The quality of this batch was unacceptable",
    "Your driver was rude to our staff",
    "We are extremely disappointed with how this order",
    "This is the third time your consignment has arrived",
    "We have lost a customer because of your delay",
    "Your after-sales service has been appalling",
    "I have never dealt with a less organised supplier",
    "The packing was careless and half the stock",
    "Nobody at your office answers the phone",
    "We are raising a formal complaint about this shipment",
    "Aapka maal time par nahi aaya",
    # public_callouts / ordinary_complaint
    "Can the admins please look into what happened",
    "Third consignment in a row arriving with torn",
    "The quantity billed does not match",
    "Still waiting on the credit note promised",
    "Rates quoted on the call were not what appeared",
    "They committed 15 days and it has been 40",
    "Quality of the last lot was below the approved sample",
    "Nobody picked up the phone for four days",
    "Their LR copy has the wrong destination pin code",
    "We are unhappy with the delay but their team",
    "Had to pay demurrage because the documents",
    "The grade supplied was 202 when the PO",
    "Not satisfied with how the warranty claim",
    "The finishing on this batch is rough",
    "Maal time par nahi aaya, please status update",
    # public_callouts / mild_venting
    "Honestly disappointed, but these things happen",
    "Frustrating week, though the consignment did",
    "Not the smoothest order we have run",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    matched: set[str] = set()
    total_changed = 0
    for path in sorted(glob.glob(str(HERE / "eval_*.csv"))):
        p = Path(path)
        if "grievance" in p.name or "policy" in p.name:
            continue
        rows = list(csv.DictReader(p.open(encoding="utf-8")))
        if not rows or "label" not in rows[0]:
            continue
        changed = 0
        for r in rows:
            public = r["label"]
            if r["label"] == "0":
                for prefix in REFUSED_WHEN_PUBLISHED:
                    if r["text"].startswith(prefix):
                        public, _ = "1", matched.add(prefix)
                        changed += 1
                        break
            r["public_label"] = public
        total_changed += changed
        if changed:
            print(f"  {p.name}: {changed} rows refused when published")
        if not args.check:
            fields = [f for f in rows[0] if f != "public_label"] + ["public_label"]
            with p.open("w", encoding="utf-8", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=fields, quoting=csv.QUOTE_ALL)
                w.writeheader()
                w.writerows(rows)

    print(f"\n{total_changed} rows carry public_label 1 with label 0")
    unmatched = [p for p in REFUSED_WHEN_PUBLISHED if p not in matched]
    if unmatched:
        # A prefix that matches nothing means a row was edited or removed and this list is
        # drifting away from the data. Silent drift is how a relabelling becomes folklore.
        print(f"\n  WARNING: {len(unmatched)} entries matched no row:", file=sys.stderr)
        for u in unmatched:
            print(f"    {u}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
