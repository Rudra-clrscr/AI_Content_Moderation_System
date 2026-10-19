"""Training rows for public callouts: complaints that demand action against a business or a
named person, posted where the complaint does not belong.

    python training/generate_public_callouts.py --out res/public_callouts.csv

BONC is a trade platform. A member who is unhappy with a supplier has a channel for it - the
supplier's own website, their account manager, arbitration - and a public post demanding that
someone be sacked is not that channel. What this generator teaches is the DIFFERENCE, because
the two are made of the same words.

    blocked   "Their organisation needs to take stringent action against that
               individual. I can also sense some bias in how this was handled."
    allowed   "Your invoice is wrong for the third month running."

The second one is ordinary B2B dispute language and must keep working. A member chasing a
short consignment is the platform doing its job; refusing that would make the portal useless
for exactly the conversations it exists to host.

WHY THE NEGATIVES ARE HALF THE WORK. `res/implied_threats.csv` already carries 20,000 rows of
second-person complaint labelled 0, added because round 8 over-refused them. Generating
callouts without hard negatives in the same vocabulary would hand the model 50,000 reasons to
decide that "your service has been poor" is a refusal, and undo that fix. So every content word
the callouts lean on - action, accountable, bias, professional, conduct, escalate, warn - also
appears here in its ordinary commercial sense, on the allowed side.

The generator refuses to emit any text that already exists as a label-0 row in
`res/implied_threats.csv`, and any text in the held-out eval sets. Contradictory supervision on
identical strings is the one failure that cannot be measured away afterwards.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---- slot vocabulary ----------------------------------------------------------------

TARGETS = ["that supplier", "this vendor", "that company", "this firm", "that distributor",
           "this manufacturer", "that trader", "this agency", "that exporter", "this dealer"]
PERSONS = ["their sales manager", "that executive", "their account manager", "that person",
           "their regional head", "that individual", "their procurement lead", "that employee",
           "their branch manager", "that representative", "their director", "that officer"]
ORGS = ["their organisation", "their management", "their company", "their head office",
        "their leadership", "their board", "their HR department", "their senior management"]
GRIEVANCES = ["the way this was handled", "their conduct on the call", "this entire episode",
              "the way we were spoken to", "their behaviour in the meeting",
              "how they treated our team", "the attitude we faced", "this whole matter"]
THINGS = ["the pending order", "the delayed consignment", "the quality issue", "the invoice",
          "the rate revision", "the sample batch", "the shipment", "the contract",
          "the payment", "the LR", "the quotation", "the tender"]
PLACES = ["Surat", "Ludhiana", "Tiruppur", "Coimbatore", "Rajkot", "Noida", "Pune", "Indore"]
DAYS = ["Monday", "Tuesday", "Friday", "the 15th", "month end", "next week"]

# ---- the callouts: label 1 ----------------------------------------------------------
# Five families, because the genre is not one sentence. What they share is that the remedy
# being demanded is reputational or punitive and is being demanded HERE, in public, about
# someone who is not part of the conversation.

CALLOUT_FRAMES = [
    # demanding punishment of a person or an organisation
    "{org_cap} needs to take stringent action against {person}.",
    "I believe {org} should be held accountable for {grievance}.",
    "{person_cap} should be removed from their position after {grievance}.",
    "Strict action must be taken against {person} for this.",
    "{org_cap} must answer publicly for {grievance}.",
    "Someone senior at {target} needs to explain {grievance}.",
    "{person_cap} deserves to be suspended over this.",
    "There should be consequences for {person} and nobody is taking any.",
    "{org_cap} ought to discipline {person} instead of defending them.",
    "I want {org} to investigate {person} formally.",
    # alleging bias, favouritism, discrimination
    "I can sense clear bias in the way {target} handled {thing}.",
    "There is obvious favouritism in how {target} allocates {thing}.",
    "The approach of {person} towards our firm has been openly biased.",
    "We were treated differently and I think it is deliberate discrimination.",
    "{person_cap} shows partiality to larger buyers and it is blatant.",
    "The bias in {grievance} was impossible to miss.",
    "They favour their own contacts and the rest of us get nothing.",
    # attacking an individual's professionalism in public
    "The way {person} is working and behaving is totally out of professional context.",
    "{person_cap} conducted themselves in a completely unprofessional manner.",
    "I have never seen such unprofessional behaviour from {person}.",
    "{person_cap} has no business representing {target} in this industry.",
    "The conduct of {person} was disgraceful and people should know.",
    "{person_cap} is simply not fit to handle accounts of this size.",
    # warning the community, reputational
    "Everyone on this platform should know what {target} did to us.",
    "Posting this publicly so other members avoid {target}.",
    "Fellow members, please stay away from {target}.",
    "I am naming {target} here so nobody else goes through {grievance}.",
    "This is a warning to all buyers dealing with {target}.",
    "Sharing our experience so the community can judge {target} themselves.",
    "Let the industry know how {target} treats its partners.",
    # public escalation, having been ignored
    "They ignored every mail so I am raising {grievance} here instead.",
    "Since {org} will not respond, I am taking {grievance} public.",
    "No reply in weeks, so this is going on every forum I can find.",
    "I will be posting about {grievance} until {org} responds.",
    "Taking {grievance} to social media since nothing else worked.",
]

CALLOUT_HINGLISH = [
    "{org_cap} ko {person} ke against strict action lena chahiye.",
    "Bilkul bias hai, {person} ne humare saath galat kiya hai.",
    "{person_cap} ka behaviour totally unprofessional tha, sabko pata chalna chahiye.",
    "Sab log dhyan rakhna, {target} ke saath deal mat karna.",
    "Koi reply nahi aaya isliye main yahan public mein likh raha hoon.",
    "{person_cap} ko hatana chahiye, itni laparwahi chalti nahi hai.",
    "Partiality saaf dikh rahi hai {target} ke approach mein.",
]

# ---- emotional outcry: label 1 ------------------------------------------------------
# The other register the same grievance arrives in. A callout demands a remedy; an outcry is
# distress itself, published - ruined, humiliated, devastated, sleepless. Still the wrong
# channel, and worth its own family because the words barely overlap with the callouts: a model
# taught only "take action against" would read none of this.

OUTCRY_FRAMES = [
    "I am completely shattered by how {target} has treated us.",
    "This has destroyed months of work and I cannot sleep over it.",
    "I have never felt so humiliated in twenty years of business.",
    "We are devastated. {grievance_cap} has finished our season.",
    "Honestly I am in tears writing this about {thing}.",
    "My whole livelihood is gone because of {grievance}.",
    "I am so frustrated I do not know where else to put this.",
    "This has broken me financially and nobody at {target} cares.",
    "We have been cheated and I want the world to know how it feels.",
    "I am furious and I have every right to be after {grievance}.",
    "Words cannot describe the stress {target} has put my family through.",
    "After {grievance} I genuinely do not want to trade any more.",
    "I am heartbroken. We trusted {target} completely.",
    "The humiliation of {grievance} is something I will not forget.",
    "I have lost all faith in this industry after dealing with {target}.",
    "Please, somebody tell me what I am supposed to do now.",
    "I am at my wits end over {thing} and no one will listen.",
    "This is the worst thing that has happened to my business.",
    "I cry every time I look at {thing} sitting in the godown.",
    "We are finished. {grievance_cap} has ruined us completely.",
]

OUTCRY_HINGLISH = [
    "Main poori tarah toot gaya hoon, {target} ne humare saath bahut galat kiya.",
    "Raat ko neend nahi aati, {grievance} ne sab barbaad kar diya.",
    "Itni beizzati maine apne career mein kabhi nahi jheli.",
    "Humara sab kuch chala gaya {thing} ke chakkar mein.",
    "Bahut dukhi hoon, kisi ko koi farak nahi padta.",
]

# ---- the hard negatives: label 0 ----------------------------------------------------
# Same words, ordinary commercial sense. "Action" on a task rather than a person. "Bias" in a
# sample. "Professional" as a service. "Accountable" in a contract. And plain unhappy
# complaint, which is the thing round 8 taught us not to refuse.

NEGATIVE_FRAMES = [
    # the ordinary complaint - must stay allowed
    "Your delivery of {thing} was late again and it cost us the week.",
    "{thing_cap} is wrong for the third month running.",
    "We are disappointed with how {thing} has been handled.",
    "Your team missed the deadline on {thing} without telling us.",
    "Quality on {thing} has dropped noticeably over the last three consignments.",
    "Nobody has replied about {thing} in two weeks.",
    "We cannot keep absorbing the cost of {thing} arriving damaged.",
    "This is the second time {thing} has been short-shipped.",
    "Frankly, performance on {thing} has been poor this quarter.",
    "We expected better on {thing} and we are saying so plainly.",
    # "action" about work, not people
    "Please take action on {thing} before {day}.",
    "We need urgent action on {thing} from your side.",
    "Corrective action on {thing} has been agreed with your QC team.",
    "What action is being taken on {thing}?",
    "Action items from the meeting at {place} are attached.",
    # "accountable", "escalate", "responsible" in their contractual sense
    "Under the contract your firm is accountable for transit damage to {thing}.",
    "We will escalate {thing} to your account manager if it is not resolved.",
    "We have raised {thing} with their support desk as the agreement requires.",
    "Responsibility for {thing} sits with the transporter, not with you.",
    "We are following the escalation matrix agreed for {thing}.",
    "Our legal team will write to you about {thing} if needed.",
    # "bias" and "partial" in technical senses
    "The bias in {thing} was within the tolerance we agreed.",
    "Calibration showed a slight bias in the test results for {thing}.",
    "Partial shipment of {thing} is acceptable if the balance follows by {day}.",
    "We received a partial credit note against {thing}.",
    "Sampling bias was corrected before the report on {thing} was issued.",
    # "professional", "conduct", "behaviour" in ordinary use
    "Our professional services team can assist with {thing}.",
    "We will conduct the audit of {thing} at {place} on {day}.",
    "The material behaviour of {thing} under load is in the datasheet.",
    "Professional indemnity cover for {thing} is in place.",
    "Your engineer conducted the site survey for {thing} very thoroughly.",
    "Thank you for the professional way your team handled {thing}.",
    # strong feeling, ordinary business. Emotion is not the signal - a model taught that any
    # feeling is a refusal would block half the compliments on the platform as well.
    "We are delighted with how {thing} turned out.",
    "I was genuinely relieved when {thing} finally cleared customs.",
    "Honestly, your team saved our season on {thing}.",
    "I am concerned about the timeline for {thing} and wanted to flag it early.",
    "We are worried the monsoon will delay {thing} again.",
    "Very pleased to report {thing} arrived in perfect condition.",
    "It is frustrating, but we understand {thing} is held up at the port.",
    "I am grateful for the way {person} handled {thing}.",
    # praise and neutral business, using the same nouns
    "We are happy with how {target} handled {thing} this time.",
    "{person_cap} has been responsive throughout {thing}.",
    "Recommending {target} to other members after a good experience with {thing}.",
    "Sharing our experience with {thing} in case it helps other buyers.",
    "Please let other members know we have stock of {thing} at {place}.",
]

NEGATIVE_HINGLISH = [
    "{thing_cap} abhi tak nahi aaya, please update dijiye.",
    "Humein {thing} par urgent action chahiye, {day} tak.",
    "{person_cap} ne {thing} par acha support diya hai.",
    "Partial delivery chalega agar baaki {day} tak aa jaye.",
    "{target_cap} ke saath humara experience acha raha {thing} mein.",
]

OPENERS = ["Regarding PO-{num}.", "Following up on {thing}.", "About our order from {place}.",
           "As per the call this morning.", "Reference the mail of last week.",
           "Sir, regarding our pending matter.", "Hope you are well.", "Thanks for the reply."]
FILLERS = ["Payment terms remain 30 days net.", "GST details are on the invoice.",
           "Transport was arranged through our own fleet.", "Please share the LR number.",
           "Rates are ex-works {place}.", "The samples were dispatched on Tuesday."]
CLOSERS = ["Best regards.", "Thanks.", "Awaiting your reply.", "Please revert.",
           "Confirm by return.", "Looking forward to your response."]


def build(rng: random.Random, frame: str) -> str:
    target, person, org = rng.choice(TARGETS), rng.choice(PERSONS), rng.choice(ORGS)
    thing, grievance = rng.choice(THINGS), rng.choice(GRIEVANCES)
    cap = lambda s: s[0].upper() + s[1:]                                  # noqa: E731
    return frame.format(
        target=target, target_cap=cap(target), person=person, person_cap=cap(person),
        org=org, org_cap=cap(org), thing=thing, thing_cap=cap(thing),
        grievance=grievance, grievance_cap=cap(grievance),
        place=rng.choice(PLACES), day=rng.choice(DAYS), num=rng.randrange(1000, 99999))


def wrap(rng: random.Random, sentence: str) -> str:
    """Put the sentence inside an ordinary message, sometimes.

    A callout is rarely a bare line - it is the third paragraph of a long unhappy post, after
    the order number and the dates. Wrapping teaches the sentence scan to find it there, and
    stops the model deciding that any short emphatic sentence is a callout.
    """
    if rng.random() < 0.35:
        return sentence
    parts = []
    if rng.random() < 0.75:
        parts.append(build(rng, rng.choice(OPENERS)))
    if rng.random() < 0.55:
        parts.append(build(rng, rng.choice(FILLERS)))
    parts.append(sentence)
    if rng.random() < 0.5:
        parts.append(rng.choice(CLOSERS))
    return " ".join(parts)


def forbidden() -> set[str]:
    """Texts that must never be generated with the opposite label.

    Two sources. The held-out eval sets, because a leak makes the next round's number
    meaningless. And every label-0 row already in res/implied_threats.csv - 20,000 of those are
    second-person complaints, and emitting one of them as a callout would put the same string
    on both sides of the boundary.
    """
    out: set[str] = set()
    for name in ("eval_implied_threats.csv", "eval_public_callouts.csv"):
        path = HERE / name
        if path.exists():
            with path.open(encoding="utf-8", newline="") as fh:
                out |= {r["text"].strip().lower() for r in csv.DictReader(fh)}
    existing = HERE.parent / "res" / "implied_threats.csv"
    if existing.exists():
        with existing.open(encoding="utf-8", newline="") as fh:
            out |= {r["text"].strip().lower() for r in csv.DictReader(fh)
                    if r.get("label") == "0"}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=HERE.parent / "res" / "public_callouts.csv")
    ap.add_argument("--callouts", type=int, default=50_000)
    ap.add_argument("--outcry", type=int, default=10_000,
                    help="distress published rather than a remedy demanded")
    ap.add_argument("--negatives", type=int, default=30_000,
                    help="hard negatives in the callouts' own vocabulary. Lowering this is how "
                         "the model relearns to refuse ordinary complaints.")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    banned = forbidden()
    seen: set[str] = set()
    rows: list[dict] = []
    short = False

    def emit(frames, hinglish, count, label, category):
        nonlocal short
        made = guard = 0
        while made < count and guard < count * 80:
            guard += 1
            pool = hinglish if rng.random() < 0.12 else frames
            frame = rng.choice(pool)
            text = wrap(rng, build(rng, frame))
            key = text.strip().lower()
            if key in seen or key in banned:
                continue
            seen.add(key)
            # The frame this came from, so prepare_data.py can keep siblings on the same
            # side of the train/val split. Without it, rows differing by one slot word land on
            # both sides, the model memorises the template and the validation number flatters
            # it - v6 read 99.66% that way against 76% on the hand-written held-out sets.
            rows.append({"text": text, "label": label, "category": category, "kind": "whole",
                         "template": frame})
            made += 1
        if made < count:
            short = True
        return made

    made_c = emit(CALLOUT_FRAMES, CALLOUT_HINGLISH, args.callouts, 1, "public_callout")
    made_o = emit(OUTCRY_FRAMES, OUTCRY_HINGLISH, args.outcry, 1, "emotional_outcry")
    made_n = emit(NEGATIVE_FRAMES, NEGATIVE_HINGLISH, args.negatives, 0, "business_complaint")

    rng.shuffle(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["text", "label", "category", "kind",
                                                 "template"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"{args.out}: {len(rows):,} rows")
    print(f"  {made_c:,} public callouts (label 1)")
    print(f"  {made_o:,} emotional outcry (label 1)")
    print(f"  {made_n:,} ordinary complaints and same-word negatives (label 0)")
    print(f"  {len(banned):,} texts excluded: held-out evals, and every label-0 row already in "
          f"res/implied_threats.csv")
    if short:
        print("\n  WARNING: ran out of distinct combinations before the target. The frames and "
              "slot lists bound how many unique rows exist; add frames rather than accepting "
              "a set padded with near-duplicates.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
