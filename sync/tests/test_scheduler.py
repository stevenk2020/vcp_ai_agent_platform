import os
import time
from datetime import timedelta

import pytest

from sync import __main__ as cli
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


def service(settings, d, tmp_path, source=None, configured=True):
    import dataclasses

    s = settings if configured else dataclasses.replace(settings, source_host="", source_user="", source_password="", source_database="")
    store, poll = Store(s).connect(), Store(s).connect()
    runner = Runner(s, store, {d.copy: d}, source, clock=FakeClock(), sleep=lambda x: None, scrubber=Scrubber(s.secret_values))
    return sch.Service(s, [d], store=store, poll_store=poll, runner=runner, heartbeat_file=str(tmp_path / "hb")), store, poll


def test_jobs_are_registered_with_the_configured_intervals(settings, d, tmp_path):
    svc, store, poll = service(settings, d, tmp_path)
    try:
        jobs = {j.id: j for j in svc.build_scheduler().get_jobs()}
        assert set(jobs) == {"sync:sales_contract", "poll", "heartbeat"}
        refresh = jobs["sync:sales_contract"]
        assert refresh.trigger.interval == timedelta(minutes=10) and refresh.max_instances == 1 and refresh.coalesce is True
        assert jobs["poll"].trigger.interval == timedelta(seconds=5) and jobs["poll"].max_instances == 1
        assert jobs["heartbeat"].trigger.interval == timedelta(seconds=30)
    finally:
        store.close(); poll.close()


def test_the_heartbeat_is_touched_at_start_and_health_follows_its_age(settings, d, tmp_path):
    svc, store, poll = service(settings, d, tmp_path, FakeSource(d, make_rows(d, 3)))
    try:
        assert sch.health(svc.heartbeat_file) == 1               # no file yet
        svc.startup()
        assert os.path.exists(svc.heartbeat_file) and sch.health(svc.heartbeat_file) == 0
        assert sch.health(svc.heartbeat_file, now=time.time() + 89) == 0
        assert sch.health(svc.heartbeat_file, now=time.time() + 91) == 1
    finally:
        store.close(); poll.close()


def test_without_a_source_secret_the_service_starts_and_ticks_do_nothing(settings, d, tmp_path):
    svc, store, poll = service(settings, d, tmp_path, source=None, configured=False)
    try:
        svc.startup()
        svc.tick("sales_contract")
        assert store.conn.execute("SELECT count(*) AS n FROM serving.sync_runs").fetchone()["n"] == 0
        assert store.table_exists("sales_contract") and store.state("sales_contract")["data_as_of"] is None
    finally:
        store.close(); poll.close()


def test_a_tick_runs_a_sync_when_configured(settings, d, tmp_path):
    svc, store, poll = service(settings, d, tmp_path, FakeSource(d, make_rows(d, 4)))
    try:
        svc.startup()
        svc.tick("sales_contract")
        assert store.state("sales_contract")["copied_rows"] == 4
        assert store.conn.execute("SELECT trigger FROM serving.sync_runs").fetchone()["trigger"] == "schedule"
    finally:
        store.close(); poll.close()


def test_startup_marks_leftover_running_rows_as_interrupted(settings, d, tmp_path):
    svc, store, poll = service(settings, d, tmp_path)
    try:
        store.apply_control(); store.register(d)
        store.start_run(d.copy, "schedule", None, FakeClock()())
        svc.startup()
        r = store.conn.execute("SELECT outcome, reason_code FROM serving.sync_runs").fetchone()
        assert (r["outcome"], r["reason_code"]) == ("failed", "interrupted")
    finally:
        store.close(); poll.close()


def test_startup_leaves_running_rows_alone_while_another_worker_holds_the_lock(settings, d, tmp_path):
    svc, store, poll = service(settings, d, tmp_path)
    other = Store(settings).connect()
    try:
        store.apply_control(); store.register(d)
        store.start_run(d.copy, "schedule", None, FakeClock()())
        assert other.try_lock(d.copy)
        svc.startup()
        assert store.conn.execute("SELECT outcome FROM serving.sync_runs").fetchone()["outcome"] == "running"
    finally:
        other.close(); store.close(); poll.close()


def test_a_config_error_is_reported_without_a_trace_or_secret(tmp_path, capsys):
    (tmp_path / "sync_writer_password").write_text("pg-secret-123")
    (tmp_path / "source_sqlserver").write_text("SQL_SERVER_PASSWORD=Pw-Secret-9\nSQL_SERVER_HOST=h\nSQL_SERVER_USER=u\nSQL_SERVER_DATABASE=db\n")
    code = cli.main(["serve"], env={"SOURCE_READ_TIMEOUT_SECONDS": "10"}, secrets_dir=tmp_path)
    err = capsys.readouterr().err
    assert code == 1 and "SOURCE_READ_TIMEOUT_SECONDS" in err and "Traceback" not in err
    assert "pg-secret-123" not in err and "Pw-Secret-9" not in err


def test_the_json_log_formatter_merges_event_fields_and_keeps_plain_messages():
    import json
    import logging

    f = cli.JsonFormatter()
    rec = logging.LogRecord("x", logging.INFO, "", 0, '{"event": "run succeeded", "run_id": 5}', None, None)
    out = json.loads(f.format(rec))
    assert out["event"] == "run succeeded" and out["run_id"] == 5 and out["level"] == "INFO" and "ts" in out
    out = json.loads(f.format(logging.LogRecord("x", logging.WARNING, "", 0, "plain text", None, None)))
    assert out["msg"] == "plain text"
