from __future__ import annotations

from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from jarvis_core.coding_agents import (
    CodingAuthorizationDenied,
    CodingCapability,
    CodingCapabilityPolicy,
)
from jarvis_core.developer import DeveloperRepositoryService
from jarvis_core.storage import (
    AccessDenied, Database, DEFAULT_MIGRATIONS, Migration, Permission, WorkspaceKind,
)


class CodingCapabilityPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.db = Database(self.base / "jarvis.sqlite3")
        self.db.initialize()
        self.db.create_workspace("Synthetic Coding", WorkspaceKind.PROJECT,
                                 "owner", "ws-code")
        self.root = self.base / "approved"
        self.root.mkdir()
        self.root_record = self.db.register_approved_root("owner", "ws-code", self.root)
        self.repo_path = self.root / "synthetic"
        self.repo_path.mkdir()
        self._git("init", "-q")
        self._git("config", "user.name", "Synthetic")
        self._git("config", "user.email", "synthetic@example.invalid")
        (self.repo_path / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
        self._git("add", "main.py")
        self._git("commit", "-qm", "synthetic baseline")
        self.repositories = DeveloperRepositoryService(self.db)
        registered = self.repositories.register(
            "owner", "ws-code", self.root_record.id, self.repo_path, confirmed=True)
        self.repository = self.repositories.index("owner", "ws-code", registered.id)
        self.policy = CodingCapabilityPolicy(self.db)
        self.db.grant_permission("owner", "ws-code", "reader", Permission.READ)
        self.db.grant_permission("owner", "ws-code", "reader", Permission.WRITE)

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *args):
        subprocess.run(["git", "-C", str(self.repo_path), *args], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_memory_write_and_workspace_read_do_not_grant_code_authority(self):
        self.assertTrue(self.db.check_access("reader", "ws-code", Permission.READ))
        self.assertTrue(self.db.check_access("reader", "ws-code", Permission.WRITE))
        for capability in (CodingCapability.READ_REPOSITORY_METADATA,
                           CodingCapability.READ_INDEXED_SOURCE,
                           CodingCapability.PROPOSE_CODE_MODIFICATIONS):
            self.assertFalse(self.policy.is_granted(
                "reader", "ws-code", self.repository.id, capability))
            with self.assertRaises(CodingAuthorizationDenied):
                self.policy.require("reader", "ws-code", self.repository.id, capability)

    def test_manager_must_explicitly_grant_one_repository_capability(self):
        with self.assertRaises(CodingAuthorizationDenied):
            self.policy.grant("owner", "ws-code", self.repository.id, "reader",
                              CodingCapability.READ_INDEXED_SOURCE, confirmed=False)
        with self.assertRaises(AccessDenied):
            self.policy.grant("reader", "ws-code", self.repository.id, "reader",
                              CodingCapability.READ_INDEXED_SOURCE, confirmed=True)
        grant = self.policy.grant(
            "owner", "ws-code", self.repository.id, "reader",
            CodingCapability.READ_INDEXED_SOURCE, confirmed=True)
        self.assertEqual(grant.capability, CodingCapability.READ_INDEXED_SOURCE)
        self.policy.require("reader", "ws-code", self.repository.id,
                            CodingCapability.READ_INDEXED_SOURCE)
        self.assertFalse(self.policy.is_granted(
            "reader", "ws-code", self.repository.id,
            CodingCapability.READ_REPOSITORY_METADATA))

    def test_write_execution_and_external_capabilities_fail_closed(self):
        for capability in (
            CodingCapability.CREATE_ISOLATED_WORKTREE,
            CodingCapability.MODIFY_TASK_WORKTREE,
            CodingCapability.EXECUTE_VERIFICATION,
            CodingCapability.REQUEST_NETWORK_ACCESS,
            CodingCapability.CREATE_COMMIT,
            CodingCapability.PUSH_BRANCH,
            CodingCapability.OPEN_PULL_REQUEST,
            CodingCapability.MERGE_OR_DEPLOY,
        ):
            with self.assertRaises(CodingAuthorizationDenied):
                self.policy.grant("owner", "ws-code", self.repository.id, "reader",
                                  capability, confirmed=True)
            self.assertFalse(self.policy.is_granted(
                "owner", "ws-code", self.repository.id, capability))

    def test_grant_revoke_and_audit_are_repository_scoped(self):
        grant = self.policy.grant(
            "owner", "ws-code", self.repository.id, "reader",
            CodingCapability.READ_REPOSITORY_METADATA, confirmed=True)
        grants = self.policy.list_grants("owner", "ws-code", self.repository.id)
        self.assertEqual([entry.id for entry in grants], [grant.id])
        with self.assertRaises(AccessDenied):
            self.policy.list_grants("reader", "ws-code", self.repository.id)
        with self.assertRaises(CodingAuthorizationDenied):
            self.policy.require("reader", "wrong-workspace", self.repository.id,
                                CodingCapability.READ_REPOSITORY_METADATA)
        self.assertTrue(self.policy.revoke("owner", grant.id, confirmed=True))
        self.assertFalse(self.policy.is_granted(
            "reader", "ws-code", self.repository.id,
            CodingCapability.READ_REPOSITORY_METADATA))
        self.assertFalse(self.policy.revoke("owner", grant.id, confirmed=True))
        with self.db._connection() as connection:
            event_types = {row[0] for row in connection.execute(
                "SELECT event_type FROM audit_events WHERE workspace_id='ws-code'")}
        self.assertTrue({"coding.capability_granted", "coding.capability_revoked"} <= event_types)

    def test_manifest_separates_requested_granted_denied_and_unavailable(self):
        self.policy.grant(
            "owner", "ws-code", self.repository.id, "reader",
            CodingCapability.READ_REPOSITORY_METADATA, confirmed=True)
        manifest = self.policy.manifest("reader", "ws-code", self.repository.id, [
            CodingCapability.READ_REPOSITORY_METADATA,
            CodingCapability.PROPOSE_CODE_MODIFICATIONS,
            CodingCapability.MODIFY_TASK_WORKTREE,
        ])
        self.assertEqual(manifest["granted"], ["read_repository_metadata"])
        self.assertEqual(manifest["denied"], ["propose_code_modifications"])
        self.assertEqual(manifest["unavailable"], ["modify_task_worktree"])
        self.assertFalse(manifest["agent_may_approve"])
        self.assertFalse(manifest["execution_enabled"])

    def test_v7_upgrade_rollback_backup_and_restore_include_p4_policy(self):
        path = self.base / "migration.sqlite3"
        v7 = Database(path, migrations=DEFAULT_MIGRATIONS[:7])
        v7.initialize()
        v7.create_workspace("Migration", WorkspaceKind.PERSONAL, "manager", "ws-migration")
        faulty = Database(path, migrations=(*DEFAULT_MIGRATIONS[:7], Migration(
            8, (*DEFAULT_MIGRATIONS[7].statements, "INVALID SQL"))))
        with self.assertRaises(sqlite3.OperationalError):
            faulty.initialize()
        with sqlite3.connect(path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 7)
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='coding_tasks'").fetchone())
        Database(path).initialize()
        grant = self.policy.grant(
            "owner", "ws-code", self.repository.id, "reader",
            CodingCapability.READ_INDEXED_SOURCE, confirmed=True)
        backup = self.db.backup_to(self.base / "p4-backup.sqlite3", actor_id="owner")
        restored = Database.restore_from_backup(backup, self.base / "p4-restored.sqlite3")
        restored_db = Database(restored)
        self.assertTrue(CodingCapabilityPolicy(restored_db).is_granted(
            "reader", "ws-code", self.repository.id,
            CodingCapability.READ_INDEXED_SOURCE))
        with restored_db._connection() as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT id FROM coding_repository_grants WHERE id=?", (grant.id,)).fetchone())
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
