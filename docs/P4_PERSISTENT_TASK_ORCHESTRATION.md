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

## Privacy and remaining limits

Goals and simulated summaries persist in the local application database, with
strict size limits and logical expiration timestamps. They are not encrypted
by this migration. Expiration does not yet purge records; event/approval
immutability and foreign-key retention also mean there is no task-level delete
workflow. Before multi-user operation, define and implement a reviewed
retention, export, and deletion policy. Treat the database and backups as
sensitive.

Task plans do not yet have provider assignments, external planners, queue
workers, leases, or distributed locking. SQLite transactions and unique
idempotency keys protect current local operations, but this is not a
multi-process orchestration service. Cancellation is durable state only; no
child process exists to terminate. Agent-created worktrees, file changes,
subprocesses, network, commits, pushes, pull requests, merges, and deployment
remain unavailable until the independent Windows isolation gate is passed.
