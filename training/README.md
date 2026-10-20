# Training bonc-v4

How the v4 moderation model is built, from the datasets in `res/` to the bundle in
`flask_api/models/v4`. Every step is a script, so a new round is a re-run with more data.

## What v4 is

| | v3 | v4 |
|---|---|---|
| Labels | `safe`, `review`, `reject` | `safe`, `unsafe` |
| Risk score | `0.25·P(review) + P(reject)` (hand-tuned) | `P(unsafe)`, temperature-calibrated |
| Decision | allow ≤ 0.30 < revise < 0.70 ≤ reject | **allow ≤ 0.5 < reject** (the author rewrites) |
| Safe training data | product listings only | listings, articles, profiles, reviews and complaints, everyday prose, titles, sentences, word windows |
| Quantization | dynamic INT8 (scores change with batching) | chosen by measurement: must agree with FP32 on ≥ 99% of decisions **and** be batch-invariant |

A calibrated P(unsafe) makes 0.5 a meaningful boundary: above it, harm is more likely
than not.

## Pipeline

Use the GPU venv (`D:\AI\bonc-train`, torch with CUDA). Data and checkpoints live on `D:`
because `E:` is nearly full.

```powershell
$env:HF_HOME = "D:\AI\huggingface"
$py = "D:\AI\bonc-train\Scripts\python.exe"
& $py synth_data.py --n 80000              # -> D:/AI/bonc-data/synthetic_v4.csv
& $py prepare_data.py                      # -> D:/AI/bonc-data/{train,val,test}.csv
& $py train.py --epochs 2                  # GPU, ~50 min on an RTX 4050 -> D:/AI/bonc-v4/ckpt
& $py calibrate_export.py                  # temperature, test metrics, ONNX, quantization -> flask_api/models/v4
& $py train_triage.py                      # the linear pre-filter -> flask_api/models/triage-v1
& $py tune_triage.py --device cuda --write  # pick its threshold by decision impact
& $py e2e_eval.py --device cuda            # whole system, per held-out set
cd ..\flask_api
& $py scripts\compare_models.py models\v3 models\v4 --no-builtin --testset ..\training\eval_handwritten.csv
```

`--device cuda` runs the evaluation scripts through the checkpoint on the GPU instead of the
ONNX bundle on CPU: a full `e2e_eval` pass drops from ~5 minutes to ~42 seconds, and the two
agree exactly (`torch_scorer.verify_against_onnx`: 152/152 decisions, max difference 0.0003).
The **deployed service is always ONNX on CPU** — the bundle contract requires it — so confirm
anything a GPU sweep decides with a final `--device cpu` run.

### Which phases can use the GPU

| Phase | Device | Why |
|---|---|---|
| `train.py` | GPU | ~90 min for 1.5 epochs on an RTX 4050 |
| Temperature + test metrics, `train_triage.py` teacher scoring | GPU | ~240k spans scored in a couple of minutes |
| `tune_triage.py --device cuda`, `e2e_eval.py --device cuda` | GPU | ~12x faster than the CPU bundle, decisions identical |
| **Quantization comparison** in `calibrate_export.py` | **CPU, necessarily** | It measures the artefact the service ships, which runs on `CPUExecutionProvider`. Timing it on a GPU would answer the wrong question, and `onnxruntime-gpu` doesn't work on this host anyway (its CUDA provider wants the CUDA 13 toolkit; only torch's 12.4 runtime is present). |
| Tests, `verify_demo_sentences.py`, `e2e_articles.py` | CPU | They exercise the deployed path end to end |

`--candidates` keeps that CPU-bound phase short: `fp32_emb16` has won every round, and each
rejected candidate costs several minutes of CPU, so only it is rebuilt by default. Pass
`--candidates all` when the base model, the ONNX opset or the runtime changes — that is when a
previously-losing quantization could start winning.

## The triage pre-filter

`train_triage.py` and `tune_triage.py` build Layer 1.5, a logistic regression over hashed
n-grams that keeps obviously-safe spans away from DeBERTa (see `flask_api/README.md` for how
the service uses it). Two things make it safe rather than just fast:

- **It is distilled from the model, not from the labels.** The target is "would v4 have
  flagged this span?" — a span v4 allows costs nothing to skip, whatever its document was
  labelled. Training on the document label instead inflated the positive rate from 11% to 29%
  and wrecked the skip rate, because most sentences of a scam-carrying article are innocent.
- **The threshold is chosen on decisions, not on spans.** Span-level misses badly overstate
  the risk: the whole text is still scored by the full model, so a skipped sentence only
  matters if it was the thing holding the decision up. `tune_triage.py` sweeps thresholds
  through the real pipeline and takes the highest with no decision change (0.2), halved for
  margin (**0.1**). At 0.4, two harmful posts start being allowed.

Retrain both whenever the model changes. `app/triage.py` owns the feature hashing that
training and serving share, and bumping `FEATURE_VERSION` there makes the loader reject
stale weight files instead of scoring nonsense.

## Data

| Source | Rows | Original label | v4 label |
|---|---|---|---|
| `res/Dataset123.xlsx` | 212k | 0 listings / 1 "review" / 2 "reject" | see relabelling |
| `res/bonc_optimized_final_dataset.csv` | 55k | same scheme, balanced; mostly copies of Dataset123 plus leetspeak variants | deduplicated, same relabelling |
| `res/bonc_network_moderation_dataset.csv.xlsx` | 879 | SAFE / REVIEW / REJECT | SAFE, REVIEW → safe; REJECT → unsafe (on-domain, counted twice) |
| `synthetic_v4.csv` (`synth_data.py`; a copy is in `res/`) | 90k | generated | safe / unsafe |
| sentences and 6–24-word windows cut from safe texts | 30k | — | safe |

**Relabelling (`prepare_data.py`).** Label 1 in the `res/` data mixes clean Wikipedia and
Twitter prose with profane and offensive tweets. Training on it as its own class is what
taught v3 "anything that isn't a listing is review" (guide section 2.1). It's split with
the profanity word lists (`flask_api/config/private/wordlists`, minus the B2B exclusions):
text with profanity or slurs → unsafe, everything else → safe. Label 2 → unsafe.

**Synthetic data (`synth_data.py`)** covers what `res/` lacks (guide sections 2.3 and 3.1):

- **Safe:** listings for 30+ Indian B2B product lines, company profiles, business articles (export, quality, logistics, compliance, safety, market, fraud awareness), personal articles, reviews and *complaints*, announcements, job posts, enquiries, titles, and Hinglish.
- **Everyday, non-business sentences in simple grammar:** family, weather, school, travel, feelings ("our dog died", "I was angry"). Articles are free text and needn't sound like business.
- **Hard negatives:** scary words used legitimately ("toxic chemicals handled safely", "30% advance, balance on delivery", "we are not a first-copy seller", "BONC will never ask for your OTP").
- **Hard positives:** subtle and registration-fee scams, phishing, lottery, counterfeits ("first copy", "7A", "master copy"), advance-payment and off-platform tricks, veiled threats, insults, group hate, defamation, adult services, illegal goods, fake reviews.
- **Insults aimed at a person, in either order** ("he is a liar" and "what a liar he is"): policy from the senior review.
- **Obfuscation:** leetspeak, spaced letters, Hinglish. Harmful sentences are also hidden inside clean posts.
- **Grammar noise:** dropped articles, wrong agreement or tense, run-ons, no punctuation. Applied to safe **and** unsafe text, so broken English neither raises the risk nor hides harm.

**Splits** are made by text group (a text and the sentences cut from it stay together): 88%
train, 6% validation (early stopping and temperature), 6% test (never used for training or
calibration).

**`eval_handwritten.csv`** is a separate hand-written set written for this round. It is
never used for training, tuning or calibration and is not generated by the templates, so it
checks generalisation beyond the generator's phrasing. At about 150 texts it's still small
(see the guide's section 5 on sample size). Grow it with real, double-labelled platform
data before trusting sigma levels.

## Scripts

| Script | What it does |
|---|---|
| `synth_data.py` | Generates the synthetic dataset (a copy lives in `res/`) |
| `prepare_data.py` | Relabels `res/`, adds spans, splits train/val/test, drops held-out eval texts |
| `train.py` | Fine-tunes DeBERTa-v3-small on the GPU |
| `calibrate_export.py` | Temperature scaling, test metrics, ONNX export, quantization choice, bundle |
| `shrink_embeddings.py`, `quantize_weight_only.py` | The quantization candidates `calibrate_export.py` compares |
| `train_triage.py`, `tune_triage.py` | The linear pre-filter and its threshold |
| `torch_scorer.py` | GPU scorer for offline sweeps, with a check that it matches the shipped bundle |
| `generate_support_complaints.py` | The public-surface grievance genre: buyer vs seller |
| `generate_workplace_grievances.py` | The same genre, employee vs employer (toxic workplace, overwork, profane venting) |
| `train_incremental.py` | Adds a category to an existing fine-tune without retraining the corpus |
| `e2e_eval.py` | Whole system (gate + model + scans + routing) per held-out set |
| `eval_*.csv` | Hand-written held-out sets, never trained on |

## Adding a category without a full retrain

`train.py` always starts from `microsoft/deberta-v3-small` over the whole corpus: 40-44 min on
this machine, and longer every time a category is added, because the corpus is what grows. The
admin tab's "upload a category and train" feature cannot wait an hour per upload, so
`train_incremental.py` warm-starts from the checkpoint already in service and trains on the
upload plus a ~15% rehearsal sample of the old data.

    python training/train_incremental.py         --init D:/AI/bonc-grievance-v5/ckpt         --new res/workplace_grievances.csv         --replay D:/AI/bonc-grievance-data-v5         --out D:/AI/bonc-grievance-v7inc/ckpt

Measured on the 103,000-row workplace category: **9.8 min against 43.8 min**, and the selected
checkpoint came at step 940 of 3762 - under three minutes of actual training. Downstream is
unchanged: `calibrate_export.py` then `accept_model.py`, same as `train.py`'s output.

Three things about it are load-bearing rather than optional:

**The rehearsal sample.** Training on the upload alone forgets the categories the checkpoint
already holds, and the training loss cannot show it, because the loss only ever sees the new
rows. `--replay-frac 0` is available and the run says what it cost.

**The regression gate.** The old validation set is measured with the incoming weights before a
single step is taken, so the baseline is this checkpoint's own behaviour rather than a number
from another run's log. If old-capability AUC falls past `--max-regression` the script exits
non-zero. The admin tab should gate on that exit code: an upload that silently degrades the
live model is worse than one that fails, because the failure is visible the same afternoon.

**Checkpoint selection on the SUM of both validation losses.** A model that wins on the new
category by forgetting the old one wins on new-val loss alone, so selecting on new-val loss
picks the damaged checkpoint.

### The workplace category, three rounds

All four checkpoints warm-started from `bonc-grievance-v5/ckpt` (the one behind the deployed
`grievance-v3`) with a 15% rehearsal sample. Caught / wrongly-refused at **threshold 0.50**,
measured by `eval_grievance_model.py` on the three sets that were not generated:

| | rows | workplace (n=56) | handwritten-1 (n=52) | handwritten-2 (n=38) |
|---|---|---|---|---|
| v5 baseline | - | 42.3% / 16.7% | 76.9% / 0% | 61.1% / 0% |
| round 1 | 100k | **80.8% / 30.0%** | **96.2%** / 0% | **94.4%** / 0% |
| round 2 | 103k | 61.5% / **13.3%** | 80.8% / 0% | 77.8% / 0% |
| round 3 | 131k | 76.9% / 16.7% | 80.8% / 0% | 88.9% / 0% |

A fourth run, `v8inc`, warm-started from `bonc-grievance-v6/ckpt` instead - see below. At
**threshold 0.978**, which is what `settings.yaml` ships:

| | workplace (n=56) | handwritten-1 | handwritten-2 |
|---|---|---|---|
| grievance-v6 | 53.8% / 23.3% | 73.1% / 0% | 72.2% / 0% |
| round 3 (on v5) | 61.5% / 13.3% | 80.8% / 0% | 77.8% / 0% |
| **v8inc (on v6)** | **76.9% / 10.0%** | **84.6% / 0%** | **88.9% / 0%** |

Two more runs settled which base is right, and the answer was not the obvious one. The v6 run
had been interrupted at step 19200 of 23342, so it was finished properly
(`bonc-grievance-v6full`, 36.5 min) and the workplace category stacked on that
(`bonc-grievance-v8full`). Both at threshold 0.978:

| | workplace | handwritten-1 | handwritten-2 |
|---|---|---|---|
| v6 interrupted | 53.8% / 23.3% | 73.1% / 0% | 72.2% / 0% |
| v6 completed | 76.9% / 36.7% | 76.9% / 3.8% | 94.4% / 5.0% |
| v8inc (on v6 interrupted) | **76.9% / 10.0%** | **84.6% / 0%** | **88.9% / 0%** |
| v8full (on v6 completed) | 57.7% / 10.0% | 61.5% / 0% | 83.3% / 0% |

**Finishing the interrupted run made it worse.** It bought recall and became the only
checkpoint that wrongly refuses legitimate text on BOTH hand-written sets, and `train.py`'s own
checkpoint selection says why: best val loss came at step 8754 of 17508 and rose from 0.0049 to
0.0780 over the second epoch. Two epochs is too many for this corpus at 280k rows. Stacking the
workplace category on the looser base inherited the looseness and lost 19 points of workplace
recall against v8inc.

So `bonc-grievance-v8inc/ckpt` is the artifact: the only checkpoint here with both >=76%
workplace recall and <=10% false refusals, and 10.0% is what `settings.yaml` records for the
deployed supplier-only model. It holds the deployed precision, adds a category, and improves
supplier recall by 11-17 points. A longer-trained base is not a better base, and "the run
finished" is not evidence of anything - the independent sets are.

**THE BASE CHECKPOINT MATTERED MORE THAN THREE ROUNDS OF DATA WORK.** Rounds 1-3 all
warm-started from `bonc-grievance-v5/ckpt`, because v5 is the checkpoint behind the deployed
`grievance-v3` bundle and the v6 run had been interrupted at step 19200 of 23342. Defensible,
and wrong: v6 already caught 80.8% of the workplace set at threshold 0.50 with no workplace
data in it at all, so rounds 1-3 spent their effort recovering ground v6 already held. Same
data, same hyperparameters, same 11 minutes, one different `--init`, and v8inc dominates round
3 on every axis at once - +15.4 points of recall AND -3.3 points of false refusals.

The lesson for the admin tab: `--init` should be whatever checkpoint is CURRENTLY BEST, which
is not always what is currently deployed, and the two drift apart exactly when someone is
mid-way through improving the model. Record the lineage per checkpoint or this happens again.

Round 1 has the best recall everywhere and refuses three in ten legitimate employer posts,
which is not shippable. Round 2 halved the false refusals and gave back most of the recall.
Round 3 recovers the recall at two thirds of round 2's precision cost and is the best balance
of the three - but 16.7% is still above what should go in front of members, and the remaining
headroom is not another round of frames. It needs workplace text nobody generated.

Note that 0.978 - the threshold in `settings.yaml` - was chosen for `grievance-v3`'s score
distribution and does not transfer: every checkpoint here scores differently, and round 3
reads 61.5% / 13.3% at 0.978 against 76.9% / 16.7% at 0.50. Re-pick the operating point per
checkpoint off the sweep, or let the moderation level choose it.

The two bugs the rounds found are the same bug twice, and the second is the more useful one:

  round 1   the grievance TOPIC predicted the label, so "I asked for leave and got it
            without a single question" refused at 0.9998 while "I like my team" passed at
            0.0004. Fix: 16,000 first-person, on-topic, good-outcome rows.
  round 2   NEGATION predicted the label, because the fix above put nobody/no one/not one on
            the negative side only. "Nobody here gives a damn as long as the dispatch number
            looks right" fell to 0.000. Fix: 9,000 negation-bearing positives, plus
            contentment-through-negation negatives so the marker does not simply invert.

The generalisation: **a surface feature added to one side only becomes the feature the model
uses instead of the meaning.** Check both sides of any marker a new category introduces.

`--replay` must be the data directory the `--init` checkpoint was trained on. The deployed
grievance lineage is `bonc-grievance-data-v5` -> `bonc-grievance-v5/ckpt` ->
`flask_api/models/grievance-v3`.

Adding a *category* is usually not adding a *label*: the decision stays binary and the new
category is a new region of label 1. `--add-labels` grows the head for a genuinely new output
class, which changes the ONNX output shape, the calibration and every consumer of the score -
so it is explicit, and the script will not infer it from the uploaded CSV.

## Two data rules learned the hard way

**Every safe example must be one the gate would also allow.** `prepare_data.py` drops safe rows
the shipped gate blocks, and prints how many. It caught 373 on its first run — mostly real
Wikipedia prose carrying gate terms that the lexicon relabelling had marked safe, plus generated
rows using dismissive words (`worthless`, `pathetic`, `beyond stupid`) that are *already* on the
gate's blocklist. Training on those teaches the model to disagree with the rules it sits behind.
`DISMISSIVE_NEUTRAL` vs `DISMISSIVE_BLOCKED` in `synth_data.py` keeps the two apart.

**A class written as a handful of fixed strings gets memorised, not learned.** Hinglish was 12
literal sentences. Round 3 scored those 12 at 0.001 and a held-out paraphrase —
"Hamare paas 20 saal ka experience hai, quality mein koi compromise nahi" — at **0.99 unsafe**,
which for an Indian B2B marketplace is a serious defect. Every class that matters is now
generated compositionally (slots for product, quantity, city, period), the way the everyday
English already was. When a template happens to reproduce an evaluation sentence exactly,
`prepare_data.py` drops it, but the eval row is then a weak near-template test — so
`eval_dismissive.csv` carries a `hinglish_heldout` group written in deliberately different
vocabulary and structure.

**Praise and complaint share their subject matter, so a new category needs its own praise.**
This rule is stated in `generate_support_complaints.py` and round 1 of
`generate_workplace_grievances.py` failed to carry it over: 15 ordinary-workplace rows against
45,000 grievance positives. The generated test split said 99.97% accuracy. The independent set
`eval_grievance_workplace.csv` said 76.9% caught and **33.3% of legitimate employer writing
refused**, with a flat threshold sweep - so never a dial. The cause was the topic, not the
sentiment or the voice:

    0.9999  I have worked here nine years and been treated fairly throughout.
    0.9998  I asked for three days leave and got it without a single question.
    0.0004  I like my team and the work suits me.

Name a grievance topic - tenure and treatment, asking for leave, a supervisor's temper - and
it refused however the story ended; leave the topic out and the identical sentiment passed. It
had learned the subject. Round 2 added 16,000 first-person, on-topic, good-outcome rows, a
negation block (`nobody is asked to work a Sunday without being paid` refused at 0.999 and
contains no grievance at all), and management aphorisms.

The general form: when you add a category, the negatives it needs are not "things unlike it" -
they are *the same subject matter with the opposite verdict*, at comparable volume.

## Rounds and results

- **Round 1:** held-out hand-written accuracy 96.1%, with 2 false rejects: normal advance-payment terms, and "earn … register" in a legitimate promotion. It missed veiled threats, "read out the code" phishing and leetspeak.
- **Round 2:**
  - Before changing any data, a **second held-out set** (`eval_handwritten_2.csv`) was written. Then concept-level data was added: payment-terms variants, legitimate promotions, negations, neutral identity mentions, short fragments, veiled threats, code/PIN phishing, identity hate, and more leetspeak.
  - Identity words were removed from the profanity split. Round 1 had learned that "Homosexuality" as a heading is unsafe (0.99).
  - Result: no false rejects on either hand-written set; held-out set 2 AUC 1.000.
- **Round 3** added the `dismissive` class (contempt aimed at a person) with `professional_criticism` as its counterweight. It worked — "his way of thinking is absurd" 0.999 versus "the price they quoted was absurd" 0.002 — but regressed Hinglish.
- **Round 4** fixed Hinglish by generating it compositionally (see above), then regressed the dismissive set to 85.7% legitimate-allowed: four complaints read as contempt.
- **Round 5** fixed those four, each a distinct gap:
  - `pathetic`/`worthless` were barred from *every* safe example, so the model learned the word itself was abuse. They are gate terms only when a pronoun shares the sentence, so `blunt_about_a_thing()` now uses them safely with no pronoun present.
  - The targeted check scores "their warehouse is a joke" as a standalone fragment with no context, and that shape had no safe examples — `about_someones_work()` supplies them.
  - Hinglish haggling and emotional complaint ("bahut dukh hua") were missing; short forms scored 0.000 but conversational ones failed.
  - The guard was tightened to drop safe rows the gate marks **revise** as well as **block** — neither is published, so both contradict a "safe" label. 373 → 863 rows dropped.

| Held-out set (full system) | round 3 | round 4 | **round 5** |
|---|---|---|---|
| hand-written | 97.7% / 92.2% | 100% / 93.8% | **100%** / 92.2% |
| hand-written 2 | 100% / 90.9% | 100% / 95.5% | **100%** / 95.5% |
| dismissive | 95.5% / 100% | 85.7% / 100% | **100%** / 93.3% |
| articles | 100% / 83.3% | 100% / 83.3% | **100%** / 83.3% |
| test split | 96.7% / 97.7% | 95.3% / 98.7% | **96.3%** / 98.3% |

Round 5 has the best validation loss of any round (0.0582) and a **false-reject rate of 0.0000
on every hand-written set** — no legitimate content turned away. The trade is a slightly higher
false-allow rate (test split 2.26% → 2.73%); a wrongly-rejected seller is a product defect,
while a missed borderline post still meets the gate and the sentence scan behind it.

- **Full system, the original 219 held-out texts:** all 127 legitimate allowed and 87 of 92 harmful caught (v3: 118/127 and 45/92). McNemar p < 0.0001, sigma level 3.50. Details are in `docs/MODEL_IMPROVEMENT_GUIDE.md`, section 0.

Numbers are in `flask_api/models/v4/model_meta.json` (`metrics`, `quantization_report`).
Re-run everything with the pipeline above, then `e2e_eval.py`.

## Labelling guidelines (v4)

**safe (publish):** any normal content, even when negative, informal or badly written:

- product listings, prices and offers, including loud marketing ("LIMITED TIME!! 50% OFF")
- articles, case studies, how-tos, news, personal stories, titles and headings
- reviews and complaints, even harsh ones about *service* ("delivery was two weeks late, they refused a refund")
- everyday sentences and feelings ("I was furious when the train was late")
- normal payment terms (advance plus balance, bank transfer to a company account), warnings about fraud, anti-counterfeit statements
- Hinglish and non-native English

**unsafe (reject; the author must rewrite):**

- **fraud:** guaranteed returns, money doubling, registration, joining or processing fees for jobs, dealerships, loans or prizes; phishing for OTPs, passwords or cards
- **payment tricks:** full advance to a personal account, pay off-platform, crypto or gift cards only
- **counterfeits and fake documents:** first copy, replica, 7A, master copy, fake ISI marks, fake GST bills, degrees or IDs
- **abuse:** insults, profanity or threats aimed at a person, business or reader, veiled threats included ("she will regret crossing me")
- **dismissive contempt for a person:** treating someone as stupid or worthless rather than disputing what they did — "his way of thinking is absurd", "he has no clue how this works", "don't bother explaining, she won't understand". The test is the **target**, not the adjective: the same words about a price, a delay or a rule are safe ("the price they quoted was absurd"), and so is factual criticism of someone's work ("his estimate was too optimistic", "I disagree with his approach"). The gate cannot make this distinction — it only sees a pronoun and a term in one sentence — which is why it lives in the data.
- **hate:** generalisations against a religion, caste, region, gender or community
- **defamation:** accusing a named business of crimes as fact ("X adulterates its ghee, boycott them")
- **adult services, illegal goods** (drugs, weapons, wildlife), fake reviews or followers, betting

**When unsure:** ask "would a reasonable buyer or seller be harmed or deceived if this were
published?" A harsh but factual complaint is safe. The same complaint with an insult
attached is unsafe.

**Before the next round:** double-label a random 500 texts and measure Cohen's kappa
(guide section 3.1.6). Below 0.7, fix these guidelines before adding data.
