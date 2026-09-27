# Model improvement and efficient use: a guide for the next training rounds

For the ML team, the API team and reviewers. Every number here was measured
on the live system (model `deberta-v3-small-int8-bonc-v3`) with the scripts
in `flask_api/scripts/`, so it can be reproduced.

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

## 7. Next steps, in order

1. **Build the labelled test set** (1,000+ per class, stratified), with guidelines and double labelling. *(ML team, with API team support)*
2. **Retrain with rebalanced safe data** and the hard negatives and positives from sections 3.1 and 2.3. *(ML team)*
3. **Calibrate** (temperature scaling), and ship `label_weights` in the meta. *(ML team)*
4. **Static INT8 quantization**, then batched sentence scanning. *(ML team, then API team)*
5. **Shadow mode, and p-chart monitoring** of the decision mix. *(API team, with Data team for the stored decisions)*
