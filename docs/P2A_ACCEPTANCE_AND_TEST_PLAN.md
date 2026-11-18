# P2-A — Acceptance and Test Plan

**Status:** proposed test plan for [P2-A design](P2A_AUTHENTICATED_MEMORY_CHAT_DESIGN.md). No tests or implementation are added by this design branch.

## Test fixtures

Use temporary SQLite databases initialized with existing schema v5, explicit workspaces `ws-a` and `ws-b`, principals `alice` and `bob`, and deterministic in-process fake providers. Use approved synthetic Markdown/text sources only. Never use personal files or a real model in automated tests. Keep the provider transport fake; no Ollama/LM Studio process, model download, or cloud endpoint is needed.

## A. Identity, pairing, and workspace authorization

1. Pairing binds the configured server principal to `SessionRecord.actor_id`; browser JSON fields `actor_id`, `principal_id`, or `user_id` cannot change it.
2. Missing principal configuration, nonexistent principal grants, missing/expired sessions, bad pairing code, expired pairing, replayed pairing, wrong Host/Origin, and bad CSRF fail closed before retrieval or provider invocation.
3. `GET /api/v1/workspaces` returns only `Database.list_workspaces(session.actor_id)` results. It never accepts an actor selector.
4. Workspace selection succeeds only if `Database.get_workspace` grants READ. Unknown and unauthorized IDs yield the same public result. A workspace with only WRITE but no READ is not listed/selectable for chat.
5. Chat contains no workspace or actor authority fields. It uses the session's active workspace. No selected workspace returns a safe conflict without touching the provider.
6. A direct guessed ID, stale active selection, permission revocation before the request, cross-workspace document ID, or cross-workspace candidate ID never returns data or reaches the provider.
7. READ-only sessions may chat and inspect candidates, but cannot propose, approve, or reject. WRITE grants are checked by core calls, not UI hiding.
8. Revoke the active session and confirm workspace/chat/candidate endpoints subsequently reject it. Confirm logout clears active workspace and session citation references.

## B. Retrieval and provenance

1. Active document-only, approved-memory-only, and mixed evidence each produce the expected `[D#]` and `[M#]` labels and exact core provenance.
2. Changed, deleted, or revoked source memories remain marked `historical_sources`, cite `[M#]`, and cannot establish current state alone. User-authored memories remain marked `user_authored` with no fabricated source path.
3. Pending, rejected, deleted, or inaccessible memories never enter generation context or memory FTS results.
4. Search scopes remain one workspace. Instrument the fake generator and assert no doc, memory, path, or content from `ws-b` is supplied to an `ws-a` request.
5. Enforce proposed combined limit of five evidence records and 2,400 excerpt characters for doc-only, memory-only, and mixed cases. Check the policy does not exceed request input and provider output caps.
6. Revoke READ after workspace selection but before chat; the API and the extra pre-generation check deny the request. Document behavior for revocation during an already running model request.

Existing regression references: `tests/test_retrieval.py::test_workspace_isolation_and_direct_api_authorization`, `tests/test_retrieval.py::test_soft_deleted_and_revoked_source_documents_are_excluded`, `tests/test_memory_search.py::test_unicode_parameterization_permissions_and_deterministic_ties`, `tests/test_memory_search.py::test_evidence_provenance_is_preserved_and_marked_historical`, and `tests/test_memory_search.py::test_historical_snapshot_can_answer_past_question_but_not_claim_current_state`.

## C. Citation and prompt-injection handling

1. Fake model returns exact valid D/M citations: response includes only matching citations and provenance cards, linked server-side to the same `RAGResult`.
2. Unknown, malformed, wrong-kind, unbracketed, or cross-request labels raise/fail as invalid; browser receives no partial answer, and error contains no model text.
3. Missing citation, no authorized evidence, model abstention, and incomplete `done` status produce distinct typed outcomes or safe generic errors as designed; none invent citations.
4. Hostile document and memory excerpts containing commands to reveal other workspaces, alter system policy, disclose secrets, or create/approve memories remain untrusted quoted evidence. No tools are passed, no API action is triggered, and only the same authorized workspace reaches the generator.
5. A valid citation with an unsupported claim is not represented as an entailment guarantee. UI source cards expose safe relative paths/IDs and active/historical/user-authored status only; no canonical absolute path is returned.

Existing regression references: `tests/test_rag.py::test_document_instructions_are_untrusted_and_cannot_change_authorization`, `tests/test_rag.py::test_invalid_model_citation_is_rejected`, `tests/test_rag.py::test_malformed_or_unsupported_references_are_rejected`, `tests/test_rag.py::test_memory_prompt_requires_exact_source_specific_citation_labels`, `tests/test_rag.py::test_mixed_context_marks_historical_memory_separately_and_untrusted`, and `tests/test_memory_search.py::test_combined_rag_denies_workspace_access_and_rejects_invalid_memory_citation`.

## D. Provider independence and failure handling

1. Run identical RAG requests through deterministic fake implementations of the common contract. Assert no Ollama class, `GenerationSettings`, `think`, `num_ctx`, or Ollama-specific error is required by RAG.
2. Run adapter regression suites for both `tests/test_ollama.py` and `tests/test_lmstudio.py`. Assert Bionic remains loopback-only, proxy-disabled, redirect-disabled, `reasoning="off"`, `store=false`, and finite-timeout.
3. Selected model is explicitly configured server-side; request bodies cannot choose another model. No discovery, download, auto-selection, fallback, or cloud route occurs.
4. Missing model, provider connection error, timeout, oversized/malformed response, invalid Unicode, tool-call output, and unexpected exception map to sanitized HTTP errors. No provider response body or traceback is logged/returned.
5. Force FTS5 unavailable/index missing and SQLite busy paths; response is unavailable/temporary and no generator call occurs.

## E. Candidate proposal and human approval

1. User explicitly requests candidate creation. The service checks WRITE and calls `MemoryService.propose`; candidate is pending, audited, and absent from `MemoryService.search`/`memory_search`.
2. Evidence-backed proposals accept only active `[D#]` labels from a live, unexpired, session/workspace-bound citation reference. Unknown/expired request IDs, `[M#]`, wrong session, wrong actor, changed workspace, stale document, revoked root, cross-workspace source, duplicate/conflicting key, and oversized content fail without durable active memory.
3. User-authored mode carries no source citation and is visibly designated as user-authored; it must not inherit model or memory citations.
4. GET review returns exact candidate content, workspace, provenance, evidence snapshots, revision, and hashes; inaccessible candidate IDs are indistinguishable from nonexistent IDs.
5. No approval occurs from chat output, candidate creation, GET, page load, or assistant suggestion. Approve/reject requires a separate POST with valid session, CSRF, WRITE permission, and current revision/digests. The server issues its own `DecisionContext` only for that snapshot.
6. Approval activates memory only after `MemoryService.approve` commits and records `approved_by`; rejection leaves it absent from search. Wrong actor, missing signature, expiry, replay, content/evidence mutation, revision conflict, inactive source, and cross-workspace evidence are rejected.
7. Browser does not submit signing keys or signed `DecisionContext`; service never accepts a caller-created approval signature. Audit omits candidate text and chat transcript.

Existing regression references: `tests/test_memory.py::test_propose_approve_retrieve_and_unicode`, `tests/test_memory.py::test_stale_candidate_content_or_evidence_rejected`, `tests/test_memory.py::test_unauthorized_approval_cross_workspace_evidence_denied`, `tests/test_memory.py::test_signed_decision_required_and_actor_bound`, `tests/test_memory.py::test_approval_decision_replay_denied`, `tests/test_memory.py::test_revoked_or_deleted_source_blocks_approval`, and `tests/test_memory_search.py::test_pending_rejected_and_approved_memories_follow_lifecycle_index`.

## F. Privacy, UI, and migration regression

1. Chat accepts one bounded message only; requests do not contain prior turns; process/browser restart clears displayed transcript and citation cache. Verify no `localStorage`, `sessionStorage`, database chat table, or body logging.
2. Citation cache stores no prompt, answer, or excerpts; enforce TTL, per-session count limit, and invalidation on logout/workspace switch/restart.
3. English and Arabic workspace selection, citation cards, candidate review and approval are keyboard accessible, RTL-correct, readable, and announce loading/error states. The user must see evidence type/status before approval.
4. `Database.current_version` stays 5 and no chat tables are created. Run existing schema v4→v5 upgrade, backup/restore, and FTS trigger tests. If future scope adds schema, stop and design a separate migration plan before coding.

## G. Required implementation verification commands

After implementation approval, run focused suites first, then complete offline regressions and UI checks:

```powershell
py -3.14 -m unittest tests.test_m03_p2a_identity tests.test_m03_p2a_chat tests.test_m03_p2a_approval -v
py -3.14 -m unittest tests.test_rag tests.test_memory tests.test_memory_search tests.test_retrieval tests.test_storage tests.test_m03_b1_local_chat tests.test_ollama tests.test_lmstudio -v
py -3.14 -m unittest discover -s tests -v
py -3.14 -m compileall -q jarvis_core prototypes tests
node --input-type=module --check < prototypes/m03_b1/app.js
node --test tests/m03_a_galaxy.test.mjs
```

Also run `git diff --check`. Use fake generators/transports in CI. Live provider use is opt-in and must remain local-only; do not claim Windows acceptance until the owner performs the approved browser smoke test with the runtime already installed and configured.
