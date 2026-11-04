# M01 — Foundation Acceptance Matrix

**Acceptance report:** M01-E implementation was merged into the `main` baseline `039746069da6734946331ff9f2d332b223dc8c87`. The project maintainer reports M01-A through M01-E were Windows-verified before this baseline. This matrix describes exercised scope and residual gaps; it does not claim the unresolved cross-workspace consent criterion is implemented.

## Acceptance matrix

| M01 criterion | Evidence in repository | M01 result / remaining item |
|---|---|---|
| A.1 per-user database location | `test_default_database_path_uses_synthetic_posix_and_windows_settings`; `default_database_path()` | Linux test exercises both path branches with synthetic settings; the maintainer reports the merged M01-A–E suite was Windows-verified. |
| A.2 offline storage and migrations | `test_core_storage_operations_do_not_use_network`; memory offline test; initialization and migration tests | Existing tests plus the M01-E integration flow run with socket creation blocked. |
| A.3–A.4 versioned migration, rollback, restore | `test_fresh_database_initialization`, `test_successful_upgrade_applies_new_migration_once`, migration rollback tests in storage/memory; v2 and v3 upgrade coverage; backup tests | Migrations v1→v4, v2→v3 and v3→v4 are covered across the suite. Combined backup coverage is added for FTS5, memory evidence and revisions. |
| A.5 FTS5 availability | retrieval capability probe and clear-failure test | Covered by `test_fts5_capability_probe_and_clear_failure`; no FTS extension is downloaded. |
| A.6 parameterized SQL and malformed input | storage, retrieval and memory SQL-like input tests | Covered by existing unit tests and preserved by this slice. |
| A.7 no personal corpus or credentials in repository/logs | synthetic fixtures; audit privacy assertions | M01-E uses temporary synthetic content. A repository scan is not proof against future accidental secrets; normal secret scanning is outside this slice. |
| B.1–B.2 workspace identity and service-layer scoping | storage, ingestion, retrieval and memory workspace tests; integration direct-call denials | Covered for current one-workspace service calls. |
| B.3 no-permission isolation | direct workspace mismatch and unauthorized actor tests; M01-E service calls | Covered for existing per-workspace APIs; direct calls deny without exposing a result. |
| B.4 cross-workspace consent | no combined retrieval API or consent receipt exists | **Open acceptance criterion.** Cross-workspace aggregation remains unsupported and denied by default. M01-E does not add consent or claim this criterion is fulfilled. A future approved scope must define the narrowly scoped consent and audit model. |
| B.5 direct API bypass attempts | existing storage/service tests and M01-E calls without UI mediation | Covered at the service API boundary for exercised operations. Authentication of the supplied actor remains the trusted host's responsibility. |
| B.6–B.7 approved roots and revocation | storage path tests; ingestion symlink/junction/root replacement tests; M01-E root registration, sync and revocation | The maintainer reports the merged M01-A–E suite was Windows-verified. Hostile concurrent path replacement cannot be fully prevented by current standard-library Windows traversal. |
| C.1–C.2 durable memory provenance and pending-only proposals | memory lifecycle tests and M01-E evidence-linked proposal | Covered with synthetic evidence and user-authored designation. No importer or model auto-approval path exists. |
| C.3–C.5 snapshot-bound approval and reviewable evidence | candidate digest, expiry, replay and stale-evidence tests; M01-E expired-context and source-change checks | Covered at the storage service. A valid signature proves trusted-host issuance, not that a human clicked; no authentication/UI exists. |
| C.6 edit and revision provenance | edit/history tests; M01-E revision evidence checks | Covered, including evidence preservation and privacy-conscious audit metadata. |
| D.1–D.5 sync and source lifecycle | ingestion tests; M01-E rename/delete/revocation flow | Covered for supported text files, bounded sync and source provenance. Windows filesystem race limitation remains. |
| E.1–E.5 reversible deletion and purge | lifecycle tests plus M01-E delete/restore/purge assertions | Covered: normal reads exclude deleted state, authorized recovery is separate, purge removes active evidence/revision derivatives. Historical backups may retain prior copies. |
| F.1–F.5 lexical search | retrieval tests; M01-E indexed-document query and backup restore query | Covered for exact-token lexical search in one authorized workspace. Arabic clitic segmentation, stemming and cross-language semantic matching are not provided. Memory search is deferred. |
| G.1 approval, rejection, sync, memory restore and purge audit | storage/memory audit tests; M01-E event counts, privacy check and lifecycle flow | Covered for exercised source and memory lifecycle events. **Gap:** offline database replacement via `restore_from_backup()` has no actor-bound audit event; it is a static maintenance operation without actor/session context. No cross-workspace consent event exists because that capability is not implemented. |

## Synthetic integration scenarios

`tests/test_foundation_integration.py` exercises:

- approved-root registration, first sync, FTS5 result provenance and excerpts;
- direct permission denials and READ-only boundaries across sync, search, proposal, edit, history and recovery operations;
- evidence-backed pending proposal, expired approval context, snapshot-bound approval, editing and revision evidence;
- source rename, deletion and root revocation while an approved memory retains its original evidence snapshot;
- rejection of approval when a source path changes or its root is revoked;
- soft deletion, reader exclusion, authorized restoration, explicit purge, derivative cleanup and replay failure;
- audit event counts and absence of raw memory text from audit metadata;
- full backup/restore retaining document FTS5 search, current memory, evidence and revision history;
- competing edits from one expected revision and stale state-version rejection;
- offline operation with socket creation blocked.

All fixture documents and memory text are synthetic and created under temporary directories.

## Restore behavior

`Database.restore_from_backup()` now owns source and target SQLite connections through `ExitStack`/`closing`. If opening the temporary target fails after the source opens, the source is closed before temporary-file cleanup, the existing destination is not touched, and the open failure propagates. The focused regression injects this failure and verifies closure and destination preservation. Restore still validates integrity, schema and foreign keys before atomic replacement. On Windows, every SQLite handle must be closed before replacing an existing database.

## Acceptance and implementation interface

The existing internal service interfaces are usable for an M02 adapter behind a trusted host: explicit actor/workspace arguments, separate storage/ingestion/search/memory services, and bounded local operations. They are not yet a stable external API contract. Before M02 exposes them beyond the process, define authentication/session identity, request/response compatibility, error mapping and audit policy. Do not treat actor IDs or the decision signer as authentication.

## Verification performed

On Linux x86_64 (`Linux-6.18.57-x86_64-with-glibc2.39`), Python 3.12.14 and SQLite 3.53.1:

- `python -m compileall -q jarvis_core tests` — passed.
- `python -m unittest tests.test_foundation_integration -v` — 3 tests passed.
- `python -m unittest discover -s tests -v` — 76 tests run: 70 passed, 6 Windows-specific skips, 0 failures/errors.

The six skips are the platform-specific Windows ingestion checks (file identity, hard links, junctions, ordinary Windows file metadata, root replacement and symlinks). These are not treated as passing Windows checks.

## Verification commands

Linux:

```console
python -m compileall -q jarvis_core tests
python -m unittest tests.test_foundation_integration -v
python -m unittest discover -s tests -v
```

Windows PowerShell:

```powershell
py -3.12 -m compileall -q jarvis_core tests
py -3.12 -m unittest tests.test_foundation_integration -v
py -3.12 -m unittest discover -s tests -v
```

The maintainer reports M01-A through M01-E were verified on Windows before merge. The report's original run details below are historical and do not substitute for retaining platform-specific CI logs for future changes.

## Known limitations and exit gate

- Cross-workspace retrieval with explicit, scoped user consent is unresolved and remains unsupported. M01 exit requires either an approved follow-up design and implementation or explicit acceptance that this confirmed requirement is deferred.
- The trusted-host signer does not authenticate a user or prove a human confirmation occurred. There is no interactive UI or auth/session service.
- Windows path-based traversal reduces but does not eliminate hostile local filesystem races. Junction and symlink checks do not replace handle-relative native traversal.
- SQLite content is unencrypted; OS account permissions and private backups are relied on. Old backups can retain purged content.
- Search is lexical-only. Arabic morphology, stemming, clitic segmentation, semantic retrieval and Arabic-English cross-language matching are not claimed.
- Windows filesystem behavior has known limits: current standard-library traversal does not eliminate hostile concurrent path replacement races.
- Cross-workspace aggregation remains unsupported and denied by default. It is an unresolved product criterion and must not be represented as implemented by the M01-E merge.
- The trusted-host signer does not authenticate a user or prove a human confirmation occurred; no UI/authentication service exists.

M01-A through M01-E are delivered and merged. The cross-workspace consent criterion remains explicitly unresolved; this report preserves it as a limitation rather than implying full criterion compliance.
