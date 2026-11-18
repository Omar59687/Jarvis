# P2-A — Authenticated Memory and Evidence Chat Design

**Status:** Proposed design only. No production code changes are included in this branch. This proposal is based on M03-B2 commit `346b7ceee4fcc0330ef59e8c1537e73c5df6cf3f`; it does not authorize implementation or replace the existing M01/M02/M03 security decisions.

**Goal:** let a paired Jarvis browser ask one question against one explicitly selected workspace's active documents and approved memories, receive an answer with request-scoped citations and provenance, and stage/approve durable memories only through an explicit authenticated human action.

## 1. Decisions proposed

1. Keep the existing M03-B2 loopback service and session lifecycle. Bind each session server-side to one configured `actor_id`; do not accept actor identity from JSON, URL, headers, or browser state.
2. Make workspace selection a server-validated session operation. The browser can request a workspace ID, but the service must call `Database.get_workspace(actor_id, workspace_id)` before storing it as the session's active workspace. Every later operation rechecks READ or WRITE permission using that server-held actor and workspace.
3. Reuse `SearchIndex`, `MemoryService`, `WorkspaceEvidenceComposer`, and `DocumentRAG.answer_with_memories`. Do not reproduce their SQL, FTS filtering, provenance, or citation parsing in HTTP handlers.
4. Keep chat single-turn and ephemeral. Do not persist questions, answers, or conversation turns. Bionic already sends `store: false`; Ollama receives a single request with no prior turn.
5. Keep all model output untrusted. The existing RAG prompt hardening, strict citation parser, typed outcomes, and no-evidence abstention remain mandatory. A valid citation proves that a label was issued for the request, not that the cited text entails the claim.
6. Keep candidate proposal separate from approval. A pending candidate is not an active memory. Only an explicit, authenticated, CSRF-protected approve action may call `MemoryService.approve`; the service creates a short-lived decision context for the exact candidate snapshot and never accepts a client-signed context.
7. Use the existing SQLite schema v5. This first slice needs no migration because it adds no chat-history table and reuses existing candidate, evidence, receipt, memory, revision, and FTS5 tables. Any future conversation persistence or memory-to-memory provenance requires a separately reviewed migration.
8. No cloud provider, tool, agent, filesystem action, voice feature, or multi-workspace query is introduced.

## 2. What the inspected code implements today

### Identity and permission primitives

- `Database` has workspace principals and explicit `read`, `write`, `manage_sources`, and `manage_workspace` grants. `create_workspace` grants the initial owner all four permissions; later grants/revocations require `manage_workspace`. See [`storage.py`](../jarvis_core/storage.py#L53), [`storage.py`](../jarvis_core/storage.py#L144), [`storage.py`](../jarvis_core/storage.py#L656), [`storage.py`](../jarvis_core/storage.py#L722), and [`storage.py`](../jarvis_core/storage.py#L820).
- `Database.list_workspaces(actor_id)` returns only workspaces with a READ grant, and `get_workspace` checks READ. The core explicitly warns that `actor_id` must come from a trusted host, not an HTTP client. See [`storage.py`](../jarvis_core/storage.py#L1) and [`storage.py`](../jarvis_core/storage.py#L790).
- M03-B2 pairing creates a one-use terminal code and an opaque cookie/CSRF session. `SessionRecord` currently stores no actor or workspace, and `LocalChatServer` does not own a `Database`. Pairing proves possession of the terminal code; it does not currently map a browser to a Jarvis principal. See [`service.py`](../prototypes/m03_b1/service.py#L90), [`service.py`](../prototypes/m03_b1/service.py#L120), [`service.py`](../prototypes/m03_b1/service.py#L129), and [`service.py`](../prototypes/m03_b1/service.py#L209).
- M03-B2 chat checks the session cookie, CSRF, bounded body, rate limit, and provider errors, then calls the model. It has no workspace selector or memory retrieval. See [`service.py`](../prototypes/m03_b1/service.py#L471) and [`service.py`](../prototypes/m03_b1/service.py#L492).

### Retrieval, evidence, and memory lifecycle

- `FolderSynchronizer.sync` checks READ and WRITE, obtains an active approved root, and persists bounded ingestion. Existing ingestion scans approved roots; P2-A must not open or read arbitrary paths itself. See [`ingestion.py`](../jarvis_core/ingestion.py#L409).
- `SearchIndex.search(actor_id, workspace_id, ...)` checks READ before query/FTS work, filters documents to the requested workspace, active roots, and non-deleted rows, and returns bounded excerpts plus document/root/path IDs. See [`retrieval.py`](../jarvis_core/retrieval.py#L96) and [`retrieval.py`](../jarvis_core/retrieval.py#L109).
- `MemoryService.search` checks READ for the same workspace, searches only non-deleted approved memories, and labels provenance as `user_authored`, `active_sources`, or `historical_sources`. It returns evidence snapshots containing source document/root/path, source SHA-256, and observation time. See [`memory.py`](../jarvis_core/memory.py#L95) and [`memory.py`](../jarvis_core/memory.py#L430).
- `WorkspaceEvidenceComposer.retrieve` calls document and memory search using the same actor/workspace/query. `EvidenceSelector` assigns request-local `[D#]` and `[M#]` labels, separates active documents from memory provenance, deduplicates conservatively, and budgets excerpts. `DocumentRAG.answer_with_memories` uses that composer. See [`rag.py`](../jarvis_core/rag.py#L136), [`rag.py`](../jarvis_core/rag.py#L223), and [`rag.py`](../jarvis_core/rag.py#L277).
- The RAG prompt JSON-encodes user question/evidence boundaries, labels evidence untrusted, tells the model not to follow evidence instructions, distinguishes historical memories, and requires exact citation labels. The result parser rejects unknown/malformed citations, abstains on missing citations, and rejects incomplete generation. See [`rag.py`](../jarvis_core/rag.py#L107), [`rag.py`](../jarvis_core/rag.py#L314), and [`rag.py`](../jarvis_core/rag.py#L406).
- `MemoryService.propose` requires WRITE, captures only active same-workspace document evidence or an explicit user-authored designation, creates a pending candidate, and audits the event. `approve` requires WRITE, verifies a short-lived context bound to actor/workspace/candidate/version/content digest/evidence digest, rechecks current evidence, consumes a one-use receipt, creates the approved memory/revision, and audits the approval. See [`memory.py`](../jarvis_core/memory.py#L125), [`memory.py`](../jarvis_core/memory.py#L254), [`memory.py`](../jarvis_core/memory.py#L287), and [`memory.py`](../jarvis_core/memory.py#L361).
- The decision-context module explicitly says a signature proves trusted-host issuance, **not** that a human clicked. The host must keep its signing key private. See [`memory.py`](../jarvis_core/memory.py#L1) and [`memory.py`](../jarvis_core/memory.py#L125).
- Schema v4 added candidate, evidence, memory, revision, and decision-receipt tables; v5 added the approved-memory FTS index and triggers. The current database schema version is 5. See [`storage.py`](../jarvis_core/storage.py#L22) and [`storage.py`](../jarvis_core/storage.py#L365).

### Provider/RAG mismatch to resolve

M03-B2 adds `ModelProvider` and `GenerationResult`, but `rag.py` still defines its own Ollama-oriented `Generator` requiring `allowed_models`, imports `GenerationSettings` and `OllamaConfigurationError`, and passes Ollama-specific `seed`, `num_ctx`, `num_predict`, and `think` settings. The Bionic adapter has no `allowed_models` set and currently fixes temperature in its payload. Therefore `LMStudioBionicClient` cannot currently be passed directly to `DocumentRAG`; P2-A must bridge this contract before wiring chat. Compare [`model_provider.py`](../jarvis_core/model_provider.py#L40), [`lmstudio.py`](../jarvis_core/lmstudio.py#L72), and [`rag.py`](../jarvis_core/rag.py#L60), [`rag.py`](../jarvis_core/rag.py#L300), [`rag.py`](../jarvis_core/rag.py#L380).

## 3. Authentication and identity mapping

### Initial P2-A scope: one configured local principal

The current product has no account table, login provider, or user-management API. The smallest safe first integration is one Jarvis principal configured by the trusted launcher/service configuration, for example `--actor-id <existing-principal-id>` (or an equivalent protected local settings value). The ID must already have permission rows in the selected database. If missing, unknown, or invalid, startup or workspace access fails closed; do not select the first workspace, owner, or principal automatically.

At successful pairing, the service copies that trusted configured ID into the process-local `SessionRecord.actor_id`. It is immutable for the lifetime of the session. The browser never submits an actor ID and never receives one as authority. A JSON `actor_id` field is rejected or ignored and must never affect authorization. This maps the paired browser to the single configured local principal; it is not a multi-user identity system.

The one-time pairing credential remains displayed only in the trusted terminal, short-lived, one-use, and outside URLs/logs. Pairing authenticates browser possession to this local service; the service-side configuration supplies the principal mapping. For an initial single-user alpha, all paired sessions use this principal. A multi-user deployment must add a reviewed identity provider/account binding before it is in scope; it must not infer identity from a browser-supplied name, workspace permission, model response, or cookie value.

### Session and active-workspace state

Extend `SessionRecord` with server-owned `actor_id` and nullable `active_workspace_id`. Preserve hashed cookie lookup, CSRF token, idle/absolute expiry, in-flight/rate-limit state, and logout revocation. A session is never upgraded by changing a client field.

Workspace selection is a separate authenticated operation:

1. `GET /api/v1/workspaces` resolves the session and calls `Database.list_workspaces(session.actor_id)`.
2. `PUT /api/v1/session/workspace` accepts one candidate `workspace_id`; require exact Host/Origin, valid session, CSRF, bounded JSON, and successful `Database.get_workspace(session.actor_id, workspace_id)` before storing it on the session.
3. Every search/chat/proposal/review/approval request uses `session.actor_id` and `session.active_workspace_id`. It rechecks the appropriate permission in the relevant core service at request time. If the active workspace is revoked or unavailable, clear it and fail closed.
4. A chat payload does not select a workspace; it contains only the user message. Thus a request cannot silently switch workspaces. To ask in another workspace, the user explicitly selects it first.

Workspace IDs are selectors, not capabilities. A guessed ID, changed UI state, stale session selection, or direct API call grants no access. `AccessDenied` should map to one generic unavailable response so clients cannot distinguish an unknown workspace from a known but inaccessible one. Do not concatenate multiple workspaces or provide an “all workspaces” default.

**Permission distinction:** chat and workspace listing need READ. Candidate creation and approval use the existing WRITE requirement. P2-A does not introduce a separate approver role; adding one requires a separately reviewed permission/schema decision. The same authorized actor may propose and approve, but must perform a distinct review/approve action.

## 4. Permission-filtered evidence and provider-independent RAG

### Request flow

For each one-turn `POST /api/v1/chat`:

1. Validate Host, Origin, session, CSRF, content type, body length, message length, and rate/concurrency limits before retrieval or provider invocation.
2. Read `actor_id` and `active_workspace_id` only from the resolved session. Require a selected workspace.
3. Call the existing document and memory APIs through `DocumentRAG.answer_with_memories(actor_id, workspace_id, question, ...)`. Never accept a client-supplied evidence excerpt, source status, path, citation label, or permission result.
4. The composer calls `SearchIndex.search` and `MemoryService.search` independently for exactly the same workspace. Each checks READ and filters its own data. Before model generation, perform one additional current READ check to catch a revoked grant between composition and generation. Permission revocation during an already-running local generation takes effect on the next request; cancellation of in-flight inference is not available in the current provider contract and is a documented residual risk.
5. Do not call the provider if no authorized evidence is found. Return the existing deterministic no-evidence abstention with no citations.
6. Return only an accepted `RAGResult`; never return raw provider response objects or unvalidated model citation syntax.

### Provider-neutral contract change

Before wiring RAG to providers, make `rag.py` consume the shared generic contract rather than `OllamaClient` details:

- Add a vendor-neutral generation-options value containing at least `temperature` and `max_output_tokens`. Keep finite timeout policy in configured provider instances (already present as `timeout` in the M03-B2 contract); if per-request timeout override is added, clamp it centrally.
- A provider instance is constructed for one explicit, allowlisted model at service startup. Validation is performed through a provider-neutral `validate_model`/configured-model contract; do not let a request choose an arbitrary model. Both Ollama and Bionic receive the same selected model from trusted service configuration.
- Map generic `temperature=0.0` and `max_output_tokens=192` to each adapter. Keep provider-specific context/window or seed settings inside the adapter and do not require them from RAG. Bionic must continue sending `reasoning: "off"` and `store: false`; Ollama must retain explicit local model checks and remote/cloud metadata rejection.
- RAG should raise/map provider-neutral configuration, timeout, connection, protocol, and incomplete-generation errors. Keep HTTP error bodies, provider paths, and stack traces out of browser responses.

### Evidence size and citations

The existing evidence text budget is 2,400 characters and each search currently accepts a source-specific limit up to five; the combined selector can therefore return more than five items when both source types are present. P2-A should make the effective **combined** cap explicit (proposed: at most five evidence records total and at most 2,400 excerpt characters total), split fairly when both types are available while preserving rank order. This is a bounded policy adjustment in the RAG layer, not a retrieval algorithm rewrite. Tests must cover document-only, memory-only, and mixed results so one source cannot exceed the combined cap.

Citations are allocated per answer request. Join `RAGResult.citations` to its `RAGResult.evidence` by the server-issued citation ID and return only cited records in the answer's citation list. Do not accept client citations as authority. Return compact source cards containing citation label, source type, memory source status/provenance where applicable, and safe relative path/ID; do not return canonical absolute paths or full source files. If the evidence drawer also offers uncited retrieved items, label them “retrieved context” rather than citations.

For active documents, cite the active document ID/root ID/relative path returned by `SearchIndex`. For memories, preserve `memory_id`, `provenance_kind`, `source_status`, and immutable source snapshots. `historical_sources` stays visibly historical and can support a past-tense question, but cannot establish current state by itself. `user_authored` memories have no document evidence and must not be presented as document-backed.

## 5. Prompt injection and untrusted content

Keep the current defense-in-depth policy: documents, approved memories, user messages, and model outputs are untrusted. Evidence is encoded as JSON records under explicit sections; it never becomes a system instruction. The system prompt says to ignore instructions inside evidence, cite exact issued labels, abstain when evidence is insufficient, and state conflicts. Preserve the active-vs-historical distinction.

Authorization is enforced before prompt assembly. Prompt text is not an authorization mechanism. Never give the model tools, filesystem access, memory APIs, provider credentials, or a route to approve its own suggestion. Validate the answer's citations and `done=True` completion before displaying it. Invalid/malformed citations produce a generic failed-answer response and no answer text; do not strip an invalid citation and accept the remainder. Citation validity is not entailment verification, so UI copy must not imply that a citation proves truth.

New tests must include hostile document and memory text requesting cross-workspace disclosure, prompt-policy replacement, secret exposure, or invented labels. Assert the text remains quoted/untrusted input, no out-of-scope search occurs, invalid labels are rejected, and the generator receives no data from another workspace.

## 6. API contracts and failure behavior

All routes remain same-origin, loopback-only, versioned under `/api/v1`, with exact Host/Origin policy, no CORS, CSRF for state-changing routes, bounded request bodies, and generic error responses.

| Route | Request / response | Authority and result |
|---|---|---|
| `GET /api/v1/session` | Existing session response plus `active_workspace_id` or `null`; never return actor ID | Cookie session only |
| `GET /api/v1/workspaces` | `[{id, name, kind}]` | Server calls `Database.list_workspaces(session.actor_id)`; READ grants only |
| `PUT /api/v1/session/workspace` | `{workspace_id}` → `{active_workspace_id}` | CSRF + server calls `get_workspace` before setting session state |
| `POST /api/v1/chat` | `{message}` → `{answer, outcome, citations, request_id}` | Session actor/workspace only; single turn; uses `answer_with_memories`; no client evidence or model fields |
| `POST /api/v1/memory-candidates` | `{content, provenance, citation_labels}`; user-authored mode has no citations | Requires WRITE. Service resolves citation labels from a bounded, session/workspace-bound request cache, allows only active `[D#]` evidence for evidence-backed candidates, then calls `MemoryService.propose` with document IDs. `M#` evidence cannot be converted into a document-source claim. |
| `GET /api/v1/memory-candidates/{id}` | Candidate snapshot, revision, content digest, safe evidence card data, pending state | READ; core loads by both workspace and candidate ID; hide inaccessible IDs as unavailable |
| `POST /api/v1/memory-candidates/{id}/approve` | `{expected_revision, expected_content_sha256, expected_evidence_sha256}` → approved memory identity/status | CSRF + WRITE + explicit confirmation UI. Service loads/rendered snapshot, compares revision/digests, issues its own `DecisionContext`, calls `MemoryService.approve`, and returns only after commit. No decision signature is accepted from the browser. |
| `POST /api/v1/memory-candidates/{id}/reject` | Same snapshot precondition → rejected state | CSRF + WRITE + distinct explicit reject action; service issues its own reject context and calls `MemoryService.reject` |

For the memory proposal request, “provenance” is a strict enum: `evidence` or `user_authored`. Evidence mode requires one or more citation labels resolving to currently active document citations; user-authored mode requires no citations and is clearly labeled as such in review. Content is bounded well below the core 50,000-character ceiling (proposed API limit: 1,000 characters) and is editable before approval.

The citation lookup cache retains only `{request_id, session_id_hash, actor_id, workspace_id, citation_label -> source_type/document_id}` for a short TTL (proposed 10 minutes) and bounded count. It stores no question, answer, evidence excerpt, or prompt. Expiry, session revocation, workspace change, or service restart invalidates it. A stale request ID or `[M#]` proposal source is rejected. `MemoryService.propose` then independently revalidates current active source identity and captures digest/observed time. If this cache is judged unnecessary, the simpler alternative is for the user to select current active document IDs directly in the review UI; either way the server must validate ownership/active status and never trust client-provided provenance fields.

### Status/error mapping

- `400 invalid_request`: malformed JSON, invalid message/provenance, bad limits.
- `401 authentication_required`: missing, expired, revoked, or invalid session.
- `403 csrf_rejected` / `origin_not_allowed`: failed CSRF or Origin; no provider call.
- `404 workspace_unavailable` / `candidate_unavailable`: absent and unauthorized workspace/candidate are indistinguishable.
- `409 workspace_required`, `candidate_changed`, `candidate_not_pending`, `chat_in_progress`, or `rate_limited`: safe state conflict; do not leak private content.
- `413 request_too_large`: body above route cap.
- `503 search_unavailable` / `model_unavailable`: FTS missing, database busy, or local provider unavailable; sanitized.
- `504 generation_timeout`: finite provider timeout.
- `502 invalid_model_answer` / `generation_failed`: malformed provider output, incomplete generation, unknown/invalid citation, or unexpected internal exception. Never return the raw answer on invalid citations.

No route logs message bodies, evidence excerpts, pairing/session/CSRF tokens, provider response bodies, absolute paths, or exception text. Operational logs may include a generated request ID, route name, status/error code, and elapsed time only.

## 7. Memory approval and provenance policy

P2-A does not allow model-side memory writes. A human explicitly invokes “Propose as memory” (or manually drafts a memory) in the selected workspace. `POST /memory-candidates` creates only a pending candidate; the approved-memory index must remain unchanged. The user sees exact candidate text, provenance type, workspace, evidence citations, source paths, and snapshot status before a separate approval action.

The approval POST must be a distinct action with a final confirmation in the UI. The trusted service reads the latest candidate and source snapshot, compares the submitted revision/digests as stale-view guards, issues a new signed context internally, and invokes the existing approval method. `MemoryService` remains the authority for current WRITE permission, candidate state, evidence digest, active-source checks, context age, and replay receipt. Only after approval commits may the memory appear in `MemoryService.search`.

For this single-process alpha, generate one 256-bit random decision key at service startup with `secrets.token_bytes(32)` and pass the same in-memory key to `DecisionContextIssuer` and `MemoryService`. Never accept it from a browser or persist/log it. Restarting the service invalidates any in-flight signed context; candidate rows remain pending and can be reviewed again. If key persistence is later required, design protected key storage and rotation separately rather than reusing the SQLite database as a secret store.

This first slice attaches new evidence-backed candidates only to active document citations (`[D#]`). The existing schema stores memory-to-document provenance snapshots but no memory-to-memory edges; promoting a derived claim from `[M#]` as if it had direct document evidence would misstate provenance. For a user-authored note based on remembered information, require an explicit `user_authored` designation and no evidence links. A future schema can add memory-to-memory provenance if requested, with migration and provenance tests.

`DecisionContextIssuer` does not cryptographically prove a person clicked. P2-A's application-level consent evidence is: an authenticated session bound to the configured principal, CSRF-protected explicit approve POST, a separately rendered exact candidate snapshot, exact revision/digest preconditions, server-only signature issuance, one-use consumption, and the existing `memory.approved` audit event. The residual risk of same-origin script compromise or a person who has the local pairing credential remains; do not describe the HMAC alone as human authentication.

## 8. Context, history, retention, and migrations

- P2-A stays single-turn: no transcript table, no server-side chat log, no previous turns added to generation, and no localStorage/sessionStorage for content. The browser may keep the current displayed turn in volatile memory until reload/close, as M03-B1 does.
- Keep provider generation finite (M03-B2 default 120 s, hard ceiling 300 s), input bounded (retain 1,000-character user message unless a tested need changes it), output bounded (retain 16,000 characters at service boundary), and retrieval bounded (proposed combined evidence cap 5 records / 2,400 excerpt characters). Enforce limits before model invocation and on generated output.
- Bionic uses `store:false`; Ollama uses one non-stream generation request and receives no prior turns. Do not assume third-party runtime diagnostic logs are disabled; document configured local-runtime retention and cloud-routing prerequisites.
- Do not save queries, responses, or candidate suggestions in the audit event. Candidate lifecycle audit records existing metadata such as actor, workspace, event type, and evidence count; candidate content remains in the lifecycle tables only until approval/rejection rules clear it.
- **Migration:** none for this design. `Database.initialize()` already migrates schema v1–v5 atomically; schema v4 and v5 contain the memory lifecycle and search features. Add a new migration only if later scope explicitly requires durable conversations, a user/account table, separate approver permissions, or memory-to-memory provenance. Any such migration must include upgrade, rollback/failure, backup/restore, FTS trigger, and privacy-retention tests.

## 9. Threat model and residual risk

| Threat / asset | P2-A control | Residual risk |
|---|---|---|
| Forged actor ID or principal escalation | Actor comes from trusted service configuration and is copied to session at pairing; actor fields from clients never enter core calls | Single configured principal is single-user only; compromise of the trusted launcher/config or same-user process defeats the boundary |
| Guessed or cross-workspace ID | Server validates workspace selection via `get_workspace`, then session binds it; every service call rechecks DB permission; no multi-workspace query | In-flight generation may finish after a concurrent permission revoke; next request is denied |
| Revoked/deleted source in answer | Existing SearchIndex filters deleted docs/revoked roots; MemoryService marks changed/deleted/revoked provenance historical; memory evidence is revalidated at approval | Approved historical memory remains available for past questions by design; user must see historical status |
| Prompt injection in documents/memories | Treat evidence as untrusted JSON, no tools/actions, same-workspace retrieval before generation, strict citation parser | Models may still follow malicious text or make unsupported claims; citations do not prove entailment |
| Citation fabrication or reuse | Per-request labels, server-side citation resolution, strict `InvalidCitationError`, no client-selected labels as authority | Model may make unsupported claim against a valid cited item; no entailment verifier exists |
| Silent memory approval / replay | Candidate remains pending; separate approve/reject actions; exact snapshot `DecisionContext`, 5-minute lifetime, one-use receipt, current source recheck, audit | Signature alone does not prove a human clicked; XSS, browser extension, or stolen paired session may issue an action as the local principal |
| CSRF / hostile web origin | Preserve exact Host/Origin, SameSite cookie, CSRF header, no CORS, strict CSP/static allowlist, bounded early body handling | Plain HTTP loopback and local port races remain; browser vulnerabilities/extensions and malicious same-user processes are out of scope |
| Prompt/history leakage | No persistent chat, no history forwarding, no body logging, Bionic `store:false`, bounded request/response | Runtime-level diagnostics are outside Jarvis; local runtime must be configured locally and its logging understood |
| Resource exhaustion | Existing request caps/handler cap/rate limits plus RAG total evidence and output cap; finite model timeout; bounded candidate and cache TTL/count | Inference can still consume local CPU/GPU/RAM; no per-model resource isolation is implemented |

## 10. Dependencies, changed files, and implementation impact

No new production package is proposed. Reuse Python stdlib HTTP/SQLite, `Database`, `SearchIndex`, `MemoryService`, `DocumentRAG`, the M03-B2 provider adapters, and bundled local UI assets.

Expected implementation changes after design approval:

| File | Proposed change |
|---|---|
| `prototypes/m03_b1/service.py` | Inject `Database`/services/provider and trusted configured actor; extend process-local session identity/workspace binding; add workspace, memory-chat, candidate review/approval routes; preserve M03-B2 security controls |
| `jarvis_core/rag.py` | Replace Ollama-only generator settings/errors with shared provider-neutral options/errors; enforce combined evidence limits; add pre-generation permission recheck if not delegated to service |
| `jarvis_core/model_provider.py` | Define common options/model validation contract compatible with both adapters; keep timeout finite and provider error taxonomy stable |
| `prototypes/m03_b1/app.js`, `index.html`, `chat.css` | Add accessible bilingual workspace selection, citation/evidence panel, candidate editor/review and explicit confirmation; keep transcript volatile and no direct provider calls |
| `tests/test_m03_p2a_*` | Add endpoint/session integration, workspace denial, citation response, and approval consent regression suites |
| `tests/test_rag.py`, `tests/test_memory.py`, `tests/test_memory_search.py` | Add provider-neutral generator, aggregate context bounds, workspace revocation timing, and API-to-core provenance cases while retaining existing unit tests |
| `docs/ROADMAP.md`, `docs/DECISIONS.md` | Mark P2-A design as proposed and record approval gate; no implementation status until code and tests exist |

Do not modify schema or add dependencies in the first implementation unless tests demonstrate a requirement that cannot be met with existing schema/interfaces. If a principal provisioning CLI is needed, scope it as trusted local setup rather than a browser account-management endpoint.

## 11. Implementation stages and gates

Detailed observable acceptance tests are in [P2-A acceptance and test plan](P2A_ACCEPTANCE_AND_TEST_PLAN.md). The proposed independent review slices are:

1. **P2-A1 — Trusted principal and workspace session binding.** Add explicit service config, actor-bound sessions, workspace list/select routes, READ/WRITE checks, and API error mapping. No RAG yet. Gate: spoofed actor/workspace inputs cannot bypass DB grants; existing M03-B2 pairing/Host/Origin/CSRF tests remain green.
2. **P2-A2 — Provider-neutral, one-workspace evidence chat.** Refactor RAG to the shared provider contract, connect existing SearchIndex + MemoryService + composer, add combined evidence limits and citation responses, and preserve no-history operation. Gate: fake provider tests for doc-only/memory-only/mixed/historical/no-evidence/invalid-citation paths; cross-workspace and revoked permission never reach provider; Ollama and Bionic adapter tests stay green.
3. **P2-A3 — Human-reviewed candidate lifecycle.** Add user-triggered proposal and candidate review/approve/reject routes using existing MemoryService/DecisionContext flow. Gate: proposal is pending only; memory search unchanged before approval; only explicit matching snapshot approve activates it; stale/replayed/wrong actor/cross-workspace/CSRF failures do not write.
4. **P2-A4 — Bilingual UI, privacy, regression, and owner smoke acceptance.** Wire selector, evidence panel, and explicit memory confirmation; verify accessibility/RTL, no transcript persistence, all Python/Node regressions, Windows provider smoke with fake tests plus owner-authorized local provider test. Gate: independent code review before any production integration or broader multi-user claim.

Do not start P2-A implementation until this design is approved. Do not create a PR, merge, or change `main` without separate owner approval.
