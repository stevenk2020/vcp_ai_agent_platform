"""US4: never two runs at once; restarts leave no stuck lock."""

import threading

import pytest

from sync import definition as df
from sync.runner import Runner
from sync.scrub import Scrubber
from sync.store import Store
from tests.conftest import TABLES_DIR
from tests.fakes import FakeClock, FakeSource, make_rows


@pytest.fixture
def d():
    return df.load(TABLES_DIR / "sales_contract.toml")


def runner_for(settings, d, source):
    store = Store(settings).connect()
    store.apply_control()
    return store, Runner(settings, store, {d.copy: d}, source, clock=FakeClock(), sleep=lambda s: None,
                         scrubber=Scrubber(settings.secret_values))


def test_two_simultaneous_runs_read_the_source_once(settings, d):
    source = FakeSource(d, make_rows(d, 10))
    source.block = threading.Event()
    s1, r1 = runner_for(settings, d, source)
    s2, r2 = runner_for(settings, d, source)
    try:
        results = []
        t1 = threading.Thread(target=lambda: results.append(r1.run_once("sales_contract", "schedule")))
        t1.start()
        assert source.in_read.wait(10)
        second = r2.run_once("sales_contract", "admin")          # a request while the first is still reading
        assert second.outcome == "skipped_busy" and second.run_id is None
        source.block.set()
        t1.join(30)
        assert results[0].outcome == "succeeded" and source.read_calls == 1
        assert s1.conn.execute("SELECT count(*) AS n FROM serving.sync_runs").fetchone()["n"] == 1   # no row for the skipped one
        assert r2.run_once("sales_contract", "admin").outcome == "succeeded"                          # free again
        assert source.read_calls == 2
    finally:
        s1.close(); s2.close()


def test_a_leftover_running_row_is_failed_at_start_and_the_next_run_works(settings, d, tmp_path):
    from sync import scheduler as sch

    source = FakeSource(d, make_rows(d, 10))
    store, runner = runner_for(settings, d, source)
    poll = Store(settings).connect()
    try:
        store.register(d)
        stale = store.start_run(d.copy, "schedule", None, FakeClock()())
        service = sch.Service(settings, [d], store=store, poll_store=poll, runner=runner, heartbeat_file=str(tmp_path / "hb"))
        service.startup()
        row = store.conn.execute("SELECT outcome, reason_code FROM serving.sync_runs WHERE id = %s", (stale,)).fetchone()
        assert (row["outcome"], row["reason_code"]) == ("failed", "interrupted")
        assert runner.run_once("sales_contract", "schedule").outcome == "succeeded"
    finally:
        store.close(); poll.close()


def test_the_lock_of_a_killed_connection_is_released(settings, d):
    source = FakeSource(d, make_rows(d, 10))
    holder = Store(settings).connect()
    store, runner = runner_for(settings, d, source)
    try:
        assert holder.try_lock(d.copy)
        assert runner.run_once("sales_contract", "schedule").outcome == "skipped_busy"
        holder._lock_conn.close()                                  # the worker holding the lock dies
        assert runner.run_once("sales_contract", "schedule").outcome == "succeeded"
    finally:
        holder.close(); store.close()


def test_the_lock_is_released_after_every_outcome(settings, d):
    source = FakeSource(d, make_rows(d, 10))
    store, runner = runner_for(settings, d, source)
    other = Store(settings).connect()
    try:
        runner.run_once("sales_contract", "schedule")
        source.rows = []
        runner.run_once("sales_contract", "schedule")                # refused
        source.rows = make_rows(d, 10)
        source.read_failures = [RuntimeError("boom")]
        runner.run_once("sales_contract", "schedule")                # failed (unexpected)
        assert other.try_lock(d.copy)
    finally:
        other.close(); store.close()
