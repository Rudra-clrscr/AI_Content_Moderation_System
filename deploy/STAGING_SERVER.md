# The staging server: how it is set up and how to run it

Written for: anyone with sudo on the AI/ML staging box who has to update, restart, fix or
hand over the moderation service.

**Live at:** `https://boncaistageapi.boncnetwork.com` (Swagger at `/docs`). This is **staging**,
not production: the platform's .NET backend and the team test against it. Production should
get its own host name and its own tokens.

It was deployed by hand from this repository, under different names from the ones
[README.md](README.md), `install.sh` and `bonc-api.service` use. **Everything on this page
describes the real server; where the two disagree, this page is right.**

---

## Where everything is

| What | Where |
|---|---|
| Host | Contabo VPS `vmi3527693`, 169.58.222.81, Ubuntu 24.04, 4 vCPU, 7.8 GB RAM, **no swap**. Shared with other teams' services. |
| Code | `/opt/moderation` (a git checkout), owned by user **`moderation`** |
| Python | `/opt/moderation/.venv` |
| Settings and secrets | `/opt/moderation/flask_api/.env`, `chmod 600`, owner `moderation` |
| Service | systemd unit **`moderation-api`**, `/etc/systemd/system/moderation-api.service` |
| App server | gunicorn, 1 worker × 4 threads, on **127.0.0.1:8005**. Only nginx can reach it. |
| Public entry | nginx site `/etc/nginx/sites-available/boncaistageapi` on 80 and 443, which proxies to 8005 |
| Certificate | Let's Encrypt via Certbot, renews itself. Expires 2027-02-08 unless renewed. |
| Data | `flask_api/data/admin.db` (admins, levels, audit log, blocklist), `system.db` (image database), `uploads/` |
| Image dataset | `/opt/moderation/images` (835 MB, copied up by hand, not in git) |
| Backups | `/var/backups/moderation`, daily, last 14 kept (see [Backups](#backups)) |

Ports 8000–8003 and the sites `aiapi.`, `aistageapi.` and `vcs.boncnetwork.com` belong to other
teams. `aistageapi` is **not** ours, despite the similar name.

---

## Everyday commands

```bash
systemctl status moderation-api                       # running? since when?
sudo journalctl -u moderation-api -n 100 --no-pager   # recent log (sudo, or it shows nothing)
sudo journalctl -u moderation-api -f                  # follow it live
sudo systemctl restart moderation-api                 # ~20-60 s while the models load
curl -s http://127.0.0.1:8005/ready; echo             # "status":"ready" once loaded
sudo -u moderation /opt/moderation/.venv/bin/python /opt/moderation/flask_api/scripts/check_deploy.py --url http://127.0.0.1:8005
```

`check_deploy.py` is the check that matters: `/ready` says "ready" even when the image model or
the Hindi OCR model is missing and every picture goes through unexamined. It also runs before
every start (`ExecStartPre`), so a missing model stops the service starting instead of
quietly running half-blind.

**A restart interrupts moderation for up to a minute.** Callers should hold submissions during
it, not publish them, so don't restart during busy hours without telling the .NET team.

---

## Updating to a new version

```bash
cd /opt/moderation
sudo -u moderation git status --short        # READ THIS FIRST - see below
sudo -u moderation git pull
sudo -u moderation git lfs pull              # the text model is in LFS
sudo -u moderation .venv/bin/pip install -r flask_api/requirements.txt
sudo systemctl restart moderation-api
sudo -u moderation .venv/bin/python flask_api/scripts/check_deploy.py --url http://127.0.0.1:8005
```

**Admins change tracked files from the dashboard.** The policy screens rewrite
`flask_api/config/visual_policy.yaml`, and retraining rewrites `models/img_v1/probe.npz` and
`prompts.npz`. If `git status` lists them, that is admin work, not stray edits. Don't
`git checkout` or `git reset` it away. Run a backup first, then
`git stash && git pull && git stash pop`, and resolve any conflict by keeping the admin's
labels.

Run `scripts/fetch_clip.py` or `scripts/fetch_ocr_langs.py devanagari` only if
`check_deploy.py` says the image model or OCR model is missing.

**Never run `deploy/install.sh` on this server.** It is written for `/opt/bonc`, user `bonc`,
unit `bonc-api` and port 8000, and would install a second, conflicting service.

---

## Settings in `.env` that matter here

Values are not written down anywhere but the file itself. Read one with
`sudo grep '^NAME=' /opt/moderation/flask_api/.env`. Restart after any change.

| Key | Set to | Why |
|---|---|---|
| `GUNICORN_BIND` | `127.0.0.1:8005` | local only; nginx is the way in |
| `AUTH_DISABLED` | `1` | **all authentication off**: no bearer token on `/v1/moderate*`, no login on `/v1/admin/*`. Anyone who can reach the host can change levels, edit the policy and clear the audit log. Chosen on 2026-11-11 so the .NET team can put their own authentication in front. Remove the line, or set it to `0`, and restart to turn the checks below back on. `/ready` shows `admin_auth`. |
| `SERVICE_TOKEN` | empty | the bearer token for `/v1/moderate*`, used only when `AUTH_DISABLED` is off. **Empty means those endpoints are open to anyone.** Only the token goes here, without the word `Bearer`. Don't reuse the old one, which was exposed: generate a new one with the command below. |
| `ADMIN_TOKEN` | empty for now | if set, scripts can use the admin APIs with `X-Admin-Token: <value>`. Admins log in with email and password regardless. |
| `SECRET_KEY` | 64 hex chars | signs admin login sessions; changing it logs every admin out |
| `ADMIN_EMAIL` | the first admin's address | default for `admin_cli` |
| `SESSION_COOKIE_SECURE` | `1` | admin cookies only over HTTPS. Admin login over plain `http://` will not work, deliberately. |
| `TRUST_PROXY_HOPS` | `1` | nginx is in front, so the caller's address is read from `X-Forwarded-For`. Make it `2` once the .NET backend forwards the member's address. |
| `DEMO_PAGE` | `0` | the browser demo is not served here |
| `RESULT_SINK` | `log` | results go to the journal, not SQL Server |

New random secret, never printed to the screen:

```bash
sudo sed -i "s/^SERVICE_TOKEN=.*/SERVICE_TOKEN=$(openssl rand -hex 32)/" /opt/moderation/flask_api/.env
sudo systemctl restart moderation-api
```

Changing `SERVICE_TOKEN` locks out everyone using the old one, including the .NET backend.
Hand the new value over privately: not in a group chat, not in a screenshot.

## Admin accounts

```bash
cd /opt/moderation/flask_api
sudo -u moderation ../.venv/bin/python -m app.admin_cli list
sudo -u moderation ../.venv/bin/python -m app.admin_cli create-admin --email someone@boncnetwork.com
sudo -u moderation ../.venv/bin/python -m app.admin_cli set-password --email someone@boncnetwork.com
```

Email is not configured (no Brevo settings), so "forgot password" sends nothing. Reset
passwords with `set-password`. Give each colleague their own admin account rather than
sharing one.

---

## Memory: the limits, and what happens when they are wrong

The unit has `MemoryHigh=2400M`, `MemoryMax=2700M`, `OOMScoreAdjust=500`.

| Part | Resident |
|---|---|
| Worker with text and image models, before any picture | ~1.45 GB |
| OCR, loaded on the first image and never released | +200 MB, up to +290 MB after 12 MP photos |
| Decoded images on 4 threads | +50–150 MB |
| **Normal peak** | **~1.75–1.9 GB** |

The limits were 1800M/2G at first. The service never crashed. It **crawled**: OCR took
80–100 s instead of ~2 s, image uploads returned `504 Gateway Time-out`, `/health` took 5 s,
and the journal showed nothing. The requests nginx gave up on kept running, so every later
image queued behind them on the OCR lock until a restart.

**If images are slow or 504ing, check this first:**

```bash
systemctl show moderation-api -p MemoryCurrent -p MemoryPeak -p MemoryHigh
cat /sys/fs/cgroup/system.slice/moderation-api.service/memory.events     # "high" climbing = throttled
cat /sys/fs/cgroup/system.slice/moderation-api.service/memory.pressure   # "full avg60" well above 0 = stalling
free -h                                                                  # what the box has left
```

Don't raise the limits without checking `free -h`. The other services on the box need that memory too.
**Never add a second gunicorn worker.** Each worker loads its own copy of the models (~1.45 GB),
and two don't fit.

---

## nginx

The site is `/etc/nginx/sites-available/boncaistageapi`. Certbot added the 443 block and the
80 → 443 redirect to it. Everything goes to `http://127.0.0.1:8005`, with
`client_max_body_size 20m` (uploads are capped at 15 MB by the app) and 180 s proxy timeouts.

```bash
sudo nginx -t && sudo systemctl reload nginx       # after any edit
sudo certbot certificates                           # expiry dates
sudo certbot renew --dry-run                        # renewal works?
sudo tail -50 /var/log/nginx/error.log
```

A temporary second site, `moderation-api`, used to serve `http://169.58.222.81:8005` directly,
with ufw allowing 8005. Both were removed once the domain was live. If you put a public listener
on 8005 again, give it the **IP address** (`listen 169.58.222.81:8005;`), never a bare
`listen 8005;`, which collides with gunicorn on 127.0.0.1:8005.

---

## Backups

`deploy/backup.py`, run daily at 03:30 by `moderation-backup.timer`. It covers both databases
(copied with SQLite's online backup and integrity-checked), admin uploads, the admin-edited
config and the probe files. It does not cover `.env` (secrets) or the 835 MB `images/` dataset,
whose original is on the machine it was uploaded from. Add `--with-images` for a one-off full copy.

**Setup (once):**

```bash
sudo install -d -o moderation -g moderation -m 700 /var/backups/moderation
sudo cp /opt/moderation/deploy/moderation-backup.service /opt/moderation/deploy/moderation-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now moderation-backup.timer
sudo systemctl start moderation-backup && sudo journalctl -u moderation-backup -n 5 --no-pager
systemctl list-timers moderation-backup.timer
```

**Restore:**

```bash
sudo systemctl stop moderation-api
cd /tmp && sudo -u moderation tar -xzf /var/backups/moderation/moderation-YYYYMMDD-HHMMSS.tar.gz
sudo -u moderation cp -a /tmp/flask_api/. /opt/moderation/flask_api/     # databases, uploads, config, probe
sudo systemctl start moderation-api
```

The backups sit on the same disk as the service. They protect against a bad admin edit, an
accidental delete or a corrupted database, not against losing the server. Now and then, copy
the newest archive off the box (`scp` it to your own machine).

---

## When something is wrong

| Symptom | Likely cause | Look at |
|---|---|---|
| Images 504, `/health` slow | memory throttling | the [Memory](#memory-the-limits-and-what-happens-when-they-are-wrong) commands |
| Service restarting in a loop | startup crash, or `check_deploy.py` failing before start | `sudo journalctl -u moderation-api -n 80` |
| `ModuleNotFoundError: sklearn` at start | requirements not installed after a pull | `pip install -r flask_api/requirements.txt` |
| 401 `a valid bearer token is required` | wrong token, or `Bearer ` missing from the header | the header must be `Authorization: Bearer <token>`. In Swagger, type `Bearer ` in the box too. |
| Admin login works on `https://` but not `http://` | `SESSION_COOKIE_SECURE=1` | intended. Use the domain. |
| "Welcome to nginx!" or nginx 404 on the domain | the nginx site is not enabled | `ls -l /etc/nginx/sites-enabled/` |
| Every request comes from the same IP in the audit log | `TRUST_PROXY_HOPS` doesn't match the proxies in front | see the `.env` table |
| Hindi text in pictures not read | Devanagari OCR model missing | `check_deploy.py`, then `scripts/fetch_ocr_langs.py devanagari` |
