# Jarvis 2.0 — Decision Log

Decision status uses **Confirmed requirement**, **Proposed**, **Open**, or **Deferred**. A proposal becomes a project decision only after review and validation.

## Confirmed requirements

### C-01 — Local-first with optional cloud
Core storage and retrieval should not require paid APIs or subscriptions. Cloud model routing is optional and user configured.

### C-02 — Explicit memory consent
A model may suggest a durable memory, but it cannot permanently save it without explicit user approval.

### C-03 — Workspace isolation
Personal and project workspaces are separate. Cross-workspace retrieval requires permission.

### C-04 — Reversible deletion
Ordinary deletion should be recoverable. Permanent purge is a separate explicit operation.

### C-05 — Approved-folder sync
Automatic synchronization is limited to roots explicitly approved by the user and can be revoked.

### C-06 — Bilingual and Windows target
The product targets Arabic and English, Windows desktop, and a browser dashboard.

## Proposed architecture decisions

### P-01 — Enforce policy in a local application service
**Proposal:** a trusted service owns workspace checks, memory writes, tool approvals, and audit events. UI clients are not authorization authorities.  
**Reason:** desktop, browser, MCP, and agents must share one policy enforcement point.  
**Validation:** prototype local-session security and browser-origin protections before exposing an API.

### P-02 — SQLite for the initial local store
**Proposal:** begin with SQLite and migrations; evaluate FTS5 for initial full-text search.  
**Reason:** embedded storage fits a single-user local-first foundation and avoids requiring a database server.  
**Caveat:** confirm the shipped SQLite build includes FTS5; validate concurrency, backups, migration rollback/recovery, and expected data size.

### P-03 — Tauri desktop shell and shared web UI
**Proposal:** evaluate Tauri 2 with a TypeScript UI and Python local service.  
**Reason:** reuses browser UI and allows narrowly granted native desktop capabilities while Python aligns with local AI and integration libraries.  
**Trade-off:** multiple runtime boundaries, packaging, Windows WebView2/build prerequisites, and local-service lifecycle require a compatibility spike. This remains unselected until that spike.

### P-04 — Ollama as the first local model adapter
**Proposal:** evaluate Ollama behind a replaceable model interface.  
**Reason:** it supports local model serving and has an open-source runtime.  
**Caveat:** model-weight terms, Arabic quality, system RAM, context sizes, and MX450 performance are model-specific and unverified.

### P-05 — Defer vectors until retrieval evidence requires them
**Proposal:** start with scoped metadata and full-text retrieval; introduce vector indexing only after representative relevance evaluation.  
**Reason:** reduces M01 dependency and operational complexity.  
**Validation:** compare search quality on an approved bilingual corpus before choosing an embedding model or vector extension.

### P-06 — Keep autonomous execution out of the foundation
**Proposal:** M01-M07 support explicit user-directed operations; Mission Mode is deferred to M08.  
**Reason:** permissions, audit, stop controls, budgets, and recovery depend on earlier milestones.

### P-07 — Shared desktop and browser foundation
**Proposal:** keep Tauri 2 + a shared TypeScript UI + the existing Python service as the leading conditional direction for the confirmed Windows desktop and browser-dashboard target; use a browser-only Python implementation for the first low-cost contract spike. This is not an approval to build or package the production app.
**Reason:** a shared web UI can serve the two client surfaces, while Tauri offers a Windows shell and scoped native bridge. The extra Rust, C++ build, WebView2, Python-child lifecycle, and packaging surfaces are real costs. A browser-only app is simpler, but does not provide the intended native shell.
**Validation:** M03-A records the comparison and a mock health-only spike. Owner verification of Windows prerequisites, packaged service lifecycle, startup/resource measurements, and security tests is still required before selecting the stack. See [M03-A](M03A_DESKTOP_FOUNDATION.md).

### P-08 — Restricted local-chat development alpha
**Proposal:** M03-B1 may use a short-lived terminal-paired, process-local browser session to expose one bounded chat endpoint backed only by the existing Ollama adapter explicitly configured for installed `gemma3:4b`. The conversation is single-turn and ephemeral; it has no workspace, memory, file, browser, tool, or cloud access. This alpha is not the production dashboard authentication decision.
**Reason:** exercise the Knowledge Galaxy command dock with a real local model behind a small reviewable service boundary while preserving existing retrieval and authorization code.
**Validation:** deterministic tests use a fake generator. The owner must verify Ollama cloud routing is disabled and perform the real Windows conversation after code review. See [M03-B1](M03B1_LOCAL_CHAT.md).

### P-09 — Provider-neutral local chat with explicit Bionic option
**Proposal:** M03-B2 places a small common generation contract in front of the existing Ollama adapter and an optional LM Studio Bionic native API adapter. The operator must explicitly supply provider, model, and numeric loopback URL at startup; no provider/model is selected by default and there is no fallback or model discovery. Bionic requests default to `reasoning: "off"` and `store: false`.
**Reason:** keep the local chat UI and service independent of a specific runtime while allowing the owner to use the already available Bionic endpoint without installing Ollama.
**Security boundary:** adapters reject non-loopback URLs, bypass system proxies, do not follow redirects, bound requests/responses, and expose no tool or integration fields. The local runtime must itself be configured for local-only inference.
**Validation:** deterministic provider and service tests cover native request construction, response parsing, safe errors, and authenticated response delivery. Live Windows Bionic verification remains an owner task; see [M03-B2](M03B2_LOCAL_PROVIDER.md).

### P-10 — Single-workspace authenticated memory chat (P2-A design)
**Proposal:** bind each paired local browser session to a trusted, configured Jarvis principal; let the user select one workspace only after server-side READ authorization; route chat through existing document search, approved-memory search, and citation-validated RAG. Proposals remain pending until a separate authenticated WRITE-authorized human approval action bound to an exact candidate snapshot. Chat remains single-turn and non-persistent.
**Reason:** add memory-grounded assistance without creating a second permissions/retrieval stack or weakening user consent.
**Constraints:** no client-supplied actor authority, no cross-workspace default, no model-triggered writes, no tools/filesystem mutation/cloud, and no chat transcript table. P2-A1 through A4 reuse the existing lifecycle APIs and provider-neutral RAG contract.
**Implementation status:** P2-A1 through A4 are implemented on this task branch. The P2 closure report records owner live-acceptance gates that remain open; implementation is not equivalent to product acceptance.
**Validation:** temporary SQLite and deterministic fake-provider suites cover pairing, permissions, RAG citations, candidate lifecycle, and UI/API integration. See [P2 final acceptance](P2_FINAL_ENGINEERING_ACCEPTANCE.md), [design](P2A_AUTHENTICATED_MEMORY_CHAT_DESIGN.md), and [P2-A4](P2A4_CONNECTED_UI.md).

### P-11 — Read-only repository intelligence

**Proposal implemented as a first slice:** register local Git repositories only beneath existing approved roots, using the workspace's `MANAGE_SOURCES` permission and an interactive trusted-terminal confirmation. Index commit-pinned Git blobs into workspace-scoped tables and FTS5; expose authenticated search, source lines, and provider-backed source-grounded answers.
**Security constraints:** no execution of repository code or scripts, no browser registration, no arbitrary path scanning, no cross-workspace fallback, no tools/edits, bounded indexing, and citations bind to a commit/path/line. Python uses AST; other language extraction is explicitly partial lexical metadata. The derived graph and commit-diff evidence are bounded and permission-scoped.
**Status:** partial P3 vertical slice implemented. It includes a derived snapshot graph, recent commit selectors, bounded commit diffs, and line-overlap hints for indexed Python AST symbols. It does not provide comprehensive parsers, semantic call graphs, historical indexed snapshots, or tested Windows path-race resistance. See [P3 docs](P3_DEVELOPER_INTELLIGENCE.md).

### P-12 — P4 supervised coding agents (bounded foundation)
**Decision:** add repository-scoped capability grants independent of workspace and memory permissions, a read-only task supervisor, provider-neutral adapter contract with a deterministic fake, exact-diff human review receipts, audit/recovery, and a dry-run verification planner. No agent runtime or external dependency is included.
**Open gate:** target Windows 11 isolation has not been verified. Worktree creation, file mutation, command/network execution, commit, push, PR, merge, and deploy remain unavailable. Do not interpret proposal approval as permission for a subsequent operation.
**Status:** partial foundation implemented and tested on disposable synthetic repositories. See [P4 implementation status](P4_IMPLEMENTATION_STATUS.md), [P4 architecture](P4_SUPERVISED_CODING_AGENTS_DESIGN.md), and [P4 security and acceptance plan](P4_SECURITY_AND_ACCEPTANCE_PLAN.md).

### Conversational context update — 2026-11-22

**Decision:** Keep a small recent conversation window in each authenticated, process-local chat session so follow-up questions can use prior turns. Use it only within the same session and selected workspace; clear it on workspace change, logout/revocation, or session expiry. Do not write conversational turns to SQLite, browser storage, or permanent memory. Any durable conversation retention remains an open consent and retention decision.

**Implemented:** The chat service holds at most six recent turns and 8 KiB per session, passes a bounded 3 KiB serialized context to general chat or workspace RAG, and retains a successful turn only after its response is delivered. Workspace factual claims remain subject to the existing evidence and citation rules; conversation context is explicitly untrusted and is not workspace evidence. Text and voice clients using the same authenticated chat endpoint share the same context.

**Evidence:** Session-store and HTTP regression coverage in `tests/test_m03_b1_local_chat.py` and `tests/test_m03_p2a_chat.py`.

## Open decisions

- Exact system RAM and supported Windows versions.
- Tauri/Python service packaging versus an alternative single-process app architecture.
- Authentication mechanism for browser dashboard access to the loopback service.
- Whether local database encryption is in scope and which Windows-backed key mechanism to use.
- Cloud provider adapters and explicit content disclosure/redaction behavior.
- Search quality benchmark and model choices for Arabic-English memory retrieval.
- Voice activity detection, wake-word, local speech recognition and speech output dependencies.
- What source snapshots are retained versus only source references and extracted evidence.
- Whether to adopt REA and at which version after reviewing exact third-party licenses and target isolation.
- How long deleted records remain recoverable and how purge propagates to backups.

## Change process

Update this log when a proposal is accepted, revised, or rejected. Record the decision owner, date, implementation evidence, and consequences; do not mark a decision “implemented” based only on this document.
