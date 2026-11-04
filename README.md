# Jarvis 2.0

**A local-first personal AI second brain**

Jarvis 2.0 is a modular personal assistant foundation for capturing, organizing, and retrieving information across personal and project workspaces. M01-A through M01-E are merged in `main`; M02-A is the local-only Ollama adapter slice.

> **Project status:** M01 foundation slices are merged. M02-A adds a bounded adapter for an explicitly configured local Ollama service and allowlisted installed models. It does not implement RAG or memory search. No cloud, GUI or voice capability is claimed.

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
| M01 — Second Brain Foundation | M01-A through M01-E merged; see acceptance report for open criterion and limitations |
| M02 — AI Intelligence Engine | M02-A local Ollama adapter; later slices remain gated |
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
- [M02-A Ollama adapter](docs/M02A_OLLAMA_ADAPTER.md) — local adapter boundary, deployment requirements, and hardware facts.
- [Agent and contributor instructions](AGENTS.md).

## Repository status

The stated `main` baseline contains M01-A through M01-E. See [M01 Acceptance](docs/M01_ACCEPTANCE.md) for the acceptance matrix, unresolved cross-workspace consent criterion, and residual limitations. M02-A status and boundaries are documented separately.

## Development principles

1. Reuse maintained software where it fits; verify the exact version, dependency closure, license, and security posture before adoption.
2. Keep local-first operation and ordinary use free of mandatory paid services.
3. Make data location, evidence, workspace scope, retention, and deletion behavior understandable to the user.
4. Require explicit consent for permanent memories, cross-workspace retrieval, sensitive integrations, and consequential external actions.
5. Separate proposals from verified implementation. Do not report tests, integrations, or security properties that have not been checked.

## Review status

M01-E is merged. Cross-workspace aggregation remains unsupported and denied by default; the acceptance report preserves this open requirement. M02-A is limited to the local model adapter; M02-B remains gated. See [M01 Acceptance](docs/M01_ACCEPTANCE.md).
