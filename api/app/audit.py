"""Audit trail writer and a logging filter that keeps secrets out of logs."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from app.clock import Clock

FORBIDDEN_KEYS = frozenset(
    {"code", "token", "password", "secret", "cookie", "authorization", "pepper", "id_token", "access_token"}
)

# audit `detail` additionally never carries an access request's own text (only has_name / has_reason flags);
# "name" is not in FORBIDDEN_KEYS because the log filter strips those from log records and LogRecord has a `name`
DETAIL_FORBIDDEN_KEYS = FORBIDDEN_KEYS | {"name", "reason_text"}

ACTIONS = frozenset(
    {
        "code_requested", "code_send_failed", "code_verified", "code_failed", "code_locked",
        "google_signin", "signin_pending", "signin_refused", "rate_limited",
        "session_created", "session_revoked", "signed_out",
        "allowlist_added", "allowlist_removed",
        "user_approved", "user_rejected", "user_suspended", "role_changed",
        "first_admin_created", "first_admin_refused", "last_admin_blocked", "access_requested",
        "sync_requested",
    }
)


def _check_detail(value: Any, path: str = "detail") -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            if str(key).lower() in DETAIL_FORBIDDEN_KEYS:
                raise ValueError(f"audit {path} must not contain the key {key!r}")
            _check_detail(inner, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_detail(item, path)


async def record(
    conn,
    clock: Clock,
    action: str,
    outcome: str = "success",
    *,
    actor_user_id: Any = None,
    actor_email: str | None = None,
    target: str | None = None,
    ip: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Insert one audit event. The detail may never contain a code, token or other secret."""
    if action not in ACTIONS:
        raise ValueError(f"unknown audit action {action!r}")
    detail = detail or {}
    _check_detail(detail)
    await conn.execute(
        "INSERT INTO app.audit_events (at, actor_user_id, actor_email, action, target, outcome, ip, detail) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s::inet, %s)",
        (clock.now(), actor_user_id, actor_email, action, target, outcome, ip, Jsonb(detail)),
    )


_STANDARD = set(logging.LogRecord("x", 0, "x", 0, "", (), None).__dict__) | {"message", "asctime"}


class SecretScrubFilter(logging.Filter):
    """Drops fields named like secrets from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        for name in list(record.__dict__):
            if name.lower() in FORBIDDEN_KEYS:
                delattr(record, name)
        if isinstance(record.args, dict):
            record.args = {k: v for k, v in record.args.items() if str(k).lower() not in FORBIDDEN_KEYS}
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and key.lower() not in FORBIDDEN_KEYS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if any(getattr(h, "_agent_platform", False) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler._agent_platform = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SecretScrubFilter())
    root.addHandler(handler)
    root.setLevel(level)
