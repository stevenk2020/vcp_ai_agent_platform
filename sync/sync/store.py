"""Postgres side, all as role sync_writer: control tables, staging, COPY, atomic swap, runs, state, lock."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg import errors
from psycopg.rows import dict_row

from sync.definition import TableDefinition

log = logging.getLogger("sync.store")


class SwapFailed(RuntimeError):
    """The swap could not take its lock in time, after every attempt."""


class Store:
    def __init__(self, settings, conninfo: str | None = None) -> None:
        self.s = settings
        self.conninfo = conninfo or (
            f"host={settings.postgres_host} port={settings.postgres_port} dbname={settings.postgres_db} "
            f"user={settings.postgres_user} password={settings.postgres_password} connect_timeout=10"
        )
        self.conn: psycopg.Connection | None = None
        self._lock_conn: psycopg.Connection | None = None

    # ---- connections ----
    def connect(self) -> "Store":
        self.conn = psycopg.connect(self.conninfo, autocommit=True, row_factory=dict_row)
        return self

    def close(self) -> None:
        for c in (self.conn, self._lock_conn):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass
        self.conn = self._lock_conn = None

    # ---- control tables ----
    def apply_control(self, sql_path: str | Path | None = None) -> None:
        path = Path(sql_path or Path(self.s.sql_dir) / "control.sql")
        self.conn.execute(path.read_text(encoding="utf-8"))

    def register(self, definition: TableDefinition) -> None:
        self.conn.execute(
            "INSERT INTO serving.sync_state (table_name, definition_hash, stale_after_seconds, failing_after) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (table_name) DO UPDATE SET definition_hash = EXCLUDED.definition_hash, "
            "stale_after_seconds = EXCLUDED.stale_after_seconds, failing_after = EXCLUDED.failing_after",
            (definition.copy, definition.hash(), self.s.sync_stale_minutes * 60, self.s.sync_failing_after),
        )

    # ---- the copy ----
    def table_exists(self, name: str) -> bool:
        return self.conn.execute("SELECT to_regclass(%s) IS NOT NULL AS e", (f'serving."{name}"',)).fetchone()["e"]

    def ensure_table(self, d: TableDefinition) -> None:
        """An empty live table, so readers never meet 'relation does not exist'."""
        if self.table_exists(d.copy):
            return
        with self.conn.transaction():
            self.conn.execute(d.create_table_sql(d.copy, d.pkey))
            for index in d.indexes:
                self.conn.execute(d.index_sql(index, d.copy, index.name))

    def row_count(self, name: str) -> int:
        return self.conn.execute(f'SELECT count(*) AS n FROM serving."{name}"').fetchone()["n"]

    def drop_staging(self, d: TableDefinition) -> None:
        self.conn.execute(f'DROP TABLE IF EXISTS serving."{d.staging}"')
        self.conn.execute(f'DROP TABLE IF EXISTS serving."{d.old}"')

    def create_staging(self, d: TableDefinition) -> None:
        self.drop_staging(d)
        self.conn.execute(d.create_staging_sql())

    def copy_rows(self, d: TableDefinition, rows: Iterable[list]) -> int:
        cols = ", ".join(f'"{c}"' for c in d.column_names)
        n = 0
        with self.conn.cursor() as cur:
            with cur.copy(f'COPY serving."{d.staging}" ({cols}) FROM STDIN') as cp:
                for row in rows:
                    cp.write_row(row)
                    n += 1
        return n

    def finish_staging(self, d: TableDefinition) -> None:
        for index in d.indexes:
            self.conn.execute(d.index_sql(index, d.staging, d.temp_index_name(index)))
        self.conn.execute(f'ANALYZE serving."{d.staging}"')

    def swap(self, d: TableDefinition, state_update: Callable[[psycopg.Cursor], None],
             run_update: Callable[[psycopg.Cursor], None], lock_timeout_s: int | None = None, attempts: int = 3) -> None:
        """Replace the live table with the staging table in one short transaction (research R-5)."""
        timeout = int(lock_timeout_s or self.s.swap_lock_timeout_seconds)
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                with self.conn.transaction():
                    cur = self.conn.cursor()
                    cur.execute(f"SET LOCAL lock_timeout = '{timeout}s'")
                    if self.table_exists(d.copy):
                        cur.execute(f'ALTER TABLE serving."{d.copy}" RENAME TO "{d.old}"')
                        cur.execute(f'DROP TABLE serving."{d.old}"')  # frees the index names
                    cur.execute(f'ALTER TABLE serving."{d.staging}" RENAME TO "{d.copy}"')
                    cur.execute(f'ALTER TABLE serving."{d.copy}" RENAME CONSTRAINT "{d.staging}_pkey" TO "{d.pkey}"')
                    for index in d.indexes:
                        cur.execute(f'ALTER INDEX serving."{d.temp_index_name(index)}" RENAME TO "{index.name}"')
                    state_update(cur)
                    run_update(cur)
                return
            except errors.LockNotAvailable as exc:
                last = exc
                log.warning("swap attempt %s of %s could not get its lock within %ss", attempt, attempts, timeout)
        raise SwapFailed(f"could not take the table lock for the swap after {attempts} attempts") from last

    # ---- runs, state, requests ----
    def start_run(self, table: str, trigger: str, requested_by: str | None, started_at: datetime) -> int:
        return self.conn.execute(
            "INSERT INTO serving.sync_runs (table_name, trigger, requested_by, started_at, outcome) "
            "VALUES (%s, %s, %s, %s, 'running') RETURNING id", (table, trigger, requested_by, started_at),
        ).fetchone()["id"]

    def finish_run(self, run_id: int, outcome: str, finished_at: datetime, duration_ms: int, *, reason_code: str | None = None,
                   source_rows: int | None = None, copied_rows: int | None = None, error_text: str | None = None,
                   warnings: dict | None = None, cur=None) -> None:
        (cur or self.conn).execute(
            "UPDATE serving.sync_runs SET outcome = %s, finished_at = %s, duration_ms = %s, reason_code = %s, "
            "source_rows = %s, copied_rows = %s, error_text = %s, warnings = %s WHERE id = %s",
            (outcome, finished_at, duration_ms, reason_code, source_rows, copied_rows, error_text,
             json.dumps(warnings or {}), run_id),
        )

    def record_success(self, cur, table: str, run_id: int, data_as_of: datetime, source_rows: int, copied_rows: int) -> None:
        cur.execute(
            "UPDATE serving.sync_state SET data_as_of = %s, last_run_id = %s, last_outcome = 'succeeded', last_reason = NULL, "
            "source_rows = %s, copied_rows = %s, consecutive_failures = 0, updated_at = now() WHERE table_name = %s",
            (data_as_of, run_id, source_rows, copied_rows, table),
        )

    def record_failure(self, table: str, run_id: int, outcome: str, reason_code: str) -> None:
        self.conn.execute(
            "UPDATE serving.sync_state SET last_run_id = %s, last_outcome = %s, last_reason = %s, "
            "consecutive_failures = consecutive_failures + 1, updated_at = now() WHERE table_name = %s",
            (run_id, outcome, reason_code, table),
        )

    def state(self, table: str) -> dict | None:
        return self.conn.execute("SELECT * FROM serving.sync_state WHERE table_name = %s", (table,)).fetchone()

    def prune_runs(self, retention_days: int) -> None:
        self.conn.execute("DELETE FROM serving.sync_runs WHERE started_at < now() - make_interval(days => %s) AND outcome <> 'running'",
                          (retention_days,))

    def fail_running_runs(self, finished_at: datetime) -> int:
        """Rows left 'running' by a worker that died; only called when no run can be alive (lock free)."""
        cur = self.conn.execute(
            "UPDATE serving.sync_runs SET outcome = 'failed', reason_code = 'interrupted', finished_at = %s, "
            "error_text = 'the worker stopped while this run was in progress' WHERE outcome = 'running'", (finished_at,))
        return cur.rowcount

    # ---- advisory lock (one run at a time, per table) ----
    def try_lock(self, table: str) -> bool:
        if self._lock_conn is None or self._lock_conn.closed:
            self._lock_conn = psycopg.connect(self.conninfo, autocommit=True)
        got = self._lock_conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (f"sync:{table}",)).fetchone()[0]
        return bool(got)

    def unlock(self, table: str) -> None:
        if self._lock_conn is not None and not self._lock_conn.closed:
            self._lock_conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (f"sync:{table}",))
