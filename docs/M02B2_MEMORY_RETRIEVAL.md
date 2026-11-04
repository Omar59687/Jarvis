# M02-B2 — Searchable Approved Memories

**Status:** Merged in the stated `main` baseline. This slice adds a separate FTS5 index for approved active memories and an explicit mixed document/memory RAG path. M02-C adds deterministic evidence selection and context management on a review branch; it does not alter M01's approval, edit, deletion, restoration, or purge rules.

## Schema and synchronization

Schema v5 adds `memory_search(memory_id UNINDEXED, workspace_id UNINDEXED, content)` as a separate FTS5 virtual table using the existing `unicode61 remove_diacritics 2` tokenizer. The document index remains `document_search`; memory text is never inserted into it.

The v5 migration runs transactionally and idempotently through the existing migration runner. It backfills `memories` with `deleted_at IS NULL`, which contains approved memories only. Triggers on `memories` insert, update, soft-delete/restore, and hard-delete keep the index in the same SQLite transaction as the lifecycle change. Pending/rejected candidates have no trigger path into this table. A migration rollback test appends a deliberate failing statement after the v5 DDL/backfill and checks that user_version remains v4 and no index or trigger is left behind.

Backup restore validates the v5 FTS5 table schema and all three synchronization triggers before replacing a destination. Synthetic backup/restore tests query the restored index and verify that subsequent edits update it. The database backup includes the memory index; no separate index file is created.

## Search API

`MemoryService.search(actor_id, workspace_id, query, *, limit=10)` uses the existing FTS query normalization and parameterized expression builder. It checks workspace READ permission before validating the query, bounds result count to the existing maximum of 20, orders by BM25 score and then stable memory ID, and returns `MemorySearchResult` values containing the stable memory ID, workspace, bounded excerpt, score, revision, provenance kind, source status, and immutable evidence snapshots.

Only approved active memories are returned. Soft-deleted records are filtered in SQL and removed from the index by trigger; purge deletes their FTS row transactionally. Restoring a memory reindexes the current approved text. Editing removes the prior indexed text and inserts the updated text in the same transaction as the revision and audit update.

`source_status` is one of:

- `user_authored` — explicit user-authored memory with no external evidence.
- `active_sources` — every captured evidence source still matches its document ID, root, path and content hash, and its root remains active.
- `historical_sources` — at least one source was deleted, changed, renamed, or revoked after approval. The approved memory and its original evidence snapshots remain searchable, but are not represented as active document passages.

This is lexical matching, not semantic retrieval. It preserves the exact-token FTS5 behavior: Arabic clitic segmentation/stemming and cross-language Arabic-English matching are not added.

## RAG composition and citations

The original `DocumentRAG.answer(actor_id, workspace_id, question, model, ...)` remains document-only and unchanged for existing callers. The new explicit `answer_with_memories(..., memory_service=..., document_limit=3, memory_limit=3)` calls both existing workspace-scoped services through `WorkspaceEvidenceComposer`. Each service checks authorization itself; the composition layer never opens SQLite. Limits are bounded independently, the total evidence text remains bounded, and an empty combined result deterministically abstains without calling the model.

Document citations use `[D1]`, `[D2]`, and memory citations use `[M1]`, `[M2]`. The output validator checks each exact label against the evidence retrieved for that request. Mixed prompt records identify document records as active documents and memory records as approved memories, with `source_status` and captured source paths. Historical source snapshots are explicitly identified as historical memory evidence, not active document evidence. Pending, rejected, soft-deleted, and purged memories cannot enter the model prompt through `MemoryService.search()`.

The model still sees untrusted content. Prompt wording does not enforce permissions or guarantee instruction-following. Citation syntax only resolves a response reference to a retrieved item; it does not establish factual support, entailment, or truth.

## Verification

Linux:

```console
python -m compileall -q jarvis_core tests examples
python -m unittest tests.test_memory_search -v
python -m unittest tests.test_rag -v
python -m unittest discover -s tests -v
python examples/document_rag_demo.py
```

Windows PowerShell (Python 3.12):

```powershell
py -3.12 -m compileall -q jarvis_core tests examples
py -3.12 -m unittest tests.test_memory_search -v
py -3.12 -m unittest tests.test_rag -v
py -3.12 -m unittest discover -s tests -v
py -3.12 examples/document_rag_demo.py
```

Tests use synthetic data, temporary databases, and fake model responses. No live Ollama service, model download, or network access is required. The Linux run does not verify Windows filesystem behavior; rerun the Windows-specific ingestion regressions on Windows.

## Limitations

- SQLite remains unencrypted, and old backups may retain purged memory content.
- FTS5 scores and excerpts are lexical and can omit semantically relevant memories.
- Historical memory evidence is an approved snapshot. It is not evidence that the original document remains active or unchanged.
- Existing trusted-host actor IDs are not authentication. The M01 cross-workspace consent criterion remains unresolved; this API supports only one requested workspace and does not combine workspaces.
- Existing Windows path traversal race limitations remain. No new filesystem access is introduced by memory search.
- The separate FTS table is kept synchronized by lifecycle triggers; there is no user-facing index repair/rebuild command in this slice.
