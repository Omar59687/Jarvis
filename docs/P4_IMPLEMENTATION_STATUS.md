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
