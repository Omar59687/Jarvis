"""Trusted-terminal access to the bounded P4 task-planning workflow.

This is deliberately a Python API for an already authenticated local host,
not an HTTP endpoint or general command runner. Every state-changing action
uses a task-specific confirmation phrase.
"""
from __future__ import annotations

from typing import Callable, TextIO
import unicodedata
from uuid import uuid4

from .orchestration import (
    DeterministicFakePlanner,
    DeterministicFakeWorker,
    OrchestrationError,
    TaskOrchestrationService,
)
from .storage import Database, Permission


def _safe_terminal(value: object) -> str:
    text = str(value)
    return "".join(char if not unicodedata.category(char).startswith(("C", "Zl", "Zp"))
                   else f"\\u{ord(char):04x}" for char in text)


class OrchestrationOperatorWorkflow:
    """Manager-only terminal workflow with injected, deterministic fakes."""

    def __init__(self, principal_id: str, workspace_id: str, database: Database,
                 service: TaskOrchestrationService, *, input_fn: Callable[[str], str] = input,
                 output: TextIO) -> None:
        self.principal_id = principal_id
        self.workspace_id = workspace_id
        self.db = database
        self.service = service
        self.input = input_fn
        self.output = output
        self.db.require_access(principal_id, workspace_id, Permission.MANAGE_WORKSPACE)

    def _write(self, value: object = "") -> None:
        self.output.write(_safe_terminal(value) + "\n")

    @staticmethod
    def _key() -> str:
        return uuid4().hex

    def list_tasks(self, *, include_history: bool = True) -> None:
        for task in self.service.list_tasks(self.principal_id, self.workspace_id,
                                            include_history=include_history):
            self._write(f"{task.id} | {task.state.value} | mode={task.execution_mode} | "
                        f"repo={task.repository_id or 'workspace'} | "
                        f"revision={task.current_plan_revision} | expires={task.expires_at}")

    def create_task(self, goal: str, *, repository_id: str | None = None,
                    requested_capabilities: tuple[str, ...] = ()) -> str | None:
        self._write(f"Create plan-only task in workspace {self.workspace_id}; "
                    f"repository={repository_id or 'none'}; capabilities="
                    f"{', '.join(requested_capabilities) or 'none'}.")
        self._write(f"Goal to persist: {goal}")
        self._write("Approval authorizes the plan only. It does not authorize execution or writes.")
        if self.input("Type CREATE TASK to continue: ") != "CREATE TASK":
            self._write("Task creation cancelled.")
            return None
        task = self.service.create_task(
            self.principal_id, self.workspace_id, goal,
            repository_id=repository_id,
            requested_capabilities=requested_capabilities,
            idempotency_key=self._key(),
        )
        self._write(f"Created {task.id} ({task.state.value}); nothing executed.")
        return task.id

    def propose_fake_plan(self, task_id: str, planner: DeterministicFakePlanner) -> str:
        if type(planner) is not DeterministicFakePlanner:
            raise OrchestrationError("model_planning_disabled")
        if self.input(f"Type PROPOSE PLAN {task_id} to store this fake plan: ") != f"PROPOSE PLAN {task_id}":
            self._write("Plan proposal cancelled.")
            return "cancelled"
        plan = self.service.propose_plan(self.principal_id, task_id, planner,
                                         idempotency_key=self._key())
        self._write(f"Recorded deterministic fake plan revision {plan.revision}: "
                    f"{plan.plan_sha256}. No model was called.")
        return plan.plan_sha256

    def inspect_task(self, task_id: str) -> None:
        task = self.service.get_task(self.principal_id, task_id)
        self._write(f"Task {task.id}: {task.state.value}; mode={task.execution_mode}; "
                    f"workspace={task.workspace_id}; repository={task.repository_id or 'none'}; "
                    f"base={task.repository_commit or 'none'}; "
                    f"plan={task.current_plan_revision}; approved={task.approved_plan_revision}; "
                    f"expires={task.expires_at}")
        self._write(f"Capabilities requested={task.requested_capabilities}; "
                    f"execution={task.capability_manifest.get('execution_enabled')}; "
                    f"writes={task.capability_manifest.get('file_write_enabled')}; "
                    f"processes={task.capability_manifest.get('process_execution_enabled')}; "
                    f"network={task.capability_manifest.get('network_enabled')}")
        blocked = self.service.blocked_capabilities(self.principal_id, task.id)
        self._write(f"Currently blocked capabilities={blocked or 'none'}")
        if task.current_plan_revision:
            for progress in self.service.get_progress(self.principal_id, task.id):
                self._write(f"  {progress.subtask_id}: {progress.state.value} "
                            f"{progress.progress_percent}% | {progress.result_summary or ''}")

    def review_plan(self, task_id: str, revision: int | None = None) -> str:
        plan = self.service.get_plan(self.principal_id, task_id, revision)
        self._write(f"Plan revision {plan.revision} SHA-256 {plan.plan_sha256} ({plan.state}).")
        for item in plan.subtasks:
            self._write(f"  {item.id}: {item.title}")
            self._write(f"    Goal: {item.goal}")
            self._write(f"    Accept: {item.acceptance_condition}")
            self._write(f"    Requires: {', '.join(item.required_capabilities) or 'none'}")
            self._write(f"    Depends on: {', '.join(item.dependencies) or 'none'}")
        decision = self.input("Type APPROVE <revision SHA-256> or REJECT <revision SHA-256>: ")
        pieces = decision.split(" ", 1)
        if len(pieces) != 2 or pieces[0] not in {"APPROVE", "REJECT"}:
            self._write("No decision recorded.")
            return "no_decision"
        if pieces[1] != plan.plan_sha256:
            self._write("Digest mismatch; no decision recorded.")
            return "digest_mismatch"
        method = self.service.approve_plan if pieces[0] == "APPROVE" else self.service.reject_plan
        result = method(self.principal_id, task_id, plan.revision, plan.plan_sha256,
                        idempotency_key=self._key())
        self._write(f"Recorded {result.state.value}. This is plan approval only; no code ran.")
        return result.state.value

    def simulate_next_step(self, task_id: str, worker: DeterministicFakeWorker) -> str:
        if type(worker) is not DeterministicFakeWorker:
            raise OrchestrationError("real_worker_execution_disabled")
        if self.input(f"Type SIMULATE {task_id} to record fake progress: ") != f"SIMULATE {task_id}":
            self._write("Simulation cancelled.")
            return "cancelled"
        task = self.service.simulate_next_step(self.principal_id, task_id, worker,
                                               idempotency_key=self._key())
        self._write(f"{task.state.value}; fake progress only; no agent tools or files used.")
        return task.state.value

    def cancel(self, task_id: str) -> str:
        if self.input(f"Type CANCEL {task_id} to cancel: ") != f"CANCEL {task_id}":
            self._write("Cancellation cancelled.")
            return "unchanged"
        task = self.service.cancel_task(self.principal_id, task_id,
                                        idempotency_key=self._key())
        self._write(f"Task state: {task.state.value}.")
        return task.state.value

    def recover(self) -> tuple[str, ...]:
        if self.input("Type RECOVER TASKS to mark active work interrupted: ") != "RECOVER TASKS":
            self._write("Recovery check cancelled.")
            return ()
        task_ids = self.service.recover_after_restart(
            self.principal_id, self.workspace_id, idempotency_key_prefix=self._key())
        self._write(f"Marked {len(task_ids)} task(s) interrupted. Nothing resumed.")
        return task_ids

    def inspect_events(self, task_id: str) -> None:
        for event in self.service.events(self.principal_id, task_id):
            self._write(f"{event['created_at']} {event['event_type']} "
                        f"actor={event['actor_id']} revision={event['plan_revision']} "
                        f"sha256={event['snapshot_sha256'] or ''} {event['metadata']}")
