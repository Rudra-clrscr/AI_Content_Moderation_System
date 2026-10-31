#!/usr/bin/env bash
# Put both models and the moderation API onto a fresh Debian/Ubuntu host (e.g. a Contabo VPS).
#
#     sudo bash deploy/install.sh
#
# Idempotent: safe to re-run after a `git pull` to pick up a new model or new dependencies.
# It does NOT write .env and does not touch an existing one - secrets are yours to place.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/bonc}"
APP_USER="${APP_USER:-bonc}"
PY="${PY:-python3}"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ---- 1. system packages -------------------------------------------------------------
# git-lfs is not optional: the text model is an LFS object, and without it the clone leaves a
# 130-byte pointer where models/v9-ver3/model.onnx should be.
say "system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    "${PY}" "${PY}-venv" "${PY}-dev" build-essential \
    git git-lfs ca-certificates curl \
    ffmpeg          `# faster-whisper decodes audio through it; media.video.enabled is true` \
    libgl1 libglib2.0-0   `# Pillow/opencv runtime for the image path on a headless box`
git lfs install --system

# ---- 2. user and checkout -----------------------------------------------------------
say "service user and directories"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

if [ ! -d "$APP_DIR/.git" ]; then
    echo "No checkout at $APP_DIR. Clone it first, as $APP_USER:"
    echo "    sudo -u $APP_USER git clone <repo-url> $APP_DIR"
    exit 1
fi

# ---- 3. the text model comes from LFS -----------------------------------------------
say "text model (models/v9-ver3, from git LFS)"
sudo -u "$APP_USER" git -C "$APP_DIR" lfs pull

# ---- 4. python environment ----------------------------------------------------------
say "python environment"
sudo -u "$APP_USER" "$PY" -m venv "$APP_DIR/.venv" 2>/dev/null || true
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip wheel
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/flask_api/requirements.txt"
# gunicorn is the Linux server; requirements.txt ships waitress, which is the Windows one.
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet gunicorn

# ---- 5. the image model is NOT in git ------------------------------------------------
# Only four small files of models/img_v1 are tracked. vision_model.onnx is 352 MB and is
# fetched here, verified against a pinned SHA256 by the script itself. Skip this and the
# service still starts, still answers /ready with status "ready", and publishes every picture
# unexamined - which is why check_deploy.py runs before the service does.
say "image model (models/img_v1/vision_model.onnx, 352 MB)"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" "$APP_DIR/flask_api/scripts/fetch_clip.py"

# The text encoder is deliberately NOT fetched: it is only needed to rebuild prompts.npz from
# config/visual_policy.yaml, which is an offline step on whichever host edits the policy.

# ---- 6. OCR language models ----------------------------------------------------------
# settings.yaml pdf.ocr points at models/ocr/devanagari_rec.onnx, which is also not in git.
say "OCR language models"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" \
    "$APP_DIR/flask_api/scripts/fetch_ocr_langs.py" devanagari || \
    echo "  (OCR fetch failed. Devanagari uploads are then REFUSED as unreadable rather than published unchecked - the safe direction, but it turns away ordinary Hindi signage. Re-run this step when the network allows.)"

# ---- 7. prove both models are there before anything serves traffic -------------------
say "verifying both models"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" "$APP_DIR/flask_api/scripts/check_deploy.py"

# ---- 8. service ----------------------------------------------------------------------
say "systemd unit"
if [ ! -f "$APP_DIR/flask_api/.env" ]; then
    echo "  No $APP_DIR/flask_api/.env - the unit needs it for ADMIN_TOKEN and DB_*."
    echo "  Create it, then: systemctl enable --now bonc-api"
else
    chown "$APP_USER:$APP_USER" "$APP_DIR/flask_api/.env"
    chmod 600 "$APP_DIR/flask_api/.env"
    install -m 644 "$APP_DIR/deploy/bonc-api.service" /etc/systemd/system/bonc-api.service
    systemctl daemon-reload
    systemctl enable bonc-api
    echo "  systemctl restart bonc-api    # when you are ready"
fi

say "done"
echo "After starting, confirm BOTH models are live - not just that it is up:"
echo "    $APP_DIR/.venv/bin/python $APP_DIR/flask_api/scripts/check_deploy.py --url http://127.0.0.1:8000"
