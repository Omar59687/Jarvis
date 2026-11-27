# M08-B — Intelligent Mission Planning

**Status:** confirmed implementation scope; implementation is on
`feature/m08-b-mission-planning`. M08-C and later phases are not included.

## Confirmed requirements

- Add structured, bounded planning to M08-A missions using the existing
  `TaskOrchestrationService` and plan persistence.
- Provide a deterministic plan and safe fallback when no local model is
  configured or provider output is unusable.
- Optionally call an explicitly configured local provider through the existing
  provider contract. The LM Studio Bionic adapter supports configured models
  such as `qwen3.5-4b` and `prism-ml/bonsai-27b`; availability and performance
  depend on the user's installed model and hardware.
- Validate schema, step count/text bounds, dependency references and cycles,
  and reject model capability requests. Planning does not grant authority.
- Reuse workspace-checked, commit-pinned Developer Intelligence for bounded
  repository context. Retrieved repository text is untrusted prompt data.
- Bound provider attempts, provider timeout settings, prompt/context estimate,
  retrieved files, output allowance and response size. Record immutable
  planning provenance in the append-only mission event ledger.
- Preserve M08-A mission authorization, task-plan approval, lifecycle,
  budgets, restart behavior and simulation-only execution.
- Keep Python the primary language. Independent local Python coding is a future
  capability. T3 Code and other external agents remain optional integrations.
  Dedicated Java and other language support is deferred.

## Planning flow

1. The manager-authorized mission facade validates workspace access and reads
   the mission's existing task and exact repository snapshot.
2. If the task is repository-bound, the planner calls the existing search and
   source APIs for a small number of files at the task's pinned commit. The
   source API rechecks workspace access and snapshot currency.
3. The planner packages goal and evidence as JSON data. Its trusted system
   prompt marks repository content untrusted and forbids capability requests.
4. A successful response must be strict schema-v1 JSON. The orchestration plan
   validator enforces bounded fields, known dependencies, acyclic ordering and
   requested-capability limits. Mission plans additionally require every
   `required_capabilities` list to be empty.
5. Provider absence, timeout/failure, malformed output, validation failure or
   context overflow yields a deterministic bounded plan. No provider response
   text or exception detail is placed in audit metadata.
6. The task plan persists using existing orchestration storage. An immutable
   `plan_linked` event binds plan digest and planning provenance. The task plan,
   mission linkage, provenance event, and reservation completion commit in one
   SQLite transaction. Existing
   authorization still requires separate digest-bound task-plan approval and
   M08-A mission authorization before deterministic simulation.

## Durable idempotency and recovery

Schema v16 adds `mission_planning_reservations`, uniquely scoped by workspace,
mission principal, mission, and idempotency key. The immutable request digest
binds the task goal, repository snapshot, capabilities, model, and planner
limits. A write transaction inserts the reservation before provider work. A
second matching caller receives `mission_plan_in_progress`; a different
request using that key receives an idempotency conflict. Each configured
provider attempt is counted in a committed transaction immediately before the
provider call. No SQLite write transaction remains open during inference.

Completed reservations replay their digest-checked task plan without calling
the provider. An interrupted or expired reservation is moved to `interrupted`
and is not retried under the same key. Its configured attempt count is durable.
If a process disappears, other callers observe the active reservation until
its provider-timeout lease expires; the first caller after expiry fences it as
interrupted. No automatic takeover or resume occurs. Owner tokens fence plan
commit, and the plan, task event, mission event, mission state, provenance and
reservation completion share one `BEGIN IMMEDIATE` transaction.

This provides one provider owner during a live reservation and crash-safe
single-commit persistence. It cannot guarantee exactly-once external inference:
a process may crash after the provider accepted a request but before Jarvis
records its response. That reservation is conservatively interrupted and is
not automatically repeated. A host crash immediately after the database commit
may leave the caller unaware of success; a replay reads the completed plan.
SQLite migration v16 is additive and runs with the existing atomic migration
chain. Backups at v16 must contain the reservation table and required columns.
No new runtime or service dependency was added.

## Context limits and evidence

`MissionPlanningConfig` defaults to a 16,384-token context window, reserves
4,096 output tokens, permits at most 4,096 generated output tokens, allows one
attempt by default (maximum two), and uses the provider's bounded timeout. The
current estimate is `ceil(UTF-8 bytes / 4)`. This is a rough application-side
budget heuristic, **not exact token accounting** and not a claim about a
model's real tokenizer. The user-configured model server remains responsible
for its actual loaded context and memory limits. A smaller or unavailable
budget fails to the deterministic planner. There is no benchmark establishing
that either named model fits a particular GPU, RAM, or quantization setup.

The repository context cap defaults to three files and 6,000 characters. Files
are selected with existing lexical/symbol search and retrieved at the task's
commit. The index can be stale or incomplete; no semantic repository
understanding is claimed. Context overflow does not truncate the mission goal
or authorize a network fallback.

## External research and licensing screen

Research consulted official project repositories/documentation on 2026-11-27.
The recorded Git refs are upstream `HEAD` values at research time, not adopted
dependencies. Licenses below are repository SPDX metadata and must be checked
at an exact release if future code/assets are redistributed.

| Project and source | Research ref / license | Reusable approach | Cost, risks and M08-B decision |
|---|---|---|---|
| [Aider repository map docs](https://aider.chat/docs/repomap.html), [source](https://github.com/Aider-AI/aider/tree/5dc9490bb35f9729ef2c95d00a19ccd30c26339c) | `5dc9490bb35f9729ef2c95d00a19ccd30c26339c`, Apache-2.0 | Rank concise repository symbols/relationships against the task and fit only high-value context into a budget. | Python tool with a larger dependency tree and optional tree-sitter parsing; no model/runtime bundled. Use existing Jarvis symbol search and source API instead of adding Aider or its dependencies. No code copied. |
| [OpenHands legacy CodeAct docs](https://docs.all-hands.dev/openhands/usage/agents), [source](https://github.com/OpenHands/OpenHands/tree/be48abe2585a01b4ee666e71ba5c43c47fb63772) | `be48abe2585a01b4ee666e71ba5c43c47fb63772`, MIT | Separate planning/model output from tool policy and keep agent actions mediated by an execution runtime. | Full coding-agent systems require sandbox/runtime, process, filesystem and often container resources. Those capabilities are outside M08-B. No dependency or code adopted. |
| [OpenCode provider docs](https://opencode.ai/docs/providers/), [agents](https://opencode.ai/docs/agents/), [permissions](https://opencode.ai/docs/permissions/), [source](https://github.com/anomalyco/opencode/tree/7bfda96047aea7dd28fc61dbfa7c69bea670849e) | `7bfda96047aea7dd28fc61dbfa7c69bea670849e`, MIT | Explicit provider/model configuration, context controls and policy gates around agent tools. | TypeScript/Node distribution and provider ecosystem add a runtime and transitive dependencies; provider selection does not replace Jarvis authorization. Keep external coding agents optional; no dependency or code adopted. |
| [LangGraph persistence docs](https://docs.langchain.com/oss/python/langgraph/persistence), [durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution), [source](https://github.com/langchain-ai/langgraph/tree/3e3dbd42c5329dd404669ccd923c005c814bee3a) | `3e3dbd42c5329dd404669ccd923c005c814bee3a`, MIT | Durable workflow state, checkpoint identity and explicit interruption/resume points. | Framework and checkpoint integrations enlarge runtime/dependency surface. Existing SQLite mission/task ledgers already provide the needed bounded durable state; no framework adopted. |

These are architecture screens, not complete source, dependency, advisory, or
legal audits. External pages and repository content were treated as untrusted
reference data; no external source code was downloaded or executed.

The comparison also does not establish model or framework hardware sizing.
Aider and OpenCode rely on the configured model/provider for inference; OpenHands
adds an execution environment whose container and model memory/CPU needs depend
on deployment; LangGraph persists workflow state but does not supply inference.
Jarvis adds none of these runtimes. The named local model weights, quantization,
GPU/CPU fit and actual context allocation are operator-managed and were not
benchmarked here.

## Security boundaries

- A model plan is a proposal. The model does not choose the actor/workspace,
  repository scope, grants, authorization, budget, provider URL, or worker.
- Repository content can contain prompt injection. It is bounded and labeled
  untrusted; even a successful injection cannot grant a capability because
  every generated step is rejected if it requests one.
- Existing workspace/principal checks and commit-pinned repository read APIs
  remain authoritative. Cross-workspace context retrieval is denied.
- No shell, process, filesystem write, external network, GitHub write, commit,
  push, pull request, promotion, deploy, autonomous loop, or auto-resume is
  added. M08-A simulation remains the only execution behavior.
- The provider interface is injected by trusted application construction.
  Jarvis-shipped local adapters enforce numeric loopback and disable proxy and
  redirect routing; M08-B adds no remote provider implementation.

## Implemented evidence

Evidence is limited to the changed code and executed checks reported with this
branch. `MissionPlanningService` is a trusted Python API; no UI or HTTP route is
added. Local model behavior remains unverified until a configured endpoint and
installed model pass the bounded smoke test. Context token estimates are not a
tokenizer guarantee; actual model RAM/context compatibility is unverified.

## Verification

- `python -m unittest tests.test_mission_planning tests.test_missions -v` — 31 passed.
- `python -m unittest discover -s tests -v` — 439 passed, 10 skipped.
- `python -m unittest tests.test_developer_intelligence.DeveloperIntelligenceTests.test_authenticated_api_returns_workspace_scoped_sources_and_cited_chat -v` — 1 passed when run separately, and passed in the final full suite. An earlier broader focused run hit that test's five-second HTTP timeout once.
- `node --test tests/*.test.mjs` — 25 passed, 0 failed.
- Python compilation for `jarvis_core` and JavaScript syntax checks for tracked `.js`/`.mjs` files — passed.
- `git diff --check` — passed.
- Live Bionic model smoke test — not run for this repair; no service was started.
