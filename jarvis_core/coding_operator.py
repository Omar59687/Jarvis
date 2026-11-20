"""Small trusted-terminal workflow for supervised coding task records.

This module is deliberately not exposed through the browser or service API.
It needs already-constructed services from a trusted local bootstrapper.
"""
from __future__ import annotations

from typing import Callable, TextIO
import unicodedata

from .coding_agents import (
    AgentChange,
    CodingCapability,
    CodingCapabilityPolicy,
    CodingTaskService,
    FakeAgentAdapter,
    ReadOnlyWorktreeManager,
)
from .developer import DeveloperRepositoryService


def _safe_terminal(value: object) -> str:
    text = str(value)
    return "".join(char if char in "\n\t" or not unicodedata.category(char).startswith("C")
                   else f"\\u{ord(char):04x}" for char in text)


class CodingOperatorWorkflow:
    """Interactive helper; every mutation requires an exact terminal phrase."""

    def __init__(self, principal_id: str, workspace_id: str,
                 repositories: DeveloperRepositoryService,
                 policy: CodingCapabilityPolicy, tasks: CodingTaskService,
                 *, input_fn: Callable[[str], str] = input,
                 output: TextIO) -> None:
        self.principal_id = principal_id
        self.workspace_id = workspace_id
        self.repositories = repositories
        self.policy = policy
        self.tasks = tasks
        self.input = input_fn
        self.output = output

    def _write(self, value: object = "") -> None:
        self.output.write(_safe_terminal(value) + "\n")

    def inspect_repositories(self) -> None:
        for repo in self.repositories.list_repositories(self.principal_id, self.workspace_id):
            grants = self.policy.list_grants(
                self.principal_id, self.workspace_id, repo.id)
            self._write(f"{repo.id} | {repo.name} | {repo.index_status} | {repo.head_commit or 'unknown'}")
            for grant in grants:
                self._write(f"  grant {grant.capability.value}: {grant.principal_id}; "
                            f"expires={grant.expires_at or 'never'}; revoked={bool(grant.revoked_at)}")

    def grant(self, repository_id: str, principal_id: str,
              capability: CodingCapability | str) -> str | None:
        capability = CodingCapability(capability)
        self._write(f"Grant {capability.value} to {principal_id} for repository {repository_id}?")
        if self.input("Type GRANT to continue: ") != "GRANT":
            self._write("Grant cancelled.")
            return None
        grant = self.policy.grant(self.principal_id, self.workspace_id,
                                  repository_id, principal_id, capability, confirmed=True)
        self._write(f"Granted {grant.id}.")
        return grant.id

    def revoke(self, grant_id: str) -> bool:
        grants = self.policy.list_grants(self.principal_id, self.workspace_id)
        grant = next((item for item in grants if item.id == grant_id), None)
        if grant is None or grant.revoked_at is not None:
            self._write("Grant is unavailable or already revoked.")
            return False
        self._write(f"Revoke {grant.capability.value} for {grant.principal_id} "
                    f"on repository {grant.repository_id}?")
        if self.input(f"Type REVOKE {grant.id} to continue: ") != f"REVOKE {grant.id}":
            self._write("Revocation cancelled.")
            return False
        changed = self.policy.revoke(self.principal_id, grant.id, confirmed=True)
        self._write("Grant revoked." if changed else "Grant was already revoked.")
        return changed

    def list_tasks(self) -> None:
        for task in self.tasks.list_tasks(self.principal_id, self.workspace_id):
            self._write(f"{task.id} | {task.status.value} | {task.repository_id} | "
                        f"{task.base_commit} | expires={task.expires_at}")

    def inspect_task(self, task_id: str) -> None:
        task = self.tasks.get_task(self.principal_id, task_id)
        self._write(f"Task {task.id}: {task.status.value}; repository={task.repository_id}; "
                    f"base={task.base_commit}; request_sha256={task.request_sha256}; "
                    f"diff_sha256={task.diff_sha256 or 'none'}; "
                    f"write={task.manifest.get('task_write_enabled')}; "
                    f"execution={task.manifest.get('process_execution_enabled')}; "
                    f"network={task.manifest.get('network_enabled')}")

    def propose_fake(self, task_id: str, request_text: str,
                     changes: tuple[AgentChange, ...]) -> str:
        """Exercise proposal/review using only the deterministic, non-executing fake."""
        if (not isinstance(changes, tuple) or len(changes) > 50
                or any(type(item) is not AgentChange for item in changes)):
            raise ValueError("fake proposal exceeds the bounded terminal contract")
        adapter = FakeAgentAdapter(changes)
        task = self.tasks.propose_with_adapter(
            self.principal_id, task_id, request_text, adapter)
        self._write(f"Recorded bounded fake proposal for {task.id}; no files were changed.")
        return task.status.value

    def create_task(self, repository_id: str, base_commit: str,
                    requested_capabilities: list[CodingCapability | str] | None = None) -> str | None:
        requested = requested_capabilities or [
            CodingCapability.READ_REPOSITORY_METADATA,
            CodingCapability.READ_INDEXED_SOURCE,
        ]
        self._write(f"Repository: {repository_id}; base commit: {base_commit}")
        self._write("Requested capabilities: " + ", ".join(
            CodingCapability(item).value for item in requested))
        self._write("Granted capabilities will be rechecked by the server-side policy.")
        request = self.input("Task request (stored as a digest only): ")
        self._write("The request text is not persisted; only its SHA-256 is recorded.")
        if self.input("Type CREATE TASK to continue: ") != "CREATE TASK":
            self._write("Task creation cancelled.")
            return None
        task = self.tasks.create_task(
            self.principal_id, self.workspace_id, repository_id, base_commit, request,
            requested_capabilities=requested_capabilities)
        self._write(f"Created task {task.id} ({task.status.value}).")
        return task.id

    def review(self, task_id: str) -> str:
        proposal = self.tasks.view_proposal(self.principal_id, task_id)
        self._write(f"Task {task_id}; repository {proposal['repository_id']}; "
                    f"base {proposal['base_commit']}; diff SHA-256 {proposal['diff_sha256']}")
        self._write(proposal["diff"])
        decision = self.input("Type APPROVE <full SHA-256> or REJECT <full SHA-256>: ")
        pieces = decision.split(" ", 1)
        if len(pieces) != 2 or pieces[0] not in {"APPROVE", "REJECT"}:
            self._write("No decision recorded.")
            return "no_decision"
        if pieces[1] != proposal["diff_sha256"]:
            self._write("Digest did not match; no decision recorded.")
            return "digest_mismatch"
        action = "approve_proposal" if pieces[0] == "APPROVE" else "reject_proposal"
        task = self.tasks.decide_proposal(
            self.principal_id, task_id, action, pieces[1], confirmed=True)
        self._write(f"Recorded {task.status.value}; no files were changed.")
        return task.status.value

    def inspect_audit(self, task_id: str) -> None:
        for event in self.tasks.audit_events(self.principal_id, task_id):
            self._write(f"{event['created_at']} {event['event_type']} "
                        f"{event['snapshot_sha256'] or ''} {event['metadata']}")

    def cancel(self, task_id: str) -> bool:
        changed = self.tasks.cancel_task(self.principal_id, task_id)
        self._write("Task cancelled." if changed else "Task was not cancellable.")
        return changed

    def recover(self) -> list[str]:
        interrupted = self.tasks.recover_after_restart(self.principal_id, self.workspace_id)
        self._write(f"Marked {len(interrupted)} interrupted task(s); nothing was resumed.")
        return interrupted

    def inspect_worktree_plan(self, task_id: str) -> None:
        plan = ReadOnlyWorktreeManager(self.tasks).plan(self.principal_id, task_id)
        self._write(f"Worktree plan: {plan.state}; dirty files={plan.dirty_file_count}; "
                    f"write available={plan.write_available}. {plan.detail}")
