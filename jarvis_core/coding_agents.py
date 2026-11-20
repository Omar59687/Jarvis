"""Deny-by-default policy primitives for supervised repository tasks.

This module does not launch agents, write worktrees, execute processes, or
contact network services. Repository grants are separate from workspace and
memory permissions and are always scoped to one registered repository.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import difflib
import hashlib
import json
from pathlib import PurePosixPath
import re
import secrets
import sqlite3
from threading import Event
from threading import Lock
from typing import Protocol
from uuid import uuid4

from .developer import (
    DeveloperRepositoryService,
    RepositoryError,
    StaleRepository,
    _contains_secret_material,
    _git,
    _indexable_path,
    _repo_head,
    _safe_relative,
)
from .storage import AccessDenied, Database, Permission, _now, _nonempty


class CodingCapability(str, Enum):
    READ_REPOSITORY_METADATA = "read_repository_metadata"
    READ_INDEXED_SOURCE = "read_indexed_source"
    PROPOSE_CODE_MODIFICATIONS = "propose_code_modifications"
    CREATE_ISOLATED_WORKTREE = "create_isolated_worktree"
    MODIFY_TASK_WORKTREE = "modify_task_worktree"
    EXECUTE_VERIFICATION = "execute_verification"
    REQUEST_NETWORK_ACCESS = "request_network_access"
    CREATE_COMMIT = "create_commit"
    PUSH_BRANCH = "push_branch"
    OPEN_PULL_REQUEST = "open_pull_request"
    MERGE_OR_DEPLOY = "merge_or_deploy"


# Until the target Windows isolation gate passes, no capability can authorize
# repository mutation or process/network execution.
GRANTABLE_CODING_CAPABILITIES = frozenset({
    CodingCapability.READ_REPOSITORY_METADATA,
    CodingCapability.READ_INDEXED_SOURCE,
    CodingCapability.PROPOSE_CODE_MODIFICATIONS,
})


class CodingAuthorizationDenied(AccessDenied):
    """A task capability is absent, revoked, or unavailable on this platform."""


@dataclass(frozen=True)
class CodingCapabilityGrant:
    id: str
    workspace_id: str
    repository_id: str
    principal_id: str
    capability: CodingCapability
    granted_by: str
    granted_at: str
    expires_at: str | None
    revoked_at: str | None


class CodingCapabilityPolicy:
    """Trusted-host authority for explicit, repository-scoped task grants."""

    def __init__(self, database: Database) -> None:
        self.db = database

    def grant(
        self,
        manager_id: str,
        workspace_id: str,
        repository_id: str,
        principal_id: str,
        capability: CodingCapability | str,
        *,
        confirmed: bool,
        expires_at: str | None = None,
    ) -> CodingCapabilityGrant:
        manager_id = _nonempty(manager_id, "manager ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        repository_id = _nonempty(repository_id, "repository ID")
        principal_id = _nonempty(principal_id, "principal ID")
        capability = CodingCapability(capability)
        if confirmed is not True:
            raise CodingAuthorizationDenied("explicit_terminal_confirmation_required")
        if capability not in GRANTABLE_CODING_CAPABILITIES:
            raise CodingAuthorizationDenied("capability_unavailable_without_isolation")
        if expires_at is not None:
            from datetime import datetime, timezone
            try:
                expiry = datetime.fromisoformat(expires_at)
            except (TypeError, ValueError):
                raise ValueError("capability expiry must be an ISO timestamp") from None
            if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
                raise ValueError("capability expiry must be a future timezone-aware timestamp")
            expires_at = expiry.astimezone(timezone.utc).isoformat(timespec="microseconds")

        grant_id = uuid4().hex
        granted_at = _now()
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                Database._require_access(
                    connection, manager_id, workspace_id, Permission.MANAGE_WORKSPACE
                )
                repository = connection.execute(
                    """SELECT r.id FROM developer_repositories r
                       JOIN approved_roots a ON a.id=r.approved_root_id
                       WHERE r.id=? AND r.workspace_id=? AND r.revoked_at IS NULL
                         AND a.revoked_at IS NULL""",
                    (repository_id, workspace_id),
                ).fetchone()
                if repository is None:
                    raise CodingAuthorizationDenied("repository_unavailable")
                connection.execute(
                    """INSERT INTO coding_repository_grants
                       (id,workspace_id,repository_id,principal_id,capability,granted_by,
                        granted_at,expires_at,revoked_at)
                       VALUES(?,?,?,?,?,?,?,?,NULL)""",
                    (grant_id, workspace_id, repository_id, principal_id,
                     capability.value, manager_id, granted_at, expires_at),
                )
                self.db._insert_audit(
                    connection, workspace_id, manager_id, "coding.capability_granted",
                    "coding_repository_grant", grant_id,
                    {"repository_id": repository_id, "principal_id": principal_id,
                     "capability": capability.value},
                )
                self.db._commit(connection)
            except Exception:
                self.db._rollback(connection)
                raise
        return CodingCapabilityGrant(grant_id, workspace_id, repository_id,
                                     principal_id, capability, manager_id,
                                     granted_at, expires_at, None)

    def revoke(self, manager_id: str, grant_id: str, *, confirmed: bool) -> bool:
        manager_id = _nonempty(manager_id, "manager ID")
        grant_id = _nonempty(grant_id, "grant ID")
        if confirmed is not True:
            raise CodingAuthorizationDenied("explicit_terminal_confirmation_required")
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                row = connection.execute(
                    "SELECT workspace_id,repository_id,principal_id,capability,revoked_at "
                    "FROM coding_repository_grants WHERE id=?", (grant_id,),
                ).fetchone()
                if row is None:
                    self.db._commit(connection)
                    return False
                Database._require_access(
                    connection, manager_id, row["workspace_id"], Permission.MANAGE_WORKSPACE
                )
                if row["revoked_at"] is not None:
                    self.db._commit(connection)
                    return False
                revoked_at = _now()
                connection.execute(
                    "UPDATE coding_repository_grants SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                    (revoked_at, grant_id),
                )
                self.db._insert_audit(
                    connection, row["workspace_id"], manager_id, "coding.capability_revoked",
                    "coding_repository_grant", grant_id,
                    {"repository_id": row["repository_id"],
                     "principal_id": row["principal_id"],
                     "capability": row["capability"]},
                )
                self.db._commit(connection)
                return True
            except Exception:
                self.db._rollback(connection)
                raise

    def list_grants(self, manager_id: str, workspace_id: str,
                    repository_id: str | None = None) -> list[CodingCapabilityGrant]:
        manager_id = _nonempty(manager_id, "manager ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        with self.db._connection() as connection:
            Database._require_access(
                connection, manager_id, workspace_id, Permission.MANAGE_WORKSPACE
            )
            if repository_id is None:
                rows = connection.execute(
                    "SELECT id,workspace_id,repository_id,principal_id,capability,granted_by,"
                    "granted_at,expires_at,revoked_at FROM coding_repository_grants "
                    "WHERE workspace_id=? ORDER BY granted_at,id LIMIT 500", (workspace_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT id,workspace_id,repository_id,principal_id,capability,granted_by,"
                    "granted_at,expires_at,revoked_at FROM coding_repository_grants "
                    "WHERE workspace_id=? AND repository_id=? ORDER BY granted_at,id LIMIT 500",
                    (workspace_id, _nonempty(repository_id, "repository ID")),
                ).fetchall()
        return [CodingCapabilityGrant(
            row["id"], row["workspace_id"], row["repository_id"], row["principal_id"],
            CodingCapability(row["capability"]), row["granted_by"], row["granted_at"],
            row["expires_at"], row["revoked_at"],
        ) for row in rows]

    def is_granted(self, principal_id: str, workspace_id: str, repository_id: str,
                   capability: CodingCapability | str) -> bool:
        principal_id = _nonempty(principal_id, "principal ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        repository_id = _nonempty(repository_id, "repository ID")
        capability = CodingCapability(capability)
        if capability not in GRANTABLE_CODING_CAPABILITIES:
            return False
        self.db.initialize()
        with self.db._connection() as connection:
            if not self.db.check_access(principal_id, workspace_id, Permission.READ):
                return False
            row = connection.execute(
                """SELECT 1 FROM coding_repository_grants g
                   JOIN developer_repositories r ON r.id=g.repository_id
                     AND r.workspace_id=g.workspace_id
                   JOIN approved_roots a ON a.id=r.approved_root_id
                     AND a.workspace_id=r.workspace_id
                   WHERE g.principal_id=? AND g.workspace_id=? AND g.repository_id=?
                     AND g.capability=? AND g.revoked_at IS NULL
                     AND (g.expires_at IS NULL OR g.expires_at>?)
                     AND r.revoked_at IS NULL AND a.revoked_at IS NULL LIMIT 1""",
                (principal_id, workspace_id, repository_id, capability.value, _now()),
            ).fetchone()
            return row is not None

    def require(self, principal_id: str, workspace_id: str, repository_id: str,
                capability: CodingCapability | str) -> None:
        capability = CodingCapability(capability)
        if not self.is_granted(principal_id, workspace_id, repository_id, capability):
            raise CodingAuthorizationDenied("coding_capability_denied")

    def manifest(self, principal_id: str, workspace_id: str, repository_id: str,
                 requested: list[CodingCapability | str]) -> dict[str, object]:
        unique = sorted({CodingCapability(item) for item in requested}, key=lambda item: item.value)
        granted = [item.value for item in unique
                   if self.is_granted(principal_id, workspace_id, repository_id, item)]
        unavailable = [item.value for item in unique
                       if item not in GRANTABLE_CODING_CAPABILITIES]
        denied = [item.value for item in unique
                  if item.value not in granted and item.value not in unavailable]
        return {
            "repository_id": repository_id,
            "workspace_id": workspace_id,
            "requested": [item.value for item in unique],
            "granted": granted,
            "denied": denied,
            "unavailable": unavailable,
            "interactive_approval_required": [
                CodingCapability.PROPOSE_CODE_MODIFICATIONS.value,
                CodingCapability.CREATE_ISOLATED_WORKTREE.value,
                CodingCapability.MODIFY_TASK_WORKTREE.value,
                CodingCapability.EXECUTE_VERIFICATION.value,
                CodingCapability.REQUEST_NETWORK_ACCESS.value,
                CodingCapability.CREATE_COMMIT.value,
                CodingCapability.PUSH_BRANCH.value,
                CodingCapability.OPEN_PULL_REQUEST.value,
                CodingCapability.MERGE_OR_DEPLOY.value,
            ],
            "agent_may_approve": False,
            "execution_enabled": False,
        }


class CodingTaskState(str, Enum):
    REQUESTED = "requested"
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    STALE = "stale"


@dataclass(frozen=True)
class CodingTask:
    id: str
    workspace_id: str
    repository_id: str
    principal_id: str
    base_commit: str
    request_sha256: str
    manifest: dict[str, object]
    manifest_sha256: str
    status: CodingTaskState
    diff_sha256: str | None
    approved_diff_sha256: str | None
    created_at: str
    updated_at: str
    expires_at: str


@dataclass(frozen=True)
class WorktreePlan:
    task_id: str
    repository_id: str
    base_commit: str
    state: str
    dirty_file_count: int
    write_available: bool = False
    detail: str = ""


@dataclass(frozen=True)
class IndexedSourceInput:
    relative_path: str
    content_sha256: str
    content: str


@dataclass(frozen=True)
class AgentTaskInput:
    task_id: str
    workspace_id: str
    repository_id: str
    base_commit: str
    request_text: str
    capability_manifest: dict[str, object]
    indexed_sources: tuple[IndexedSourceInput, ...]
    timeout_seconds: int
    max_files: int
    max_file_bytes: int
    max_diff_bytes: int


@dataclass(frozen=True)
class AgentChange:
    relative_path: str
    new_content: str


@dataclass(frozen=True)
class AgentProposal:
    changes: tuple[AgentChange, ...] = ()
    evidence_paths: tuple[str, ...] = ()
    logs: tuple[str, ...] = ()
    approval_requests: tuple[str, ...] = ()
    outcome: str = "proposed"


class AgentAdapter(Protocol):
    """Provider-independent proposal interface; adapters have no direct tools."""

    def propose(self, task: AgentTaskInput, cancelled: Event) -> AgentProposal: ...

    def cancel(self, task_id: str) -> None: ...


class FakeAgentAdapter:
    """Deterministic offline adapter for tests; never executes commands or writes files."""

    def __init__(self, changes: tuple[AgentChange, ...] = (), *,
                 approval_requests: tuple[str, ...] = (),
                 logs: tuple[str, ...] = ()) -> None:
        self.changes = changes
        self.approval_requests = approval_requests
        self.logs = logs
        self.calls: list[str] = []
        self.cancelled: set[str] = set()

    def propose(self, task: AgentTaskInput, cancelled: Event) -> AgentProposal:
        self.calls.append(task.task_id)
        if cancelled.is_set():
            return AgentProposal(outcome="cancelled")
        return AgentProposal(self.changes, tuple(item.relative_path for item in task.indexed_sources),
                             self.logs, self.approval_requests)

    def cancel(self, task_id: str) -> None:
        self.cancelled.add(task_id)


MAX_TASKS_PER_PRINCIPAL = 50
MAX_TASK_REQUEST_CHARS = 4000
MAX_TASK_LIFETIME_SECONDS = 24 * 60 * 60
_COMMIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class CodingTaskService:
    """Persist scoped task intent and audit hashes; never stores the request body."""

    def __init__(self, database: Database,
                 repositories: DeveloperRepositoryService | None = None,
                 policy: CodingCapabilityPolicy | None = None) -> None:
        self.db = database
        self.repositories = repositories or DeveloperRepositoryService(database)
        self.policy = policy or CodingCapabilityPolicy(database)
        self._active_cancellations: dict[str, tuple[Event, AgentAdapter]] = {}
        self._active_lock = Lock()

    @staticmethod
    def _task(row) -> CodingTask:
        try:
            manifest = json.loads(row["manifest_json"])
            if not isinstance(manifest, dict):
                raise ValueError
        except (ValueError, TypeError):
            raise CodingAuthorizationDenied("task_record_unavailable") from None
        return CodingTask(
            row["id"], row["workspace_id"], row["repository_id"], row["principal_id"],
            row["base_commit"], row["request_sha256"], manifest,
            row["manifest_sha256"], CodingTaskState(row["status"]),
            row["diff_sha256"], row["approved_diff_sha256"],
            row["created_at"], row["updated_at"], row["expires_at"],
        )

    def create_task(self, principal_id: str, workspace_id: str, repository_id: str,
                    base_commit: str, request_text: str, *,
                    requested_capabilities: list[CodingCapability | str] | None = None,
                    lifetime_seconds: int = 4 * 60 * 60) -> CodingTask:
        principal_id = _nonempty(principal_id, "principal ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        repository_id = _nonempty(repository_id, "repository ID")
        if not isinstance(base_commit, str) or not _COMMIT_SHA.fullmatch(base_commit):
            raise ValueError("base commit must be a full object ID")
        if (not isinstance(request_text, str) or not request_text.strip()
                or len(request_text) > MAX_TASK_REQUEST_CHARS or "\x00" in request_text):
            raise ValueError("task request is empty or outside the size limit")
        if (type(lifetime_seconds) is not int or not 60 <= lifetime_seconds <= MAX_TASK_LIFETIME_SECONDS):
            raise ValueError("task lifetime is outside the supported bound")
        self.db.require_access(principal_id, workspace_id, Permission.READ)
        self.policy.require(principal_id, workspace_id, repository_id,
                            CodingCapability.READ_REPOSITORY_METADATA)
        self.policy.require(principal_id, workspace_id, repository_id,
                            CodingCapability.READ_INDEXED_SOURCE)
        repository = self.repositories._repository(principal_id, workspace_id, repository_id)
        if repository.index_status != "ready" or repository.head_commit != base_commit:
            raise StaleRepository()
        self.repositories._ensure_current(principal_id, workspace_id, repository)
        requested = requested_capabilities or [CodingCapability.READ_REPOSITORY_METADATA,
                                               CodingCapability.READ_INDEXED_SOURCE]
        manifest = self.policy.manifest(principal_id, workspace_id, repository_id, requested)
        manifest.update({
            "base_commit": base_commit,
            "task_write_enabled": False,
            "process_execution_enabled": False,
            "network_enabled": False,
            "worktree_path": None,
            "budgets": {"wall_time_seconds": lifetime_seconds,
                        "max_files": 50, "max_file_bytes": 262144,
                        "max_diff_bytes": 524288},
        })
        manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        manifest_digest = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
        request_digest = hashlib.sha256(request_text.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        created_at = now.isoformat(timespec="microseconds")
        expires_at = (now + timedelta(seconds=lifetime_seconds)).isoformat(timespec="microseconds")
        task_id = uuid4().hex
        self.db.initialize()
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                Database._require_access(connection, principal_id, workspace_id, Permission.READ)
                active = connection.execute(
                    "SELECT COUNT(*) FROM coding_tasks WHERE principal_id=? AND workspace_id=? "
                    "AND status IN ('requested','proposed','approved') AND expires_at>?",
                    (principal_id, workspace_id, created_at),
                ).fetchone()[0]
                if active >= MAX_TASKS_PER_PRINCIPAL:
                    raise CodingAuthorizationDenied("active_task_limit")
                connection.execute(
                    """INSERT INTO coding_tasks
                       (id,workspace_id,repository_id,principal_id,base_commit,request_sha256,
                        manifest_json,manifest_sha256,status,created_at,updated_at,expires_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (task_id, workspace_id, repository_id, principal_id, base_commit,
                     request_digest, manifest_json, manifest_digest,
                     CodingTaskState.REQUESTED.value, created_at, created_at, expires_at),
                )
                self._event(connection, task_id, workspace_id, principal_id,
                            "task_requested", manifest_digest,
                            {"repository_id": repository_id, "base_commit": base_commit,
                             "request_sha256": request_digest})
                self.db._commit(connection)
            except Exception:
                self.db._rollback(connection)
                raise
        return CodingTask(task_id, workspace_id, repository_id, principal_id, base_commit,
                          request_digest, manifest, manifest_digest,
                          CodingTaskState.REQUESTED, None, None, created_at, created_at, expires_at)

    def _event(self, connection, task_id: str, workspace_id: str, principal_id: str,
               event_type: str, snapshot_sha256: str | None,
               metadata: dict[str, str]) -> None:
        connection.execute(
            "INSERT INTO coding_task_events(id,task_id,workspace_id,principal_id,event_type,"
            "snapshot_sha256,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (uuid4().hex, task_id, workspace_id, principal_id, event_type,
             snapshot_sha256, json.dumps(metadata, sort_keys=True, separators=(",", ":")), _now()),
        )

    def get_task(self, principal_id: str, task_id: str) -> CodingTask:
        principal_id = _nonempty(principal_id, "principal ID")
        task_id = _nonempty(task_id, "task ID")
        self.db.initialize()
        with self.db._connection() as connection:
            row = connection.execute(
                "SELECT * FROM coding_tasks WHERE id=? AND principal_id=?",
                (task_id, principal_id),
            ).fetchone()
            if row is None:
                raise CodingAuthorizationDenied("task_unavailable")
            Database._require_access(
                connection, principal_id, row["workspace_id"], Permission.READ)
            self.policy.require(principal_id, row["workspace_id"], row["repository_id"],
                                CodingCapability.READ_REPOSITORY_METADATA)
            return self._task(row)

    def list_tasks(self, principal_id: str, workspace_id: str, limit: int = 50) -> list[CodingTask]:
        principal_id = _nonempty(principal_id, "principal ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid task list limit")
        self.db.require_access(principal_id, workspace_id, Permission.READ)
        with self.db._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM coding_tasks WHERE principal_id=? AND workspace_id=? "
                "ORDER BY updated_at DESC,id LIMIT ?",
                (principal_id, workspace_id, limit),
            ).fetchall()
        return [self.get_task(principal_id, row["id"]) for row in rows]

    def audit_events(self, principal_id: str, task_id: str, limit: int = 100) -> list[dict[str, object]]:
        task = self.get_task(principal_id, task_id)
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("invalid audit limit")
        with self.db._connection() as connection:
            rows = connection.execute(
                "SELECT event_type,snapshot_sha256,metadata_json,created_at FROM coding_task_events "
                "WHERE task_id=? AND workspace_id=? ORDER BY created_at,id LIMIT ?",
                (task.id, task.workspace_id, limit),
            ).fetchall()
        return [{"event_type": row[0], "snapshot_sha256": row[1],
                 "metadata": json.loads(row[2]), "created_at": row[3]} for row in rows]

    @staticmethod
    def _proposal_path(value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("proposal path must be text")
        try:
            safe = _safe_relative(value)
        except RepositoryError:
            raise ValueError("proposal path is invalid") from None
        parts = PurePosixPath(safe).parts
        windows_device = re.compile(
            r"(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$")
        if (safe != value or any(":" in part or part.endswith((".", " "))
                                 or windows_device.fullmatch(part) for part in parts)
                or _indexable_path(safe) is None):
            raise ValueError("proposal path is outside the supported source scope")
        return safe

    def _indexed_source(self, principal_id: str, task: CodingTask,
                        relative_path: str) -> IndexedSourceInput:
        self.db.require_access(principal_id, task.workspace_id, Permission.READ)
        self.policy.require(principal_id, task.workspace_id, task.repository_id,
                            CodingCapability.READ_INDEXED_SOURCE)
        repository = self.repositories._repository(
            principal_id, task.workspace_id, task.repository_id)
        self.repositories._ensure_current(principal_id, task.workspace_id, repository)
        with self.db._connection() as connection:
            row = connection.execute(
                "SELECT content_sha256,content FROM developer_files WHERE repository_id=? "
                "AND workspace_id=? AND commit_sha=? AND relative_path=?",
                (task.repository_id, task.workspace_id, task.base_commit, relative_path),
            ).fetchone()
        if row is None:
            raise CodingAuthorizationDenied("indexed_source_unavailable")
        return IndexedSourceInput(relative_path, row["content_sha256"], row["content"])

    def _require_requested_task(self, task: CodingTask) -> None:
        if task.status is not CodingTaskState.REQUESTED:
            raise CodingAuthorizationDenied("task_not_proposable")
        self._require_unexpired(task)

    @staticmethod
    def _require_unexpired(task: CodingTask) -> None:
        try:
            expiry = datetime.fromisoformat(task.expires_at)
        except ValueError:
            raise CodingAuthorizationDenied("task_unavailable") from None
        if expiry <= datetime.now(timezone.utc):
            raise CodingAuthorizationDenied("task_expired")
        manifest_json = json.dumps(task.manifest, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(manifest_json.encode("utf-8")).hexdigest() != task.manifest_sha256:
            raise CodingAuthorizationDenied("task_manifest_changed")

    def _reject_agent_output(self, task: CodingTask, principal_id: str, reason: str) -> None:
        self._append_task_event(task, principal_id, "agent_output_rejected",
                                task.manifest_sha256, {"reason": reason})
        raise CodingAuthorizationDenied(reason)

    def propose_with_adapter(self, principal_id: str, task_id: str, request_text: str,
                             adapter: AgentAdapter, *, cancelled: Event | None = None) -> CodingTask:
        task = self.get_task(principal_id, task_id)
        self._require_requested_task(task)
        if (not isinstance(request_text, str) or not request_text.strip()
                or len(request_text) > MAX_TASK_REQUEST_CHARS
                or hashlib.sha256(request_text.encode("utf-8")).hexdigest() != task.request_sha256):
            raise CodingAuthorizationDenied("task_request_snapshot_changed")
        self.policy.require(principal_id, task.workspace_id, task.repository_id,
                            CodingCapability.PROPOSE_CODE_MODIFICATIONS)
        if CodingCapability.PROPOSE_CODE_MODIFICATIONS.value not in task.manifest.get("granted", []):
            raise CodingAuthorizationDenied("coding_capability_denied")
        self.policy.require(principal_id, task.workspace_id, task.repository_id,
                            CodingCapability.READ_INDEXED_SOURCE)

        # The fixed P3 service selects bounded indexed evidence; the adapter
        # cannot provide a disk path or widen its repository scope.
        hits = self.repositories.search(principal_id, task.workspace_id,
                                        task.repository_id, request_text, limit=5)
        sources: list[IndexedSourceInput] = []
        total_source_bytes = 0
        for hit in hits:
            source = self._indexed_source(principal_id, task, str(hit["relative_path"]))
            size = len(source.content.encode("utf-8"))
            if size <= 256 * 1024 and total_source_bytes + size <= 512 * 1024:
                sources.append(source)
                total_source_bytes += size

        budgets = task.manifest.get("budgets")
        if not isinstance(budgets, dict):
            raise CodingAuthorizationDenied("task_manifest_changed")
        envelope = AgentTaskInput(
            task.id, task.workspace_id, task.repository_id, task.base_commit,
            request_text, task.manifest, tuple(sources),
            int(budgets.get("wall_time_seconds", 0)),
            int(budgets.get("max_files", 0)),
            int(budgets.get("max_file_bytes", 0)),
            int(budgets.get("max_diff_bytes", 0)),
        )
        cancellation = cancelled or Event()
        with self._active_lock:
            self._active_cancellations[task.id] = (cancellation, adapter)
        try:
            output = adapter.propose(envelope, cancellation)
        except Exception:
            # Provider exceptions can contain prompts, repository snippets,
            # credentials, or host paths. Persist a fixed code only.
            self._append_task_event(task, principal_id, "agent_proposal_failed",
                                    task.manifest_sha256, {"reason": "adapter_exception"})
            raise CodingAuthorizationDenied("agent_proposal_failed") from None
        finally:
            with self._active_lock:
                self._active_cancellations.pop(task.id, None)
        if cancellation.is_set() or not isinstance(output, AgentProposal) or output.outcome == "cancelled":
            self._record_transition(task, principal_id, CodingTaskState.CANCELLED.value,
                                    task.manifest_sha256, "task_cancelled",
                                    {"reason": "cancelled_during_proposal"})
            raise CodingAuthorizationDenied("task_cancelled")
        if (type(output) is not AgentProposal or output.outcome != "proposed"
                or not isinstance(output.changes, tuple)
                or not isinstance(output.evidence_paths, tuple)
                or not isinstance(output.logs, tuple)
                or not isinstance(output.approval_requests, tuple)
                or any(type(item) is not AgentChange for item in output.changes)
                or len(output.changes) > 50
                or len(output.evidence_paths) > 20 or len(output.logs) > 100
                or len(output.approval_requests) > 10
                or any(not isinstance(item, str) or len(item) > 4000 for item in output.logs)
                or any(not isinstance(item, str) or len(item) > 200 for item in output.evidence_paths)
                or any(not isinstance(item, str) or len(item) > 200 for item in output.approval_requests)):
            self._reject_agent_output(task, principal_id, "agent_output_invalid")
        if output.approval_requests:
            self._append_task_event(task, principal_id, "agent_approval_request_denied",
                                    task.manifest_sha256,
                                    {"request_count": str(min(len(output.approval_requests), 10))})
            raise CodingAuthorizationDenied("agent_cannot_approve")

        source_by_path = {item.relative_path: item for item in sources}
        seen: set[str] = set()
        changes: list[dict[str, str]] = []
        patch_parts: list[str] = []
        total_output_bytes = 0
        for change in output.changes:
            try:
                path = self._proposal_path(change.relative_path)
            except (ValueError, TypeError):
                self._reject_agent_output(task, principal_id, "proposal_path_invalid")
            if path in seen:
                self._reject_agent_output(task, principal_id, "agent_output_invalid")
            seen.add(path)
            source = source_by_path.get(path)
            if source is None:
                self._reject_agent_output(task, principal_id,
                                          "proposal_outside_indexed_evidence")
            if not isinstance(change.new_content, str) or "\x00" in change.new_content:
                self._reject_agent_output(task, principal_id, "agent_output_invalid")
            try:
                encoded = change.new_content.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                self._reject_agent_output(task, principal_id, "agent_output_invalid")
            if len(encoded) > int(budgets.get("max_file_bytes", 0)):
                self._reject_agent_output(task, principal_id, "agent_output_limit")
            total_output_bytes += len(encoded)
            if total_output_bytes > 1024 * 1024 or _contains_secret_material(change.new_content):
                self._reject_agent_output(task, principal_id,
                                          "proposal_rejected_by_safety_screen")
            patch_parts.append("".join(difflib.unified_diff(
                source.content.splitlines(keepends=True), change.new_content.splitlines(keepends=True),
                fromfile=f"a/{path}", tofile=f"b/{path}", n=3,
            )))
            changes.append({"relative_path": path,
                            "before_sha256": source.content_sha256,
                            "after_content": change.new_content})
        patch = "".join(patch_parts)
        patch_bytes = patch.encode("utf-8")
        if len(patch_bytes) > int(budgets.get("max_diff_bytes", 0)):
            self._reject_agent_output(task, principal_id, "agent_output_limit")
        evidence_paths = [path for path in output.evidence_paths if path in source_by_path][:20]
        diff_digest = hashlib.sha256(patch_bytes).hexdigest()
        proposal_json = json.dumps({"changes": changes, "diff": patch,
                                    "evidence_paths": evidence_paths},
                                   sort_keys=True, separators=(",", ":"))
        self.policy.require(principal_id, task.workspace_id, task.repository_id,
                            CodingCapability.PROPOSE_CODE_MODIFICATIONS)
        repository = self.repositories._repository(
            principal_id, task.workspace_id, task.repository_id)
        if repository.head_commit != task.base_commit:
            raise StaleRepository()
        self._update_proposal(task, principal_id, proposal_json, diff_digest,
                              len(changes), len(evidence_paths), len(output.logs))
        return self.get_task(principal_id, task.id)

    def _append_task_event(self, task: CodingTask, principal_id: str, event_type: str,
                           snapshot_sha256: str | None, metadata: dict[str, str]) -> None:
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                Database._require_access(connection, principal_id, task.workspace_id, Permission.READ)
                self._event(connection, task.id, task.workspace_id, principal_id,
                            event_type, snapshot_sha256, metadata)
                self.db._commit(connection)
            except Exception:
                self.db._rollback(connection)
                raise

    def _record_transition(self, task: CodingTask, principal_id: str, status: str,
                           snapshot_sha256: str | None, event_type: str,
                           metadata: dict[str, str]) -> None:
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                Database._require_access(connection, principal_id, task.workspace_id, Permission.READ)
                cursor = connection.execute(
                    "UPDATE coding_tasks SET status=?,updated_at=? WHERE id=? "
                    "AND principal_id=? AND status=?",
                    (status, _now(), task.id, principal_id, task.status.value),
                )
                if cursor.rowcount != 1:
                    raise CodingAuthorizationDenied("task_changed")
                self._event(connection, task.id, task.workspace_id, principal_id,
                            event_type, snapshot_sha256, metadata)
                self.db._commit(connection)
            except Exception:
                self.db._rollback(connection)
                raise

    def _update_proposal(self, task: CodingTask, principal_id: str,
                         proposal_json: str, diff_digest: str,
                         change_count: int, evidence_count: int, log_count: int) -> None:
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                Database._require_access(connection, principal_id, task.workspace_id, Permission.READ)
                self.policy.require(principal_id, task.workspace_id, task.repository_id,
                                    CodingCapability.PROPOSE_CODE_MODIFICATIONS)
                cursor = connection.execute(
                    "UPDATE coding_tasks SET status='proposed',proposal_json=?,diff_sha256=?,"
                    "updated_at=? WHERE id=? AND principal_id=? AND status='requested' "
                    "AND manifest_sha256=?",
                    (proposal_json, diff_digest, _now(), task.id, principal_id,
                     task.manifest_sha256),
                )
                if cursor.rowcount != 1:
                    raise CodingAuthorizationDenied("task_changed")
                self._event(connection, task.id, task.workspace_id, principal_id,
                            "proposal_recorded", diff_digest,
                            {"change_count": str(change_count),
                             "evidence_count": str(evidence_count),
                             "log_count": str(log_count)})
                self.db._commit(connection)
            except Exception:
                self.db._rollback(connection)
                raise

    def view_proposal(self, principal_id: str, task_id: str) -> dict[str, object]:
        task = self.get_task(principal_id, task_id)
        self._require_unexpired(task)
        if task.status not in {CodingTaskState.PROPOSED, CodingTaskState.APPROVED,
                               CodingTaskState.REJECTED, CodingTaskState.INTERRUPTED}:
            raise CodingAuthorizationDenied("proposal_unavailable")
        with self.db._connection() as connection:
            row = connection.execute(
                "SELECT proposal_json,diff_sha256,approved_diff_sha256,status FROM coding_tasks "
                "WHERE id=? AND principal_id=?", (task.id, principal_id),
            ).fetchone()
        if row is None or not row["proposal_json"]:
            raise CodingAuthorizationDenied("proposal_unavailable")
        try:
            proposal = json.loads(row["proposal_json"])
            patch = proposal["diff"]
        except (ValueError, TypeError, KeyError):
            raise CodingAuthorizationDenied("proposal_unavailable") from None
        if not isinstance(patch, str):
            raise CodingAuthorizationDenied("proposal_unavailable")
        actual_digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        if actual_digest != row["diff_sha256"] or (
            row["status"] == CodingTaskState.APPROVED.value
            and row["approved_diff_sha256"] != actual_digest
        ):
            self._record_transition(task, principal_id, CodingTaskState.STALE.value,
                                    actual_digest, "approval_snapshot_invalidated",
                                    {"reason": "diff_digest_mismatch"})
            raise CodingAuthorizationDenied("approval_snapshot_stale")
        return {"task_id": task.id, "workspace_id": task.workspace_id,
                "repository_id": task.repository_id, "base_commit": task.base_commit,
                "status": row["status"], "diff_sha256": actual_digest,
                "diff": patch, "changes": proposal.get("changes", []),
                "evidence_paths": proposal.get("evidence_paths", [])}

    def decide_proposal(self, principal_id: str, task_id: str, action: str,
                        expected_diff_sha256: str, *, confirmed: bool,
                        decision_id: str | None = None) -> CodingTask:
        if action not in {"approve_proposal", "reject_proposal"}:
            raise ValueError("unsupported proposal decision")
        if confirmed is not True:
            raise CodingAuthorizationDenied("explicit_terminal_confirmation_required")
        if not isinstance(expected_diff_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_diff_sha256):
            raise ValueError("diff digest must be a SHA-256 value")
        decision_id = decision_id or secrets.token_urlsafe(24)
        if not isinstance(decision_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", decision_id):
            raise ValueError("decision ID is invalid")
        task = self.get_task(principal_id, task_id)
        if task.status is not CodingTaskState.PROPOSED:
            raise CodingAuthorizationDenied("task_not_reviewable")
        self.policy.require(principal_id, task.workspace_id, task.repository_id,
                            CodingCapability.PROPOSE_CODE_MODIFICATIONS)
        proposal = self.view_proposal(principal_id, task_id)
        if proposal["diff_sha256"] != expected_diff_sha256:
            raise CodingAuthorizationDenied("approval_snapshot_mismatch")
        repository = self.repositories._repository(principal_id, task.workspace_id, task.repository_id)
        self.repositories._ensure_current(principal_id, task.workspace_id, repository)
        status = (CodingTaskState.APPROVED.value if action == "approve_proposal"
                  else CodingTaskState.REJECTED.value)
        now = _now()
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                Database._require_access(connection, principal_id, task.workspace_id, Permission.READ)
                connection.execute(
                    "INSERT INTO coding_task_decision_receipts"
                    "(decision_id,task_id,workspace_id,principal_id,action,snapshot_sha256,consumed_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (decision_id, task.id, task.workspace_id, principal_id, action,
                     expected_diff_sha256, now),
                )
                cursor = connection.execute(
                    "UPDATE coding_tasks SET status=?,approved_diff_sha256=?,updated_at=? "
                    "WHERE id=? AND principal_id=? AND status='proposed' AND diff_sha256=? "
                    "AND manifest_sha256=?",
                    (status, expected_diff_sha256 if action == "approve_proposal" else None,
                     now, task.id, principal_id, expected_diff_sha256, task.manifest_sha256),
                )
                if cursor.rowcount != 1:
                    raise CodingAuthorizationDenied("approval_snapshot_mismatch")
                self._event(connection, task.id, task.workspace_id, principal_id,
                            action, expected_diff_sha256,
                            {"decision_id": decision_id})
                self.db._commit(connection)
            except sqlite3.IntegrityError:
                self.db._rollback(connection)
                raise CodingAuthorizationDenied("decision_replayed") from None
            except Exception:
                self.db._rollback(connection)
                raise
        return self.get_task(principal_id, task.id)

    def cancel_task(self, principal_id: str, task_id: str) -> bool:
        task = self.get_task(principal_id, task_id)
        if task.status not in {CodingTaskState.REQUESTED, CodingTaskState.PROPOSED}:
            return False
        with self._active_lock:
            active = self._active_cancellations.get(task.id)
            if active is not None:
                active[0].set()
                try:
                    active[1].cancel(task.id)
                except Exception:
                    pass
        self._record_transition(task, principal_id, CodingTaskState.CANCELLED.value,
                                task.diff_sha256, "task_cancelled", {"reason": "operator_cancelled"})
        return True

    def recover_after_restart(self, principal_id: str, workspace_id: str) -> list[str]:
        tasks = self.list_tasks(principal_id, workspace_id, limit=100)
        interrupted: list[str] = []
        for task in tasks:
            if task.status in {CodingTaskState.REQUESTED, CodingTaskState.PROPOSED}:
                self._record_transition(task, principal_id, CodingTaskState.INTERRUPTED.value,
                                        task.diff_sha256, "task_interrupted",
                                        {"reason": "service_restart_no_auto_resume"})
                interrupted.append(task.id)
        return interrupted


class ReadOnlyWorktreeManager:
    """Preflight only: no worktree is created until a verified OS sandbox exists."""

    def __init__(self, tasks: CodingTaskService) -> None:
        self.tasks = tasks

    def plan(self, principal_id: str, task_id: str) -> WorktreePlan:
        task = self.tasks.get_task(principal_id, task_id)
        self.tasks.policy.require(principal_id, task.workspace_id, task.repository_id,
                                  CodingCapability.READ_REPOSITORY_METADATA)
        repo = self.tasks.repositories._repository(
            principal_id, task.workspace_id, task.repository_id)
        path = self.tasks.repositories._repository_path(principal_id, task.workspace_id, repo)
        head, _ = _repo_head(path)
        if head != task.base_commit or repo.head_commit != task.base_commit:
            raise StaleRepository()
        status = _git(path, ["status", "--porcelain=v1", "--untracked-files=all"],
                      limit=1024 * 1024)
        dirty_count = sum(1 for line in status.splitlines() if line)
        if dirty_count:
            return WorktreePlan(task.id, task.repository_id, task.base_commit,
                                "dirty_baseline", dirty_count, False,
                                "The registered checkout has local changes; no worktree was created.")
        return WorktreePlan(task.id, task.repository_id, task.base_commit,
                            "blocked_isolation_unverified", 0, False,
                            "Target operating-system filesystem, process, and network isolation is unverified.")

    def create(self, principal_id: str, task_id: str) -> None:
        # Even an interactive approval cannot replace the missing platform gate.
        self.tasks.get_task(principal_id, task_id)
        raise CodingAuthorizationDenied("write_isolation_unverified")


@dataclass(frozen=True)
class VerificationPlan:
    task_id: str
    check_id: str
    argv: tuple[str, ...]
    state: str
    executable: bool = False
    reason: str = "operating-system isolation is unverified"


class VerificationPlanner:
    """Describe fixed checks without running project code or subprocesses."""

    _CHECKS = {
        "python_compile": ("python", "-m", "compileall", "-q", "."),
        "node_syntax": ("node", "--check", "<approved-file>"),
    }

    def __init__(self, tasks: CodingTaskService) -> None:
        self.tasks = tasks

    def plan(self, principal_id: str, task_id: str, check_id: str) -> VerificationPlan:
        task = self.tasks.get_task(principal_id, task_id)
        if not isinstance(check_id, str) or check_id not in self._CHECKS:
            raise ValueError("verification check is not allowlisted")
        self.tasks.policy.require(principal_id, task.workspace_id, task.repository_id,
                                  CodingCapability.READ_REPOSITORY_METADATA)
        return VerificationPlan(task_id, check_id, self._CHECKS[check_id],
                                "blocked_isolation_unverified")
