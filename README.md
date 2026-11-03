# Jarvis 2.0

**A local-first personal AI second brain**

Jarvis 2.0 is a modular personal assistant foundation for capturing, organizing, and retrieving information across personal and project workspaces. M01-A, M01-B and M01-C are merged in `main`; M01-D memory approval and reversible deletion is in review.

> **Project status:** local SQLite storage, approved-folder ingestion and workspace-scoped bilingual lexical search are merged. M01-D adds a reviewable local memory lifecycle on a draft branch. No semantic search, AI, cloud, GUI or voice capability is claimed.

## Product goals

- Windows desktop application and browser dashboard.
- Arabic and English interaction.
- Persistent memories linked to evidence and their source.
- Synchronization only from user-approved local folders.
- Explicit confirmation before a suggested fact becomes permanent memory.
- Personal and project workspaces with explicit approval for cross-workspace retrieval.
- Reversible memory deletion.
- Local Ollama inference with optional, user-configured cloud routing.
- Later voice, MCP/API, GitHub, OpenCode, browser automation, authorized reverse engineering, and multi-agent development workflows.
- Proactive suggestions that never execute without approval; any future Mission Mode must be bounded and separately approved.
- No required paid API or subscription.

## Status

| Milestone | State |
|---|---|
| M01 — Second Brain Foundation | M01-A/B/C merged; M01-D in review |
| M02 — AI Intelligence Engine | Planned |
| M03 — Desktop, Dashboard and Voice | Planned |
| M04 — Integration Platform | Planned |
| M05 — Reverse Engineering | Planned; authorized targets only |
| M06 — AI Software Factory | Planned |
| M07 — Computer and Productivity Automation | Planned |
| M08 — Mission Mode, Proactivity and Reliability | Planned |

## Documentation map

- [Project specification](docs/PROJECT_SPEC.md) — user needs, scope, requirements, and open questions.
- [Architecture](docs/ARCHITECTURE.md) — proposed components, boundaries, and trust model.
- [Roadmap](docs/ROADMAP.md) — milestone sequence and gates.
- [Decisions](docs/DECISIONS.md) — confirmed constraints and provisional architecture decisions.
- [Open-source stack audit](docs/OPEN_SOURCE_STACK.md) — component comparison, licenses, risks, and adoption status.
- [M01 specification](docs/M01_SPEC.md) — acceptance criteria for the first milestone.
- [M01-B ingestion](docs/M01B_INGESTION.md) — approved-folder synchronization.
- [M01-C retrieval](docs/M01C_RETRIEVAL.md) — lexical search behavior and limitations.
- [Agent and contributor instructions](AGENTS.md).

## Repository status

The stated `main` baseline contains M01-A SQLite storage and permissions, M01-B approved-folder ingestion, and M01-C FTS5 lexical retrieval. M01-D memory approval and reversible deletion is under review on `feature/m01-d-memory-lifecycle`. See milestone documents for behavior and verification evidence.

## Development principles

1. Reuse maintained software where it fits; verify the exact version, dependency closure, license, and security posture before adoption.
2. Keep local-first operation and ordinary use free of mandatory paid services.
3. Make data location, evidence, workspace scope, retention, and deletion behavior understandable to the user.
4. Require explicit consent for permanent memories, cross-workspace retrieval, sensitive integrations, and consequential external actions.
5. Separate proposals from verified implementation. Do not report tests, integrations, or security properties that have not been checked.

## Review status

M01-D is the current review slice. Do not begin M01-E or merge M01-D without explicit approval. See [M01-D Memory Lifecycle](docs/M01D_MEMORY_LIFECYCLE.md).
