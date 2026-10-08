# Measuring the `sexual` label

**Status: recall is unknown, and the gap is deliberate rather than an oversight.**

`config/visual_policy.yaml` carries a `sexual` label because the policy needs one. Everything
measured about it so far is the *false-positive* side, using `images/body_parts` — 105 licensed
pictures of athletes, physiotherapy, anatomy diagrams and swimming. That set answers the
question that matters commercially: **does this label refuse ordinary members?** It does not
(3 of 105 at the shipped threshold, and that is shared with `gore`).

What no measurement here shows is whether the label **catches what it is named for**. The
honest summary for anyone reading a result: `weapon` and `gore` have measured recall,
`sexual` does not.

## Why there is no positive set in this repo

Collecting one by scraping the open web is not an option and should not be attempted. Untargeted
collection of sexual imagery carries a real risk of pulling illegal material, which would be a
far worse outcome than an unmeasured label — for the person running the scraper most of all.
There are also consent and licensing problems that a scrape cannot solve. `images/body_parts`
was collectable precisely because every subject in it is benign.

Two routes work. The second is better, and needs no such dataset at all.

## Route 1 — request a licensed research dataset

These exist, are used in published work, and are released under agreements rather than
downloads. Expect to sign something and to state a research purpose.

| Dataset | Content | How to get it |
|---|---|---|
| [NPDI / Pornography-2k](https://arxiv.org/pdf/2212.00668) | 1,000 pornographic and 1,000 ordinary videos, ~140 hours | Written request to Prof. Sandra Avila, `sandra@ic.unicamp.br`. Copyright prevents direct distribution, so access is granted case by case |
| [LSPD](https://inass.org/wp-content/uploads/2021/09/2022022819.pdf) | 500,000 labelled images, 4,000 videos, with sexual-object annotations | Request from the authors of the paper |

Once a set is on disk, no code change is needed: put it in `images/sexual_positive/` and
`flask_api/scripts/eval_clip_images.py` picks it up as a set expected to be caught, alongside
weapons and gore. It is skipped when absent, so nothing breaks in the meantime.

Handle whatever arrives as the agreement requires, keep it off shared machines, and do not
commit it — `images/` is already gitignored.

## Route 2 — a reference classifier over BONC's own uploads (recommended)

The question that actually matters is not "what does this label score on an academic corpus"
but "what does it do to BONC's traffic". That can be answered without anyone holding a
pornography dataset:

1. Take [`Falconsai/nsfw_image_detection`](https://huggingface.co/Falconsai/nsfw_image_detection)
   — a ViT fine-tuned for exactly this, Apache 2.0, so it can be used commercially, and
   reported at 98% on its own evaluation set.
2. Run it and the CLIP `sexual` label over the same stream of real uploads.
3. Compare. Every image the reference calls NSFW and the `sexual` label does not is a miss, and
   it is a miss **on traffic BONC actually receives**, which is worth more than recall on a
   corpus of professional pornography that no B2B marketplace sees.

This measures the label where it is used, needs no restricted data, and the disagreements are
reviewable by whoever already handles reports.

It also raises a question worth asking once the numbers exist: if a dedicated classifier is
being run anyway, should it *replace* the `sexual` prompts rather than grade them? A purpose-
trained head will beat four zero-shot wordings. The argument for keeping the prompts is that
the policy stays in one file and one model; the argument against is that `sexual` is the one
label where that convenience has not been shown to work. Decide it on the measurement, not in
advance.

## What not to do

- **Do not lower `reject_min` to compensate.** The threshold is shared across every label, and
  the measured cost is in `config/settings.yaml`: going from 0.90 to 0.50 to chase this one
  label would refuse 17 of 105 ordinary body pictures and 5 of 200 safe objects.
- **Do not add more `sexual` prompts untested.** Three prompt changes have cost recall in this
  project already, each one measured only after the fact. The rule in `visual_policy.yaml`
  holds here too: name the scene, not the pose — and re-run `eval_clip_images.py` after.
- **Do not report the label as working.** Until one of the routes above produces a number, a
  result carrying `category: sexual_content` means the prompts matched, not that the label has
  a known hit rate.
