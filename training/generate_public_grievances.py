"""Buyer grievances about goods and service - the genre that belongs on the seller's own
channel rather than on BONC's feed.

    python training/generate_public_grievances.py --out res/public_grievances.csv

    "The item arrived completely damaged and looks nothing like the picture on your website."
    "I bought this blender last week, but the motor stopped working on the very first use."
    "I ordered a medium blue jacket, but I received a small red sweater instead."

WHAT THIS IS FOR, AND WHAT IT IS NOT FOR.

These rows measure `complaint.public_grievance` in config/gate_patterns.yaml - a gate rule
scoped to `surfaces: [article, post, advertisement]`. They are NOT wired into model training,
and feeding them to the model would undo the thing the rule is built to do.

The reason is that this genre has no fixed label. The same sentence is refused in an article
and published in a proposal, because a member chasing a damaged consignment through a proposal
is the platform working. The model never sees the surface - it gets text and nothing else - so
training it on these as `unsafe` would refuse them everywhere, including in the dispute traffic
BONC exists to carry. `res/public_callouts.csv` already holds 30,000 rows of this genre
labelled 0 on purpose, after an earlier round over-refused them.

So: `label` here means "should the gate block this on a PUBLIC surface". Read it as a target
for the regex, not as a sentence that is unsafe wherever it appears. If the policy ever
changes to "complaints are refused everywhere", this file becomes training data and the 30,000
negatives have to be reconciled first - not after.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

ITEMS = ["blender", "jacket", "sweater", "mixer grinder", "ceiling fan", "water pump",
         "office chair", "LED panel", "induction cooktop", "pressure cooker", "laptop bag",
         "bedsheet set", "pair of shoes", "wall clock", "geyser", "trimmer"]
PARCEL = ["item", "parcel", "package", "product", "order", "consignment"]
BREAKAGE = ["damaged", "broken", "faulty", "defective", "smashed", "cracked"]
COLOURS = ["blue", "red", "black", "green", "grey", "maroon"]
SIZES = ["small", "medium", "large", "extra large"]
DAYS = ["last week", "on Monday", "ten days ago", "last month", "on the 3rd"]

# ---- the grievances: what the gate rule should catch on a public surface ------------
GRIEVANCE_FRAMES = [
    "The {parcel} arrived completely {broken} and looks nothing like the picture on your website.",
    "My {parcel} came {broken} and nothing like the photo in the listing.",
    "I bought this {item} {day}, but the motor stopped working on the very first use.",
    "Bought a {item} {day} and it stopped working on the first day.",
    "The fabric of this shirt is extremely thin and it ripped during the first wash.",
    "The colour faded during the first wash and the stitching came apart.",
    "I ordered a {size} {colour} jacket, but I received a {size2} {colour2} sweater instead.",
    "I ordered a {item} but I received something completely different.",
    "I purchased two units but I was sent only one and nobody will answer.",
    "Your system says my package was delivered, but I have checked everywhere and it is not here.",
    "The tracking says my parcel was delivered but it never arrived at our address.",
    "My claim was denied based on a clause hidden deep within the fine print and never explained.",
    "The warranty claim was rejected on a technicality nobody mentioned at the time of sale.",
    "My refund was refused even though the {item} was returned within the window.",
    "The replacement was denied because of a condition buried in the fine print.",
    "Worst service I have ever had from any seller, avoid them.",
    "Terrible quality and terrible support, never again.",
    "That company's delivery system is very bad and nobody there seems to care.",
    "Their service is really poor and the staff are useless.",
    "This supplier's quality is terrible compared to what was promised.",
    # Courier conduct: the goods are fine, the handling is the complaint.
    "The driver left my fragile {parcel} out in the pouring rain instead of under the porch.",
    "The courier dumped my {parcel} in the rain and walked off without knocking.",
    "My {parcel} was left unattended on the street for hours.",
    "The delivery boy threw the {parcel} over the gate.",
    "Their delivery agent was extremely rude when I asked him to wait for a signature.",
    "He left my {parcel} outside the gate in the heavy rain.",
]

# ---- the negatives: same words, legitimate use --------------------------------------
# These decide whether the rule is narrow enough. Each is a sentence a member could
# reasonably publish on the feed, using the grievance vocabulary.
NEGATIVE_FRAMES = [
    "We handle {broken}-goods claims within 48 hours of delivery.",
    "Our warranty covers manufacturing defects on every {item} for twelve months.",
    "If a {parcel} arrives {broken}, photograph it before signing and we replace it free.",
    "Returns are accepted within 14 days if the {item} is unused.",
    "Their pricing is very competitive and the lead times are short.",
    "That company's delivery system is reliable and fast.",
    "This supplier's quality has been consistent across every batch.",
    "Our support team is available all week for installation questions.",
    "We stock the {item} in {colour} and {colour2}, sizes {size} to {size2}.",
    "Please confirm whether the consignment was delivered on Tuesday.",
    "The tracking number will be shared as soon as the parcel leaves our warehouse.",
    "We ship {item} units across India with insurance included.",
    "Claims are settled directly with the transporter, not the buyer.",
    "Read the terms carefully before placing a bulk order.",
    "Our refund policy is published in full on the product page.",
    "The first wash may soften the fabric; that is normal for this weave.",
    "Case study: how we cut damaged-in-transit rates from 4% to under 1%.",
    "A guide to reading warranty terms before you sign a supply contract.",
    "Our drivers never leave a {parcel} in the rain; every doorstep drop is photographed.",
    "We do not leave a {parcel} unattended - someone must sign for it.",
    "Rain covers are standard on every consignment we move during the monsoon.",
    "Our delivery staff are trained in handling fragile goods.",
]


def build(rng: random.Random, frame: str) -> str:
    c1, c2 = rng.sample(COLOURS, 2)
    s1, s2 = rng.sample(SIZES, 2)
    return frame.format(item=rng.choice(ITEMS), parcel=rng.choice(PARCEL),
                        broken=rng.choice(BREAKAGE), day=rng.choice(DAYS),
                        colour=c1, colour2=c2, size=s1, size2=s2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=HERE.parent / "res" / "public_grievances.csv")
    ap.add_argument("--grievances", type=int, default=6_000)
    ap.add_argument("--negatives", type=int, default=4_000)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    rows: list[dict] = []
    seen: set[str] = set()

    def emit(frames, count, label, group):
        made = guard = 0
        while made < count and guard < count * 60:
            guard += 1
            frame = rng.choice(frames)
            text = build(rng, frame)
            if text.lower() in seen:
                continue
            seen.add(text.lower())
            rows.append({"text": text, "label": label, "group": group, "template": frame})
            made += 1
        return made

    g = emit(GRIEVANCE_FRAMES, args.grievances, 1, "public_grievance")
    n = emit(NEGATIVE_FRAMES, args.negatives, 0, "legitimate_commercial")
    short = [(name, got, want) for name, got, want in
             (("grievances", g, args.grievances), ("negatives", n, args.negatives))
             if got < want]
    rng.shuffle(rows)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["text", "label", "group", "template"])
        w.writeheader()
        w.writerows(rows)

    print(f"{args.out}: {len(rows):,} rows")
    print(f"  {g:,} grievances (label 1: the gate should block these on a public surface)")
    print(f"  {n:,} legitimate commercial language (label 0: publishable everywhere)")
    print("\nNOT model training data - see the docstring. Measure the gate rule with:")
    print("  python training/eval_grievance_rule.py")
    if short:
        for name, got, want in short:
            print(f"\n  WARNING: asked for {want:,} {name} and produced {got:,}. The frame and "
                  f"slot lists bound how many distinct rows exist.", file=sys.stderr)
        print("  Add frames rather than accepting a set padded with near-duplicates - and note "
              "that\n  coverage measured on frames the regex was written against is close to "
              "circular either way.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
