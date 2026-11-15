"""Generate docs/data/<copy>-columns.md from the definition files: `python -m sync.docgen [out_dir]`."""

from __future__ import annotations

import sys
from pathlib import Path

from sync.definition import TableDefinition, load_all


def render(d: TableDefinition) -> str:
    lines = [
        f"# Columns of `serving.{d.copy}` (generated, do not edit)",
        "",
        f"Source: `{d.source}`. Generated from `sync/tables/{d.copy}.toml` by `python -m sync.docgen`; a test fails when",
        "this file is out of date. Key: " + ", ".join(f"`{k}`" for k in d.key) + ". Every column is nullable except the key.",
        "",
        "| Column | Source type | Copy type | Meaning | Sensitivity |",
        "| --- | --- | --- | --- | --- |",
    ]
    for c in d.columns:
        lines.append(f"| `{c.name}` | {c.source_type} | {c.copy_type} | {c.meaning} | {c.sensitivity} |")
    lines += ["", "## Excluded columns (never read from the source, never stored)", "", "| Column | Source type | Meaning | Reason |", "| --- | --- | --- | --- |"]
    for e in d.excluded:
        lines.append(f"| `{e.name}` | {e.source_type} | {e.meaning} | {e.reason} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path("../docs/data")
    tables = Path(argv[2]) if len(argv) > 2 else Path("tables")
    root.mkdir(parents=True, exist_ok=True)
    for d in load_all(tables):
        (root / f"{d.copy}-columns.md").write_text(render(d), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
