# Jarvis 2.0

**A local-first personal AI second brain — project foundation**

Jarvis 2.0 is planned as a modular personal assistant that helps capture, organize, retrieve, and act on information across personal and project workspaces. Its design combines local storage and local models with optional cloud model routing, explicit permissions, and evidence-linked memory.

> **Project status:** foundation and architecture documentation only. No application code, runtime, model, or third-party dependency has been added. Requirements marked **Confirmed** come from the project brief; architecture choices marked **Proposed** still require implementation evidence and review.

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
| M01 — Second Brain Foundation | Specified; not implemented |
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
- [Agent and contributor instructions](AGENTS.md).

## Repository status

The repository was inspected before this documentation change. At that point, `main` contained only a one-character README and no application code, tests, or existing architecture decisions. The documents therefore establish a starting point; they do not describe implemented behavior.

## Development principles

1. Reuse maintained software where it fits; verify the exact version, dependency closure, license, and security posture before adoption.
2. Keep local-first operation and ordinary use free of mandatory paid services.
3. Make data location, evidence, workspace scope, retention, and deletion behavior understandable to the user.
4. Require explicit consent for permanent memories, cross-workspace retrieval, sensitive integrations, and consequential external actions.
5. Separate proposals from verified implementation. Do not report tests, integrations, or security properties that have not been checked.

## Next task

The proposed next task is **M01-A: SQLite storage and permissions**. It is not authorized by this documentation change and should begin only after explicit approval. See [M01_SPEC.md](docs/M01_SPEC.md).
