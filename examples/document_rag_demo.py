"""Run an offline M02-B1 demonstration with one synthetic document.

From the repository root: ``python examples/document_rag_demo.py``
No Ollama service, network, model download, or personal files are used.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis_core import Database, GenerationResult, WorkspaceKind
from jarvis_core.ingestion import FolderSynchronizer
from jarvis_core.ollama import GenerationSettings
from jarvis_core.rag import DocumentRAG
from jarvis_core.retrieval import SearchIndex


class DemoGenerator:
    allowed_models = frozenset({"synthetic-demo:latest"})

    def generate(self, prompt, model, *, system_prompt=None, settings=GenerationSettings()):
        assert settings.think is False
        assert "Jarvis stores approved notes locally" in prompt
        return GenerationResult(
            model=model,
            text="Jarvis stores approved notes locally [D1].",
            done=True,
        )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="jarvis-rag-demo-") as temporary:
        base = Path(temporary)
        database = Database(base / "demo.sqlite3")
        database.initialize()
        workspace = database.create_workspace(
            "Synthetic demo", WorkspaceKind.PERSONAL, "demo-user", "demo-workspace"
        )
        approved_root = base / "approved"
        approved_root.mkdir()
        root = database.register_approved_root(
            "demo-user", workspace.id, approved_root
        )
        (approved_root / "synthetic-note.md").write_text(
            "Jarvis stores approved notes locally and preserves source paths.",
            encoding="utf-8",
        )
        FolderSynchronizer(database).sync("demo-user", workspace.id, root.id)

        rag = DocumentRAG(SearchIndex(database), DemoGenerator())
        result = rag.answer(
            "demo-user",
            workspace.id,
            "Where does Jarvis store approved notes?",
            "synthetic-demo:latest",
        )
        print(result.answer)
        for citation in result.citations:
            print(f"[{citation.citation_id}] {citation.relative_path}")


if __name__ == "__main__":
    main()
