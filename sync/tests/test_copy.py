"""US1: a complete, faithful copy of the source (scenarios 1, 2 and 5; the swap itself is in test_swap_atomic)."""

import datetime as dt
from decimal import Decimal

import pytest

from sync import definition as df
from sync.convert import Converter
from sync.runner import Runner
from sync.scrub import Scrubber
from sync.store import Store
from tests.conftest import TABLES_DIR
from tests.fakes import FakeClock, FakeSource, make_row, make_rows


@pytest.fixture
def d():
    return df.load(TABLES_DIR / "sales_contract.toml")


@pytest.fixture
def store(settings):
    st = Store(settings).connect()
    st.apply_control()
    yield st
    st.close()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def make_runner(settings, store, d, clock):
    def make(source, **kw):
        return Runner(settings, store, {d.copy: d}, source, clock=clock, sleep=lambda s: None,
                      scrubber=Scrubber(settings.secret_values), **kw)
    return make


def copy_rows(sw, d):
    cols = ", ".join(f'"{c}"' for c in d.column_names)
    e, r_ = d.column_names.index("entity"), d.column_names.index("row_id")
    return {(r[e], r[r_]): r for r in sw.execute(f'SELECT {cols} FROM serving."{d.copy}"').fetchall()}


def expected(d, row):
    return Converter(d).row(tuple(row[c.name] for c in d.columns))


def test_first_run_copies_every_row_and_value(d, store, sw, make_runner, clock):
    rows = make_rows(d, 5)
    rows[0].update(customer_name="Bệnh viện Chợ Rẫy – Cơ sở 2", remark="", tax_code=None, finished_date=None,
                   unit_price_after_tax_contract=7839.9993000000001, amount_after_tax_contract=2239999994400.0)
    rows[1].update(item_name="a\ttab\nnewline \\ backslash", confirmed_date=None, confirmed_year=None)
    source = FakeSource(d, rows)
    result = make_runner(source).run_once("sales_contract", "schedule")
    assert result.outcome == "succeeded" and result.source_rows == result.copied_rows == 5
    copy = copy_rows(sw, d)
    assert len(copy) == 5
    for row in rows:
        assert list(copy[(row["entity"], row["row_id"])]) == expected(d, row)
    first = copy[(rows[0]["entity"], rows[0]["row_id"])]
    names = d.column_names
    assert first[names.index("customer_name")] == "Bệnh viện Chợ Rẫy – Cơ sở 2"
    assert first[names.index("remark")] == "" and first[names.index("tax_code")] is None
    assert first[names.index("unit_price_after_tax_contract")] == Decimal("7839.999300")
    assert first[names.index("amount_after_tax_contract")] == Decimal("2239999994400.0000")
    run = sw.execute("SELECT * FROM serving.sync_runs").fetchall()
    assert len(run) == 1


def test_run_row_is_succeeded_with_counts_and_no_reason(d, store, make_runner):
    make_runner(FakeSource(d, make_rows(d, 4))).run_once("sales_contract", "schedule")
    r = store.conn.execute("SELECT * FROM serving.sync_runs").fetchone()
    assert (r["outcome"], r["trigger"], r["source_rows"], r["copied_rows"], r["reason_code"], r["error_text"]) == ("succeeded", "schedule", 4, 4, None, None)
    assert r["finished_at"] is not None and r["duration_ms"] >= 0 and r["requested_by"] is None


def test_second_run_replaces_the_copy_with_the_new_source_state(d, sw, make_runner):
    rows = make_rows(d, 6)
    source = FakeSource(d, rows)
    runner = make_runner(source)
    runner.run_once("sales_contract", "schedule")
    # rows 0 and 1 removed, row 2 changed, two rows added
    source.rows = [dict(r) for r in rows[2:]]
    source.rows[0]["item_name"] = "changed"
    source.rows += [make_row(d, "nufa", "new1"), make_row(d, "durabe", "new2")]
    runner.run_once("sales_contract", "schedule")
    copy = copy_rows(sw, d)
    assert set(copy) == {(r["entity"], r["row_id"]) for r in source.rows}
    assert copy[(rows[2]["entity"], rows[2]["row_id"])][d.column_names.index("item_name")] == "changed"
    assert ("vcp", "r1") not in copy and ("salud", "r0") not in copy


def test_the_copy_has_exactly_the_definition_columns_and_never_selects_the_excluded_ones(d, sw, make_runner):
    source = FakeSource(d, make_rows(d, 3))
    make_runner(source).run_once("sales_contract", "schedule")
    columns = [r[0] for r in sw.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='serving' AND table_name='sales_contract' ORDER BY ordinal_position").fetchall()]
    assert columns == d.column_names and len(columns) == 62
    assert "employee_code_bdd" not in columns and "employee_name_bdd" not in columns
    assert source.statements and all("employee_" not in s for s in source.statements)
    assert "SECRET-STAFF-DATA" not in str(sw.execute("SELECT * FROM serving.sales_contract").fetchall())


def test_state_records_the_start_of_the_read_and_the_counts(d, store, make_runner, clock):
    runner = make_runner(FakeSource(d, make_rows(d, 4)))
    started = clock()
    runner.run_once("sales_contract", "schedule")
    st = store.state("sales_contract")
    assert st["data_as_of"] == started and (st["source_rows"], st["copied_rows"], st["consecutive_failures"]) == (4, 4, 0)
    assert st["last_outcome"] == "succeeded" and st["last_reason"] is None and st["last_run_id"]
    assert (st["stale_after_seconds"], st["failing_after"]) == (1800, 3)


def test_indexes_and_key_exist_on_the_copy(d, sw, make_runner):
    make_runner(FakeSource(d, make_rows(d, 3))).run_once("sales_contract", "schedule")
    names = {r[0] for r in sw.execute("SELECT indexname FROM pg_indexes WHERE schemaname='serving' AND tablename='sales_contract'").fetchall()}
    assert names == {d.pkey} | {i.name for i in d.indexes}


def test_rounded_values_are_counted_as_a_warning(d, store, make_runner):
    rows = make_rows(d, 3)
    rows[0]["unit_price_after_tax_contract"] = 1.23456789
    make_runner(FakeSource(d, rows)).run_once("sales_contract", "schedule")
    assert store.conn.execute("SELECT warnings FROM serving.sync_runs").fetchone()["warnings"] == {"rounded_values": 1}
