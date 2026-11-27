"""Trusted-terminal controls for explicitly authorized mission simulation."""
from __future__ import annotations

from typing import Callable, TextIO
import secrets
from uuid import uuid4

from .missions import MissionService
from .orchestration import DeterministicFakePlanner, DeterministicFakeWorker
from .orchestration_operator import _safe_terminal
from .storage import Database, Permission


class MissionOperatorWorkflow:
    """Manager-only helper; identity is injected by the trusted local host."""

    def __init__(self, principal_id: str, workspace_id: str, database: Database,
                 missions: MissionService, *, input_fn: Callable[[str], str] = input,
                 output: TextIO) -> None:
        self.principal_id = principal_id
        self.workspace_id = workspace_id
        self.db = database
        self.missions = missions
        self.input = input_fn
        self.output = output
        self.db.require_access(principal_id, workspace_id, Permission.MANAGE_WORKSPACE)

    def _write(self, value: object) -> None:
        self.output.write(_safe_terminal(value) + "\n")

    @staticmethod
    def _key() -> str:
        return uuid4().hex

    def list(self, *, include_archived: bool = False) -> tuple[str, ...]:
        missions = self.missions.list(self.principal_id, self.workspace_id,
                                      include_archived=include_archived)
        for mission in missions:
            self._write(f"{mission.id} | {mission.state} | task={mission.task_id} | "
                        f"plan={mission.plan_revision} | steps={mission.steps_used}/{mission.max_steps} | "
                        f"expires={mission.expires_at}")
        return tuple(mission.id for mission in missions)

    def create(self, goal: str, *, max_steps: int = 25, max_requests: int = 25,
               max_retries: int = 0, max_duration_seconds: int = 3600) -> str | None:
        self._write("Create a plan-only mission. Simulation is unavailable until its exact plan is approved.")
        self._write(f"Persisted goal: {goal}")
        expected = f"CREATE MISSION {self.workspace_id}"
        if not secrets.compare_digest(self.input(f"Type {expected}: "), expected):
            self._write("Mission creation cancelled.")
            return None
        mission = self.missions.create(self.principal_id, self.workspace_id, goal,
            idempotency_key=self._key(), max_steps=max_steps, max_requests=max_requests,
            max_retries=max_retries, max_duration_seconds=max_duration_seconds)
        self._write(f"Created mission {mission.id}; deterministic simulation only.")
        return mission.id

    def propose_fake_plan(self, mission_id: str, planner: DeterministicFakePlanner) -> str:
        expected = f"PROPOSE MISSION PLAN {mission_id}"
        if not secrets.compare_digest(self.input(f"Type {expected}: "), expected):
            self._write("Plan proposal cancelled.")
            return "cancelled"
        plan = self.missions.propose_fake_plan(self.principal_id, mission_id, planner,
                                               idempotency_key=self._key())
        self._write(f"Stored plan revision {plan.revision}, digest {plan.plan_sha256}.")
        return plan.plan_sha256

    def authorize(self, mission_id: str, revision: int, plan_sha256: str) -> str:
        expected = f"AUTHORIZE MISSION {mission_id} {plan_sha256}"
        if not secrets.compare_digest(self.input(f"Type {expected}: "), expected):
            self._write("Mission authorization cancelled.")
            return "cancelled"
        mission = self.missions.authorize(self.principal_id, mission_id, revision, plan_sha256,
                                         idempotency_key=self._key())
        self._write(f"Authorized mission {mission.id} revision {revision}; simulation only.")
        return mission.state

    def reject(self, mission_id: str, revision: int, plan_sha256: str) -> str:
        expected = f"REJECT MISSION {mission_id} {plan_sha256}"
        if not secrets.compare_digest(self.input(f"Type {expected}: "), expected):
            self._write("Mission rejection cancelled.")
            return "cancelled"
        mission = self.missions.reject(self.principal_id, mission_id, revision, plan_sha256,
                                      idempotency_key=self._key())
        self._write(f"Rejected mission {mission.id} revision {revision}.")
        return mission.state

    def simulate(self, mission_id: str, worker: DeterministicFakeWorker) -> str:
        expected = f"SIMULATE MISSION {mission_id}"
        if not secrets.compare_digest(self.input(f"Type {expected}: "), expected):
            self._write("Simulation cancelled.")
            return "cancelled"
        mission = self.missions.simulate_next(self.principal_id, mission_id, worker,
                                              idempotency_key=self._key())
        self._write(f"Mission {mission.id}: {mission.state}, steps={mission.steps_used}/{mission.max_steps}.")
        return mission.state

    def pause(self, mission_id: str) -> str:
        expected = f"PAUSE MISSION {mission_id}"
        if not secrets.compare_digest(self.input(f"Type {expected}: "), expected):
            return "unchanged"
        return self.missions.pause(self.principal_id, mission_id, idempotency_key=self._key()).state

    def cancel(self, mission_id: str) -> str:
        expected = f"CANCEL MISSION {mission_id}"
        if not secrets.compare_digest(self.input(f"Type {expected}: "), expected):
            return "unchanged"
        return self.missions.cancel(self.principal_id, mission_id, idempotency_key=self._key()).state

    def resume(self, mission_id: str) -> str:
        expected = f"RESUME MISSION {mission_id}"
        if not secrets.compare_digest(self.input(f"Type {expected}: "), expected):
            return "unchanged"
        return self.missions.resume(self.principal_id, mission_id, idempotency_key=self._key()).state

    def recover(self) -> tuple[str, ...]:
        expected = f"RECOVER MISSIONS {self.workspace_id}"
        if not secrets.compare_digest(self.input(f"Type {expected}: "), expected):
            return ()
        result = self.missions.recover_after_restart(self.principal_id, self.workspace_id)
        self._write(f"Interrupted {len(result)} mission(s); nothing resumed.")
        return result
