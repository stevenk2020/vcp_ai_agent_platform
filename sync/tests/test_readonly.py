"""The worker only reads. Everything the real SqlServerSource executes is checked against the allowed shapes."""

import re

import pytest

from sync import definition as df
from sync import source as src
from tests.conftest import TABLES_DIR


class StubCursor:
    def __init__(self, log, results):
        self.log, self.results, self.last = log, results, None

    def execute(self, sql, params=None):
        self.log.append(sql)
        self.last = sql
        if self.results.get("raise"):
            raise self.results["raise"]

    def fetchone(self):
        return self.results.get(self._key(), [None])[0] if self.results.get(self._key()) else None

    def fetchall(self):
        return self.results.get(self._key(), [])

    def fetchmany(self, n):
        rows = self.results.setdefault("rows", [])
        out, self.results["rows"] = rows[:n], rows[n:]
        return out

    def _key(self):
        s = self.last or ""
        return "account" if "IS_SRVROLEMEMBER" in s else "columns" if "INFORMATION_SCHEMA" in s else "snap" if "sys.databases" in s else "rows"


class StubConn:
    def __init__(self, results):
        self.statements, self.results, self.kwargs, self.closed = [], results, {}, False

    def cursor(self):
        return StubCursor(self.statements, self.results)

    def close(self):
        self.closed = True


def make(settings, results=None, **overrides):
    import dataclasses

    s = dataclasses.replace(settings, **overrides)
    conn = StubConn(results or {})
    seen = {}

    def connect(**kwargs):
        seen.update(kwargs)
        return conn

    recorded = []
    return src.SqlServerSource(s, "dbo.sales_contract", recorder=recorded.append, connect=connect), conn, seen, recorded


def full_run(source, definition):
    with source as s:
        s.account_info()
        s.columns()
        s.snapshot_enabled()
        list(s.read(definition.select_sql()))


@pytest.fixture(scope="module")
def d():
    return df.load(TABLES_DIR / "sales_contract.toml")


def results():
    return {"account": [(0, 0, 0, 0, 0)], "columns": [("entity", "nvarchar")], "snap": [(0,)], "rows": [("vcp",)]}


def test_every_statement_has_an_allowed_shape_and_nothing_else_is_sent(settings, d):
    source, conn, _, recorded = make(settings, results())
    full_run(source, d)
    assert conn.statements == recorded and len(recorded) == 8
    for sql in recorded:
        assert any(p.match(sql) for p in src.ALLOWED_STATEMENTS), sql
        # look at keywords only: drop the permission probes (they name INSERT/UPDATE/DELETE as arguments)
        # and the bracketed identifiers (a column may be called created_at)
        words = re.sub(r"HAS_PERMS_BY_NAME\([^)]*\)|\[\w+\]", " ", sql.upper())
        for bad in ("NOLOCK", "READUNCOMMITTED", "INSERT", "UPDATE", "DELETE", "EXEC", "DROP", "ALTER", "CREATE", "MERGE", "TRUNCATE", "INTO"):
            assert not re.search(r"\b" + bad + r"\b", words), (bad, sql[:80])
        assert ";" not in sql and "employee_" not in sql.lower()


def test_lock_timeout_equals_the_read_limit_and_isolation_is_read_committed(settings, d):
    source, conn, seen, _ = make(settings, results(), source_read_timeout_seconds=240)
    full_run(source, d)
    assert "SET LOCK_TIMEOUT 240000" in conn.statements
    assert "SET TRANSACTION ISOLATION LEVEL READ COMMITTED" in conn.statements
    assert not any("SNAPSHOT" in s for s in conn.statements)
    assert seen["timeout"] == 240 and seen["autocommit"] is True


def test_snapshot_mode_adds_exactly_the_snapshot_statement(settings, d):
    source, conn, _, _ = make(settings, results(), source_snapshot_read="on")
    full_run(source, d)
    assert "SET TRANSACTION ISOLATION LEVEL SNAPSHOT" in conn.statements
    assert "SET TRANSACTION ISOLATION LEVEL READ COMMITTED" not in conn.statements
    assert len(conn.statements) == 8


def test_encryption_is_required_by_default_and_other_values_warn(settings, caplog):
    source, _, seen, _ = make(settings, results())
    with source:
        pass
    assert seen["encryption"] == "require" and not [r for r in caplog.records if r.levelname == "WARNING"]
    source, _, seen, _ = make(settings, results(), source_encrypt="off")
    with caplog.at_level("WARNING"), source:
        pass
    assert seen["encryption"] == "off" and any("not 'require'" in r.getMessage() for r in caplog.records)


def test_account_flags(settings):
    for row, expected in (((1, 0, 0, 0, 0), "sysadmin"), ((0, 1, 0, 0, 0), "db_owner"), ((0, 0, 0, 1, 0), "write"),
                          ((0, 0, 0, 0, 0), None), ((None, None, None, None, None), None)):
        source, *_ = make(settings, {"account": [row]})
        with source as s:
            info = s.account_info()
        assert (info.privileged is None) == (expected is None), row
        if expected:
            assert expected in info.privileged


@pytest.mark.parametrize("exc,expected", [
    (Exception(18456, b"Login failed for user 'x'"), src.SourceLoginFailed),
    (Exception(1222, b"Lock request time out period exceeded"), src.SourceReadTimeout),
    (Exception(20003, b"Adaptive Server connection timed out"), src.SourceReadTimeout),
    (Exception(20009, b"Unable to connect: Adaptive Server is unavailable"), src.SourceUnreachable),
    (Exception(20002, b"Adaptive Server connection failed"), src.SourceUnreachable),
    (Exception(515, b"Cannot insert the value NULL"), src.SourceError),
])
def test_driver_errors_map_to_typed_errors(exc, expected):
    mapped = src.map_error(exc)
    assert type(mapped) is expected


def test_connect_errors_are_mapped(settings):
    source = src.SqlServerSource(settings, "dbo.sales_contract", connect=lambda **k: (_ for _ in ()).throw(Exception(20009, b"Unable to connect")))
    with pytest.raises(src.SourceUnreachable):
        with source:
            pass


def test_read_errors_are_mapped(settings, d):
    source, conn, *_ = make(settings, {"raise": None})
    with source as s:
        conn.results["raise"] = Exception(1222, b"Lock request time out")
        with pytest.raises(src.SourceReadTimeout):
            list(s.read(d.select_sql()))
