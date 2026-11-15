"""The definition of one copied table: parsing, checks, hash and the SQL generated from it."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SOURCE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
COPY_TYPE = re.compile(r"^(text|date|integer|timestamp\(3\)|numeric\((\d+),(\d+)\))$")
SENSITIVITY = {"none", "business_address"}


class DefinitionError(ValueError):
    """The definition file is invalid."""


@dataclass(frozen=True)
class Column:
    name: str
    source_type: str
    copy_type: str
    meaning: str
    sensitivity: str = "none"

    @property
    def numeric(self) -> tuple[int, int] | None:
        m = COPY_TYPE.match(self.copy_type)
        return (int(m.group(2)), int(m.group(3))) if m and m.group(2) else None


@dataclass(frozen=True)
class Excluded:
    name: str
    source_type: str
    meaning: str
    reason: str


@dataclass(frozen=True)
class Index:
    name: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class TableDefinition:
    source: str
    copy: str
    key: tuple[str, ...]
    version: int
    columns: tuple[Column, ...]
    excluded: tuple[Excluded, ...]
    indexes: tuple[Index, ...]
    min_fraction: float | None = None
    min_rows: int | None = None
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    # ---- names ----
    @property
    def staging(self) -> str:
        return f"{self.copy}__new"

    @property
    def old(self) -> str:
        return f"{self.copy}__old"

    @property
    def pkey(self) -> str:
        return f"{self.copy}_pkey"

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def hash(self) -> str:
        return hashlib.sha256(json.dumps(self.raw, sort_keys=True, default=str).encode()).hexdigest()

    # ---- SQL ----
    def select_sql(self) -> str:
        """Explicit column list in SQL Server syntax; never `*`, never a hint, never a filter."""
        schema, table = self.source.split(".")
        cols = ", ".join(f"[{c.name}]" for c in self.columns)
        return f"SELECT {cols} FROM [{schema}].[{table}]"

    def _column_ddl(self) -> str:
        return ", ".join(f'"{c.name}" {c.copy_type}' for c in self.columns)

    def create_table_sql(self, table: str, pkey: str) -> str:
        key = ", ".join(f'"{k}"' for k in self.key)
        return f'CREATE TABLE serving."{table}" ({self._column_ddl()}, CONSTRAINT "{pkey}" PRIMARY KEY ({key}))'

    def create_staging_sql(self) -> str:
        return self.create_table_sql(self.staging, f"{self.staging}_pkey")

    def index_sql(self, index: Index, table: str, name: str) -> str:
        cols = ", ".join(f'"{c}"' for c in index.columns)
        return f'CREATE INDEX "{name}" ON serving."{table}" ({cols})'

    def temp_index_name(self, index: Index) -> str:
        return f"{index.name}__new"


def _check_name(value: str, what: str) -> str:
    if not isinstance(value, str) or not IDENT.match(value):
        raise DefinitionError(f"{what} {value!r} is not a plain identifier")
    return value


def parse(data: dict) -> TableDefinition:
    table = data.get("table") or {}
    source, copy = table.get("source", ""), table.get("copy", "")
    if not SOURCE_NAME.match(source):
        raise DefinitionError(f"table.source {source!r} must look like schema.table")
    _check_name(copy, "table.copy")
    columns = []
    for raw in data.get("columns", []):
        name = _check_name(raw.get("name", ""), "column name")
        copy_type = raw.get("copy_type", "")
        if not COPY_TYPE.match(copy_type):
            raise DefinitionError(f"column {name}: unsupported copy_type {copy_type!r}")
        if not raw.get("meaning", "").strip():
            raise DefinitionError(f"column {name}: meaning is required")
        sensitivity = raw.get("sensitivity", "none")
        if sensitivity not in SENSITIVITY:
            raise DefinitionError(f"column {name}: sensitivity must be one of {sorted(SENSITIVITY)}")
        columns.append(Column(name, raw.get("source_type", ""), copy_type, raw["meaning"], sensitivity))
    names = [c.name for c in columns]
    if len(set(names)) != len(names):
        raise DefinitionError("duplicate column names")
    excluded = tuple(
        Excluded(_check_name(e.get("name", ""), "excluded name"), e.get("source_type", ""), e.get("meaning", ""), e.get("reason", ""))
        for e in data.get("excluded", [])
    )
    for e in excluded:
        if e.name in names:
            raise DefinitionError(f"{e.name} is both a column and excluded")
        if not e.reason.strip():
            raise DefinitionError(f"excluded column {e.name} needs a reason")
    key = tuple(table.get("key", []))
    if not key or any(k not in names for k in key):
        raise DefinitionError("every key column must be one of the columns")
    indexes = []
    for raw in data.get("indexes", []):
        cols = tuple(raw.get("columns", []))
        if any(c not in names for c in cols) or not cols:
            raise DefinitionError(f"index {raw.get('name')}: unknown column")
        indexes.append(Index(_check_name(raw.get("name", ""), "index name"), cols))
    guards = data.get("guards", {})
    return TableDefinition(
        source=source, copy=copy, key=key, version=int(table.get("version", 1)),
        columns=tuple(columns), excluded=excluded, indexes=tuple(indexes),
        min_fraction=guards.get("min_fraction"), min_rows=guards.get("min_rows"), raw=data,
    )


def load(path: str | Path) -> TableDefinition:
    with open(path, "rb") as handle:
        return parse(tomllib.load(handle))


def load_all(directory: str | Path) -> list[TableDefinition]:
    """Every `*.toml` in the directory, sorted by file name."""
    return [load(p) for p in sorted(Path(directory).glob("*.toml"))]
