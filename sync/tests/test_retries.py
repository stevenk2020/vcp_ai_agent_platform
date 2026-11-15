"""US5: source problems degrade safely: retries, typed failures, the failing state, and recovery."""

import dataclasses
import pytest

from sync import definition as df
from sync import scheduler as sch
from sync.runner import Runner
from sync.scrub import Scrubber
from sync.source import SourceLoginFailed, SourceReadTimeout, SourceUnreachable
from sync.state import derive_state
from sync.store import Store, SwapFailed
from tests.conftest import TABLES_DIR
from tests.fakes import FakeClock, FakeSource, make_rows


@pytest.fixture
def d():
    return df.load(TABLES_DIR / "sales_contract.toml")


class Env:
    def __init__(self, settings, d, **override):
        self.settings = dataclasses.replace(settings, **override)
        self.d = d
        self.source = FakeSource(d, make_rows(d, 10))
        self.store = Store(self.settings).connect()
        self.store.apply_control()
        self.sleeps: list[float] = []
        self.clock = FakeClock()
        self.runner = Runner(self.settings, self.store, {d.copy: d}, self.source, clock=self.clock,
                             sleep=self.sleeps.append, scrubber=Scrubber(self.settings.secret_values))

    def run(self):
        return self.runner.run_once("sales_contract", "schedule")


@pytest.fixture
def env(settings, d):
    e = Env(settings, d, sync_retry_base_seconds=15)
    yield e
    e.store.close()


def test_two_connection_errors_then_success_waits_15_then_45(env):
    env.source.connect_failures = [SourceUnreachable("down"), SourceUnreachable("down")]
    assert env.run().outcome == "succeeded"
    assert env.sleeps == [15, 45] and env.source.enter_calls == 3
    assert env.store.state("sales_contract")["consecutive_failures"] == 0


def test_three_connection_errors_fail_after_exactly_three_attempts_and_keep_the_copy(env):
    assert env.run().outcome == "succeeded"
    before = env.store.conn.execute("SELECT count(*) AS n FROM serving.sales_contract").fetchone()["n"]
    state_before = env.store.state("sales_contract")
    env.source.enter_calls = 0
    env.source.connect_failures = [SourceUnreachable("down")] * 3
    r = env.run()
    assert (r.outcome, r.reason_code) == ("failed", "source_unreachable") and env.source.enter_calls == 3
    assert env.sleeps == [15, 45]                                    # no wait after the last attempt
    assert env.store.conn.execute("SELECT count(*) AS n FROM serving.sales_contract").fetchone()["n"] == before
    st = env.store.state("sales_contract")
    assert st["consecutive_failures"] == 1 and st["data_as_of"] == state_before["data_as_of"]   # a failure never moves data_as_of
    assert (st["last_outcome"], st["last_reason"]) == ("failed", "source_unreachable")


def test_a_read_timeout_is_recorded_without_retry_and_leaves_no_staging(env):
    env.source.hang = True
    r = env.run()
    assert (r.outcome, r.reason_code) == ("failed", "read_timeout")
    assert env.source.enter_calls == 1 and env.source.read_calls == 1 and env.sleeps == []
    assert not env.store.table_exists(env.d.staging)
    other = Store(env.settings).connect()
    try:
        assert other.try_lock("sales_contract")                      # lock released
    finally:
        other.close()


def test_a_login_failure_is_recorded_without_retry(env):
    env.source.connect_failures = [SourceLoginFailed("Login failed for user")]
    r = env.run()
    assert (r.outcome, r.reason_code) == ("failed", "login_failed") and env.source.enter_calls == 1 and env.sleeps == []


def test_an_unexpected_error_records_only_the_class_name_and_a_scrubbed_message(env):
    env.source.read_failures = [RuntimeError("boom with Sup3r-Secret-PW inside")]
    r = env.run()
    assert (r.outcome, r.reason_code) == ("failed", "unexpected")
    assert "RuntimeError" in r.message and "Sup3r-Secret-PW" not in r.message and "***" in r.message


def test_a_swap_that_cannot_lock_is_recorded_as_swap_failed(env, monkeypatch):
    def boom(*a, **k):
        raise SwapFailed("no lock")

    monkeypatch.setattr(env.store, "swap", boom)
    r = env.run()
    assert (r.outcome, r.reason_code) == ("failed", "swap_failed") and not env.store.table_exists(env.d.staging)


def test_three_failures_make_it_failing_and_a_success_heals_it(env):
    assert env.run().outcome == "succeeded"
    now = env.clock()                                                 # the same clock that stamped data_as_of
    env.source.hang = True
    for expected in (1, 2, 3):
        assert env.run().outcome == "failed"
        st = env.store.state("sales_contract")
        assert st["consecutive_failures"] == expected
        assert derive_state(st, now)["state"] == ("failing" if expected >= 3 else "healthy")
    env.source.hang = False
    assert env.run().outcome == "succeeded"
    st = env.store.state("sales_contract")
    assert st["consecutive_failures"] == 0 and derive_state(st, now)["state"] == "healthy"


def test_the_scheduler_never_gives_up_and_the_first_tick_after_the_outage_succeeds(settings, d, tmp_path):
    """SC-007: six intervals of failures, then the source returns: the next tick heals it with no operator action."""
    s = dataclasses.replace(settings, sync_retry_base_seconds=1)
    source = FakeSource(d, make_rows(d, 10))
    store, poll = Store(s).connect(), Store(s).connect()
    clock = FakeClock()
    runner = Runner(s, store, {d.copy: d}, source, clock=clock, sleep=lambda x: None, scrubber=Scrubber(s.secret_values))
    try:
        svc = sch.Service(s, [d], store=store, poll_store=poll, runner=runner, clock=clock, heartbeat_file=str(tmp_path / "hb"))
        svc.startup()
        source.hang = True
        for _ in range(6):
            clock.advance(minutes=10)
            svc.tick("sales_contract")
        assert store.conn.execute("SELECT count(*) AS n FROM serving.sync_runs WHERE outcome = 'failed'").fetchone()["n"] == 6
        assert source.read_calls == 6                                  # every tick really tried the source
        source.hang = False
        clock.advance(minutes=10)
        svc.tick("sales_contract")
        st = store.state("sales_contract")
        assert (st["last_outcome"], st["consecutive_failures"]) == ("succeeded", 0)
    finally:
        store.close(); poll.close()
