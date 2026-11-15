"""Checks that stop a run before it can harm the copy. Filled in by tasks T020 (guards) and T033 (account)."""

from __future__ import annotations


class Refused(Exception):
    """A guard stopped the run; the copy is untouched. `reason_code` is one of the fixed codes."""

    def __init__(self, reason_code: str, text: str) -> None:
        super().__init__(text)
        self.reason_code, self.text = reason_code, text


class RunFailed(Exception):
    """The run cannot continue for a reason that is not a guard (for example a privileged account)."""

    def __init__(self, reason_code: str, text: str) -> None:
        super().__init__(text)
        self.reason_code, self.text = reason_code, text


def check_account(info) -> None:  # T033
    return None


def check_shape(definition, source_columns: dict[str, str]) -> dict:  # T020; returns warnings
    return {}


def check_counts(definition, settings, source_rows: int, current_rows: int, allow_empty: bool = False) -> None:  # T020
    return None
