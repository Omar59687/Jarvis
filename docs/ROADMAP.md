# Jarvis 2.0 — Roadmap

**Status:** M01-A through M01-E, M02-A through M02-D, Q01, and M03-A are in the stated `main` baseline. M03-B2 is the explicit local-provider baseline for this design branch; P2-A is design-only here and is not implemented or merged.

## Phase gates

For each milestone:

1. Approve a written scope and acceptance criteria.
2. Implement only that milestone on a feature branch.
3. Run its targeted checks, relevant earlier-milestone regression checks, and integration checks.
4. Review security, privacy, permissions, dependencies, and license changes.
5. Record evidence and unresolved limitations.
6. Do not proceed to the next milestone until the user approves the gate.

## Milestones

| ID | Name | Intended outcome | Gate examples |
|---|---|---|---|
| M01 | Second Brain Foundation | Local workspaces, durable evidence-linked memories, approved-folder sync, scoped search, approval and reversible deletion | M01 criteria in [M01_SPEC.md](M01_SPEC.md); local-only operation; isolation; migration/restore checks |
| M02 | AI Intelligence Engine | M02-A through M02-D and Q01 merged; later work requires separate approval | Loopback/model allowlist; separate indexes; single-workspace authorization; evidence selection, context limits, citation diagnostics and reproducible evaluation |
| M03 | Desktop, Dashboard and Voice | M03-A synthetic Knowledge Galaxy prototype merged; M03-B1 proposes bounded local single-turn chat alpha; production desktop/dashboard and voice remain future work | Windows compatibility; accessibility/RTL; secure local sessions; local audio processing; permission-bound native APIs |
| M04 | Integration Platform | Versioned APIs and reviewed MCP/API connectors, GitHub and OpenCode integration | Per-tool authorization; secrets handling; connector isolation; approval for writes |
| M05 | Reverse Engineering | REA-based workflows for authorized software analysis | Legal authorization; version/license/dependency review; isolated target copies; bounded workflows |
| M06 | AI Software Factory | Multi-agent coding workflows with repository-scoped workspaces | Agent capability matrix; reviewable diffs; human approval before merge/deploy; reproducibility |
| M07 | Computer and Productivity Automation | Carefully scoped browser and desktop automation | Dedicated profiles; action confirmation; domain/path policy; safe interruption and recovery |
| M08 | Mission Mode, Proactivity and Reliability | Opt-in proactive suggestions and bounded multi-step missions | Explicit goals, permission policy, budget/time limits, audit, stop control, failure recovery |
| P2-A | Authenticated Memory and Evidence Chat | Proposed first memory-powered chat using one authorized workspace, citations, and explicit memory approval | Server-derived actor identity; READ/WRITE checks; one-workspace RAG; citation provenance; no transcript persistence; approval snapshot/replay tests |

## Dependency notes

- M01 precedes model and agent features because workspace and memory permissions are foundational.
- M02 depends on M01 evidence and retrieval interfaces.
- M03 can proceed after stable local service interfaces exist; voice and 3D visualization must not bypass authorization.
- M04/M05/M06/M07 should use one shared permission/audit model rather than inventing per-integration authority.
- M08 depends on tested reliability and stop/recovery behavior across earlier milestones; it is not a shortcut around their approval gates.

## Current state and next task

M01-A through M01-E, M02-A through M02-D, Q01, and M03-A are in the stated `main` baseline. M01's acceptance report retains unresolved cross-workspace consent and other documented limitations; aggregation remains unsupported and denied by default. M03-B2 is the provider-abstraction baseline for this branch, not a production authenticated dashboard. P2-A is currently a design proposal only. Its implementation-ready architecture, threat model, API, and test gates are documented in [P2-A design](P2A_AUTHENTICATED_MEMORY_CHAT_DESIGN.md) and [P2-A test plan](P2A_ACCEPTANCE_AND_TEST_PLAN.md). No P2-A production code is authorized by those documents.
