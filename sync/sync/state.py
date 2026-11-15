"""The derived state of a synced table (data-model.md): the same rules are implemented in the API and tested
against the same cases file (sync/tests/state_cases.json)."""

from __future__ import annotations

from datetime import datetime


def derive_state(row: dict, now: datetime) -> dict:
    """`row` holds data_as_of, consecutive_failures, stale_after_seconds, failing_after (as stored with the data)."""
    as_of = row.get("data_as_of")
    age = None if as_of is None else max(0, int((now - as_of).total_seconds()))
    if as_of is None:
        state = "no_data"
    elif row["consecutive_failures"] >= row["failing_after"]:
        state = "failing"
    elif age > row["stale_after_seconds"]:
        state = "stale"
    else:
        state = "healthy"
    return {"state": state, "age_seconds": age}
