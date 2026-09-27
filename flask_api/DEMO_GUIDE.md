# Demo guide: BONC Content Moderation

This guide covers how to run a live demonstration of the moderation service:
what to click, what to say, and a bank of tested sentences to paste.

Every sentence here was checked against the real model
(`deberta-v3-small-int8-bonc-v1`), so the decision listed next to it is what
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
             (instant rules)    (AI risk score)       (allow / reject)
```

| Step | What it does | Typical time |
|---|---|---|
| 1. Regex gate | Checks fixed rules: scam wording, abusive words, blocked domains. A **block** rejects immediately and skips the model. A **flag** is recorded for the audit log without changing the decision. | ~0.1–0.5 ms |
| 2. Model | A fine-tuned DeBERTa-v3-small, INT8-quantized, gives a risk score from 0 to 1. | ~10–25 ms |
| 3. Routing | Risk ≥ 0.50 is **rejected**; anything lower is **allowed**. There's no human-review step: every item is decided automatically. | — |
| 4. Audit log | Every decision is stored with its score, the model and rule versions, and the thresholds used. | — |

---

## 2. Check the page and warm up

Open **http://127.0.0.1:8000/demo**. The chips at the top of the page should read:

`status ready` · `mode sync` · `model deberta-v3-small-int8-bonc-v1` · `gate rules 8` · `allow < 0.50 ≤ reject (risk)`

If they don't, see section 6.

**Warm up before the audience arrives.** Click each example button once, then
refresh the page (`F5`) to clear the session table.

---

## 3. Run of show (about 10 minutes)

The page has ten example buttons. Click them in this order. Each one also has
a direct link (`http://127.0.0.1:8000/demo#1` to `#10`), which is handy if you
prepare browser tabs in advance.

| # | Click | Result | What to say |
|---|---|---|---|
| 1 | **ALLOW**: steel valves | allow, risk 0.000 | "Normal business content is published immediately. Inference takes about 10 to 15 milliseconds." |
| 2 | **REJECT**: distributors | reject, risk 0.50 | "There's no manual review step. When the model is unsure, it sits right at 0.50, and unsure content is rejected, so nothing risky is published while waiting for a person." |
| 3 | **REJECT**: crypto payment | reject by the **gate**, although the model alone would say safe (0.005) | "This is why there are two layers. The model missed the off-platform payment. A rule catches it and rejects it instantly." |
| 4 | **REJECT**: bank details and OTP | reject, risk 0.999 | "Phishing is caught by the model." |
| 5 | **REJECT**: threat | reject, risk 1.000 | "Abuse and threats are caught too." |
| 6 | **REJECT**: double your money | reject by the **gate**; the model step is struck out | "Obvious scams never reach the model. The rule check costs about 0.05 ms, so it's a free first filter." |
| 7 | **REJECT**: scumbag | reject by the gate | "An abusive-language blocklist. In production it also loads the policy team's full list from a private file." |
| 8 | **REJECT**: phishing domain | reject by the gate | "Known scam domains are blocked outright. Adding one is a one-line config change, with no retraining needed." |
| 9 | **REJECT**: insult aimed at a business | reject by the gate (`abuse.targeted`) | "Comments about other businesses can't be used for abuse. When a sentence mentions someone (he, she, they, their…) and then insults them, it's rejected instantly." |
| 10 | **REJECT**: threat hidden in a good review | reject by the **sentence check**; the whole text scores almost 0 | "The whole comment looks positive, so the model alone would publish it. We also score each sentence that talks about someone. This one is a threat, so the whole comment is rejected." |
| 11 | Paste sentences from section 4 | — | Take requests from the audience, using the sentence bank below. |
| 12 | Expand **Full result payload** | — | "This is exactly what goes into the audit table: score, decision, model and rule versions, and thresholds. Every decision can be traced later." |

Finish on the **This session** table, which shows the count of each decision and the mean inference time.

## 4. Sentence bank

Copy a sentence into the **Content** box, pick the content type, and click **Moderate** (or press `Ctrl + Enter`).

### 4.1 Allowed: normal business listings

| Sentence | Content type | Result |
|---|---|---|
| Cotton bedsheets in king and queen sizes, 300 thread count. Bulk orders welcome. | Product listing | allow · 0.000 |
| High quality PVC pipes, ISI marked, available in all sizes. Bulk orders welcome. | Product listing | allow · 0.000 |
| LED panel lights, 12W to 48W, BIS certified. Test certificates provided with every order. | Product listing | allow · 0.000 |
| Solar panels 330W mono PERC, 25 year warranty. Installation support available. | Product listing | allow · 0.000 |
| Ceramic floor tiles, 600x600 mm, glossy and matt finish. Samples available on request. | Product listing | allow · 0.000 |
| Fresh alphonso mangoes from Ratnagiri, packed in 5 kg boxes. Bulk orders welcome. | Product listing | allow · 0.002 |
| Get 70% off on all branded goods this week only, hurry before stock runs out! | Advertisement | allow · 0.044 |

**Talking point:** aggressive but legitimate sales language ("70% off", "hurry") is still allowed.

### 4.2 Rejected: the model is unsure

These score just above 0.50. With no human-review step, "unsure" counts as reject.

| Sentence | Content type | Result |
|---|---|---|
| Looking for distributors in Gujarat. Very high margins, no investment needed, just register with a small fee. | Post | reject · 0.501 |
| Work from home and earn big, message us for details | Advertisement | reject · 0.562 |
| Our competitor sells fake products, do not trust them | Post | reject · 0.505 |

### 4.3 Rejected by a rule, although the model says safe

| Sentence | Content type | Model alone | Final |
|---|---|---|---|
| Premium basmati rice exporter, 20 years in business, FSSAI certified. Bulk orders welcome. Payment accepted via bitcoin. | Product listing | safe · 0.000 | **reject** by gate (`payment.off_platform`) |
| Industrial stainless steel valves, sizes 15mm to 300mm. Test certificates provided with every order. We also accept payment in crypto. | Product listing | safe · 0.005 | **reject** by gate (`payment.off_platform`) |

**Talking point:** "The model on its own would publish this. The rule catches a
payment channel that's risky for buyers and rejects it."

For contrast, paste the basmati sentence **without** the last sentence ("Payment accepted via bitcoin."). It comes back **allow**.

### 4.4 Rejected by the model

| Sentence | Content type | Result |
|---|---|---|
| Send your bank details and OTP to claim your prize money today | Post | reject · 0.999 |
| You people are idiots and I will destroy your business | Post | reject · 1.000 |
| Earn 50000 per week from home!!! No experience needed, limited slots, DM now | Advertisement | reject · 0.998 |
| Congratulations! You have won a lottery of 10 lakh rupees. Pay the processing charges to receive it. | Post | reject · 0.894 |

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
| Hellfire hot sauce, 250 ml bottles, made with ghost peppers. Bulk orders welcome. | "go to hell" | allow · 0.004 |
| Premium basmati rice exporter, 20 years in business. Our go-to rice for biryani. Bulk orders welcome. | "go to hell" | allow · 0.001 |
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
| Excellent packaging and prompt delivery on every order. They should all be burned alive along with their shop. | Post | reject · targeted (whole text 0.001, sentence 0.984) |
| Fast shipping and good quality cotton, highly recommended for bulk buyers. He is a thief who steals from customers and belongs in jail. | Post | reject · targeted (whole text 0.027, sentence 0.992) |

**Talking point:** "Scoring the whole comment would have allowed the threat,
because the friendly first sentence dilutes it. Scoring the sentence that
mentions someone catches it."

### 4.9 Mentioning people is fine

Subject words on their own don't trigger anything. Only insults or reject-level
sentences aimed at someone do.

| Sentence | Content type | Result |
|---|---|---|
| Great supplier. They delivered 500 units on time and the quality was excellent. | Post | allow · 0.000 |
| Industrial vacuum cleaner, 2000W motor sucks up dust and water. Warranty provided with every order. | Product listing | allow · 0.000 |
| LED panel lights with an idiot-proof click-fit design. BIS certified. Test certificates provided with every order. | Product listing | allow · 0.000 |

**Talking point:** "'They delivered on time' is allowed. 'Sucks' in a vacuum
cleaner ad isn't aimed at anyone, and 'idiot-proof' is one word, so neither
triggers the rules."

## 5. Gate rule reference

The rules live in `flask_api/config/gate_patterns.yaml`.

| Rule | Action | Triggers on |
|---|---|---|
| `abuse.blocklist` | block | `moron`, `scumbag`, `go to hell`, plus the private policy list when present |
| `abuse.targeted` | block | A subject word (he, she, his, her, him, they, them, their) followed **later in the same sentence** by a curated insult (idiot, pathetic, clown, loser…) or a word from the downloaded profanity lists |
| `profanity.wordlist` | block | Profanity from the downloaded lists, even when it isn't aimed at anyone |
| `spam.blacklisted_domain` | block | `fast-cash-bonanza.example`, `claim-free-gift.test`, `paypa1-verify.example` |
| `scam.advance_fee` | block | "guaranteed returns/profits of N%", "double/triple your money/investment", "pay a (small) processing/release/clearance fee" |
| `spam.url_shortener` | flag | Short links: `bit.ly/…`, `tinyurl.com/…`, `t.co/…`, `goo.gl/…` and similar. Recorded only, because they're common in legitimate ads |
| `payment.off_platform` | block | "pay/payment/send" within about 40 characters of "gift card", "crypto", "bitcoin", "USDT", "Western Union", "MoneyGram" |
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

## 7. If you're asked about limitations

- **There's no human-review step, so the v1 model's "unsure" score matters.** It gives many ordinary listings and many harmful posts almost the same score, about 0.50. With the threshold at 0.50, unsure content is rejected. That catches scams and threats, but it also rejects some legitimate text (for example "Family-run textile mill in Surat…"). A few harmful posts still score below 0.50 and are allowed, for example "Limited stock! Contact us on WhatsApp… full payment in advance only" (0.452) and "replica watches" (0.002). The ML team is retraining the model to separate these cases.
- **The threshold (0.50) is a setting, not code.** It will be re-tuned for the retrained model. Changing `reject_min` in `settings.yaml` needs no code change.
- **The word and domain lists hold demo entries.** The production slur list comes from the policy team through a private file that isn't stored in git.
- **The sentence check only looks at he, she, his, her, him, they, them and their.** "You are an idiot" isn't covered by it (the whole-text model score still applies). "You" was left out because it appears in almost every legitimate ad ("we deliver to you").
- **The demo runs in sync mode**, with one request and one answer. An async mode (Celery with Redis) is built in for heavy load. It returns `pending` and processes the content in the background.
