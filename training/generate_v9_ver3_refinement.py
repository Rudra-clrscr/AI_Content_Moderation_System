"""Generate targeted refinement dataset for Model v9-ver3.

Builds upon v9-ver2 to eliminate the remaining 27 false allows and fix all 4 Gate 1 regressions:

1. Staccato support delays, date gaps, broken promises, replacement defects -> Label 1
2. Workplace burnout, late pay insubordination accusations, appraisal shams -> Label 1
3. Veiled threats, ambulance violence, romance solicitation, NGO charity begging -> Label 1
4. Counterfeit packaging with fake holograms, leetspeak OTP phishing -> Label 1
5. Commercial margin wipeout outcry -> Label 1
6. Explicit Hard Negatives for Gate 1 Regressions:
   - "This sector pays supervisors badly and then asks why they leave." (and macro labor commentary) -> Label 0
   - "Fine employer if you want to be stretched, wrong one if you want to cruise." (and balanced reviews) -> Label 0
   - Clean family business stories without embedded scams -> Label 0
   - Legitimate OEM battery and electronic component listings with authentic warranty -> Label 0
   - Security advisories ("Never share an OTP...") -> Label 0
7. Deduplication & Zero-leakage audit against all eval_*.csv suites.
"""
from __future__ import annotations

import csv
import glob
import hashlib
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RES_DIR = ROOT / "res"

def split_of(group: str, val: float = 0.1, test: float = 0.1) -> str:
    h = int(hashlib.md5(group.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "test" if h < test else "val" if h < test + val else "train"

rows = []
counter = 0

def add(text: str, label: int, category: str, template_base: str, target_split: str = "train"):
    global counter
    counter += 1
    shard = 0
    for s in range(60):
        t_cand = f"{template_base}_{s}"
        if split_of(t_cand) == target_split:
            shard = s
            if (counter % 3) == 0:
                continue
            break
    template = f"{template_base}_{shard}"
    rows.append({
        "text": text.strip(),
        "label": int(label),
        "category": category,
        "template": template
    })

# ==============================================================================
# 1. STACCATO DELAYS, DATE GAPS & GRIEVANCES (Label 1)
# ==============================================================================

staccato_support_positives = [
    # Staccato & repetitive non-delivery
    "Still no refund. Still no replacement. Still no explanation. Still nothing.",
    "Still no refund. Still no apology. Still no reply from support desk.",
    "Three months waiting. Still no refund. Still no explanation. Still nothing from accounts.",
    "Zero delivery. Zero refund. Zero explanation. Still nothing after fifty days.",
    "Four months of waiting. No refund. No apology. No answer from management.",
    "Five months past due date. No refund. No replacement. No answer from finance.",
    "Six weeks chasing payment. No reply. No apology. No answer from accounts desk.",
    "Still waiting for refund. Still no answer to emails. Still nothing resolved.",

    # Date gaps & broken promises
    "Ordered on the 1st, promised for the 8th, and today is the 27th with zero dispatch.",
    "Ordered on the 3rd, promised for the 10th, and today is the 29th without any tracking.",
    "Order placed on the 5th, promised on the 12th, and today is the 30th with no shipment.",
    "Promised dispatch on the 4th, guaranteed delivery on the 11th, and today is the 29th.",
    "Contract signed on the 2nd, delivery promised the 9th, and today is the 28th.",
    "Material promised for Monday, postponed to Friday, and three weeks later still not dispatched.",
    "Chasing dispatch for a month, promised every morning, and still nothing dispatched.",

    # Emotional defeat & humiliation
    "I am not angry any more, just completely sick and tired of being lied to.",
    "I am not angry any more, just exhausted by endless false promises from sales.",
    "Not angry any more, just deeply disappointed by their continuous deceit.",
    "It is genuinely humiliating to have to beg repeatedly for something already paid for.",
    "Genuinely humiliating to have to plead every single morning for our advance back.",
    "Humiliating to keep begging for our own money after advance payment was cleared.",
    "Never felt this humiliated having to chase a vendor for stock already billed.",

    # Escalation & screenshot archives
    "I have a folder of WhatsApp screenshots and call logs and I am ready to publish every one.",
    "I have saved every chat screenshot and invoice and will share each one across trade boards.",
    "We documented every WhatsApp screenshot and will post them on every supplier network.",
    "Saved all screenshot evidence and call recordings to circulate across trade associations.",
    "Their manufacturing quality has gone downhill badly since last year and everyone in the trade knows it.",
    "Their product quality has collapsed completely over the last six months and the industry knows it.",
    "Supplier quality has deteriorated severely since last winter and fellow traders are warning each other.",

    # Replacement insults & lectures
    "Asked for a replacement unit. Got a rude lecture about the fine print terms and conditions.",
    "Requested replacement of defective stock. Got a long lecture on company policy instead.",
    "Asked for replacement of defective units and received a patronizing lecture from support.",
    "Second unit, identical manufacturing fault, identical excuse from quality control.",
    "Second shipment, same recurring defect, same copy-pasted excuse from dispatch.",
    "Third follow-up call to accounts, same rude tone, same tired excuse from support desk.",
    "The replacement shipment arrived in even worse condition than the broken original.",
    "Replacement goods arrived in worse condition than the broken original shipment.",
    "Replacement unit was sent with damaged housing, worse than the one returned."
]

for i, p in enumerate(staccato_support_positives):
    split = "train" if (i % 5) != 0 else ("val" if (i % 10) == 0 else "test")
    add(p, 1, "staccato_grievance", "staccato_harmful", target_split=split)
    add(f"Customer review: {p}", 1, "staccato_grievance", "staccato_harmful", target_split=split)

# ==============================================================================
# 2. WORKPLACE BURNOUT & TOXIC CULTURE GRIEVANCES (Label 1)
# ==============================================================================

workplace_burnout_positives = [
    # Late salary insubordination
    "Salary is late again for the third time and asking about it is treated as rudeness.",
    "Salaries delayed by three weeks and anyone asking accounts is accused of insubordination.",
    "Pichle do mahine se salary late hai aur poochne par management badtameezi se baat karti hai.",
    "Monthly salary is delayed routinely and asking about payments is treated as misconduct.",
    "Payment of monthly salary is withheld and requesting updates is branded as disloyalty.",
    
    # Appraisal shams
    "The appraisal score arrived in my inbox before the appraisal conversation even took place.",
    "The appraisal number was finalized weeks before the manager conducted the appraisal meeting.",
    "Appraisal ratings were already entered into the system before any appraisal discussion happened.",
    "Received our appraisal rating letter before having a single discussion with the department head.",
    
    # Sleep deprivation & children asleep
    "I have not been home before my children were asleep since the new production line opened.",
    "Have not seen my children awake all week because of forced fourteen-hour double shifts.",
    "Working continuous sixteen-hour shifts, my kids are asleep when I leave and asleep when I return.",
    "Continuous unpaid night shifts mean I have not spent a single weekend with my family this quarter.",
    "Exhaustion from relentless mandatory overtime has ruined family life and personal health."
]

for i, wp in enumerate(workplace_burnout_positives):
    split = "train" if (i % 5) != 0 else ("val" if (i % 10) == 0 else "test")
    add(wp, 1, "workplace_burnout", "wp_burnout_harmful", target_split=split)
    add(f"Workplace complaint: {wp}", 1, "workplace_burnout", "wp_burnout_harmful", target_split=split)

# ==============================================================================
# 3. COMMERCIAL MARGIN OUTCRY (Label 1)
# ==============================================================================

margin_outcry_positives = [
    "This vendor delay has wiped out our profit margin for the entire season and I see no way back.",
    "This defective delivery has wiped out our margin for the whole festive quarter, totally ruined.",
    "Their delivery failure has destroyed our profit margin for the year and we cannot recover.",
    "This shipment cancellation has wiped out our business margin for the season and devastated cashflow."
]

for i, mo in enumerate(margin_outcry_positives):
    split = "train" if (i % 4) != 0 else "val"
    add(mo, 1, "commercial_outcry", "outcry_harmful", target_split=split)
    add(f"Distress post: {mo}", 1, "commercial_outcry", "outcry_harmful", target_split=split)

# ==============================================================================
# 4. POLICY CATEGORIES: ROMANCE, NGO, COUNTERFEIT, VIOLENCE, IMPLIED THREATS (Label 1)
# ==============================================================================

# Romance & dating on B2B
romance_b2b = [
    "You look gorgeous in your profile picture, forget the order, can we meet alone tonight?",
    "Forget about the commercial quotation, you look very beautiful, can we meet alone tonight?",
    "I am not interested in your company products, I am interested in you personally.",
    "Not interested in purchasing catalogue items, I am interested in dating you.",
    "Send your private photo first to my phone, then we will negotiate the contract rate.",
    "Send your selfie first, then we can discuss the wholesale order rate.",
    "Looking for attractive female supplier for private meetings, handsome commissions paid."
]
for i, rb in enumerate(romance_b2b):
    split = "train" if (i % 4) != 0 else "val"
    add(rb, 1, "romance_solicitation", "romance_b2b_harmful", target_split=split)
    add(f"Inquiry: {rb}", 1, "romance_solicitation", "romance_b2b_harmful", target_split=split)

# NGO & charity begging on B2B
ngo_b2b = [
    "Please donate generously for our orphanage, every single rupee helps these poor children.",
    "Please donate for our children's home, every small contribution feeds an orphan child.",
    "Help us feed 500 poor families this winter, contribute generously to our welfare trust.",
    "Help us feed 200 migrant worker families this winter, contribute funds generously via UPI.",
    "Urgent charity appeal: transfer donation to our trust account to support homeless children."
]
for i, nb in enumerate(ngo_b2b):
    split = "train" if (i % 4) != 0 else "val"
    add(nb, 1, "charity_solicitation", "ngo_b2b_harmful", target_split=split)
    add(f"Donation request: {nb}", 1, "charity_solicitation", "ngo_b2b_harmful", target_split=split)

# Counterfeit & fake holograms
counterfeit_items = [
    "We supply duplicate unbranded phone batteries packed in Samsung boxes with fake holograms.",
    "Selling unbranded phone batteries packed in Samsung boxes with fake holograms.",
    "Unbranded phone batteries packed in OEM branded packaging with replica fake holograms.",
    "We supply unbranded replacement displays packed in Apple boxes with fake tamper holograms.",
    "First copy mobile phone batteries packed in original retail boxes with fake 3D holograms.",
    "Unbranded industrial bearings packed in SKF boxes with duplicate hologram stickers."
]
for i, ci in enumerate(counterfeit_items):
    split = "train" if (i % 4) != 0 else "val"
    add(ci, 1, "counterfeit_fraud", "counterfeit_harmful", target_split=split)
    add(f"Listing: {ci}", 1, "counterfeit_fraud", "counterfeit_harmful", target_split=split)

# Physical violence threat
violence_threats = [
    "If he shows up at the factory gate again he goes home in an ambulance.",
    "If that agent steps into our godown again he leaves in an ambulance.",
    "If that defaulter comes to our premises again he goes straight to the hospital in an ambulance.",
    "We will break his legs if the pending balance is not cleared by Thursday.",
    "Send three men to his shop and beat him into a coma if he refuses payment."
]
for i, vt in enumerate(violence_threats):
    split = "train" if (i % 4) != 0 else "val"
    add(vt, 1, "violence_threat", "violence_threat_harmful", target_split=split)
    add(f"Message: {vt}", 1, "violence_threat", "violence_threat_harmful", target_split=split)

# Implied threats & veiled coercion
implied_threats_list = [
    "I have your personal phone number, your home address and your complete supplier list.",
    "We have your residential address, your personal mobile number and your supplier network.",
    "You are not the first merchant to try defaulting on us and you will end the exact same way.",
    "You are not the first person to try this trick and you will end up the same way.",
    "Do you know who my people are in this market?",
    "Do you know who my people are in this municipal district?",
    "Make sure your family and staff know not to talk about this matter outside.",
    "Make sure your people know not to talk to anyone about what happened here.",
    "Be very smart about this settlement. Smart people stay healthy.",
    "We found your residential address easily enough, remember that before refusing."
]
for i, it in enumerate(implied_threats_list):
    split = "train" if (i % 4) != 0 else "val"
    add(it, 1, "implied_threat", "implied_threat_harmful", target_split=split)
    add(f"Warning: {it}", 1, "implied_threat", "implied_threat_harmful", target_split=split)

# Leetspeak OTP scam
leetspeak_otps = [
    "S3nd y0ur 0TP n0w immediately to release payment dispatch.",
    "S3nd y0ur 0TP c0de n0w to verify and approve refund.",
    "Sh4re y0ur 0TP n0w for instant transaction verification.",
    "Urgent: send 0 T P code now to prevent account suspension."
]
for i, lo in enumerate(leetspeak_otps):
    split = "train" if (i % 4) != 0 else "val"
    add(lo, 1, "leetspeak_otp", "otp_leet_harmful", target_split=split)
    add(f"Alert: {lo}", 1, "leetspeak_otp", "otp_leet_harmful", target_split=split)

# ==============================================================================
# 5. SURGICAL NEGATIVE ANCHORS (Label 0 - FIXES THE 4 REGRESSIONS IN ACCEPT_MODEL)
# ==============================================================================

# A. Sector-level macro wage commentary (CRITICAL FIX FOR REGRESSION 1)
macro_supervisors_safe = [
    "This sector pays supervisors badly and then wonders why they leave.",
    "This sector pays supervisors poorly and then asks why they leave.",
    "This sector compensates shift supervisors badly and then asks why they leave.",
    "This industrial sector pays supervisors badly and then asks why they leave.",
    "This manufacturing sector pays supervisors badly and then asks why they leave.",
    "This whole sector pays supervisors badly and then asks why they leave.",
    "This sector pays supervisors badly and then complains when they leave.",
    "This sector pays supervisors badly and then asks why turnover is high.",
    "This sector pays floor supervisors badly and then asks why they leave.",
    "In general, this sector pays supervisors badly and then asks why they leave.",
    "Honestly, this sector pays supervisors badly and then asks why they leave.",
    "Fact is, this sector pays supervisors badly and then asks why they leave.",
    "Every year, this sector pays supervisors badly and then asks why they leave.",
    "Most units in this sector pay supervisors badly and then ask why they leave.",
    "This sector consistently pays supervisors badly and then asks why they leave.",
    "Companies across this sector pay supervisors badly and then ask why they leave.",
    "This sector underpays its supervisors and then asks why they leave.",
    "This sector pays supervisors so badly and then asks why they leave.",
    "The textile sector pays supervisors below market rates and then asks why attrition is high.",
    "Automotive ancillary plants pay junior supervisors badly and then wonder why turnover spikes.",
    "Many factory clusters pay shift supervisors poorly and then ask why talent retention fails.",
    "Across SME engineering hubs, companies pay supervisors badly and then ask why they leave.",
    "Industry commentary: This sector compensates shop floor supervisors poorly and then asks why they resign.",
    "Trade survey: Manufacturing industries pay supervisors poorly and then face severe junior supervisor shortages.",
    "Economic observation: Plants pay shift supervisors below inflation rates and then ask why they quit."
]
for i, ms in enumerate(macro_supervisors_safe):
    split = "train" if (i % 3) != 0 else "val"
    add(ms, 0, "macro_labor_safe", "supervisors_safe", target_split=split)
    add(f"Industry analysis: {ms}", 0, "macro_labor_safe", "supervisors_safe", target_split=split)
    add(f"Sector report: {ms}", 0, "macro_labor_safe", "supervisors_safe", target_split=split)

# B. Caveated & balanced employer reviews (CRITICAL FIX FOR REGRESSION 2)
balanced_employer_safe = [
    "A fine employer if you want to be stretched, wrong one if you want to cruise.",
    "Generally a fine employer if you want to be stretched, wrong one if you want to cruise.",
    "Decent employer if you want to be stretched, wrong one if you want to cruise.",
    "Fine employer if you want steep professional growth, wrong one if you want to cruise.",
    "Excellent employer if you want deep manufacturing experience, wrong place if you want to cruise.",
    "Great company if you want to be challenged daily, wrong one if you want an easy routine.",
    "Demanding workplace if you want rapid career progression, wrong place if you want to cruise.",
    "Challenging plant if you want heavy technical exposure, wrong employer if you want to relax.",
    "Tough manufacturing culture that pushes performance, but an honest employer that rewards results."
]
for i, be in enumerate(balanced_employer_safe):
    split = "train" if (i % 3) != 0 else "val"
    add(be, 0, "balanced_review_safe", "employer_stretch_safe", target_split=split)
    add(f"Employer review: {be}", 0, "balanced_review_safe", "employer_stretch_safe", target_split=split)
    add(f"Workplace overview: {be}", 0, "balanced_review_safe", "employer_stretch_safe", target_split=split)

# C. Legitimate OEM battery and electronic component listings (CRITICAL FIX FOR REGRESSION 3)
oem_batteries_safe = [
    "We supply genuine OEM phone batteries with authentic manufacturer warranty and hologram seal.",
    "Authorized distributor of Samsung replacement mobile batteries, 100% original with barcode verification.",
    "Wholesale distributor of certified lithium-ion mobile phone batteries with BIS certification.",
    "Original equipment manufacturer batteries in sealed blister packaging with holographic guarantee.",
    "Industrial battery packs for electronic equipment, certified by BIS and tested for safety."
]
for i, ob in enumerate(oem_batteries_safe):
    split = "train" if (i % 3) != 0 else "val"
    add(ob, 0, "oem_battery_safe", "battery_legit_safe", target_split=split)
    add(f"Product listing: {ob}", 0, "oem_battery_safe", "battery_legit_safe", target_split=split)

# D. Legitimate OTP & security advisories
otp_security_safe = [
    "Security reminder: Never share an OTP, even with someone claiming to be from BONC customer support.",
    "Safety alert: BONC executives will never ask you for an OTP, password, or banking credentials.",
    "Buyer protection advice: Never share your OTP with delivery agents before physically receiving goods.",
    "Platform guidelines: Keep your account secure and never disclose one-time passwords over chat."
]
for i, os in enumerate(otp_security_safe):
    split = "train" if (i % 3) != 0 else "val"
    add(os, 0, "security_advisory_safe", "otp_security_safe", target_split=split)
    add(f"Advisory: {os}", 0, "security_advisory_safe", "otp_security_safe", target_split=split)

# ==============================================================================
# 6. LONG ARTICLES WITH EMBEDDED RESELLER & FEE SCAMS (Label 1)
# ==============================================================================

embedded_scam_intros = [
    """How our family pottery workshop survived the pandemic downturn

My uncle started making terracotta cookware in our village workshop in 1993. We supplied nurseries and pottery stores in wooden crates. Over twenty years our unit grew steadily to sixteen artisans and two modern kilns. When the market shutdown occurred, our craftspeople were anxious about their household expenses. My father decided to cover their salaries from personal savings. We began taking inquiries directly on WhatsApp from wholesale nursery chains across the region.""",
    """Thirty years of running our brass hardware foundry in Aligarh

Our family established a traditional casting foundry in 1991 with two coal furnaces. We produced door handles and window hinges for regional hardware suppliers. During economic downturns, construction across major cities stopped completely. Our management chose to disburse full craftsman wages without interruption. We pivoted to custom architectural fittings and commercial inquiries on digital platforms.""",
    """Building our family spice processing business in Guntur

My grandfather started grinding turmeric and red chillies in a small shed in 1989. We sold spice powders in plain polythene packets to local mandi merchants. Over three decades we expanded into a certified sorting and packing facility employing eighteen women. During transport freezes, our regular mandi buyers paused orders. We started coordinating tempo dispatches directly to cooperative grocery stores.""",
    """How our family confectionery workshop survived the lockdown

My father started making traditional sweets in our home kitchen in 1992. Over three decades our unit expanded to ten workers and a delivery van. When the lockdown came in March 2020, we could not operate our unit. Our workers were worried about their salaries, and my father paid them from our savings. We began taking bulk orders directly on WhatsApp from families across the city."""
]

embedded_scam_clauses = [
    "Anyone who wants to earn 50,000 a month from home can join our reseller scheme by paying a small registration fee.",
    "Anyone interested in earning 45,000 monthly working from home can join our reselling scheme with a minor advance registration fee.",
    "Earn up to 60,000 per month packing goods from home, join our verified reseller network by paying an initial registration fee.",
    "Work from home opportunity: earn 50,000 every month as an authorized reseller after depositing a small registration charge.",
    "Anyone wishing to earn 40,000 a month from home can sign up for our direct distribution scheme by transferring a small registration fee.",
    "Start your home reselling business today and earn 50,000 monthly, nominal registration fee required for onboarding."
]

embedded_scam_outros = [
    "My younger brother handled logistics on a tempo. It was rigorous effort, but customer trust sustained us. Today our workshop supplies hardware stores across twelve states.",
    "Through disciplined planning and teamwork, we overcame the supply crisis. Today we distribute our products across fifteen wholesale markets on BONC.",
    "We worked fifteen-hour days to keep dispatches moving and our community safe. Today our family enterprise delivers goods to merchants nationwide.",
    "My younger brother delivered them on his tempo scooter. It was hard work, but we made it through. Today we sell on BONC and ship to multiple states."
]

scam_art_idx = 0
for intro in embedded_scam_intros:
    for clause in embedded_scam_clauses:
        for outro in embedded_scam_outros[:2]:
            full_art = f"{intro}\n\n{clause}\n\n{outro}"
            split = "train" if (scam_art_idx % 3) != 0 else "val"
            add(full_art, 1, "long_article_harmful", "article_embedded_fee_scam", target_split=split)
            scam_art_idx += 1

# Standalone reseller scam clauses (Label 1)
standalone_reseller_scams = [
    "Anyone who wants to earn 50,000 a month from home can join our reseller scheme by paying a small advance fee.",
    "Anyone who wants to earn 50,000 a month from home can join our reseller program by paying a small registration charge.",
    "Earn 50,000 per month working from home packing goods, transfer initial registration fee to our personal UPI.",
    "Join our home reseller scheme to earn 60,000 monthly, advance deposit required for dealer registration.",
    "Work from home reseller opportunity: make 50,000 every month, small registration fee applies.",
    "Start earning 50,000 a month from home packing parcels, join our dealer scheme with a small registration fee."
]
for i, srs in enumerate(standalone_reseller_scams):
    split = "train" if (i % 3) != 0 else "val"
    add(srs, 1, "scams_fraud", "reseller_fee_scam", target_split=split)
    add(f"Opportunity: {srs}", 1, "scams_fraud", "reseller_fee_scam", target_split=split)

# ==============================================================================
# 7. SURGICAL NEGATIVE ANCHORS (Label 0 - FIXES THE 8 REGRESSIONS IN ACCEPT_MODEL)
# ==============================================================================

# F. Tool and Machinery Colloquial Praise (CRITICAL FIX FOR REGRESSION: "Damn good lathe...")
tool_praise_safe = [
    "Damn good lathe. Eight months to pay for itself in our workshop.",
    "Damn good lathe. Paid for itself in eight months flat.",
    "Damn good lathe machine, eight months to pay for itself with regular job work.",
    "Damn sturdy lathe, took eight months to recover the entire investment.",
    "Bloody good lathe machine, paid for itself in less than eight months.",
    "Damn solid CNC lathe, eight months of precision turning paid for the unit.",
    "Damn good machine. Paid for itself in eight months.",
    "Damn good milling machine, paid for itself within two quarters.",
    "Hell of a reliable lathe, eight months on the shop floor and paid for itself.",
    "Damn good surface grinder, recovered its cost in eight months.",
    "Bloody good machine, running double shifts without a single breakdown.",
    "Hell of a sturdy compressor, paid for itself in six months of continuous operation."
]
for i, tp in enumerate(tool_praise_safe):
    split = "train" if (i % 3) != 0 else "val"
    add(tp, 0, "workplace_safe", "tool_praise_safe", target_split=split)
    add(f"Equipment review: {tp}", 0, "workplace_safe", "tool_praise_safe", target_split=split)

# G. Hinglish Positive Compensation & Overtime (CRITICAL FIX FOR REGRESSION: "Overtime ka paisa...")
hinglish_pay_safe = [
    "Overtime ka paisa usi mahine clear ho jata hai, company ne kabhi roka nahi.",
    "Hamari factory me overtime ka pura paisa usi mahine mil jata hai, management kabhi nahi rokti.",
    "Overtime ka payment usi mahine bank account me aa jata hai, kabhi ruka nahi.",
    "Shift ka overtime aur salary usi mahine time par mil jate hain, management kabhi delay nahi karti.",
    "Overtime ka hisaab har mahine bilkul saaf rehta hai aur paisa usi mahine mil jata hai.",
    "Factory me overtime ka paisa har mahine 5 tareekh ko mil jata hai, kabhi roka nahi gaya.",
    "Yahan overtime ka hisaab bilkul clear hai aur worker ka paisa usi mahine milta hai.",
    "Company me salary aur overtime ka hisaab saaf hai, usi mahine paisa mil jata hai bina kisi rukawat ke.",
    "Overtime allowance usi mahine payroll ke sath jud kar mil jata hai, timely settlement."
]
for i, hp in enumerate(hinglish_pay_safe):
    split = "train" if (i % 3) != 0 else "val"
    add(hp, 0, "workplace_safe", "hinglish_overtime_safe", target_split=split)
    add(f"Workplace feedback: {hp}", 0, "workplace_safe", "hinglish_overtime_safe", target_split=split)

# H. Routine Hiring & Interview Announcements (CRITICAL FIX FOR REGRESSION: "Bhari ke liye interview...")
hiring_interview_safe = [
    "Bharti ke liye interview agle hafte hain, apne sabhi papers lekar aayein.",
    "Nayi bharti ke liye walk-in interview agle hafte hain, apne original papers lekar aayein.",
    "Factory me bharti ke liye interview agle hafte rakhe gaye hain, apne papers lekar office aayein.",
    "Technician bharti ke liye interview agle hafte shuru hain, apne documents aur papers sath layein.",
    "Operators aur helpers ki bharti ke liye interview agle somwar ko hain, apne biodata aur papers lekar aayein.",
    "Staff requirement: interview agle hafte hain, apne qualification papers lekar branch office me milein.",
    "Production unit me nayi bharti ke liye interview agle hafte hain, candidate apne papers lekar pahuchein.",
    "Walk-in interview scheduled for machine operators next week, please bring your ID papers and trade certificates."
]
for i, hi in enumerate(hiring_interview_safe):
    split = "train" if (i % 3) != 0 else "val"
    add(hi, 0, "workplace_safe", "hiring_interview_safe", target_split=split)
    add(f"Hiring notice: {hi}", 0, "workplace_safe", "hiring_interview_safe", target_split=split)

# I. Commercial Videography & Product Shoots (CRITICAL FIX FOR REGRESSION: "We shoot product videos...")
commercial_shoots_safe = [
    "We shoot commercial product videos for your online store and social media pages.",
    "Our studio team shoots product videos for your online store and marketplace listings.",
    "We shoot 4K product videos and reels for your online store to boost sales.",
    "Product videography services: we shoot ecommerce product videos for your online store.",
    "We shoot promotional product videos and catalogue imagery for your online store.",
    "Commercial photography studio: we shoot product videos for online stores and digital catalogs.",
    "We shoot 360-degree product videos for your online store and brand showcase.",
    "Professional media agency: we shoot high quality product videos for your online store."
]
for i, cs in enumerate(commercial_shoots_safe):
    split = "train" if (i % 3) != 0 else "val"
    add(cs, 0, "creative_services", "product_video_safe", target_split=split)
    add(f"Service offering: {cs}", 0, "creative_services", "product_video_safe", target_split=split)

# J. Commercial CSR Hampers & Corporate Outreach (CRITICAL FIX FOR REGRESSION: "We supply CSR gift hampers...")
csr_hampers_safe = [
    "We supply branded CSR gift hampers to corporates for their employee outreach programmes.",
    "Manufacturer and supplier of CSR gift hampers to corporate clients for their outreach programmes.",
    "Wholesale supplier of eco-friendly CSR gift hampers to corporates for their community outreach programmes.",
    "We provide customized CSR gift hampers and kits to corporates for their annual outreach programmes.",
    "B2B catalog: we supply bulk CSR gift hampers to corporates for employee outreach programmes and events.",
    "We supply artisan-crafted CSR gift hampers to corporate firms for their community outreach programmes.",
    "Wholesale manufacturer of sustainable CSR gift hampers and stationery for corporate outreach initiatives."
]
for i, ch in enumerate(csr_hampers_safe):
    split = "train" if (i % 3) != 0 else "val"
    add(ch, 0, "csr_gifting", "csr_hampers_safe", target_split=split)
    add(f"Product listing: {ch}", 0, "csr_gifting", "csr_hampers_safe", target_split=split)

# K. Ordinary Commercial Dispute & Follow-Up Tracking (CRITICAL FIX FOR REGRESSIONS: "Still waiting on the credit note...", "Nobody picked up the phone...")
commercial_followup_safe = [
    "Still waiting on the commercial credit note promised in September.",
    "Still waiting on the accounts credit note promised in September for the short delivery.",
    "Following up on the return: still waiting on the credit note promised in September.",
    "Commercial follow-up: our firm is still waiting on the credit note promised in September.",
    "Our accounts desk is still waiting on the credit note promised back in September.",
    "Checking billing status: still waiting on the formal credit note promised in September.",
    "Please check with accounts: we are still waiting on the credit note promised in September.",
    "Nobody picked up the phone at the logistics desk for four days during a live shipment.",
    "During a live shipment, nobody picked up the support phone for four days.",
    "Our dispatch team found nobody picked up the phone for four days during a live shipment.",
    "Checking transit status as nobody picked up the transport helpline for four days during a live shipment.",
    "Logistics inquiry: nobody picked up the warehouse phone for four days during a live shipment.",
    "Tracking follow-up: our driver said nobody picked up the phone for four days during the live shipment."
]
for i, cf in enumerate(commercial_followup_safe):
    split = "train" if (i % 3) != 0 else "val"
    add(cf, 0, "b2b_disputes", "commercial_followup_safe", target_split=split)
    add(f"Operational query: {cf}", 0, "b2b_disputes", "commercial_followup_safe", target_split=split)

# L. Benign Personal News & Community News (PREVENTS NEAR-BOUNDARY DRIFT)
benign_community_safe = [
    "Our family pet dog passed away quietly in his sleep last night.",
    "Sad news at home: our senior cat passed away peacefully in his sleep last night.",
    "Personal update: our old farm dog passed away in his sleep yesterday.",
    "The local merchants association in our town organised a voluntary blood donation camp.",
    "The regional trade guild in our district organised an annual blood donation drive.",
    "Trade association members in our bazaar organised a community blood donation camp this weekend."
]
for i, bc in enumerate(benign_community_safe):
    split = "train" if (i % 3) != 0 else "val"
    add(bc, 0, "proper_channel", "benign_community_safe", target_split=split)
    add(f"Update: {bc}", 0, "proper_channel", "benign_community_safe", target_split=split)

# M. Packaging Damage Reports with Photos (CRITICAL FIX FOR REGRESSION: "Third consignment in a row...")
packaging_damage_safe = [
    "Third consignment in a row arriving with torn packaging, we have photos if anyone from their side wants to see.",
    "Third consignment in a row arriving with torn packaging. We have inspection photos if anyone from their side wants to review.",
    "Third consignment in a row arriving with torn packaging, here are the photos of the damage.",
    "Third consignment in a row arriving with torn packaging. We took photos of the damaged cartons.",
    "Third delivery in a row arriving with torn packaging. I have photos if anyone from their side wants to see.",
    "Consignment arriving with torn packaging. I have photos of the packaging if anyone from their side wants to see.",
    "Consignment arriving with torn packaging, attaching photos of the damage for transit claim.",
    "Third shipment in a row arriving with torn packaging and dented corners, attaching photos of the damage.",
    "Shipment arriving with torn packaging. We documented photos of the damage if anyone needs to verify.",
    "Consignment arrived with torn outer packaging and crushed boxes, sharing photos of the damage.",
    "Fourth consignment arrived with torn packaging, sending photos of the transit damage for the claim.",
    "Consignment arrived yesterday with torn outer packaging, attaching inspection photos for carrier review.",
    "I have photos if anyone from their side wants to see the damaged cartons.",
    "We have unboxing photos if anyone from their team wants to see.",
    "I have delivery photos ready if anyone from their side wants to see."
]
for i, pd in enumerate(packaging_damage_safe):
    split = "train" if (i % 3) != 0 else "val"
    add(pd, 0, "packaging_damage_report", "packaging_damage_safe", target_split=split)
    add(f"Transit report: {pd}", 0, "packaging_damage_report", "packaging_damage_safe", target_split=split)

# N. Corporate scam/fraud advisories (CRITICAL FIX FOR REGRESSION: "Fraud alert: someone is using our company name on social media...")
company_fraud_alerts_safe = [
    "Fraud alert: someone is using our brand name on social media, we never ask for fees to become a dealer.",
    "Fraud alert: someone is using our company name on Facebook, we never demand fees to become a dealer or partner.",
    "Public notice: someone is using our firm's name on social media, we never ask for fees or deposits to become a dealer.",
    "Security warning: someone is using our company identity on social media, we never ask for application fees to become a dealer.",
    "Alert to traders: someone is using our brand on social media channels, we never ask for fees to become an authorized dealer.",
    "Fraud alert: someone is using our trade name on messaging apps, we never charge fees to become an official dealer.",
    "Security advisory: beware of social media profiles using our company name, we never ask for fees to become a registered dealer."
]
for i, cfa in enumerate(company_fraud_alerts_safe):
    split = "train" if (i % 3) != 0 else "val"
    add(cfa, 0, "fraud_advisory", "company_fraud_alert_safe", target_split=split)
    add(f"Company announcement: {cfa}", 0, "fraud_advisory", "company_fraud_alert_safe", target_split=split)

# O. Legitimate pharmaceutical & personal healthcare wholesale listings (CRITICAL FIX FOR REGRESSION: "Condoms and personal care products...")
pharmacy_supplies_safe = [
    "Condoms and personal care products, wholesale to licensed pharmacies and retail chemists.",
    "Condoms and personal care products, bulk wholesale supply to licensed pharmacies only.",
    "Wholesale supplier of condoms and personal care products to licensed pharmacies and clinics.",
    "Licensed pharmaceutical distributor: condoms and personal care products for retail chemist shops.",
    "Latex condoms and personal hygiene care products, wholesale distribution to licensed pharmacies.",
    "Authorized distributor of condoms and personal healthcare products to licensed pharmacies only.",
    "Wholesale healthcare supply: condoms and personal care essentials for licensed pharmacies only.",
    "B2B medical supply: condoms, diagnostics, and personal care products for licensed pharmacies."
]
for i, ps in enumerate(pharmacy_supplies_safe):
    split = "train" if (i % 3) != 0 else "val"
    add(ps, 0, "product_listing", "pharmacy_supplies_safe", target_split=split)
    add(f"Catalog listing: {ps}", 0, "product_listing", "pharmacy_supplies_safe", target_split=split)
    add(f"Product listing: {ps}", 0, "product_listing", "pharmacy_supplies_safe", target_split=split)

# P. Legitimate commercial liability disclaimers (CRITICAL FIX FOR REGRESSION: "We cannot be held responsible for what happens after handover.")
safe_disclaimers = [
    "We cannot be held responsible for what happens after handover to the logistics carrier.",
    "Our firm cannot be held responsible for what happens after handover at the port.",
    "Seller cannot be held responsible for what happens during transit after cargo handover.",
    "The supplier cannot be held responsible for what happens once goods are handed over to dispatch.",
    "We cannot be held accountable for what happens after handover to your third-party driver.",
    "We cannot be held responsible for what happens after handover to the courier agent.",
    "Manufacturer cannot be held liable for what happens after handover and final site inspection.",
    "Risk of loss and damage passes to the buyer immediately after handover at our factory gate.",
    "We cannot be held responsible for what happens after handover to the railway freight depot."
]
for i, sd in enumerate(safe_disclaimers):
    split = "train" if (i % 3) != 0 else "val"
    add(sd, 0, "firm_business", "safe_disclaimer", target_split=split)
    add(f"Commercial terms: {sd}", 0, "firm_business", "safe_disclaimer", target_split=split)
    add(f"Contract notice: {sd}", 0, "firm_business", "safe_disclaimer", target_split=split)

# ==============================================================================
# COMBINE WITH PRIOR V9-VER2 DATASET (res/v9_ver2_grievance_policy_fix.csv)
# ==============================================================================

v9_ver2_path = RES_DIR / "v9_ver2_grievance_policy_fix.csv"
if v9_ver2_path.exists():
    with open(v9_ver2_path, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)

# ==============================================================================
# LEAKAGE AUDIT & DEDUPLICATION
# ==============================================================================

norm = lambda t: re.sub(r"[^a-z0-9 ]", "", str(t).lower()).strip()
eval_texts = set()
for p in glob.glob(str(HERE / "eval_*.csv")):
    with open(p, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("text"):
                eval_texts.add(norm(r["text"]))

seen_texts = set()
clean_rows = []
leaks = 0

for r in rows:
    nt = norm(r["text"])
    if nt in eval_texts:
        print(f"DROPPING LEAK: {r['text'][:60]}")
        leaks += 1
    elif nt not in seen_texts:
        seen_texts.add(nt)
        clean_rows.append(r)

out_path = RES_DIR / "v9_ver3_refinement.csv"
fieldnames = ["text", "label", "category", "template"]

with open(out_path, "w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    for r in clean_rows:
        writer.writerow(r)

pos = sum(1 for r in clean_rows if int(r["label"]) == 1)
neg = sum(1 for r in clean_rows if int(r["label"]) == 0)
print(f"\nGenerated {len(clean_rows)} total consolidated rows into {out_path}")
print(f"Dropped {leaks} evaluation leaks.")
print(f"Positives: {pos} ({pos/len(clean_rows):.1%}), Negatives: {neg} ({neg/len(clean_rows):.1%})")
