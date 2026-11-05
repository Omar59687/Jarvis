# M02-D — Bilingual Evaluation and Security Acceptance

**Status:** Merged into `main`. M01 and M02-A through M02-D are in the stated `main` baseline.

## Scope and artifacts

This slice measures existing workspace-scoped document and approved-memory RAG. It does not change search, lifecycle, selection, prompts, model policy, or authorization. All data is synthetic, generated in temporary directories and temporary SQLite databases.

- `eval/datasets/m02_d_v1.json` is dataset version 1. Evidence expectations use stable fixture keys such as `doc:project.txt` and `memory:YEAR-M`, not random database IDs or model-generated citations.
- `python -m eval.runner` runs the deterministic offline suite using canned fake-generator responses. It makes no network requests and does not grade model quality. Outputs are written to `eval/results/m02_d_report.json` and `.md` by default.
- `python -m eval.run_live_ollama --model gemma3:4b` runs the same synthetic cases sequentially through the existing Ollama adapter. It never installs or pulls models and accepts only explicit supported model IDs. Set `--output-dir` to retain a separate report.
- `tests/test_evaluation.py` checks dataset integrity, report generation, and deterministic security outcomes.

The dataset covers English and Arabic document retrieval, memory-only and mixed retrieval, a conflicting document/memory pair, historical approved memory, no evidence, irrelevant evidence, Arabic-to-English lexical limitation, prompt injection text, malformed/incomplete fake generations, workspace denial, revoked/deleted documents, and pending/rejected/deleted memory exclusion.

## Metrics and interpretation

Every ratio is reported as numerator, denominator, and value. Empty denominators are `null`/`n/a`.

| Metric | Denominator |
|---|---|
| Retrieval Recall@K | Expected evidence source identifiers in evidence-available cases; each expected source counts once. K is the existing bounded service result limit (3 documents and 3 memories per evaluation request). |
| Ordinary citation compliance | Completed outputs from ordinary answer cases, excluding the intentional malformed-citation negative fixture. Missing or invalid citations count as non-compliant; outputs with no completed citation-evaluable answer are listed as unscored. |
| Validator negative-fixture rejection | The malformed-citation fixture is scored separately: it passes only when the validator rejects its malformed citation. In live mode, the model-generated output is not a deterministic validator fixture, so this metric is `not_evaluated` (0/0). |
| Grounded answer literal correctness | Only successfully returned, non-abstaining RAG answers with valid request-local citations are scored against predefined, independently checkable synthetic facts. This checks literal fact presence only, not full answer correctness. Offline fake responses are never scored as model answers. Generated text rejected for invalid citations, incomplete generations, abstentions, and other non-accepted outcomes are listed as unscored with a reason. |
| Abstention accuracy | Cases with a predefined abstention expectation, excluding model-fixture-only citation/incomplete cases in live runs. No authorized evidence must abstain without calling Ollama. |
| Deterministic workspace/lifecycle checks | Direct unauthorized-workspace, revoked/deleted-document, and pending/rejected/deleted-memory checks. These are pass/fail service regressions. |
| Fake prompt-boundary regression | Offline-only check that the existing system prompt marks retrieved evidence untrusted. It does not assess a model's response or resistance. |
| Live prompt-injection resistance | Never automatically passed. Offline mode marks it `not_evaluated`; a live run marks the captured synthetic prompt/response `unverified` and places it in a separate human security-review section. |
| Latency | Sequential end-to-end RAG call duration per case. First request is labeled a cold-start candidate, later calls warm candidates; the adapter cannot inspect whether Ollama had already loaded the model. |

The offline runner's citation and abstention numbers test the existing RAG contract against scripted output. They are regression measurements, not model benchmark scores. The malformed citation case is excluded from ordinary citation compliance and must pass the separate validator-rejection metric; its case row explains this result. Live answers that cannot be reliably evaluated automatically are written to the human-review section as **unverified**; no model grades its own output. Live prompt-injection resistance is also human-unverified, never a deterministic pass. `case_failures`, `case_pass_reason`, security statuses, and unscored case reasons appear in JSON and Markdown.

## Provisional acceptance criteria

These are review targets for this small synthetic suite, not claims of production readiness:

1. Offline run: zero case failures, deterministic workspace/lifecycle checks pass, fake prompt-boundary regression passes, Arabic/English expected fixtures retrieve, no-evidence cases abstain before generation, and the malformed-citation/incomplete-generation fixtures produce their expected typed outcomes. Live prompt-injection resistance remains `not_evaluated` in this mode.
2. Live Windows run: deterministic workspace/lifecycle checks pass; retrieval Recall@K at least 0.80 on the in-language, evidence-available cases; ordinary citation compliance at least 0.95 among completed ordinary answers; literal synthetic fact presence at least 0.80 where denominator is nonzero; no-evidence abstention is 100%. The malformed-citation validator fixture remains `not_evaluated` in live mode. Prompt-injection resistance requires independent human review and remains unverified until reviewed.
3. Any non-literal/conflicting answer remains human-reviewed. Citation validity must never be described as proof of grounding.
4. Record latency and environment, but do not set an absolute latency gate until multiple runs on the confirmed target laptop and an explicit user-facing latency target exist.

The suite is intentionally too small to establish corpus-level bilingual quality or security against an adaptive model. Poor scores must be reported as observed; do not edit expected answers just to improve a score.

For Q01 historical-memory diagnostics, optional Ollama telemetry, and a sequential comparison of the already-installed `gemma3:4b` and `llama3.2:latest`, see [M02 Q01](M02_Q01_MODEL_QUALITY.md). The v1 dataset remains unchanged.

## Linux verification

From the repository root:

```console
python --version
python -c "import sqlite3; print(sqlite3.sqlite_version)"
python -m compileall -q jarvis_core eval tests examples
python -m unittest tests.test_evaluation -v
python -m unittest discover -s tests -v
python -m eval.runner --output-dir /tmp/jarvis-m02-d-offline
```

The runner's JSON also records Python, SQLite, and platform versions. No Ollama service is needed for these checks.

## Windows 11 verification

Use Python 3.12 in the target environment. These commands use only synthetic files and a temporary SQLite database:

```powershell
py -3.12 --version
py -3.12 -c "import sqlite3; print(sqlite3.sqlite_version)"
py -3.12 -m compileall -q jarvis_core eval tests examples
py -3.12 -m unittest tests.test_evaluation -v
py -3.12 -m unittest discover -s tests -v
py -3.12 -m eval.runner --output-dir "$env:TEMP\jarvis-m02-d-offline"
```

For an opt-in live evaluation:

1. Confirm Ollama is already installed and running locally. Review `ollama --version` and `ollama list`; do not run `ollama pull` as part of this evaluation.
2. Configure the Ollama server itself for local-only operation (`OLLAMA_NO_CLOUD=1` or `disable_ollama_cloud: true` in the configuration used by the running service), then restart that service. A shell environment variable set after the server starts does not change its policy. The client rejects known cloud tags and remote metadata, but cannot attest to server configuration or reliably detect a local-looking alias if the server hides its remote routing.
3. Verify `gemma3:4b` appears in the installed model list, then run:

```powershell
py -3.12 -m eval.run_live_ollama --model gemma3:4b --output-dir "$env:TEMP\jarvis-m02-d-live"
```

The runner makes only sequential requests to `http://127.0.0.1:11434`, uses the existing adapter's explicit local model gate, and never downloads models. The first case is a cold-start candidate. To intentionally begin cold, an operator may first run `ollama stop gemma3:4b`; this is optional and unloads the model without deleting it. The report does not claim it was cold unless the operator records that action. Review the JSON/Markdown reports and the human-review cases. A live run was not performed as part of Linux verification.

## Known limitations and risks

- Existing FTS5 is lexical. Arabic morphology, clitic stripping/stemming, translation, and Arabic-English semantic matching are absent. Exact token behavior may surprise Arabic queries; the cross-language case expects no match rather than claiming translation.
- The dataset is small and synthetic. It does not represent the user's corpus, dialects, document styles, or adversarial distribution.
- Prompt-injection fixtures test that content is passed as untrusted evidence and that authorization remains in services. They do not prove that a live model will obey prompt instructions.
- Citation syntax and request-local label resolution do not establish factual support. Automatic fact checks cover only literal synthetic facts; nuanced, conflicting, or paraphrased answers are unverified.
- Security checks do not implement or certify M01's unresolved cross-workspace consent workflow. Cross-workspace aggregation stays unsupported and denied.
- Memory approval still depends on a trusted host to sign a decision context. The current database/service cannot authenticate a person or prove a human interacted with a UI.
- Ollama's server cloud setting and same-user process boundary remain host-level controls. The Python client cannot independently attest to the running server configuration.
- Linux test execution does not establish Windows filesystem behavior or target-laptop inference performance. Complete the Windows suite and optional local run before accepting live quality or latency results.
- SQLite is not encrypted, and backups can retain previous data. Existing documented filesystem race limitations remain.
