from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jarvis_core.developer import (
    DeveloperRepositoryService,
    StaleRepository,
    _check_git_tree_for_links,
    _path_has_link_component,
)
from jarvis_core.developer_cli import main as developer_cli
from jarvis_core.model_provider import GenerationResult
from jarvis_core.storage import Database, Permission, WorkspaceKind


class _InteractiveInput(io.StringIO):
    def isatty(self) -> bool:
        return True


class _CitationProvider:
    timeout = 1.0

    def generate(self, prompt, model, **kwargs):
        return GenerationResult(model, "The function sums the values [R1].", True)


class P3DeveloperAcceptanceTests(unittest.TestCase):
    """Exercise the trusted-terminal P3 flow with a disposable multi-language Git repo."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.db_path = self.base / "jarvis.sqlite3"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.db.create_workspace("P3 Acceptance", WorkspaceKind.PROJECT, "operator", "ws-p3")
        self.root = self.base / "approved"
        self.root.mkdir()
        self.root_record = self.db.register_approved_root("operator", "ws-p3", self.root)
        self.repo_path = self.root / "synthetic-repo"
        self.repo_path.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "P3 Acceptance")
        self.git("config", "user.email", "p3@example.invalid")
        self.write("src/math_utils.py", (
            "def calculate_total(values):\n"
            "    return sum(values)\n"
        ))
        self.write("src/consumer.py", (
            "from src.math_utils import calculate_total\n\n"
            "def render(values):\n"
            "    return calculate_total(values)\n"
        ))
        self.write("web/app.ts", "export function displayTotal(value: number) { return value; }\n")
        self.write("lib/controller.dart", "class Controller {\n  void start() {}\n}\n")
        self.write("server/Worker.cs", "public class Worker { public int Run() { return 1; } }\n")
        self.write("docs/guide.md", "# P3 Guide\nSee [math](../src/math_utils.py).\n")
        self.write("src/naïve_math.py", "def café():\n    return 7\n")
        self.git("add", ".")
        self.git("commit", "-qm", "initial synthetic repository")
        self.base_commit = self.git_output("rev-parse", "HEAD")
        self.service = DeveloperRepositoryService(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo_path), *args], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def git_output(self, *args):
        return subprocess.run(["git", "-C", str(self.repo_path), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True).stdout.strip()

    def write(self, relative_path: str, content: str):
        path = self.repo_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")

    def call_cli(self, argv: list[str], input_text: str, *, input_hook=None):
        output = io.StringIO()
        errors = io.StringIO()
        def read_input(prompt=""):
            if input_hook is not None:
                input_hook()
            return input_text.rstrip("\n")
        with patch("sys.stdin", _InteractiveInput(input_text)), \
             patch("builtins.input", side_effect=read_input), \
             redirect_stdout(output), redirect_stderr(errors):
            code = developer_cli(["--database", str(self.db_path), *argv])
        return code, output.getvalue(), errors.getvalue()

    def register_through_cli(self) -> tuple[str, str]:
        code, output, errors = self.call_cli([
            "register", "--actor-id", "operator", "--workspace-id", "ws-p3",
            "--approved-root-id", self.root_record.id, "--path", str(self.repo_path),
        ], "REGISTER")
        self.assertEqual(code, 0, errors)
        repository_id = output.split("Registered repository ", 1)[1].split(" ", 1)[0]
        self.assertIn(self.base_commit, output)
        return repository_id, output

    def test_terminal_registration_commit_pinned_index_search_graph_diff_and_dirty_fallback(self):
        repository_id, registration_output = self.register_through_cli()
        self.assertIn("Registered repository", registration_output)

        # A committed edit creates a second valid comparison point. A later dirty
        # edit must remain invisible because P3 indexes Git objects at HEAD only.
        self.write("src/math_utils.py", (
            "def calculate_total(values):\n"
            "    return sum(values) + 1\n"
        ))
        self.git("add", "src/math_utils.py")
        self.git("commit", "-qm", "change total calculation")
        target_commit = self.git_output("rev-parse", "HEAD")
        self.write("src/math_utils.py", "def calculate_total(values):\n    return 999999\n")
        confirmation = f"INDEX {target_commit}"
        code, output, errors = self.call_cli([
            "index", "--actor-id", "operator", "--workspace-id", "ws-p3",
            "--repository-id", repository_id,
        ], confirmation)
        self.assertEqual(code, 0, errors)
        self.assertIn(target_commit, output)
        self.assertIn("dirty and untracked files are excluded", output)

        python_hits = self.service.search("operator", "ws-p3", repository_id,
                                          "calculate_total")
        python_hit = next(item for item in python_hits
                          if item["relative_path"] == "src/math_utils.py")
        self.assertEqual(python_hit["commit_sha"], target_commit)
        self.assertEqual(python_hit["analysis_status"], "complete")
        source = self.service.source("operator", "ws-p3", repository_id,
                                     python_hit["id"], start_line=2, line_count=1)
        self.assertEqual((source["start_line"], source["end_line"]), (2, 2))
        self.assertEqual(source["content"], "    return sum(values) + 1")
        self.assertNotIn("999999", source["content"])
        answer = self.service.answer("operator", "ws-p3", repository_id,
                                     "Where are values summed?", _CitationProvider(), "offline-test")
        self.assertEqual(answer.outcome, "valid_citations")
        citation = answer.citations[0]
        self.assertEqual(citation.commit_sha, target_commit)
        self.assertEqual(citation.relative_path, "src/math_utils.py")
        self.assertEqual((citation.start_line, citation.end_line), (1, 2))
        self.assertIn("def calculate_total", citation.excerpt)

        expectations = {
            "displayTotal": ("web/app.ts", "partial"),
            "Controller": ("lib/controller.dart", "partial"),
            "Worker": ("server/Worker.cs", "partial"),
            "P3 Guide": ("docs/guide.md", "partial"),
            "café": ("src/naïve_math.py", "complete"),
        }
        for term, (path, analysis_status) in expectations.items():
            with self.subTest(term=term):
                hits = self.service.search("operator", "ws-p3", repository_id, term)
                self.assertTrue(hits)
                self.assertEqual(hits[0]["relative_path"], path)
                self.assertEqual(hits[0]["analysis_status"], analysis_status)

        graph = self.service.graph("operator", "ws-p3", repository_id)
        self.assertEqual(graph["commit_sha"], target_commit)
        self.assertTrue(any(edge["type"] == "references" for edge in graph["edges"]))
        changed = self.service.changes("operator", "ws-p3", repository_id, self.base_commit)
        by_path = {item["relative_path"]: item for item in changed["changes"]}
        self.assertEqual(by_path["src/math_utils.py"]["change_type"], "modified")
        self.assertEqual(changed["base_commit"], self.base_commit)
        self.assertEqual(changed["target_commit"], target_commit)

        self.write("src/math_utils.py", "def calculate_total(values):\n    return -5\n")
        self.assertEqual(self.service.list_repositories("operator", "ws-p3")[0].index_status, "ready")
        self.assertEqual(self.service.search("operator", "ws-p3", repository_id,
                                             "calculate_total")[0]["commit_sha"], target_commit)

    def test_exact_commit_confirmation_rejects_head_race(self):
        repository_id, _ = self.register_through_cli()
        preview = self.service.preview_index("operator", "ws-p3", repository_id)

        def advance_head():
            self.write("src/math_utils.py", "def calculate_total(values):\n    return 42\n")
            self.git("add", "src/math_utils.py")
            self.git("commit", "-qm", "advance after confirmation preview")

        code, output, errors = self.call_cli([
            "index", "--actor-id", "operator", "--workspace-id", "ws-p3",
            "--repository-id", repository_id,
        ], f"INDEX {preview['current_commit']}", input_hook=advance_head)
        self.assertEqual(code, 2)
        self.assertIn(preview["current_commit"], output)
        self.assertIn("denied or unavailable", errors)
        with self.db._connection() as connection:
            row = connection.execute(
                "SELECT index_status FROM developer_repositories WHERE id=?", (repository_id,)
            ).fetchone()
        self.assertEqual(row[0], "stale")

    def test_cli_refuses_noninteractive_use_and_redacts_locked_database(self):
        with patch("sys.stdin", io.StringIO("")), redirect_stderr(io.StringIO()) as errors:
            with self.assertRaises(SystemExit) as caught:
                developer_cli(["--database", str(self.db_path), "index", "--actor-id", "operator",
                                "--workspace-id", "ws-p3", "--repository-id", "missing"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("interactive trusted terminal", errors.getvalue())

        with patch("sys.stdin", _InteractiveInput("")), \
             patch("jarvis_core.developer_cli.Database.initialize",
                   side_effect=sqlite3.OperationalError(f"database is locked: {self.db_path}")), \
             redirect_stderr(io.StringIO()) as errors:
            code = developer_cli(["--database", str(self.db_path), "register", "--actor-id", "operator",
                                  "--workspace-id", "ws-p3", "--approved-root-id", self.root_record.id,
                                  "--path", str(self.repo_path)])
        self.assertEqual(code, 2)
        self.assertIn("database is unavailable or locked", errors.getvalue())
        self.assertNotIn(str(self.db_path), errors.getvalue())

    def test_windows_reparse_point_attribute_is_rejected_by_path_guard(self):
        object_root = self.repo_path / ".git" / "objects"
        target = object_root / "redirecting-directory"
        target.mkdir()
        original_lstat = Path.lstat

        def fake_lstat(path: Path):
            info = original_lstat(path)
            if path == target:
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info

        with patch.object(Path, "lstat", fake_lstat):
            self.assertTrue(_path_has_link_component(target, self.repo_path))
            self.assertFalse(_check_git_tree_for_links(object_root))

    def test_replacement_at_registered_path_makes_index_stale(self):
        repository_id, _ = self.register_through_cli()
        code, _, errors = self.call_cli([
            "index", "--actor-id", "operator", "--workspace-id", "ws-p3",
            "--repository-id", repository_id,
        ], f"INDEX {self.base_commit}")
        self.assertEqual(code, 0, errors)

        replacement = self.root / "replacement"
        replacement.mkdir()
        subprocess.run(["git", "-C", str(replacement), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(replacement), "config", "user.name", "Replacement"], check=True)
        subprocess.run(["git", "-C", str(replacement), "config", "user.email", "replacement@example.invalid"], check=True)
        (replacement / "other.py").write_text("def unrelated():\n    return 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(replacement), "add", "."], check=True)
        subprocess.run(["git", "-C", str(replacement), "commit", "-qm", "replacement repo"], check=True)
        moved = self.root / "old-repository"
        self.repo_path.rename(moved)
        replacement.rename(self.repo_path)

        with self.assertRaises(StaleRepository):
            self.service.search("operator", "ws-p3", repository_id, "unrelated")
        self.assertEqual(self.service.list_repositories("operator", "ws-p3")[0].index_status, "stale")


if __name__ == "__main__":
    unittest.main()
