"""FR-015: another table is added by a definition file alone; tables are independent."""

import shutil

import pytest

from sync import definition as df
from sync import scheduler as sch
from sync.runner import Runner
from sync.scrub import Scrubber
from sync.store import Store
from tests.conftest import FIXTURES, TABLES_DIR
from tests.fakes import FakeClock, FakeSource, make_rows


@pytest.fixture
def both(tmp_path):
    shutil.copy(TABLES_DIR / "sales_contract.toml", tmp_path / "sales_contract.toml")
    shutil.copy(FIXTURES / "toy.toml", tmp_path / "toy.toml")
    return {d.copy: d for d in df.load_all(tmp_path)}


def toy_rows(n, tag="t"):
    return [{"id": f"{tag}{i}", "qty": float(i), "amount": 10.5 * i, "secret_note": "NEVER COPY"} for i in range(n)]


def make(settings, both, sources):
    store = Store(settings).connect()
    store.apply_control()
    runner = Runner(settings, store, both, lambda d: sources[d.copy], clock=FakeClock(), sleep=lambda s: None,
                    scrubber=Scrubber(settings.secret_values))
    return store, runner


def test_definitions_are_discovered_and_one_job_per_table_is_registered(settings, both, tmp_path):
    assert sorted(both) == ["sales_contract", "toy"]
    import dataclasses

    s = dataclasses.replace(settings, tables_dir=str(tmp_path))
    store, poll = Store(s).connect(), Store(s).connect()
    try:
        svc = sch.Service(s, list(both.values()), store=store, poll_store=poll, heartbeat_file=str(tmp_path / "hb"))
        ids = {j.id for j in svc.build_scheduler().get_jobs()}
        assert {"sync:sales_contract", "sync:toy", "poll", "heartbeat"} == ids
    finally:
        store.close(); poll.close()


def test_the_second_table_is_copied_without_any_change_to_the_worker(settings, both, sw):
    sources = {"sales_contract": FakeSource(both["sales_contract"], make_rows(both["sales_contract"], 4)),
               "toy": FakeSource(both["toy"], toy_rows(5))}
    store, runner = make(settings, both, sources)
    try:
        assert runner.run_once("toy", "schedule").outcome == "succeeded"
        assert sw.execute("SELECT count(*), sum(qty), sum(amount) FROM serving.toy").fetchone()[0:2] == (5, 10)
        columns = [r[0] for r in sw.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='serving' AND table_name='toy' ORDER BY ordinal_position").fetchall()]
        assert columns == ["id", "qty", "amount"]                       # the excluded column is not copied
        assert all("secret_note" not in s for s in sources["toy"].statements)
        assert {r["table_name"] for r in store.conn.execute("SELECT table_name FROM serving.sync_state").fetchall()} == {"toy"}
    finally:
        store.close()


def test_tables_have_separate_locks_and_state(settings, both):
    sources = {"sales_contract": FakeSource(both["sales_contract"], make_rows(both["sales_contract"], 4)),
               "toy": FakeSource(both["toy"], toy_rows(5))}
    store, runner = make(settings, both, sources)
    other = Store(settings).connect()
    try:
        assert other.try_lock("sales_contract")                       # a run of sales_contract is "active" elsewhere
        assert runner.run_once("sales_contract", "schedule").outcome == "skipped_busy"
        assert runner.run_once("toy", "schedule").outcome == "succeeded"   # toy is not blocked by it
        assert store.state("toy")["copied_rows"] == 5
        assert store.state("sales_contract") is None            # the busy run never touched its state
    finally:
        other.close(); store.close()


def test_a_refusal_on_one_table_leaves_the_other_untouched(settings, both, sw):
    sources = {"sales_contract": FakeSource(both["sales_contract"], make_rows(both["sales_contract"], 4)),
               "toy": FakeSource(both["toy"], toy_rows(5))}
    store, runner = make(settings, both, sources)
    try:
        assert runner.run_once("sales_contract", "schedule").outcome == "succeeded"
        assert runner.run_once("toy", "schedule").outcome == "succeeded"
        sources["toy"].rows = toy_rows(3) + toy_rows(1, "t")          # a repeated key: refused
        result = runner.run_once("toy", "schedule")
        assert (result.outcome, result.reason_code) == ("refused", "duplicate_key")
        assert sw.execute("SELECT count(*) FROM serving.toy").fetchone() == (5,)
        assert sw.execute("SELECT count(*) FROM serving.sales_contract").fetchone() == (4,)
        assert store.state("sales_contract")["consecutive_failures"] == 0 and store.state("toy")["consecutive_failures"] == 1
    finally:
        store.close()
