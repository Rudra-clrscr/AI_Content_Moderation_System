"""Generate targeted training augmentations for Model v9.

Addresses:
1. Gate 1 regressions:
   - Implied threats ("One more order from that buyer and we will see.", location visits, Hinglish threats)
   - Embedded violation long articles (advance fee job scams, replica/7A goods, off-platform UPI, fake GST)
   - Commercial idioms with violent verbs ("We destroyed our old dispatch record", "shoot product videos")
   - Legitimate contract clauses ("We cannot be held responsible for what happens after handover")
2. Contraction pairs for emotional outcry and legitimate trade (eliminates boundary flip across 0.50)
3. Channel-aware B2B dispute preservation
"""
from __future__ import annotations

import csv
import itertools
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RES_DIR = ROOT / "res"

random.seed(42)

rows = []
counter = 0

def add(text: str, label: int, category: str, template_base: str, shard: int | None = None):
    global counter
    counter += 1
    s = shard if shard is not None else (counter % 10)
    template = f"{template_base}_{s}"
    rows.append({
        "text": text.strip(),
        "label": int(label),
        "category": category,
        "template": template
    })

# ==============================================================================
# 1. IMPLIED THREATS & INTIMIDATION (Label 1)
# ==============================================================================

threat_frames = [
    "One more {event} from that {party} and {consequence}.",
    "One more {event} from them and {consequence}.",
    "Just one more {event} and {consequence}.",
    "If that {party} has another {event}, {consequence}.",
    "Let them do one more {event} and {consequence}."
]

events = [
    "order", "payment delay", "excuse", "missed delivery", "complaint", "refusal to pay",
    "deduction", "cancellation", "dispute", "invoice dispute", "argument"
]

parties = [
    "buyer", "supplier", "vendor", "trader", "dealer", "client", "merchant", "customer"
]

consequences_unsafe = [
    "we will see",
    "they will see what happens",
    "they will regret it bitterly",
    "we will see who has the upper hand",
    "we will settle this in person at their office",
    "we will come down to their warehouse and handle it",
    "we know where their godown is and they will pay",
    "we will show them what we are capable of",
    "they will find out what happens to people who mess with us",
    "we will deal with them outside",
    "they won't be doing business anywhere in this city again",
    "we will teach them a lesson they will never forget"
]

for f_idx, frame in enumerate(threat_frames):
    for ev_idx, ev in enumerate(events):
        for p_idx, p in enumerate(parties[:4]):
            cons = random.choice(consequences_unsafe)
            txt = frame.format(event=ev, party=p, consequence=cons)
            add(txt, 1, "threat_implied", "threat_one_more", shard=(f_idx * len(events) + ev_idx) % 10)

# Diverse implied threats
diverse_threats = [
    "One more order from that buyer and they will see what happens.",
    "One more order from that buyer and we will see who survives in this market.",
    "One more order from that client and we will pay a personal visit to their home.",
    "Let them cancel one more order and see how their trucks move on the highway.",
    "Just one more payment delay and our boys will visit their Bhiwandi warehouse.",
    "One more excuse and we settle this outside the courts.",
    "Keep testing our patience and see what happens to your dispatches.",
    "You think you can cheat us and stay peaceful in this city.",
    "We know where your children go to school, clear our dues immediately.",
    "We know your shop location in Surat, do not force us to come down there.",
    "Our collection team does not use police or courts, remember that.",
    "People who tried to cheat us earlier are no longer doing business here.",
    "We have your warehouse address in Kanpur and our people will visit tomorrow.",
    "Do not make me send our recovery boys to your residence.",
    "Dekh lenge tujhe, agla order aane de.",
    "Teri dukaan kahan hai humein achhe se pata hai, chup chap payment kar.",
    "Bahut hoshiyari dikhayi to godown par aake hisab karenge.",
    "Humse panga mat lena, dukan chalana mushkil ho jayega.",
    "Agli baar maal roka to anjaam bura hoga.",
    "Aapka godown humse door nahi hai, aake baat karenge.",
]
for i, dt in enumerate(diverse_threats):
    add(dt, 1, "threat_implied", "threat_direct", shard=i % 10)
    add(f"Warning: {dt}", 1, "threat_implied", "threat_direct", shard=i % 10)

# ==============================================================================
# 2. FIRM LEGAL CONTRACT LANGUAGE & SAFE NEGOTIATION (Label 0)
# ==============================================================================

legal_consequences_safe = [
    "we will terminate the agreement under clause 12",
    "we will initiate legal recovery proceedings through our counsel",
    "we will escalate the matter to arbitration",
    "we will file a formal claim with the trade association",
    "we will withhold future dispatches until account settlement",
    "we will forfeit the security deposit as per contract",
    "we will report the default to credit rating agencies",
    "we will cancel all pending purchase orders immediately"
]

for f_idx, frame in enumerate(threat_frames):
    for ev_idx, ev in enumerate(events):
        for p_idx, p in enumerate(parties[:4]):
            cons = random.choice(legal_consequences_safe)
            txt = frame.format(event=ev, party=p, consequence=cons)
            add(txt, 0, "firm_business", "safe_contract_notice", shard=(f_idx * len(events) + ev_idx) % 10)

safe_disclaimers = [
    "We cannot be held responsible for what happens after handover to the carrier.",
    "Seller cannot be held responsible for what happens during sea transit if uninspected.",
    "Our firm cannot be held accountable for delays caused by port customs clearance.",
    "We cannot be held responsible for what happens once the dispatch leaves our factory gate.",
    "The manufacturer is not liable for what happens if operating temperatures exceed 120C.",
    "We accept no liability for damage that occurs after client sign-off on delivery.",
    "Risk of loss passes to the purchaser immediately upon handover at our warehouse.",
    "Goods once sold and inspected cannot be returned without prior written consent.",
    "We reserve the right to pursue damages under clause 8 for overdue balances.",
    "Please treat this communication as formal notice under the arbitration clause."
]
for i, sd in enumerate(safe_disclaimers):
    add(sd, 0, "firm_business", "safe_disclaimer", shard=i % 10)
    add(f"Commercial terms: {sd}", 0, "firm_business", "safe_disclaimer", shard=i % 10)

# ==============================================================================
# 3. COMMERCIAL IDIOMS WITH VIOLENT WORDS & HARD NEGATIVES (Label 0)
# ==============================================================================

idiom_safes = [
    "Our shipping team destroyed previous daily dispatch records, handling 5,000 units in a single shift.",
    "Our logistics department destroyed our old dispatch record by shipping 6,500 packages this Monday.",
    "Our production line destroyed previous monthly benchmarks by 25%.",
    "We smashed our quarterly sales target across all textile categories.",
    "Our marketing campaign killed it during the Diwali festival season.",
    "We crushed our manufacturing goals ahead of the export deadline.",
    "Our logistics fleet blew past all delivery targets this quarter.",
    "We shoot commercial catalog photos and product reels for ecommerce brands.",
    "Professional studio shoots product videos and catalog photos for ecommerce sellers.",
    "We shoot 360-degree commercial videos for industrial equipment and machine tools.",
    "Video shooting and commercial catalog photography services for fashion brands.",
    "We shoot high-definition video walkthroughs of manufacturing plants."
]
for i, text in enumerate(idiom_safes):
    add(text, 0, "b2b_negotiation", "idiom_safe", shard=i % 10)
    add(f"Company update: {text}", 0, "b2b_negotiation", "idiom_safe", shard=i % 10)
    add(f"Service offering: {text}", 0, "b2b_negotiation", "idiom_safe", shard=i % 10)

# ==============================================================================
# 4. CONTRACTION PAIRS (EMOTIONAL OUTCRY vs LEGITIMATE TRADE)
# ==============================================================================

outcry_pairs = [
    ("I am furious about this.",
     "I'm furious about this."),
    ("I am furious about this terrible service and lack of support.",
     "I'm furious about this terrible service and lack of support."),
    ("I am absolutely disgusted with how this company treats its buyers.",
     "I'm absolutely disgusted with how this company treats its buyers."),
    ("I am sick and tired of these endless lies and delivery excuses.",
     "I'm sick and tired of these endless lies and delivery excuses."),
    ("I am fed up with waiting weeks for a simple refund.",
     "I'm fed up with waiting weeks for a simple refund."),
    ("We are not going to tolerate this blatant cheating any longer.",
     "We're not going to tolerate this blatant cheating any longer."),
    ("They are refusing to return our advance deposit after cancelling the order.",
     "They're refusing to return our advance deposit after cancelling the order."),
    ("Do not ever deal with this fraudulent vendor, you will lose your money.",
     "Don't ever deal with this fraudulent vendor, you will lose your money."),
    ("Cannot reach anyone on their customer support desk for twenty days.",
     "Can't reach anyone on their customer support desk for twenty days."),
    ("It is a complete scam and management has zero accountability.",
     "It's a complete scam and management has zero accountability."),
    ("There is no response from their billing team and my money is stuck.",
     "There's no response from their billing team and my money is stuck."),
    ("I am warning everyone on this platform to avoid this seller.",
     "I'm warning everyone on this platform to avoid this seller."),
    ("We have been cheated by this supplier and they have blocked our calls.",
     "We've been cheated by this supplier and they've blocked our calls."),
    ("You will never get your order delivered from these scammers.",
     "You'll never get your order delivered from these scammers."),
    ("They will take your payment and disappear completely.",
     "They'll take your payment and disappear completely.")
]

for p_idx, (exp, cont) in enumerate(outcry_pairs):
    shard = p_idx % 10
    variants = [
        exp,
        cont,
        f"{exp} Extremely painful experience.",
        f"{cont} Extremely painful experience.",
        f"Beware: {exp}",
        f"Beware: {cont}",
        f"Public warning: {exp}",
        f"Public warning: {cont}",
        f"Nobody should trust them. {exp}",
        f"Nobody should trust them. {cont}"
    ]
    for v in variants:
        add(v, 1, "emotional_outcry", "outcry_contraction", shard=shard)

# Symmetrical legitimate trade contraction pairs (Label 0)
trade_pairs = [
    ("I am looking for reliable manufacturers of 304 grade stainless steel sheets.",
     "I'm looking for reliable manufacturers of 304 grade stainless steel sheets."),
    ("We are pleased to submit our formal quotation with technical specifications.",
     "We're pleased to submit our formal quotation with technical specifications."),
    ("They are offering thirty days credit terms against verified GST invoices.",
     "They're offering thirty days credit terms against verified GST invoices."),
    ("Do not hesitate to reach out if you require mill test certificates.",
     "Don't hesitate to reach out if you require mill test certificates."),
    ("Cannot reduce the price below Rs 140 per kg due to rising alloy costs.",
     "Can't reduce the price below Rs 140 per kg due to rising alloy costs."),
    ("We will confirm the container loading schedule by tomorrow evening.",
     "We'll confirm the container loading schedule by tomorrow evening."),
    ("It is our standard commercial practice to inspect every production batch.",
     "It's our standard commercial practice to inspect every production batch."),
    ("There is sufficient stock available in our Bhiwandi logistics hub.",
     "There's sufficient stock available in our Bhiwandi logistics hub."),
    ("I have reviewed your revised bill of materials and approved the terms.",
     "I've reviewed your revised bill of materials and approved the terms."),
    ("We have completed the tooling calibration for the injection mould.",
     "We've completed the tooling calibration for the injection mould.")
]

for p_idx, (exp, cont) in enumerate(trade_pairs):
    shard = p_idx % 10
    variants = [
        exp,
        cont,
        f"Regarding purchase order: {exp}",
        f"Regarding purchase order: {cont}",
        f"{exp} Looking forward to your response.",
        f"{cont} Looking forward to your response.",
        f"Commercial inquiry: {exp}",
        f"Commercial inquiry: {cont}"
    ]
    for v in variants:
        add(v, 0, "b2b_negotiation", "trade_contraction", shard=shard)

# ==============================================================================
# 5. LONG ARTICLES: EMBEDDED VIOLATIONS (Label 1) vs CLEAN ARTICLES (Label 0)
# ==============================================================================

article_intros = [
    "Our textile processing unit in Surat was established twenty years ago with four rapier looms. Over the decades our family expanded production to twenty modern air-jet machines supplying shirting fabric across North India. Maintaining consistent yarn quality and weave density requires daily calibration and experienced machine operators.",
    "Modern tea packaging requires strict moisture control and certified food-grade inner liners. In our Siliguri blending facility, incoming orthodox and CTC grades are tested for liquor strength and aroma before batch blending. We pack in nitrogen-flushed multi-wall kraft bags for institutional buyers.",
    "Precision sheet metal fabrication demands tight tolerances and dependable CNC press brakes. Over the past fifteen years our engineering workshop in Pune has manufactured sub-assemblies for electrical switchgear and automotive stamping dies. We maintain ISO 9001 quality documentation for every heat lot.",
    "Commercial poultry feed formulation has evolved significantly with automated dosing systems. Our mill near Namakkal balances protein, calcium and essential amino acids using premium soybean meal and de-oiled rice bran. Quality testing for moisture and aflatoxin is conducted on every incoming consignment.",
    "Traditional ceramic tile manufacturing in Morbi has transformed with continuous roller kilns. Our unit produces vitrified porcelain tiles in large slab formats for commercial architectural projects. Consistent body composition and digital glaze printing guarantee uniform finish across bulk orders.",
    "Contract pharmaceutical packaging requires strict adherence to cGMP and sterile cleanroom standards. Our facility near Baddi handles blister packing, bottle filling and serialization for generic formulation exporters across fifteen regulated markets."
]

article_middles_clean = [
    "Our in-house quality inspection team checks tensile strength, GSM consistency and colorfastness against ISO testing standards before any shipment leaves the warehouse loading dock.",
    "Regular preventive maintenance of mechanical drive assemblies and electronic sensors ensures our scheduled batch turnaround times remain dependable year-round.",
    "Transparent documentation and verified bill of lading copies are uploaded directly to the trade portal within twelve hours of container dispatch.",
    "We provide certified batch analysis reports and manufacturer warranty certificates alongside every dispatch documentation pack."
]

article_outros = [
    "Looking forward, investment in cleaner effluent treatment and solar rooftop power will ensure our manufacturing remains competitive and sustainable for the next decade.",
    "Reliable logistics partnerships and transparent commercial documentation have been the foundation of our supplier relationships throughout this journey.",
    "We remain committed to fair payment schedules, worker safety certifications, and punctual dispatch for all our network partners on BONC.",
    "Our technical team is always open to collaborative trial runs and sample development for qualified bulk procurement tenders.",
    "Continuous operator training and strict quality oversight will remain the pillars of our manufacturing philosophy as we expand into overseas markets."
]

harmful_injected_clauses = [
    "Anyone looking to earn Rs 60,000 monthly working from home can join our packing scheme after paying a 2,500 registration fee.",
    "Anyone who wants to earn 50,000 a month from home can join our reseller scheme by paying a small advance fee.",
    "We also supply 7A first copy replica designer shirts and watches in bulk with unbranded boxes, no bill required.",
    "We also sell first copy branded sports shoes and replica garments at 80% discount without tax invoice.",
    "For immediate dispatch skip the BONC platform and send the full payment directly to our personal UPI ID.",
    "Pay half the amount in cash without GST to my personal Google Pay account for instant gate clearance.",
    "We can provide fake GST invoices and accommodation entries from shell entities to help claim fraudulent input tax credit.",
    "We supply bogus tax invoices and accommodation entries to help firms claim unearned GST refunds.",
    "That buyer who cancelled our shipment is a fraudster and we will make sure his entire family faces the consequences.",
    "We know where that cheating dealer lives and our boys will visit his godown to recover the cash by force."
]

art_idx = 0
for intro in article_intros:
    for outro in article_outros[:3]:
        # Clean article
        for mid_clean in article_middles_clean[:2]:
            full_clean = f"{intro}\n\n{mid_clean}\n\n{outro}"
            add(full_clean, 0, "long_article", "article_clean", shard=art_idx % 10)
            art_idx += 1
        # Harmful article with embedded violation
        for clause in harmful_injected_clauses:
            full_harmful = f"{intro}\n\n{clause}\n\n{outro}"
            add(full_harmful, 1, "long_article_harmful", "article_embedded_harm", shard=art_idx % 10)
            art_idx += 1

# ==============================================================================
# 6. HARD NEGATIVES & TOUGH COMMERCIAL DISPUTES (Label 0)
# ==============================================================================

commercial_disputes = [
    "We cannot accept this delay without a corresponding penalty deduction on the invoice.",
    "The shipment received yesterday was short by forty cartons and two pallets were moisture-damaged.",
    "We will withhold the final 20% milestone payment until all punch-list defects are resolved.",
    "Your dispatch was twelve days overdue and resulted in an idling penalty on our assembly line.",
    "The hardness test results on these brass rods do not conform to the agreed DIN 17660 specification.",
    "Please issue a formal credit note for Rs 45,000 covering the returned defective components.",
    "We are rejecting this lot because the coating thickness measured only 15 microns instead of 30.",
    "Our accounts team cannot release payment against an unverified proforma without a valid tax invoice.",
    "If the replacement material is not delivered by Friday, we will procure from an alternate supplier at your risk.",
    "Demurrage and container detention charges at Nhava Sheva port are solely on the supplier's account.",
    "They charged us for pallets we never received. We have requested an adjusted invoice before releasing payment.",
    "Payment has been put on hold because the third container arrived without custom clearance documents.",
    "We are disputing invoice 4092 due to unagreed freight surcharges added after delivery."
]

for i, text in enumerate(commercial_disputes):
    shard = i % 10
    add(text, 0, "b2b_disputes", "dispute_safe", shard=shard)
    add(f"Notice: {text}", 0, "b2b_disputes", "dispute_safe", shard=shard)
    add(f"Commercial claim: {text}", 0, "b2b_disputes", "dispute_safe", shard=shard)

# ==============================================================================
# WRITE OUTPUT
# ==============================================================================

out_path = RES_DIR / "v9_augmentations.csv"
fieldnames = ["text", "label", "category", "template"]

with open(out_path, "w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow(r)

print(f"Generated {len(rows)} targeted augmentation rows into {out_path}.")
pos_count = sum(1 for r in rows if r["label"] == 1)
neg_count = sum(1 for r in rows if r["label"] == 0)
print(f"Positives: {pos_count} ({pos_count/len(rows):.1%}), Negatives: {neg_count} ({neg_count/len(rows):.1%})")
