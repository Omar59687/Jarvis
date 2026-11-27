# M08-A implementation status

**Status:** Implemented on `feature/m08-a-mission-foundation`; awaiting review.
This report records branch evidence and does not approve M08-B.

## Implemented

- `MissionService` is a manager-controlled facade over `TaskOrchestrationService`.
- Schema v15 adds workspace/principal/task-bound missions, immutable mission
  authorization receipts, append-only event and budget ledgers, and immutable
  bounded progress checkpoints. Existing task rows remain the execution unit.
- Mission authorization binds exact plan and budget digests and permits only
  the `simulate` capability. Existing task-plan approval remains a separate
  receipt; both are checked before simulation.
- Deterministic fake planner and worker are required by exact type. Missions
  have persistent step, request, retry and elapsed-time limits. Token limits
  are not claimed because provider token accounting does not exist.
- Pause, cancel, and explicit resume are manager gated. Recovery marks active
  missions interrupted; it never resumes work.
- Checkpoint JSON is bounded and digest checked; the latest checkpoint is
  compared to current task progress before recovery/resume.
- Mission archive and restore use existing task archive controls. Explicit
  task purge cascades mission records under existing manager confirmation,
  preview digest, and tombstone flow. Automatic deletion is disabled.
- Existing SQLite backup/restore validates schema v15 tables and protections.
  Backups include goals, plans, mission history, and checkpoints and are
  sensitive. Purge does not erase historical backups or promise physical media
  erasure.
- `MissionOperatorWorkflow` exposes confirmation-gated operations for an
  already authenticated trusted host; no HTTP route or product UI is added.

## Security boundaries

- No model, paid service, dependency, shell command, process, filesystem write,
  network, or external action was introduced.
- M06 repository grants remain independent. Mission capability expansion is
  rejected; execution, verification, promotion, deployment, Git write, and
  repository mutation remain unavailable.
- Workspace access is rechecked for reads and manager authority is rechecked
  for mutations. Mission records bind to exactly one workspace, principal, and
  existing task.
- Audit metadata excludes goal/plan content, summaries, and raw exceptions.
  Checkpoints retain only progress state and hashes of simulation summaries.

## Acceptance coverage

`tests/test_missions.py` covers ownership and denial, bounds, exact authorization,
stale digest, duplicate request handling, budget exhaustion, fake-only
simulation, checkpoint checks, pause/resume/cancel/recovery, capability
expansion denial, audit privacy, append-only receipts, backup/restore, and
trusted-terminal confirmation. Existing orchestration/coding-agent and M07
tests are required regressions.

## Remaining limitations

- Mission service integration is a trusted Python API; there is no production
  host/UI wiring.
- The facade persists mission and task creation in separate transactions. A
  process failure between them can leave an unlinked plan-only task; it cannot
  authorize or execute anything. Future cleanup can be operator driven.
- Budget request/step reservations are consumed before simulation to fail
  closed. A crash can charge a step without producing a checkpoint. Recovery
  detects missing/stale progress checkpoints and refuses resume.
- The deterministic worker is simulation only; simulation completion is not
  verified task completion.
- No Windows isolation, real execution, model token accounting, proactive
  trigger, notification, or automatic resume is implemented.

## Verification results

- `python -m unittest tests.test_missions -v` — 11 passed.
- Focused M08-A/orchestration/M06/M07 regression run — 85 passed, 1 skipped (before the final v14-upgrade test was added; the final full run below includes all of these suites).
- `python -m unittest discover -s tests -v` — 418 passed, 10 skipped. Skips include Windows symlink creation blocked by runner privilege and Windows-only containment evidence unavailable on this runner.
- `node --test tests/*.test.mjs` — 25 passed, 0 failed.
- `python -m py_compile jarvis_core/missions.py jarvis_core/mission_operator.py jarvis_core/storage.py` — passed.
- `git diff --check` — passed.

The 10 skips are not counted as verified behavior. The full suite passed on the
current Windows Python environment; Windows OS isolation remains unverified.
