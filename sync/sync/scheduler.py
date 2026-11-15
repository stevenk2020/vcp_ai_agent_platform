"""The service: start-up, scheduled refreshes, the request poll and the heartbeat."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

from sync import definition as df
from sync.runner import Runner
from sync.source import SqlServerSource
from sync.store import Store

log = logging.getLogger("sync.scheduler")

HEARTBEAT_FILE = os.environ.get("SYNC_HEARTBEAT_FILE", "/tmp/sync-heartbeat")
HEARTBEAT_EVERY_SECONDS = 30
HEARTBEAT_MAX_AGE_SECONDS = 90


def source_factory(settings):
    """A factory of read-only SQL Server connections, or None when the source is not configured."""
    if not settings.source_configured:
        return None
    return lambda d: SqlServerSource(settings, d.source)


class Service:
    def __init__(self, settings, definitions: list, *, store=None, poll_store=None, runner=None, clock=None,
                 heartbeat_file: str = HEARTBEAT_FILE) -> None:
        self.s = settings
        self.defs = {d.copy: d for d in definitions}
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.store = store or Store(settings).connect()
        self.poll_store = poll_store or Store(settings).connect()
        self.runner = runner or Runner(settings, self.store, self.defs, source_factory(settings), clock=self.clock)
        self.heartbeat_file = heartbeat_file
        self.run_lock = threading.Lock()  # one run at a time inside this process (the advisory lock covers other processes)

    # ---- start-up ----
    def startup(self) -> None:
        self.store.apply_control()
        for d in self.defs.values():
            self.store.ensure_table(d)
            self.store.register(d)
        # a run row still 'running' belongs to a worker that died, but only when no run is alive anywhere
        free = [t for t in self.defs if self.store.try_lock(t)]
        try:
            if len(free) == len(self.defs):
                n = self.store.fail_running_runs(self.clock())
                if n:
                    log.warning("marked %s run(s) as interrupted", n)
        finally:
            for t in free:
                self.store.unlock(t)
        if not self.s.source_configured:
            log.warning('{"event": "source_not_configured", "detail": "scheduled refreshes are skipped until secrets/source_sqlserver is filled"}')
        else:
            log.info('{"event": "started", "tables": %s, "snapshot_read": "%s", "encryption": "%s"}',
                     list(self.defs), self.s.source_snapshot_read, self.s.source_encrypt)
        self.heartbeat()

    # ---- jobs ----
    def tick(self, table: str) -> None:
        if not self.s.source_configured:
            return
        with self.run_lock:
            self.runner.run_once(table, "schedule")

    def poll_requests(self) -> None:  # filled in by task T027
        return None

    def heartbeat(self) -> None:
        Path(self.heartbeat_file).touch()

    def build_scheduler(self) -> BlockingScheduler:
        scheduler = BlockingScheduler(timezone=timezone.utc)
        now = datetime.now(timezone.utc)
        interval = self.s.sync_interval_minutes
        for table in self.defs:
            scheduler.add_job(self.tick, "interval", minutes=interval, args=[table], id=f"sync:{table}", next_run_time=now,
                              max_instances=1, coalesce=True, misfire_grace_time=interval * 60)
        scheduler.add_job(self.poll_requests, "interval", seconds=self.s.sync_poll_seconds, id="poll", max_instances=1,
                          coalesce=True, next_run_time=now)
        scheduler.add_job(self.heartbeat, "interval", seconds=HEARTBEAT_EVERY_SECONDS, id="heartbeat", max_instances=1,
                          coalesce=True, next_run_time=now)
        return scheduler

    def serve(self) -> None:
        self.startup()
        self.build_scheduler().start()


def health(path: str = HEARTBEAT_FILE, now: float | None = None) -> int:
    """Exit code for the container health check: 0 when the heartbeat file is fresh."""
    try:
        age = (now if now is not None else time.time()) - os.path.getmtime(path)
    except OSError:
        return 1
    return 0 if age < HEARTBEAT_MAX_AGE_SECONDS else 1
