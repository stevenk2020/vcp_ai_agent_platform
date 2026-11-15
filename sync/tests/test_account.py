"""US6: refuse to run with a privileged source account, before anything is read or created."""

import dataclasses
import logging

import pytest

from sync import definition as df
from sync import scheduler as sch
from sync.runner import Runner
from sync.scrub import Scrubber
from sync.source import AccountInfo
from sync.store import Store
from tests.conftest import TABLES_DIR
from tests.fakes import FakeClock, FakeSource, make_rows


@pytest.fixture
def d():
    return df.load(TABLES_DIR / "sales_contract.toml")


@pytest.fixture
def env(settings, d):
    source = FakeSource(d, make_rows(d, 10))
    store = Store(settings).connect()
    store.apply_control()
    staged = []
    original = store.create_staging
    store.create_staging = lambda definition: (staged.append(1), original(definition))[1]
    runner = Runner(settings, store, {d.copy: d}, source, clock=FakeClock(), sleep=lambda s: None, scrubber=Scrubber(settings.secret_values))
    yield runner, source, store, staged
    store.close()


@pytest.mark.parametrize("info,finding", [
    (AccountInfo(True, False, False), "sysadmin"),
    (AccountInfo(False, True, False), "db_owner"),
    (AccountInfo(False, False, True), "write"),
])
def test_a_privileged_account_is_refused_before_anything_is_read_or_created(env, info, finding, settings):
    runner, source, store, staged = env
    source.account = info
    r = runner.run_once("sales_contract", "schedule")
    assert (r.outcome, r.reason_code) == ("failed", "privileged_account")
    assert finding in r.message and settings.source_user not in r.message          # the finding, never the account name
    assert source.read_calls == 0 and staged == [] and not store.table_exists("sales_contract__new")
    st = store.state("sales_contract")
    assert (st["last_reason"], st["consecutive_failures"], st["data_as_of"]) == ("privileged_account", 1, None)


def test_a_read_only_account_passes(env):
    runner, source, store, staged = env
    assert runner.run_once("sales_contract", "schedule").outcome == "succeeded" and source.read_calls == 1


def test_the_check_runs_before_every_run_not_only_at_start(env):
    runner, source, store, staged = env
    assert runner.run_once("sales_contract", "schedule").outcome == "succeeded"
    source.account = AccountInfo(False, True, False)                  # the DBA promotes the account between two runs
    assert runner.run_once("sales_contract", "schedule").reason_code == "privileged_account"
    source.account = AccountInfo(False, False, False)
    assert runner.run_once("sales_contract", "schedule").outcome == "succeeded"
    assert source.read_calls == 2


def test_a_service_started_with_a_privileged_account_stays_alive_and_says_so(settings, d, tmp_path, caplog):
    source = FakeSource(d, make_rows(d, 10))
    source.account = AccountInfo(True, False, False)
    store, poll = Store(settings).connect(), Store(settings).connect()
    runner = Runner(settings, store, {d.copy: d}, source, clock=FakeClock(), sleep=lambda s: None, scrubber=Scrubber(settings.secret_values))
    try:
        svc = sch.Service(settings, [d], store=store, poll_store=poll, runner=runner, heartbeat_file=str(tmp_path / "hb"))
        with caplog.at_level(logging.WARNING):
            svc.startup()                                             # does not raise
        assert any("privileged" in r.getMessage() for r in caplog.records)
        assert sch.health(svc.heartbeat_file) == 0                    # still alive (heartbeat fresh)
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            svc.tick("sales_contract")
            svc.tick("sales_contract")
        assert len([r for r in caplog.records if "privileged" in r.getMessage()]) == 2   # once per run
    finally:
        store.close(); poll.close()
