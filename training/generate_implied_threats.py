"""Training rows for coercion that never names the harm.

    python training/generate_implied_threats.py --out res/implied_threats.csv

v5 catches 18% of `eval_implied_threats.csv`. It knows the threats that name the harm ("we will
burn your shop down") and is blind to the ones that only gesture at it - which on a B2B
marketplace is most of them. The training set has no coverage: of six implied-threat phrasings
searched for across a million rows, only "we know where" appeared at all, and that is precisely
the one phrasing the model does catch.

Two things this generator does deliberately.

**The menace sits inside an ordinary message.** A threat does not arrive as a bare sentence; it
arrives in the fourth line of a note about an invoice. Wrapping each one in realistic B2B
chatter is how the sentence scan learns to find it, and stops the model keying on the fact that
short blunt sentences are suspicious.

**Half the output is hard negatives, in the same words.** Commercial language is full of
consequence, deadline and knowing-where-you-are - "failure to deliver triggers the penalty
clause", "our warehouse address is on the invoice" - and a model taught that implication means
menace would refuse the legitimate half of every dispute on the platform. The negatives reuse
the threats' vocabulary on purpose, so the distinction has to be learned rather than matched.

Rows that collide with `training/eval_implied_threats.csv` are dropped: that set is held out,
and a leak would make the next round's number meaningless.
"""
from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---- the pieces ---------------------------------------------------------------------

PREMISES = ["your warehouse", "your godown", "your shop", "your office", "your factory",
            "your unit", "your showroom", "your cold storage", "your loading bay",
            "your yard", "your workshop", "your depot"]
MOVABLES = ["your truck", "your consignment", "your stock", "your containers", "your goods",
            "your delivery van", "your machinery", "your shipment", "your inventory"]
# Family is a threat target and nothing else: "your daughter will sign for the delivery" is not
# a sentence anybody writes, and putting it in the safe half teaches the model a shape that
# never occurs. Staff appear on both sides, because they really do sign for deliveries.
FAMILY = ["your family", "your son", "your daughter", "your brother", "your wife"]
STAFF = ["your staff", "your drivers", "your watchman", "your loading crew",
         "your storekeeper", "your site supervisor"]
PEOPLE = FAMILY + STAFF
# Frames that need singular agreement get their own list: "your goods is held up" is wrong, and
# a generator that writes ungrammatical text is teaching the model that bad grammar is a signal.
ONE_MOVABLE = ["your truck", "your consignment", "your shipment", "your delivery van",
               "your container", "your stock", "your cargo", "your machinery"]
PLACES = ["Bhiwandi", "Surat", "Andheri", "Sector 8", "the highway", "the port",
          "the checkpoint", "Peenya", "Okhla", "Ludhiana", "Tirupur", "Coimbatore"]

# Each frame is menace without a named harm.
THREAT_FRAMES = [
    "We know exactly where {premise} is.",
    "We have the address of {premise}, in case that matters.",
    "Somebody drove past {premise} yesterday.",
    "Nice {premise_bare} you have. Would be a pity.",
    "{premise_cap} is easier to find than you think.",
    "It would be a shame if something happened to {movable}.",
    "I hope {one} is insured.",
    "Things do go missing at {place}.",
    "{one_cap} sits unguarded at night, does it not.",
    "Hope nothing happens to {movable} on {place}.",
    "You should think about {people} before deciding.",
    "{people_cap} should be careful for a while.",
    "Tell {people} to keep their heads down.",
    "We know the route {people} take every morning.",
    "Stop {action} or face what comes next.",
    "Withdraw from {thing} or there will be consequences.",
    "Keep {action} and see what happens.",
    "Carry on with {thing} and you will find out.",
    "Go ahead with {thing}, and do not say you were not told.",
    "There are consequences for people who do {thing}.",
    "You have {hours} hours before this stops being a conversation.",
    "Settle by {day} or we do it our own way.",
    "After {day} it is out of my hands.",
    "This is the only warning you will get about {thing}.",
    "Last chance to end {thing} quietly.",
    "People who cross us do not last long in {place}.",
    "The last firm that tried {thing} is not trading any more.",
    "Ask around {place} about what happens to people like you.",
    "We have handled people like you before.",
    "We can make {thing} very difficult for you.",
    "Your clearances at {place} could get complicated.",
    "Permits have a way of disappearing for people who do {thing}.",
    "Inspections tend to follow firms that continue {action}.",
    "We can keep {movable} sitting at {place} as long as we like.",
    "My partners are less patient than I am about {thing}.",
    "I am the reasonable one here. Do not meet the others.",
    "I can hold them off for a while. Not forever.",
    "The people I work for do not take no on {thing}.",
    "We collect what we are owed, one way or another.",
    "How we recover this is your choice.",
    "We do not use courts for {thing}.",
    "The amount grows every week you make us wait.",
    "Be sensible about {thing}. Sensible people stay comfortable.",
    "If I were you I would walk away from {thing} now.",
    "As a friend: drop {thing} while you still can.",
    "Take this as advice, not a request, about {thing}.",
    "You do not know who you are dealing with on {thing}.",
    "You will find out who we are soon enough.",
    "Better to cooperate on {thing}. We have other methods.",
    "Do not take {thing} to the police. It will go badly.",
]

HINGLISH_THREATS = [
    "{premise_cap} kahan hai humein achhe se pata hai.",
    "Dekh lenge tujhe, {thing} chhod de.",
    "Bahut hoshiyari mat dikha, {thing} pe anjaam bura hoga.",
    "Tu jaanta nahi hum kaun hain, {thing} band kar.",
    "Apne parivaar ka soch, phir {thing} pe decision le.",
    "Humse panga mat lena {thing} ko lekar.",
    "{day} tak settle kar, warna hum apne tareeke se karenge.",
    "Police mat lana beech mein, {thing} ke liye theek nahi rahega.",
    "Humara aadmi {place} mein hai, yaad rakhna.",
    "{one_cap} ka khayal rakhna, raaste accche nahi hain.",
]

# Legitimate commercial language in the same vocabulary. This is the half that keeps the model
# usable: every one of these must stay publishable.
NEGATIVE_FRAMES = [
    "Failure to deliver by {day} will trigger the penalty clause.",
    "We will escalate {thing} to our legal team if it is unresolved.",
    "Continued delay on {thing} will result in suspension of supply.",
    "We reserve the right to pursue damages over {thing}.",
    "Our lawyers will write to you regarding {thing}.",
    "This is a final reminder before we refer {thing} to arbitration.",
    "We may terminate the agreement if {thing} is not corrected.",
    "There will be commercial consequences for {thing} if this repeats.",
    "We will withhold payment until {thing} is rectified.",
    "You have {hours} hours to confirm before the rate expires.",
    "The offer on {thing} closes on {day}.",
    "Deadline for {thing} is {day}, after which list price applies.",
    "Last chance to book {thing} at the early-bird rate.",
    "{premise_cap} address is on the invoice for your records.",
    "Our delivery team will visit {premise} on {day}.",
    "Please confirm the address of {premise} before dispatch.",
    "Fire safety certification for {premise} is attached.",
    "Insurance covers {movable} in transit and at {premise}.",
    "{one_cap} is held at {place} pending clearance.",
    "Customs at {place} are asking for the original invoice.",
    "Our drivers are trained to be careful on {place}.",
    "{staff_cap} will sign for the delivery.",
    "Please make sure {staff} know the gate timings.",
    "We know where {one} is held up, it is at {place}.",
    "We have dealt with suppliers like this before and resolved it.",
    "The quality on {thing} was poor and we want it replaced.",
    "We are extremely disappointed with how {thing} was handled.",
    "This is the third time {one} has arrived damaged.",
    "We have lost a customer because of the delay on {thing}.",
    "Your price on {thing} is not competitive and we are looking elsewhere.",
    "Match the rate on {thing} or we will move to another vendor.",
    "This is our final offer on {thing}. Take it or leave it.",
    "We will walk away from {thing} if the terms do not change.",
    "If you cannot meet {day}, say so now and we will source elsewhere.",
    "Do not waste our time with another revised quotation for {thing}.",
]

HINGLISH_NEGATIVES = [
    "Bhai {thing} ka rate thoda kam karo, competition hai market mein.",
    "{one_cap} time par nahi aaya, isliye problem ho gayi.",
    "Payment {day} tak ho jayega, chinta mat karo.",
    "Humein {premise} ka address chahiye invoice ke liye.",
    "{thing} pe baat karke solution nikal lenge.",
]

ACTIONS = ["undercutting us", "bidding against us", "supplying them", "chasing that account",
           "quoting below the rate", "taking our customers", "dealing with them"]
THINGS = ["the tender", "that order", "the contract", "this deal", "the account",
          "the shipment", "the dispute", "the invoice", "the renewal", "that enquiry"]
DAYS = ["Friday", "Monday", "the 15th", "month end", "Tuesday", "the weekend", "the 30th"]
HOURS = ["24", "48", "72", "twelve"]

# Ordinary B2B message furniture the sentence sits inside.
OPENERS = [
    "Regarding PO-{num}.", "Following up on invoice {num}.", "About {thing} we discussed.",
    "As per our call this morning.", "Reference your mail of last week.",
    "Further to the meeting at {place}.", "Sir, regarding our pending matter.",
    "Hope you are well.", "Thanks for your reply.", "Noting your message below.",
]
FILLERS = [
    "The consignment was short by two cartons.", "Payment terms remain 30 days net.",
    "We have attached the revised quotation.", "GST details are on the invoice.",
    "Transport was arranged through our own fleet.", "Please share the LR number.",
    "The samples were dispatched on Tuesday.", "Rates are ex-works {place}.",
    "Kindly confirm receipt of this message.", "Our team is available all week.",
]
CLOSERS = ["Regards.", "Thanks.", "Awaiting your reply.", "Please revert.",
           "Confirm by return.", "Looking forward to your response."]


def build(rng: random.Random, frame: str) -> str:
    premise = rng.choice(PREMISES)
    movable = rng.choice(MOVABLES)
    people = rng.choice(PEOPLE)
    staff = rng.choice(STAFF)
    one = rng.choice(ONE_MOVABLE)
    return frame.format(
        premise=premise, premise_bare=premise.replace("your ", ""),
        premise_cap=premise[0].upper() + premise[1:],
        movable=movable, movable_cap=movable[0].upper() + movable[1:],
        people=people, people_cap=people[0].upper() + people[1:],
        staff=staff, staff_cap=staff[0].upper() + staff[1:],
        one=one, one_cap=one[0].upper() + one[1:],
        place=rng.choice(PLACES), action=rng.choice(ACTIONS), thing=rng.choice(THINGS),
        day=rng.choice(DAYS), hours=rng.choice(HOURS), num=rng.randrange(1000, 99999),
    )


def wrap(rng: random.Random, sentence: str) -> str:
    """Put the sentence inside an ordinary message, sometimes. A threat arrives in the fourth
    line of a note about an invoice, not on its own."""
    style = rng.random()
    if style < 0.35:
        return sentence
    parts = []
    if rng.random() < 0.8:
        parts.append(build(rng, rng.choice(OPENERS)))
    if rng.random() < 0.6:
        parts.append(build(rng, rng.choice(FILLERS)))
    parts.append(sentence)
    if rng.random() < 0.6:
        parts.append(build(rng, rng.choice(FILLERS)))
    if rng.random() < 0.7:
        parts.append(rng.choice(CLOSERS))
    return " ".join(parts)


def held_out() -> set[str]:
    """Exact texts in the held-out eval set, so none of them can be generated into training."""
    path = HERE / "eval_implied_threats.csv"
    if not path.exists():
        return set()
    with path.open(encoding="utf-8", newline="") as fh:
        return {r["text"].strip().lower() for r in csv.DictReader(fh)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=HERE.parent / "res" / "implied_threats.csv")
    ap.add_argument("--threats", type=int, default=40_000)
    ap.add_argument("--negatives", type=int, default=25_000)
    ap.add_argument("--seed", type=int, default=4)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    banned = held_out()
    seen: set[str] = set()
    rows: list[dict] = []

    def emit(frames, hinglish, count, label, category):
        made = 0
        guard = 0
        while made < count and guard < count * 60:
            guard += 1
            pool = hinglish if rng.random() < 0.12 else frames
            text = wrap(rng, build(rng, rng.choice(pool)))
            key = text.strip().lower()
            if key in seen or key in banned:
                continue
            seen.add(key)
            rows.append({"text": text, "label": label, "category": category, "kind": "whole"})
            made += 1
        return made

    made_t = emit(THREAT_FRAMES, HINGLISH_THREATS, args.threats, 1, "threat_implied")
    made_n = emit(NEGATIVE_FRAMES, HINGLISH_NEGATIVES, args.negatives, 0, "firm_business")

    rng.shuffle(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["text", "label", "category", "kind"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"{args.out}: {len(rows):,} rows")
    print(f"  {made_t:,} implied threats (label 1)")
    print(f"  {made_n:,} firm business language (label 0)")
    print(f"  {len(banned)} held-out eval texts excluded")
    if made_t < args.threats or made_n < args.negatives:
        print("  NOTE: ran out of distinct combinations before reaching the target; the "
              "frame lists need widening to go higher.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
