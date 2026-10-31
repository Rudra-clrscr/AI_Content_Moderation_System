#!/usr/bin/env bash
# Survey a host BEFORE installing anything. Reads only - it writes nothing, installs nothing,
# starts nothing and changes no configuration.
#
#     bash deploy/preflight.sh            # or: curl it down and run it, nothing is modified
#
# This exists because the target box is not empty. Another service (an AI search feature) is
# already deployed on it, and the ways a second Python/ML service collides with the first are
# all quiet ones: the same TCP port, the same systemd unit name, the same install directory,
# the same /etc/gitconfig, the same CUDA or BLAS thread pools fighting over cores. None of
# that announces itself at install time - it shows up as the OTHER service failing.
#
# Run this, read it, and only then run install.sh.
set -uo pipefail          # deliberately not -e: a missing tool must not stop the survey

hr() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

hr "host"
. /etc/os-release 2>/dev/null && echo "  ${PRETTY_NAME:-unknown}  kernel $(uname -r)  $(uname -m)"
have systemd-detect-virt && echo "  virtualisation: $(systemd-detect-virt 2>/dev/null)"

hr "memory and cpu  (decides how many gunicorn workers fit: ~1.24 GB each)"
free -h 2>/dev/null | sed 's/^/  /'
echo "  cores: $(nproc 2>/dev/null)"
echo "  --- biggest consumers already running ---"
ps -eo rss,comm --sort=-rss 2>/dev/null | head -8 | awk 'NR==1{print "    RSS_KB COMMAND"; next}{printf "    %8d %s\n", $1, $2}'

hr "disk  (models need about 1.5 GB; the venv another 2-3 GB)"
df -h / /opt /var 2>/dev/null | sed 's/^/  /' | sort -u

hr "PORTS ALREADY IN USE  (gunicorn wants 127.0.0.1:8000 - change GUNICORN_BIND if taken)"
if have ss; then
    ss -ltnp 2>/dev/null | sed 's/^/  /'
else
    netstat -ltnp 2>/dev/null | sed 's/^/  /'
fi

hr "services already installed  (the new unit would be called bonc-api)"
systemctl list-units --type=service --state=running --no-pager --no-legend 2>/dev/null \
    | awk '{print "  " $1}' | head -30
echo "  --- is the name taken? ---"
if systemctl list-unit-files 2>/dev/null | grep -q '^bonc-api\.service'; then
    echo "  WARNING: bonc-api.service ALREADY EXISTS - install.sh would overwrite it"
else
    echo "  bonc-api.service: free"
fi

hr "install directory  (install.sh defaults to /opt/bonc; it must not land on the search app)"
ls -la /opt /srv 2>/dev/null | sed 's/^/  /'
for d in /opt/bonc /srv/bonc; do
    [ -e "$d" ] && echo "  NOTE: $d already exists - choose another APP_DIR or confirm it is ours"
done

hr "python  (this service is built and tested on 3.12)"
for p in python3 python3.12 python3.11; do
    have "$p" && echo "  $p -> $($p -V 2>&1)"
done
echo "  --- other virtualenvs on the box, so we do not disturb one ---"
find /opt /srv /home /root -maxdepth 4 -name pyvenv.cfg 2>/dev/null | head -10 | sed 's/^/    /'

hr "git and lfs  (install.sh must NOT touch /etc/gitconfig: the search app may rely on it)"
have git && echo "  $(git --version)"
have git-lfs && echo "  $(git lfs version)" || echo "  git-lfs: NOT installed (needed for the text model)"
echo "  --- current system-wide git config, for reference only ---"
git config --system --list 2>/dev/null | sed 's/^/    /' || echo "    (none)"

hr "gpu  (if there is one, the two services will contend for it)"
if have nvidia-smi; then
    nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv 2>/dev/null | sed 's/^/  /'
    echo "  --- processes already holding the GPU ---"
    nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv 2>/dev/null | sed 's/^/    /'
else
    echo "  no nvidia-smi: CPU-only, which is what requirements.txt pins (onnxruntime CPU)"
fi

hr "odbc  (the SQL sink needs 'ODBC Driver 18 for SQL Server')"
have odbcinst && odbcinst -q -d 2>/dev/null | sed 's/^/  /' || echo "  odbcinst not present - no ODBC drivers installed yet"

hr "nothing was changed"
echo "  This script only read. Send the output back before running install.sh, in particular"
echo "  the PORTS section and anything already in /opt."
