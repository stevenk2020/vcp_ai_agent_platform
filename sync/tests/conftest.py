"""Shared fixtures: a throwaway Postgres (the isolated test project) reached as the real roles."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

from sync import config as cfg

SECRETS = Path(os.environ.get("SECRETS_DIR", "/run/secrets"))
SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
TABLES_DIR = Path(__file__).resolve().parents[1] / "tables"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

ROLE_SECRETS = {
    "app_owner": "app_owner_password",
    "sync_writer": "sync_writer_password",
    "agent_ro": "agent_ro_password",
    "api_rw": "api_rw_password",
}


def pg(role: str = "sync_writer", autocommit: bool = True) -> psycopg.Connection:
    password = (SECRETS / ROLE_SECRETS[role]).read_text().strip()
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"), dbname=os.environ.get("POSTGRES_DB", "platform"),
        user=role, password=password, autocommit=autocommit, connect_timeout=10,
    )


@pytest.fixture
def settings() -> cfg.Settings:
    return cfg.Settings(
        postgres_host=os.environ.get("POSTGRES_HOST", "postgres"),
        postgres_db=os.environ.get("POSTGRES_DB", "platform"),
        postgres_password=(SECRETS / "sync_writer_password").read_text().strip(),
        source_host="db.test.invalid", source_user="reader", source_password="Sup3r-Secret-PW", source_database="erp_test",
        tables_dir=str(TABLES_DIR), sql_dir=str(SQL_DIR),
        sync_retry_base_seconds=1,
        guard_min_rows=3,
    )


@pytest.fixture
def sw():
    """Autocommit connection as sync_writer."""
    with pg("sync_writer") as conn:
        yield conn


def drop_serving(conn: psycopg.Connection) -> None:
    for (name,) in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'serving'").fetchall():
        conn.execute(f'DROP TABLE IF EXISTS serving."{name}" CASCADE')


@pytest.fixture(autouse=True)
def clean_serving():
    """Every test starts and ends with an empty serving schema."""
    with pg("sync_writer") as conn:
        drop_serving(conn)
    yield
    with pg("sync_writer") as conn:
        drop_serving(conn)
