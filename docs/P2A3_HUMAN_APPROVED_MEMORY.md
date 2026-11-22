# P2-A3 — Human-approved memory candidates

## Lifecycle

Candidate endpoints reuse the existing `MemoryService`; the HTTP layer does not duplicate candidate storage or approval logic. A paired session supplies a server-bound actor and selected workspace. Mutations require exact Host/Origin, same-origin CSRF, current READ and WRITE grants, bounded JSON, and a per-session mutation limit. The optional `expected_workspace_id` request field is a stale-tab consistency guard only; the service compares it to the session-bound selection and never treats it as identity or authorization. The core repeats permission checks in its write transactions.

- `POST /api/v1/memory-candidates` creates a pending candidate only after a user submits the editor. Evidence-backed proposals require an unexpired evidence request from the same session and active workspace. Only exact `[D#]` labels from a valid-citation chat response resolve to document IDs. `[M#]`, unknown, repeated, stale, or cross-workspace labels fail. A user-authored proposal must be explicitly marked and cannot include document citation fields.
- `GET /api/v1/memory-candidates` lists at most 50 pending candidates; `GET /api/v1/memory-candidates/{id}` inspects one pending candidate. Cross-workspace, missing, and unavailable candidate IDs remain indistinguishable.
- `PUT /api/v1/memory-candidates/{id}` edits a pending candidate using an exact expected revision. It re-resolves the selected source labels through the short-lived session cache, and `MemoryService.edit_candidate` recaptures active source hashes.
- `POST /api/v1/memory-candidates/{id}/approve` and `/reject` require the client to submit the exact revision, content hash, evidence hash, and `confirmed_snapshot: true` rendered by the review UI. The service then issues the trusted short-lived `DecisionContext`; `MemoryService` verifies the signature and exact snapshot, rechecks current WRITE permission, validates source freshness, and consumes a replay receipt transactionally.

Model output never calls these endpoints or obtains a decision context. Chat only returns a response and safe evidence cards. The pending candidate UI requires a separate user edit/review action and explicit approve/reject click. The signing key is random, process-local, and never returned or persisted; a restart makes prior contexts unusable. No memory is auto-approved.

Candidate content, source excerpts, and conversation are not written to chat history. The existing memory tables retain pending/approved/rejected candidate state and audit records. Approved memory contents are only searchable after the core approve transaction commits.

## Safe response fields

Candidate JSON contains candidate ID, workspace ID, pending state, revision, content and evidence digests, provenance kind, and source identity/provenance (document/root IDs, workspace-relative path, source digest, observation timestamp). It contains no absolute path or source file body. Chat provenance cards contain bounded excerpts; only the current request's validated citation records are exposed as citations. Invalid model citations produce an abstaining response with retrieved records labeled as evidence, not citations.

## Test plan

```powershell
python -m unittest tests.test_m03_p2a_chat tests.test_memory tests.test_memory_search -v
```

The tests use disposable SQLite databases and synthetic sources with a deterministic fake provider. They cover missing CSRF, READ/WRITE boundaries, source-label validation, explicit review, stale snapshot rejection, edit revision changes, approval, rejection, and replay rejection. Tests do not call a live model or real user workspace.
