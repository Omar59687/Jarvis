# P4 Windows isolation decision and owner acceptance

**Decision:** P4 remains read-only and execution-disabled. This branch hardens task cancellation races, expands the trusted fake-proposal workflow, and adds a synthetic diagnostic harness. It does not add an agent runtime or certify a Windows boundary.

## Findings from the existing implementation

### Completion matrix for this checkpoint

| Status | Scope |
|---|---|
| Implemented and regression-tested | Repository-scoped grant policy, deny-by-default unavailable capabilities, bounded synthetic fake proposals, digest-bound human review, audit reads, task cancellation, restart quarantine, no-write worktree preflight, and proposal path filtering. |
| Hardened on this branch | Concurrent cancel/proposal completion now commits cancellation once and returns a stable `task_cancelled` outcome. The trusted helper now supports fake proposal creation, task listing/inspection, explicit grant revocation, review, cancellation, audit inspection, recovery, and blocked worktree-plan inspection. |
| Partially implemented | Trusted terminal interface is an injected in-process helper. There is no standalone authenticated CLI bootstrap; identity and services must be supplied by the trusted host. Proposal text is retained in the local database and the audit log is not externally tamper-evident. |
| Blocked by policy | Agent worktree creation, repository writes, verification execution, network, automatic commits, push, PR, merge, and deployment. No capability checkbox or proposal approval can enable them. |
| Unverified on target Windows | Windows Sandbox/VM containment, NTFS race behavior, ACL boundaries, credentials, Git hook/filter behavior inside a guest, complete process-tree termination, timeouts, network/loopback denial, host-write denial, and interrupted OS-job cleanup. |

| Area | Current behavior | Decision / remaining gap |
|---|---|---|
| Authority | Repository-scoped manager grants are separate from workspace and memory permissions; policy grants only metadata, indexed-source reads, and proposal generation. | Keep. Worktree, write, process, network, commit, push, PR, merge, and deploy capabilities remain unavailable. |
| Task state | SQLite stores task identity, hashes, state, bounded proposal snapshot, and audit metadata; request text is not persisted. Restart recovery quarantines requested/proposed tasks. | Keep. No auto-resume and no task filesystem exists. |
| Approval | Human decision binds to the displayed diff SHA-256 and a one-use decision receipt; approved content is not written. | Keep. Approval is review bookkeeping, never authorization to execute. |
| Revocation | Grant reads are rechecked before proposal recording and review; there is no OS process to terminate. | Current implementation does not provide active-runtime revocation because no runtime exists. A future runner must connect revocation to host cancellation before it can be enabled. |
| Cancellation | A running adapter gets an Event and `cancel()` callback; task transitions are persisted. | Fixed the concurrent adapter-return/operator-cancel race with an atomic, idempotent state transition. This is still adapter cancellation, not process-tree containment. |
| Repository identity | Registered repository and base commit are revalidated; the worktree planner detects staged, unstaged, unmerged, and untracked changes. | Keep. The planner cannot create a worktree and reports isolation unverified. |
| Logs and audit | Fixed event codes and hashes are stored; adapter exception text is suppressed; terminal output escapes control characters. | Keep. The local SQLite database and bounded proposal source snapshots remain sensitive. Audit rows are access-scoped but are not a tamper-evident external log. |
| P3/P4 authority | P3 indexed-source access and P4 proposal authority are separate from arbitrary disk paths. HTTP identity is derived by the authenticated service, not accepted as an actor field. | Keep. `CodingOperatorWorkflow` is an in-process trusted helper, not an HTTP endpoint. |

## Windows mechanism assessment

The supported product target has previously been reported as Windows 11 Home. Microsoft currently documents that Windows Sandbox is unsupported on Home and lists Pro, Enterprise, Pro Education/SE, and Education as supported editions. The Windows Hyper-V role likewise requires Windows 11 Pro or Enterprise. A Windows Sandbox host also requires hardware virtualization, at least 4 GB RAM (8 GB recommended), 1 GB free disk space, and at least two CPU cores (four recommended). These are prerequisites, not security acceptance results.

Windows Sandbox starts with networking and clipboard redirection enabled by default. Host mapped folders can expose data to the guest; writable mapped folder changes persist on the host. A future evaluation must explicitly disable networking, clipboard, audio/video, printer sharing and vGPU, and must not mount the owner's checkout or profile. A read-only mount may be used only for a synthetic fixture. Any writable export area must be a separate empty staging directory, treated as hostile input by a trusted host-side importer.

WSL2 is useful for development and test reproducibility but is not accepted as the isolation boundary here. Microsoft documents that Linux access to Windows files runs with the Windows user's permissions; WSL networking can reach host services, and Windows firewall integration does not itself define Jarvis's agent-specific allowlist. Ordinary ACLs, a second directory, a Git worktree, container defaults, or a process permission prompt are also not sufficient evidence of containment.

### Recommended architecture

1. Keep the Jarvis service and the owner's source checkout outside the agent guest. Give the guest no credentials, browser profile, SSH keys, cloud tokens, personal files, or production database.
2. For an initial disposable acceptance, use a fully patched Windows Pro or Enterprise machine with Windows Sandbox and an explicitly restrictive `.wsb` configuration. If the owner's device remains Home, stay read-only or run acceptance on a separate supported host; do not install unsupported feature workarounds.
3. If a persistent supervised runtime is later approved, prefer a dedicated, patched VM on a supported Pro/Enterprise host, with a per-task disposable guest or snapshot, no bridged/LAN access, explicit host firewall policy, no shared credentials, and a narrow reviewed artifact-transfer channel. Review guest escape, host patching, VM integration services, snapshot rollback, and the transfer/import parser separately.
4. Require a separate independent review of host/guest filesystem behavior, process-tree termination, timeout enforcement, network denial (including loopback and private subnets), Git hooks/filters/config, and interrupted-job recovery before any write or process capability becomes grantable.

Windows Sandbox is a candidate for disposable containment testing, not a certification. Hyper-V-backed VMs, Sandbox, WSL2, and containers have different host integration and operational properties; the harness must report what was actually tested instead of labeling a platform generically secure.

## Owner acceptance procedure (Windows 11)

Run the following only on a disposable synthetic repository and a supported Windows host. Do not point the harness at a personal repository or credentials.

1. Import this branch/bundle into a disposable clone. Open PowerShell and record the Windows edition/build, Python version, Git for Windows version, CPU architecture, RAM, virtualization state, and filesystem for the lab path.
2. If the device is Windows Home, stop the containment portion and record it as `UNVERIFIED`; Windows Sandbox and the Hyper-V role are not supported on that edition. Do not modify Windows licensing/feature checks to bypass this.
3. On Pro/Enterprise, enable Windows Sandbox only through Windows Features, reboot if asked, then create a `.wsb` file with the configuration below. Mount only a newly created synthetic harness directory read-only if a transfer is needed. Do not mount the repository, user profile, home folder, secrets, or a writable host checkout.
4. Start Sandbox using that `.wsb` file, copy the harness into the guest's local temporary directory by the approved read-only fixture mount, and run:

   ```powershell
   py -3 scripts\p4_windows_acceptance.py --output $env:TEMP\p4-windows-acceptance.json
   py -3 -m unittest tests.test_coding_agents tests.test_developer_intelligence tests.test_ingestion -v
   ```

5. Preserve the JSON report and command output locally. The harness creates files only under temporary directories. Record every result as `PASS`, `FAIL`, `SKIPPED`, or `UNVERIFIED`; explain skipped filesystem features. The expected overall status remains `NOT_READY` while process, network, credential, ACL, and external-tool boundary checks are `UNVERIFIED`.
6. Independently verify Sandbox networking is disabled, no host profile or checkout is visible, clipboard is disabled, and closing Sandbox destroys guest state. These observations alone do not prove resistance to guest escape or kernel vulnerabilities.
7. Do not run an agent, execute repository code, create a writable host mount, or promote a proposal during this acceptance. A later write-capable pilot requires a separate approved harness and independent review.

### Restrictive `.wsb` starting configuration

This configuration intentionally has no mapped folders and no startup command. It is a test starting point only; verify the effective settings on the exact Windows build.

```xml
<Configuration>
  <VGpu>Disable</VGpu>
  <Networking>Disable</Networking>
  <AudioInput>Disable</AudioInput>
  <VideoInput>Disable</VideoInput>
  <ProtectedClient>Enable</ProtectedClient>
  <PrinterRedirection>Disable</PrinterRedirection>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <MemoryInMB>4096</MemoryInMB>
</Configuration>
```

If a Windows build rejects a setting, mark that control `FAIL` and do not weaken the configuration to make the run pass. Increasing memory is an operational choice; it does not improve isolation by itself.

## Harness interpretation

Run on any platform with:

```powershell
py -3 scripts\p4_windows_acceptance.py
```

The harness tests the deny-by-default capability set, synthetic path checks, and disposable Git identity. On Windows it additionally probes creation and handling of NTFS symlinks, hard links, alternate data streams, and case-only names where the host allows those operations. A fixture-creation `PASS` means only that the test primitive worked. ACL containment, junction race resistance, credential denial, Git hook/filter behavior under a sandbox, process-tree kill, timeout, network/loopback denial, host-write denial, and interrupted OS-job cleanup remain `UNVERIFIED` until a real isolated runner and independent Windows test are available. On Linux, Windows filesystem semantics are always reported `UNVERIFIED`; Linux simulation is not Windows evidence.

## Platform references

- Microsoft, [Windows Sandbox editions and defaults](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/)
- Microsoft, [Install Windows Sandbox and prerequisites](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-install)
- Microsoft, [Windows Sandbox configuration and mapped-folder behavior](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file)
- Microsoft, [Hyper-V requirements and Home edition support](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/get-started/install-hyper-v)
- Microsoft, [WSL enterprise security and Windows file permissions](https://learn.microsoft.com/en-us/windows/wsl/enterprise)

## M06-B update — 2026-11-23

**Implemented:** The M06-B assessment API and comparison are recorded in [M06-B Windows Execution Isolation](M06B_WINDOWS_EXECUTION_ISOLATION.md). Windows Sandbox remains only the proposed disposable evaluation candidate; the assessment consumes supplied facts and never creates or certifies the boundary. The full comparison includes Hyper-V, Windows containers, restricted tokens/AppContainer, Job Objects, and WSL2/WSL Containers, with official source links and access date.

**Decision:** Continue to deny execution. No runtime, Windows feature, or permission was installed/enabled. The assessment is not evidence of Sandbox availability or containment. Do not proceed to command execution until the independent acceptance gate in the M06-B document passes on the actual target Windows edition.