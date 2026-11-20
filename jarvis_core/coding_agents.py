"""Deny-by-default policy primitives for supervised repository tasks.

This module does not launch agents, write worktrees, execute processes, or
contact network services. Repository grants are separate from workspace and
memory permissions and are always scoped to one registered repository.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import uuid4

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
