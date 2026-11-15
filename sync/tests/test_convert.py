import datetime as dt
from decimal import Decimal

import psycopg
import pytest

from sync import definition as df
from sync.convert import Converter, OutOfRange
from tests.conftest import TABLES_DIR


@pytest.fixture(scope="module")
def d():
    return df.load(TABLES_DIR / "sales_contract.toml")


def col(d, name):
    return next(c for c in d.columns if c.name == name)


def conv(d, name, value):
    return Converter(d).value(col(d, name), value)


def test_float_noise_is_removed_through_the_shortest_text(d):
    assert conv(d, "unit_price_after_tax_contract", 7839.9993000000001) == Decimal("7839.999300")
    assert conv(d, "unit_price_before_tax_contract", 0.1) == Decimal("0.100000")


def test_large_totals_fit(d):
    assert conv(d, "amount_after_tax_contract", 2239999994400.0) == Decimal("2239999994400.0000")
    assert conv(d, "price_after_tax_returned", -4199039965.0) == Decimal("-4199039965.0000")


def test_values_that_do_not_fit_are_refused_naming_the_column_only(d):
    with pytest.raises(OutOfRange) as e:
        conv(d, "amount_after_tax_contract", 1e30)
    assert "amount_after_tax_contract" in str(e.value) and "1e+30" not in str(e.value).lower()
    for bad in (float("nan"), float("inf")):
        with pytest.raises(OutOfRange):
            conv(d, "quantity_contract", bad)


def test_whole_number_columns(d):
    assert conv(d, "finished_month_no", 202808.0) == 202808 and conv(d, "finished_leadtime", -2865.0) == -2865
    with pytest.raises(OutOfRange):
        conv(d, "contract_leadtime", 12.5)
    with pytest.raises(OutOfRange):
        conv(d, "city_code", 3e12)


def test_empty_string_and_null_stay_distinct(d):
    assert conv(d, "tax_code", "") == "" and conv(d, "tax_code", None) is None
    assert conv(d, "quantity_contract", None) is None


def test_nul_character_is_refused(d):
    with pytest.raises(OutOfRange):
        conv(d, "item_name", "a\x00b")


def test_rounded_counter_is_exact(d):
    c = Converter(d)
    column = col(d, "unit_price_after_tax_contract")
    for v in (1.5, 7839.9993, 7839.9993000000001, 0.1234567, 2.0000001):
        c.value(column, v)
    # 1.5, 7839.9993, 7839.9993000000001 (repr shortest = 7839.9993) need no rounding; the last two need it
    assert c.rounded == 2


def test_half_even_rounding(d):
    assert conv(d, "unit_price_after_tax_contract", 0.0000005) == Decimal("0.000000")
    assert conv(d, "unit_price_after_tax_contract", 0.0000015) == Decimal("0.000002")


def test_datetime_is_kept_without_zone_and_date_from_datetime(d):
    moment = dt.datetime(2026, 11, 4, 22, 50, 2, 113000)
    assert conv(d, "update_time", moment) == moment and conv(d, "doc_date", moment) == dt.date(2026, 11, 4)


def test_text_round_trips_through_a_real_copy(d, sw):
    text = 'Bart 20mg - Nước SX : Italia (30 viên)\twith tab\nnewline \\ backslash "quote" \' ‘đ’'
    sw.execute("CREATE TABLE serving.convtest (t text, n numeric(24,6), i integer, e text, nul text, ts timestamp(3), dd date)")
    row = [text, Converter(d).value(col(d, "unit_price_after_tax_contract"), 7839.9993000000001), 202808, "", None,
           dt.datetime(2026, 11, 4, 22, 50, 2, 113000), dt.date(2023, 1, 3)]
    with sw.cursor() as cur:
        with cur.copy("COPY serving.convtest FROM STDIN") as cp:
            cp.write_row(row)
    got = sw.execute("SELECT t, n, i, e, nul, ts, dd FROM serving.convtest").fetchone()
    assert got == (text, Decimal("7839.999300"), 202808, "", None, dt.datetime(2026, 11, 4, 22, 50, 2, 113000), dt.date(2023, 1, 3))
