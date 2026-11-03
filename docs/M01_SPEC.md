# M01 — Second Brain Foundation Specification

**Status:** acceptance criteria proposed; not implemented.  
**Next proposed task:** M01-A — SQLite storage and permissions. M01-A requires explicit user approval before work begins.

## 1. Goal

Deliver a local-first foundation for personal and project knowledge that stores evidence-linked memories, searches only authorized workspaces, synchronizes only approved folders, asks before permanent memory writes, and supports reversible deletion.

M01 does not require an LLM, embeddings, voice, MCP, browser automation, REA, coding agents, or Mission Mode.

## 2. M01 scope

### In scope

- Local persistence and schema migrations.
- Personal and project workspace identities.
- Workspace-scoped memory and evidence records.
- Approval workflow for proposed permanent memories.
- Reversible deletion and explicit purge.
- User-selected folder roots and bounded synchronization.
- Search with workspace isolation.
- Audit events for permissions, approvals, sync, deletion, restore, and purge.
- Export/backup and restore path appropriate to the chosen storage design.

### Out of scope

- Semantic/vector search and model-generated memories.
- Cloud synchronization or multi-user collaboration.
- Voice, wake word, desktop shell, knowledge galaxy.
- External connectors, arbitrary MCP servers, REA, OpenCode, coding agents, or automated computer control.
- Autonomous execution.

## 3. Proposed core concepts

- **Workspace:** personal or project context, with an explicit stable ID and type.
- **Memory:** user-approved durable fact/note with status and workspace ownership.
- **Evidence:** source citation or captured excerpt supporting a memory; includes source identity and observed/imported timestamp.
- **Source:** an approved file or user-entered source; tracks path/URL and provenance without implying trust.
- **Pending memory:** proposed content not yet included in ordinary retrieval.
- **Sync root:** user-approved canonical folder path and revocable configuration.
- **Audit event:** append-oriented record of approval, access, sync, restore, deletion, or purge.
- **Tombstone:** reversible deletion state and restoration metadata.

Exact schema is an M01-A design deliverable, not prescribed here.

## 4. Acceptance criteria

### A. Local storage and migrations

1. The application creates and opens its database under a documented per-user application data location.
2. Database access and migrations work without network access, cloud credentials, model installation, or a database server.
3. Every schema change has a versioned migration and a documented backup/recovery approach.
4. Migration tests cover a fresh database, upgrade from the prior schema, interrupted/failed migration recovery, and backup restore.
5. The runtime reports whether FTS5 is available; search degrades safely or blocks launch with a clear message if required storage/search capability is absent.
6. Queries use parameter binding; malformed content cannot alter SQL structure.
7. No personal corpus or credentials are checked into the repository or emitted in ordinary logs.

### B. Workspace boundaries and permissions

1. Personal and project workspaces have distinct IDs and types.
2. Every memory/evidence/source query is scoped to an explicit workspace in the trusted service layer.
3. A caller with no permission receives no cross-workspace records, evidence, counts, or distinguishing metadata through search, suggestions, or exports.
4. Cross-workspace retrieval is denied by default and requires a recorded, user-confirmed, narrowly scoped approval.
5. Tests include attempts to bypass UI controls through direct API/storage calls.
6. A sync root grants access only to its selected path; path traversal, junctions/symlinks, and paths outside the approved root are rejected or safely handled.
7. Revoking a sync root prevents later scans from reading it and records the revocation.

### C. Evidence and memory approval

1. Each persisted memory includes workspace, creation/update timestamps, status, and at least one evidence link or an explicit user-authored/no-external-evidence marker.
2. A model or importer may create a pending candidate only; it cannot mark a candidate approved.
3. Permanent memory insertion requires a direct user approval event tied to the displayed candidate and evidence set.
4. Rejection leaves no permanent memory; pending content is excluded from normal search and retrieval.
5. Displayed evidence includes enough source identity and location to let the user verify it.
6. Editing an approved memory preserves or updates provenance and records the change.

### D. Synchronization

1. Sync is opt-in per root and can be paused or revoked.
2. The system records source identity, last-seen state, errors, and changed/deleted file status without executing file contents.
3. File removal or rename does not silently hard-delete a memory previously approved from that source.
4. Import handles duplicate content idempotently and reports failures rather than silently skipping them.
5. Archive extraction, symlink/junction traversal, unsupported file types, and oversized inputs have explicit safe limits before parsing is implemented.

### E. Reversible deletion and purge

1. Ordinary deletion marks a memory deleted and removes it from normal search.
2. The user can restore a deleted memory, retaining its workspace and evidence links.
3. Purge is a distinct confirmed action and documents whether backups retain older copies and for how long.
4. Purge removes or invalidates all active indexes, cached derivatives, and evidence references associated with the record.
5. Tests verify deleted items cannot appear in normal search or cross-workspace results before restore.

### F. Search and usability

1. Keyword search returns only records from the selected authorized workspace.
2. Results expose evidence/source references and deletion/pending states are not mixed with approved results.
3. Arabic and English text can be stored and returned without data loss; normalization/ranking quality is a later measured task.
4. The product handles empty, malformed, and oversized queries with clear feedback.
5. Search behavior is documented as lexical for M01; no semantic-search claim is made.

### G. Audit, recovery, and review

1. Approval, rejection, cross-workspace authorization, sync changes, restore, and purge produce reviewable audit events.
2. Audit records avoid storing secrets or unnecessary raw personal content.
3. A documented backup can be restored into an isolated test location.
4. The review package reports tested scenarios and known limitations; no test or security property is claimed without execution evidence.

## 5. M01-A work breakdown

M01-A should define and implement only the persistence foundation:

- Select SQLite runtime/build and verify its capabilities.
- Propose schema and migration mechanism for workspaces, permissions, memories, evidence, sources, pending approvals, tombstones, and audit events.
- Implement data access that requires explicit workspace context.
- Define permission checks for read/write/restore/purge.
- Add focused tests for migration and isolation after the user approves implementation.
- Document database location, backup, restore, and purge semantics.

Folder watching/import, UI, AI, and connectors should remain separate follow-up tasks so storage authorization can be reviewed before integrations depend on it.

## 6. M01 exit gate

M01 is complete only when all acceptance criteria relevant to the shipped scope have reproducible verification evidence, security/privacy review findings are addressed or explicitly accepted, backup/restore and migration behavior are demonstrated, and the user approves the milestone gate.
