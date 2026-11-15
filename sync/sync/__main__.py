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


def main(argv: list[str] | None = None, env: dict | None = None, secrets_dir=None) -> int:
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

        Service(settings, definitions).serve()
        return 0
    print(f"sync: unknown command {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
