"""The three things v6 got wrong, as training rows.

    python training/generate_v7_gaps.py --out res/v7_gaps.csv

v6 is not a worse model than v5 - implied_threats went 17.5% -> 59.2% caught and
public_callouts 5.1% -> 59.0%. But training/compare_text_models.py found three specific
failures, and each one is a different kind of problem, so each needs a different fix.

1. THREE HARMFUL TEXTS v5 CAUGHT AND v6 PUBLISHES.

       expired medicines repacked with fresh dates   0.918 -> 0.018
       refundable deposit, guaranteed orders         0.999 -> 0.001
       pay or we take the value in goods             0.999 -> 0.003

   Verified end to end: all three return `allow` today, and no gate rule covers them.

   The data is not the cause for the second one - "guaranteed orders" appears 2,144 times in
   v6's training set and every single row is label 1. v6 was taught that genre and lost it
   anyway, which is what adding 400,000 callout and implied-threat rows to a 1.26M row set
   does to a boundary. So these families are rebuilt here from several angles rather than
   restored verbatim: a model that only knows the one phrasing will lose it again.

   Adulterated goods genuinely is missing: "repacked" appears twice in 1.26M rows, both label
   0, both a bakery listing.

2. ONE PRONOUN DECIDES. "I have called their customer support six times and nobody helps"
   scores 0.994 on v6 and 0.001 without "their". v5 scores both 0.000.

   Not mysterious: rows containing "their " are 68.5% label 1 against a 38.8% base rate. The
   callouts generator leans on "their" because that is how a callout names its target, and the
   model took the shortcut. This file carries MINIMAL PAIRS - the same sentence, same label,
   different pronoun - on both sides, so the pronoun predicts nothing on its own.
   build_v7_data.py does the same to a sample of the existing rows, which is where the volume
   comes from; these are the clean, deliberate cases.

3. THE NEGATIVES. Every family here has a counterpart in the same vocabulary: a lawful
   security deposit, a pharma trader whose batch dates are the point, a firm stating its
   recovery terms. Without them this file teaches that "deposit" and "expiry" are refusals,
   and the next round's regression is a false positive instead of a false negative.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

GOODS = ["medicines", "tablets", "syrups", "baby food", "cooking oil", "spices", "cosmetics",
         "surgical gloves", "protein powder", "milk powder", "sanitiser", "eye drops"]
PARTS = ["bearings", "brake pads", "cables", "filters", "valves", "switchgear", "fasteners"]
MONEY = ["Rs 7,500", "Rs 1,500", "Rs 25,000", "Rs 50,000", "Rs 2 lakh", "Rs 10,000"]
PERIOD = ["every month", "monthly", "each quarter", "from day one", "within 30 days"]
# Deadline-shaped only: every frame below says "by {day}" or "due {day}", and "pay by
# three days" is not something a person writes.
DAYS = ["Friday", "Monday", "the 10th", "month end", "the 15th", "next Tuesday",
        "this weekend", "the 25th"]
PLACES = ["Surat", "Ludhiana", "Tiruppur", "Rajkot", "Noida", "Pune", "Indore", "Hosur",
          "Coimbatore", "Jaipur", "Agra", "Kanpur", "Nashik", "Bhiwandi", "Panipat"]
# Volume for the two bare families below comes from slots, not from composition. They are
# refused BECAUSE they are short, so wrapping them in context would fix a row that was never
# the problem. Varying the name and the noun keeps them bare and still gives the model
# thousands of distinct examples instead of twenty-six.
BRANDS = ["shreetextiles", "mahalaxmisteel", "arihantpolymers", "sunrisetraders", "jkfasteners",
          "balajiexports", "nationalpipes", "royalfabrics", "ganeshengg", "omsaicables",
          "kiranplastics", "vishwakarmatools", "sagarchemicals", "newindiaprints",
          "deepakspices", "rathitimber", "jyotielectricals", "surajpackaging"]
GOODS_SHORT = ["maal", "stock", "material", "order", "samaan", "lot", "consignment"]

# ---- family 1: adulterated, expired and relabelled goods (label 1) -------------------
ADULTERATED = [
    "Expired {goods} repacked with fresh dates, very cheap for bulk buyers.",
    "Old stock {goods} relabelled with new expiry, moving fast at half price.",
    "We can print any batch date you want on the {goods} cartons.",
    "Near-expiry {goods} available, labels can be changed before dispatch.",
    "{goods_cap} past date but perfectly fine, we reprint the strip before shipping.",
    "Seized stock of {goods}, no bills, dates adjusted on request.",
    "Rejected export consignment of {goods}, repacked and ready for the local market.",
    "Second quality {parts} stamped as original, buyer will not know the difference.",
    "Duplicate {parts} with original branding, same packaging, one third the price.",
    "We supply {goods} without batch numbers so nothing can be traced back.",
    "Factory reject {parts} polished and sold as new, bulk rates available.",
    "Recalled {goods} bought cheap, we can re-sticker and supply anywhere in India.",
    "Mixing cheaper oil into the {goods} keeps the cost down and nobody tests it.",
    "Our {goods} are not licensed but we can arrange paperwork that looks right.",
]

# ---- family 2: advance fee and guaranteed-return schemes (label 1) ------------------
ADVANCE_FEE = [
    "Register now with a refundable deposit of {money} and get guaranteed orders {period}.",
    "Pay {money} for the territory rights and we guarantee business {period}.",
    "Deposit {money} today, guaranteed monthly orders, limited distributorships left.",
    "Franchise fee {money} only, assured returns {period}, no risk at all.",
    "Pay the registration of {money} and your firm is empanelled with the department.",
    "One-time security of {money} and we guarantee a fixed income {period}.",
    "Send {money} as processing charges and the export order will be released to you.",
    "Agency available against a deposit of {money}, guaranteed buyback of all stock.",
    "Invest {money} in our supply chain scheme and double it {period}.",
    "Membership of {money} gets you orders from our panel, assured, in writing.",
    "Pay {money} now to lock the rate; the refund is automatic if you are not satisfied.",
    "Guaranteed tender allotment after a facilitation payment of {money}.",
]

# ---- family 3: collection threats and extortion (label 1) ---------------------------
# v6 already catches "we drive past your unit every day". These are the money-recovery
# register, which it does not: a demand with a consequence attached.
COLLECTION = [
    "Pay, or we come and take the value in goods.",
    "Clear the dues by {day} or we lift stock worth the amount from your godown.",
    "We will recover it from your warehouse if the payment does not come by {day}.",
    "Settle it or our people will collect in kind from your {place} unit.",
    "The amount is due {day}; after that we do not ask politely.",
    "You can pay now or explain it to the men we send.",
    "We have your address in {place}. Pay by {day}.",
    "Either the money arrives by {day} or your stock does not leave the yard.",
    "We will hold your consignment until the old dues are cleared, one way or another.",
    "Our recovery team is in {place} next week. Keep the cash ready.",
    "Pay {money} by {day} or we take it out of the goods lying with the transporter.",
    "No more notices. Next time we come to the factory.",
]

# ---- family 4: credential and OTP phishing (label 1) --------------------------------
# regression_check.py found this one, not me: v5 scores "send me otp fast otherwise your
# account will closed" at 0.950 and v6 at 0.159. Broken-grammar phishing is its own register
# and v6 lost it, so it is rebuilt here across several phrasings rather than the one.
PHISHING = [
    "send me otp fast otherwise your account will closed",
    "Share the OTP you just received or the order will be cancelled.",
    "Tell me the 6 digit code sent on your number to confirm the payment.",
    "Your account will be blocked today, send the verification code immediately.",
    "KYC pending, share your PAN and the OTP to keep the account active.",
    "I am calling from the payment team, read out the code on your phone.",
    "Forward the SMS code to complete the refund of {money}.",
    "Your GST registration will be cancelled, verify by sending the OTP now.",
    "Login on this link and confirm your bank details to release the payment.",
    "Send your UPI PIN to receive the advance for the order.",
    "Update your bank details here or the invoice amount will be returned.",
    "Account suspend ho jayega, OTP abhi bhejiye.",
    "Verification failed, share the code again quickly before it expires.",
    "Click the link and re-enter your card number to confirm the order.",
]

# ---- family 5: what v6 started refusing - Hinglish satisfaction and bare contacts ----
# Also from regression_check.py. These are label 0: v6 refuses them and should not.
#   "Aapke yahan se maal mangwa kar dekha, theek thaak nikla."  0.000 -> 0.997
#   "visit shreetextiles.co.in for details"                     0.170 -> 0.595
HINGLISH_OK = [
    "Aapke yahan se {gs} mangwa kar dekha, theek thaak nikla.",
    "{gs_cap} time par mil gaya, quality bhi acchi thi.",
    "Inse {gs} order kiya tha, koi dikkat nahi hui.",
    "{place_cap} se {gs} mangwaya, delivery theek rahi.",
    "Rate thoda zyada hai par {gs} sahi hai.",
    "Dobara {gs} order karenge, experience acha raha.",
    "{gs_cap} dekh kar order kiya, waise hi nikla.",
    "Aapke yahan se maal mangwa kar dekha, theek thaak nikla.",
    "Maal time par mil gaya, quality bhi acchi thi.",
    "Inse order kiya tha, koi dikkat nahi hui.",
    "Rate thoda zyada hai par material sahi hai.",
    "Humne {place} se mangwaya, delivery theek rahi.",
    "Packing acchi thi, {parts} bilkul sahi haalat mein mile.",
    "Dobara order karenge, experience acha raha.",
    "Payment terms clear the, koi confusion nahi.",
    "Sample dekh kar order kiya, waise hi nikla.",
    "Bhai log, inka service theek hai, try kar sakte ho.",
    "Thoda late hua par inform kar diya tha unhone.",
    "{place_cap} wale supplier se acha response mila.",
]

CONTACT_FRAGMENTS = [
    "visit {brand}.co.in for details",
    "see www.{brand}.in for the catalogue",
    "{brand}.com has the full range",
    "rate list on {brand}.co.in",
    "email sales at {brand} dot com",
    "visit shreetextiles.co.in for details",
    "see www.{place}traders.in for the catalogue",
    "details on our website",
    "catalogue link in bio",
    "write to sales for the rate list",
    "more on our site",
    "full range at the {place} showroom",
    "contact number on the profile",
    "rates updated on the website every Monday",
    "brochure available, ask on chat",
    "address and timings on our page",
    "find us on the BONC directory",
]

# ---- family 6: the three the first v6r retrain still lost ---------------------------
# accept_model.py on the retrained bundle: 4 regressions, one of which the pipeline's sentence
# scan catches anyway. These are the three that genuinely publish.
#
#   Seedha mere account mein paisa daalo, platform ka chakkar chhodo.   0.992 -> 0.002
#   Be a pity if your godown had an accident.                          0.982 -> 0.146
#   Do not involve police, it will be worse for you.                   0.564 -> 0.009
#
# The Hinglish one is my own doing: HINGLISH_OK above put bare Hinglish on the safe side with
# nothing harmful in the same register to balance it, so the model learned the language again -
# the identical mistake the grievance model made, made in the other direction.
# DEFERRED, both Hinglish families, by decision on 2026-10-17: Hinglish is being trained
# separately later. The frames stay here because they are what that round starts from, and
# because leaving them out of the file would lose the reason.
#
# What deferring costs, exactly - these two pull against each other and dropping both leaves
# the second one broken against v5:
#
#   Aapke yahan se maal mangwa kar dekha, theek thaak nikla.   v5 publishes, v6 refuses
#   Seedha mere account mein paisa daalo, platform ka chakkar  v5 refuses, v6r publishes
#
# HINGLISH_OK fixes the first and causes the second, by teaching the language instead of the
# genre. Both are real regressions against v5 and both are now out of scope rather than fixed,
# which is a choice that should be visible rather than discovered later.
HINGLISH_HARMFUL = [
    "Seedha mere account mein paisa daalo, platform ka chakkar chhodo.",
    "Platform ko bypass karke direct payment kar dijiye, sasta padega.",
    "Bill mat banwao, cash mein de do, {money} bach jayega.",
    "Mere personal UPI par bhej do, company account mein mat daalo.",
    "GST ka chakkar chhodo, kacha bill bana denge.",
    "Advance mere account mein, baaki delivery par - platform ko mat batana.",
    "Yahan baat mat karo, WhatsApp par number de raha hoon.",
    "Portal se hat kar deal karenge to dono ka commission bachega.",
    "Paisa pehle bhejo warna order cancel ho jayega.",
    "Apna OTP bata do, verification ke liye chahiye.",
]

# The veiled register: a threat with no verb of violence in it. "Be a pity if your godown had
# an accident" is a sentence about misfortune, and that is exactly how it works.
VEILED_THREATS = [
    "Be a pity if your godown had an accident.",
    "Would be a shame if something happened to that consignment.",
    "Accidents do happen to warehouses in {place} this time of year.",
    "Your loading bay looks like it could catch fire easily.",
    "Nice shop. Would be sad if customers stopped coming.",
    "Hope nothing happens to your truck on the {place} highway.",
    "Insurance covers fire, does it? Good to know.",
    "Do not involve police, it will be worse for you.",
    "Keep the lawyers out of this if you know what is good for you.",
    "Going to the authorities would be a mistake you regret.",
    "Nobody needs to know about this conversation. For your own sake.",
    "Settle it quietly or it stops being quiet.",
    "It would be unfortunate if your buyers heard certain things.",
    "Your family lives above the shop, no? Long hours for them.",
    "Think about what matters to you before you refuse again.",
    "I would not want your name coming up in the wrong conversation.",
]

# ---- the negatives, in the same vocabulary (label 0) --------------------------------
LAWFUL = [
    # deposits and fees that are ordinary trade
    "A refundable security deposit of {money} applies and is returned on contract closure.",
    "The distributorship has no joining fee; margins are on invoice.",
    "Our franchise discloses all costs up front and guarantees nothing about volume.",
    "Registration on the portal is free; empanelment is decided by the department.",
    "We hold {money} as retention money, released after the defect liability period.",
    "Advance is 30% against a bank guarantee of the same value.",
    "The deposit is held in escrow and refunded if the order is cancelled.",
    # pharma and food trade where dates are the whole point
    "WHO-GMP certified {goods} with batch numbers and expiry printed on every strip.",
    "We do not buy near-expiry {goods}; our minimum shelf life is nine months.",
    "All {goods} are shipped with the manufacturer's certificate of analysis.",
    "Any stock within six months of expiry is returned to the principal, not sold.",
    "Cold chain maintained end to end for {goods}; data loggers in every carton.",
    "Batch recall procedure: we notify every buyer within 24 hours and collect the stock.",
    "Our {parts} are genuine OEM with warranty cards and traceable part numbers.",
    "We stock {parts} in {place} with GST invoice and test certificates.",
    # recovery and dues, stated lawfully
    "Overdue invoices carry interest at 18% per annum as per the contract.",
    "If dues remain unpaid by {day} we will refer the matter to arbitration.",
    "Our legal team will issue a notice under the contract if payment is not received.",
    "We reserve a lien on goods until payment, as stated in the terms of sale.",
    "Recovery is pursued through the agreed dispute resolution process, not otherwise.",
    "Payment terms remain 30 days net; reminders go out on day 31.",
    "We have never had to escalate a payment dispute beyond a reminder.",
    "Credit limits are reviewed quarterly and shared with the buyer in writing.",
    "Disputed invoices are parked, not pursued, until the query is closed.",
    "We publish our standard terms so there are no surprises at payment time.",
    "Every consignment ships with a packing list the buyer signs on receipt.",
    "Returns of unopened {goods} are accepted within 14 days of delivery.",
    "Our {place} warehouse is open for buyer inspection by appointment.",
    "Third-party lab reports for {goods} are available on request.",
    "Spot checks on incoming {parts} are logged and retained for two years.",
    "We decline orders we cannot fulfil rather than substitute the brand.",
    "Insurance covers transit damage; claims are settled with the transporter.",
]

# ---- minimal pronoun pairs: same sentence, same label, different pronoun -------------
# Written as a template with {p}, filled with each pronoun in turn. The point is that the
# pronoun carries no information: both members of a pair get the SAME label.
PRONOUNS = ["their", "your", "its", "the firm's", "the supplier's"]

PRONOUN_SAFE = [
    "I have called {p} customer support six times and nobody helps.",
    "We are still waiting for {p} confirmation on the rate revision.",
    "{p_cap} delivery team arrived on time and the paperwork was in order.",
    "Please share {p} GST details so we can raise the invoice.",
    "{p_cap} quotation is competitive and the lead time works for us.",
    "We have asked {p} account manager to review the pricing.",
    "{p_cap} warranty covers manufacturing defects for twelve months.",
    "The consignment is with {p} transporter and should arrive on Tuesday.",
    "We are reviewing {p} proposal with our technical team this week.",
    "{p_cap} support desk closed the ticket the same afternoon.",
]

PRONOUN_UNSAFE = [
    "Post this everywhere so {p} reputation is finished in this market.",
    "We will make sure {p} name is dragged through every group until they pay.",
    "Somebody should teach {p} people a lesson they will remember.",
    "Share {p} home address here so members know where to find them.",
    "We will ruin {p} business whatever it takes.",
]


# Composition, for the same reason generate_support_complaints.py needs it: fourteen frames
# over a dozen slots is 671 distinct rows, and 671 rows will not move a boundary in a 1.26M
# row training set. An opener and a tail drawn independently put the genre in the middle of a
# real posting, which is also where it appears in life - a scam is rarely a bare sentence.
TRADE_OPENERS = ["", "", "",
                 "Wholesale enquiry.", "Dealers welcome.", "Bulk rates on request.",
                 "Direct from the unit in {place}.", "Stock ready for dispatch.",
                 "Serious buyers only.", "Limited quantity available.",
                 "We supply across India.", "Established since 2011.",
                 "Message for the rate list.", "Trade enquiry from {place}.",
                 "Clearance lot.", "Regular supply possible."]
TRADE_TAILS = ["", "", "",
               "Contact on WhatsApp.", "Payment in advance.", "Transport extra.",
               "Rates negotiable for volume.", "Delivery across {place} and nearby.",
               "Ask for the current list.", "First come first served.",
               "No returns after dispatch.", "Minimum order applies.",
               "Call between 10 and 6.", "Samples can be arranged."]
LAWFUL_OPENERS = ["", "", "",
                  "For buyers comparing suppliers:", "Our standard terms, since it came up:",
                  "A note on how we work.", "Compliance summary.",
                  "From our quality team:", "Posting this because it is asked often.",
                  "Supply terms, in short:", "For the record:",
                  "Our process, briefly.", "Details for new buyers:"]
LAWFUL_TAILS = ["", "", "",
                "Documentation on request.", "Happy to share the SOP.",
                "Audited annually.", "Certificates available with every lot.",
                "Terms are in the contract annexure.", "Ask us if anything is unclear.",
                "This is standard across our lanes.", "Numbers can be verified.",
                "Nothing unusual, but worth stating."]


def fill(rng: random.Random, frame: str, **extra) -> str:
    goods, parts, place = rng.choice(GOODS), rng.choice(PARTS), rng.choice(PLACES)
    gs = rng.choice(GOODS_SHORT)
    return frame.format(brand=rng.choice(BRANDS), gs=gs, gs_cap=gs[0].upper() + gs[1:],goods=goods, goods_cap=goods[0].upper() + goods[1:],
                        parts=parts, money=rng.choice(MONEY), period=rng.choice(PERIOD),
                        day=rng.choice(DAYS), place=place,
                        place_cap=place[0].upper() + place[1:], **extra)


def compose(rng: random.Random, core: str, openers, tails) -> tuple[str, str]:
    """Text plus the SKELETON of the three frames, which goes in `template`. prepare_data.py
    and build_v7_data.py both group on it, so a row and its siblings cannot land on opposite
    sides of a split - the mistake that let v6 report 99.66% accuracy while the hand-written
    sets said 76%."""
    opener, tail = rng.choice(openers), rng.choice(tails)
    parts = [fill(rng, p) for p in (opener, core, tail) if p]
    return " ".join(parts), " | ".join(p for p in (opener, core, tail) if p)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "res" / "v7_gaps.csv")
    ap.add_argument("--per-family", type=int, default=9_000)
    ap.add_argument("--negatives", type=int, default=20_000)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    rows, seen = [], set()

    def emit(frames, count, label, category, openers, tails):
        made = guard = 0
        while made < count and guard < count * 80:
            guard += 1
            text, skeleton = compose(rng, rng.choice(frames), openers, tails)
            if text.lower() in seen:
                continue
            seen.add(text.lower())
            rows.append({"text": text, "label": label, "category": category,
                         "kind": "whole", "template": skeleton})
            made += 1
        return made

    counts = {
        "adulterated_goods": emit(ADULTERATED, args.per_family, 1, "adulterated_goods",
                                  TRADE_OPENERS, TRADE_TAILS),
        "advance_fee": emit(ADVANCE_FEE, args.per_family, 1, "advance_fee",
                            TRADE_OPENERS, TRADE_TAILS),
        "collection_threat": emit(COLLECTION, args.per_family, 1, "collection_threat",
                                  TRADE_OPENERS, TRADE_TAILS),
        # The lawful rows take their own openers and tails. Sharing the harmful pool would
        # teach the opener, not the offence: "Clearance lot." would become a refusal.
        "phishing": emit(PHISHING, args.per_family, 1, "phishing",
                         TRADE_OPENERS, TRADE_TAILS),
        "veiled_threat": emit(VEILED_THREATS, args.per_family, 1, "veiled_threat",
                              TRADE_OPENERS, TRADE_TAILS),
        "lawful_counterpart": emit(LAWFUL, args.negatives, 0, "lawful_counterpart",
                                   LAWFUL_OPENERS, LAWFUL_TAILS),
        # Short by design: these two families are short texts and composing them with openers
        # would destroy the very thing being fixed. "visit shreetextiles.co.in for details" is
        # refused BECAUSE it is bare; wrapping it in three sentences of context makes it a
        # different row that was never the problem.
        "contact_fragment": emit(CONTACT_FRAGMENTS, args.per_family // 2, 0,
                                 "contact_fragment", [""], [""]),
    }

    # Minimal pairs. Every pronoun of every frame, so the set is exactly balanced: whatever
    # the pronoun is, the label distribution over it is the file's distribution. A sample
    # would not guarantee that, and the whole point is that the correlation is zero.
    pairs = 0
    for frames, label in ((PRONOUN_SAFE, 0), (PRONOUN_UNSAFE, 1)):
        for frame in frames:
            for p in PRONOUNS:
                text = fill(rng, frame, p=p, p_cap=p[0].upper() + p[1:])
                if text.lower() in seen:
                    continue
                seen.add(text.lower())
                rows.append({"text": text, "label": label, "category": "pronoun_pair",
                             "kind": "whole", "template": "pronoun:" + frame})
                pairs += 1
    counts["pronoun_pair"] = pairs

    rng.shuffle(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["text", "label", "category", "kind", "template"])
        w.writeheader()
        w.writerows(rows)

    print(f"{args.out}: {len(rows):,} rows")
    for k, v in counts.items():
        print(f"  {v:>7,}  {k}")
    print(f"  {len(set(r['template'] for r in rows)):,} distinct templates")

    # hinglish_ok and contact_fragment are deliberately uncomposed, so their ceiling is the
    # frame list and a short count there is the design, not a defect.
    expected = {"lawful_counterpart": args.negatives, "hinglish_ok": 0, "contact_fragment": 0,
                "pronoun_pair": 0}
    short = [(k, v) for k, v in counts.items()
             if v < expected.get(k, args.per_family)]
    for k, v in short:
        print(f"\n  WARNING: {k} produced {v:,} rows, short of the target. Add frames rather "
              f"than accepting near-duplicates.", file=sys.stderr)
    return 1 if short else 0


if __name__ == "__main__":
    raise SystemExit(main())
