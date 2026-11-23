"""Non-executing assessment of a future Windows Sandbox boundary.

This module consumes caller-supplied host observations. It does not probe the
machine, change Windows features, create a sandbox, or launch any process.
Execution remains unavailable even when every reported prerequisite passes.
"""
from __future__ import annotations

from dataclasses import dataclass


SUPPORTED_WINDOWS_EDITIONS = frozenset({
    "pro", "enterprise", "education", "pro education", "pro education se",
})
SUPPORTED_ARCHITECTURES = frozenset({"amd64", "x86_64", "arm64", "aarch64", "x64"})

WINDOWS_SANDBOX_MIN_BUILD = 18362
WINDOWS_SANDBOX_MIN_MEMORY_MIB = 4096
PROPOSED_HOST_MEMORY_RESERVE_MIB = 2048
WINDOWS_SANDBOX_MIN_FREE_DISK_MIB = 1024
WINDOWS_SANDBOX_MIN_CPU_CORES = 2

PROPOSED_MAX_WALL_SECONDS = 600
PROPOSED_MAX_MEMORY_MIB = 4096
PROPOSED_MAX_DISK_MIB = 4096
PROPOSED_MAX_CHILD_PROCESSES = 64
PROPOSED_MAX_CPU_PERCENT = 100
PROPOSED_MAX_OUTPUT_BYTES = 2_000_000


@dataclass(frozen=True)
class WindowsSandboxHostFacts:
    os_name: str | None = None
    windows_build: int | None = None
    edition: str | None = None
    architecture: str | None = None
    sandbox_feature_enabled: bool | None = None
    hardware_virtualization_enabled: bool | None = None
    installed_memory_mib: int | None = None
    available_memory_mib: int | None = None
    free_disk_mib: int | None = None
    cpu_cores: int | None = None


@dataclass(frozen=True)
class ExecutionResourceLimits:
    wall_seconds: int | None = None
    memory_mib: int | None = None
    disk_mib: int | None = None
    child_processes: int | None = None
    cpu_percent: int | None = None
    output_bytes: int | None = None


@dataclass(frozen=True)
class IsolationControlEvidence:
    network_disabled: bool | None = None
    clipboard_disabled: bool | None = None
    vgpu_disabled: bool | None = None
    audio_input_disabled: bool | None = None
    video_input_disabled: bool | None = None
    printer_redirection_disabled: bool | None = None
    protected_client_enabled: bool | None = None
    stable_sandbox_client_verified: bool | None = None
    configuration_hash_verified: bool | None = None
    host_mappings_absent_or_synthetic_read_only: bool | None = None
    host_credentials_excluded: bool | None = None
    environment_allowlisted: bool | None = None
    guest_admin_containment_verified: bool | None = None
    child_process_containment_verified: bool | None = None
    wall_timeout_enforced: bool | None = None
    memory_limit_enforced: bool | None = None
    disk_limit_enforced: bool | None = None
    child_process_limit_enforced: bool | None = None
    output_limit_enforced: bool | None = None
    audit_sink_verified: bool | None = None
    cleanup_verified: bool | None = None


@dataclass(frozen=True)
class ExecutionAuthorizationEvidence:
    explicit_snapshot_bound_approval: bool = False
    execute_verification_capability: bool = False
    workspace_authorized: bool = False


@dataclass(frozen=True)
class WindowsIsolationAssessment:
    sandbox_host_candidate_available: bool
    authorization_reported: bool
    controls_reported_complete: bool
    execution_enabled: bool
    reason_codes: tuple[str, ...]


def _valid_integer(value: int | None, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _resource_limit_reasons(
    limits: ExecutionResourceLimits | None,
    host: WindowsSandboxHostFacts,
) -> list[str]:
    if limits is None:
        return ["resource_limits_missing"]
    bounds = (
        ("wall_seconds", limits.wall_seconds, 1, PROPOSED_MAX_WALL_SECONDS),
        ("memory_mib", limits.memory_mib, 1, PROPOSED_MAX_MEMORY_MIB),
        ("disk_mib", limits.disk_mib, 1, PROPOSED_MAX_DISK_MIB),
        ("child_processes", limits.child_processes, 1, PROPOSED_MAX_CHILD_PROCESSES),
        ("cpu_percent", limits.cpu_percent, 1, PROPOSED_MAX_CPU_PERCENT),
        ("output_bytes", limits.output_bytes, 1, PROPOSED_MAX_OUTPUT_BYTES),
    )
    reasons = [f"resource_limit_invalid_{name}" for name, value, minimum, maximum in bounds
                if not _valid_integer(value, minimum, maximum)]
    if type(host.available_memory_mib) is not int or host.available_memory_mib < 0:
        reasons.append("resource_host_memory_unknown")
    elif (type(limits.memory_mib) is int
            and limits.memory_mib > min(
                PROPOSED_MAX_MEMORY_MIB,
                host.available_memory_mib - PROPOSED_HOST_MEMORY_RESERVE_MIB)):
        reasons.append("resource_limit_exceeds_host_memory_reserve")
    if (type(host.free_disk_mib) is int and type(limits.disk_mib) is int
            and limits.disk_mib > min(PROPOSED_MAX_DISK_MIB, host.free_disk_mib - 1024)):
        reasons.append("resource_limit_exceeds_host_disk_reserve")
    return reasons


def assess_windows_sandbox(
    host: WindowsSandboxHostFacts,
    *,
    authorization: ExecutionAuthorizationEvidence | None = None,
    controls: IsolationControlEvidence | None = None,
    limits: ExecutionResourceLimits | None = None,
) -> WindowsIsolationAssessment:
    """Assess reported prerequisites without enabling or performing execution."""
    if not isinstance(host, WindowsSandboxHostFacts):
        raise TypeError("host facts must be WindowsSandboxHostFacts")

    reasons: list[str] = []
    if not (host.os_name or "").casefold().startswith("windows"):
        reasons.append("unsupported_platform")
    edition = (host.edition or "").casefold().replace("_", " ").strip()
    edition = {
        "professional": "pro", "professional n": "pro",
        "pro education/se": "pro education se",
    }.get(edition, edition)
    if edition not in SUPPORTED_WINDOWS_EDITIONS:
        reasons.append("unsupported_windows_edition")
    if not _valid_integer(host.windows_build, WINDOWS_SANDBOX_MIN_BUILD, 100_000):
        reasons.append("unsupported_or_unknown_windows_build")
    architecture = (host.architecture or "").casefold()
    if architecture not in SUPPORTED_ARCHITECTURES:
        reasons.append("unsupported_or_unknown_architecture")
    if (architecture in {"arm64", "aarch64"} and type(host.windows_build) is int
            and host.windows_build < 22621):
        reasons.append("unsupported_arm64_windows_build")
    if host.sandbox_feature_enabled is not True:
        reasons.append("sandbox_feature_not_confirmed_enabled")
    if host.hardware_virtualization_enabled is not True:
        reasons.append("hardware_virtualization_not_confirmed_enabled")
    if not _valid_integer(host.installed_memory_mib, WINDOWS_SANDBOX_MIN_MEMORY_MIB, 1_048_576):
        reasons.append("insufficient_or_unknown_host_memory")
    if not _valid_integer(host.free_disk_mib, WINDOWS_SANDBOX_MIN_FREE_DISK_MIB, 1_048_576):
        reasons.append("insufficient_or_unknown_free_disk")
    if not _valid_integer(host.cpu_cores, WINDOWS_SANDBOX_MIN_CPU_CORES, 4096):
        reasons.append("insufficient_or_unknown_cpu_cores")
    candidate_available = not reasons

    authorization = authorization or ExecutionAuthorizationEvidence()
    authorization_reasons = []
    if authorization.explicit_snapshot_bound_approval is not True:
        authorization_reasons.append("explicit_approval_missing")
    if authorization.execute_verification_capability is not True:
        authorization_reasons.append("execution_capability_missing")
    if authorization.workspace_authorized is not True:
        authorization_reasons.append("workspace_authorization_missing")
    authorization_reported = not authorization_reasons
    reasons.extend(authorization_reasons)

    controls = controls or IsolationControlEvidence()
    control_reasons = [
        f"control_unverified_{name}"
        for name, value in controls.__dict__.items()
        if value is not True
    ]
    controls_reported_complete = not control_reasons
    reasons.extend(control_reasons)
    reasons.extend(_resource_limit_reasons(limits, host))

    reasons.append("execution_implementation_absent")
    return WindowsIsolationAssessment(
        sandbox_host_candidate_available=candidate_available,
        authorization_reported=authorization_reported,
        controls_reported_complete=controls_reported_complete,
        execution_enabled=False,
        reason_codes=tuple(dict.fromkeys(reasons)),
    )