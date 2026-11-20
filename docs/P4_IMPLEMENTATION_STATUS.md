# P4 supervised coding foundation — implementation status

**Branch:** `feature/p4-supervised-agent-foundation`  
**Base:** `0ce5083f49df9219d3df34c731aeba57921db17e`  
**Scope:** authorization, read-only indexed-source task proposals, exact-diff review, audit, and fail-closed plans. This is not a coding-agent runtime or secure sandbox.

## Acceptance matrix

| Checkpoint | Status | Implemented evidence | Limit / remaining gate |
|---|---|---|---|
| P4-A Authorization and capabilities | Partial | Separate repository-scoped grants for metadata, indexed source, and proposal generation; manager confirmation; expiry/revocation; explicit requested/granted/denied/unavailable manifest; migration v8; audit | All mutation, execution, network, commit, push, PR, merge, and deploy capabilities are unavailable. Workspace or memory WRITE never grants code access. |
| P4-B Isolated worktree management | Blocked for writes; read-only preflight available | Registered repository and exact commit revalidation; fixed Git status command; dirty-state detection; symlink replacement test | No task worktree is created. This Linux environment cannot establish the target Windows 11 filesystem/process/network isolation required by the design. |
| P4-C Supervised change workflow | Partial, in-memory proposal only | Request digest (request text not stored), bounded fake adapter contract, indexed evidence scope, proposal and diff digest, explicit human review/reject, stale-diff invalidation, one-use decision receipts, cancellation, restart quarantine | No filesystem writes, task branch, commit, promotion, or real agent integration. Proposal approval only records review; it does not authorize later actions. |
| P4-D Verification | Planner only | Finite check IDs map to fixed argument vectors; arbitrary commands rejected | No subprocess is launched. Execution is blocked until isolation is proven on target Windows. |
| P4-E Agent adapter | Contract and fake only | Provider-independent task/proposal/cancel protocol; deterministic fake; self-approval and out-of-evidence changes rejected | Codex, T3 Code, OpenCode, and any external runtime are not connected or installed. |
| P4-F Operator workflow | Partial trusted-terminal helper | Injected terminal I/O for repository/grant inspection, task creation, exact-digest review, audit, cancel, recovery, and worktree-plan display; control characters escaped before terminal output | It is not a standalone executable or browser UI. A trusted application bootstrap must supply the authenticated local principal and database services. |
| P4-G Security and regression review | Partial, automated offline coverage | Capability separation, cross-workspace denial, stale baseline, traversal, symlink replacement, bounded proposals, secret-like output, replay, stale digest, cancellation/restart, migration rollback/upgrade/backup/restore, error redaction | Windows junction/reparse/race/process/network containment cannot be tested here. Existing full-suite results are listed in the delivery report, not inferred from this status file. |

## Security model and explicit decisions

- **Authority:** repository grants are independent of workspace READ/WRITE and memory permissions. Grant/revoke is manager-authorized, repository-scoped, auditable, and expiry-aware. Every task operation rechecks workspace READ and the relevant repository grant.
- **Intent and approval:** task capability requests are hashed into an immutable manifest. Human review is explicit and bound to the exact SHA-256 of the displayed diff. A decision receipt is one-use. The fake adapter cannot approve itself.
- **Privacy:** task request bodies and adapter logs are not persisted. Audit metadata contains fixed event codes, counts, hashes, and identifiers; adapter exception messages are never returned or persisted.
- **Residual privacy risk:** a plain SHA-256 request digest can reveal equality and permit guesses for low-entropy task text if the database is disclosed. Proposal snapshots also persist bounded proposed source text in the local database. Treat that database as sensitive; a future multi-user/runtime stage needs a reviewed retention/deletion policy and a keyed verifier or equivalent protected design before storing user prompts.
- **Evidence:** proposed paths must be among bounded, current, commit-pinned indexed sources returned by P3. The proposal layer does not read arbitrary disk paths. The registered repository's head is rechecked before recording and approval.
- **No code execution:** there is no subprocess runner. The verification planner returns fixed, non-executable plans only. Repository text is never executed.
- **Isolation:** **not established** for the product's Windows 11 Home target. Therefore worktree creation, file mutation, command execution, and network access remain denied. This is a hard fail-closed gate, not a documented sandbox substitute.
- **External agents:** none installed or connected. Local execution is not treated as safe; no external skills, plugins, models, or cloud routes are introduced.

## Schema and recovery

Migration 8 adds repository grants, coding tasks, task events, and one-use decision receipts with repository/workspace and task/workspace/principal foreign-key scoping. Task snapshots contain hashes, not task text. Migration is transactional. Tests cover fresh initialization, v7 upgrade, injected migration rollback, backup/restore, grant persistence, and `foreign_key_check`. No P1/P2/P3 data tables are rewritten.

Requested or proposed tasks found after restart are marked `interrupted`; they are not resumed automatically. Approval never writes a file. There is no automatic cleanup of directories or branches because this slice creates none.

## Windows readiness procedure

Run on the owner's target Windows 11 edition before enabling any write or execution gate:

1. Create a disposable Windows account and synthetic Git repository; verify the exact Python, Git for Windows, filesystem, and OS edition/build.
2. Test whether the proposed isolation mechanism blocks the task process tree from the owner checkout, user profile, credential stores, named pipes, local services, LAN, public network, and configured proxies.
3. Test NTFS symlinks, junctions/reparse points, hardlinks, alternate data streams, case collisions, long paths, ACL inheritance, and concurrent path replacement from inside and outside the boundary.
4. Use a child-process fixture to test timeout and complete process-tree cancellation. Include attempts to use Git hooks, fsmonitor, external diff/textconv, filters, and malicious repository-local config.
5. Capture before/after hashes of the original checkout and unrelated directories; verify no writes outside a designated disposable worktree.
6. Repeat after restart and interruption; verify task quarantine, no implicit resume, and no automatic deletion of unrelated files or branches.
7. Record evidence and have an independent reviewer approve the platform gate. Until every required containment property is demonstrated, retain the current read-only behavior.

Current developer commands (all operate only on the synthetic tests):

```powershell
python -m unittest tests.test_coding_agents -v
python -m unittest discover -s tests
node --test tests/chat_ui_state.test.mjs tests/m03_a_galaxy.test.mjs
```

The read-only baseline preflight counts distinct NUL-delimited staged, unstaged,
unmerged, and untracked Git paths. Its unstaged comparison ignores only CR at
line endings, so Windows CRLF checkout conversion does not make an otherwise
clean baseline appear dirty; staged changes, untracked files, mode changes,
and other content edits remain blocking. It does not parse human-formatted
`git status` lines, whose path and newline formatting is platform-sensitive.

SQLite migration connections are closed in the database context manager;
backup targets close in a `finally` block; restore source and temporary target
connections close through an `ExitStack`, including validation failures. Tests
assert connections are unusable after injected migration, backup, and restore
failures. Windows file-lock behavior still requires execution on Windows.

## Independent review checklist

- Confirm exact base and branch isolation; no P2-fix commit is included.
- Review migration constraints, rollback, backup validation, and restore tests.
- Inspect every capability grant and verify workspace/memory permissions do not imply code permissions.
- Check current READ/grant/repository state on task creation, proposal, review, audit, cancel, and recovery.
- Confirm proposal inputs come from current commit-pinned indexed evidence only and all output bounds are enforced.
- Confirm diff digest is recomputed on display and approval, decisions are one-use, and a later action is not implicitly authorized.
- Verify no code path launches a process, writes a worktree, makes a network request, or calls a real model/agent.
- Test on the owner's Windows target before considering any write-capable follow-up.

## Remaining prerequisites for real agent work

1. Owner review of a repository-specific code-authority policy and the exact grant/revoke UX.
2. Independently verified isolation on the target Windows edition, including filesystem, process tree, and network containment.
3. Separate dependency/data-flow/license review for one exact agent runtime and provider configuration.
4. A later independently approved slice for worktree creation and writes, and another for supervised verification/promotion. Push, PR, merge, and deployment remain separate decisions.

## Persistent orchestration follow-on (schema v9)

The isolated `feature/p4-persistent-task-orchestration` checkpoint adds
`orchestration_tasks`, immutable plan-content revisions, bounded subtasks and
dependencies, one-use exact-plan decision receipts, and append-only lifecycle
events. This is a separate planned-task model; it does not reinterpret schema-v8
coding-task approvals. The deterministic fake planner/worker can demonstrate
bounded simulated progress and restart recovery without a model, external call,
process, or repository write. The new manager-only trusted-terminal helper
supports task creation, inspection, exact-digest approval/rejection, simulation,
cancellation, recovery, and event history.

| Orchestration capability | Checkpoint status | Evidence / remaining limit |
|---|---|---|
| Persistent workspace/principal/repository scope | Implemented | Migration 9, optional repository commit pin, bounded goal and integrity digest; local DB remains sensitive. |
| Immutable approved plan revision | Implemented | Canonical plan SHA-256, current/approved revision pointers, one-use approval receipt; edited plan requires new approval. |
| Subtask dependencies and progress | Implemented for simulation | Dependency-cycle validation and simulated progress; there is no real assignment or worker queue. |
| Lifecycle history and idempotency | Implemented locally | Append-only event/receipt triggers, unique workspace/actor request keys, audit entries; not a distributed event system. |
| Restart recovery | Implemented, fail-closed | Queued/simulating tasks become interrupted; explicit manager resume only for approved simulated work; no automatic worker resumes. |
| Trusted operator flow | Helper implemented | `OrchestrationOperatorWorkflow` is injected into an authenticated trusted host; it is not a standalone CLI or HTTP endpoint. |
| P1/P2/P3 and v8 approval compatibility | Preserved by additive migration | Existing schemas are not rewritten; schema and full regressions remain part of checkpoint verification. |
| Real model, agent, or provider planning | Blocked | Only deterministic fake planner is accepted by the service. |
| File writes, process/network execution, commits, remote actions | Blocked | Existing Windows `NOT_READY` isolation gate remains unchanged. |
| Expiration deletion / retention UX | Not implemented | Expiration is logical; no task purge/export workflow exists, so local DB and backups must be treated as sensitive. |

See [persistent orchestration design and operator boundaries](P4_PERSISTENT_TASK_ORCHESTRATION.md).
