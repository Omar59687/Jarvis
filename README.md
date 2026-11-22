# Jarvis 2.0

**A local-first personal AI second brain**

Jarvis 2.0 is a modular personal assistant foundation for capturing, organizing, and retrieving information across personal and project workspaces. M01-A through M01-E, M02-A through M02-D, Q01, and M03-A are in the stated `main` baseline.

> **Project status:** This branch implements P2-A1's trusted principal/session binding and authenticated workspace listing/selection API on top of the M03-B2 local chat baseline. It remains a restricted local alpha: the Knowledge Galaxy workspace view is still synthetic, chat is still general single-turn conversation, and no retrieval, memory writes, tools, account provisioning, or cloud integration is connected.

## Product goals

- Windows desktop application and browser dashboard.
- Arabic and English interaction.
- Persistent memories linked to evidence and their source.
- Synchronization only from user-approved local folders.
- Explicit confirmation before a suggested fact becomes permanent memory.
- Personal and project workspaces with explicit approval for cross-workspace retrieval.
- Reversible memory deletion.
- Explicit local model-provider selection; this chat alpha does not enable cloud routing.
- Later voice, MCP/API, GitHub, OpenCode, browser automation, authorized reverse engineering, and multi-agent development workflows.
- Proactive suggestions that never execute without approval; any future Mission Mode must be bounded and separately approved.
- No required paid API or subscription.

## Status

| Milestone | State |
|---|---|
| M01 — Second Brain Foundation | M01-A through M01-E merged; see acceptance report for open criterion and limitations |
| M02 — AI Intelligence Engine | M02-A through M02-D and Q01 merged; later slices remain gated |
| M03 — Desktop, Dashboard and Voice | M03-A visual prototype merged; M03-B1 local chat alpha baseline; M03-B2 local-provider baseline; P2-A memory/evidence chat design proposed on this branch; no production dashboard integration |
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
- [M02-B1 document RAG](docs/M02B1_DOCUMENT_RAG.md) — bounded single-workspace document evidence and request-local citation references.
- [M02-B2 searchable memories](docs/M02B2_MEMORY_RETRIEVAL.md) — separate approved-memory FTS5 index and mixed document/memory RAG composition.
- [M02-C grounding and context](docs/M02C_GROUNDING_CONTEXT.md) — deterministic evidence selection, separated context packaging, and answer outcomes.
- [M02-C synthetic diagnostic](examples/m02_c_grounding_demo.py) — prints the selected evidence and exact fake-model prompt.
- [M02-D evaluation](docs/M02D_EVALUATION.md) — synthetic dataset, offline and opt-in local Ollama runners, metrics, and verification.
- [Q01 model quality checkpoint](docs/M02_Q01_MODEL_QUALITY.md) — historical-memory diagnostic, Ollama timing metadata, and opt-in sequential local comparison.
- [M03-A desktop foundation](docs/M03A_DESKTOP_FOUNDATION.md) — stack comparison, local-service trust boundary, Windows checks, and prototype acceptance criteria.
- [M03-A mock prototype](prototypes/m03_a/README.md) — browser-only synthetic galaxy preview and harmless loopback health check.
- [M03-B1 local chat alpha](docs/M03B1_LOCAL_CHAT.md) — original single-provider baseline and its pairing/security boundary.
- [M03-B2 local provider abstraction](docs/M03B2_LOCAL_PROVIDER.md) — provider configuration, Bionic/Ollama adapters, security boundary, Windows startup, tests, and limitations.
- [P2-A authenticated memory chat design](docs/P2A_AUTHENTICATED_MEMORY_CHAT_DESIGN.md) — proposed identity, workspace, evidence, citations, consent, privacy, API, and threat model.
- [P2-A acceptance and test plan](docs/P2A_ACCEPTANCE_AND_TEST_PLAN.md) — implementation slices and observable regression/security gates.
- [P2-A1 trusted workspace sessions](docs/P2A1_TRUSTED_WORKSPACE_SESSION.md) — implemented API behavior, Windows configuration and smoke steps, and current limits.
- [Agent and contributor instructions](AGENTS.md).

## Repository status

The stated `main` baseline contains M01-A through M01-E, M02-A through M02-D, Q01, and the synthetic M03-A browser/service prototype. This branch starts from the P2-A design commit and implements only P2-A1's trusted session identity and workspace selection API. No memory-powered chat integration is implemented here. See [P2-A design](docs/P2A_AUTHENTICATED_MEMORY_CHAT_DESIGN.md), [P2-A1 implementation](docs/P2A1_TRUSTED_WORKSPACE_SESSION.md), and its [acceptance/test plan](docs/P2A_ACCEPTANCE_AND_TEST_PLAN.md).

## Development principles

1. Reuse maintained software where it fits; verify the exact version, dependency closure, license, and security posture before adoption.
2. Keep local-first operation and ordinary use free of mandatory paid services.
3. Make data location, evidence, workspace scope, retention, and deletion behavior understandable to the user.
4. Require explicit consent for permanent memories, cross-workspace retrieval, sensitive integrations, and consequential external actions.
5. Separate proposals from verified implementation. Do not report tests, integrations, or security properties that have not been checked.

## Review status

M01-E, M02-A through M02-D, Q01, and M03-A are in the stated baseline. Cross-workspace aggregation remains unsupported and denied by default; P2-A1 exposes no multi-workspace retrieval. This branch implements only trusted session binding and permission-filtered workspace listing/selection; P2-A2 and later remain unimplemented. See [M01 Acceptance](docs/M01_ACCEPTANCE.md), [Q01](docs/M02_Q01_MODEL_QUALITY.md), [M03-A](docs/M03A_DESKTOP_FOUNDATION.md), and [P2-A1](docs/P2A1_TRUSTED_WORKSPACE_SESSION.md).
