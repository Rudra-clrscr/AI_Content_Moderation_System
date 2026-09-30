# Demo guide: BONC Content Moderation

This guide covers how to run a live demonstration of the moderation service:
what to click, what to say, and a bank of tested sentences to paste.

Every sentence here was checked against the real model
(`deberta-v3-small-bonc-v4`) with `scripts/verify_demo_sentences.py`, so the decision
listed next to it is what you will see. **Paste the sentences exactly as written.**
Risk scores can move a little when a few words are added or removed.

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
| 2. Model | bonc-v4: a fine-tuned DeBERTa-v3-small gives a calibrated risk from 0 to 1, the probability that the text is unsafe. | ~10–20 ms |
| 1.5. Triage pre-filter | A tiny linear model clears obviously-safe sentences in ~0.1 ms each, so DeBERTa is only called for the rest. Removes about 73% of the model calls on a normal post, with no decision change. | ~0.1 ms per sentence |
| 2c. Sentence and word scan | Every remaining sentence is scored on its own (in one batched call); long run-on sentences also in 40-word windows. For anything flagged, the **trigger words** are found word by word. Leetspeak is read as plain letters ("F1rst c0py" → "First copy"). | ~25–100 ms per article |
| 3. Routing | Risk **≤ 0.5 is allowed**. Risk **> 0.5 is rejected**: the author sees the sentences to rewrite, with the trigger words in bold, and resubmits. A gate **revise** rule still sends content back for a small fix. There's no human-review queue. | — |
| 4. Audit log | Every decision is stored with its score, the model and rule versions, and the thresholds used. | — |

---

## 2. Check the page and warm up

Open **http://127.0.0.1:8000/demo**. The chips at the top of the page should read:

`status ready` · `mode sync` · `model deberta-v3-small-bonc-v4` · `gate rules 8` · `allow ≤ 0.50 · reject > 0.50 (risk)`

If they don't, see section 6.

**Warm up before the audience arrives.** Click each example button once, then
refresh the page (`F5`) to clear the session table.

---

## 3. Run of show (about 10 minutes)

The page has thirteen example buttons. Click them in this order. Each one also has
a direct link (`http://127.0.0.1:8000/demo#1` to `#13`), which is handy if you
prepare browser tabs in advance.

| # | Click | Result | What to say |
|---|---|---|---|
| 1 | **ALLOW**: steel valves | allow, risk 0.000 | "Normal business content is published immediately. Inference takes about 10 to 20 milliseconds." |
| 2 | **ALLOW**: a rainy day at the shop (article) | allow | "Articles don't have to sound like business. The old model flagged almost any prose that wasn't a product listing; v4 was retrained so everyday writing, even with simple or broken grammar, is allowed." |
| 3 | **REJECT**: PVC pipes + "Earn 50000 per week" | reject; only the "Earn 50000 per week from home…" sentence is highlighted, with the trigger words in bold | "Anything over 0.5 is rejected, and nothing waits in a moderator queue. The author sees exactly which sentence and which words are the problem, rewrites them, and publishes again. Click **Rewrite post** and the text box selects that sentence." |
| 4 | **REVISE**: crypto payment | revise by the **gate**; "payment in crypto" is highlighted, although the model alone says safe (0.000) | "This is why there are two layers. A rule catches the off-platform payment and tells the seller exactly what to remove." |
| 5 | **REJECT**: bank details and OTP | reject, risk 0.999 | "Phishing is caught by the model." |
| 6 | **REJECT**: threat | reject, risk 0.998 | "Abuse and threats are caught too." |
| 7 | **REJECT**: double your money | reject by the **gate**; the model step is struck out | "Obvious scams never reach the model. The rule check costs about 0.05 ms, so it's a free first filter." |
| 8 | **REJECT**: scumbag | reject by the gate | "An abusive-language blocklist. In production it also loads the policy team's full list from a private file." |
| 9 | **REJECT**: phishing domain | reject by the gate | "Known scam domains are blocked outright. Adding one is a one-line config change, with no retraining needed." |
| 10 | **REJECT**: "What a pathetic clown their owner is" | reject by the gate (`abuse.targeted`) | "Nobody may point abuse at an individual. When a sentence has he, she, his, her, they, them… **and** an insult, in either order, it's rejected instantly." |
| 11 | **REJECT**: threat inside a good review | reject; the threat sentence is highlighted | "The first sentence is friendly, but it doesn't dilute the threat: the whole comment scores 0.999, and the author is shown the sentence to remove." |
| 12 | **REJECT**: "F1rst c0py R0lex w4tches" | reject; "F1rst" and "c0py" in bold | "Leetspeak doesn't get through. Each word is also read as plain letters, 'F1rst c0py' as 'First copy', and the riskier reading counts." |
| 13 | **REJECT**: scam sentence inside a long article | reject; only the scam sentence is highlighted, with "join", "paying", "earn", "50000" in bold | "Every sentence is scored, and the word-by-word scan shows the author exactly what to rewrite. The rest of the article is fine." |
| 14 | Paste sentences from section 4 | — | Take requests from the audience, using the sentence bank below. |
| 15 | Expand **Full result payload** | — | "This is exactly what goes into the audit table: score, decision, every sentence's score, the word scores, the model and rule versions, and the thresholds. Every decision can be traced later." |

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

### 4.2 Rejected, rewrite one sentence: a money pitch in a normal listing

Risk above 0.5 is rejected, and the author is shown what to rewrite. When a post
has several sentences, only the risky one is highlighted, with its trigger words
in bold.

| Sentence | Content type | Result |
|---|---|---|
| High quality PVC pipes, ISI marked, available in all sizes. Earn 50000 per week from home, no experience needed. | Product listing | reject · highlights "Earn 50000 per week from home…" |
| LED panel lights, 12W to 48W, BIS certified. Test certificates provided with every order. Work from home and earn big, message us for details. | Product listing | reject · highlights "Work from home and earn big…" |
| Cotton bedsheets in king and queen sizes, 300 thread count. Urgent requirement: pay registration charges to get guaranteed government tender approval. | Product listing | reject · highlights "Urgent requirement: pay registration charges…" |
| Work from home and earn big, message us for details | Advertisement | reject · 0.999 |

**Talking point:** "With v3 these were 'revise'. Now there is one line at 0.5: the
listing isn't published, but the seller only has to rewrite the highlighted
sentence."

### 4.2b Everyday writing is allowed

| Sentence | Content type | Result |
|---|---|---|
| My son broke his arm playing football, so I was away from the shop last week. | Article | allow |
| yesterday me and my friend go to the market and buy vegetables | Article | allow |
| Payment terms: 30% advance with the order, balance before dispatch by bank transfer to our company account. | Product listing | allow |
| We never sell copies or replicas; every product is genuine and invoiced. | Product listing | allow |

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

Policy (senior review): nobody may point abuse at an individual. The **gate rule**
`abuse.targeted` rejects any sentence that has a pointing word (he, she, his, her,
him, they, them, their…) **and** an insult or profanity, in either order. The
model also scores the whole text, every sentence, and each part of a sentence that
mentions someone.

| Sentence | Content type | Result |
|---|---|---|
| Their owner is a pathetic clown, avoid this shop | Post | reject by gate (`abuse.targeted`) |
| What a pathetic clown their owner is, avoid this shop | Post | reject by gate (`abuse.targeted`) |
| They are idiots, never buy from them | Post | reject by gate (`abuse.targeted`) |
| He sucks at customer service | Post | reject by gate (`abuse.targeted`) |
| his thinking and mindset shameless | Post | reject by gate (`abuse.targeted`) |
| he is a liar and a fraudster | Post | reject by gate (`abuse.targeted`) |
| Excellent packaging and prompt delivery on every order. They should all be burned alive along with their shop. | Post | reject · 0.999 |
| Solar panels 330W mono PERC, 25 year warranty. Beware of other sellers, they are all frauds. | Post | reject · 0.999 |
| Fast shipping and good quality cotton, highly recommended for bulk buyers. He is a thief who steals from customers and belongs in jail. | Post | reject · 0.999 |

**Talking point:** "With v3 the friendly first sentence diluted the threat: the whole
comment scored 0.20, and only the sentence check caught it. v4 isn't fooled by the
padding (0.999), and the sentence checks are still there as a safety net."

### 4.9a A harmful sentence hidden in a clean listing (sentence scan)

Every sentence is also scored on its own, and any one over 0.5 rejects the whole
post, even when the full text looks clean. v3 needed this for short listings (the
solar listing below scored 0.000 as a whole). v4 already scores these texts 0.999
as a whole, and the scan now shows the author which sentence and which words to
rewrite. It remains the safety net for long articles.

| Sentence | Content type | Result |
|---|---|---|
| Solar panels 330W mono PERC, 25 year warranty. Limited slots for dealers, register now with a small fee. | Product listing | reject · highlights "Limited slots for dealers, register now…" |
| Your account will be suspended. Share your password and card number to verify immediately. | Post | reject · 0.999 |
| Congratulations! You have won a lottery of 10 lakh rupees. Pay the processing charges to receive it. | Post | reject · 0.999 |

**Talking point:** "Only the scam sentence is highlighted, and the trigger words in
it are in bold. The seller keeps the listing and rewrites one line."

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

### 4.10 PDF uploads

Click **Upload a PDF…** on the demo page (or `POST /v1/moderate/pdf`). The text is extracted
and checked by the same pipeline; the box then shows what was actually read, and each issue is
tagged with the page it came from.

- A normal 2-page catalogue (products, "30% advance, balance before dispatch") → **allowed**
- The same catalogue with "Earn 50000 per week… pay a small registration fee" on page 2 → **rejected**, with both sentences tagged **page 2**
- A **scanned** flyer (an image, no text in the file at all) advertising "Earn 50000 per week… pay a small registration fee" → **rejected**, read by OCR
- A scanned legitimate catalogue → **allowed**, read by OCR
- A blank or illegible scan → **not accepted**: "no text could be read"
- Anything that isn't a PDF → **not accepted**

**Talking point, and the one to make:** "A scanned page has no text in it, so there's nothing
to check. If we let that through, uploading a *screenshot* of a scam would defeat the whole
system. So we OCR the page — and if OCR can't read it either, we refuse it rather than approve
it. Blurring the image until the OCR fails doesn't get you published; it gets you turned away."

### 4.11 Article media (images, video, PDFs)

On **http://127.0.0.1:8000/articles**, click **+ Write Article**, fill in a title and body,
then use the **Upload image, video, or PDF** dropzone. Each attachment is checked as you add
it, and a blocked one stops the publish.

| Attach | State shown | Why |
|---|---|---|
| A picture of "Earn 50000 per week… pay a small registration fee" | **blocked**, quoting the phrase | OCR read the scam out of the image |
| An ordinary product photo | **not inspected** | no text in it — allowed, but the picture itself is never classified |
| A clean PDF catalogue | **ok**, "1 page read" | text extracted and checked |
| Any video | **blocked** | nothing in a video can be read by this service |

Press **Publish** with the bad ones attached: the article is *not* published and the banner
names the files to remove. Remove them and it publishes.

**Talking point:** "An article goes out as a whole, so the attachments are moderated with it —
otherwise you write a clean article and put the scam in the picture. Notice the photo says
*not inspected*, not *ok*: we read text, we don't classify pictures. Saying so is the honest
thing; pretending a green tick means the image is safe would be worse than no check at all."

Point at the response: the page shows `"source": "ocr"` with its confidence, so an auditor can
see which pages were read off pixels rather than text. A text PDF answers in ~100 ms; an OCR'd
page costs about half a second to a second and a half. Long PDFs are rejected rather than
truncated, for the same reason as above: a truncated check that answers "allowed" is worse than
no check.

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
| A sentence gives a different result | Check that it's pasted exactly, and that the header shows `model deberta-v3-small-bonc-v4`. Then run `scripts\verify_demo_sentences.py` (server running) to re-check every sentence. |

---

## 6b. Articles page: moderation inside the real publishing flow

Open **http://127.0.0.1:8000/articles** (same server, same `DEMO_PAGE=1`). It
reproduces the Business Dashboard's Articles tab and "Write article" modal,
with moderation wired into **Publish**. Click **+ Write Article** and try these
(each flow is covered by an automated browser test):

| Title | Body | What happens |
|---|---|---|
| Cotton bedsheets for wholesale buyers | Cotton bedsheets in king and queen sizes, 300 thread count. Bulk orders welcome. | **Published**; "1 of 2 free article publishes left" |
| PVC pipes for contractors | High quality PVC pipes, ISI marked, available in all sizes. Earn 50000 per week from home, no experience needed. | **Rejected, rewrite**: "Your article can't be published… Rewrite the highlighted parts", with the "Earn 50000…" sentence highlighted in the editor and "Earn", "50000" marked in red. Click **Show** to select it, rewrite it, and publish again. |
| A rainy day at the shop | It rained all afternoon and the street in front of our shop flooded. We closed early and drank tea with the neighbours. | **Published**: everyday writing is fine |
| Investment opportunity | Invest now and double your money in 7 days. | **Rejected**: "…community policies on fraud and scams", with "double your money" highlighted |
| Our supplier portal | Log in to our supplier portal to see prices. Select "our supplier portal" and use the 🔗 button to link it to `https://paypa1-verify.example/login`. | **Rejected**: the hidden link URL is checked too |

Then show the **Rejected** tab ("1 part to rewrite"): **Open** brings the article
back with the highlights, ready to rewrite. Also show that **Save as Draft** publishes nothing and
isn't checked.

**Talking point:** "Nothing waits in a moderator queue. Honest authors fix one
highlighted sentence and publish in seconds; scams are stopped at the Publish
button."

## 7. If you're asked about limitations

- **There's no human-review queue.** Content over the 0.5 boundary is rejected and goes back to its author with the sentences and trigger words to rewrite. The trade-off: someone posting scams also sees what tripped the filter. Set `highlight_on_reject: false` in `config/feedback_messages.yaml` to show only the policy area.
- **The live model is v4** (`models/v4`, the only bundle tracked in git). To roll back, restore a v3 bundle into `flask_api/models/v3/` and set `dir: models/v3` with thresholds 0.30 / 0.70. On 219 held-out texts, v4 allowed every legitimate one and caught 87 of 92 harmful ones, against v3's 118/127 and 45/92 (McNemar p < 0.0001). Details are in `docs/MODEL_IMPROVEMENT_GUIDE.md`, section 0.
- **What v4 still misses:**
  - some veiled threats ("People who cross us tend to have accidents. Remember that." scores 0.48, just under the line)
  - wildlife trade ("pangolin scales and rhino horn")
  - "his thinking and mindset disturbing" (0.009): *disturbing* is deliberately not in the insult list ("He found the test results disturbing")
  These go to the next training round.
- **Some examples are in-sample.** Several of v3's known failures ("She will regret the day she crossed me", "register with a small fee", "replica watches") were used as templates for v4's synthetic training data, as the improvement guide recommended. v4 now rejects them, but they don't prove generalisation. The held-out sets in `training/` do.
- **Rejected text costs more.** Allowed posts take about 11 ms (one sentence) to about 100 ms (a long article), helped by the triage pre-filter, which keeps roughly three quarters of the sentences away from the model. A rejection also runs the word-by-word trigger search, which the pre-filter doesn't shortcut: about 0.2–0.4 s for an article, up to about 1 s for a long run-on paragraph. Speeding that path up is the next thing worth doing.
- **The word and domain lists hold demo entries.** The production slur list comes from the policy team through a private file that isn't stored in git.
- **The pronoun rule covers he, she, his, her, him, they, them, their (and -self forms), not "you".** Adding "you" blocked fraud-awareness articles ("Scammers create urgency, so take your time"). "You are an idiot" is still rejected by the model (0.994).
- **The demo runs in sync mode**, with one request and one answer. An async mode (Celery with Redis) is built in for heavy load. It returns `pending` and processes the content in the background.
