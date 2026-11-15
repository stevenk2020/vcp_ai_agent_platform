"""The source side: a small interface, and the real SQL Server implementation (read-only by construction)."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("sync.source")


class SourceError(Exception):
    """Any problem talking to the source."""


class SourceUnreachable(SourceError):
    """Cannot connect, or the connection was lost."""


class SourceLoginFailed(SourceError):
    """The source refused the login."""


class SourceReadTimeout(SourceError):
    """The read (or the wait for a lock) exceeded the read limit."""


@dataclass(frozen=True)
class AccountInfo:
    sysadmin: bool
    db_owner: bool
    can_write: bool

    @property
    def privileged(self) -> str | None:
        if self.sysadmin:
            return "the source account is a member of sysadmin"
        if self.db_owner:
            return "the source account is a member of db_owner"
        if self.can_write:
            return "the source account can write to the table"
        return None


class Source(Protocol):
    """One connection to the source; used as a context manager."""

    def __enter__(self) -> "Source": ...
    def __exit__(self, *exc) -> None: ...
    def account_info(self) -> AccountInfo: ...
    def columns(self) -> dict[str, str]: ...
    def snapshot_enabled(self) -> bool: ...
    def read(self, select_sql: str, batch_size: int = 5000) -> Iterator[list[tuple]]: ...


SourceFactory = Callable[[], Source]

# Statement shapes the worker may send (checked by tests against everything the real class executes).
ALLOWED_STATEMENTS = (
    re.compile(r"^SET NOCOUNT ON$"),
    re.compile(r"^SET DEADLOCK_PRIORITY LOW$"),
    re.compile(r"^SET LOCK_TIMEOUT \d+$"),
    re.compile(r"^SET TRANSACTION ISOLATION LEVEL (READ COMMITTED|SNAPSHOT)$"),
    re.compile(r"^SELECT IS_SRVROLEMEMBER\('sysadmin'\), IS_MEMBER\('db_owner'\), "
               r"HAS_PERMS_BY_NAME\(N'[\w.]+', 'OBJECT', 'INSERT'\), HAS_PERMS_BY_NAME\(N'[\w.]+', 'OBJECT', 'UPDATE'\), "
               r"HAS_PERMS_BY_NAME\(N'[\w.]+', 'OBJECT', 'DELETE'\)$"),
    re.compile(r"^SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA\.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s$"),
    re.compile(r"^SELECT snapshot_isolation_state FROM sys\.databases WHERE name = DB_NAME\(\)$"),
    re.compile(r"^SELECT (\[\w+\](, )?)+ FROM \[\w+\]\.\[\w+\]$"),
)


def map_error(exc: Exception) -> SourceError:
    """Translate a driver exception into one of the typed errors (the message stays; the caller scrubs it)."""
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    text = " ".join(str(a) for a in exc.args)
    low = text.lower()
    if code == 18456 or "login failed" in low:
        return SourceLoginFailed(text)
    if code in (1222, 20003) or "timed out" in low or "timeout" in low:
        return SourceReadTimeout(text)
    if code in (20002, 20009, 20047, 20017, 20006) or "connect" in low or "network" in low or "server is unavailable" in low:
        return SourceUnreachable(text)
    return SourceError(text)


class SqlServerSource:
    """Read-only SQL Server access through pymssql. Every statement goes through `_run`, which records it."""

    def __init__(self, settings, source_table: str, recorder: Callable[[str], None] | None = None,
                 connect: Callable[..., object] | None = None) -> None:
        self.s = settings
        self.table = source_table  # validated as schema.table by the definition
        self.recorder = recorder
        self._connect = connect
        self.conn = None
        self.cur = None

    # ---- lifecycle ----
    def __enter__(self) -> "SqlServerSource":
        if self.s.source_encrypt != "require":
            log.warning("source encryption is %r, not 'require': the password and the data may cross the network unprotected",
                        self.s.source_encrypt)
        connect = self._connect
        if connect is None:
            import pymssql  # imported here so unit tests need no driver

            connect = pymssql.connect
        try:
            self.conn = connect(
                server=self.s.source_host, port=str(self.s.source_port), user=self.s.source_user,
                password=self.s.source_password, database=self.s.source_database,
                login_timeout=15, timeout=self.s.source_read_timeout_seconds,
                encryption=self.s.source_encrypt, autocommit=True, charset="UTF-8",
            )
            self.cur = self.conn.cursor()
            self._run("SET NOCOUNT ON")
            self._run("SET DEADLOCK_PRIORITY LOW")
            self._run(f"SET LOCK_TIMEOUT {self.s.read_timeout_ms}")
            level = "SNAPSHOT" if self.s.source_snapshot_read == "on" else "READ COMMITTED"
            self._run(f"SET TRANSACTION ISOLATION LEVEL {level}")
        except SourceError:
            raise
        except Exception as exc:
            raise map_error(exc) from None
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self.conn is not None:
                self.conn.close()
        except Exception:
            pass
        self.conn = self.cur = None

    # ---- statements ----
    def _run(self, sql: str, params: tuple | None = None) -> None:
        if self.recorder:
            self.recorder(sql)
        try:
            self.cur.execute(sql, params) if params is not None else self.cur.execute(sql)
        except Exception as exc:
            raise map_error(exc) from None

    def account_info(self) -> AccountInfo:
        t = self.table
        self._run(
            "SELECT IS_SRVROLEMEMBER('sysadmin'), IS_MEMBER('db_owner'), "
            f"HAS_PERMS_BY_NAME(N'{t}', 'OBJECT', 'INSERT'), HAS_PERMS_BY_NAME(N'{t}', 'OBJECT', 'UPDATE'), "
            f"HAS_PERMS_BY_NAME(N'{t}', 'OBJECT', 'DELETE')"
        )
        row = self.cur.fetchone() or (None,) * 5
        flags = [bool(v) for v in row]  # None (unknown) counts as not granted
        return AccountInfo(sysadmin=flags[0], db_owner=flags[1], can_write=any(flags[2:5]))

    def columns(self) -> dict[str, str]:
        schema, table = self.table.split(".")
        self._run("SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
                  (schema, table))
        return {name: dtype for name, dtype in self.cur.fetchall()}

    def snapshot_enabled(self) -> bool:
        self._run("SELECT snapshot_isolation_state FROM sys.databases WHERE name = DB_NAME()")
        row = self.cur.fetchone()
        return bool(row and row[0] == 1)

    def read(self, select_sql: str, batch_size: int = 5000) -> Iterator[list[tuple]]:
        self._run(select_sql)
        try:
            while True:
                rows = self.cur.fetchmany(batch_size)
                if not rows:
                    return
                yield [tuple(r) for r in rows]
        except Exception as exc:
            raise map_error(exc) from None
