# Jarvis 2.0 — Roadmap

**Status:** M01-A through M01-E and M02-A through M02-C are in the stated `main` baseline. M02-D evaluation is implemented on a review branch; later sequencing remains gated.

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
| M02 | AI Intelligence Engine | M02-A local Ollama adapter through M02-C grounding/context handling in baseline; M02-D evaluation on review branch; later work requires separate approval | Loopback/model allowlist; separate indexes; single-workspace authorization; evidence selection, context limits, citation diagnostics and reproducible evaluation |
| M03 | Desktop, Dashboard and Voice | Windows desktop shell, browser dashboard, 3D knowledge galaxy, voice input/wake word/local speech recognition | Windows compatibility; accessibility/RTL; local audio processing; permission-bound native APIs |
| M04 | Integration Platform | Versioned APIs and reviewed MCP/API connectors, GitHub and OpenCode integration | Per-tool authorization; secrets handling; connector isolation; approval for writes |
| M05 | Reverse Engineering | REA-based workflows for authorized software analysis | Legal authorization; version/license/dependency review; isolated target copies; bounded workflows |
| M06 | AI Software Factory | Multi-agent coding workflows with repository-scoped workspaces | Agent capability matrix; reviewable diffs; human approval before merge/deploy; reproducibility |
| M07 | Computer and Productivity Automation | Carefully scoped browser and desktop automation | Dedicated profiles; action confirmation; domain/path policy; safe interruption and recovery |
| M08 | Mission Mode, Proactivity and Reliability | Opt-in proactive suggestions and bounded multi-step missions | Explicit goals, permission policy, budget/time limits, audit, stop control, failure recovery |

## Dependency notes

- M01 precedes model and agent features because workspace and memory permissions are foundational.
- M02 depends on M01 evidence and retrieval interfaces.
- M03 can proceed after stable local service interfaces exist; voice and 3D visualization must not bypass authorization.
- M04/M05/M06/M07 should use one shared permission/audit model rather than inventing per-integration authority.
- M08 depends on tested reliability and stop/recovery behavior across earlier milestones; it is not a shortcut around their approval gates.

## Current state and next task

M01-A through M01-E and M02-A through M02-C are in the stated `main` baseline. M01's acceptance report retains unresolved cross-workspace consent and other documented limitations; aggregation remains unsupported and denied by default. M02-D adds synthetic offline and opt-in local Ollama evaluation on a review branch. It does not add embeddings, UI, tools, or cloud routing. M03 remains separately gated.
