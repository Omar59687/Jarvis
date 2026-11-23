# M06 approval-controlled file changes

**Status:** Implemented bounded proposal, review, approval, application, audit, and recovery in an isolated feature branch. This is a local-first file-change vertical slice, not an autonomous agent or a verified OS sandbox.

## Trust boundaries and controls

- The local service derives the actor from the authenticated session and requires the selected authorized workspace.
- Repository access reuses M05 registration and repository capability policy. Proposals require metadata, indexed-source, and proposal capabilities. Applying requires workspace READ and WRITE plus the distinct `apply_approved_file_changes` repository capability.
- Model output and indexed repository content are untrusted. Model proposals use only 1–5 selected indexed files, a bounded prompt, strict JSON shape and target validation, and at most 512 generated tokens. Model output never approves itself.
- Human approval is bound to actor, workspace, repository, base commit, exact patch digest and operation paths, has a 15-minute expiry, and is single-use. Delete operations require a separate delete-specific confirmation.
- Paths must pass the existing repository path policy and link-component checks. Source preconditions are rechecked. Writes stage bounded files and use atomic replacement; failures trigger bounded rollback or `recovery_required` state.
- Approved changes go to a per-proposal task workspace containing only selected files. The registered source checkout remains unchanged. UI output uses text rendering; audit metadata contains paths, operation labels, hashes, counts, and outcome, not proposed content.
- Migration v14 adds the new capability and proposal table while preserving prior grant rows. No new dependency is introduced.

## Explicitly unavailable

No verification command is run. The workflow does not execute repository code, invoke a shell, create commits, promote changes, push, open PRs, merge, deploy, access networks, or autonomously approve. The task workspace is scoped by the application and filesystem paths; it is **not** OS-enforced isolation. It does not establish protection from other same-user processes, Windows junction/reparse races, or host compromise. Do not describe it as a sandbox. The source checkout is not modified, and users must manually transfer/review task output if they choose to use it.

These limits preserve the P4 Windows isolation gate in [P4 security and acceptance plan](P4_SECURITY_AND_ACCEPTANCE_PLAN.md). A later stage must verify OS containment, process/network restrictions, crash recovery, Windows link-race resistance, and approved verification before any execution or promotion capability is considered.

## Review evidence

Focused coverage is in `tests/test_approved_changes.py` and `tests/test_approved_changes_http.py`. The Windows symlink-creation case may be skipped when the test account lacks the OS privilege; this skip does not establish that link-race protection passed. On the final tree, `python -m unittest discover -s tests -v` passed 372 tests with 10 skips; skips were platform/privilege-dependent symlink cases. `node --test tests/*.test.mjs` passed 24 tests. `python -m compileall -q jarvis_core prototypes tests`, `node --check prototypes/m03_b1/app.js`, and `git diff --check` passed. The M06 focused tests are 17 Python tests (16 pass, 1 Windows symlink privilege skip), including service-level CSRF/auth and source-checkout preservation.