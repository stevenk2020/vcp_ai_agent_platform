import shutil

import pytest

from sync import __main__ as cli
from sync import definition as df
from sync.source import SourceUnreachable
from tests.conftest import SECRETS, TABLES_DIR
from tests.fakes import FakeSource, make_rows


@pytest.fixture
def d():
    return df.load(TABLES_DIR / "sales_contract.toml")


@pytest.fixture
def run_cli(tmp_path, settings, d):
    import os

    (tmp_path / "sync_writer_password").write_text((SECRETS / "sync_writer_password").read_text())
    (tmp_path / "source_sqlserver").write_text(
        "SQL_SERVER_HOST=db.test.invalid\nSQL_SERVER_USER=reader\nSQL_SERVER_PASSWORD=Sup3r-Secret-PW\nSQL_SERVER_DATABASE=erp_test\n")
    env = {"POSTGRES_HOST": os.environ.get("POSTGRES_HOST", "postgres"), "POSTGRES_DB": os.environ.get("POSTGRES_DB", "platform"),
           "SYNC_TABLES_DIR": str(TABLES_DIR), "SYNC_SQL_DIR": str(TABLES_DIR.parent / "sql"), "GUARD_MIN_ROWS": "3", "SYNC_RETRY_BASE_SECONDS": "1"}

    def run(*argv, source=None):
        return cli.main(list(argv), env=env, secrets_dir=tmp_path, factory=source)

    return run


def test_exit_codes_for_each_outcome(run_cli, d, capsys):
    source = FakeSource(d, make_rows(d, 10))
    assert run_cli("run", "sales_contract", source=source) == 0
    assert "succeeded" in capsys.readouterr().out
    source.rows = []
    assert run_cli("run", "sales_contract", source=source) == 2
    out = capsys.readouterr().out
    assert "refused" in out and "zero_rows" in out
    assert run_cli("run", "sales_contract", "--allow-empty", source=source) == 0
    source.rows = make_rows(d, 10)
    source.connect_failures = [SourceUnreachable("down")] * 3
    assert run_cli("run", "sales_contract", source=source) == 3
    assert "source_unreachable" in capsys.readouterr().out


def test_a_second_run_while_one_is_active_exits_4(run_cli, d, settings, capsys):
    from sync.store import Store

    other = Store(settings).connect()
    try:
        assert other.try_lock("sales_contract")
        assert run_cli("run", "sales_contract", source=FakeSource(d, make_rows(d, 10))) == 4
        assert "skipped_busy" in capsys.readouterr().out
    finally:
        other.close()


def test_unknown_table_and_unknown_command_are_usage_errors(run_cli, d, capsys):
    assert run_cli("run", "nope", source=FakeSource(d, [])) == 64
    assert run_cli("run", source=FakeSource(d, [])) == 64
    assert run_cli("frobnicate") == 64


def test_status_shows_the_derived_state_and_no_secret(run_cli, d, capsys):
    source = FakeSource(d, make_rows(d, 10))
    run_cli("run", "sales_contract", source=source)
    capsys.readouterr()
    assert run_cli("status") == 0
    out = capsys.readouterr().out
    assert "sales_contract: healthy" in out and "rows=10" in out and "consecutive_failures=0" in out and "succeeded" in out
    source.rows = []
    run_cli("run", "sales_contract", source=source)
    capsys.readouterr()
    run_cli("status")
    out = capsys.readouterr().out
    assert "refused zero_rows" in out and "consecutive_failures=1" in out
    for secret in ("Sup3r-Secret-PW", "db.test.invalid", "erp_test"):
        assert secret not in out


@pytest.mark.parametrize("failures,state", [(0, "healthy"), (3, "failing")])
def test_status_reports_failing_after_three_failures(run_cli, d, capsys, failures, state):
    source = FakeSource(d, make_rows(d, 10))
    run_cli("run", "sales_contract", source=source)
    source.rows = []
    for _ in range(failures):
        run_cli("run", "sales_contract", source=source)
    capsys.readouterr()
    run_cli("status")
    assert f"sales_contract: {state}" in capsys.readouterr().out


def test_status_before_the_service_ever_started(run_cli, capsys):
    run_cli("status")
    assert "not registered yet" in capsys.readouterr().out
