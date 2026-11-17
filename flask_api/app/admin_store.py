"""Where admin accounts, password-reset codes and the saved moderation settings live.

One small SQLite file (Python standard library, nothing to install). It is separate from the
SQL Server result sink on purpose: the admin screen must work, and settings must survive a
restart, even when DB_SERVER / DB_NAME are not configured.

Path: ADMIN_DB_PATH, default flask_api/data/admin.db (the data/ folder is gitignored).
"""
from __future__ import annotations

import hmac
import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

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
CREATE TABLE IF NOT EXISTS moderation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE,
    user_id TEXT,
    content_id TEXT,
    content_type TEXT NOT NULL,
    decision TEXT NOT NULL,
    decided_by TEXT,
    severity TEXT,
    risk_score REAL,
    predicted_label TEXT,
    feedback_title TEXT,
    feedback_message TEXT,
    content_preview TEXT,
    gate_matches TEXT,
    model_version TEXT,
    latency_ms REAL,
    raw_result TEXT,
    client_ip TEXT,
    client_network TEXT,
    created_at REAL NOT NULL,
    decided_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mod_log_created ON moderation_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mod_log_decision ON moderation_log (decision);
CREATE INDEX IF NOT EXISTS idx_mod_log_content_type ON moderation_log (content_type);
CREATE INDEX IF NOT EXISTS idx_mod_log_severity ON moderation_log (severity);
CREATE INDEX IF NOT EXISTS idx_mod_log_user ON moderation_log (user_id);
-- No index on client_ip here: this script runs before the migration below, and on a database
-- that predates the column an index over it fails and takes the whole startup with it. It is
-- created beside the column it covers, in _add_missing_columns.

-- Addresses an operator has refused. `scope` is 'ip' for one device or 'network' for the
-- block it sits in, because an address that rotates within its own /24 is the usual reason
-- somebody reaches for this twice. 'member' refuses one portal member by user_id, wherever
-- they post from - the one block a phone hopping between networks cannot step around.
CREATE TABLE IF NOT EXISTS blocked_client (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    value TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'ip',
    reason TEXT,
    created_by TEXT,
    created_at REAL NOT NULL,
    UNIQUE (value, scope)
);
CREATE INDEX IF NOT EXISTS idx_blocked_value ON blocked_client (value);
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
            self._add_missing_columns(db)

    @staticmethod
    def _add_missing_columns(db) -> None:
        """Add columns that CREATE TABLE IF NOT EXISTS cannot add to a database that exists.

        The schema above only ever runs against a fresh file, so a new column reaches every
        developer and nobody already running the thing - the live audit log had 8,177 rows when
        client_ip was added. Additive and idempotent: ALTER TABLE ADD COLUMN on SQLite is a
        metadata-only change, existing rows read NULL, and nothing here rewrites or drops.
        """
        wanted = {"moderation_log": {"client_ip": "TEXT", "client_network": "TEXT",
                                     "modality": "TEXT"}}
        for table, columns in wanted.items():
            have = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
            if not have:
                continue
            for name, decl in columns.items():
                if name not in have:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                    log.info("admin store: added %s.%s", table, name)
        db.execute("CREATE INDEX IF NOT EXISTS idx_mod_log_ip ON moderation_log (client_ip)")
        # Rows written before `modality` was recorded. A picture's own verdict is
        # recognisable - a visual.* match, or the `[photo.jpg]` placeholder that stands in for
        # content - so those are filed as image. What cannot be recovered is the OCR text read
        # out of a picture, which was logged as plain text: those older rows count as text.
        db.execute("""UPDATE moderation_log SET modality = CASE
                        WHEN gate_matches LIKE '%"visual.%'
                          OR (substr(content_preview, 1, 1) = '['
                              AND substr(content_preview, -1, 1) = ']'
                              AND instr(content_preview, '.') > 0) THEN 'image'
                        ELSE 'text' END
                      WHERE modality IS NULL""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_mod_log_modality ON moderation_log (modality)")

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

    # ---- moderation logs & audit trail ------------------------------------------------
    def _extract_log_params(self, result: dict) -> dict:
        feedback = result.get("feedback") or {}
        severity = feedback.get("severity")
        decision = (result.get("decision") or "").lower()
        if not severity:
            if decision == "reject":
                severity = "high"
            elif decision == "revise":
                severity = "medium"
            elif decision == "allow":
                severity = "low"
            else:
                severity = "none"

        content = result.get("content") or ""
        content_preview = content[:250] + ("..." if len(content) > 250 else "")

        # No content_id fallback. A content id is not an author, and falling back to it made
        # every row in the dashboard claim a user that was really the post's own id, shown
        # twice over in the audit modal. Nothing populates user_id yet - ModerationRequest
        # carries the field but no endpoint parses it from the request body - so this reads
        # "anonymous", which is the true answer until that is wired.
        user_id = result.get("user_id") or result.get("user") or result.get("author") or "anonymous"
        content_id = result.get("content_id")
        latency = result.get("latency_ms")
        total_ms = latency.get("total") if isinstance(latency, dict) else (latency if isinstance(latency, (int, float)) else None)

        # Recorded as the endpoint resolved it - see api.client_address - rather than re-derived
        # here, because this class never sees a request and guessing would mean recording an
        # address nobody connected from.
        client = result.get("client") or {}

        modality = str(result.get("modality") or "").lower()
        if modality not in ("text", "image"):
            visual = any(str((m or {}).get("rule_id", "")).startswith("visual.")
                         for m in (result.get("gate_matches") or []) if isinstance(m, dict))
            modality = "image" if visual or result.get("media") else "text"

        return {
            "modality": modality,
            "client_ip": client.get("ip"),
            "client_network": client.get("network"),
            "request_id": result.get("request_id") or f"req_{int(time.time()*1000)}",
            "user_id": str(user_id) if user_id is not None else None,
            "content_id": str(content_id) if content_id is not None else None,
            "content_type": str(result.get("content_type") or "article"),
            "decision": str(result.get("decision") or "unknown"),
            "decided_by": str(result.get("decided_by") or ""),
            "severity": str(severity),
            "risk_score": result.get("risk_score"),
            "predicted_label": result.get("predicted_label"),
            "feedback_title": feedback.get("title") or "",
            "feedback_message": feedback.get("message") or "",
            "content_preview": content_preview,
            "gate_matches": json.dumps(result.get("gate_matches") or []),
            "model_version": result.get("model_version"),
            "latency_ms": total_ms,
            "raw_result": json.dumps(result, sort_keys=True),
            "created_at": time.time(),
            "decided_at": result.get("decided_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def record_moderation_log(self, result: dict) -> None:
        try:
            params = self._extract_log_params(result)
            with self._tx() as db:
                db.execute("""
                    INSERT OR REPLACE INTO moderation_log (
                        request_id, user_id, content_id, content_type,
                        decision, decided_by, severity, risk_score,
                        predicted_label, feedback_title, feedback_message,
                        content_preview, gate_matches, model_version,
                        latency_ms, raw_result, client_ip, client_network,
                        created_at, decided_at, modality
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    params["request_id"], params["user_id"], params["content_id"], params["content_type"],
                    params["decision"], params["decided_by"], params["severity"], params["risk_score"],
                    params["predicted_label"], params["feedback_title"], params["feedback_message"],
                    params["content_preview"], params["gate_matches"], params["model_version"],
                    params["latency_ms"], params["raw_result"],
                    params["client_ip"], params["client_network"],
                    params["created_at"], params["decided_at"], params["modality"]
                ))
        except Exception:
            # The audit log must never fail a moderation decision, but a write that fails
            # silently is a log that looks empty for no stated reason.
            log.exception("moderation_log write failed request_id=%s", result.get("request_id"))

    @staticmethod
    def _moderation_log_filter(decision: str | None, content_type: str | None,
                               severity: str | None, query: str | None,
                               modality: str | None = None, since: float | None = None,
                               until: float | None = None,
                               user: str | None = None) -> tuple[str, list]:
        """(WHERE clause, parameters) for the moderation log, shared by the page and the export.

        One builder, because the export button means "give me what I am looking at". When the
        two were written separately the export quietly covered a different set of rows than the
        screen it sits on, which is the kind of thing nobody checks a downloaded file for.
        """
        where_clauses = []
        args: list = []
        if decision and decision.lower() != "all":
            where_clauses.append("LOWER(decision) = LOWER(?)")
            args.append(decision)
        if content_type and content_type.lower() != "all":
            where_clauses.append("LOWER(content_type) = LOWER(?)")
            args.append(content_type)
        if severity and severity.lower() != "all":
            where_clauses.append("LOWER(severity) = LOWER(?)")
            args.append(severity)
        if query and query.strip():
            q_like = f"%{query.strip()}%"
            where_clauses.append("(user_id LIKE ? OR content_id LIKE ? OR content_preview LIKE ? OR request_id LIKE ? OR feedback_message LIKE ?)")
            args.extend([q_like, q_like, q_like, q_like, q_like])
        if modality and modality.lower() in ("text", "image"):
            where_clauses.append("modality = ?")
            args.append(modality.lower())
        # created_at is epoch seconds, written when the row is, so a range is exact rather
        # than depending on how a caller formatted decided_at.
        if since is not None:
            where_clauses.append("created_at >= ?")
            args.append(float(since))
        if until is not None:
            where_clauses.append("created_at < ?")
            args.append(float(until))
        if user and user.strip():
            where_clauses.append("user_id = ?")
            args.append(user.strip())
        return (("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""), args

    # The columns an export carries, in the order a reader wants them. `raw_result` is
    # deliberately left out: it is the whole decision as JSON, several KB a row, and it turns a
    # readable table into something nobody can open in Word.
    EXPORT_COLUMNS = [
        ("created_at", "Timestamp"), ("request_id", "Request ID"), ("user_id", "User"),
        ("content_id", "Content ID"), ("content_type", "Surface"), ("modality", "Model"),
        ("decision", "Decision"),
        ("severity", "Severity"), ("risk_score", "Risk score"), ("decided_by", "Decided by"),
        ("predicted_label", "Predicted label"), ("model_version", "Model version"),
        ("latency_ms", "Latency (ms)"), ("client_ip", "Device IP"),
        ("client_network", "Network"), ("content_preview", "Content"),
        ("feedback_title", "Feedback title"), ("feedback_message", "Feedback message"),
    ]

    def export_moderation_logs(self, *, decision: str | None = None,
                               content_type: str | None = None, severity: str | None = None,
                               query: str | None = None, limit: int = 50000,
                               **more) -> list[dict]:
        """Every row matching the filter, oldest last, for a download.

        Capped rather than unbounded: the whole result is built in memory to be written into a
        workbook or a document, and an operator who clears nothing would otherwise discover the
        limit as a dead server rather than as a truncated file. The caller is told when the cap
        bit, so the file can say so about itself.
        """
        where_sql, args = self._moderation_log_filter(decision, content_type, severity, query,
                                                      **more)
        cols = ", ".join(c for c, _ in self.EXPORT_COLUMNS)
        with self._tx() as db:
            rows = db.execute(
                f"SELECT {cols} FROM moderation_log {where_sql} "
                f"ORDER BY created_at DESC, id DESC LIMIT ?",
                tuple(args + [int(limit)])).fetchall()
            return [dict(r) for r in rows]

    def count_moderation_logs(self, *, decision: str | None = None,
                              content_type: str | None = None, severity: str | None = None,
                              query: str | None = None, **more) -> int:
        where_sql, args = self._moderation_log_filter(decision, content_type, severity, query,
                                                      **more)
        with self._tx() as db:
            row = db.execute(f"SELECT COUNT(*) AS cnt FROM moderation_log {where_sql}",
                             tuple(args)).fetchone()
            return int(row["cnt"]) if row else 0

    def get_moderation_logs(self, *, decision: str | None = None, content_type: str | None = None,
                            severity: str | None = None, query: str | None = None,
                            limit: int = 50, offset: int = 0, modality: str | None = None,
                            since: float | None = None, until: float | None = None,
                            user: str | None = None, offender_min: int = 3,
                            offender_rate: float = 0.5) -> dict:
        more = {"modality": modality, "since": since, "until": until, "user": user}
        where_sql, args = self._moderation_log_filter(decision, content_type, severity, query,
                                                      **more)
        # THE KPIs DESCRIBE WHAT IS ON THE SCREEN. They were counted over the whole table
        # whatever the filters said, so narrowing the log to one surface or one week left
        # "Allowed" and "Refusals" showing all-time totals beside rows that did not add up to
        # them. Same filter as the table - except the decision filter, because "REJECT only"
        # with Allowed reading 0 answers nothing; the cards keep the split, the table narrows.
        kpi_sql, kpi_args = self._moderation_log_filter(None, content_type, severity, query,
                                                        **more)

        with self._tx() as db:
            stats = {"total": 0, "allow": 0, "revise": 0, "reject": 0}
            by_modality = {m: {"total": 0, "allow": 0, "revise": 0, "reject": 0}
                           for m in ("text", "image")}
            for row in db.execute(
                    f"SELECT COALESCE(modality, 'text') AS m, LOWER(decision) AS dec, "
                    f"COUNT(*) AS cnt FROM moderation_log {kpi_sql} "
                    f"GROUP BY COALESCE(modality, 'text'), LOWER(decision)", tuple(kpi_args)):
                m = row["m"] if row["m"] in by_modality else "text"
                d, c = row["dec"], row["cnt"]
                # Anything outside allow/revise/reject (a disabled surface's own decision, say)
                # still counts toward the total, so the total is the number of rows.
                if d in stats:
                    stats[d] += c
                    by_modality[m][d] += c
                stats["total"] += c
                by_modality[m]["total"] += c
            for bucket in [stats, *by_modality.values()]:
                done = bucket["allow"] + bucket["revise"] + bucket["reject"]
                bucket["reject_rate"] = round(bucket["reject"] / done, 4) if done else None
            span = db.execute(f"SELECT MIN(created_at) AS first, MAX(created_at) AS last "
                              f"FROM moderation_log {kpi_sql}", tuple(kpi_args)).fetchone()

            total_row = db.execute(f"SELECT COUNT(*) as cnt FROM moderation_log {where_sql}", tuple(args)).fetchone()
            total = total_row["cnt"] if total_row else 0

            query_sql = f"""
                SELECT id, request_id, user_id, content_id, content_type,
                       decision, decided_by, severity, risk_score, predicted_label,
                       feedback_title, feedback_message, content_preview,
                       model_version, latency_ms, client_ip, client_network,
                       created_at, decided_at, raw_result, modality
                FROM moderation_log
                {where_sql}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
            """
            rows = db.execute(query_sql, tuple(args + [limit, offset])).fetchall()
            logs = []
            for r in rows:
                item = dict(r)
                if item.get("raw_result"):
                    try:
                        item["raw_result"] = json.loads(item["raw_result"])
                    except Exception:
                        pass
                logs.append(item)

        offenders = self.repeat_offenders(since=since, until=until, min_rejects=offender_min,
                                          min_rate=offender_rate, content_type=content_type)
        return {
            "logs": logs,
            "total": total,
            "limit": limit,
            "offset": offset,
            "stats": stats,
            "by_modality": by_modality,
            "range": {"since": since, "until": until,
                      "first_at": span["first"] if span else None,
                      "last_at": span["last"] if span else None},
            "generated_at": time.time(),
            "repeat_offenders": offenders,
        }

    def member_record(self, user_id: str, *, since: float | None = None,
                      until: float | None = None, min_rejects: int = 3,
                      min_rate: float = 0.5) -> dict:
        """One member's moderation record in a window, and whether it makes them an offender.

        The same counts and the same two-part rule as repeat_offenders, for a member who may
        or may not meet it - so a caller can ask "is this member one?" and get the numbers
        behind the answer either way, rather than inferring "no" from absence in a list.
        """
        min_rejects = max(1, int(min_rejects))
        min_rate = min(1.0, max(0.0, float(min_rate)))
        where_sql, args = self._moderation_log_filter(None, None, None, None,
                                                      since=since, until=until, user=user_id)
        with self._tx() as db:
            r = db.execute(f"""
                SELECT COUNT(*) AS total,
                       COALESCE(SUM(LOWER(decision) = 'allow'), 0) AS allowed,
                       COALESCE(SUM(LOWER(decision) = 'revise'), 0) AS revised,
                       COALESCE(SUM(LOWER(decision) = 'reject'), 0) AS rejected,
                       COALESCE(SUM(LOWER(decision) = 'reject' AND COALESCE(modality, 'text') = 'text'), 0) AS text_rejected,
                       COALESCE(SUM(LOWER(decision) = 'reject' AND modality = 'image'), 0) AS image_rejected,
                       MAX(CASE WHEN LOWER(decision) = 'reject' THEN created_at END) AS last_reject_at,
                       MAX(created_at) AS last_at
                FROM moderation_log {where_sql}""", tuple(args)).fetchone()
        out = {"user_id": user_id, **dict(r)}
        out["reject_rate"] = round(out["rejected"] / out["total"], 4) if out["total"] else None
        out["is_repeat_offender"] = bool(
            out["total"] and out["rejected"] >= min_rejects
            and out["rejected"] / out["total"] >= min_rate)
        out.update(min_rejects=min_rejects, min_rate=min_rate)
        return out

    def repeat_offenders(self, *, since: float | None = None, until: float | None = None,
                         min_rejects: int = 3, min_rate: float = 0.5,
                         content_type: str | None = None, limit: int = 50,
                         modality: str | None = None) -> dict:
        """Members whose submissions are refused too often, in the window being looked at.

        Both conditions, not either: a count alone flags the busiest honest member (ten
        refusals out of four hundred posts), and a rate alone flags a newcomer with one post
        refused out of one. Counted per user_id, never per address - the portal calls from its
        own servers, so an address names the portal, not a member, and would make everyone one
        offender. Rows with no user id ("anonymous") cannot be attributed and are left out.
        """
        where_sql, args = self._moderation_log_filter(None, content_type, None, None,
                                                      since=since, until=until,
                                                      modality=modality)
        cond = "user_id IS NOT NULL AND user_id != '' AND user_id != 'anonymous'"
        where_sql = f"{where_sql} AND {cond}" if where_sql else f"WHERE {cond}"
        min_rejects = max(1, int(min_rejects))
        min_rate = min(1.0, max(0.0, float(min_rate)))
        with self._tx() as db:
            rows = db.execute(f"""
                SELECT user_id,
                       COUNT(*) AS total,
                       SUM(LOWER(decision) = 'reject') AS rejected,
                       SUM(LOWER(decision) = 'revise') AS revised,
                       SUM(LOWER(decision) = 'reject' AND COALESCE(modality, 'text') = 'text') AS text_rejected,
                       SUM(LOWER(decision) = 'reject' AND modality = 'image') AS image_rejected,
                       MAX(CASE WHEN LOWER(decision) = 'reject' THEN created_at END) AS last_reject_at,
                       MAX(created_at) AS last_at
                FROM moderation_log {where_sql}
                GROUP BY user_id
                HAVING rejected >= ? AND (rejected * 1.0 / total) >= ?
                ORDER BY rejected DESC, last_reject_at DESC
                LIMIT ?""", tuple(args + [min_rejects, min_rate, int(limit)])).fetchall()
            attributed = db.execute(
                f"SELECT COUNT(*) AS n FROM moderation_log {where_sql}", tuple(args)).fetchone()
        return {
            "users": [{**dict(r), "reject_rate": round(r["rejected"] / r["total"], 4)}
                      for r in rows],
            "min_rejects": min_rejects, "min_rate": min_rate,
            # So the screen can say "no member ids are being sent" rather than "no offenders".
            "attributed_rows": int(attributed["n"]) if attributed else 0,
        }

    def log_health(self, window_s: float = 3600.0) -> dict:
        """Traffic over the last window, from the audit log: volume, refusals, latency."""
        since = time.time() - window_s
        out: dict = {"window_s": window_s}
        with self._tx() as db:
            for m in ("text", "image"):
                rows = db.execute(
                    "SELECT LOWER(decision) AS d, latency_ms FROM moderation_log "
                    "WHERE created_at >= ? AND COALESCE(modality, 'text') = ?",
                    (since, m)).fetchall()
                lat = sorted(float(r["latency_ms"]) for r in rows if r["latency_ms"] is not None)
                out[m] = {
                    "requests": len(rows),
                    "rejected": sum(1 for r in rows if r["d"] == "reject"),
                    "latency_ms_avg": round(sum(lat) / len(lat), 1) if lat else None,
                    "latency_ms_p95": (round(lat[min(len(lat) - 1, int(len(lat) * 0.95))], 1)
                                       if lat else None),
                }
            last = db.execute("SELECT MAX(created_at) AS t FROM moderation_log").fetchone()
            out["last_decision_at"] = last["t"] if last else None
        return out

    # ---- blocked addresses --------------------------------------------------------

    @staticmethod
    def _block_scope(scope: str | None) -> str:
        s = str(scope or "").lower()
        return s if s in ("ip", "network", "member") else "ip"

    def block_client(self, value: str, scope: str = "ip", reason: str = "",
                     created_by: str = "") -> dict:
        """Refuse an address, the network it sits in, or a member. Idempotent on (value, scope)."""
        value = str(value or "").strip()
        if not value:
            raise ValueError("an address or member id is required")
        scope = self._block_scope(scope)
        now = time.time()
        with self._tx() as db:
            db.execute(
                "INSERT INTO blocked_client (value, scope, reason, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(value, scope) DO UPDATE SET "
                "reason = excluded.reason, created_by = excluded.created_by",
                (value, scope, reason or None, created_by or None, now))
            row = db.execute("SELECT * FROM blocked_client WHERE value = ? AND scope = ?",
                             (value, scope)).fetchone()
            return dict(row)

    def unblock_client(self, value: str, scope: str | None = None) -> int:
        value = str(value or "").strip()
        with self._tx() as db:
            if scope:
                cur = db.execute("DELETE FROM blocked_client WHERE value = ? AND scope = ?",
                                 (value, self._block_scope(scope)))
            else:
                cur = db.execute("DELETE FROM blocked_client WHERE value = ?", (value,))
            return cur.rowcount

    def list_blocked_clients(self) -> list[dict]:
        with self._tx() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM blocked_client ORDER BY created_at DESC")]

    def blocked_entry(self, ip: str | None, network: str | None,
                      user: str | None = None) -> dict | None:
        """The block that stops this caller, or None. Checked on every submission.

        Every key in one query: a caller is refused because its address is listed, its
        network is, or the member it says it is posting for is - and asking three times per
        request triples the cost of the common case where none is. Scopes are matched as well
        as values, so a member id that happens to read like an address blocks only a member.
        """
        clauses, args = [], []
        addrs = [k for k in (ip, network) if k]
        if addrs:
            clauses.append(f"(scope IN ('ip', 'network') AND value IN ({','.join('?' * len(addrs))}))")
            args += addrs
        if user and user != "anonymous":
            clauses.append("(scope = 'member' AND value = ?)")
            args.append(user)
        if not clauses:
            return None
        with self._tx() as db:
            row = db.execute(
                f"SELECT * FROM blocked_client WHERE {' OR '.join(clauses)} "
                "ORDER BY CASE scope WHEN 'member' THEN 0 WHEN 'ip' THEN 1 ELSE 2 END LIMIT 1",
                args).fetchone()
            return dict(row) if row else None

    def blocked_members(self) -> dict[str, dict]:
        """Every blocked member, by user id - for marking them on the log screen."""
        with self._tx() as db:
            return {r["value"]: dict(r) for r in db.execute(
                "SELECT * FROM blocked_client WHERE scope = 'member'")}

    def get_moderation_log_by_request_id(self, request_id: str) -> dict | None:
        with self._tx() as db:
            row = db.execute("SELECT * FROM moderation_log WHERE request_id = ?", (request_id,)).fetchone()
            if not row:
                return None
            item = dict(row)
            if item.get("raw_result"):
                try:
                    item["raw_result"] = json.loads(item["raw_result"])
                except Exception:
                    pass
            return item

    def clear_moderation_logs(self) -> int:
        with self._tx() as db:
            cursor = db.execute("DELETE FROM moderation_log")
            return cursor.rowcount
