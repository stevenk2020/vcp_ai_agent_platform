import threading
import time
from datetime import datetime, timezone

import psycopg
import pytest

from sync import definition as df
from sync.store import Store, SwapFailed
from tests.conftest import TABLES_DIR, pg


@pytest.fixture
def d():
    return df.load(TABLES_DIR / "sales_contract.toml")


@pytest.fixture
def store(settings):
    st = Store(settings).connect()
    st.apply_control()
    yield st
    st.close()


NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)


def fill(store, d, n, tag="a"):
    """A staging table with n rows whose key columns are set and everything else null."""
    store.create_staging(d)
    idx = {c: i for i, c in enumerate(d.column_names)}
    rows = []
    for i in range(n):
        row = [None] * len(d.columns)
        row[idx["entity"]], row[idx["row_id"]], row[idx["item_name"]] = "vcp", f"{tag}{i}", tag
        rows.append(row)
    assert store.copy_rows(d, rows) == n
    store.finish_staging(d)


def noop(cur):
    return None


def index_names(sw, d):
    return sorted(r[0] for r in sw.execute("SELECT indexname FROM pg_indexes WHERE schemaname='serving' AND tablename=%s", (d.copy,)).fetchall())


def test_swap_replaces_the_table_and_agent_ro_reads_it_at_once(store, d, sw):
    store.ensure_table(d)
    assert store.row_count(d.copy) == 0
    fill(store, d, 1, "old")
    store.swap(d, noop, noop)
    fill(store, d, 3, "new")
    store.swap(d, noop, noop)
    with pg("agent_ro") as agent:
        assert agent.execute("SELECT count(*), min(item_name) FROM serving.sales_contract").fetchone() == (3, "new")
    assert not store.table_exists(d.staging) and not store.table_exists(d.old)


def test_index_and_key_names_are_final_after_two_swaps(store, d, sw):
    expected = sorted([d.pkey] + [i.name for i in d.indexes])
    store.ensure_table(d)
    for tag in ("a", "b"):
        fill(store, d, 2, tag)
        store.swap(d, noop, noop)
        assert index_names(sw, d) == expected
    assert not [n for n in index_names(sw, d) if "__new" in n]


def test_swap_works_without_a_live_table_yet(store, d):
    fill(store, d, 2)
    store.swap(d, noop, noop)
    assert store.row_count(d.copy) == 2


def test_state_and_run_updates_are_in_the_same_transaction(store, d):
    store.register(d)
    run_id = store.start_run(d.copy, "schedule", None, NOW)
    fill(store, d, 2)

    def state(cur):
        store.record_success(cur, d.copy, run_id, NOW, 2, 2)

    def run(cur):
        store.finish_run(run_id, "succeeded", NOW, 5, source_rows=2, copied_rows=2, cur=cur)

    store.swap(d, state, run)
    st = store.state(d.copy)
    assert st["data_as_of"] == NOW and st["copied_rows"] == 2 and st["consecutive_failures"] == 0


def test_a_failing_state_update_rolls_the_swap_back(store, d):
    store.ensure_table(d)
    fill(store, d, 1, "old")
    store.swap(d, noop, noop)
    fill(store, d, 4, "new")

    def boom(cur):
        raise RuntimeError("state update failed")

    with pytest.raises(RuntimeError):
        store.swap(d, boom, noop)
    assert store.row_count(d.copy) == 1 and store.table_exists(d.staging)  # old table intact, staging still there


def test_swap_that_cannot_get_the_lock_fails_and_leaves_the_old_table(store, d, settings):
    store.ensure_table(d)
    fill(store, d, 1, "old")
    store.swap(d, noop, noop)
    fill(store, d, 3, "new")
    reader = pg("sync_writer", autocommit=False)
    reader.execute("SELECT count(*) FROM serving.sales_contract")  # holds ACCESS SHARE until commit
    t0 = time.monotonic()
    try:
        with pytest.raises(SwapFailed):
            store.swap(d, noop, noop, lock_timeout_s=1, attempts=3)
    finally:
        reader.rollback()
        reader.close()
    assert 2.5 < time.monotonic() - t0 < 12
    assert store.row_count(d.copy) == 1
    store.drop_staging(d)
    assert not store.table_exists(d.staging) and not store.table_exists(d.old)


def test_swap_succeeds_once_the_reader_lets_go_within_the_retries(store, d):
    store.ensure_table(d)
    fill(store, d, 2)
    reader = pg("sync_writer", autocommit=False)
    reader.execute("SELECT count(*) FROM serving.sales_contract")
    threading.Timer(1.5, reader.rollback).start()
    store.swap(d, noop, noop, lock_timeout_s=1, attempts=3)
    assert store.row_count(d.copy) == 2
    reader.close()


def test_lock_is_exclusive_and_released_with_its_connection(settings):
    a, b = Store(settings).connect(), Store(settings).connect()
    try:
        assert a.try_lock("sales_contract") is True
        assert b.try_lock("sales_contract") is False
        assert b.try_lock("other_table") is True            # different table, different lock
        a._lock_conn.close()                                 # a crashed run: the connection goes away
        assert b.try_lock("sales_contract") is True
    finally:
        a.close(); b.close()


def test_unlock_frees_the_lock(settings):
    a, b = Store(settings).connect(), Store(settings).connect()
    try:
        assert a.try_lock("t") and not b.try_lock("t")
        a.unlock("t")
        assert b.try_lock("t")
    finally:
        a.close(); b.close()


def test_fail_running_runs_only_touches_running_rows(store, d):
    store.register(d)
    r1 = store.start_run(d.copy, "schedule", None, NOW)
    r2 = store.start_run(d.copy, "admin", None, NOW)
    store.finish_run(r2, "succeeded", NOW, 1)
    assert store.fail_running_runs(NOW) == 1
    rows = {r["id"]: r for r in store.conn.execute("SELECT * FROM serving.sync_runs").fetchall()}
    assert rows[r1]["outcome"] == "failed" and rows[r1]["reason_code"] == "interrupted"
    assert rows[r2]["outcome"] == "succeeded"


def test_prune_removes_old_finished_runs_only(store, d):
    store.register(d)
    old = store.start_run(d.copy, "schedule", None, datetime(2026, 1, 1, tzinfo=timezone.utc))
    store.finish_run(old, "succeeded", NOW, 1)
    running_old = store.start_run(d.copy, "schedule", None, datetime(2026, 1, 1, tzinfo=timezone.utc))
    fresh = store.start_run(d.copy, "schedule", None, datetime.now(timezone.utc))
    store.prune_runs(30)
    ids = {r["id"] for r in store.conn.execute("SELECT id FROM serving.sync_runs").fetchall()}
    assert ids == {running_old, fresh}
