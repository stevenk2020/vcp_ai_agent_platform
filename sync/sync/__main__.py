"""Entry point: `python -m sync` runs the service; `health`, `run`, `status` are operator commands."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from sync import config as cfg
from sync import definition as df
from sync.scrub import ScrubFilter, Scrubber


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        base = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "level": record.levelname, "logger": record.name}
        try:
            extra = json.loads(message)
            if isinstance(extra, dict):
                return json.dumps({**base, **extra})
        except ValueError:
            pass
        return json.dumps({**base, "msg": message})


def setup_logging(settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(ScrubFilter(Scrubber(settings.secret_values)))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)  # one line per job every 5 seconds is noise


def main(argv: list[str] | None = None, env: dict | None = None, secrets_dir=None, factory=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "serve"
    if command == "health":
        from sync.scheduler import health

        return health()
    try:
        settings = cfg.load(env=env, secrets_dir=secrets_dir)
        definitions = df.load_all(settings.tables_dir)
        if not definitions:
            raise cfg.ConfigError(f"no table definitions found in {settings.tables_dir}")
    except (cfg.ConfigError, df.DefinitionError) as exc:
        print(f"sync: configuration error: {exc}", file=sys.stderr)
        return 1
    setup_logging(settings)
    if command == "serve":
        from sync.scheduler import Service

        try:
            Service(settings, definitions, factory=factory).serve()
        except cfg.ConfigError as exc:
            print(f"sync: configuration error: {exc}", file=sys.stderr)
            return 1
        return 0
    if command == "run":
        return cmd_run(settings, definitions, argv[1:], factory)
    if command == "status":
        return cmd_status(settings, definitions)
    print(f"sync: unknown command {command!r}", file=sys.stderr)
    return 64


EXIT = {"succeeded": 0, "refused": 2, "failed": 3, "skipped_busy": 4}


def cmd_run(settings, definitions, args: list[str], factory=None) -> int:
    """`run <table> [--allow-empty]`: one sync now, under the same lock as the scheduled runs."""
    from sync.runner import Runner
    from sync.scheduler import source_factory
    from sync.store import Store

    allow_empty = "--allow-empty" in args
    names = [a for a in args if not a.startswith("--")]
    by_name = {d.copy: d for d in definitions}
    if len(names) != 1 or names[0] not in by_name:
        print(f"usage: python -m sync run <table> [--allow-empty]   (tables: {', '.join(sorted(by_name))})", file=sys.stderr)
        return 64
    store = Store(settings).connect()
    try:
        store.apply_control()
        store.ensure_table(by_name[names[0]])
        runner = Runner(settings, store, by_name, factory if factory is not None else source_factory(settings),
                        scrubber=Scrubber(settings.secret_values))
        result = runner.run_once(names[0], "command", allow_empty=allow_empty)
    finally:
        store.close()
    detail = f" reason={result.reason_code}" if result.reason_code else ""
    rows = f" source_rows={result.source_rows} copied_rows={result.copied_rows}" if result.source_rows is not None else ""
    print(f"{result.outcome}{detail}{rows} run_id={result.run_id or '-'} {result.message}".strip())
    return EXIT[result.outcome]


def cmd_status(settings, definitions) -> int:
    from sync.state import derive_state
    from sync.store import Store

    store = Store(settings).connect()
    scrub = Scrubber(settings.secret_values)
    try:
        store.apply_control()  # idempotent; lets `status` work before the service ever started
        now = datetime.now(timezone.utc)
        for d in definitions:
            row = store.state(d.copy)
            if row is None:
                print(f"{d.copy}: not registered yet (the service has not started)")
                continue
            derived = derive_state(row, now)
            age = "-" if derived["age_seconds"] is None else f"{derived['age_seconds'] // 60} min"
            print(f"{d.copy}: {derived['state']}  data_as_of={row['data_as_of'] or '-'}  age={age}  "
                  f"rows={row['copied_rows'] if row['copied_rows'] is not None else '-'}  consecutive_failures={row['consecutive_failures']}")
            for run in store.conn.execute("SELECT * FROM serving.sync_runs WHERE table_name = %s ORDER BY id DESC LIMIT 5", (d.copy,)).fetchall():
                reason = f" {run['reason_code']}" if run["reason_code"] else ""
                text = scrub.clean(run["error_text"] or "", limit=160)
                print(f"  #{run['id']} {run['started_at']:%Y-%m-%d %H:%M:%S} {run['trigger']:<8} {run['outcome']}{reason}"
                      f"  {run['duration_ms'] if run['duration_ms'] is not None else '-'} ms  {text}".rstrip())
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
