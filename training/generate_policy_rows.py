"""Text rows for the policy categories the picture model already has labels for.

    python training/generate_policy_rows.py --out res/policy_rows.csv

config/visual_policy.yaml refuses weapons, gore, unrest, sexual content, revealing clothing,
romantic content, charity solicitation, physique display and minors - in PICTURES. A member
who writes the same thing in words has, until now, been judged by a text model that was never
taught most of those categories. Measured on eval_policy_categories.csv, caught rate:

    category    v5     v6
    violence    25%    75%
    threats     20%    80%
    nudity     100%    60%     <- v6 lost it
    romance     40%    20%     <- v6 lost it
    gym         33%    67%
    NGO         40%    20%     <- v6 lost it
    minors      80%    80%

Both models allow 100% of the legitimate trade text in that set, which is the half worth
protecting: a lingerie manufacturer, a wedding caterer, a gym equipment supplier and a school
uniform maker are ordinary BONC businesses and must stay publishable. Every family here is
written in pairs for that reason - the offence and the trade that shares its vocabulary.

WHAT EACH CATEGORY MEANS IN TEXT, since it is not always the picture's meaning:

  violence     a stated intention to hurt someone. Not knives, not security guards.
  nudity       solicitation of sexual content or services. Not garment manufacturing.
  romance      using a trade platform to proposition a person. Not the wedding industry,
               which is one of the larger businesses on here.
  physique     promoting a body, a transformation or physique content. Not selling gym kit.
  charity      an appeal for donations. Refused because it is not a trade posting - the same
               decision taken for pictures in config/visual_policy.yaml.
  minors       employing, soliciting or exposing children. Not school uniforms or toys.

The minors family is deliberately the narrowest and most explicit. A model that learns "any
sentence mentioning a child is unsafe" would refuse the uniform maker and the toy wholesaler,
and that failure is harder to notice than the one it is meant to prevent.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

PLACES = ["Surat", "Ludhiana", "Tiruppur", "Rajkot", "Noida", "Pune", "Indore", "Coimbatore",
          "Jaipur", "Agra", "Kanpur", "Nashik", "Bhiwandi", "Panipat", "Hosur", "Erode"]
DAYS = ["Friday", "Monday", "the 10th", "month end", "the 15th", "next week"]
MONEY = ["Rs 500", "Rs 1,000", "Rs 2,500", "Rs 5,000", "Rs 10,000", "Rs 25,000"]
PEOPLE = ["that trader", "their manager", "the broker", "that fellow", "their agent",
          "the transporter", "that supplier"]
AGES = ["12", "13", "14", "15", "under 16", "under 15", "11 to 13", "13 to 15"]

TRADE_OPENERS = ["", "", "", "Wholesale enquiry.", "Dealers welcome.", "Direct from the unit.",
                 "Serious enquiries only.", "Message for rates.", "Trade enquiry from {place}.",
                 "Stock ready.", "Regular supply possible.", "Bulk orders accepted."]
TRADE_TAILS = ["", "", "", "Contact on WhatsApp.", "Rates negotiable.", "Transport extra.",
               "Call between 10 and 6.", "Delivery across {place}.", "Ask for the list.",
               "Minimum order applies."]

FAMILIES: dict[str, tuple[int, list[str], list[str]]] = {}

# ---- violence ------------------------------------------------------------------------
FAMILIES["violence"] = (1, [
    "We will break {person}'s legs if the payment does not come by {day}.",
    "Send two of our boys and sort {person} out physically.",
    "{person_cap} deserves a beating and we will arrange it.",
    "If he shows his face at the gate again he goes home in an ambulance.",
    "Our people will catch him outside the market and finish this.",
    "A few broken fingers and he will remember the due date.",
    "We know where {person} parks. It will not end well for him.",
    "Teach him with hands, words are not working.",
    "I will come to the shop myself and nobody will like what happens.",
    "Get four men ready, we are going to his godown on {day}.",
    "He should be dragged out and thrashed in front of everyone.",
    "One more delay and we handle it the old way.",
], [
    "Knife sharpening service for commercial kitchens, pickup and delivery in {place}.",
    "We supply butcher knives and cleavers to hotels, ISI marked.",
    "Industrial safety training covers crush and cut injury prevention.",
    "Licensed security guards provided for factory premises in {place}.",
    "Fire axes and emergency tools for building safety compliance.",
    "Martial arts mats and protective gear for training academies.",
    "Our CCTV package covers the gate, the yard and the loading bay.",
    "Boxing gloves and punching bags, wholesale to sports retailers.",
    "Anti-cut gloves for the glass handling line, EN388 rated.",
])

# ---- nudity and sexual solicitation ----------------------------------------------------
FAMILIES["nudity"] = (1, [
    "Send me your nudes and I will place a bigger order.",
    "Share some private pictures before we finalise the deal.",
    "Call girls available for outstation businessmen, full night.",
    "Escort service for corporate clients, discreet and verified.",
    "Send a photo without clothes and the rate is yours.",
    "Video call at night, you know what I mean, then we talk business.",
    "Massage with full service available near the {place} hotel.",
    "Girls arranged for your client visits, {money} per evening.",
    "Private pictures for {money}, DM me, nobody will know.",
    "Looking for a woman willing to send intimate photos for payment.",
], [
    "We manufacture lingerie and innerwear for export, OEM orders welcome.",
    "Swimwear and beachwear supplier in {place}, sizes XS to XXL.",
    "Medical examination gowns and hospital linen, bulk supply.",
    "Maternity wear manufacturer, 500 pieces MOQ, GST invoice.",
    "Thermal innerwear for the winter season, wholesale rates.",
    "We stitch undergarments for a European label, audited unit.",
    "Nursing wear and hospital uniforms, washable to 90 degrees.",
    "Dancewear and leotards for academies, custom sizes.",
])

# ---- romance on a trade platform -------------------------------------------------------
FAMILIES["romance"] = (1, [
    "You look beautiful, forget the order, can we meet alone on {day}?",
    "I am not interested in your products, I am interested in you.",
    "Looking for a girlfriend on this platform, serious enquiries only.",
    "Send your photo first, then we will discuss the rate.",
    "Your profile picture is lovely, are you single?",
    "Business can wait, let us have dinner together on {day}.",
    "I keep thinking about you since the call, give me your personal number.",
    "Are you married? I would like to know you better.",
    "Skip the quotation, tell me if you are free this weekend.",
    "You and me, coffee in {place}, forget the invoice.",
], [
    "Wedding decoration and mandap setup services across {place}.",
    "Bridal lehenga manufacturer, custom embroidery, wholesale rates.",
    "We print wedding invitation cards, 1000 pieces minimum.",
    "Catering for weddings and receptions, veg and non-veg menus.",
    "Wedding photography and videography, full day coverage.",
    "Return gifts and favour boxes for wedding functions, bulk rates.",
    "Banquet hall in {place} available for receptions, 500 capacity.",
    "Artificial flower garlands for wedding mandaps, wholesale.",
])

# ---- physique display ------------------------------------------------------------------
FAMILIES["physique"] = (1, [
    "Transform your body in 6 weeks, DM for my shirtless before and after photos.",
    "Follow my fitness page, abs transformation content daily, link in bio.",
    "Personal training with physique shoots included, join the challenge.",
    "Check my body transformation reel and message me for the plan.",
    "Shirtless progress pictures every week, subscribe for the full album.",
    "My physique speaks for itself, DM for coaching and photo sessions.",
    "Bodybuilding posing videos and private shoots, {money} per session.",
    "Rate my physique and I will send the diet plan free.",
], [
    "Gym equipment manufacturer, treadmills and multi-stations, 2 year warranty.",
    "Supplying rubber gym flooring to fitness centres across {place}.",
    "Whey protein distributor, FSSAI licensed, batch tested.",
    "We fit out commercial gyms turnkey, from flooring to mirrors.",
    "Dumbbells and weight plates, cast iron, wholesale to dealers.",
    "Gym management software for memberships and attendance.",
    "Sports nutrition range for retail chains, MRP labelling done.",
    "Treadmill servicing and spare parts across {place}.",
])

# ---- charity solicitation --------------------------------------------------------------
FAMILIES["charity"] = (1, [
    "Please donate for our orphanage, every rupee helps these children.",
    "Sponsor a child's education for just {money} a month, donate now.",
    "Our NGO needs urgent funds for flood relief, send to this UPI.",
    "Help us feed 500 families this winter, contribute generously.",
    "Fundraising drive for cancer patients, share widely and donate.",
    "Your {money} can give a village clean drinking water, donate today.",
    "We are collecting for the {place} shelter, any amount is welcome.",
    "Support our free school, donations are 80G exempt, send what you can.",
    "Medical emergency fund for a worker's daughter, please help.",
    "Join our donation drive for blankets this winter, give generously.",
], [
    "We supply CSR gift hampers to corporates for their outreach programmes.",
    "Printing services for NGOs: annual reports, brochures and banners.",
    "Our firm has a registered CSR policy under Section 135.",
    "Bulk supply of blankets and tarpaulins to relief agencies, GST invoice.",
    "We manufacture school furniture and supply to trusts on tender.",
    "Audit services for charitable trusts, 12A and 80G filings.",
    "Catering contracts for institutional kitchens including hostels.",
    "Water purification units supplied to rural projects, AMC available.",
    "Tender notice: supply of stationery to a charitable trust in {place}.",
    "We sponsor a local sports team; the logo placement is on the jerseys.",
    "Logistics partner for relief material movement, {place} to site.",
    "Solar lanterns supplied to development projects, 2 year warranty.",
    "Accounting software for trusts with FCRA reporting built in.",
    "We donate our rejected-but-usable stock rather than landfill it.",
])

# ---- minors ----------------------------------------------------------------------------
# Narrow and explicit on purpose. "Any sentence mentioning a child is unsafe" would refuse the
# uniform maker and the toy wholesaler, and that failure is harder to see than this one.
FAMILIES["minors"] = (1, [
    "We hire boys of {age} for the polishing section, they work cheap.",
    "Looking for girls aged {age} for packing work, no documents needed.",
    "School dropouts aged {age} preferred for the night shift, less wages.",
    "Child models wanted for private photo shoots, parents need not come.",
    "We employ minors in the workshop, nobody checks in this area.",
    "Boys of {age} can be kept at {money} a month, they do not complain.",
    "Need young helpers aged {age}, no ID required, cash payment.",
    "Children work faster on the small parts, we take them from the village.",
    "Send teenage girls for the hostel job, we will not ask for papers.",
    "Underage workers available for the season, arranged quietly.",
], [
    "School uniform manufacturer, sizes for ages 5 to 16, bulk orders.",
    "Children's toys and stationery wholesale, BIS certified.",
    "We supply mid-day meal containers to government schools.",
    "Baby food and infant formula distributor, FSSAI licensed.",
    "Our factory follows a strict no-child-labour policy, audited yearly.",
    "Paediatric furniture for clinics, child-safe edges and non-toxic paint.",
    "Kids' footwear manufacturer in {place}, sizes 1 to 7.",
    "Creche equipment and soft play items for corporate day care.",
    "Nursery school furniture and learning aids, institutional supply.",
    "We run an apprenticeship for 18-plus trainees under the NAPS scheme.",
])


def fill(rng: random.Random, frame: str) -> str:
    person = rng.choice(PEOPLE)
    return frame.format(place=rng.choice(PLACES), day=rng.choice(DAYS),
                        money=rng.choice(MONEY), age=rng.choice(AGES),
                        person=person, person_cap=person[0].upper() + person[1:])


def compose(rng: random.Random, core: str, openers, tails) -> tuple[str, str]:
    opener, tail = rng.choice(openers), rng.choice(tails)
    parts = [fill(rng, p) for p in (opener, core, tail) if p]
    return " ".join(parts), " | ".join(p for p in (opener, core, tail) if p)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "res" / "policy_rows.csv")
    ap.add_argument("--per-family", type=int, default=6_000,
                    help="harmful rows per category; negatives get the same")
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    rows, seen, counts = [], set(), {}

    def emit(frames, count, label, category):
        made = guard = 0
        while made < count and guard < count * 80:
            guard += 1
            text, skeleton = compose(rng, rng.choice(frames), TRADE_OPENERS, TRADE_TAILS)
            if text.lower() in seen:
                continue
            seen.add(text.lower())
            rows.append({"text": text, "label": label, "category": category,
                         "kind": "whole", "template": skeleton})
            made += 1
        return made

    for name, (_, harmful, lawful) in FAMILIES.items():
        counts[name] = emit(harmful, args.per_family, 1, f"policy_{name}")
        # The negatives are the same size as the positives, not a token few. These categories
        # all sit on top of real BONC businesses - lingerie, weddings, gyms, school uniforms -
        # and an imbalanced family is how a model learns the industry instead of the offence.
        counts[name + "_ok"] = emit(lawful, args.per_family, 0, f"policy_{name}_ok")

    rng.shuffle(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["text", "label", "category", "kind", "template"])
        w.writeheader()
        w.writerows(rows)

    print(f"{args.out}: {len(rows):,} rows")
    for k, v in counts.items():
        print(f"  {v:>7,}  {k}")
    print(f"  {len(set(r['template'] for r in rows)):,} distinct skeletons")
    short = [(k, v) for k, v in counts.items() if v < args.per_family]
    for k, v in short:
        print(f"\n  WARNING: {k} produced {v:,} of {args.per_family:,}. Add frames rather than "
              f"accepting near-duplicates.", file=sys.stderr)
    return 1 if short else 0


if __name__ == "__main__":
    raise SystemExit(main())
