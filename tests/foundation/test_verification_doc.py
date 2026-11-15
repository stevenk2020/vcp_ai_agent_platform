"""US6: the four Phase 0 verification items are tracked with owner, status and a place for findings."""

from __future__ import annotations

import re

from conftest import REPO_ROOT

DOC = REPO_ROOT / "docs" / "phase0-verification.md"
VALID_STATUS = {"open", "in_progress", "resolved"}
COLUMNS = ["id", "question", "owner", "status", "finding", "date"]


def _rows() -> list[dict[str, str]]:
    lines = [line for line in DOC.read_text(encoding="utf-8").splitlines() if line.startswith("|")]
    header = [cell.strip() for cell in lines[0].strip("|").split("|")]
    assert header == COLUMNS, header
    rows = []
    for line in lines[2:]:  # skip header and separator
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) == len(COLUMNS), line
        rows.append(dict(zip(COLUMNS, cells)))
    return rows


def test_exactly_v1_to_v4_exist():
    assert [row["id"] for row in _rows()] == ["V-1", "V-2", "V-3", "V-4"]


def test_each_item_has_question_owner_and_valid_status():
    for row in _rows():
        assert len(row["question"]) > 20, row["id"]
        assert row["owner"], row["id"]
        assert row["status"] in VALID_STATUS, row["id"]


def test_resolved_items_carry_a_finding_and_date():
    for row in _rows():
        if row["status"] == "resolved":
            assert row["finding"], f"{row['id']} is resolved without a finding"
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["date"]), f"{row['id']} needs an ISO date"


def test_doc_states_the_sync_worker_gate():
    text = DOC.read_text(encoding="utf-8")
    assert "sync-worker feature may start when V-1, V-2 and V-3" in text
    assert "must not start until" not in text


def test_the_gate_is_met_v1_to_v3_resolved_and_v4_may_be_any_valid_status():
    rows = {row["id"]: row for row in _rows()}
    for item in ("V-1", "V-2", "V-3"):
        assert rows[item]["status"] == "resolved", item
    assert rows["V-4"]["status"] in VALID_STATUS
