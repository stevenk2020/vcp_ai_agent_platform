"""US5: versioned SQL migrations are re-runnable, apply only what is pending, and roll back on failure."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT, connect

MIGRATIONS = REPO_ROOT / "db" / "migrations"


def make_migrate() -> subprocess.CompletedProcess[str]:
    return subprocess.run(["make", "migrate"], cwd=REPO_ROOT, text=True, capture_output=True)


def migrate_with(directory: Path) -> subprocess.CompletedProcess[str]:
    """Run the migrate service against another migrations directory."""
    return subprocess.run(
        ["docker", "compose", "run", "--rm", "-v", f"{directory}:/db/migrations:ro", "migrate"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )


def applied(output: str) -> list[str]:
    return [line for line in output.splitlines() if "Applying:" in line]


@pytest.fixture
def scratch_migrations(tmp_path: Path):
    directory = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, directory)
    directory.chmod(0o755)
    yield directory
    # remove anything the test migrations left behind
    with connect("app_owner") as conn:
        conn.execute("DROP TABLE IF EXISTS app.mig_probe, app.partial_probe")
        conn.execute("DELETE FROM app.schema_migrations WHERE version LIKE '2099%'")


def test_migrate_twice_is_idempotent():
    first = make_migrate()
    assert first.returncode == 0, first.stdout + first.stderr
    second = make_migrate()
    assert second.returncode == 0, second.stdout + second.stderr
    assert applied(second.stdout + second.stderr) == []


def test_baseline_creates_platform_meta_with_one_row(app_owner):
    assert make_migrate().returncode == 0
    rows = app_owner.execute(
        "SELECT key, value FROM app.platform_meta WHERE key = 'schema_generation'"
    ).fetchall()
    assert rows == [("schema_generation", "1")]


def test_only_pending_migration_is_applied(scratch_migrations, app_owner):
    assert make_migrate().returncode == 0
    (scratch_migrations / "20990101000000_probe.sql").write_text(
        "-- migrate:up\nCREATE TABLE app.mig_probe (id int);\n-- migrate:down\nDROP TABLE app.mig_probe;\n"
    )
    result = migrate_with(scratch_migrations)
    assert result.returncode == 0, result.stdout + result.stderr
    names = applied(result.stdout + result.stderr)
    assert len(names) == 1 and "20990101000000_probe" in names[0]
    assert app_owner.execute("SELECT to_regclass('app.mig_probe')").fetchone()[0] == "app.mig_probe"


def test_failed_migration_leaves_no_partial_change_and_stays_pending(scratch_migrations, app_owner):
    assert make_migrate().returncode == 0
    (scratch_migrations / "20990101000100_broken.sql").write_text(
        "-- migrate:up\n"
        "CREATE TABLE app.partial_probe (id int);\n"
        "SELECT * FROM app.this_table_does_not_exist;\n"
        "-- migrate:down\n"
        "DROP TABLE IF EXISTS app.partial_probe;\n"
    )
    first = migrate_with(scratch_migrations)
    assert first.returncode != 0
    assert app_owner.execute("SELECT to_regclass('app.partial_probe')").fetchone()[0] is None
    versions = [r[0] for r in app_owner.execute("SELECT version FROM app.schema_migrations").fetchall()]
    assert "20990101000100" not in versions
    # still pending: the next run tries it again
    second = migrate_with(scratch_migrations)
    assert second.returncode != 0
    assert any("20990101000100_broken" in line for line in applied(second.stdout + second.stderr))


def test_agent_cannot_read_platform_meta_or_migration_table(agent_ro):
    from psycopg import errors

    for table in ("app.platform_meta", "app.schema_migrations"):
        with pytest.raises((errors.InsufficientPrivilege, errors.UndefinedTable)):
            agent_ro.execute(f"SELECT * FROM {table}")
