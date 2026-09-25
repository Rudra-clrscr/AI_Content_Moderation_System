"""SQL Server connection helper (Intern 3 data layer).

pyodbc is imported lazily so the API still starts on machines without an
ODBC driver when the SQL sink isn't used. pyodbc enables ODBC connection
pooling by default, so opening a connection per write reuses pooled ones.
"""
from __future__ import annotations

from app.config import Settings


def _odbc_value(value: str) -> str:
    """Brace-quote a connection-string value so ; = { } in passwords are safe."""
    return "{" + value.replace("}", "}}") + "}"


def connection_string(settings: Settings) -> str:
    if not settings.db_server or not settings.db_name:
        raise RuntimeError("Database configuration is incomplete (DB_SERVER and DB_NAME are required)")

    parts = [
        f"DRIVER={_odbc_value(settings.db_driver)}",
        f"SERVER={_odbc_value(settings.db_server)}",
        f"DATABASE={_odbc_value(settings.db_name)}",
    ]
    if settings.db_user:
        parts += [f"UID={_odbc_value(settings.db_user)}", f"PWD={_odbc_value(settings.db_password or '')}"]
    else:
        parts.append("Trusted_Connection=yes")
    parts.append(f"TrustServerCertificate={'yes' if settings.db_trust_server_certificate else 'no'}")
    return ";".join(parts) + ";"


def get_connection(settings: Settings):
    import pyodbc

    return pyodbc.connect(connection_string(settings), timeout=settings.db_timeout_seconds)
