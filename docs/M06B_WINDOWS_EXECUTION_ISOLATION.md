# M06-B — Windows Execution Isolation

**Status:** Implemented a pure, non-executing assessment API and its synthetic tests. No OS isolation runtime, host feature probe, command runner, or new execution permission is implemented. The execution gate remains closed.

**Confirmed scope:** Evaluate a future boundary for explicitly approved development verification. Preserve M06-A file-change behavior. No repository command or imported code is run by this milestone.

## Security architecture

### Threat model

| Threat | Required future control | What this milestone proves / does not prove |
|---|---|---|
| Filesystem escape | Separate disposable guest; no host profile or checkout mounts; stage only reviewed synthetic input; treat all output as hostile; test reparse points and concurrent mutation | Assessment can refuse missing host-share/cleanup evidence. It performs no filesystem containment and proves no escape resistance. |
| Same-user process interference | Do not launch code in the host user session; guest must have no host process handles, named pipes, shared memory, desktop/session, or writable host paths. Test host-process access and escape attempts. | A VM boundary is the proposed control; no same-user interference test was run. |
| Windows junction/reparse-point attacks | Do not expose writable host directories; canonicalize input staging; use handle-aware validation in trusted host import; adversarial race testing on target NTFS | No junction/reparse probing or race test is implemented here. Path checks alone are not a sandbox. |
| Credential and environment access | No host credentials, tokens, profile, SSH agent, browser data, or inherited variables in guest; build a minimal allowlisted environment; guest-admin code contained by the disposable VM boundary | Missing credential/environment/guest-containment evidence blocks the assessment. No credentials are inspected, copied, or sanitized by this module. |
| Network exfiltration | Disable guest networking and sharing; verify public, private, host-loopback, DNS, and proxy routes are unreachable from the guest | Missing network evidence blocks the assessment. No network test or firewall rule is applied. |
| Child-process escape | Start the approved process suspended, assign it to a non-breakaway Job Object, then resume; terminate the entire job on timeout/cancel; retain the VM boundary as the primary security boundary | Missing child-containment evidence blocks the assessment. Job Objects alone do not isolate filesystem, credentials, network, or kernel access. No process is created. |
| Process/resource exhaustion | Per-task wall time, CPU, memory, virtual disk, process count, and output quotas enforced by both guest supervisor and VM host; kill/discard the guest on quota breach | The API validates proposed ceilings only. It does not enforce or measure any limit. |
| Malicious child output and audit tampering | Host-side bounded audit using stable outcome/reason codes and hashes; never trust guest logs or output; revalidate and quarantine artifacts before import | The assessment returns path-free reason codes. It does not persist an execution outcome; there are no executions to audit. Existing local SQLite audit is access-scoped but is not an externally tamper-evident log. |
| Crash, timeout, or cleanup failure | Destroy the disposable guest; isolate failed output; clear only a verified per-task temporary root; record cleanup failure and keep execution disabled | Missing cleanup evidence blocks assessment. No guest or temporary task root is created or cleaned. Secure erasure is not claimed. |

### Existing Jarvis authorization and audit boundary

M06-A's file-change path remains separate from process execution. `CodingCapabilityPolicy` only grants its current allowlist; `EXECUTE_VERIFICATION`, worktree mutation, network, commit, push, merge, and deployment capabilities remain unavailable. `CodingTaskService` binds tasks to the authenticated principal, authorized workspace, and registered repository and records fixed audit outcomes. `CodingOperatorWorkflow` is a trusted in-process terminal helper with literal human confirmation, not an arbitrary-shell endpoint. `VerificationPlanner` only describes fixed checks and always returns `blocked_isolation_unverified`. M06-A application separately requires its existing file-change capability and workspace READ/WRITE. M06-B adds no grants, routes, migration, or changes to these behaviors.

The new assessment does not create an audit row because it does not launch or complete an execution. A future runner must persist a bounded, sanitized outcome through the existing task audit path, outside the guest, and treat SQLite as access-scoped but not externally tamper-evident.
### Windows options evaluated

Facts below reflect Microsoft and vendor documentation consulted on **2026-11-23**. Requirements are availability prerequisites, not proof that Jarvis isolation works. The target edition was not probed during this milestone; prior repository documentation reports Windows 11 Home. If that report remains current, Sandbox and the Hyper-V role are unavailable on that host and no paid upgrade is assumed.

| Option | Isolation and requirements | Availability, operations, and local-first fit | Decision |
|---|---|---|---|
| **Windows Sandbox** | Disposable, hypervisor-backed Windows environment. Supported editions are Pro, Enterprise, Pro Education/SE, and Education; Home is unsupported. Requires hardware virtualization and documented host resources (at least 4 GB RAM, 1 GB free disk, two CPU cores). Networking, clipboard redirection, audio input, and vGPU are enabled by default and must be explicitly disabled. A `.wsb` `LogonCommand` runs as an administrator account inside the guest; protected-client mode adds a security layer but does not prove command containment from the host. Mapped folders are a host boundary and writable mappings persist. | Built into supported Windows editions; no paid cloud service or extra runtime. Low operational overhead, but supported SKU and virtualization are hard gates. Must configure every task and independently test host sharing, egress, guest identity, process cleanup, resource controls, and output import. | **Selected as the disposable evaluation candidate only. Not a verified or enabled execution boundary.** If guest-administrator containment, disabled sharing, resource enforcement, or cleanup cannot be established, refuse and evaluate a dedicated VM instead. |
| **Dedicated Hyper-V VM** | Separate guest kernel and separately configurable guest identity, disk, virtual switch, snapshots, and VM lifecycle. Hyper-V on Windows client requires a supported edition (documented for Pro/Enterprise) and hardware virtualization. | Built-in Windows feature, no cloud service. Higher setup/patch/snapshot/guest-image/licensing burden. Stronger architectural control than process isolation, but shared folders, enhanced session, virtual networking, guest additions, and management APIs must be explicitly reviewed. A Windows guest may have separate licensing obligations. | **Fallback architecture** if Sandbox cannot meet the independent acceptance tests; not installed or configured by this change. |
| **Windows containers** | Process-isolated containers share the host kernel; Hyper-V-isolated containers use a lightweight utility VM for a separate kernel. Windows image/host version compatibility and container runtime support matter. | Potentially efficient, but more runtime/image/update/licensing complexity. A container is not automatically an application-level security boundary; process isolation is not selected for hostile code. Docker Desktop is a proprietary external runtime with use-dependent subscription terms and is not required or adopted. | Not selected. Hyper-V isolation would still require host/guest boundary testing; no container runtime is installed. |
| **Restricted token** | Removes privileges/SIDs and can reduce a process token’s authority. | Built-in Win32 API and free to use; requires correct process creation, ACLs, handle hygiene, and testing. Same host kernel and account/session; not a VM boundary and does not by itself block network or all same-user interaction. | Defense in depth inside a validated guest only; not sufficient alone. |
| **AppContainer / Win32 app isolation** | Capability- and ACL-based per-process restrictions; useful for tightly declared app resource access. | Built into Windows APIs, but compatibility and complete policy coverage are workload-specific. A general build/test toolchain may need many capabilities; same host kernel remains in scope. | Not selected as the sole hostile-code boundary. Could be layered and separately validated later. |
| **Job Objects** | Can constrain process groups and provide CPU/memory/process controls and kill-on-close behavior when correctly configured. Breakaway and assignment races need explicit handling. | Built-in Win32 API, no paid service; low deployment cost but requires a native trusted supervisor and careful launch order. Does not constrain filesystem, network, credentials, or the kernel. | Required defense-in-depth for child/resource lifecycle inside a guest, never a sandbox by itself. |
| **WSL 2 / WSL Containers** | WSL 2 uses a utility VM, but integrates with Windows files, Windows process interop, localhost/networking, and host services. Microsoft's WSL Containers API is newly documented and requires WSL 2.9.3 or later. | WSL is a local Windows feature. It is convenient for Linux tooling but has host integration and evolving API/support surface; a Linux root user is not a Windows security boundary. | Not selected for hostile repository code. Treat as developer convenience, not isolation. |

**Selected architecture for future evaluation:** A fresh Windows Sandbox per approved task on a supported, fully patched Pro/Enterprise-class host, with network, clipboard, audio input, and vGPU disabled; protected-client mode enabled; video input and printer redirection explicitly confirmed disabled; no profile, credential, repository, or writable checkout mapped; only a sanitized synthetic input fixture if required. The `.wsb` configuration and stable Sandbox client must be pinned. A `LogonCommand` runs with guest-administrator rights, so containment of that elevated guest process requires independent validation; never pass untrusted text as a shell command. The host remains outside the guest. A trusted supervisor must enforce immutable command IDs/arguments, explicit one-use snapshot-bound approval, a minimal guest environment, child-process Job Object containment, time/resource limits, bounded host-side audit, and disposable cleanup. Host output must be treated as attacker-controlled and imported only through a separately reviewed parser. This is a proposed architecture, not implemented behavior. If any requirement cannot be demonstrated, the system must return to read-only mode; the dedicated-VM fallback requires a separate review.

## Non-executing assessment API

`jarvis_core/windows_isolation.py` accepts explicit `WindowsSandboxHostFacts`, authorization evidence, isolation-control evidence, and resource limits. It returns stable, path-free reason codes and separates reported host prerequisites from approval and control evidence. Unknown facts fail closed. The module does not inspect the host or verify caller claims; it does not import `subprocess`, invoke Windows management APIs, change optional features, create a sandbox, write an audit record, or start a process.

`execution_enabled` is always `False`, even if every supplied fact is `True`. The existing `VerificationPlanner` remains `blocked_isolation_unverified`, and existing M06-A approval/capability behavior is unchanged. These are integration points for a later separately approved supervisor, not an execution API.

The code defines **proposed, unenforced** ceilings for wall time (600 seconds), guest memory and disk (4,096 MiB each), child count (64), CPU quota (100 percent), and captured output (2,000,000 bytes). The assessment reserves 2,048 MiB of caller-reported available host RAM and 1,024 MiB of free host disk; a future launcher would need to remeasure these immediately before launch. These conservative policy placeholders require hardware/workload calibration and host enforcement before any use. The host minimums in code are only Windows Sandbox availability prerequisites.

## Validation evidence

**Implemented/tested:** Synthetic tests exercise supported and unsupported reported host facts, missing Sandbox/virtualization, explicit approval and capability denial, resource ceilings, missing child-process and cleanup evidence, stable refusal, and a no-process-launch invariant. Existing verification planning remains blocked.

**Not established:** No actual Windows edition/feature probe, Sandbox or Hyper-V configuration, host ACL, guest filesystem/network/credential probe, resource enforcement, child-process kill, cleanup, race test, or independent Windows security validation ran. Synthetic assertions are not host evidence. No runtime, package, Windows feature, or new permission was installed or enabled.

## Future acceptance gate — execution remains NO-GO

Before separately authorizing even one verification command, require all of the following on a disposable synthetic repository and the actual supported Windows edition:

1. Independent security review of the exact Sandbox/VM config and threat assumptions.
2. Demonstrated deny-by-default; human approval binds task, repo snapshot, exact allowlisted command/arguments, limits, and expiry; current workspace and execution permissions rechecked at launch.
3. No host profile/credentials/environment inheritance; no writable host checkout; guest identity and all Windows integration channels documented and tested.
4. Network denial tested against public, private, host-loopback, DNS, proxy, and alternate adapters.
5. Malicious path/reparse, Git configuration/hook, same-user interference, and concurrent race probes.
6. Child process cannot break containment; timeout/cancel terminates every descendant and the guest; CPU/memory/disk/process/output limits trigger bounded failure.
7. Crash, power-loss, audit-write failure, and cleanup failure leave the canonical checkout untouched and task terminal/quarantined.
8. Bounded output import, audit privacy/integrity review, full Python/Node regressions, and updated owner-reviewed security evidence.

Passing this future gate would not itself authorize execution, promotion, push, or deployment; those require separate explicit approval.

## Official references

All links below are official first-party documentation. Accessed **2026-11-23**; check for updates before any future adoption.

- Microsoft, [Windows Sandbox overview, editions, behavior, and requirements](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/).
- Microsoft, [Install Windows Sandbox and prerequisites](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-install).
- Microsoft, [Configure Windows Sandbox (`.wsb`) and mapped folders](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file).
- Microsoft, [Install Hyper-V and supported Windows editions](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/get-started/install-hyper-v).
- Microsoft, [Windows containers and isolation modes](https://learn.microsoft.com/en-us/virtualization/windowscontainers/about/).
- Microsoft, [Windows container host setup](https://learn.microsoft.com/en-us/virtualization/windowscontainers/quick-start/set-up-environment).
- Microsoft, [CreateRestrictedToken function](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-createrestrictedtoken).
- Microsoft, [AppContainer isolation](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation).
- Microsoft, [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).
- Microsoft, [WSL enterprise security](https://learn.microsoft.com/en-us/windows/wsl/enterprise), [WSL filesystems](https://learn.microsoft.com/en-us/windows/wsl/filesystems), and [WSL networking](https://learn.microsoft.com/en-us/windows/wsl/networking).
- Microsoft, [WSL Containers CLI and API](https://learn.microsoft.com/en-us/windows/wsl/wsl-container); WSL 2.9.3 was released 2026-09-29 per the [official WSL releases](https://github.com/microsoft/WSL/releases).
- Docker, [Docker Desktop license terms](https://docs.docker.com/subscription/desktop-license/); candidate runtime only, not adopted.