# M08-D — Complete Reliability and Recovery

**Status:** Implemented on `feature/m08-d-reliability`, based on main commit
`eca012ace5c1247d682c1821cef1598a5f8d5578`. M08-C was merged through PR #26
at that commit. SQLite remains schema v18; no migration or dependency was
needed. M08-E has not started.

## Confirmed defects addressed

- Restart recovery existed only as an explicit operator command; backend startup
  did not quarantine stale active missions or tasks.
- Mission restart recovery changed only the mission row, potentially leaving
  its queued/simulating task active.
- Mission simulation persisted a budget reservation, task progress, checkpoint,
  ledger consumption and mission event in separate transactions. A crash or
  persistence error between task and mission commits could leave inconsistent
  progress.
- Different concurrent mission-step keys could reserve concurrently. The
  budget ledger had no single-operation-in-progress fence for a mission.
- Repository capabilities were checked before the deterministic worker call,
  but not again in the task progress transaction.
- Expired missions were rejected by authorization checks but could remain in an
  active state and consume active-mission capacity.

## Implemented recovery and transaction behavior

- Before the local backend serves, it finds only workspaces where its configured
  principal currently has READ, WRITE and MANAGE_WORKSPACE and performs
  transactionally consistent restart recovery there. Scopes it cannot manage
  are left untouched for an authorized manager.
- Recovery moves queued/simulating tasks to `interrupted` and authorized/running
  missions to `interrupted` in the same SQLite transaction. It appends existing
  bounded task and mission events and sets `auto_resume=false`. Nothing resumes.
- Overdue nonterminal missions transition to the existing `expired` state.
  Their active tasks are interrupted. The existing `mission_interrupted` event
  vocabulary is retained; fixed metadata marks `reason=deadline_expired`.
- Unresolved mission budget reservations are closed with an append-only `failed`
  ledger row during recovery. Their already reserved request/step budget stays
  consumed, and the operation key cannot be replayed.
- Only one unresolved `reserved` simulation operation may exist per mission.
  A concurrent caller receives `mission_operation_in_progress`. A stale worker
  cannot commit after recovery changes its task or mission state.
- Startup recovery also changes every workspace `in_progress` planning
  reservation to `interrupted` and clears its owner token and lease in the same
  transaction. A provider call already in flight can finish locally, but its
  fenced owner check rejects persistence; replaying that idempotency key does
  not call the provider again.
- Mission task progress now accepts a private transaction hook. Mission
  checkpoint, state, ledger consumption and event writes commit in the same
  transaction as task progress. Injected failure rolls back task progress, then
  the existing state vocabulary quarantines both records as blocked when
  current permissions still authorize that write.
- Step, request, retry and elapsed-time limits are checked before simulation
  and again when progress is committed. Repository capability revocation is
  rechecked under the SQLite write lock after worker return; revoked capability
  prevents progress and blocks the task.
- Mission creation capacity ignores overdue missions. Existing idempotency,
  provider planning reservation leases, proactivity scheduler leases, backup
  compatibility and purge behavior remain in place.

No recovery-specific dashboard was added: existing task status views show
interrupted/blocked state, and the authenticated terminal operator already
provides explicit recover/resume controls. Existing session/workspace/origin/
CSRF policies are unchanged.

## Security and diagnostics

Startup recovery rechecks current workspace permissions in each transaction.
It performs state quarantine only; mission authorization, task-plan approval
and explicit resume remain necessary before deterministic simulation can
continue. No model is invoked during restart recovery. No real worker, shell,
filesystem write, network action, deployment, promotion, autostart or new
execution capability is introduced.

Persisted state uses existing fixed states and event metadata. Recovery events
store previous state and fixed reason codes only. Provider errors and raw
exception bodies are not added to durable records. Existing planning provenance
distinguishes configured provider timeout, provider failure, and deterministic
fallback without storing model responses. The provider remains responsible for
honoring its declared timeout; the application does not claim it can terminate
an arbitrary in-process provider call. Exactly-once external inference is not
claimed.

Recovery is per workspace transaction. If a later workspace recovery fails,
the backend does not begin serving; already committed workspaces remain
recovered, and a later startup can safely repeat recovery for remaining scopes.
The application assumes one actively serving backend per database. The
existing service does not provide a cross-process singleton lease for multiple
custom-port backend instances.

## Verification evidence

Controlled failure injection uses temporary SQLite databases only. It tests
atomic rollback of task/mission recovery, rollback of task progress and
checkpoint persistence, concurrent mission-step rejection, recovery racing a
live worker, startup recovery without resume, repository capability revocation
during worker execution, and overdue mission expiry. Existing mission planning
tests cover timeout fallback and reservation restart recovery; existing
proactivity tests cover scheduler lease recovery.

Acceptance commands and results:

- `conda run -n jarvis-ai python -m unittest discover -s tests -v` — **470
  tests run, 460 passed, 10 skipped, 0 failures, 0 errors** (406.171 seconds).
  The Windows symlink privilege test was among the platform skips.
- `node --test tests/*.test.mjs` — **28 tests passed, 0 skipped, 0 failures**.
- Python AST syntax validation passed for all seven changed Python files.
- `git diff --check` — passed.

## M08-E remaining acceptance

M08-E still needs its separately defined acceptance scope, cross-milestone
security/reliability review, supported-Windows runtime acceptance, full backup
and restore/purge review, and final milestone acceptance evidence. This report
does not claim those future checks or start M08-E.
