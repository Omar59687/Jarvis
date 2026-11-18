# P2-A2 — Evidence-backed chat

## Boundary

The authenticated M03-B2 chat route uses the configured local `ModelProvider` for both general chat and, only when the paired session has an explicitly selected workspace, `DocumentRAG.answer_with_memories`. RAG no longer imports Ollama settings or requires an Ollama model allowlist. Each adapter maps the shared `temperature` and output-token options to its native request. The model and provider remain explicitly configured at service startup; there is no discovery or fallback.

Workspace mode requires both an explicit trusted actor and an explicit existing SQLite database (or an injected `Database` in tests). Startup initializes the schema and requires that actor to have READ permission on at least one existing workspace. It does not create principals, workspaces, or grants. General-only mode remains available when both workspace configuration values are omitted.

## Retrieval and answer policy

- The service checks current READ permission before entering RAG and again after RAG returns. `DocumentRAG` checks before retrieval, after document and approved-memory retrieval, immediately before provider generation, and after generation. Search and memory services retain their own authorization checks and active-source filters.
- Evidence is bounded by the existing character budget and a five-record combined cap. Documents use `[D#]`; approved memories use `[M#]`. The RAG validator only returns citations issued for that request. Invalid or malformed citations produce a generic abstaining response; the factual model text is discarded. No-evidence questions abstain without a provider call.
- The prompt treats retrieved content as untrusted data and separates active documents from approved memories. Historical and active-source memory provenance is preserved. UI provenance cards contain only bounded excerpts, opaque document/memory IDs, and workspace-relative paths. Absolute filesystem paths are never returned.
- Current-turn evidence references live only in a bounded in-memory per-session cache for ten minutes. The cache stores the workspace, citation-to-document IDs, and expiry; it stores neither question nor answer text. Only active-document citation labels can support a proposed memory. `MemoryService.propose` rechecks WRITE and source validity.
- Chat remains single-turn and ephemeral. There is no conversation history, automatic memory write, tool call, filesystem operation, or cloud route.

## API behavior

`POST /api/v1/chat` remains the same-origin, paired-session, CSRF-protected endpoint. With no selected workspace it returns the existing `{reply, model, provider}` general-chat shape. With a selected workspace it returns `reply`, `outcome`, `abstained`, safe `citations`, and a short-lived `evidence_request_id`. Outcomes include `valid_citations`, `no_authorized_evidence`, `model_abstention`, `missing_citation`, and `invalid_citations`. Search-index failure, permission failure, provider failure, and timeouts remain generic and do not disclose internal paths or exception details.

`GET /api/v1/workspaces` and `PUT /api/v1/session/workspace` remain the authenticated workspace boundary. A client-supplied actor or workspace permission is never trusted; each selection is checked against the server-bound actor in `Database`.

## Verification

Run the synthetic integration and grounding tests with:

```powershell
python -m unittest tests.test_m03_p2a_chat tests.test_rag tests.test_memory_search -v
```

The tests use a temporary SQLite database, synthetic documents, and a deterministic fake provider. Live provider behavior remains subject to the configured local provider and model being installed; no live Windows or Bionic acceptance is claimed by these tests.
