# M01-D — Memory Approval and Reversible Deletion

## Scope and status

M01-D adds local SQLite lifecycle services for durable memories. A proposal is not a memory until an authorized approval transition completes. No importer, model, or caller-supplied boolean can approve a proposal. This slice uses the standard library only and does not add memory search, embeddings, an AI adapter, UI, or cloud calls.

## Schema v4

The v4 migration adds:

- `memory_candidates`: candidate workspace, proposer, provenance type, exact content digest, evidence-set digest (including the presented provenance snapshot), revision, state and decision timestamps. Pending content is stored for review; resolved candidate content is cleared.
- `memory_candidate_evidence`: immutable review snapshot of document ID, root ID, relative path, source hash and observation time.
- `memories`: active approved content and workspace ownership, approval attribution, content revision, state version and reversible deletion timestamp. The source candidate is unique, preventing duplicate approval.
- `memory_evidence`: evidence snapshots attached to an approved memory, with cascading cleanup on purge.
- `memory_revisions` and `memory_revision_evidence`: content/evidence snapshots for approval and later edits.
- `memory_decision_receipts`: one-time decision identifiers preventing replay.

Migration v4 is applied atomically with the previous migrations using `BEGIN IMMEDIATE`. A failed statement rolls back the DDL and schema version. v3 databases upgrade in place. Backups of schema v4 are validated for the memory tables when restored. The FTS5 document index remains separate and does not index memories.

## Service use

Import `MemoryService`, `DecisionContextIssuer` and types from `jarvis_core`. Initialize the existing `Database`, then create a service with a verifier key and a trusted-host issuer with the corresponding signing key.

A candidate must cite one or more active ingested documents in the selected workspace, or be explicitly marked user-authored with no external evidence. Mixed user-authored and document-backed provenance is rejected. Document references are checked for workspace, active root and non-deleted source state. Approval verifies the signed evidence snapshot and rechecks document identity, path, source hash and active status. Routine changes to a source's last-seen timestamp do not stale the snapshot.

The service supports `propose`, `inspect_candidate`, `edit_candidate`, `list_candidates`, `approve`, `reject`, `list_memories`, `get`, `edit`, `list_revisions` for active memories, `list_deleted`, `list_deleted_revisions` for authorized recovery history, `soft_delete`, `restore`, `purge_snapshot` and `purge`. Every operation takes explicit actor and workspace identifiers and checks the existing workspace permission table. Reads are workspace-scoped. Ordinary reads only return non-deleted approved memories. `list_revisions` also rejects deleted memories. The explicit deleted-memory recovery path requires workspace `write` permission; `list_deleted` and `list_deleted_revisions` expose the deleted content/history only to that scope. Pending, rejected and deleted content never enters normal memory reads.

Candidate edits use an expected revision. Memory edits use an expected revision; deletion and restoration use an expected state version. SQLite write transactions serialize lifecycle transitions, unique constraints prevent duplicate approval, and decision receipts prevent replay. Stale or repeated state transitions raise `LifecycleConflict` or `InvalidDecision`.

## Trusted-host approval boundary

For approval or rejection, the trusted host must inspect the candidate and its evidence, show that exact snapshot to the user, and issue a short-lived `DecisionContext` bound to actor, workspace, candidate ID, revision, content digest, evidence digest, action, timestamp and unique decision ID. The service verifies the signature and consumes its decision ID in the same transaction as the lifecycle change. The same pattern applies to purge, with a separate `purge_memory` action bound to a previously soft-deleted memory.

The storage service verifies that a configured trusted host issued the decision context. It cannot verify that a human actually clicked or authenticate a human by itself. There is no UI or authentication/session service in M01-D. The future host must authenticate its user, render the bound review snapshot, protect the signing key, and issue the context only after interactive confirmation. Never expose the signing key or issuer as an unrestricted remote API. The database and actor IDs are not a security boundary against a local process or user that can modify the database or trusted host.

## Editing, deletion and purge

Editing an approved memory keeps the existing evidence links, writes a new content/evidence revision snapshot, updates the revision and state version, and creates an audit event containing only operation metadata. Audit fields do not include raw memory text or source text.

Soft deletion hides a memory from normal reads and keeps evidence/revisions intact for restore. Restore retains the original workspace and evidence set. Purge is allowed only after soft deletion, requires workspace-management permission and a separate signed decision, and transactionally deletes the memory, evidence and revision derivatives. The one-time decision receipt and privacy-conscious audit record remain. Purge affects the live database; historical backups can retain earlier copies. This implementation does not claim secure erasure from storage media or backup archives.

## Backup, restore and limitations

Use the existing `Database.backup_to` and `Database.restore_from_backup` APIs. Backups are full database copies. Restore validates SQLite integrity, supported schema, required v4 memory tables and foreign keys before replacing a destination. Close all database handles before restore, particularly on Windows.

This is local application-level authorization. A process that can write the SQLite database can bypass service checks. The signer is a trusted-host capability, not proof of human action. Evidence rows preserve provenance snapshots even if a source is later deleted or its root revoked; new approval is blocked when evidence is inactive or changed. Memory lexical/semantic search is deliberately not added. No real personal documents are used in tests.

## Verification

Run `python -m unittest discover -s tests -v` from the repository root. M01-D tests use synthetic workspaces and temporary files. The historical pre-M01-E Linux run used Python 3.12.14 and SQLite 3.53.1: 71 tests ran, 65 passed and 6 Windows-specific ingestion checks were skipped. The maintainer reports the merged M01-A through M01-E baseline was Windows-verified; see [M01 Acceptance](M01_ACCEPTANCE.md) for remaining limitations.
