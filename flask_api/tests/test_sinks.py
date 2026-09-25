import pytest

from app import create_app, sinks
from app.db import connection_string
from app.sinks import FailSafeSink, LoggingSink, SqlServerSink, build_sink


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, *params):
        if self.conn.fail_with:
            raise self.conn.fail_with
        self.conn.executed.append(params)


class FakeConn:
    def __init__(self, fail_with=None):
        self.fail_with = fail_with
        self.executed, self.committed, self.closed = [], False, False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


RESULT = {"request_id": "r1", "content_type": "post", "status": "completed", "decision": "allow",
          "gate_version": 1, "label_scores": {"safe": 0.9}, "gate_matches": [], "content": "secret text"}


@pytest.fixture
def fake_conn(monkeypatch):
    holder = {}

    def connect(settings, fail_with=None):
        holder["conn"] = FakeConn(holder.get("fail_with"))
        return holder["conn"]

    monkeypatch.setattr(sinks, "get_connection", connect)
    return holder


def test_sql_sink_inserts_commits_closes(make_settings, fake_conn):
    SqlServerSink(make_settings()).emit(RESULT)
    conn = fake_conn["conn"]
    params = conn.executed[0]
    assert params[0] == "r1" and params[11] == "1"   # gate_version sent as text
    assert "secret text" not in params                # raw content is not stored
    assert conn.committed and conn.closed


def test_sql_sink_ignores_duplicate_request_id(make_settings, fake_conn):
    fake_conn["fail_with"] = Exception("23000", "[23000] Violation of PRIMARY KEY constraint (2627)")
    SqlServerSink(make_settings()).emit(RESULT)       # no exception
    assert fake_conn["conn"].closed and not fake_conn["conn"].committed


def test_sql_sink_raises_other_errors_and_closes(make_settings, fake_conn):
    fake_conn["fail_with"] = Exception("08001", "cannot connect")
    with pytest.raises(Exception, match="cannot connect"):
        SqlServerSink(make_settings()).emit(RESULT)
    assert fake_conn["conn"].closed


def test_failsafe_sink_swallows_and_logs(caplog):
    class Boom:
        def emit(self, result):
            raise RuntimeError("db down")
    FailSafeSink(Boom()).emit(RESULT)
    record = next(r for r in caplog.records if "db_write_failed" in r.getMessage())
    assert "r1" in record.getMessage() and "secret text" not in record.getMessage()


def test_build_sink(make_settings):
    assert isinstance(build_sink(make_settings(result_sink="sql")), FailSafeSink)
    assert isinstance(build_sink(make_settings(result_sink="log")), LoggingSink)


def test_invalid_sink_setting(make_settings):
    with pytest.raises(ValueError):
        make_settings(result_sink="mongo")


def test_db_outage_does_not_break_moderation(make_settings, scorer):
    # No DB_SERVER configured -> every write fails; requests must still get decisions.
    c = create_app(make_settings(result_sink="sql"), scorer=scorer).test_client()
    for content, decision in [("Steel pipes for sale", "allow"), ("badword", "reject")]:
        r = c.post("/v1/moderate", json={"content": content, "content_type": "post"})
        assert r.status_code == 200 and r.get_json()["decision"] == decision


def test_connection_string_windows_auth(make_settings):
    cs = connection_string(make_settings(db_server="host", db_name="bonc"))
    assert "Trusted_Connection=yes" in cs and "UID" not in cs
    assert "DRIVER={ODBC Driver 18 for SQL Server}" in cs


def test_connection_string_sql_auth_escapes_password(make_settings):
    cs = connection_string(make_settings(db_server="host", db_name="bonc", db_user="svc",
                                         db_password="p;w}d", db_driver="SQL Server",
                                         db_trust_server_certificate=False))
    assert "UID={svc}" in cs and "PWD={p;w}}d}" in cs
    assert "Trusted_Connection" not in cs
    assert "DRIVER={SQL Server}" in cs and "TrustServerCertificate=no" in cs


def test_connection_string_requires_server(make_settings):
    with pytest.raises(RuntimeError, match="DB_SERVER"):
        connection_string(make_settings())
