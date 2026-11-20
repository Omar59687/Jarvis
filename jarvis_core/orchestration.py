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
_TASK_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_SAFE_EVENT_TYPES = {
    "task_created", "plan_proposed", "plan_approved", "plan_rejected",
    "task_blocked", "simulation_progress", "simulation_completed",
    "task_cancelled", "task_interrupted", "simulation_resumed",
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
                    "AND expires_at>?", (workspace_id, actor_id, created_at),
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
                   include_history: bool = True) -> tuple[OrchestrationTask, ...]:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("task list limit must be between 1 and 100")
        self.db.require_access(actor_id, workspace_id, Permission.READ)
        manager = self.db.check_access(actor_id, workspace_id, Permission.MANAGE_WORKSPACE)
        sql = "SELECT id FROM orchestration_tasks WHERE workspace_id=?"
        params: list[object] = [workspace_id]
        if not manager:
            sql += " AND principal_id=?"
            params.append(actor_id)
        if not include_history:
            sql += " AND state NOT IN ('simulation_completed','completed','failed','cancelled','stale')"
        sql += " ORDER BY updated_at DESC,id LIMIT ?"
        params.append(limit)
        with self.db._connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return tuple(self.get_task(actor_id, row["id"]) for row in rows)

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
                current = connection.execute("SELECT state,execution_mode,current_plan_revision "
                                             "FROM orchestration_tasks WHERE id=? AND principal_id=?",
                                             (task.id, task.principal_id)).fetchone()
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
                connection.execute(
                    "UPDATE orchestration_tasks SET state='awaiting_approval',execution_mode='planned',"
                    "current_plan_revision=?,approved_plan_revision=NULL,result_summary=NULL,updated_at=? "
                    "WHERE id=? AND principal_id=?",
                    (revision, now, task.id, task.principal_id),
                )
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
                            "WHERE id=? AND current_plan_revision=? AND state IN ('awaiting_approval','blocked')",
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
                    "approved_plan_revision=?,updated_at=? WHERE id=? AND state IN "
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
                    "SELECT state,approved_plan_revision FROM orchestration_tasks WHERE id=?",
                    (task.id,),
                ).fetchone()
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
                    "WHERE id=? AND current_plan_revision=? AND state IN ('queued','simulating')",
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
        if task.state in {TaskState.COMPLETED, TaskState.FAILED,
                          TaskState.SIMULATION_COMPLETED, TaskState.CANCELLED}:
            raise OrchestrationError("task_not_cancellable")
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                current = connection.execute(
                    "SELECT state,current_plan_revision,capability_manifest_sha256 "
                    "FROM orchestration_tasks WHERE id=? AND principal_id=?",
                    (task.id, task.principal_id),
                ).fetchone()
                if current is None:
                    raise OrchestrationError("task_unavailable")
                current_state = TaskState(current["state"])
                if current_state in {TaskState.COMPLETED, TaskState.FAILED,
                                     TaskState.SIMULATION_COMPLETED, TaskState.CANCELLED}:
                    raise OrchestrationError("task_not_cancellable")
                Database._require_access(connection, actor_id, task.workspace_id, Permission.WRITE)
                cursor = connection.execute(
                    "UPDATE orchestration_tasks SET state='cancelled',updated_at=? "
                    "WHERE id=? AND principal_id=? AND state=?",
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
                        "WHERE id=? AND state IN ('queued','simulating')",
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
                progressed = connection.execute(
                    "SELECT 1 FROM orchestration_subtasks WHERE task_id=? AND revision=? "
                    "AND progress_percent>0 LIMIT 1", (task.id, plan.revision),
                ).fetchone() is not None
                state = "simulating" if progressed else "queued"
                mode = "simulated" if progressed else "planned"
                cursor = connection.execute(
                    "UPDATE orchestration_tasks SET state=?,execution_mode=?,updated_at=? "
                    "WHERE id=? AND state IN ('interrupted','blocked') AND approved_plan_revision=?",
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
