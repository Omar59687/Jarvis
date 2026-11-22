# P4 persistent task orchestration checkpoint

## Scope

Schema migration 9 adds a durable, workspace-scoped task-planning layer beside
the existing schema-v8 `coding_tasks`. It does not reinterpret, migrate, or
inherit approval from existing coding-task proposals. A P4 coding-task approval
continues to mean only that the operator reviewed its exact displayed diff.

This checkpoint is **plan-only plus deterministic simulation**. There is no
model planner, remote service, agent tool, worktree manager, file writer,
subprocess, network client, automatic commit, or deployment path in the new
orchestration package. A task state of `simulation_completed` is explicitly
not a completed coding task. The reserved `completed` and `failed` states are
not reachable through this service.

## Data and approval model

Each task is bound to a workspace and authenticated principal supplied by the
trusted host, and may be pinned to one currently indexed repository commit.
The bounded human goal and capability manifest are stored in SQLite; both have
SHA-256 integrity checks. Plans are immutable numbered revisions with at most
25 bounded subtasks and 200 dependency edges. Plan content is canonicalized
and hashed. Approval and rejection receipts bind the actor, exact plan digest,
revision, action, and one-use request key. Any new plan revision clears the
current approval pointer; it must receive a new approval.

Workspace READ/WRITE is required for task access and mutation. Plan decisions
and explicit recovery/resume require `MANAGE_WORKSPACE`. Repository-required
capabilities are checked separately through the existing repository grant
policy. Revoked grants block simulation and are shown by
`blocked_capabilities`. The task grant manifest explicitly keeps filesystem
writes, process execution, network, and execution approval disabled.

Lifecycle events and decision receipts are append-only in SQLite. Repeated
operation keys return the existing result when the operation and target match;
reusing a key for a different event or task fails. The fake worker's progress
is stored as bounded percentages and summaries. A restart marks queued or
simulating tasks interrupted; it never resumes them automatically. A manager
must explicitly resume a previously approved simulation after checking current
authorization again.

## Trusted-terminal integration

`jarvis_core.orchestration_operator.OrchestrationOperatorWorkflow` is a
manager-only helper for a trusted local application bootstrap. The bootstrap
must inject the authenticated principal; do not take actor identity from a
browser or an unauthenticated HTTP request. It provides task creation, list and
inspection, fake plan proposal, exact-digest plan review, simulated progress,
explicit cancellation, restart recovery, and event history. Mutating terminal
operations require exact phrases such as `CREATE TASK`, `APPROVE <digest>`,
`SIMULATE <task-id>`, `CANCEL <task-id>`, and `RECOVER TASKS`. The workflow is
not wired to a standalone CLI or web route; the existing trusted host is the
authentication boundary and remains responsible for presenting the helper.

## Offline acceptance

Run:

```text
python -m unittest tests.test_orchestration -v
```

The synthetic scenario creates a task, proposes and approves a dependency
plan, simulates partial progress, restarts through a fresh service/backup,
marks the running task interrupted, explicitly resumes, completes only the
fake simulation, and inspects append-only events. Additional tests cover exact
revision approval, changed-plan reapproval, cycles, dependency ordering,
idempotency collisions, workspace isolation, revoked grants, cancellation,
corrupt evidence, and terminal confirmations. These tests do not establish
Windows containment or real-agent/model behavior.

## Retention, archiving, and safe deletion (schema migration 10)

Schema migration 10 adds an additive, workspace-scoped task-data lifecycle
layer without duplicating task payloads:

- **Safe default policy:** Workspaces without an explicit policy default to no
  retention deadlines (`None` for completed, cancelled, failed, and interrupted
  states) and `auto_delete_enabled = False`. Even when retention periods are
  configured, automatic deletion is permanently disabled (`CHECK(auto_delete_enabled = 0)`).
  Retention periods only govern eligibility for operator-initiated previews and
  confirmed purges.
- **Expired policies fail closed:** A retention policy with an `expires_at`
  timestamp in the past causes policy-driven `preview_purge` and `purge_tasks`
  calls to fail closed (`retention_policy_expired`) until a workspace manager
  renews or updates the policy.
- **Hide vs. archive vs. delete:**
  - *Hiding from active views:* `list_tasks(..., include_history=False)` hides
    terminal history; `list_tasks(..., include_archived=False)` (default) hides
    archived tasks from standard listings without altering records.
  - *Archiving (`archive_task` / `restore_archived_task`):* Sets `archived_at`
    and `archived_by` in place on `orchestration_tasks` without copying goals,
    plans, subtasks, approvals, or events. Archived tasks remain readable via
    `get_task`, `get_plan`, `get_progress`, `events`, and bounded
    `list_task_history`, while rejecting state mutations until restored. Active
    tasks and pending human approvals cannot be archived.
  - *Operator-controlled purge (`preview_purge` -> `purge_tasks`):* Requires
    authenticated `READ`, `WRITE`, and `MANAGE_WORKSPACE` authority, a bounded
    preview (at most 50 tasks per batch), and exact confirmation
    `PURGE <workspace_id> <preview_sha256>`. Active tasks (`draft`,
    `awaiting_approval`, `queued`, `simulating`, `blocked`) and tasks with
    pending plan approvals cannot be purged.
- **Atomic deletion and stale-preview protection:** `purge_tasks` runs inside a
  `BEGIN IMMEDIATE` SQLite transaction, re-validates manager permissions and
  policy state, recomputes the canonical `preview_sha256` from live task state,
  revision pointers, goal/summary digests, dependent row counts, and latest
  event ID, and aborts (`purge_preview_stale`) if anything changed since the
  preview. Authorized deletion records an immutable `orchestration_purge_operations`
  receipt, inserts minimal non-sensitive `orchestration_purge_tombstones`
  (workspace ID, task ID, state, archive timestamp, `goal_sha256`, dependent
  row counts including purged approval/event counts, actor, and timestamp — no
  plaintext goals or summaries), atomically cascades deletion across
  `orchestration_tasks`, `orchestration_plans`, `orchestration_subtasks`,
  `orchestration_dependencies`, `orchestration_approvals`, and
  `orchestration_task_events`, and writes an `audit_events` entry. Unauthorized
  direct `DELETE` statements on `orchestration_approvals` and
  `orchestration_task_events` remain blocked by SQLite triggers.
- **Idempotency and concurrency:** Repeating `purge_tasks` with the same
  `(workspace_id, actor_id, idempotency_key)` and matching `preview_sha256` and
  `task_ids` returns the recorded `PurgeReceipt` with `already_purged=True`
  (still enforcing current workspace manager authorization). Reusing an
  idempotency key with different parameters raises `idempotency_key_conflict`.
  Concurrent task updates or purges serialize via SQLite `BEGIN IMMEDIATE`,
  either failing closed with `purge_preview_stale` / `task_unavailable` or
  rolling back cleanly on SQLite failure.

## Privacy and remaining limits

Goals, plan subtasks, and simulated summaries persist in plaintext in the local
SQLite database, with strict size bounds and logical expiration timestamps.
This migration intentionally does **not** add application-layer encryption
because no reviewed key-management subsystem exists in Jarvis yet; treat the
database, WAL files, and backups as sensitive.

Purging removes rows from the active SQLite database only. It does **not**
retroactively erase records from historical database backups or guarantee
physical block/page erasure on SSDs or copy-on-write filesystems. Restoring an
older backup restores whatever tasks, policies, and tombstones existed in that
backup snapshot; `restore_from_backup` verifies schema v10 tables and
immutability triggers before replacing the active database. When approval
receipts are purged along with a task, their exact count remains preserved on
the immutable `orchestration_purge_tombstones` row and `audit_events` entry.

Deletion is strictly a trusted-operator action (via `TaskOrchestrationService`
or `OrchestrationOperatorWorkflow`), never an AI-generated tool call or HTTP
endpoint. Task plans do not yet have provider assignments, external planners,
queue workers, leases, or distributed locking. SQLite transactions and unique
idempotency keys protect current local operations, but this is not a
multi-process orchestration service. Cancellation is durable state only; no
child process exists to terminate. P4 Windows isolation remains `NOT_READY`;
agent-created worktrees, file writes, subprocesses, network access, automatic
Git operations, and deployment remain disabled.
