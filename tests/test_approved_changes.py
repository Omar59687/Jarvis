from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from jarvis_core.approved_changes import ApprovedFileChangeService
from jarvis_core.coding_agents import (
    CodingAuthorizationDenied, CodingCapability, CodingCapabilityPolicy,
)
from jarvis_core.developer import DeveloperRepositoryService, RepositoryError
from jarvis_core.storage import AccessDenied, Database, DEFAULT_MIGRATIONS, Permission, WorkspaceKind


class ApprovedFileChangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.db = Database(self.base / "jarvis.sqlite3")
        self.db.initialize()
        self.db.create_workspace("Synthetic workspace", WorkspaceKind.PROJECT, "owner", "ws-m06")
        self.root = self.base / "approved"
        self.root.mkdir()
        self.root_record = self.db.register_approved_root("owner", "ws-m06", self.root)
        self.repo_path = self.root / "repo"
        self.repo_path.mkdir()
        self._git("init", "-q")
        self._git("config", "user.name", "Synthetic")
        self._git("config", "user.email", "synthetic@example.invalid")
        (self.repo_path / "main.py").write_text("VALUE = 1\n", encoding="utf-8", newline="")
        self._git("add", "main.py")
        self._git("commit", "-qm", "base")
        self.repositories = DeveloperRepositoryService(self.db)
        registered = self.repositories.register(
            "owner", "ws-m06", self.root_record.id, self.repo_path, confirmed=True)
        self.repository = self.repositories.index("owner", "ws-m06", registered.id)
        self.policy = CodingCapabilityPolicy(self.db)
        self.service = ApprovedFileChangeService(self.db, self.repositories, self.policy)
        self.db.grant_permission("owner", "ws-m06", "reader", Permission.READ)
        self.db.grant_permission("owner", "ws-m06", "reader", Permission.WRITE)
        self.policy.grant("owner", "ws-m06", self.repository.id, "owner",
                          CodingCapability.READ_REPOSITORY_METADATA, confirmed=True)
        self.policy.grant("owner", "ws-m06", self.repository.id, "owner",
                          CodingCapability.READ_INDEXED_SOURCE, confirmed=True)
        self.policy.grant("owner", "ws-m06", self.repository.id, "owner",
                          CodingCapability.PROPOSE_CODE_MODIFICATIONS, confirmed=True)
        self.policy.grant("owner", "ws-m06", self.repository.id, "owner",
                          CodingCapability.APPLY_APPROVED_FILE_CHANGES, confirmed=True)

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *args):
        subprocess.run(["git", "-C", str(self.repo_path), *args], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _proposal(self, ops=None):
        return self.service.propose("owner", "ws-m06", self.repository.id, ops or [
            {"path": "main.py", "operation": "modify", "content": "VALUE = 2\n"},
        ])

    def _approve(self, proposal, **kwargs):
        return self.service.approve_and_apply(
            "owner", "ws-m06", proposal["id"], proposal["patch_sha256"],
            confirmed=True, **kwargs)

    def test_v13_migration_preserves_grants_and_adds_file_change_schema(self):
        path = self.base / "schema-v13.sqlite3"
        legacy = Database(path, migrations=DEFAULT_MIGRATIONS[:13])
        legacy.initialize()
        legacy.create_workspace("Legacy workspace", WorkspaceKind.PROJECT, "owner", "ws-legacy")
        approved_root = self.base / "legacy-approved"
        approved_root.mkdir()
        root_record = legacy.register_approved_root("owner", "ws-legacy", approved_root)
        repo = approved_root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (repo / "main.py").write_text("VALUE = 1\\n", encoding="utf-8", newline="")
        subprocess.run(["git", "-C", str(repo), "add", "main.py"], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                        "user.email=test@example.invalid", "commit", "-qm", "base"], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        repositories = DeveloperRepositoryService(legacy)
        registered = repositories.register("owner", "ws-legacy", root_record.id, repo, confirmed=True)
        policy = CodingCapabilityPolicy(legacy)
        prior = policy.grant("owner", "ws-legacy", registered.id, "owner",
                             CodingCapability.READ_REPOSITORY_METADATA, confirmed=True)

        upgraded = Database(path)
        upgraded.initialize()
        upgraded_policy = CodingCapabilityPolicy(upgraded)
        self.assertTrue(upgraded_policy.is_granted(
            "owner", "ws-legacy", registered.id, CodingCapability.READ_REPOSITORY_METADATA))
        self.assertTrue(upgraded.check_access("owner", "ws-legacy", Permission.READ))
        self.assertTrue(upgraded.check_access("owner", "ws-legacy", Permission.WRITE))
        with upgraded._connection() as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT id FROM coding_repository_grants WHERE id=?", (prior.id,)).fetchone())
            self.assertIsNotNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='coding_file_change_proposals'"
            ).fetchone())
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
    def test_proposal_is_review_only_until_digest_bound_approval(self):
        proposal = self._proposal()
        self.assertEqual(proposal["status"], "pending_approval")
        self.assertIn("VALUE = 2", proposal["diff"])
        self.assertEqual((self.repo_path / "main.py").read_text(), "VALUE = 1\n")
        result = self._approve(proposal)
        self.assertEqual(result["status"], "applied")
        self.assertEqual((self.repo_path / "main.py").read_text(), "VALUE = 1\n")
        output = self.service.get_output("owner", "ws-m06", proposal["id"])
        self.assertFalse(output["source_repository_changed"])
        self.assertEqual(output["files"][0]["content"], "VALUE = 2\n")
        events = self.service.audit("owner", "ws-m06", proposal["id"])
        self.assertEqual([e["event_type"] for e in events], [
            "file_change_proposed", "file_change_approved", "file_change_applied"])
        self.assertNotIn("VALUE = 2", json.dumps(events))
        self.assertEqual(events[2]["metadata"]["targets"],
                         [{"path": "main.py", "operation": "modify"}])

    def test_rejects_missing_approval_wrong_digest_and_replay(self):
        proposal = self._proposal()
        with self.assertRaises(CodingAuthorizationDenied):
            self.service.approve_and_apply("owner", "ws-m06", proposal["id"],
                                           proposal["patch_sha256"], confirmed=False)
        with self.assertRaises(CodingAuthorizationDenied):
            self.service.approve_and_apply("owner", "ws-m06", proposal["id"],
                                           "0" * 64, confirmed=True)
        self._approve(proposal)
        with self.assertRaises(CodingAuthorizationDenied):
            self._approve(proposal)

    def test_create_and_delete_require_path_preconditions_and_delete_specific_confirmation(self):
        create = self._proposal([{"path": "new_module.py", "operation": "create",
                                  "content": "VALUE = 3\n"}])
        self.assertFalse((self.repo_path / "new_module.py").exists())
        self._approve(create)
        self.assertFalse((self.repo_path / "new_module.py").exists())
        self.assertEqual(self.service.get_output("owner", "ws-m06", create["id"])["files"][0]["content"],
                         "VALUE = 3\n")
        delete = self._proposal([{"path": "main.py", "operation": "delete", "content": None}])
        with self.assertRaisesRegex(CodingAuthorizationDenied, "delete_confirmation_required"):
            self._approve(delete)
        self.assertTrue((self.repo_path / "main.py").exists())
        self._approve(delete, delete_confirmed=True)
        self.assertTrue((self.repo_path / "main.py").exists())
        self.assertIsNone(self.service.get_output("owner", "ws-m06", delete["id"])["files"][0]["content"])

    def test_cross_workspace_and_cross_principal_access_are_denied(self):
        proposal = self._proposal()
        self.db.create_workspace("Other", WorkspaceKind.PROJECT, "owner", "ws-other")
        self.db.grant_permission("owner", "ws-other", "reader", Permission.READ)
        with self.assertRaises(AccessDenied):
            self.service.get("reader", "ws-m06", proposal["id"])
        with self.assertRaises(AccessDenied):
            self.service.get("owner", "ws-other", proposal["id"])

    def test_write_capability_is_distinct_from_workspace_write(self):
        self.policy.revoke("owner", next(g.id for g in self.policy.list_grants(
            "owner", "ws-m06", self.repository.id)
            if g.capability is CodingCapability.APPLY_APPROVED_FILE_CHANGES), confirmed=True)
        proposal = self._proposal()
        with self.assertRaises(CodingAuthorizationDenied):
            self._approve(proposal)
        self.assertEqual((self.repo_path / "main.py").read_text(), "VALUE = 1\n")

    def test_capability_revocation_before_approval_commit_is_rechecked(self):
        proposal = self._proposal()
        grant_id = next(g.id for g in self.policy.list_grants(
            "owner", "ws-m06", self.repository.id)
            if g.capability is CodingCapability.APPLY_APPROVED_FILE_CHANGES)
        check_preconditions = self.service._check_preconditions

        def revoke_after_preconditions(root, operations):
            check_preconditions(root, operations)
            self.policy.revoke("owner", grant_id, confirmed=True)

        with patch.object(self.service, "_check_preconditions", revoke_after_preconditions):
            with self.assertRaisesRegex(CodingAuthorizationDenied, "coding_capability_denied"):
                self._approve(proposal)
        self.assertEqual((self.repo_path / "main.py").read_text(), "VALUE = 1\n")
        self.assertEqual(self.service.get("owner", "ws-m06", proposal["id"])["status"],
                         "pending_approval")

    def test_workspace_write_revocation_before_approval_commit_is_rechecked(self):
        proposal = self._proposal()
        check_preconditions = self.service._check_preconditions

        def revoke_after_preconditions(root, operations):
            check_preconditions(root, operations)
            self.db.revoke_permission("owner", "ws-m06", "owner", Permission.WRITE)

        with patch.object(self.service, "_check_preconditions", revoke_after_preconditions):
            with self.assertRaises(AccessDenied):
                self._approve(proposal)
        self.assertEqual((self.repo_path / "main.py").read_text(), "VALUE = 1\n")
        self.assertEqual(self.service.get("owner", "ws-m06", proposal["id"])["status"],
                         "pending_approval")

    def test_task_workspace_inside_registered_checkout_is_rejected(self):
        nested_db = Database(self.repo_path / "jarvis.sqlite3")
        nested_db.initialize()
        nested_db.create_workspace("Nested database", WorkspaceKind.PROJECT,
                                   "owner", "ws-nested")
        nested_root = nested_db.register_approved_root("owner", "ws-nested", self.root)
        repositories = DeveloperRepositoryService(nested_db)
        registered = repositories.register("owner", "ws-nested", nested_root.id,
                                            self.repo_path, confirmed=True)
        repository = repositories.index("owner", "ws-nested", registered.id)
        policy = CodingCapabilityPolicy(nested_db)
        for capability in (CodingCapability.READ_REPOSITORY_METADATA,
                           CodingCapability.READ_INDEXED_SOURCE,
                           CodingCapability.PROPOSE_CODE_MODIFICATIONS,
                           CodingCapability.APPLY_APPROVED_FILE_CHANGES):
            policy.grant("owner", "ws-nested", repository.id, "owner",
                         capability, confirmed=True)
        service = ApprovedFileChangeService(nested_db, repositories, policy)
        proposal = service.propose("owner", "ws-nested", repository.id, [{
            "path": "main.py", "operation": "modify", "content": "VALUE = 2\n"
        }])

        with self.assertRaisesRegex(CodingAuthorizationDenied,
                                    "task_workspace_overlaps_repository"):
            service.approve_and_apply("owner", "ws-nested", proposal["id"],
                                      proposal["patch_sha256"], confirmed=True)
        self.assertEqual((self.repo_path / "main.py").read_text(encoding="utf-8"),
                         "VALUE = 1\n")
        self.assertFalse((self.repo_path / "coding_tasks").exists())

    def test_task_workspace_failure_diagnostic_omits_filesystem_paths(self):
        with patch.object(Path, "mkdir",
                          side_effect=PermissionError(5, "denied", str(self.base))):
            with self.assertLogs("jarvis_core.approved_changes", level="WARNING") as captured:
                with self.assertRaisesRegex(CodingAuthorizationDenied,
                                            "task_workspace_unavailable"):
                    self.service._create_task_workspace(
                        "ws-m06", self.repository.id, "a" * 32)
        diagnostic = "\n".join(captured.output)
        self.assertIn("stage=create_directory", diagnostic)
        self.assertIn("exception_type=PermissionError", diagnostic)
        self.assertNotIn(str(self.base), diagnostic)

    def test_modified_payload_and_expired_approval_are_rejected(self):
        proposal = self._proposal()
        with self.db._connection() as connection:
            changed_payload = json.loads(connection.execute(
                "SELECT proposal_json FROM coding_file_change_proposals WHERE id=?",
                (proposal["id"],)).fetchone()[0])
            changed_payload["operations"][0]["after_content"] = "VALUE = 8\n"
            connection.execute("UPDATE coding_file_change_proposals SET proposal_json=? WHERE id=?",
                               (json.dumps(changed_payload), proposal["id"]))
            connection.commit()
        with self.assertRaisesRegex(CodingAuthorizationDenied, "file_change_record_invalid"):
            self.service.get("owner", "ws-m06", proposal["id"])
        expired = self._proposal()
        with self.db._connection() as connection:
            connection.execute("UPDATE coding_file_change_proposals SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                               (expired["id"],))
            connection.commit()
        with self.assertRaisesRegex(CodingAuthorizationDenied, "file_change_expired"):
            self._approve(expired)
        self.assertEqual(self.service.get("owner", "ws-m06", expired["id"])["status"], "expired")

    def test_stale_file_snapshot_and_concurrent_modification_are_rejected(self):
        proposal = self._proposal()
        (self.repo_path / "main.py").write_text("VALUE = 9\n", encoding="utf-8")
        with self.assertRaisesRegex(CodingAuthorizationDenied, "file_change_snapshot_stale"):
            self._approve(proposal)
        self.assertEqual((self.repo_path / "main.py").read_text(), "VALUE = 9\n")

    def test_symlink_escape_is_rejected(self):
        outside = self.base / "outside.py"
        outside.write_text("VALUE = 99\n", encoding="utf-8")
        try:
            (self.repo_path / "link.py").symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaises(CodingAuthorizationDenied):
            self._proposal([{"path": "link.py", "operation": "modify", "content": "VALUE = 0\n"}])
        self.assertEqual(outside.read_text(), "VALUE = 99\n")

    def test_invalid_path_and_unbounded_operations_are_rejected(self):
        for path in ("../outside.py", ".git/config", "secrets.env"):
            with self.assertRaises((CodingAuthorizationDenied, ValueError, RepositoryError)):
                self._proposal([{"path": path, "operation": "create", "content": "x = 1\n"}])
        with self.assertRaises(ValueError):
            self.service.propose("owner", "ws-m06", self.repository.id, [{}] * 21)

    def test_partial_failure_rolls_back_already_replaced_files(self):
        (self.repo_path / "other.py").write_text("OTHER = 1\n", encoding="utf-8")
        self._git("add", "other.py")
        self._git("commit", "-qm", "add second source")
        self.repository = self.repositories.index("owner", "ws-m06", self.repository.id)
        proposal = self._proposal([
            {"path": "main.py", "operation": "modify", "content": "VALUE = 2\n"},
            {"path": "other.py", "operation": "modify", "content": "OTHER = 2\n"},
        ])
        from jarvis_core.approved_changes import ApprovedFileChangeService
        original_replace = ApprovedFileChangeService._replace_existing
        calls = 0
        def fail_second(src, dst):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic replacement failure")
            return original_replace(src, dst)
        with patch("jarvis_core.approved_changes.ApprovedFileChangeService._replace_existing",
                   side_effect=fail_second):
            with self.assertRaisesRegex(CodingAuthorizationDenied, "file_change_apply_failed"):
                self._approve(proposal)
        self.assertEqual((self.repo_path / "main.py").read_text(), "VALUE = 1\n")
        self.assertEqual((self.repo_path / "other.py").read_text(), "OTHER = 1\n")
        self.assertEqual(self.service.get("owner", "ws-m06", proposal["id"])["status"], "failed")

    def test_explicit_recovery_rolls_back_interrupted_apply(self):
        proposal = self._proposal()
        with self.db._connection() as connection:
            raw = connection.execute("SELECT proposal_json FROM coding_file_change_proposals WHERE id=?",
                                     (proposal["id"],)).fetchone()[0]
            payload = json.loads(raw)
            connection.execute("UPDATE coding_file_change_proposals SET status='applying' WHERE id=?",
                               (proposal["id"],))
            connection.commit()
        task_root = self.service._create_task_workspace("ws-m06", self.repository.id, proposal["id"])
        self.service._seed_task_workspace(task_root, payload["operations"])
        (task_root / "main.py").write_text("VALUE = 2\n", encoding="utf-8", newline="")
        self.assertEqual(self.service.recover("owner", "ws-m06", proposal["id"])["status"], "recovered")
        self.assertEqual((self.repo_path / "main.py").read_text(), "VALUE = 1\n")
        self.assertFalse(task_root.exists())

    def test_local_model_proposal_is_bounded_and_never_applies_automatically(self):
        class FakeLocalGenerator:
            def __init__(self):
                self.calls = []
            def generate(self, prompt, model, **options):
                self.calls.append((prompt, model, options))
                from types import SimpleNamespace
                return SimpleNamespace(text=json.dumps({"changes": [
                    {"path": "main.py", "content": "VALUE = 7\n"}
                ]}))
        generator = FakeLocalGenerator()
        proposal = self.service.propose_from_local_model(
            "owner", "ws-m06", self.repository.id,
            "Change VALUE to seven", ["main.py"], generator, "local-test-model")
        self.assertEqual(proposal["status"], "pending_approval")
        self.assertEqual((self.repo_path / "main.py").read_text(), "VALUE = 1\n")
        self.assertEqual(generator.calls[0][1], "local-test-model")
        self.assertEqual(generator.calls[0][2]["temperature"], 0.0)
        self.assertEqual(generator.calls[0][2]["max_output_tokens"], 512)
        self.assertIn("VALUE = 1", generator.calls[0][0])
        self.assertIn("Change VALUE to seven", generator.calls[0][0])

    def test_local_model_cannot_expand_targets_or_emit_non_json_actions(self):
        class MaliciousGenerator:
            def generate(self, *_args, **_kwargs):
                from types import SimpleNamespace
                return SimpleNamespace(text=json.dumps({"changes": [
                    {"path": "../outside.py", "content": "x = 1\n"}
                ]}))
        with self.assertRaisesRegex(CodingAuthorizationDenied, "local_proposal_failed"):
            self.service.propose_from_local_model(
                "owner", "ws-m06", self.repository.id, "change it", ["main.py"],
                MaliciousGenerator(), "local-test-model")
        with self.db._connection() as connection:
            count = connection.execute("SELECT COUNT(*) FROM coding_file_change_proposals").fetchone()[0]
        self.assertEqual(count, 0)

    def test_workspace_selection_change_cancels_late_model_proposal(self):
        class FakeLocalGenerator:
            def generate(self, *_args, **_kwargs):
                from types import SimpleNamespace
                return SimpleNamespace(text=json.dumps({"changes": [
                    {"path": "main.py", "content": "VALUE = 4\n"}
                ]}))
        with self.assertRaisesRegex(CodingAuthorizationDenied, "workspace_selection_changed"):
            self.service.propose_from_local_model(
                "owner", "ws-m06", self.repository.id, "change it", ["main.py"],
                FakeLocalGenerator(), "local-test-model", context_is_current=lambda: False)
        with self.db._connection() as connection:
            count = connection.execute("SELECT COUNT(*) FROM coding_file_change_proposals").fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
