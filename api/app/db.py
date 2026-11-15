"""Async Postgres pool for the API role."""

from __future__ import annotations

from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        conninfo = (
            f"host={settings.postgres_host} port={settings.postgres_port} dbname={settings.postgres_db} "
            f"user={settings.postgres_user} password={settings.postgres_password} connect_timeout=5"
        )
        self.pool = AsyncConnectionPool(
            conninfo, min_size=1, max_size=10, open=False, kwargs={"row_factory": dict_row}
        )

    async def open(self) -> None:
        await self.pool.open(wait=True, timeout=20)

    async def close(self) -> None:
        await self.pool.close()

    @asynccontextmanager
    async def transaction(self):
        """A connection inside a transaction: committed on normal exit, rolled back on an exception."""
        async with self.pool.connection() as conn:
            async with conn.transaction():
                yield conn

    async def ping(self) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("SELECT 1")
