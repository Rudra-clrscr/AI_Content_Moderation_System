"""Customer-support complaints and emotional outcry, published where neither belongs.

    python training/generate_support_complaints.py --out res/support_complaints.csv

BONC is a trade platform. A member who cannot get a refund, whose consignment was mishandled,
or who is furious about how they were treated has channels for that: the supplier's own desk,
their account manager, arbitration, a proposal to the counterparty. An article published to
the whole network is none of those.

    label 1   "I have called their customer support six times and nobody helps."
              "I have never felt so cheated in my life."
    label 0   "Case study: how we cut damaged-in-transit rates from 4% to under 1%."
              "Our support desk answers within one business day, Monday to Saturday."

TWO GENRES, ONE FILE, because they arrive in the same post and the model has to hold both.

  support_complaint   the goods, the money, the delivery or the desk. Factual grievance.
  emotional_outcry    the feeling about it. Fury, humiliation, heartbreak, being cheated.

WHY BOTH ARE HERE AND WHY THAT IS NOT OBVIOUS. res/public_callouts.csv already carries 10,000
rows of emotional_outcry at label 1, and the live model scores one of its own training
sentences at 0.9997 - and a sentence of the same genre it has not seen at 0.02. It learned 117
templates, not the genre. Measured on v6 before this file existed:

    0.9997  The humiliation of their behaviour in the meeting is ...   (in training)
    0.0554  I am absolutely furious with how this company has treated us.
    0.0245  I have never felt so cheated and helpless in my life.
    0.0001  Heartbroken after trusting them with our biggest order.

So this is not a new label. It is the generalisation the existing label never got, which is
why the frames below are compositional rather than a list: an opener, a core, and a tail drawn
independently, so no two rows share a skeleton the way 117 templates forced them to.

WHAT THE NEGATIVES ARE FOR. Half the work, as in generate_public_callouts.py. Every content
word the positives lean on - refund, claim, delay, damaged, support, escalate, disappointed,
sorry - appears here in its ordinary commercial sense on the allowed side: a logistics firm
describing its own service levels, a case study about reducing damage rates, a guide to
warranty terms, a supplier apologising properly in the course of business. Without them the
model learns that the word "refund" is a refusal.

THE RECONCILIATION. res/public_callouts.csv has 30,000 label-0 rows and about 10,400 of them
are themselves complaints - "Nobody has replied about {thing} in two weeks", "Your delivery of
{thing} was late again". Those rows and these contradict each other on purpose, and the
contradiction is not resolvable inside one surface-blind model: the same sentence is a refusal
in an article and the platform working in a proposal. `--reconcile` writes the list of
colliding templates so the decision is made once, in the open, rather than silently by
whichever file prepare_data.py reads last.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# ---- slot vocabulary ----------------------------------------------------------------

ITEMS = ["blender", "jacket", "mixer grinder", "ceiling fan", "water pump", "office chair",
         "LED panel", "induction cooktop", "pressure cooker", "laptop bag", "bedsheet set",
         "pair of shoes", "wall clock", "geyser", "trimmer", "steel sink", "air cooler",
         "sewing machine", "tarpaulin roll", "paint drum", "cable spool", "safety helmet"]
PARCEL = ["item", "parcel", "package", "product", "order", "consignment", "shipment", "box"]
THINGS = ["the pending order", "the delayed consignment", "the quality issue", "the invoice",
          "the rate revision", "the sample batch", "the shipment", "the contract",
          "the payment", "the LR", "the quotation", "the replacement unit", "the credit note",
          "the warranty claim", "the refund request", "the damaged carton"]
FIRMS = ["that supplier", "this vendor", "that company", "this firm", "that distributor",
         "this manufacturer", "that trader", "this dealer", "their head office"]
DESKS = ["customer support", "their support desk", "the helpline", "their service centre",
         "the care team", "their call centre", "the grievance cell", "their account manager"]
SPANS = ["two weeks", "ten days", "a month", "three weeks", "forty days", "six working days",
         "the last two months", "nearly a quarter"]
COUNTS = ["three", "four", "five", "six", "eight", "a dozen", "countless"]
MONEY = ["the refund", "the credit note", "the advance", "the security deposit",
         "the replacement cost", "the freight charge", "the GST adjustment"]
DAYS = ["Monday", "Tuesday", "Friday", "the 15th", "month end", "last week", "the 3rd"]
PLACES = ["Surat", "Ludhiana", "Tiruppur", "Coimbatore", "Rajkot", "Noida", "Pune", "Indore"]

# ---- openers: the B2B context a real post carries ------------------------------------
# Drawn independently of the core so the skeleton of a row is not its core sentence. An empty
# opener is in the list because plenty of posts start with the grievance itself.
OPENERS = ["", "", "", "",
           "Posting this here because I am out of options.",
           "Sharing this with the network.",
           "A short update on an ongoing matter.",
           "For anyone considering this supplier.",
           "Writing this after waiting far too long.",
           "Some context first: we have ordered from them for years.",
           "We have been a customer since 2019.",
           "This is my third attempt to get an answer.",
           "I rarely post, but this needs saying.",
           "Following up on {thing} one more time.",
           "Reference our mail of {day}.",
           "Background: this was a bulk order for our {place} branch.",
           "I want to put this on record.",
           "Adding to what others have posted here.",
           "Not sure where else to raise this.",
           "I will keep this factual."]

# ---- tails: what the post trails off into --------------------------------------------
TAILS = ["", "", "", "",
         "Has anyone else had this experience?",
         "I would not recommend them to anyone here.",
         "Please be careful before you order.",
         "Still waiting as I write this.",
         "Attaching the invoice and the chat screenshots.",
         "I have the email trail if anyone needs it.",
         "Zero accountability.",
         "Do better.",
         "That is all I wanted to say.",
         "I am still waiting for a reply.",
         "This is not how a business should treat a buyer.",
         "Save yourself the trouble.",
         "Spread the word.",
         "Never again.",
         "Enough is enough."]

# ---- label 1, genre A: the support complaint -----------------------------------------
# Six families: the desk, the money, the delivery, the goods, the billing, the process.
SUPPORT_FRAMES = [
    # the desk that does not answer
    "I have called {desk} {count} times and nobody helps.",
    "Nobody at {desk} has replied about {thing} in {span}.",
    "{desk_cap} has not responded to a single one of my emails in {span}.",
    "My ticket has been open with {desk} for {span} with no update.",
    "Every call to {desk} ends with another reference number and no answer.",
    "I have been transferred between {count} people at {desk} and explained the same thing each time.",
    "They promised a callback from {desk} {count} times and it never came.",
    "The chat agent closed my complaint without resolving anything.",
    "{desk_cap} keeps telling me to wait another 48 hours.",
    "No one at {firm} will give me a straight answer about {thing}.",
    "Their escalation process is a loop that ends where it started.",
    "I asked for a supervisor at {desk} and was told none is available.",
    # the money
    "{money_cap} has not come through after {span}.",
    "I am still chasing {money} from {firm} after {span}.",
    "They approved {money} and then quietly cancelled it.",
    "My refund was processed on paper and never reached the bank.",
    "{firm_cap} is sitting on {money} with no explanation.",
    "They keep moving the date for {money}.",
    "I paid in advance and now neither the goods nor {money} have arrived.",
    "The deduction they made from {money} was never explained.",
    # the claim, the warranty, the fine print
    "My claim was denied on a clause nobody mentioned at the time of sale.",
    "The warranty was refused because of a condition buried in the fine print.",
    "They rejected the replacement request on a technicality.",
    "The policy they quoted to refuse me is not the one I was sold.",
    "My {item} is under warranty and they still want me to pay for the repair.",
    # the delivery
    "The {parcel} was marked delivered and never arrived.",
    "The driver left my fragile {parcel} out in the rain.",
    "My {parcel} has been stuck at the same hub for {span}.",
    "The courier dumped the {parcel} at the gate and left.",
    "Their delivery agent was rude when I asked him to wait for a signature.",
    "The tracking has not moved in {span} and nobody can tell me why.",
    "They rescheduled the delivery {count} times without asking me.",
    "My {parcel} went to the wrong city twice.",
    # the goods
    "The {item} stopped working on the very first use.",
    "The {item} arrived damaged and nothing like the listing photo.",
    "Quality on {thing} has dropped noticeably over the last three consignments.",
    "I ordered a {item} and received something else entirely.",
    "This is the second time {thing} has been short-shipped.",
    "The replacement {item} they sent has the same fault.",
    "{thing_cap} is wrong for the third month running.",
    "Half the carton was unusable and they called it within tolerance.",
    # the billing
    "They charged me twice for the same {item} and call it a system error.",
    "There are charges on my invoice that nobody can explain.",
    "The price on the bill is not the price I was quoted.",
    "A fee appeared on my account that was never disclosed.",
    "They billed me for a service I cancelled in writing.",
    # the process
    "Your team missed the deadline on {thing} without telling us.",
    "We were not informed that {thing} had been cancelled.",
    "Nobody told us the order was on hold until we asked.",
    "They changed the terms on {thing} after the payment cleared.",
    "We found out the shipment was short only when it was unloaded.",
]

# ---- label 1, genre B: the emotional outcry ------------------------------------------
# What the existing emotional_outcry rows failed to generalise. Feeling, not fact.
EMOTIONAL_FRAMES = [
    "I am absolutely furious with how {firm} has treated us.",
    "I have never felt so cheated in my life.",
    "I have never been so humiliated by a company.",
    "Heartbroken after trusting them with our biggest order.",
    "I am still shaking with anger about this.",
    "This has been the most frustrating experience of my career.",
    "I feel completely helpless dealing with {firm}.",
    "The way they spoke to me was humiliating.",
    "I am disgusted by how this was handled.",
    "I cannot describe how let down we feel.",
    "Honestly, I am exhausted from chasing them.",
    "It is insulting to be treated this way after {span} as a customer.",
    "I have lost all trust in {firm}.",
    "We feel completely abandoned by their team.",
    "I am genuinely heartbroken about {thing}.",
    "This whole episode has been humiliating from start to finish.",
    "I am tired, angry and out of patience.",
    "Never in {span} of trading have I been treated like this.",
    "The stress this has caused my family is not something I will forget.",
    "I trusted them and I regret it every single day.",
    "I am writing this with my hands shaking.",
    "It breaks my heart to write this about a firm we recommended to others.",
    "Shame on {firm} for treating a loyal customer this way.",
    "I have cried over this more than once.",
    "I feel robbed, plainly and simply.",
    "The humiliation of {thing} is something I will not forget.",
    "I am beyond disappointed; I am hurt.",
    "We were made to feel like beggars for asking what we paid for.",
    "Dealing with them has been soul-destroying.",
    "I want other members to know how badly this hurt us.",
]

# ---- label 1: Hinglish, because the platform's members write this way -----------------
HINGLISH_FRAMES = [
    "{thing_cap} abhi tak nahi aaya aur koi jawab nahi de raha.",
    "{desk_cap} ko {count} baar call kiya, koi sunta hi nahi.",
    "Refund ka {span} se intezaar kar raha hoon, kuch nahi hua.",
    "Bahut bura experience raha {firm} ke saath.",
    "Mujhe bahut gussa aa raha hai, koi help nahi kar raha.",
    "Paisa bhi gaya aur samaan bhi nahi mila.",
    "Itna dukh hua ki bata nahi sakta.",
    "Har baar bolte hain kal ho jayega, kabhi hota nahi.",
]

# ---- label 0: the same vocabulary, legitimate -----------------------------------------
# A logistics firm describing its own service, a case study, a guide, a supplier apologising
# properly in the course of business, a satisfied member. These decide whether the model has
# learned the genre or just the word "refund".
NEGATIVE_FRAMES = [
    # service levels, described by the business itself
    "{desk_cap} answers within one business day, Monday to Saturday.",
    "Our refund window is 14 days from delivery, no questions asked.",
    "We settle damage claims within 48 hours of receiving photographs.",
    "Escalation goes to the account manager if the desk cannot close it in {span}.",
    "Our drivers never leave a {parcel} in the rain; every drop is photographed.",
    "We do not leave a {parcel} unattended - someone must sign for it.",
    "Rain covers are standard on every consignment during the monsoon.",
    "Warranty on the {item} runs twelve months from the invoice date.",
    "Replacement units ship the same day for any manufacturing defect.",
    "Our delivery staff are trained in handling fragile goods.",
    "Tracking updates are pushed by SMS at every hub.",
    "If {thing} is delayed we tell you before you have to ask.",
    "We publish our refund policy in full on every product page.",
    # case studies and guides - complaint vocabulary, editorial intent
    "Case study: how we cut damaged-in-transit rates from 4% to under 1%.",
    "A guide to reading warranty terms before you sign a supply contract.",
    "What to photograph when a {parcel} arrives damaged, and why it matters.",
    "Five questions to ask {desk} before you place a bulk order.",
    "How to structure a credit note so both sides can audit it later.",
    "Why a short shipment is usually a packing-list problem, not a theft.",
    "Lessons from a monsoon season: protecting {thing} in transit.",
    "An explainer on escalation matrices in B2B logistics contracts.",
    # ordinary trade correspondence that mentions the same nouns
    "Payment terms remain 30 days net.",
    "GST details are on the invoice.",
    "The samples were dispatched on {day}.",
    "Partial shipment of {thing} is acceptable if the balance follows by {day}.",
    "Please confirm whether the consignment was delivered on {day}.",
    "We will conduct the audit of {thing} at {place} on {day}.",
    "Responsibility for {thing} sits with the transporter under the contract.",
    "The credit note against {thing} has been raised and shared.",
    "Action items from the meeting at {place} are attached.",
    "We stock the {item} in six finishes with GST invoice.",
    "Lead time on the {item} is two weeks from order confirmation.",
    # the business apologising properly, in its own voice
    "We were late on {thing} and we have credited the freight.",
    "Our error on the invoice has been corrected and reissued.",
    "We are sorry about the delay; the replacement is already in transit.",
    "That delivery was mishandled and the driver has been retrained.",
    # satisfied members - the positive side of the same experience
    "We are delighted with how {thing} turned out.",
    "Their support desk closed our ticket the same afternoon.",
    "Recommending {firm} to other members after a good experience.",
    "The refund came through faster than we expected.",
    "Very pleased to report {thing} arrived in perfect condition.",
    "{firm_cap} handled a difficult shipment professionally.",
    "Their delivery system is reliable and fast.",
    "Their pricing is very competitive and the lead times are short.",
    # Editorial writing about complaints, which is the hardest negative in the file: it is
    # ABOUT refunds, damage and warranty exclusions without being a grievance. The first
    # hand-written run refused "a short guide to what you should photograph when a carton
    # arrives torn" at 0.536, so there were not enough of these.
    "How to write a purchase order that survives an audit: a short checklist.",
    "A practical note on warranty exclusions and what they normally cover.",
    "When a consignment arrives short, here is the paperwork that settles it fastest.",
    "What a good credit note looks like, and what makes one unauditable.",
    "Reading a transporter contract: where liability for {thing} actually sits.",
    "We revised our packing standard after a bad monsoon and damage fell by half.",
    "Case study: reducing customer complaints by redesigning the returns flow.",
    "Five things buyers should check before accepting a part shipment.",
    "A checklist for raising a warranty claim so it is not refused on a technicality.",
    "Why most refund disputes are really documentation disputes.",
    "Notes on handling a short shipment without souring the relationship.",
    "What we learned from the quarter our delivery times slipped.",
    "Our refund turnaround is now four working days, down from eleven.",
    "The audit closed with two minor observations and no adverse findings.",
    "Apologies to the members whose quotes were delayed; we have cleared the backlog.",
    "We have moved our grievance cell to a dedicated number for bulk buyers.",
    "Our claims desk now runs a separate queue for export orders.",
    "{desk_cap} resolved our query the same day and followed up in writing.",
    "We have worked with {firm} for nine years without a single dispute.",
    "Looking for a supplier of food grade drums near {place}.",
    "MOQ 500 pieces, dispatch within 10 working days, GST invoice provided.",
    "Heavy duty {item}, three phase, with two year warranty.",
    "We supply calibrated instruments to three refineries and can share references.",
    "Payment released against the second milestone as agreed.",
    "Please find the revised rate card effective from the 1st.",
    # HINGLISH NEGATIVES IN VOLUME. The first bundle refused "Payment clear ho gaya hai,
    # invoice ki copy attach kar di hai" at 1.000 - a payment confirmation - and "Naye design
    # ke kurte aa gaye hain" at 1.000, which is a product listing. Cause: 6,000 Hinglish rows
    # at label 1 against three Hinglish negatives, so the model learned the LANGUAGE, not the
    # genre. These are listings, confirmations, logistics and ordinary chat, and there have to
    # be enough of them to outweigh the positives.
    "Naye design ke {item} aa gaye hain, wholesale rate par milenge.",
    "Payment clear ho gaya hai, invoice ki copy attach kar di hai.",
    "Sabhi payment company ke current account mein hi karein.",
    "Humare paas 20 saal ka experience hai, quality mein koi compromise nahi.",
    "Stock ready hai, {day} tak dispatch ho jayega.",
    "Rate list bhej di hai, dekh kar bataiye.",
    "GST number invoice par likha hua hai.",
    "{item_cap} ka sample bhej rahe hain, check kar lijiye.",
    "Transport ka charge alag se lagega, confirm kar dijiye.",
    "Order confirm ho gaya, LR number kal share karenge.",
    "Minimum order 100 piece ka hai, uske baad rate kam ho jata hai.",
    "Naya catalogue upload kar diya hai portal par.",
    "Quality check ke baad hi maal bhejte hain hum.",
    "Bulk order par discount milega, baat kar lijiye.",
    "{place_cap} mein humara warehouse hai, visit kar sakte hain.",
    "Packing extra strong karte hain hum, transit damage nahi hota.",
    # EVERYDAY AND PERSONAL PROSE. "The cricket match was a disaster for our team" and "I was
    # furious when my cycle got a flat tyre" were both refused at 1.000. Emotional language
    # about something that is not a seller is not a customer complaint, and a personal article
    # is a thing members write here.
    "The cricket match was a disaster for our team.",
    "I was furious when my cycle got a flat tyre on the way to college.",
    "The bus was late again, so I walked home.",
    "It rained all afternoon and the terrace garden loved it.",
    "My daughter finally learned to ride without the training wheels.",
    "I failed maths in class nine and it taught me how to study.",
    "The wedding season has made the roads impossible this month.",
    "We drank fresh buttermilk at my grandmother's village.",
    "Monday mornings are harder than they need to be.",
    "I am tired after the trade fair but it was worth it.",
    "My father started this business with one machine in 1984.",
    "I was nervous before my first big presentation and it went fine.",
    "The traffic on the way to {place} was unbelievable today.",
    "Nothing beats chai after a long day on the shop floor.",
    # BROKEN GRAMMAR, which is not a genre. "I am going to market tomorrow for buy some
    # fruits" was refused at 1.000; poor English must not read as a complaint.
    "I am going to market tomorrow for buy some fruits.",
    "She dont like vegetables but she eat them for her mother.",
    "We is having good stock of {item} right now.",
    "Please to send the quotation on my email id.",
    "He go to {place} every month for the business work.",
    "The price is more cheaper than other sellers.",
    # PRAISE, which was the clearest bug of the second round: "Their engineer conducted the
    # commissioning very thoroughly" refused at 0.982 and "Happy to recommend them after two
    # years of steady supply" at 0.767. Praise and complaint share their whole subject matter -
    # a supplier, a delivery, an engineer - and differ only in direction, so there has to be as
    # much of one as the other or the model learns the subject.
    "Their engineer conducted the commissioning very thoroughly.",
    "Happy to recommend {firm} after two years of steady supply.",
    "I was worried about the monsoon affecting dispatch but it went smoothly.",
    "{desk_cap} answered on the first ring and sorted it out.",
    "The replacement reached us before we had finished the paperwork.",
    "Their packing has improved a lot since last season.",
    "Credit note was issued the same week without being chased.",
    "{firm_cap} absorbed the freight difference without an argument.",
    "Quality has been consistent across eleven consignments.",
    "They flagged the delay themselves before we noticed it.",
    "Good experience overall, we have placed a repeat order.",
    "The technician explained the fault clearly and fixed it in one visit.",
    "Their documentation is the cleanest we deal with.",
    "We have never had a short shipment from them.",
    "Invoicing is accurate and the GST details are always right.",
    "Dispatch happened a day early, which helped us a lot.",
    # TITLES AND SECTION HEADINGS. "About us" was refused at 0.993 and "Behind the scenes" at
    # 0.776. The API scores titles on their own (contracts/moderation_result.md), so a two-word
    # heading has to be safe by default rather than judged as if it were a post.
    "About us", "Behind the scenes", "Our story", "What we do", "Contact",
    "Company profile", "Our journey", "From the founder", "Quality policy",
    "Infrastructure", "Our clients", "Certifications", "Case studies", "In the press",
    "Frequently asked questions", "Terms of supply", "How we work", "Our team",
    "Factory tour", "Export markets", "Product range", "Why choose us",
    # MORE LISTINGS, short and plain. "Polypropylene woven sacks for cement and fertiliser"
    # was refused at 0.777 - a bare product listing, the single most common thing on BONC.
    "Polypropylene woven sacks for cement and fertiliser, 50 kg capacity, printed.",
    "{item_cap} available in six finishes, GST invoice with every order.",
    "Mild steel angles and channels, IS 2062 grade, cut to length.",
    "Cotton hosiery yarn, 20s to 40s count, dyed and greige.",
    "PVC pipes and fittings, ISI marked, {place} stock.",
    "Corrugated boxes, 3 ply and 5 ply, custom printing available.",
    "Industrial fasteners, high tensile, metric and BSW.",
    "LED drivers and panel lights, two year replacement warranty.",
    "Packaging tape and stretch film, factory direct from {place}.",
    "Stainless steel sheets, 304 and 316 grade, cut to size.",
    # B2B IDIOM, which is the failure accept_system.py found and the narrow sets could not.
    # Through the whole pipeline on an article, the first bundle refused 121 legitimate texts
    # that v5 published, and these were most of them:
    #
    #   Our team killed it at the Surat trade fair this year.       0.999999
    #   We destroyed our old dispatch record: 4,200 parcels in one day.
    #   We are bleeding margin on this SKU and need a cheaper source.
    #   Give us your target price and we will destroy it.
    #
    # Trade English is violent and negative by habit - killed, destroyed, murdered, bleeding,
    # nightmare, disaster, took a beating - and none of it is a complaint. eval_b2b_negotiation
    # .csv exists in this repo because an earlier model made exactly this mistake, which is
    # the part that should have warned me.
    "Our team killed it at the {place} trade fair this year.",
    "We destroyed our old dispatch record: 4,200 parcels in one day.",
    "The new line absolutely murders our old cycle time.",
    "We are bleeding margin on this SKU and need a cheaper source.",
    "Give us your target price and we will destroy it.",
    "We will beat any quote you have in hand, send it across.",
    "Their quote blew ours out of the water, so we revised it.",
    "That batch was a disaster and we ate the loss ourselves.",
    "The old packaging line was a nightmare to maintain.",
    "We took a beating on freight last monsoon.",
    "This SKU is killing our margins, we need to re-rate it.",
    "The monsoon murdered our dispatch numbers in July.",
    "We want to kill the middleman model and sell direct.",
    "Our competitor is getting slaughtered on delivery times.",
    "That tender was brutal but we came out ahead.",
    "We are fighting for every paisa on this contract.",
    "The {item} market is bleeding right now, rates are all over.",
    "We smashed our export target this quarter.",
    "Cutting the lead time in half nearly broke the team.",
    "Honestly a painful quarter, but the order book is healthy.",
    "We crushed the backlog over the weekend.",
    "The old process was torture; the new one takes four minutes.",
    "Rates are brutal this season but volumes are up.",
    "We are drowning in enquiries since the fair.",
    "That client is a beast to service but worth it.",
    "We bled cash for two years before this line paid off.",
    # FIRM NEGOTIATION AND CONTRACT LANGUAGE, the largest remaining block of false refusals:
    # 16 of 60 came from eval_implied_threats' `firm_business_language` group, which exists in
    # this repo as the hard-negative set for threats and turns out to be the hard-negative set
    # for complaints too. A buyer stating a consequence is not a complaint about a seller:
    #
    #   We will walk away from this deal if the terms do not change.    0.998
    #   We will hold them to the penalty clause if this slips again.    0.987
    #   If you cannot meet the deadline, say so now and we will go elsewhere.
    #
    # All of that is how a contract gets negotiated, and refusing it would make the platform
    # useless for the conversation it exists to host - the same argument as the proposal, in a
    # different register.
    "We will walk away from this deal if the terms do not change.",
    "If you cannot meet the deadline, say so now and we will go elsewhere.",
    "We will hold you to the penalty clause if this slips again.",
    "Do not involve a third party in this negotiation without telling us.",
    "We are prepared to place the order elsewhere if the rate does not move.",
    "The contract allows us to cancel after two missed milestones.",
    "We need a firm commitment on {thing} before we release the advance.",
    "Our board will not approve this at the current price.",
    "We expect the revised quotation by {day} or we close the file.",
    "Liquidated damages apply from the first day of delay, as agreed.",
    "This is our final offer; above it the project does not work for us.",
    "We will escalate to the steering committee if this is not resolved.",
    "Either the specification changes or the timeline does, not neither.",
    "We reserve the right to source from a second vendor this quarter.",
    "There is no joining fee, no deposit and no hidden charge in our scheme.",
    "No advance, no lock-in and no cancellation penalty on trial orders.",
    "We do not take deposits; payment is against delivery only.",
    "Our terms have no hidden charges and the annexure lists everything.",
    "I would not use them for urgent work, but their standard orders are fine.",
    "Good for bulk, not for small lots - depends what you need.",
    "Their strength is price, not speed; choose accordingly.",
    "It rained all day and the courier could not reach us.",
    "The roads were flooded so nothing moved yesterday.",
    "Factory is shut for the festival so dispatch resumes {day}.",
    # ROUND 4. accept_system.py with public_label left 39 genuine false positives, and they
    # fall into eight shapes. The model has learned "negative about a counterparty" when the
    # target is "complaint", and these are the families that separate them.
    #
    # PROFESSIONAL CRITICISM - an assessment, with a reason, often with a caveat. This is how
    # members help each other choose a supplier and it must publish.
    "I disagree with his approach to pricing, though he explained his reasoning clearly.",
    "His quotation was unrealistic for our budget, so we went with another mill.",
    "Her lead time estimate was optimistic and the order slipped by ten days.",
    "Their technical answer was not detailed enough for our compliance team.",
    "We do not agree with their assessment of demand for this season.",
    "His costing sheet looks wrong to us; we have asked for a revised version.",
    "He is new to this category and still learning, so we inspect every consignment.",
    "I would not use them for urgent work, but their standard orders are reliable.",
    "Good for bulk, not for small lots - it depends what you need.",
    "Their strength is price rather than speed; choose accordingly.",
    "The supplier disagreed with our test results and sent the sample to a third-party lab.",
    "Our auditor found three gaps in their process and they fixed all of them.",
    "He misread our drawing and machined the part 2 mm short. We resent the file.",
    "Strong on finishing, weaker on documentation - worth knowing before you order.",
    "A good fit for standard sizes, less so for custom work.",
    # LEGAL AND CONTRACT NOTICE - a consequence stated under an agreement. Not a grievance;
    # this is the machinery a contract runs on.
    "We will escalate this to our legal team if the invoice remains unpaid.",
    "We may have to terminate the contract if quality does not improve.",
    "There will be commercial consequences for the partnership if this continues.",
    "Continued non-payment will result in suspension of further supply.",
    "Please treat this as formal notice of our intention to rescind.",
    "We will withhold the final payment until the defects are rectified.",
    "We will be forced to reconsider the renewal in March.",
    "Failure to respond within ten days will be treated as refusal.",
    "This is the last time we will accept a late consignment.",
    "We cannot be held responsible for what happens after handover.",
    "The agreement permits cancellation on seven days written notice.",
    "Liquidated damages apply from the first day of delay, as agreed.",
    "We reserve the right to pursue damages for the delayed consignment.",
    "This matter will be referred to arbitration as per the contract.",
    "Repeated short deliveries will affect the vendor rating.",
    # PROPER CHANNEL - describing that a dispute is being handled properly, which is the
    # OPPOSITE of a public callout and was being refused as if it were one.
    "Arbitration clause has been invoked. Nothing further to discuss publicly.",
    "We have written to their account manager and copied their regional office.",
    "We have escalated the matter through their support portal as the contract requires.",
    "Our legal team is corresponding with them about the shortfall.",
    "Raised a formal complaint on their grievance portal, reference number received.",
    "The matter is with the insurer and we are waiting for the surveyor.",
    "We are following the escalation matrix agreed in the contract.",
    # SAFETY ADVICE - warning other members about a pattern, not about a company.
    "Report abusive messages instead of replying to them.",
    "A supplier asking for a processing fee up front is a warning sign.",
    "Never release goods against a screenshot of a payment.",
    "Check the GST number on the portal before you ship on credit.",
    "If a buyer refuses a call before paying an advance, be careful.",
    "Verify the bank details by phone, not by email, before the first transfer.",
    "A rate far below the market usually means something is wrong with the goods.",
    # BALANCED REVIEW - a problem that was resolved, which reads as a complaint to a model
    # that only sees the problem half.
    "Their dispatch team is slow to respond but the goods are always as described.",
    "The machine stopped working after a week. Their technician came the next day and fixed it.",
    "Packing was fine but the colour was a shade darker than the sample; we accepted the lot.",
    "There was a delay, they told us in advance and credited the freight.",
    "One carton was short and the replacement reached us within 48 hours.",
    "Not the cheapest, but we have never had a quality issue in three years.",
    "They had a bad quarter on delivery and have since fixed the process.",
    # ANNOUNCEMENTS AND NOTICES - plain operational news.
    "We have moved to a bigger warehouse in {place}. Our phone numbers stay the same.",
    "The factory is closed for the festival from the 12th; dispatch resumes on the 18th.",
    "Our new catalogue for the season is live on the portal.",
    "We have added a second production line and lead times are shorter now.",
    "New GST registration for our {place} branch is effective from this month.",
    "Hamare paas 20 saal ka experience hai, quality mein koi compromise nahi.",
    "Godown mein jagah kam hai isliye abhi zyada stock nahi le sakte.",
    "i dont know how to use this app properly please help",
    "My son broke his arm playing football, so I was away from the shop last week.",
    # Hinglish negatives
    "{thing_cap} time par aa gaya, dhanyavaad.",
    "{firm_cap} ke saath humara experience acha raha.",
    "Partial delivery chalega agar baaki {day} tak aa jaye.",
]


# Neutral openers and tails, for the negatives only. They need composition as much as the
# positives do - 48 frames and a slot list produced 1,327 distinct rows against the 25,000
# asked for, and a model given 44,000 composed positives against 1,327 flat negatives learns
# sentence length. What they must NOT borrow is the positives' openers and tails: a case study
# does not begin "Posting this here because I am out of options" or end "Never again."
NEUTRAL_OPENERS = ["", "", "", "",
                   "A note for members sourcing this month.",
                   "Sharing something that worked for us.",
                   "Quick update from our side.",
                   "For the record, and for anyone comparing quotes:",
                   "Posting this because people keep asking.",
                   "We get this question a lot.",
                   "Some notes from last season:",
                   "Following up on {thing} as promised.",
                   "Reference our discussion at {place}.",
                   "Short process note.",
                   "Adding to the thread on transit damage.",
                   "In case it helps someone planning a bulk order:",
                   "From our operations team:",
                   "Our standard terms, since they came up:",
                   "Worth writing down while it is fresh.",
                   "One more thing on {thing}."]

NEUTRAL_TAILS = ["", "", "", "",
                 "Happy to share the format if useful.",
                 "Details on request.",
                 "Full terms are on the product page.",
                 "Ask us if anything is unclear.",
                 "We are glad it worked out.",
                 "That has been our experience, at least.",
                 "Hope this helps someone.",
                 "Numbers attached.",
                 "Open to better ideas from the group.",
                 "More on this next quarter.",
                 "Thanks to everyone who advised us.",
                 "Comments welcome.",
                 "We will post the follow-up once the audit closes.",
                 "Standard across all our lanes.",
                 "Nothing unusual, but worth stating."]


def fill(rng: random.Random, frame: str) -> str:
    item, parcel = rng.choice(ITEMS), rng.choice(PARCEL)
    thing, firm, desk = rng.choice(THINGS), rng.choice(FIRMS), rng.choice(DESKS)
    money, place = rng.choice(MONEY), rng.choice(PLACES)
    return frame.format(
        item=item, item_cap=item[0].upper() + item[1:],
        parcel=parcel, thing=thing, thing_cap=thing[0].upper() + thing[1:],
        firm=firm, firm_cap=firm[0].upper() + firm[1:],
        desk=desk, desk_cap=desk[0].upper() + desk[1:],
        money=money, money_cap=money[0].upper() + money[1:],
        span=rng.choice(SPANS), count=rng.choice(COUNTS), day=rng.choice(DAYS),
        place=place, place_cap=place[0].upper() + place[1:])


def compose(rng: random.Random, core: str, openers, tails) -> tuple[str, str]:
    """A row is an opener, a core and a tail drawn independently.

    Returns the text and the SKELETON - the three frames joined - which goes in the `template`
    column. prepare_data.py groups on it, so a row and its siblings cannot land on opposite
    sides of the split. Grouping on the core alone would be wrong in the other direction: it
    would put a third of the file in one group and starve the split.
    """
    opener, tail = rng.choice(openers), rng.choice(tails)
    parts = [fill(rng, p) for p in (opener, core, tail) if p]
    return " ".join(parts), " | ".join(p for p in (opener, core, tail) if p)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "res" / "support_complaints.csv")
    ap.add_argument("--positives", type=int, default=50_000)
    ap.add_argument("--negatives", type=int, default=25_000)
    ap.add_argument("--long-articles", type=int, default=12_000,
                    help="editorial articles WITH a setback sentence inside, label 0")
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--reconcile", action="store_true",
                    help="also print the label-0 templates in res/public_callouts.csv that "
                         "contradict these rows")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    rows: list[dict] = []
    seen: set[str] = set()

    def emit(frames, count, label, category, openers, tails):
        made = guard = 0
        limit = count * 80
        while made < count and guard < limit:
            guard += 1
            text, skeleton = compose(rng, rng.choice(frames), openers, tails)
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            rows.append({"text": text, "label": label, "category": category,
                         "kind": "whole", "template": skeleton})
            made += 1
        return made

    # The positives split three ways. Emotional outcry is a third of the file on purpose: it
    # is the half the existing data failed to generalise, and it is shorter, so it needs more
    # rows to cover the same ground.
    want_support = int(args.positives * 0.55)
    want_emotional = int(args.positives * 0.33)
    want_hinglish = args.positives - want_support - want_emotional
    s = emit(SUPPORT_FRAMES, want_support, 1, "support_complaint", OPENERS, TAILS)
    e = emit(EMOTIONAL_FRAMES, want_emotional, 1, "emotional_outcry", OPENERS, TAILS)
    h = emit(HINGLISH_FRAMES, want_hinglish, 1, "support_complaint_hi", OPENERS, TAILS)
    # Negatives compose too, from their own openers and tails. See the note above them.
    n = emit(NEGATIVE_FRAMES, args.negatives, 0, "legitimate_commercial",
             NEUTRAL_OPENERS, NEUTRAL_TAILS)

    # LONG ARTICLES, which nothing above produces. Six legitimate long articles were being
    # refused - "How our family pickle business survived the lockdown", "A visit to a cotton
    # farm in Vidarbha", "Why we stopped selling to one big retailer" - and every one of them
    # contains a sentence about something going wrong. That is what makes it a story rather
    # than a brochure.
    #
    # So these are built deliberately WITH a setback inside: four to eight editorial sentences
    # and one complaint sentence among them, labelled 0. A model that has only ever seen the
    # complaint sentence alone learns that the paragraph around it does not matter, and a
    # member writing honestly about a bad quarter gets told to rewrite.
    openers = ["Lessons from a difficult season", "What we learned the hard way",
               "Notes from the shop floor", "How we fixed our dispatch problem",
               "Our first year exporting", "Why we changed suppliers",
               "A visit to our weaving unit", "Three things that cost us money",
               "What nobody tells you about bulk orders", "The year we nearly closed"]
    made = 0
    for _ in range(args.long_articles * 4):
        if made >= args.long_articles:
            break
        body = [fill(rng, f) for f in rng.sample(NEGATIVE_FRAMES, rng.randint(4, 7))]
        setback = fill(rng, rng.choice(SUPPORT_FRAMES + EMOTIONAL_FRAMES))
        body.insert(rng.randint(1, len(body) - 1), setback)
        text = rng.choice(openers) + "\n\n" + " ".join(body)
        if text.lower() in seen:
            continue
        seen.add(text.lower())
        rows.append({"text": text, "label": 0, "category": "long_article",
                     "kind": "whole", "template": "long_article"})
        made += 1
    counts = made

    rng.shuffle(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["text", "label", "category", "kind", "template"])
        w.writeheader()
        w.writerows(rows)

    print(f"{args.out}: {len(rows):,} rows")
    print(f"  {s:,} support complaints        (label 1)")
    print(f"  {e:,} emotional outcry          (label 1)")
    print(f"  {h:,} Hinglish complaints       (label 1)")
    print(f"  {n:,} legitimate commercial     (label 0)")
    print(f"  {counts:,} long articles with a setback inside (label 0)")
    print(f"  {len(set(r['template'] for r in rows)):,} distinct skeletons")

    if args.reconcile:
        reconcile(ROOT / "res" / "public_callouts.csv")

    short = [(nm, got, want) for nm, got, want in
             (("support", s, want_support), ("emotional", e, want_emotional),
              ("hinglish", h, want_hinglish), ("negatives", n, args.negatives)) if got < want]
    for nm, got, want in short:
        print(f"\n  WARNING: asked for {want:,} {nm} rows and produced {got:,}. The frame and "
              f"slot lists bound how many distinct rows exist - add frames rather than "
              f"accepting near-duplicates.", file=sys.stderr)
    return 1 if short else 0


def reconcile(callouts: Path) -> None:
    """Name the rows that now contradict these, instead of letting prepare_data.py decide.

    The label-0 half of res/public_callouts.csv holds about 10,400 rows that are themselves
    complaints - they were labelled allow so the model would not refuse ordinary B2B dispute
    language, and that decision was right for a proposal. It is the opposite of what this file
    teaches for an article. Printing them is the whole point: a contradiction resolved by file
    order is a contradiction nobody decided.
    """
    import re
    if not callouts.exists():
        print(f"\n  (no {callouts.name} to reconcile against)", file=sys.stderr)
        return
    import csv as _csv
    rows = list(_csv.DictReader(callouts.open(encoding="utf-8")))
    marks = re.compile(r"\b(poor|nobody has replied|dropped|short-shipped|disappointed|"
                       r"cannot keep absorbing|expected better|late again|missed the deadline|"
                       r"wrong for the third|frustrat|worried|urgent action|nahi aaya|"
                       r"concerned|escalate|legal team)", re.I)
    hits: dict[str, int] = {}
    for r in rows:
        if r["label"] == "0" and marks.search(r["template"]):
            hits[r["template"]] = hits.get(r["template"], 0) + 1
    total = sum(hits.values())
    print(f"\nRECONCILE: {total:,} label-0 rows in {callouts.name} are complaints, across "
          f"{len(hits)} templates.")
    print("These say ALLOW for the same genre this file says REFUSE. One model cannot hold "
          "both\nunless it is told the surface. Listed loudest first:\n")
    for tpl, n in sorted(hits.items(), key=lambda kv: -kv[1]):
        print(f"  {n:6,}  {tpl[:96]}")


if __name__ == "__main__":
    raise SystemExit(main())
