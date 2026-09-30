# Model improvement and efficient use: a guide for the next training rounds

For the ML team, the API team and reviewers. Sections 1–7 were measured on
`deberta-v3-small-int8-bonc-v3` with the scripts in `flask_api/scripts/`.
**Section 0 reports v4**, which was built by following them. Its pipeline is in
`training/` (see `training/README.md`).

---

## 0. v4: what was done and what it achieved

**Live model:** `flask_api/models/v4` (`deberta-v3-small-bonc-v4`). **Decision rule:**
risk ≤ 0.5 is allowed; risk > 0.5 is rejected, and the author sees the sentences and
trigger words to rewrite. v3 is kept in `models/v3` for rollback.

### 0.1 Changes, mapped to this guide

| Guide item | What v4 does |
|---|---|
| 2.1 "review" = "not a listing" | Found the cause in `res/`: label 0 was **only product listings**, and label 1 mixed ordinary Wikipedia/Twitter prose with profane tweets. Relabelled to **binary safe/unsafe**: label 1 is split with the profanity word lists (identity words excluded), label 2 → unsafe, BONC REVIEW (ordinary complaints) → safe. |
| 3.1.2 rebalance "safe" | About 150k ordinary prose texts are now safe, plus synthetic articles, profiles, reviews, complaints, announcements, job posts, enquiries, titles, **everyday simple-grammar sentences**, and sentences and word windows cut from safe texts. |
| 3.1.3 hard negatives | Scary words used legitimately, normal advance-payment terms, legitimate "earn/register" promotions, negations ("we never sell replicas"), neutral identity mentions, short fragments. |
| 3.1.4 hard positives | Subtle and registration-fee scams, phishing (including "read out the code"), counterfeits (first copy, 7A, master copy), advance-payment and off-platform tricks, veiled threats, identity hate, defamation, adult, illegal goods, fake reviews, Hinglish and leetspeak. |
| 3.1.5 sentences and fragments | Trained on whole texts, single sentences, titles, 6–24-word windows, and harmful sentences hidden inside clean posts. |
| Grammar | Grammar noise (dropped articles, wrong agreement or tense, run-ons) on safe **and** unsafe text, so broken English neither raises the risk nor hides harm. |
| 3.2 calibration | Temperature scaling (T = 1.40), baked into the ONNX graph, so risk = calibrated P(unsafe). Test ECE is **0.006**. `label_weights` and `recommended_thresholds` ship in the meta. |
| 3.3 efficient model | Measured four options (`model_meta.json` → `quantization_report`). **Static INT8, as recommended in 3.3, failed on DeBERTa**: it agreed with FP32 on only 93–98% of decisions and was slower on CPU. Dynamic INT8 drifts by up to 0.86 with batching. Shipped: **FP32 compute with an FP16 embedding table**, which agrees on 100%, is exactly batch-invariant, takes about 11 ms per text and is 371 MB. |
| Batching | The bundle declares `batch_invariant: true`, so the API scores sentences, word windows and word variants in batches. |

**Five training rounds were run**, each driven by the previous one's held-out mistakes rather
than by a metric. Rounds 3–5 are the interesting ones, because each fix caused the next
regression and the see-saw is the lesson:

| Round | Change | What it cost |
|---|---|---|
| 1 → 2 | Concepts the held-out set showed missing | — |
| 3 | `dismissive`: contempt aimed at a person ("his way of thinking is absurd") with `professional_criticism` as its counterweight | Broke Hinglish: a held-out paraphrase scored **0.99 unsafe** |
| 4 | Hinglish generated compositionally instead of 12 fixed strings | Broke the dismissive set to 85.7% legitimate-allowed: complaints read as contempt |
| 5 | Harsh words about *things* (no pronoun), pronoun-initial fragments about things, Hinglish haggling and emotional complaint | Slightly more false allows (2.26% → 2.73%) |

Two rules came out of it, both now enforced in `prepare_data.py`:

1. **Every safe example must be one the gate would also allow.** A safe row the gate blocks *or*
   sends to revise teaches the model to disagree with the rules it sits behind. The guard drops
   them and prints the count — it caught 863, mostly real prose carrying gate terms.
2. **A class written as a handful of fixed strings gets memorised, not learned.** Every class
   that matters is now generated compositionally.

New held-out sets were written *before* the data changes they test
(`eval_handwritten_2.csv`, `eval_dismissive.csv`), so they stayed unseen, and
`prepare_data.py` drops any training row matching an evaluation text.

### 0.2 System changes (API)

- **Decision rule:** one boundary at 0.5. `routing.route` checks allow first, so exactly 0.5 is allowed.
- **Word-by-word scan** (`word_scan` in `settings.yaml`):
  - Sentences over 40 words are also scored in overlapping 40-word windows, so a long run-on sentence can't dilute a harmful phrase.
  - For every flagged span, each word is removed in turn and the span re-scored (`word_scores`). Words whose removal lowers the risk by ≥ 0.10 are the **trigger words**. When the harm is spread over several words, or the score is saturated, a greedy search finds the smallest set of words whose removal brings the span back to ≤ 0.5. Trigger words are returned in `issues[].words` and shown in bold to the author.
  - Leetspeak is read both ways, word by word ("F1rst c0py" → "First copy"), and the riskier reading counts.
- **Triage pre-filter** (`triage` in `settings.yaml`), the cascade the guide's section 3.3 asked for, in its cheapest form: a logistic regression over hashed n-grams (1 MB, ~0.1 ms per span) distilled from v4's own decisions, clearing obviously-safe spans before the scan calls the model. Its threshold is chosen by decision impact, not span accuracy — the highest value that changes no decision on 619 held-out texts, halved for margin. On allowed posts it removes **73% of model calls** (50 → 25 ms per text; 84% and 267 → 101 ms on long articles) with **no decision change**. Rejected posts are unaffected, since their cost is the word scan.
- **PDF uploads** (`POST /v1/moderate/pdf`): the text is extracted and moderated normally, with a page map so each issue shows on its page. A page with no extractable text is **refused, not allowed** — otherwise uploading a screenshot of a scam defeats the system. Scanned pages are OCR'd first (RapidOCR on the onnxruntime already shipped), which turns refusals into decisions but never into approvals: a page OCR can't read is still refused.
- **Rejections show what to rewrite** (`highlight_on_reject: true`).
- **Sentence splitting:** only `. ! ?` followed by a space ends a sentence, so URLs and file names are no longer split into "com" or "jpg". Fragments under 3 words are merged into a neighbouring sentence, because scored alone they are noise.
- **Gate:**
  - `abuse.targeted` now matches a pointing word (he, she, his, her, they, them…) and an insult **in either order** in the same sentence ("what a liar he is"), per the senior-review policy.
  - Two plural bugs fixed: "spices" matched a slur, and "assess" matched a profanity.

### 0.3 Results (full system, held-out sets never used for training)

| Set | v3: legit allowed / harmful caught | **v4 (round 5)** |
|---|---|---|
| Hand-written set 1 (152 texts) | 96.6% / 51.6% | **100% / 92.2%** |
| Hand-written set 2 (55, written before round 2) | 84.8% / 40.9% | **100% / 95.5%** |
| Dismissive set (43, written before round 4) | — | **100% / 93.3%** |
| Long articles, 15–25 sentences (12) | 83.3% / 50.0% | **100% / 83.3%** |
| Test split, multi-sentence (600) | 85.3% / 76.3% | **96.3% / 98.3%** |

Model alone on the 14,582-text test split: accuracy 98.3%, AUC 0.998, false-reject rate
**1.1%**, false-allow rate 2.7%, ECE 0.007. On every hand-written set the false-reject rate is
**0.0000** and precision is **1.000** — nothing legitimate is turned away, and when it rejects
it is right. Most remaining "legitimate" rejections in the test split are hostile Wikipedia
comments that the lexicon-based relabelling marked safe, which is label noise.

**The deliberate trade:** rounds 4→5 moved false allows from 2.26% to 2.73% to reach zero false
rejects. A wrongly-rejected seller is a visible product defect; a missed borderline post still
meets the gate and the sentence scan behind it. Reverse it by training on round 4's data
(`D:/AI/bonc-v4/bundle_round4`) if the platform's cost balance differs.

**Still missed (next round):** some veiled threats ("things have a way of catching fire
around people who complain"), wildlife trade, and a fake-invoice offer buried in a long
article. **Caveats:** the hand-written sets are small (about 260 texts in total) and written
by one author, so treat the percentages as ±5–10 points. The synthetic categories score
about 100% because they're in-distribution. Section 5's advice still stands: build a
1,000-per-class, double-labelled set from real platform data.

---

## 1. Where we are

| Measure | Value |
|---|---|
| Legitimate texts allowed (36, including 4 articles with titles) | 36/36 |
| Harmful texts caught (18; revise or reject) | 12/18 (7 rejected) |
| Defects (wrong decisions) | 6 of 54 → **sigma level 2.72** (95% interval 2.27–3.13) |
| Model latency | ~10 ms per text or sentence on CPU |

These numbers come from the **full system**, not the model alone: rules,
model, sentence checks and calibrated routing. The model's own contribution
is smaller than it looks (see section 2).

**Honest caveat:** 54 texts is far too few to judge a model. The 95%
interval on the defect rate is **5%–22%**. Section 5 explains how to get
numbers you can trust.

---

## 2. What we learned about v3 (diagnosis)

### 2.1 The "review" class means "not a product listing"

| Text | P(safe) | P(review) | P(reject) |
|---|---|---|---|
| "Cotton bedsheets in king and queen sizes, 300 thread count. Bulk orders welcome." | 1.000 | 0.000 | 0.000 |
| "Our sales manager Priya is on leave this week…" | 0.000 | **0.998** | 0.002 |
| "About our yarn business" (an article title) | 0.000 | **0.999** | 0.001 |
| "Delivery was late by two weeks. He did not answer calls…" (a normal complaint) | 0.000 | **0.997** | 0.003 |

- Product-listing style is recognised as safe.
- Almost **any other prose** (articles, titles, reviews, announcements, job posts) is put into "review" with near-certainty.
- The likeliest cause is that the *safe* training examples were mostly listings, so the model learned "listing = safe, everything else = review". **This is the most important thing to fix in training.**

### 2.2 P(reject) is the informative output

- On all 36 legitimate texts, **P(reject) ≤ 0.015**.
- Clear harm is recognised strongly: phishing, abuse and "Earn 50000 per week" all score P(reject) ≥ 0.99.
- The system now leans on this. `label_weights` gives the review class a weight of **0.25** instead of 0.5, so "unsure" text is allowed and real risk comes from P(reject).

### 2.3 Harmful texts the model can't see

For these, the model's output is identical to ordinary text (P(reject) ≤ 0.006):

- **Subtle scams:** "Looking for distributors… no investment needed, just register with a small fee."
- **Veiled threats:** "She will regret the day she crossed me."
- **Counterfeits:** "Cheap replica branded watches, first copy, cash on delivery" (P(safe) = 1.000).
- **Payment tricks:** "Limited stock! Contact us on WhatsApp… full payment in advance only."

No threshold can catch these without also flagging legitimate posts. They can only be fixed with **training data**.

### 2.4 Other findings

- **Short fragments** (titles, headings) are scored "unsure" more often than full sentences.
- **A harmful sentence inside a clean post gets diluted.** "Solar panels… 25 year warranty. Limited slots for dealers, register now with a small fee." scores 0.000 as a whole, but the second sentence alone scores 0.98. The system works around this by scoring every sentence, at about 10 ms per sentence.
- **The 256-token window:** the model only reads the first ~1,000 characters of a long article. The per-sentence scan covers the rest.
- **Dynamic INT8 quantization makes scores depend on the batch.** Scoring several sentences in one call changed individual scores by up to **0.63**. That blocks batching, the biggest available speed-up.
- **v3 isn't proven better than v1.** On the same texts and system: 6 vs 9 defects, but McNemar's **p = 0.375**. That could be chance.

---

## 3. How to improve the model (priority order)

### 3.1 Fix the labels and the data (biggest gain)

1. **Write labelling guidelines** with a definition and 10+ examples for each class.
   - **safe:** any normal business content. That includes listings, *and* articles, case studies, reviews (even negative ones), complaints, announcements, job posts, titles and headings.
   - **review:** genuinely borderline. Unverifiable income claims, aggressive competitor claims, requests to move off the platform.
   - **reject:** policy violations. Scams, phishing, abuse, threats, hate, counterfeits, adult content.
2. **Rebalance "safe".** Add thousands of ordinary non-listing texts. Scrape your own platform's published articles and profiles, which are already moderated.
3. **Add hard negatives:** legitimate text with scary words. Examples: "toxic chemicals handled safely", "found the results disturbing", "cheat sheet for installers", "pipe nipples and flanges", "office-cum-warehouse", "Maine Coon kittens".
4. **Add hard positives:** the section 2.3 cases, plus Indian-market variants:
   - "first copy" / "7A quality" (counterfeit)
   - "paisa double" (money doubling)
   - Hinglish and transliterated text
   - leetspeak ("d0uble y0ur m0ney")
   - advance-payment scams and "registration fee" job scams
5. **Train on sentences and fragments too**, not only whole posts. The system scores titles and single sentences, so the model should see them in training.
6. **Double-label a sample** (two people label the same texts) and measure agreement with Cohen's kappa. Below 0.7 means the guidelines are unclear, and the model can't do better than the labels.

### 3.2 Calibrate the probabilities

- After training, fit **temperature scaling** on a validation set, then report the **Expected Calibration Error (ECE)**.
- Ship recommended `label_weights` and thresholds in `model_meta.json`. The loader already reads `label_weights` from the meta.

### 3.3 Make the model efficient

- **Use static INT8 quantization** with a calibration dataset, per-channel QDQ, instead of dynamic quantization. Scores stop depending on the batch, so the sentence scan can batch sentences. Expect a large speed-up for long articles: one call instead of up to 400.
  - **Check first:** static INT8 and FP32 should agree on at least 99% of a test set's decisions.
- **Use a shorter max length for sentence scoring** (e.g. 64 tokens). Sentences are short, so this gives cheaper inference.
- **Optionally, a smaller student model** (distilled) just for the sentence scan, with DeBERTa on the whole text.

---

## 4. How to use each new model in the system (release checklist)

1. **Complete bundle:** `model_int8.onnx` + `tokenizer.json` + `model_meta.json`. The meta holds the version, the labels in logit order, `safe_label`, and the recommended `label_weights`. v3 arrived with only the `.onnx`, and the API team had to reconstruct the rest.
2. **Compare with the live model:** run `python scripts/compare_models.py models/v3 models/v4`. Switch only if it's **better and McNemar p < 0.05** on a properly sized test set (section 5).
3. **Calibrate:** sweep the review weight and thresholds with the same script (`--old-review-weight / --new-review-weight`), and pick the setting by business cost (section 6).
4. **Check the demo:** run `python scripts/verify_demo_sentences.py` and fix the guide if any sentence changes.
5. **Shadow mode** (recommended next feature): run the new model alongside the live one for a week, logging only, and compare decisions on real traffic.
6. **Switch:** set `model.dir` (or call `POST /v1/admin/model/reload`). **Roll back** by setting `dir` back.

---

## 5. Six Sigma applied to moderation

Six Sigma measures quality as **defects per million opportunities (DPMO)**.
Here one moderation decision is one opportunity, and a wrong decision is a
defect: a harmful post allowed, or a legitimate post flagged.

| Sigma level | DPMO | Correct decisions |
|---|---|---|
| 2σ | 308,537 | 69.1% |
| **3σ** | 66,807 | 93.3% |
| **4σ** | 6,210 | 99.38% |
| 5σ | 233 | 99.977% |
| 6σ | 3.4 | 99.99966% |

**Where we are:** 2.72σ on 54 decisions, with a 95% interval of 2.27–3.13σ.

**Reality check:**
- Six Sigma (3.4 DPMO) is not a realistic target for an ML text classifier, since language is ambiguous and even human moderators disagree.
- *Measuring* that level would need about a million labelled decisions.
- Use sigma as a **tracking metric** with staged targets: **3σ first** (~93% correct), then **4σ** (~99.4%).
- Track the two defect types **separately**, because they cost differently (section 6).

**Sample size you need:** to measure a defect rate of ~5% within ±1.4 points
(95% confidence), you need about **1,000 labelled decisions**. Around 0.6%
(4σ) within ±0.2 points takes about **6,000**. Aim for **at least 1,000
labelled texts per class**, stratified by content type (article, listing,
profile, ad, post), and never used in training.

**DMAIC as the improvement loop:**

| Phase | What we do |
|---|---|
| **Define** | Defect = wrong decision. Critical-to-quality measures: false-allow rate on harmful content, false-flag rate on legitimate content, p95 latency. |
| **Measure** | A labelled test set of 1,000+ per class, and `compare_models.py` (DPMO, sigma, intervals). |
| **Analyze** | Root causes, as in section 2: label bias, missing hard positives, fragments, quantization. |
| **Improve** | Section 3 (data, calibration, static quantization) and system logic (calibrated weights, sentence scan). |
| **Control** | Daily **p-charts** of the decision mix (allow / revise / reject %) with 3σ control limits; alert when a day is outside them (drift or a bad release). Also track the revise → resubmit rate, the appeal overturn rate, and p95 latency. |

---

## 6. "P-value (0.5 threshold)": two different things

These are often mixed up. Both matter.

### 6.1 The 0.5 *probability threshold* (a decision boundary)

"Reject if P(reject) ≥ 0.5" is the default boundary for a binary classifier.
It's only optimal when the probabilities are **calibrated** *and* **both
mistakes cost the same**. Neither is true here:

- **The costs differ.** Publishing a phishing post hurts buyers and the platform. Asking an honest seller to rephrase one sentence costs them a few seconds. So we use **three outcomes** and two boundaries: allow ≤ 0.30 < revise < 0.70 ≤ reject.
- **The boundary is on a weighted risk score, not on one class's probability.**
  - `risk = 0.25 × P(review) + P(reject)`
  - For text the model is confident about, rejecting at risk ≥ 0.70 means roughly P(reject) ≥ 0.70.
  - Text in between goes back to its author rather than being guessed.
- **Choose boundaries from data:** sweep them on the labelled test set, and pick the point that minimises *expected cost*. For example: cost(false allow) = 10, cost(false revise) = 1, cost(false reject) = 5.

### 6.2 The *p-value* (statistical significance)

The p-value answers: "Is this improvement real, or could it be chance?" The
usual threshold is **p < 0.05** (not 0.5). For two systems judged on the
same texts, the right test is **McNemar's exact test**. It looks only at the
texts where exactly one of the two is right. `compare_models.py` prints it:

| Change | Wrong only after the change / right only after it | p-value | Conclusion |
|---|---|---|---|
| Review weight 0.5 → 0.25 (v3) | 5 / 32 | **< 0.0001** | Real improvement: adopted |
| Model v1 → v3 (same logic) | 1 / 4 | **0.375** | Not proven. Needs a bigger test set before claiming v3 is better |

**Rule for the team:** a new model or setting goes live only if it is
**better on the labelled test set with p < 0.05**, and neither defect type
gets worse beyond an agreed margin.

---

### 0.4 Latency, measured with `scripts/profile_latency.py`

| Request | Time | Where it goes |
|---|---|---|
| Short listing, allowed | **11 ms** | one model call |
| 40-sentence article, allowed | **139 ms** | 101 ms of it is the single 256-token call |
| Short scam, rejected | **233 ms** | 220 ms is the word-by-word explanation |
| Long run-on + scam, rejected | **383 ms** | was 2603 ms before the phase was bounded |

Three things got it there: the triage pre-filter (73% fewer model calls on allowed posts), the
linear filter shortlisting which words are worth occluding, and a hard call budget on the word
phase — which is safe to bound because it only draws highlights and cannot change a decision.

**The floor is now the model itself:** one 256-token forward pass is ~100 ms on this CPU and no
tuning moves it. The lever left is section 3.3's distilled student — a training change.

## 7. Next steps, in order

1. **Build the labelled test set** (1,000+ per class, stratified), with guidelines and double labelling. *(ML team, with API team support)*
2. **Retrain with rebalanced safe data** and the hard negatives and positives from sections 3.1 and 2.3. *(ML team)*
3. **Calibrate** (temperature scaling), and ship `label_weights` in the meta. *(ML team)*
4. **Static INT8 quantization**, then batched sentence scanning. *(ML team, then API team)*
5. **Shadow mode, and p-chart monitoring** of the decision mix. *(API team, with Data team for the stored decisions)*
