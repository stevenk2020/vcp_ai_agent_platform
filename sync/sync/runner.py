"""One sync run: lock, checks, read, staging, guards, atomic swap, record. Never touches the copy on a problem."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import psycopg

from sync import guards
from sync.convert import Converter, OutOfRange
from sync.source import SourceError, SourceLoginFailed, SourceReadTimeout, SourceUnreachable
from sync.store import Store, SwapFailed

log = logging.getLogger("sync.runner")

SNAPSHOT_OFF_TEXT = ("SOURCE_SNAPSHOT_READ=on but the source reports that snapshot isolation is off "
                     "(ALLOW_SNAPSHOT_ISOLATION); ask the DBA to enable it or set SOURCE_SNAPSHOT_READ=off")

FIXED_TEXT = {
    "source_unreachable": "The source could not be reached",
    "read_timeout": "The read of the source took longer than the read limit",
    "login_failed": "The source refused the login",
    "swap_failed": "The copy could not be replaced (table lock not available)",
    "source_not_configured": "The source connection is not configured (secrets/source_sqlserver is empty)",
}


@dataclass
class RunResult:
    outcome: str                      # succeeded | failed | refused | skipped_busy
    reason_code: str | None = None
    run_id: int | None = None
    source_rows: int | None = None
    copied_rows: int | None = None
    message: str = ""
    warnings: dict = field(default_factory=dict)


class Runner:
    def __init__(self, settings, store: Store, definitions: dict, source_factory, *, clock=None, sleep=time.sleep,
                 scrubber=None, monotonic=time.monotonic) -> None:
        self.s = settings
        self.store = store
        self.defs = definitions
        self.factory = source_factory
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        self.scrub = scrubber
        self.monotonic = monotonic

    # ---- public ----
    def run_once(self, table: str, trigger: str, requested_by: str | None = None, allow_empty: bool = False) -> RunResult:
        d = self.defs[table]
        if not self.store.try_lock(table):
            self._log("run skipped", table=table, reason="another run is active")
            return RunResult("skipped_busy", message="another run is already active")
        t0 = self.monotonic()
        self.store.register(d)  # idempotent: the state row and its thresholds exist
        run_id = self.store.start_run(table, trigger, requested_by, self.clock())
        warnings: dict = {}
        try:
            try:
                if self.factory is None:
                    raise guards.RunFailed("source_not_configured", FIXED_TEXT["source_not_configured"])
                source_rows, copied_rows = self._execute(d, run_id, t0, warnings, allow_empty)
            except guards.Refused as e:
                return self._fail(d, run_id, t0, "refused", e.reason_code, e.text, warnings)
            except guards.RunFailed as e:
                return self._fail(d, run_id, t0, "failed", e.reason_code, e.text, warnings)
            except SourceLoginFailed as e:
                return self._fail(d, run_id, t0, "failed", "login_failed", self._with_detail("login_failed", e), warnings)
            except SourceReadTimeout as e:
                return self._fail(d, run_id, t0, "failed", "read_timeout", self._with_detail("read_timeout", e), warnings)
            except SourceUnreachable as e:
                return self._fail(d, run_id, t0, "failed", "source_unreachable", self._with_detail("source_unreachable", e), warnings)
            except OutOfRange as e:
                return self._fail(d, run_id, t0, "refused", "value_out_of_range", str(e), warnings)
            except psycopg.errors.UniqueViolation:
                return self._fail(d, run_id, t0, "refused", "duplicate_key",
                                  "the source has the same key (" + ", ".join(d.key) + ") more than once", warnings)
            except psycopg.errors.NotNullViolation:
                return self._fail(d, run_id, t0, "refused", "key_missing",
                                  "the source has a row without a value in a key column (" + ", ".join(d.key) + ")", warnings)
            except SwapFailed as e:
                return self._fail(d, run_id, t0, "failed", "swap_failed", FIXED_TEXT["swap_failed"], warnings)
            except SourceError as e:
                return self._fail(d, run_id, t0, "failed", "unexpected", self._with_detail("unexpected", e, "source error"), warnings)
            except psycopg.Error as e:
                # never include the driver text: it can quote row values
                return self._fail(d, run_id, t0, "failed", "unexpected", f"unexpected database error {type(e).__name__}", warnings)
            except Exception as e:  # noqa: BLE001
                return self._fail(d, run_id, t0, "failed", "unexpected", self._with_detail("unexpected", e, f"unexpected {type(e).__name__}"), warnings)
            self._log("run succeeded", table=table, run_id=run_id, source_rows=source_rows, copied_rows=copied_rows)
            self.store.prune_runs(self.s.sync_log_retention_days)
            return RunResult("succeeded", run_id=run_id, source_rows=source_rows, copied_rows=copied_rows, warnings=warnings)
        finally:
            try:
                self.store.drop_staging(d)
            finally:
                self.store.unlock(table)

    # ---- one run ----
    def _open_source(self, d):
        """Connect with retries for connection errors only (waits base, base*3, ...)."""
        attempts, wait = max(1, self.s.sync_retry_attempts), self.s.sync_retry_base_seconds
        for attempt in range(1, attempts + 1):
            source = self.factory(d)
            try:
                source.__enter__()
                return source
            except SourceUnreachable:
                if attempt == attempts:
                    raise
                self._log("source unreachable, will retry", attempt=attempt, wait_seconds=wait)
                self.sleep(wait)
                wait *= 3

    def _execute(self, d, run_id: int, t0: float, warnings: dict, allow_empty: bool) -> tuple[int, int]:
        source = self._open_source(d)
        try:
            guards.check_account(source.account_info())
            if self.s.source_snapshot_read == "on" and not source.snapshot_enabled():
                raise guards.RunFailed("unexpected", SNAPSHOT_OFF_TEXT)
            warnings.update(guards.check_shape(d, source.columns()))
            self.store.create_staging(d)
            converter = Converter(d)
            counter = {"n": 0}

            def rows():
                data_batches = source.read(d.select_sql())
                for batch in data_batches:
                    for r in batch:
                        counter["n"] += 1
                        yield converter.row(r)

            data_as_of = self.clock()  # the read starts now (platform clock): "data as of"
            copied = self.store.copy_rows(d, rows())
            source_rows = counter["n"]
        finally:
            source.__exit__(None, None, None)
        self.store.finish_staging(d)
        current = self.store.row_count(d.copy) if self.store.table_exists(d.copy) else 0
        guards.check_counts(d, self.s, source_rows, current, allow_empty)
        if converter.rounded:
            warnings["rounded_values"] = converter.rounded
        if allow_empty:
            warnings["allow_empty"] = True

        def state(cur):
            self.store.record_success(cur, d.copy, run_id, data_as_of, source_rows, copied)

        def run(cur):
            self.store.finish_run(run_id, "succeeded", self.clock(), int((self.monotonic() - t0) * 1000),
                                  source_rows=source_rows, copied_rows=copied, warnings=warnings, cur=cur)

        self.store.swap(d, state, run)
        return source_rows, copied

    # ---- failures ----
    def _with_detail(self, code: str, exc: Exception, base: str | None = None) -> str:
        text = base or FIXED_TEXT[code]
        detail = self.scrub.clean(str(exc), limit=300) if self.scrub else ""
        return f"{text}: {detail}" if detail else text

    def _fail(self, d, run_id: int, t0: float, outcome: str, reason: str, text: str, warnings: dict) -> RunResult:
        clean = self.scrub.clean(text) if self.scrub else text[:500]
        ms = int((self.monotonic() - t0) * 1000)
        with self.store.conn.transaction():
            self.store.finish_run(run_id, outcome, self.clock(), ms, reason_code=reason, error_text=clean,
                                  warnings=warnings, cur=self.store.conn)
            self.store.record_failure(d.copy, run_id, outcome, reason)
        self._log("run " + outcome, table=d.copy, run_id=run_id, reason=reason)
        if reason == "privileged_account":
            log.warning("run refused: privileged source account (%s)", clean)
        return RunResult(outcome, reason_code=reason, run_id=run_id, message=clean, warnings=warnings)

    def _log(self, event: str, **fields) -> None:
        log.info(json.dumps({"event": event, **fields}, default=str))
