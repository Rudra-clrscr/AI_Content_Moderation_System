"""Workplace grievance: an employee's fury at their employer, published to the network.

    python training/generate_workplace_grievances.py --out res/workplace_grievances.csv

THE GENRE THIS ADDS, AND WHY THE EXISTING FILE DOES NOT COVER IT.

res/support_complaints.csv holds one axis of grievance: a BUYER against a SELLER. The desk
that will not answer, the refund that never came, the consignment that arrived crushed. Every
positive frame in it points outward at a counterparty the writer paid money to.

This file is the other axis: an EMPLOYEE against an EMPLOYER.

    label 1   "The reward for doing a good job here is getting assigned everyone else's
               unfinished shit."
              "Constantly asked to do the work of three people for the pay of half a person."
              "This job is draining the life out of everyone, and management couldn't give a
               damn as long as numbers look green."
    label 0   "We pay overtime at double rate and the shift roster is published a month ahead."
              "Five signs your shop floor is burning people out, and what we changed."
              "Damn good machine. Paid for itself in eight months."

A model trained only on buyer-vs-seller does not reliably catch these: there is no refund, no
consignment, no supplier. The vocabulary is management, shifts, appraisals, HR, overtime - and
the grammatical subject is "this place" rather than "that vendor". Measured on models/
grievance-v3 before this file existed, against the five sentences the product owner supplied:

    0.0283  The reward for doing a good job here is getting assigned everyone else's ... shit.
    0.9393  Constantly asked to do the work of three people for the pay of half a person.
    0.9724  Every single task is an 'urgent fire,' simply because management has no brain ...
    0.0235  They preach work-life balance on LinkedIn while demanding late nights and weekends.
    0.9762  This job is draining the life out of everyone, and management couldn't give a damn.

The shipped threshold is 0.978, so ALL FIVE publish today - including the three the model is
clearly suspicious of, which is its own lesson: the two it scores at 0.02 are a genre it has
never seen, and the three at 0.94-0.98 are the ones whose phrasing happens to rhyme with a
supplier complaint. Neither group clears the bar.

WHAT ALREADY WORKS, AND MUST STILL WORK AFTERWARDS. The same run scored the profane and
literal negatives correctly without ever having been trained on them:

    0.0241  Damn good machine. Paid for itself in eight months.
    0.0209  Toxic waste disposal, 12 licensed tankers, pan-India coverage.
    0.0210  Dumpster bins, 1100 litre HDPE, wheeled and powder coated.
    0.0215  Five signs your shop floor is burning people out, and what we changed.

That is the capability this file's negatives exist to protect. Teaching the positives without
PROFANE_NEGATIVE_FRAMES would move those four, and three of the four are somebody's business.

WHY THIS IS THE SAME QUESTION AND NOT A NEW MODEL. BONC is a trade platform. An employee with
a grievance has a manager, an HR desk, a works committee, a labour commissioner and, if it
comes to it, a court. An article broadcast to every supplier and buyer on the network is none
of those, exactly as a refund complaint is not. Same question, same head, new genre - which is
why this ships as additional data for models/grievance-* rather than a third classifier.

PROFANITY IS NOT THE SIGNAL, AND THIS FILE MUST NOT TEACH THAT IT IS.

The request that prompted this file named "damn" and "toxic dumpster" specifically, and that is
the trap. Abuse and profanity are already the MAIN model's job (models/v6, surface-blind, every
surface). If this head learns that "damn" means refuse, then:

    "Damn good machine, paid for itself in eight months."        <- a product review
    "Toxic waste disposal, 12 licensed tankers, pan-India."      <- a listing
    "Dumpster bins, 1100 litre HDPE, wheeled, powder coated."    <- a listing

all get refused, and two of those three are somebody's business. So every profanity the
positives lean on appears in the negatives in its ordinary trade sense, and in volume:
PROFANE_NEGATIVE_FRAMES below is deliberately as long as the profane positive register. The
word carries no weight on its own; the thing it is pointed at is what decides.

THE HARDEST NEGATIVES ARE THE ONES ABOUT WORKPLACES.

Four families, all of them things members legitimately write here, all of them sharing their
whole vocabulary with the positives:

  employer brand     "We pay overtime at double rate and publish the roster a month ahead."
  management writing "Five signs your shop floor is burning people out, and what we changed."
  labour compliance  "Overtime beyond 48 hours a week needs written consent under the Act."
  hiring             "Hiring 3 CNC operators, two shifts, overtime paid, PF and ESI from day one."

Management writing is the worst of them, and the reason is structural rather than lexical: an
article about burnout CONTAINS the grievance. "People were doing the work of three and we were
losing them every quarter" is a sentence in an honest retention case study and a sentence in a
rant, and nothing in the words tells them apart. Only the frame does - a cause given, a change
made, a lesson drawn - which is why CONTRAST_* below builds the publishable half out of the
same cores as the positives, the device generate_support_complaints.py landed on in round 5
after four rounds of adding separate negative frames stopped working.

SECOND PERSON IS A GRIEVANCE; THIRD PERSON IS USUALLY NOT. "They preach work-life balance on
LinkedIn while demanding weekend shifts" is one of the five supplied positives. "Firms that
preach work-life balance and demand weekend shifts do not keep staff" is commentary on an
industry, and it publishes. The positives are first person about a specific place; the
commentary negatives are third person about a category, and there are enough of the latter
that the model cannot take "weekend shifts" as the feature.

COLLISION WITH THE EXISTING FILE. res/support_complaints.csv already carries workplace-shaped
label-0 rows, put there as B2B idiom: "Cutting the lead time in half nearly broke the team",
"The old process was torture; the new one takes four minutes", "Monday mornings are harder than
they need to be". Those stay label 0 and nothing here contradicts them - none of them is a
grievance against an employer, they are a firm describing its own work. `--check-collisions`
verifies that claim against the file rather than asserting it, because a text carrying both
labels across two generators is the one failure train_grievance.py can only drop, not fix.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# ---- slot vocabulary -----------------------------------------------------------------
# Deliberately NOT the supplier vocabulary of generate_support_complaints.py. The whole point
# of this file is that the nouns are different: a boss instead of a vendor, a shift instead of
# a consignment, an appraisal instead of an invoice.
PLACE_OF_WORK = ["this company", "this place", "this firm", "this organisation", "this office",
                 "this factory", "my workplace", "this plant", "this department", "this team"]
BOSSES = ["management", "the management", "my manager", "the boss", "my reporting manager",
          "the senior leadership", "my team lead", "the owner", "the HR head", "the floor supervisor",
          "upper management", "the directors"]
DEPTS = ["HR", "the HR team", "accounts", "the payroll team", "the admin department",
          "the operations team", "the sales team", "the production floor", "the dispatch team"]
ROLES = ["operator", "supervisor", "accountant", "sales executive", "store keeper",
         "quality inspector", "machine operator", "driver", "field technician", "junior engineer",
         "data entry operator", "despatch clerk", "fitter", "welder", "packer"]
SPANS = ["two years", "eight months", "three years", "five years", "eleven months",
         "the last two quarters", "nearly a decade", "six years", "four months"]
COUNTS = ["three", "four", "five", "six", "eight", "a dozen", "countless"]
MONEY_WORK = ["my salary", "last month's salary", "the overtime money", "my arrears",
              "the bonus", "the increment", "my full and final settlement",
              "the travel reimbursement", "the PF contribution", "my notice-period dues"]
HOURS = ["twelve-hour", "fourteen-hour", "sixteen-hour", "eleven-hour", "thirteen-hour"]
TIMES = ["11 at night", "midnight", "2 a.m.", "well past 10", "the last bus"]
PERKS = ["work-life balance", "employee wellbeing", "a people-first culture",
         "mental health awareness", "flexible hours", "a family atmosphere",
         "open-door policy", "growth opportunities"]
PLACES = ["Surat", "Ludhiana", "Tiruppur", "Coimbatore", "Rajkot", "Noida", "Pune", "Indore"]
DAYS = ["Monday", "Friday", "Saturday", "the 7th", "month end", "Diwali", "every weekend"]
ITEMS = ["CNC lathe", "injection moulding machine", "packing line", "boiler", "forklift",
         "power press", "air compressor", "dyeing machine", "stitching unit", "diesel genset"]

# ---- openers ---------------------------------------------------------------------------
# Independent of the core, as in generate_support_complaints.py, so a row's skeleton is not
# its core sentence. Empty entries are weighted in because plenty of posts open with the rant.
OPENERS = ["", "", "", "", "",
           "I resigned last week and I am going to say why.",
           "Posting this because someone should.",
           "Last day today, so here is the honest version.",
           "I have kept quiet for too long.",
           "For anyone thinking of joining them.",
           "Throwaway details, real experience.",
           "I will probably get into trouble for this.",
           "Two years here, and this is what I learned.",
           "Not naming names, but people will know.",
           "Writing this at {time} because I am still at my desk.",
           "I loved this job once.",
           "Some context: I joined as a {role}.",
           "Before anyone says I should have raised it internally - I did, {count} times.",
           "This is not a rant. It is a record.",
           "Adding my experience to the thread about working conditions.",
           "Asking the group: is this normal?",
           "I need to get this off my chest."]

TAILS = ["", "", "", "", "",
         "Think twice before you sign.",
         "Has anyone else worked somewhere like this?",
         "I would not wish this on anyone.",
         "Zero accountability, top to bottom.",
         "Still owed money as I write this.",
         "Do better.",
         "That is all.",
         "Never again.",
         "Enough.",
         "Ask me privately if you want the name.",
         "I have the messages if anyone doubts it.",
         "Burnt out at {span} in.",
         "Rate them honestly if you have worked there.",
         "Life is too short for this.",
         "Please look after yourselves."]

# ---- label 1: the grievance against the employer ---------------------------------------
# Eight families. Each is a real shape the five supplied sentences fall into, generalised:
# workload, incompetent planning, hypocrisy, pay, disrespect, favouritism, attrition, exit.
WORKPLACE_FRAMES = [
    # workload and the reward for competence - the first supplied sentence's family
    "The reward for doing a good job here is getting assigned everyone else's unfinished work.",
    "Do your job well at {place_of_work} and you inherit the workload of whoever did theirs badly.",
    "Constantly asked to do the work of three people for the pay of half a person.",
    "I am doing the job of {count} people and getting paid for most of one.",
    "Every competent person here is carrying {count} others.",
    "{bosses_cap} keeps adding to my plate and taking nothing off it.",
    "I have had no handover, no backup and no leave in {span}.",
    "The {hours} days are not an emergency any more, they are the schedule.",
    "I have not taken a full weekend off in {span}.",
    "Staying till {time} is expected and never acknowledged.",
    "There is no headcount, so the plan is that we absorb it.",
    "The workload doubled when two people left and nobody was replaced.",
    "I eat lunch at my desk every day because there is no time.",
    # planning, and the permanent emergency - the second supplied sentence's family
    "Every single task here is an urgent fire because nobody plans anything.",
    "Every task is an emergency, which tells you exactly how much planning happens upstairs.",
    "{bosses_cap} has no idea what we actually do all day.",
    "Deadlines are invented in a meeting and handed down without asking anyone who does the work.",
    "Priorities change three times a week and we are blamed for the churn.",
    "They changed the target mid-month and then asked why we missed it.",
    "We find out about decisions that affect us from the WhatsApp group.",
    "Nobody upstairs has planned anything past tomorrow in {span}.",
    "{bosses_cap} could not run a tea stall, let alone a {items}.",
    "The same crisis happens every month and nobody fixes the cause.",
    "They cut the staff and kept the targets.",
    "Every process here is a workaround for a decision nobody will revisit.",
    # hypocrisy - the fourth supplied sentence's family
    "They preach {perks} in public while demanding late nights and weekend shifts.",
    "{perks_cap} is in the induction deck and nowhere else.",
    "They post about {perks} online and roster us on {day}.",
    "The values are on the wall and nowhere in the building.",
    "There is an open-door policy as long as you do not walk through it.",
    "Feedback is welcome right up to the moment you give any.",
    "They celebrate {perks} on social media and cancel our leave the same week.",
    "We are a family, apparently, right until someone asks about the increment.",
    "The mental health webinar was scheduled at 9 p.m., which tells you everything.",
    # The "they preach X while demanding Y" shape, named platforms included. This family was
    # 9 frames of 85 in the first bundle, and the supplied sentence "They preach work-life
    # balance on LinkedIn while demanding late nights and weekend shifts" moved only 0.023 ->
    # 0.140 on a 3,000-row smoke run while the other four cleared 0.98. The reason is in this
    # file by design: it is three words from a NEGATIVE - "Firms that preach {perks} and roster
    # weekend shifts do not keep staff" - and the only thing separating them is "they" (one
    # employer, a grievance) from "firms that" (a category, commentary). A boundary that fine
    # needs more than nine examples on each side, so the positives get these and the
    # third-person commentary negatives stay exactly as they are.
    "They preach {perks} on LinkedIn while demanding late nights and weekend shifts.",
    "They post about {perks} on LinkedIn and roster us through the weekend.",
    "They advertise {perks} on the careers page and demand {hours} days.",
    "They talk about {perks} in every town hall while the overtime goes unpaid.",
    "They preach {perks} to the press and cancel leave at {day}.",
    "Plenty about {perks} on their Instagram, nothing about it in the roster.",
    "They demand weekend shifts from the same people they call family on LinkedIn.",
    "The careers page promises {perks}; the roster promises {hours} days.",
    "They won an award for {perks} the same month they cut the shift allowance.",
    # pay
    "{money_work_cap} is three weeks late and nobody will say why.",
    "I am still chasing {money_work} after {span}.",
    "They promised an increment at six months and it has been {span}.",
    "The appraisal was a formality; the number was decided before the conversation.",
    "Overtime is compulsory and unpaid.",
    "They deduct for five minutes late and pay nothing for three hours extra.",
    "My {money_work} has been held back over paperwork they lost themselves.",
    "The increment this year was less than the inflation on my bus fare.",
    "They withheld my {money_work} to force me to serve the notice period.",
    "Freshers are hired at more than I make after {span} here.",
    # disrespect and humiliation
    "{bosses_cap} shouted at me in front of the whole floor.",
    "Being spoken to like that in front of my juniors was humiliating.",
    "{bosses_cap} does not speak to the staff, he speaks at them.",
    "Mistakes are not corrected here, they are used.",
    "I was told I should be grateful to have a job.",
    "They read our messages and call it monitoring.",
    "Asking a question is treated as insubordination.",
    "{bosses_cap} takes the credit in the review and the blame goes down the line.",
    "I was denied leave for my own sister's wedding.",
    "They refused leave when my father was in hospital.",
    "The toilets have not been cleaned properly in months and nobody at {depts} cares.",
    "We were made to stand through a two-hour dressing-down for a target we were never told.",
    # favouritism and politics
    "Promotions here go to whoever is closest to {bosses}, not whoever does the work.",
    "There are two rules at {place_of_work}: one for the owner's relatives and one for us.",
    "The appraisal depends on who you had lunch with, not what you shipped.",
    "Raising a problem gets you labelled negative.",
    "The people who complain the least get the most work and the smallest increment.",
    "{depts_cap} exists to protect {bosses}, not us.",
    "I reported harassment to {depts} and it went nowhere.",
    "The person who was promoted had been here four months.",
    # attrition and burnout - the fifth supplied sentence's family
    "This job is draining the life out of everyone and {bosses} does not care as long as the numbers look green.",
    "{counts_cap} people have left my team in {span} and nobody has asked why.",
    "Everyone good has gone and {bosses} calls it attrition in the market.",
    "I am burnt out at {span} in and I am {span} from thirty.",
    "I have started dreading Sunday evenings.",
    "My health has gone downhill since I joined and I know exactly why.",
    "This place has cost me my sleep, my weekends and two friendships.",
    "We are all just waiting for our offers to come through.",
    "The exit interview asked nothing anyone would act on.",
    "I have not seen my children awake on a weekday in {span}.",
    "The stress has followed me home and my family has noticed.",
    # the exit
    "They made me serve the full notice period and then withheld the relieving letter.",
    "My experience letter has been pending for {span} after resignation.",
    "They threatened to block my background verification if I left.",
    "I was asked to train my replacement while my {money_work} was still unpaid.",
    "Resigning here is treated as a betrayal rather than a decision.",
]

# ---- label 1, round 3: negation, and the families the paraphrases missed -----------------
# ROUND 2 OVERCORRECTED, AND IT DID SO IN THE MIRROR IMAGE OF ROUND 1. The good-outcome family
# took false refusals from 30.0% to 10.0% on eval_grievance_workplace.csv - it worked - but
# recall fell from 76.9% to 53.8%, and on the supplier sets from 92.3%/94.4% to 80.8%/66.7%.
#
# The misses were not borderline, which is what gives the cause away:
#
#     0.000  Nobody here gives a damn as long as the dispatch number looks right.
#     0.000  Six of us have gone in a year and not one exit interview changed anything.
#     0.773  Nobody plans anything, so everything is an emergency by Thursday.
#
# Round 2's negation block is label 0 and has no counterpart at label 1, so "nobody", "no one"
# and "not one" became allow-markers. That is round 1's bug again - a surface feature made to
# predict the label - and the lesson generalises past this file: any feature added to one side
# only is a feature the model will use INSTEAD of the meaning. Negation has to be orthogonal to
# the label, so it now appears in volume on both.
#
# The second group of misses is thinness rather than bias. Favouritism, appraisal theatre,
# attrition-ignored and no-consultation had four to eight frames each, and the independent set
# paraphrases them: "Promotions go to whoever sits nearest the owner" against this file's
# "Promotions here go to whoever is closest to {bosses}". Close, and 0.001. So those families
# get broadened - in different words from the eval set, which is checked rather than trusted
# (--check-collisions, and train_incremental.py's leakage check).
NEGATION_POSITIVE_FRAMES = [
    "Nobody here gives a damn how long you have been doing the work.",
    "Nobody upstairs has planned a single week properly in {span}.",
    "No one has ever explained why {money_work} arrives late.",
    "Not one of my {count} complaints to {depts} has gone anywhere.",
    "Not a single exit interview in {span} has changed anything.",
    "Nobody told us the shift pattern was changing until the roster went up.",
    "No one checks whether the overtime we work is ever paid.",
    "I have never once been thanked for covering someone else's week.",
    "Nobody has ever asked the floor what would actually help.",
    "Not one person in {depts} will put anything in writing.",
    "No one here is going to tell {bosses} the plan does not work.",
    "Nobody has been replaced since the two resignations in March.",
    "I have not had a full weekend off since the new line started.",
    "I have not seen my children awake on a weekday in {span}.",
    "Never in {span} have I been given a reason, only a deadline.",
    "No amount of asking gets {money_work} moving.",
    "Nothing anybody raises at the meeting survives the walk back to the floor.",
    "There is no version of this roster that lets anyone rest.",
    "Nobody who has complained here has had it end well for them.",
    "No one has told me what my notice period actually is.",
]

# The thin families, broadened. Deliberately NOT the eval set's phrasings.
THIN_FAMILY_FRAMES = [
    # favouritism
    "The promotion went to whoever eats lunch with {bosses}.",
    "Being related to the owner is the only career path here.",
    "The quiet competent people get the work; the loud ones get the grade.",
    "Advancement depends on visibility, not output.",
    "The last three promotions all sat on the same side of the office.",
    "Merit is the word they use for whoever they had already chosen.",
    # appraisal theatre
    "The rating was filled in before anyone sat down with me.",
    "My appraisal was a form being read aloud at me.",
    "The appraisal conversation happened after the number was locked.",
    "Nobody can tell me which criterion produced my rating.",
    "The review cycle is a ritual with a predetermined outcome.",
    "I was marked down for something that was never once raised during the year.",
    # attrition ignored
    "{counts_cap} resignations this year and not a single question asked about why.",
    "Half my team has turned over and {bosses} calls it the market.",
    "People keep leaving and the only response is a hiring post.",
    "We have trained and lost {count} {role}s in {span}.",
    "Everyone with options has used them.",
    "The handover list is longer than the team.",
    # no consultation
    "We find out what we are doing when the roster appears.",
    "Decisions about our shifts are announced, never discussed.",
    "The people who set the plan have never asked the people who run it.",
    "Changes arrive as instructions and leave as problems for us.",
    "I learned my department was being restructured from a notice board.",
    "Nobody who does this job was in the room when it was redesigned.",
    # burnout, the family that scored 0.004
    "I get home after my family is asleep and leave before they wake.",
    "My weekends have belonged to this place for {span}.",
    "I cannot remember the last day I did not think about work.",
    "The season ends and I am too tired to notice.",
    "I have run out of whatever it was that got me through last year.",
    # profane, cyclical and indifferent - the two that scored 0.000
    "Same mess every quarter, new deadline stapled to it.",
    "It is the same damn cycle and nobody breaks it.",
    "Another quarter, another pile of someone else's work.",
    "They do not care what it costs us as long as dispatch looks right.",
    "The numbers matter and we do not, and they have stopped pretending otherwise.",
]

# ---- label 1: the profane register -----------------------------------------------------
# The register the request named: damn, hell, shit, crap, bloody, "toxic dumpster". It is a
# SEPARATE family only so its share of the file can be set deliberately - it is the same
# label, and PROFANE_NEGATIVE_FRAMES below is its counterweight. Kept at mild profanity, which
# is what this genre actually uses in writing; slurs and targeted abuse are models/v6's job
# and putting them here would blur two different decisions.
PROFANE_FRAMES = [
    "The reward for doing a good job here is getting assigned everyone else's unfinished shit.",
    "This job is draining the life out of everyone, and management couldn't give a damn as long as numbers look green.",
    "{bosses_cap} couldn't give a damn about any of us.",
    "This place is a toxic dumpster and everyone inside it knows.",
    "{place_of_work_cap} is a toxic dumpster fire held together by people who are too tired to leave.",
    "The culture here is toxic, plain and simple.",
    "Working here has been a damn nightmare from the first week.",
    "Nobody gives a damn whether you sink or swim.",
    "I am damn tired of covering for {bosses}.",
    "What a bloody waste of two good years.",
    "The whole appraisal process is crap and everybody knows it.",
    "They do not care, and that is the damn problem.",
    "Every day here is the same shit with a different deadline.",
    "I am done cleaning up other people's shit for free.",
    "It is a hell of a way to treat people who built this place.",
    "Working at {place_of_work} is hell and I say that having had worse jobs.",
    "Management here is a damn joke.",
    "They dumped the whole mess on us and went home.",
    "Honestly, this place is rotten from the top down.",
    "The hypocrisy is damn breathtaking.",
    "I gave this place {span} and got shafted at the end of it.",
    "The amount of crap we put up with for this salary is unreal.",
    "A toxic workplace is still a toxic workplace with a ping pong table in it.",
    "{bosses_cap} has made this a bloody miserable place to work.",
    "Everything here is a damn fire drill.",
    "The place is a dumpster and {bosses} keeps painting the lid.",
    "I am sick of this toxic nonsense and I am not alone.",
    "Sick and damn tired of being the only one who shows up.",
    "What a toxic, draining, thankless place to spend {span}.",
]

# ---- label 1: Hinglish ------------------------------------------------------------------
# Members write this way, and the first bundle's Hinglish lesson is recorded in
# generate_support_complaints.py: 6,000 Hinglish positives against three Hinglish negatives
# taught the model the LANGUAGE instead of the genre. So the share here is small and
# HINGLISH_NEGATIVES below is proportionally large.
HINGLISH_FRAMES = [
    "{bosses_cap} ko humari koi parwah nahi hai.",
    "Salary {count} hafte late hai aur koi jawab nahi de raha.",
    "Roz {time} tak kaam karwate hain, overtime ka paisa nahi dete.",
    "Yahan kaam karne wale ki koi izzat nahi hai.",
    "Teen logon ka kaam ek aadmi se karwate hain.",
    "Chutti maangne par ehsaan jatate hain.",
    "{place_of_work_cap} ka mahaul bahut kharab ho gaya hai.",
    "Increment ke naam par mazaak hua hai is saal.",
    "Resign kar diya phir bhi full and final nahi mila.",
    "Sab achhe log chale gaye, management ko farak nahi padta.",
    "Do mahine se salary late aa rahi hai aur koi sunta nahi.",
    "Yahan kaam karne wale ki izzat nahi, sirf kaam hai.",
    "Kisi ne aaj tak nahi poocha ki humein kya pareshani hai.",
    "{bosses_cap} ne kabhi ek baar bhi shukriya nahi bola.",
    "Overtime karwate hain par register mein likhte bhi nahi.",
    "Promotion usi ko milta hai jo {bosses} ke saath baithta hai.",
    "Itne log chale gaye par kisi ne wajah nahi poochi.",
    "Ghar jata hoon to bachche so chuke hote hain.",
    "Har mahine wahi dikkat, naya bahana.",
    "Shift badalne ki khabar notice board se milti hai.",
]

# ---- label 0: legitimate employer, HR and management content ----------------------------
# Half the work, as in generate_support_complaints.py. Every content word the positives lean on
# - overtime, shifts, appraisal, attrition, burnout, management, HR, notice period - appears
# here in its ordinary sense. Four families, named in the docstring, plus hiring.
NEGATIVE_FRAMES = [
    # employer brand: a firm describing its own terms, which is a thing it is allowed to say
    "We pay overtime at double rate and the shift roster is published a month ahead.",
    "Salaries are credited on the 1st, without exception, including the month we had a bad quarter.",
    "PF and ESI start from day one, not after probation.",
    "Our notice period is thirty days both ways and we do not hold relieving letters.",
    "We publish our appraisal criteria before the cycle starts so nobody is guessing.",
    "Leave is 24 days a year and we do not ask what it is for.",
    "Shift allowance is paid in the same month, not the next one.",
    "Canteen food is subsidised and the same for the floor and the office.",
    "We shut the plant for the festival and nobody is asked to come in.",
    "Our {span} retention on the production floor is 91%, and we publish it.",
    "Every {role} gets safety training before they touch a {items}.",
    "The works committee meets monthly and the minutes go up on the notice board.",
    "We stopped the 9 p.m. calls. Productivity did not drop.",
    "Overtime is voluntary here and it is always paid.",
    "Full and final settlement is cleared within 15 days of the last working day.",
    "We moved to a five-day week two years ago and output is up.",
    "Our youngest supervisor joined as a {role} eleven years ago.",
    # management and HR writing: an article ABOUT these problems, which is the hardest family
    "Five signs your shop floor is burning people out, and what we changed.",
    "Why we stopped treating every order as an emergency, and what it cost to fix.",
    "A note on attrition: we lost {count} people in a quarter and here is what we found.",
    "How we rebuilt our appraisal process after the feedback was unanimous.",
    "What exit interviews told us that the engagement survey never did.",
    "Planning badly is expensive: our overtime bill was the proof.",
    "An honest look at why our best {role}s were leaving.",
    "On {perks}: the gap between the policy and the roster is the only number that matters.",
    "We asked the floor what was broken and the answers were uncomfortable.",
    "Workload audit: how we found three people doing the work of five.",
    "Running a factory without the permanent emergency - a process note.",
    "What a good handover looks like, and why nobody has time for one.",
    "Hiring is easier than retaining. Here is our retention checklist.",
    "Why we publish the shift roster a month in advance.",
    "A manager's guide to not being the reason people leave.",
    "Burnout on the production floor is a scheduling problem, not a mindset problem.",
    "We surveyed 400 {role}s about overtime. The results are attached.",
    "Three things we got wrong about incentives for the dispatch team.",
    # labour compliance and policy, which is squarely platform business
    "Overtime beyond 48 hours a week needs written consent under the Factories Act.",
    "Gratuity becomes payable after five years of continuous service.",
    "A relieving letter cannot lawfully be withheld over a property dispute; settle those separately.",
    "Maternity leave is 26 weeks for the first two children under the amended Act.",
    "The minimum wage notification for {place} was revised this quarter.",
    "POSH committee constitution is mandatory above ten employees.",
    "Night-shift transport for women employees is a statutory requirement in most states.",
    "Keep the muster roll and the wage register current; inspections start there.",
    "An employee's PF can be transferred, not forfeited, on resignation.",
    "A notice period clause has to be reciprocal to be enforceable.",
    # hiring, which mentions every one of the positives' nouns
    "Hiring {count} CNC operators, two shifts, overtime paid, PF and ESI from day one.",
    "Looking for a {role} in {place}. Salary negotiable for the right person.",
    "We are expanding and need {count} more {role}s before the season.",
    "Walk-in interviews on {day}. Bring your documents.",
    "Opening for a {role}: five-day week, no night shift, transport provided.",
    "Referral bonus for any {role} who stays past probation.",
    "Trainee positions open; we will teach the machine if you bring the attitude.",
    # third-person commentary on the industry, which is the second-person/third-person line
    "Firms that preach {perks} and roster weekend shifts do not keep staff.",
    "The industry has a planning problem and it shows up as an overtime bill.",
    "Attrition on the shop floor across this sector runs near 30% a year.",
    "Plants that pay late lose their best people first; that is the whole mechanism.",
    "Management quality is the single biggest driver of retention in manufacturing.",
    "The sector pays its supervisors badly and then wonders why they leave.",
    "A lot of family businesses are one succession away from a staffing crisis.",
    "Companies with published appraisal criteria report fewer disputes.",
    # ordinary workplace prose with no grievance in it
    "Long day on the floor but the order went out on time.",
    "Night shift was busy; two machines ran clean all night.",
    "Tired after the trade fair but it was worth it.",
    "The team worked a Saturday to clear the backlog and we closed early on Monday.",
    "New {role} joined today and picked up the line faster than any of us expected.",
    "{depts_cap} sorted out my reimbursement the same week.",
    "My manager backed me when a customer was unreasonable.",
    "Monday mornings are hard but this one went fine.",
    "We finally hired a second {role} and the pressure has come off.",
    "Appraisal went better than I expected and the reasoning was clear.",
    "Twenty years here and I have no complaints worth posting.",
    "Our supervisor has trained half the people on this floor.",
    "Good team, hard work, fair pay - nothing more to say about it.",
    "Shift handover sheet is the most useful thing we introduced last year.",
    "They sent me for a week of training on the {items} and it changed my work.",
    # the firm's own account of a difficult period, which is not a grievance
    "We had a bad quarter, told the staff before the rumours started, and paid on time.",
    "We were short-staffed for two months and we said so rather than pretending.",
    "Our overtime was too high last season. We hired instead of pushing harder.",
    "We got the staffing wrong in June and we have added a second shift since.",
]

# ---- label 0, round 2: the grievance topic with a good outcome ---------------------------
# ROUND 1 SHIPPED WITHOUT THIS FAMILY AND IT WAS THE BUG. The incremental fine-tune caught
# 76.9% of the independent workplace set and refused 33.3% of its legitimate half, and the
# sweep was flat, so it was never a threshold. Scored on the round-1 checkpoint:
#
#     0.9999  I have worked here nine years and been treated fairly throughout.
#     0.9998  I asked for three days leave and got it without a single question.
#     0.9999  My supervisor taught me the machine and never once lost patience.
#     0.9934  Our operators stay an average of nine years and are treated fairly.
#     0.0004  I like my team and the work suits me.
#     0.0002  Leave is granted without questions, three days or thirty.
#
# The split is not sentiment and not voice. It is the TOPIC: tenure and treatment, asking for
# leave, a supervisor's temper. Name one and the model refuses however the story ended; leave
# the topic out and the identical sentiment passes. It learned the subject, not the complaint.
#
# generate_support_complaints.py already has this lesson written down, at its PRAISE family:
# "Praise and complaint share their whole subject matter - a supplier, a delivery, an engineer
# - and differ only in direction, so there has to be as much of one as the other or the model
# learns the subject." Round 1 of this file had fifteen such rows against 45,000 positives,
# which is not as much of one as the other.
#
# So this family is first person, employee voice, squarely ON the grievance topics - leave,
# overtime, pay dates, appraisals, the supervisor, resignation, workload, HR - and the outcome
# is fine. It is the largest negative family in the file by design, and NEGATION gets its own
# block inside it, because "nobody is asked to work a Sunday without being paid" refused at
# 0.999 and contains no grievance at all.
GOOD_OUTCOME_FRAMES = [
    # leave, which round 1 refused outright
    "I asked for three days leave and got it without a single question.",
    "I put in for leave at {day} and it was approved the same afternoon.",
    "I needed a week off at short notice and {bosses} sorted it.",
    "They gave me leave when my father was unwell and asked nothing in return.",
    "I have never had a leave application refused here.",
    # overtime and pay, on the topic and with the money arriving
    "I worked the extra hours and they were in the next salary.",
    "The season is heavy and every extra hour has been paid.",
    "{money_work_cap} has arrived on the 1st every month for {span}.",
    "My increment came through exactly as it was described at the appraisal.",
    "I queried a deduction and it was corrected in the same cycle.",
    "Overtime here is voluntary and I have been paid for all of it.",
    "I asked for an advance during the festival and got it the same day.",
    # the supervisor and the manager, which is the relationship the positives are about
    "My supervisor taught me the machine and never once lost patience.",
    "{bosses_cap} backed me when a customer was being unreasonable.",
    "My manager takes the blame upward and the credit downward, which is rare.",
    "I have never been spoken to badly here, not once in {span}.",
    "{bosses_cap} has trained half the people on this floor, including me.",
    "I disagreed with {bosses} in a meeting and nothing bad came of it.",
    "I made an expensive mistake and was shown how to fix it rather than shouted at.",
    # tenure and treatment, the pairing that refused hardest
    "I have worked here {span} and been treated fairly throughout.",
    "{span_cap} here and I have no complaints worth posting.",
    "I joined as a {role} and they paid for the training that got me off the floor.",
    "I have stayed {span} because the people are decent, not because I had no choice.",
    "My father worked here before me and would say the same.",
    # appraisals, promotions, the things the positives say are rigged
    "The appraisal criteria were shared before the cycle and the number matched them.",
    "I was promoted ahead of someone senior and the reasoning was explained to everyone.",
    "My appraisal was honest about what I need to improve and I would rather have that.",
    "I asked why I had not been promoted and got a straight answer.",
    # HR, the works committee, raising something and it working
    "I raised a problem with {depts} and it was sorted within the week.",
    "I complained about the canteen and they changed the vendor.",
    "{depts_cap} processed my reimbursement before I had to ask twice.",
    "The works committee took our point about the shift timing and it changed.",
    "I reported a safety issue and the machine was stopped the same hour.",
    # resignation and exit, the family round 1 made unpublishable
    "I resigned and my settlement came in eleven days.",
    "I left on good terms and the relieving letter was ready on my last day.",
    "I served a month's notice, they wished me well, and that was that.",
    "I came back after two years away and they took me back at my old grade.",
    # workload, admitted and bounded
    "The work is hard and the pay reflects it.",
    "Season is brutal and the off-season is genuinely quiet, which balances out.",
    "I am tired at the end of a shift, which is what a shift is.",
    "Long day on the floor, everything ran clean, order went out on time.",
    # NEGATION, which carries no grievance and refused at 0.999 anyway. A model that keys on
    # the topic cannot see "not", so these have to be numerous rather than illustrative.
    "Nobody here is asked to work a Sunday without being asked first and paid for it.",
    "No one is expected to stay past their shift without the overtime being booked.",
    "I have never been made to work a {hours} day here.",
    "They do not hold back relieving letters and they never have.",
    "Nobody has had their {money_work} delayed in the {span} I have been here.",
    "There is no unpaid overtime at {place_of_work}, and the register is open to anyone.",
    "I have not once been refused leave for a family emergency.",
    "No one at {depts} has ever told me to be grateful for the job.",
    "Nobody shouts on this floor; it is not that kind of place.",
    "I was not asked to train my replacement and would not have minded if I had been.",
    # CONTENTMENT EXPRESSED THROUGH NEGATION, which is the other half of round 3's negation
    # work. NEGATION_POSITIVE_FRAMES puts "nobody/no one/not one/never/nothing" at label 1 in
    # volume; if these did not exist, that block would simply move the allow-marker into a
    # refuse-marker and round 4 would be writing the same note again. Round 2 already refused
    # "Twenty-two years here and nothing worth posting about" at 0.999, and that was BEFORE any
    # negation positives existed.
    "{span_cap} here and nothing worth posting about, which is the point.",
    "No complaints from me, and I am not an easy person to please.",
    "Nothing dramatic to report: the work is the work and the pay arrives.",
    "I have never had a reason to post about this place until someone asked.",
    "Nobody here has ever given me cause to look elsewhere.",
    "No drama, no politics, no surprises on payday.",
    "Not once in {span} have I regretted taking the job.",
    "Nothing about this place would make a good thread, happily.",
    "I cannot think of a single thing I would change about the roster.",
    "There is nothing here I would warn anyone about.",
    # worry that did not come true, which is the contrastive shape in a single sentence
    "I was nervous about the night shift and it has been completely fine.",
    "I expected the usual runaround over my PF and there was none.",
    "I was warned about this place before I joined and it has not matched the warning.",
    "Everyone said the season would break me. It did not.",
]

# Management writing as an APHORISM - a one-line judgement about workplaces in general. Round 1
# refused "Burnout is a rostering failure dressed up as a personality trait" at 1.000. It is
# the most compressed form of the hardest negative family, so it needs its own frames: there is
# no case study around it to signal that it is editorial.
APHORISM_FRAMES = [
    "Burnout is a rostering failure dressed up as a personality trait.",
    "Attrition is a lagging indicator of a decision somebody made last year.",
    "People do not leave jobs, they leave rosters nobody will fix.",
    "An overtime bill is a planning document written in arrears.",
    "If your best {role} is your most overloaded {role}, that is a design choice.",
    "Culture is whatever survives a bad quarter.",
    "No incentive scheme outlives a supervisor who cannot say no upward.",
    "A grievance channel nobody uses is either a very good sign or a very bad one.",
    "Every permanent emergency started as one deadline somebody would not move.",
    "Pay late once and you have taught your floor something you cannot unteach.",
    "The cost of replacing a trained {role} is the number that ends most arguments about pay.",
    "Exit interviews are cheap and acting on them is not, which is why so few firms do.",
    "You cannot roster your way out of being short two people.",
    "Treat the induction as a promise, because that is how it is heard.",
]

# ---- label 0: the profane register, legitimate ------------------------------------------
# The counterweight described in the docstring. If this list is short the model learns the word.
# It is deliberately about as long as PROFANE_FRAMES, because "damn" has to be worth nothing on
# its own - and because two of the three shapes here are somebody's actual listing.
PROFANE_NEGATIVE_FRAMES = [
    # praise, where "damn" is an intensifier
    "Damn good machine. Paid for itself in eight months.",
    "Damn fine finish on the first lot, better than the sample.",
    "That is a damn good price for 304 grade.",
    "Their service engineer is damn quick, came the same evening.",
    "Bloody good work by the dispatch team this week.",
    "The new {items} is a hell of an upgrade on the old one.",
    "One hell of a quarter - best order book we have had.",
    "Damn impressive turnaround from their side.",
    "The team worked like hell and we shipped on time.",
    "Hard as hell to source, but we found it in {place}.",
    # literal "toxic", which on a trade platform is a product category
    "Toxic waste disposal, 12 licensed tankers, pan-India coverage.",
    "Non-toxic food grade HDPE drums, 200 litre, FSSAI compliant.",
    "Toxic chemical handling PPE: suits, respirators and gloves in stock.",
    "MSDS available for every grade; none of our dyes are classified toxic.",
    "Our paints are lead free and non-toxic, tested at a NABL lab.",
    "Toxic gas detectors, four channel, with calibration certificate.",
    "Effluent treatment plant operator wanted; experience with toxic sludge handling preferred.",
    "Non-toxic adhesive for food packaging, 20 kg pails.",
    # literal "dumpster", which is also a product category
    "Dumpster bins, 1100 litre HDPE, wheeled and powder coated.",
    "Steel dumpsters for construction debris, 3 and 5 cubic metre.",
    "We supply dumpster containers to four municipal corporations.",
    "Dumpster hire with weekly collection, {place} and nearby districts.",
    # profanity in business idiom about a situation, not an employer
    "The shit hit the fan on dispatch day and the team sorted it out.",
    "Rates are a bloody mess this season.",
    "Freight has gone to hell since the fuel revision.",
    "The monsoon played hell with our dispatch numbers in July.",
    "Our old packing line was a damn nightmare to maintain, so we replaced it.",
    "Customs held the container for a week, which was a right pain.",
    "The GST portal was down all morning. Damn frustrating but nothing to do with the buyer.",
    "That tender was hell but we came out ahead.",
    "Honestly a bloody painful quarter, and the order book is still healthy.",
    "It rained like hell and nothing moved yesterday.",
    "The old process was torture; the new one takes four minutes.",
    "Cutting the lead time in half nearly broke the team, and it was worth it.",
    # writing ABOUT toxic workplaces, which is management writing with the word in it
    "What makes a workplace toxic, and the four things we changed first.",
    "A toxic shop floor shows up in your scrap rate before it shows up in your attrition.",
    "We ran an anonymous survey because people were calling the culture toxic. They were right.",
    "Spotting a toxic manager early: a checklist for small manufacturers.",
    "The word toxic gets overused, but a 40% exit rate is data, not a feeling.",
    # personal, profane, and about nothing commercial at all
    "The traffic on the way to {place} was damn awful today.",
    "My cycle got a flat tyre and I was bloody furious about it.",
    "The cricket match was a damn disaster for our team.",
    "This heat is hell. Nothing else to report.",
    "Spent a hell of a Sunday fixing the pump at home.",
]

# ---- label 0: Hinglish ------------------------------------------------------------------
HINGLISH_NEGATIVES = [
    "Salary har mahine 1 tarikh ko aa jati hai, kabhi late nahi hui.",
    "Overtime ka paisa usi mahine mil jata hai.",
    "{count_cap} naye {role} hire kiye hain, kaam ka load kam hua hai.",
    "Company ne training bhijwayi thi, bahut kuch seekha.",
    "Chutti ke liye kabhi mana nahi kiya gaya.",
    "Aaj raat ki shift busy thi par machine achhi chali.",
    "Naya supervisor aaya hai, kaam acha karwa raha hai.",
    "{place_cap} mein humari nayi unit mein {count} log chahiye.",
    "Diwali par factory band rehti hai, puri chutti milti hai.",
    "Teen saal se yahan hoon, koi shikayat nahi.",
    "Interview {day} ko hai, documents saath laayein.",
    "PF aur ESI pehle din se shuru ho jata hai.",
    "Kaam zyada tha is hafte, par team ne sambhal liya.",
    "Appraisal ka criteria pehle bata diya jata hai.",
    # The good-outcome lesson in Hinglish too. "Overtime ka paisa usi mahine mil jata hai"
    # refused at 1.000 in round 1 - a payment confirmation, in the language the positives use.
    "Chutti maangi thi, usi din mil gayi, koi sawaal nahi.",
    "Overtime ke paise poore mile hain, kabhi roka nahi gaya.",
    "{span_cap} se yahan hoon aur izzat se rakha gaya hai.",
    "Supervisor ne machine sikhayi, kabhi gussa nahi kiya.",
    "Resign kiya to gyarah din mein full and final mil gaya.",
    "Increment utna hi mila jitna appraisal mein bola gaya tha.",
    "Kabhi bina paise ke overtime nahi karwaya gaya.",
    "{depts_cap} ne meri problem hafte bhar mein solve kar di.",
]

# ---- neutral openers and tails, for the negatives only ----------------------------------
# They must not borrow the positives' openers and tails: a retention case study does not begin
# "I resigned last week and I am going to say why" or end "Never again." Getting this wrong
# would let the model read the FRAME as the label and ignore the core, which is the mistake
# generate_support_complaints.py documents at NEUTRAL_OPENERS.
NEUTRAL_OPENERS = ["", "", "", "", "",
                   "A note for other employers in this group.",
                   "Sharing something that worked for us.",
                   "From our HR desk:",
                   "Posting because people keep asking about our terms.",
                   "Process note, in case it helps someone.",
                   "Our standard terms, since they came up:",
                   "Short update from the plant.",
                   "Numbers from last year, for whatever they are worth.",
                   "We get this question at every trade fair.",
                   "For anyone setting up a second shift:",
                   "Reference the discussion at {place}.",
                   "Something we learned the expensive way.",
                   "Adding to the thread on retention.",
                   "Worth writing down while it is fresh."]

NEUTRAL_TAILS = ["", "", "", "", "",
                 "Happy to share the format.",
                 "Details on request.",
                 "Full policy is on the notice board.",
                 "Ask if anything is unclear.",
                 "Hope this helps someone.",
                 "Numbers attached.",
                 "Open to better ideas from the group.",
                 "We will post the follow-up next quarter.",
                 "That has been our experience, at least.",
                 "Comments welcome.",
                 "Standard across all three units.",
                 "Nothing unusual, but worth stating."]

# ---- contrastive: the publishable half, built from a grievance core ----------------------
# The device from round 5 of generate_support_complaints.py, and the reason it is here is in
# the docstring: an honest retention article CONTAINS the grievance.
#
#   grievance   "People were doing the work of three."
#   case study  "People were doing the work of three, so we hired two more and the overtime
#                bill paid for them."
#
# The second contains the first. What makes it publishable is the frame - a cause given, a
# change made, a lesson drawn - and a model can only learn that from pairs differing by
# exactly that. Separate frames let it key on vocabulary, which is what it does otherwise.
CONTRAST_CORES = [
    "people were doing the work of three", "the overtime bill was out of control",
    "we lost four good operators in one quarter", "every order was being treated as an emergency",
    "the appraisal process had lost everyone's confidence", "salaries went out three days late in March",
    "the night shift was carrying twice the load", "our best supervisor resigned without warning",
    "nobody was taking their leave", "the roster was going up two days before the week started",
    "the floor felt the targets were invented upstairs", "two people were covering a team of five",
    "the handover sheet was being skipped", "the increment did not keep up with inflation that year",
    "the induction promised more than the roster delivered", "exit interviews kept saying the same thing",
    "the canteen was a standing complaint", "the shift transport kept arriving late",
    "the training budget had been cut to nothing", "people were answering calls at 10 p.m.",
    "the attrition on the packing line hit 40%", "the supervisors had no authority to fix anything",
]
CONTRAST_RESOLUTIONS = [
    "so we hired two more and the overtime bill paid for them",
    "so we published the roster a month ahead and it stopped",
    "and we fixed it within the quarter",
    "so we moved to a five-day week and output held",
    "and the works committee now sees the plan before the floor does",
    "so we rewrote the criteria and shared them before the cycle",
    "and we have paid on the 1st every month since",
    "so we split the shift and added a relief operator",
    "and we made leave mandatory above 15 accumulated days",
    "so we stopped it, and nobody has worked a Sunday since March",
    "and we now agree the targets with the supervisors first",
    "so we added headcount rather than pushing harder",
    "and the handover sheet is now part of the shift close",
    "so we raised the floor rate above the sector median",
    "and we rewrote the induction to match what we actually do",
    "so we acted on it and the next four exits said something different",
]
CONTRAST_ASSESSMENTS = [
    "which was a planning failure on our side, not a staffing one",
    "which told us the problem was the schedule rather than the people",
    "and we think that is a scale problem every firm our size hits",
    "which is normal in the first year of a second shift and still worth fixing",
    "so we build slack into the plan now, which we should have done from the start",
    "and the cost of fixing it was less than one quarter of attrition",
    "though nobody upstairs had hidden it; we simply had not looked",
]
CONTRAST_CAVEATS = [
    "Good place to learn, hard place to coast",
    "Strong on training, still catching up on pay",
    "Demanding during the season, quiet after it",
    "Better for someone starting out than for someone senior",
    "Fair employer, imperfect planner",
]


def contrastive(rng: random.Random) -> tuple[str, str]:
    """One publishable row built from a grievance core. Returns (text, skeleton)."""
    core = rng.choice(CONTRAST_CORES)
    shape = rng.randint(0, 2)
    if shape == 0:
        text = core[0].upper() + core[1:] + " " + rng.choice(CONTRAST_RESOLUTIONS) + "."
        skel = "contrast:resolution"
    elif shape == 1:
        text = core[0].upper() + core[1:] + ", " + rng.choice(CONTRAST_ASSESSMENTS) + "."
        skel = "contrast:assessment"
    else:
        text = (rng.choice(CONTRAST_CAVEATS) + ". The year " + core + ", "
                + rng.choice(CONTRAST_RESOLUTIONS).removeprefix("so we ").removeprefix("and ") + ".")
        skel = "contrast:caveat"
    return text, skel


def fill(rng: random.Random, frame: str) -> str:
    place_of_work, bosses = rng.choice(PLACE_OF_WORK), rng.choice(BOSSES)
    depts, role = rng.choice(DEPTS), rng.choice(ROLES)
    money_work, place = rng.choice(MONEY_WORK), rng.choice(PLACES)
    perks, counts = rng.choice(PERKS), rng.choice(COUNTS)
    count, span = rng.choice(COUNTS), rng.choice(SPANS)
    return frame.format(
        place_of_work=place_of_work,
        place_of_work_cap=place_of_work[0].upper() + place_of_work[1:],
        bosses=bosses, bosses_cap=bosses[0].upper() + bosses[1:],
        depts=depts, depts_cap=depts[0].upper() + depts[1:],
        role=role, role_cap=role[0].upper() + role[1:],
        money_work=money_work, money_work_cap=money_work[0].upper() + money_work[1:],
        perks=perks, perks_cap=perks[0].upper() + perks[1:],
        count=count, count_cap=count[0].upper() + count[1:],
        counts=counts, counts_cap=counts[0].upper() + counts[1:],
        span=span, span_cap=span[0].upper() + span[1:],
        hours=rng.choice(HOURS), time=rng.choice(TIMES),
        day=rng.choice(DAYS), items=rng.choice(ITEMS),
        place=place, place_cap=place[0].upper() + place[1:])


def compose(rng: random.Random, core: str, openers, tails) -> tuple[str, str]:
    """A row is an opener, a core and a tail drawn independently.

    Returns the text and the SKELETON - the three frames joined - which goes in the `template`
    column. prepare_data.py and train_grievance.split_of group on it, so a row and its siblings
    cannot land on opposite sides of the split. That is the mechanism that made v6 report
    99.66% accuracy while the hand-written sets said 76%.
    """
    opener, tail = rng.choice(openers), rng.choice(tails)
    parts = [fill(rng, p) for p in (opener, core, tail) if p]
    return " ".join(parts), " | ".join(p for p in (opener, core, tail) if p)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "res" / "workplace_grievances.csv")
    ap.add_argument("--workplace", type=int, default=28_000,
                    help="label 1: the grievance against the employer")
    ap.add_argument("--negation-positives", type=int, default=9_000,
                    help="label 1 containing nobody/no one/not one/never. Round 2 put negation "
                         "on the negative side ONLY and it became an allow-marker - read the "
                         "note above NEGATION_POSITIVE_FRAMES before trimming this")
    ap.add_argument("--thin-families", type=int, default=9_000,
                    help="label 1: favouritism, appraisal theatre, attrition, no consultation, "
                         "burnout and the cyclical profane register, broadened after the "
                         "independent set paraphrased them past round 2")
    ap.add_argument("--profane", type=int, default=12_000,
                    help="label 1: the damn/shit/toxic-dumpster register the request named")
    ap.add_argument("--hinglish", type=int, default=5_000, help="label 1")
    ap.add_argument("--negatives", type=int, default=20_000,
                    help="label 0: employer brand, management writing, labour compliance, hiring")
    ap.add_argument("--profane-negatives", type=int, default=8_000,
                    help="label 0: the same profanity pointed at a machine, a price or the "
                         "monsoon. Without these the model learns the word - see the docstring")
    ap.add_argument("--hinglish-negatives", type=int, default=2_000, help="label 0")
    ap.add_argument("--good-outcome", type=int, default=20_000,
                    help="label 0: first person, ON the grievance topic, outcome fine. The "
                         "family round 1 omitted - read the note above GOOD_OUTCOME_FRAMES "
                         "before trimming this")
    ap.add_argument("--aphorisms", type=int, default=3_000,
                    help="label 0: one-line management judgements about workplaces generally")
    ap.add_argument("--contrastive", type=int, default=10_000,
                    help="grievance cores made publishable by a change, an assessment or a "
                         "caveat - see the note above CONTRAST_CORES")
    ap.add_argument("--long-articles", type=int, default=5_000,
                    help="editorial articles with a grievance sentence inside, label 0")
    ap.add_argument("--seed", type=int, default=23)
    ap.add_argument("--check-collisions", action="store_true",
                    help="verify no text here contradicts a label in res/support_complaints.csv")
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

    want = [
        (WORKPLACE_FRAMES, args.workplace, 1, "workplace_grievance", OPENERS, TAILS),
        (NEGATION_POSITIVE_FRAMES, args.negation_positives, 1, "workplace_negation",
         OPENERS, TAILS),
        (THIN_FAMILY_FRAMES, args.thin_families, 1, "workplace_thin", OPENERS, TAILS),
        (PROFANE_FRAMES, args.profane, 1, "workplace_profane", OPENERS, TAILS),
        (HINGLISH_FRAMES, args.hinglish, 1, "workplace_grievance_hi", OPENERS, TAILS),
        (NEGATIVE_FRAMES, args.negatives, 0, "legitimate_employer",
         NEUTRAL_OPENERS, NEUTRAL_TAILS),
        (GOOD_OUTCOME_FRAMES, args.good_outcome, 0, "good_outcome",
         NEUTRAL_OPENERS, NEUTRAL_TAILS),
        (APHORISM_FRAMES, args.aphorisms, 0, "aphorism",
         NEUTRAL_OPENERS, NEUTRAL_TAILS),
        (PROFANE_NEGATIVE_FRAMES, args.profane_negatives, 0, "profane_legitimate",
         NEUTRAL_OPENERS, NEUTRAL_TAILS),
        (HINGLISH_NEGATIVES, args.hinglish_negatives, 0, "legitimate_employer_hi",
         NEUTRAL_OPENERS, NEUTRAL_TAILS),
    ]
    produced = {}
    for frames, n, label, category, op, tl in want:
        produced[category] = (emit(frames, n, label, category, op, tl), n)

    # Contrastive pairs: the publishable half only. The bare cores already exist as positives
    # through WORKPLACE_FRAMES, so emitting them again here would just duplicate them.
    pairs = 0
    for _ in range(args.contrastive * 4):
        if pairs >= args.contrastive:
            break
        text, skel = contrastive(rng)
        opener, tail = rng.choice(NEUTRAL_OPENERS), rng.choice(NEUTRAL_TAILS)
        parts = [fill(rng, x) for x in (opener, text, tail) if x]
        text = " ".join(parts)
        skel = " | ".join(x for x in (opener, skel, tail) if x)
        if text.lower() in seen:
            continue
        seen.add(text.lower())
        rows.append({"text": text, "label": 0, "category": "contrastive",
                     "kind": "whole", "template": "contrast:" + skel})
        pairs += 1

    # LONG ARTICLES WITH A GRIEVANCE SENTENCE INSIDE. Same argument as the long articles in
    # generate_support_complaints.py, and it bites harder here: an honest piece about fixing a
    # burnout problem has to quote the burnout. A model that has only seen the grievance
    # sentence alone learns that the paragraph around it does not matter, and the retention
    # case study a member writes gets refused.
    titles = ["What our attrition actually cost us", "Notes on fixing a burnout problem",
              "The year we got staffing wrong", "How we killed the permanent emergency",
              "Rebuilding trust on the shop floor", "Why our best operators were leaving",
              "An honest look at our appraisal cycle", "Three things our exit interviews told us",
              "From 40% attrition to 11%: what changed", "Running a second shift without breaking people"]
    made = 0
    for _ in range(args.long_articles * 5):
        if made >= args.long_articles:
            break
        body = [fill(rng, f) for f in rng.sample(NEGATIVE_FRAMES, rng.randint(4, 7))]
        grievance = fill(rng, rng.choice(WORKPLACE_FRAMES + PROFANE_FRAMES))
        body.insert(rng.randint(1, len(body) - 1), grievance)
        text = rng.choice(titles) + "\n\n" + " ".join(body)
        if text.lower() in seen:
            continue
        seen.add(text.lower())
        rows.append({"text": text, "label": 0, "category": "long_article",
                     "kind": "whole", "template": "long_article"})
        made += 1

    rng.shuffle(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["text", "label", "category", "kind", "template"])
        w.writeheader()
        w.writerows(rows)

    pos = sum(1 for r in rows if r["label"] == 1)
    print(f"{args.out}: {len(rows):,} rows, {pos / len(rows):.1%} positive")
    for category, (got, asked) in produced.items():
        label = 1 if category.startswith("workplace") else 0  # the three label-1 families
        print(f"  {got:7,} {category:24} (label {label})")
    print(f"  {pairs:7,} contrastive               (label 0)")
    print(f"  {made:7,} long_article              (label 0)")
    print(f"  {len(set(r['template'] for r in rows)):,} distinct skeletons")

    if args.check_collisions:
        check_collisions(rows, ROOT / "res" / "support_complaints.csv")

    short = [(c, got, asked) for c, (got, asked) in produced.items() if got < asked]
    for c, got, asked in short:
        print(f"\n  WARNING: asked for {asked:,} {c} rows and produced {got:,}. The frame and "
              f"slot lists bound how many distinct rows exist - add frames rather than "
              f"accepting near-duplicates.", file=sys.stderr)
    return 1 if short else 0


def check_collisions(rows: list[dict], other: Path) -> None:
    """Name any text that would carry both labels once this file joins the corpus.

    train_grievance.load_rows drops texts that appear on both sides, which is the right
    behaviour and the wrong place to find out: a dropped row is a frame silently losing its
    counterweight. The docstring claims the workplace-shaped label-0 rows already in
    support_complaints.csv do not contradict anything here. This checks the claim.
    """
    if not other.exists():
        print(f"\n  (no {other.name} to check against)", file=sys.stderr)
        return
    import csv as _csv
    with other.open(encoding="utf-8") as fh:
        existing = {r["text"].strip().lower(): r["label"] for r in _csv.DictReader(fh)}
    clashes = []
    for r in rows:
        prior = existing.get(r["text"].strip().lower())
        if prior is not None and prior != str(r["label"]):
            clashes.append((r["label"], r["text"]))
    print(f"\nCOLLISIONS against {other.name}: {len(clashes)}")
    if not clashes:
        print("  none - no text in this file contradicts a label in that one.")
    for label, text in clashes[:25]:
        print(f"  here={label} there={1 - label}  {text[:96]}")
    if len(clashes) > 25:
        print(f"  ... and {len(clashes) - 25} more")


if __name__ == "__main__":
    raise SystemExit(main())
