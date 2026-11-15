"""Turn source values into exact, typed Postgres values (research R-8). Floats never reach the copy as floats."""

from __future__ import annotations

import datetime as dt
import math
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from sync.definition import Column, TableDefinition

INT_MIN, INT_MAX = -(2**31), 2**31 - 1


class OutOfRange(ValueError):
    """A value does not fit its column. The message names the column, never the value."""

    def __init__(self, column: str, why: str) -> None:
        super().__init__(f"value in column {column} {why}")
        self.column = column


class Converter:
    """Converts one source row at a time and counts values that had to be rounded."""

    def __init__(self, definition: TableDefinition) -> None:
        self.columns = definition.columns
        self.rounded = 0

    def row(self, source_row: tuple) -> list:
        return [self.value(c, v) for c, v in zip(self.columns, source_row)]

    def value(self, column: Column, v):
        if v is None:
            return None
        t = column.copy_type
        if t == "text":
            text = v if isinstance(v, str) else str(v)
            if "\x00" in text:
                raise OutOfRange(column.name, "contains a NUL character")
            return text
        if t == "date":
            return v.date() if isinstance(v, dt.datetime) else v
        if t == "timestamp(3)":
            return v
        if t == "integer":
            return self._integer(column, v)
        scale = column.numeric
        return self._numeric(column, v, *scale)

    def _integer(self, column: Column, v) -> int:
        try:
            whole = v == int(v)
        except (ValueError, OverflowError):
            whole = False
        if not whole:
            raise OutOfRange(column.name, "is not a whole number")
        n = int(v)
        if not INT_MIN <= n <= INT_MAX:
            raise OutOfRange(column.name, "is out of the integer range")
        return n

    def _numeric(self, column: Column, v, precision: int, scale: int) -> Decimal:
        if isinstance(v, float):
            if math.isnan(v) or math.isinf(v):
                raise OutOfRange(column.name, "is not a finite number")
            exact = Decimal(repr(v))  # shortest round-trip text: 7839.9993 stays 7839.9993
        else:
            exact = Decimal(v) if not isinstance(v, Decimal) else v
        if not exact.is_finite() or exact.adjusted() + 1 > precision - scale:
            raise OutOfRange(column.name, f"does not fit numeric({precision},{scale})")
        try:
            rounded = exact.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_EVEN)
        except InvalidOperation:
            raise OutOfRange(column.name, f"does not fit numeric({precision},{scale})") from None
        if rounded != exact:
            self.rounded += 1
        return rounded
