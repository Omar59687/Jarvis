"""Trusted-terminal access to the bounded P4 task-planning workflow.

This is deliberately a Python API for an already authenticated local host,
not an HTTP endpoint or general command runner. Every state-changing action
uses a task-specific confirmation phrase.
"""
from __future__ import annotations

from typing import Callable, TextIO
import secrets
import unicodedata
from uuid import uuid4

from .orchestration import (
    DeterministicFakePlanner,
    DeterministicFakeWorker,
    MAX_PURGE_BATCH,
    OrchestrationError,
    PurgeReceipt,
    PurgeTombstone,
    TaskOrchestrationService,
    TaskRetentionPolicy,
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

    def list_tasks(self, *, include_history: bool = True, include_archived: bool = False) -> None:
        for task in self.service.list_tasks(self.principal_id, self.workspace_id,
                                            include_history=include_history,
                                            include_archived=include_archived):
            archived = f" | archived={task.archived_at}" if task.archived_at else ""
            self._write(f"{task.id} | {task.state.value} | mode={task.execution_mode} | "
                        f"repo={task.repository_id or 'workspace'} | "
                        f"revision={task.current_plan_revision} | expires={task.expires_at}{archived}")

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
                    f"archived={task.archived_at or 'no'}; expires={task.expires_at}")
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

    def inspect_retention_policy(self, *, as_of: str | None = None) -> TaskRetentionPolicy:
        policy = self.service.get_retention_policy(self.principal_id, self.workspace_id, as_of=as_of)
        self._write(
            f"Retention policy for {policy.workspace_id}: "
            f"completed_days={policy.completed_retention_days}, "
            f"cancelled_days={policy.cancelled_retention_days}, "
            f"failed_days={policy.failed_retention_days}, "
            f"interrupted_days={policy.interrupted_retention_days}, "
            f"auto_delete={policy.auto_delete_enabled}, "
            f"expires_at={policy.expires_at or 'none'}, "
            f"expired={policy.is_expired}"
        )
        return policy

    def configure_retention_policy(self, *,
                                   completed_retention_days: int | None = None,
                                   cancelled_retention_days: int | None = None,
                                   failed_retention_days: int | None = None,
                                   interrupted_retention_days: int | None = None,
                                   policy_lifetime_days: int | None = None) -> TaskRetentionPolicy | None:
        self._write(
            f"Configure retention policy for {self.workspace_id}: "
            f"completed={completed_retention_days}, cancelled={cancelled_retention_days}, "
            f"failed={failed_retention_days}, interrupted={interrupted_retention_days}, "
            f"policy_lifetime_days={policy_lifetime_days}."
        )
        self._write("Automatic deletion remains disabled; deletion still requires explicit operator confirmation.")
        expected = f"SET RETENTION {self.workspace_id}"
        if not secrets.compare_digest(self.input(f"Type {expected} to continue: "), expected):
            self._write("Retention policy update cancelled.")
            return None
        policy = self.service.set_retention_policy(
            self.principal_id, self.workspace_id,
            completed_retention_days=completed_retention_days,
            cancelled_retention_days=cancelled_retention_days,
            failed_retention_days=failed_retention_days,
            interrupted_retention_days=interrupted_retention_days,
            policy_lifetime_days=policy_lifetime_days,
        )
        self._write(f"Updated retention policy for {policy.workspace_id}; auto_delete=False.")
        return policy

    def archive_task(self, task_id: str) -> str:
        expected = f"ARCHIVE {task_id}"
        if not secrets.compare_digest(self.input(f"Type {expected} to archive: "), expected):
            self._write("Archive cancelled.")
            return "unchanged"
        task = self.service.archive_task(self.principal_id, task_id, idempotency_key=self._key())
        self._write(f"Archived {task.id} at {task.archived_at}; task remains intact and restorable.")
        return "archived"

    def restore_archived_task(self, task_id: str) -> str:
        expected = f"RESTORE {task_id}"
        if not secrets.compare_digest(self.input(f"Type {expected} to restore: "), expected):
            self._write("Restore cancelled.")
            return "unchanged"
        task = self.service.restore_archived_task(self.principal_id, task_id, idempotency_key=self._key())
        self._write(f"Restored {task.id} ({task.state.value}).")
        return "restored"

    def purge_tasks(self, *,
                    task_ids: tuple[str, ...] | None = None,
                    use_retention_policy: bool = False,
                    archived_only: bool = False,
                    as_of: str | None = None,
                    limit: int = MAX_PURGE_BATCH) -> PurgeReceipt | None:
        preview = self.service.preview_purge(
            self.principal_id, self.workspace_id,
            task_ids=task_ids,
            use_retention_policy=use_retention_policy,
            archived_only=archived_only,
            as_of=as_of,
            limit=limit,
        )
        if preview.task_count == 0 or preview.confirmation_phrase is None:
            self._write("No eligible tasks to purge.")
            return None
        self._write(
            f"Purge preview for {preview.workspace_id}: tasks={preview.task_count}, "
            f"plans={preview.plan_count}, subtasks={preview.subtask_count}, "
            f"dependencies={preview.dependency_count}, approvals={preview.approval_count}, "
            f"events={preview.event_count}, truncated={preview.truncated}, "
            f"protected_skipped={preview.protected_skipped_count}"
        )
        for item in preview.items:
            self._write(
                f"  {item.task_id} | {item.state.value} | archived={item.archived_at or 'no'} | "
                f"updated={item.updated_at} | goal_sha256={item.goal_sha256[:12]}... | "
                f"plans={item.plan_count} approvals={item.approval_count} events={item.event_count}"
            )
        self._write(
            "Warning: purge deletes these rows from the active SQLite database and records "
            "non-sensitive audit tombstones. It does not erase historical backups or guarantee SSD physical erasure."
        )
        typed = self.input(f"Type {preview.confirmation_phrase} to purge: ")
        if not secrets.compare_digest(typed, preview.confirmation_phrase):
            self._write("Purge cancelled; no task records deleted.")
            return None
        receipt = self.service.purge_tasks(
            self.principal_id, self.workspace_id,
            task_ids=preview.task_ids,
            expected_preview_sha256=preview.preview_sha256,
            confirmation=typed,
            idempotency_key=self._key(),
            use_retention_policy=use_retention_policy,
            archived_only=archived_only,
            as_of=as_of,
        )
        self._write(
            f"Purged {receipt.task_count} task(s) (purge_id={receipt.purge_id}, "
            f"approvals={receipt.approval_count}, events={receipt.event_count})."
        )
        return receipt

    def inspect_purge_tombstones(self, *, limit: int = 50) -> tuple[PurgeTombstone, ...]:
        tombstones = self.service.list_purge_tombstones(
            self.principal_id, self.workspace_id, limit=limit
        )
        for item in tombstones:
            self._write(
                f"{item.purged_at} | task={item.task_id} | state={item.task_state.value} | "
                f"purge_id={item.purge_id} | "
                f"approvals={item.approval_count} events={item.event_count} by={item.purged_by}"
            )
        return tombstones
