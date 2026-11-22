"""Persistent approval-first task planning and simulation.

This module stores human goals, immutable plan revisions, dependencies, and
lifecycle events. Its only worker implementation is deterministic and
simulation-only; it cannot launch tools, processes, or network requests.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import re
import secrets
import sqlite3
from typing import Protocol
from uuid import uuid4

from .coding_agents import CodingAuthorizationDenied, CodingCapability, CodingCapabilityPolicy
from .developer import DeveloperRepositoryService, RepositoryError, StaleRepository
from .storage import AccessDenied, Database, Permission, _now, _nonempty


MAX_GOAL_CHARS = 4000
MAX_SUBTASKS = 25
MAX_SUBTASK_TITLE_CHARS = 120
MAX_SUBTASK_GOAL_CHARS = 1000
MAX_ACCEPTANCE_CHARS = 300
MAX_RESULT_SUMMARY_CHARS = 500
MAX_LIFETIME_SECONDS = 30 * 24 * 60 * 60
MAX_ACTIVE_TASKS = 100
MAX_DEPENDENCIES = 200
MAX_RETENTION_DAYS = 3650
MAX_POLICY_LIFETIME_DAYS = 365
MAX_PURGE_BATCH = 50
MAX_HISTORY_OFFSET = 10_000
_TASK_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_SAFE_EVENT_TYPES = {
    "task_created", "plan_proposed", "plan_approved", "plan_rejected",
    "task_blocked", "simulation_progress", "simulation_completed",
    "task_cancelled", "task_interrupted", "simulation_resumed",
    "task_archived", "task_restored",
}


class OrchestrationError(AccessDenied):
    """Stable task-orchestration error that does not reveal inaccessible records."""


class TaskState(str, Enum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    QUEUED = "queued"
    SIMULATING = "simulating"
    SIMULATION_COMPLETED = "simulation_completed"
    BLOCKED = "blocked"
    COMPLETED = "completed"  # Reserved for a future verified runtime; unreachable here.
    FAILED = "failed"  # Reserved for a future verified runtime; unreachable here.
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    STALE = "stale"


_PROTECTED_TASK_STATES = frozenset({
    TaskState.DRAFT,
    TaskState.AWAITING_APPROVAL,
    TaskState.QUEUED,
    TaskState.SIMULATING,
    TaskState.BLOCKED,
})

_RETENTION_ELIGIBLE_STATES = frozenset({
    TaskState.SIMULATION_COMPLETED,
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELLED,
    TaskState.INTERRUPTED,
    TaskState.STALE,
})


class SubtaskState(str, Enum):
    PENDING = "pending"
    BLOCKED = "blocked"
    SIMULATING = "simulating"
    SIMULATED_COMPLETED = "simulated_completed"
    SIMULATED_FAILED = "simulated_failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class OrchestrationTask:
    id: str
    workspace_id: str
    principal_id: str
    repository_id: str | None
    repository_commit: str | None
    goal: str
    goal_sha256: str
    state: TaskState
    execution_mode: str
    requested_capabilities: tuple[str, ...]
    capability_manifest: dict[str, object]
    capability_manifest_sha256: str
    current_plan_revision: int
    approved_plan_revision: int | None
    result_summary: str | None
    created_at: str
    updated_at: str
    expires_at: str
    archived_at: str | None = None
    archived_by: str | None = None


@dataclass(frozen=True)
class TaskRetentionPolicy:
    workspace_id: str
    completed_retention_days: int | None = None
    cancelled_retention_days: int | None = None
    failed_retention_days: int | None = None
    interrupted_retention_days: int | None = None
    auto_delete_enabled: bool = False
    updated_by: str | None = None
    updated_at: str | None = None
    expires_at: str | None = None
    is_expired: bool = False


@dataclass(frozen=True)
class TaskHistoryPage:
    items: tuple[OrchestrationTask, ...]
    total_count: int
    limit: int
    offset: int
    has_more: bool


@dataclass(frozen=True)
class PurgePreviewItem:
    task_id: str
    state: TaskState
    archived_at: str | None
    updated_at: str
    goal_sha256: str
    plan_count: int
    subtask_count: int
    dependency_count: int
    approval_count: int
    event_count: int


@dataclass(frozen=True)
class PurgePreview:
    workspace_id: str
    preview_sha256: str
    confirmation_phrase: str | None
    archived_only: bool
    use_retention_policy: bool
    items: tuple[PurgePreviewItem, ...]
    task_count: int
    plan_count: int
    subtask_count: int
    dependency_count: int
    approval_count: int
    event_count: int
    truncated: bool
    protected_skipped_count: int

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(item.task_id for item in self.items)


@dataclass(frozen=True)
class PurgeTombstone:
    workspace_id: str
    task_id: str
    purge_id: str
    task_state: TaskState
    archived_at: str | None
    goal_sha256: str
    plan_count: int
    subtask_count: int
    dependency_count: int
    approval_count: int
    event_count: int
    purged_by: str
    purged_at: str


@dataclass(frozen=True)
class PurgeReceipt:
    purge_id: str
    workspace_id: str
    preview_sha256: str
    task_ids: tuple[str, ...]
    task_count: int
    plan_count: int
    subtask_count: int
    dependency_count: int
    approval_count: int
    event_count: int
    purged_by: str
    purged_at: str
    already_purged: bool = False


@dataclass(frozen=True)
class PlannedSubtask:
    id: str
    title: str
    goal: str
    acceptance_condition: str
    required_capabilities: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskPlanProposal:
    subtasks: tuple[PlannedSubtask, ...]


@dataclass(frozen=True)
class TaskPlan:
    task_id: str
    revision: int
    plan_sha256: str
    state: str
    subtasks: tuple[PlannedSubtask, ...]


@dataclass(frozen=True)
class SubtaskProgress:
    subtask_id: str
    state: SubtaskState
    progress_percent: int
    result_summary: str | None


@dataclass(frozen=True)
class TaskPlanningRequest:
    task_id: str
    workspace_id: str
    repository_id: str | None
    repository_commit: str | None
    goal: str
    requested_capabilities: tuple[str, ...]
    currently_granted_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class SimulationStep:
    progress_percent: int
    summary: str
    completed: bool = False


class TaskPlanner(Protocol):
    def propose_plan(self, request: TaskPlanningRequest) -> TaskPlanProposal: ...


class TaskWorker(Protocol):
    def simulate_step(self, subtask: PlannedSubtask, current_progress: int) -> SimulationStep: ...


class DeterministicFakePlanner:
    """Offline fixture planner. No model, remote API, or repository tool is called."""

    def __init__(self, proposal: TaskPlanProposal) -> None:
        self.proposal = proposal
        self.calls = 0

    def propose_plan(self, request: TaskPlanningRequest) -> TaskPlanProposal:
        self.calls += 1
        return self.proposal


class DeterministicFakeWorker:
    """Scripted worker whose only effect is returning bounded fake progress."""

    def __init__(self, steps: dict[str, tuple[SimulationStep, ...]]) -> None:
        self.steps = dict(steps)
        self.calls: list[tuple[str, int]] = []

    def simulate_step(self, subtask: PlannedSubtask, current_progress: int) -> SimulationStep:
        sequence = self.steps.get(subtask.id, ())
        self.calls.append((subtask.id, current_progress))
        step = next((item for item in sequence if item.progress_percent > current_progress), None)
        if step is None:
            raise OrchestrationError("fake_simulation_step_unconfigured")
        return step


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="strict")).hexdigest()


def _idempotency(value: str) -> str:
    if not isinstance(value, str) or not _IDEMPOTENCY.fullmatch(value):
        raise ValueError("idempotency key must be 16-128 URL-safe characters")
    return value


def _bounded_text(value: object, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{name} is required")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ValueError(f"{name} contains invalid text") from None
    if len(value) > limit or len(encoded) > limit * 4:
        raise ValueError(f"{name} exceeds the supported bound")
    return value.strip()


def _parse_utc(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} must be an ISO-8601 UTC timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _validate_retention_days(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= MAX_RETENTION_DAYS:
        raise ValueError(f"{name} must be None or an integer between 1 and {MAX_RETENTION_DAYS}")
    return value


def _retention_days_for_state(policy: TaskRetentionPolicy, state: TaskState) -> int | None:
    if state in {TaskState.COMPLETED, TaskState.SIMULATION_COMPLETED}:
        return policy.completed_retention_days
    if state in {TaskState.CANCELLED, TaskState.STALE}:
        return policy.cancelled_retention_days
    if state == TaskState.FAILED:
        return policy.failed_retention_days
    if state == TaskState.INTERRUPTED:
        return policy.interrupted_retention_days
    return None


def _purge_confirmation_phrase(workspace_id: str, preview_sha256: str) -> str:
    return f"PURGE {workspace_id} {preview_sha256}"


def _plan_payload(subtasks: tuple[PlannedSubtask, ...]) -> dict[str, object]:
    return {"schema": 1, "subtasks": [{
        "id": item.id,
        "title": item.title,
        "goal": item.goal,
        "acceptance_condition": item.acceptance_condition,
        "required_capabilities": sorted(item.required_capabilities),
        "dependencies": sorted(item.dependencies),
    } for item in subtasks]}


def _validate_plan(proposal: TaskPlanProposal,
                   requested_capabilities: set[str]) -> tuple[PlannedSubtask, ...]:
    if type(proposal) is not TaskPlanProposal or not isinstance(proposal.subtasks, tuple):
        raise ValueError("planner result must be a bounded task plan")
    if not proposal.subtasks or len(proposal.subtasks) > MAX_SUBTASKS:
        raise ValueError("plan must contain 1-25 subtasks")
    normalized: list[PlannedSubtask] = []
    identifiers: set[str] = set()
    edge_count = 0
    valid_capabilities = {item.value for item in CodingCapability}
    for item in proposal.subtasks:
        if type(item) is not PlannedSubtask or not _TASK_ID.fullmatch(item.id):
            raise ValueError("subtask ID or structure is invalid")
        if item.id in identifiers:
            raise ValueError("subtask IDs must be unique")
        identifiers.add(item.id)
        title = _bounded_text(item.title, "subtask title", MAX_SUBTASK_TITLE_CHARS)
        goal = _bounded_text(item.goal, "subtask goal", MAX_SUBTASK_GOAL_CHARS)
        condition = _bounded_text(item.acceptance_condition, "acceptance condition", MAX_ACCEPTANCE_CHARS)
        if (not isinstance(item.required_capabilities, tuple)
                or not isinstance(item.dependencies, tuple)
                or len(item.required_capabilities) > len(valid_capabilities)
                or len(item.dependencies) > MAX_SUBTASKS):
            raise ValueError("subtask capability or dependency list is invalid")
        capabilities: set[str] = set()
        for value in item.required_capabilities:
            try:
                capability = CodingCapability(value).value
            except (TypeError, ValueError):
                raise ValueError("plan contains an unknown capability") from None
            if capability not in requested_capabilities:
                raise ValueError("plan cannot request an unapproved task capability")
            capabilities.add(capability)
        dependencies = tuple(sorted(set(item.dependencies)))
        if len(dependencies) != len(item.dependencies) or any(
                not isinstance(value, str) or not _TASK_ID.fullmatch(value) for value in dependencies):
            raise ValueError("subtask dependencies are invalid")
        edge_count += len(dependencies)
        normalized.append(PlannedSubtask(item.id, title, goal, condition,
                                         tuple(sorted(capabilities)), dependencies))
    if edge_count > MAX_DEPENDENCIES:
        raise ValueError("plan has too many dependencies")
    for item in normalized:
        if any(dependency not in identifiers for dependency in item.dependencies):
            raise ValueError("plan references an unknown dependency")

    graph = {item.id: item.dependencies for item in normalized}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError("plan dependency cycle detected")
        if node in visited:
            return
        visiting.add(node)
        for parent in graph[node]:
            visit(parent)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)
    return tuple(normalized)


class TaskOrchestrationService:
    """Durable workspace-scoped task planning with simulation-only workers."""

    def __init__(self, database: Database,
                 repositories: DeveloperRepositoryService | None = None,
                 policy: CodingCapabilityPolicy | None = None) -> None:
        self.db = database
        self.repositories = repositories or DeveloperRepositoryService(database)
        self.policy = policy or CodingCapabilityPolicy(database)

    @staticmethod
    def _task(row) -> OrchestrationTask:
        try:
            requested = json.loads(row["requested_capabilities_json"])
            manifest = json.loads(row["capability_manifest_json"])
            capability_values = {item.value for item in CodingCapability}
            granted = manifest.get("granted") if isinstance(manifest, dict) else None
            if (not isinstance(requested, list) or len(requested) > len(CodingCapability)
                    or any(not isinstance(item, str) for item in requested)
                    or any(item not in capability_values for item in requested)
                    or requested != sorted(set(requested))
                    or not isinstance(manifest, dict)
                    or manifest.get("requested") != requested
                    or not isinstance(granted, list)
                    or any(not isinstance(item, str) or item not in capability_values
                           or item not in requested for item in granted)
                    or granted != sorted(set(granted))
                    or manifest.get("workspace_id") != row["workspace_id"]
                    or manifest.get("repository_id") != row["repository_id"]
                    or manifest.get("execution_enabled") is not False
                    or manifest.get("file_write_enabled") is not False
                    or manifest.get("process_execution_enabled") is not False
                    or manifest.get("network_enabled") is not False
                    or manifest.get("plan_approval_is_execution_approval") is not False
                    or len(row["capability_manifest_json"].encode("utf-8")) > 12000
                    or _digest(row["goal"]) != row["goal_sha256"]
                    or _digest(_canonical_json(manifest)) != row["capability_manifest_sha256"]
                    or (row["result_summary"] is not None and
                        (not isinstance(row["result_summary"], str)
                         or len(row["result_summary"]) > MAX_RESULT_SUMMARY_CHARS))):
                raise ValueError
            _bounded_text(row["goal"], "task goal", MAX_GOAL_CHARS)
            archived_at = row["archived_at"]
            archived_by = row["archived_by"]
            if (archived_at is None) != (archived_by is None):
                raise ValueError
            if archived_at is not None:
                if not isinstance(archived_by, str) or not archived_by.strip():
                    raise ValueError
                _parse_utc(archived_at, "archived_at")
        except (ValueError, TypeError, UnicodeError):
            raise OrchestrationError("task_evidence_corrupt") from None
        try:
            return OrchestrationTask(
                row["id"], row["workspace_id"], row["principal_id"],
                row["repository_id"], row["repository_commit"], row["goal"],
                row["goal_sha256"], TaskState(row["state"]), row["execution_mode"],
                tuple(requested), manifest, row["capability_manifest_sha256"],
                row["current_plan_revision"], row["approved_plan_revision"],
                row["result_summary"], row["created_at"], row["updated_at"], row["expires_at"],
                archived_at, archived_by,
            )
        except (ValueError, TypeError):
            raise OrchestrationError("task_evidence_corrupt") from None

    def _require_task_access(self, actor_id: str, task_id: str, *, write: bool = False,
                             manage: bool = False):
        actor_id = _nonempty(actor_id, "actor ID")
        task_id = _nonempty(task_id, "task ID")
        permission = Permission.WRITE if write else Permission.READ
        with self.db._connection() as connection:
            row = connection.execute(
                "SELECT * FROM orchestration_tasks WHERE id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise OrchestrationError("task_unavailable")
            Database._require_access(connection, actor_id, row["workspace_id"], permission)
            manager = self.db.check_access(actor_id, row["workspace_id"], Permission.MANAGE_WORKSPACE)
            if manage and not manager:
                raise OrchestrationError("task_manager_approval_required")
            if actor_id != row["principal_id"] and not manager:
                raise OrchestrationError("task_unavailable")
            return self._task(row)

    def _existing_event(self, workspace_id: str, actor_id: str,
                        idempotency_key: str, event_type: str,
                        task_id: str | None = None):
        with self.db._connection() as connection:
            row = connection.execute(
                "SELECT * FROM orchestration_task_events WHERE workspace_id=? "
                "AND actor_id=? AND idempotency_key=?",
                (workspace_id, actor_id, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        if row["event_type"] != event_type or (task_id is not None and row["task_id"] != task_id):
            raise OrchestrationError("idempotency_key_conflict")
        try:
            return json.loads(row["metadata_json"])
        except (ValueError, TypeError):
            raise OrchestrationError("task_evidence_corrupt") from None

    def _insert_event(self, connection: sqlite3.Connection, task: OrchestrationTask,
                      actor_id: str, event_type: str, idempotency_key: str,
                      *, revision: int | None = None, snapshot_sha256: str | None = None,
                      metadata: dict[str, str] | None = None) -> int:
        if event_type not in _SAFE_EVENT_TYPES:
            raise ValueError("unsupported lifecycle event")
        safe_metadata = metadata or {}
        if (not isinstance(safe_metadata, dict) or len(safe_metadata) > 12
                or any(not isinstance(key, str) or len(key) > 40
                       or not isinstance(value, str) or len(value) > 200
                       for key, value in safe_metadata.items())):
            raise ValueError("event metadata exceeds the supported bound")
        rendered = _canonical_json(safe_metadata)
        if len(rendered.encode("utf-8")) > 2048:
            raise ValueError("event metadata exceeds the supported bound")
        cursor = connection.execute(
            "INSERT INTO orchestration_task_events(task_id,workspace_id,principal_id,actor_id,event_type,"
            "plan_revision,snapshot_sha256,idempotency_key,metadata_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (task.id, task.workspace_id, task.principal_id, actor_id, event_type, revision,
             snapshot_sha256, idempotency_key, rendered, _now()),
        )
        event_id = int(cursor.lastrowid)
        Database._insert_audit(connection, task.workspace_id, actor_id,
                               f"orchestration.{event_type}", "orchestration_task", task.id,
                               {"event_id": str(event_id), "revision": str(revision or 0),
                                "snapshot_sha256": snapshot_sha256 or ""})
        return event_id

    def create_task(self, actor_id: str, workspace_id: str, goal: str, *,
                    repository_id: str | None = None,
                    requested_capabilities: tuple[str, ...] = (),
                    idempotency_key: str,
                    lifetime_seconds: int = 7 * 24 * 60 * 60) -> OrchestrationTask:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        goal = _bounded_text(goal, "task goal", MAX_GOAL_CHARS)
        idempotency_key = _idempotency(idempotency_key)
        if type(lifetime_seconds) is not int or not 60 <= lifetime_seconds <= MAX_LIFETIME_SECONDS:
            raise ValueError("task lifetime is outside the supported bound")
        if not isinstance(requested_capabilities, tuple):
            raise ValueError("requested capabilities must be a tuple")
        requested = tuple(sorted({CodingCapability(item).value for item in requested_capabilities}))
        if len(requested) != len(requested_capabilities):
            raise ValueError("requested capabilities must be unique")
        if repository_id is None and requested:
            raise ValueError("repository capabilities require repository scope")
        self.db.require_access(actor_id, workspace_id, Permission.READ)
        self.db.require_access(actor_id, workspace_id, Permission.WRITE)
        repo_commit = None
        if repository_id is not None:
            repository_id = _nonempty(repository_id, "repository ID")
            effective_requested = tuple(sorted(set(requested) | {
                CodingCapability.READ_REPOSITORY_METADATA.value}))
            self.policy.require(actor_id, workspace_id, repository_id,
                                CodingCapability.READ_REPOSITORY_METADATA)
            repository = self.repositories._repository(actor_id, workspace_id, repository_id)
            self.repositories._ensure_current(actor_id, workspace_id, repository)
            if repository.index_status != "ready" or not repository.head_commit:
                raise StaleRepository()
            repo_commit = repository.head_commit
            manifest = self.policy.manifest(actor_id, workspace_id, repository_id,
                                            list(effective_requested))
        else:
            effective_requested = requested
            manifest = {"workspace_id": workspace_id, "repository_id": None,
                        "requested": list(requested), "granted": [], "denied": [],
                        "unavailable": [], "execution_enabled": False}
        manifest.update({"execution_enabled": False, "file_write_enabled": False,
                         "process_execution_enabled": False, "network_enabled": False,
                         "plan_approval_is_execution_approval": False})
        manifest_json = _canonical_json(manifest)
        goal_sha = _digest(goal)
        requested_hash = _digest(_canonical_json(list(effective_requested)))
        existing = self._existing_event(workspace_id, actor_id, idempotency_key, "task_created")
        if existing is not None:
            if (existing.get("goal_sha256") != goal_sha
                    or existing.get("repository_id") != (repository_id or "")
                    or existing.get("requested_capabilities_sha256") != requested_hash):
                raise OrchestrationError("idempotency_key_conflict")
            return self.get_task(actor_id, self._existing_task_id(workspace_id, actor_id, idempotency_key))

        task_id = uuid4().hex
        now = datetime.now(timezone.utc)
        created_at = now.isoformat(timespec="microseconds")
        expires_at = (now + timedelta(seconds=lifetime_seconds)).isoformat(timespec="microseconds")
        self.db.initialize()
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                Database._require_access(connection, actor_id, workspace_id, Permission.READ)
                Database._require_access(connection, actor_id, workspace_id, Permission.WRITE)
                active = connection.execute(
                    "SELECT COUNT(*) FROM orchestration_tasks WHERE workspace_id=? AND principal_id=? "
                    "AND state IN ('draft','awaiting_approval','queued','simulating','blocked','interrupted') "
                    "AND archived_at IS NULL AND expires_at>?", (workspace_id, actor_id, created_at),
                ).fetchone()[0]
                if active >= MAX_ACTIVE_TASKS:
                    raise OrchestrationError("active_task_limit")
                connection.execute(
                    "INSERT INTO orchestration_tasks(id,workspace_id,principal_id,repository_id,"
                    "repository_commit,goal,goal_sha256,state,execution_mode,requested_capabilities_json,"
                    "capability_manifest_json,capability_manifest_sha256,created_at,updated_at,expires_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (task_id, workspace_id, actor_id, repository_id, repo_commit, goal, goal_sha,
                     TaskState.DRAFT.value, "planned", _canonical_json(list(effective_requested)),
                     manifest_json, _digest(manifest_json), created_at, created_at, expires_at),
                )
                task = self._task(connection.execute(
                    "SELECT * FROM orchestration_tasks WHERE id=?", (task_id,)
                ).fetchone())
                self._insert_event(connection, task, actor_id, "task_created", idempotency_key,
                                   metadata={"goal_sha256": goal_sha,
                                             "repository_id": repository_id or "",
                                             "requested_capabilities_sha256": requested_hash})
                self.db._commit(connection)
            except sqlite3.IntegrityError as exc:
                self.db._rollback(connection)
                if "idempotency" in str(exc).lower() or "unique" in str(exc).lower():
                    existing = self._existing_event(workspace_id, actor_id, idempotency_key, "task_created")
                    if (existing is not None and existing.get("goal_sha256") == goal_sha
                            and existing.get("repository_id") == (repository_id or "")
                            and existing.get("requested_capabilities_sha256") == requested_hash):
                        return self.get_task(actor_id, self._existing_task_id(
                            workspace_id, actor_id, idempotency_key))
                raise OrchestrationError("task_conflict") from None
            except Exception:
                self.db._rollback(connection)
                raise
        return self.get_task(actor_id, task_id)

    def _existing_task_id(self, workspace_id: str, actor_id: str, key: str) -> str:
        with self.db._connection() as connection:
            row = connection.execute(
                "SELECT task_id FROM orchestration_task_events WHERE workspace_id=? "
                "AND actor_id=? AND idempotency_key=?",
                (workspace_id, actor_id, key),
            ).fetchone()
        if row is None:
            raise OrchestrationError("task_unavailable")
        return row["task_id"]

    def get_task(self, actor_id: str, task_id: str) -> OrchestrationTask:
        return self._require_task_access(actor_id, task_id)

    def list_tasks(self, actor_id: str, workspace_id: str, *, limit: int = 100,
                   include_history: bool = True,
                   include_archived: bool = False) -> tuple[OrchestrationTask, ...]:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("task list limit must be between 1 and 100")
        if type(include_history) is not bool or type(include_archived) is not bool:
            raise ValueError("task list filter flags must be booleans")
        self.db.initialize()
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                Database._require_access(connection, actor_id, workspace_id, Permission.READ)
                manager = self._has_permission(connection, actor_id, workspace_id,
                                               Permission.MANAGE_WORKSPACE)
                sql = "SELECT * FROM orchestration_tasks WHERE workspace_id=?"
                params: list[object] = [workspace_id]
                if not manager:
                    sql += " AND principal_id=?"
                    params.append(actor_id)
                if not include_archived:
                    sql += " AND archived_at IS NULL"
                if not include_history:
                    sql += " AND state NOT IN ('simulation_completed','completed','failed','cancelled','stale')"
                sql += " ORDER BY updated_at DESC,id LIMIT ?"
                params.append(limit)
                rows = connection.execute(sql, params).fetchall()
            finally:
                self.db._rollback(connection)
        return tuple(self._task(row) for row in rows)

    def _plan_rows(self, connection: sqlite3.Connection, task: OrchestrationTask,
                   revision: int) -> tuple[PlannedSubtask, ...]:
        rows = connection.execute(
            "SELECT * FROM orchestration_subtasks WHERE task_id=? AND workspace_id=? "
            "AND principal_id=? AND revision=? ORDER BY ordinal",
            (task.id, task.workspace_id, task.principal_id, revision),
        ).fetchall()
        subtasks: list[PlannedSubtask] = []
        for row in rows:
            try:
                capabilities = json.loads(row["required_capabilities_json"])
                if not isinstance(capabilities, list) or len(capabilities) > len(CodingCapability):
                    raise ValueError
                dependencies = [item[0] for item in connection.execute(
                    "SELECT depends_on_subtask_id FROM orchestration_dependencies WHERE task_id=? "
                    "AND revision=? AND subtask_id=? ORDER BY depends_on_subtask_id",
                    (task.id, revision, row["subtask_id"]),
                ).fetchall()]
                if not isinstance(capabilities, list):
                    raise ValueError
                subtasks.append(PlannedSubtask(row["subtask_id"], row["title"], row["goal"],
                                               row["acceptance_condition"], tuple(capabilities),
                                               tuple(dependencies)))
            except (ValueError, TypeError):
                raise OrchestrationError("task_evidence_corrupt") from None
        return tuple(subtasks)

    def get_plan(self, actor_id: str, task_id: str, revision: int | None = None) -> TaskPlan:
        task = self._require_task_access(actor_id, task_id)
        revision = task.current_plan_revision if revision is None else revision
        if type(revision) is not int or revision <= 0:
            raise ValueError("plan revision is invalid")
        with self.db._connection() as connection:
            plan = connection.execute(
                "SELECT * FROM orchestration_plans WHERE task_id=? AND workspace_id=? "
                "AND principal_id=? AND revision=?",
                (task.id, task.workspace_id, task.principal_id, revision),
            ).fetchone()
            if plan is None:
                raise OrchestrationError("plan_unavailable")
            subtasks = self._plan_rows(connection, task, revision)
        computed = _digest(_canonical_json(_plan_payload(subtasks)))
        if computed != plan["plan_sha256"]:
            raise OrchestrationError("task_evidence_corrupt")
        try:
            _validate_plan(TaskPlanProposal(subtasks), set(task.requested_capabilities))
        except (TypeError, ValueError):
            raise OrchestrationError("task_evidence_corrupt") from None
        return TaskPlan(task.id, revision, computed, plan["state"], subtasks)

    def _require_unexpired(self, task: OrchestrationTask) -> None:
        try:
            if datetime.fromisoformat(task.expires_at) <= datetime.now(timezone.utc):
                raise OrchestrationError("task_expired")
        except ValueError:
            raise OrchestrationError("task_evidence_corrupt") from None

    def _check_repository_snapshot(self, actor_id: str, task: OrchestrationTask) -> None:
        if task.repository_id is None:
            return
        try:
            self.policy.require(actor_id, task.workspace_id, task.repository_id,
                                CodingCapability.READ_REPOSITORY_METADATA)
            repository = self.repositories._repository(actor_id, task.workspace_id,
                                                      task.repository_id)
            self.repositories._ensure_current(actor_id, task.workspace_id, repository)
        except (RepositoryError, CodingAuthorizationDenied):
            raise OrchestrationError("task_repository_unavailable") from None
        if repository.head_commit != task.repository_commit:
            raise StaleRepository()

    def propose_plan(self, actor_id: str, task_id: str, planner: TaskPlanner, *,
                     idempotency_key: str) -> TaskPlan:
        actor_id = _nonempty(actor_id, "actor ID")
        idempotency_key = _idempotency(idempotency_key)
        task = self._require_task_access(actor_id, task_id, write=True)
        duplicate = self._existing_event(task.workspace_id, actor_id,
                                         idempotency_key, "plan_proposed", task.id)
        if duplicate is not None:
            return self.get_plan(actor_id, task.id, int(duplicate["revision"]))
        if type(planner) is not DeterministicFakePlanner:
            raise OrchestrationError("model_planning_disabled")
        self._require_unexpired(task)
        if task.archived_at is not None:
            raise OrchestrationError("task_archived")
        if task.state not in {TaskState.DRAFT, TaskState.AWAITING_APPROVAL,
                              TaskState.BLOCKED, TaskState.QUEUED}:
            raise OrchestrationError("task_plan_not_editable")
        if task.state in {TaskState.QUEUED, TaskState.BLOCKED} and task.execution_mode == "simulated":
            raise OrchestrationError("task_plan_locked_after_simulation")
        self._check_repository_snapshot(actor_id, task)
        request = TaskPlanningRequest(
            task.id, task.workspace_id, task.repository_id, task.repository_commit,
            task.goal, task.requested_capabilities,
            tuple(task.capability_manifest.get("granted", [])),
        )
        proposal = planner.propose_plan(request)
        subtasks = _validate_plan(proposal, set(task.requested_capabilities))
        plan_hash = _digest(_canonical_json(_plan_payload(subtasks)))
        revision = task.current_plan_revision + 1
        now = _now()
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                Database._require_access(connection, actor_id, task.workspace_id, Permission.WRITE)
                current = connection.execute("SELECT state,execution_mode,current_plan_revision,archived_at "
                                             "FROM orchestration_tasks WHERE id=? AND principal_id=?",
                                             (task.id, task.principal_id)).fetchone()
                if current is not None and current["archived_at"] is not None:
                    raise OrchestrationError("task_archived")
                if (current is None or current["current_plan_revision"] != task.current_plan_revision
                        or current["state"] not in {"draft", "awaiting_approval", "blocked", "queued"}
                        or (current["execution_mode"] == "simulated")):
                    raise OrchestrationError("task_changed")
                if task.current_plan_revision:
                    connection.execute(
                        "UPDATE orchestration_plans SET state='superseded' "
                        "WHERE task_id=? AND revision=? AND state IN ('awaiting_approval','approved')",
                        (task.id, task.current_plan_revision),
                    )
                connection.execute(
                    "INSERT INTO orchestration_plans(task_id,workspace_id,principal_id,revision,"
                    "plan_sha256,state,created_at) VALUES(?,?,?,?,?,'awaiting_approval',?)",
                    (task.id, task.workspace_id, task.principal_id, revision, plan_hash, now),
                )
                for ordinal, subtask in enumerate(subtasks):
                    connection.execute(
                        "INSERT INTO orchestration_subtasks(task_id,workspace_id,principal_id,revision,"
                        "subtask_id,ordinal,title,goal,acceptance_condition,required_capabilities_json,"
                        "state,progress_percent,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,'pending',0,?)",
                        (task.id, task.workspace_id, task.principal_id, revision,
                         subtask.id, ordinal, subtask.title, subtask.goal,
                         subtask.acceptance_condition,
                         _canonical_json(list(subtask.required_capabilities)), now),
                    )
                    for dependency in subtask.dependencies:
                        connection.execute(
                            "INSERT INTO orchestration_dependencies(task_id,workspace_id,principal_id,"
                            "revision,subtask_id,depends_on_subtask_id) VALUES(?,?,?,?,?,?)",
                            (task.id, task.workspace_id, task.principal_id, revision,
                             subtask.id, dependency),
                        )
                cursor = connection.execute(
                    "UPDATE orchestration_tasks SET state='awaiting_approval',execution_mode='planned',"
                    "current_plan_revision=?,approved_plan_revision=NULL,result_summary=NULL,updated_at=? "
                    "WHERE id=? AND principal_id=? AND archived_at IS NULL",
                    (revision, now, task.id, task.principal_id),
                )
                if cursor.rowcount != 1:
                    raise OrchestrationError("task_changed")
                updated = self._task(connection.execute(
                    "SELECT * FROM orchestration_tasks WHERE id=?", (task.id,)
                ).fetchone())
                self._insert_event(connection, updated, actor_id, "plan_proposed", idempotency_key,
                                   revision=revision, snapshot_sha256=plan_hash,
                                   metadata={"revision": str(revision),
                                             "subtask_count": str(len(subtasks))})
                self.db._commit(connection)
            except Exception:
                self.db._rollback(connection)
                raise
        return self.get_plan(actor_id, task.id, revision)

    def list_plans(self, actor_id: str, task_id: str) -> tuple[TaskPlan, ...]:
        task = self._require_task_access(actor_id, task_id)
        with self.db._connection() as connection:
            rows = connection.execute(
                "SELECT revision FROM orchestration_plans WHERE task_id=? ORDER BY revision",
                (task.id,),
            ).fetchall()
        return tuple(self.get_plan(actor_id, task.id, row["revision"]) for row in rows)

    def get_progress(self, actor_id: str, task_id: str,
                     revision: int | None = None) -> tuple[SubtaskProgress, ...]:
        task = self._require_task_access(actor_id, task_id)
        revision = task.current_plan_revision if revision is None else revision
        if type(revision) is not int or revision <= 0:
            raise ValueError("plan revision is invalid")
        self.get_plan(actor_id, task.id, revision)
        with self.db._connection() as connection:
            rows = connection.execute(
                "SELECT subtask_id,state,progress_percent,result_summary "
                "FROM orchestration_subtasks WHERE task_id=? AND revision=? ORDER BY ordinal",
                (task.id, revision),
            ).fetchall()
        try:
            progress: list[SubtaskProgress] = []
            for row in rows:
                summary = row["result_summary"]
                if summary is not None:
                    _bounded_text(summary, "stored simulation summary", MAX_RESULT_SUMMARY_CHARS)
                if type(row["progress_percent"]) is not int or not 0 <= row["progress_percent"] <= 100:
                    raise ValueError
                progress.append(SubtaskProgress(row["subtask_id"], SubtaskState(row["state"]),
                                                row["progress_percent"], summary))
            return tuple(progress)
        except (ValueError, TypeError):
            raise OrchestrationError("task_evidence_corrupt") from None

    def _required_capabilities(self, plan: TaskPlan) -> set[str]:
        return {capability for item in plan.subtasks for capability in item.required_capabilities}

    def _authorize_plan(self, actor_id: str, task: OrchestrationTask, plan: TaskPlan) -> list[str]:
        if task.repository_id is None:
            return []
        missing: list[str] = []
        try:
            self._check_repository_snapshot(actor_id, task)
        except StaleRepository:
            missing.append("repository_snapshot_changed")
        except OrchestrationError:
            missing.append("repository_snapshot_unavailable")
        for capability in sorted(self._required_capabilities(plan)):
            if not self.policy.is_granted(actor_id, task.workspace_id,
                                          task.repository_id, capability):
                missing.append(capability)
        return missing

    def approve_plan(self, actor_id: str, task_id: str, revision: int,
                     expected_plan_sha256: str, *, idempotency_key: str,
                     decision_id: str | None = None) -> OrchestrationTask:
        return self._decide_plan(actor_id, task_id, revision, expected_plan_sha256,
                                 "approve_plan", idempotency_key, decision_id)

    def reject_plan(self, actor_id: str, task_id: str, revision: int,
                    expected_plan_sha256: str, *, idempotency_key: str,
                    decision_id: str | None = None) -> OrchestrationTask:
        return self._decide_plan(actor_id, task_id, revision, expected_plan_sha256,
                                 "reject_plan", idempotency_key, decision_id)

    def _decide_plan(self, actor_id: str, task_id: str, revision: int,
                     expected_hash: str, action: str, idempotency_key: str,
                     decision_id: str | None) -> OrchestrationTask:
        actor_id = _nonempty(actor_id, "actor ID")
        idempotency_key = _idempotency(idempotency_key)
        if type(revision) is not int or revision <= 0 or not _SHA256.fullmatch(expected_hash or ""):
            raise ValueError("plan revision or digest is invalid")
        task = self._require_task_access(actor_id, task_id, write=True, manage=True)
        duplicate = self._existing_event(task.workspace_id, actor_id,
                                         idempotency_key, "plan_approved" if action == "approve_plan"
                                         else "plan_rejected", task.id)
        if duplicate is not None:
            return self.get_task(actor_id, task.id)
        self._require_unexpired(task)
        if task.archived_at is not None:
            raise OrchestrationError("task_archived")
        if revision != task.current_plan_revision or task.state not in {
                TaskState.AWAITING_APPROVAL, TaskState.BLOCKED}:
            raise OrchestrationError("plan_not_reviewable")
        plan = self.get_plan(actor_id, task.id, revision)
        if plan.plan_sha256 != expected_hash or plan.state != "awaiting_approval":
            raise OrchestrationError("plan_snapshot_mismatch")
        missing: list[str] = []
        if action == "approve_plan":
            missing = self._authorize_plan(actor_id, task, plan)
            if missing:
                with self.db._connection() as connection:
                    self.db._begin(connection)
                    try:
                        connection.execute(
                            "UPDATE orchestration_tasks SET state='blocked',updated_at=? "
                            "WHERE id=? AND current_plan_revision=? AND archived_at IS NULL "
                            "AND state IN ('awaiting_approval','blocked')",
                            (_now(), task.id, revision),
                        )
                        updated = self._task(connection.execute(
                            "SELECT * FROM orchestration_tasks WHERE id=?", (task.id,)
                        ).fetchone())
                        self._insert_event(connection, updated, actor_id, "task_blocked",
                                           idempotency_key, revision=revision,
                                           snapshot_sha256=expected_hash,
                                           metadata={"reason": "missing_capabilities",
                                                     "capabilities": ",".join(missing)[:200]})
                        self.db._commit(connection)
                    except Exception:
                        self.db._rollback(connection)
                        raise
                return self.get_task(actor_id, task.id)
        if decision_id is None:
            decision_id = secrets.token_urlsafe(24)
        if not isinstance(decision_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", decision_id):
            raise ValueError("approval receipt ID is invalid")
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                Database._require_access(connection, actor_id, task.workspace_id, Permission.WRITE)
                Database._require_access(connection, actor_id, task.workspace_id, Permission.MANAGE_WORKSPACE)
                if action == "approve_plan":
                    connection.execute(
                        "INSERT INTO orchestration_approvals(decision_id,task_id,workspace_id,"
                        "principal_id,revision,action,plan_sha256,idempotency_key,decided_by,decided_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (decision_id, task.id, task.workspace_id, task.principal_id, revision,
                         action, expected_hash, idempotency_key, actor_id, _now()),
                    )
                    connection.execute(
                        "UPDATE orchestration_plans SET state='approved',reviewed_by=?,reviewed_at=? "
                        "WHERE task_id=? AND revision=? AND state='awaiting_approval'",
                        (actor_id, _now(), task.id, revision),
                    )
                    new_state = TaskState.QUEUED.value
                    event_type = "plan_approved"
                else:
                    connection.execute(
                        "INSERT INTO orchestration_approvals(decision_id,task_id,workspace_id,"
                        "principal_id,revision,action,plan_sha256,idempotency_key,decided_by,decided_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (decision_id, task.id, task.workspace_id, task.principal_id, revision,
                         action, expected_hash, idempotency_key, actor_id, _now()),
                    )
                    connection.execute(
                        "UPDATE orchestration_plans SET state='rejected',reviewed_by=?,reviewed_at=? "
                        "WHERE task_id=? AND revision=? AND state='awaiting_approval'",
                        (actor_id, _now(), task.id, revision),
                    )
                    new_state = TaskState.DRAFT.value
                    event_type = "plan_rejected"
                cursor = connection.execute(
                    "UPDATE orchestration_tasks SET state=?,execution_mode='planned',"
                    "approved_plan_revision=?,updated_at=? WHERE id=? AND archived_at IS NULL AND state IN "
                    "('awaiting_approval','blocked') AND current_plan_revision=?",
                    (new_state, revision if action == "approve_plan" else None,
                     _now(), task.id, revision),
                )
                if cursor.rowcount != 1:
                    raise OrchestrationError("task_changed")
                updated = self._task(connection.execute(
                    "SELECT * FROM orchestration_tasks WHERE id=?", (task.id,)
                ).fetchone())
                self._insert_event(connection, updated, actor_id, event_type, idempotency_key,
                                   revision=revision, snapshot_sha256=expected_hash,
                                   metadata={"decision_id": decision_id})
                self.db._commit(connection)
            except sqlite3.IntegrityError:
                self.db._rollback(connection)
                raise OrchestrationError("approval_replayed_or_conflict") from None
            except Exception:
                self.db._rollback(connection)
                raise
        return self.get_task(actor_id, task.id)

    def _authorized_plan(self, actor_id: str, task: OrchestrationTask) -> TaskPlan:
        if (task.approved_plan_revision is None
                or task.approved_plan_revision != task.current_plan_revision):
            raise OrchestrationError("approved_plan_unavailable")
        plan = self.get_plan(actor_id, task.id, task.approved_plan_revision)
        if plan.state != "approved":
            raise OrchestrationError("approved_plan_unavailable")
        return plan

    def blocked_capabilities(self, actor_id: str, task_id: str) -> tuple[str, ...]:
        task = self._require_task_access(actor_id, task_id)
        if task.current_plan_revision <= 0:
            return ()
        plan = self.get_plan(actor_id, task.id, task.current_plan_revision)
        return tuple(self._authorize_plan(actor_id, task, plan))

    def _mark_capability_blocked(self, actor_id: str, task: OrchestrationTask,
                                 plan: TaskPlan, idempotency_key: str,
                                 missing: list[str]) -> OrchestrationTask:
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                Database._require_access(connection, actor_id, task.workspace_id, Permission.WRITE)
                cursor = connection.execute(
                    "UPDATE orchestration_tasks SET state='blocked',updated_at=? "
                    "WHERE id=? AND current_plan_revision=? AND approved_plan_revision=? "
                    "AND state IN ('queued','simulating','blocked')",
                    (_now(), task.id, plan.revision, plan.revision),
                )
                if cursor.rowcount:
                    current = self._task(connection.execute(
                        "SELECT * FROM orchestration_tasks WHERE id=?", (task.id,)
                    ).fetchone())
                    self._insert_event(connection, current, actor_id, "task_blocked",
                                       idempotency_key, revision=plan.revision,
                                       snapshot_sha256=plan.plan_sha256,
                                       metadata={"reason": "capability_revoked",
                                                 "capabilities": ",".join(missing)[:200]})
                self.db._commit(connection)
            except sqlite3.IntegrityError:
                self.db._rollback(connection)
                return self.get_task(actor_id, task.id)
            except Exception:
                self.db._rollback(connection)
                raise
        return self.get_task(actor_id, task.id)

    def simulate_next_step(self, actor_id: str, task_id: str, worker: TaskWorker, *,
                           idempotency_key: str) -> OrchestrationTask:
        actor_id = _nonempty(actor_id, "actor ID")
        idempotency_key = _idempotency(idempotency_key)
        task = self._require_task_access(actor_id, task_id, write=True)
        if type(worker) is not DeterministicFakeWorker:
            raise OrchestrationError("real_worker_execution_disabled")
        duplicate = self._existing_event(task.workspace_id, actor_id,
                                         idempotency_key, "simulation_progress", task.id)
        if duplicate is not None:
            return self.get_task(actor_id, task.id)
        blocked_duplicate = self._existing_event(task.workspace_id, actor_id,
                                                 idempotency_key, "task_blocked", task.id)
        if blocked_duplicate is not None:
            return self.get_task(actor_id, task.id)
        self._require_unexpired(task)
        if task.archived_at is not None:
            raise OrchestrationError("task_archived")
        if task.state not in {TaskState.QUEUED, TaskState.SIMULATING}:
            raise OrchestrationError("task_not_simulatable")
        plan = self._authorized_plan(actor_id, task)
        missing = self._authorize_plan(actor_id, task, plan)
        if missing:
            return self._mark_capability_blocked(actor_id, task, plan, idempotency_key, missing)
        with self.db._connection() as connection:
            rows = connection.execute(
                "SELECT subtask_id,state,progress_percent FROM orchestration_subtasks "
                "WHERE task_id=? AND revision=? ORDER BY ordinal",
                (task.id, plan.revision),
            ).fetchall()
            dependencies = {row["subtask_id"]: {item[0] for item in connection.execute(
                "SELECT depends_on_subtask_id FROM orchestration_dependencies "
                "WHERE task_id=? AND revision=? AND subtask_id=?",
                (task.id, plan.revision, row["subtask_id"]),
            ).fetchall()} for row in rows}
        state_by_id = {row["subtask_id"]: row["state"] for row in rows}
        candidate = next((row for row in rows
                          if row["state"] in {"pending", "simulating"}
                          and dependencies[row["subtask_id"]] <= {
                              key for key, state in state_by_id.items()
                              if state == SubtaskState.SIMULATED_COMPLETED.value}), None)
        if candidate is None:
            if rows and all(row["state"] == SubtaskState.SIMULATED_COMPLETED.value for row in rows):
                return self._complete_simulation(actor_id, task, plan,
                                                 idempotency_key)
            raise OrchestrationError("no_simulatable_subtask")
        subtask = next(item for item in plan.subtasks if item.id == candidate["subtask_id"])
        step = worker.simulate_step(subtask, candidate["progress_percent"])
        if (type(step) is not SimulationStep or type(step.progress_percent) is not int
                or not candidate["progress_percent"] < step.progress_percent <= 100
                or type(step.completed) is not bool
                or step.completed != (step.progress_percent == 100)):
            raise OrchestrationError("fake_worker_result_invalid")
        summary = _bounded_text(step.summary, "simulation summary", MAX_RESULT_SUMMARY_CHARS)
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                task_state = connection.execute(
                    "SELECT state,approved_plan_revision,archived_at FROM orchestration_tasks WHERE id=?",
                    (task.id,),
                ).fetchone()
                if task_state is not None and task_state["archived_at"] is not None:
                    raise OrchestrationError("task_archived")
                if (task_state is None or task_state["state"] not in {"queued", "simulating"}
                        or task_state["approved_plan_revision"] != plan.revision):
                    raise OrchestrationError("task_changed")
                Database._require_access(connection, actor_id, task.workspace_id, Permission.WRITE)
                cursor = connection.execute(
                    "UPDATE orchestration_subtasks SET state=?,progress_percent=?,result_summary=?,updated_at=? "
                    "WHERE task_id=? AND revision=? AND subtask_id=? AND state IN ('pending','simulating') "
                    "AND progress_percent=?",
                    (SubtaskState.SIMULATED_COMPLETED.value if step.completed else SubtaskState.SIMULATING.value,
                     step.progress_percent, summary, _now(), task.id, plan.revision,
                     subtask.id, candidate["progress_percent"]),
                )
                if cursor.rowcount != 1:
                    raise OrchestrationError("task_progress_conflict")
                task_cursor = connection.execute(
                    "UPDATE orchestration_tasks SET state='simulating',execution_mode='simulated',updated_at=? "
                    "WHERE id=? AND current_plan_revision=? AND archived_at IS NULL AND state IN ('queued','simulating')",
                    (_now(), task.id, plan.revision),
                )
                if task_cursor.rowcount != 1:
                    raise OrchestrationError("task_changed")
                current = self._task(connection.execute(
                    "SELECT * FROM orchestration_tasks WHERE id=?", (task.id,)
                ).fetchone())
                self._insert_event(connection, current, actor_id, "simulation_progress",
                                   idempotency_key, revision=plan.revision,
                                   snapshot_sha256=plan.plan_sha256,
                                   metadata={"subtask_id": subtask.id,
                                             "progress_percent": str(step.progress_percent),
                                             "simulated": "true"})
                self.db._commit(connection)
            except sqlite3.IntegrityError:
                self.db._rollback(connection)
                raise OrchestrationError("idempotency_key_conflict") from None
            except Exception:
                self.db._rollback(connection)
                raise
        return self.get_task(actor_id, task.id)

    def _complete_simulation(self, actor_id: str, task: OrchestrationTask,
                              plan: TaskPlan, idempotency_key: str) -> OrchestrationTask:
        duplicate = self._existing_event(task.workspace_id, actor_id,
                                         idempotency_key, "simulation_completed", task.id)
        if duplicate is not None:
            return self.get_task(actor_id, task.id)
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                current = connection.execute(
                    "SELECT state,approved_plan_revision FROM orchestration_tasks WHERE id=?",
                    (task.id,),
                ).fetchone()
                if (current is None or current["state"] != "simulating"
                        or current["approved_plan_revision"] != plan.revision):
                    raise OrchestrationError("task_changed")
                rows = connection.execute(
                    "SELECT COUNT(*) FROM orchestration_subtasks WHERE task_id=? AND revision=? "
                    "AND state<>'simulated_completed'", (task.id, plan.revision),
                ).fetchone()[0]
                if rows:
                    raise OrchestrationError("simulation_incomplete")
                summary = f"SIMULATION ONLY: all {len(plan.subtasks)} planned subtasks were simulated."
                cursor = connection.execute(
                    "UPDATE orchestration_tasks SET state='simulation_completed',"
                    "execution_mode='simulated',result_summary=?,updated_at=? "
                    "WHERE id=? AND state='simulating' AND current_plan_revision=?",
                    (summary, _now(), task.id, plan.revision),
                )
                if cursor.rowcount != 1:
                    raise OrchestrationError("task_changed")
                updated = self._task(connection.execute(
                    "SELECT * FROM orchestration_tasks WHERE id=?", (task.id,)
                ).fetchone())
                self._insert_event(connection, updated, actor_id, "simulation_completed",
                                   idempotency_key, revision=plan.revision,
                                   snapshot_sha256=plan.plan_sha256,
                                   metadata={"subtask_count": str(len(plan.subtasks)),
                                             "simulated": "true"})
                self.db._commit(connection)
            except Exception:
                self.db._rollback(connection)
                raise
        return self.get_task(actor_id, task.id)

    def cancel_task(self, actor_id: str, task_id: str, *,
                    idempotency_key: str) -> OrchestrationTask:
        actor_id = _nonempty(actor_id, "actor ID")
        idempotency_key = _idempotency(idempotency_key)
        task = self._require_task_access(actor_id, task_id, write=True)
        duplicate = self._existing_event(task.workspace_id, actor_id,
                                         idempotency_key, "task_cancelled", task.id)
        if duplicate is not None:
            return self.get_task(actor_id, task.id)
        if task.archived_at is not None:
            raise OrchestrationError("task_archived")
        if task.state in {TaskState.COMPLETED, TaskState.FAILED,
                          TaskState.SIMULATION_COMPLETED, TaskState.CANCELLED}:
            raise OrchestrationError("task_not_cancellable")
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                current = connection.execute(
                    "SELECT state,current_plan_revision,capability_manifest_sha256,archived_at "
                    "FROM orchestration_tasks WHERE id=? AND principal_id=?",
                    (task.id, task.principal_id),
                ).fetchone()
                if current is None:
                    raise OrchestrationError("task_unavailable")
                if current["archived_at"] is not None:
                    raise OrchestrationError("task_archived")
                current_state = TaskState(current["state"])
                if current_state in {TaskState.COMPLETED, TaskState.FAILED,
                                     TaskState.SIMULATION_COMPLETED, TaskState.CANCELLED}:
                    raise OrchestrationError("task_not_cancellable")
                Database._require_access(connection, actor_id, task.workspace_id, Permission.WRITE)
                cursor = connection.execute(
                    "UPDATE orchestration_tasks SET state='cancelled',updated_at=? "
                    "WHERE id=? AND principal_id=? AND archived_at IS NULL AND state=?",
                    (_now(), task.id, task.principal_id, current_state.value),
                )
                if cursor.rowcount != 1:
                    raise OrchestrationError("task_changed")
                connection.execute(
                    "UPDATE orchestration_subtasks SET state='cancelled',updated_at=? "
                    "WHERE task_id=? AND revision=? AND state IN ('pending','simulating','blocked')",
                    (_now(), task.id, current["current_plan_revision"]),
                )
                updated = self._task(connection.execute(
                    "SELECT * FROM orchestration_tasks WHERE id=?", (task.id,)
                ).fetchone())
                self._insert_event(connection, updated, actor_id, "task_cancelled",
                                   idempotency_key, revision=current["current_plan_revision"] or None,
                                   snapshot_sha256=current["capability_manifest_sha256"],
                                   metadata={"previous_state": current_state.value,
                                             "simulated_only": "true"})
                self.db._commit(connection)
            except Exception:
                self.db._rollback(connection)
                raise
        return self.get_task(actor_id, task.id)

    def recover_after_restart(self, actor_id: str, workspace_id: str, *,
                              idempotency_key_prefix: str) -> tuple[str, ...]:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        prefix = _idempotency(idempotency_key_prefix)
        if not self.db.check_access(actor_id, workspace_id, Permission.MANAGE_WORKSPACE):
            raise OrchestrationError("task_manager_approval_required")
        tasks = self.list_tasks(actor_id, workspace_id, include_history=False)
        interrupted: list[str] = []
        for task in tasks:
            if task.state not in {TaskState.QUEUED, TaskState.SIMULATING}:
                continue
            key = f"{prefix[:80]}_{task.id[:24]}"
            key = key[:128]
            with self.db._connection() as connection:
                self.db._begin(connection)
                try:
                    cursor = connection.execute(
                        "UPDATE orchestration_tasks SET state='interrupted',updated_at=? "
                        "WHERE id=? AND archived_at IS NULL AND state IN ('queued','simulating')",
                        (_now(), task.id),
                    )
                    if cursor.rowcount == 1:
                        Database._require_access(connection, actor_id, workspace_id, Permission.WRITE)
                        updated = self._task(connection.execute(
                            "SELECT * FROM orchestration_tasks WHERE id=?", (task.id,)
                        ).fetchone())
                        self._insert_event(connection, updated, actor_id, "task_interrupted", key,
                                           revision=task.approved_plan_revision,
                                           snapshot_sha256=task.capability_manifest_sha256,
                                           metadata={"previous_state": task.state.value,
                                                     "auto_resume": "false"})
                        interrupted.append(task.id)
                    self.db._commit(connection)
                except Exception:
                    self.db._rollback(connection)
                    raise
        return tuple(interrupted)

    def resume_simulation(self, actor_id: str, task_id: str, *,
                          idempotency_key: str) -> OrchestrationTask:
        actor_id = _nonempty(actor_id, "actor ID")
        idempotency_key = _idempotency(idempotency_key)
        task = self._require_task_access(actor_id, task_id, write=True, manage=True)
        duplicate = self._existing_event(task.workspace_id, actor_id,
                                         idempotency_key, "simulation_resumed", task.id)
        if duplicate is not None:
            return self.get_task(actor_id, task.id)
        if task.archived_at is not None:
            raise OrchestrationError("task_archived")
        if task.state not in {TaskState.INTERRUPTED, TaskState.BLOCKED}:
            raise OrchestrationError("task_not_interrupted")
        self._require_unexpired(task)
        plan = self._authorized_plan(actor_id, task)
        missing = self._authorize_plan(actor_id, task, plan)
        if missing:
            raise OrchestrationError("task_capability_revoked")
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                Database._require_access(connection, actor_id, task.workspace_id, Permission.WRITE)
                Database._require_access(connection, actor_id, task.workspace_id, Permission.MANAGE_WORKSPACE)
                current = connection.execute(
                    "SELECT state,archived_at,approved_plan_revision FROM orchestration_tasks WHERE id=?",
                    (task.id,),
                ).fetchone()
                if current is None:
                    raise OrchestrationError("task_unavailable")
                if current["archived_at"] is not None:
                    raise OrchestrationError("task_archived")
                if (current["state"] not in {"interrupted", "blocked"}
                        or current["approved_plan_revision"] != plan.revision):
                    raise OrchestrationError("task_changed")
                progressed = connection.execute(
                    "SELECT 1 FROM orchestration_subtasks WHERE task_id=? AND revision=? "
                    "AND progress_percent>0 LIMIT 1", (task.id, plan.revision),
                ).fetchone() is not None
                state = "simulating" if progressed else "queued"
                mode = "simulated" if progressed else "planned"
                cursor = connection.execute(
                    "UPDATE orchestration_tasks SET state=?,execution_mode=?,updated_at=? "
                    "WHERE id=? AND archived_at IS NULL AND state IN ('interrupted','blocked') "
                    "AND approved_plan_revision=?",
                    (state, mode, _now(), task.id, plan.revision),
                )
                if cursor.rowcount != 1:
                    raise OrchestrationError("task_changed")
                updated = self._task(connection.execute(
                    "SELECT * FROM orchestration_tasks WHERE id=?", (task.id,)
                ).fetchone())
                self._insert_event(connection, updated, actor_id, "simulation_resumed",
                                   idempotency_key, revision=plan.revision,
                                   snapshot_sha256=plan.plan_sha256,
                                   metadata={"explicit": "true", "simulated_only": "true"})
                self.db._commit(connection)
            except Exception:
                self.db._rollback(connection)
                raise
        return self.get_task(actor_id, task.id)

    def events(self, actor_id: str, task_id: str, *, limit: int = 250) -> tuple[dict[str, object], ...]:
        task = self._require_task_access(actor_id, task_id)
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("event limit must be between 1 and 500")
        with self.db._connection() as connection:
            rows = connection.execute(
                "SELECT id,event_type,plan_revision,snapshot_sha256,idempotency_key,actor_id,metadata_json,created_at "
                "FROM orchestration_task_events WHERE task_id=? AND workspace_id=? "
                "ORDER BY id LIMIT ?", (task.id, task.workspace_id, limit),
            ).fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"])
                if (not isinstance(metadata, dict) or len(metadata) > 12
                        or len(row["metadata_json"].encode("utf-8")) > 2048
                        or any(not isinstance(key, str) or len(key) > 40
                               or not isinstance(value, str) or len(value) > 200
                               for key, value in metadata.items())
                        or row["event_type"] not in _SAFE_EVENT_TYPES
                        or not _IDEMPOTENCY.fullmatch(row["idempotency_key"])
                        or (row["snapshot_sha256"] is not None
                            and not _SHA256.fullmatch(row["snapshot_sha256"]))):
                    raise ValueError
            except (ValueError, TypeError):
                raise OrchestrationError("task_evidence_corrupt") from None
            result.append({"id": row["id"], "event_type": row["event_type"],
                           "plan_revision": row["plan_revision"],
                           "snapshot_sha256": row["snapshot_sha256"],
                           "idempotency_key": row["idempotency_key"],
                           "actor_id": row["actor_id"],
                           "metadata": metadata, "created_at": row["created_at"]})
        return tuple(result)

    @staticmethod
    def _has_permission(connection: sqlite3.Connection, actor_id: str,
                        workspace_id: str, permission: Permission) -> bool:
        try:
            Database._require_access(connection, actor_id, workspace_id, permission)
            return True
        except AccessDenied:
            return False

    @staticmethod
    def _require_workspace_manager(connection: sqlite3.Connection, actor_id: str,
                                   workspace_id: str, *, write: bool = False) -> None:
        Database._require_access(connection, actor_id, workspace_id, Permission.READ)
        if write:
            Database._require_access(connection, actor_id, workspace_id, Permission.WRITE)
        try:
            Database._require_access(connection, actor_id, workspace_id, Permission.MANAGE_WORKSPACE)
        except AccessDenied:
            raise OrchestrationError("task_manager_approval_required") from None

    @staticmethod
    def _has_pending_approval(connection: sqlite3.Connection, task_id: str, state: str) -> bool:
        if state == TaskState.AWAITING_APPROVAL.value:
            return True
        if state == TaskState.CANCELLED.value:
            return False
        return connection.execute(
            "SELECT 1 FROM orchestration_plans WHERE task_id=? AND state='awaiting_approval' LIMIT 1",
            (task_id,),
        ).fetchone() is not None

    @staticmethod
    def _policy_from_row(workspace_id: str, row, effective_now: datetime) -> TaskRetentionPolicy:
        if row is None:
            return TaskRetentionPolicy(workspace_id=workspace_id, auto_delete_enabled=False,
                                       is_expired=False)
        try:
            if row["auto_delete_enabled"] != 0:
                raise ValueError
            completed = _validate_retention_days(row["completed_retention_days"], "completed_retention_days")
            cancelled = _validate_retention_days(row["cancelled_retention_days"], "cancelled_retention_days")
            failed = _validate_retention_days(row["failed_retention_days"], "failed_retention_days")
            interrupted = _validate_retention_days(row["interrupted_retention_days"], "interrupted_retention_days")
            updated_by = _nonempty(row["updated_by"], "updated_by")
            _parse_utc(row["updated_at"], "updated_at")
            expires_at = row["expires_at"]
            is_expired = False
            if expires_at is not None:
                is_expired = _parse_utc(expires_at, "expires_at") <= effective_now
        except (ValueError, TypeError):
            raise OrchestrationError("task_evidence_corrupt") from None
        return TaskRetentionPolicy(
            workspace_id=workspace_id,
            completed_retention_days=completed,
            cancelled_retention_days=cancelled,
            failed_retention_days=failed,
            interrupted_retention_days=interrupted,
            auto_delete_enabled=False,
            updated_by=updated_by,
            updated_at=row["updated_at"],
            expires_at=expires_at,
            is_expired=is_expired,
        )

    def get_retention_policy(self, actor_id: str, workspace_id: str, *,
                             as_of: str | None = None) -> TaskRetentionPolicy:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        effective_now = _parse_utc(as_of, "as_of") if as_of is not None else datetime.now(timezone.utc)
        self.db.require_access(actor_id, workspace_id, Permission.READ)
        with self.db._connection() as connection:
            Database._require_access(connection, actor_id, workspace_id, Permission.READ)
            row = connection.execute(
                "SELECT * FROM orchestration_retention_policies WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
        return self._policy_from_row(workspace_id, row, effective_now)

    def set_retention_policy(self, actor_id: str, workspace_id: str, *,
                             completed_retention_days: int | None = None,
                             cancelled_retention_days: int | None = None,
                             failed_retention_days: int | None = None,
                             interrupted_retention_days: int | None = None,
                             policy_lifetime_days: int | None = None) -> TaskRetentionPolicy:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        completed = _validate_retention_days(completed_retention_days, "completed_retention_days")
        cancelled = _validate_retention_days(cancelled_retention_days, "cancelled_retention_days")
        failed = _validate_retention_days(failed_retention_days, "failed_retention_days")
        interrupted = _validate_retention_days(interrupted_retention_days, "interrupted_retention_days")
        if policy_lifetime_days is not None:
            if (type(policy_lifetime_days) is not int
                    or not 1 <= policy_lifetime_days <= MAX_POLICY_LIFETIME_DAYS):
                raise ValueError(
                    f"policy_lifetime_days must be None or between 1 and {MAX_POLICY_LIFETIME_DAYS}"
                )
        now_dt = datetime.now(timezone.utc)
        updated_at = now_dt.isoformat(timespec="microseconds")
        expires_at = (
            (now_dt + timedelta(days=policy_lifetime_days)).isoformat(timespec="microseconds")
            if policy_lifetime_days is not None else None
        )
        self.db.initialize()
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                self._require_workspace_manager(connection, actor_id, workspace_id, write=True)
                connection.execute(
                    "INSERT INTO orchestration_retention_policies("
                    "workspace_id,completed_retention_days,cancelled_retention_days,"
                    "failed_retention_days,interrupted_retention_days,auto_delete_enabled,"
                    "updated_by,updated_at,expires_at) "
                    "VALUES(?,?,?,?,?,0,?,?,?) "
                    "ON CONFLICT(workspace_id) DO UPDATE SET "
                    "completed_retention_days=excluded.completed_retention_days,"
                    "cancelled_retention_days=excluded.cancelled_retention_days,"
                    "failed_retention_days=excluded.failed_retention_days,"
                    "interrupted_retention_days=excluded.interrupted_retention_days,"
                    "auto_delete_enabled=0,"
                    "updated_by=excluded.updated_by,"
                    "updated_at=excluded.updated_at,"
                    "expires_at=excluded.expires_at",
                    (workspace_id, completed, cancelled, failed, interrupted,
                     actor_id, updated_at, expires_at),
                )
                Database._insert_audit(
                    connection, workspace_id, actor_id,
                    "orchestration.retention_policy_updated",
                    "orchestration_retention_policy", workspace_id,
                    {
                        "completed_days": str(completed if completed is not None else "none"),
                        "cancelled_days": str(cancelled if cancelled is not None else "none"),
                        "failed_days": str(failed if failed is not None else "none"),
                        "interrupted_days": str(interrupted if interrupted is not None else "none"),
                        "expires_at": expires_at or "none",
                        "auto_delete_enabled": "false",
                    },
                )
                row = connection.execute(
                    "SELECT * FROM orchestration_retention_policies WHERE workspace_id=?",
                    (workspace_id,),
                ).fetchone()
                self.db._commit(connection)
            except Exception:
                self.db._rollback(connection)
                raise
        return self._policy_from_row(workspace_id, row, now_dt)

    def list_task_history(self, actor_id: str, workspace_id: str, *,
                          states: tuple[TaskState | str, ...] | None = None,
                          include_archived: bool = True,
                          archived_only: bool = False,
                          older_than: str | None = None,
                          limit: int = 50,
                          offset: int = 0) -> TaskHistoryPage:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("history limit must be between 1 and 100")
        if type(offset) is not int or not 0 <= offset <= MAX_HISTORY_OFFSET:
            raise ValueError(f"history offset must be between 0 and {MAX_HISTORY_OFFSET}")
        if type(include_archived) is not bool or type(archived_only) is not bool:
            raise ValueError("history archive filter flags must be booleans")
        if archived_only and not include_archived:
            raise ValueError("archived_only requires include_archived=True")
        normalized_states: tuple[str, ...] = ()
        if states is not None:
            if not isinstance(states, tuple) or not 1 <= len(states) <= len(TaskState):
                raise ValueError("states filter must be a non-empty tuple of task states")
            normalized_states = tuple(sorted({TaskState(item).value for item in states}))
        older_cutoff = _parse_utc(older_than, "older_than") if older_than is not None else None
        self.db.initialize()
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                Database._require_access(connection, actor_id, workspace_id, Permission.READ)
                manager = self._has_permission(connection, actor_id, workspace_id,
                                               Permission.MANAGE_WORKSPACE)
                where = ["workspace_id=?"]
                params: list[object] = [workspace_id]
                if not manager:
                    where.append("principal_id=?")
                    params.append(actor_id)
                if archived_only:
                    where.append("archived_at IS NOT NULL")
                elif not include_archived:
                    where.append("archived_at IS NULL")
                if normalized_states:
                    placeholders = ",".join("?" for _ in normalized_states)
                    where.append(f"state IN ({placeholders})")
                    params.extend(normalized_states)
                if older_cutoff is not None:
                    where.append("updated_at<=?")
                    params.append(older_cutoff.isoformat(timespec="microseconds"))
                clause = " AND ".join(where)
                total_count = int(connection.execute(
                    f"SELECT COUNT(*) FROM orchestration_tasks WHERE {clause}", params
                ).fetchone()[0])
                rows = connection.execute(
                    f"SELECT * FROM orchestration_tasks WHERE {clause} "
                    "ORDER BY updated_at DESC, id ASC LIMIT ? OFFSET ?",
                    [*params, limit, offset],
                ).fetchall()
            finally:
                self.db._rollback(connection)
        items = tuple(self._task(row) for row in rows)
        return TaskHistoryPage(
            items=items,
            total_count=total_count,
            limit=limit,
            offset=offset,
            has_more=(offset + len(items) < total_count),
        )

    def archive_task(self, actor_id: str, task_id: str, *,
                     idempotency_key: str) -> OrchestrationTask:
        actor_id = _nonempty(actor_id, "actor ID")
        idempotency_key = _idempotency(idempotency_key)
        task = self._require_task_access(actor_id, task_id, write=True)
        duplicate = self._existing_event(task.workspace_id, actor_id,
                                         idempotency_key, "task_archived", task.id)
        if duplicate is not None:
            return self.get_task(actor_id, task.id)
        if task.archived_at is not None:
            raise OrchestrationError("task_already_archived")
        if task.state not in _RETENTION_ELIGIBLE_STATES:
            raise OrchestrationError("task_not_archivable")
        archived_at = _now()
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                Database._require_access(connection, actor_id, task.workspace_id, Permission.WRITE)
                if actor_id != task.principal_id:
                    self._require_workspace_manager(connection, actor_id, task.workspace_id, write=True)
                current = connection.execute(
                    "SELECT state,archived_at,current_plan_revision,capability_manifest_sha256 "
                    "FROM orchestration_tasks WHERE id=? AND principal_id=?",
                    (task.id, task.principal_id),
                ).fetchone()
                if current is None:
                    raise OrchestrationError("task_unavailable")
                if current["archived_at"] is not None:
                    raise OrchestrationError("task_already_archived")
                if self._has_pending_approval(connection, task.id, current["state"]):
                    raise OrchestrationError("task_has_pending_approval")
                if TaskState(current["state"]) not in _RETENTION_ELIGIBLE_STATES:
                    raise OrchestrationError("task_not_archivable")
                cursor = connection.execute(
                    "UPDATE orchestration_tasks SET archived_at=?,archived_by=? "
                    "WHERE id=? AND principal_id=? AND archived_at IS NULL AND state=?",
                    (archived_at, actor_id, task.id, task.principal_id, current["state"]),
                )
                if cursor.rowcount != 1:
                    raise OrchestrationError("task_changed")
                updated = self._task(connection.execute(
                    "SELECT * FROM orchestration_tasks WHERE id=?", (task.id,)
                ).fetchone())
                self._insert_event(
                    connection, updated, actor_id, "task_archived", idempotency_key,
                    revision=current["current_plan_revision"] or None,
                    snapshot_sha256=current["capability_manifest_sha256"],
                    metadata={"previous_state": current["state"], "archived_at": archived_at},
                )
                self.db._commit(connection)
            except sqlite3.IntegrityError:
                self.db._rollback(connection)
                if self._existing_event(task.workspace_id, actor_id,
                                        idempotency_key, "task_archived", task.id) is not None:
                    return self.get_task(actor_id, task.id)
                raise OrchestrationError("idempotency_key_conflict") from None
            except Exception:
                self.db._rollback(connection)
                raise
        return self.get_task(actor_id, task.id)

    def restore_archived_task(self, actor_id: str, task_id: str, *,
                              idempotency_key: str) -> OrchestrationTask:
        actor_id = _nonempty(actor_id, "actor ID")
        idempotency_key = _idempotency(idempotency_key)
        task = self._require_task_access(actor_id, task_id, write=True)
        duplicate = self._existing_event(task.workspace_id, actor_id,
                                         idempotency_key, "task_restored", task.id)
        if duplicate is not None:
            return self.get_task(actor_id, task.id)
        if task.archived_at is None:
            raise OrchestrationError("task_not_archived")
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                Database._require_access(connection, actor_id, task.workspace_id, Permission.WRITE)
                if actor_id != task.principal_id:
                    self._require_workspace_manager(connection, actor_id, task.workspace_id, write=True)
                current = connection.execute(
                    "SELECT state,archived_at,current_plan_revision,capability_manifest_sha256 "
                    "FROM orchestration_tasks WHERE id=? AND principal_id=?",
                    (task.id, task.principal_id),
                ).fetchone()
                if current is None:
                    raise OrchestrationError("task_unavailable")
                if current["archived_at"] is None:
                    raise OrchestrationError("task_not_archived")
                cursor = connection.execute(
                    "UPDATE orchestration_tasks SET archived_at=NULL,archived_by=NULL "
                    "WHERE id=? AND principal_id=? AND archived_at IS NOT NULL",
                    (task.id, task.principal_id),
                )
                if cursor.rowcount != 1:
                    raise OrchestrationError("task_changed")
                updated = self._task(connection.execute(
                    "SELECT * FROM orchestration_tasks WHERE id=?", (task.id,)
                ).fetchone())
                self._insert_event(
                    connection, updated, actor_id, "task_restored", idempotency_key,
                    revision=current["current_plan_revision"] or None,
                    snapshot_sha256=current["capability_manifest_sha256"],
                    metadata={"restored_state": current["state"]},
                )
                self.db._commit(connection)
            except sqlite3.IntegrityError:
                self.db._rollback(connection)
                if self._existing_event(task.workspace_id, actor_id,
                                        idempotency_key, "task_restored", task.id) is not None:
                    return self.get_task(actor_id, task.id)
                raise OrchestrationError("idempotency_key_conflict") from None
            except Exception:
                self.db._rollback(connection)
                raise
        return self.get_task(actor_id, task.id)

    def _collect_purge_preview(self, connection: sqlite3.Connection, workspace_id: str, *,
                               task_ids: tuple[str, ...] | None,
                               use_retention_policy: bool,
                               archived_only: bool,
                               effective_now: datetime,
                               limit: int,
                               strict_targets: bool) -> PurgePreview:
        policy: TaskRetentionPolicy | None = None
        if use_retention_policy:
            policy_row = connection.execute(
                "SELECT * FROM orchestration_retention_policies WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
            policy = self._policy_from_row(workspace_id, policy_row, effective_now)
            if policy.is_expired:
                raise OrchestrationError("retention_policy_expired")

        selected_tasks: list[OrchestrationTask] = []
        truncated = False
        protected_skipped = 0

        if task_ids is not None:
            if not isinstance(task_ids, tuple) or not 1 <= len(task_ids) <= MAX_PURGE_BATCH:
                raise ValueError(f"task_ids must be a tuple of 1 to {MAX_PURGE_BATCH} task IDs")
            normalized_ids = tuple(sorted({_nonempty(item, "task ID") for item in task_ids}))
            if len(normalized_ids) != len(task_ids):
                raise ValueError("task_ids must be unique")
            for tid in normalized_ids:
                row = connection.execute(
                    "SELECT * FROM orchestration_tasks WHERE workspace_id=? AND id=?",
                    (workspace_id, tid),
                ).fetchone()
                if row is None:
                    if strict_targets:
                        tombstone = connection.execute(
                            "SELECT 1 FROM orchestration_purge_tombstones "
                            "WHERE workspace_id=? AND task_id=?",
                            (workspace_id, tid),
                        ).fetchone()
                        if tombstone is not None:
                            raise OrchestrationError("purge_preview_stale")
                    raise OrchestrationError("task_unavailable")
                task = self._task(row)
                if self._has_pending_approval(connection, task.id, task.state.value):
                    raise OrchestrationError("task_has_pending_approval")
                if task.state in _PROTECTED_TASK_STATES or task.state not in _RETENTION_ELIGIBLE_STATES:
                    raise OrchestrationError("task_protected_from_purge")
                if archived_only and task.archived_at is None:
                    raise OrchestrationError(
                        "purge_preview_stale" if strict_targets else "task_not_archived"
                    )
                if use_retention_policy:
                    assert policy is not None
                    retention_days = _retention_days_for_state(policy, task.state)
                    try:
                        updated_dt = _parse_utc(task.updated_at, "updated_at")
                    except ValueError:
                        raise OrchestrationError("task_evidence_corrupt") from None
                    if (retention_days is None
                            or updated_dt > effective_now - timedelta(days=retention_days)):
                        raise OrchestrationError(
                            "purge_preview_stale" if strict_targets else "task_within_retention_period"
                        )
                selected_tasks.append(task)
        else:
            assert policy is not None
            sql = "SELECT * FROM orchestration_tasks WHERE workspace_id=?"
            if archived_only:
                sql += " AND archived_at IS NOT NULL"
            sql += " ORDER BY updated_at ASC, id ASC"
            rows = connection.execute(sql, (workspace_id,)).fetchall()
            eligible: list[OrchestrationTask] = []
            for row in rows:
                task = self._task(row)
                if (task.state in _PROTECTED_TASK_STATES
                        or self._has_pending_approval(connection, task.id, task.state.value)):
                    protected_skipped += 1
                    continue
                if task.state not in _RETENTION_ELIGIBLE_STATES:
                    protected_skipped += 1
                    continue
                retention_days = _retention_days_for_state(policy, task.state)
                if retention_days is None:
                    continue
                try:
                    updated_dt = _parse_utc(task.updated_at, "updated_at")
                except ValueError:
                    raise OrchestrationError("task_evidence_corrupt") from None
                if updated_dt <= effective_now - timedelta(days=retention_days):
                    if len(eligible) < limit:
                        eligible.append(task)
                    else:
                        truncated = True
            selected_tasks = sorted(eligible, key=lambda item: item.id)

        preview_items: list[PurgePreviewItem] = []
        snapshot_entries: list[dict[str, object]] = []
        total_plans = total_subtasks = total_deps = total_approvals = total_events = 0

        for task in sorted(selected_tasks, key=lambda item: item.id):
            plan_count = int(connection.execute(
                "SELECT COUNT(*) FROM orchestration_plans WHERE workspace_id=? AND task_id=?",
                (workspace_id, task.id),
            ).fetchone()[0])
            subtask_count = int(connection.execute(
                "SELECT COUNT(*) FROM orchestration_subtasks WHERE workspace_id=? AND task_id=?",
                (workspace_id, task.id),
            ).fetchone()[0])
            dep_count = int(connection.execute(
                "SELECT COUNT(*) FROM orchestration_dependencies WHERE workspace_id=? AND task_id=?",
                (workspace_id, task.id),
            ).fetchone()[0])
            approval_count = int(connection.execute(
                "SELECT COUNT(*) FROM orchestration_approvals WHERE workspace_id=? AND task_id=?",
                (workspace_id, task.id),
            ).fetchone()[0])
            event_row = connection.execute(
                "SELECT COUNT(*) AS cnt, COALESCE(MAX(id), 0) AS max_id "
                "FROM orchestration_task_events WHERE workspace_id=? AND task_id=?",
                (workspace_id, task.id),
            ).fetchone()
            event_count = int(event_row["cnt"])
            latest_event_id = int(event_row["max_id"])
            total_plans += plan_count
            total_subtasks += subtask_count
            total_deps += dep_count
            total_approvals += approval_count
            total_events += event_count
            preview_items.append(PurgePreviewItem(
                task_id=task.id,
                state=task.state,
                archived_at=task.archived_at,
                updated_at=task.updated_at,
                goal_sha256=task.goal_sha256,
                plan_count=plan_count,
                subtask_count=subtask_count,
                dependency_count=dep_count,
                approval_count=approval_count,
                event_count=event_count,
            ))
            snapshot_entries.append({
                "task_id": task.id,
                "state": task.state.value,
                "archived_at": task.archived_at,
                "updated_at": task.updated_at,
                "current_plan_revision": task.current_plan_revision,
                "approved_plan_revision": task.approved_plan_revision,
                "goal_sha256": task.goal_sha256,
                "result_summary_sha256": (
                    _digest(task.result_summary) if task.result_summary is not None else None
                ),
                "plan_count": plan_count,
                "subtask_count": subtask_count,
                "dependency_count": dep_count,
                "approval_count": approval_count,
                "event_count": event_count,
                "latest_event_id": latest_event_id,
            })

        preview_sha256 = _digest(_canonical_json({
            "schema": 1,
            "workspace_id": workspace_id,
            "archived_only": archived_only,
            "items": snapshot_entries,
        }))
        confirmation_phrase = (
            _purge_confirmation_phrase(workspace_id, preview_sha256)
            if preview_items else None
        )
        return PurgePreview(
            workspace_id=workspace_id,
            preview_sha256=preview_sha256,
            confirmation_phrase=confirmation_phrase,
            archived_only=archived_only,
            use_retention_policy=use_retention_policy,
            items=tuple(preview_items),
            task_count=len(preview_items),
            plan_count=total_plans,
            subtask_count=total_subtasks,
            dependency_count=total_deps,
            approval_count=total_approvals,
            event_count=total_events,
            truncated=truncated,
            protected_skipped_count=protected_skipped,
        )

    def preview_purge(self, actor_id: str, workspace_id: str, *,
                      task_ids: tuple[str, ...] | None = None,
                      use_retention_policy: bool = False,
                      archived_only: bool = False,
                      as_of: str | None = None,
                      limit: int = MAX_PURGE_BATCH) -> PurgePreview:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        if type(use_retention_policy) is not bool or type(archived_only) is not bool:
            raise ValueError("purge preview flags must be booleans")
        if task_ids is None and not use_retention_policy:
            raise ValueError("specify task_ids or use_retention_policy=True")
        if type(limit) is not int or not 1 <= limit <= MAX_PURGE_BATCH:
            raise ValueError(f"purge preview limit must be between 1 and {MAX_PURGE_BATCH}")
        effective_now = _parse_utc(as_of, "as_of") if as_of is not None else datetime.now(timezone.utc)
        with self.db._connection() as connection:
            self._require_workspace_manager(connection, actor_id, workspace_id, write=True)
            return self._collect_purge_preview(
                connection, workspace_id,
                task_ids=task_ids,
                use_retention_policy=use_retention_policy,
                archived_only=archived_only,
                effective_now=effective_now,
                limit=limit,
                strict_targets=False,
            )

    @staticmethod
    def _receipt_from_operation(connection: sqlite3.Connection, op_row, *,
                                already_purged: bool) -> PurgeReceipt:
        task_ids = tuple(row["task_id"] for row in connection.execute(
            "SELECT task_id FROM orchestration_purge_tombstones "
            "WHERE workspace_id=? AND purge_id=? ORDER BY task_id",
            (op_row["workspace_id"], op_row["purge_id"]),
        ).fetchall())
        return PurgeReceipt(
            purge_id=op_row["purge_id"],
            workspace_id=op_row["workspace_id"],
            preview_sha256=op_row["preview_sha256"],
            task_ids=task_ids,
            task_count=int(op_row["task_count"]),
            plan_count=int(op_row["plan_count"]),
            subtask_count=int(op_row["subtask_count"]),
            dependency_count=int(op_row["dependency_count"]),
            approval_count=int(op_row["approval_count"]),
            event_count=int(op_row["event_count"]),
            purged_by=op_row["actor_id"],
            purged_at=op_row["purged_at"],
            already_purged=already_purged,
        )

    def purge_tasks(self, actor_id: str, workspace_id: str, *,
                    task_ids: tuple[str, ...],
                    expected_preview_sha256: str,
                    confirmation: str,
                    idempotency_key: str,
                    use_retention_policy: bool = False,
                    archived_only: bool = False,
                    as_of: str | None = None) -> PurgeReceipt:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        idempotency_key = _idempotency(idempotency_key)
        if type(use_retention_policy) is not bool or type(archived_only) is not bool:
            raise ValueError("purge flags must be booleans")
        if not isinstance(task_ids, tuple) or not 1 <= len(task_ids) <= MAX_PURGE_BATCH:
            raise ValueError(f"task_ids must be a tuple of 1 to {MAX_PURGE_BATCH} task IDs")
        sorted_ids = tuple(sorted({_nonempty(item, "task ID") for item in task_ids}))
        if len(sorted_ids) != len(task_ids):
            raise ValueError("task_ids must be unique")
        if not isinstance(expected_preview_sha256, str) or not _SHA256.fullmatch(expected_preview_sha256):
            raise ValueError("expected_preview_sha256 must be a 64-character hex digest")
        if not isinstance(confirmation, str):
            raise ValueError("confirmation must be a string")
        expected_confirmation = _purge_confirmation_phrase(workspace_id, expected_preview_sha256)
        if not secrets.compare_digest(confirmation, expected_confirmation):
            raise OrchestrationError("purge_confirmation_mismatch")
        effective_now = _parse_utc(as_of, "as_of") if as_of is not None else datetime.now(timezone.utc)

        self.db.initialize()
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                self._require_workspace_manager(connection, actor_id, workspace_id, write=True)
                existing_op = connection.execute(
                    "SELECT * FROM orchestration_purge_operations "
                    "WHERE workspace_id=? AND actor_id=? AND idempotency_key=?",
                    (workspace_id, actor_id, idempotency_key),
                ).fetchone()
                if existing_op is not None:
                    receipt = self._receipt_from_operation(connection, existing_op, already_purged=True)
                    if (not secrets.compare_digest(receipt.preview_sha256, expected_preview_sha256)
                            or receipt.task_ids != sorted_ids):
                        raise OrchestrationError("idempotency_key_conflict")
                    self.db._rollback(connection)
                    return receipt

                live_preview = self._collect_purge_preview(
                    connection, workspace_id,
                    task_ids=sorted_ids,
                    use_retention_policy=use_retention_policy,
                    archived_only=archived_only,
                    effective_now=effective_now,
                    limit=MAX_PURGE_BATCH,
                    strict_targets=True,
                )
                if (not live_preview.items
                        or not secrets.compare_digest(live_preview.preview_sha256, expected_preview_sha256)):
                    raise OrchestrationError("purge_preview_stale")

                purge_id = uuid4().hex
                purged_at = _now()
                connection.execute(
                    "INSERT INTO orchestration_purge_operations("
                    "purge_id,workspace_id,actor_id,idempotency_key,preview_sha256,"
                    "task_count,plan_count,subtask_count,dependency_count,"
                    "approval_count,event_count,purged_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (purge_id, workspace_id, actor_id, idempotency_key, expected_preview_sha256,
                     live_preview.task_count, live_preview.plan_count, live_preview.subtask_count,
                     live_preview.dependency_count, live_preview.approval_count,
                     live_preview.event_count, purged_at),
                )
                for item in live_preview.items:
                    connection.execute(
                        "INSERT INTO orchestration_purge_tombstones("
                        "workspace_id,task_id,purge_id,task_state,archived_at,goal_sha256,"
                        "plan_count,subtask_count,dependency_count,approval_count,"
                        "event_count,purged_by,purged_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (workspace_id, item.task_id, purge_id, item.state.value, item.archived_at,
                         item.goal_sha256, item.plan_count, item.subtask_count,
                         item.dependency_count, item.approval_count, item.event_count,
                         actor_id, purged_at),
                    )
                for item in live_preview.items:
                    cursor = connection.execute(
                        "DELETE FROM orchestration_tasks WHERE workspace_id=? AND id=?",
                        (workspace_id, item.task_id),
                    )
                    if cursor.rowcount != 1:
                        raise OrchestrationError("purge_preview_stale")
                    for child_table in (
                        "orchestration_plans",
                        "orchestration_subtasks",
                        "orchestration_dependencies",
                        "orchestration_approvals",
                        "orchestration_task_events",
                    ):
                        remaining = connection.execute(
                            f"SELECT 1 FROM {child_table} WHERE workspace_id=? AND task_id=? LIMIT 1",
                            (workspace_id, item.task_id),
                        ).fetchone()
                        if remaining is not None:
                            raise OrchestrationError("purge_integrity_error")
                Database._insert_audit(
                    connection, workspace_id, actor_id,
                    "orchestration.tasks_purged", "orchestration_purge", purge_id,
                    {
                        "preview_sha256": expected_preview_sha256,
                        "task_count": str(live_preview.task_count),
                        "plan_count": str(live_preview.plan_count),
                        "subtask_count": str(live_preview.subtask_count),
                        "dependency_count": str(live_preview.dependency_count),
                        "approval_count": str(live_preview.approval_count),
                        "event_count": str(live_preview.event_count),
                        "task_ids_sha256": _digest(_canonical_json(list(sorted_ids))),
                    },
                )
                self.db._commit(connection)
            except Exception:
                self.db._rollback(connection)
                raise
        return PurgeReceipt(
            purge_id=purge_id,
            workspace_id=workspace_id,
            preview_sha256=expected_preview_sha256,
            task_ids=sorted_ids,
            task_count=live_preview.task_count,
            plan_count=live_preview.plan_count,
            subtask_count=live_preview.subtask_count,
            dependency_count=live_preview.dependency_count,
            approval_count=live_preview.approval_count,
            event_count=live_preview.event_count,
            purged_by=actor_id,
            purged_at=purged_at,
            already_purged=False,
        )

    @staticmethod
    def _tombstone(row) -> PurgeTombstone:
        try:
            if not _SHA256.fullmatch(row["goal_sha256"]):
                raise ValueError
            if row["archived_at"] is not None:
                _parse_utc(row["archived_at"], "archived_at")
            _parse_utc(row["purged_at"], "purged_at")
            return PurgeTombstone(
                workspace_id=row["workspace_id"],
                task_id=row["task_id"],
                purge_id=row["purge_id"],
                task_state=TaskState(row["task_state"]),
                archived_at=row["archived_at"],
                goal_sha256=row["goal_sha256"],
                plan_count=int(row["plan_count"]),
                subtask_count=int(row["subtask_count"]),
                dependency_count=int(row["dependency_count"]),
                approval_count=int(row["approval_count"]),
                event_count=int(row["event_count"]),
                purged_by=_nonempty(row["purged_by"], "purged_by"),
                purged_at=row["purged_at"],
            )
        except (ValueError, TypeError):
            raise OrchestrationError("task_evidence_corrupt") from None

    def get_purge_tombstone(self, actor_id: str, workspace_id: str, task_id: str) -> PurgeTombstone:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        task_id = _nonempty(task_id, "task ID")
        with self.db._connection() as connection:
            self._require_workspace_manager(connection, actor_id, workspace_id, write=False)
            row = connection.execute(
                "SELECT * FROM orchestration_purge_tombstones WHERE workspace_id=? AND task_id=?",
                (workspace_id, task_id),
            ).fetchone()
        if row is None:
            raise OrchestrationError("tombstone_unavailable")
        return self._tombstone(row)

    def list_purge_tombstones(self, actor_id: str, workspace_id: str, *,
                              limit: int = 50) -> tuple[PurgeTombstone, ...]:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("tombstone limit must be between 1 and 100")
        with self.db._connection() as connection:
            self._require_workspace_manager(connection, actor_id, workspace_id, write=False)
            rows = connection.execute(
                "SELECT * FROM orchestration_purge_tombstones WHERE workspace_id=? "
                "ORDER BY purged_at DESC, task_id ASC LIMIT ?",
                (workspace_id, limit),
            ).fetchall()
        return tuple(self._tombstone(row) for row in rows)
