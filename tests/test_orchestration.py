from __future__ import annotations

from pathlib import Path
import sqlite3
import subprocess
import tempfile
from threading import Barrier, Thread
import unittest

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
from jarvis_core.storage import AccessDenied, Database, Permission, WorkspaceKind


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


if __name__ == "__main__":
    unittest.main()
