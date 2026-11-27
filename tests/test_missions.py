from __future__ import annotations

from io import StringIO
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from jarvis_core.missions import MissionService
from jarvis_core.mission_operator import MissionOperatorWorkflow
from jarvis_core.orchestration import (DeterministicFakePlanner, DeterministicFakeWorker,
    OrchestrationError, PlannedSubtask, SimulationStep, TaskOrchestrationService, TaskPlanProposal)
from jarvis_core.storage import (AccessDenied, DEFAULT_MIGRATIONS, Database, Permission,
                                SCHEMA_VERSION, WorkspaceKind)


def key(label: str) -> str:
    return (label + "_" * 32)[:32]


class MissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(Path(self.tmp.name) / "jarvis.sqlite3")
        self.db.initialize()
        self.db.create_workspace("Mission", WorkspaceKind.PROJECT, "owner", "ws")
        self.tasks = TaskOrchestrationService(self.db)
        self.service = MissionService(self.db, self.tasks)

    def create(self, **kwargs):
        return self.service.create("owner", "ws", "Summarize the synthetic source",
                                   idempotency_key=key("mission-create"), **kwargs)

    def plan_and_authorize(self, mission, *, key_suffix="a"):
        proposal = TaskPlanProposal((PlannedSubtask("one", "Step", "Simulate a bounded step", "Report completion"),))
        plan = self.service.propose_fake_plan("owner", mission.id, DeterministicFakePlanner(proposal),
                                              idempotency_key=key("mission-plan-" + key_suffix))
        authorized = self.service.authorize("owner", mission.id, plan.revision, plan.plan_sha256,
                                             idempotency_key=key("mission-auth-" + key_suffix))
        return authorized, plan

    def test_creation_ownership_cross_workspace_and_limits(self):
        mission = self.create()
        self.assertEqual(mission.workspace_id, "ws")
        self.assertEqual(mission.principal_id, "owner")
        with self.assertRaises(AccessDenied):
            self.service.get("other", mission.id)
        with self.assertRaises(Exception):
            self.service.create("owner", "missing-workspace", "No access", idempotency_key=key("bad-scope"))
        with self.assertRaises(ValueError):
            self.create(max_steps=1000)

    def test_create_idempotency_validates_immutable_request(self):
        mission = self.create(max_steps=3, max_requests=4)
        replay = self.create(max_steps=3, max_requests=4)
        self.assertEqual(replay.id, mission.id)
        with self.assertRaisesRegex(OrchestrationError, "idempotency_key_conflict"):
            self.service.create("owner", "ws", "Different goal", idempotency_key=key("mission-create"),
                                max_steps=3, max_requests=4)
        with self.assertRaisesRegex(OrchestrationError, "idempotency_key_conflict"):
            self.create(max_steps=4, max_requests=4)
        with self.db._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM missions").fetchone()[0], 1)

    def test_create_idempotency_isolated_by_principal_and_workspace(self):
        mission = self.create()
        self.db.grant_permission("owner", "ws", "manager2", Permission.READ)
        self.db.grant_permission("owner", "ws", "manager2", Permission.WRITE)
        self.db.grant_permission("owner", "ws", "manager2", Permission.MANAGE_WORKSPACE)
        other = self.service.create("manager2", "ws", "Summarize the synthetic source",
                                    idempotency_key=key("mission-create"))
        self.assertNotEqual(other.id, mission.id)
        self.db.create_workspace("Another", WorkspaceKind.PROJECT, "owner", "ws2")
        separate = self.service.create("owner", "ws2", "Summarize the synthetic source",
                                       idempotency_key=key("mission-create"))
        self.assertNotEqual(separate.id, mission.id)

    def test_concurrent_create_same_request_is_duplicate_safe(self):
        barrier = threading.Barrier(2)
        original = self.tasks.create_task

        def synchronized_create(*args, **kwargs):
            barrier.wait(timeout=5)
            return original(*args, **kwargs)

        self.tasks.create_task = synchronized_create
        results, errors = [], []

        def create():
            try:
                results.append(self.create())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=create) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].id, results[1].id)
        with self.db._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM missions").fetchone()[0], 1)

    def test_exact_authorization_duplicate_budget_checkpoint_and_simulation(self):
        mission = self.create(max_steps=2, max_requests=2)
        mission, plan = self.plan_and_authorize(mission)
        replay = self.service.authorize("owner", mission.id, plan.revision, plan.plan_sha256,
                                        idempotency_key=key("mission-auth-a"))
        self.assertEqual(replay.authorization_sha256, mission.authorization_sha256)
        with self.assertRaises(OrchestrationError):
            self.service.authorize("owner", mission.id, plan.revision, "0" * 64,
                                   idempotency_key=key("mission-auth-stale"))
        worker = DeterministicFakeWorker({"one": (SimulationStep(100, "SIMULATED", True),)})
        result = self.service.simulate_next("owner", mission.id, worker,
                                            idempotency_key=key("mission-step"))
        self.assertEqual(result.state, "running")
        self.assertTrue(self.service.verify_checkpoints("owner", mission.id))
        duplicate = self.service.simulate_next("owner", mission.id, worker,
                                               idempotency_key=key("mission-step"))
        self.assertEqual(duplicate.steps_used, 1)
        completed = self.service.simulate_next("owner", mission.id, worker,
                                               idempotency_key=key("mission-finish"))
        self.assertEqual(completed.state, "completed")
        self.assertEqual(completed.steps_used, 2)
        with self.assertRaises(OrchestrationError):
            self.service.simulate_next("owner", mission.id, worker,
                                        idempotency_key=key("mission-over"))

    def test_pause_resume_cancel_and_restart_quarantine(self):
        mission = self.create()
        mission, _ = self.plan_and_authorize(mission)
        paused = self.service.pause("owner", mission.id, idempotency_key=key("pause"))
        self.assertEqual(paused.state, "paused")
        self.assertEqual(self.service.pause("owner", mission.id, idempotency_key=key("pause-again")).state, "paused")
        self.assertEqual(self.service.resume("owner", mission.id, idempotency_key=key("resume")).state, "authorized")
        self.assertEqual(self.service.recover_after_restart("owner", "ws"), (mission.id,))
        self.assertEqual(self.service.get("owner", mission.id).state, "interrupted")
        self.assertEqual(self.service.resume("owner", mission.id, idempotency_key=key("resume-after-restart")).state, "authorized")
        self.assertEqual(self.service.cancel("owner", mission.id, idempotency_key=key("cancel")).state, "cancelled")
        self.assertEqual(self.service.cancel("owner", mission.id, idempotency_key=key("cancel-repeat")).state, "cancelled")
        with self.assertRaises(OrchestrationError):
            self.service.pause("owner", mission.id, idempotency_key=key("invalid-pause"))

    def test_capability_expansion_and_non_deterministic_worker_rejected(self):
        mission = self.create()
        plan = self.service.propose_fake_plan("owner", mission.id,
            DeterministicFakePlanner(TaskPlanProposal((PlannedSubtask("one", "Step", "Goal", "Done"),))),
            idempotency_key=key("proposal"))
        with self.assertRaises(OrchestrationError):
            self.service.authorize("owner", mission.id, plan.revision, plan.plan_sha256,
                                   idempotency_key=key("authorize-expanded"), capabilities=("simulate", "shell"))
        with self.assertRaises(OrchestrationError):
            self.service.simulate_next("owner", mission.id, object(), idempotency_key=key("real-worker"))

    def test_rejection_and_plan_revision_invalidate_authorization(self):
        mission = self.create()
        plan = self.service.propose_fake_plan("owner", mission.id,
            DeterministicFakePlanner(TaskPlanProposal((PlannedSubtask("one", "Step", "Goal", "Done"),))),
            idempotency_key=key("reject-proposal"))
        rejected = self.service.reject("owner", mission.id, plan.revision, plan.plan_sha256,
                                       idempotency_key=key("reject"))
        self.assertEqual(rejected.state, "draft")
        plan2 = self.service.propose_fake_plan("owner", mission.id,
            DeterministicFakePlanner(TaskPlanProposal((PlannedSubtask("two", "New step", "New goal", "Done"),))),
            idempotency_key=key("new-proposal"))
        self.assertGreater(plan2.revision, plan.revision)
        with self.assertRaises(OrchestrationError):
            self.service.authorize("owner", mission.id, plan.revision, plan.plan_sha256,
                                   idempotency_key=key("stale-authorize"))

    def test_manager_revocation_prevents_mission_mutation(self):
        mission = self.create()
        self.db.grant_permission("owner", "ws", "manager2", Permission.MANAGE_WORKSPACE)
        self.db.revoke_permission("manager2", "ws", "owner", Permission.MANAGE_WORKSPACE)
        with self.assertRaises(OrchestrationError):
            self.service.pause("owner", mission.id, idempotency_key=key("revoked-pause"))

    def test_scoped_private_audit_and_immutable_receipts(self):
        mission = self.create()
        mission, plan = self.plan_and_authorize(mission)
        events = self.service.events("owner", mission.id)
        self.assertIn("mission_authorized", [row["event_type"] for row in events])
        self.assertTrue(all("Summarize the synthetic source" not in str(row) for row in events))
        with self.db._connection() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE mission_authorizations SET plan_sha256=? WHERE mission_id=?",
                             ("0" * 64, mission.id))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE mission_events SET event_type='mission_cancelled' WHERE mission_id=?",
                             (mission.id,))

    def test_backup_restore_and_schema_version(self):
        mission = self.create()
        backup = Path(self.tmp.name) / "backup.sqlite3"
        restored = Path(self.tmp.name) / "restored.sqlite3"
        self.db.backup_to(backup, "owner")
        Database.restore_from_backup(backup, restored)
        reopened = Database(restored)
        self.assertEqual(reopened.current_version, SCHEMA_VERSION)
        self.assertEqual(MissionService(reopened).get("owner", mission.id).id, mission.id)

    def test_v15_database_upgrades_additively_to_v18(self):
        path = Path(self.tmp.name) / "legacy-v15.sqlite3"
        legacy = Database(path, migrations=DEFAULT_MIGRATIONS[:-3])
        legacy.initialize()
        self.assertEqual(legacy.current_version, 15)
        legacy.create_workspace("Legacy", WorkspaceKind.PROJECT, "owner", "legacy")
        upgraded = Database(path)
        upgraded.initialize()
        self.assertEqual(upgraded.current_version, 18)
        with upgraded._connection() as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"missions", "mission_authorizations", "mission_events",
                         "mission_budget_ledger", "mission_checkpoints",
                         "mission_planning_reservations", "proactivity_preferences",
                         "proactive_suggestions", "proactivity_suggestion_events",
                         "proactivity_scheduler_claims"} <= tables)

    def test_trusted_terminal_uses_confirmation(self):
        out = StringIO()
        operator = MissionOperatorWorkflow("owner", "ws", self.db, self.service,
                                           input_fn=lambda _: "", output=out)
        self.assertIsNone(operator.create("not persisted"))
        self.assertIn("cancelled", out.getvalue().lower())

    def test_archive_and_confirmed_task_purge_cascade(self):
        mission = self.create()
        self.service.cancel("owner", mission.id, idempotency_key=key("purge-cancel"))
        archived = self.service.archive("owner", mission.id, idempotency_key=key("archive"))
        self.assertIsNotNone(archived.archived_at)
        preview = self.tasks.preview_purge("owner", "ws", task_ids=(mission.task_id,), archived_only=True)
        self.tasks.purge_tasks("owner", "ws", task_ids=(mission.task_id,),
            expected_preview_sha256=preview.preview_sha256,
            confirmation=preview.confirmation_phrase,
            idempotency_key=key("purge-mission"), archived_only=True)
        with self.assertRaises(OrchestrationError):
            self.service.get("owner", mission.id)


if __name__ == "__main__":
    unittest.main()
