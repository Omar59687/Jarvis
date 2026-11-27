from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest

from jarvis_core.missions import MissionService
from jarvis_core.mission_planning import MissionPlanningService, MissionPlanningConfig
from jarvis_core.model_provider import GenerationResult
from jarvis_core.orchestration import (
    DeterministicFakePlanner, PlannedSubtask, TaskOrchestrationService, TaskPlanProposal,
)
from jarvis_core.storage import Database, WorkspaceKind
from prototypes.m03_b1.service import make_server


class _NoInference:
    def generate(self, *_args, **_kwargs):
        raise AssertionError("startup recovery must not call a model")


class _BlockingProvider:
    timeout = 5.0

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def generate(self, *_args, **_kwargs):
        self.entered.set()
        if not self.release.wait(5):
            raise AssertionError("test provider release timed out")
        return GenerationResult("offline-test", '{"schema_version":1,"subtasks":['
            '{"id":"step-01","title":"Review","goal":"Review stated goal",'
            '"acceptance_condition":"Plan is reviewed","required_capabilities":[],"dependencies":[]}]}', True)


class M08DReliabilityTests(unittest.TestCase):
    def test_restart_recovery_fences_in_progress_planning_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "planning-recovery.sqlite3")
            database.initialize()
            database.create_workspace("Recovery fixture", WorkspaceKind.PROJECT,
                                      "owner", "planning-recovery-ws")
            tasks = TaskOrchestrationService(database)
            missions = MissionService(database, tasks)
            mission = missions.create(
                "owner", "planning-recovery-ws", "Synthetic planning recovery fixture",
                idempotency_key="planning-recovery-create",
            )
            provider = _BlockingProvider()
            planner = MissionPlanningService(provider, model="offline-test",
                                             config=MissionPlanningConfig(max_attempts=1))
            errors = []

            def plan():
                try:
                    missions.propose_plan("owner", mission.id, planner,
                                          idempotency_key="planning-recovery-plan")
                except Exception as exc:  # capture the worker's expected stale-owner result
                    errors.append(exc)

            worker = threading.Thread(target=plan)
            worker.start()
            self.assertTrue(provider.entered.wait(5))
            missions.recover_after_restart("owner", "planning-recovery-ws")
            provider.release.set()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertEqual(str(errors[0]), "mission_plan_owner_stale")
            self.assertEqual(tasks.get_task("owner", mission.task_id).current_plan_revision, 0)
            with database._connection() as connection:
                reservation = connection.execute(
                    "SELECT state,owner_token,lease_expires_at FROM mission_planning_reservations "
                    "WHERE mission_id=?", (mission.id,),
                ).fetchone()
            self.assertEqual(tuple(reservation), ("interrupted", None, None))

    def test_backend_startup_quarantines_active_mission_and_task_without_resuming(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "isolated.sqlite3")
            database.initialize()
            database.create_workspace("Recovery fixture", WorkspaceKind.PROJECT,
                                      "owner", "recovery-ws")
            tasks = TaskOrchestrationService(database)
            missions = MissionService(database, tasks)
            mission = missions.create(
                "owner", "recovery-ws", "Synthetic restart recovery fixture",
                idempotency_key="startup-recovery-create-mission",
            )
            plan = missions.propose_fake_plan(
                "owner", mission.id,
                DeterministicFakePlanner(TaskPlanProposal((PlannedSubtask(
                    "one", "Synthetic step", "No external action", "Remain simulated"),))),
                idempotency_key="startup-recovery-propose-plan",
            )
            missions.authorize("owner", mission.id, plan.revision, plan.plan_sha256,
                               idempotency_key="startup-recovery-authorize")

            server = make_server(0, actor_id="owner", database=database,
                                 model="offline-test", generator=_NoInference(),
                                 pairing_code="isolated-recovery-test")
            try:
                recovered_mission = missions.get("owner", mission.id)
                recovered_task = tasks.get_task("owner", mission.task_id)
                self.assertEqual(recovered_mission.state, "interrupted")
                self.assertEqual(recovered_task.state.value, "interrupted")
                self.assertEqual(recovered_mission.steps_used, 0)
                self.assertEqual(tasks.get_progress("owner", mission.task_id, plan.revision)[0].progress_percent, 0)
                events = missions.events("owner", mission.id)
                self.assertEqual(events[0]["event_type"], "mission_interrupted")
                self.assertFalse(any(event["event_type"] in {"mission_simulation_step", "mission_completed"}
                                     for event in events))
            finally:
                server.server_close()


if __name__ == "__main__":
    unittest.main()
