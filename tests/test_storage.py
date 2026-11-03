from __future__ import annotations

import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
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
                    connection.execute("PRAGMA user_version").fetchone()[0], 1
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

    def test_repeated_initialization_is_idempotent(self) -> None:
        for _ in range(3):
            self.db.initialize()
        with self.db._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            count = connection.execute(
                "SELECT COUNT(*) FROM workspaces WHERE id = ?", ("project-1",)
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_successful_upgrade_applies_new_migration_once(self) -> None:
        version_one = Database(self.directory / "upgrade.sqlite3")
        version_one.initialize()
        version_two_migrations = (
            *version_one._migrations,
            Migration(2, ("CREATE TABLE migration_marker (value TEXT NOT NULL)",)),
        )
        version_two = Database(
            self.directory / "upgrade.sqlite3", migrations=version_two_migrations
        )
        version_two.initialize()
        version_two.initialize()
        with version_two._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = ? AND name = ?",
                    ("table", "migration_marker"),
                ).fetchone()[0],
                1,
            )

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
        environment["PYTHONPATH"] = str(ROOT) + (os.pathsep + existing if existing else "")
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.db_path), self.workspace.id],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
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


if __name__ == "__main__":
    unittest.main()
