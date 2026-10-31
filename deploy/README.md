# Deploying both models to a Linux host (Contabo VPS)

Written for: whoever provisions and operates the BONC moderation server.

This service runs **two models**, and they arrive by **different routes**. That asymmetry is
the whole reason this document exists.

| | what it does | how it reaches the host | size |
|---|---|---|---|
| `models/v9-ver3` | scores text | **git LFS** — comes with the clone | 371 MB |
| `models/img_v1` | scores pictures | **fetched on the host** — only 4 small files are in git | 352 MB |

Two supporting pieces are also not in git: the Devanagari OCR model
(`scripts/fetch_ocr_langs.py devanagari`) and Whisper `small`, which downloads itself on first
start because `media.video.enabled` is true.

---

## This box is not empty

An AI search service is already deployed on the target host, so **nothing here deletes or
overwrites anything that is not ours.** Run the read-only survey first and read it:

```bash
bash deploy/preflight.sh      # writes nothing, installs nothing, starts nothing
```

The ways a second Python/ML service quietly breaks the first are all in that output: the same
TCP port, the same systemd unit name, the same install directory, the same `/etc/gitconfig`,
the same GPU. None of them announce themselves at install time — they show up as the *other*
service failing.

What `install.sh` now refuses rather than overwrites:

| | behaviour |
|---|---|
| **Port 8000** | checked first; the script **exits** if anything is listening. `uvicorn`, `gunicorn` and `flask run` all default to 8000, so this is the likely clash. Re-run with `PORT=8010 GUNICORN_BIND=127.0.0.1:8010`. |
| **`/etc/gitconfig`** | untouched. `git lfs install` runs for the service user only, never `--system`. |
| **`$APP_DIR` ownership** | `chown` on the directory itself, never `-R`, and it **refuses** if the directory belongs to another user. |
| **`bonc-api.service`** | if a unit of that name exists and differs, it is left alone and you are told to diff it. |
| **Microsoft apt repo** | skipped entirely if the driver is already present; the keyring and sources file are never rewritten if they exist. |
| **`$APP_DIR`** | refuses to run against `/`, `/opt`, `/srv`, `/usr`, `/etc`, `/var`, `/home`, `/root`. |

What it *does* change outside `$APP_DIR`, deliberately: apt packages (`git-lfs`, `ffmpeg`,
`libgl1`, `tmux`, `unixodbc`…), one systemd unit, and optionally Microsoft's apt source. Those
are additive, but they are shared — if the search service pins a system library, check
`preflight.sh` output before installing.

---

## The failure to design against

Miss the image-model step and **nothing tells you**. Measured on a host with
`vision_model.onnx` removed:

```
GET /ready   ->  "status": "ready",  "model_ready": true
             ->  "visual": {"enabled": true, "ready": false, "reason": "...not in models/img_v1"}
POST an image ->  200 allow,  "visual_content_checked": false
```

The service is green, a load-balancer health check on `/ready` passes, text moderation works
properly — and every picture publishes unexamined. `app/clip.py` does this deliberately: a
missing image model must not take text moderation down with it. The cost is that the failure
is quiet, and `visual.ready` is nested two levels inside `/ready` where nobody looks on
deployment day.

So the rule for this deployment is: **never conclude it worked from `status: "ready"`.**

```bash
.venv/bin/python flask_api/scripts/check_deploy.py --url http://127.0.0.1:8000
```

That reads both models and exits non-zero if either is absent. `bonc-api.service` runs it as
`ExecStartPre`, so a host missing the image model refuses to start rather than serving blind.

---

## The measured host (vmi3527693, Contabo, surveyed 2026-10-31)

Ubuntu 24.04.5, **4 cores, 7.8 GB RAM, no swap**, 80 GB free, no GPU, Python 3.12.3.
It is a shared staging box, not a dedicated ML server — it already runs
`bonc-search-ai-staging`, `bonc-brochureiq-staging`, `bonc-video-api`, `bonc-video-sfu`,
`fastapi`, `fastapi-staging`, nginx and coturn.

```
Mem:  7.8Gi total   4.7Gi used   3.1Gi available   Swap: 0B
existing gunicorn RSS:  2.55 + 1.26 + 1.04 + 0.30  =  5.15 GB
```

| configuration | needs | left of 3.1 GB | |
|---|---|---|---|
| text model only | 0.81 GB | +2.29 | ok |
| **both models, `VIDEO_CHECK=0`** | **1.47 GB** | **+1.63** | **ok — the chosen configuration** |
| both models + Whisper | 2.47 GB | +0.63 | tight |
| both models, 2 workers | 2.94 GB | +0.16 | will OOM |

**One worker, video off.** Two workers do not fit, and neither does Whisper alongside both
models. There is **no swap**, so there is no soft landing: a global out-of-memory event is
resolved by the kernel killing whichever process scores worst, and that score tracks RSS —
which on this box is the **2.5 GB AI search**. Deploying carelessly here does not degrade
this service, it kills that one.

`bonc-api.service` therefore sets `MemoryHigh=1536M`, `MemoryMax=2G` and
`OOMScoreAdjust=500`: this service is throttled first, hard-capped second, and volunteered
to the OOM killer ahead of everything that was here before it.

### Video moderation is off, deliberately

Only the text and image models are wanted for now, which is also the only configuration that
fits. `VIDEO_CHECK=0` is set **in the systemd unit**, not left to `.env`, because forgetting it
is expensive: `wsgi.py` warms Whisper in a background thread on startup, which downloads the
model on first run and costs ~1 GB resident — taking the total to 2.47 GB against 3.1 GB
available.

It also means `ffmpeg` is **not installed**. It exists only so faster-whisper can decode
audio, and adding a large system package to a box that is not ours, for a feature that never
runs, is the kind of thing this deploy is trying to avoid. `apt-get install -y ffmpeg` if video
is ever turned on.

**One consequence to decide on.** With video off, an uploaded video follows
`media.allow_unchecked_video`, which is `true` in `settings.yaml` — so **videos publish** with
`checked: false` rather than being refused. That is the pre-existing behaviour and it is
recorded honestly in the result, but on a moderation service it is worth a deliberate choice.
Set `allow_unchecked_video: false` and an uploaded video is turned away with "video cannot be
checked by this service" and told to attach a still or a PDF instead.

### What this host already answers

| | |
|---|---|
| ports 8000–8003 | **all taken** by existing gunicorn masters — use **8004** |
| nginx on 80/443 | present, so **`trust_proxy_hops: 1`** is required, not optional |
| ODBC Driver 18 | **already installed** — the Microsoft apt step is skipped entirely |
| git-lfs | **not installed** — `install.sh` adds it (needed for the text model) |
| `/opt/bonc` | free; `/opt` holds `bonc-Video`, `bonc-video`, a release dir and `microsoft` |
| `bonc-api.service` | name is free |
| GPU | none — CPU-only, which is what `requirements.txt` pins |

With 4 cores shared across eight services, `OMP_NUM_THREADS=2` rather than 4.

**The honest recommendation:** both models fit, but with ~1.6 GB of headroom on a box whose
other tenants fluctuate. If image moderation matters in production, this wants its own
instance or more RAM. Adding 2–4 GB of swap would not make it fast — ONNX inference that
swaps is unusable — but it would turn a kill into a slowdown, which is the better failure.

---

## Sizing the VPS

Measured resident memory, **per gunicorn worker** — each worker loads its own copy
(`wsgi.py` says so, and `preload_app` is off because onnxruntime sessions do not survive a
fork safely):

```
text model  models/v9-ver3    558 MB
image model models/img_v1     661 MB
                             ------
per worker                   ~1.24 GB   before Python, Flask and request buffers
```

Whisper `small` is roughly another 1 GB on top, in whichever worker takes a video.

| Contabo plan | workers | note |
|---|---|---|
| 4 GB | 1 | tight with video on; consider `VIDEO_CHECK=0` |
| 8 GB | 2–3 | comfortable |
| 16 GB | 6 | |

**Do not size by CPU cores.** The usual "2 × cores + 1" on an 8-core box asks for 17 workers
and ~21 GB of models. Add `GUNICORN_THREADS` instead: onnxruntime releases the GIL, so threads
overlap without a second copy of a 1.24 GB model.

CPU-only is fine and is what a standard Contabo VPS gives you — `requirements.txt` pins the
CPU `onnxruntime`. Measured on this codebase, CLIP runs ~14.8 images/s on 6 CPU cores. Only
install `onnxruntime-gpu` if the plan actually has an NVIDIA card, and then remove the CPU
build first — they provide the same module and must not be installed together.

---

## Install

Tested against **Ubuntu 24.04 LTS** (`python3` there is 3.12, which is what this was
developed and tested on).

```bash
tmux new -s bonc                                # the install is long; SSH drops kill it
git clone <repo-url> /opt/bonc
bash /opt/bonc/deploy/install.sh
```

Run it under `tmux`. It downloads ~400 MB of models and builds a virtualenv; a dropped session
leaves a half-built venv and a half-populated `models/` directory, neither of which announces
itself. Reattach with `tmux attach -t bonc`.

**The SQL Server driver.** `result_sink: sql` writes every decision through `pyodbc` using
"ODBC Driver 18 for SQL Server", which Ubuntu does not carry. The script adds Microsoft's
repository and installs it. Without it nothing fails loudly: `sinks.FailSafeSink` catches the
error so storage can never refuse a member's post, so requests succeed, the dashboard's own
SQLite audit log still fills, and the only sign is one `db_write_failed` line per decision
while SQL Server receives nothing. Skip it with `INSTALL_MSSQL_DRIVER=0`, and set
`RESULT_SINK=log` in `.env` if this host is not meant to write to SQL Server at all.

`install.sh` is idempotent — re-run it after a `git pull` to pick up a new model or new
dependencies. It installs system packages (including **git-lfs**, without which the text model
is a 130-byte pointer, and **ffmpeg**, which faster-whisper decodes audio through), creates the
venv, pulls LFS, fetches the image and OCR models, and verifies both models before installing
the unit.

It does **not** write `.env` and will not touch an existing one. Create
`/opt/bonc/flask_api/.env` yourself with at least:

```
ADMIN_TOKEN=...
DB_SERVER=...   DB_NAME=...   DB_USER=...   DB_PASSWORD=...
```

`chmod 600` it. Secrets stay out of the systemd unit on purpose: unit files are world-readable
and `systemctl show` prints `Environment=` back to any user, which is why the unit uses
`EnvironmentFile=`.

Then:

```bash
sudo systemctl restart bonc-api
journalctl -u bonc-api -f
```

---

## Before you put it behind nginx

`trust_proxy_hops` in `config/settings.yaml` is **0**, which is correct only while nothing sits
in front. Put nginx, Cloudflare or a load balancer in front without changing it and every
moderation decision records the **proxy's** address instead of the author's — the audit log
fills with one address, and the Block button in the dashboard then refuses everybody.

Set it to the number of proxies in front (nginx alone is `1`). The service logs a warning once
if it sees `X-Forwarded-For` arriving while this is 0, so the mistake is visible — but set it
deliberately rather than waiting for the log.

A matching nginx note: `client_max_body_size` must be at least the largest upload the service
accepts (`media.max_bytes` in settings.yaml), or nginx rejects the upload with a 413 of its own
before the service ever sees it, and the author gets a bare nginx error page instead of a
moderation message.

---

## Verifying a deploy

In order. Stop at the first failure.

```bash
cd /opt/bonc/flask_api

# 1. both models on disk, neither an LFS pointer
../.venv/bin/python scripts/check_deploy.py

# 2. the rollback bundles, if you keep any on this host
../.venv/bin/python scripts/check_rollback_bundles.py

# 3. the running service: text AND image, not just "status: ready"
../.venv/bin/python scripts/check_deploy.py --url http://127.0.0.1:8000

# 4. an actual decision FROM THE MODEL. The sentence matters: "double your money" is caught
#    by a Layer 1 gate rule, so it rejects with decided_by=gate and model_version=null and
#    proves nothing about the model. This one reaches the model.
curl -s localhost:8000/v1/moderate -H 'Content-Type: application/json' \
  -d '{"content":"Share your OTP with me and I will release the payment immediately.","content_type":"article"}' \
  | python -c 'import json,sys; d=json.load(sys.stdin); print(d["decision"], d["decided_by"], d["model_version"])'
# expect: reject model bonc-v9-ver3

# 5. an actual image decision - this is the one that proves the second model is live
curl -s -F file=@some.jpg localhost:8000/v1/moderate/media \
  | python -c 'import json,sys; d=json.load(sys.stdin); print(d["media"]["visual_content_checked"])'
# expect: True.  False means the image model is not loaded, whatever /ready said.
```

Step 5 is the one worth keeping in a smoke test. It is the only check that fails when the image
model is missing and everything else looks healthy.

---

## Rolling back

The live model is one line: `model.dir` in `config/settings.yaml`, then
`systemctl restart bonc-api`.

That only works if the bundle you are rolling back **to** is on this host. Superseded bundles
are dropped from git when their replacement ships, and that drop deletes them from every
machine that pulls the commit — see the note in `.gitignore`. `check_rollback_bundles.py`
tells you which are actually present before you need one.

The two models roll back independently: `model.dir` moves the text model and nothing else;
replacing `models/img_v1/` moves the image model and leaves text alone.
