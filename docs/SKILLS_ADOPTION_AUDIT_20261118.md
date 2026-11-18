# Jarvis — Curated Skill Adoption & Audit Plan (Proposal)

**Date:** 2026-11-18  
**Status:** Candidate list; **none installed or approved for autonomous execution**.  
**GitHub baseline:** `97d0be3f03623cdfea6916af3f931192f2ea0a08`.

## Policy and terms

A skill is untrusted instruction content, not a privileged integration. Installation into an agent does not authorize commands or confer Jarvis permission. Do not install the complete collections by default. Begin with project-scoped copies after pinning the exact commit, license, required reference files, scripts and companion skills.

For every skill: inspect exact revision, read full `SKILL.md`, references and helper scripts, record hash/version/license/maintainer, check prompt-injection and hidden operations, verify runtime compatibility (Codex versus OpenCode), verify that it does not auto-merge, auto-deploy, publish issues, run arbitrary shells, transmit local content, or bypass human approval. If it requires these actions, gate or adapt it. Test in a disposable fixture.

## Source registries

| Source | Canonical URL | Findings and cautions |
|---|---|---|
| Matt Pocock | https://github.com/mattpocock/skills | Published `skills/engineering` and `skills/productivity` packages. Some skills invoke companion skills via a host Skill tool; `to-spec` and `to-tickets` publish to a configured issue tracker by default. |
| pstack upstream | https://github.com/cursor/plugins/tree/main/pstack | Built around Cursor's harness and agent orchestration. Auto-ship/merge playbooks are inconsistent with Jarvis approval rule. |
| pstack standalone mirror | https://github.com/backnotprop/pstack | Documents Codex/OpenCode compatibility; mirror patches/adaptations mean provenance must be pinned separately from upstream. |
| Addy Osmani | https://github.com/addyosmani/agent-skills | 25 published skill directories; selected skills may reference repo-level `references/` not copied by per-skill installers. |
| Jarvis current instructions | https://github.com/Omar59687/Jarvis/blob/main/AGENTS.md | Authoritative existing project constraints, including no unapproved merge, no secrets and scope restrictions. |
| REA (future) | https://github.com/morluto/rea | Tool integration for authorized analysis only; not equivalent to a skill pack. Isolated execution and third-party licensing review required before M05. |

## First-wave candidate skills (15)

| Source | Exact skill folder | Priority | Host review requirements |
|---|---|---|---|
| Matt | `skills/engineering/grill-with-docs` | essential for discovery | **Requires** `grilling` and `domain-modeling` skills; wrapper alone will not work. Modifies glossary/ADRs if invoked. |
| Matt | `skills/engineering/to-spec` | essential for requirements | Assumes issue tracker setup and may publish without a separate review step; adapt to draft-only until approved. |
| Matt | `skills/engineering/to-tickets` | essential for slicing | Can create tracker tickets; ensure explicit owner approval before publishing. |
| Matt | `skills/productivity/handoff` | essential for cross-account transitions | Saves handoff to user temp path; standardize redacted GitHub-based permanent handoff instead. |
| Addy | `skills/context-engineering` | essential | Review project instruction precedence and shared references. |
| Addy | `skills/security-and-hardening` | essential | Has reference links (e.g., `references/hardening-patterns.md` and repo-level checklist); include them. |
| Addy | `skills/test-driven-development` | essential | Verify required test commands against Jarvis Python/Node suite. |
| Addy | `skills/code-review-and-quality` | essential | Review-only must not be given merge capability. |
| Addy | `skills/api-and-interface-design` | high | Contract and error semantics align with Python core and browser API. |
| Addy | `skills/source-driven-development` | high | Sources may use network; scope and provenance checks needed. |
| Addy | `skills/debugging-and-error-recovery` | high | Explicit approval on destructive cleanup or retries. |
| pstack | `skills/interrogate` | high | Calls `arena` and uses subagents/worktrees; require companions, isolate temp copies and disable writes to live worktree. |
| pstack | `skills/benchmark-checklist` | high | Uses Unix performance tools in examples; replace with verified Windows-compatible instrumentation. |
| pstack | `skills/how` | high | Uses host-specific read-only subagent APIs; fallback to single read-only analysis if unsupported. |
| pstack | `skills/correct` | later | Can rewrite project conventions; draft proposed enforcement changes for review first. |

**Notably absent from first wave:** pstack `poteto-mode`, `swarm`, `shipping`, `autopilot-full`, unattended GitHub merge/deploy, remote bot UI, broad computer-control skills. Defer until separate risk and bounded-mission review.

## Host matrix — to verify, not assume

| Consumer | Intended use | Scope / delivery | Verification |
|---|---|---|---|
| Local Codex | scoped implementation, independent review | Project-local reviewed skills using documented compatible layout; `AGENTS.md` plus task contract | List skills actually discovered; run one harmless read-only skill and one dummy TDD task |
| Local OpenCode / T3 | implementation and source review | Project `.opencode/skills/<skill>/SKILL.md` after host/version check | Check `skill` discovery, reference resolution and no unsafe auto-actions |
| ChatGPT account A | architecture, task preparation, GitHub review | GitHub docs, available connected skills/tools; never assume workstation skill installation carries over | Explicitly list available skills and GitHub permission scope |
| ChatGPT account B | independent implementation or review | Same GitHub artifacts + redacted task handoff; account connector/skill setup separately verified | Reproduce first independent review using identical commit and acceptance test plan |

Do not copy secrets, model weights, environment files, personal data or local corpus into skill folders or handoff messages. Do not automatically install global skills in both local agents.

## Jarvis-owned skill candidates

- `jarvis-repository-inventory`: read-only inventory tied to commit SHA, source paths, test coverage and dependency metadata.
- `jarvis-reverse-engineer`: evidence-backed call/data/architecture reconstruction, hypothesis ledger and target authorization proof.
- `jarvis-permission-audit`: canonical path checks, authenticated approval contracts, connector segregation, outbound-data protections.
- `jarvis-model-benchmark`: cold/warm latency, token throughput, VRAM/RAM, errors, Arabic/English and tool-use fixtures.
- `jarvis-agent-handoff`: ID, branch/ref, changed files, confidence, tests, unresolved issues, approval status.
- `jarvis-release-gate`: reproducible checks, security controls, rollback evidence, explicit owner merge/deploy action.

These are **future specifications**, not installed tools or operational capabilities.

## Adoption work packages (no execution until approved)

1. Record exact upstream **commit SHAs** and select canonical source (upstream versus mirror). Review per-skill licenses and third-party content.
2. Inventory full dependency files for the 15 candidates, companion skills, referenced repo-level assets and external scripts.
3. Reduce overlap: one primary workflow for spec, one for implementation, one for review; optional specialist skills on demand.
4. Prepare project-local controlled copies with a registry including SHA-256, license, source URL, code owner, host, permission requirements and activation trigger.
5. Fixture-test Codex and OpenCode skill discovery and behavior. Confirm issue/PR publishing and execution require explicit owner approval.
6. Only after reviewing diff, enable the selected registry; verify it does not modify `main` or PR #14.

## Proposed permanent manifest schema

```yaml
skill_id: code-review-and-quality
source: https://github.com/addyosmani/agent-skills
source_path: skills/code-review-and-quality
source_commit: TO_PIN_AFTER_REVIEW
sha256: TO_CALCULATE_AFTER_REVIEW
license_at_revision: TO_VERIFY
dependents: []
reference_assets: TO_AUDIT
consumers: [codex, opencode]
activation: explicit_or_review_stage
risk: review_only
host_permissions:
  repository_read: allow
  shell_execution: ask
  filesystem_write: deny
  github_merge: deny
status: proposed
```

The `host_permissions` keys are **policy requirements**, not a proven schema enforced by Codex/OpenCode. Translate them to actual supported controls only after testing.

## Audit boundary

This preliminary review inspected upstream inventories and selected skill bodies on GitHub; it did not inspect all 15 skills' scripts/references, their exact licenses at pinned commits, transitive dependencies, or execute any skill. **Nothing is safe-to-install yet by this document alone.**
