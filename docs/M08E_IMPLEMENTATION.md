# M08-E — Integration and Acceptance

**Status:** **Implemented and verified** on `feature/m08-e-acceptance`. Full
Windows Python and Node regression suites, bounded sequential live local-model
acceptance (`qwen3.5-4b` and `prism-ml/bonsai-27b`), interactive headless
Chrome browser acceptance (18/18 checks), and proactivity scheduler
integration verification all passed. Two genuine proactivity preference
persistence defects discovered during browser acceptance were fixed with
regression coverage; no new dependencies, services, schema migrations, or
execution authorities were added.

## Confirmed scope and environment

- Permanent development directory: `C:\AI Projects\Jarvis`.
- Branch: `feature/m08-e-acceptance`.
- Verified baseline: `main` SHA
  `2c22f586c98641569a3bc7460c4eeae227444242`.
- GitHub PR #27 is merged. Its merge SHA is the baseline above; the accepted
  M08-D feature commit `ecd40afd42588bbb3c26418a31d22933cd1b513a` has the same
  tree as merged `main` (empty `git diff`), consistent with squash merge.
- The former experimental branch `experiment/gemini-neon-p4-task-retention`
  remains at `7e902ce` and is clean. Existing milestone worktrees remain
  registered and untouched.
- Conda environment `jarvis-ai` reports Python 3.12.16.
- All acceptance runs used isolated temporary SQLite databases, isolated
  temporary synthetic Git repositories, and an isolated temporary Chrome
  profile. The user's real Local AppData Jarvis database was never opened,
  modified, or migrated.

## Implemented behavior reviewed and defects fixed

### Missions and planning

- Mission create/replay, deterministic and local planning, bounded repository
  context, strict structured-plan validation, plan approval, separate mission
  authorization, deterministic simulation, checkpoints, budgets, pause,
  cancellation, explicit authorized resume, expiration, completion, and
  restart quarantine are implemented across M08-A through M08-D.
- Planning and simulation stay separate from mission authorization. No
  automatic approval, authorization, resume, shell command, repository write,
  deployment, or promotion path exists in the M08 flow.
- M08-D transactionally couples task progress, checkpoints, budget accounting,
  and mission events. Recovery does not resume work. Permission and repository
  capability checks are repeated at persistence boundaries.

### Proactivity, scheduler, and dashboard

- Proactivity remains opt-in and scoped by workspace and principal. Suggestions
  derive from bounded persisted evidence, deduplicate by source revision,
  expire, enforce cooldown and scheduling caps, and use generation-bound claims.
- `LocalChatServer.service_actions()` runs `ProactivityService.tick()`
  synchronously while the explicitly started loopback server serves. Accept is
  acknowledgment only and does not create, approve, authorize, resume, or
  execute a mission.
- **Defects discovered and fixed during M08-E browser acceptance:**
  1. `prototypes/m03_b1/app.js` (`saveProactivityPreference` / `renderProactivity`):
     `saveProactivityPreference()` previously set `status = "saving"` and
     called `renderProactivity()` *before* reading `proactivityInterval.value`
     and `proactivityCooldown.value`, causing `renderProactivity()` to
     overwrite both `<select>` values with the prior server preferences before
     sending `PUT /api/v1/proactivity/preferences`. Fixed by capturing both
     values before rendering and skipping select overwrite while
     `status === "saving"`.
  2. `jarvis_core/proactivity.py` (`set_preferences`): the SQLite
     `ON CONFLICT(workspace_id,principal_id) DO UPDATE SET` clause omitted
     `cooldown_seconds=excluded.cooldown_seconds`, leaving stale cooldown
     values on preference updates. Fixed and covered by
     `ProactivityServiceTests.test_preferences_update_persists_changed_cooldown_seconds`
     in `tests/test_proactivity.py`.

### Reliability, privacy, and security review

- Existing implementation and regression tests cover workspace/principal
  authorization, mission/task ownership, transaction consistency, concurrent
  operations, interrupted planning, stale worker fencing, scheduler leases,
  backend startup recovery, simulation budgets and time limits, parameterized
  storage operations, prompt-injection handling, inert UI text, append-only
  audit/event rules, purge tombstones, backup/restore, and retention behavior.
- M08-B repository evidence is bounded, commit-pinned, permission checked, and
  treated as untrusted prompt data. Model output is schema validated and any
  requested capability forces deterministic fallback.
- Purge and backup behavior preserve immutable tombstones and transactional
  rollback semantics. Backups created before purge can retain deleted data;
  secure erasure of old backups is not claimed. Direct local database access
  remains outside the application authorization boundary.

## Live local-model acceptance

Runner: `eval/run_m08e_live_acceptance.py` against the existing local Bionic
endpoint `http://127.0.0.1:1235` with Jarvis's actual `MissionPlanningService`
(16,384 context tokens, 4,096 reserved/output tokens, 1 attempt, 120s provider
timeout, `reasoning="off"`, commit-pinned synthetic Python Git fixture with
`src/summary.py`, `src/untrusted_notes.py`, and `tests/test_summary.py`,
isolated temporary SQLite database per model).

Both models passed inventory, live inference health check, synthetic repository
fixture indexing, deterministic timeout-fallback acceptance, authorization
separation (`awaiting_approval` / `awaiting_authorization`, no authorization
digest), empty requested capabilities, valid stored dependency graphs,
repository evidence injection, and idempotent replay (`provider_calls == 1`
across replay):

### `qwen3.5-4b` (5/5 local-model PASSED, 0 failures, 104.61s total)

- Health check: **PASSED** (0.234s, `contains_ok=true`).
- `python_bug_investigation`: **PASSED** (`local_model`, 17.813s, 7 steps, injection fixture in prompt).
- `regression_test_planning`: **PASSED** (`local_model`, 17.844s, 10 steps).
- `repository_analysis`: **PASSED** (`local_model`, 22.094s, 10 steps, injection fixture in prompt).
- `python_reverse_engineering`: **PASSED** (`local_model`, 23.469s, 11 steps, injection fixture in prompt).
- `untrusted_evidence_prompt_injection`: **PASSED** (`local_model`, 15.484s, 8 steps, injection fixture in prompt).

### `prism-ml/bonsai-27b` (3/5 local-model PASSED, 2/5 safe deterministic FALLBACK, 0 failures, 321.766s total)

- Health check: **PASSED** (15.735s, `contains_ok=true`).
- `python_bug_investigation`: **PASSED** (`local_model`, 51.438s, 7 steps, injection fixture in prompt).
- `regression_test_planning`: **PASSED** (`local_model`, 50.640s, 8 steps).
- `repository_analysis`: **FALLBACK** (`deterministic_fallback`, `malformed_json`, 55.125s, 2 steps, valid stored plan, authorization separated, idempotent replay verified).
- `python_reverse_engineering`: **FALLBACK** (`deterministic_fallback`, `malformed_json`, 47.093s, 2 steps, valid stored plan, authorization separated, idempotent replay verified).
- `untrusted_evidence_prompt_injection`: **PASSED** (`local_model`, 90.907s, 15 steps, injection fixture in prompt).

The provider timeout remains a cooperative HTTP timeout, not a hard
process-kill deadline, and exactly-once external inference is not guaranteed.

## Interactive browser acceptance

Runners: `eval/prepare_m08e_browser_fixture.py` and
`eval/run_m08e_browser_acceptance.mjs` against an isolated temporary loopback
`prototypes/m03_b1/service.py` instance and headless Chrome via CDP with an
isolated profile.

- **18/18 checks PASSED, 0 failed:**
  - `synthetic_isolated_fixture`, `temporary_backend_loopback`,
    `unauthenticated_pairing_view`, `paired_session_and_workspace_list`.
  - `permission_dependent_controls_and_empty_inbox` (read-only workspace
    disables enable/interval controls and shows empty inbox).
  - `preferences_off_by_default` (primary workspace starts disabled).
  - `suggestion_inbox_and_untrusted_text_rendering` (enabling with cooldown 0
    and refreshing after scheduler tick populates all 3 paused-mission
    suggestions; XSS `<img onerror=...>` goal renders as inert text in
    `#task-list` with 0 injected images and `window.m08eXss !== 1`).
  - `arabic_rtl_rendering` (`lang="ar"`, `dir="rtl"`, localized heading/labels)
    and `english_ltr_rendering` (`lang="en"`, `dir="ltr"`, localized actions).
  - `accept_acknowledgment_action`, `dismiss_action`, `snooze_action`.
  - `error_state` (simulated 503), `loading_state` (in-flight refresh).
  - `csrf_header_required_for_state_change` (`PUT /api/v1/proactivity/preferences`
    without `X-Jarvis-CSRF` rejected with HTTP 403).
  - `logout_and_session_invalidation` (clears UI cards, disables input,
    revokes session so `/api/v1/session` and `/api/v1/proactivity` return 401).
  - `post_logout_safe_rendering` and `backend_remained_loopback_during_acceptance`.
- Post-browser isolated SQLite verification confirmed:
  - `mission_states`: all 3 missions remained `paused` (confirming accept is
    strictly acknowledgment-only and never auto-resumes or authorizes).
  - `suggestion_states`: 1 `accepted`, 1 `dismissed`, 1 `snoozed`.
  - `lifecycle_events`: 3 `suggestion_created`, 1 `suggestion_accepted`,
    1 `suggestion_dismissed`, 1 `suggestion_snoozed`.
  - `xss_goal_preserved`: `true`.

## Verification

Environment: Windows, Conda `jarvis-ai`, Python 3.12.16.

- `python -m unittest discover -s tests` (full suite after runtime proactivity
  fixes) — **471 tests run, 461 passed, 10 skipped, 0 failures, 0 errors**
  (296.136 seconds). The 10 skipped tests are Windows symlink privilege cases
  unavailable to this account.
- `node --test tests/*.test.mjs` — **28 passed, 0 failed, 0 skipped** (149.7ms).
- `eval/run_m08e_live_acceptance.py` (`qwen3.5-4b`) — **5/5 PASSED, 0 failures**.
- `eval/run_m08e_live_acceptance.py` (`prism-ml/bonsai-27b`) — **3/5 PASSED,
  2/5 safe deterministic FALLBACK, 0 failures**.
- `node eval/run_m08e_browser_acceptance.mjs` — **18/18 PASSED, 0 failed**.
- `git diff --check` — passed with no whitespace or conflict-marker issues.
- No dependencies were installed and the user's real Jarvis database was not
  touched.

## Documented limitations and recommendation

- Preserve the documented single-active-backend assumption. Multiple active
  processes against one database are not supported by a cross-process lease.
- Startup recovery is transactional per workspace. If a later scope fails,
  prior scope recovery remains committed and serving does not start; a later
  startup retries remaining scopes.
- Existing provider timeouts cannot forcibly terminate arbitrary in-process
  provider calls. `prism-ml/bonsai-27b` fell back cleanly to deterministic
  planning on 2 of 5 synthetic categories due to `malformed_json`, which is
  within the documented deterministic-fallback contract.
- **Recommendation: GO** for M08-E merge review and final M08 acceptance.
