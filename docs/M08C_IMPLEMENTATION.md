# M08-C — Jarvis Proactivity Implementation and Acceptance

**Status:** implemented on `feature/m08-c-proactivity`. M08-B is already
merged to `main` at `debea200e0c9e205ff54e00ab290a2098b72d9f3`. The original
M08-C baseline was `debea200e0c9e205ff54e00ab290a2098b72d9f3`; the accepted
C01.1 checkpoint is preserved in the branch history. M08-D and M08-E have not
started.

## Implemented behavior

- Proactivity preferences are persistent per workspace/principal and off until
  explicit opt-in. The settings include generation, 15-minute to 24-hour scan
  interval, 24-hour default cooldown, and timestamps.
- A deterministic scanner checks at most 100 task rows per scope and emits at
  most 25 new suggestions per scope per tick. It processes one due scope per
  default tick and can be configured in trusted service construction up to 10.
  An unfinished mission is eligible after 24 hours. Blocked, paused and
  interrupted mission/task suggestions come from actual persisted states.
- Suggestions live for 30 days by default; at most 100 active or snoozed
  suggestions are retained per scope. Deduplication includes workspace,
  principal, type/reason, source identities and the source revision digest.
  Stale evidence retracts the suggestion before display or action.
- Accept, dismiss and bounded snooze are permission checked. Accept only
  acknowledges. Lifecycle records and audit entries commit with the state
  transition.
- Scheduler claims are atomic SQLite writes with generation-bound tokens and
  leases capped at 300 seconds. Expired leases can be reclaimed after restart;
  stale workers cannot commit.
- `service_actions()` runs the bounded synchronous tick only while the
  explicitly started local backend is serving. It creates no scheduler thread
  and calls no model.
- The existing dashboard has an English/Arabic inbox, explicit enable/stop and
  bounded settings, plus acknowledge/dismiss/snooze actions. Its requests use
  the existing paired session, selected workspace, same-origin CSRF and
  origin protections. Pending loads/actions are fenced and cleared on logout
  and workspace changes.

## Database, deletion and privacy

Schema v17 added the three C01.1 tables. Schema v18 adds the bounded reason
code and durable per-scope scheduler claim/lease table. Both migrations use
the existing transactional migration runner. Fresh initialization, upgrade
from v16 and v17, rollback on failed v18 DDL, backup validation and restore
are covered by targeted tests.

SQLite enforces preference bounds/defaults, scope uniqueness, fixed types,
states, reasons and event vocabulary, SHA-256 digest shape, timestamp/lifetime
bounds, JSON-object metadata size, and composite task/mission source
references. Those composite keys prevent cross-workspace/principal evidence
references. Service code enforces authorization, opt-in, default lifetime,
cooldown, active/new-suggestion caps, scan/scope caps, generation fencing,
current source state and revision, and safe metadata construction.

Suggestion rows do not contain raw mission goals, task goals, summaries, model
responses or free-form source content. The UI receives fixed reason labels,
opaque source identities and timestamps. Audit metadata contains only fixed
codes/counts/digests, never source content. Existing READ/WRITE permission
checks are repeated for service operations. Removing READ or WRITE durably
disables the preference, increments generation and invalidates a claim in the
same transaction; restoring permission does not reactivate it.

Events reject update and ordinary delete. The existing manager-confirmed task
purge first writes the immutable purge tombstone, which authorizes cascading
deletion of task/mission suggestion rows and their event rows. This preserves
the existing privacy purge contract. Backups made before purge may still
contain the old rows; no secure erasure of historical backups is claimed.

## Security and capability limits

The implementation adds no dependency, external API, cloud service, model
inference, scheduler thread, Windows autostart, notification delivery, or new
execution grant. It does not create, authorize, resume or execute a mission or
task. Existing simulation-only boundaries remain in force. Proactive
notification delivery is deferred. SQLite protection is an application data
integrity boundary and is not protection against a local user who can directly
edit the database file.

## Verification evidence

Environment: Conda `jarvis-ai`, Python 3.12.16.

- `conda run -n jarvis-ai python -m unittest discover -s tests -v` — 462 run,
  452 passed, 10 skipped, 0 failures, 0 errors. Skips were platform or local
  filesystem capability checks, including Windows symlink creation requiring
  privileges unavailable in this environment.
- `conda run -n jarvis-ai python -m unittest tests.test_proactivity tests.test_proactivity_http tests.test_proactivity_storage tests.test_missions tests.test_mission_planning tests.test_orchestration -v` — 84 passed, 0 skipped, 0 failures, 0 errors.
- `node --test tests/*.test.mjs` — 28 passed, 0 skipped, 0 failures.
- `node --check prototypes/m03_b1/app.js` — passed.
- `conda run -n jarvis-ai python -m py_compile jarvis_core/proactivity.py jarvis_core/storage.py prototypes/m03_b1/service.py` — passed.
- `git diff --check` — passed.

The targeted suites cover migration/rollback/data preservation, preference and
scope constraints, source foreign keys, purge, backup/restore, permission
revocation/restoration, generation fencing, concurrency, leases/restart,
lifecycle/evidence, bounded resources, authenticated API/CSRF/origin
protections, acknowledgment-only behavior, and frontend workspace/logout
fencing.
