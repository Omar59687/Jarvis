# M08-A: Mission Foundation

**Status:** Confirmed implementation scope for M08-A. This was not an approval
for later phases at the time; M08-B has since been separately approved. M08-C
through M08-E remain gated.

## Confirmed requirements

- Reuse `TaskOrchestrationService` and SQLite. A mission is a durable facade
  over one orchestration task, not a second task/worker engine.
- Keep mission authorization separate from task-plan approval and from
  repository capability grants. Both mission authorization and task-plan
  approval must be current before simulation.
- M08-A supports deterministic simulation only. No model planner, external
  service, arbitrary shell command, filesystem write, or external action.
- Persist finite step, request, retry and elapsed-time limits. Token budgets
  are not claimed or enforced because no reliable provider accounting exists.
- Persist bounded checkpoint digests and progress; recover active missions to
  `interrupted` and require explicit reauthorization to resume.
- Proactivity is disabled. No scheduled or background mission execution.
- Retention uses explicit manager archive/purge controls. Automatic deletion is
  disabled. Backups include mission records and remain sensitive.

## Mission lifecycle

`draft -> awaiting_authorization -> authorized -> running -> paused | blocked |
interrupted -> completed | cancelled | expired`

`completed` means deterministic simulation completed, not verified work. Plan
revision changes clear mission authorization. Rejection returns the mission to
`draft`. Cancellation is terminal. Pause/cancel are durable and idempotent.
Restart recovery moves `authorized` or `running` missions to `interrupted`;
nothing resumes automatically.

## Limits and records

- One mission references exactly one task and shares its workspace and owner.
- At most 25 steps, 200 dependencies, 100 active missions per principal per
  workspace, and 30 days maximum lifetime, matching current orchestration
  limits.
- Each mission has a positive maximum simulated-step count, request count,
  retry count, and wall-clock duration. No provider token accounting.
- A budget ledger records bounded reserve/consume/deny entries with idempotency
  keys. Checkpoints store plan revision, task progress digest and sequence, not
  arbitrary tool output.
- Mission authorization binds the plan revision and digest, permitted
  capability set, budget digest and expiry. M08-A permits simulation only and
  rejects all execution/write/network/promotion/deployment capabilities.
- Audit metadata uses fixed event codes, scalar bounded values, and no raw goal,
  plan body, exception text, or model output.

## Acceptance criteria

1. Mission creation binds the authenticated owner, workspace and task and
   rejects cross-workspace or mismatched ownership.
2. Mission lifecycle transitions reject invalid, terminal, stale and expired
   changes; pause, cancel and resume are idempotent.
3. Mission authorization requires an exact current plan digest/revision and
   capability/budget digest. A plan edit or capability expansion invalidates
   authorization.
4. Simulation requires both mission authorization and existing task-plan
   approval, and invokes only the deterministic fake worker.
5. Step, request, retry and time bounds persist across duplicate requests and
   restart. Token enforcement is not represented.
6. Checkpoints are digest-validated against current task progress and cannot be
   edited independently.
7. Startup recovery interrupts active missions; explicit manager resume
   rechecks workspace access, expiry, current plan, authorization and budgets.
8. Cancellation and permission revocation prevent later simulated progress.
9. Audit and budget history are workspace scoped, append only, bounded, and
   do not contain raw sensitive content.
10. Migration rollback, backup/restore, M06/M07 regression and the existing
    Python/Node suites pass on supported environments; platform-specific
    skips are reported.

## Open decisions deferred beyond M08-A

- Model-backed planning, provider choice, and model token accounting.
- Proactive triggers, scheduling, and notification UX.
- Any capability that writes files, runs commands, uses a network, verifies,
  promotes, commits, pushes, merges or deploys.
- Product UI/API integration and production authenticated host selection.

## Implemented evidence

Schema v15 and the M08-A service/operator are merged to `main` via PR #24.
See [M08-A implementation status](M08A_IMPLEMENTATION_STATUS.md) for file and
verification evidence. Requirements above are not evidence by themselves.
