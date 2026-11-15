"""US1 / SC-003: readers never see an empty or half-loaded copy while it is replaced."""

import threading

import pytest

from sync import definition as df
from sync.runner import Runner
from sync.scrub import Scrubber
from sync.store import Store
from tests.conftest import TABLES_DIR, pg
from tests.fakes import FakeClock, FakeSource, make_rows


@pytest.fixture
def d():
    return df.load(TABLES_DIR / "sales_contract.toml")


def build(settings, d, source):
    store = Store(settings).connect()
    store.apply_control()
    return store, Runner(settings, store, {d.copy: d}, source, clock=FakeClock(), sleep=lambda s: None,
                         scrubber=Scrubber(settings.secret_values))


def test_reader_sees_only_complete_states_during_replacements(settings, d):
    import dataclasses

    settings = dataclasses.replace(settings, guard_min_rows=1)
    old_total, new_total, third_total = 35_000, 35_000, 20_000
    source = FakeSource(d, make_rows(d, old_total, "a", sparse=True))
    store, runner = build(settings, d, source)
    try:
        assert runner.run_once("sales_contract", "schedule").outcome == "succeeded"
        seen: list[int] = []
        sums: list[tuple] = []
        stop = threading.Event()
        errors: list[Exception] = []

        def reader():
            try:
                with pg("agent_ro") as agent:
                    while not stop.is_set() or len(seen) < 1000:
                        seen.append(agent.execute("SELECT count(*) FROM serving.sales_contract").fetchone()[0])
                        if len(seen) % 100 == 0:
                            # data and the "data as of" counts agree when read in one statement
                            sums.append(agent.execute(
                                "SELECT (SELECT count(*) FROM serving.sales_contract), copied_rows FROM serving.sync_state").fetchone())
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t = threading.Thread(target=reader)
        t.start()
        source.rows = make_rows(d, new_total, "b", sparse=True)
        assert runner.run_once("sales_contract", "schedule").outcome == "succeeded"
        source.rows = make_rows(d, third_total, "c", sparse=True)
        assert runner.run_once("sales_contract", "schedule").outcome == "succeeded"
        stop.set()
        t.join(60)
        assert not errors, errors
        assert len(seen) >= 1000
        assert set(seen) <= {old_total, new_total, third_total}, sorted(set(seen) - {old_total, new_total, third_total})
        assert 0 not in seen
        # the visible count and the recorded count never disagree in one statement
        assert all(a == b or b is None for a, b in sums), sums
    finally:
        store.close()
