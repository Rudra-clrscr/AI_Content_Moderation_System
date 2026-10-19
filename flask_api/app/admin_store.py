"""Where admin accounts, password-reset codes and the saved moderation settings live.

One small SQLite file (Python standard library, nothing to install). It is separate from the
SQL Server result sink on purpose: the admin screen must work, and settings must survive a
restart, even when DB_SERVER / DB_NAME are not configured.

Path: ADMIN_DB_PATH, default flask_api/data/admin.db (the data/ folder is gitignored).
"""
from __future__ import annotations

import hmac
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS admin (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    session_version INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reset_code (
    email TEXT PRIMARY KEY,
    code_hash TEXT NOT NULL,
    expires_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    token_hash TEXT,
    token_expires_at REAL
);
CREATE TABLE IF NOT EXISTS setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class AdminStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()      # one writer at a time inside this process
        self._mem = None
        if self.path == ":memory:":        # tests: one shared connection
            self._mem = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        with self._tx() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _tx(self):
        """Everything inside commits together or not at all (BEGIN IMMEDIATE blocks other writers)."""
        with self._lock:
            db = self._mem or sqlite3.connect(self.path, timeout=10, isolation_level=None)
            db.row_factory = sqlite3.Row
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                if self._mem is None:
                    db.close()

    # ---- admin accounts ---------------------------------------------------------------
    def get_admin(self, email: str):
        with self._tx() as db:
            return db.execute("SELECT * FROM admin WHERE email = ?", (email.lower().strip(),)).fetchone()

    def get_admin_by_id(self, admin_id: int):
        with self._tx() as db:
            return db.execute("SELECT * FROM admin WHERE id = ?", (admin_id,)).fetchone()

    def admin_count(self) -> int:
        with self._tx() as db:
            return db.execute("SELECT COUNT(*) FROM admin").fetchone()[0]

    def create_admin(self, email: str, password_hash: str) -> None:
        with self._tx() as db:
            db.execute("INSERT INTO admin (email, password_hash, created_at) VALUES (?,?,?)",
                       (email.lower().strip(), password_hash, time.time()))

    def set_password(self, email: str, password_hash: str) -> bool:
        """Explicit password change. Bumps session_version so every old login stops working."""
        with self._tx() as db:
            cur = db.execute("UPDATE admin SET password_hash = ?, session_version = session_version + 1 "
                             "WHERE email = ?", (password_hash, email.lower().strip()))
            return cur.rowcount == 1

    # ---- reset codes ------------------------------------------------------------------
    def put_code(self, email: str, code_hash: str, ttl: float) -> None:
        """Store a new code; REPLACE drops any older code and token for this email."""
        now = time.time()
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO reset_code (email, code_hash, expires_at, attempts, created_at) "
                       "VALUES (?,?,?,0,?)", (email.lower().strip(), code_hash, now + ttl, now))

    def get_code(self, email: str):
        with self._tx() as db:
            return db.execute("SELECT * FROM reset_code WHERE email = ?", (email.lower().strip(),)).fetchone()

    def verify_code(self, email: str, code_hash: str, max_attempts: int, token_hash: str, token_ttl: float) -> str:
        """Check a code. Returns 'ok', 'bad', 'expired' or 'locked'. All in one transaction."""
        email = email.lower().strip()
        now = time.time()
        with self._tx() as db:
            row = db.execute("SELECT * FROM reset_code WHERE email = ?", (email,)).fetchone()
            if row is None or row["token_hash"]:
                return "bad"
            if row["expires_at"] < now:
                db.execute("DELETE FROM reset_code WHERE email = ?", (email,))
                return "expired"
            if row["attempts"] >= max_attempts:
                db.execute("DELETE FROM reset_code WHERE email = ?", (email,))
                return "locked"
            if not hmac.compare_digest(row["code_hash"], code_hash):
                db.execute("UPDATE reset_code SET attempts = attempts + 1 WHERE email = ?", (email,))
                return "bad"
            # Right code: it is now spent. It is swapped for a one-time reset token.
            db.execute("UPDATE reset_code SET code_hash = '', token_hash = ?, token_expires_at = ? "
                       "WHERE email = ?", (token_hash, now + token_ttl, email))
            return "ok"

    def reset_password(self, email: str, token_hash: str, password_hash: str) -> bool:
        """Atomic: check the token, change the password, kill every session, delete the code."""
        email = email.lower().strip()
        now = time.time()
        with self._tx() as db:
            row = db.execute("SELECT * FROM reset_code WHERE email = ?", (email,)).fetchone()
            if (row is None or not row["token_hash"] or row["token_hash"] != token_hash
                    or (row["token_expires_at"] or 0) < now):
                return False
            db.execute("UPDATE admin SET password_hash = ?, session_version = session_version + 1 "
                       "WHERE email = ?", (password_hash, email))
            db.execute("DELETE FROM reset_code WHERE email = ?", (email,))
            return True

    # ---- saved moderation settings ----------------------------------------------------
    def load_settings(self) -> dict:
        with self._tx() as db:
            row = db.execute("SELECT value FROM setting WHERE key = 'moderation'").fetchone()
        return json.loads(row["value"]) if row else {}

    def save_settings(self, data: dict) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO setting (key, value) VALUES ('moderation', ?)",
                       (json.dumps(data, sort_keys=True),))
