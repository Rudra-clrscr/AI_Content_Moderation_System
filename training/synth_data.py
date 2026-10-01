"""Synthetic B2B moderation data for bonc-v4 (MODEL_IMPROVEMENT_GUIDE.md, section 3.1).

    # Generate fresh synthetic dataset:
    python synth_data.py --out D:/AI/bonc-data/synthetic_v4.csv --n 500000

    # Add 500k rows to existing dataset (enhanced grammar and business sense wordings):
    python synth_data.py --append --in-file res/synthetic_v4.csv --out res/synthetic_v4.csv --add-n 500000 --copy-to D:/AI/bonc-data/synthetic_v4.csv

Every row is (text, label, category, kind):

  label     0 = safe (publish), 1 = unsafe (reject: the author must rewrite)
  category  what the text is, e.g. listing, article, scam_income, counterfeit, b2b_negotiation, ...
  kind      whole | sentence | title | mixed (a harmful sentence inside a clean post)

Enhancements for v4+ performance:
  * Expanded B2B business sense wordings:
    - Volume slab pricing, tiered discounts, RFQs, BOQs, tender specifications.
    - Commercial negotiations: payment terms (Net 30/60, PDC, LC at sight, usance LC, DP/CAD,
      20% advance + 80% against LR/BL, prompt payment cash discounts).
    - Supply chain & documentation: GSTIN, HSN codes, e-way bills, Lorry Receipts (LR/bilti),
      Bill of Lading (BL), packing lists, IncoTerms (FOB, CIF, EXW, CFR, DAP), demurrage.
    - Quality assurance & testing: Mill Test Certificates (MTC), NABL reports, chemical analysis,
      tensile/yield strength, elongation, precision tolerances (+/- 0.02 mm), pressure testing.
    - B2B dispute resolution: RMA requests, debit notes for transit damage/shortage, credit notes,
      liquidated damages (LD) for delayed supply, quarantined lots, replacement under warranty.
    - Hard negative business idioms & aggressive trade vocabulary: phrases like "beat your current price",
      "slash landed cost", "crush your current price by X%", "kill the middleman", "killed our defect rate",
      "murders our cycle time", "bleeding margin", "ate the loss", "blew competitor out of the water",
      "cut-throat competition", "dead stock clearance", "killer offer" used in legitimate commercial context.
  * Comprehensive grammar variation engine:
    - Expanded non-native and Indian business English grammar: subject-verb agreement variations,
      dropped articles, irregular tense nuances, mass noun pluralization (equipments, machineries,
      furnitures, advices, feedbacks, stationeries), prepositional trade idioms ("discuss about",
      "revert back", "order for", "cope up with", "comply to", "prepone"), continuous aspect for
      stative verbs ("we are having in stock", "buyer is wanting discount"), discourse markers
      ("kindly do the needful", "at the earliest", "as per telecon").
    - Grammar noise applied across BOTH safe and unsafe text uniformly so non-standard grammar
      never correlates with violation risk.
  * Rule 1 Gate Guard:
    - Every safe candidate text is verified against the Layer 1 Gate (gate_patterns.yaml) so no
      safe row teaches the model to contradict the rules it sits behind.
  * Held-Out Test Protection:
    - Prevents any template collision with eval_*.csv datasets.
"""
from __future__ import annotations

import argparse
import csv
import random
import re
import sys
from pathlib import Path

# Paths & optional Gate import for Rule 1 enforcement
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "flask_api"))

try:
    from app.gate import Gate
    _GATE = Gate.from_yaml(ROOT / "flask_api" / "config" / "gate_patterns.yaml")
except Exception:
    _GATE = None

_EVAL_KEYS = set()
for f in HERE.glob("eval_*.csv"):
    try:
        with f.open(encoding="utf-8", errors="ignore") as fp:
            reader = csv.reader(fp)
            header = next(reader, None)
            for row in reader:
                if row:
                    _EVAL_KEYS.add(re.sub(r"\s+", " ", row[0]).strip().lower())
    except Exception:
        pass

R = random.Random()


def pick(xs):
    return R.choice(xs)


def maybe(p, text, alt=""):
    return text if R.random() < p else alt


# --------------------------------------------------------------------------- vocab

CITIES = ["Surat", "Pune", "Mumbai", "Delhi", "Ahmedabad", "Ludhiana", "Coimbatore", "Chennai", "Bengaluru",
          "Hyderabad", "Kolkata", "Jaipur", "Indore", "Rajkot", "Tiruppur", "Kanpur", "Noida", "Gurugram",
          "Nagpur", "Vadodara", "Nashik", "Moradabad", "Panipat", "Kochi", "Visakhapatnam", "Bhiwandi",
          "Aurangabad", "Faridabad", "Lucknow", "Guwahati", "Morbi", "Jamnagar", "Agra", "Firozabad",
          "Peenya", "Pimpri", "Chakan", "Sriperumbudur", "Manesar", "Baddi", "Ankleshwar", "Vapi",
          "Jamshedpur", "Belagavi", "Hosur", "Rudrapur", "Alwar", "Rourkela", "Durgapur", "Salem"]

STATES = ["Gujarat", "Maharashtra", "Tamil Nadu", "Punjab", "Karnataka", "Rajasthan", "Uttar Pradesh",
          "West Bengal", "Telangana", "Kerala", "Haryana", "Madhya Pradesh", "Uttarakhand", "Jharkhand"]

FIRST = ["Priya", "Rahul", "Meena", "Arjun", "Sunita", "Vikram", "Anita", "Rohit", "Kavya", "Suresh", "Neha",
         "Amit", "Pooja", "Rajesh", "Deepa", "Karan", "Lakshmi", "Imran", "Farah", "Harpreet", "Joseph",
         "Ayesha", "Sanjay", "Divya", "Manoj", "Ritu", "Gopal", "Sneha", "Arif", "Nikhil", "Ashok",
         "Girish", "Manish", "Sunil", "Vivek", "Kishore", "Tarun", "Vijay", "Anand", "Rakesh"]

LAST = ["Sharma", "Patel", "Iyer", "Singh", "Reddy", "Gupta", "Mehta", "Khan", "Nair", "Joshi", "Verma",
        "Das", "Agarwal", "Kulkarni", "Bose", "Menon", "Chauhan", "Shah", "Rao", "D'Souza", "Pillai",
        "Bhat", "Chopra", "Singhal", "Trivedi", "Banerjee", "Bhardwaj", "Deshmukh", "Nambiar"]

CO_A = ["Shree", "Sai", "Om", "Balaji", "Ganesh", "Krishna", "Global", "National", "Royal", "Prime", "Apex",
        "Sunrise", "Bharat", "Indo", "Star", "Classic", "Unique", "Perfect", "Supreme", "Metro", "Galaxy",
        "Zenith", "Titan", "Vanguard", "Precision", "Delta", "Micro", "Standard", "Everest", "Universal"]

CO_B = ["Industries", "Enterprises", "Exports", "Traders", "Polymers", "Textiles", "Engineering", "Agro Foods",
        "Pharma", "Solutions", "Impex", "Chemicals", "Steel Works", "Packaging", "Electricals", "Logistics",
        "Castings", "Fasteners", "Components", "Inorganics", "Metals", "Power Systems", "Precision Tools"]


def company():
    return f"{pick(CO_A)} {pick(CO_A + ['Laxmi', 'Durga', 'Vishwa', 'Tech', 'Power'])} {pick(CO_B)}" if R.random() < .3 \
        else f"{pick(CO_A)} {pick(CO_B)}"


def person():
    return f"{pick(FIRST)} {pick(LAST)}" if R.random() < .6 else pick(["Mr.", "Ms.", "Mrs."]) + " " + pick(LAST)


# product -> (specs, units, certs)
PRODUCTS = {
    # Original Catalog
    "PVC pipes": (["ISI marked, 20 mm to 110 mm", "6 kg/cm2 and 10 kg/cm2 pressure ratings", "lead-free, UV stabilised"], "metres", ["ISI", "BIS"]),
    "copper wires and cables": (["1.5 sq mm to 16 sq mm", "FR-LSH insulation", "99.97% pure electrolytic copper"], "coils", ["ISI", "ISO 9001"]),
    "cotton yarn": (["Ne 20s to Ne 60s, combed and carded", "compact and ring spun", "for knitting and weaving"], "kg", ["OEKO-TEX", "GOTS"]),
    "basmati rice": (["1121 sella and steam", "extra-long grain, aged 12 months", "Pusa 1509 variety"], "tonnes", ["FSSAI", "APEDA"]),
    "turmeric powder": (["curcumin 3% min", "steam sterilised", "25 kg HDPE bags"], "kg", ["FSSAI", "Spices Board"]),
    "LED panel lights": (["12W to 48W", "6500K cool white and 3000K warm white", "2 year warranty"], "pieces", ["BIS", "CE"]),
    "solar panels": (["330W mono PERC", "540W bifacial modules", "25 year performance warranty"], "panels", ["IEC 61215", "BIS", "ALMM listed"]),
    "stainless steel valves": (["15 mm to 300 mm", "SS304 and SS316", "PN16 rated, flanged ends"], "pieces", ["ISO 9001", "API 6D"]),
    "ceramic floor tiles": (["600x600 mm, glossy and matt", "double charge vitrified", "anti-skid finish"], "boxes", ["ISO 13006"]),
    "alphonso mangoes": (["from Ratnagiri and Devgad", "packed in 5 kg boxes", "naturally ripened, carbide free"], "boxes", ["FSSAI", "GI tagged"]),
    "industrial chemicals": (["caustic soda flakes 98%", "hydrochloric acid 33%", "technical grade solvents"], "drums", ["ISO 14001", "REACH"]),
    "corrugated boxes": (["3 ply, 5 ply and 7 ply", "custom printed", "burst factor 18 to 22"], "pieces", ["FSC"]),
    "CNC machined parts": (["aluminium and brass", "tolerance up to 0.01 mm", "for automotive and aerospace clients"], "pieces", ["IATF 16949", "ISO 9001"]),
    "cotton bedsheets": (["king and queen sizes", "300 thread count", "reactive printed, shrink resistant"], "sets", ["OEKO-TEX"]),
    "ayurvedic herbal supplements": (["ashwagandha and triphala capsules", "GMP-certified plant", "60 capsules per bottle"], "bottles", ["AYUSH", "GMP"]),
    "submersible pumps": (["0.5 HP to 10 HP", "stainless steel body", "for borewells up to 400 ft"], "units", ["BEE 5 star", "ISI"]),
    "office furniture": (["ergonomic chairs and modular desks", "powder coated steel frames", "5 year warranty"], "units", ["BIFMA"]),
    "surgical gloves": (["latex and nitrile", "powder free, sterile", "sizes 6 to 8.5"], "boxes", ["CE", "ISO 13485", "CDSCO"]),
    "tea": (["CTC and orthodox grades", "from Assam and Darjeeling estates", "packed in 1 kg and 25 kg bags"], "kg", ["FSSAI", "Tea Board"]),
    "jute bags": (["laminated and unlaminated", "custom logo printing", "eco-friendly and reusable"], "pieces", ["ISO 9001"]),
    "granite slabs": (["polished and flamed finish", "18 mm and 20 mm thickness", "black galaxy and tan brown"], "sq ft", []),
    "diesel generators": (["15 kVA to 500 kVA", "silent canopy", "CPCB IV+ compliant"], "units", ["CPCB", "ISO 8528"]),
    "brass hardware": (["door handles, hinges and knobs", "antique and chrome finish", "made in Moradabad"], "pieces", []),
    "HDPE granules": (["blow and injection grades", "MFI 0.3 to 20", "virgin and recycled"], "tonnes", ["ISO 9001"]),
    "handmade leather wallets and belts": (["full grain leather", "hand stitched", "custom embossing available"], "pieces", []),
    "industrial vacuum cleaners": (["2000W motor, wet and dry", "HEPA filtration", "80 litre tank"], "units", ["CE"]),
    "pipe fittings": (["elbows, tees, flanges and nipples", "MS and GI", "threaded and socket weld"], "pieces", ["ISI"]),
    "Maine Coon kittens": (["vaccinated and dewormed", "KCI registered parents", "12 weeks old"], "kittens", []),
    "steel TMT bars": (["Fe 500D and Fe 550D", "8 mm to 32 mm", "earthquake resistant"], "tonnes", ["BIS"]),
    "organic fertiliser": (["vermicompost and neem cake", "50 kg bags", "certified organic inputs"], "bags", ["NPOP"]),
    "readymade garments": (["men's formal shirts", "sizes S to XXL", "100% cotton, slim and regular fit"], "pieces", []),
    "spray guns and glue guns": (["HVLP spray guns", "hot melt glue guns 40W to 100W", "for workshops and crafts"], "pieces", ["CE"]),
    "bath bombs and soaps": (["handmade, essential oil based", "paraben free", "gift boxes of 6"], "boxes", []),

    # Expanded B2B Industrial Catalog
    "seamless carbon steel pipes": (["ASTM A106 Grade B, Sch 40 and Sch 80", "1/2 inch to 24 inch OD", "bevelled ends, black varnished"], "metres", ["IBR", "ISO 9001", "API 5L"]),
    "stainless steel fasteners": (["SS304 and SS316 hex bolts, nuts and washers", "M6 to M36 sizes", "DIN 933 and DIN 934 standards"], "kg", ["ISO 3506", "CE"]),
    "distribution transformers": (["100 kVA to 2500 kVA oil cooled", "11kV/433V step down", "BEE 5-star energy efficient, copper wound"], "units", ["BIS", "IS 1180", "CPRI tested"]),
    "solar grid-tie inverters": (["5 kW to 50 kW three phase", "MPPT efficiency 99.8%", "IP65 outdoor enclosure with Wi-Fi monitoring"], "units", ["IEC 62109", "MNRE approved", "CE"]),
    "HDPE pressure pipes": (["PE 100 grade, PN 6 to PN 16", "20 mm to 630 mm OD", "for potable water supply and sewerage"], "metres", ["IS 4984", "ISO 4427"]),
    "heavy corrugated cartons": (["5-ply and 7-ply heavy duty", "kraft paper 150 to 300 GSM", "bursting strength 18 to 26 kg/cm2"], "pieces", ["FSC certified", "ISO 9001"]),
    "hydraulic gear pumps": (["group 1, 2 and 3", "displacement 4 cc to 60 cc/rev", "operating pressure up to 250 bar"], "units", ["CE", "ISO 9001"]),
    "industrial safety footwear": (["steel toe cap withstanding 200 Joules", "oil and acid resistant PU sole", "antistatic, breathable leather upper"], "pairs", ["IS 15298", "EN ISO 20345", "CE"]),
    "CRCA steel sheets": (["0.5 mm to 3.0 mm thickness", "IS 513 Grade D/DD/EDD", "oil coated, slitted coils and cut sheets"], "tonnes", ["BIS", "ISO 9001"]),
    "flanged ball valves": (["Class 150 and 300", "WCB body, SS316 ball and stem", "fire-safe design, blow-out proof stem"], "pieces", ["API 607", "ISO 10497", "IBR"]),
    "pallet stretch wrap film": (["cast extrusion, 23 and 29 microns", "manual and machine grade rolls", "up to 300% stretchability"], "rolls", ["ISO 9001"]),
    "industrial safety helmets": (["high-density polyethylene (HDPE)", "6-point textile suspension", "chin strap and ratchet adjustment"], "pieces", ["IS 2925", "CE EN 397"]),
    "cast iron sluice valves": (["PN 1.0 and PN 1.6 rating", "50 mm to 600 mm NB", "non-rising stem, bronze trim"], "pieces", ["IS 14846", "ISO 9001"]),
    "submersible copper winding wire": (["poly wrapped and dual coated", "sizes 0.6 mm to 2.2 mm", "high dielectric breakdown voltage"], "kg", ["ISI", "ISO 9001"]),
    "aluminium extrusions": (["6063-T6 alloy", "anodised and powder coated finish", "for curtain walls, windows and partitions"], "tonnes", ["Qualicoat", "ISO 9001"]),
    "PP woven bags": (["unlaminated and BOPP laminated", "50 kg capacity for cement, grain, and sugar", "UV stabilised with gusseting"], "bags", ["IS 11652", "FSSAI approved"]),
    "pharmaceutical raw materials": (["paracetamol IP/BP/USP", "metformin hydrochloride IP", "GMP manufactured with active COA"], "kg", ["WHO-GMP", "CDSCO"]),
    "fire fighting centrifugal pumps": (["diesel engine driven and electrical motor driven", "discharge 500 to 2500 GPM", "head 60 to 120 metres"], "sets", ["UL listed", "FM approved", "TAC approved"]),
    "abrasive grinding wheels": (["depressed centre and cutting-off wheels", "reinforced resinoid bond", "for MS, SS, and cast iron"], "boxes", ["EN 12413", "ISO 9001"]),
    "industrial induction motors": (["IE3 and IE4 premium efficiency", "0.75 kW to 315 kW, foot and flange mounted", "IP55 totally enclosed fan cooled (TEFC)"], "units", ["IS 12615", "CE", "BIS"]),
    "precision CNC brass connectors": (["hex bushes, nipples, and pipe connectors", "machined on Swiss-type CNC lathes", "tolerance within +/- 10 microns"], "pieces", ["ISO 9001", "RoHS compliant"]),
    "industrial conveyor belts": (["EP canvas carcass, rubber covers Grade M24 and N17", "width 400 mm to 1600 mm", "high abrasion and tear resistance"], "metres", ["IS 1891", "ISO 9001"]),
    "laboratory chemicals": (["analytical reagent (AR) grade acetone and methanol", "99.9% purity by GC", "packaged in amber glass bottles and HDPE carboys"], "litres", ["ISO 17025", "NABL certified"]),
    "lead acid traction batteries": (["for electric forklifts and reach trucks", "24V, 48V, and 80V configurations", "tubular positive plate construction, 1500 cycles"], "units", ["CE", "ISO 14001"]),
    "rubber hydraulic hoses": (["wire braided R1AT and R2AT", "1/4 inch to 2 inch ID", "operating pressure up to 400 bar, oil resistant"], "metres", ["DIN EN 853", "SAE 100 R2"]),
    "rotary screw air compressors": (["7.5 kW to 75 kW stationary units", "air cooled, 7 to 13 bar working pressure", "integrated dryer and filtration system"], "units", ["CE", "ISO 9001"]),
    "pallet racks and shelving": (["heavy duty slotted angle and beam racks", "loading capacity 1000 kg to 3000 kg per level", "powder coated anti-corrosion finish"], "bays", ["EN 15512", "ISO 9001"]),
}

CERT_EXTRA = ["ISO 9001:2015 certified", "GST registered", "MSME registered", "export house recognised by DGFT",
              "ZED certified", "NABL-accredited in-house lab", "RoHS compliant", "CE certified", "BIS certified",
              "ISO 14001:2015 compliant", "IATF 16949 compliant"]

MOQ = ["Minimum order {n} {u}.", "MOQ {n} {u}.", "Bulk orders welcome.", "Samples available on request.",
       "Test certificates provided with every order.", "Pan-India delivery in 5 to 7 days.",
       "Export quality packing.", "Free delivery within {city} for orders above {n} {u}.",
       "Custom sizes on request.", "Dealer enquiries welcome.", "Price on request, GST extra.",
       "Ready stock available.", "Third-party inspection accepted.", "Credit terms available for verified buyers.",
       "Discounts available on full truckload (FTL) orders.", "Ex-stock subject to prior sale."]

INCOTERMS = ["Ex-Works (EXW)", "FOB Mundra", "FOB Nhava Sheva", "FOB Chennai", "CIF Jebel Ali", "CIF Hamburg",
             "CIF Singapore", "CFR Colombo", "DAP destination godown", "FOR site"]
INSP_AGENCIES = ["SGS", "TUV Rheinland", "Bureau Veritas", "Intertek", "NABL accredited lab", "DNV", "Lloyd's Register"]
TRANSPORTERS = ["V-Trans", "TCI Express", "Gati", "Safexpress", "Blue Dart", "Delhivery", "local transport"]


def listing():
    prod, (specs, unit, certs) = pick(list(PRODUCTS.items()))
    s1, s2 = R.sample(specs, 2)
    parts = [f"{pick(['', 'Premium ', 'High quality ', 'Wholesale ', 'Industrial grade ', 'Export quality '])}{prod}, {s1}."]
    if R.random() < .7:
        parts.append(f"{_sentence_case(s2)}.")
    if certs and R.random() < .6:
        parts.append(f"{pick(certs)} certified.")
    for _ in range(R.randint(1, 3)):
        parts.append(pick(MOQ).format(n=pick([10, 50, 100, 200, 500, 1000]), u=unit, city=pick(CITIES)))
    return " ".join(dict.fromkeys(parts))


PROFILE_T = [
    "{co} is a {kind} of {prod} based in {city}, {state}. {since} {cert}",
    "Family-run {kind2} in {city} producing {prod} since {year}. {cert}",
    "We are a {cert_l} {kind} of {prod}, supplying clients across {region} since {year}.",
    "{co}: {prod} {kind}s with {n} years of experience. Our plant in {city} has a capacity of {cap} {unit} per month.",
    "Founded in {year} by {person}, {co} now employs {emp} people and exports {prod} to {n2} countries.",
    "Logistics company offering warehousing, cold chain and last-mile delivery services in {city} and {city2}.",
    "Third-generation {kind2} from {city}. We combine traditional craftsmanship with modern quality control.",
    "{co} is a women-led {kind} of {prod}. We work with {n} artisan families in {state}.",
]


def profile():
    prod, (_, unit, certs) = pick(list(PRODUCTS.items()))
    return pick(PROFILE_T).format(
        co=company(), kind=pick(["manufacturer", "supplier", "exporter", "trader", "distributor", "wholesaler"]),
        kind2=pick(["business", "mill", "factory", "workshop", "unit"]), prod=prod, city=pick(CITIES),
        city2=pick(CITIES), state=pick(STATES), since=maybe(.6, f"In business since {R.randint(1965, 2022)}."),
        cert=maybe(.7, f"{pick(CERT_EXTRA)}."), cert_l=pick(["ISO 9001 certified", "GST registered", "government-recognised"]),
        year=R.randint(1965, 2022), region=pick(["India", "Europe and Asia", "the Middle East", "South India", "North India"]),
        n=R.randint(5, 60), cap=pick([50, 200, 1000, 5000]), unit=unit, person=person(), emp=R.randint(8, 900),
        n2=R.randint(3, 40)).replace("  ", " ").strip()


# Article sentence pools by topic
ARTICLE_TOPICS = {
    "export": (["How we started exporting to Europe", "Five lessons from our first export order", "A beginner's guide to export documentation",
                "Exporting {prod}: what buyers ask for", "Why we got our IEC code early"],
               ["Our first export order came from a buyer in {country} in {year}.",
                "Getting the IEC code and RCMC took about three weeks.",
                "Buyers in {country} ask for test reports before they place a trial order.",
                "We learned to quote FOB and CIF prices clearly to avoid confusion later.",
                "Letters of credit protect both sides, but they need careful checking.",
                "Our freight forwarder helped us understand container loading and HS codes.",
                "The first shipment was delayed at customs because one invoice had a typo.",
                "Since then we check every document twice before the container is sealed.",
                "Today exports make up {pct}% of our revenue.",
                "Trade fairs in {country} brought us most of our long-term buyers.",
                "Small exporters should start with one market and learn it well.",
                "Export incentives under RoDTEP helped our margins in the first year."]),
    "quality": (["Why ISO certification matters for small factories", "How we cut our rejection rate", "Quality control on the shop floor",
                 "What a good inspection report looks like", "Our journey to zero defects"],
                ["Our rejection rate was {pct}% two years ago.",
                 "We started recording every defect with a photo and a cause.",
                 "Most problems came from two machines that needed recalibration.",
                 "The ISO 9001 audit forced us to write down processes we had only in our heads.",
                 "Operators now check the first piece of every batch against the drawing.",
                 "Customers can see the inspection report before dispatch.",
                 "We found the test results disturbing at first, but they showed us where to improve.",
                 "Training the team took longer than buying the new gauges.",
                 "Our returns fell by half within a year.",
                 "Quality is cheaper than rework, even for a small unit like ours.",
                 "Third-party inspection agencies check every export lot."]),
    "logistics": (["Case study: cutting logistics costs", "How we reduced delivery times", "Choosing a warehouse partner",
                   "What the new e-way bill rules mean for us", "Shipping fragile goods safely"],
                  ["We moved to consolidated shipments in {year}.",
                   "Delivery times improved from twelve days to eight.",
                   "Damaged goods were our biggest complaint, so we redesigned the packaging.",
                   "Corner protectors and five-ply boxes cost a little more but save a lot.",
                   "We now track every consignment and share the link with the buyer.",
                   "Our transporter in {city} handles the last mile for us.",
                   "Monsoon months need extra planning because roads flood.",
                   "Part-truckload rates dropped once we committed to weekly volumes.",
                   "The e-way bill has to be generated before the truck leaves the gate.",
                   "Warehousing near the port cut our demurrage charges."]),
    "growth": (["How we scaled a family textile mill", "From four looms to sixty", "Growing a small business without debt",
                "What we learned in our first ten years", "About our {prod} business"],
               ["We started with four looms in {year}.",
                "Today we run sixty and employ {n} people.",
                "My father handled sales while my mother managed accounts.",
                "The first big order came from a buyer we met at a trade fair in {city}.",
                "We reinvested profits instead of taking loans.",
                "Hiring a professional accountant was the best decision we made.",
                "Our children now run the export division and their team is growing.",
                "Digital catalogues brought in buyers from states we had never sold to.",
                "Not every year was good; the pandemic cut our orders by half.",
                "We kept every worker on the payroll through the lockdown.",
                "Growth came from repeat customers more than from new ones."]),
    "compliance": (["GST basics for new sellers", "Understanding e-invoicing", "How to read a test certificate",
                    "Labelling rules for packaged food", "BIS certification explained"],
                   ["Every B2B sale above the threshold needs an e-invoice.",
                    "Input tax credit can only be claimed if the supplier files returns on time.",
                    "Packaged food must show the FSSAI licence number on the label.",
                    "BIS certification is mandatory for many electrical products.",
                    "Keep copies of every invoice for at least six years.",
                    "Your chartered accountant can help you set up the e-invoicing portal.",
                    "Late filing attracts interest and penalties, so set reminders.",
                    "The HSN code on the invoice decides the tax rate.",
                    "A test certificate should list the standard, the batch number and the lab's accreditation."]),
    "safety": (["Handling hazardous chemicals safely", "Fire safety in small factories", "Worker safety is good business",
                "What to do when a machine fails", "Safety audits: a checklist"],
               ["They handle toxic chemicals safely and follow all regulations.",
                "Every worker gets gloves, goggles and a respirator.",
                "Acids are stored away from bases and flammable solvents.",
                "We run a fire drill every quarter and log the results.",
                "An explosion-proof motor costs more but is required in solvent areas.",
                "Material safety data sheets are posted next to every storage tank.",
                "The killer mistake in most factories is blocked fire exits.",
                "A near miss last year made us redesign the loading bay.",
                "Accident rates fell after we started daily five-minute safety talks.",
                "First-aid kits are checked every Monday."]),
    "market": (["Monsoon demand for pumps", "Why steel prices are rising", "Cotton prices this season",
                "Festive season sales outlook", "The market for solar in rural India"],
               ["Demand for submersible pumps rises every year before the monsoon.",
                "Steel prices went up {pct}% this quarter because of higher iron ore costs.",
                "Cotton arrivals were lower this season in Gujarat and Maharashtra.",
                "Dealers expect strong festive demand in October and November.",
                "Rooftop solar is growing fast thanks to state subsidies.",
                "Small buyers are shifting from cash to UPI and bank transfers.",
                "Prices may soften once the new crop arrives.",
                "Exporters are watching the rupee closely.",
                "Several mills have announced capacity expansions.",
                "Buyers are asking for shorter lead times and smaller lots."]),
    "fraud_awareness": (["How to spot fake buyers", "Protect your business from online fraud", "Beware of fake products",
                         "Five scams every seller should know", "Safe payments on BONC"],
                        ["BONC will never ask for your OTP or password.",
                         "Be careful if a buyer asks you to pay a fee before they place an order.",
                         "Genuine buyers don't mind paying through the platform.",
                         "Always verify a new buyer's GST number before shipping on credit.",
                         "If an offer promises guaranteed returns, it is probably a scam.",
                         "We add a hologram to every box so customers can tell our products from counterfeits.",
                         "Buy only from authorised dealers to avoid fake products.",
                         "Report suspicious messages to the support team.",
                         "Never share your bank login with anyone who calls claiming to be from the bank.",
                         "Scammers often create urgency, so take your time and check.",
                         "Counterfeit goods hurt honest manufacturers and their workers."]),
}
COUNTRIES = ["Germany", "the UAE", "the UK", "the US", "Japan", "Kenya", "Australia", "Bangladesh", "Italy", "Saudi Arabia"]


def fill(s):
    return s.format(prod=pick(list(PRODUCTS)), country=pick(COUNTRIES), year=R.randint(1985, 2023),
                    pct=R.randint(3, 40), city=pick(CITIES), n=R.randint(20, 900))


def article_parts(topic=None):
    topic = topic or pick(list(ARTICLE_TOPICS))
    titles, sents = ARTICLE_TOPICS[topic]
    body = [fill(s) for s in R.sample(sents, k=min(len(sents), R.randint(2, 7)))]
    return fill(pick(titles)), body, topic


def article():
    title_, body, topic = article_parts()
    if R.random() < .3:
        _, more, _ = article_parts()
        body += more[:3]
    sep = pick([" ", " ", "\n"])
    return f"{title_}\n\n{sep.join(body)}", topic


REVIEW_POS = ["Great supplier. They delivered {n} units on time and the quality was excellent.",
              "Ordered {prod} last month. They arrived damaged, but their support team replaced them quickly.",
              "Their prices are higher than competitors, but the quality justifies it. Recommended.",
              "{name} from their sales team was very helpful and patient with our questions.",
              "Fast delivery, good packaging and honest billing. Will order again.",
              "We have worked with {co} for {y} years. They have never missed a delivery date.",
              "Their prices are shamelessly low this week, grab the deal.",
              "Excellent packaging and prompt delivery on every order.",
              "Good quality {prod}, exactly as described. The GST invoice came with the goods.",
              "She answered every question within an hour and shared the test report without us asking."]

REVIEW_NEG = ["The packaging was poor. They need to improve how they pack fragile items.",
              "Delivery was late by two weeks. He did not answer calls and we lost a client because of it.",
              "Quality was not as described. They sent the wrong grade of steel and refused a refund.",
              "Their customer support was dismissive and unhelpful when we raised an issue.",
              "The {prod} we received were a lower grade than the sample. We have raised a complaint.",
              "Two of the {n} pieces were cracked. They replaced them, but only after three reminders.",
              "Communication was slow and the invoice had errors. Expected better from a certified supplier.",
              "His explanation for the shipment delay did not add up, so we have paused new orders.",
              "Her team's handling of our return request was disorganized and slow.",
              "I would not recommend them for urgent orders; they missed two deadlines in a row.",
              "The colour of the fabric did not match the approved sample. We are waiting for a credit note.",
              "We suspect the certificates provided for this product may not be genuine and have asked for the originals.",
              "They overcharged freight compared to the quote. We asked for a revised invoice."]


def review():
    t = pick(REVIEW_POS + REVIEW_NEG)
    return t.format(n=pick([50, 100, 500, 1200]), prod=pick(list(PRODUCTS)), name=pick(FIRST), co=company(),
                    y=R.randint(2, 15))


ANNOUNCE = ["We are attending the {fair} next week. Visit our stall {stall} to see our new product range.",
            "Our office will be closed on {day} for {festival}. Orders placed now will ship on {day2}.",
            "We have opened a new branch in {city}. Local buyers can now pick up orders directly.",
            "Happy to share that our plant is now {cert}.",
            "Prices of {prod} will be revised from the first of next month because of higher raw material costs.",
            "Our new website is live. You can download catalogues and price lists there.",
            "Get {pct}% off on all {prod} this week only, hurry before stock runs out!",
            "Monsoon offer: free delivery on orders above {n} units until {day}.",
            "Thank you to all our buyers for {y} years of trust.",
            "We are launching a new range of {prod} at the {fair}.",
            "Our sales manager {name} is on leave this week. She will be back on Monday to handle your orders.",
            "Contact our founder directly. He handles all bulk enquiries personally.",
            "Ask for {name} in accounts. She can share GST invoices and bank details for payment.",
            "We met Mr. {last} at the expo. He showed us their new range of {prod}.",
            "Limited stock of the festive collection left. Order through BONC before Friday.",
            "Our {prod} catalogue is available on WhatsApp too; message us for the PDF."]

FAIRS = ["Delhi Trade Fair", "IHGF Delhi Fair", "India International Trade Fair", "Gulfood Dubai", "Heimtextil Frankfurt",
         "Auto Expo", "Elecrama", "Plastindia", "IMTEX Bengaluru", "India Textile Expo", "Engimach"]
FESTIVALS = ["Diwali", "Holi", "Eid", "Pongal", "Onam", "Christmas", "Independence Day", "Ganesh Chaturthi"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "the 15th", "the 2nd", "the 30th"]


def announcement():
    return pick(ANNOUNCE).format(fair=pick(FAIRS), stall=f"{pick('ABCDEFH')}-{R.randint(1, 240)}", day=pick(DAYS),
                                 day2=pick(DAYS), festival=pick(FESTIVALS), city=pick(CITIES), cert=pick(CERT_EXTRA),
                                 prod=pick(list(PRODUCTS)), pct=pick([10, 15, 20, 25, 30, 40, 50, 70]), n=pick([10, 50, 100]),
                                 y=R.randint(5, 50), name=pick(FIRST), last=pick(LAST))


JOBS = ["Hiring sales representatives in {city}, commission plus fixed salary. Apply via our careers page.",
        "We are hiring a quality engineer with {y} years of experience in {prod}. Send your CV to hr@{dom}.",
        "Vacancy: accounts assistant, B.Com with Tally knowledge, salary {s} per month, {city}.",
        "Machine operators needed for our {city} plant. ITI preferred. Free accommodation and PF provided.",
        "Walk-in interview for export documentation executive on {day} at our {city} office.",
        "Internship for MBA students in marketing, stipend {s}, three months.",
        "We are looking for a warehouse supervisor. Shift allowance and ESI benefits included."]


def job():
    return pick(JOBS).format(city=pick(CITIES), y=R.randint(2, 10), prod=pick(list(PRODUCTS)), dom="example.com",
                             s=pick(["Rs 18,000", "Rs 25,000", "Rs 32,000", "Rs 12,000"]), day=pick(DAYS))


ENQ = ["Please send a quotation for {n} {u} of {prod} delivered to {city}.",
       "What is the current price and availability for {prod}?",
       "We are looking for a long-term supplier of {prod}. Please share your catalogue and MOQ.",
       "Can you share the test certificate and HSN code for your {prod}?",
       "Requesting a callback regarding {prod} availability in {city}.",
       "Please advise on payment terms for a {n}-unit order.",
       "Do you offer private labelling for {prod}?",
       "We need {prod} for a government project. Can you provide BIS certification and past supply records?",
       "Please connect us with businesses similar to {co} for sourcing {prod}."]


def enquiry():
    prod, (_, u, _) = pick(list(PRODUCTS.items()))
    return pick(ENQ).format(n=pick([100, 250, 500, 2000]), u=u, prod=prod, city=pick(CITIES), co=company())


# Legitimate text with scary words (hard negatives).
HARD_NEG = [
    "They handle toxic chemicals safely and follow all regulations.", "He found the test results disturbing and recalled the batch.",
    "Download our cheat sheet for solar installers.", "Pipe nipples and flanges in MS and GI, all sizes.",
    "Office-cum-warehouse available on rent in {city}.", "Maine Coon kittens, vaccinated, from registered breeders.",
    "Industrial vacuum cleaner, 2000W motor sucks up dust and water.", "Our killer offer: 20% off on all pumps this Diwali.",
    "Photo shoot services for product catalogues.", "Bath bombs and handmade soaps in gift boxes.",
    "Hot glue guns and spray guns for workshops.", "Crack-resistant tiles for heavy traffic areas.",
    "We execute orders within 48 hours.", "Pharmaceutical drugs are supplied only to licensed chemists and hospitals.",
    "Our pest control team kills termites and cockroaches without harming pets.", "Heavy-duty shredder destroys old documents securely.",
    "The attack on our website last year taught us to invest in security.", "We hate delays as much as you do, so we ship the same day.",
    "Stop wasting money on cheap valves that leak.", "Butt welding fittings in carbon steel.",
    "Explosion-proof lights for refineries and paint shops.", "Blasting media: steel shots and grit for surface preparation.",
    "No fake products here: every item comes with a GST invoice and warranty card.", "Original spare parts only, no duplicates or first copies.",
    "Terms: 30% advance with the purchase order, balance against delivery.", "Payment by bank transfer to our company account; GST invoice provided.",
    "We accept payment through BONC's secure checkout.", "A refundable security deposit is charged for rented equipment, as per the written agreement.",
    "Distributors wanted in {state}. No fees of any kind; we supply stock on credit to verified partners.",
    "Earn reward points on every repeat order.", "Our dealers earned 18% higher margins last year thanks to faster delivery.",
    "Registration for the trade fair is free for BONC members.", "Please complete GST registration before listing products.",
    "The Earn and Learn programme pays ITI apprentices a monthly stipend, as per government rules.",
    "Beware of fake websites using our name. Our only website is the one listed on our BONC profile.",
    "Never share your OTP with anyone, including our staff.", "BONC will never ask for your password.",
    "Dead stock clearance: last season's fabric at cost price.", "Killing it this quarter: record exports for our team!",
    "Loaded dice and poker chips for casino supply stores.", "Surgical blades and scalpels for hospitals, sterile packed.",
    "Knives and cleavers for commercial kitchens.", "Air guns for tyre inflation and pneumatic tools.",
    "Fertiliser and pesticide sprayers for farms.", "Adult diapers and incontinence products for hospitals.",
    "Breast pumps and maternity products, hospital grade.", "Cotton buds and cotton balls for pharmacies.",
    "Weed removal tools and garden hoes.", "Cock valves and ball valves in brass.",
    "Rooster and hen chicks from our poultry farm.", "Wood screws and self-drilling screws, zinc plated.",
    "The competition in this market is fierce, so we focus on service.", "Our competitor has lower prices, but we offer a longer warranty.",
    "This product is not a replica; it is manufactured under licence from the brand owner.",
    "We sell refurbished laptops with a 6-month warranty and a clear grade label.",
    "Whatsapp us for the catalogue and price list.", "Cash on delivery is available for orders under Rs 10,000.",
    "Limited slots available for our free training workshop on export documentation.",
    "Work from home roles available for customer support, salary paid monthly by bank transfer, no fees.",
    "Guaranteed delivery in 7 days or we refund the freight.", "Double-walled steel tanks for water storage.",
    "Money-back guarantee if the fabric shrinks more than 2%.", "Lottery tickets? No. We sell ticket printers for events.",
    "The shooting range needs acoustic panels; we supplied 400 of them.", "Bulletproof vests are supplied only to authorised government agencies with an end-user certificate.",
]


def hard_negative():
    r = R.random()
    if r < .45:
        return pick(HARD_NEG).format(city=pick(CITIES), state=pick(STATES))
    if r < .6:
        return payment_terms()
    if r < .72:
        return legit_promo()
    if r < .87:
        return negation()
    return identity_neutral()


def payment_terms():
    pct = pick([10, 20, 25, 30, 40, 50])
    when = pick(["with the purchase order", "at order confirmation", "on booking", "up front", "in advance"])
    rest = pick(["before dispatch", "against delivery", "after inspection at our factory", "within 30 days of delivery",
                 "on receipt of goods", "against the GST invoice", "after installation"])
    how = pick(["", " by RTGS to our company account", " by bank transfer to our current account", " by cheque in the company's name",
                " through BONC checkout"])
    return pick([f"Payment terms: {pct}% {when}, balance {rest}{how}.",
                 f"We take {pct}% {when} and the remaining amount {rest}{how}.",
                 f"Terms: {pct} percent advance, {100 - pct} percent {rest}.",
                 f"A booking amount of Rs {pick(['5,000', '10,000', '25,000'])} confirms your order; the balance is paid {rest}.",
                 f"Regular buyers get {pick([30, 45, 60, 90])} days credit; new buyers pay part of the amount {when}.",
                 f"Letter of credit at sight is accepted for export orders{how}.",
                 f"Payment by {pick(['RTGS', 'NEFT', 'cheque', 'demand draft', 'LC'])} only{how}, invoice with every shipment."])


def legit_promo():
    return pick([f"Earn {pick(['reward points', 'cashback', 'loyalty points', 'an extra 2% discount'])} on "
                 f"{pick(['every order', 'orders above Rs 50,000', 'repeat purchases', 'early payments'])}.",
                 f"Register {pick(['for free', 'online', 'on our website', 'on our dealer portal'])} to "
                 f"{pick(['download the catalogue', 'get the price list', 'book a stall', 'claim your warranty', 'track your orders'])}.",
                 f"Sign up {pick(['for free', 'today'])} and get our {pick(['monthly newsletter', 'weekly price updates', 'festival offers'])}.",
                 f"Our dealers earn good margins because we deliver on time.",
                 f"Free samples for registered buyers, courier charges extra.",
                 f"Join our distributor network: no fees, just a GST number and a shop.",
                 f"Refer a retailer and earn a gift voucher when they place their first order."])


def negation():
    dont_choice = pick(["do not", "never", "don't"])
    return pick([f"We {dont_choice} sell {pick(['first copies', 'replicas', 'duplicate parts', 'used parts', 'copies of branded goods'])}; "
                 f"{pick(['every product is genuine', 'all goods are original and invoiced', 'everything comes with a warranty'])}.",
                 f"There is no {pick(['registration', 'joining', 'processing', 'security'])} fee{pick(['', ' of any kind', ' for dealers'])}.",
                 f"{pick(['Our staff', 'BONC', 'Our bank', 'Nobody from our company'])} will never ask for your "
                 f"{pick(['OTP', 'password', 'PIN', 'card number', 'verification code'])}.",
                 f"This is not a {pick(['scheme', 'chain plan', 'get-rich-quick offer', 'MLM', 'investment plan'])}; "
                 f"it is a {pick(['regular job with a fixed salary', 'normal wholesale business', 'salaried sales role'])}.",
                 f"We are not {pick(['a first-copy seller', 'resellers of fake goods', 'a lottery', 'a loan company'])}.",
                 f"No advance is needed for {pick(['samples', 'orders under Rs 10,000', 'existing customers'])}."])


_QUOTED = ["price", "quote", "freight charge", "sample fee", "shipping cost", "rate"]
_IMPOSED = ["delay", "lead time", "paperwork", "minimum order quantity", "return policy",
            "warranty terms", "waiting time", "penalty clause", "packaging"]
_DOCUMENT = ["quotation", "estimate", "invoice", "costing sheet", "proposal", "drawing", "sample"]
_FALLS_SHORT = ["is higher than the market rate", "did not match the approved sample",
                "arrived two weeks after the agreed date", "was not what we agreed on the call",
                "does not cover our use case", "came without the test certificate",
                "was based on last year's prices", "left the freight cost out",
                "needs to be revised before we can proceed"]


def blunt_about_a_thing():
    harsh = pick(DISMISSIVE_BLOCKED + DISMISSIVE_NEUTRAL)
    thing = pick(_IMPOSED + _QUOTED)
    return pick([
        f"A {harsh} excuse for {pick(['packaging', 'a warranty', 'after-sales service', 'a delivery schedule'])}, "
        f"{pick(['three boxes were crushed', 'two cartons arrived open', 'nothing was protected', 'the seal was broken'])}.",
        f"The {thing} was {harsh}, honestly.",
        f"The {thing} is {harsh} and the whole market knows it.",
        f"{_sentence_case(harsh)} {thing}, and no explanation for it.",
        f"What a {harsh} {thing}. We have moved the order elsewhere.",
        f"The packaging was {harsh} but the goods inside were fine.",
    ])


def about_someones_work():
    pr = pick(PRONOUNS)
    return pick([
        f"{pr['his_c']} {pick(_IMPOSED)} is {pick(DISMISSIVE_NEUTRAL)} compared with everyone else.",
        f"{pr['his_c']} {pick(_QUOTED)} was higher than we expected.",
        f"{pr['his_c']} packaging was poor and two cartons were damaged.",
        f"{pr['his_c']} warehouse is far from the highway, so pickup takes longer.",
        f"{pr['his_c']} team replaced the damaged units without any argument.",
        f"{pr['his_c']} answer came late but it was complete.",
        f"{pr['he']} quoted a rate we could not accept, so we looked elsewhere.",
        f"{pr['he']} delivered two weeks late and we lost a client because of it.",
        f"{pr['his_c']} {pick(_DOCUMENT)} still has last year's prices on it.",
    ])


HI_CRITICISM = [
    "Bhai sahab, thoda rate kam kar dijiye to order pakka hai.",
    "Thoda discount mil jayega to abhi order kar deta hoon.",
    "Last price bata dijiye, phir final karta hoon.",
    "Itna mehnga hai, thoda kam kijiye na.",
    "Rate zyada lag raha hai, kuch adjust kijiye.",
    "Bulk mein le rahe hain to kuch to kam kijiye.",
    "Itne paise lagane ke baad bhi machine chali nahi, bahut dukh hua.",
    "Maal kharab nikla, humein bahut nuksan hua.",
    "Do din se wait kar rahe hain, bahut pareshan hain.",
    "Order time par nahi aaya isliye mann kharab ho gaya.",
    "Quality dekh kar nirash hue, wapas bhej diya hai.",
    "Paisa to de diya lekin maal abhi tak nahi mila, bahut tension hai.",
    "Pichli baar acha tha, is baar quality kam lagi.",
    "Packing theek nahi thi, do carton damage ho gaye, bahut bura laga.",
    "Bahut umeed thi lekin service se khush nahi hain.",
    "Machine chali nahi aur service wale ne phone nahi uthaya, bahut dukh hua.",
]


def professional_criticism():
    r = R.random()
    if r < .18:
        return blunt_about_a_thing()
    if r < .36:
        return about_someones_work()
    if r < .54:
        return pick(HI_CRITICISM)
    pr = pick(PRONOUNS)
    dis = pick(DISMISSIVE_NEUTRAL)
    if R.random() < .5:
        quoted, imposed = pick(_QUOTED), pick(_IMPOSED)
        return pick([
            f"The {quoted} they quoted was {dis}.",
            f"It is {dis} that the {imposed} took this long to sort out.",
            f"Their {imposed} is {dis} compared with every other supplier we use.",
            f"The {imposed} is {dis} and we said so on the call.",
            f"{pr['he']} said the {dis} {imposed} was set by head office, not by {pr['him']}.",
            f"We think the new {imposed} is {dis}, but the product itself is good.",
            f"Charging that much for a {quoted} is {dis} in this market.",
        ])
    return pick([
        f"I disagree with {pr['his']} approach, but {pr['he_l']} explained it clearly.",
        f"{pr['his_c']} proposal is unrealistic for our budget this quarter.",
        f"{pr['he']} misunderstood our specification, so we sent the drawing again.",
        f"{pr['his_c']} estimate was too optimistic and the schedule slipped.",
        f"{pr['he']} underestimated the lead time, which cost us a week.",
        f"{pr['his_c']} {pick(_DOCUMENT)} {pick(_FALLS_SHORT)}.",
        f"We do not agree with {pr['his']} assessment of the market.",
        f"{pr['his_c']} calculation looks incorrect to us; we have asked for a revised sheet.",
        f"{pr['he']} is new to this category and still learning, so we check every order.",
        f"{pr['his_c']} answer was not detailed enough for our compliance team.",
        f"I would not use {pr['him']} for urgent work, though {pr['his']} standard orders are fine.",
    ])


# --------------------------------------------------------------------------- new B2B & grammar generators

def b2b_negotiation():
    """B2B commercial negotiations, volume discount slabs, counter-offers, payment schedules."""
    prod, (_, unit, certs) = pick(list(PRODUCTS.items()))
    p1 = R.randint(150, 4500)
    p2 = int(p1 * 0.94)
    p3 = int(p1 * 0.88)
    qnum = f"QTN-{R.randint(1000, 9999)}"
    city = pick(CITIES)
    incoterm = pick(INCOTERMS)
    pct = pick([5, 8, 10, 12, 15])
    qty = pick([100, 250, 500, 1000, 2500])

    templates = [
        f"Volume slab pricing for {prod}: 50-199 {unit} @ Rs {p1}, 200-999 {unit} @ Rs {p2}, 1000+ {unit} @ Rs {p3} per {unit}. {incoterm}, GST extra.",
        f"Quotation #{qnum} for {qty} {unit} of {prod}: base rate Rs {p1}/{unit}. We offer {pct}% volume rebate for orders dispatched in full truckload (FTL).",
        f"We reviewed your rate sheet for {prod}. We can issue a PO today at Rs {p2} per {unit} if payment terms are Net 30 days.",
        f"Can you offer 45 days credit against post-dated cheques (PDC), or a 2% prompt payment cash discount for RTGS settlement within 48 hours?",
        f"We can match your target price of Rs {p2} for {prod} subject to a minimum order quantity of {qty} {unit}.",
        f"Payment terms: 20% advance with purchase order, balance 80% against pre-dispatch inspection report and LR copy.",
        f"For export orders of {prod}: Letter of Credit (LC) at sight or 60 days usance accepted from nationalised and scheduled commercial banks.",
        f"Price validity notice: Quoted rates for {prod} are firm for 15 days from quote date #{qnum} due to raw material volatility.",
        f"We accept delivery of {qty} {unit} in two scheduled tranches: half immediately from ready stock and balance within 21 days.",
        f"Counter-offer: Your rate of Rs {p1} exceeds our target price. For an annual contract of {qty * 10} {unit} of {prod}, can you revise to Rs {p3}?",
        f"Commercial terms for {prod}: 30 days interest-free credit for empaneled buyers, delivery Ex-Works {city}.",
        f"If tooling charges for custom {prod} are amortised over {qty} pieces, we are ready to sign the bilateral supply agreement.",
    ]
    return pick(templates)


def procurement_tender():
    """Tenders, Requests for Quotation (RFQs), Bill of Quantities (BOQ), vendor qualification."""
    prod, (specs, unit, certs) = pick(list(PRODUCTS.items()))
    rfq = f"RFQ-{R.randint(10000, 99999)}"
    city = pick(CITIES)
    co = company()
    emd = pick(["25,000", "50,000", "1,00,000", "2,50,000"])
    pct = pick([3, 5, 10])
    qty = pick([500, 1500, 5000, 10000])

    templates = [
        f"Request for Quotation ({rfq}): supply and delivery of {qty} {unit} of {prod} to our {city} plant. Tender closing date {pick(DAYS)}.",
        f"Tender notice: Sealed techno-commercial bids invited for supply of {prod}. Earnest Money Deposit (EMD) of Rs {emd} payable by demand draft or bank guarantee.",
        f"Vendor empanelment: {co} has been shortlisted as an approved L1 supplier for {prod} under our annual rate contract.",
        f"Pre-bid clarification meeting scheduled for {pick(DAYS)} regarding technical specifications and delivery schedule for {prod}.",
        f"Submission of Performance Bank Guarantee (PBG) of {pct}% required within 14 days of Purchase Order issuance.",
        f"Bill of Quantities (BOQ) item 2: {qty} {unit} of {prod}, {pick(specs)}, factory inspection and test certificate mandatory.",
        f"Technical bid opened today: All samples submitted by {co} complied with BIS and ISO benchmarks. Commercial bids open on {pick(DAYS)}.",
        f"Expressions of Interest (EOI) invited from ISO 9001 certified manufacturers for long-term contract supply of {prod}.",
        f"Two-envelope bidding system: Technical bid must contain OEM authorisation, past 3 years balance sheet, and test certificates for {prod}.",
    ]
    return pick(templates)


def hard_negative_pricing_idiom():
    """Aggressive trade idioms, fierce pricing vocabulary, and market idioms used in legal B2B commerce."""
    prod, (_, unit, certs) = pick(list(PRODUCTS.items()))
    pct = pick([8, 10, 12, 15, 20])
    amt = pick(["50,000", "1,20,000", "2,50,000"])
    city = pick(CITIES)
    fair = pick(FAIRS)

    templates = [
        f"We will beat your current price by {pct}% on the same grade of {prod}, share your last invoice and we will better it.",
        f"Our factory-direct rates will crush your current landed cost for {prod} by at least {pct}%, guaranteed on paper.",
        f"We can undercut your existing vendor on {prod} while matching all BIS specifications and test certificates.",
        f"Give us your target price for {unit} of {prod} and our commercial desk will match or destroy it.",
        f"Looking for a reliable mill in {city} that can slash our raw material cost per {unit} this fiscal year.",
        f"Our automated stamping line absolutely murders our old cycle time: from 14 seconds down to 4 seconds per piece.",
        f"We killed our defect rate on {prod} from 3.8% down to 0.1% by installing automated optical sorting.",
        f"Our sales team killed it at the {fair} expo, securing {pick([25, 40, 60])} confirmed wholesale dealership contracts.",
        f"We want to kill the middleman commission and supply {prod} directly from our {city} works to factory floors.",
        f"That monsoon shipment suffered water damage; we ate the entire loss ourselves and dispatched an emergency replacement lot.",
        f"Their tender quote blew ours out of the water, so we re-engineered our fabrication line to reduce overhead.",
        f"We are bleeding margin on {prod} due to rising metal prices and urgently need a competitive secondary source.",
        f"Cut-throat pricing in the wholesale {prod} market requires high volume throughput and lean inventory.",
        f"We took a severe beating on ocean container freight during monsoon, but honoured all existing purchase order prices.",
        f"Dead stock clearance: factory surplus inventory of {prod} offered at cost price to clear warehouse floor space.",
        f"Killer festive offer: order {pick([200, 500, 1000])} {unit} of {prod} before {pick(DAYS)} and receive complimentary tooling worth Rs {amt}.",
        f"We slashed our lead time for custom {prod} from four weeks to five business days.",
        f"Our rock-bottom pricing on {prod} allows retailers to earn healthy margins in a highly competitive market.",
    ]
    return pick(templates)


def b2b_logistics_docs():
    """Supply chain, logistics, GST, e-way bills, IncoTerms, transit insurance, and warehousing."""
    prod, (_, unit, _) = pick(list(PRODUCTS.items()))
    ewb = f"{R.randint(1000, 9999)}-{R.randint(1000, 9999)}-{R.randint(1000, 9999)}"
    lr = f"LR-{R.randint(10000, 99999)}"
    hsn = pick(["8481", "7307", "3917", "5208", "8504", "8544", "6802", "3808", "8413"])
    gst = pick([5, 12, 18, 28])
    veh = f"{pick(['GJ', 'MH', 'DL', 'TN', 'HR', 'KA'])}-{R.randint(1, 99):02d}-{pick(['AB', 'CD', 'EF', 'GH'])}-{R.randint(1000, 9999)}"
    trans = pick(TRANSPORTERS)
    port = pick(["Mundra", "Nhava Sheva", "Kandla", "Chennai", "Kolkata"])
    fport = pick(["Dubai", "Singapore", "Rotterdam", "Colombo", "Durban"])
    qty = pick([50, 150, 400, 1200, 3000])

    templates = [
        f"E-way bill #{ewb} generated for {qty} {unit} of {prod}. Consignment loaded in vehicle #{veh} via {trans}.",
        f"HSN code {hsn} applies to {prod} at {gst}% GST. Tax invoice with full Input Tax Credit (ITC) eligibility will accompany goods.",
        f"Material dispatched under Lorry Receipt (LR) #{lr} through {trans}. Consignee copy forwarded for bank delivery against payment.",
        f"Shipping terms: IncoTerms 2020 FOB {port} port. Seller handles terminal handling charges (THC), container stuffing, and customs clearance.",
        f"CIF {fport} quotation: Marine transit insurance covered under Institute Cargo Clauses (A) from warehouse to warehouse.",
        f"Demurrage and container detention charges will be to buyer's account if customs clearance exceeds seven free days at port.",
        f"Packaging: {qty} {unit} shrink-wrapped on ISPM-15 heat-treated wooden pallets, secured with composite polyester strapping.",
        f"Consignment arrived at {pick(CITIES)} transshipment hub. Transporter tracking link shared with purchase department.",
        f"Part-truckload (PTL) dispatch scheduled for {pick(DAYS)}. Freight to-pay basis as agreed in purchase order.",
        f"Goods damaged during road transit: Transporter damage certificate obtained under LR #{lr} for insurance surveyor inspection.",
    ]
    return pick(templates)


def b2b_qa_specs():
    """Technical specifications, Mill Test Certificates (MTC), NABL testing, dimensional tolerances."""
    prod, (specs, unit, certs) = pick(list(PRODUCTS.items()))
    batch = f"BATCH-{R.randint(1000, 9999)}"
    heat = f"HEAT-{R.randint(50000, 99999)}"
    insp = pick(INSP_AGENCIES)
    ts = R.randint(480, 650)
    ys = R.randint(320, 450)
    el = R.randint(18, 30)

    templates = [
        f"Mill Test Certificate (MTC) for {heat}: Tensile strength {ts} MPa, Yield strength {ys} MPa, Elongation {el}%, verified compliant with ASTM standard.",
        f"Chemical analysis report for {prod}: Carbon 0.18%, Manganese 0.65%, Silicon 0.22%, Sulphur and Phosphorus below 0.035%.",
        f"Batch #{batch} subjected to 100% hydrostatic pressure testing at {pick([10, 16, 25, 40])} bar for 30 minutes with zero pressure drop observed.",
        f"Precision machining report: Outer diameter turned to {pick([25, 50, 100])} mm within tolerance of +/- 0.02 mm. Optical CMM inspection sheet attached.",
        f"Pre-dispatch inspection (PDI) conducted by {insp} at our {pick(CITIES)} factory; formal inspection release note signed.",
        f"Certificate of Analysis (COA) for {prod}: Active assay {pick([99.2, 99.5, 99.8])}%, moisture content below 0.5%, heavy metals within USP/BP pharmacopoeial limits.",
        f"RoHS and REACH compliance declaration: All components of {prod} supplied under this contract are free of lead, mercury, and restricted phthalates.",
        f"Surface roughness measured at Ra {pick([0.4, 0.8, 1.6])} microns after cylindrical grinding. Inspection report enclosed with delivery challan.",
        f"Annual Maintenance Contract (AMC) for {prod}: Includes 4 quarterly preventive maintenance visits and 24-hour breakdown support.",
    ]
    return pick(templates)


def b2b_disputes():
    """Non-violating commercial complaints, RMA, debit/credit notes, liquidated damages."""
    prod, (_, unit, _) = pick(list(PRODUCTS.items()))
    inv = f"INV-{R.randint(1000, 9999)}"
    dn = f"DN-{R.randint(100, 999)}"
    cn = f"CN-{R.randint(100, 999)}"
    amt = pick(["12,450", "28,600", "45,000", "85,200"])
    qty = pick([5, 12, 25, 60])

    templates = [
        f"Debit note #{dn} issued for Rs {amt} against invoice #{inv} towards {qty} {unit} short-received at our {pick(CITIES)} warehouse.",
        f"Quarantine notice: Lot #{R.randint(100, 999)} of {prod} failed hardness testing (32 HRC observed vs 45 HRC specified). Please issue Return Material Authorization (RMA).",
        f"As per clause 8 of the Purchase Order, liquidated damages (LD) at 0.5% per week of delay amounting to Rs {amt} will be deducted from invoice #{inv}.",
        f"Credit note #{cn} of Rs {amt} received towards agreed price difference on supply of {prod}. Adjusted against outstanding ledger balance.",
        f"Joint inspection held at buyer's facility on {pick(DAYS)} confirmed transit vibration damage due to loose pallet banding. Carrier claim lodged.",
        f"The sample lot of {prod} was rejected due to gauge thickness variation (+/- 0.12 mm observed vs +/- 0.03 mm allowed). Please submit revised samples.",
        f"Notice of commercial discrepancy: Invoice #{inv} calculated GST at 18% instead of the applicable 12% rate under HSN. Kindly issue amended invoice.",
        f"Warranty replacement request: Three units of {prod} developed seal leakage within the 12-month warranty period. Replacement dispatched Ex-Works.",
    ]
    return pick(templates)


def grammar_business_prose():
    """Indian business English idioms and non-native grammar phrasing common in trade correspondence."""
    prod, (_, unit, _) = pick(list(PRODUCTS.items()))
    qty = pick([100, 250, 500, 1000])
    trans = pick(TRANSPORTERS)
    city = pick(CITIES)

    templates = [
        f"Dear Sir, please find attached the revised quotation for {prod} and kindly do the needful at the earliest.",
        f"With reference to our telecon today, we are having {qty} {unit} of {prod} in ready stock for immediate dispatch.",
        f"We request you to kindly prepone the delivery of {prod} by one week as our client site work is standing idle.",
        f"Please discuss about the commercial terms with our accounts manager, she will revert back on same today itself.",
        f"We are dealing in all kinds of industrial equipments and machineries since last {pick([12, 15, 20])} years with good reputation.",
        f"The buyer has raised complaint that two pieces of {prod} was having minor scratch, please arrange immediate replacement.",
        f"Kindly confirm whether we can send the payment through NEFT, also please share the pakka GST invoice with HSN code.",
        f"Can you please revert back with your best possible rate, we are wanting to place an order of {qty} pieces of {prod}.",
        f"Yesterday we did not received the LR copy, so our clearing agent could not take delivery from transporter godown in {city}.",
        f"Our firm are having 15 CNC machines and supplying precision components to major auto companies across {pick(STATES)}.",
        f"The rates quoted by your sales executive is more cheaper than competitors, so our management is approving the sample.",
        f"Please do not worry regarding quality, we provide same-to-same piece as per approved master sample.",
        f"Because of festive holidays our factory were closed, now dispatch has been resumed from today morning.",
        f"We are pleased to inform that your order for {qty} {unit} of {prod} has been dispatched through {trans}.",
        f"Kindly arrange to send the debit note copy, we will adjust the amount in next billing cycle without fail.",
        f"Due to heavy rains in {city} transporter was unable to place container on time, goods will dispatch tomorrow.",
    ]
    return pick(templates)


def grammar_everyday_prose():
    """Everyday prose and personal writing exhibiting realistic non-standard grammar, punctuation variations, and casual phrasing."""
    city = pick(CITIES)
    yr = R.randint(1985, 2021)

    templates = [
        f"Me and my brother visited the industrial exhibition yesterday and buyed new measuring gauges for workshop.",
        f"The weather was very bad since three days so delivery trucks could not move on highway near {city}.",
        f"My father who started this small workshop in {yr} were always telling to maintain honest relations with buyers.",
        f"Yesterday I discuss with my friend about opening a wholesale electrical counter in {city}, he give good advices.",
        f"Every morning our supervisor inspects all machineries before starting the shift production run.",
        f"After school the children was playing in ground while we were discussing family function arrangements.",
        f"Train were late by three hours due to fog, but anyhow we reached vendor meeting on time.",
        f"She have completed engineering degree last year and now handling our export documentation desk.",
        f"We was waiting for parcel since last Friday, finally courier boy delivered today afternoon.",
        f"My uncle have thirty years experience in casting foundry and he know all technical parameters.",
    ]
    return pick(templates)


IDENTITY = ["Muslim", "Hindu", "Sikh", "Christian", "Dalit", "gay", "lesbian", "transgender", "LGBTQ", "women",
            "tribal", "disabled", "Jain", "Parsi", "Bengali", "Bihari", "North-Eastern"]


def identity_neutral():
    g = pick(IDENTITY)
    return pick([f"{_sentence_case(g)} entrepreneurs {pick(['met at the trade fair', 'are growing fast in our city', 'shared their stories at the summit'])}.",
                 f"Our team includes {g} {pick(['engineers', 'artisans', 'managers', 'workers'])}.",
                 f"{_sentence_case(g)} {pick(['history', 'culture', 'festivals', 'rights', 'representation'])}",
                 f"A {g} {pick(['woman', 'man', 'founder', 'weaver'])} {pick(['started this company', 'designed our logo', 'runs our export desk'])}.",
                 f"We celebrate {pick(['Eid', 'Diwali', 'Christmas', 'Gurpurab', 'Pride month', 'Onam'])} with all our staff.",
                 f"Panel discussion on {pick(['inclusion', 'diversity', 'equal pay'])} for {g} employees.",
                 "Homosexuality", "Religion and business", "Caste and entrepreneurship in India"])


SAFE_FRAGMENTS = ["Warm wishes", "Thank u", "Tks", "Pls check", "Noted sir", "PFA", "Ok done", "Annual report", "MEGA SALE",
                  "BEST QUALITY ASSURED", "NEW ARRIVALS", "Contact us", "Hello sir", "Good evening", "brochure_2024",
                  "quotation_final_v2.docx", "price-list.xlsx", "www.sunrisepolymers.in", "sunrisepolymers.in/catalogue",
                  "weeks", "png", "Tommy", "3 rows", "instagram page", "HSN 5208", "GSTIN 27ABCDE1234F1Z5", "Sr. No. 4",
                  "Chapter 2", "Step three", "Plot no. 17", "Mob", "Fig. 3", "Kindly revert", "Same as above", "PO attached",
                  "Inv #1029", "Bilti copy", "E-way bill 4510", "ASTM A106", "ISO 9001", "MTC ready", "PDC enclosed"]


def fragment():
    f = pick(SAFE_FRAGMENTS)
    return f if R.random() < .7 else f"{f} {pick(SAFE_FRAGMENTS)}"


TITLES = ["About our {prod} business", "Draft about {prod}", "Case study: {topic}", "Why {prod} prices are rising",
          "Our story", "Meet the team", "New arrivals", "Price list {year}", "Festive offers", "Company profile",
          "How we work", "Quality policy", "Export markets", "Frequently asked questions", "Contact us",
          "{prod}: a buyer's guide", "Tips for first-time exporters", "Behind the scenes at our {city} plant",
          "Monsoon sale on {prod}", "Product catalogue", "Terms and conditions", "Our certifications",
          "Customer reviews", "Industry update", "Lessons from {year}", "Why choose us",
          "Procurement guidelines for {prod}", "Understanding GST on {prod}", "Quality inspection checklist for {prod}"]


def title():
    return pick(TITLES).format(prod=pick(list(PRODUCTS)), topic=pick(["cutting logistics costs", "zero defects",
                               "going digital", "export growth", "saving energy", "vendor evaluation"]),
                               year=R.randint(2018, 2026), city=pick(CITIES))


HI_PRODUCT = ["kurte", "sarees", "chappal", "bartan", "LED bulb", "PVC pipe", "cotton cloth", "masala",
              "chawal", "cement", "tiles", "furniture", "mobile cover", "bags", "shoes", "toys",
              "steel plate", "wire", "paint", "pump", "motor", "fan", "geyser", "chairs", "table",
              "packing material", "carton", "rassi", "taala", "pankha", "kapda", "dhaga", "button"]
HI_QTY = ["10", "20", "25", "50", "100", "200", "500", "1000"]
HI_DAYS = ["2 se 3 din", "5 se 7 din", "ek hafte", "10 din", "15 din", "do hafte", "3-4 din"]
HI_SAFE = [
    "{p} ka naya stock aa gaya hai, dealers sampark karein.",
    "Naye design ke {p} aa gaye hain, wholesale rate par milenge.",
    "{p} sabhi size aur colour mein available hai.",
    "Hamare paas {p} ki puri range hai.",
    "{p} ka rate thoda zyada hai lekin quality best hai.",
    "Bulk order pe extra discount milega, abhi enquiry bhejein.",
    "{n} piece se upar order karne par delivery free hai.",
    "Delivery {d} mein ho jayegi.",
    "{c} mein same day delivery available hai.",
    "Order confirm hone ke baad {pct}% advance, baaki delivery par.",
    "Payment company ke current account mein hi karein, GST bill turant milega.",
    "Sample chahiye to message karein, courier se bhej denge.",
    "Rate list WhatsApp par bhej deta hoon.",
    "Hum {y} se yeh kaam kar rahe hain.",
    "Hamare paas {yr} saal ka experience hai, quality mein koi compromise nahi.",
    "Hamari factory {c} mein hai, aap visit kar sakte hain.",
    "Quality check ke baad hi dispatch hota hai.",
    "ISI mark ka maal hai, test report bhi milegi.",
    "Diwali ke liye special gift boxes ready hain.",
    "Festival offer: {n} carton lene par ek carton free.",
    "Kal se naya rate lagu hoga, purana stock khatam ho raha hai.",
    "Aapka order dispatch ho gaya hai, tracking number bhej diya hai.",
    "Kripya apna GST number bhejein, bill banana hai.",
    "Minimum order {n} piece ka hai.",
    "Retail aur wholesale dono karte hain.",
    "Transport ka kharcha alag lagega.",
    "Maal check karke hi lena, damage ki complaint 24 ghante mein karein.",
    "Hamara showroom {c} mein hai, subah 10 baje se shaam 8 baje tak khula rehta hai.",
    "{p} ke liye alag se quotation bhej raha hoon.",
    "Stock limited hai, pehle aao pehle pao.",
    "Aap apna address bhej dijiye, courier kar dunga.",
    "Hum pure {st} mein supply karte hain.",
    "Naya catalogue ready hai, PDF bhej dunga.",
    "Is baar ka maal pichli baar se behtar hai.",
    "Aaj barish ho rahi hai isliye delivery thodi late hogi.",
    "Kal chhutti hai, office band rahega.",
    "Train late thi isliye main der se pahuncha.",
    "Tabiyat theek nahi thi isliye kal nahi aa paya.",
    "Bacche school se aa gaye hain, shaam ko baat karta hoon.",
    "Shaadi ka season hai isliye demand zyada hai.",
    "Unka rate market se zyada hai, isliye humne doosre supplier se liya.",
    "Delivery late hui thi, humne complaint darj ki thi.",
    "Maal sample jaisa nahi tha, humne wapas bhej diya.",
    "Packing theek nahi thi, do carton damage ho gaye.",
    "Rate bahut zyada hai, humein yeh sahi nahi laga.",
    "Unka behaviour theek tha lekin quality thodi kam thi.",
]


def hinglish_safe():
    return pick(HI_SAFE).format(p=pick(HI_PRODUCT), n=pick(HI_QTY), d=pick(HI_DAYS), c=pick(CITIES),
                                pct=pick([20, 25, 30, 40, 50]), y=R.randint(1985, 2020),
                                yr=R.randint(5, 40), st=pick(STATES))


G_SUBJ = [("I", "1s"), ("We", "1p"), ("You", "2"), ("My brother", "3s"), ("My sister", "3s"), ("Our teacher", "3s"),
          ("The children", "3p"), ("My parents", "3p"), ("She", "3s"), ("He", "3s"), ("They", "3p"),
          ("My friend", "3s"), ("The neighbours", "3p"), ("Everyone", "3s"), ("Grandmother", "3s"), ("Our team", "3s")]

G_VERB = [
    ("go", "goes", "went", "going", ["to school", "to the market", "to the temple", "to the park", "for a walk", "to work by bus"]),
    ("eat", "eats", "ate", "eating", ["rice and dal", "breakfast at eight", "too many sweets", "dinner together", "fresh fruit"]),
    ("read", "reads", "read", "reading", ["the newspaper", "a story book", "the news on the phone", "a long novel"]),
    ("play", "plays", "played", "playing", ["cricket", "football in the evening", "the guitar", "chess with grandfather", "badminton"]),
    ("watch", "watches", "watched", "watching", ["a movie", "the match on TV", "the rain from the window", "cartoons"]),
    ("cook", "cooks", "cooked", "cooking", ["biryani", "a simple lunch", "tea for the guests", "chapati and sabzi"]),
    ("visit", "visits", "visited", "visiting", ["our relatives", "the museum", "the doctor", "the old fort", "the village"]),
    ("clean", "cleans", "cleaned", "cleaning", ["the house", "the kitchen", "the car", "the garden"]),
    ("write", "writes", "wrote", "writing", ["a letter", "in a diary", "a poem", "notes for the exam"]),
    ("buy", "buys", "bought", "buying", ["vegetables", "a new phone", "school books", "flowers for mother"]),
    ("learn", "learns", "learned", "learning", ["English", "to swim", "to drive", "a new song", "coding"]),
    ("help", "helps", "helped", "helping", ["the old man", "my mother at home", "the new student", "our neighbours"]),
    ("travel", "travels", "travelled", "travelling", ["to Goa", "by train", "to the hills", "with family"]),
    ("miss", "misses", "missed", "missing", ["the bus", "my hometown", "the old days", "the last train"]),
]
G_TIME_PAST = ["yesterday", "last week", "last year", "in the morning", "two days ago", "during the holidays", "when I was young"]
G_TIME_NOW = ["every day", "on Sundays", "in the evening", "usually", "sometimes", "after school", "every morning"]
G_TIME_FUT = ["tomorrow", "next week", "this weekend", "after the exams", "next year", "soon"]

FEELINGS = ["I was very angry when the train was late.", "She felt sad after her friend moved away.",
            "Our dog died last winter and we all cried.", "He was scared of the dark as a child.",
            "I hate waiting in long queues.", "We were tired after the long journey.",
            "My little brother is afraid of dogs.", "The movie was so boring that I fell asleep.",
            "I lost my wallet and felt terrible.", "The exam was hard and I was worried about the result.",
            "It was the worst day of my week, everything went wrong.", "She was disappointed with her marks.",
            "The heat this summer is killing me.", "My phone died in the middle of the call.",
            "The accident on the highway blocked traffic for hours.", "The villain in the story is cruel and selfish.",
            "The war in the history book killed thousands of people.", "He fought with his brother over the remote.",
            "The storm destroyed many houses near the coast.", "I could not stop laughing at the silly joke."]

GENERAL = ["The weather is pleasant today.", "It rained heavily all night.", "The sun rises in the east.",
           "Water boils at one hundred degrees.", "Mumbai is a busy city.", "The Himalayas are very tall mountains.",
           "Trees give us oxygen and shade.", "Please close the door when you leave.", "Drink plenty of water in summer.",
           "Do you like tea or coffee?", "What time does the shop open?", "Where did you keep my keys?",
           "How was your trip to Kerala?", "Can you help me carry this bag?", "Why is the sky blue?",
           "If it rains, we will stay at home.", "If I had more time, I would learn painting.",
           "The book on the table is mine.", "There are seven days in a week.", "Cats sleep for most of the day.",
           "Reading every day improves vocabulary.", "The festival brings the whole family together.",
           "Our school has a big library.", "The river was full after the monsoon.", "Birds were singing in the morning.",
           "Happy birthday to my dear friend!", "Thank you for your kind words.", "Good morning, have a nice day.",
           "Wash your hands before eating.", "Walking is good for health.", "The train was crowded but comfortable.",
           "My favourite colour is blue.", "The baby is sleeping, please be quiet.", "We planted a mango tree in the garden."]

PERSONAL_ARTICLES = {
    "My first job": ["I got my first job at the age of twenty.", "The office was far from my home.",
                     "I woke up at six every day to catch the bus.", "My manager was strict but kind.",
                     "I made many mistakes in the first month.", "Slowly I learned how to do the work well.",
                     "I still remember my first salary.", "I bought a saree for my mother with it."],
    "A trip to the hills": ["Last summer we went to Manali.", "The road was narrow and full of turns.",
                            "My sister felt sick on the way.", "The view from the hotel was beautiful.",
                            "We walked by the river every evening.", "It was very cold at night.",
                            "We ate hot maggi at a small stall.", "I want to go back next year."],
    "Festival memories": ["Diwali was my favourite festival as a child.", "We cleaned the whole house a week before.",
                          "Mother made sweets for all the neighbours.", "We lit diyas on every window.",
                          "The sound of crackers scared our dog.", "Now we celebrate with fewer crackers.",
                          "The joy of the family together is the same."],
    "Learning to cook": ["I could not cook anything until last year.", "My first dal was too salty.",
                         "My roommate laughed but she helped me.", "I watched videos and practised every day.",
                         "Now I can make simple meals.", "Cooking at home also saves money."],
    "My grandfather": ["My grandfather was a farmer.", "He woke up before sunrise every day.",
                       "He told us stories about his childhood.", "He was strict about discipline.",
                       "He passed away three years ago.", "We miss him a lot.", "His advice still guides me."],
    "Why I like reading": ["Books take me to new places.", "I read before sleeping every night.",
                           "My favourite writer is R. K. Narayan.", "Reading helped my English a lot.",
                           "I borrow books from the library.", "Sometimes I read the same book twice."],
    "A rainy day": ["It started raining in the afternoon.", "The streets were full of water.",
                    "Children made paper boats.", "The power went off for two hours.",
                    "We drank tea and ate pakoras.", "The smell of wet soil was lovely."],
}


def simple_sentence():
    subj, person_label = pick(G_SUBJ)
    base, third, past, ing, objs = pick(G_VERB)
    obj = pick(objs)
    r = R.random()
    if r < .25:
        return f"{subj} {past} {obj} {pick(G_TIME_PAST)}."
    if r < .45:
        v = third if person_label == "3s" else base
        return f"{subj} {v} {obj} {pick(G_TIME_NOW)}."
    if r < .6:
        return f"{subj} will {base} {obj} {pick(G_TIME_FUT)}."
    if r < .7:
        be = {"1s": "am", "3s": "is"}.get(person_label, "are")
        return f"{subj} {be} {ing} {obj} now."
    if r < .78:
        aux = "does" if person_label == "3s" else "do"
        return f"{aux.capitalize()} {subj.lower() if subj != 'I' else subj} {base} {obj}?"
    if r < .86:
        aux = "doesn't" if person_label == "3s" else "don't"
        return f"{subj} {aux} {base} {obj} {pick(G_TIME_NOW)}."
    if r < .92:
        return f"{_sentence_case(base)} {obj} {pick(G_TIME_FUT)}, please."
    return f"If {subj.lower() if subj != 'I' else subj} {past} {obj}, {pick(['everyone was happy', 'it was fun', 'we were late', 'nothing changed'])}."


def everyday():
    r = R.random()
    if r < .45:
        return " ".join(simple_sentence() for _ in range(R.randint(1, 3))), "everyday"
    if r < .65:
        return pick(FEELINGS), "everyday_feelings"
    if r < .85:
        return " ".join(R.sample(GENERAL, R.randint(1, 3))), "everyday_general"
    title_, sents = pick(list(PERSONAL_ARTICLES.items()))
    k = R.randint(3, len(sents))
    body = sents[:k] if R.random() < .6 else R.sample(sents, k)
    if R.random() < .4:
        body.insert(R.randint(0, len(body)), simple_sentence())
    return f"{title_}\n\n{' '.join(body)}", "article_personal"


# Non-native / careless grammar swaps. Applied to safe AND unsafe text so grammar never moves the label.
_GRAMMAR_SWAPS = [
    (r"\b(is|are)\b", {"is": "are", "are": "is"}),
    (r"\b(was|were)\b", {"was": "were", "were": "was"}),
    (r"\b(has|have)\b", {"has": "have", "have": "has"}),
    (r"\b(goes)\b", {"goes": "go"}),
    (r"\b(went)\b", {"went": "go"}),
    (r"\b(does)\b", {"does": "do"}),
    (r"\b(bought)\b", {"bought": "buyed"}),
    (r"\b(doesn't)\b", {"doesn't": "don't"}),
    (r"\b(children)\b", {"children": "childs"}),
    (r"\b(an)\b", {"an": "a"}),
    (r"\b(sent)\b", {"sent": "send"}),
    (r"\b(paid)\b", {"paid": "payed"}),
    (r"\b(received)\b", {"received": "receive"}),
    (r"\b(dispatched)\b", {"dispatched": "dispatch"}),
    (r"\b(quoted)\b", {"quoted": "quote"}),
    (r"\b(verified)\b", {"verified": "verify"}),
    (r"\b(equipment)\b", {"equipment": "equipments"}),
    (r"\b(machinery)\b", {"machinery": "machineries"}),
    (r"\b(furniture)\b", {"furniture": "furnitures"}),
    (r"\b(advice)\b", {"advice": "advices"}),
    (r"\b(feedback)\b", {"feedback": "feedbacks"}),
    (r"\b(stationery)\b", {"stationery": "stationeries"}),
    (r"\b(information)\b", {"information": "informations"}),
    (r"\b(infrastructure)\b", {"infrastructure": "infrastructures"}),
    (r"\b(scrap)\b", {"scrap": "scraps"}),
    (r"\b(revert)\b", {"revert": "revert back"}),
    (r"\b(discuss)\b", {"discuss": "discuss about"}),
    (r"\b(comply with)\b", {"comply with": "comply to"}),
    (r"\b(demanded)\b", {"demanded": "demanded for"}),
    (r"\b(cope with)\b", {"cope with": "cope up with"}),
    (r"\b(better)\b", {"better": "more better"}),
    (r"\b(cheaper)\b", {"cheaper": "more cheaper"}),
    (r"\b(faster)\b", {"faster": "more faster"}),
    (r"\b(didn't receive|did not receive)\b", {"didn't receive": "didn't received", "did not receive": "did not received"}),
    (r"\b(we have)\b", {"we have": "we are having"}),
    (r"\b(they have)\b", {"they have": "they are having"}),
    (r"\b(we know)\b", {"we know": "we are knowing"}),
    (r"\b(we want)\b", {"we want": "we are wanting"}),
    (r"\b(the goods are)\b", {"the goods are": "the goods is"}),
    (r"\b(supplier has)\b", {"supplier has": "supplier have"}),
    (r"\b(firm has)\b", {"firm has": "firm have"}),
    (r"\b(don't)\b", {"don't": "dont"}),
    (r"\b(can't)\b", {"can't": "cant"}),
    (r"\b(won't)\b", {"won't": "wont"}),
    (r"\b(it's)\b", {"it's": "its"}),
]


def bad_grammar(text):
    out = text
    for _ in range(R.randint(1, 3)):
        r = R.random()
        if r < .30:   # drop articles
            out = re.sub(r"\b(the|a|an|The|A|An) ", "", out, count=R.randint(1, 3))
        elif r < .70:
            pat, table = pick(_GRAMMAR_SWAPS)
            out = re.sub(pat, lambda m: table.get(m.group(1), m.group(1)), out, count=1)
        elif r < .80:  # no final punctuation / lowercased initial
            out = out.rstrip(".!?")
            out = out[:1].lower() + out[1:]
        elif r < .90:  # run-on: commas instead of full stops
            out = re.sub(r"\.\s+(\w)", lambda m: ", " + m.group(1).lower(), out, count=2)
        else:          # doubled word for emphasis or typo
            words = out.split(" ")
            if len(words) > 3:
                i = R.randrange(len(words))
                words.insert(i, words[i])
                out = " ".join(words)
    return out


SAFE_GEN = [
    (listing, "listing", 12),
    (profile, "profile", 6),
    (review, "review", 8),
    (announcement, "announcement", 6),
    (job, "job", 3),
    (enquiry, "enquiry", 5),
    (hard_negative, "hard_negative", 8),
    (professional_criticism, "professional_criticism", 6),
    (b2b_negotiation, "b2b_negotiation", 14),
    (procurement_tender, "procurement_tender", 10),
    (hard_negative_pricing_idiom, "hard_negative_pricing_idiom", 12),
    (b2b_logistics_docs, "b2b_logistics_docs", 10),
    (b2b_qa_specs, "b2b_qa_specs", 10),
    (b2b_disputes, "b2b_disputes", 8),
    (grammar_business_prose, "grammar_business_prose", 16),
    (grammar_everyday_prose, "grammar_everyday_prose", 12),
]

# --------------------------------------------------------------------------- unsafe

AMOUNTS = ["50000", "50,000", "1 lakh", "25000", "Rs 5000", "10k", "$500", "2 lakh", "35,000", "80,000"]
PERIODS = ["per week", "per month", "a week", "every month", "daily", "in 10 days", "weekly"]

UNSAFE = {
    "scam_income": [
        "Earn {amt} {per} from home! No experience needed, limited slots, DM now.",
        "Work from home and earn big, message us for details.",
        "Earn {amt} {per} just by liking videos. Join now before slots fill up.",
        "Guaranteed {pct}% monthly returns on your investment. No risk, 100% safe.",
        "Double your money in {d} days with our trading scheme.",
        "Invest {amt} today and get {amt2} back in {d} days, guaranteed.",
        "Part-time job: earn {amt} {per} by typing work. Joining kit fee applies.",
        "Join our crypto signal group and get 10x returns every week. Paid membership only.",
        "Become a millionaire in six months with our secret business system.",
        "Unlimited income from home, no work required, just share this link with friends.",
        "Our members earn {amt} {per} with zero effort. Ask me how.",
        "Chain plan: recruit five members and get {amt} {per} for life.",
        "Risk-free forex trading with assured profit of {pct}% per month.",
    ],
    "registration_fee": [
        "Looking for distributors in {state}. Very high margins, no investment needed, just register with a small fee.",
        "Limited slots for dealers, register now with a small fee.",
        "Urgent requirement: pay registration charges to get guaranteed government tender approval.",
        "Dealership available. Pay a refundable security deposit of {amt} to our agent's personal account to confirm.",
        "Job offer at a multinational company. Pay {fee} for training and uniform before joining.",
        "Selected for data entry job. Transfer {fee} registration fee today to receive your ID card.",
        "Franchise with assured income. Send the joining amount by UPI to confirm your slot.",
        "Your loan of {amt} is approved. Pay {fee} processing fee to release it.",
        "Instant business loan without documents, pay the file charge first.",
        "We guarantee your product will be approved for government supply. Pay our consultancy fee in advance.",
        "Agency required. Pay {fee} to get the territory rights and guaranteed orders.",
    ],
    "phishing": [
        "Your account will be suspended. Share your password and card number to verify immediately.",
        "Send your bank details and OTP to claim your prize money today.",
        "Your BONC seller account is blocked. Click the link and enter your login details to unblock it.",
        "KYC pending. Update your PAN and card details on this link within 24 hours or your payments stop.",
        "Share the OTP you just received to complete the payment verification.",
        "Dear seller, we need your net banking password to process the refund.",
        "Please send a photo of your debit card front and back for verification.",
        "Your GST number will be cancelled today. Verify by paying the penalty at this link.",
        "Tell me the CVV on your card so I can send the advance.",
        "{pick_ask} the {pick_code} {pick_got} so we can {pick_purpose}.",
        "{pick_ask} your {pick_secret} on this form to {pick_purpose}.",
    ],
    "lottery": [
        "Congratulations! You have won a lottery of {amt} rupees. Pay the processing charges to receive it.",
        "You are the lucky winner of a car in our anniversary draw. Pay the registration and tax to claim.",
        "Your mobile number has won {amt} in the KBC lucky draw. Call now and pay the release fee.",
        "Claim your free iPhone now, just pay the delivery charges.",
    ],
    "counterfeit": [
        "Cheap replica branded watches, first copy, cash on delivery.",
        "First copy {brand} bags available at wholesale rate.",
        "7A quality {brand} shoes, same as original, no bill.",
        "Master copy {brand} perfumes at one tenth of the original price.",
        "Duplicate {brand} spare parts with original packing and hologram.",
        "We print any brand logo on your products, {brand} and others, looks 100% original.",
        "Fake {brand} labels and tags available in bulk.",
        "Copy of {brand} phone chargers, looks exactly like original, lowest price.",
        "Mirror quality {brand} sunglasses, nobody can tell the difference.",
        "Old stock relabelled with new expiry dates, very cheap.",
        "ISI mark stickers available for any product without testing.",
    ],
    "payment_trick": [
        "Limited stock! Contact us on WhatsApp for wholesale prices, full payment in advance only.",
        "Don't order through BONC, the fees are high. Pay me directly on UPI and I will ship.",
        "100% advance payment to my personal account only, no GST bill, no returns.",
        "Deal outside the platform for a better price. Call me and pay in cash.",
        "Send the full amount first by gift card, then we dispatch.",
        "Pay only in crypto or Western Union, bank transfer not accepted.",
        "Price is lower if you pay to my wife's account; the company account has problems.",
        "Only today! Pay now or lose the stock, no questions, no refunds.",
    ],
    "threat": [
        "She will regret the day she crossed me.",
        "Pay me back for the order or you will regret it.",
        "We know where your warehouse is. Think carefully before you file that complaint.",
        "Watch your back, {name}. This is not over.",
        "If you post one more bad review, you will see what happens to your shop.",
        "I will make sure {he} never works in this market again, whatever it takes.",
        "Cancel the complaint or your family will pay for it.",
        "I hope she dies in an accident soon.",
        "They should all be burned alive along with their shop.",
        "I will destroy your business and your reputation.",
        "Your trucks may not reach the city safely next time.",
        "{he} had better keep {his} mouth shut if {he} knows what's good for {him}.",
        "I will beat you when you come outside.",
        "i will break his legs if he come to my shop again",
        "You will die soon, I promise.",
        "I will find you and hurt you.",
        "wait and see what i do to you and your family",
        "{pick_bad} tends to happen to people who {pick_why}.",
        "Your {pick_asset} {pick_fate} if you {pick_why2}.",
        "We know where your {pick_family} {pick_live}. {pick_warn}",
        "Nice {pick_asset} you have. Would be a shame if something happened to it.",
        "You will find out what happens to people who {pick_why}.",
        "Consider this your last warning, {name}.",
    ],
    "abuse": [
        "You people are idiots and I will destroy your business.",
        "{he} is a lying scumbag who cheated us on our order.",
        "{he} is a moron who ruined our entire shipment on purpose.",
        "You people are worthless and I hope your company collapses.",
        "What a pathetic, useless bunch of clowns running this company.",
        "The owner is a disgusting cheat and should rot.",
        "Only a brainless fool would buy from these losers.",
        "Shut up and stop spamming us, you stupid woman.",
        "His thinking and mindset are disgusting, he is a filthy pig.",
        "This supplier is a bloody thief and a liar.",
        "Go to hell, you useless idiot.",
        "you are very stupid person and your shop is rubbish",
        "She is so ugly and dumb, nobody like her.",
        "Get lost, you dirty dog.",
        "This man is a donkey, he have no brain.",
        "What a shameless liar {he_l} is.",
        "Idiots like {him} should not be allowed to run a business.",
        "Pathetic cheats, every one of {him}.",
        "Such a useless fool, {his} whole team is the same.",
        "Stupid people like {him} ruin the market for everyone.",
        "Fraudster, that is what {he_l} is.",
    ],
    "hate": [
        "Don't trust suppliers from that region, they are all cheats.",
        "Never do business with {group}, they are all thieves.",
        "{group} should not be allowed to sell on this platform.",
        "We don't hire {group}; they are lazy and dirty.",
        "All {group} traders are liars, avoid them.",
        "Keep {group} out of our market association.",
        "{group} people are the reason this industry is corrupt.",
        "{group} should not be allowed to {pick_allowed}.",
        "People from that {pick_community} should {pick_exclude}.",
    ],
    "dismissive": [
        "{his_c} way of thinking is {dis}.",
        "{his_c} whole approach is {dis}, {he_l} clearly has no idea what {he_l} is doing.",
        "{he} has no clue how this industry works.",
        "{he} hasn't got the faintest idea what {he_l} is talking about.",
        "Everything {he_l} says about this market is {dis}.",
        "{his_c} ideas are {dis} and always have been.",
        "Anyone who thinks like {him} should not be running a business.",
        "{he} is completely clueless about {his} own product.",
        "{his_c} understanding of pricing is {dis}.",
        "{he} is out of {his} depth and everyone can see it.",
        "{his_c} opinion on this counts for nothing.",
        "Don't waste your time explaining, {he_l} won't understand it anyway.",
        "{he} talks a lot but {he_l} understands nothing.",
        "{his_c} reasoning is {dis}; no serious buyer would accept it.",
        "It is {dis} that someone like {him} is allowed to trade here.",
        "{he} is the most incompetent supplier I have ever dealt with.",
        "{his_c} team has no idea what they are doing, {dis} from start to finish.",
        "Reading {his} messages is painful, {he_l} makes no sense at all.",
    ],
    "defamation": [
        "Our competitor {co} sells fake products, do not trust them.",
        "{co} are frauds who cheat every customer. Everyone should boycott them.",
        "Don't buy from {co}, the owner is a known criminal.",
        "{co} mixes chalk powder in their turmeric, spread the word.",
        "Everyone knows {co} bribes officials to pass inspections.",
    ],
    "adult": [
        "Hot college girls available for private meetings, call now.",
        "Body massage with extra services at your hotel, full satisfaction.",
        "Escort service in {city}, discreet and available 24x7.",
        "Adult videos and private photos for sale, message for the link.",
        "Lonely? Chat with sexy girls tonight, pay per minute.",
    ],
    "illegal": [
        "Ganja and charas available, home delivery in {city}.",
        "Pistols without licence, contact for rates.",
        "Fake GST invoices available for input tax credit, any amount.",
        "Genuine-looking degree certificates from any university, no exams needed.",
        "Ivory and tiger skin items for collectors, strictly private sale.",
        "Tramadol and codeine syrup without prescription, bulk available.",
        "Cracked software and licence keys at 90% off, fully activated.",
        "Fake driving licences and Aadhaar cards made in one day.",
        "We can get your product BIS certified without testing, for a fee.",
        "Stolen phones available cheap, no questions asked.",
        "Sand and coal available without royalty papers, cash only.",
    ],
    "spam": [
        "Click here bit.ly/fr33-cash to get free cash now!!!",
        "FREE FREE FREE!!! Visit our site now to win big, limited offer!!!",
        "Buy followers and fake reviews for your BONC profile, 1000 reviews for Rs 999.",
        "Get 5-star reviews for your business, guaranteed, no real customers needed.",
        "SEO backlinks casino betting cheap cheap cheap visit now.",
        "Online betting app, win daily, use my referral code for a bonus.",
    ],
}

BRANDS = ["Rolex", "Gucci", "Nike", "Adidas", "Louis Vuitton", "Apple", "Samsung", "Ray-Ban", "Bosch", "Philips",
          "Chanel", "Titan", "Puma", "Boat", "Havells"]
GROUPS = ["Biharis", "Marwaris", "Muslims", "Hindus", "Christians", "Dalits", "South Indians", "northerners",
          "Sikhs", "Bengalis", "migrants", "Chinese sellers", "tribals", "women sellers", "Gay people",
          "Transgender people", "Lesbians", "Disabled people"]

HINGLISH_UNSAFE = [
    ("scam_income", "Ghar baithe {amt} {per} kamao, koi experience nahi chahiye, abhi DM karo."),
    ("scam_income", "Paisa double scheme, sirf {d} din mein, 100% guarantee."),
    ("scam_income", "Paise lagao aur har mahine {pct}% pakka return pao."),
    ("registration_fee", "Registration fee sirf {fee} bhejo aur job pakki."),
    ("registration_fee", "Dealership ke liye {fee} jama karo, order ki guarantee hai."),
    ("phishing", "Aapka account band ho jayega, OTP turant bhejo."),
    ("phishing", "Card number aur CVV bhejo, refund process karna hai."),
    ("counterfeit", "First copy maal available hai, original jaisa, bill nahi milega."),
    ("counterfeit", "{brand} ka duplicate maal sasta mil jayega, koi pehchan nahi payega."),
    ("payment_trick", "Pura payment advance mein, sirf mere personal UPI par."),
    ("payment_trick", "BONC se order mat karo, seedha mujhe paise bhejo, sasta padega."),
    ("threat", "Dekh lena, tera dhandha band karwa dunga."),
    ("threat", "Complaint wapas le warna anjaam bura hoga."),
    ("abuse", "Yeh supplier ek number ka chor aur dhokebaaz hai, bewakoof log."),
    ("abuse", "Tum log bilkul nikamme aur gadhe ho."),
    ("hate", "{group} se kabhi maal mat lena, sab chor hote hain."),
    ("dismissive", "Usko kuch nahi pata, bekar aadmi hai."),
    ("dismissive", "Unki soch hi galat hai, unhe kuch samajh nahi aata."),
    ("dismissive", "Isse baat karna time waste hai, kuch akal nahi hai."),
    ("dismissive", "Woh apne hi product ke baare mein kuch nahi jaanta."),
    ("illegal", "Fake GST bill chahiye to contact karo, koi bhi amount."),
    ("adult", "Hot ladkiyan available hain, raat ki service, call karo."),
]

PRONOUNS = [dict(he="He", he_l="he", his="his", his_c="His", him="him"),
            dict(he="She", he_l="she", his="her", his_c="Her", him="her"),
            dict(he="They", he_l="they", his="their", his_c="Their", him="them")]

DISMISSIVE_NEUTRAL = ["absurd", "ridiculous", "laughable", "nonsense", "a joke", "utter rubbish",
                      "complete nonsense", "embarrassing", "hopeless", "a waste of time"]
DISMISSIVE_BLOCKED = ["pathetic", "worthless", "beyond stupid"]
DISMISSIVE = DISMISSIVE_NEUTRAL + DISMISSIVE_BLOCKED


def unsafe_sentence(cat=None):
    if cat is None and R.random() < .12:
        cat, tpl = pick(HINGLISH_UNSAFE)
    else:
        cat = cat or pick(list(UNSAFE))
        tpl = pick(UNSAFE[cat])
    pr = pick(PRONOUNS)
    amt = pick(AMOUNTS)
    text = tpl.format(amt=amt, amt2=pick(AMOUNTS), per=pick(PERIODS), pct=pick([10, 20, 30, 50, 100]),
                      d=pick([7, 10, 21, 30, 45]), state=pick(STATES), fee=pick(["Rs 999", "Rs 1,500", "Rs 2,500", "Rs 5,000", "500 rupees"]),
                      brand=pick(BRANDS), name=pick(FIRST), group=pick(GROUPS), co=company(), city=pick(CITIES), **pr,
                      **_slots())
    text = _sentence_case(text)
    if cat in ("scam_income", "counterfeit", "phishing", "registration_fee") and R.random() < .15:
        text = leetspeak(text, rate=.6)
    return text, cat


def _slots():
    return dict(
        pick_bad=pick(["Accidents", "Bad luck", "Fires", "Trouble", "Something unfortunate"]),
        pick_why=pick(["cross us", "complain about us", "don't pay on time", "talk to the police", "post bad reviews"]),
        pick_why2=pick(["keep complaining", "don't withdraw the case", "post one more review", "don't pay by Friday"]),
        pick_asset=pick(["shop", "godown", "factory", "office", "trucks", "warehouse"]),
        pick_fate=pick(["may not be standing next week", "could catch fire", "might not be safe", "will not survive the month"]),
        pick_family=pick(["family", "children", "wife", "parents", "kids"]),
        pick_live=pick(["live", "go to school", "stay", "work"]),
        pick_warn=pick(["Think carefully.", "Drop the complaint.", "Be careful.", "You have been warned."]),
        pick_ask=pick(["Read out", "Tell me", "Share", "Forward", "Type", "Enter", "Send"]),
        pick_code=pick(["code", "OTP", "verification code", "SMS", "six-digit number", "one-time password"]),
        pick_got=pick(["you just received", "on your screen", "we sent you", "that came to your phone"]),
        pick_secret=pick(["ATM PIN", "UPI PIN", "net banking password", "card number and CVV", "login password"]),
        pick_purpose=pick(["release your payment", "complete your KYC", "process the refund", "unblock your account", "verify you"]),
        pick_allowed=pick(["run businesses", "sell here", "open shops in our area", "join the association", "live in this colony"]),
        pick_community=pick(["community", "caste", "religion", "region", "state"]),
        pick_exclude=pick(["not be allowed to open shops here", "be thrown out of the market", "never be hired", "go back where they came from"]),
        dis=pick(DISMISSIVE),
    )


def _sentence_case(t):
    return t[0].upper() + t[1:] if t else t


# --------------------------------------------------------------------------- augmentation

LEET = {"a": ["4", "@"], "e": ["3"], "i": ["1", "!"], "o": ["0"], "s": ["5", "$"], "t": ["7"]}


def leetspeak(text, rate=.35):
    words = text.split(" ")
    out = []
    for w in words:
        if len(w) > 3 and R.random() < rate:
            w = "".join(pick(LEET[c.lower()]) if c.lower() in LEET and R.random() < .5 else c for c in w)
        out.append(w)
    return " ".join(out)


def spaced(text):
    words = text.split(" ")
    idx = [i for i, w in enumerate(words) if len(w) > 3]
    for i in R.sample(idx, k=min(len(idx), R.randint(1, 2))):
        words[i] = pick([" ", ".", "-", "*"]).join(words[i])
    return " ".join(words)


def noise(text):
    """Surface variation that must NOT change the label (applied to safe and unsafe alike)."""
    if R.random() < .25:
        text = bad_grammar(text)
    r = R.random()
    if r < .06:
        return text.upper()
    if r < .12:
        return text.lower()
    if r < .18:
        return text.rstrip(".") + pick(["!!", "!!!", " :)", " 🙏", " 👍", "."])
    if r < .22:  # typo
        i = R.randrange(max(1, len(text) - 1))
        return text[:i] + text[i + 1:]
    return text


def obfuscate(text):
    r = R.random()
    if r < .15:
        return leetspeak(text)
    if r < .22:
        return spaced(text)
    return text


# --------------------------------------------------------------------------- assembly

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def safe_post():
    """A safe text of any kind; returns (text, category)."""
    r = R.random()
    if r < .02:
        return fragment(), "fragment"
    if r < .12:
        return everyday()
    if r < .22:
        text, topic = article()
        if R.random() < .3:
            text += " " + " ".join(simple_sentence() for _ in range(R.randint(1, 2)))
        return text, f"article_{topic}"
    if r < .26:
        return title(), "title"
    if r < .32:
        return hinglish_safe(), "hinglish"
    if r < .42:
        return grammar_business_prose(), "grammar_business_prose"
    if r < .50:
        return b2b_negotiation(), "b2b_negotiation"
    if r < .58:
        return hard_negative_pricing_idiom(), "hard_negative_pricing_idiom"
    fn, cat = R.choices([(f, c) for f, c, _ in SAFE_GEN], weights=[w for *_, w in SAFE_GEN])[0]
    text = fn()
    if R.random() < .35 and cat != "hard_negative":
        more_fn, _ = R.choices([(f, c) for f, c, _ in SAFE_GEN], weights=[w for *_, w in SAFE_GEN])[0]
        text = f"{text} {more_fn()}"
    return text, cat


def _is_safe_clean(text: str) -> bool:
    """Ensure safe text does not contradict Layer 1 Gate or collide with held-out eval sets."""
    if not text or len(text.strip()) < 3:
        return False
    norm = re.sub(r"\s+", " ", text).strip().lower()
    if norm in _EVAL_KEYS:
        return False
    if _GATE is not None:
        try:
            res = _GATE.check(text)
            if res.blocked or res.needs_revision:
                return False
        except Exception:
            pass
    return True


def generate(n: int, seed: int, existing_rows: list[dict] | None = None):
    R.seed(seed)
    rows, seen = [], set()

    if existing_rows:
        for r in existing_rows:
            key = (r["text"].strip().lower(), int(r["label"]))
            seen.add(key)

    def add(text, label, cat, kind):
        text = re.sub(r"[ \t]+", " ", text).strip()
        key = (text.lower(), label)
        if text and key not in seen:
            if label == 0 and not _is_safe_clean(text):
                return False
            seen.add(key)
            rows.append({"text": text, "label": label, "category": cat, "kind": kind})
            return True
        return False

    target_count = n
    attempts = 0
    max_attempts = n * 4

    while len(rows) < target_count and attempts < max_attempts:
        attempts += 1
        r = R.random()
        if r < .46:                                    # safe, whole text (augmented with business & grammar)
            text, cat = safe_post()
            if R.random() < .05:
                text = leetspeak(text) if R.random() < .7 else spaced(text)
            add(noise(text), 0, cat, "title" if cat == "title" else "whole")
            if R.random() < .35:                       # individual sentences scored alone
                for s in SENT_SPLIT.split(text.replace("\n", " "))[:4]:
                    if len(s.split()) >= 2:
                        add(s, 0, cat, "sentence")
        elif r < .68:                                  # unsafe sentence on its own
            s, cat = unsafe_sentence()
            add(noise(obfuscate(s)), 1, cat, "sentence")
        elif r < .87:                                  # unsafe sentence hidden in a clean post
            clean, _ = safe_post()
            s, cat = unsafe_sentence()
            s = obfuscate(s)
            parts = SENT_SPLIT.split(clean)
            parts.insert(R.randint(0, len(parts)), s)
            mixed = " ".join(parts)
            add(bad_grammar(mixed) if R.random() < .20 else mixed, 1, cat, "mixed")
        else:                                          # several unsafe sentences together
            cat = pick(list(UNSAFE))
            s1, _ = unsafe_sentence(cat)
            s2, _ = unsafe_sentence(cat)
            add(noise(f"{s1} {s2}" if s1 != s2 else s1), 1, cat, "whole")

    R.shuffle(rows)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="D:/AI/bonc-data/synthetic_v4.csv")
    ap.add_argument("--in-file", default=None, help="existing CSV file to load when appending")
    ap.add_argument("--n", type=int, default=70_000, help="target rows to generate (if not appending)")
    ap.add_argument("--add-n", type=int, default=None, help="number of new rows to add when appending")
    ap.add_argument("--append", action="store_true", help="append generated rows to existing dataset")
    ap.add_argument("--copy-to", default=None, help="optional additional destination path to copy the CSV to")
    ap.add_argument("--seed", type=int, default=4)
    args = ap.parse_args()

    out = Path(args.out)
    existing_rows = []

    if args.append:
        in_path = Path(args.in_file if args.in_file else args.out)
        if in_path.exists():
            print(f"Loading existing rows from {in_path}...")
            with in_path.open(encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                existing_rows = list(reader)
            print(f"Loaded {len(existing_rows)} existing rows.")
        else:
            print(f"Input file {in_path} not found, generating from scratch.")
        num_to_generate = args.add_n if args.add_n is not None else args.n
        print(f"Generating {num_to_generate} new unique rows (grammar & business sense wordings)...")
        new_rows = generate(num_to_generate, args.seed, existing_rows=existing_rows)
        rows = existing_rows + new_rows
        random.Random(args.seed).shuffle(rows)
    else:
        print(f"Generating {args.n} rows from scratch with seed {args.seed}...")
        rows = generate(args.n, args.seed)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["text", "label", "category", "kind"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {out}")

    if args.copy_to:
        copy_dest = Path(args.copy_to)
        copy_dest.parent.mkdir(parents=True, exist_ok=True)
        with copy_dest.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["text", "label", "category", "kind"])
            w.writeheader()
            w.writerows(rows)
        print(f"Also copied {len(rows)} rows to {copy_dest}")

    from collections import Counter
    print("Labels distribution:", Counter(r["label"] for r in rows))
    print("Kinds distribution:", Counter(r["kind"] for r in rows))
    print("Top 15 categories:", Counter(r["category"] for r in rows).most_common(15))


if __name__ == "__main__":
    main()
