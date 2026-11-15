"""Documentation names only commands that exist (feature 004, T035)."""

from __future__ import annotations

import re

from conftest import REPO_ROOT

DOCS = ["docs/sync.md", "docs/vps-setup.md", "docs/restore.md", "README.md"]


def _targets() -> set[str]:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    return set(re.findall(r"^([A-Za-z][\w-]*):", makefile, flags=re.M))


def test_every_make_target_in_the_docs_exists():
    targets = _targets()
    missing = []
    for doc in DOCS:
        text = (REPO_ROOT / doc).read_text(encoding="utf-8")
        for name in re.findall(r"`make ([A-Za-z][\w-]*)", text):
            if name not in targets:
                missing.append((doc, name))
    assert not missing, missing


def test_the_sync_runbook_mentions_every_new_command():
    text = (REPO_ROOT / "docs/sync.md").read_text(encoding="utf-8")
    for name in ("make source-secret", "make sync-status", "make sync-now", "SOURCE_ENCRYPT", "SOURCE_SNAPSHOT_READ", "--allow-empty"):
        assert name in text, name
