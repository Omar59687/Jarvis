# Jarvis 2.0 — Roadmap

**Status:** M01-A through M01-E, M02-A through M02-D, Q01, M03-A, P2-A, M05, M06, and M07 implementation are present in project history. P2 owner acceptance remains open. M06-A provides approval-controlled file changes in private per-proposal task workspaces. M06-B adds only a non-executing Windows isolation assessment and architecture; neither changes registered repositories nor enables command execution, verification, or promotion. M07 is complete on `main` via PR #22; it delivers a local tray companion and does not add desktop automation.

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
| M05 | Developer Intelligence 2.0 | Incremental, source-grounded cross-file repository understanding reusing the existing P3 infrastructure | Snapshot safety; bounded symbols/references/graph; workspace isolation; validated file/line evidence; honest parser coverage; Python and Node CI |
| M06 | AI Software Factory | Approval-controlled bounded file-change proposals in private task workspaces | M06-A: separate apply capability, exact-diff approval, stale/replay denial, atomic writes/recovery; M06-B: Windows isolation assessment only; execution/verification/promotion remain gated |
| M07 | Native Windows Desktop Assistant | Local tray companion for the existing authenticated dashboard, backend status, optional shortcut, opt-in notifications, and existing push-to-talk UI; no computer automation | Single-instance and graceful companion lifecycle; numeric-loopback-only health; no autostart; preserve browser session and M04/M06 permissions |
| M08 | Mission Mode, Proactivity and Reliability | Opt-in proactive suggestions and bounded multi-step missions | Explicit goals, permission policy, budget/time limits, audit, stop control, failure recovery |
| P2-A | Authenticated Memory and Evidence Chat | Proposed first memory-powered chat using one authorized workspace, citations, and explicit memory approval | Server-derived actor identity; READ/WRITE checks; one-workspace RAG; citation provenance; no transcript persistence; approval snapshot/replay tests |
| P2-A1 | Trusted Principal and Workspace Session Binding | Bind paired sessions to one explicitly configured principal; expose READ-filtered workspace listing and validated session selection | Identity spoof rejection; exact Host/Origin/CSRF; cross-workspace denial; revocation clears selection; existing chat regressions |
| P2-A2 | Evidence-backed Chat | Reuse provider-neutral RAG and permission-filtered documents plus approved memories | READ rechecks; strict citation validation; bounded provenance; abstention; no cross-workspace access |
| P2-A3 | Human-approved Memory | Create editable pending candidates and explicit review/approval/rejection actions | Current WRITE; exact source/content snapshot; explicit UI confirmation; replay/stale/cross-workspace rejection |
| P2-A4 | Connected Knowledge Galaxy UI | Bind workspace selection, evidence cards, candidate editing/review, and bilingual RTL to the local APIs | Same-origin CSRF; ephemeral transcript; general-chat mode; accessible controls; no automatic model action |
| P3 | Developer Intelligence | Understand explicitly registered local repositories through commit-bound, permission-filtered evidence | Explicit terminal approval; no code execution; bounded index; source citations; stale snapshot invalidation; local provider only |
| P4 | Supervised Coding Agents | Future supervised coding tasks in isolated, disposable repository workspaces | M06 adds private application-scoped task workspaces only; OS isolation, command execution, verification, promotion, and agent runtimes remain gated and unavailable |

## Dependency notes

- M01 precedes model and agent features because workspace and memory permissions are foundational.
- M02 depends on M01 evidence and retrieval interfaces.
- M03 can proceed after stable local service interfaces exist; voice and 3D visualization must not bypass authorization.
- M04/M05/M06/M07 should use one shared permission/audit model rather than inventing per-integration authority.
- M08 depends on tested reliability and stop/recovery behavior across earlier milestones; it is not a shortcut around their approval gates.

## M08-A status

M08-A scope was approved for the `feature/m08-a-mission-foundation` branch at
baseline `94b056732038e9cdf1d106aa4fa9838508800da6`. Its implementation is
awaiting review and does not authorize M08-B. See [M08-A specification](M08A_MISSION_FOUNDATION_SPEC.md)
and [implementation status](M08A_IMPLEMENTATION_STATUS.md). M08-B through
M08-E remain unapproved.

## Current state and next task

M01's acceptance report retains unresolved cross-workspace consent and other documented limitations; aggregation remains unsupported and denied by default. P2-A1 through A4 are implemented, with owner live acceptance and independent review still required. P3 includes terminal-approved repositories under existing approved roots, commit-pinned Git-object indexing, multi-language partial metadata, a bounded permission-scoped graph, commit changes, source citations, and the developer dashboard. M05 Developer Intelligence 2.0 extends that foundation; it is not a complete semantic analyzer and never executes repository code. REA-based reverse-engineering workflows are deferred and require a separately approved roadmap milestone. P4 retains unresolved target-Windows OS isolation; M06-A enables only explicit approved file changes inside private application-scoped task workspaces. M06-B adds a pure assessment API and architecture comparison; command execution, verification, promotion, and deployment remain disabled. See [M06 approval-controlled execution](M06_APPROVAL_CONTROLLED_EXECUTION.md), [P2 acceptance](P2_FINAL_ENGINEERING_ACCEPTANCE.md), [P3 implementation and limits](P3_DEVELOPER_INTELLIGENCE.md), [M05 Developer Intelligence 2.0](M05_DEVELOPER_INTELLIGENCE_2.md), [P4 architecture](P4_SUPERVISED_CODING_AGENTS_DESIGN.md), and [P4 acceptance plan](P4_SECURITY_AND_ACCEPTANCE_PLAN.md).
