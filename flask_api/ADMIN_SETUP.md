# Admin login, moderation settings and password recovery

## What it does
* **Admin tab** -> login (email + password) -> a popup with the AI-moderation ON/OFF switch and the
  Lenient / Medium / Strict level -> Save.
* One setting covers **every** content type (Articles, Add Business, Videos, Requests, Proposals,
  Business Proposals, and the rest of `ContentType`). The backend enforces it; the page only shows it.
* The setting is saved in `data/admin.db`, so it survives refreshes and restarts.
* **Forgot password** emails a 6-digit code (10 minutes, single use, 5 wrong tries) through Brevo.
* There is **no sign-up**. Administrators are created only with the command line below.

## One-time setup (PowerShell, from `flask_api`)
```powershell
copy .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"      # paste the result after SECRET_KEY= in .env
python -m app.admin_cli create-admin --email you@example.com   # asks for a password (10+ chars, letters+numbers)
python -m flask --app wsgi run --port 5000
```
Open http://localhost:5000/articles -> **Admin**.
Other commands: `python -m app.admin_cli set-password --email ...`, `python -m app.admin_cli list`.
Nothing resets a password at startup.

## Email (Brevo, free: 300 emails/day, no credit card)
1. Create a free account at brevo.com.
2. Add and verify your sender address (Senders, domains & dedicated IPs -> Senders). Brevo emails
   you a link; the menu names can change, so follow the current dashboard.
3. SMTP & API -> API Keys -> generate a key.
4. In `.env`: `EMAIL_PROVIDER_API_KEY=` the key, `EMAIL_FROM=` the verified sender address.
5. Restart, click **Forgot password**, and check the inbox (and spam).
Twilio's SendGrid was checked and not used: its free plan is only a 60-day trial (100 emails/day).
If email is not configured, the code is NOT sent and the server log says so; the page still shows
the same generic message (so it never reveals whether an email is registered).

## Local `.env` vs production hosting
* `.env` (local only): everything in `.env.example`.
* Production: set the same names as real environment variables in your host. Use HTTPS, set
  `SESSION_COOKIE_SECURE=1`, keep `data/` on disk that is backed up, and never commit `.env`.
* Run **one server process** (e.g. `waitress-serve --port=8000 wsgi:app`). With several gunicorn
  workers, a settings change reaches only the worker that handled it until the others restart, and
  rate limits are counted per worker.

## What turning moderation OFF does
Only the moderation checks stop; every request is allowed and the result says `decided_by: disabled`.
Login, who-can-do-what, input validation, upload size/type checks and the model reload endpoint are
unchanged. When ON, `reject` and `revise` are never published, and a moderator error publishes nothing.
