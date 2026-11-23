from __future__ import annotations

import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis_core import (  # noqa: E402
    AccessDenied,
    Database,
    InvalidPath,
    LastWorkspaceManager,
    Migration,
    Permission,
    WorkspaceKind,
)
from jarvis_core.storage import DEFAULT_MIGRATIONS  # noqa: E402
from jarvis_core.storage import default_database_path  # noqa: E402


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.db_path = self.directory / "jarvis.sqlite3"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.workspace = self.db.create_workspace(
            "مشروع Jarvis — Project", WorkspaceKind.PROJECT, "alice", "project-1"
        )

    def test_fresh_database_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fresh.sqlite3"
            db = Database(path)
            db.initialize()
            self.assertTrue(path.exists())
            with db._connection() as connection:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0], Database(path).current_version
                )
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = ?", ("table",)
                    )
                }
            self.assertTrue(
                {"workspaces", "workspace_permissions", "approved_roots", "audit_events"}
                <= tables
            )
            with db._connection() as connection:
                file_columns = {row[1] for row in connection.execute("PRAGMA table_info(developer_files)")}
                task_columns = {row[1] for row in connection.execute("PRAGMA table_info(orchestration_tasks)")}
                tombstone_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(orchestration_purge_tombstones)")
                }
                indexes = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    )
                }
                triggers = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                    )
                }
                self.assertIsNone(connection.execute("PRAGMA foreign_key_check").fetchone())
            self.assertIn("analysis_status", file_columns)
            self.assertTrue({"archived_at", "archived_by"} <= task_columns)
            self.assertNotIn("goal_sha256", tombstone_columns)
            self.assertEqual(
                tombstone_columns,
                {
                    "workspace_id", "task_id", "purge_id", "task_state", "archived_at",
                    "plan_count", "subtask_count", "dependency_count", "approval_count",
                    "event_count", "purged_by", "purged_at",
                },
            )
            self.assertIn("orchestration_purge_tombstones_workspace", indexes)
            self.assertTrue(
                {
                    "orchestration_purge_operations_no_update",
                    "orchestration_purge_operations_no_delete",
                    "orchestration_purge_tombstones_no_update",
                    "orchestration_purge_tombstones_no_delete",
                    "orchestration_task_events_no_delete",
                    "orchestration_approvals_no_delete",
                }
                <= triggers
            )
            self.assertTrue(
                {
                    "ingested_documents",
                    "sync_runs",
                    "sync_events",
                    "sync_preferences",
                    "sync_root_state",
                    "document_search",
                    "memory_search",
                    "developer_repositories",
                    "developer_files",
                    "developer_file_search",
                    "orchestration_tasks",
                    "orchestration_plans",
                    "orchestration_subtasks",
                    "orchestration_dependencies",
                    "orchestration_approvals",
                    "orchestration_task_events",
                    "orchestration_retention_policies",
                    "orchestration_purge_operations",
                    "orchestration_purge_tombstones",
                }
                <= tables
            )

    def test_repeated_initialization_is_idempotent(self) -> None:
        for _ in range(3):
            self.db.initialize()
        with self.db._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], self.db.current_version)
            count = connection.execute(
                "SELECT COUNT(*) FROM workspaces WHERE id = ?", ("project-1",)
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_successful_upgrade_applies_new_migration_once(self) -> None:
        path = self.directory / "upgrade.sqlite3"
        version_one = Database(path, migrations=DEFAULT_MIGRATIONS[:1])
        version_one.initialize()
        version_one.create_workspace(
            "Preserved", WorkspaceKind.PERSONAL, "upgrade-owner", "upgrade-1"
        )
        version_current = Database(path)
        version_current.initialize()
        next_version = DEFAULT_MIGRATIONS[-1].version + 1
        version_next_migrations = (
            *DEFAULT_MIGRATIONS,
            Migration(next_version, ("CREATE TABLE migration_marker (value TEXT NOT NULL)",)),
        )
        version_next = Database(path, migrations=version_next_migrations)
        version_next.initialize()
        version_next.initialize()
        with version_next._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], next_version)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = ? AND name = ?",
                    ("table", "migration_marker"),
                ).fetchone()[0],
                1,
            )
        self.assertEqual(version_next.get_workspace("upgrade-owner", "upgrade-1").name, "Preserved")

    def test_v8_upgrade_adds_orchestration_schema_and_v9_failure_rolls_back(self) -> None:
        path = self.directory / "v8-orchestration.sqlite3"
        version_eight = Database(path, migrations=DEFAULT_MIGRATIONS[:8])
        version_eight.initialize()
        version_eight.create_workspace(
            "Preserved P4", WorkspaceKind.PROJECT, "upgrade-owner", "upgrade-p4"
        )
        with version_eight._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 8)

        broken_v9 = Database(path, migrations=(
            *DEFAULT_MIGRATIONS[:8],
            Migration(9, (
                "CREATE TABLE orchestration_upgrade_probe (id INTEGER PRIMARY KEY)",
                "INVALID V9 SQL",
            )),
        ))
        with self.assertRaises(sqlite3.OperationalError):
            broken_v9.initialize()
        with broken_v9._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 8)
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                ("orchestration_upgrade_probe",),
            ).fetchone())

        upgraded = Database(path)
        upgraded.initialize()
        with upgraded._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], upgraded.current_version)
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"orchestration_tasks", "orchestration_plans",
                         "orchestration_task_events",
                         "orchestration_retention_policies",
                         "orchestration_purge_operations",
                         "orchestration_purge_tombstones"} <= tables)
        self.assertEqual(upgraded.get_workspace("upgrade-owner", "upgrade-p4").name,
                         "Preserved P4")

    def test_v9_upgrade_adds_retention_schema_and_v10_failure_rolls_back(self) -> None:
        path = self.directory / "v9-retention.sqlite3"
        version_nine = Database(path, migrations=DEFAULT_MIGRATIONS[:9])
        version_nine.initialize()
        version_nine.create_workspace(
            "Preserved V9", WorkspaceKind.PROJECT, "upgrade-owner", "upgrade-v9"
        )
        with version_nine._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 9)
            connection.execute(
                "INSERT INTO orchestration_tasks(id,workspace_id,principal_id,goal,goal_sha256,"
                "state,execution_mode,requested_capabilities_json,capability_manifest_json,"
                "capability_manifest_sha256,created_at,updated_at,expires_at) "
                "VALUES('v9task','upgrade-v9','upgrade-owner','Legacy task',"
                "'0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',"
                "'cancelled','planned','[]','{}',"
                "'0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',"
                "'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00','2026-02-01T00:00:00+00:00')"
            )
            connection.commit()

        broken_v10 = Database(path, migrations=(
            *DEFAULT_MIGRATIONS[:9],
            Migration(10, (
                "ALTER TABLE orchestration_tasks ADD COLUMN archived_at TEXT",
                "CREATE TABLE retention_upgrade_probe (id INTEGER PRIMARY KEY)",
                "INVALID V10 SQL",
            )),
        ))
        with self.assertRaises(sqlite3.OperationalError):
            broken_v10.initialize()
        with broken_v10._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 9)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(orchestration_tasks)")}
            self.assertNotIn("archived_at", columns)
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                ("retention_upgrade_probe",),
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM orchestration_tasks WHERE id='v9task'"
            ).fetchone()[0], 1)

        upgraded = Database(path)
        upgraded.initialize()
        with upgraded._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], upgraded.current_version)
            row = connection.execute(
                "SELECT state,archived_at,archived_by FROM orchestration_tasks WHERE id='v9task'"
            ).fetchone()
            triggers = {item[0] for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'")}
        self.assertEqual(row["state"], "cancelled")
        self.assertIsNone(row["archived_at"])
        self.assertIsNone(row["archived_by"])
        self.assertTrue({
            "orchestration_purge_operations_no_update",
            "orchestration_purge_operations_no_delete",
            "orchestration_purge_tombstones_no_update",
            "orchestration_purge_tombstones_no_delete",
            "orchestration_task_events_no_delete",
            "orchestration_approvals_no_delete",
        } <= triggers)

    def test_migration_failure_rolls_back_schema_and_version(self) -> None:
        migrations = (
            Migration(1, ("CREATE TABLE migration_probe (id INTEGER PRIMARY KEY)",)),
            Migration(2, ("CREATE TABLE another_probe (id INTEGER)", "INVALID SQL")),
        )
        db = Database(self.directory / "broken.sqlite3", migrations=migrations)
        with self.assertRaises(sqlite3.OperationalError):
            db.initialize()
        with db._connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = ?", ("table",)
                )
            }
        self.assertEqual(version, 0)
        self.assertNotIn("migration_probe", tables)
        self.assertNotIn("another_probe", tables)

    def test_workspace_and_permissions_persist_across_process_restart(self) -> None:
        script = """
import sys
from jarvis_core import Database
db = Database(sys.argv[1])
workspace = db.get_workspace("alice", sys.argv[2])
print(workspace.name)
"""
        environment = os.environ.copy()

        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = str(ROOT) + (
            os.pathsep + existing if existing else ""
        )

        environment["PYTHONIOENCODING"] = "utf-8"

        result = subprocess.run(
            [sys.executable, "-c", script, str(self.db_path), self.workspace.id],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "مشروع Jarvis — Project")

    def test_workspace_listing_and_access_are_isolated(self) -> None:
        self.db.create_workspace("Personal", WorkspaceKind.PERSONAL, "bob", "personal-1")
        self.assertEqual([w.id for w in self.db.list_workspaces("alice")], ["project-1"])
        self.assertEqual([w.id for w in self.db.list_workspaces("bob")], ["personal-1"])
        with self.assertRaises(AccessDenied):
            self.db.get_workspace("alice", "personal-1")
        with self.assertRaises(AccessDenied):
            self.db.require_access("unknown", "project-1", Permission.READ)
        self.assertFalse(self.db.check_access("unknown", "project-1", Permission.READ))

    def test_explicit_grant_and_revoke_and_manager_protection(self) -> None:
        self.assertTrue(
            self.db.grant_permission(
                "alice", "project-1", "bob", Permission.READ
            )
        )
        root_path = self.directory / "bob-root"
        root_path.mkdir()
        with self.assertRaises(AccessDenied):
            self.db.register_approved_root("bob", "project-1", root_path)
        self.assertFalse(
            self.db.grant_permission(
                "alice", "project-1", "bob", Permission.READ
            )
        )
        self.assertTrue(self.db.check_access("bob", "project-1", Permission.READ))
        with self.assertRaises(AccessDenied):
            self.db.grant_permission(
                "bob", "project-1", "mallory", Permission.READ
            )
        with self.assertRaises(LastWorkspaceManager):
            self.db.revoke_permission(
                "alice", "project-1", "alice", Permission.MANAGE_WORKSPACE
            )
        self.assertTrue(
            self.db.revoke_permission(
                "alice", "project-1", "bob", Permission.READ
            )
        )
        self.assertFalse(self.db.check_access("bob", "project-1", Permission.READ))
        permission_events = [
            event
            for event in self.db.audit_events("alice", "project-1")
            if event["event_type"] in {"permission.granted", "permission.revoked"}
        ]
        self.assertEqual(
            [(event["event_type"], event["metadata"]) for event in permission_events],
            [
                ("permission.granted", {"permission": "read"}),
                ("permission.revoked", {"permission": "read"}),
            ],
        )

    def test_approved_root_register_revoke_and_audit_persistence(self) -> None:
        root_path = self.directory / "approved"
        root_path.mkdir()
        root = self.db.register_approved_root("alice", "project-1", root_path)
        duplicate = self.db.register_approved_root("alice", "project-1", root_path)
        self.assertEqual(root.id, duplicate.id)
        self.assertEqual(len(self.db.list_approved_roots("alice", "project-1")), 1)
        self.assertTrue(self.db.revoke_approved_root("alice", "project-1", root.id))
        self.assertFalse(self.db.revoke_approved_root("alice", "project-1", root.id))
        self.assertEqual(self.db.list_approved_roots("alice", "project-1"), [])
        candidate = root_path / "note.txt"
        candidate.write_text("synthetic", encoding="utf-8")
        with self.assertRaises(AccessDenied):
            self.db.resolve_approved_path(
                "alice", "project-1", root.id, candidate
            )
        events = self.db.audit_events("alice", "project-1")
        kinds = [event["event_type"] for event in events]
        self.assertIn("source_root.registered", kinds)
        self.assertIn("source_root.revoked", kinds)
        self.assertFalse(any("canonical_path" in str(event["metadata"]) for event in events))

    def test_approved_path_rejects_traversal_and_outside_symlink(self) -> None:
        approved = self.directory / "approved"
        outside = self.directory / "outside"
        approved.mkdir()
        outside.mkdir()
        inside_file = approved / "note.txt"
        inside_file.write_text("synthetic", encoding="utf-8")
        outside_file = outside / "secret.txt"
        outside_file.write_text("synthetic", encoding="utf-8")
        root = self.db.register_approved_root("alice", "project-1", approved)
        self.assertEqual(
            self.db.resolve_approved_path(
                "alice", "project-1", root.id, inside_file
            ),
            inside_file.resolve(),
        )
        with self.assertRaises(InvalidPath):
            self.db.resolve_approved_path(
                "alice", "project-1", root.id, approved / ".." / "outside" / "secret.txt"
            )

        link = approved / "outside-link.txt"
        try:
            link.symlink_to(outside_file)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable in this environment")
        with self.assertRaises(InvalidPath):
            self.db.resolve_approved_path("alice", "project-1", root.id, link)

    def test_symbolic_link_root_is_rejected_when_supported(self) -> None:
        target = self.directory / "target"
        target.mkdir()
        link = self.directory / "root-link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable in this environment")
        with self.assertRaises(InvalidPath):
            self.db.register_approved_root("alice", "project-1", link)

    def test_unicode_and_sql_parameterization(self) -> None:
        name = "عربي English '); DROP TABLE workspaces; --"
        workspace = self.db.create_workspace(
            name, WorkspaceKind.PERSONAL, "alice", "unicode-1"
        )
        self.assertEqual(self.db.get_workspace("alice", workspace.id).name, name)
        with self.db._connection() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM workspaces WHERE id = ?", ("unicode-1",)
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = ? AND name = ?",
                    ("table", "workspaces"),
                ).fetchone()[0],
                1,
            )

    def test_audit_event_survives_reopening_database(self) -> None:
        self.db.grant_permission("alice", "project-1", "bob", Permission.READ)
        reopened = Database(self.db_path)
        events = reopened.audit_events("alice", "project-1")
        self.assertEqual(events[-1]["event_type"], "permission.granted")
        self.assertEqual(events[-1]["metadata"], {"permission": "read"})

    def test_backup_and_restore(self) -> None:
        self.db.grant_permission("alice", "project-1", "bob", Permission.READ)
        with self.assertRaises(AccessDenied):
            self.db.backup_to(self.directory / "unauthorized-backup.sqlite3", "bob")
        backup = self.db.backup_to(self.directory / "backup.sqlite3", actor_id="alice")
        restored_path = self.directory / "restored.sqlite3"
        Database.restore_from_backup(backup, restored_path)
        restored = Database(restored_path)
        self.assertEqual(
            restored.get_workspace("alice", "project-1").name,
            self.workspace.name,
        )
        self.assertTrue(restored.check_access("bob", "project-1", Permission.READ))
        self.assertEqual(
            restored.audit_events("alice", "project-1")[-1]["event_type"],
            "permission.granted",
        )

    def _create_existing_destination(self) -> tuple[Path, Database]:
        destination = self.directory / "existing.sqlite3"
        existing = Database(destination)
        existing.initialize()
        existing.create_workspace(
            "Must remain", WorkspaceKind.PERSONAL, "existing-owner", "existing-1"
        )
        return destination, existing

    def test_invalid_backup_does_not_replace_existing_destination(self) -> None:
        destination, existing = self._create_existing_destination()
        original_bytes = destination.read_bytes()
        invalid_backup = self.directory / "invalid-backup.sqlite3"
        invalid_backup.write_bytes(b"not a Jarvis SQLite backup")
        with self.assertRaises(sqlite3.DatabaseError):
            Database.restore_from_backup(invalid_backup, destination)
        self.assertEqual(destination.read_bytes(), original_bytes)
        self.assertEqual(
            existing.get_workspace("existing-owner", "existing-1").name, "Must remain"
        )
        self.assertFalse(Path(str(destination) + ".restore-tmp").exists())

    def test_corrupt_backup_does_not_replace_existing_destination(self) -> None:
        destination, existing = self._create_existing_destination()
        original_bytes = destination.read_bytes()
        valid_backup = self.db.backup_to(
            self.directory / "valid-before-corruption.sqlite3", actor_id="alice"
        )
        corrupt_backup = self.directory / "corrupt-backup.sqlite3"
        corrupt_backup.write_bytes(valid_backup.read_bytes()[:100])
        real_connect = sqlite3.connect
        opened_connections = []

        def track_connection(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            opened_connections.append(connection)
            return connection

        with patch("jarvis_core.storage.sqlite3.connect", side_effect=track_connection):
            with self.assertRaises(sqlite3.DatabaseError):
                Database.restore_from_backup(corrupt_backup, destination)
        self.assertEqual(len(opened_connections), 2)
        for connection in opened_connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
        self.assertEqual(destination.read_bytes(), original_bytes)
        self.assertEqual(
            existing.get_workspace("existing-owner", "existing-1").name, "Must remain"
        )
        self.assertFalse(Path(str(destination) + ".restore-tmp").exists())

    def test_backup_closes_source_and_target_when_backup_fails(self) -> None:
        real_connect = sqlite3.connect
        opened_connections = []

        class FailingBackupConnection(sqlite3.Connection):
            def backup(self, target, *args, **kwargs):
                raise sqlite3.OperationalError("synthetic backup failure")

        def track_connection(*args, **kwargs):
            connection = real_connect(*args, factory=FailingBackupConnection, **kwargs)
            opened_connections.append(connection)
            return connection

        destination = self.directory / "backup-failure.sqlite3"
        with patch("jarvis_core.storage.sqlite3.connect", side_effect=track_connection):
            with self.assertRaisesRegex(sqlite3.OperationalError, "backup failure"):
                self.db.backup_to(destination, actor_id="alice")
        self.assertGreaterEqual(len(opened_connections), 3)
        for connection in opened_connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_restore_closes_source_when_target_connection_open_fails(self) -> None:
        backup = self.db.backup_to(self.directory / "source.sqlite3", actor_id="alice")
        destination, existing = self._create_existing_destination()
        original_bytes = destination.read_bytes()
        real_connect = sqlite3.connect
        opened_source: list[sqlite3.Connection] = []

        def fail_target(path, *args, **kwargs):
            if str(path).endswith(".restore-tmp"):
                raise sqlite3.OperationalError("synthetic target-open failure")
            connection = real_connect(path, *args, **kwargs)
            opened_source.append(connection)
            return connection

        with patch("jarvis_core.storage.sqlite3.connect", side_effect=fail_target):
            with self.assertRaisesRegex(sqlite3.OperationalError, "target-open failure"):
                Database.restore_from_backup(backup, destination)

        self.assertEqual(len(opened_source), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened_source[0].execute("SELECT 1")
        self.assertEqual(destination.read_bytes(), original_bytes)
        self.assertEqual(
            existing.get_workspace("existing-owner", "existing-1").name, "Must remain"
        )
        self.assertFalse(Path(str(destination) + ".restore-tmp").exists())

    def test_default_database_path_uses_synthetic_posix_and_windows_settings(self) -> None:
        storage_module = __import__("jarvis_core.storage", fromlist=["default_database_path"])
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/synthetic/data"}, clear=True):
            with patch.object(
                storage_module, "os", SimpleNamespace(name="posix", environ=os.environ)
            ):
                posix_path = default_database_path()
        self.assertEqual(
            posix_path,
            Path("/synthetic/data/jarvis/jarvis.sqlite3"),
        )

        with patch.dict(
            os.environ,
            {"LOCALAPPDATA": "C:/Synthetic/User/AppData/Local"},
            clear=True,
        ):
            with patch.object(
                storage_module, "os", SimpleNamespace(name="nt", environ=os.environ)
            ):
                windows_path = default_database_path()
        self.assertEqual(
            str(windows_path).replace("\\", "/"),
            "C:/Synthetic/User/AppData/Local/Jarvis/jarvis.sqlite3",
        )

    def test_core_storage_operations_do_not_use_network(self) -> None:
        with patch.object(socket, "socket", side_effect=AssertionError("network attempted")):
            with patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("network attempted"),
            ):
                path = self.directory / "offline.sqlite3"
                db = Database(path)
                db.initialize()
                workspace = db.create_workspace(
                    "Offline", WorkspaceKind.PERSONAL, "offline-user"
                )
                self.assertEqual(
                    db.get_workspace("offline-user", workspace.id).name, "Offline"
                )

    def test_v10_upgrade_drops_tombstone_goal_sha256_and_v11_failure_rolls_back(self) -> None:
        path = self.directory / "v10-tombstone-privacy.sqlite3"
        version_ten = Database(path, migrations=DEFAULT_MIGRATIONS[:10])
        version_ten.initialize()
        version_ten.create_workspace(
            "Preserved V10", WorkspaceKind.PROJECT, "upgrade-owner", "upgrade-v10"
        )
        preview_sha = "a" * 64
        goal_sha = "b" * 64
        with version_ten._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 10)
            connection.execute(
                "INSERT INTO orchestration_purge_operations("
                "purge_id,workspace_id,actor_id,idempotency_key,preview_sha256,"
                "task_count,plan_count,subtask_count,dependency_count,"
                "approval_count,event_count,purged_at) "
                "VALUES('purge-v10-1','upgrade-v10','upgrade-owner','idem-v10-1',?,"
                "2,3,5,1,2,9,'2026-02-01T00:00:00+00:00')",
                (preview_sha,),
            )
            connection.execute(
                "INSERT INTO orchestration_purge_tombstones("
                "workspace_id,task_id,purge_id,task_state,archived_at,goal_sha256,"
                "plan_count,subtask_count,dependency_count,approval_count,"
                "event_count,purged_by,purged_at) "
                "VALUES('upgrade-v10','task-v10-1','purge-v10-1','completed',"
                "'2026-01-20T00:00:00+00:00',?,2,3,1,1,5,'upgrade-owner','2026-02-01T00:00:00+00:00')",
                (goal_sha,),
            )
            connection.execute(
                "INSERT INTO orchestration_purge_tombstones("
                "workspace_id,task_id,purge_id,task_state,archived_at,goal_sha256,"
                "plan_count,subtask_count,dependency_count,approval_count,"
                "event_count,purged_by,purged_at) "
                "VALUES('upgrade-v10','task-v10-2','purge-v10-1','cancelled',"
                "NULL,?,1,2,0,1,4,'upgrade-owner','2026-02-01T00:00:00+00:00')",
                (goal_sha,),
            )
            connection.commit()

        broken_v11 = Database(path, migrations=(
            *DEFAULT_MIGRATIONS[:10],
            Migration(11, (
                "CREATE TABLE tombstone_upgrade_probe (id INTEGER PRIMARY KEY)",
                "DROP TABLE orchestration_purge_tombstones",
                "INVALID V11 SQL",
            )),
        ))
        with self.assertRaises(sqlite3.OperationalError):
            broken_v11.initialize()
        with broken_v11._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 10)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(orchestration_purge_tombstones)")}
            self.assertIn("goal_sha256", columns)
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                ("tombstone_upgrade_probe",),
            ).fetchone())
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM orchestration_purge_tombstones").fetchone()[0],
                2,
            )

        upgraded = Database(path, migrations=DEFAULT_MIGRATIONS[:11])
        upgraded.initialize()
        upgraded.initialize()
        with upgraded._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 11)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(orchestration_purge_tombstones)")}
            self.assertNotIn("goal_sha256", columns)
            self.assertEqual(
                columns,
                {
                    "workspace_id", "task_id", "purge_id", "task_state", "archived_at",
                    "plan_count", "subtask_count", "dependency_count", "approval_count",
                    "event_count", "purged_by", "purged_at",
                },
            )
            rows = connection.execute(
                "SELECT * FROM orchestration_purge_tombstones ORDER BY task_id"
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(
                dict(rows[0]),
                {
                    "workspace_id": "upgrade-v10",
                    "task_id": "task-v10-1",
                    "purge_id": "purge-v10-1",
                    "task_state": "completed",
                    "archived_at": "2026-01-20T00:00:00+00:00",
                    "plan_count": 2,
                    "subtask_count": 3,
                    "dependency_count": 1,
                    "approval_count": 1,
                    "event_count": 5,
                    "purged_by": "upgrade-owner",
                    "purged_at": "2026-02-01T00:00:00+00:00",
                },
            )
            self.assertEqual(rows[1]["task_id"], "task-v10-2")
            self.assertIsNone(rows[1]["archived_at"])
            self.assertEqual(rows[1]["event_count"], 4)
            indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            self.assertIn("orchestration_purge_tombstones_workspace", indexes)
            self.assertIsNone(connection.execute("PRAGMA foreign_key_check").fetchone())

            # Immutability triggers, CHECK constraints, and FK constraints remain enforced after rebuild.
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE orchestration_purge_tombstones SET task_state='draft' WHERE task_id='task-v10-1'"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM orchestration_purge_tombstones WHERE task_id='task-v10-1'"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO orchestration_purge_tombstones("
                    "workspace_id,task_id,purge_id,task_state,archived_at,"
                    "plan_count,subtask_count,dependency_count,approval_count,"
                    "event_count,purged_by,purged_at) "
                    "VALUES('upgrade-v10','bad-count','purge-v10-1','completed',NULL,"
                    "-1,0,0,0,0,'upgrade-owner','2026-02-01T00:00:00+00:00')"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO orchestration_purge_tombstones("
                    "workspace_id,task_id,purge_id,task_state,archived_at,"
                    "plan_count,subtask_count,dependency_count,approval_count,"
                    "event_count,purged_by,purged_at) "
                    "VALUES('upgrade-v10','bad-fk','missing-purge-id','completed',NULL,"
                    "0,0,0,0,0,'upgrade-owner','2026-02-01T00:00:00+00:00')"
                )

    def test_v10_and_v11_backup_restore_compatibility_and_tampered_v11_rejection(self) -> None:
        v10_path = self.directory / "source-v10.sqlite3"
        v10_db = Database(v10_path, migrations=DEFAULT_MIGRATIONS[:10])
        v10_db.initialize()
        v10_db.create_workspace("Backup V10", WorkspaceKind.PROJECT, "alice", "ws-b10")
        with v10_db._connection() as connection:
            connection.execute(
                "INSERT INTO orchestration_purge_operations("
                "purge_id,workspace_id,actor_id,idempotency_key,preview_sha256,"
                "task_count,plan_count,subtask_count,dependency_count,"
                "approval_count,event_count,purged_at) "
                "VALUES('purge-b10','ws-b10','alice','idem-b10',?,"
                "1,1,2,0,1,4,'2026-02-02T00:00:00+00:00')",
                ("c" * 64,),
            )
            connection.execute(
                "INSERT INTO orchestration_purge_tombstones("
                "workspace_id,task_id,purge_id,task_state,archived_at,goal_sha256,"
                "plan_count,subtask_count,dependency_count,approval_count,"
                "event_count,purged_by,purged_at) "
                "VALUES('ws-b10','task-b10','purge-b10','completed',NULL,?,"
                "1,2,0,1,4,'alice','2026-02-02T00:00:00+00:00')",
                ("d" * 64,),
            )
            connection.commit()

        v10_backup = v10_db.backup_to(self.directory / "v10.backup.sqlite3", actor_id="alice")

        # Valid v10 backup restores cleanly and upgrades safely to v11.
        restored_v10_path = self.directory / "restored-v10.sqlite3"
        Database.restore_from_backup(v10_backup, restored_v10_path)
        conn = sqlite3.connect(restored_v10_path)
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 10)
        finally:
            conn.close()

        upgraded_v11 = Database(restored_v10_path, migrations=DEFAULT_MIGRATIONS[:11])
        upgraded_v11.initialize()
        with upgraded_v11._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 11)
            tombstone_cols = {row[1] for row in connection.execute("PRAGMA table_info(orchestration_purge_tombstones)")}
            self.assertNotIn("goal_sha256", tombstone_cols)
            row = connection.execute(
                "SELECT task_id,purge_id,event_count FROM orchestration_purge_tombstones WHERE workspace_id='ws-b10'"
            ).fetchone()
            self.assertEqual((row["task_id"], row["purge_id"], row["event_count"]), ("task-b10", "purge-b10", 4))

        # Valid v11 backup restores cleanly without goal_sha256.
        v11_backup = upgraded_v11.backup_to(self.directory / "v11.backup.sqlite3", actor_id="alice")
        restored_v11_path = self.directory / "restored-v11.sqlite3"
        Database.restore_from_backup(v11_backup, restored_v11_path)
        with Database(restored_v11_path)._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 11)
            tombstone_cols = {row[1] for row in connection.execute("PRAGMA table_info(orchestration_purge_tombstones)")}
            self.assertNotIn("goal_sha256", tombstone_cols)

        destination, existing = self._create_existing_destination()
        original_bytes = destination.read_bytes()

        # Tampered v11 backup 1: claims user_version=11 while retaining goal_sha256 column.
        tampered_has_hash = self.directory / "tampered-v11-goal-hash.sqlite3"
        tampered_has_hash.write_bytes(v10_backup.read_bytes())
        conn = sqlite3.connect(tampered_has_hash)
        try:
            conn.execute("PRAGMA user_version = 11")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaisesRegex(sqlite3.DatabaseError, "goal_sha256"):
            Database.restore_from_backup(tampered_has_hash, destination)

        # Tampered v11 backup 2: missing immutability trigger.
        tampered_no_trigger = self.directory / "tampered-v11-no-trigger.sqlite3"
        tampered_no_trigger.write_bytes(v11_backup.read_bytes())
        conn = sqlite3.connect(tampered_no_trigger)
        try:
            conn.execute("DROP TRIGGER orchestration_purge_tombstones_no_delete")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaisesRegex(sqlite3.DatabaseError, "tombstone protections"):
            Database.restore_from_backup(tampered_no_trigger, destination)

        # Tampered v11 backup 3: missing required tombstone column.
        tampered_missing_col = self.directory / "tampered-v11-missing-col.sqlite3"
        tampered_missing_col.write_bytes(v11_backup.read_bytes())
        conn = sqlite3.connect(tampered_missing_col)
        try:
            conn.execute("DROP TABLE orchestration_purge_tombstones")
            conn.execute(
                "CREATE TABLE orchestration_purge_tombstones ("
                "workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, purge_id TEXT NOT NULL, "
                "PRIMARY KEY(workspace_id,task_id))"
            )
            conn.execute(
                "CREATE TRIGGER orchestration_purge_tombstones_no_update "
                "BEFORE UPDATE ON orchestration_purge_tombstones BEGIN SELECT RAISE(ABORT, 'x'); END"
            )
            conn.execute(
                "CREATE TRIGGER orchestration_purge_tombstones_no_delete "
                "BEFORE DELETE ON orchestration_purge_tombstones BEGIN SELECT RAISE(ABORT, 'x'); END"
            )
            conn.commit()
        finally:
            conn.close()
        with self.assertRaisesRegex(sqlite3.DatabaseError, "missing required schema"):
            Database.restore_from_backup(tampered_missing_col, destination)

        # Tampered v10 backup: claims user_version=10 but lacks v10 goal_sha256 column.
        tampered_v10 = self.directory / "tampered-v10-no-hash.sqlite3"
        tampered_v10.write_bytes(v11_backup.read_bytes())
        conn = sqlite3.connect(tampered_v10)
        try:
            conn.execute("PRAGMA user_version = 10")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaisesRegex(sqlite3.DatabaseError, "missing required schema"):
            Database.restore_from_backup(tampered_v10, destination)

        # Destination remained untouched across all rejected restores.
        self.assertEqual(destination.read_bytes(), original_bytes)
        self.assertEqual(existing.get_workspace("existing-owner", "existing-1").name, "Must remain")


if __name__ == "__main__":
    unittest.main()
