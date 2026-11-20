# P4 — Security and Acceptance Plan

**Status:** Original platform and execution gates remain in force. A bounded authorization/task/proposal foundation is implemented, but it does not enable a runtime or execution capability. See [current implementation status](P4_IMPLEMENTATION_STATUS.md) and [P4 architecture](P4_SUPERVISED_CODING_AGENTS_DESIGN.md).

## Security invariants

1. Identity and workspace scope come only from the authenticated Jarvis session and current `Database` permissions. Recheck authorization at task start, every approval, and result promotion.
2. Repository paths come only from a registered repository ID and approved root. The browser and agent cannot select a filesystem path.
3. Repository contents, prompts, model output, agent events, command output, skills and test reports are untrusted data. None can grant capabilities or alter policy.
4. Deny shell, write, network, secrets, cloud inference, skills/plugins/MCP, child agents, push, PR, merge and deployment by default. A local process or local model is not a security boundary.
5. Write-capable experiments require a verified OS isolation boundary that contains the complete process tree and prevents access to the owner checkout and host secrets. If this cannot be established on the target Windows edition, remain read-only.
6. No operation is approved by model output. Consequential approvals are authenticated, CSRF-protected, one-use and bound to the exact task, repository, commit, manifest and action parameters.
7. Cancellation, timeout, permission revocation, service restart or policy uncertainty must stop or deny work. Interrupted tasks are quarantined and never resumed automatically.

## Threats and required controls

| Threat | Required control and test evidence |
|---|---|
| Cross-workspace or revoked repository access | Server-derived principal; current READ plus distinct code-write permission; revoke between approval and execution; no result or artifact leakage. |
| Prompt injection in source, issues, skills or tool output | Treat all content as data; immutable host manifest; fake adapter tries to request broader access; reject request and preserve policy. |
| Host filesystem escape | Per-task disposable root, OS sandbox, canonical-path and open-time checks; traversal, symlink, junction, reparse point, ADS, hardlink, case-collision and race tests on Windows. |
| Git config/hooks/argument injection | Isolated Git metadata; sanitized `GIT_*`; no hooks, external diff/textconv, prompts, lazy fetch or arbitrary args; adversarial synthetic repositories/configs. |
| Secret disclosure | No inherited credentials; secret-like paths denied; bounded scanner/redaction; sentinel secrets in environment and repository must not appear in prompts, logs, artifacts or provider requests. |
| Network/cloud exfiltration | OS-enforced default-deny network; explicit provider/data disclosure; test attempts to access loopback, private LAN, public DNS/HTTP and proxy variables. No silent fallback. |
| Unbounded resource use | Wall, CPU, memory, process, disk, output, event, diff and approval limits; oversized/flooding fake adapter; verify bounded cleanup. |
| Approval replay or stale diff promotion | One-use, expiring decision snapshot binds repository/workspace, base/task SHA, manifest and diff digest; change each field and verify rejection. |
| Process survives cancellation | Cancel descendants using verified Windows process-group/Job Object policy; child-spawning fixture; ensure all descendants stop and task state is terminal. |
| Interrupted task or corrupted audit | Crash/fault injection at every state transition; pending decisions invalidated; task root quarantined; audit contains hashes/status, not secrets/prompts. |
| Malicious or unlicensed dependency/skill | Pin exact release/commit, hashes and license; no auto-install/discovery; legal/security review before adoption. |

## Acceptance gates

### Gate 0 — Design and platform decision

- Resolve repository-scoped code-write permission separately from `MANAGE_SOURCES` and memory `WRITE`.
- Select a Windows isolation mechanism supported on the actual owner machine and establish network, filesystem, ACL, process-tree and resource guarantees.
- Decide data disclosure, provider location, audit retention and deletion behavior.
- Record the exact runtime, license, version, update source and disabled features. No runtime install is part of this gate.

**Pass:** reviewed decision record; threat model updated; a Windows test plan and isolated test account/device exist. No code execution capability is granted.

### Gate 1 — Fake supervisor, read-only

- Add only task schema/state management, immutable capability manifest, adapter protocol, event validation, expiry and cancellation with a deterministic fake adapter.
- Test identity spoofing, workspace/repository scope, READ revocation, malformed event, prompt-injection request, approval replay, bounds, redaction and restart recovery.
- Maintain all P1/P2/P3 tests; no process launches in tests.

**Pass:** offline suite demonstrates every denied action is rejected before side effects; full regression and migration/backup/restore tests pass.

### Gate 2 — One runtime, synthetic read-only repository

- Integrate one pinned runtime through an adapter. Use a synthetic repository and explicit provider/model/routing configuration.
- Deny writes, shell, plugins, skills, MCP, browser, child agents, network beyond the narrowly approved inference transport, and credentials.
- Independently observe filesystem/network/process behavior on Windows; do not infer security from runtime flags.

**Pass:** observed capability set matches manifest; public/private network and host file probes fail; UI accurately identifies local versus remote inference.

### Gate 3 — Disposable edit workspace

- Enable writes only in a fresh per-task clone/worktree under the verified OS boundary.
- Test path escapes, reparse/junction races, Git metadata/hook attacks, secrets, malicious config, denied network, resource exhaustion, cancellation and interruption.
- Confirm canonical checkout remains byte-for-byte unchanged.

**Pass:** all containment tests pass on target Windows; inability to prove an invariant returns to read-only mode.

### Gate 4 — Human review and local promotion

- Present exact base/task SHAs, manifest, changed paths, binary/large files, secret findings, diff hash and reproducible test report.
- Require reauthentication/current permissions and explicit one-use confirmation bound to exact diff before writing a commit to a named isolated feature branch.
- No push, PR, merge, release or deployment.

**Pass:** altered/stale diff, revoked permissions, replayed approval, failing/unrun tests and unexpected files prevent promotion. The user's original working tree is not modified.

### Later separately approved integrations

Hosted Git, push/PR, cloud inference, external skills/connectors, multi-agent coordination, unattended scheduling and deployment each require their own data-flow review, approval UX, threat model and acceptance gate. They are outside the first P4 implementation.

## Required regression suites

- Existing full Python suite, P1/P2 memory and workspace isolation, P3 repository registration/index/graph/change/Q&A, migration rollback, backup/restore, and provider tests.
- Deterministic fake-adapter lifecycle and authorization suite at each gate.
- Node UI state and galaxy tests; English/Arabic localization, RTL, keyboard-only and screen-reader smoke checks.
- Windows-specific filesystem and process containment suite on the owner's target edition. Linux results do not substitute for Windows acceptance.
- Static checks, clean diff, documentation link validation, and dependency/license inventory.

## Release evidence template

For each gate record: commit SHA; OS edition/build; Python/Git/runtime versions; configured provider and inference location; test commands/results; denied network destinations; process-tree cancellation results; filesystem escape probes; permission/revocation results; diff/test artifact hashes; dependency licenses; known failures; reviewer and date. Do not place source excerpts, model transcripts, credentials or personal repository data in public CI artifacts.
