from pathlib import Path

import psycopg
import pytest

from tests.conftest import SQL_DIR, pg

SQL = (SQL_DIR / "control.sql").read_text()


@pytest.fixture
def applied(sw):
    sw.execute(SQL)
    return sw


def test_applying_twice_changes_nothing_and_raises_nothing(sw):
    sw.execute(SQL)
    sw.execute("INSERT INTO serving.sync_state (table_name) VALUES ('t')")
    sw.execute(SQL)
    assert sw.execute("SELECT count(*) FROM serving.sync_state").fetchone() == (1,)
    tables = {r[0] for r in sw.execute("SELECT tablename FROM pg_tables WHERE schemaname='serving'").fetchall()}
    assert {"sync_state", "sync_runs", "sync_requests"} <= tables


def test_api_rw_rights_are_exactly_the_granted_ones(applied):
    with pg("api_rw") as api:
        assert api.execute("SELECT count(*) FROM serving.sync_state").fetchone() == (0,)
        assert api.execute("SELECT count(*) FROM serving.sync_runs").fetchone() == (0,)
        assert api.execute("SELECT count(*) FROM serving.sync_requests").fetchone() == (0,)
        api.execute("INSERT INTO serving.sync_requests (table_name, requested_by) VALUES ('t', gen_random_uuid())")
        for sql in ("UPDATE serving.sync_requests SET state='taken'", "DELETE FROM serving.sync_requests",
                    "INSERT INTO serving.sync_runs (table_name, trigger, started_at, outcome) VALUES ('t','admin',now(),'running')",
                    "UPDATE serving.sync_state SET consecutive_failures = 0", "INSERT INTO serving.sync_state (table_name) VALUES ('x')",
                    "DELETE FROM serving.sync_runs"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                api.execute(sql)


def test_api_rw_cannot_read_any_other_table_in_serving(applied):
    applied.execute("CREATE TABLE serving.some_copy (id int)")
    with pg("api_rw") as api:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api.execute("SELECT * FROM serving.some_copy")


def test_two_pending_inserts_are_allowed_by_the_database(applied):
    # no unique index: the API guards against duplicates, the worker merges them
    with pg("api_rw") as api:
        for _ in range(2):
            api.execute("INSERT INTO serving.sync_requests (table_name, requested_by) VALUES ('t', gen_random_uuid())")
    assert applied.execute("SELECT count(*) FROM serving.sync_requests WHERE state='pending'").fetchone() == (2,)


def test_agent_ro_reads_only_sync_state_among_the_control_tables(applied):
    applied.execute(SQL)  # a second apply must not give the rights back
    with pg("agent_ro") as agent:
        assert agent.execute("SELECT count(*) FROM serving.sync_state").fetchone() == (0,)
        for table in ("sync_runs", "sync_requests"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                agent.execute(f"SELECT * FROM serving.{table}")
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            agent.execute("UPDATE serving.sync_state SET consecutive_failures = 1")


def test_checks_reject_bad_values(applied):
    with pytest.raises(psycopg.errors.CheckViolation):
        applied.execute("INSERT INTO serving.sync_runs (table_name, trigger, started_at, outcome) VALUES ('t','startup',now(),'running')")
    with pytest.raises(psycopg.errors.CheckViolation):
        applied.execute("INSERT INTO serving.sync_runs (table_name, trigger, started_at, outcome, reason_code) VALUES ('t','admin',now(),'failed','made_up')")
    with pytest.raises(psycopg.errors.CheckViolation):
        applied.execute("INSERT INTO serving.sync_runs (table_name, trigger, started_at, outcome, error_text) VALUES ('t','admin',now(),'failed',%s)", ("x" * 501,))
