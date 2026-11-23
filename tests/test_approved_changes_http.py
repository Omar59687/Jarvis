from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from jarvis_core.coding_agents import CodingCapability, CodingCapabilityPolicy
from jarvis_core.developer import DeveloperRepositoryService
from jarvis_core.storage import Database, Permission, WorkspaceKind
from prototypes.m03_b1.service import API_PREFIX, make_server


class FakeProvider:
    def generate(self, prompt, model, **kwargs):
        from jarvis_core.model_provider import GenerationResult
        return GenerationResult(model, "{}", True)


class ApprovedFileChangeHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.db = Database(self.base / "jarvis.sqlite3")
        self.db.initialize()
        self.db.create_workspace("HTTP test", WorkspaceKind.PROJECT, "owner", "ws-http")
        self.root = self.base / "approved"
        self.root.mkdir()
        root_record = self.db.register_approved_root("owner", "ws-http", self.root)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self._git("init", "-q")
        self._git("config", "user.name", "Synthetic")
        self._git("config", "user.email", "synthetic@example.invalid")
        (self.repo / "main.py").write_text("VALUE = 1\\n", encoding="utf-8", newline="")
        self._git("add", "main.py")
        self._git("commit", "-qm", "base")
        repositories = DeveloperRepositoryService(self.db)
        registered = repositories.register("owner", "ws-http", root_record.id,
                                           self.repo, confirmed=True)
        registered = repositories.index("owner", "ws-http", registered.id)
        policy = CodingCapabilityPolicy(self.db)
        for capability in (CodingCapability.READ_REPOSITORY_METADATA,
                           CodingCapability.READ_INDEXED_SOURCE,
                           CodingCapability.PROPOSE_CODE_MODIFICATIONS,
                           CodingCapability.APPLY_APPROVED_FILE_CHANGES):
            policy.grant("owner", "ws-http", registered.id, "owner", capability, confirmed=True)
        self.server = make_server(0, generator=FakeProvider(), model="local-test",
                                  actor_id="owner", database=self.db, pairing_code="test-pair")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        status, headers, body = self.request("POST", f"{API_PREFIX}/session/pair",
                                             {"pairing_code": "test-pair"})
        self.assertEqual(status, 200, body)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.csrf = json.loads(body)["csrf_token"]
        status, _, body = self.request("PUT", f"{API_PREFIX}/session/workspace",
                                       {"workspace_id": "ws-http"})
        self.assertEqual(status, 200, body)
        self.repository_id = registered.id

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def _git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def request(self, method, path, payload=None, *, include_csrf=True):
        headers = {"Host": self.server.expected_host, "Origin": self.server.expected_origin,
                   "Cookie": getattr(self, "cookie", "")}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
            if include_csrf:
                headers["X-Jarvis-CSRF"] = getattr(self, "csrf", "")
        request = Request(f"http://{self.server.expected_host}{path}", data=data,
                          headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except HTTPError as response:
            with response:
                return response.code, response.headers, response.read()

    def test_http_proposal_requires_csrf_and_auth_then_applies_exact_reviewed_diff(self):
        payload = {"repository_id": self.repository_id, "operations": [{
            "path": "main.py", "operation": "modify", "content": "VALUE = 2\\n"
        }]}
        status, _, body = self.request("POST", f"{API_PREFIX}/coding/file-changes",
                                       payload, include_csrf=False)
        self.assertEqual(status, 403)
        self.assertEqual((self.repo / "main.py").read_text(), "VALUE = 1\\n")
        status, _, body = self.request("POST", f"{API_PREFIX}/coding/file-changes", payload)
        self.assertEqual(status, 201, body)
        proposal = json.loads(body)
        status, _, body = self.request("GET", f"{API_PREFIX}/coding/file-changes/{proposal['id']}")
        self.assertEqual(status, 200, body)
        self.assertIn("VALUE = 2", json.loads(body)["diff"])
        status, _, body = self.request(
            "POST", f"{API_PREFIX}/coding/file-changes/{proposal['id']}/approve",
            {"patch_sha256": proposal["patch_sha256"], "confirmed": True})
        self.assertEqual(status, 200, body)
        result = json.loads(body)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["audit"][-1]["event_type"], "file_change_applied")
        status, _, body = self.request("GET", f"{API_PREFIX}/coding/file-changes/{proposal['id']}/output")
        self.assertEqual(status, 200, body)
        output = json.loads(body)
        self.assertFalse(output["source_repository_changed"])
        self.assertEqual(output["files"][0]["content"], "VALUE = 2\\n")
        self.assertEqual((self.repo / "main.py").read_text(), "VALUE = 1\\n")

    def test_model_generation_uses_only_selected_indexed_paths_and_does_not_apply(self):
        provider = self.server.generator
        class ProposedProvider:
            def __init__(self):
                self.calls = []
            def generate(self, prompt, model, **kwargs):
                self.calls.append((prompt, model, kwargs))
                from jarvis_core.model_provider import GenerationResult
                return GenerationResult(model, json.dumps({"changes": [
                    {"path": "main.py", "content": "VALUE = 6\\n"}
                ]}), True)
        provider = ProposedProvider()
        self.server.generator = provider
        status, _, body = self.request("POST", f"{API_PREFIX}/coding/file-changes", {
            "repository_id": self.repository_id,
            "request": "Set VALUE to six",
            "paths": ["main.py"],
        })
        self.assertEqual(status, 201, body)
        proposal = json.loads(body)
        self.assertEqual(proposal["status"], "pending_approval")
        self.assertIn("Set VALUE to six", provider.calls[0][0])
        self.assertEqual((self.repo / "main.py").read_text(), "VALUE = 1\\n")


if __name__ == "__main__":
    unittest.main()
