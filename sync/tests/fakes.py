"""FakeSource: an in-memory source with hooks to simulate blocking, hanging, failures and accounts."""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator

from sync.source import AccountInfo, SourceReadTimeout


class FakeSource:
    def __init__(self, definition, rows: list[dict] | None = None, extra_types: dict[str, str] | None = None) -> None:
        self.rows = list(rows or [])
        self.types = {c.name: c.source_type for c in definition.columns}
        for e in definition.excluded:
            self.types[e.name] = e.source_type
        self.types.update(extra_types or {})
        self.account = AccountInfo(False, False, False)
        self.snapshot = False
        self.connect_failures: list[Exception] = []   # raised one per __enter__
        self.read_failures: list[Exception] = []      # raised one per read
        self.block: threading.Event | None = None     # read waits until set (a source that is still committing)
        self.hang = False                             # read raises SourceReadTimeout
        self.read_calls = 0
        self.enter_calls = 0
        self.statements: list[str] = []               # the SELECT texts received
        self.in_read = threading.Event()

    # the object is its own factory and its own connection
    def __call__(self):
        return self

    def __enter__(self):
        self.enter_calls += 1
        if self.connect_failures:
            raise self.connect_failures.pop(0)
        return self

    def __exit__(self, *exc) -> None:
        return None

    def account_info(self) -> AccountInfo:
        return self.account

    def columns(self) -> dict[str, str]:
        return dict(self.types)

    def snapshot_enabled(self) -> bool:
        return self.snapshot

    def read(self, select_sql: str, batch_size: int = 5000) -> Iterator[list[tuple]]:
        self.read_calls += 1
        self.statements.append(select_sql)
        self.in_read.set()
        if self.read_failures:
            raise self.read_failures.pop(0)
        if self.hang:
            raise SourceReadTimeout("Adaptive Server connection timed out")
        if self.block is not None and not self.snapshot:
            self.block.wait(30)
        names = re.findall(r"\[(\w+)\]", select_sql.split(" FROM ")[0])
        data = [tuple(row.get(n) for n in names) for row in self.rows]
        for i in range(0, len(data), batch_size):
            yield data[i:i + batch_size]
