# M02-C — Grounding and Context Management

**Status:** Implemented on `feature/m02-c-grounding-context`; awaiting review. M01, M02-A, M02-B1, and M02-B2 are present in the stated `main` baseline.

## Evidence selection

`EvidenceSelector` is a deterministic, standard-library component used by both `DocumentRAG.answer()` and `answer_with_memories()`. It receives results already returned by the authorized `SearchIndex` and `MemoryService` interfaces. It does not access SQLite or change workspace authorization.

- Search order is ranking order and remains stable.
- If both source types have results, each gets 1,200 characters from the existing 2,400-character total. A sole source type may use all 2,400 characters.
- It suppresses duplicates within one source type when their ordered Unicode word-token sequences match after case, whitespace, and punctuation variation. It does not merge document and memory records, or memory records with different provenance status/type. Differences in words, numbers, or negation remain distinct, preserving possible conflicts.
- Excerpts are clipped before prompt construction. The existing search result limits still bound record count.

The duplicate rule is intentionally conservative and lexical. It does not infer paraphrases or semantic equivalence.

## Context and outcomes

Context contains separate untrusted JSON sections for active documents, approved memories with active sources, historical approved memories, and user-authored approved memories. Historical snapshots are identified as memory evidence and are not described as current documents. The prompt asks the model to ignore instructions embedded in evidence, report conflicts rather than silently choose, and return the exact `JARVIS_ABSTAIN` marker when supplied evidence is insufficient.

`RAGResult.outcome` distinguishes:

- `no_authorized_evidence` — authorized search returned no usable evidence; the model is not called.
- `model_abstention` — the model returned the exact abstention marker.
- `missing_citation` — generation completed but no supported citation label was included; a deterministic abstention result is returned.
- `valid_citations` — every returned citation resolves to evidence selected for that request.

`InvalidCitationError.outcome` identifies invalid citations; `IncompleteGenerationError.outcome` identifies incomplete generation. Workspace permission denial continues to raise the existing `AccessDenied` exception before generation. Citation validation establishes reference resolution only; it does not prove that a passage entails a claim or that a claim is true.

The `DocumentRAG.answer()` signature and document-only behavior remain available. Evidence with identical token sequences may now be collapsed within its document result set; the returned evidence and citation identifiers reflect the selected records.

## Verification and diagnostic

Run the offline diagnostic from the repository root:

```console
python examples/m02_c_grounding_demo.py
```

It builds a temporary database and synthetic approved folder, approves one synthetic user-authored memory, and uses a fake generator. It prints the selection rules, each exact selected evidence record and its source status, the complete prompt passed to the fake generator, and the typed result. No Ollama service, network, model download, or personal files are used.

Linux verification:

```console
python -m compileall -q jarvis_core tests examples
python -m unittest tests.test_rag -v
python -m unittest tests.test_memory_search -v
python -m unittest discover -s tests -v
python examples/m02_c_grounding_demo.py
```

Windows PowerShell:

```powershell
py -3.12 -m compileall -q jarvis_core tests examples
py -3.12 -m unittest tests.test_rag -v
py -3.12 -m unittest tests.test_memory_search -v
py -3.12 -m unittest discover -s tests -v
py -3.12 examples/m02_c_grounding_demo.py
```

## Limitations

- The local suite can prove deterministic selection, bounds, permission failures, and citation-label handling; it cannot establish factual entailment or truth.
- Prompt instructions and the abstention marker are not a model security boundary. A model may ignore them or produce unsupported statements with valid citation syntax.
- Lexical retrieval can miss relevant passages and Arabic morphology/cross-language matching remains limited by the existing FTS5 tokenizer.
- Near-duplicate detection handles token-identical formatting variants only; paraphrases and conflicting statements are kept as separate records.
- Cross-workspace retrieval remains unsupported and denied. Existing trusted-host identity and local database protection limitations remain.
- Linux execution does not verify Windows filesystem behavior; run the full regression suite on Windows before merge.
