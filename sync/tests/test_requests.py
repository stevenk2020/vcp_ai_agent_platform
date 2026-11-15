"""US4: admin requests are picked up by the poll, once, in order, and never lost or run twice."""

import threading
import time
import uuid

import pytest

from sync import definition as df
from sync import scheduler as sch
from sync.runner import Runner
from sync.scrub import Scrubber
from sync.store import Store
from tests.conftest import TABLES_DIR
from tests.fakes import FakeClock, FakeSource, make_rows


@pytest.fixture
def d():
    return df.load(TABLES_DIR / "sales_contract.toml")


@pytest.fixture
def svc(settings, d, tmp_path):
    source = FakeSource(d, make_rows(d, 10))
    store, poll = Store(settings).connect(), Store(settings).connect()
    runner = Runner(settings, store, {d.copy: d}, source, clock=FakeClock(), sleep=lambda s: None, scrubber=Scrubber(settings.secret_values))
    service = sch.Service(settings, [d], store=store, poll_store=poll, runner=runner, heartbeat_file=str(tmp_path / "hb"))
    service.startup()
    yield service, source, store
    store.close(); poll.close()


def request(store, table="sales_contract", person=None, age_minutes=0):
    person = person or uuid.uuid4()
    return store.conn.execute(
        "INSERT INTO serving.sync_requests (table_name, requested_by, requested_at) VALUES (%s, %s, now() - make_interval(mins => %s)) RETURNING id",
        (table, person, age_minutes)).fetchone()["id"], person


def states(store):
    return [(r["id"], r["state"], r["run_id"]) for r in store.conn.execute("SELECT * FROM serving.sync_requests ORDER BY id").fetchall()]


def test_a_pending_request_is_taken_and_produces_an_admin_run(svc):
    service, source, store = svc
    rid, person = request(store)
    service.poll_requests()
    run = store.conn.execute("SELECT * FROM serving.sync_runs").fetchone()
    assert run["trigger"] == "admin" and run["requested_by"] == person and run["outcome"] == "succeeded"
    assert states(store) == [(rid, "taken", run["id"])] and source.read_calls == 1


def test_a_double_click_causes_one_run_and_cancels_the_duplicate(svc):
    service, source, store = svc
    first, _ = request(store)
    second, _ = request(store)
    service.poll_requests()
    assert source.read_calls == 1
    assert {s for _, s, _ in states(store)} == {"taken", "cancelled"}
    service.poll_requests()
    assert source.read_calls == 1                                  # nothing left to do


def test_a_request_older_than_the_ttl_is_cancelled_and_never_run(svc):
    service, source, store = svc
    rid, _ = request(store, age_minutes=16)
    service.poll_requests()
    assert states(store) == [(rid, "cancelled", None)] and source.read_calls == 0


def test_a_request_for_an_unregistered_table_is_cancelled(svc):
    service, source, store = svc
    rid, _ = request(store, table="nope")
    service.poll_requests()
    assert states(store) == [(rid, "cancelled", None)] and source.read_calls == 0


def test_a_request_arriving_during_a_scheduled_run_runs_after_it_and_is_not_lost(svc):
    service, source, store = svc
    source.block = threading.Event()
    ticker = threading.Thread(target=lambda: service.tick("sales_contract"))
    ticker.start()
    assert source.in_read.wait(10)
    probe = Store(service.s).connect()          # its own connection: the runner's connection is busy with the run
    try:
        rid, _ = request(probe)
        poller = threading.Thread(target=service.poll_requests)
        poller.start()
        time.sleep(1)
        assert states(probe)[0][1] == "pending" and source.read_calls == 1       # waiting, not run in parallel
        source.block.set()
        ticker.join(30); poller.join(30)
        assert source.read_calls == 2                                            # the request ran after the tick finished
        runs = probe.conn.execute("SELECT trigger FROM serving.sync_runs ORDER BY id").fetchall()
        assert [r["trigger"] for r in runs] == ["schedule", "admin"]
        assert states(probe)[0][1] == "taken"
    finally:
        probe.close()


def test_a_request_is_put_back_when_another_worker_holds_the_lock(svc, settings, d):
    service, source, store = svc
    other = Store(settings).connect()
    try:
        assert other.try_lock(d.copy)
        rid, _ = request(store)
        service.poll_requests()
        assert states(store) == [(rid, "pending", None)] and source.read_calls == 0
        other.unlock(d.copy)
        service.poll_requests()
        assert states(store)[0][1] == "taken" and source.read_calls == 1
    finally:
        other.close()


def test_without_a_source_a_request_records_one_not_configured_run(settings, d, tmp_path):
    import dataclasses

    s = dataclasses.replace(settings, source_host="", source_user="", source_password="", source_database="")
    store, poll = Store(s).connect(), Store(s).connect()
    try:
        service = sch.Service(s, [d], store=store, poll_store=poll, heartbeat_file=str(tmp_path / "hb"))
        service.startup()
        request(store)
        service.poll_requests()
        runs = store.conn.execute("SELECT outcome, reason_code, error_text FROM serving.sync_runs").fetchall()
        assert len(runs) == 1 and (runs[0]["outcome"], runs[0]["reason_code"]) == ("failed", "source_not_configured")
        assert "not configured" in runs[0]["error_text"]
        service.tick("sales_contract")                                       # the scheduled path stays quiet
        assert store.conn.execute("SELECT count(*) AS n FROM serving.sync_runs").fetchone()["n"] == 1
    finally:
        store.close(); poll.close()
