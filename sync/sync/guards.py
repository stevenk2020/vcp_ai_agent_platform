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


def check_account(info) -> None:  # T033 refines the message handling
    reason = info.privileged
    if reason:
        raise RunFailed("privileged_account", f"refusing to run: {reason}")


def check_shape(definition, source_columns: dict[str, str]) -> dict:
    """The columns the copy needs must exist with the same type; extra source columns only warn.

    Returns the warnings for the run record. Names only, never values.
    """
    missing = [c.name for c in definition.columns if c.name not in source_columns]
    retyped = [c.name for c in definition.columns if c.name in source_columns and source_columns[c.name] != c.source_type]
    if missing or retyped:
        parts = []
        if missing:
            parts.append("missing columns: " + ", ".join(missing))
        if retyped:
            parts.append("changed types: " + ", ".join(retyped))
        raise Refused("shape_changed", "source shape changed (" + "; ".join(parts) + ")")
    known = {c.name for c in definition.columns} | {e.name for e in definition.excluded}
    extra = sorted(set(source_columns) - known)
    return {"extra_source_columns": extra} if extra else {}


def check_counts(definition, settings, source_rows: int, current_rows: int, allow_empty: bool = False) -> None:
    """Zero rows, far fewer rows than the current copy, or fewer than the minimum: refuse and keep the copy."""
    if allow_empty:
        return
    fraction = definition.min_fraction if definition.min_fraction is not None else settings.guard_min_fraction
    minimum = definition.min_rows if definition.min_rows is not None else settings.guard_min_rows
    if source_rows == 0:
        raise Refused("zero_rows", "source returned no rows")
    if current_rows > 0 and source_rows < fraction * current_rows:
        raise Refused(
            "below_fraction",
            f"source returned {source_rows} rows, fewer than {round(fraction * 100)}% of the current copy ({current_rows})",
        )
    if source_rows < minimum:
        raise Refused("below_minimum", f"source returned {source_rows} rows, fewer than the minimum of {minimum}")
