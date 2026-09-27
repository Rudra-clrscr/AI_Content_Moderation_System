# Demo guide: BONC Content Moderation

This guide covers how to run a live demonstration of the moderation service:
what to click, what to say, and a bank of tested sentences to paste.

Every sentence here was checked against the real model
(`deberta-v3-small-int8-bonc-v3`), so the decision listed next to it is what
you will see. **Paste the sentences exactly as written.** The v1 model can
change its answer when a few words are added or removed.

## Start here: set up and start the server

The setup commands depend on your terminal. Pick your guide, follow it until
the server is running, then come back here to section 2.

| Your prompt looks like | Terminal | Guide |
|---|---|---|
| `PS C:\Users\you>` | PowerShell (also Windows Terminal and VS Code) | **[DEMO_GUIDE_POWERSHELL.md](DEMO_GUIDE_POWERSHELL.md)** |
| `C:\Users\you>` | Command Prompt | **[DEMO_GUIDE_CMD.md](DEMO_GUIDE_CMD.md)** |

---

## 1. What the demo shows

Each piece of content passes through four steps:

```
 content ──► 1. Regex gate ──► 2. DeBERTa model ──► 3. Threshold routing ──► 4. Audit log
             (instant rules)    (AI risk score)       (allow / revise / reject)
```

| Step | What it does | Typical time |
|---|---|---|
| 1. Regex gate | Checks fixed rules: scam wording, abusive words, blocked domains. A **block** rejects immediately and skips the model. A **revise** rule highlights the problem so the author can fix it. A **flag** is only recorded. | ~0.1–0.5 ms |
| 2. Model | A fine-tuned DeBERTa-v3-small, INT8-quantized, gives a risk score from 0 to 1. | ~10–25 ms |
| 2c. Sentence scan | Posts with several sentences: each sentence is scored on its own, and one reject-level sentence rejects the whole post. This catches a scam hidden inside a clean listing. | ~10 ms per sentence |
| 3. Routing | Risk ≤ 0.30 is **allowed**. Risk ≥ 0.70 is **rejected**. Anything in between is sent back to the author to **revise**, with the problem parts highlighted and an instruction for each. There's no human-review queue. | — |
| 4. Audit log | Every decision is stored with its score, the model and rule versions, and the thresholds used. | — |

---

## 2. Check the page and warm up

Open **http://127.0.0.1:8000/demo**. The chips at the top of the page should read:

`status ready` · `mode sync` · `model deberta-v3-small-int8-bonc-v3` · `gate rules 8` · `allow ≤ 0.30 · revise · reject ≥ 0.70 (risk)`

If they don't, see section 6.

**Warm up before the audience arrives.** Click each example button once, then
refresh the page (`F5`) to clear the session table.

---

## 3. Run of show (about 10 minutes)

The page has twelve example buttons. Click them in this order. Each one also has
a direct link (`http://127.0.0.1:8000/demo#1` to `#12`), which is handy if you
prepare browser tabs in advance.

| # | Click | Result | What to say |
|---|---|---|---|
| 1 | **ALLOW**: steel valves | allow, risk 0.001 | "Normal business content is published immediately. Inference takes about 10 to 15 milliseconds." |
| 2 | **REVISE**: PVC pipes + "Earn 50000 per week" | revise; only the "Earn 50000 per week from home…" sentence is highlighted | "Nothing waits in a moderator queue. Like LinkedIn's check before posting, the author sees exactly which sentence is the problem and why. They edit it and post again. Click **Edit post** and the text box selects that sentence." |
| 3 | **REVISE**: crypto payment | revise by the **gate**; "payment in crypto" is highlighted, although the model alone says safe (0.000) | "This is why there are two layers. The model missed the off-platform payment. A rule catches it and tells the seller exactly what to remove." |
| 4 | **REJECT**: bank details and OTP | reject, risk 0.999 | "Phishing is caught by the model." |
| 5 | **REJECT**: threat | reject, risk 1.000 | "Abuse and threats are caught too." |
| 6 | **REJECT**: double your money | reject by the **gate**; the model step is struck out | "Obvious scams never reach the model. The rule check costs about 0.05 ms, so it's a free first filter." |
| 7 | **REJECT**: scumbag | reject by the gate | "An abusive-language blocklist. In production it also loads the policy team's full list from a private file." |
| 8 | **REJECT**: phishing domain | reject by the gate | "Known scam domains are blocked outright. Adding one is a one-line config change, with no retraining needed." |
| 9 | **REJECT**: insult aimed at a business | reject by the gate (`abuse.targeted`) | "Comments about other businesses can't be used for abuse. When a sentence mentions someone (he, she, they, their…) and then insults them, it's rejected instantly." |
| 10 | **REJECT**: threat hidden in a good review | reject by the **sentence check**; the whole text scores only 0.20 | "The whole comment looks positive, so the model alone would publish it. We also score each sentence that talks about someone. This one is a threat, so the whole comment is rejected." |
| 11 | **REVISE**: LED lights + "Work from home and earn big" | revise; the work-from-home pitch is highlighted | "A normal listing with one off-topic money pitch: the seller keeps the listing and removes one line." |
| 12 | **REJECT**: solar panels + "register now with a small fee" | reject by the **sentence scan**; the whole listing scores 0.000 | "A scammer can hide one bad sentence inside a normal listing, and the whole text looks clean. We score every sentence on its own, and one reject-level sentence rejects the post." |
| 13 | Paste sentences from section 4 | — | Take requests from the audience, using the sentence bank below. |
| 14 | Expand **Full result payload** | — | "This is exactly what goes into the audit table: score, decision, model and rule versions, and thresholds. Every decision can be traced later." |

Finish on the **This session** table, which shows the count of each decision and the mean inference time.

## 4. Sentence bank

Copy a sentence into the **Content** box, pick the content type, and click **Moderate** (or press `Ctrl + Enter`).

### 4.1 Allowed: normal business listings

| Sentence | Content type | Result |
|---|---|---|
| Cotton bedsheets in king and queen sizes, 300 thread count. Bulk orders welcome. | Product listing | allow · 0.000 |
| High quality PVC pipes, ISI marked, available in all sizes. Bulk orders welcome. | Product listing | allow · 0.000 |
| Solar panels 330W mono PERC, 25 year warranty. Installation support available. | Product listing | allow · 0.000 |
| Ceramic floor tiles, 600x600 mm, glossy and matt finish. Samples available on request. | Product listing | allow · 0.000 |
| Office chairs with lumbar support, available in mesh and leather. Free delivery in Bangalore. | Product listing | allow · 0.000 |
| Handmade leather wallets and belts, available in brown and black. Bulk orders welcome. | Product listing | allow · 0.000 |
| Stainless steel kitchen sinks, sizes 18 to 36 inches. Warranty provided with every order. | Product listing | allow · 0.000 |
| Get 70% off on all branded goods this week only, hurry before stock runs out! | Advertisement | allow · 0.015 |

**Talking point:** aggressive but legitimate sales language ("70% off", "hurry") is still allowed.

### 4.2 Revise: the model sees some risk in a sentence

The author gets the post back with the sentence highlighted and an instruction
to rephrase it. When a post has several sentences, only the risky one is
highlighted.

| Sentence | Content type | Result |
|---|---|---|
| High quality PVC pipes, ISI marked, available in all sizes. Earn 50000 per week from home, no experience needed. | Product listing | revise · highlights "Earn 50000 per week from home…" |
| LED panel lights, 12W to 48W, BIS certified. Test certificates provided with every order. Work from home and earn big, message us for details. | Product listing | revise · highlights "Work from home and earn big…" |
| Cotton bedsheets in king and queen sizes, 300 thread count. Urgent requirement: pay registration charges to get guaranteed government tender approval. | Product listing | revise · highlights "Urgent requirement: pay registration charges…" |
| Work from home and earn big, message us for details | Advertisement | revise · 0.544 |

### 4.3 Revise: a rule asks the author to remove something

| Sentence | Content type | Model alone | Final |
|---|---|---|---|
| Premium basmati rice exporter, 20 years in business, FSSAI certified. Bulk orders welcome. Payment accepted via bitcoin. | Product listing | safe · 0.000 | **revise** by gate (`payment.off_platform`); highlights "Payment accepted via bitcoin" |
| Cotton bedsheets in king and queen sizes, 300 thread count. We also accept payment in crypto. | Product listing | safe · 0.000 | **revise** by gate (`payment.off_platform`); highlights "payment in crypto" |

**Talking point:** "The model on its own would publish this. The rule catches a
payment channel that's risky for buyers and tells the seller exactly what to
remove, so they can fix it in seconds."

For contrast, paste the basmati sentence **without** the last sentence ("Payment accepted via bitcoin."). It comes back **allow**.

### 4.4 Rejected by the model

| Sentence | Content type | Result |
|---|---|---|
| Send your bank details and OTP to claim your prize money today | Post | reject · 0.999 |
| You people are idiots and I will destroy your business | Post | reject · 1.000 |
| Earn 50000 per week from home!!! No experience needed, limited slots, DM now | Advertisement | reject · 0.999 |
| his mindset is toxic and sick | Post | reject · 0.997 |

### 4.5 Rejected by the gate: model not called

| Sentence | Content type | Rule |
|---|---|---|
| Invest now and double your money in 7 days | Advertisement | `scam.advance_fee` |
| Guaranteed returns of 40% every month on your investment | Advertisement | `scam.advance_fee` |
| Pay a small processing fee to release your shipment | Post | `scam.advance_fee` |
| Our supplier is a scumbag, never order from them | Post | `abuse.blocklist` |
| This vendor is a moron, go to hell | Post | `abuse.blocklist` |
| Verify your seller account at paypa1-verify.example today | Post | `spam.blacklisted_domain` |
| Claim your reward at claim-free-gift.test before midnight | Advertisement | `spam.blacklisted_domain` |
| Order now at fast-cash-bonanza.example/offer and get 90% off | Advertisement | `spam.blacklisted_domain` |

**Talking point:** "Inference shows *skipped*, and the whole decision took well under a millisecond."

### 4.6 Tricks the gate sees through

Before matching, the gate normalises text: it ignores case, collapses extra spaces, converts look-alike Unicode characters, and strips invisible characters.

| Sentence (copy exactly) | Trick | Result |
|---|---|---|
| Our supplier is a SCUMBAG, never order from them | Capital letters | reject by gate |
| Our supplier is a ｓｃｕｍｂａｇ, never order from them | Full-width look-alike letters | reject by gate |
| Invest now and DOUBLE     YOUR     MONEY in 7 days | Capitals and extra spaces | reject by gate |

### 4.7 The gate doesn't over-block

These sentences contain parts of blocked words or phrases, but not the phrases themselves. They pass the gate, and the model allows them.

| Sentence | Looks like | Result |
|---|---|---|
| Hellfire hot sauce, 250 ml bottles, made with ghost peppers. Bulk orders welcome. | "go to hell" | allow · 0.001 |
| Premium basmati rice exporter, 20 years in business. Our go-to rice for biryani. Bulk orders welcome. | "go to hell" | allow · 0.000 |
| Industrial stainless steel valves, sizes 15mm to 300mm. Double-walled models available. Test certificates provided with every order. | "double your money" | allow · 0.000 |

**Talking point:** "Rules match whole words and phrases, so honest listings aren't caught by accident."

### 4.8 Abuse aimed at a person or business

Two checks work together here. The **gate rule** `abuse.targeted` rejects a
sentence where a subject word (he, she, his, her, him, they, them, their) is
followed later in the same sentence by an insult or profanity. The **sentence
check** has the model score each sentence that mentions someone, from the
subject word to the end of the sentence, and rejects the whole post if that
part scores at reject level.

| Sentence | Content type | Result |
|---|---|---|
| Their owner is a pathetic clown, avoid this shop | Post | reject by gate (`abuse.targeted`) |
| They are idiots, never buy from them | Post | reject by gate (`abuse.targeted`) |
| He sucks at customer service | Post | reject by gate (`abuse.targeted`) |
| his thinking and mindset shameless | Post | reject by gate (`abuse.targeted`) |
| he is a liar and a fraudster | Post | reject by gate (`abuse.targeted`) |
| Excellent packaging and prompt delivery on every order. They should all be burned alive along with their shop. | Post | reject · targeted (whole text 0.196, sentence 0.993) |
| Solar panels 330W mono PERC, 25 year warranty. Beware of other sellers, they are all frauds. | Post | reject · targeted |
| Fast shipping and good quality cotton, highly recommended for bulk buyers. He is a thief who steals from customers and belongs in jail. | Post | reject · targeted (whole text 0.260, sentence 0.992) |

**Talking point:** "Scoring the whole comment only gives 0.20, so the model alone
would publish a death threat: the friendly first sentence dilutes it. Scoring
the sentence that mentions someone gives 0.993, so it's rejected."

### 4.9a A harmful sentence hidden in a clean listing (sentence scan)

Every sentence is also scored on its own. If any one sentence scores at reject
level, the whole post is rejected, even when the full text looks clean.

| Sentence | Content type | Result |
|---|---|---|
| Solar panels 330W mono PERC, 25 year warranty. Limited slots for dealers, register now with a small fee. | Product listing | reject · sentence (whole text 0.000) |
| Your account will be suspended. Share your password and card number to verify immediately. | Post | reject · sentence (whole text 0.267) |
| Congratulations! You have won a lottery of 10 lakh rupees. Pay the processing charges to receive it. | Post | reject · sentence (whole text 0.308) |

**Talking point:** "The whole solar listing scores 0.000, so on its own the model
would publish it. The 'register now with a small fee' sentence alone scores
0.98, so the post is rejected. A sentence that's only somewhat risky, like
'Earn 50000 per week from home' (0.64), gets a revise prompt with that sentence
highlighted instead."

### 4.9 Mentioning people is fine

Subject words on their own don't trigger anything. Only insults or reject-level
sentences aimed at someone do.

| Sentence | Content type | Result |
|---|---|---|
| Solar panels 330W mono PERC. They come with a 25 year warranty. | Product listing | allow · 0.000 |
| Office chairs with lumbar support. They are available in mesh and leather. | Product listing | allow · 0.000 |
| Industrial vacuum cleaner, 2000W motor sucks up dust and water. Warranty provided with every order. | Product listing | allow · 0.000 |
| LED panel lights with an idiot-proof click-fit design. BIS certified. Test certificates provided with every order. | Product listing | allow · 0.000 |

**Talking point:** "'They come with a 25 year warranty' is allowed. 'Sucks' in a vacuum
cleaner ad isn't aimed at anyone, and 'idiot-proof' is one word, so neither
triggers the rules."

## 5. Gate rule reference

The rules live in `flask_api/config/gate_patterns.yaml`.

| Rule | Action | Triggers on |
|---|---|---|
| `abuse.blocklist` | block | `moron`, `scumbag`, `go to hell`, plus the private policy list when present |
| `abuse.targeted` | block | A subject word (he, she, his, her, him, they, them, their) followed **later in the same sentence** by a curated insult (idiot, pathetic, clown, loser…) or character attack (shameless, disgraceful, liar, crook, fraudster…) or a word from the downloaded profanity lists |
| `profanity.wordlist` | revise | Profanity from the downloaded lists that isn't aimed at anyone. The word is highlighted and the author is asked to remove it |
| `spam.blacklisted_domain` | block | `fast-cash-bonanza.example`, `claim-free-gift.test`, `paypa1-verify.example` |
| `scam.advance_fee` | block | "guaranteed returns/profits of N%", "double/triple your money/investment", "pay a (small) processing/release/clearance fee" |
| `spam.url_shortener` | flag | Short links: `bit.ly/…`, `tinyurl.com/…`, `t.co/…`, `goo.gl/…` and similar. Recorded only, because they're common in legitimate ads |
| `payment.off_platform` | revise | "pay/payment/send" within about 40 characters of "gift card", "crypto", "bitcoin", "USDT", "Western Union", "MoneyGram". The phrase is highlighted and the author is asked to remove it |
| `contact.messenger_redirect` | flag | "WhatsApp/Telegram/Signal" followed by a phone number. Recorded only, because it's common in legitimate Indian B2B listings |

The demo domains use reserved endings (`.example`, `.test`) on purpose, so no real website is named.

The downloaded profanity lists (about 900 words after exclusions) come from
[LDNOOBW](https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words) (CC BY 4.0)
and [google-profanity-words](https://github.com/coffee-and-fun/google-profanity-words) (MIT).
`config/wordlist_exclusions.txt` removes entries with innocent B2B meanings, such as *flange*,
*nipple* (pipe fittings), *hoe* (garden tools) and *cum* ("office-cum-warehouse").

---

## 6. Troubleshooting on the page

For terminal and setup problems, see the troubleshooting section of your terminal guide
([PowerShell](DEMO_GUIDE_POWERSHELL.md#6-troubleshooting-powershell) or
[Command Prompt](DEMO_GUIDE_CMD.md#6-troubleshooting-command-prompt)).

| What you see | Cause and fix |
|---|---|
| **demo page disabled** | `DEMO_PAGE` wasn't set in the server's window. PowerShell and Command Prompt set it differently, so check your terminal guide's section 3. |
| `service unreachable` in the header | The server window was closed or crashed. Start it again. |
| `status not_ready` in the header | The model didn't load. Usually the model file wasn't downloaded: run `git lfs pull`, then restart the server. |
| `gate rules 7` instead of 8 | The profanity word lists haven't been downloaded. Run `.venv\Scripts\python.exe scripts\fetch_wordlists.py`, then restart the server. |
| `gate rules` is 6 or lower | You're running an older copy of the code. Run `git pull`, then restart the server. |
| A sentence gives a different result | Check that it's pasted exactly. The v1 model is sensitive to small wording changes. |

---

## 6b. Articles page: moderation inside the real publishing flow

Open **http://127.0.0.1:8000/articles** (same server, same `DEMO_PAGE=1`). It
reproduces the Business Dashboard's Articles tab and "Write article" modal,
with moderation wired into **Publish**. Click **+ Write Article** and try these
(each flow is covered by an automated browser test):

| Title | Body | What happens |
|---|---|---|
| Cotton bedsheets for wholesale buyers | Cotton bedsheets in king and queen sizes, 300 thread count. Bulk orders welcome. | **Published**; "1 of 2 free article publishes left" |
| PVC pipes for contractors | High quality PVC pipes, ISI marked, available in all sizes. Earn 50000 per week from home, no experience needed. | **Needs changes**: "Your article needs a few changes", with the "Earn 50000…" sentence highlighted in the editor. Click **Show** to select it. |
| Investment opportunity | Invest now and double your money in 7 days. | **Rejected**: "…community policies on fraud and scams" |
| Our supplier portal | Log in to our supplier portal to see prices. Select "our supplier portal" and use the 🔗 button to link it to `https://paypa1-verify.example/login`. | **Rejected**: the hidden link URL is checked too |

Then show the **Needs changes** and **Rejected** tabs, which keep those articles
with their reasons. Also show that **Save as Draft** publishes nothing and
isn't checked.

**Talking point:** "Nothing waits in a moderator queue. Honest authors fix one
highlighted sentence and publish in seconds; scams are stopped at the Publish
button."

## 7. If you're asked about limitations

- **There's no human-review queue.** Middle-band content goes back to its author with the problem highlighted. Rejections name the policy area, such as "fraud and scams", but deliberately don't highlight the trigger words, so people posting scams can't learn how to reword around the filters. That's one setting (`highlight_on_reject`) in `config/feedback_messages.yaml`.
- **The live model is v3** (`models/v3`; v1 is in `models/v1`, restored with `dir: models/v1`). v3's "review" class fires on almost any prose that isn't a product listing, so its weight in the risk score is calibrated down to 0.25 (`label_weights` in `settings.yaml`). With that, all 36 legitimate test posts are allowed, and 12 of 18 harmful ones are caught. The model gives some harmful posts exactly the same output as ordinary text, so nothing can catch them without also flagging legitimate posts. Examples: "Looking for distributors… just register with a small fee", "She will regret the day she crossed me", "his thinking and mindset disturbing", and counterfeit "replica watches". These are with the ML team for retraining.
- **Multi-sentence posts cost more.** The sentence scan scores every sentence on its own (up to 8), so a 3–4 sentence post takes about 30–45 ms of model time instead of about 11 ms. The INT8 model can't batch sentences without changing their scores, so they're scored one at a time.
- **The thresholds (0.30 / 0.70) are placeholders.** They'll be tuned once the model is recalibrated. They're configuration values, so changing them needs no code change.
- **The word and domain lists hold demo entries.** The production slur list comes from the policy team through a private file that isn't stored in git.
- **Some negative words are deliberately not in the insult list**, because they have innocent business uses: *disturbing* ("He found the test results disturbing"), *toxic* ("toxic chemicals"), *cheat* ("cheat sheet"), *corrupt* ("a corrupt file"). "his thinking and mindset disturbing" is therefore not caught by the rules, and the v3 model scores it like ordinary text, so it's allowed. Full sentences such as "he is disturbing and toxic" are still rejected by the model.
- **The sentence check only looks at he, she, his, her, him, they, them and their.** "You are an idiot" isn't covered by it (the whole-text model score still applies). "You" was left out because it appears in almost every legitimate ad ("we deliver to you"). Some threats get only a revise with the v1 model ("I hope she dies…"). This is being passed to the ML team for the retrained model.
- **The demo runs in sync mode**, with one request and one answer. An async mode (Celery with Redis) is built in for heavy load. It returns `pending` and processes the content in the background.
