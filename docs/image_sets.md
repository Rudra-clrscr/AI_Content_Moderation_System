# What each image set is, and what it can measure

`images/` is gitignored. This file is not — it is the record of which sets a number may
honestly be quoted from, because two of them cannot support one and said nothing about it.

## Explicit images were found in the `minors` set

**18 files were quarantined out of `images/minors/` to `images/_quarantine_explicit/`.** They
are explicit close-ups, in a folder of children's photographs. They were used for scoring only:
the probe is fitted on `safe`/`weapon`/`gore`, `prompts.npz` holds text embeddings and no image
data, so nothing shipped ever contained them.

**Every one with a provenance row traces to a single search term: `"child face close up"`.** On
Commons, "close up" plus a body returns anatomical and pornographic close-ups, and the
collector's filters read titles and categories — they cannot see pixels. The term is removed.

Fifteen were found by the reference classifier in `models/nsfw_ref`; three were found by eye
during the manual audit, **including one the classifier scores 0.000** — it is certain that
file is ordinary and it is not. That number is why `collect_commons_images.py` now says in as
many words that its pixel gate is a net and not a guarantee.

The gate is measured, not guessed: on the audited set the highest score among pictures that
belong there is 0.351 and the lowest among the quarantined ones is 0.760, so the ceiling sits
at 0.40. Tested, it rejects 17 of the 18 and none of 60 legitimate portraits.

Three of the eighteen came from `minors/kid_neg/`, which was supplied rather than collected.

## The audit, and why it happened

`violence` (147) and `threats` (86) were collected from Wikimedia Commons by search term and
reported recalls of 47% and 38%. Those were the two weakest figures in the policy and were
quoted as the model's weakness in the settings file, the contract document and the pull
request.

Every picture in both sets was then looked at, on contact sheets, one by one. The figures were
not measuring the model.

**`threats`, 86 images.** Roughly ten are threatening. The rest are, in descending order of
frequency: **about twenty photographs of metal and punk bands on stage** (the collector's terms
pulled in band names and gig listings), handwritten manuscripts, candlelit vigils, Victorian
engravings and caricatures, portraits, a wild boar, a crown, a world map, a beach, the Tower of
London, award ceremonies and a Valentine's card.

**`violence`, 147 images.** Perhaps twenty-five could be called violent, and most of those are
**boxing, professional wrestling and MMA** — sport, which a gym or a sportswear seller may
legitimately post. Eight are publicity shots of the same wrestler posing alone. The remainder
includes an FBI award handshake, Christmas markets, military swimming drills, an F-16 on a
runway, office photographs, a meerkat, a snake illustration, and a great deal of 19th-century
engraving.

## What this means for the numbers

- **`violence` 47% and `threats` 38% must not be quoted as recall.** Both sets are registered
  as `None` (mixed expectation) in `scripts/eval_clip_images.py`, exactly as `non_graphic` is,
  and are reported but excluded from recall and false-positive aggregates.
- Read the other way, the model *refused* 69 of 147 and 33 of 86 of these. Since so few are
  genuinely either thing, a good share of those refusals were false positives hiding inside a
  recall figure. The audit's real finding is not that the model is weak here — it is that
  nobody knew which way the error ran.
- **`unrest_real`** (84 supplied Indian press photographs of police-protester confrontation) is
  the trustworthy measure for this category, at **99%**. When it and the collected sets
  disagree, it wins.

## The other four sets, audited the same way

Every remaining collected set was then reviewed picture by picture. None is as bad as the two
above, and none is clean:

| set | n | genuine | what the rest is |
|---|---|---|---|
| `revealing` | 135 | **~79%** | archival garment photographs, Victorian fashion plates, clothed headshots |
| `minors` | 193 | **~79%** | classical **busts and sculptures** of youths, adult portraits, a photographer's paper label |
| `romance` | 121 | **~69%** | sunset beaches and landscapes — the term pulled *romantic scenery* — monuments, two cats, a nebula, a telegram |
| `unrest` | 218 | **~67%** | memorials, commemorative plaques, buildings, press conferences, engravings |

All four stay registered as recall sets. The contamination is ordinary noise rather than a
different subject, which is what separates them from `violence` and `threats`: a set that is
70% right measures something, a set that is 15% right measures nothing. Expect the true recall
on each to be somewhat higher than the headline, since a chunk of every denominator is not the
thing being counted.

`minors` was reviewed over 192 of its 194 files.

## The pattern, which is not limited to these two

| provenance | recall | false refusal |
|---|---|---|
| supplied by BONC | **99%** | 10% |
| packaged datasets | 98% | 2% |
| collected from Commons | **68%** | 15% |

Every weak figure in the policy is a Commons figure. Collecting by search term gets diversity
and does not get representativeness, and a set assembled that way can be worse than no set,
because it produces a number that looks like evidence.

## Sets, and what each is for

| set | n | expect | provenance | notes |
|---|---|---|---|---|
| `flag` | 300 | block | dataset | pistols and knives |
| `allow` | 200 | pass | dataset | phones, wallets, everyday objects |
| `graphic_injury_gore_500/.../graphic_injury_gore` | 300 | block | dataset | |
| `graphic_injury_gore_500/.../non_graphic` | 200 | **mixed** | dataset | its own README says it holds weapons and fighting |
| `BONC_Positive` | 62 | pass | **real traffic** | the only set drawn from real uploads. 63 files, two identical |
| `body_parts` | 105 | pass | collected | athletes, physiotherapy, anatomy |
| `crowds_safe` | 206 | pass | collected | trade fairs, queues, processions, festivals |
| `unrest` | 218 | block | collected | |
| `unrest_real` | 84 | block | **supplied** | Indian press photography. Trust this one |
| `weapons` | 200 | block | collected | varied, incl. lathi |
| `violence` | 147 | **mixed** | collected | **audited — see above** |
| `threats` | 86 | **mixed** | collected | **audited — see above** |
| `romance` | 121 | block | collected | |
| `revealing` | 135 | block | collected | |
| `minors` | 208 | block | collected | `kid_neg/` inside it duplicates 73 of these |
| `minors_incidental` | 167 | pass | collected | children present, not the subject |
| `nudity` | 37 | block | supplied | artistic nudes; one genre, thin |
| `gym_neg` | 72 | block | supplied | physique display |
| `gym_positive` | 49 | pass | supplied | gym as a business. 3 are mislabelled |
| `weddings` | 205 | block | supplied | arrived in four batches |
| `weddings_commons` | 14 | block | collected | split out of `crowds_safe`; mostly paintings |

`scripts/eval_clip_images.py` walks these recursively and de-duplicates by content hash, so a
foldered drop and a duplicated copy are both handled without changing the count silently.

## If you want the weak categories measured properly

Supply them the way the gym, wedding and protest sets were supplied: pictures that look like
what members actually upload. 84 supplied protest photographs produced a 99% that 365 collected
ones could not. That is the whole lesson.
