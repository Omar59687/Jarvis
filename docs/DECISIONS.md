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
