# Jarvis P4 — Read-Only Acceptance

Date: 2026-11-22
Integration baseline: b231d6f
Branch: integration/p4-stabilization-20261122

## Decision

GO for the read-only foundation.
NO-GO for real agent execution.

## Evidence

- Full Python regression: 306 tests; 297 passed, 9 skipped.
- Node UI regression: 18 passed, 0 failed.
- Security-focused regression: 23 tests; 22 passed, 1 skipped.
- Python compilation: passed.
- Windows diagnostic: 6 PASS, 0 FAIL, 1 SKIPPED, 7 UNVERIFIED.
- Overall execution-isolation result: NOT_READY.
- Git working tree: clean at acceptance.
- Windows browser smoke: authenticated test workspace, archived
  task visibility, read-only detail, lifecycle history and
  hiding/clearing archived task verified manually.
- Browser smoke used a disposable Schema v11 database and
  synthetic provider. No real model or agent execution.
- Automated tests cover v10-to-v11 migration, backup/restore,
  rollback, tombstone immutability and historical purge receipts.

## Limitations

- Windows filesystem, process and network isolation are unverified.
- Real worker execution and agent filesystem writes remain disabled.
- Browser testing was manual, not a complete automated E2E suite.
- Dashboard task listing retains its 100-task service window.
- Historical backups and SQLite residual storage may contain
  previously recorded content-derived digests.
- The owner's personal Jarvis database was not migrated during
  browser acceptance.
- Final independent release review is required before merging.

## Release boundary

No automatic purge, unrestricted agent execution, deployment,
or promotion to main is authorized by this acceptance.
