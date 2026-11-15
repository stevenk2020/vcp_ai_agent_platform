"""US2: agent_ro session defaults (read-only, 30 s timeout, search_path = serving)."""

from __future__ import annotations

import time

import pytest
from psycopg import errors

from conftest import connect


def test_agent_session_defaults(agent_ro):
    assert agent_ro.execute("SHOW default_transaction_read_only").fetchone() == ("on",)
    assert agent_ro.execute("SHOW statement_timeout").fetchone() == ("30s",)
    assert agent_ro.execute("SHOW search_path").fetchone() == ("serving",)


def test_new_transactions_are_read_only(agent_ro):
    assert agent_ro.execute("SHOW transaction_read_only").fetchone() == ("on",)


@pytest.mark.slow
def test_long_query_is_cancelled_at_30_seconds():
    with connect("agent_ro") as conn:
        start = time.monotonic()
        with pytest.raises(errors.QueryCanceled):
            conn.execute("SELECT pg_sleep(31)")
        elapsed = time.monotonic() - start
    assert 29 <= elapsed <= 33, f"cancelled after {elapsed:.1f}s"
