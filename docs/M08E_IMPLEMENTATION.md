# M08-E — Integration and Acceptance

**Status:** integration regression passed; final acceptance remains **Open**.
The required expanded live-model and interactive browser checks did not
complete in this environment. No M08-E runtime changes or new dependencies
were required by this acceptance review.

## Confirmed scope and environment

- Permanent development directory: `C:\AI Projects\Jarvis`.
- Branch: `feature/m08-e-acceptance`.
- Verified baseline: `main` SHA
  `2c22f586c98641569a3bc7460c4eeae227444242`.
- GitHub PR #27 is merged. Its merge SHA is the baseline above; the accepted
  M08-D feature commit `ecd40afd42588bbb3c26418a31d22933cd1b513a` has the same
  tree as merged `main` (empty `git diff`), consistent with squash merge.
- The former experimental branch `experiment/gemini-neon-p4-task-retention`
  remains at `7e902ce` and is clean. Its commit and all local refs remain
  available in the same repository.
- No branch or worktree already used `feature/m08-e-acceptance`; the new branch
  was created directly in this directory. Existing milestone worktrees remain
  registered and untouched.
- Conda environment `jarvis-ai` reports Python 3.12.16.
- The target directory had no uncommitted or untracked work. Ignored files were
  Python bytecode caches and `.pytest_cache`; no `.env`, local database, `data/`,
  or `user-data/` entry was present. Runtime configuration in repository code
  defaults databases to the user's Local AppData location; this review did not
  open, migrate, or start the application against that database.

## Implemented behavior reviewed

### Missions and planning

- Mission create/replay, deterministic and local planning, bounded repository
  context, strict structured-plan validation, plan approval, separate mission
  authorization, deterministic simulation, checkpoints, budgets, pause,
  cancellation, explicit authorized resume, expiration, completion, and
  restart quarantine are implemented across M08-A through M08-D.
- Planning and simulation stay separate from mission authorization. No
  automatic approval, authorization, resume, shell command, repository write,
  deployment, or promotion path was found in the reviewed M08 flow.
- M08-D transactionally couples task progress, checkpoints, budget accounting,
  and mission events. Recovery does not resume work. Permission and repository
  capability checks are repeated at persistence boundaries.
- Existing M08-A through M08-D suites exercise idempotency, persistence,
  ownership, cross-workspace denial, plan and authorization separation,
  lifecycle transitions, budget/time bounds, injected rollback, stale worker
  fencing, planning interruption, restart recovery, expiration, and
  deterministic fallback.

### Proactivity and dashboard

- Proactivity remains opt-in and scoped by workspace and principal. Suggestions
  derive from bounded persisted evidence, deduplicate by source revision,
  expire, enforce cooldown and scheduling caps, and use generation-bound claims.
- Accept is acknowledgment only. It does not create, approve, authorize, resume,
  or execute a mission. Revocation disables preferences and fences stale
  workers; restart can reclaim expired scheduler leases.
- Authenticated API tests cover session, CSRF, exact origin, workspace
  switching, permission errors, and suggestion lifecycle. JavaScript tests
  cover workspace/logout fencing and localized controls/state. Safe rendering
  checks reject unsafe interpolation patterns in the dashboard code.
- **Browser acceptance is Open:** preview tooling reported unavailable. No
  claim is made that the dashboard was exercised in a browser. HTTP and
  JavaScript tests do not establish visual layout, interactive accessibility,
  or end-to-end English/Arabic rendering.

### Reliability, privacy, and security review

- Existing implementation and regression tests cover workspace/principal
  authorization, mission/task ownership, transaction consistency, concurrent
  operations, interrupted planning, stale worker fencing, scheduler leases,
  backend startup recovery, simulation budgets and time limits, parameterized
  storage operations, prompt-injection handling, inert UI text, append-only
  audit/event rules, purge tombstones, backup/restore, and retention behavior.
- No confirmed implementation defect was found during this acceptance review;
  therefore no runtime code was changed. The full tests are evidence for the
  covered cases, not a claim of formal security certification or exhaustive
  security review.
- M08-B repository evidence is bounded, commit-pinned, permission checked, and
  treated as untrusted prompt data. Model output is schema validated and any
  requested capability forces deterministic fallback. Existing prompt
  injection tests cover both labeling and capability-denial behavior.
- SQL operations in the reviewed mission/proactivity/storage paths use bound
  values for data. Tests cover scope isolation and permission revocation at
  transaction boundaries.
- Purge and backup behavior preserve immutable tombstones and transactional
  rollback semantics. Backups created before purge can retain deleted data;
  secure erasure of old backups is not claimed. Direct local database access
  remains outside the application authorization boundary.

## Live local-model acceptance

The existing Bionic endpoint `http://127.0.0.1:1235` responded to `/v1/models`
with HTTP 200 and listed `qwen3.5-4b` and `prism-ml/bonsai-27b`.

An expanded, sequential acceptance attempt called Jarvis's actual
`MissionPlanningService` with its configured limits (16,384 context tokens,
4,096 reserved/output tokens, one attempt, 120-second provider timeout) for
five synthetic categories: Python coding, debugging, regression design,
repository analysis, and authorized reverse-engineering planning. It did not
produce a completed result during the observed run; only the acceptance client
was interrupted, and the existing Bionic service was left running. No
structured output, quality score, latency, persistence, or idempotency result
from this expanded run is reported as passed. No project repository evidence
was supplied to the models in this attempt.

M08-B's prior recorded acceptance documents successful 15-check planning
smokes for both models, including structured plans, deterministic fallback,
persisted plan/provenance, idempotent replay, capability denial, approval and
authorization boundaries, and configured limits. It also records a Qwen
timeout followed by successful acceptance. Those earlier results do not
replace the expanded task-category and repository-evidence checks requested
for M08-E.

The provider timeout is a cooperative HTTP timeout, not a hard process-kill
deadline. A provider call that does not return control can outlive the planning
deadline; Jarvis checks elapsed time after the call returns. Exactly-once
external inference is not guaranteed. These documented limitations remain.

## Verification

Environment: Windows, Conda `jarvis-ai`, Python 3.12.16.

- `conda run -n jarvis-ai python -m unittest discover -s tests -v` — **470
  tests run, 460 passed, 10 skipped, 0 failures, 0 errors** (306.159 seconds).
  The skipped Windows symlink capability cases require a privilege not
  available to this account.
- `node --test tests/*.test.mjs` — **28 passed, 0 failed, 0 skipped**.
- `git diff --check` — passed for the final staged documentation diff.
- Browser end-to-end — **not run; preview unavailable**.
- Expanded live model acceptance — **incomplete; no result** as detailed above.
- No dependencies were installed and no Jarvis application/backend service was started.

## Open acceptance gates and recommendation

- Re-run live tests sequentially for both models with bounded task counts and
  record structured output, latency, repository evidence, fallback behavior,
  persistence, and idempotent replay.
- Run the authenticated dashboard in an available browser and verify English
  and Arabic flows, session invalidation, CSRF/origin behavior, lifecycle
  controls, and safe rendering.
- Preserve the documented single-active-backend assumption. Multiple active
  processes against one database are not supported by a cross-process lease.
- Startup recovery is transactional per workspace. If a later scope fails,
  prior scope recovery remains committed and serving does not start; a later
  startup retries remaining scopes.
- Existing provider timeouts cannot forcibly terminate arbitrary in-process
  provider calls. Provider/model hardware fit and maximum context capacity are
  not established here.
- Local Python full-suite and JavaScript regression passed, but M08-E merge and
  final M08 acceptance are **NO-GO** until the two unavailable acceptance gates
  above are completed.
