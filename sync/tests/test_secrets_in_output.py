"""SC-006: no source or database secret in any run text, log line, status output or stored warning."""

import dataclasses
import logging

import pytest

from sync import __main__ as cli
from sync import definition as df
from sync.runner import Runner
from sync.scrub import ScrubFilter, Scrubber
from sync.source import SourceLoginFailed, SourceReadTimeout, SourceUnreachable
from sync.store import Store
from tests.conftest import SECRETS, TABLES_DIR
from tests.fakes import FakeClock, FakeSource, make_rows


@pytest.fixture
def d():
    return df.load(TABLES_DIR / "sales_contract.toml")


def leak(settings):
    pw = settings.postgres_password
    return (f"Adaptive Server connection failed (DB-Lib 20002) to {settings.source_host}:1433 user={settings.source_user} "
            f"password={settings.source_password} database={settings.source_database} "
            f"Server={settings.source_host};Uid={settings.source_user};Pwd={settings.source_password};Database={settings.source_database} "
            f"postgres://sync_writer:{pw}@postgres/platform")


def secrets_of(settings):
    return [settings.source_host, settings.source_user, settings.source_password, settings.source_database, settings.postgres_password]


def test_no_secret_survives_any_failure_path(settings, d, caplog, tmp_path, capsys):
    settings = dataclasses.replace(settings, sync_retry_base_seconds=1)
    text = leak(settings)
    source = FakeSource(d, make_rows(d, 10))
    store = Store(settings).connect()
    store.apply_control()
    # the real logging setup: scrub filter on the handler, as the service installs it
    caplog.set_level(logging.DEBUG)
    caplog.handler.addFilter(ScrubFilter(Scrubber(secrets_of(settings))))
    runner = Runner(settings, store, {d.copy: d}, source, clock=FakeClock(), sleep=lambda s: None, scrubber=Scrubber(secrets_of(settings)))
    try:
        assert runner.run_once("sales_contract", "schedule").outcome == "succeeded"
        failures = [
            ("connect", [SourceUnreachable(text)] * 3),
            ("connect", [SourceLoginFailed(text)]),
            ("read", [SourceReadTimeout(text)]),
            ("read", [RuntimeError(text)]),
            ("read", [ValueError(text)]),
        ]
        for where, errs in failures:
            source.connect_failures, source.read_failures = (errs, []) if where == "connect" else ([], errs)
            r = runner.run_once("sales_contract", "schedule")
            assert r.outcome == "failed"
            for secret in secrets_of(settings):
                assert secret.lower() not in r.message.lower(), (where, secret)
        source.rows = []
        runner.run_once("sales_contract", "schedule")                          # a refusal too
        rows = store.conn.execute("SELECT error_text, warnings::text AS w, reason_code FROM serving.sync_runs").fetchall()
        assert len(rows) == 7
        blob_db = " ".join((r["error_text"] or "") + (r["w"] or "") for r in rows)
        for secret in secrets_of(settings):
            assert secret.lower() not in blob_db.lower(), secret
        assert all(len(r["error_text"] or "") <= 500 for r in rows)
        assert "***" in blob_db
        logs = caplog.text + " ".join(rec.getMessage() for rec in caplog.records)
        for secret in secrets_of(settings):
            assert secret.lower() not in logs.lower(), secret
    finally:
        store.close()


def test_status_output_has_no_secret(settings, d, tmp_path, capsys):
    import os

    (tmp_path / "sync_writer_password").write_text((SECRETS / "sync_writer_password").read_text())
    (tmp_path / "source_sqlserver").write_text(
        f"SQL_SERVER_HOST={settings.source_host}\nSQL_SERVER_USER={settings.source_user}\n"
        f"SQL_SERVER_PASSWORD={settings.source_password}\nSQL_SERVER_DATABASE={settings.source_database}\n")
    env = {"POSTGRES_HOST": os.environ.get("POSTGRES_HOST", "postgres"), "POSTGRES_DB": os.environ.get("POSTGRES_DB", "platform"),
           "SYNC_TABLES_DIR": str(TABLES_DIR), "SYNC_SQL_DIR": str(TABLES_DIR.parent / "sql"), "SYNC_RETRY_BASE_SECONDS": "1"}
    source = FakeSource(d, make_rows(d, 10))
    source.connect_failures = [SourceUnreachable(leak(settings))] * 3
    cli.main(["run", "sales_contract"], env=env, secrets_dir=tmp_path, factory=source)
    cli.main(["status"], env=env, secrets_dir=tmp_path)
    out = capsys.readouterr().out
    assert "failed" in out
    for secret in secrets_of(settings)[:4]:
        assert secret.lower() not in out.lower()


def test_the_scrub_filter_is_what_the_service_installs(settings):
    cli.setup_logging(settings)
    root = logging.getLogger()
    assert any(isinstance(f, ScrubFilter) for h in root.handlers for f in h.filters)
