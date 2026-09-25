import pyodbc

from app.config import load_settings


def get_connection():
    settings = load_settings()

    if not settings.db_server or not settings.db_name:
        raise RuntimeError("Database configuration is incomplete")

    connection_string = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={settings.db_server};"
        f"DATABASE={settings.db_name};"
        "Trusted_Connection=yes;"
        "TrustServerCertificate=yes;"
    )

    return pyodbc.connect(connection_string)