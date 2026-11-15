from collections import Counter
from pathlib import Path

import pytest

from sync import definition as df
from sync import docgen
from tests.conftest import TABLES_DIR

REPO_DOC = Path("/repo-docs/data/sales_contract-columns.md")


@pytest.fixture(scope="module")
def d():
    return df.load(TABLES_DIR / "sales_contract.toml")


def test_62_columns_with_the_documented_types(d):
    assert len(d.columns) == 62
    counts = Counter(c.copy_type for c in d.columns)
    assert counts == {"text": 29, "date": 3, "timestamp(3)": 2, "integer": 10, "numeric(20,4)": 4,
                      "numeric(24,6)": 2, "numeric(24,4)": 12}
    whole = {"finished_year", "finished_month_no", "finished_quarter_no", "finished_leadtime", "confirmed_year",
             "confirmed_month_no", "confirmed_quarter_no", "confirmed_leadtime", "contract_leadtime", "city_code"}
    assert {c.name for c in d.columns if c.copy_type == "integer"} == whole


def test_every_column_has_a_meaning_and_a_known_sensitivity(d):
    assert all(c.meaning.strip() for c in d.columns)
    assert {c.name for c in d.columns if c.sensitivity == "business_address"} == {"customer_address", "address"}


def test_excluded_columns_are_never_selected_or_stored(d):
    assert [e.name for e in d.excluded] == ["employee_code_bdd", "employee_name_bdd"]
    assert not {e.name for e in d.excluded} & set(d.column_names)
    sql = d.select_sql() + d.create_staging_sql()
    assert "employee_code_bdd" not in sql and "employee_name_bdd" not in sql


def test_key_indexes_and_names(d):
    assert d.key == ("entity", "row_id")
    assert [i.columns for i in d.indexes] == [("entity",), ("confirmed_date",), ("finished_date",), ("customer_code",),
                                              ("code_old",), ("cat_1", "cat_2")]
    assert d.staging == "sales_contract__new" and d.old == "sales_contract__old" and d.pkey == "sales_contract_pkey"


def test_select_is_explicit_and_hint_free(d):
    sql = d.select_sql()
    assert "*" not in sql and "NOLOCK" not in sql.upper() and "WITH (" not in sql.upper() and "WHERE" not in sql.upper()
    assert sql.startswith("SELECT [entity]") and sql.endswith("FROM [dbo].[sales_contract]")


def test_hash_changes_with_content(d):
    other = df.parse({**d.raw, "table": {**d.raw["table"], "version": 2}})
    assert d.hash() == df.load(TABLES_DIR / "sales_contract.toml").hash() and d.hash() != other.hash()


def base():
    return {"table": {"source": "dbo.t", "copy": "t", "key": ["id"]},
            "columns": [{"name": "id", "source_type": "nvarchar", "copy_type": "text", "meaning": "x"}]}


@pytest.mark.parametrize("mutate", [
    lambda b: b["columns"][0].update(meaning=""),
    lambda b: b["columns"][0].update(copy_type="varchar"),
    lambda b: b["columns"][0].update(name="id; drop"),
    lambda b: b["columns"][0].update(sensitivity="secret"),
    lambda b: b["table"].update(key=["missing"]),
    lambda b: b["table"].update(source="no_schema"),
    lambda b: b.update(excluded=[{"name": "id", "reason": "r"}]),
    lambda b: b.update(excluded=[{"name": "zzz", "reason": ""}]),
    lambda b: b.update(indexes=[{"name": "i", "columns": ["nope"]}]),
    lambda b: b["columns"].append(dict(b["columns"][0])),
])
def test_invalid_definitions_are_refused(mutate):
    data = base()
    mutate(data)
    with pytest.raises(df.DefinitionError):
        df.parse(data)


def test_the_committed_dictionary_matches_the_definition(d):
    committed = Path("/repo-docs/data/sales_contract-columns.md")
    if not committed.exists():
        pytest.skip("docs are mounted only in the test image build (see compose)")
    assert committed.read_text(encoding="utf-8") == docgen.render(d)
