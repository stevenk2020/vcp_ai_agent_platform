"""The derived state rules, table-driven from the same cases file the API tests use."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from sync.state import derive_state

CASES = json.loads((Path(__file__).parent / "state_cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_state_rules(case):
    row = dict(case["row"])
    if row["data_as_of"] is not None:
        row["data_as_of"] = datetime.fromisoformat(row["data_as_of"])
    got = derive_state(row, datetime.fromisoformat(case["now"]))
    assert got == case["expect"]


def test_the_cases_cover_all_four_states():
    assert {c["expect"]["state"] for c in CASES} == {"no_data", "healthy", "stale", "failing"}
