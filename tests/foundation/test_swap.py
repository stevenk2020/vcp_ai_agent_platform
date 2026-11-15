"""US2: the snapshot + atomic swap pattern keeps agent_ro access without extra grants.

Guards the ALTER DEFAULT PRIVILEGES design: a table swapped in by sync_writer must be readable
by agent_ro immediately (spec section 6.2).
"""

from __future__ import annotations

import pytest

from conftest import connect


@pytest.fixture(autouse=True)
def clean(sync_writer):
    for name in ("t", "t__new", "t__old"):
        sync_writer.execute(f"DROP TABLE IF EXISTS serving.{name}")
    yield
    for name in ("t", "t__new", "t__old"):
        sync_writer.execute(f"DROP TABLE IF EXISTS serving.{name}")


def test_swapped_in_table_is_readable_by_agent_without_extra_grant(sync_writer):
    sync_writer.execute("CREATE TABLE serving.t (id int)")
    sync_writer.execute("INSERT INTO serving.t VALUES (1)")

    # staging table, filled and checked before the swap
    sync_writer.execute("CREATE TABLE serving.t__new (id int)")
    sync_writer.execute("INSERT INTO serving.t__new VALUES (10), (20), (30)")

    with sync_writer.transaction():
        sync_writer.execute("ALTER TABLE serving.t RENAME TO t__old")
        sync_writer.execute("ALTER TABLE serving.t__new RENAME TO t")
        sync_writer.execute("DROP TABLE serving.t__old")

    with connect("agent_ro") as agent:
        assert agent.execute("SELECT count(*) FROM serving.t").fetchone() == (3,)


def test_new_table_created_after_bootstrap_is_readable_by_agent(sync_writer):
    sync_writer.execute("CREATE TABLE serving.t (id int)")
    with connect("agent_ro") as agent:
        assert agent.execute("SELECT count(*) FROM serving.t").fetchone() == (0,)
