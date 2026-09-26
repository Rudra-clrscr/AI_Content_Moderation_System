# Demo guide: BONC Content Moderation

This guide covers how to set up and run a live demonstration of the moderation
service on your own computer. It includes the exact sentences to use and what
each one will show.

Every sentence in this guide was checked against the real model
(`deberta-v3-small-int8-bonc-v1`), so the decision listed next to it is what
you will see. **Paste the sentences exactly as written.** The v1 model can
change its answer when a few words are added or removed.

---

## 1. What the demo shows

Each piece of content passes through four steps:

```
 content ──► 1. Regex gate ──► 2. DeBERTa model ──► 3. Threshold routing ──► 4. Audit log
             (instant rules)    (AI risk score)       (allow / review / reject)
```

| Step | What it does | Typical time |
|---|---|---|
| 1. Regex gate | Checks fixed rules: scam wording, abusive words, blocked domains. A **block** rejects immediately and skips the model. A **flag** forces at least a human review. | ~0.05 ms |
| 2. Model | A fine-tuned DeBERTa-v3-small, INT8-quantized, gives a risk score from 0 to 1. | ~10–25 ms |
| 3. Routing | Risk ≤ 0.30 is **allowed**. Risk ≥ 0.70 is **rejected**. Anything in between goes to **human review**. | — |
| 4. Audit log | Every decision is stored with its score, the model and rule versions, and the thresholds used. | — |

---

## 2. One-time setup on a new computer

You need:
- **Python 3.12 or newer**, from python.org. Tick "Add Python to PATH" during install.
- **Git for Windows**, which already includes Git LFS.

> **Which terminal am I in?** If the prompt starts with **`PS`** (for example
> `PS F:\BONC>`), it's **PowerShell**. Windows Terminal and VS Code open this
> by default. If it's just a path (for example `F:\BONC>`), it's **Command
> Prompt**. The two use different commands for settings, so use the matching
> block below. Commands that aren't split by terminal work in both.

The steps below run Python as `.venv\Scripts\python.exe`, so you never need to
"activate" the virtual environment. Activation works differently in each
terminal, and it's the most common reason a setup fails.

```
git lfs install
git clone https://github.com/Rudra-clrscr/AI_Content_Moderation_System.git
cd AI_Content_Moderation_System\flask_api
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Check that the model downloaded completely. `model_int8.onnx` should be about
**172 MB**, not a few hundred bytes:

```
dir models\current
```

If it's tiny, run `git lfs pull` and check again.

---

## 3. Start the server (every time)

Start in the `AI_Content_Moderation_System\flask_api` folder.

**PowerShell** (prompt starts with `PS`):

```powershell
$env:DEMO_PAGE = "1"
$env:RESULT_SINK = "log"
.venv\Scripts\python.exe -m waitress --port=8000 wsgi:app
```

**Command Prompt:**

```cmd
set DEMO_PAGE=1
set RESULT_SINK=log
.venv\Scripts\python.exe -m waitress --port=8000 wsgi:app
```

> Don't mix them up. In PowerShell, `set DEMO_PAGE=1` runs without an error but
> sets nothing, so the page says *demo page disabled*.

Wait for `Serving on http://0.0.0.0:8000`. Leave this window open, because
closing it stops the server. The settings only last while this window is open,
so run all three lines again each time you start.

Open **http://127.0.0.1:8000/demo** in your browser, or run `start http://127.0.0.1:8000/demo`
from a second terminal window.

To stop the server, click the first window and press `Ctrl + C`.

> **Why two settings?** `DEMO_PAGE=1` turns on the demo page, which is off by
> default so it never appears in production. `RESULT_SINK=log` writes decisions
> to the console instead of SQL Server, so the demo doesn't need a database.
> Without it, every request logs a `db_write_failed` error in the server window.

### Check it is working

The chips at the top of the page should read:

`status ready` · `mode sync` · `model deberta-v3-small-int8-bonc-v1` · `gate rules 6` · `allow ≤ 0.30 · reject ≥ 0.70 (risk)`

You can also check from a second terminal window. `curl.exe` works in both PowerShell and Command Prompt:

```
curl.exe http://127.0.0.1:8000/health
curl.exe http://127.0.0.1:8000/ready
```

**Warm up before the audience arrives.** Click each example button once, then
refresh the page (`F5`) to clear the session table.

---

## 4. Run of show (about 10 minutes)

The page has eight example buttons. Click them in this order. Each one also has
a direct link (`http://127.0.0.1:8000/demo#1` to `#8`), which is handy if you
prepare browser tabs in advance.

| # | Click | Result | What to say |
|---|---|---|---|
| 1 | **ALLOW**: steel valves | allow, risk 0.000 | "Normal business content is published immediately. Inference takes about 10 to 15 milliseconds." |
| 2 | **REVIEW**: distributors | review, risk 0.50 | "Borderline content isn't auto-rejected. It goes to a human moderator." |
| 3 | **REVIEW**: crypto payment | review, but the model says safe (0.005) | "This is why there are two layers. The model missed the off-platform payment. A rule flagged it, and a flag always forces human review." |
| 4 | **REJECT**: bank details and OTP | reject, risk 0.999 | "Phishing is caught by the model." |
| 5 | **REJECT**: threat | reject, risk 1.000 | "Abuse and threats are caught too." |
| 6 | **REJECT**: double your money | reject by the **gate**; the model step is struck out | "Obvious scams never reach the model. The rule check costs about 0.05 ms, so it's a free first filter." |
| 7 | **REJECT**: scumbag | reject by the gate | "An abusive-language blocklist. In production it also loads the policy team's full list from a private file." |
| 8 | **REJECT**: phishing domain | reject by the gate | "Known scam domains are blocked outright. Adding one is a one-line config change, with no retraining needed." |
| 9 | Paste sentences from section 5 | — | Take requests from the audience, using the sentence bank below. |
| 10 | Expand **Full result payload** | — | "This is exactly what goes into the audit table: score, decision, model and rule versions, and thresholds. Every decision can be traced later." |

Finish on the **This session** table, which shows the count of each decision and the mean inference time.

---

## 5. Sentence bank

Copy a sentence into the **Content** box, pick the content type, and click **Moderate** (or press `Ctrl + Enter`).

### 5.1 Allowed: normal business listings

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

### 5.2 Review: the model is unsure

| Sentence | Content type | Result |
|---|---|---|
| Looking for distributors in Gujarat. Very high margins, no investment needed, just register with a small fee. | Post | review · 0.501 |
| Work from home and earn big, message us for details | Advertisement | review · 0.562 |
| Our competitor sells fake products, do not trust them | Post | review · 0.505 |
| Limited stock! Contact us on WhatsApp for wholesale prices, full payment in advance only. | Advertisement | review · 0.452 |

### 5.3 Review: a rule flag overrides a "safe" model score

| Sentence | Content type | Model alone | Final |
|---|---|---|---|
| Premium basmati rice exporter, 20 years in business, FSSAI certified. Bulk orders welcome. Payment accepted via bitcoin. | Product listing | safe · 0.000 | **review** (flag `payment.off_platform`) |
| Industrial stainless steel valves, sizes 15mm to 300mm. Test certificates provided with every order. We also accept payment in crypto. | Product listing | safe · 0.005 | **review** (flag `payment.off_platform`) |

**Talking point:** "The model on its own would publish this. The rule catches a
payment channel that's risky for buyers, and a person checks it."

For contrast, paste the basmati sentence **without** the last sentence ("Payment accepted via bitcoin."). It comes back **allow**.

### 5.4 Rejected by the model

| Sentence | Content type | Result |
|---|---|---|
| Send your bank details and OTP to claim your prize money today | Post | reject · 0.999 |
| You people are idiots and I will destroy your business | Post | reject · 1.000 |
| Earn 50000 per week from home!!! No experience needed, limited slots, DM now | Advertisement | reject · 0.998 |
| Congratulations! You have won a lottery of 10 lakh rupees. Pay the processing charges to receive it. | Post | reject · 0.894 |

### 5.5 Rejected by the gate: model not called

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

### 5.6 Tricks the gate sees through

Before matching, the gate normalises text: it ignores case, collapses extra spaces, converts look-alike Unicode characters, and strips invisible characters.

| Sentence (copy exactly) | Trick | Result |
|---|---|---|
| Our supplier is a SCUMBAG, never order from them | Capital letters | reject by gate |
| Our supplier is a ｓｃｕｍｂａｇ, never order from them | Full-width look-alike letters | reject by gate |
| Invest now and DOUBLE     YOUR     MONEY in 7 days | Capitals and extra spaces | reject by gate |

### 5.7 The gate doesn't over-block

These sentences contain parts of blocked words or phrases, but not the phrases themselves. They pass the gate, and the model allows them.

| Sentence | Looks like | Result |
|---|---|---|
| Hellfire hot sauce, 250 ml bottles, made with ghost peppers. Bulk orders welcome. | "go to hell" | allow · 0.004 |
| Premium basmati rice exporter, 20 years in business. Our go-to rice for biryani. Bulk orders welcome. | "go to hell" | allow · 0.001 |
| Industrial stainless steel valves, sizes 15mm to 300mm. Double-walled models available. Test certificates provided with every order. | "double your money" | allow · 0.000 |

**Talking point:** "Rules match whole words and phrases, so honest listings aren't caught by accident."

---

## 6. Gate rule reference

The rules live in `flask_api/config/gate_patterns.yaml`.

| Rule | Action | Triggers on |
|---|---|---|
| `abuse.blocklist` | block | `moron`, `scumbag`, `go to hell`, plus the private policy list when present |
| `spam.blacklisted_domain` | block | `fast-cash-bonanza.example`, `claim-free-gift.test`, `paypa1-verify.example` |
| `scam.advance_fee` | block | "guaranteed returns/profits of N%", "double/triple your money/investment", "pay a (small) processing/release/clearance fee" |
| `spam.url_shortener` | flag | Short links: `bit.ly/…`, `tinyurl.com/…`, `t.co/…`, `goo.gl/…` and similar |
| `payment.off_platform` | flag | "pay/payment/send" within about 40 characters of "gift card", "crypto", "bitcoin", "USDT", "Western Union", "MoneyGram" |
| `contact.messenger_redirect` | flag | "WhatsApp/Telegram/Signal" followed by a phone number |

The demo domains use reserved endings (`.example`, `.test`) on purpose, so no real website is named.

---

## 7. Optional: calling the API from the terminal

This shows that the page is only a front end: the same API serves any client. Run these from a second terminal window while the server is running.

**PowerShell:**

```powershell
# Allowed listing
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/moderate -ContentType "application/json" -Body '{"content": "LED panel lights, 12W to 48W, BIS certified. Test certificates provided with every order.", "content_type": "product_listing", "content_id": "LST-1001"}'

# Blocked by the gate
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/moderate -ContentType "application/json" -Body '{"content": "Invest now and double your money in 7 days", "content_type": "advertisement"}'

# Bad input is rejected cleanly (the error text is printed from the catch block)
try { Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/moderate -ContentType "application/json" -Body '{"content": "   ", "content_type": "post"}' } catch { $_.ErrorDetails.Message }
try { Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/moderate -ContentType "application/json" -Body '{"content": "Hello", "content_type": "tweet"}' } catch { $_.ErrorDetails.Message }
```

**Command Prompt:**

```cmd
:: Allowed listing
curl -X POST http://127.0.0.1:8000/v1/moderate -H "Content-Type: application/json" -d "{\"content\": \"LED panel lights, 12W to 48W, BIS certified. Test certificates provided with every order.\", \"content_type\": \"product_listing\", \"content_id\": \"LST-1001\"}"

:: Blocked by the gate
curl -X POST http://127.0.0.1:8000/v1/moderate -H "Content-Type: application/json" -d "{\"content\": \"Invest now and double your money in 7 days\", \"content_type\": \"advertisement\"}"

:: Bad input is rejected cleanly
curl -X POST http://127.0.0.1:8000/v1/moderate -H "Content-Type: application/json" -d "{\"content\": \"   \", \"content_type\": \"post\"}"
curl -X POST http://127.0.0.1:8000/v1/moderate -H "Content-Type: application/json" -d "{\"content\": \"Hello\", \"content_type\": \"tweet\"}"
```

The first bad-input request returns `invalid_content`. The second returns `invalid_content_type` and lists the allowed types.

Latency benchmark, in a second terminal window inside `flask_api`:
```
.venv\Scripts\python.exe scripts\bench_latency.py
```

It prints p50 and p95 inference times against the 30 ms budget. On the development laptop, p95 is about 28 ms.
Slower machines may report `OVER`. The budget assumes a 4-core server.

---

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| `'python' is not recognized` | Reinstall Python with "Add Python to PATH" ticked, or use `py` instead of `python`. |
| The page says `service unreachable` | The server window was closed or crashed. Start it again (section 3). |
| `status not_ready` in the header | The model didn't load. Run `dir models\current`. If `model_int8.onnx` is tiny, run `git lfs pull`, then restart the server. |
| The page shows **demo page disabled** | The setting wasn't applied. In PowerShell use `$env:DEMO_PAGE = "1"`, not `set DEMO_PAGE=1`. Run it in the same window, then restart the server. |
| `No module named waitress` | Python ran outside the virtual environment. Start the server with `.venv\Scripts\python.exe -m waitress …` as shown in section 3. |
| `db_write_failed` errors in the server window | `RESULT_SINK` isn't set to `log`. Set it the way section 3 shows for your terminal, then restart. |
| Port 8000 already in use | Run `netstat -ano \| findstr :8000` to find the process, or start on another port: `python -m waitress --port=8080 wsgi:app`. |
| A sentence gives a different result | Check that it's pasted exactly. The v1 model is sensitive to small wording changes. |

---

## 9. If you're asked about limitations

- **Model v1 is conservative.** Some legitimate text (for example "Family-run textile mill in Surat…") lands in review, and it misses some counterfeit listings ("replica watches"). The ML team is recalibrating it. Until then, the review queue catches its mistakes instead of wrongly rejecting content.
- **The thresholds (0.30 / 0.70) are placeholders.** They'll be tuned once the model is recalibrated. They're configuration values, so changing them needs no code change.
- **The word and domain lists hold demo entries.** The production slur list comes from the policy team through a private file that isn't stored in git.
- **The demo runs in sync mode**, with one request and one answer. An async mode (Celery with Redis) is built in for heavy load. It returns `pending` and processes the content in the background.
