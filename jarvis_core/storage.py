"""SQLite persistence and workspace/source permission primitives.

This module deliberately uses only the Python standard library. Authorization
here is application-level: callers must pass the identity established by a
trusted host. Do not accept an actor ID directly from an untrusted HTTP client.
"""

from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Iterator, Protocol, Sequence
from uuid import uuid4


SCHEMA_VERSION = 18
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


class ConversationMemoryDisabled(RuntimeError):
    """Raised when persistent conversation consent is absent or revoked."""


class ConversationMemoryChanged(RuntimeError):
    """Raised when consent or saved history changes during a chat request."""


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
    Migration(
        version=3,
        statements=(
            """
            CREATE VIRTUAL TABLE document_search USING fts5(
                document_id UNINDEXED,
                workspace_id UNINDEXED,
                root_id UNINDEXED,
                relative_path UNINDEXED,
                extracted_text,
                tokenize = 'unicode61 remove_diacritics 2'
            )
            """,
            """
            INSERT INTO document_search
                (document_id, workspace_id, root_id, relative_path, extracted_text)
            SELECT d.id, d.workspace_id, d.root_id, d.relative_path, d.extracted_text
            FROM ingested_documents AS d
            JOIN approved_roots AS r ON r.id = d.root_id
            WHERE d.deleted_at IS NULL AND r.revoked_at IS NULL
            """,
            """
            CREATE TRIGGER ingested_documents_search_insert
            AFTER INSERT ON ingested_documents
            WHEN NEW.deleted_at IS NULL AND EXISTS (
                SELECT 1 FROM approved_roots
                WHERE id = NEW.root_id AND workspace_id = NEW.workspace_id
                  AND revoked_at IS NULL
            )
            BEGIN
                INSERT INTO document_search
                    (document_id, workspace_id, root_id, relative_path, extracted_text)
                VALUES
                    (NEW.id, NEW.workspace_id, NEW.root_id, NEW.relative_path,
                     NEW.extracted_text);
            END
            """,
            """
            CREATE TRIGGER ingested_documents_search_update
            AFTER UPDATE OF id, workspace_id, root_id, relative_path,
                            extracted_text, deleted_at ON ingested_documents
            WHEN OLD.id IS NOT NEW.id
              OR OLD.workspace_id IS NOT NEW.workspace_id
              OR OLD.root_id IS NOT NEW.root_id
              OR OLD.relative_path IS NOT NEW.relative_path
              OR OLD.extracted_text IS NOT NEW.extracted_text
              OR OLD.deleted_at IS NOT NEW.deleted_at
            BEGIN
                DELETE FROM document_search WHERE document_id = OLD.id;
                INSERT INTO document_search
                    (document_id, workspace_id, root_id, relative_path, extracted_text)
                SELECT NEW.id, NEW.workspace_id, NEW.root_id, NEW.relative_path,
                       NEW.extracted_text
                WHERE NEW.deleted_at IS NULL AND EXISTS (
                    SELECT 1 FROM approved_roots
                    WHERE id = NEW.root_id AND workspace_id = NEW.workspace_id
                      AND revoked_at IS NULL
                );
            END
            """,
            """
            CREATE TRIGGER ingested_documents_search_delete
            AFTER DELETE ON ingested_documents
            BEGIN
                DELETE FROM document_search WHERE document_id = OLD.id;
            END
            """,
            """
            CREATE TRIGGER approved_roots_search_status
            AFTER UPDATE OF revoked_at ON approved_roots
            WHEN OLD.revoked_at IS NOT NEW.revoked_at
            BEGIN
                DELETE FROM document_search WHERE root_id = NEW.id;
                INSERT INTO document_search
                    (document_id, workspace_id, root_id, relative_path, extracted_text)
                SELECT d.id, d.workspace_id, d.root_id, d.relative_path, d.extracted_text
                FROM ingested_documents AS d
                WHERE d.root_id = NEW.id AND d.deleted_at IS NULL
                  AND NEW.revoked_at IS NULL;
            END
            """,
            """
            CREATE TRIGGER approved_roots_search_delete
            BEFORE DELETE ON approved_roots
            BEGIN
                DELETE FROM document_search WHERE root_id = OLD.id;
            END
            """,
        ),
    ),
    Migration(
        version=4,
        statements=(
            """CREATE TABLE memory_candidates (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                proposed_by TEXT NOT NULL,
                request_key TEXT,
                provenance_kind TEXT NOT NULL CHECK(provenance_kind IN ('evidence','user_authored')),
                content TEXT,
                content_sha256 TEXT NOT NULL,
                evidence_sha256 TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
                state TEXT NOT NULL CHECK(state IN ('pending','approved','rejected')),
                approved_memory_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                decided_at TEXT,
                UNIQUE(id, workspace_id),
                UNIQUE(workspace_id, proposed_by, request_key),
                CHECK((state = 'pending' AND content IS NOT NULL) OR
                      (state != 'pending' AND content IS NULL))
            )""",
            "CREATE INDEX memory_candidates_pending ON memory_candidates(workspace_id,state,created_at,id)",
            """CREATE TABLE memory_candidate_evidence (
                candidate_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                root_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                PRIMARY KEY(candidate_id, document_id),
                FOREIGN KEY(candidate_id,workspace_id) REFERENCES memory_candidates(id,workspace_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                source_candidate_id TEXT NOT NULL UNIQUE,
                content TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                provenance_kind TEXT NOT NULL CHECK(provenance_kind IN ('evidence','user_authored')),
                created_by TEXT NOT NULL,
                approved_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
                state_version INTEGER NOT NULL DEFAULT 1 CHECK(state_version > 0),
                deleted_at TEXT,
                UNIQUE(id,workspace_id),
                FOREIGN KEY(source_candidate_id,workspace_id) REFERENCES memory_candidates(id,workspace_id)
            )""",
            "CREATE INDEX memories_active ON memories(workspace_id,deleted_at,updated_at,id)",
            """CREATE TABLE memory_evidence (
                memory_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                root_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                PRIMARY KEY(memory_id,document_id),
                FOREIGN KEY(memory_id,workspace_id) REFERENCES memories(id,workspace_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE memory_revisions (
                memory_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                content TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                provenance_kind TEXT NOT NULL CHECK(provenance_kind IN ('evidence','user_authored')),
                edited_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(memory_id,revision),
                FOREIGN KEY(memory_id,workspace_id) REFERENCES memories(id,workspace_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE memory_revision_evidence (
                memory_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                document_id TEXT NOT NULL,
                root_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                PRIMARY KEY(memory_id,revision,document_id),
                FOREIGN KEY(memory_id,revision) REFERENCES memory_revisions(memory_id,revision) ON DELETE CASCADE
            )""",
            """CREATE TABLE memory_decision_receipts (
                decision_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                context_sha256 TEXT NOT NULL,
                consumed_at TEXT NOT NULL
            )""",
        ),
    ),
    Migration(
        version=5,
        statements=(
            """CREATE VIRTUAL TABLE memory_search USING fts5(
                memory_id UNINDEXED,
                workspace_id UNINDEXED,
                content,
                tokenize = 'unicode61 remove_diacritics 2'
            )""",
            """INSERT INTO memory_search (memory_id, workspace_id, content)
                SELECT id, workspace_id, content FROM memories
                WHERE deleted_at IS NULL""",
            """CREATE TRIGGER memories_search_insert
                AFTER INSERT ON memories
                WHEN NEW.deleted_at IS NULL
                BEGIN
                    INSERT INTO memory_search (memory_id, workspace_id, content)
                    VALUES (NEW.id, NEW.workspace_id, NEW.content);
                END""",
            """CREATE TRIGGER memories_search_update
                AFTER UPDATE OF id, workspace_id, content, deleted_at ON memories
                WHEN OLD.id IS NOT NEW.id
                  OR OLD.workspace_id IS NOT NEW.workspace_id
                  OR OLD.content IS NOT NEW.content
                  OR OLD.deleted_at IS NOT NEW.deleted_at
                BEGIN
                    DELETE FROM memory_search WHERE memory_id = OLD.id;
                    INSERT INTO memory_search (memory_id, workspace_id, content)
                    SELECT NEW.id, NEW.workspace_id, NEW.content
                    WHERE NEW.deleted_at IS NULL;
                END""",
            """CREATE TRIGGER memories_search_delete
                AFTER DELETE ON memories
                BEGIN
                    DELETE FROM memory_search WHERE memory_id = OLD.id;
                END""",
        ),
    ),
    Migration(
        version=6,
        statements=(
            """CREATE TABLE developer_repositories (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                approved_root_id TEXT NOT NULL REFERENCES approved_roots(id) ON DELETE CASCADE,
                relative_path TEXT NOT NULL,
                name TEXT NOT NULL,
                registered_by TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                revoked_at TEXT,
                head_commit TEXT,
                branch_name TEXT,
                indexed_at TEXT,
                index_status TEXT NOT NULL DEFAULT 'not_indexed'
                    CHECK(index_status IN ('not_indexed','ready','stale','failed')),
                UNIQUE(id, workspace_id),
                UNIQUE(approved_root_id, relative_path)
            )""",
            "CREATE INDEX developer_repositories_workspace ON developer_repositories(workspace_id,revoked_at,name,id)",
            """CREATE TABLE developer_files (
                id TEXT PRIMARY KEY,
                repository_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                extension TEXT NOT NULL,
                language TEXT NOT NULL,
                git_blob_oid TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 0 AND 1048576),
                content TEXT NOT NULL,
                symbols_json TEXT NOT NULL DEFAULT '[]',
                imports_json TEXT NOT NULL DEFAULT '[]',
                FOREIGN KEY(repository_id,workspace_id) REFERENCES developer_repositories(id,workspace_id) ON DELETE CASCADE,
                UNIQUE(repository_id,relative_path)
            )""",
            "CREATE INDEX developer_files_repo_path ON developer_files(repository_id,commit_sha,relative_path)",
            """CREATE VIRTUAL TABLE developer_file_search USING fts5(
                workspace_id UNINDEXED, repository_id UNINDEXED, file_id UNINDEXED,
                commit_sha UNINDEXED, relative_path, language UNINDEXED, content,
                tokenize='unicode61'
            )""",
            """CREATE TRIGGER developer_files_search_insert AFTER INSERT ON developer_files BEGIN
                INSERT INTO developer_file_search(workspace_id,repository_id,file_id,commit_sha,relative_path,language,content)
                VALUES(NEW.workspace_id,NEW.repository_id,NEW.id,NEW.commit_sha,NEW.relative_path,NEW.language,NEW.content);
            END""",
            """CREATE TRIGGER developer_files_search_delete AFTER DELETE ON developer_files BEGIN
                DELETE FROM developer_file_search WHERE file_id=OLD.id;
            END""",
            """CREATE TRIGGER developer_files_search_update AFTER UPDATE ON developer_files BEGIN
                DELETE FROM developer_file_search WHERE file_id=OLD.id;
                INSERT INTO developer_file_search(workspace_id,repository_id,file_id,commit_sha,relative_path,language,content)
                VALUES(NEW.workspace_id,NEW.repository_id,NEW.id,NEW.commit_sha,NEW.relative_path,NEW.language,NEW.content);
            END""",
        ),
    ),
    Migration(
        version=7,
        statements=(
            "ALTER TABLE developer_files ADD COLUMN analysis_status TEXT NOT NULL DEFAULT 'unknown' CHECK(analysis_status IN ('complete','partial','failed','unsupported','unknown'))",
        ),
    ),
    Migration(
        version=8,
        statements=(
            """CREATE TABLE coding_repository_grants (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                repository_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                capability TEXT NOT NULL CHECK(capability IN (
                    'read_repository_metadata','read_indexed_source','propose_code_modifications',
                    'create_isolated_worktree','modify_task_worktree','execute_verification',
                    'request_network_access','create_commit','push_branch','open_pull_request',
                    'merge_or_deploy'
                )),
                granted_by TEXT NOT NULL,
                granted_at TEXT NOT NULL,
                expires_at TEXT,
                revoked_at TEXT,
                FOREIGN KEY(repository_id,workspace_id)
                    REFERENCES developer_repositories(id,workspace_id) ON DELETE CASCADE
            )""",
            "CREATE INDEX coding_repository_grants_lookup ON coding_repository_grants(workspace_id,repository_id,principal_id,capability,revoked_at)",
            """CREATE TABLE coding_tasks (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                repository_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                base_commit TEXT NOT NULL CHECK(length(base_commit) IN (40,64)),
                request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
                manifest_json TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256)=64),
                status TEXT NOT NULL CHECK(status IN (
                    'requested','proposed','approved','rejected','cancelled','interrupted','stale'
                )),
                proposal_json TEXT,
                diff_sha256 TEXT,
                approved_diff_sha256 TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                UNIQUE(id,workspace_id,principal_id),
                FOREIGN KEY(repository_id,workspace_id)
                    REFERENCES developer_repositories(id,workspace_id) ON DELETE CASCADE
            )""",
            "CREATE INDEX coding_tasks_workspace_status ON coding_tasks(workspace_id,status,updated_at)",
            """CREATE TABLE coding_task_events (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                principal_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                snapshot_sha256 TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id,workspace_id,principal_id)
                    REFERENCES coding_tasks(id,workspace_id,principal_id) ON DELETE CASCADE
            )""",
            "CREATE INDEX coding_task_events_task ON coding_task_events(task_id,created_at,id)",
            """CREATE TABLE coding_task_decision_receipts (
                decision_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('approve_proposal','reject_proposal')),
                snapshot_sha256 TEXT NOT NULL CHECK(length(snapshot_sha256)=64),
                consumed_at TEXT NOT NULL,
                FOREIGN KEY(task_id,workspace_id,principal_id)
                    REFERENCES coding_tasks(id,workspace_id,principal_id) ON DELETE CASCADE
            )""",
        ),
    ),
    Migration(
        version=9,
        statements=(
            """CREATE TABLE orchestration_tasks (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                principal_id TEXT NOT NULL,
                repository_id TEXT,
                repository_commit TEXT,
                goal TEXT NOT NULL CHECK(length(trim(goal)) BETWEEN 1 AND 4000),
                goal_sha256 TEXT NOT NULL CHECK(length(goal_sha256)=64),
                state TEXT NOT NULL CHECK(state IN (
                    'draft','awaiting_approval','queued','simulating',
                    'simulation_completed','blocked','completed','failed',
                    'cancelled','interrupted','stale'
                )),
                execution_mode TEXT NOT NULL CHECK(execution_mode IN ('planned','simulated')),
                requested_capabilities_json TEXT NOT NULL,
                capability_manifest_json TEXT NOT NULL,
                capability_manifest_sha256 TEXT NOT NULL CHECK(length(capability_manifest_sha256)=64),
                current_plan_revision INTEGER NOT NULL DEFAULT 0 CHECK(current_plan_revision>=0),
                approved_plan_revision INTEGER,
                result_summary TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                UNIQUE(id,workspace_id,principal_id),
                CHECK((repository_id IS NULL AND repository_commit IS NULL) OR
                      (repository_id IS NOT NULL AND repository_commit IS NOT NULL AND
                       length(repository_commit) IN (40,64))),
                FOREIGN KEY(repository_id,workspace_id)
                    REFERENCES developer_repositories(id,workspace_id) ON DELETE CASCADE
            )""",
            "CREATE INDEX orchestration_tasks_workspace_state ON orchestration_tasks(workspace_id,state,updated_at,id)",
            """CREATE TABLE orchestration_plans (
                task_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision>0),
                plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256)=64),
                state TEXT NOT NULL CHECK(state IN ('awaiting_approval','approved','rejected','superseded')),
                created_at TEXT NOT NULL,
                reviewed_by TEXT,
                reviewed_at TEXT,
                PRIMARY KEY(task_id,revision),
                UNIQUE(task_id,workspace_id,principal_id,revision),
                FOREIGN KEY(task_id,workspace_id,principal_id)
                    REFERENCES orchestration_tasks(id,workspace_id,principal_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE orchestration_subtasks (
                task_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                subtask_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 0 AND 24),
                title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 120),
                goal TEXT NOT NULL CHECK(length(trim(goal)) BETWEEN 1 AND 1000),
                acceptance_condition TEXT NOT NULL CHECK(length(trim(acceptance_condition)) BETWEEN 1 AND 300),
                required_capabilities_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN (
                    'pending','blocked','simulating','simulated_completed','simulated_failed','cancelled'
                )),
                progress_percent INTEGER NOT NULL DEFAULT 0 CHECK(progress_percent BETWEEN 0 AND 100),
                result_summary TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(task_id,revision,subtask_id),
                UNIQUE(task_id,revision,ordinal),
                UNIQUE(task_id,workspace_id,principal_id,revision,subtask_id),
                FOREIGN KEY(task_id,workspace_id,principal_id,revision)
                    REFERENCES orchestration_plans(task_id,workspace_id,principal_id,revision) ON DELETE CASCADE
            )""",
            """CREATE TABLE orchestration_dependencies (
                task_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                subtask_id TEXT NOT NULL,
                depends_on_subtask_id TEXT NOT NULL,
                PRIMARY KEY(task_id,revision,subtask_id,depends_on_subtask_id),
                CHECK(subtask_id<>depends_on_subtask_id),
                FOREIGN KEY(task_id,workspace_id,principal_id,revision,subtask_id)
                    REFERENCES orchestration_subtasks(task_id,workspace_id,principal_id,revision,subtask_id) ON DELETE CASCADE,
                FOREIGN KEY(task_id,workspace_id,principal_id,revision,depends_on_subtask_id)
                    REFERENCES orchestration_subtasks(task_id,workspace_id,principal_id,revision,subtask_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE orchestration_approvals (
                decision_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('approve_plan','reject_plan')),
                plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256)=64),
                idempotency_key TEXT NOT NULL,
                decided_by TEXT NOT NULL,
                decided_at TEXT NOT NULL,
                UNIQUE(workspace_id,decided_by,idempotency_key),
                FOREIGN KEY(task_id,workspace_id,principal_id,revision)
                    REFERENCES orchestration_plans(task_id,workspace_id,principal_id,revision) ON DELETE CASCADE
            )""",
            """CREATE TABLE orchestration_task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                plan_revision INTEGER,
                snapshot_sha256 TEXT,
                idempotency_key TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(workspace_id,actor_id,idempotency_key),
                FOREIGN KEY(task_id,workspace_id,principal_id)
                    REFERENCES orchestration_tasks(id,workspace_id,principal_id) ON DELETE CASCADE
            )""",
            "CREATE INDEX orchestration_task_events_task ON orchestration_task_events(task_id,created_at,id)",
            """CREATE TRIGGER orchestration_task_events_no_update
                BEFORE UPDATE ON orchestration_task_events
                BEGIN SELECT RAISE(ABORT, 'orchestration events are append-only'); END""",
            """CREATE TRIGGER orchestration_task_events_no_delete
                BEFORE DELETE ON orchestration_task_events
                BEGIN SELECT RAISE(ABORT, 'orchestration events are append-only'); END""",
            """CREATE TRIGGER orchestration_approvals_no_update
                BEFORE UPDATE ON orchestration_approvals
                BEGIN SELECT RAISE(ABORT, 'orchestration approvals are immutable'); END""",
            """CREATE TRIGGER orchestration_approvals_no_delete
                BEFORE DELETE ON orchestration_approvals
                BEGIN SELECT RAISE(ABORT, 'orchestration approvals are immutable'); END""",
            """CREATE TRIGGER orchestration_subtasks_plan_fields_immutable
                BEFORE UPDATE OF task_id,workspace_id,principal_id,revision,subtask_id,ordinal,title,goal,
                    acceptance_condition,required_capabilities_json ON orchestration_subtasks
                BEGIN SELECT RAISE(ABORT, 'orchestration plan revisions are immutable'); END""",
        ),
    ),
    Migration(
        version=10,
        statements=(
            "ALTER TABLE orchestration_tasks ADD COLUMN archived_at TEXT",
            "ALTER TABLE orchestration_tasks ADD COLUMN archived_by TEXT",
            "CREATE INDEX orchestration_tasks_workspace_archive ON orchestration_tasks(workspace_id,archived_at,state,updated_at,id)",
            """CREATE TABLE orchestration_retention_policies (
                workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
                completed_retention_days INTEGER CHECK(completed_retention_days IS NULL OR completed_retention_days BETWEEN 1 AND 3650),
                cancelled_retention_days INTEGER CHECK(cancelled_retention_days IS NULL OR cancelled_retention_days BETWEEN 1 AND 3650),
                failed_retention_days INTEGER CHECK(failed_retention_days IS NULL OR failed_retention_days BETWEEN 1 AND 3650),
                interrupted_retention_days INTEGER CHECK(interrupted_retention_days IS NULL OR interrupted_retention_days BETWEEN 1 AND 3650),
                auto_delete_enabled INTEGER NOT NULL DEFAULT 0 CHECK(auto_delete_enabled = 0),
                updated_by TEXT NOT NULL CHECK(length(trim(updated_by)) > 0),
                updated_at TEXT NOT NULL,
                expires_at TEXT
            )""",
            """CREATE TABLE orchestration_purge_operations (
                purge_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                actor_id TEXT NOT NULL CHECK(length(trim(actor_id)) > 0),
                idempotency_key TEXT NOT NULL,
                preview_sha256 TEXT NOT NULL CHECK(length(preview_sha256)=64),
                task_count INTEGER NOT NULL CHECK(task_count > 0),
                plan_count INTEGER NOT NULL CHECK(plan_count >= 0),
                subtask_count INTEGER NOT NULL CHECK(subtask_count >= 0),
                dependency_count INTEGER NOT NULL CHECK(dependency_count >= 0),
                approval_count INTEGER NOT NULL CHECK(approval_count >= 0),
                event_count INTEGER NOT NULL CHECK(event_count >= 0),
                purged_at TEXT NOT NULL,
                UNIQUE(workspace_id,actor_id,idempotency_key)
            )""",
            "CREATE INDEX orchestration_purge_operations_workspace ON orchestration_purge_operations(workspace_id,purged_at,purge_id)",
            """CREATE TABLE orchestration_purge_tombstones (
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                task_id TEXT NOT NULL,
                purge_id TEXT NOT NULL REFERENCES orchestration_purge_operations(purge_id),
                task_state TEXT NOT NULL,
                archived_at TEXT,
                goal_sha256 TEXT NOT NULL CHECK(length(goal_sha256)=64),
                plan_count INTEGER NOT NULL CHECK(plan_count >= 0),
                subtask_count INTEGER NOT NULL CHECK(subtask_count >= 0),
                dependency_count INTEGER NOT NULL CHECK(dependency_count >= 0),
                approval_count INTEGER NOT NULL CHECK(approval_count >= 0),
                event_count INTEGER NOT NULL CHECK(event_count >= 0),
                purged_by TEXT NOT NULL CHECK(length(trim(purged_by)) > 0),
                purged_at TEXT NOT NULL,
                PRIMARY KEY(workspace_id,task_id)
            )""",
            "CREATE INDEX orchestration_purge_tombstones_workspace ON orchestration_purge_tombstones(workspace_id,purged_at,task_id)",
            """CREATE TRIGGER orchestration_purge_operations_no_update
                BEFORE UPDATE ON orchestration_purge_operations
                BEGIN SELECT RAISE(ABORT, 'orchestration purge operations are immutable'); END""",
            """CREATE TRIGGER orchestration_purge_operations_no_delete
                BEFORE DELETE ON orchestration_purge_operations
                BEGIN SELECT RAISE(ABORT, 'orchestration purge operations are immutable'); END""",
            """CREATE TRIGGER orchestration_purge_tombstones_no_update
                BEFORE UPDATE ON orchestration_purge_tombstones
                BEGIN SELECT RAISE(ABORT, 'orchestration purge tombstones are immutable'); END""",
            """CREATE TRIGGER orchestration_purge_tombstones_no_delete
                BEFORE DELETE ON orchestration_purge_tombstones
                BEGIN SELECT RAISE(ABORT, 'orchestration purge tombstones are immutable'); END""",
            "DROP TRIGGER IF EXISTS orchestration_task_events_no_delete",
            """CREATE TRIGGER orchestration_task_events_no_delete
                BEFORE DELETE ON orchestration_task_events
                WHEN NOT EXISTS (
                    SELECT 1 FROM orchestration_purge_tombstones p
                    WHERE p.task_id = OLD.task_id AND p.workspace_id = OLD.workspace_id
                )
                BEGIN SELECT RAISE(ABORT, 'orchestration events are append-only'); END""",
            "DROP TRIGGER IF EXISTS orchestration_approvals_no_delete",
            """CREATE TRIGGER orchestration_approvals_no_delete
                BEFORE DELETE ON orchestration_approvals
                WHEN NOT EXISTS (
                    SELECT 1 FROM orchestration_purge_tombstones p
                    WHERE p.task_id = OLD.task_id AND p.workspace_id = OLD.workspace_id
                )
                BEGIN SELECT RAISE(ABORT, 'orchestration approvals are immutable'); END""",
        ),
    ),
    Migration(
        version=11,
        statements=(
            "DROP TRIGGER IF EXISTS orchestration_task_events_no_delete",
            "DROP TRIGGER IF EXISTS orchestration_approvals_no_delete",
            """CREATE TABLE orchestration_purge_tombstones_v11 (
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                task_id TEXT NOT NULL,
                purge_id TEXT NOT NULL REFERENCES orchestration_purge_operations(purge_id),
                task_state TEXT NOT NULL,
                archived_at TEXT,
                plan_count INTEGER NOT NULL CHECK(plan_count >= 0),
                subtask_count INTEGER NOT NULL CHECK(subtask_count >= 0),
                dependency_count INTEGER NOT NULL CHECK(dependency_count >= 0),
                approval_count INTEGER NOT NULL CHECK(approval_count >= 0),
                event_count INTEGER NOT NULL CHECK(event_count >= 0),
                purged_by TEXT NOT NULL CHECK(length(trim(purged_by)) > 0),
                purged_at TEXT NOT NULL,
                PRIMARY KEY(workspace_id,task_id)
            )""",
            """INSERT INTO orchestration_purge_tombstones_v11 (
                workspace_id,task_id,purge_id,task_state,archived_at,
                plan_count,subtask_count,dependency_count,approval_count,
                event_count,purged_by,purged_at
            )
            SELECT workspace_id,task_id,purge_id,task_state,archived_at,
                plan_count,subtask_count,dependency_count,approval_count,
                event_count,purged_by,purged_at
            FROM orchestration_purge_tombstones""",
            "DROP TABLE orchestration_purge_tombstones",
            "ALTER TABLE orchestration_purge_tombstones_v11 RENAME TO orchestration_purge_tombstones",
            "CREATE INDEX orchestration_purge_tombstones_workspace ON orchestration_purge_tombstones(workspace_id,purged_at,task_id)",
            """CREATE TRIGGER orchestration_purge_tombstones_no_update
                BEFORE UPDATE ON orchestration_purge_tombstones
                BEGIN SELECT RAISE(ABORT, 'orchestration purge tombstones are immutable'); END""",
            """CREATE TRIGGER orchestration_purge_tombstones_no_delete
                BEFORE DELETE ON orchestration_purge_tombstones
                BEGIN SELECT RAISE(ABORT, 'orchestration purge tombstones are immutable'); END""",
            """CREATE TRIGGER orchestration_task_events_no_delete
                BEFORE DELETE ON orchestration_task_events
                WHEN NOT EXISTS (
                    SELECT 1 FROM orchestration_purge_tombstones p
                    WHERE p.task_id = OLD.task_id AND p.workspace_id = OLD.workspace_id
                )
                BEGIN SELECT RAISE(ABORT, 'orchestration events are append-only'); END""",
            """CREATE TRIGGER orchestration_approvals_no_delete
                BEFORE DELETE ON orchestration_approvals
                WHEN NOT EXISTS (
                    SELECT 1 FROM orchestration_purge_tombstones p
                    WHERE p.task_id = OLD.task_id AND p.workspace_id = OLD.workspace_id
                )
                BEGIN SELECT RAISE(ABORT, 'orchestration approvals are immutable'); END""",
        ),
    ),
    Migration(
        version=12,
        statements=(
            """CREATE TABLE conversation_memory_settings (
                actor_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                retention_days INTEGER NOT NULL CHECK(retention_days BETWEEN 1 AND 365),
                revision INTEGER NOT NULL CHECK(revision >= 1),
                consented_at TEXT,
                revoked_at TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(actor_id,workspace_id),
                CHECK((enabled=1 AND consented_at IS NOT NULL AND revoked_at IS NULL)
                   OR (enabled=0 AND revoked_at IS NOT NULL))
            )""",
            """CREATE TABLE persistent_conversations (
                id TEXT PRIMARY KEY,
                actor_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                UNIQUE(id,actor_id,workspace_id)
            )""",
            """CREATE TABLE persistent_conversation_turns (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
                user_text TEXT NOT NULL CHECK(length(user_text) BETWEEN 1 AND 1000),
                assistant_text TEXT NOT NULL CHECK(length(assistant_text) BETWEEN 1 AND 16000),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                UNIQUE(conversation_id,ordinal),
                FOREIGN KEY(conversation_id,actor_id,workspace_id)
                    REFERENCES persistent_conversations(id,actor_id,workspace_id) ON DELETE CASCADE
            )""",
            "CREATE INDEX persistent_conversations_scope_expiry ON persistent_conversations(actor_id,workspace_id,expires_at,updated_at DESC,id)",
            "CREATE INDEX persistent_conversation_turns_scope_order ON persistent_conversation_turns(actor_id,workspace_id,expires_at,created_at DESC,id)",
        ),
    ),
    Migration(
        version=13,
        statements=(
            "ALTER TABLE developer_files ADD COLUMN references_json TEXT NOT NULL DEFAULT '[]'",
            "UPDATE developer_files SET analysis_status='unknown'",
            "UPDATE developer_repositories SET index_status='stale' WHERE head_commit IS NOT NULL",
        ),
    ),
    Migration(
        version=14,
        statements=(
            "ALTER TABLE coding_repository_grants RENAME TO coding_repository_grants_v13",
            "DROP INDEX IF EXISTS coding_repository_grants_lookup",
            """CREATE TABLE coding_repository_grants (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, repository_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                capability TEXT NOT NULL CHECK(capability IN (
                    'read_repository_metadata','read_indexed_source','propose_code_modifications',
                    'apply_approved_file_changes','create_isolated_worktree','modify_task_worktree',
                    'execute_verification','request_network_access','create_commit','push_branch',
                    'open_pull_request','merge_or_deploy')),
                granted_by TEXT NOT NULL, granted_at TEXT NOT NULL, expires_at TEXT, revoked_at TEXT,
                FOREIGN KEY(repository_id,workspace_id)
                    REFERENCES developer_repositories(id,workspace_id) ON DELETE CASCADE
            )""",
            "INSERT INTO coding_repository_grants SELECT * FROM coding_repository_grants_v13",
            "DROP TABLE coding_repository_grants_v13",
            "CREATE INDEX coding_repository_grants_lookup ON coding_repository_grants(workspace_id,repository_id,principal_id,capability,revoked_at)",
            """CREATE TABLE coding_file_change_proposals (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, repository_id TEXT NOT NULL,
                principal_id TEXT NOT NULL, base_commit TEXT NOT NULL CHECK(length(base_commit) IN (40,64)),
                patch_sha256 TEXT NOT NULL CHECK(length(patch_sha256)=64), proposal_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN (
                    'pending_approval','applying','applied','rejected','failed','recovery_required','recovered','expired')),
                approved_by TEXT, approved_at TEXT, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(id,workspace_id,repository_id,principal_id),
                FOREIGN KEY(repository_id,workspace_id)
                    REFERENCES developer_repositories(id,workspace_id) ON DELETE CASCADE
            )""",
            "CREATE INDEX coding_file_change_workspace ON coding_file_change_proposals(workspace_id,repository_id,status,created_at)",
        ),
    ),
    Migration(
        version=15,
        statements=(
            """CREATE TABLE missions (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN (
                    'draft','awaiting_authorization','authorized','running','paused',
                    'blocked','interrupted','completed','cancelled','expired')),
                plan_revision INTEGER NOT NULL DEFAULT 0 CHECK(plan_revision >= 0),
                authorized_revision INTEGER,
                authorization_sha256 TEXT CHECK(authorization_sha256 IS NULL OR length(authorization_sha256)=64),
                max_steps INTEGER NOT NULL CHECK(max_steps BETWEEN 1 AND 25),
                max_requests INTEGER NOT NULL CHECK(max_requests BETWEEN 1 AND 100),
                max_retries INTEGER NOT NULL CHECK(max_retries BETWEEN 0 AND 10),
                max_duration_seconds INTEGER NOT NULL CHECK(max_duration_seconds BETWEEN 1 AND 2592000),
                steps_used INTEGER NOT NULL DEFAULT 0 CHECK(steps_used >= 0),
                requests_used INTEGER NOT NULL DEFAULT 0 CHECK(requests_used >= 0),
                retries_used INTEGER NOT NULL DEFAULT 0 CHECK(retries_used >= 0),
                started_at TEXT,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT,
                archived_by TEXT,
                UNIQUE(id,workspace_id,principal_id),
                CHECK(steps_used <= max_steps AND requests_used <= max_requests
                    AND retries_used <= max_retries),
                CHECK((authorized_revision IS NULL AND authorization_sha256 IS NULL)
                    OR (authorized_revision IS NOT NULL AND authorization_sha256 IS NOT NULL)),
                CHECK((archived_at IS NULL AND archived_by IS NULL)
                    OR (archived_at IS NOT NULL AND archived_by IS NOT NULL)),
                FOREIGN KEY(task_id,workspace_id,principal_id)
                    REFERENCES orchestration_tasks(id,workspace_id,principal_id) ON DELETE CASCADE
            )""",
            "CREATE INDEX missions_scope_state ON missions(workspace_id,principal_id,state,updated_at,id)",
            """CREATE TABLE mission_authorizations (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                plan_revision INTEGER NOT NULL CHECK(plan_revision > 0),
                plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256)=64),
                budget_sha256 TEXT NOT NULL CHECK(length(budget_sha256)=64),
                capability_set_json TEXT NOT NULL CHECK(capability_set_json='[\"simulate\"]'),
                authorization_sha256 TEXT NOT NULL CHECK(length(authorization_sha256)=64),
                approved_by TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(workspace_id,approved_by,idempotency_key),
                UNIQUE(mission_id,plan_revision,authorization_sha256),
                FOREIGN KEY(mission_id,workspace_id,principal_id)
                    REFERENCES missions(id,workspace_id,principal_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE mission_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK(event_type IN (
                    'mission_created','plan_linked','mission_authorized','mission_rejected',
                    'mission_paused','mission_resumed','mission_cancelled','mission_interrupted',
                    'mission_simulation_step','mission_completed','mission_archived','mission_restored')),
                plan_revision INTEGER,
                snapshot_sha256 TEXT,
                idempotency_key TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(workspace_id,actor_id,idempotency_key),
                FOREIGN KEY(mission_id,workspace_id,principal_id)
                    REFERENCES missions(id,workspace_id,principal_id) ON DELETE CASCADE
            )""",
            "CREATE INDEX mission_events_scope ON mission_events(mission_id,created_at,id)",
            """CREATE TABLE mission_budget_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                operation_key TEXT NOT NULL,
                request_delta INTEGER NOT NULL CHECK(request_delta BETWEEN 0 AND 1),
                step_delta INTEGER NOT NULL CHECK(step_delta BETWEEN 0 AND 1),
                retry_delta INTEGER NOT NULL CHECK(retry_delta BETWEEN 0 AND 1),
                outcome TEXT NOT NULL CHECK(outcome IN ('reserved','consumed','denied','failed')),
                created_at TEXT NOT NULL,
                UNIQUE(mission_id,operation_key,outcome),
                FOREIGN KEY(mission_id,workspace_id,principal_id)
                    REFERENCES missions(id,workspace_id,principal_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE mission_checkpoints (
                mission_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                sequence INTEGER NOT NULL CHECK(sequence > 0),
                plan_revision INTEGER NOT NULL CHECK(plan_revision > 0),
                progress_sha256 TEXT NOT NULL CHECK(length(progress_sha256)=64),
                progress_json TEXT NOT NULL CHECK(length(progress_json) <= 12000),
                created_at TEXT NOT NULL,
                PRIMARY KEY(mission_id,sequence),
                FOREIGN KEY(mission_id,workspace_id,principal_id)
                    REFERENCES missions(id,workspace_id,principal_id) ON DELETE CASCADE
            )""",
            """CREATE TRIGGER mission_authorizations_no_update BEFORE UPDATE ON mission_authorizations
                BEGIN SELECT RAISE(ABORT, 'mission authorizations are immutable'); END""",
            """CREATE TRIGGER mission_authorizations_no_delete BEFORE DELETE ON mission_authorizations
                WHEN EXISTS (SELECT 1 FROM missions WHERE id=OLD.mission_id)
                BEGIN SELECT RAISE(ABORT, 'mission authorizations are immutable'); END""",
            """CREATE TRIGGER mission_events_no_update BEFORE UPDATE ON mission_events
                BEGIN SELECT RAISE(ABORT, 'mission events are append-only'); END""",
            """CREATE TRIGGER mission_events_no_delete BEFORE DELETE ON mission_events
                WHEN EXISTS (SELECT 1 FROM missions WHERE id=OLD.mission_id)
                BEGIN SELECT RAISE(ABORT, 'mission events are append-only'); END""",
            """CREATE TRIGGER mission_checkpoints_no_update BEFORE UPDATE ON mission_checkpoints
                BEGIN SELECT RAISE(ABORT, 'mission checkpoints are immutable'); END""",
            """CREATE TRIGGER mission_checkpoints_no_delete BEFORE DELETE ON mission_checkpoints
                WHEN EXISTS (SELECT 1 FROM missions WHERE id=OLD.mission_id)
                BEGIN SELECT RAISE(ABORT, 'mission checkpoints are immutable'); END""",
            """CREATE TRIGGER mission_ledger_no_update BEFORE UPDATE ON mission_budget_ledger
                BEGIN SELECT RAISE(ABORT, 'mission budget ledger is append-only'); END""",
            """CREATE TRIGGER mission_ledger_no_delete BEFORE DELETE ON mission_budget_ledger
                WHEN EXISTS (SELECT 1 FROM missions WHERE id=OLD.mission_id)
                BEGIN SELECT RAISE(ABORT, 'mission budget ledger is append-only'); END""",
            """CREATE TRIGGER mission_ledger_no_auto_resume BEFORE INSERT ON mission_budget_ledger
                WHEN NEW.outcome NOT IN ('reserved','consumed','denied','failed')
                BEGIN SELECT RAISE(ABORT, 'invalid mission budget event'); END""",
        ),
    ),
    Migration(
        version=16,
        statements=(
            """CREATE TABLE mission_planning_reservations (
                workspace_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                mission_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
                state TEXT NOT NULL CHECK(state IN ('in_progress','interrupted','completed')),
                owner_token TEXT,
                lease_expires_at TEXT,
                attempts_used INTEGER NOT NULL DEFAULT 0 CHECK(attempts_used >= 0),
                result_revision INTEGER,
                result_sha256 TEXT CHECK(result_sha256 IS NULL OR length(result_sha256)=64),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(workspace_id,principal_id,mission_id,idempotency_key),
                FOREIGN KEY(mission_id,workspace_id,principal_id)
                    REFERENCES missions(id,workspace_id,principal_id) ON DELETE CASCADE,
                CHECK((state='in_progress' AND owner_token IS NOT NULL AND lease_expires_at IS NOT NULL
                       AND result_revision IS NULL AND result_sha256 IS NULL)
                   OR (state='interrupted' AND owner_token IS NULL AND lease_expires_at IS NULL
                       AND result_revision IS NULL AND result_sha256 IS NULL)
                   OR (state='completed' AND owner_token IS NULL AND lease_expires_at IS NULL
                       AND result_revision IS NOT NULL AND result_sha256 IS NOT NULL))
            )""",
            "CREATE INDEX mission_planning_reservations_state ON mission_planning_reservations(state,lease_expires_at)",
        ),
    ),)

DEFAULT_MIGRATIONS = (*DEFAULT_MIGRATIONS, Migration(
    version=17,
    statements=(
        "CREATE UNIQUE INDEX missions_task_scope ON missions(task_id,workspace_id,principal_id)",
        "CREATE UNIQUE INDEX missions_source_scope ON missions(id,task_id,workspace_id,principal_id)",
        """CREATE TABLE proactivity_preferences (
            workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            principal_id TEXT NOT NULL CHECK(length(trim(principal_id)) BETWEEN 1 AND 200),
            enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
            generation INTEGER NOT NULL DEFAULT 0 CHECK(generation >= 0),
            check_interval_seconds INTEGER NOT NULL DEFAULT 3600
                CHECK(check_interval_seconds BETWEEN 900 AND 86400),
            cooldown_seconds INTEGER NOT NULL DEFAULT 86400
                CHECK(cooldown_seconds BETWEEN 0 AND 2592000),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(workspace_id,principal_id)
        )""",
        """CREATE TABLE proactive_suggestions (
            id TEXT PRIMARY KEY CHECK(length(trim(id)) BETWEEN 1 AND 200),
            workspace_id TEXT NOT NULL,
            principal_id TEXT NOT NULL CHECK(length(trim(principal_id)) BETWEEN 1 AND 200),
            suggestion_type TEXT NOT NULL CHECK(suggestion_type IN (
                'mission_follow_up','task_follow_up','task_blocked','task_completed')),
            source_kind TEXT NOT NULL CHECK(source_kind IN ('mission','task')),
            source_task_id TEXT NOT NULL,
            source_mission_id TEXT,
            source_revision_sha256 TEXT NOT NULL CHECK(
                length(source_revision_sha256)=64 AND
                source_revision_sha256 NOT GLOB '*[^0-9a-f]*'),
            deduplication_key TEXT NOT NULL CHECK(
                length(deduplication_key)=64 AND
                deduplication_key NOT GLOB '*[^0-9a-f]*'),
            state TEXT NOT NULL DEFAULT 'active' CHECK(state IN (
                'active','accepted','dismissed','snoozed','retracted','expired')),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL CHECK(
                julianday(expires_at) IS NOT NULL AND
                julianday(created_at) IS NOT NULL AND
                julianday(expires_at) > julianday(created_at) AND
                julianday(expires_at) <= julianday(created_at) + 30),
            snoozed_until TEXT CHECK(snoozed_until IS NULL OR
                (julianday(snoozed_until) IS NOT NULL AND
                 julianday(snoozed_until) > julianday(created_at) AND
                 julianday(snoozed_until) <= julianday(expires_at))),
            UNIQUE(id,workspace_id,principal_id),
            UNIQUE(id,workspace_id,principal_id,source_task_id),
            UNIQUE(workspace_id,principal_id,deduplication_key),
            CHECK((source_kind='task' AND source_mission_id IS NULL) OR
                  (source_kind='mission' AND source_mission_id IS NOT NULL)),
            FOREIGN KEY(source_task_id,workspace_id,principal_id)
                REFERENCES orchestration_tasks(id,workspace_id,principal_id) ON DELETE CASCADE,
            FOREIGN KEY(source_mission_id,source_task_id,workspace_id,principal_id)
                REFERENCES missions(id,task_id,workspace_id,principal_id) ON DELETE CASCADE
        )""",
        "CREATE INDEX proactive_suggestions_scope_state ON proactive_suggestions(workspace_id,principal_id,state,expires_at)",
        """CREATE TABLE proactivity_suggestion_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggestion_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            source_task_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN (
                'suggestion_created','suggestion_accepted','suggestion_dismissed',
                'suggestion_snoozed','suggestion_unsnoozed','suggestion_retracted',
                'suggestion_expired')),
            metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(
                length(metadata_json) <= 4096 AND json_valid(metadata_json) AND
                json_type(metadata_json)='object'),
            created_at TEXT NOT NULL,
            FOREIGN KEY(suggestion_id,workspace_id,principal_id,source_task_id)
                REFERENCES proactive_suggestions(id,workspace_id,principal_id,source_task_id) ON DELETE CASCADE
        )""",
        "CREATE INDEX proactivity_suggestion_events_scope ON proactivity_suggestion_events(workspace_id,principal_id,suggestion_id,id)",
        """CREATE TRIGGER proactivity_suggestion_events_no_update
            BEFORE UPDATE ON proactivity_suggestion_events
            BEGIN SELECT RAISE(ABORT, 'proactivity suggestion events are append-only'); END""",
        """CREATE TRIGGER proactivity_suggestion_events_no_delete
            BEFORE DELETE ON proactivity_suggestion_events
            WHEN NOT EXISTS (SELECT 1 FROM orchestration_purge_tombstones p
                WHERE p.workspace_id=OLD.workspace_id AND p.task_id=OLD.source_task_id)
            BEGIN SELECT RAISE(ABORT, 'proactivity suggestion events are append-only'); END""",
    ),
))

DEFAULT_MIGRATIONS = (*DEFAULT_MIGRATIONS, Migration(
    version=18,
    statements=(
        """ALTER TABLE proactive_suggestions ADD COLUMN reason_code TEXT NOT NULL
            DEFAULT 'legacy' CHECK(reason_code IN (
                'legacy','mission_aged','mission_blocked','mission_paused',
                'mission_interrupted','task_blocked','task_interrupted'))""",
        """CREATE TABLE proactivity_scheduler_claims (
            workspace_id TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            generation INTEGER NOT NULL CHECK(generation >= 0),
            claim_token TEXT,
            lease_expires_at TEXT,
            next_check_at TEXT,
            last_checked_at TEXT,
            last_suggestion_at TEXT,
            PRIMARY KEY(workspace_id,principal_id),
            FOREIGN KEY(workspace_id,principal_id)
                REFERENCES proactivity_preferences(workspace_id,principal_id) ON DELETE CASCADE,
            CHECK((claim_token IS NULL AND lease_expires_at IS NULL) OR
                  (claim_token IS NOT NULL AND length(claim_token)=32 AND
                   claim_token NOT GLOB '*[^0-9a-f]*' AND
                   julianday(lease_expires_at) IS NOT NULL))
        )""",
        "CREATE INDEX proactivity_scheduler_due ON proactivity_scheduler_claims(next_check_at,lease_expires_at,workspace_id,principal_id)",
    ),
))


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
        connection.execute("PRAGMA secure_delete = ON")
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
                    try:
                        for statement in migration.statements:
                            connection.execute(statement)
                    except sqlite3.OperationalError as exc:
                        if migration.version >= 3 and (
                            "fts5" in str(exc).lower()
                            or "no such module" in str(exc).lower()
                        ):
                            raise RuntimeError(
                                "Jarvis schema v3 requires SQLite built with FTS5 support"
                            ) from exc
                        raise
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
                if permission_value in {Permission.READ, Permission.WRITE}:
                    pref = connection.execute(
                        "SELECT generation FROM proactivity_preferences "
                        "WHERE workspace_id=? AND principal_id=? AND enabled=1",
                        (workspace_id, principal_id),
                    ).fetchone()
                    if pref is not None:
                        generation = int(pref["generation"]) + 1
                        now = _now()
                        connection.execute(
                            "UPDATE proactivity_preferences SET enabled=0,generation=?,updated_at=? "
                            "WHERE workspace_id=? AND principal_id=?",
                            (generation, now, workspace_id, principal_id),
                        )
                        connection.execute(
                            "UPDATE proactivity_scheduler_claims SET generation=?,claim_token=NULL,"
                            "lease_expires_at=NULL,next_check_at=NULL "
                            "WHERE workspace_id=? AND principal_id=?",
                            (generation, workspace_id, principal_id),
                        )
                        self._insert_audit(
                            connection, workspace_id, actor_id,
                            "proactivity.disabled_permission_revoked", "proactivity_preference",
                            principal_id, {"generation": str(generation)},
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

    @staticmethod
    def _purge_expired_conversations(
        connection: sqlite3.Connection, actor_id: str, workspace_id: str, now: str
    ) -> int:
        expired_turns = connection.execute(
            "DELETE FROM persistent_conversation_turns "
            "WHERE actor_id=? AND workspace_id=? AND expires_at<=?",
            (actor_id, workspace_id, now),
        ).rowcount
        expired_conversations = connection.execute(
            """DELETE FROM persistent_conversations
                WHERE actor_id=? AND workspace_id=?
                  AND (expires_at<=? OR NOT EXISTS (
                      SELECT 1 FROM persistent_conversation_turns t
                      WHERE t.conversation_id=persistent_conversations.id
                        AND t.actor_id=persistent_conversations.actor_id
                        AND t.workspace_id=persistent_conversations.workspace_id))""",
            (actor_id, workspace_id, now),
        ).rowcount
        if expired_turns or expired_conversations:
            connection.execute(
                "UPDATE conversation_memory_settings SET revision=revision+1,updated_at=? "
                "WHERE actor_id=? AND workspace_id=?",
                (now, actor_id, workspace_id),
            )
        return int(expired_turns) + int(expired_conversations)

    def purge_expired_conversation_memory(self, actor_id: str) -> int:
        """Hard-delete expired turns for one trusted principal; used by the local retention sweep."""
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        now = _now()
        deleted = 0
        with self._connection() as connection:
            self._begin(connection)
            try:
                workspaces = connection.execute(
                    "SELECT DISTINCT workspace_id FROM persistent_conversations WHERE actor_id=?",
                    (actor_id,),
                ).fetchall()
                for row in workspaces:
                    deleted += self._purge_expired_conversations(
                        connection, actor_id, str(row["workspace_id"]), now,
                    )
                self._commit(connection)
                return deleted
            except Exception:
                self._rollback(connection)
                raise

    @staticmethod
    def _conversation_settings_payload(row: sqlite3.Row | None) -> dict[str, object]:
        if row is None:
            return {
                "enabled": False, "retention_days": 30, "revision": 0,
                "consented_at": None, "revoked_at": None,
            }
        return {
            "enabled": bool(row["enabled"]),
            "retention_days": int(row["retention_days"]),
            "revision": int(row["revision"]),
            "consented_at": row["consented_at"],
            "revoked_at": row["revoked_at"],
        }

    def conversation_memory_settings(self, actor_id: str, workspace_id: str) -> dict[str, object]:
        """Return workspace-scoped consent and retention settings; never enables by default."""
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        with self._connection() as connection:
            self._require_access(connection, actor_id, workspace_id, Permission.READ)
            self._purge_expired_conversations(connection, actor_id, workspace_id, _now())
            connection.commit()
            row = connection.execute(
                "SELECT enabled,retention_days,revision,consented_at,revoked_at "
                "FROM conversation_memory_settings WHERE actor_id=? AND workspace_id=?",
                (actor_id, workspace_id),
            ).fetchone()
            can_write = connection.execute(
                "SELECT 1 FROM workspace_permissions WHERE workspace_id=? "
                "AND principal_id=? AND permission=?",
                (workspace_id, actor_id, Permission.WRITE.value),
            ).fetchone() is not None
            return {**self._conversation_settings_payload(row), "can_write": can_write}

    def set_conversation_memory_settings(
        self, actor_id: str, workspace_id: str, *, enabled: bool, retention_days: int = 30
    ) -> dict[str, object]:
        """Set explicit per-user/workspace consent. Revocation deletes saved content atomically."""
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        if type(retention_days) is not int or not 1 <= retention_days <= 365:
            raise ValueError("retention_days must be an integer between 1 and 365")
        now = _now()
        with self._connection() as connection:
            self._begin(connection)
            try:
                self._require_access(
                    connection, actor_id, workspace_id,
                    Permission.WRITE if enabled else Permission.READ,
                )
                previous = connection.execute(
                    "SELECT revision FROM conversation_memory_settings "
                    "WHERE actor_id=? AND workspace_id=?",
                    (actor_id, workspace_id),
                ).fetchone()
                revision = (int(previous["revision"]) + 1) if previous else 1
                if not enabled:
                    connection.execute(
                        "DELETE FROM persistent_conversations WHERE actor_id=? AND workspace_id=?",
                        (actor_id, workspace_id),
                    )
                connection.execute(
                    """INSERT INTO conversation_memory_settings
                        (actor_id,workspace_id,enabled,retention_days,revision,
                         consented_at,revoked_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(actor_id,workspace_id) DO UPDATE SET
                        enabled=excluded.enabled, retention_days=excluded.retention_days,
                        revision=excluded.revision,
                        consented_at=CASE WHEN excluded.enabled=1 THEN excluded.consented_at
                                          ELSE conversation_memory_settings.consented_at END,
                        revoked_at=excluded.revoked_at, updated_at=excluded.updated_at""",
                    (actor_id, workspace_id, int(enabled), retention_days, revision,
                     now if enabled else None, None if enabled else now, now),
                )
                self._insert_audit(
                    connection, workspace_id, actor_id,
                    "conversation_memory.enabled" if enabled else "conversation_memory.revoked",
                    "conversation_memory_policy", workspace_id,
                    {"enabled": str(enabled).lower(), "retention_days": str(retention_days)},
                )
                self._commit(connection)
            except Exception:
                self._rollback(connection)
                raise
        return self.conversation_memory_settings(actor_id, workspace_id)

    def list_persistent_conversations(
        self, actor_id: str, workspace_id: str, *, limit: int = 25
    ) -> list[dict[str, object]]:
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        with self._connection() as connection:
            self._require_access(connection, actor_id, workspace_id, Permission.READ)
            now = _now()
            self._purge_expired_conversations(connection, actor_id, workspace_id, now)
            connection.commit()
            settings = connection.execute(
                "SELECT enabled FROM conversation_memory_settings WHERE actor_id=? AND workspace_id=?",
                (actor_id, workspace_id),
            ).fetchone()
            if settings is None or not bool(settings["enabled"]):
                return []
            rows = connection.execute(
                """SELECT c.id,c.created_at,c.updated_at,c.expires_at,
                           COUNT(t.id) AS turn_count,
                           (SELECT user_text FROM persistent_conversation_turns first_turn
                            WHERE first_turn.conversation_id=c.id
                            ORDER BY first_turn.ordinal LIMIT 1) AS preview
                    FROM persistent_conversations c
                    LEFT JOIN persistent_conversation_turns t
                      ON t.conversation_id=c.id AND t.actor_id=c.actor_id
                     AND t.workspace_id=c.workspace_id AND t.expires_at>?
                    WHERE c.actor_id=? AND c.workspace_id=? AND c.expires_at>?
                    GROUP BY c.id ORDER BY c.updated_at DESC,c.id LIMIT ?""",
                (now, actor_id, workspace_id, now, limit),
            ).fetchall()
            return [{
                "id": row["id"], "created_at": row["created_at"],
                "updated_at": row["updated_at"], "expires_at": row["expires_at"],
                "turn_count": int(row["turn_count"]),
                "preview": str(row["preview"] or "")[:160],
            } for row in rows]

    def get_persistent_conversation(
        self, actor_id: str, workspace_id: str, conversation_id: str, *, limit: int = 100
    ) -> dict[str, object] | None:
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        conversation_id = _nonempty(conversation_id, "conversation ID", 128)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._connection() as connection:
            self._require_access(connection, actor_id, workspace_id, Permission.READ)
            now = _now()
            self._purge_expired_conversations(connection, actor_id, workspace_id, now)
            connection.commit()
            settings = connection.execute(
                "SELECT enabled FROM conversation_memory_settings WHERE actor_id=? AND workspace_id=?",
                (actor_id, workspace_id),
            ).fetchone()
            if settings is None or not bool(settings["enabled"]):
                return None
            conversation = connection.execute(
                """SELECT id,created_at,updated_at,expires_at FROM persistent_conversations
                    WHERE id=? AND actor_id=? AND workspace_id=? AND expires_at>?""",
                (conversation_id, actor_id, workspace_id, now),
            ).fetchone()
            if conversation is None:
                return None
            turns = connection.execute(
                """SELECT ordinal,user_text,assistant_text,created_at,expires_at
                    FROM persistent_conversation_turns
                    WHERE conversation_id=? AND actor_id=? AND workspace_id=? AND expires_at>?
                    ORDER BY ordinal DESC LIMIT ?""",
                (conversation_id, actor_id, workspace_id, now, limit),
            ).fetchall()
            return {
                "id": conversation["id"], "created_at": conversation["created_at"],
                "updated_at": conversation["updated_at"], "expires_at": conversation["expires_at"],
                "turns": [{"ordinal": row["ordinal"], "user": row["user_text"],
                           "assistant": row["assistant_text"], "created_at": row["created_at"],
                           "expires_at": row["expires_at"]} for row in reversed(turns)],
            }

    def delete_persistent_conversation(
        self, actor_id: str, workspace_id: str, conversation_id: str
    ) -> bool:
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        conversation_id = _nonempty(conversation_id, "conversation ID", 128)
        now = _now()
        with self._connection() as connection:
            self._begin(connection)
            try:
                self._require_access(connection, actor_id, workspace_id, Permission.READ)
                cursor = connection.execute(
                    "DELETE FROM persistent_conversations WHERE id=? AND actor_id=? AND workspace_id=?",
                    (conversation_id, actor_id, workspace_id),
                )
                if cursor.rowcount:
                    connection.execute(
                        "UPDATE conversation_memory_settings SET revision=revision+1,updated_at=? "
                        "WHERE actor_id=? AND workspace_id=?",
                        (now, actor_id, workspace_id),
                    )
                    self._insert_audit(
                        connection, workspace_id, actor_id,
                        "conversation_memory.conversation_deleted", "conversation", conversation_id, {},
                    )
                self._commit(connection)
                return cursor.rowcount == 1
            except Exception:
                self._rollback(connection)
                raise

    def search_persistent_conversation_context(
        self, actor_id: str, workspace_id: str, query: str, *,
        exclude_conversation_id: str | None = None, limit: int = 6,
        max_bytes: int = 3072,
    ) -> tuple[int, list[tuple[str, str]]]:
        """Retrieve bounded, workspace-authorized turns; returned text is always untrusted context."""
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        if not isinstance(query, str) or len(query) > 1000:
            raise ValueError("query must be a string of at most 1000 characters")
        if type(limit) is not int or not 1 <= limit <= 12 or type(max_bytes) is not int or not 256 <= max_bytes <= 8192:
            raise ValueError("context bounds are invalid")
        with self._connection() as connection:
            self._require_access(connection, actor_id, workspace_id, Permission.READ)
            now = _now()
            self._purge_expired_conversations(connection, actor_id, workspace_id, now)
            connection.commit()
            settings = connection.execute(
                "SELECT enabled,revision FROM conversation_memory_settings WHERE actor_id=? AND workspace_id=?",
                (actor_id, workspace_id),
            ).fetchone()
            if settings is None or not bool(settings["enabled"]):
                return (int(settings["revision"]) if settings else 0, [])
            sql = """SELECT user_text,assistant_text,created_at,id,conversation_id
                FROM persistent_conversation_turns WHERE actor_id=? AND workspace_id=? AND expires_at>?"""
            params: list[object] = [actor_id, workspace_id, now]
            if exclude_conversation_id is not None:
                sql += " AND conversation_id<>?"
                params.append(exclude_conversation_id)
            sql += " ORDER BY created_at DESC,id DESC LIMIT 120"
            rows = connection.execute(sql, params).fetchall()
            stop_words = {"what", "when", "where", "which", "who", "why", "how", "was", "were", "did", "does", "the", "and", "for", "with", "from", "that", "this", "have", "your", "my", "you", "jarvis", "please", "about", "say", "said", "we", "our", "were", "are", "is", "it", "to", "of", "in", "on"}
            terms = {term for term in re.findall(r"\w+", query.casefold(), flags=re.UNICODE)
                     if len(term) > 2 and term not in stop_words}
            ranked: list[tuple[int, str, str, str, str]] = []
            for row in rows:
                user = str(row["user_text"])
                assistant = str(row["assistant_text"])
                stored_terms = set(re.findall(r"\w+", f"{user} {assistant}".casefold(), flags=re.UNICODE))
                score = len(terms & stored_terms)
                ranked.append((score, str(row["created_at"]), str(row["id"]), user, assistant))
            ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
            selected: list[tuple[str, str]] = []
            used = 2
            for _, _, _, user, assistant in ranked:
                encoded_user = json.dumps(user, ensure_ascii=False, separators=(",", ":"))
                encoded_assistant = json.dumps(assistant, ensure_ascii=False, separators=(",", ":"))
                size = len((f'{{"user":{encoded_user},"assistant":{encoded_assistant}}}').encode("utf-8"))
                separator_size = 1 if selected else 0
                if used + separator_size + size > max_bytes:
                    continue
                selected.append((user, assistant))
                used += separator_size + size
                if len(selected) >= limit:
                    break
            selected.reverse()
            return int(settings["revision"]), selected

    def save_persistent_conversation_turn(
        self, actor_id: str, workspace_id: str, *, conversation_id: str | None,
        expected_revision: int, user_text: str, assistant_text: str,
    ) -> str:
        """Append a completed opted-in turn with current WRITE permission and consent revision."""
        self.initialize()
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ConversationMemoryChanged("conversation memory settings changed")
        if (not isinstance(user_text, str) or not user_text.strip() or len(user_text) > 1000
                or "\x00" in user_text or not isinstance(assistant_text, str)
                or not assistant_text.strip() or len(assistant_text) > 16000 or "\x00" in assistant_text):
            raise ValueError("conversation turn exceeds storage bounds")
        user_text.encode("utf-8")
        assistant_text.encode("utf-8")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat(timespec="microseconds")
        with self._connection() as connection:
            self._begin(connection)
            try:
                self._require_access(connection, actor_id, workspace_id, Permission.READ)
                self._require_access(connection, actor_id, workspace_id, Permission.WRITE)
                self._purge_expired_conversations(connection, actor_id, workspace_id, now)
                setting = connection.execute(
                    "SELECT enabled,retention_days,revision FROM conversation_memory_settings "
                    "WHERE actor_id=? AND workspace_id=?",
                    (actor_id, workspace_id),
                ).fetchone()
                if setting is None or not bool(setting["enabled"]):
                    raise ConversationMemoryDisabled("persistent conversation memory is disabled")
                if int(setting["revision"]) != expected_revision:
                    raise ConversationMemoryChanged("conversation memory settings changed")
                expires_at = (now_dt + timedelta(days=int(setting["retention_days"]))).isoformat(timespec="microseconds")
                if conversation_id is None:
                    conversation_id = uuid4().hex
                    connection.execute(
                        "INSERT INTO persistent_conversations "
                        "(id,actor_id,workspace_id,created_at,updated_at,expires_at) VALUES (?,?,?,?,?,?)",
                        (conversation_id, actor_id, workspace_id, now, now, expires_at),
                    )
                else:
                    conversation = connection.execute(
                        """SELECT c.id,COALESCE(MAX(t.ordinal),0) AS last_ordinal
                            FROM persistent_conversations c LEFT JOIN persistent_conversation_turns t
                              ON t.conversation_id=c.id AND t.actor_id=c.actor_id
                             AND t.workspace_id=c.workspace_id
                            WHERE c.id=? AND c.actor_id=? AND c.workspace_id=? GROUP BY c.id""",
                        (conversation_id, actor_id, workspace_id),
                    ).fetchone()
                    if conversation is None:
                        raise ConversationMemoryChanged("conversation was deleted or is unavailable")
                    if int(conversation["last_ordinal"]) >= 100:
                        conversation_id = uuid4().hex
                        connection.execute(
                            "INSERT INTO persistent_conversations "
                            "(id,actor_id,workspace_id,created_at,updated_at,expires_at) VALUES (?,?,?,?,?,?)",
                            (conversation_id, actor_id, workspace_id, now, now, expires_at),
                        )
                    else:
                        connection.execute(
                            "UPDATE persistent_conversations SET updated_at=?,expires_at=? WHERE id=? AND actor_id=? AND workspace_id=?",
                            (now, expires_at, conversation_id, actor_id, workspace_id),
                        )
                ordinal = int(connection.execute(
                    "SELECT COALESCE(MAX(ordinal),0)+1 FROM persistent_conversation_turns "
                    "WHERE conversation_id=? AND actor_id=? AND workspace_id=?",
                    (conversation_id, actor_id, workspace_id),
                ).fetchone()[0])
                connection.execute(
                    """INSERT INTO persistent_conversation_turns
                        (id,conversation_id,actor_id,workspace_id,ordinal,user_text,assistant_text,created_at,expires_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (uuid4().hex, conversation_id, actor_id, workspace_id, ordinal,
                     user_text.strip(), assistant_text.strip(), now, expires_at),
                )
                self._commit(connection)
                return conversation_id
            except Exception:
                self._rollback(connection)
                raise

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

        try:
            with ExitStack() as connections:
                source = connections.enter_context(closing(sqlite3.connect(source_path)))
                target = connections.enter_context(closing(sqlite3.connect(temporary)))
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
                if source_version >= 3:
                    search_table = source.execute(
                        "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?",
                        ("table", "document_search"),
                    ).fetchone()
                    if search_table is None or "using fts5" not in str(search_table[0]).lower():
                        raise sqlite3.DatabaseError(
                            "backup is missing the required FTS5 search index"
                        )
                if source_version >= 4:
                    v4_tables = {
                        "memory_candidates": {"id", "workspace_id", "content", "state", "content_sha256", "evidence_sha256"},
                        "memory_candidate_evidence": {"candidate_id", "workspace_id", "document_id", "root_id", "relative_path", "source_sha256"},
                        "memories": {"id", "workspace_id", "content", "deleted_at", "source_candidate_id"},
                        "memory_evidence": {"memory_id", "workspace_id", "document_id", "root_id", "relative_path", "source_sha256"},
                        "memory_revisions": {"memory_id", "workspace_id", "revision", "content_sha256"},
                        "memory_revision_evidence": {"memory_id", "revision", "document_id", "root_id", "relative_path", "source_sha256"},
                        "memory_decision_receipts": {"decision_id", "context_sha256"},
                    }
                    for table, required_columns in v4_tables.items():
                        columns = {row[1] for row in source.execute(f"PRAGMA table_info({table})").fetchall()}
                        if not required_columns <= columns:
                            raise sqlite3.DatabaseError(f"backup is missing required schema for table {table}")
                if source_version >= 5:
                    memory_search = source.execute(
                        "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?",
                        ("table", "memory_search"),
                    ).fetchone()
                    if memory_search is None or "using fts5" not in str(memory_search[0]).lower():
                        raise sqlite3.DatabaseError(
                            "backup is missing the required memory FTS5 search index"
                        )
                    memory_search_columns = {
                        row[1]
                        for row in source.execute("PRAGMA table_info(memory_search)").fetchall()
                    }
                    if not {"memory_id", "workspace_id", "content"} <= memory_search_columns:
                        raise sqlite3.DatabaseError(
                            "backup memory FTS5 search index has an unsupported schema"
                        )
                    triggers = {
                        row[0]
                        for row in source.execute(
                            "SELECT name FROM sqlite_master WHERE type = ?",
                            ("trigger",),
                        ).fetchall()
                    }
                    required_triggers = {
                        "memories_search_insert",
                        "memories_search_update",
                        "memories_search_delete",
                    }
                    if not required_triggers <= triggers:
                        raise sqlite3.DatabaseError(
                            "backup is missing memory search synchronization triggers"
                        )
                if source_version >= 6:
                    developer_tables = {
                        "developer_repositories": {"id", "workspace_id", "approved_root_id", "relative_path", "head_commit", "index_status"},
                        "developer_files": {"id", "repository_id", "workspace_id", "commit_sha", "relative_path", "git_blob_oid", "content_sha256", "symbols_json", "imports_json"},
                    }
                    for table, required_columns in developer_tables.items():
                        columns = {row[1] for row in source.execute(f"PRAGMA table_info({table})").fetchall()}
                        if not required_columns <= columns:
                            raise sqlite3.DatabaseError(f"backup is missing required schema for table {table}")
                    developer_search = source.execute(
                        "SELECT sql FROM sqlite_master WHERE type=? AND name=?",
                        ("table", "developer_file_search"),
                    ).fetchone()
                    if developer_search is None or "using fts5" not in str(developer_search[0]).lower():
                        raise sqlite3.DatabaseError("backup is missing the developer FTS5 index")
                    triggers = {row[0] for row in source.execute(
                        "SELECT name FROM sqlite_master WHERE type=?", ("trigger",)
                    ).fetchall()}
                    if not {"developer_files_search_insert", "developer_files_search_delete", "developer_files_search_update"} <= triggers:
                        raise sqlite3.DatabaseError("backup is missing developer search synchronization triggers")
                if source_version >= 7:
                    columns = {row[1] for row in source.execute("PRAGMA table_info(developer_files)").fetchall()}
                    if "analysis_status" not in columns:
                        raise sqlite3.DatabaseError("backup is missing developer analysis status")
                if source_version >= 13:
                    columns = {row[1] for row in source.execute("PRAGMA table_info(developer_files)").fetchall()}
                    if "references_json" not in columns:
                        raise sqlite3.DatabaseError("backup is missing developer reference metadata")
                if source_version >= 9:
                    orchestration_tables = {
                        "orchestration_tasks": {"id", "workspace_id", "principal_id", "repository_id", "goal", "state", "execution_mode", "current_plan_revision", "approved_plan_revision"},
                        "orchestration_plans": {"task_id", "workspace_id", "principal_id", "revision", "plan_sha256", "state"},
                        "orchestration_subtasks": {"task_id", "revision", "subtask_id", "state", "progress_percent"},
                        "orchestration_dependencies": {"task_id", "revision", "subtask_id", "depends_on_subtask_id"},
                        "orchestration_approvals": {"decision_id", "task_id", "revision", "action", "plan_sha256"},
                        "orchestration_task_events": {"id", "task_id", "workspace_id", "principal_id", "actor_id", "event_type", "idempotency_key", "metadata_json"},
                    }
                    for table, required_columns in orchestration_tables.items():
                        columns = {row[1] for row in source.execute(
                            f"PRAGMA table_info({table})").fetchall()}
                        if not required_columns <= columns:
                            raise sqlite3.DatabaseError(
                                f"backup is missing required schema for table {table}")
                    orchestration_triggers = {row[0] for row in source.execute(
                        "SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()}
                    if not {
                        "orchestration_task_events_no_update",
                        "orchestration_task_events_no_delete",
                        "orchestration_approvals_no_update",
                        "orchestration_approvals_no_delete",
                        "orchestration_subtasks_plan_fields_immutable",
                    } <= orchestration_triggers:
                        raise sqlite3.DatabaseError(
                            "backup is missing orchestration append-only protections")
                if source_version >= 10:
                    v10_tables = {
                        "orchestration_tasks": {"archived_at", "archived_by"},
                        "orchestration_retention_policies": {
                            "workspace_id", "completed_retention_days",
                            "cancelled_retention_days", "failed_retention_days",
                            "interrupted_retention_days", "auto_delete_enabled",
                            "updated_by", "updated_at", "expires_at",
                        },
                        "orchestration_purge_operations": {
                            "purge_id", "workspace_id", "actor_id",
                            "idempotency_key", "preview_sha256", "task_count",
                            "purged_at",
                        },
                        "orchestration_purge_tombstones": {
                            "workspace_id", "task_id", "purge_id", "task_state",
                            "archived_at", "plan_count", "subtask_count",
                            "dependency_count", "approval_count", "event_count",
                            "purged_by", "purged_at",
                        },
                    }
                    if source_version == 10:
                        v10_tables["orchestration_purge_tombstones"].add("goal_sha256")
                    for table, required_columns in v10_tables.items():
                        columns = {row[1] for row in source.execute(
                            f"PRAGMA table_info({table})").fetchall()}
                        if not required_columns <= columns:
                            raise sqlite3.DatabaseError(
                                f"backup is missing required schema for table {table}")
                    if source_version >= 11:
                        tombstone_columns = {row[1] for row in source.execute(
                            "PRAGMA table_info(orchestration_purge_tombstones)").fetchall()}
                        if "goal_sha256" in tombstone_columns:
                            raise sqlite3.DatabaseError(
                                "backup contains removed goal_sha256 column on orchestration_purge_tombstones")
                    v10_triggers = {row[0] for row in source.execute(
                        "SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()}
                    if not {
                        "orchestration_purge_operations_no_update",
                        "orchestration_purge_operations_no_delete",
                        "orchestration_purge_tombstones_no_update",
                        "orchestration_purge_tombstones_no_delete",
                    } <= v10_triggers:
                        raise sqlite3.DatabaseError(
                            "backup is missing orchestration purge tombstone protections")
                if source_version >= 12:
                    conversation_tables = {
                        "conversation_memory_settings": {
                            "actor_id", "workspace_id", "enabled", "retention_days",
                            "revision", "consented_at", "revoked_at", "updated_at",
                        },
                        "persistent_conversations": {
                            "id", "actor_id", "workspace_id", "created_at", "updated_at", "expires_at",
                        },
                        "persistent_conversation_turns": {
                            "id", "conversation_id", "actor_id", "workspace_id", "ordinal",
                            "user_text", "assistant_text", "created_at", "expires_at",
                        },
                    }
                    for table, required_columns in conversation_tables.items():
                        columns = {row[1] for row in source.execute(
                            f"PRAGMA table_info({table})").fetchall()}
                        if not required_columns <= columns:
                            raise sqlite3.DatabaseError(
                                f"backup is missing required schema for table {table}")
                    conversation_foreign_keys = source.execute(
                        "PRAGMA foreign_key_list(persistent_conversation_turns)"
                    ).fetchall()
                    if not any(row[2] == "persistent_conversations" for row in conversation_foreign_keys):
                        raise sqlite3.DatabaseError(
                            "backup is missing conversation ownership constraints")
                if source_version >= 15:
                    mission_tables = {
                        "missions": {"id", "task_id", "workspace_id", "principal_id", "state", "max_steps", "steps_used"},
                        "mission_authorizations": {"id", "mission_id", "plan_revision", "plan_sha256", "budget_sha256", "authorization_sha256"},
                        "mission_events": {"id", "mission_id", "workspace_id", "event_type", "metadata_json"},
                        "mission_budget_ledger": {"id", "mission_id", "operation_key", "request_delta", "step_delta", "outcome"},
                        "mission_checkpoints": {"mission_id", "sequence", "plan_revision", "progress_sha256", "progress_json"},
                    }
                    for table, required_columns in mission_tables.items():
                        columns = {row[1] for row in source.execute(
                            f"PRAGMA table_info({table})").fetchall()}
                        if not required_columns <= columns:
                            raise sqlite3.DatabaseError(f"backup is missing required schema for table {table}")
                    mission_triggers = {row[0] for row in source.execute(
                        "SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()}
                    required_mission_triggers = {
                        "mission_authorizations_no_update", "mission_authorizations_no_delete",
                        "mission_events_no_update", "mission_events_no_delete",
                        "mission_checkpoints_no_update", "mission_checkpoints_no_delete",
                        "mission_ledger_no_update", "mission_ledger_no_delete",
                    }
                    if not required_mission_triggers <= mission_triggers:
                        raise sqlite3.DatabaseError("backup is missing mission append-only protections")
                if source_version >= 16:
                    reservation_columns = {row[1] for row in source.execute(
                        "PRAGMA table_info(mission_planning_reservations)").fetchall()}
                    if not {"workspace_id", "principal_id", "mission_id", "idempotency_key",
                            "request_sha256", "state", "owner_token", "attempts_used",
                            "result_revision", "result_sha256"} <= reservation_columns:
                        raise sqlite3.DatabaseError("backup is missing mission planning reservation schema")
                if source_version >= 17:
                    proactivity_tables = {
                        "proactivity_preferences": {
                            "workspace_id", "principal_id", "enabled", "generation",
                            "check_interval_seconds", "cooldown_seconds", "created_at", "updated_at",
                        },
                        "proactive_suggestions": {
                            "id", "workspace_id", "principal_id", "suggestion_type",
                            "source_kind", "source_task_id", "source_mission_id",
                            "source_revision_sha256", "deduplication_key", "state",
                            "created_at", "expires_at", "snoozed_until",
                        },
                        "proactivity_suggestion_events": {
                            "id", "suggestion_id", "workspace_id", "principal_id",
                            "source_task_id", "event_type", "metadata_json", "created_at",
                        },
                    }
                    for table, required_columns in proactivity_tables.items():
                        columns = {row[1] for row in source.execute(
                            f"PRAGMA table_info({table})").fetchall()}
                        if not required_columns <= columns:
                            raise sqlite3.DatabaseError(
                                f"backup is missing required schema for table {table}")
                    proactivity_triggers = {row[0] for row in source.execute(
                        "SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()}
                    if not {
                        "proactivity_suggestion_events_no_update",
                        "proactivity_suggestion_events_no_delete",
                    } <= proactivity_triggers:
                        raise sqlite3.DatabaseError(
                            "backup is missing proactivity append-only protections")
                if source_version >= 18:
                    suggestion_columns = {row[1] for row in source.execute(
                        "PRAGMA table_info(proactive_suggestions)").fetchall()}
                    if "reason_code" not in suggestion_columns:
                        raise sqlite3.DatabaseError(
                            "backup is missing proactivity suggestion reason codes")
                    claim_columns = {row[1] for row in source.execute(
                        "PRAGMA table_info(proactivity_scheduler_claims)").fetchall()}
                    if not {"workspace_id", "principal_id", "generation", "claim_token",
                            "lease_expires_at", "next_check_at", "last_checked_at",
                            "last_suggestion_at"} <= claim_columns:
                        raise sqlite3.DatabaseError(
                            "backup is missing proactivity scheduler claim schema")
                if source.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise sqlite3.DatabaseError("backup contains foreign key violations")
                source.backup(target)
        except Exception:
            for suffix in ("", "-journal", "-wal", "-shm"):
                try:
                    Path(str(temporary) + suffix).unlink()
                except FileNotFoundError:
                    pass
            raise
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
