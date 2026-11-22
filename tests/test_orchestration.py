from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from threading import Barrier, Event, Thread, get_ident
import time
import unittest
from unittest.mock import patch

from jarvis_core.orchestration import (
    DeterministicFakePlanner,
    DeterministicFakeWorker,
    OrchestrationError,
    PlannedSubtask,
    SimulationStep,
    TaskOrchestrationService,
    TaskPlanProposal,
    TaskState,
)
from jarvis_core.orchestration_operator import OrchestrationOperatorWorkflow
from jarvis_core.coding_agents import CodingCapability, CodingCapabilityPolicy
from jarvis_core.developer import DeveloperRepositoryService
from jarvis_core.storage import (
    AccessDenied,
    DEFAULT_MIGRATIONS,
    Database,
    Permission,
    WorkspaceKind,
)


def key(label: str) -> str:
    return f"{label}_".ljust(16, "x")


def proposal(*, dependencies: tuple[str, ...] = ()) -> TaskPlanProposal:
    return TaskPlanProposal((
        PlannedSubtask("one", "Review source", "Inspect the indexed files.",
                       "List relevant files."),
        PlannedSubtask("two", "Summarize findings", "Summarize the evidence.",
                       "Provide a bounded summary.", dependencies=dependencies),
    ))


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "jarvis.sqlite3")
        self.db.initialize()
        self.db.create_workspace("Synthetic", WorkspaceKind.PROJECT, "owner", "ws")
        self.db.grant_permission("owner", "ws", "member", Permission.READ)
        self.db.grant_permission("owner", "ws", "member", Permission.WRITE)
        self.service = TaskOrchestrationService(self.db)

    def create(self, actor="owner"):
        return self.service.create_task(actor, "ws", "Prepare a source-grounded summary",
                                        idempotency_key=key("create"))

    def propose(self, task, plan=None):
        planner = DeterministicFakePlanner(plan or proposal())
        value = self.service.propose_plan("owner", task.id, planner,
                                          idempotency_key=key(f"plan{task.current_plan_revision}"))
        self.assertEqual(planner.calls, 1)
        return value

    def approve(self, task, plan):
        return self.service.approve_plan("owner", task.id, plan.revision,
                                         plan.plan_sha256,
                                         idempotency_key=key(f"approve{plan.revision}"))

    def test_lifecycle_simulation_restart_and_cancel(self):
        task = self.create()
        plan = self.propose(task, proposal(dependencies=("one",)))
        task = self.approve(task, plan)
        self.assertEqual(task.state, TaskState.QUEUED)
        worker = DeterministicFakeWorker({
            "one": (SimulationStep(40, "SIMULATED: partial"),
                    SimulationStep(100, "SIMULATED: finished", True)),
            "two": (SimulationStep(100, "SIMULATED: summarized", True),),
        })
        task = self.service.simulate_next_step("owner", task.id, worker,
                                              idempotency_key=key("step-one-a"))
        self.assertEqual(task.state, TaskState.SIMULATING)
        self.assertEqual(self.service.get_progress("owner", task.id)[0].progress_percent, 40)
        interrupted = self.service.recover_after_restart(
            "owner", "ws", idempotency_key_prefix=key("recovery"))
        self.assertEqual(interrupted, (task.id,))
        self.assertEqual(self.service.get_task("owner", task.id).state, TaskState.INTERRUPTED)
        # Worker state is not persisted; deterministic fake chooses the next
        # strictly higher scripted progress point from durable progress.
        task = self.service.resume_simulation("owner", task.id,
                                              idempotency_key=key("resume"))
        self.assertEqual(task.state, TaskState.SIMULATING)
        task = self.service.simulate_next_step("owner", task.id, worker,
                                              idempotency_key=key("step-one-b"))
        self.assertEqual(self.service.get_progress("owner", task.id)[0].state.value,
                         "simulated_completed")
        task = self.service.simulate_next_step("owner", task.id, worker,
                                              idempotency_key=key("step-two"))
        self.assertEqual(self.service.get_progress("owner", task.id)[1].state.value,
                         "simulated_completed")
        task = self.service.simulate_next_step("owner", task.id, worker,
                                              idempotency_key=key("finish"))
        self.assertEqual(task.state, TaskState.SIMULATION_COMPLETED)
        self.assertIn("SIMULATION ONLY", task.result_summary)
        with self.assertRaises(OrchestrationError):
            self.service.cancel_task("owner", task.id, idempotency_key=key("cancel-done"))
        events = self.service.events("owner", task.id)
        self.assertEqual(events[0]["event_type"], "task_created")
        self.assertTrue(any(event["event_type"] == "task_interrupted" for event in events))
        self.assertTrue(all(event["metadata"].get("simulated") == "true"
                            for event in events if event["event_type"] == "simulation_progress"))

    def test_approval_is_exact_revision_and_new_plan_needs_new_approval(self):
        task = self.create()
        first = self.propose(task)
        with self.assertRaises(OrchestrationError):
            self.service.approve_plan("owner", task.id, first.revision,
                                      "0" * 64, idempotency_key=key("wrong-digest"))
        task = self.approve(task, first)
        revised = self.propose(task, TaskPlanProposal((
            PlannedSubtask("new", "Revised", "New goal", "New acceptance"),
        )))
        self.assertEqual(revised.revision, first.revision + 1)
        self.assertEqual(self.service.list_plans("owner", task.id)[0].state, "superseded")
        updated = self.service.get_task("owner", task.id)
        self.assertIsNone(updated.approved_plan_revision)
        self.assertEqual(updated.state, TaskState.AWAITING_APPROVAL)
        with self.assertRaises(OrchestrationError):
            self.service.simulate_next_step(
                "owner", task.id, DeterministicFakeWorker({}),
                idempotency_key=key("unapproved"))

    def test_duplicate_event_delivery_is_idempotent_and_conflicts_fail(self):
        task = self.create()
        same = self.service.create_task("owner", "ws", "Prepare a source-grounded summary",
                                        idempotency_key=key("create"))
        self.assertEqual(task.id, same.id)
        with self.assertRaises(OrchestrationError):
            self.service.create_task("owner", "ws", "Different goal",
                                     idempotency_key=key("create"))
        plan = self.propose(task)
        approved = self.approve(task, plan)
        replay = self.service.approve_plan("owner", task.id, plan.revision,
                                           plan.plan_sha256,
                                           idempotency_key=key("approve1"))
        self.assertEqual(approved.state, replay.state)
        self.assertEqual(len(self.service.events("owner", task.id)), 3)

    def test_workspace_and_actor_isolation(self):
        task = self.create("member")
        with self.assertRaises(OrchestrationError):
            self.service.get_task("owner", "missing")
        self.db.create_workspace("Other", WorkspaceKind.PROJECT, "other", "other-ws")
        with self.assertRaises(AccessDenied):
            self.service.get_task("other", task.id)
        with self.assertRaises(AccessDenied):
            self.service.create_task("member", "other-ws", "Unauthorized",
                                     idempotency_key=key("cross-workspace"))

    def test_dependencies_are_enforced_and_cycles_rejected(self):
        task = self.create()
        with self.assertRaises(ValueError):
            self.propose(task, TaskPlanProposal((
                PlannedSubtask("one", "One", "Goal", "Accept", dependencies=("two",)),
                PlannedSubtask("two", "Two", "Goal", "Accept", dependencies=("one",)),
            )))
        plan = self.propose(task, proposal(dependencies=("one",)))
        self.approve(task, plan)
        worker = DeterministicFakeWorker({
            "one": (SimulationStep(100, "SIMULATED: done", True),),
            "two": (SimulationStep(100, "SIMULATED: done", True),),
        })
        self.service.simulate_next_step("owner", task.id, worker,
                                        idempotency_key=key("dep-step-one"))
        self.assertEqual(worker.calls, [("one", 0)])

    def test_fake_worker_only_and_plan_approval_never_enables_execution(self):
        task = self.create()
        plan = self.propose(task)
        task = self.approve(task, plan)
        with self.assertRaises(OrchestrationError):
            self.service.simulate_next_step("owner", task.id, object(),
                                            idempotency_key=key("real-worker"))
        self.assertFalse(task.capability_manifest["execution_enabled"])
        self.assertFalse(task.capability_manifest["file_write_enabled"])
        self.assertFalse(task.capability_manifest["process_execution_enabled"])
        self.assertFalse(task.capability_manifest["network_enabled"])
        self.assertFalse(task.capability_manifest["plan_approval_is_execution_approval"])

    def test_concurrent_simulation_and_cancellation_cannot_resurrect_task(self):
        task = self.create()
        plan = self.propose(task)
        self.approve(task, plan)
        barrier = Barrier(3)
        outcomes: list[str] = []
        worker = DeterministicFakeWorker({
            "one": (SimulationStep(100, "SIMULATED: done", True),),
        })

        def simulate():
            barrier.wait()
            try:
                value = self.service.simulate_next_step(
                    "owner", task.id, worker, idempotency_key=key("race-simulate"))
                outcomes.append(value.state.value)
            except OrchestrationError:
                outcomes.append("simulation_rejected")

        def cancel():
            barrier.wait()
            value = self.service.cancel_task(
                "owner", task.id, idempotency_key=key("race-cancel"))
            outcomes.append(value.state.value)

        threads = [Thread(target=simulate), Thread(target=cancel)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(self.service.get_task("owner", task.id).state, TaskState.CANCELLED)
        self.assertIn("cancelled", outcomes)
        self.assertLessEqual(len(worker.calls), 1)

    def test_task_evidence_corruption_is_detected(self):
        task = self.create()
        with self.db._connection() as connection:
            connection.execute("UPDATE orchestration_tasks SET goal='tampered' WHERE id=?", (task.id,))
            connection.commit()
        with self.assertRaises(OrchestrationError):
            self.service.get_task("owner", task.id)

    def test_database_restart_and_backup_restore_preserve_task_plan_and_history(self):
        task = self.create()
        plan = self.propose(task, proposal(dependencies=("one",)))
        self.approve(task, plan)
        backup = Path(self.temp.name) / "jarvis-backup.sqlite3"
        self.db.backup_to(backup, "owner")
        restored_path = Path(self.temp.name) / "restored.sqlite3"
        Database.restore_from_backup(backup, restored_path)
        after_restart = TaskOrchestrationService(Database(restored_path))
        restored_task = after_restart.get_task("owner", task.id)
        restored_plan = after_restart.get_plan("owner", task.id)
        self.assertEqual(restored_task.approved_plan_revision, plan.revision)
        self.assertEqual(restored_plan.plan_sha256, plan.plan_sha256)
        self.assertEqual(len(after_restart.events("owner", task.id)), 3)

    def test_lifecycle_events_and_approvals_are_append_only(self):
        task = self.create()
        plan = self.propose(task)
        self.approve(task, plan)
        with self.db._connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE orchestration_task_events SET metadata_json='{}' WHERE task_id=?",
                                   (task.id,))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM orchestration_approvals WHERE task_id=?", (task.id,))

    def test_revoked_repository_capability_blocks_simulation_until_explicit_resume(self):
        root = Path(self.temp.name) / "approved"
        root.mkdir()
        repo_path = root / "repo"
        repo_path.mkdir()
        subprocess.run(["git", "init", "-q", str(repo_path)], check=True)
        subprocess.run(["git", "-C", str(repo_path), "config", "user.name", "Synthetic"], check=True)
        subprocess.run(["git", "-C", str(repo_path), "config", "user.email", "synthetic@example.invalid"], check=True)
        (repo_path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo_path), "add", "module.py"], check=True)
        subprocess.run(["git", "-C", str(repo_path), "commit", "-qm", "fixture"], check=True)
        root_record = self.db.register_approved_root("owner", "ws", root)
        repositories = DeveloperRepositoryService(self.db)
        registered = repositories.register("owner", "ws", root_record.id, repo_path, confirmed=True)
        repository = repositories.index("owner", "ws", registered.id)
        policy = CodingCapabilityPolicy(self.db)
        metadata_grant = policy.grant("owner", "ws", repository.id, "owner",
                                      CodingCapability.READ_REPOSITORY_METADATA, confirmed=True)
        source_grant = policy.grant("owner", "ws", repository.id, "owner",
                                    CodingCapability.READ_INDEXED_SOURCE, confirmed=True)
        service = TaskOrchestrationService(self.db, repositories, policy)
        task = service.create_task(
            "owner", "ws", "Summarize committed source", repository_id=repository.id,
            requested_capabilities=(CodingCapability.READ_INDEXED_SOURCE.value,),
            idempotency_key=key("repo-task"))
        plan = service.propose_plan("owner", task.id, DeterministicFakePlanner(TaskPlanProposal((
            PlannedSubtask("read", "Read", "Inspect indexed source", "Summarize it",
                          required_capabilities=(CodingCapability.READ_INDEXED_SOURCE.value,)),
        ))), idempotency_key=key("repo-plan"))
        service.approve_plan("owner", task.id, plan.revision, plan.plan_sha256,
                             idempotency_key=key("repo-approve"))
        policy.revoke("owner", source_grant.id, confirmed=True)
        worker = DeterministicFakeWorker({"read": (SimulationStep(100, "SIMULATED", True),)})
        task = service.simulate_next_step("owner", task.id, worker,
                                          idempotency_key=key("revoked-step"))
        self.assertEqual(task.state, TaskState.BLOCKED)
        self.assertEqual(worker.calls, [])
        self.assertEqual(service.blocked_capabilities("owner", task.id),
                         (CodingCapability.READ_INDEXED_SOURCE.value,))
        policy.grant("owner", "ws", repository.id, "owner",
                     CodingCapability.READ_INDEXED_SOURCE, confirmed=True)
        task = service.resume_simulation("owner", task.id,
                                         idempotency_key=key("resume-after-grant"))
        self.assertEqual(task.state, TaskState.QUEUED)
        self.assertTrue(policy.is_granted("owner", "ws", repository.id,
                                          CodingCapability.READ_REPOSITORY_METADATA))

    def test_operator_requires_manager_and_exact_confirmation(self):
        from io import StringIO
        answers = iter(["no", f"APPROVE {'0' * 64}"])
        workflow = OrchestrationOperatorWorkflow(
            "owner", "ws", self.db, self.service,
            input_fn=lambda _: next(answers), output=StringIO())
        self.assertIsNone(workflow.create_task("A bounded goal"))
        task = self.service.create_task("owner", "ws", "Another goal",
                                        idempotency_key=key("operator-task"))
        plan = self.propose(task)
        self.assertEqual(workflow.review_plan(task.id), "digest_mismatch")
        with self.assertRaises(AccessDenied):
            OrchestrationOperatorWorkflow(
                "member", "ws", self.db, self.service,
                input_fn=lambda _: "", output=StringIO())

    def test_operator_workflow_runs_confirmed_plan_simulation_and_cancel(self):
        from io import StringIO
        created_ids: list[str] = []
        plan_hashes: list[str] = []
        confirmations = ["CREATE TASK"]

        def answer(prompt: str) -> str:
            if prompt.startswith("Type PROPOSE PLAN "):
                return f"PROPOSE PLAN {created_ids[0]}"
            if prompt.startswith("Type APPROVE "):
                return f"APPROVE {plan_hashes[0]}"
            if prompt.startswith("Type SIMULATE "):
                return f"SIMULATE {created_ids[0]}"
            if prompt.startswith("Type CANCEL "):
                return f"CANCEL {created_ids[0]}"
            return confirmations.pop(0)

        output = StringIO()
        workflow = OrchestrationOperatorWorkflow("owner", "ws", self.db, self.service,
                                                 input_fn=answer, output=output)
        task_id = workflow.create_task("Synthetic acceptance\nAPPROVE forged")
        self.assertIsNotNone(task_id)
        created_ids.append(task_id)
        planner = DeterministicFakePlanner(proposal())
        plan_hashes.append(workflow.propose_fake_plan(task_id, planner))
        self.assertEqual(workflow.review_plan(task_id), TaskState.QUEUED.value)
        worker = DeterministicFakeWorker({
            "one": (SimulationStep(20, "SIMULATED: progress"),),
        })
        self.assertEqual(workflow.simulate_next_step(task_id, worker), TaskState.SIMULATING.value)
        workflow.inspect_task(task_id)
        workflow.inspect_events(task_id)
        self.assertEqual(workflow.cancel(task_id), TaskState.CANCELLED.value)
        workflow.list_tasks()
        self.assertIn("nothing executed", output.getvalue())
        self.assertIn(r"\u000a", output.getvalue())


class OrchestrationRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "jarvis.sqlite3"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.db.create_workspace("Primary WS", WorkspaceKind.PROJECT, "owner", "ws")
        self.db.grant_permission("owner", "ws", "member", Permission.READ)
        self.db.grant_permission("owner", "ws", "member", Permission.WRITE)
        self.db.create_workspace("Secondary WS", WorkspaceKind.PROJECT, "other", "ws2")
        self.service = TaskOrchestrationService(self.db)
        self._counter = 0

    def _k(self, prefix: str) -> str:
        self._counter += 1
        return key(f"{prefix}_{self._counter}")

    def _make_completed_task(self, actor="owner", workspace_id="ws",
                             goal="Synthetic completed task goal",
                             revisions: int = 1):
        task = self.service.create_task(actor, workspace_id, goal, idempotency_key=self._k("create"))
        plan = None
        for rev in range(revisions):
            planner = DeterministicFakePlanner(proposal(dependencies=("one",)))
            plan = self.service.propose_plan(actor, task.id, planner,
                                             idempotency_key=self._k(f"plan{rev}"))
        assert plan is not None
        self.service.approve_plan(actor, task.id, plan.revision, plan.plan_sha256,
                                  idempotency_key=self._k("approve"))
        worker = DeterministicFakeWorker({
            "one": (SimulationStep(100, "SIMULATED: step one complete", True),),
            "two": (SimulationStep(100, "SIMULATED: step two complete", True),),
        })
        self.service.simulate_next_step(actor, task.id, worker, idempotency_key=self._k("sim1"))
        self.service.simulate_next_step(actor, task.id, worker, idempotency_key=self._k("sim2"))
        return self.service.simulate_next_step(actor, task.id, worker, idempotency_key=self._k("sim3"))

    def _make_cancelled_task(self, actor="owner", workspace_id="ws",
                             goal="Synthetic cancelled task", with_pending_plan: bool = False):
        task = self.service.create_task(actor, workspace_id, goal, idempotency_key=self._k("create"))
        if with_pending_plan:
            planner = DeterministicFakePlanner(proposal())
            self.service.propose_plan(actor, task.id, planner, idempotency_key=self._k("plan"))
        return self.service.cancel_task(actor, task.id, idempotency_key=self._k("cancel"))

    def _make_interrupted_task(self, actor="owner", workspace_id="ws",
                               goal="Synthetic interrupted task"):
        task = self.service.create_task(actor, workspace_id, goal, idempotency_key=self._k("create"))
        planner = DeterministicFakePlanner(proposal(dependencies=("one",)))
        plan = self.service.propose_plan(actor, task.id, planner, idempotency_key=self._k("plan"))
        self.service.approve_plan(actor, task.id, plan.revision, plan.plan_sha256,
                                  idempotency_key=self._k("approve"))
        worker = DeterministicFakeWorker({"one": (SimulationStep(35, "SIMULATED: partial"),)})
        self.service.simulate_next_step(actor, task.id, worker, idempotency_key=self._k("sim"))
        interrupted = self.service.recover_after_restart(actor, workspace_id,
                                                         idempotency_key_prefix=self._k("recover"))
        self.assertIn(task.id, interrupted)
        return self.service.get_task(actor, task.id)

    def test_retention_policy_defaults_and_expired_policy_fail_closed(self):
        completed = self._make_completed_task()
        cancelled = self._make_cancelled_task()

        default_policy = self.service.get_retention_policy("owner", "ws")
        self.assertIsNone(default_policy.completed_retention_days)
        self.assertIsNone(default_policy.cancelled_retention_days)
        self.assertIsNone(default_policy.failed_retention_days)
        self.assertIsNone(default_policy.interrupted_retention_days)
        self.assertFalse(default_policy.auto_delete_enabled)
        self.assertFalse(default_policy.is_expired)

        far_future = (datetime.now(timezone.utc) + timedelta(days=4000)).isoformat(timespec="microseconds")
        empty_preview = self.service.preview_purge(
            "owner", "ws", use_retention_policy=True, as_of=far_future
        )
        self.assertEqual(empty_preview.task_count, 0)
        self.assertIsNone(empty_preview.confirmation_phrase)

        policy = self.service.set_retention_policy(
            "owner", "ws",
            completed_retention_days=30,
            cancelled_retention_days=7,
            failed_retention_days=14,
            interrupted_retention_days=21,
            policy_lifetime_days=10,
        )
        self.assertEqual(policy.completed_retention_days, 30)
        self.assertEqual(policy.cancelled_retention_days, 7)
        self.assertEqual(policy.failed_retention_days, 14)
        self.assertEqual(policy.interrupted_retention_days, 21)
        self.assertFalse(policy.auto_delete_enabled)
        self.assertFalse(policy.is_expired)
        # Configuring a retention policy never automatically deletes records.
        self.assertEqual(self.service.get_task("owner", completed.id).state, TaskState.SIMULATION_COMPLETED)
        self.assertEqual(self.service.get_task("owner", cancelled.id).state, TaskState.CANCELLED)

        with self.db._connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE orchestration_retention_policies SET auto_delete_enabled=1 WHERE workspace_id='ws'"
                )

        day_8 = (datetime.now(timezone.utc) + timedelta(days=8)).isoformat(timespec="microseconds")
        day_8_preview = self.service.preview_purge("owner", "ws", use_retention_policy=True, as_of=day_8)
        self.assertEqual(day_8_preview.task_ids, (cancelled.id,))

        day_12 = (datetime.now(timezone.utc) + timedelta(days=12)).isoformat(timespec="microseconds")
        expired_view = self.service.get_retention_policy("owner", "ws", as_of=day_12)
        self.assertTrue(expired_view.is_expired)
        with self.assertRaisesRegex(OrchestrationError, "retention_policy_expired"):
            self.service.preview_purge("owner", "ws", use_retention_policy=True, as_of=day_12)
        with self.assertRaisesRegex(OrchestrationError, "retention_policy_expired"):
            self.service.purge_tasks(
                "owner", "ws",
                task_ids=day_8_preview.task_ids,
                expected_preview_sha256=day_8_preview.preview_sha256,
                confirmation=day_8_preview.confirmation_phrase,
                idempotency_key=self._k("expired-purge"),
                use_retention_policy=True,
                as_of=day_12,
            )

    def test_workspace_isolation_and_cross_workspace_denial(self):
        ws1_task = self._make_cancelled_task("owner", "ws", "WS1 private goal")
        ws2_task = self._make_cancelled_task("other", "ws2", "WS2 private goal")
        member_task = self._make_cancelled_task("member", "ws", "Member task")

        self.service.set_retention_policy("owner", "ws", cancelled_retention_days=1)
        ws2_policy = self.service.get_retention_policy("other", "ws2")
        self.assertIsNone(ws2_policy.cancelled_retention_days)

        history_ws1 = self.service.list_task_history("owner", "ws")
        self.assertEqual({t.id for t in history_ws1.items}, {ws1_task.id, member_task.id})
        history_member = self.service.list_task_history("member", "ws")
        self.assertEqual({t.id for t in history_member.items}, {member_task.id})

        with self.assertRaises(AccessDenied):
            self.service.get_retention_policy("owner", "ws2")
        with self.assertRaises(AccessDenied):
            self.service.set_retention_policy("owner", "ws2", cancelled_retention_days=1)
        with self.assertRaisesRegex(OrchestrationError, "task_unavailable"):
            self.service.preview_purge("owner", "ws", task_ids=(ws2_task.id,))
        with self.assertRaisesRegex(OrchestrationError, "task_unavailable"):
            self.service.preview_purge("owner", "ws", task_ids=(ws1_task.id, ws2_task.id))
        with self.assertRaises(AccessDenied):
            self.service.archive_task("owner", ws2_task.id, idempotency_key=self._k("xws-arch"))

        preview_ws2 = self.service.preview_purge("other", "ws2", task_ids=(ws2_task.id,))
        with self.assertRaises(AccessDenied):
            self.service.purge_tasks(
                "owner", "ws2",
                task_ids=preview_ws2.task_ids,
                expected_preview_sha256=preview_ws2.preview_sha256,
                confirmation=preview_ws2.confirmation_phrase,
                idempotency_key=self._k("xws-purge"),
            )
        # Cross-workspace task_id inside owner's own workspace is also rejected.
        fake_phrase = f"PURGE ws {preview_ws2.preview_sha256}"
        with self.assertRaisesRegex(OrchestrationError, "task_unavailable"):
            self.service.purge_tasks(
                "owner", "ws",
                task_ids=(ws2_task.id,),
                expected_preview_sha256=preview_ws2.preview_sha256,
                confirmation=fake_phrase,
                idempotency_key=self._k("xws-purge-2"),
            )
        self.assertEqual(self.service.get_task("other", ws2_task.id).id, ws2_task.id)

        self.service.purge_tasks(
            "other", "ws2",
            task_ids=preview_ws2.task_ids,
            expected_preview_sha256=preview_ws2.preview_sha256,
            confirmation=preview_ws2.confirmation_phrase,
            idempotency_key=self._k("ws2-purge"),
        )
        with self.assertRaises(AccessDenied):
            self.service.list_purge_tombstones("owner", "ws2")
        with self.assertRaises(AccessDenied):
            self.service.get_purge_tombstone("owner", "ws2", ws2_task.id)

    def test_archive_restore_visibility_bounded_history_and_mutation_guards(self):
        completed = self._make_completed_task(goal="Completed archival candidate")
        interrupted = self._make_interrupted_task(goal="Interrupted archival candidate")
        cancelled = self._make_cancelled_task(goal="Cancelled archival candidate")

        with self.db._connection() as connection:
            before_count = connection.execute("SELECT COUNT(*) FROM orchestration_tasks").fetchone()[0]

        archived = self.service.archive_task("owner", interrupted.id, idempotency_key=self._k("arch1"))
        self.assertIsNotNone(archived.archived_at)
        self.assertEqual(archived.archived_by, "owner")
        # Archiving in-place does not duplicate rows or reset updated_at retention clock.
        self.assertEqual(archived.updated_at, interrupted.updated_at)
        with self.db._connection() as connection:
            after_count = connection.execute("SELECT COUNT(*) FROM orchestration_tasks").fetchone()[0]
        self.assertEqual(before_count, after_count)

        # Hidden from default list_tasks, visible with include_archived=True and direct reads.
        default_ids = {t.id for t in self.service.list_tasks("owner", "ws")}
        self.assertNotIn(interrupted.id, default_ids)
        self.assertIn(completed.id, default_ids)
        all_ids = {t.id for t in self.service.list_tasks("owner", "ws", include_archived=True)}
        self.assertIn(interrupted.id, all_ids)
        self.assertEqual(self.service.get_task("owner", interrupted.id).archived_by, "owner")
        self.assertEqual(len(self.service.list_plans("owner", interrupted.id)), 1)
        self.assertTrue(any(e["event_type"] == "task_archived" for e in self.service.events("owner", interrupted.id)))

        # Mutations blocked while archived.
        with self.assertRaisesRegex(OrchestrationError, "task_archived"):
            self.service.resume_simulation("owner", interrupted.id, idempotency_key=self._k("res-arch"))
        with self.assertRaisesRegex(OrchestrationError, "task_archived"):
            self.service.cancel_task("owner", interrupted.id, idempotency_key=self._k("can-arch"))

        # Bounded history queries & pagination.
        archived_page = self.service.list_task_history("owner", "ws", archived_only=True)
        self.assertEqual([t.id for t in archived_page.items], [interrupted.id])
        self.assertEqual(archived_page.total_count, 1)
        self.assertFalse(archived_page.has_more)

        page1 = self.service.list_task_history("owner", "ws", include_archived=True, limit=2, offset=0)
        page2 = self.service.list_task_history("owner", "ws", include_archived=True, limit=2, offset=2)
        self.assertEqual(page1.total_count, 3)
        self.assertTrue(page1.has_more)
        self.assertFalse(page2.has_more)
        self.assertEqual(len(page1.items) + len(page2.items), 3)
        self.assertEqual(
            {t.id for t in page1.items} | {t.id for t in page2.items},
            {completed.id, interrupted.id, cancelled.id},
        )
        with self.assertRaises(ValueError):
            self.service.list_task_history("owner", "ws", limit=101)
        with self.assertRaises(ValueError):
            self.service.list_task_history("owner", "ws", offset=10_001)

        # Restoring restores visibility and allows explicit resume.
        restored = self.service.restore_archived_task("owner", interrupted.id, idempotency_key=self._k("rest1"))
        self.assertIsNone(restored.archived_at)
        self.assertIsNone(restored.archived_by)
        self.assertIn(interrupted.id, {t.id for t in self.service.list_tasks("owner", "ws")})
        resumed = self.service.resume_simulation("owner", interrupted.id, idempotency_key=self._k("res-ok"))
        self.assertEqual(resumed.state, TaskState.SIMULATING)

    def test_purge_preview_accuracy_bounds_cascade_and_non_sensitive_tombstones(self):
        secret_goal = "SENSITIVE_SYNTHETIC_GOAL_MARKER_99182"
        task_a = self._make_completed_task(goal=secret_goal, revisions=2)
        task_b = self._make_cancelled_task(goal="Secondary cancelled task")
        survivor = self._make_completed_task(goal="Surviving task for trigger verification")

        self.service.set_retention_policy("owner", "ws", completed_retention_days=1, cancelled_retention_days=1)
        future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(timespec="microseconds")

        bounded = self.service.preview_purge("owner", "ws", use_retention_policy=True, as_of=future, limit=1)
        self.assertEqual(bounded.task_count, 1)
        self.assertTrue(bounded.truncated)

        preview = self.service.preview_purge("owner", "ws", task_ids=(task_a.id, task_b.id))
        self.assertEqual(preview.task_count, 2)
        self.assertEqual(preview.plan_count, 2)
        self.assertEqual(preview.subtask_count, 4)
        self.assertEqual(preview.dependency_count, 2)
        self.assertEqual(preview.approval_count, 1)
        self.assertGreaterEqual(preview.event_count, 9)
        self.assertEqual(preview.confirmation_phrase, f"PURGE ws {preview.preview_sha256}")

        receipt = self.service.purge_tasks(
            "owner", "ws",
            task_ids=preview.task_ids,
            expected_preview_sha256=preview.preview_sha256,
            confirmation=preview.confirmation_phrase,
            idempotency_key=self._k("purge-main"),
        )
        self.assertFalse(receipt.already_purged)
        self.assertEqual(receipt.task_count, 2)
        self.assertEqual(receipt.plan_count, preview.plan_count)
        self.assertEqual(receipt.subtask_count, preview.subtask_count)
        self.assertEqual(receipt.dependency_count, preview.dependency_count)
        self.assertEqual(receipt.approval_count, preview.approval_count)
        self.assertEqual(receipt.event_count, preview.event_count)

        # Verify zero remaining rows across all 6 task tables for purged tasks.
        with self.db._connection() as connection:
            for tid in (task_a.id, task_b.id):
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM orchestration_tasks WHERE id=?", (tid,)).fetchone()[0], 0)
                for table in ("orchestration_plans", "orchestration_subtasks",
                              "orchestration_dependencies", "orchestration_approvals",
                              "orchestration_task_events"):
                    self.assertEqual(connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE task_id=?", (tid,)).fetchone()[0], 0)

            # Verify no plaintext goal or goal_sha256 leaked into purge tombstones, purge ops, or purge audit events.
            for table in ("orchestration_purge_tombstones", "orchestration_purge_operations"):
                rows = connection.execute(f"SELECT * FROM {table}").fetchall()
                joined = "\n".join(str(dict(row)) for row in rows)
                self.assertNotIn(secret_goal, joined)
                self.assertNotIn(task_a.goal_sha256, joined)
                self.assertNotIn(task_b.goal_sha256, joined)
            audit_rows = connection.execute("SELECT * FROM audit_events").fetchall()
            self.assertNotIn(secret_goal, "\n".join(str(dict(row)) for row in audit_rows))
            purge_audit_rows = connection.execute(
                "SELECT * FROM audit_events WHERE event_type='orchestration.tasks_purged'"
            ).fetchall()
            self.assertEqual(len(purge_audit_rows), 1)
            purge_audit_joined = "\n".join(str(dict(row)) for row in purge_audit_rows)
            self.assertNotIn(task_a.goal_sha256, purge_audit_joined)
            self.assertNotIn(task_b.goal_sha256, purge_audit_joined)

            # Immutability triggers remain enforced for tombstones, operations, and surviving tasks.
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM orchestration_purge_tombstones WHERE task_id=?", (task_a.id,))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE orchestration_purge_tombstones SET task_state='draft' WHERE task_id=?", (task_a.id,))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM orchestration_purge_operations WHERE purge_id=?", (receipt.purge_id,))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM orchestration_approvals WHERE task_id=?", (survivor.id,))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM orchestration_task_events WHERE task_id=?", (survivor.id,))

        tombstone = self.service.get_purge_tombstone("owner", "ws", task_a.id)
        self.assertFalse(hasattr(tombstone, "goal_sha256"))
        self.assertEqual(tombstone.task_id, task_a.id)
        self.assertEqual(tombstone.task_state, task_a.state)
        self.assertEqual(tombstone.approval_count, 1)
        self.assertEqual(tombstone.plan_count, 2)
        self.assertEqual(tombstone.purged_by, "owner")

    def test_exact_confirmation_validation_and_operator_workflow(self):
        task = self._make_cancelled_task(goal="Operator purge target")
        preview = self.service.preview_purge("owner", "ws", task_ids=(task.id,))
        bad_confirmations = (
            "",
            "PURGE",
            f"purge ws {preview.preview_sha256}",
            f"PURGE ws {preview.preview_sha256} ",
            f"PURGE other_ws {preview.preview_sha256}",
            f"PURGE ws {'0' * 64}",
        )
        for bad in bad_confirmations:
            with self.assertRaisesRegex(OrchestrationError, "purge_confirmation_mismatch"):
                self.service.purge_tasks(
                    "owner", "ws",
                    task_ids=preview.task_ids,
                    expected_preview_sha256=preview.preview_sha256,
                    confirmation=bad,
                    idempotency_key=self._k("bad-confirm"),
                )
        self.assertEqual(self.service.get_task("owner", task.id).state, TaskState.CANCELLED)

        # Operator workflow: policy, archive, restore, cancelled purge, confirmed purge, tombstones.
        prompts_seen: list[str] = []
        actions = iter([
            "SET RETENTION ws",
            f"ARCHIVE {task.id}",
            f"RESTORE {task.id}",
            "WRONG CONFIRMATION",
        ])

        def answer(prompt: str) -> str:
            prompts_seen.append(prompt)
            try:
                return next(actions)
            except StopIteration:
                # Extract exact PURGE phrase from final prompt.
                return prompt.removeprefix("Type ").removesuffix(" to purge: ")

        output = StringIO()
        workflow = OrchestrationOperatorWorkflow("owner", "ws", self.db, self.service,
                                                 input_fn=answer, output=output)
        workflow.inspect_retention_policy()
        self.assertIsNotNone(workflow.configure_retention_policy(cancelled_retention_days=7))
        self.assertEqual(workflow.archive_task(task.id), "archived")
        self.assertEqual(workflow.restore_archived_task(task.id), "restored")
        self.assertIsNone(workflow.purge_tasks(task_ids=(task.id,)))
        self.assertEqual(self.service.get_task("owner", task.id).id, task.id)
        receipt = workflow.purge_tasks(task_ids=(task.id,))
        self.assertIsNotNone(receipt)
        tombstone_output = StringIO()
        tombstone_workflow = OrchestrationOperatorWorkflow(
            "owner", "ws", self.db, self.service, input_fn=answer, output=tombstone_output
        )
        tombstones = tombstone_workflow.inspect_purge_tombstones()
        self.assertEqual(len(tombstones), 1)
        self.assertNotIn("goal_sha256", tombstone_output.getvalue())
        self.assertIn(task.id, tombstone_output.getvalue())
        rendered = output.getvalue()
        self.assertIn("auto_delete=False", rendered)
        self.assertIn("SSD physical erasure", rendered)

    def test_stale_preview_rejection(self):
        # Case 1: Archiving after preview mutates snapshot -> purge_preview_stale.
        interrupted = self._make_interrupted_task()
        preview1 = self.service.preview_purge("owner", "ws", task_ids=(interrupted.id,))
        self.service.archive_task("owner", interrupted.id, idempotency_key=self._k("arch-stale"))
        with self.assertRaisesRegex(OrchestrationError, "purge_preview_stale"):
            self.service.purge_tasks(
                "owner", "ws",
                task_ids=preview1.task_ids,
                expected_preview_sha256=preview1.preview_sha256,
                confirmation=preview1.confirmation_phrase,
                idempotency_key=self._k("stale-1"),
            )

        # Case 2: Policy tightened between preview and confirm -> purge_preview_stale.
        cancelled = self._make_cancelled_task()
        self.service.set_retention_policy("owner", "ws", cancelled_retention_days=5)
        day_6 = (datetime.now(timezone.utc) + timedelta(days=6)).isoformat(timespec="microseconds")
        preview2 = self.service.preview_purge("owner", "ws", use_retention_policy=True, as_of=day_6)
        self.assertEqual(preview2.task_ids, (cancelled.id,))
        self.service.set_retention_policy("owner", "ws", cancelled_retention_days=30)
        with self.assertRaisesRegex(OrchestrationError, "purge_preview_stale"):
            self.service.purge_tasks(
                "owner", "ws",
                task_ids=preview2.task_ids,
                expected_preview_sha256=preview2.preview_sha256,
                confirmation=preview2.confirmation_phrase,
                idempotency_key=self._k("stale-2"),
                use_retention_policy=True,
                as_of=day_6,
            )

        # Case 3: Interrupted task resumed to active state -> protected from purge.
        interrupted2 = self._make_interrupted_task()
        preview3 = self.service.preview_purge("owner", "ws", task_ids=(interrupted2.id,))
        self.service.resume_simulation("owner", interrupted2.id, idempotency_key=self._k("res-stale"))
        with self.assertRaisesRegex(OrchestrationError, "task_protected_from_purge"):
            self.service.purge_tasks(
                "owner", "ws",
                task_ids=preview3.task_ids,
                expected_preview_sha256=preview3.preview_sha256,
                confirmation=preview3.confirmation_phrase,
                idempotency_key=self._k("stale-3"),
            )

    def test_active_task_and_pending_approval_protection(self):
        # DRAFT
        draft = self.service.create_task("owner", "ws", "Draft active task", idempotency_key=self._k("draft"))
        # AWAITING_APPROVAL (pending human approval)
        awaiting = self.service.create_task("owner", "ws", "Awaiting approval task", idempotency_key=self._k("await"))
        self.service.propose_plan("owner", awaiting.id, DeterministicFakePlanner(proposal()),
                                  idempotency_key=self._k("await-plan"))
        # QUEUED
        queued = self.service.create_task("owner", "ws", "Queued active task", idempotency_key=self._k("queued"))
        qplan = self.service.propose_plan("owner", queued.id, DeterministicFakePlanner(proposal()),
                                          idempotency_key=self._k("qplan"))
        self.service.approve_plan("owner", queued.id, qplan.revision, qplan.plan_sha256,
                                  idempotency_key=self._k("qapprove"))
        # SIMULATING
        simulating = self.service.create_task("owner", "ws", "Simulating active task", idempotency_key=self._k("sim"))
        splan = self.service.propose_plan("owner", simulating.id, DeterministicFakePlanner(proposal()),
                                          idempotency_key=self._k("splan"))
        self.service.approve_plan("owner", simulating.id, splan.revision, splan.plan_sha256,
                                  idempotency_key=self._k("sapprove"))
        self.service.simulate_next_step(
            "owner", simulating.id,
            DeterministicFakeWorker({"one": (SimulationStep(25, "SIMULATED: active"),)}),
            idempotency_key=self._k("sstep"),
        )
        # CANCELLED after pending plan (should be allowed!)
        cancelled_after_plan = self._make_cancelled_task(with_pending_plan=True)

        for active_task in (draft, queued, simulating):
            with self.assertRaisesRegex(OrchestrationError, "task_not_archivable"):
                self.service.archive_task("owner", active_task.id, idempotency_key=self._k("arch-act"))
            with self.assertRaisesRegex(OrchestrationError, "task_protected_from_purge"):
                self.service.preview_purge("owner", "ws", task_ids=(active_task.id,))
            fake_hash = "a" * 64
            with self.assertRaisesRegex(OrchestrationError, "task_protected_from_purge"):
                self.service.purge_tasks(
                    "owner", "ws",
                    task_ids=(active_task.id,),
                    expected_preview_sha256=fake_hash,
                    confirmation=f"PURGE ws {fake_hash}",
                    idempotency_key=self._k("direct-act"),
                )

        with self.assertRaisesRegex(OrchestrationError, "task_not_archivable"):
            self.service.archive_task("owner", awaiting.id, idempotency_key=self._k("arch-pend"))
        with self.assertRaisesRegex(OrchestrationError, "task_has_pending_approval"):
            self.service.preview_purge("owner", "ws", task_ids=(awaiting.id,))
        fake_hash = "b" * 64
        with self.assertRaisesRegex(OrchestrationError, "task_has_pending_approval"):
            self.service.purge_tasks(
                "owner", "ws",
                task_ids=(awaiting.id,),
                expected_preview_sha256=fake_hash,
                confirmation=f"PURGE ws {fake_hash}",
                idempotency_key=self._k("direct-pend"),
            )

        # Policy-based preview skips all active/pending tasks and only picks the cancelled one.
        self.service.set_retention_policy(
            "owner", "ws", completed_retention_days=1, cancelled_retention_days=1,
            failed_retention_days=1, interrupted_retention_days=1,
        )
        future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(timespec="microseconds")
        policy_preview = self.service.preview_purge("owner", "ws", use_retention_policy=True, as_of=future)
        self.assertEqual(policy_preview.task_ids, (cancelled_after_plan.id,))
        self.assertEqual(policy_preview.protected_skipped_count, 4)

    def test_atomic_rollback_on_sqlite_failure(self):
        task = self._make_completed_task(goal="Must survive injected SQLite interruption")
        preview = self.service.preview_purge("owner", "ws", task_ids=(task.id,))
        original_plans = self.service.list_plans("owner", task.id)
        original_events = self.service.events("owner", task.id)

        with patch.object(
            Database, "_insert_audit",
            side_effect=sqlite3.OperationalError("simulated SQLite I/O failure mid-purge"),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.service.purge_tasks(
                    "owner", "ws",
                    task_ids=preview.task_ids,
                    expected_preview_sha256=preview.preview_sha256,
                    confirmation=preview.confirmation_phrase,
                    idempotency_key=self._k("fault-purge"),
                )

        # Everything rolled back atomically: task, plans, approvals, events intact; zero tombstones.
        self.assertEqual(self.service.get_task("owner", task.id).goal, task.goal)
        self.assertEqual(len(self.service.list_plans("owner", task.id)), len(original_plans))
        self.assertEqual(len(self.service.events("owner", task.id)), len(original_events))
        self.assertEqual(self.service.list_purge_tombstones("owner", "ws"), ())
        with self.db._connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM orchestration_purge_operations").fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM orchestration_approvals WHERE task_id=?", (task.id,)).fetchone()[0], 1)

    def test_concurrent_deletion_and_task_updates(self):
        # Race 1: purge vs resume on an interrupted task.
        task1 = self._make_interrupted_task()
        preview1 = self.service.preview_purge("owner", "ws", task_ids=(task1.id,))
        barrier1 = Barrier(2)
        outcomes1: dict[str, str] = {}

        def do_purge1():
            barrier1.wait()
            try:
                self.service.purge_tasks(
                    "owner", "ws",
                    task_ids=preview1.task_ids,
                    expected_preview_sha256=preview1.preview_sha256,
                    confirmation=preview1.confirmation_phrase,
                    idempotency_key=self._k("race1-purge"),
                )
                outcomes1["purge"] = "ok"
            except OrchestrationError as exc:
                outcomes1["purge"] = str(exc)

        def do_resume1():
            barrier1.wait()
            try:
                self.service.resume_simulation("owner", task1.id, idempotency_key=self._k("race1-resume"))
                outcomes1["resume"] = "ok"
            except OrchestrationError as exc:
                outcomes1["resume"] = str(exc)

        t1, t2 = Thread(target=do_purge1), Thread(target=do_resume1)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(list(outcomes1.values()).count("ok"), 1, outcomes1)
        if outcomes1["purge"] == "ok":
            with self.assertRaises(OrchestrationError):
                self.service.get_task("owner", task1.id)
            self.assertEqual(self.service.get_purge_tombstone("owner", "ws", task1.id).task_id, task1.id)
        else:
            self.assertEqual(self.service.get_task("owner", task1.id).state, TaskState.SIMULATING)
            with self.assertRaises(OrchestrationError):
                self.service.get_purge_tombstone("owner", "ws", task1.id)

        # Race 2: purge vs cancel on an interrupted task.
        task2 = self._make_interrupted_task()
        preview2 = self.service.preview_purge("owner", "ws", task_ids=(task2.id,))
        barrier2 = Barrier(2)
        outcomes2: dict[str, str] = {}

        def do_purge2():
            barrier2.wait()
            try:
                self.service.purge_tasks(
                    "owner", "ws",
                    task_ids=preview2.task_ids,
                    expected_preview_sha256=preview2.preview_sha256,
                    confirmation=preview2.confirmation_phrase,
                    idempotency_key=self._k("race2-purge"),
                )
                outcomes2["purge"] = "ok"
            except OrchestrationError as exc:
                outcomes2["purge"] = str(exc)

        def do_cancel2():
            barrier2.wait()
            try:
                self.service.cancel_task("owner", task2.id, idempotency_key=self._k("race2-cancel"))
                outcomes2["cancel"] = "ok"
            except OrchestrationError as exc:
                outcomes2["cancel"] = str(exc)

        t3, t4 = Thread(target=do_purge2), Thread(target=do_cancel2)
        t3.start()
        t4.start()
        t3.join()
        t4.join()
        self.assertEqual(list(outcomes2.values()).count("ok"), 1, outcomes2)

        # Race 3: concurrent duplicate purge with identical idempotency key.
        task3 = self._make_cancelled_task()
        preview3 = self.service.preview_purge("owner", "ws", task_ids=(task3.id,))
        shared_key = self._k("race3-samekey")
        barrier3 = Barrier(2)
        receipts: list[object] = []

        def do_dup_purge():
            barrier3.wait()
            receipts.append(self.service.purge_tasks(
                "owner", "ws",
                task_ids=preview3.task_ids,
                expected_preview_sha256=preview3.preview_sha256,
                confirmation=preview3.confirmation_phrase,
                idempotency_key=shared_key,
            ))

        t5, t6 = Thread(target=do_dup_purge), Thread(target=do_dup_purge)
        t5.start()
        t6.start()
        t5.join()
        t6.join()
        self.assertEqual(len(receipts), 2)
        self.assertEqual(receipts[0].purge_id, receipts[1].purge_id)
        self.assertEqual(sorted([receipts[0].already_purged, receipts[1].already_purged]), [False, True])

    def test_idempotent_deletion_and_conflict_rejection(self):
        task_a = self._make_cancelled_task()
        task_b = self._make_cancelled_task()
        preview_a = self.service.preview_purge("owner", "ws", task_ids=(task_a.id,))
        preview_b = self.service.preview_purge("owner", "ws", task_ids=(task_b.id,))
        idem_key = self._k("idemp-purge")

        first = self.service.purge_tasks(
            "owner", "ws",
            task_ids=preview_a.task_ids,
            expected_preview_sha256=preview_a.preview_sha256,
            confirmation=preview_a.confirmation_phrase,
            idempotency_key=idem_key,
        )
        second = self.service.purge_tasks(
            "owner", "ws",
            task_ids=preview_a.task_ids,
            expected_preview_sha256=preview_a.preview_sha256,
            confirmation=preview_a.confirmation_phrase,
            idempotency_key=idem_key,
        )
        self.assertFalse(first.already_purged)
        self.assertTrue(second.already_purged)
        self.assertEqual(first.purge_id, second.purge_id)
        self.assertEqual(len(self.service.list_purge_tombstones("owner", "ws")), 1)

        with self.assertRaisesRegex(OrchestrationError, "idempotency_key_conflict"):
            self.service.purge_tasks(
                "owner", "ws",
                task_ids=preview_b.task_ids,
                expected_preview_sha256=preview_b.preview_sha256,
                confirmation=preview_b.confirmation_phrase,
                idempotency_key=idem_key,
            )
        # Already-purged task with a brand-new idempotency key fails closed as stale.
        with self.assertRaisesRegex(OrchestrationError, "purge_preview_stale"):
            self.service.purge_tasks(
                "owner", "ws",
                task_ids=preview_a.task_ids,
                expected_preview_sha256=preview_a.preview_sha256,
                confirmation=preview_a.confirmation_phrase,
                idempotency_key=self._k("fresh-key-after-purge"),
            )

    def test_backup_restore_behavior_and_tampered_backup_rejection(self):
        # Grant owner full DB backup authority across workspaces in this fixture.
        self.db.grant_permission("other", "ws2", "owner", Permission.MANAGE_WORKSPACE)
        archived_task = self._make_completed_task(goal="Archived before backup")
        self.service.archive_task("owner", archived_task.id, idempotency_key=self._k("arch-bk"))
        purged_task = self._make_completed_task(goal="Purged after backup 1")
        self.service.set_retention_policy("owner", "ws", completed_retention_days=30, cancelled_retention_days=7)

        backup_before_purge = Path(self.temp.name) / "before_purge.sqlite3"
        self.db.backup_to(backup_before_purge, "owner")

        preview = self.service.preview_purge("owner", "ws", task_ids=(purged_task.id,))
        self.service.purge_tasks(
            "owner", "ws",
            task_ids=preview.task_ids,
            expected_preview_sha256=preview.preview_sha256,
            confirmation=preview.confirmation_phrase,
            idempotency_key=self._k("bk-purge"),
        )
        backup_after_purge = Path(self.temp.name) / "after_purge.sqlite3"
        self.db.backup_to(backup_after_purge, "owner")

        # Restoring post-purge backup preserves policy, archive state, and tombstone.
        restored_post_path = Path(self.temp.name) / "restored_post.sqlite3"
        Database.restore_from_backup(backup_after_purge, restored_post_path)
        restored_post = Database(restored_post_path)
        post_service = TaskOrchestrationService(restored_post)
        self.assertEqual(post_service.get_retention_policy("owner", "ws").completed_retention_days, 30)
        self.assertIsNotNone(post_service.get_task("owner", archived_task.id).archived_at)
        self.assertEqual(post_service.get_purge_tombstone("owner", "ws", purged_task.id).approval_count, 1)
        with self.assertRaises(OrchestrationError):
            post_service.get_task("owner", purged_task.id)

        # Restoring pre-purge historical backup reflects that historical snapshot.
        restored_pre_path = Path(self.temp.name) / "restored_pre.sqlite3"
        Database.restore_from_backup(backup_before_purge, restored_pre_path)
        restored_pre = Database(restored_pre_path)
        pre_service = TaskOrchestrationService(restored_pre)
        self.assertEqual(pre_service.get_task("owner", purged_task.id).goal, "Purged after backup 1")

        # Tampered v10 backup missing tombstone immutability trigger is rejected.
        tampered = Path(self.temp.name) / "tampered.sqlite3"
        self.db.backup_to(tampered, "owner")
        conn = sqlite3.connect(tampered)
        try:
            conn.execute("DROP TRIGGER orchestration_purge_tombstones_no_delete")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(sqlite3.DatabaseError):
            Database.restore_from_backup(tampered, Path(self.temp.name) / "tampered_target.sqlite3")

    def test_permission_revocation_blocks_purge_and_idempotent_replay(self):
        task = self._make_cancelled_task()
        # Regular member (READ+WRITE, no MANAGE_WORKSPACE) cannot configure retention or purge.
        with self.assertRaisesRegex(OrchestrationError, "task_manager_approval_required"):
            self.service.set_retention_policy("member", "ws", cancelled_retention_days=7)
        with self.assertRaisesRegex(OrchestrationError, "task_manager_approval_required"):
            self.service.preview_purge("member", "ws", task_ids=(task.id,))
        with self.assertRaisesRegex(OrchestrationError, "task_manager_approval_required"):
            self.service.list_purge_tombstones("member", "ws")

        # Grant manager2 MANAGE_WORKSPACE, preview purge, then revoke before confirmation.
        for perm in (Permission.READ, Permission.WRITE, Permission.MANAGE_WORKSPACE):
            self.db.grant_permission("owner", "ws", "manager2", perm)
        preview = self.service.preview_purge("manager2", "ws", task_ids=(task.id,))
        self.db.revoke_permission("owner", "ws", "manager2", Permission.MANAGE_WORKSPACE)
        with self.assertRaisesRegex(OrchestrationError, "task_manager_approval_required"):
            self.service.purge_tasks(
                "manager2", "ws",
                task_ids=preview.task_ids,
                expected_preview_sha256=preview.preview_sha256,
                confirmation=preview.confirmation_phrase,
                idempotency_key=self._k("revoked-purge"),
            )
        self.assertEqual(self.service.get_task("owner", task.id).state, TaskState.CANCELLED)

        # Re-grant, complete purge, then revoke all permissions: even idempotent replay is denied.
        self.db.grant_permission("owner", "ws", "manager2", Permission.MANAGE_WORKSPACE)
        idem = self._k("manager2-purge")
        self.service.purge_tasks(
            "manager2", "ws",
            task_ids=preview.task_ids,
            expected_preview_sha256=preview.preview_sha256,
            confirmation=preview.confirmation_phrase,
            idempotency_key=idem,
        )
        for perm in (Permission.MANAGE_WORKSPACE, Permission.WRITE, Permission.READ):
            self.db.revoke_permission("owner", "ws", "manager2", perm)
        with self.assertRaises(AccessDenied):
            self.service.purge_tasks(
                "manager2", "ws",
                task_ids=preview.task_ids,
                expected_preview_sha256=preview.preview_sha256,
                confirmation=preview.confirmation_phrase,
                idempotency_key=idem,
            )
        with self.assertRaises(AccessDenied):
            self.service.list_purge_tombstones("manager2", "ws")

    def test_v9_to_v10_upgraded_task_supports_archive_and_purge(self):
        upgrade_path = Path(self.temp.name) / "v9_to_v10_e2e.sqlite3"
        v9_db = Database(upgrade_path, migrations=DEFAULT_MIGRATIONS[:9])
        v9_db.initialize()
        v9_db.create_workspace("Upgraded WS", WorkspaceKind.PROJECT, "owner", "ws_up")
        # Insert a valid v9 task directly (schema v9 has no archived_at/archived_by columns).
        from jarvis_core.orchestration import _canonical_json, _digest
        manifest = {
            "workspace_id": "ws_up", "repository_id": None,
            "requested": [], "granted": [], "denied": [], "unavailable": [],
            "execution_enabled": False, "file_write_enabled": False,
            "process_execution_enabled": False, "network_enabled": False,
            "plan_approval_is_execution_approval": False,
        }
        manifest_json = _canonical_json(manifest)
        goal = "Legacy schema v9 cancelled task"
        with v9_db._connection() as conn:
            conn.execute(
                "INSERT INTO orchestration_tasks(id,workspace_id,principal_id,goal,goal_sha256,"
                "state,execution_mode,requested_capabilities_json,capability_manifest_json,"
                "capability_manifest_sha256,created_at,updated_at,expires_at) "
                "VALUES(?,?,?,?,?,'cancelled','planned','[]',?,?, "
                "'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00','2026-02-01T00:00:00+00:00')",
                ("legacy_v9_task", "ws_up", "owner", goal, _digest(goal),
                 manifest_json, _digest(manifest_json)),
            )
            conn.commit()

        v10_db = Database(upgrade_path)
        v10_db.initialize()
        service = TaskOrchestrationService(v10_db)
        task = service.get_task("owner", "legacy_v9_task")
        self.assertIsNone(task.archived_at)
        archived = service.archive_task("owner", task.id, idempotency_key=self._k("up-arch"))
        self.assertIsNotNone(archived.archived_at)
        preview = service.preview_purge("owner", "ws_up", task_ids=(task.id,), archived_only=True)
        receipt = service.purge_tasks(
            "owner", "ws_up",
            task_ids=preview.task_ids,
            expected_preview_sha256=preview.preview_sha256,
            confirmation=preview.confirmation_phrase,
            idempotency_key=self._k("up-purge"),
            archived_only=True,
        )
        self.assertEqual(receipt.task_count, 1)
        tombstone = service.get_purge_tombstone("owner", "ws_up", task.id)
        self.assertFalse(hasattr(tombstone, "goal_sha256"))
        self.assertEqual(tombstone.task_id, task.id)
        self.assertEqual(tombstone.task_state, TaskState.CANCELLED)

    def test_no_regression_of_coding_agent_restrictions(self):
        task = self._make_completed_task(goal="Verify P4 restrictions after archive/restore")
        archived = self.service.archive_task("owner", task.id, idempotency_key=self._k("p4-arch"))
        restored = self.service.restore_archived_task("owner", task.id, idempotency_key=self._k("p4-rest"))
        for snapshot in (task, archived, restored):
            self.assertFalse(snapshot.capability_manifest["execution_enabled"])
            self.assertFalse(snapshot.capability_manifest["file_write_enabled"])
            self.assertFalse(snapshot.capability_manifest["process_execution_enabled"])
            self.assertFalse(snapshot.capability_manifest["network_enabled"])
            self.assertFalse(snapshot.capability_manifest["plan_approval_is_execution_approval"])

        policy = CodingCapabilityPolicy(self.db)
        for forbidden in (
            CodingCapability.CREATE_ISOLATED_WORKTREE,
            CodingCapability.MODIFY_TASK_WORKTREE,
            CodingCapability.EXECUTE_VERIFICATION,
            CodingCapability.REQUEST_NETWORK_ACCESS,
            CodingCapability.CREATE_COMMIT,
            CodingCapability.PUSH_BRANCH,
            CodingCapability.OPEN_PULL_REQUEST,
            CodingCapability.MERGE_OR_DEPLOY,
        ):
            with self.assertRaises(AccessDenied):
                policy.grant("owner", "ws", "repo-1", "owner", forbidden, confirmed=True)

        draft = self.service.create_task("owner", "ws", "Reject non-fake planner/worker",
                                         idempotency_key=self._k("p4-nonfake"))
        class RealPlanner:
            def propose_plan(self, request):
                return proposal()
        with self.assertRaisesRegex(OrchestrationError, "model_planning_disabled"):
            self.service.propose_plan("owner", draft.id, RealPlanner(), idempotency_key=self._k("p4-real"))

    def test_concurrent_manager_revocation_during_history_and_task_listing(self):
        owner_task = self._make_completed_task(
            actor="owner",
            workspace_id="ws",
            goal="OWNER_CONFIDENTIAL_GOAL_88412",
        )
        self.assertIsNotNone(owner_task.result_summary)

        for perm in (Permission.READ, Permission.WRITE, Permission.MANAGE_WORKSPACE):
            self.db.grant_permission("owner", "ws", "temp_mgr", perm)

        own_task = self._make_cancelled_task(
            actor="temp_mgr",
            workspace_id="ws",
            goal="Temp manager own active cancelled task",
        )
        own_archived = self._make_cancelled_task(
            actor="temp_mgr",
            workspace_id="ws",
            goal="Temp manager own archived task",
        )
        self.service.archive_task("temp_mgr", own_archived.id, idempotency_key=self._k("tm-arch"))

        # Baseline while MANAGE_WORKSPACE is held: sees all workspace tasks.
        baseline = self.service.list_task_history("temp_mgr", "ws", include_archived=True)
        self.assertEqual(baseline.total_count, 3)
        self.assertEqual({t.id for t in baseline.items}, {owner_task.id, own_task.id, own_archived.id})
        self.assertEqual(
            {t.id for t in self.service.list_tasks("temp_mgr", "ws")},
            {owner_task.id, own_task.id},
        )

        def run_with_inflight_revocation(permission: Permission, reader_call):
            revocation_holding_tx = Event()
            release_revocation = Event()
            reader_entered_begin = Event()
            revoker_tid = [None]
            reader_tid = [None]
            reader_result = []
            reader_error = []
            revoker_error = []

            orig_commit = Database._commit
            orig_begin = Database._begin

            def hooked_commit(conn):
                if get_ident() == revoker_tid[0] and not revocation_holding_tx.is_set():
                    revocation_holding_tx.set()
                    if not release_revocation.wait(timeout=5.0):
                        raise TimeoutError("timed out waiting to release revocation commit")
                orig_commit(conn)

            def hooked_begin(conn):
                if get_ident() == reader_tid[0] and not reader_entered_begin.is_set():
                    reader_entered_begin.set()
                orig_begin(conn)

            def do_revoke():
                revoker_tid[0] = get_ident()
                try:
                    self.db.revoke_permission("owner", "ws", "temp_mgr", permission)
                except Exception as exc:
                    revoker_error.append(exc)

            def do_read():
                reader_tid[0] = get_ident()
                try:
                    reader_result.append(reader_call())
                except Exception as exc:
                    reader_error.append(exc)

            with patch.object(Database, "_commit", staticmethod(hooked_commit)), \
                 patch.object(Database, "_begin", staticmethod(hooked_begin)):
                revoker = Thread(target=do_revoke)
                reader = Thread(target=do_read)
                try:
                    revoker.start()
                    self.assertTrue(revocation_holding_tx.wait(timeout=5.0))
                    # Revoker now holds BEGIN IMMEDIATE with DELETE uncommitted.
                    reader.start()
                    self.assertTrue(reader_entered_begin.wait(timeout=5.0))
                    time.sleep(0.05)
                    # Protected transaction must serialize reader behind the in-flight revocation.
                    self.assertTrue(reader.is_alive())
                finally:
                    release_revocation.set()
                    revoker.join(timeout=5.0)
                    reader.join(timeout=5.0)
            self.assertFalse(revoker.is_alive())
            self.assertFalse(reader.is_alive())
            self.assertEqual(revoker_error, [])
            return reader_result, reader_error

        # 1. Revoking MANAGE_WORKSPACE during list_task_history restricts results to temp_mgr's own tasks.
        results, errors = run_with_inflight_revocation(
            Permission.MANAGE_WORKSPACE,
            lambda: self.service.list_task_history("temp_mgr", "ws", include_archived=True),
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        page = results[0]
        self.assertEqual(page.total_count, 2)
        self.assertFalse(page.has_more)
        self.assertEqual({t.id for t in page.items}, {own_task.id, own_archived.id})
        self.assertTrue(all(t.principal_id == "temp_mgr" for t in page.items))
        self.assertNotIn(owner_task.goal, [t.goal for t in page.items])
        self.assertNotIn(owner_task.result_summary, [t.result_summary for t in page.items])

        # Verify pagination and archive filtering remain accurate for non-manager READ user.
        archived_only_page = self.service.list_task_history("temp_mgr", "ws", archived_only=True)
        self.assertEqual([t.id for t in archived_only_page.items], [own_archived.id])
        self.assertEqual(archived_only_page.total_count, 1)
        p1 = self.service.list_task_history("temp_mgr", "ws", include_archived=True, limit=1, offset=0)
        p2 = self.service.list_task_history("temp_mgr", "ws", include_archived=True, limit=1, offset=1)
        self.assertEqual(p1.total_count, 2)
        self.assertTrue(p1.has_more)
        self.assertFalse(p2.has_more)
        self.assertEqual({p1.items[0].id, p2.items[0].id}, {own_task.id, own_archived.id})

        # 2. Re-grant MANAGE_WORKSPACE and verify the exact same protection for list_tasks.
        self.db.grant_permission("owner", "ws", "temp_mgr", Permission.MANAGE_WORKSPACE)
        results, errors = run_with_inflight_revocation(
            Permission.MANAGE_WORKSPACE,
            lambda: self.service.list_tasks("temp_mgr", "ws"),
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        listed = results[0]
        self.assertEqual([t.id for t in listed], [own_task.id])
        self.assertNotIn(owner_task.goal, [t.goal for t in listed])
        self.assertNotIn(owner_task.result_summary, [t.result_summary for t in listed])

        # 3. Revoking READ during list_task_history denies all access cleanly.
        results, errors = run_with_inflight_revocation(
            Permission.READ,
            lambda: self.service.list_task_history("temp_mgr", "ws", include_archived=True),
        )
        self.assertEqual(results, [])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], AccessDenied)

    def test_bounded_purge_preview_with_500_plus_synthetic_tasks(self):
        from jarvis_core.orchestration import MAX_PURGE_BATCH, _canonical_json, _digest

        as_of_dt = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        as_of_iso = as_of_dt.isoformat(timespec="microseconds")
        expires_iso = datetime(2027, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat(timespec="microseconds")
        manifest = {
            "workspace_id": "ws", "repository_id": None,
            "requested": [], "granted": [], "denied": [], "unavailable": [],
            "execution_enabled": False, "file_write_enabled": False,
            "process_execution_enabled": False, "network_enabled": False,
            "plan_approval_is_execution_approval": False,
        }
        manifest_json = _canonical_json(manifest)
        manifest_sha = _digest(manifest_json)

        task_rows: list[tuple[object, ...]] = []
        pending_plan_rows: list[tuple[object, ...]] = []

        def add_task(tid: str, state: str, updated_dt: datetime, *, archived: bool = False) -> None:
            goal = f"Synthetic task {tid}"
            ts = updated_dt.isoformat(timespec="microseconds")
            archived_at = ts if archived else None
            archived_by = "owner" if archived else None
            task_rows.append((
                tid, "ws", "owner", None, None, goal, _digest(goal),
                state, "planned", "[]", manifest_json, manifest_sha,
                1 if state == "interrupted" else 0, None, None,
                ts, ts, expires_iso, archived_at, archived_by,
            ))

        # 100 active protected tasks across all 5 protected states (very old timestamps).
        protected_states = ("draft", "awaiting_approval", "queued", "simulating", "blocked")
        base_protected = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        for i in range(100):
            add_task(
                f"prot_{i:03d}",
                protected_states[i % len(protected_states)],
                base_protected + timedelta(minutes=i),
            )

        # 20 interrupted tasks protected by a pending awaiting_approval plan.
        base_pending = datetime(2026, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        for i in range(20):
            tid = f"pend_{i:03d}"
            ts = (base_pending + timedelta(minutes=i)).isoformat(timespec="microseconds")
            add_task(tid, "interrupted", base_pending + timedelta(minutes=i))
            pending_plan_rows.append((
                tid, "ws", "owner", 1, _digest(f"plan-{tid}"), "awaiting_approval", ts,
            ))

        # 300 expired eligible tasks (interleaved states & archived flags, oldest-first).
        base_eligible = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
        eligible_ids_ordered: list[str] = []
        archived_eligible_ids_ordered: list[str] = []
        for i in range(300):
            tid = f"elig_{i:03d}"
            state = "cancelled" if i % 2 == 0 else "simulation_completed"
            is_archived = (i % 3 == 0)
            add_task(tid, state, base_eligible + timedelta(minutes=i), archived=is_archived)
            eligible_ids_ordered.append(tid)
            if is_archived:
                archived_eligible_ids_ordered.append(tid)

        # 140 unexpired tasks (70 within the 1-day SQL prefilter margin, 70 well within window).
        margin_base = as_of_dt - timedelta(days=9, hours=12)
        for i in range(70):
            add_task(f"unexp_margin_{i:03d}", "cancelled", margin_base + timedelta(minutes=i))
        recent_base = as_of_dt - timedelta(days=5)
        for i in range(70):
            add_task(f"unexp_recent_{i:03d}", "simulation_completed", recent_base + timedelta(minutes=i))

        self.assertEqual(len(task_rows), 560)
        with self.db._connection() as conn:
            conn.executemany(
                "INSERT INTO orchestration_tasks("
                "id,workspace_id,principal_id,repository_id,repository_commit,goal,goal_sha256,"
                "state,execution_mode,requested_capabilities_json,capability_manifest_json,"
                "capability_manifest_sha256,current_plan_revision,approved_plan_revision,"
                "result_summary,created_at,updated_at,expires_at,archived_at,archived_by) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                task_rows,
            )
            conn.executemany(
                "INSERT INTO orchestration_plans("
                "task_id,workspace_id,principal_id,revision,plan_sha256,state,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                pending_plan_rows,
            )
            conn.commit()

        # Default policy (no retention days): scans 0 candidate rows, reports exact protected count.
        hydrated = [0]
        orig_task = TaskOrchestrationService._task

        def counting_task(row):
            hydrated[0] += 1
            return orig_task(row)

        with patch.object(TaskOrchestrationService, "_task", staticmethod(counting_task)):
            default_preview = self.service.preview_purge(
                "owner", "ws", use_retention_policy=True, as_of=as_of_iso,
            )
        self.assertEqual(default_preview.task_count, 0)
        self.assertFalse(default_preview.truncated)
        self.assertEqual(default_preview.protected_skipped_count, 120)
        self.assertEqual(hydrated[0], 0)

        # Configure retention policy covering completed, cancelled, and interrupted tasks.
        self.service.set_retention_policy(
            "owner", "ws",
            completed_retention_days=30,
            cancelled_retention_days=10,
            interrupted_retention_days=5,
        )

        # Full-batch preview (limit=50) must hydrate only O(limit) rows, never all 560.
        hydrated[0] = 0
        with patch.object(TaskOrchestrationService, "_task", staticmethod(counting_task)):
            preview = self.service.preview_purge(
                "owner", "ws", use_retention_policy=True, as_of=as_of_iso, limit=MAX_PURGE_BATCH,
            )
        self.assertEqual(preview.task_count, MAX_PURGE_BATCH)
        self.assertTrue(preview.truncated)
        self.assertEqual(preview.protected_skipped_count, 120)
        self.assertEqual(preview.task_ids, tuple(sorted(eligible_ids_ordered[:MAX_PURGE_BATCH])))
        self.assertLessEqual(hydrated[0], MAX_PURGE_BATCH + 1)

        # Archived-only preview selects exact oldest 50 archived eligible tasks in bounded memory.
        hydrated[0] = 0
        with patch.object(TaskOrchestrationService, "_task", staticmethod(counting_task)):
            arch_preview = self.service.preview_purge(
                "owner", "ws",
                use_retention_policy=True,
                archived_only=True,
                as_of=as_of_iso,
                limit=MAX_PURGE_BATCH,
            )
        self.assertEqual(arch_preview.task_count, MAX_PURGE_BATCH)
        self.assertTrue(arch_preview.truncated)
        self.assertEqual(arch_preview.protected_skipped_count, 0)
        self.assertEqual(
            arch_preview.task_ids,
            tuple(sorted(archived_eligible_ids_ordered[:MAX_PURGE_BATCH])),
        )
        self.assertLessEqual(hydrated[0], MAX_PURGE_BATCH + 1)

        # Exact preview SHA-256 binds purge_tasks cleanly, and the next batch selects oldest 50..99.
        receipt = self.service.purge_tasks(
            "owner", "ws",
            task_ids=preview.task_ids,
            expected_preview_sha256=preview.preview_sha256,
            confirmation=preview.confirmation_phrase,
            idempotency_key=self._k("batch-500-1"),
            use_retention_policy=True,
            as_of=as_of_iso,
        )
        self.assertEqual(receipt.task_count, MAX_PURGE_BATCH)
        self.assertEqual(receipt.preview_sha256, preview.preview_sha256)

        next_preview = self.service.preview_purge(
            "owner", "ws", use_retention_policy=True, as_of=as_of_iso, limit=MAX_PURGE_BATCH,
        )
        self.assertEqual(next_preview.task_count, MAX_PURGE_BATCH)
        self.assertTrue(next_preview.truncated)
        self.assertEqual(
            next_preview.task_ids,
            tuple(sorted(eligible_ids_ordered[MAX_PURGE_BATCH:MAX_PURGE_BATCH * 2])),
        )

        # Stale preview is still rejected when a selected task changes before purge.
        small_preview = self.service.preview_purge(
            "owner", "ws", use_retention_policy=True, as_of=as_of_iso, limit=5,
        )
        self.assertEqual(small_preview.task_count, 5)
        self.assertTrue(small_preview.truncated)
        unarchived_target = next(item.task_id for item in small_preview.items if item.archived_at is None)
        self.service.archive_task("owner", unarchived_target, idempotency_key=self._k("stale-500-arch"))
        with self.assertRaisesRegex(OrchestrationError, "purge_preview_stale"):
            self.service.purge_tasks(
                "owner", "ws",
                task_ids=small_preview.task_ids,
                expected_preview_sha256=small_preview.preview_sha256,
                confirmation=small_preview.confirmation_phrase,
                idempotency_key=self._k("stale-500-purge"),
                use_retention_policy=True,
                as_of=as_of_iso,
            )

    def test_v11_tombstone_privacy_snapshot_schema2_and_v10_receipt_replay(self):
        from jarvis_core.orchestration import _canonical_json, _digest, _purge_confirmation_phrase

        # 1. Fresh v11 purge preview uses schema 2 without goal_sha256 or result_summary_sha256.
        secret_goal = "Confidential v11 goal string 9981"
        task = self._make_completed_task(goal=secret_goal)
        preview = self.service.preview_purge("owner", "ws", task_ids=(task.id,))
        self.assertEqual(preview.task_count, 1)
        with self.db._connection() as conn:
            latest_event_id = int(conn.execute(
                "SELECT COALESCE(MAX(id), 0) FROM orchestration_task_events WHERE workspace_id=? AND task_id=?",
                ("ws", task.id),
            ).fetchone()[0])
        item = preview.items[0]
        expected_v2_snapshot = _digest(_canonical_json({
            "schema": 2,
            "workspace_id": "ws",
            "archived_only": False,
            "items": [{
                "task_id": item.task_id,
                "state": item.state.value,
                "archived_at": item.archived_at,
                "updated_at": item.updated_at,
                "current_plan_revision": task.current_plan_revision,
                "approved_plan_revision": task.approved_plan_revision,
                "plan_count": item.plan_count,
                "subtask_count": item.subtask_count,
                "dependency_count": item.dependency_count,
                "approval_count": item.approval_count,
                "event_count": item.event_count,
                "latest_event_id": latest_event_id,
            }],
        }))
        self.assertEqual(preview.preview_sha256, expected_v2_snapshot)

        # Updating goal & result_summary without altering structural metadata keeps preview_sha256 identical
        # (proving neither goal_sha256 nor result_summary_sha256 is an input to persisted preview_sha256).
        alt_goal = "Different goal text for snapshot check"
        with self.db._connection() as conn:
            conn.execute(
                "UPDATE orchestration_tasks SET goal=?, goal_sha256=?, result_summary=? WHERE id=?",
                (alt_goal, _digest(alt_goal), "Different result summary", task.id),
            )
            conn.commit()
        preview_after_text_change = self.service.preview_purge("owner", "ws", task_ids=(task.id,))
        self.assertEqual(preview_after_text_change.preview_sha256, preview.preview_sha256)

        # Live task goal integrity check remains enforced (corrupted goal_sha256 fails closed).
        with self.db._connection() as conn:
            conn.execute(
                "UPDATE orchestration_tasks SET goal_sha256=? WHERE id=?",
                ("0" * 64, task.id),
            )
            conn.commit()
        with self.assertRaisesRegex(OrchestrationError, "task_evidence_corrupt"):
            self.service.get_task("owner", task.id)
        with self.db._connection() as conn:
            conn.execute(
                "UPDATE orchestration_tasks SET goal=?, goal_sha256=? WHERE id=?",
                (secret_goal, _digest(secret_goal), task.id),
            )
            conn.commit()

        # Confirmed v11 purge persists no goal hashes in tombstones, purge ops, or purge audit events.
        receipt = self.service.purge_tasks(
            "owner", "ws",
            task_ids=preview.task_ids,
            expected_preview_sha256=preview.preview_sha256,
            confirmation=preview.confirmation_phrase,
            idempotency_key=self._k("v11-privacy-purge"),
        )
        self.assertEqual(receipt.task_count, 1)
        tombstone = self.service.get_purge_tombstone("owner", "ws", task.id)
        self.assertFalse(hasattr(tombstone, "goal_sha256"))
        with self.db._connection() as conn:
            for table in ("orchestration_purge_tombstones", "orchestration_purge_operations"):
                joined = "\n".join(str(dict(r)) for r in conn.execute(f"SELECT * FROM {table}").fetchall())
                self.assertNotIn(task.goal_sha256, joined)
                self.assertNotIn(secret_goal, joined)
            purge_audits = conn.execute(
                "SELECT metadata_json FROM audit_events WHERE event_type='orchestration.tasks_purged'"
            ).fetchall()
            self.assertEqual(len(purge_audits), 1)
            purge_audit_text = "\n".join(r["metadata_json"] for r in purge_audits)
            self.assertNotIn(task.goal_sha256, purge_audit_text)
            self.assertNotIn(secret_goal, purge_audit_text)

        # 2. Historical v10 database with schema-1 purge receipt upgrades to v11 and stays replayable.
        v10_path = Path(self.temp.name) / "upgrade-v10-receipt.sqlite3"
        v10_db = Database(v10_path, migrations=DEFAULT_MIGRATIONS[:10])
        v10_db.initialize()
        v10_db.create_workspace("Historical V10", WorkspaceKind.PROJECT, "owner", "ws_v10")
        v10_db.grant_permission("owner", "ws_v10", "reader", Permission.READ)
        v10_preview_sha = _digest(_canonical_json({
            "schema": 1,
            "workspace_id": "ws_v10",
            "archived_only": True,
            "items": [{"task_id": "hist-task-1", "goal_sha256": _digest("Historical secret goal")}],
        }))
        hist_idem = self._k("hist-v10-purge")
        with v10_db._connection() as conn:
            conn.execute(
                "INSERT INTO orchestration_purge_operations("
                "purge_id,workspace_id,actor_id,idempotency_key,preview_sha256,"
                "task_count,plan_count,subtask_count,dependency_count,"
                "approval_count,event_count,purged_at) "
                "VALUES('hist-purge-1','ws_v10','owner',?,?,"
                "2,3,6,2,2,11,'2026-02-10T00:00:00+00:00')",
                (hist_idem, v10_preview_sha),
            )
            conn.execute(
                "INSERT INTO orchestration_purge_tombstones("
                "workspace_id,task_id,purge_id,task_state,archived_at,goal_sha256,"
                "plan_count,subtask_count,dependency_count,approval_count,"
                "event_count,purged_by,purged_at) "
                "VALUES('ws_v10','hist-task-1','hist-purge-1','completed',"
                "'2026-02-01T00:00:00+00:00',?,2,4,1,1,6,'owner','2026-02-10T00:00:00+00:00')",
                (_digest("Historical secret goal"),),
            )
            conn.execute(
                "INSERT INTO orchestration_purge_tombstones("
                "workspace_id,task_id,purge_id,task_state,archived_at,goal_sha256,"
                "plan_count,subtask_count,dependency_count,approval_count,"
                "event_count,purged_by,purged_at) "
                "VALUES('ws_v10','hist-task-2','hist-purge-1','cancelled',"
                "'2026-02-02T00:00:00+00:00',?,1,2,1,1,5,'owner','2026-02-10T00:00:00+00:00')",
                (_digest("Second historical goal"),),
            )
            conn.commit()

        upgraded_db = Database(v10_path)
        upgraded_db.initialize()
        upgraded_service = TaskOrchestrationService(upgraded_db)

        # Upgraded tombstones retain IDs and counts without goal_sha256; authorization remains enforced.
        listed = upgraded_service.list_purge_tombstones("owner", "ws_v10")
        self.assertEqual([t.task_id for t in listed], ["hist-task-1", "hist-task-2"])
        self.assertEqual((listed[0].plan_count, listed[0].approval_count, listed[0].event_count), (2, 1, 6))
        self.assertEqual((listed[1].plan_count, listed[1].approval_count, listed[1].event_count), (1, 1, 5))
        self.assertFalse(hasattr(listed[0], "goal_sha256"))
        with self.assertRaises(AccessDenied):
            upgraded_service.list_purge_tombstones("reader", "ws_v10")

        # Historical v10 receipt replays idempotently using its original schema-1 preview_sha256.
        replay = upgraded_service.purge_tasks(
            "owner", "ws_v10",
            task_ids=("hist-task-1", "hist-task-2"),
            expected_preview_sha256=v10_preview_sha,
            confirmation=_purge_confirmation_phrase("ws_v10", v10_preview_sha),
            idempotency_key=hist_idem,
            archived_only=True,
        )
        self.assertTrue(replay.already_purged)
        self.assertEqual(replay.purge_id, "hist-purge-1")
        self.assertEqual(replay.preview_sha256, v10_preview_sha)
        self.assertEqual(replay.task_ids, ("hist-task-1", "hist-task-2"))
        self.assertEqual((replay.task_count, replay.approval_count, replay.event_count), (2, 2, 11))

        # Reusing the historical idempotency key with a different preview digest still conflicts.
        wrong_sha = "f" * 64
        with self.assertRaisesRegex(OrchestrationError, "idempotency_key_conflict"):
            upgraded_service.purge_tasks(
                "owner", "ws_v10",
                task_ids=("hist-task-1", "hist-task-2"),
                expected_preview_sha256=wrong_sha,
                confirmation=_purge_confirmation_phrase("ws_v10", wrong_sha),
                idempotency_key=hist_idem,
                archived_only=True,
            )

        # 3. Pending approvals and cancellation remain behaviorally unchanged on upgraded v11 DB.
        pending = upgraded_service.create_task("owner", "ws_v10", "Pending approval task on v11", idempotency_key=self._k("p-create"))
        upgraded_service.propose_plan("owner", pending.id, DeterministicFakePlanner(proposal()), idempotency_key=self._k("p-plan"))
        with self.assertRaisesRegex(OrchestrationError, "task_has_pending_approval"):
            upgraded_service.preview_purge("owner", "ws_v10", task_ids=(pending.id,))
        cancelled = upgraded_service.cancel_task("owner", pending.id, idempotency_key=self._k("p-cancel"))
        self.assertEqual(cancelled.state, TaskState.CANCELLED)


if __name__ == "__main__":
    unittest.main()
