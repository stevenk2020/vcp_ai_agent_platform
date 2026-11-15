"""Settings from environment variables and secret files, with start-up validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    """The configuration is unusable; the service refuses to start."""


SNAPSHOT_MODES = ("off", "on")
ENCRYPT_MODES = ("require", "request", "off", "default")
MIN_READ_TIMEOUT_SECONDS = 180  # the source job takes about 2 minutes (spec FR-003)
SOURCE_KEYS = ("SQL_SERVER_HOST", "SQL_SERVER_PORT", "SQL_SERVER_USER", "SQL_SERVER_PASSWORD", "SQL_SERVER_DATABASE")


@dataclass(frozen=True)
class Settings:
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "platform"
    postgres_user: str = "sync_writer"
    postgres_password: str = field(default="", repr=False)
    sync_interval_minutes: int = 10
    sync_poll_seconds: int = 5
    source_snapshot_read: str = "off"
    source_read_timeout_seconds: int = 300
    source_encrypt: str = "require"
    sync_retry_attempts: int = 3
    sync_retry_base_seconds: int = 15
    guard_min_fraction: float = 0.5
    guard_min_rows: int = 1000
    swap_lock_timeout_seconds: int = 15
    sync_stale_minutes: int = 30
    sync_failing_after: int = 3
    sync_log_retention_days: int = 30
    sync_request_ttl_minutes: int = 15
    source_host: str = field(default="", repr=False)
    source_port: int = field(default=1433, repr=False)
    source_user: str = field(default="", repr=False)
    source_password: str = field(default="", repr=False)
    source_database: str = field(default="", repr=False)
    tables_dir: str = "tables"
    sql_dir: str = "sql"

    @property
    def source_configured(self) -> bool:
        return bool(self.source_host and self.source_user and self.source_password and self.source_database)

    @property
    def secret_values(self) -> list[str]:
        """Every value that must never appear in a log or an error text."""
        values = [self.postgres_password, self.source_host, self.source_user, self.source_password, self.source_database]
        return [v for v in values if v]

    @property
    def read_timeout_ms(self) -> int:
        return self.source_read_timeout_seconds * 1000


def validate(settings: Settings) -> Settings:
    if settings.source_snapshot_read not in SNAPSHOT_MODES:
        raise ConfigError(f"SOURCE_SNAPSHOT_READ must be one of {SNAPSHOT_MODES}, got {settings.source_snapshot_read!r}")
    if settings.source_encrypt not in ENCRYPT_MODES:
        raise ConfigError(f"SOURCE_ENCRYPT must be one of {ENCRYPT_MODES}, got {settings.source_encrypt!r}")
    if not (0 < settings.guard_min_fraction <= 1):
        raise ConfigError("GUARD_MIN_FRACTION must be above 0 and at most 1")
    if settings.source_read_timeout_seconds < MIN_READ_TIMEOUT_SECONDS:
        raise ConfigError(
            f"SOURCE_READ_TIMEOUT_SECONDS must be at least {MIN_READ_TIMEOUT_SECONDS} "
            "(the source job takes about 2 minutes to commit)"
        )
    for name in (
        "sync_interval_minutes", "sync_poll_seconds", "sync_retry_attempts", "sync_retry_base_seconds",
        "guard_min_rows", "swap_lock_timeout_seconds", "sync_stale_minutes", "sync_failing_after",
        "sync_log_retention_days", "sync_request_ttl_minutes",
    ):
        if getattr(settings, name) < 1:
            raise ConfigError(f"{name.upper()} must be at least 1")
    return settings


def parse_dotenv(text: str) -> dict[str, str]:
    """`KEY=value` lines; blank lines and `#` comments are ignored; surrounding quotes are removed."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _num(env: dict[str, str], name: str, default, cast):
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def load(env: dict[str, str] | None = None, secrets_dir: str | Path | None = None) -> Settings:
    """Build and validate settings. A missing or empty source secret is not an error: the worker idles."""
    env = dict(os.environ if env is None else env)
    directory = Path(secrets_dir or env.get("SECRETS_DIR", "/run/secrets"))
    source = parse_dotenv(_read(directory / "source_sqlserver"))
    d = Settings()
    port_raw = source.get("SQL_SERVER_PORT", "").strip()
    try:
        source_port = int(port_raw) if port_raw else d.source_port
    except ValueError as exc:
        raise ConfigError("SQL_SERVER_PORT in the source secret must be a number") from exc
    settings = Settings(
        postgres_host=env.get("POSTGRES_HOST", d.postgres_host),
        postgres_port=_num(env, "POSTGRES_PORT_INTERNAL", d.postgres_port, int),
        postgres_db=env.get("POSTGRES_DB", d.postgres_db),
        postgres_user=env.get("POSTGRES_USER_SYNC", d.postgres_user),
        postgres_password=_read(directory / "sync_writer_password"),
        sync_interval_minutes=_num(env, "SYNC_INTERVAL_MINUTES", d.sync_interval_minutes, int),
        sync_poll_seconds=_num(env, "SYNC_POLL_SECONDS", d.sync_poll_seconds, int),
        source_snapshot_read=env.get("SOURCE_SNAPSHOT_READ", d.source_snapshot_read).strip().lower(),
        source_read_timeout_seconds=_num(env, "SOURCE_READ_TIMEOUT_SECONDS", d.source_read_timeout_seconds, int),
        source_encrypt=env.get("SOURCE_ENCRYPT", d.source_encrypt).strip().lower(),
        sync_retry_attempts=_num(env, "SYNC_RETRY_ATTEMPTS", d.sync_retry_attempts, int),
        sync_retry_base_seconds=_num(env, "SYNC_RETRY_BASE_SECONDS", d.sync_retry_base_seconds, int),
        guard_min_fraction=_num(env, "GUARD_MIN_FRACTION", d.guard_min_fraction, float),
        guard_min_rows=_num(env, "GUARD_MIN_ROWS", d.guard_min_rows, int),
        swap_lock_timeout_seconds=_num(env, "SWAP_LOCK_TIMEOUT_SECONDS", d.swap_lock_timeout_seconds, int),
        sync_stale_minutes=_num(env, "SYNC_STALE_MINUTES", d.sync_stale_minutes, int),
        sync_failing_after=_num(env, "SYNC_FAILING_AFTER", d.sync_failing_after, int),
        sync_log_retention_days=_num(env, "SYNC_LOG_RETENTION_DAYS", d.sync_log_retention_days, int),
        sync_request_ttl_minutes=_num(env, "SYNC_REQUEST_TTL_MINUTES", d.sync_request_ttl_minutes, int),
        source_host=source.get("SQL_SERVER_HOST", ""),
        source_port=source_port,
        source_user=source.get("SQL_SERVER_USER", ""),
        source_password=source.get("SQL_SERVER_PASSWORD", ""),
        source_database=source.get("SQL_SERVER_DATABASE", ""),
        tables_dir=env.get("SYNC_TABLES_DIR", d.tables_dir),
        sql_dir=env.get("SYNC_SQL_DIR", d.sql_dir),
    )
    return validate(settings)
