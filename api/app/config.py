"""Settings from environment variables and secret files, with start-up validation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """The configuration is unusable; the service refuses to start."""


APP_ENVS = ("development", "production")
MAIL_BACKENDS = ("capture", "smtp", "disabled")


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"
    mail_backend: str = "capture"
    public_origin: str = "https://vcp.onepercentdata.site"
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "platform"
    postgres_user: str = "api_rw"
    postgres_password: str = ""
    redis_url: str = "redis://redis:6379/0"
    pepper: bytes = b""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    code_rate_per_email: int = 5
    code_rate_per_ip: int = 20
    verify_rate_per_ip: int = 30
    session_days: int = 7
    mail_capture_dir: str = "/var/mail-capture"
    mail_capture_ttl_minutes: int = 60
    sync_request_ttl_minutes: int = 15

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def session_seconds(self) -> int:
        return self.session_days * 86400


def validate(settings: Settings) -> Settings:
    if settings.app_env not in APP_ENVS:
        raise ConfigError(f"APP_ENV must be one of {APP_ENVS}, got {settings.app_env!r}")
    if settings.mail_backend not in MAIL_BACKENDS:
        raise ConfigError(f"MAIL_BACKEND must be one of {MAIL_BACKENDS}, got {settings.mail_backend!r}")
    production = settings.app_env == "production"
    if production and settings.mail_backend == "capture":
        raise ConfigError("MAIL_BACKEND=capture is for development and tests only and is refused in production")
    if settings.mail_backend == "smtp" and not settings.smtp_configured:
        raise ConfigError("MAIL_BACKEND=smtp needs SMTP_HOST and SMTP_FROM in secrets/api_external")
    if production and settings.mail_backend == "disabled" and not settings.google_configured:
        raise ConfigError(
            "email codes are disabled and Google sign-in is not configured: nobody could sign in "
            "(fill GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in secrets/api_external)"
        )
    if not settings.public_origin.startswith(("https://", "http://")):
        raise ConfigError("PUBLIC_ORIGIN must be an origin such as https://example.org")
    for name in ("code_rate_per_email", "code_rate_per_ip", "verify_rate_per_ip", "session_days", "sync_request_ttl_minutes"):
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


def _int(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from exc


def load_settings(env: dict[str, str] | None = None, secrets_dir: str | Path | None = None) -> Settings:
    env = dict(os.environ if env is None else env)
    directory = Path(secrets_dir or env.get("SECRETS_DIR", "/run/secrets"))
    external = parse_dotenv(_read(directory / "api_external"))
    defaults = Settings()

    settings = Settings(
        app_env=env.get("APP_ENV", defaults.app_env),
        mail_backend=env.get("MAIL_BACKEND", defaults.mail_backend),
        public_origin=env.get("PUBLIC_ORIGIN", defaults.public_origin).rstrip("/"),
        postgres_host=env.get("POSTGRES_HOST", defaults.postgres_host),
        postgres_port=_int(env, "POSTGRES_PORT_INTERNAL", defaults.postgres_port),
        postgres_db=env.get("POSTGRES_DB", defaults.postgres_db),
        postgres_user=env.get("POSTGRES_USER_API", defaults.postgres_user),
        postgres_password=_read(directory / "api_rw_password"),
        redis_url=env.get("REDIS_URL", defaults.redis_url),
        pepper=_read(directory / "auth_pepper").encode("utf-8"),
        smtp_host=external.get("SMTP_HOST", ""),
        smtp_port=_int(external, "SMTP_PORT", defaults.smtp_port),
        smtp_user=external.get("SMTP_USER", ""),
        smtp_password=external.get("SMTP_PASSWORD", ""),
        smtp_from=external.get("SMTP_FROM", ""),
        google_client_id=external.get("GOOGLE_CLIENT_ID", ""),
        google_client_secret=external.get("GOOGLE_CLIENT_SECRET", ""),
        code_rate_per_email=_int(env, "CODE_RATE_PER_EMAIL", defaults.code_rate_per_email),
        code_rate_per_ip=_int(env, "CODE_RATE_PER_IP", defaults.code_rate_per_ip),
        verify_rate_per_ip=_int(env, "VERIFY_RATE_PER_IP", defaults.verify_rate_per_ip),
        session_days=_int(env, "SESSION_DAYS", defaults.session_days),
        mail_capture_dir=env.get("MAIL_CAPTURE_DIR", defaults.mail_capture_dir),
        mail_capture_ttl_minutes=_int(env, "MAIL_CAPTURE_TTL_MINUTES", defaults.mail_capture_ttl_minutes),
        sync_request_ttl_minutes=_int(env, "SYNC_REQUEST_TTL_MINUTES", defaults.sync_request_ttl_minutes),
    )
    return validate(settings)
