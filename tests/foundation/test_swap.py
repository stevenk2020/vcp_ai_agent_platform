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


def test_swap_with_primary_key_and_indexes_frees_the_old_names_and_stays_readable(sync_writer):
    """The sync worker's swap (feature 004): drop the old table inside the transaction so index names are free."""
    for name in ("pk_t", "pk_t__new", "pk_t__old"):
        sync_writer.execute(f"DROP TABLE IF EXISTS serving.{name}")
    try:
        sync_writer.execute("CREATE TABLE serving.pk_t (a text, b text, v int, CONSTRAINT pk_t_pkey PRIMARY KEY (a, b))")
        sync_writer.execute("CREATE INDEX pk_t_v_idx ON serving.pk_t (v)")
        sync_writer.execute("INSERT INTO serving.pk_t VALUES ('x', '1', 1)")
        sync_writer.execute("CREATE TABLE serving.pk_t__new (a text, b text, v int, CONSTRAINT pk_t__new_pkey PRIMARY KEY (a, b))")
        sync_writer.execute("CREATE INDEX pk_t_v_idx__new ON serving.pk_t__new (v)")
        sync_writer.execute("INSERT INTO serving.pk_t__new VALUES ('x', '1', 1), ('y', '2', 2), ('z', '3', 3)")
        with sync_writer.transaction():
            sync_writer.execute("ALTER TABLE serving.pk_t RENAME TO pk_t__old")
            sync_writer.execute("DROP TABLE serving.pk_t__old")
            sync_writer.execute("ALTER TABLE serving.pk_t__new RENAME TO pk_t")
            sync_writer.execute("ALTER TABLE serving.pk_t RENAME CONSTRAINT pk_t__new_pkey TO pk_t_pkey")
            sync_writer.execute("ALTER INDEX serving.pk_t_v_idx__new RENAME TO pk_t_v_idx")
        names = sorted(r[0] for r in sync_writer.execute("SELECT indexname FROM pg_indexes WHERE schemaname='serving' AND tablename='pk_t'").fetchall())
        assert names == ["pk_t_pkey", "pk_t_v_idx"]
        with connect("agent_ro") as agent:
            assert agent.execute("SELECT count(*) FROM serving.pk_t").fetchone() == (3,)
    finally:
        for name in ("pk_t", "pk_t__new", "pk_t__old"):
            sync_writer.execute(f"DROP TABLE IF EXISTS serving.{name}")
