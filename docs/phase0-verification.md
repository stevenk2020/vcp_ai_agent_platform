# Phase 0 verification items: `dbo.sales_contract`

These four questions about the source system decide how the sync worker is built. They are open
until someone answers them with evidence. **The sync-worker feature may start when V-1, V-2 and V-3
are `resolved`.** V-4 (snapshot isolation on the source) is an optional improvement: the worker
works without it (it waits for the source's own commit) and switches to snapshot reads through the
setting `SOURCE_SNAPSHOT_READ=on` once the DBA enables it. Amended 2026-11-15 (feature 004, task T005):
the constitution (Development Workflow) names only the real row counts and the one-transaction question,
both answered; the earlier wording required every item.

Status values: `open`, `in_progress`, `resolved`.

| id | question | owner | status | finding | date |
| --- | --- | --- | --- | --- | --- |
| V-1 | Obtain read access to the source SQL Server with a read-only account (check what the current account in `.env` can do) | Kien | resolved | The account in `.env` is SELECT-only: not sysadmin, not db_owner (confirmed by the operator). The SQL Server port is reachable from this VPS (TCP check only, no login attempted from here yet). | 2026-11-15 |
| V-2 | Measure the real row count and the delete-insert cycle of `dbo.sales_contract` (how often it runs, how long the table is empty or partial) | Kien | resolved | About 40k rows. The job runs about every 10 minutes; each run takes about 2 minutes (operator, 2026-11-15). | 2026-11-15 |
| V-3 | Does the source delete-insert job run in a single transaction? (ask the table owner) | Kien | resolved | Yes: the DELETE and the INSERT run in one commit (operator, 2026-11-15). With read-committed isolation a reader is blocked until the commit, not shown an empty table; verify with the sync design. | 2026-11-15 |
| V-4 | Can the DBA enable snapshot isolation (`ALLOW_SNAPSHOT_ISOLATION`) on the source database? | Kien | in_progress | Request sent to the DBA; still OFF as of 2026-11-15. Operator will report when it is ON. | 2026-11-15 |

## How to record a finding

Change `status` to `resolved`, write the answer in `finding` (numbers, who confirmed, how), and put
the date in `date`. If the answer changes the sync design, say so in the finding and update
`specs/` accordingly.

## Why each one matters

- **V-1**: the sync worker needs a SELECT-only account; a writable account would break least
  privilege (research R-5).
- **V-2** and **V-3**: decide between reading with snapshot isolation or the row-count sanity check
  with retries (spec section 6.2). Without them the agent could read a half-loaded table.
- **V-4**: if enabled, reads are consistent without extra guards.
