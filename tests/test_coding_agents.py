from __future__ import annotations

from pathlib import Path
import json
from io import StringIO
import sqlite3
import subprocess
import tempfile
from threading import Event
import unittest

from jarvis_core.coding_agents import (
    CodingAuthorizationDenied,
    CodingCapability,
    CodingCapabilityPolicy,
    AgentChange,
    FakeAgentAdapter,
    CodingTaskService,
    ReadOnlyWorktreeManager,
)
from jarvis_core.developer import DeveloperRepositoryService, RepositoryError, StaleRepository
from jarvis_core.storage import (
    AccessDenied, Database, DEFAULT_MIGRATIONS, Migration, Permission, WorkspaceKind,
)
from jarvis_core.coding_operator import CodingOperatorWorkflow


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
        self.tasks = CodingTaskService(self.db, self.repositories, self.policy)
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

    def _grant_task_read(self):
        self.policy.grant("owner", "ws-code", self.repository.id, "owner",
                          CodingCapability.READ_REPOSITORY_METADATA, confirmed=True)
        self.policy.grant("owner", "ws-code", self.repository.id, "owner",
                          CodingCapability.READ_INDEXED_SOURCE, confirmed=True)

    def _new_task(self, text="Inspect run() safely."):
        self._grant_task_read()
        self.policy.grant("owner", "ws-code", self.repository.id, "owner",
                          CodingCapability.PROPOSE_CODE_MODIFICATIONS, confirmed=True)
        return self.tasks.create_task(
            "owner", "ws-code", self.repository.id, self.repository.head_commit,
            text, requested_capabilities=[CodingCapability.READ_REPOSITORY_METADATA,
                                          CodingCapability.READ_INDEXED_SOURCE,
                                          CodingCapability.PROPOSE_CODE_MODIFICATIONS])

    def test_task_is_bound_to_authorized_repo_base_and_request_hash_only(self):
        task = self._new_task("Please inspect the synthetic run function.")
        self.assertEqual(task.status.value, "requested")
        self.assertEqual(task.base_commit, self.repository.head_commit)
        self.assertNotIn("Please inspect", json.dumps(task.manifest))
        with self.db._connection() as connection:
            row = connection.execute("SELECT * FROM coding_tasks WHERE id=?", (task.id,)).fetchone()
            self.assertEqual(len(row["request_sha256"]), 64)
            self.assertNotIn("Please inspect", row["manifest_json"])
            event = connection.execute(
                "SELECT metadata_json FROM coding_task_events WHERE task_id=?", (task.id,)
            ).fetchone()[0]
            self.assertNotIn("Please inspect", event)
        self.assertEqual(self.tasks.audit_events("owner", task.id)[0]["event_type"],
                         "task_requested")

    def test_task_denies_stale_or_malicious_repository_identity(self):
        self._grant_task_read()
        with self.assertRaises(ValueError):
            self.tasks.create_task("owner", "ws-code", self.repository.id,
                                   "--upload-pack=evil", "Inspect safely")
        with self.assertRaises(CodingAuthorizationDenied):
            self.tasks.create_task("owner", "ws-code", "../../outside",
                                   self.repository.head_commit, "Inspect safely")
        changed = self.repo_path / "main.py"
        changed.write_text("def run():\n    return 2\n", encoding="utf-8")
        self._git("add", "main.py")
        self._git("commit", "-qm", "new synthetic commit")
        with self.assertRaises(StaleRepository):
            self.tasks.create_task("owner", "ws-code", self.repository.id,
                                   self.repository.head_commit, "Inspect safely")

    def test_task_read_requires_explicit_repository_grants_and_cross_workspace_denies(self):
        with self.assertRaises(CodingAuthorizationDenied):
            self.tasks.create_task("owner", "ws-code", self.repository.id,
                                   self.repository.head_commit, "Inspect safely")
        self._grant_task_read()
        with self.assertRaises(AccessDenied):
            self.tasks.create_task("owner", "foreign-workspace", self.repository.id,
                                   self.repository.head_commit, "Inspect safely")

    def test_task_audit_rows_cannot_be_rebound_to_another_principal_or_workspace(self):
        task = self._new_task()
        with self.db._connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO coding_task_events(id,task_id,workspace_id,principal_id,event_type,"
                    "metadata_json,created_at) VALUES(?,?,?,?,?,?,?)",
                    ("bad", task.id, "ws-code", "reader", "fake", "{}", "now"),
                )

    def test_read_only_worktree_planner_detects_dirty_baseline_then_fails_closed(self):
        task = self._new_task()
        original_branches = subprocess.run(
            ["git", "-C", str(self.repo_path), "branch", "--list"], check=True,
            stdout=subprocess.PIPE, text=True).stdout
        (self.repo_path / "untracked-note.txt").write_text("dirty", encoding="utf-8")
        planner = ReadOnlyWorktreeManager(self.tasks)
        dirty = planner.plan("owner", task.id)
        self.assertEqual(dirty.state, "dirty_baseline")
        self.assertEqual(dirty.dirty_file_count, 1)
        self.assertFalse(dirty.write_available)
        (self.repo_path / "untracked-note.txt").unlink()
        clean = planner.plan("owner", task.id)
        self.assertEqual(clean.state, "blocked_isolation_unverified")
        self.assertFalse(clean.write_available)
        with self.assertRaises(CodingAuthorizationDenied):
            planner.create("owner", task.id)
        updated_branches = subprocess.run(
            ["git", "-C", str(self.repo_path), "branch", "--list"], check=True,
            stdout=subprocess.PIPE, text=True).stdout
        self.assertEqual(original_branches, updated_branches)

    def test_registered_repository_symlink_replacement_fails_closed(self):
        task = self._new_task()
        outside = self.base / "outside"
        outside.mkdir()
        (outside / ".git").mkdir()
        original = self.root / "synthetic-original"
        self.repo_path.rename(original)
        try:
            self.repo_path.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            original.rename(self.repo_path)
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaises(RepositoryError):
            ReadOnlyWorktreeManager(self.tasks).plan("owner", task.id)

    def test_fake_adapter_proposal_is_in_memory_and_human_review_is_digest_bound(self):
        task = self._new_task("Inspect run safely")
        adapter = FakeAgentAdapter((AgentChange("main.py", "def run():\n    return 2\n"),))
        proposed = self.tasks.propose_with_adapter("owner", task.id, "Inspect run safely", adapter)
        self.assertEqual(proposed.status.value, "proposed")
        self.assertEqual(adapter.calls, [task.id])
        proposal = self.tasks.view_proposal("owner", task.id)
        self.assertIn("return 1", proposal["diff"])
        self.assertIn("return 2", proposal["diff"])
        self.assertEqual(proposal["diff_sha256"], proposed.diff_sha256)
        with self.assertRaises(CodingAuthorizationDenied):
            self.tasks.decide_proposal("owner", task.id, "approve_proposal", "0" * 64,
                                       confirmed=True)
        approved = self.tasks.decide_proposal(
            "owner", task.id, "approve_proposal", proposal["diff_sha256"],
            confirmed=True, decision_id="human-decision-0001")
        self.assertEqual(approved.status.value, "approved")
        self.assertEqual(approved.approved_diff_sha256, proposal["diff_sha256"])
        with self.assertRaises(CodingAuthorizationDenied):
            self.tasks.decide_proposal("owner", task.id, "approve_proposal",
                                       proposal["diff_sha256"], confirmed=True,
                                       decision_id="human-decision-0001")
        self.assertEqual((self.repo_path / "main.py").read_text(encoding="utf-8"),
                         "def run():\n    return 1\n")

    def test_agent_cannot_self_approve_or_escape_evidence_scope(self):
        task = self._new_task("Inspect run safely")
        self_approver = FakeAgentAdapter(
            (AgentChange("main.py", "def run():\n    return 2\n"),),
            approval_requests=("approve_proposal",))
        with self.assertRaises(CodingAuthorizationDenied) as caught:
            self.tasks.propose_with_adapter("owner", task.id, "Inspect run safely", self_approver)
        self.assertEqual(str(caught.exception), "agent_cannot_approve")
        self.assertEqual(self.tasks.get_task("owner", task.id).status.value, "requested")
        traversal = FakeAgentAdapter((AgentChange("../outside.py", "marker = 1\n"),))
        with self.assertRaises((CodingAuthorizationDenied, ValueError)):
            self.tasks.propose_with_adapter("owner", task.id, "Inspect run safely", traversal)
        out_of_scope = FakeAgentAdapter((AgentChange("missing.py", "marker = 1\n"),))
        with self.assertRaises(CodingAuthorizationDenied):
            self.tasks.propose_with_adapter("owner", task.id, "Inspect run safely", out_of_scope)

    def test_secret_like_proposal_and_over_budget_output_are_rejected(self):
        task = self._new_task("Inspect run safely")
        secret = FakeAgentAdapter((AgentChange(
            "main.py", "API_KEY = 'synthetic-secret-value-should-not-persist'\n"),))
        with self.assertRaises(CodingAuthorizationDenied):
            self.tasks.propose_with_adapter("owner", task.id, "Inspect run safely", secret)
        oversized = FakeAgentAdapter((AgentChange("main.py", "x" * 300000),))
        with self.assertRaises(CodingAuthorizationDenied):
            self.tasks.propose_with_adapter("owner", task.id, "Inspect run safely", oversized)
        with self.db._connection() as connection:
            row = connection.execute("SELECT proposal_json FROM coding_tasks WHERE id=?",
                                     (task.id,)).fetchone()
        self.assertIsNone(row[0])
        rejected = [event for event in self.tasks.audit_events("owner", task.id)
                    if event["event_type"] == "agent_output_rejected"]
        self.assertEqual([event["metadata"]["reason"] for event in rejected],
                         ["proposal_rejected_by_safety_screen", "agent_output_limit"])

    def test_diff_change_after_approval_invalidates_snapshot(self):
        task = self._new_task("Inspect run safely")
        adapter = FakeAgentAdapter((AgentChange("main.py", "def run():\n    return 2\n"),))
        proposed = self.tasks.propose_with_adapter("owner", task.id, "Inspect run safely", adapter)
        proposal = self.tasks.view_proposal("owner", task.id)
        approved = self.tasks.decide_proposal(
            "owner", task.id, "approve_proposal", proposal["diff_sha256"], confirmed=True)
        self.assertEqual(approved.status.value, "approved")
        with self.db._connection() as connection:
            row = connection.execute("SELECT proposal_json FROM coding_tasks WHERE id=?",
                                     (task.id,)).fetchone()[0]
            value = json.loads(row)
            value["diff"] += "# altered after approval\n"
            connection.execute("UPDATE coding_tasks SET proposal_json=? WHERE id=?",
                               (json.dumps(value), task.id))
            connection.commit()
        with self.assertRaises(CodingAuthorizationDenied) as caught:
            self.tasks.view_proposal("owner", task.id)
        self.assertEqual(str(caught.exception), "approval_snapshot_stale")
        self.assertEqual(self.tasks.get_task("owner", task.id).status.value, "stale")

    def test_expired_task_cannot_be_reviewed_or_approved(self):
        task = self._new_task("Inspect run safely")
        adapter = FakeAgentAdapter((AgentChange("main.py", "def run():\n    return 2\n"),))
        self.tasks.propose_with_adapter("owner", task.id, "Inspect run safely", adapter)
        with self.db._connection() as connection:
            connection.execute("UPDATE coding_tasks SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                               (task.id,))
            connection.commit()
        with self.assertRaises(CodingAuthorizationDenied) as caught:
            self.tasks.view_proposal("owner", task.id)
        self.assertEqual(str(caught.exception), "task_expired")

    def test_cancellation_and_restart_quarantine_do_not_resume_tasks(self):
        task = self._new_task("Inspect run safely")
        already_cancelled = Event()
        already_cancelled.set()
        with self.assertRaises(CodingAuthorizationDenied):
            self.tasks.propose_with_adapter("owner", task.id, "Inspect run safely",
                                            FakeAgentAdapter(), cancelled=already_cancelled)
        self.assertEqual(self.tasks.get_task("owner", task.id).status.value, "cancelled")
        restarted = self._new_task("Inspect run safely")
        interrupted = self.tasks.recover_after_restart("owner", "ws-code")
        self.assertIn(restarted.id, interrupted)
        self.assertEqual(self.tasks.get_task("owner", restarted.id).status.value, "interrupted")

    def test_adapter_exception_is_audited_without_leaking_details(self):
        task = self._new_task("Inspect run safely")

        class BrokenAdapter:
            def propose(self, task_input, cancelled):
                raise RuntimeError("/home/private/.ssh/id_rsa=secret")

            def cancel(self, task_id):
                return None

        with self.assertRaises(CodingAuthorizationDenied) as caught:
            self.tasks.propose_with_adapter("owner", task.id, "Inspect run safely", BrokenAdapter())
        self.assertEqual(str(caught.exception), "agent_proposal_failed")
        audit = self.tasks.audit_events("owner", task.id)
        failure = next(event for event in audit if event["event_type"] == "agent_proposal_failed")
        self.assertEqual(failure["metadata"], {"reason": "adapter_exception"})
        self.assertNotIn("private", json.dumps(audit))

    def test_verification_planner_rejects_arbitrary_commands_and_never_executes(self):
        from jarvis_core.coding_agents import VerificationPlanner

        task = self._new_task("Inspect run safely")
        planner = VerificationPlanner(self.tasks)
        planned = planner.plan("owner", task.id, "python_compile")
        self.assertEqual(planned.state, "blocked_isolation_unverified")
        self.assertFalse(planned.executable)
        self.assertEqual(planned.argv, ("python", "-m", "compileall", "-q", "."))
        with self.assertRaises(ValueError):
            planner.plan("owner", task.id, "python -c 'import os; ...'")

    def test_terminal_review_requires_exact_digest_and_escapes_control_characters(self):
        task = self._new_task("Inspect run safely")
        adapter = FakeAgentAdapter((AgentChange("main.py", "def run():\n    return 2\n\x1b[31m"),))
        self.tasks.propose_with_adapter("owner", task.id, "Inspect run safely", adapter)
        proposal = self.tasks.view_proposal("owner", task.id)
        answer = iter([f"APPROVE {proposal['diff_sha256']}"])
        output = StringIO()
        operator = CodingOperatorWorkflow(
            "owner", "ws-code", self.repositories, self.policy, self.tasks,
            input_fn=lambda _prompt: next(answer), output=output)
        self.assertEqual(operator.review(task.id), "approved")
        self.assertIn("\\u001b", output.getvalue())
        self.assertIn("no files were changed", output.getvalue())


if __name__ == "__main__":
    unittest.main()
