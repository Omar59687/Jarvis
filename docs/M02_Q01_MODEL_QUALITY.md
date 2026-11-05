# M02 Quality Checkpoint Q01

**Status:** Proposed on `feature/q01-model-quality`; review required. No live model benchmark was run for this checkpoint.

## Historical-memory case

The unchanged M02-D v1 case `historical-en-01` asks, “What was the old fictional room code?” Its approved memory preserves the snapshot `The fictional room code was Amber-7.`; the source document is then changed to `Renovation marker: Cedar-8.`. The expected evidence is the approved memory only and the expected answer is non-abstaining with an `[M#]` citation. The fixture is synthetic.

Plausible causes of the reported Gemma abstention include the prior wording emphasizing that historical sources are inactive/currently unavailable without explicitly saying they remain usable for a past-tense question; a model may conservatively interpret “historical” as unusable evidence. Other possibilities include ordinary generation variance, prompt/context interpretation, or a citation-format issue. The earlier response and exact prompt were not retained in the supplied benchmark summary, so the cause is not established.

The mixed-RAG prompt now distinguishes the uses: an approved historical snapshot can support a question about the past when the snapshot directly supports the answer, must be cited with its request-local `[M#]` label, and must be described as historical. A historical snapshot alone does not support a present-state claim. Retrieval, source revocation, approval, workspace authorization, evidence selection, and citation validation are unchanged. The instruction is not a factuality guarantee and does not compel an answer where evidence is insufficient.

The offline runner accepts a case filter and an opt-in diagnostic filter. The diagnostic JSON captures the exact synthetic question, selected evidence excerpts and source statuses, model system/user prompts, generated output, RAG outcome, citations, and timing metadata. It is written to the chosen output directory. Evidence remains in the diagnostic when RAG rejects a generated response; this does not make rejected citations valid. A fake-client regression test exercises the capture path; it is not a model benchmark.

## Ollama timing fields

For non-streaming `/api/chat`, Ollama may return `total_duration`, `load_duration`, `prompt_eval_count`, `prompt_eval_cached_count`, `prompt_eval_duration`, `eval_count`, and `eval_duration` in the response. The adapter now preserves valid non-negative integer values as optional fields on `GenerationResult`; omitted or malformed values stay `None`. Existing three-argument `GenerationResult(model, text, done)` construction remains valid. The field names and units follow [Ollama's API documentation](https://github.com/ollama/ollama/blob/main/docs/api.md).

Reports keep distinct measurements:

- `end_to_end_request_ms`: RAG call from immediately before answering until result/error; includes retrieval, prompt packaging, adapter installed-model check, transport, and model response.
- `ollama_client_call_elapsed_ms`: elapsed time inside the generator's client call, including the local installed-model check and chat call. It is not presented as pure inference time.
- `ollama_reported.total_duration_ns`, `load_duration_ns`, `prompt_eval_duration_ns`, and `eval_duration_ns`: server-reported nanoseconds, when present. `eval_duration_ns` is generation evaluation time; no missing field is inferred from wall time.
- `prompt_eval_count`, `prompt_eval_cached_count`, and `eval_count`: server-reported token counts when available.

First request remains a cold-start *candidate* only. The client does not observe Ollama's resident model state. Existing evaluation reports are preserved; Q01 report output uses separately selected output directories.

## Opt-in Windows workflow

Use PowerShell in the repository root with Python 3.12. These commands use synthetic fixtures and temporary SQLite databases. They do not download models:

```powershell
py -3.12 --version
py -3.12 -c "import sqlite3; print(sqlite3.sqlite_version)"
py -3.12 -m compileall -q jarvis_core eval tests examples
py -3.12 -m unittest discover -s tests -v
py -3.12 -m eval.runner --output-dir "$env:TEMP\jarvis-q01-offline"
```

Before any live run, an operator must verify that Ollama is already installed and running locally, that server-side cloud routing is disabled (for example, start/configure the Ollama service with `OLLAMA_NO_CLOUD=1` or its supported `disable_ollama_cloud: true` setting, then restart the service), and that both models are already present. Follow the host setup notes in [M02-A](M02A_OLLAMA_ADAPTER.md). Do not use `ollama pull`. The client only permits numeric loopback and explicitly allowlisted names; the server's configuration and model alias behavior remain host-level limits.

Focused historical-memory diagnostic for one explicitly selected, already-installed model:

```powershell
ollama list
py -3.12 -m eval.run_historical_diagnostic --model gemma3:4b --output-dir "$env:TEMP\jarvis-q01-historical-gemma"
py -3.12 -m eval.run_historical_diagnostic --model llama3.2:latest --output-dir "$env:TEMP\jarvis-q01-historical-llama"
```

Full sequential comparison (preflights that both named models are locally installed, then runs one full dataset report at a time into separate directories):

```powershell
ollama list
py -3.12 -m eval.run_model_comparison --output-dir "$env:TEMP\jarvis-q01-model-comparison"
```

Review each model's JSON/Markdown report, historical case, unscored/human-review items, and timing metadata separately. The comparison summary deliberately names no winner. Do not interpret one run as a stable ranking; repeat under recorded warm/cold conditions on the target laptop before drawing a conclusion.

## Linux verification

```console
python --version
python -c "import sqlite3; print(sqlite3.sqlite_version)"
python -m compileall -q jarvis_core eval tests examples
python -m unittest tests.test_ollama tests.test_memory_search tests.test_evaluation -v
python -m unittest discover -s tests -v
python -m eval.runner --output-dir /tmp/jarvis-q01-offline
```

Automated tests use only fake transports/generators and synthetic temporary data. No Ollama service, Internet, model downloads, or live model-quality claims are required. Windows execution remains necessary to verify the requested platform workflow and actual model comparison; no Windows run was available to this checkpoint.

## Limits and open points

- The original historical Gemma prompt/output were not saved, so diagnosis is plausible rather than conclusive until the focused Windows run captures them.
- Clearer instructions can reduce ambiguity but cannot guarantee the model will answer correctly or obey prompt boundaries.
- Citation validation only verifies request-local label resolution, not factual entailment.
- Ollama metadata is optional and server-reported; missing fields are explicitly absent. Client wall time includes more than model execution.
- Installed names and server metadata may not expose every alias or remote route. Keep server-side cloud disablement as a prerequisite and review the exact model identity in reports.
- No model winner or live speed/quality measurement is claimed. The supplied 77.4 second first-request benchmark remains the user-provided baseline and was not reproduced here.
- M02-D v1 expected cases and facts are unchanged. Cross-workspace aggregation remains unsupported; no database schema, retrieval ranking, GUI, voice, or integration behavior changes are made.
