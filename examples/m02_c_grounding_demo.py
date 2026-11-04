"""Offline M02-C diagnostic for selected evidence and exact model context.

Run from the repository root with ``python examples/m02_c_grounding_demo.py``.
Only synthetic files and a temporary SQLite database are used.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis_core import (
    Database,
    DecisionContextIssuer,
    GenerationResult,
    MemoryService,
    WorkspaceKind,
)
from jarvis_core.ingestion import FolderSynchronizer
from jarvis_core.ollama import GenerationSettings
from jarvis_core.rag import DocumentRAG
from jarvis_core.retrieval import SearchIndex


SIGNING_KEY = b"synthetic-m02-c-demo-signing-key-at-least-32-bytes"


class DiagnosticGenerator:
    allowed_models = frozenset({"synthetic-demo:latest"})

    def __init__(self) -> None:
        self.prompt = ""

    def generate(self, prompt, model, *, system_prompt=None, settings=GenerationSettings()):
        self.prompt = prompt
        assert settings.think is False
        return GenerationResult(
            model=model,
            text="The selected records disagree on the year [D1] [M1].",
            done=True,
        )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="jarvis-m02-c-demo-") as temporary:
        base = Path(temporary)
        database = Database(base / "demo.sqlite3")
        database.initialize()
        workspace = database.create_workspace(
            "Synthetic grounding demo", WorkspaceKind.PERSONAL, "demo-user", "demo-workspace"
        )
        approved_folder = base / "approved"
        approved_folder.mkdir()
        root = database.register_approved_root(
            "demo-user", workspace.id, approved_folder
        )
        (approved_folder / "plan-a.md").write_text(
            "launchwindow Current launch window closes in 2030.", encoding="utf-8"
        )
        (approved_folder / "plan-b.md").write_text(
            "LAUNCHWINDOW   Current launch window closes in 2030!", encoding="utf-8"
        )
        FolderSynchronizer(database).sync("demo-user", workspace.id, root.id)

        memories = MemoryService(database, SIGNING_KEY)
        issuer = DecisionContextIssuer(SIGNING_KEY)
        candidate = memories.propose(
            "demo-user", workspace.id,
            "launchwindow An earlier approved note says the window closes in 2031.",
            user_authored=True,
        )
        decision = issuer.issue(
            action="approve_candidate",
            actor_id="demo-user",
            workspace_id=workspace.id,
            resource_id=candidate.id,
            resource_version=candidate.revision,
            content_sha256=candidate.content_sha256,
            evidence_sha256=candidate.evidence_sha256,
        )
        memories.approve("demo-user", workspace.id, candidate.id, decision)

        search_index = SearchIndex(database)
        raw_documents = search_index.search("demo-user", workspace.id, "launchwindow")
        raw_memories = memories.search("demo-user", workspace.id, "launchwindow")
        generator = DiagnosticGenerator()
        rag = DocumentRAG(search_index, generator)
        result = rag.answer_with_memories(
            "demo-user", workspace.id, "launchwindow", "synthetic-demo:latest",
            memory_service=memories,
        )

        print(f"Authorized search returned {len(raw_documents)} documents and {len(raw_memories)} memories.")
        print("Selection policy: ranked results; same ordered word tokens collapse only within one source type.")
        print("When both source types match, the selector allows 1,200 excerpt characters per type (2,400 total).")
        selected_documents = sum(item.source_type == "document" for item in result.evidence)
        if len(raw_documents) > selected_documents:
            print("A document result was suppressed because its ordered word tokens matched a higher-ranked document.")
        print("The memory remains a separate provenance record even when its words overlap a document.")
        print("\nSelected evidence sent to the generator:")
        for item in result.evidence:
            source = item.relative_path if item.source_type == "document" else item.memory_id
            print(
                f"[{item.citation_id}] type={item.source_type} status={item.source_status} "
                f"source={source} chars={len(item.excerpt)} text={item.excerpt!r}"
            )
        print("\nExact fake-generator prompt:")
        print(generator.prompt)
        print("\nResult:")
        print(f"outcome={result.outcome.value} abstained={result.abstained}")
        print(result.answer)


if __name__ == "__main__":
    main()
