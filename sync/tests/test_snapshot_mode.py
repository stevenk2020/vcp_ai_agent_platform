"""US6 / SC-008: snapshot reading is a switch, not a code change; `on` with a source that says off refuses to start."""

import dataclasses
import threading

import pytest

from sync import __main__ as cli
from sync import config as cfg
from sync import definition as df
from sync import scheduler as sch
from sync.runner import Runner
from sync.scrub import Scrubber
from sync.store import Store
from tests.conftest import SECRETS, TABLES_DIR
from tests.fakes import FakeClock, FakeSource, make_rows


@pytest.fixture
def d():
    return df.load(TABLES_DIR / "sales_contract.toml")


def runner_for(settings, d, source, **override):
    s = dataclasses.replace(settings, **override)
    store = Store(s).connect()
    store.apply_control()
    return s, store, Runner(s, store, {d.copy: d}, source, clock=FakeClock(), sleep=lambda x: None, scrubber=Scrubber(s.secret_values))


def test_with_snapshot_on_a_blocked_source_does_not_delay_the_run(settings, d):
    source = FakeSource(d, make_rows(d, 10))
    source.snapshot = True
    source.block = threading.Event()                  # the source job is "still committing" and never finishes
    s, store, runner = runner_for(settings, d, source, source_snapshot_read="on")
    try:
        assert runner.run_once("sales_contract", "schedule").outcome == "succeeded"     # returns at once
    finally:
        store.close()


def test_with_snapshot_off_the_run_waits_for_the_commit(settings, d):
    source = FakeSource(d, make_rows(d, 10))
    source.block = threading.Event()
    s, store, runner = runner_for(settings, d, source)
    out = []
    try:
        t = threading.Thread(target=lambda: out.append(runner.run_once("sales_contract", "schedule")))
        t.start()
        assert source.in_read.wait(10) and t.is_alive()
        source.block.set()
        t.join(30)
        assert out[0].outcome == "succeeded"
    finally:
        store.close()


def test_on_but_the_source_says_off_fails_a_manual_run_with_that_text(settings, d):
    source = FakeSource(d, make_rows(d, 10))           # snapshot reported off
    s, store, runner = runner_for(settings, d, source, source_snapshot_read="on")
    try:
        r = runner.run_once("sales_contract", "schedule")
        assert (r.outcome, r.reason_code) == ("failed", "unexpected")
        assert "SOURCE_SNAPSHOT_READ=on" in r.message and "off" in r.message and source.read_calls == 0
    finally:
        store.close()


def test_on_but_the_source_says_off_refuses_to_start(settings, d, tmp_path):
    source = FakeSource(d, make_rows(d, 10))
    s, store, runner = runner_for(settings, d, source, source_snapshot_read="on")
    poll = Store(s).connect()
    try:
        svc = sch.Service(s, [d], store=store, poll_store=poll, runner=runner, heartbeat_file=str(tmp_path / "hb"))
        with pytest.raises(cfg.ConfigError) as e:
            svc.startup()
        assert "SOURCE_SNAPSHOT_READ=on" in str(e.value)
    finally:
        store.close(); poll.close()


def test_main_exits_1_with_the_message_when_snapshot_is_on_but_unavailable(tmp_path, capsys, d):
    import os

    (tmp_path / "sync_writer_password").write_text((SECRETS / "sync_writer_password").read_text())
    (tmp_path / "source_sqlserver").write_text("SQL_SERVER_HOST=h\nSQL_SERVER_USER=u\nSQL_SERVER_PASSWORD=p\nSQL_SERVER_DATABASE=db\n")
    env = {"POSTGRES_HOST": os.environ.get("POSTGRES_HOST", "postgres"), "POSTGRES_DB": os.environ.get("POSTGRES_DB", "platform"),
           "SYNC_TABLES_DIR": str(TABLES_DIR), "SYNC_SQL_DIR": str(TABLES_DIR.parent / "sql"), "SOURCE_SNAPSHOT_READ": "on"}
    code = cli.main(["serve"], env=env, secrets_dir=tmp_path, factory=FakeSource(d, []))
    assert code == 1 and "SOURCE_SNAPSHOT_READ=on" in capsys.readouterr().err
