"""US2: a bad source answer never replaces the copy; every refusal has a reason and leaves nothing behind."""

import dataclasses

import pytest

from sync import definition as df
from sync import guards
from sync.runner import Runner
from sync.scrub import Scrubber
from sync.store import Store
from tests.conftest import TABLES_DIR
from tests.fakes import FakeClock, FakeSource, make_row, make_rows


@pytest.fixture
def d():
    return df.load(TABLES_DIR / "sales_contract.toml")


@pytest.fixture
def clock():
    return FakeClock()


class Env:
    def __init__(self, settings, d, clock, **settings_override):
        self.settings = dataclasses.replace(settings, **settings_override)
        self.d = d
        self.clock = clock
        self.source = FakeSource(d, [])
        self.store = Store(self.settings).connect()
        self.store.apply_control()
        self.runner = Runner(self.settings, self.store, {d.copy: d}, self.source, clock=clock, sleep=lambda s: None,
                             scrubber=Scrubber(self.settings.secret_values))

    def run(self, rows=None, **kw):
        if rows is not None:
            self.source.rows = rows
        return self.runner.run_once("sales_contract", "schedule", **kw)

    def snapshot(self):
        return self.store.conn.execute(f'SELECT * FROM serving."{self.d.copy}" ORDER BY entity, row_id').fetchall()

    def clean(self):
        assert not self.store.table_exists(self.d.staging) and not self.store.table_exists(self.d.old)
        other = Store(self.settings).connect()
        try:
            assert other.try_lock(self.d.copy)           # the lock was released
        finally:
            other.close()


@pytest.fixture
def env(settings, d, clock):
    e = Env(settings, d, clock)
    yield e
    e.store.close()


def good(d, n=100, tag="g"):
    return make_rows(d, n, tag, sparse=True)


# ---------- pure count checks, at the real size ----------
def test_count_rules_at_35000_rows(settings, d):
    s = dataclasses.replace(settings, guard_min_rows=1000)
    guards.check_counts(d, s, 17_500, 35_000)                      # exactly 50% passes
    with pytest.raises(guards.Refused) as e:
        guards.check_counts(d, s, 17_499, 35_000)
    assert e.value.reason_code == "below_fraction" and "17499" in e.value.text and "35000" in e.value.text
    with pytest.raises(guards.Refused) as e:
        guards.check_counts(d, s, 0, 35_000)
    assert e.value.reason_code == "zero_rows" and e.value.text == "source returned no rows"
    with pytest.raises(guards.Refused) as e:
        guards.check_counts(d, s, 999, 0)
    assert e.value.reason_code == "below_minimum"
    guards.check_counts(d, s, 0, 35_000, allow_empty=True)


# ---------- through the runner ----------
def test_zero_rows_keep_the_copy_and_are_recorded(env, d):
    assert env.run(good(d)).outcome == "succeeded"
    before, state = env.snapshot(), env.store.state(d.copy)
    r = env.run([])
    assert (r.outcome, r.reason_code) == ("refused", "zero_rows") and r.message == "source returned no rows"
    assert env.snapshot() == before
    after = env.store.state(d.copy)
    assert after["data_as_of"] == state["data_as_of"] and after["copied_rows"] == state["copied_rows"]
    assert (after["last_outcome"], after["last_reason"], after["consecutive_failures"]) == ("refused", "zero_rows", 1)
    run = env.store.conn.execute("SELECT * FROM serving.sync_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert (run["outcome"], run["reason_code"], run["copied_rows"]) == ("refused", "zero_rows", None)
    env.clean()


def test_below_fraction_is_refused_with_both_counts_and_exactly_half_passes(env, d):
    env.run(good(d, 100))
    before = env.snapshot()
    r = env.run(good(d, 49, "h"))
    assert (r.outcome, r.reason_code) == ("refused", "below_fraction") and "49" in r.message and "100" in r.message
    assert env.snapshot() == before
    assert env.run(good(d, 50, "i")).outcome == "succeeded"
    env.clean()


def test_first_sync_below_the_minimum_keeps_an_empty_copy(settings, d, clock):
    e = Env(settings, d, clock, guard_min_rows=3)
    try:
        e.store.ensure_table(d)
        r = e.run(good(d, 2))
        assert (r.outcome, r.reason_code) == ("refused", "below_minimum")
        assert e.store.table_exists(d.copy) and e.store.row_count(d.copy) == 0
        e.clean()
    finally:
        e.store.close()


def test_thresholds_come_from_settings_and_the_definition_file_wins(settings, d, clock):
    strict = Env(settings, d, clock, guard_min_fraction=0.9)
    try:
        strict.run(good(d, 100))
        assert strict.run(good(d, 80, "s")).reason_code == "below_fraction"        # 80% < 90% (settings)
    finally:
        strict.store.close()
    relaxed_def = dataclasses.replace(d, min_fraction=0.1, min_rows=1)             # the file overrides the settings
    e = Env(settings, relaxed_def, clock, guard_min_fraction=0.9, guard_min_rows=500)
    try:
        e.run(good(d, 100))
        assert e.run(good(d, 15, "t")).outcome == "succeeded"                      # 15% >= 10%, 15 >= 1
    finally:
        e.store.close()


def test_a_refused_run_is_followed_by_a_normal_one_that_succeeds(env, d):
    env.run(good(d, 100))
    env.run([])
    assert env.store.state(d.copy)["consecutive_failures"] == 1
    assert env.run(good(d, 90, "n")).outcome == "succeeded"
    st = env.store.state(d.copy)
    assert (st["consecutive_failures"], st["last_outcome"], st["last_reason"], st["copied_rows"]) == (0, "succeeded", None, 90)


def test_consecutive_failures_count_every_refusal(env, d):
    env.run(good(d, 100))
    for i in range(3):
        env.run([])
        assert env.store.state(d.copy)["consecutive_failures"] == i + 1


def test_duplicate_key_is_refused_without_row_values(env, d):
    rows = good(d, 20)
    rows.append(make_row(d, rows[3]["entity"], rows[3]["row_id"], sparse=True))
    env.run(good(d, 20, "base"))
    before = env.snapshot()
    r = env.run(rows)
    assert (r.outcome, r.reason_code) == ("refused", "duplicate_key")
    assert rows[3]["row_id"] not in r.message and "entity" in r.message and "row_id" in r.message
    assert env.snapshot() == before
    env.clean()


def test_null_key_is_refused(env, d):
    env.run(good(d, 20, "base"))
    rows = good(d, 20)
    rows[5]["entity"] = None
    r = env.run(rows)
    assert (r.outcome, r.reason_code) == ("refused", "key_missing")
    env.clean()


def test_a_missing_or_retyped_needed_column_is_refused_listing_names_only(env, d):
    env.run(good(d, 20, "base"))
    before = env.snapshot()
    env.source.types.pop("customer_code")
    env.source.types["quantity_contract"] = "nvarchar"
    r = env.run(good(d, 20))
    assert (r.outcome, r.reason_code) == ("refused", "shape_changed")
    assert "customer_code" in r.message and "quantity_contract" in r.message and "g0" not in r.message
    assert env.snapshot() == before and env.source.read_calls == 1       # nothing was read for the refused run
    env.clean()


def test_an_extra_source_column_only_adds_a_warning(env, d):
    env.source.types["brand_new_column"] = "nvarchar"
    r = env.run(good(d, 20))
    assert r.outcome == "succeeded" and r.warnings["extra_source_columns"] == ["brand_new_column"]
    run = env.store.conn.execute("SELECT warnings FROM serving.sync_runs").fetchone()
    assert run["warnings"]["extra_source_columns"] == ["brand_new_column"]


def test_the_excluded_columns_may_disappear_or_change_without_effect(env, d):
    env.source.types.pop("employee_name_bdd")
    env.source.types["employee_code_bdd"] = "int"
    r = env.run(good(d, 20))
    assert r.outcome == "succeeded" and "extra_source_columns" not in r.warnings


@pytest.mark.parametrize("column,value", [("contract_leadtime", 12.5), ("amount_after_tax_contract", 1e30), ("city_code", 3e12)])
def test_value_out_of_range_names_the_column(env, d, column, value):
    env.run(good(d, 20, "base"))
    before = env.snapshot()
    rows = good(d, 20)
    rows[7][column] = value
    r = env.run(rows)
    assert (r.outcome, r.reason_code) == ("refused", "value_out_of_range") and column in r.message and str(value) not in r.message
    assert env.snapshot() == before
    env.clean()


def test_allow_empty_lets_a_legitimately_empty_source_replace_the_copy(env, d):
    env.run(good(d, 20))
    assert env.run([]).outcome == "refused"
    r = env.run([], allow_empty=True)
    assert r.outcome == "succeeded" and r.warnings.get("allow_empty") is True
    assert env.store.row_count(d.copy) == 0


def test_every_refusal_leaves_nothing_behind(env, d):
    env.run(good(d, 100))
    for rows in ([], good(d, 10, "x")):
        assert env.run(rows).outcome == "refused"
        env.clean()
