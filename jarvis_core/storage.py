"""SQLite persistence and workspace/source permission primitives.

This module deliberately uses only the Python standard library. Authorization
here is application-level: callers must pass the identity established by a
trusted host. Do not accept an actor ID directly from an untrusted HTTP client.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import sqlite3
from typing import Iterator, Protocol, Sequence
from uuid import uuid4


SCHEMA_VERSION = 2
REQUIRED_BACKUP_COLUMNS = {
    "workspaces": {"id", "name", "kind", "created_at"},
    "workspace_permissions": {
        "workspace_id",
        "principal_id",
        "permission",
        "granted_at",
        "granted_by",
    },
    "approved_roots": {
        "id",
        "workspace_id",
        "canonical_path",
        "created_by",
        "created_at",
        "revoked_at",
    },
    "audit_events": {
        "id",
        "workspace_id",
        "actor_id",
        "event_type",
        "resource_type",
        "resource_id",
        "metadata_json",
        "created_at",
    },
}


class WorkspaceKind(str, Enum):
    PERSONAL = "personal"
    PROJECT = "project"


class Permission(str, Enum):
    READ = "read"
    WRITE = "write"
    MANAGE_SOURCES = "manage_sources"
    MANAGE_WORKSPACE = "manage_workspace"


class AccessDenied(PermissionError):
    """Raised for missing workspaces and unauthorized workspace operations."""


class InvalidPath(ValueError):
    """Raised when a selected root or source path cannot be safely resolved."""


class LastWorkspaceManager(RuntimeError):
    """Raised when an operation would leave a workspace without a manager."""


@dataclass(frozen=True)
class Migration:
    version: int
    statements: tuple[str, ...]


@dataclass(frozen=True)
class Workspace:
    id: str
    name: str
    kind: WorkspaceKind
    created_at: str


@dataclass(frozen=True)
class ApprovedRoot:
    id: str
    workspace_id: str
    canonical_path: str
    created_by: str
    created_at: str
    revoked_at: str | None


class Persistence(Protocol):
    """Small interface for future storage adapters and memory extensions."""

    def initialize(self) -> None: ...

    def create_workspace(
        self,
        name: str,
        kind: WorkspaceKind,
        owner_id: str,
        workspace_id: str | None = None,
    ) -> Workspace: ...

    def get_workspace(self, actor_id: str, workspace_id: str) -> Workspace: ...

    def list_workspaces(self, actor_id: str) -> list[Workspace]: ...

    def check_access(
        self, actor_id: str, workspace_id: str, permission: Permission
    ) -> bool: ...

    def register_approved_root(
        self, actor_id: str, workspace_id: str, path: str | Path
    ) -> ApprovedRoot: ...

    def revoke_approved_root(
        self, actor_id: str, workspace_id: str, root_id: str
    ) -> bool: ...


DEFAULT_MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        statements=(
            """
            CREATE TABLE workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 200),
                kind TEXT NOT NULL CHECK(kind IN ('personal', 'project')),
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE workspace_permissions (
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                principal_id TEXT NOT NULL,
                permission TEXT NOT NULL CHECK(permission IN
                    ('read', 'write', 'manage_sources', 'manage_workspace')),
                granted_at TEXT NOT NULL,
                granted_by TEXT NOT NULL,
                PRIMARY KEY(workspace_id, principal_id, permission)
            )
            """,
            """
            CREATE TABLE approved_roots (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                canonical_path TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT
            )
            """,
            """
            CREATE INDEX approved_roots_active_lookup
                ON approved_roots(workspace_id, canonical_path, revoked_at)
            """,
            """
            CREATE TABLE audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT REFERENCES workspaces(id) ON DELETE SET NULL,
                actor_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX audit_events_workspace_order
                ON audit_events(workspace_id, id)
            """,
        ),
    ),
    Migration(
        version=2,
        statements=(
            """
            CREATE TABLE ingested_documents (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                root_id TEXT NOT NULL REFERENCES approved_roots(id) ON DELETE CASCADE,
                relative_path TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                extracted_text TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
                mtime_ns INTEGER NOT NULL,
                file_identity TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                deleted_at TEXT
            )
            """,
            """
            CREATE UNIQUE INDEX ingested_documents_active_path
                ON ingested_documents(root_id, relative_path)
                WHERE deleted_at IS NULL
            """,
            """
            CREATE INDEX ingested_documents_workspace_active
                ON ingested_documents(workspace_id, deleted_at, root_id)
            """,
            """
            CREATE TABLE sync_runs (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                root_id TEXT NOT NULL REFERENCES approved_roots(id) ON DELETE CASCADE,
                actor_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('completed', 'partial', 'failed')),
                discovered_count INTEGER NOT NULL DEFAULT 0,
                changed_count INTEGER NOT NULL DEFAULT 0,
                deleted_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE sync_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                root_id TEXT NOT NULL REFERENCES approved_roots(id) ON DELETE CASCADE,
                document_id TEXT REFERENCES ingested_documents(id) ON DELETE SET NULL,
                event_type TEXT NOT NULL CHECK(event_type IN
                    ('added', 'changed', 'deleted', 'renamed', 'skipped', 'error')),
                relative_path TEXT,
                previous_path TEXT,
                content_sha256 TEXT,
                error_code TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX sync_events_workspace_order
                ON sync_events(workspace_id, id)
            """,
            """
            CREATE TABLE sync_root_state (
                root_id TEXT PRIMARY KEY REFERENCES approved_roots(id) ON DELETE CASCADE,
                device_id TEXT NOT NULL,
                file_id TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE sync_preferences (
                root_id TEXT PRIMARY KEY REFERENCES approved_roots(id) ON DELETE CASCADE,
                enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                interval_seconds INTEGER NOT NULL CHECK(interval_seconds BETWEEN 60 AND 86400),
                next_due_at TEXT NOT NULL
            )
            """,
            """
            CREATE TRIGGER approved_root_revocation_disables_sync
            AFTER UPDATE OF revoked_at ON approved_roots
            WHEN NEW.revoked_at IS NOT NULL AND OLD.revoked_at IS NULL
            BEGIN
                UPDATE sync_preferences SET enabled = 0 WHERE root_id = NEW.id;
            END
            """,
        ),
    ),
)


def default_database_path(app_name: str = "Jarvis") -> Path:
    """Return a per-user database path without creating it."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / app_name / "jarvis.sqlite3"
        return Path.home() / "AppData" / "Local" / app_name / "jarvis.sqlite3"

    xdg_data = os.environ.get("XDG_DATA_HOME")
    base_path = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
    return base_path / app_name.lower() / "jarvis.sqlite3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _ensure_private_parent(path: Path) -> None:
    parent = path.parent
    existed = parent.exists()
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt" and not existed:
        try:
            parent.chmod(0o700)
        except OSError:
            pass


def _nonempty(value: str, label: str, max_length: int = 200) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > max_length:
        raise ValueError(f"{label} must contain 1 to {max_length} characters")
    return cleaned


def _as_enum(value: str | Enum, enum_type: type[Enum], label: str) -> str:
    try:
        return enum_type(value).value  # type: ignore[call-arg, attr-defined]
    except (TypeError, ValueError):
        raise ValueError(f"unsupported {label}: {value!r}") from None


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction()) if callable(is_junction) else False


def _canonical_directory(path: str | Path) -> Path:
    raw = Path(path).expanduser()
    try:
        if _is_link_or_junction(raw):
            raise InvalidPath("an approved root cannot itself be a symbolic link or junction")
        canonical = raw.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise InvalidPath(f"cannot resolve selected directory: {exc}") from exc
    if not canonical.is_dir():
        raise InvalidPath("approved root must be an existing directory")
    return canonical


class Database:
    """Versioned SQLite store for workspaces and explicit access grants."""

    def __init__(
        self,
        path: str | Path,
        migrations: Sequence[Migration] = DEFAULT_MIGRATIONS,
    ) -> None:
        self.path = Path(path).expanduser()
        self._migrations = tuple(migrations)
        versions = tuple(m.version for m in self._migrations)
        if versions != tuple(range(1, len(versions) + 1)):
            raise ValueError("migrations must be contiguous and start at version 1")

    @property
    def current_version(self) -> int:
        return self._migrations[-1].version if self._migrations else 0

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        _ensure_private_parent(self.path)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _begin(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _commit(connection: sqlite3.Connection) -> None:
        connection.commit()

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        if connection.in_transaction:
            connection.rollback()

    def initialize(self) -> None:
        """Apply all pending migrations atomically; safe to call repeatedly."""
        with self._connection() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > self.current_version:
                raise RuntimeError(
                    f"database schema {version} is newer than supported schema "
                    f"{self.current_version}"
                )
            if version == self.current_version:
                self._restrict_database_file()
                return

            self._begin(connection)
            try:
                while version < self.current_version:
                    migration = self._migrations[version]
                    if migration.version != version + 1:
                        raise RuntimeError("missing database migration")
                    for statement in migration.statements:
                        connection.execute(statement)
                    version = migration.version
                    connection.execute(f"PRAGMA user_version = {version}")
                self._commit(connection)
            except Exception:
                self._rollback(connection)
                raise
        self._restrict_database_file()

    def _restrict_database_file(self) -> None:
        if os.name != "nt":
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    @staticmethod
    def _require_access(
        connection: sqlite3.Connection,
        actor_id: str,
        workspace_id: str,
        permission: Permission,
    ) -> None:
        row = connection.execute(
            """
            SELECT 1 FROM workspace_permissions
            WHERE workspace_id = ? AND principal_id = ? AND permission = ?
            """,
            (workspace_id, actor_id, permission.value),
        ).fetchone()
        if row is None:
            # Deliberately do not distinguish a missing workspace from an
            # inaccessible one; this avoids a workspace-existence oracle.
            raise AccessDenied("workspace access denied")

    @staticmethod
    def _insert_audit(
        connection: sqlite3.Connection,
        workspace_id: str,
        actor_id: str,
        event_type: str,
        resource_type: str,
        resource_id: str,
        metadata: dict[str, str],
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events
                (workspace_id, actor_id, event_type, resource_type,
                 resource_id, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                actor_id,
                event_type,
                resource_type,
                resource_id,
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                _now(),
            ),
        )

    @staticmethod
    def _workspace(row: sqlite3.Row) -> Workspace:
        return Workspace(
            id=row["id"],
            name=row["name"],
            kind=WorkspaceKind(row["kind"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _root(row: sqlite3.Row) -> ApprovedRoot:
        return ApprovedRoot(
            id=row["id"],
            workspace_id=row["workspace_id"],
            canonical_path=row["canonical_path"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            revoked_at=row["revoked_at"],
        )

    def create_workspace(
        self,
        name: str,
        kind: WorkspaceKind,
        owner_id: str,
        workspace_id: str | None = None,
    ) -> Workspace:
        self.initialize()
        name = _nonempty(name, "workspace name")
        owner_id = _nonempty(owner_id, "owner ID")
        kind_value = _as_enum(kind, WorkspaceKind, "workspace kind")
        workspace_id = _nonempty(workspace_id or uuid4().hex, "workspace ID")
        now = _now()
        permissions = tuple(Permission)
        with self._connection() as connection:
            self._begin(connection)
            try:
                connection.execute(
                    "INSERT INTO workspaces (id, name, kind, created_at) VALUES (?, ?, ?, ?)",
                    (workspace_id, name, kind_value, now),
                )
                for permission in permissions:
                    connection.execute(
                        """
                        INSERT INTO workspace_permissions
                            (workspace_id, principal_id, permission, granted_at, granted_by)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (workspace_id, owner_id, permission.value, now, owner_id),
                    )
                self._insert_audit(
                    connection,
                    workspace_id,
                    owner_id,
                    "workspace.created",
                    "workspace",
                    workspace_id,
                    {"kind": kind_value},
                )
                self._commit(connection)
            except Exception:
                self._rollback(connection)
                raise
        return Workspace(workspace_id, name, WorkspaceKind(kind_value), now)

    def check_access(
        self, actor_id: str, workspace_id: str, permission: Permission
    ) -> bool:
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        permission_value = Permission(permission)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM workspace_permissions
                WHERE workspace_id = ? AND principal_id = ? AND permission = ?
                """,
                (workspace_id, actor_id, permission_value.value),
            ).fetchone()
            return row is not None

    def require_access(
        self, actor_id: str, workspace_id: str, permission: Permission
    ) -> None:
        if not self.check_access(actor_id, workspace_id, permission):
            raise AccessDenied("workspace access denied")

    def get_workspace(self, actor_id: str, workspace_id: str) -> Workspace:
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        with self._connection() as connection:
            self._require_access(connection, actor_id, workspace_id, Permission.READ)
            row = connection.execute(
                "SELECT id, name, kind, created_at FROM workspaces WHERE id = ?",
                (workspace_id,),
            ).fetchone()
            if row is None:
                raise AccessDenied("workspace access denied")
            return self._workspace(row)

    def list_workspaces(self, actor_id: str) -> list[Workspace]:
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT w.id, w.name, w.kind, w.created_at
                FROM workspaces AS w
                JOIN workspace_permissions AS p ON p.workspace_id = w.id
                WHERE p.principal_id = ? AND p.permission = ?
                ORDER BY w.created_at, w.id
                """,
                (actor_id, Permission.READ.value),
            ).fetchall()
            return [self._workspace(row) for row in rows]

    def grant_permission(
        self,
        actor_id: str,
        workspace_id: str,
        principal_id: str,
        permission: Permission,
    ) -> bool:
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        principal_id = _nonempty(principal_id, "principal ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        permission_value = Permission(permission)
        with self._connection() as connection:
            self._begin(connection)
            try:
                self._require_access(
                    connection, actor_id, workspace_id, Permission.MANAGE_WORKSPACE
                )
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO workspace_permissions
                        (workspace_id, principal_id, permission, granted_at, granted_by)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (workspace_id, principal_id, permission_value.value, _now(), actor_id),
                )
                changed = cursor.rowcount == 1
                if changed:
                    self._insert_audit(
                        connection=connection,
                        workspace_id=workspace_id,
                        actor_id=actor_id,
                        event_type="permission.granted",
                        resource_type="permission",
                        resource_id=principal_id,
                        metadata={"permission": permission_value.value},
                    )
                self._commit(connection)
                return changed
            except Exception:
                self._rollback(connection)
                raise

    def revoke_permission(
        self,
        actor_id: str,
        workspace_id: str,
        principal_id: str,
        permission: Permission,
    ) -> bool:
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        principal_id = _nonempty(principal_id, "principal ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        permission_value = Permission(permission)
        with self._connection() as connection:
            self._begin(connection)
            try:
                self._require_access(
                    connection, actor_id, workspace_id, Permission.MANAGE_WORKSPACE
                )
                exists = connection.execute(
                    """
                    SELECT 1 FROM workspace_permissions
                    WHERE workspace_id = ? AND principal_id = ? AND permission = ?
                    """,
                    (workspace_id, principal_id, permission_value.value),
                ).fetchone()
                if exists is None:
                    self._commit(connection)
                    return False
                if permission_value is Permission.MANAGE_WORKSPACE:
                    managers = int(
                        connection.execute(
                            """
                            SELECT COUNT(*) FROM workspace_permissions
                            WHERE workspace_id = ? AND permission = ?
                            """,
                            (workspace_id, Permission.MANAGE_WORKSPACE.value),
                        ).fetchone()[0]
                    )
                    if managers <= 1:
                        raise LastWorkspaceManager(
                            "cannot revoke the last workspace manager"
                        )
                connection.execute(
                    """
                    DELETE FROM workspace_permissions
                    WHERE workspace_id = ? AND principal_id = ? AND permission = ?
                    """,
                    (workspace_id, principal_id, permission_value.value),
                )
                self._insert_audit(
                    connection,
                    workspace_id,
                    actor_id,
                    "permission.revoked",
                    "permission",
                    principal_id,
                    {"permission": permission_value.value},
                )
                self._commit(connection)
                return True
            except Exception:
                self._rollback(connection)
                raise

    def register_approved_root(
        self, actor_id: str, workspace_id: str, path: str | Path
    ) -> ApprovedRoot:
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        with self._connection() as connection:
            self._begin(connection)
            try:
                self._require_access(
                    connection, actor_id, workspace_id, Permission.MANAGE_SOURCES
                )
                canonical = str(_canonical_directory(path))
                existing = connection.execute(
                    """
                    SELECT id, workspace_id, canonical_path, created_by,
                           created_at, revoked_at
                    FROM approved_roots
                    WHERE workspace_id = ? AND canonical_path = ? AND revoked_at IS NULL
                    ORDER BY created_at LIMIT 1
                    """,
                    (workspace_id, canonical),
                ).fetchone()
                if existing is not None:
                    self._commit(connection)
                    return self._root(existing)

                root_id = uuid4().hex
                created_at = _now()
                connection.execute(
                    """
                    INSERT INTO approved_roots
                        (id, workspace_id, canonical_path, created_by, created_at, revoked_at)
                    VALUES (?, ?, ?, ?, ?, NULL)
                    """,
                    (root_id, workspace_id, canonical, actor_id, created_at),
                )
                # The path itself is deliberately omitted from the audit record.
                self._insert_audit(
                    connection,
                    workspace_id,
                    actor_id,
                    "source_root.registered",
                    "approved_root",
                    root_id,
                    {},
                )
                self._commit(connection)
                return ApprovedRoot(
                    root_id, workspace_id, canonical, actor_id, created_at, None
                )
            except Exception:
                self._rollback(connection)
                raise

    def revoke_approved_root(
        self, actor_id: str, workspace_id: str, root_id: str
    ) -> bool:
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        root_id = _nonempty(root_id, "root ID")
        with self._connection() as connection:
            self._begin(connection)
            try:
                self._require_access(
                    connection, actor_id, workspace_id, Permission.MANAGE_SOURCES
                )
                row = connection.execute(
                    """
                    SELECT id FROM approved_roots
                    WHERE id = ? AND workspace_id = ? AND revoked_at IS NULL
                    """,
                    (root_id, workspace_id),
                ).fetchone()
                if row is None:
                    self._commit(connection)
                    return False
                revoked_at = _now()
                connection.execute(
                    "UPDATE approved_roots SET revoked_at = ? WHERE id = ?",
                    (revoked_at, root_id),
                )
                self._insert_audit(
                    connection,
                    workspace_id,
                    actor_id,
                    "source_root.revoked",
                    "approved_root",
                    root_id,
                    {},
                )
                self._commit(connection)
                return True
            except Exception:
                self._rollback(connection)
                raise

    def list_approved_roots(
        self, actor_id: str, workspace_id: str, include_revoked: bool = False
    ) -> list[ApprovedRoot]:
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        with self._connection() as connection:
            self._require_access(connection, actor_id, workspace_id, Permission.READ)
            query = """
                SELECT id, workspace_id, canonical_path, created_by, created_at, revoked_at
                FROM approved_roots WHERE workspace_id = ?
            """
            if not include_revoked:
                query += " AND revoked_at IS NULL"
            query += " ORDER BY created_at, id"
            rows = connection.execute(query, (workspace_id,)).fetchall()
            return [self._root(row) for row in rows]

    def resolve_approved_path(
        self,
        actor_id: str,
        workspace_id: str,
        root_id: str,
        candidate_path: str | Path,
    ) -> Path:
        """Resolve an existing path only if it remains inside an active root."""
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        root_id = _nonempty(root_id, "root ID")
        with self._connection() as connection:
            self._require_access(connection, actor_id, workspace_id, Permission.READ)
            row = connection.execute(
                """
                SELECT canonical_path FROM approved_roots
                WHERE id = ? AND workspace_id = ? AND revoked_at IS NULL
                """,
                (root_id, workspace_id),
            ).fetchone()
            if row is None:
                raise AccessDenied("workspace access denied")
            root = Path(row["canonical_path"])
        try:
            candidate = Path(candidate_path).expanduser().resolve(strict=True)
            candidate.relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise InvalidPath("path is outside the approved root or cannot be resolved") from exc
        return candidate

    def audit_events(self, actor_id: str, workspace_id: str) -> list[dict[str, object]]:
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        with self._connection() as connection:
            self._require_access(
                connection, actor_id, workspace_id, Permission.MANAGE_WORKSPACE
            )
            rows = connection.execute(
                """
                SELECT id, workspace_id, actor_id, event_type, resource_type,
                       resource_id, metadata_json, created_at
                FROM audit_events WHERE workspace_id = ? ORDER BY id
                """,
                (workspace_id,),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "workspace_id": row["workspace_id"],
                    "actor_id": row["actor_id"],
                    "event_type": row["event_type"],
                    "resource_type": row["resource_type"],
                    "resource_id": row["resource_id"],
                    "metadata": json.loads(row["metadata_json"]),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def _require_full_database_manager(
        self, connection: sqlite3.Connection, actor_id: str
    ) -> None:
        missing = connection.execute(
            """
            SELECT 1 FROM workspaces AS w
            WHERE NOT EXISTS (
                SELECT 1 FROM workspace_permissions AS p
                WHERE p.workspace_id = w.id AND p.principal_id = ?
                  AND p.permission = ?
            )
            LIMIT 1
            """,
            (actor_id, Permission.MANAGE_WORKSPACE.value),
        ).fetchone()
        if missing is not None:
            raise AccessDenied("workspace access denied")

    def backup_to(self, destination: str | Path, actor_id: str) -> Path:
        """Create an online SQLite backup if actor manages every workspace."""
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        destination = Path(destination).expanduser()
        if destination.resolve() == self.path.resolve():
            raise ValueError("backup destination must differ from the database")
        if destination.exists():
            raise FileExistsError(f"backup destination already exists: {destination}")
        _ensure_private_parent(destination)
        with self._connection() as source:
            self._require_full_database_manager(source, actor_id)
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
        if os.name != "nt":
            try:
                destination.chmod(0o600)
            except OSError:
                pass
        return destination

    @staticmethod
    def restore_from_backup(
        backup_path: str | Path, destination: str | Path
    ) -> Path:
        """Restore a checked SQLite backup to a destination using a temp file.

        This is an offline maintenance operation. Close all application
        connections before replacing an existing destination.
        """
        source_path = Path(backup_path).expanduser().resolve(strict=True)
        destination_path = Path(destination).expanduser()
        if source_path == destination_path.resolve():
            raise ValueError("restore source and destination must differ")
        _ensure_private_parent(destination_path)
        temporary = destination_path.with_name(destination_path.name + ".restore-tmp")
        if temporary.exists():
            raise FileExistsError(f"restore temporary file already exists: {temporary}")

        source = sqlite3.connect(source_path)
        target = sqlite3.connect(temporary)
        try:
            integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise sqlite3.DatabaseError(f"backup integrity check failed: {integrity}")
            source_version = int(source.execute("PRAGMA user_version").fetchone()[0])
            if source_version < 1 or source_version > SCHEMA_VERSION:
                raise sqlite3.DatabaseError(
                    f"unsupported backup schema version: {source_version}"
                )
            for table, required_columns in REQUIRED_BACKUP_COLUMNS.items():
                columns = {
                    row[1]
                    for row in source.execute(f"PRAGMA table_info({table})").fetchall()
                }
                if not required_columns <= columns:
                    raise sqlite3.DatabaseError(
                        f"backup is missing required schema for table {table}"
                    )
            if source.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise sqlite3.DatabaseError("backup contains foreign key violations")
            source.backup(target)
        except Exception:
            target.close()
            source.close()
            for suffix in ("", "-journal", "-wal", "-shm"):
                try:
                    Path(str(temporary) + suffix).unlink()
                except FileNotFoundError:
                    pass
            raise
        else:
            target.close()
            source.close()
        if destination_path.exists():
            current = sqlite3.connect(destination_path)
            try:
                current.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                current.close()
            for suffix in ("-wal", "-shm"):
                try:
                    Path(str(destination_path) + suffix).unlink()
                except FileNotFoundError:
                    pass
        if os.name != "nt":
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
        try:
            os.replace(temporary, destination_path)
        except OSError:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise
        return destination_path
