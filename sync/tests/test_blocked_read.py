"""US1 scenario 4: the source is still committing; the run waits, readers keep the old complete table."""

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


def test_the_run_waits_for_the_source_then_reads_the_new_complete_state(settings, d):
    source = FakeSource(d, make_rows(d, 5, "old"))
    store = Store(settings).connect()
    store.apply_control()
    runner = Runner(settings, store, {d.copy: d}, source, clock=FakeClock(), sleep=lambda s: None,
                    scrubber=Scrubber(settings.secret_values))
    try:
        assert runner.run_once("sales_contract", "schedule").outcome == "succeeded"
        source.rows = make_rows(d, 8, "new")
        source.block = threading.Event()
        out = []
        t = threading.Thread(target=lambda: out.append(runner.run_once("sales_contract", "schedule")))
        t.start()
        assert source.in_read.wait(10)
        with pg("agent_ro") as agent:                                    # while the run is waiting
            assert agent.execute("SELECT count(*) FROM serving.sales_contract").fetchone() == (5,)
        assert t.is_alive()
        source.block.set()                                               # the source commits
        t.join(30)
        assert out[0].outcome == "succeeded" and out[0].copied_rows == 8
        with pg("agent_ro") as agent:
            assert agent.execute("SELECT count(*) FROM serving.sales_contract").fetchone() == (8,)
    finally:
        store.close()
