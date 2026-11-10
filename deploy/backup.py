#!/usr/bin/env python3
"""Back up what the running moderation service writes, into one dated .tar.gz.

    sudo -u moderation /usr/bin/python3 /opt/moderation/deploy/backup.py
    sudo -u moderation /usr/bin/python3 /opt/moderation/deploy/backup.py --dest /var/backups/moderation --keep 14

Run daily by moderation-backup.timer. Standard library only, so it does not depend on the
service's virtualenv being healthy - a backup is most wanted on the day something is broken.

WHAT IS IN IT - everything an admin can change from the dashboard, which git does not have:

    flask_api/data/admin.db       admin accounts, moderation levels, the audit log, blocked clients
    flask_api/data/system.db      the image policy database (categories, labelled images, training runs)
    flask_api/data/uploads/       images admins uploaded through the dashboard
    flask_api/config/*.yaml       visual_policy.yaml is edited in place by the policy screens
    flask_api/models/img_v1/probe*.npz, prompts.npz   rewritten when an admin retrains or edits policy

WHAT IS NOT:

    images/      the 835 MB dataset. It changes only when someone copies a new set up, and its
                 source copy is on the machine it was copied from. Pass --with-images to include it.
    .env         secrets. Kept out so a backup can be copied off the box without carrying the
                 service token. Everything in it can be re-issued (see deploy/STAGING_SERVER.md).
    models/      the model weights come from git LFS and scripts/fetch_*.py, not from here.

The .db files are copied with SQLite's online backup API rather than read as plain files, so a
backup taken while the service is writing is still a consistent database, and each copy is
integrity-checked before it goes into the archive. A failed run leaves no half-written archive.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

EXTRA = [
    "flask_api/data/uploads",
    "flask_api/config/visual_policy.yaml",
    "flask_api/config/settings.yaml",
    "flask_api/models/img_v1/probe.npz",
    "flask_api/models/img_v1/probe_backup_default.npz",
    "flask_api/models/img_v1/prompts.npz",
]


def snapshot_db(src: Path, dst: Path) -> None:
    with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
        s.backup(d)
    with sqlite3.connect(dst) as c:
        result = c.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"{src.name}: integrity check failed on the copy: {result}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app-dir", default="/opt/moderation")
    ap.add_argument("--dest", default="/var/backups/moderation")
    ap.add_argument("--keep", type=int, default=14, help="archives to keep (default 14)")
    ap.add_argument("--with-images", action="store_true", help="also archive the images/ dataset")
    args = ap.parse_args()

    app, dest = Path(args.app_dir), Path(args.dest)
    data = app / "flask_api" / "data"
    dbs = sorted(data.glob("*.db"))
    if not dbs:
        print(f"no databases in {data} - wrong --app-dir?", file=sys.stderr)
        return 1

    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    out = dest / f"moderation-{dt.datetime.now():%Y%m%d-%H%M%S}.tar.gz"
    partial = out.with_name(out.name + ".partial")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            for db in dbs:
                snapshot_db(db, Path(tmp) / db.name)
            with tarfile.open(partial, "w:gz") as tar:
                for db in dbs:
                    tar.add(Path(tmp) / db.name, arcname=f"flask_api/data/{db.name}")
                for rel in EXTRA + (["images"] if args.with_images else []):
                    if (app / rel).exists():
                        tar.add(app / rel, arcname=rel)
        os.chmod(partial, 0o600)
        partial.rename(out)
    except Exception as exc:
        partial.unlink(missing_ok=True)
        print(f"backup FAILED: {exc}", file=sys.stderr)
        return 1

    old = sorted(dest.glob("moderation-*.tar.gz"))[:-args.keep] if args.keep > 0 else []
    for p in old:
        p.unlink()
    print(f"wrote {out} ({out.stat().st_size / 2**20:.1f} MB): {', '.join(d.name for d in dbs)}"
          f"{' + images' if args.with_images else ''}; pruned {len(old)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
