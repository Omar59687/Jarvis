# M02-B1 — First Document-Based RAG Answer

**Status:** Implemented in `feature/m02-b1-document-rag`; awaiting review. This slice adds document-only retrieval-augmented answers over the existing workspace-scoped FTS5 service and the local Ollama adapter.

## Behavior

`DocumentRAG.answer(actor_id, workspace_id, question, model)` calls `SearchIndex.search()` with the explicit actor and workspace. The search service enforces READ permission and excludes deleted documents and revoked roots. The RAG layer does not open SQLite, traverse folders, retrieve memories, or perform a second authorization decision.

At most five results are accepted, with three as the default. The question is limited to 1,024 characters and the complete evidence excerpts to 2,400 characters. The evidence records carry stable request-local labels (`D1`, `D2`, …), document and root IDs, relative source path, and bounded excerpt. The model prompt explicitly labels question/evidence as untrusted and asks the model to ignore instructions embedded in documents. This is prompt-level defense in depth; the model may still fail to follow it. Authorization is enforced by the service boundary, never by prompt text.

Generation uses the explicitly selected model from the Ollama client's allowlist, `think=False`, temperature 0, seed 0, 2,048 context tokens and a 192-token output cap. No model is selected, downloaded, or substituted by this service. It makes no cloud calls. The Ollama deployment itself must retain the local-only controls documented in [M02-A](M02A_OLLAMA_ADAPTER.md).

If authorized search returns no evidence, the service returns a fixed abstention and does not call the model. A non-empty model answer must include at least one exact request-local citation such as `[D1]`; otherwise the service replaces it with a fixed abstention, returns no citations, and retains retrieved evidence metadata for inspection. Incomplete generations (`done` false or not a Boolean true) raise `IncompleteGenerationError` and are not returned as answers. Citations are mapped only to evidence labels issued for that exact request. Unknown labels, malformed or noncanonical bracket references, and citation-like references outside `[D#]` raise `InvalidCitationError`. Citation syntax only points to retrieved text; it does not establish that the answer is factually supported or correct. The service does not independently verify entailment.

Arabic and English text are passed through as Unicode. Retrieval remains the existing exact-token SQLite FTS5 lexical search. Arabic clitic segmentation, stemming, semantic retrieval, and Arabic-English cross-language matching are not provided.

## Offline synthetic demonstration

From the repository root run:

```console
python examples/document_rag_demo.py
```

The demonstration creates a temporary database and one synthetic Markdown file, approves only its temporary directory, synchronizes it, retrieves its excerpt, and returns a fake local-generator answer with `[D1]`. It requires no Ollama server, model download, network access, or personal files. The automated tests use the same fake-transport principle; they do not rely on model behavior.

## Verification

Linux:

```console
python -m compileall -q jarvis_core tests examples
python -m unittest tests.test_rag -v
python -m unittest discover -s tests -v
python examples/document_rag_demo.py
```

Windows PowerShell (Python 3.12):

```powershell
py -3.12 -m compileall -q jarvis_core tests examples
py -3.12 -m unittest tests.test_rag -v
py -3.12 -m unittest discover -s tests -v
py -3.12 examples/document_rag_demo.py
```

The demonstration and automated suite are offline and synthetic on both platforms. A separate live Ollama check is not required for this slice and must be run on the target Windows laptop before claiming local model performance or answer quality.

## Limitations and risks

- Prompt injection defenses are instructions to a probabilistic model, not a hard sandbox. No tools are exposed to the model, and model text cannot change workspace selection or service permissions.
- FTS5 rank/excerpt quality is lexical; citations do not prove factual grounding. Model hallucinations can remain even when citations are syntactically valid.
- Model output validation rejects unknown citation labels but does not prove that each answer sentence is entailed by a cited excerpt.
- Missing citations produce a deterministic service-level abstention; malformed or unsupported source references and incomplete generations are rejected. The strict parser may reject otherwise harmless bracketed notation in model prose.
- The existing M01 cross-workspace consent criterion remains unresolved. This API only searches one explicitly selected workspace and relies on existing permission checks; combined cross-workspace retrieval is unsupported and denied by default.
- M01's trusted-host actor identity is not authentication. No login/session UI exists.
- Existing M01 limitations remain: unencrypted SQLite and backup copies, historical backups potentially retaining purged content, and Windows filesystem traversal races that standard-library path APIs do not eliminate.
- There is no retrieval evaluation corpus or target-laptop latency/resource benchmark in this slice.
