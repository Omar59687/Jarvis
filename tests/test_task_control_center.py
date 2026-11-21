from __future__ import annotations

import json
import logging
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from jarvis_core import Database, GenerationResult, Permission, WorkspaceKind
from jarvis_core.coding_agents import CodingCapability, CodingCapabilityPolicy
from jarvis_core.developer import DeveloperRepositoryService
from jarvis_core.orchestration import (
    DeterministicFakePlanner,
    DeterministicFakeWorker,
    PlannedSubtask,
    SimulationStep,
    TaskOrchestrationService,
    TaskPlanProposal,
)
from prototypes.m03_b1.service import (
    API_PREFIX,
    SESSION_IDLE_SECONDS,
    SessionStore,
    make_server,
)


class FakeProvider:
    def generate(self, prompt, model, *, system_prompt=None, temperature=0.3,
                 max_output_tokens=192):
        return GenerationResult(model, "ok", True)


def _two_step_planner() -> DeterministicFakePlanner:
    return DeterministicFakePlanner(
        TaskPlanProposal((
            PlannedSubtask(
                "s1",
                "Inspect schema",
                "Review persisted orchestration tables for read-only dashboard.",
                "Schema contract documented.",
                (),
                (),
            ),
            PlannedSubtask(
                "s2",
                "Verify UI rendering",
                "Check sanitized read-only display and pagination.",
                "Dashboard renders simulated status safely.",
                (),
                ("s1",),
            ),
        ))
    )


def _two_step_worker() -> DeterministicFakeWorker:
    return DeterministicFakeWorker({
        "s1": (
            SimulationStep(50, "SIMULATION ONLY: schema check halfway.", False),
            SimulationStep(100, "SIMULATION ONLY: schema check complete.", True),
        ),
        "s2": (
            SimulationStep(100, "SIMULATION ONLY: UI check complete.", True),
        ),
    })


class TaskControlCenterAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.db_path = self.base / "jarvis.sqlite3"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.db.create_workspace("Alpha Ops", WorkspaceKind.PROJECT, "alice", "ws-a")
        self.db.create_workspace("Beta Ops", WorkspaceKind.PROJECT, "alice", "ws-b")
        self.db.create_workspace("Bob Private", WorkspaceKind.PERSONAL, "bob", "ws-bob")
        self.db.grant_permission("alice", "ws-a", "alice", Permission.WRITE)
        self.db.grant_permission("alice", "ws-a", "alice", Permission.MANAGE_WORKSPACE)
        self.db.grant_permission("alice", "ws-b", "alice", Permission.WRITE)
        self.db.grant_permission("alice", "ws-b", "alice", Permission.MANAGE_WORKSPACE)
        self.db.grant_permission("bob", "ws-bob", "bob", Permission.WRITE)
        self.db.grant_permission("bob", "ws-bob", "bob", Permission.MANAGE_WORKSPACE)

        self.orch = TaskOrchestrationService(self.db)
        self.now = [1000.0]
        self.server = make_server(
            0,
            generator=FakeProvider(),
            model="test-model",
            actor_id="alice",
            database=self.db,
            pairing_code="pair-secret",
        )
        self.server.sessions = SessionStore(clock=lambda: self.now[0])
        self.server._test_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server._test_thread.start()

        status, headers, body = self.request(
            "POST", f"{API_PREFIX}/session/pair", {"pairing_code": "pair-secret"},
            include_cookie=False, include_csrf=False,
        )
        self.assertEqual(status, 200, body)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.csrf = json.loads(body)["csrf_token"]
        status, _, body = self.request("PUT", f"{API_PREFIX}/session/workspace", {"workspace_id": "ws-a"})
        self.assertEqual(status, 200, body)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server._test_thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method: str, path: str, payload=None, *,
                include_cookie: bool = True, include_csrf: bool = True):
        headers = {
            "Host": self.server.expected_host,
            "Origin": self.server.expected_origin,
        }
        if include_cookie and hasattr(self, "cookie"):
            headers["Cookie"] = self.cookie
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
            if include_csrf and hasattr(self, "csrf"):
                headers["X-Jarvis-CSRF"] = self.csrf
        elif method in {"DELETE", "PUT", "PATCH"} and include_csrf and hasattr(self, "csrf"):
            headers["X-Jarvis-CSRF"] = self.csrf
        req = Request(f"http://{self.server.expected_host}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, response.headers, response.read()
        except HTTPError as response:
            with response:
                return response.code, response.headers, response.read()

    def _seed_workspace_a_tasks(self) -> dict[str, str]:
        planner = _two_step_planner()
        worker = _two_step_worker()

        draft = self.orch.create_task("alice", "ws-a", "Draft audit plan", idempotency_key="seed_draft_key_0001")

        pending = self.orch.create_task("alice", "ws-a", "Pending review plan", idempotency_key="seed_pending_key_0001")
        self.orch.propose_plan("alice", pending.id, planner, idempotency_key="seed_pending_plan_0001")

        simulated = self.orch.create_task("alice", "ws-a", "Simulated rollout verification", idempotency_key="seed_sim_task_key_0001")
        sim_plan = self.orch.propose_plan("alice", simulated.id, planner, idempotency_key="seed_sim_plan_key_0001")
        self.orch.approve_plan(
            "alice", simulated.id, sim_plan.revision, sim_plan.plan_sha256,
            idempotency_key="seed_sim_approve_key_0001",
            decision_id="decision_secret_receipt_0001",
        )
        self.orch.simulate_next_step("alice", simulated.id, worker, idempotency_key="seed_sim_step_key_0001")

        interrupted = self.orch.create_task("alice", "ws-a", "Interrupted check", idempotency_key="seed_int_task_key_0001")
        int_plan = self.orch.propose_plan("alice", interrupted.id, planner, idempotency_key="seed_int_plan_key_0001")
        self.orch.approve_plan(
            "alice", interrupted.id, int_plan.revision, int_plan.plan_sha256,
            idempotency_key="seed_int_approve_key_0001",
        )
        self.orch.recover_after_restart("alice", "ws-a", idempotency_key_prefix="seed_recover_prefix_0001")
        # Resume simulated task so simulated stays simulating and interrupted stays interrupted.
        self.orch.resume_simulation("alice", simulated.id, idempotency_key="seed_sim_resume_key_0001")

        cancelled = self.orch.create_task("alice", "ws-a", "Cancelled exploration", idempotency_key="seed_cancel_key_0001")
        self.orch.cancel_task("alice", cancelled.id, idempotency_key="seed_cancel_act_key_0001")

        return {
            "draft": draft.id,
            "pending": pending.id,
            "simulated": simulated.id,
            "interrupted": interrupted.id,
            "cancelled": cancelled.id,
        }

    def test_authorized_listing_summary_detail_plans_and_events(self):
        ids = self._seed_workspace_a_tasks()

        status, headers, body = self.request("GET", f"{API_PREFIX}/tasks")
        self.assertEqual(status, 200, body)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        data = json.loads(body)
        self.assertEqual(data["workspace_id"], "ws-a")
        self.assertTrue(data["read_only"])
        self.assertFalse(data["real_execution_enabled"])
        summary = data["summary"]
        self.assertEqual(summary["total_visible"], 5)
        self.assertEqual(summary["draft"], 1)
        self.assertEqual(summary["awaiting_approval"], 1)
        self.assertEqual(summary["simulated"], 1)
        self.assertEqual(summary["interrupted"], 1)
        self.assertEqual(summary["cancelled"], 1)
        self.assertEqual(summary["interrupted_or_blocked"], 1)
        self.assertFalse(summary["window_truncated"])

        # Status filter + include_history=false
        status, _, body = self.request("GET", f"{API_PREFIX}/tasks?status=pending_approval")
        self.assertEqual(status, 200)
        filtered = json.loads(body)
        self.assertEqual([t["id"] for t in filtered["tasks"]], [ids["pending"]])
        self.assertEqual(filtered["tasks"][0]["display_status"], "pending_approval")
        self.assertTrue(filtered["tasks"][0]["has_pending_approval"])

        status, _, body = self.request("GET", f"{API_PREFIX}/tasks?include_history=false")
        self.assertEqual(status, 200)
        active_only = json.loads(body)
        self.assertEqual(active_only["summary"]["total_visible"], 4)
        self.assertNotIn(ids["cancelled"], [t["id"] for t in active_only["tasks"]])

        # Single task detail
        status, _, body = self.request("GET", f"{API_PREFIX}/tasks/{ids['simulated']}")
        self.assertEqual(status, 200, body)
        detail = json.loads(body)
        self.assertTrue(detail["read_only"])
        task = detail["task"]
        self.assertEqual(task["id"], ids["simulated"])
        self.assertEqual(task["display_status"], "simulated")
        self.assertTrue(task["is_simulated"])
        self.assertFalse(task["real_execution_enabled"])
        self.assertFalse(task["capability_controls"]["execution_enabled"])
        self.assertFalse(task["capability_controls"]["file_write_enabled"])
        self.assertFalse(task["capability_controls"]["process_execution_enabled"])
        self.assertFalse(task["capability_controls"]["network_enabled"])
        self.assertTrue(task["simulated_progress_summary"]["simulated_only"])
        self.assertEqual(task["simulated_progress_summary"]["total_subtasks"], 2)

        # Plans, subtasks, and dependencies
        status, _, body = self.request("GET", f"{API_PREFIX}/tasks/{ids['simulated']}/plans")
        self.assertEqual(status, 200, body)
        plans_payload = json.loads(body)
        self.assertEqual(plans_payload["task_id"], ids["simulated"])
        self.assertEqual(len(plans_payload["plans"]), 1)
        plan = plans_payload["plans"][0]
        self.assertEqual(plan["revision"], 1)
        self.assertEqual(plan["approval_state"], "approved")
        self.assertTrue(plan["is_approved_revision"])
        self.assertEqual(plan["dependencies"], [{"subtask_id": "s2", "depends_on": "s1"}])
        self.assertEqual(plan["subtasks"][0]["progress_percent"], 50)
        self.assertTrue(plan["subtasks"][0]["is_simulated"])

        # Specific revision query
        status, _, body = self.request("GET", f"{API_PREFIX}/tasks/{ids['simulated']}/plans?revision=1")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["plans"]), 1)

        # Events (must strip actor_id, idempotency_key, decision_id)
        status, _, body = self.request("GET", f"{API_PREFIX}/tasks/{ids['simulated']}/events")
        self.assertEqual(status, 200, body)
        events_text = body.decode("utf-8")
        self.assertNotIn("decision_secret_receipt_0001", events_text)
        self.assertNotIn("seed_sim_approve_key_0001", events_text)
        self.assertNotIn("idempotency_key", events_text)
        self.assertNotIn("actor_id", events_text)
        events_payload = json.loads(body)
        self.assertGreaterEqual(len(events_payload["events"]), 4)
        self.assertTrue(any(ev["simulated_only"] for ev in events_payload["events"]))

    def test_unpaired_and_no_workspace_are_denied(self):
        ids = self._seed_workspace_a_tasks()
        for endpoint in (
            f"{API_PREFIX}/tasks",
            f"{API_PREFIX}/tasks/{ids['draft']}",
            f"{API_PREFIX}/tasks/{ids['draft']}/plans",
            f"{API_PREFIX}/tasks/{ids['draft']}/events",
        ):
            status, _, body = self.request("GET", endpoint, include_cookie=False)
            self.assertEqual(status, 401, endpoint)
            self.assertEqual(json.loads(body), {"error": "authentication_required"})

        # Clear active workspace -> 409 workspace_selection_required
        status, _, _ = self.request("DELETE", f"{API_PREFIX}/session/workspace")
        self.assertEqual(status, 200)
        status, _, body = self.request("GET", f"{API_PREFIX}/tasks")
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body), {"error": "workspace_selection_required"})

    def test_non_manager_actor_is_denied_manage_permission(self):
        self.db.grant_permission("alice", "ws-a", "viewer", Permission.READ)
        self.db.grant_permission("alice", "ws-a", "viewer", Permission.WRITE)
        self.orch.create_task("viewer", "ws-a", "Viewer authored task", idempotency_key="viewer_task_key_0001")

        viewer_server = make_server(
            0,
            generator=FakeProvider(),
            model="test-model",
            actor_id="viewer",
            database=self.db,
            pairing_code="viewer-pair",
        )
        thread = threading.Thread(target=viewer_server.serve_forever, daemon=True)
        thread.start()
        try:
            def vreq(method, path, payload=None, cookie="", csrf=""):
                headers = {"Host": viewer_server.expected_host, "Origin": viewer_server.expected_origin}
                if cookie:
                    headers["Cookie"] = cookie
                data = None
                if payload is not None:
                    data = json.dumps(payload).encode("utf-8")
                    headers["Content-Type"] = "application/json"
                    if csrf:
                        headers["X-Jarvis-CSRF"] = csrf
                req = Request(f"http://{viewer_server.expected_host}{path}", data=data, headers=headers, method=method)
                try:
                    with urlopen(req, timeout=5) as resp:
                        return resp.status, resp.headers, resp.read()
                except HTTPError as resp:
                    with resp:
                        return resp.code, resp.headers, resp.read()

            st, hdrs, body = vreq("POST", f"{API_PREFIX}/session/pair", {"pairing_code": "viewer-pair"})
            self.assertEqual(st, 200)
            vcookie = hdrs["Set-Cookie"].split(";", 1)[0]
            vcsrf = json.loads(body)["csrf_token"]
            st, _, _ = vreq("PUT", f"{API_PREFIX}/session/workspace", {"workspace_id": "ws-a"}, vcookie, vcsrf)
            self.assertEqual(st, 200)
            st, _, body = vreq("GET", f"{API_PREFIX}/tasks", cookie=vcookie)
            self.assertEqual(st, 403)
            self.assertEqual(json.loads(body), {"error": "manage_permission_required"})
        finally:
            viewer_server.shutdown()
            viewer_server.server_close()
            thread.join(timeout=2)

    def test_cross_workspace_task_disclosure_is_denied_even_when_actor_manages_both(self):
        task_b = self.orch.create_task("alice", "ws-b", "Workspace B secret goal", idempotency_key="wsb_task_key_0001")
        task_bob = self.orch.create_task("bob", "ws-bob", "Bob private goal", idempotency_key="wsbob_task_key_0001")
        self.orch.create_task("alice", "ws-a", "Workspace A visible goal", idempotency_key="wsa_task_key_0001")

        # List in ws-a must not contain ws-b or ws-bob tasks
        status, _, body = self.request("GET", f"{API_PREFIX}/tasks")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertIn("Workspace A visible goal", text)
        self.assertNotIn("Workspace B secret goal", text)
        self.assertNotIn("Bob private goal", text)

        # Direct task/plans/events probes across workspaces must all return 404 task_unavailable
        for foreign_id in (task_b.id, task_bob.id, "nonexistenttaskid123"):
            for suffix in ("", "/plans", "/events"):
                status, _, body = self.request("GET", f"{API_PREFIX}/tasks/{foreign_id}{suffix}")
                self.assertEqual(status, 404, (foreign_id, suffix))
                self.assertEqual(json.loads(body), {"error": "task_unavailable"})

    def test_empty_workspace_returns_zero_summary_and_empty_list(self):
        status, _, body = self.request("GET", f"{API_PREFIX}/tasks")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["tasks"], [])
        self.assertEqual(data["summary"]["total_visible"], 0)
        self.assertEqual(data["summary"]["awaiting_approval"], 0)
        self.assertEqual(data["summary"]["interrupted_or_blocked"], 0)
        self.assertFalse(data["pagination"]["has_more"])

    def test_permission_revocation_and_session_expiry_are_enforced_immediately(self):
        ids = self._seed_workspace_a_tasks()
        # Add a second manager so alice's MANAGE_WORKSPACE can be revoked cleanly
        self.db.grant_permission("alice", "ws-a", "co_manager", Permission.READ)
        self.db.grant_permission("alice", "ws-a", "co_manager", Permission.MANAGE_WORKSPACE)

        # Revoke MANAGE_WORKSPACE mid-session -> 403 immediately
        self.db.revoke_permission("co_manager", "ws-a", "alice", Permission.MANAGE_WORKSPACE)
        status, _, body = self.request("GET", f"{API_PREFIX}/tasks")
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body), {"error": "manage_permission_required"})
        status, _, body = self.request("GET", f"{API_PREFIX}/tasks/{ids['draft']}")
        self.assertEqual(status, 403)

        # Revoke READ mid-session -> 404 workspace_unavailable and clears active workspace
        self.db.revoke_permission("co_manager", "ws-a", "alice", Permission.READ)
        status, _, body = self.request("GET", f"{API_PREFIX}/tasks")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body), {"error": "workspace_unavailable"})
        status, _, body = self.request("GET", f"{API_PREFIX}/session")
        self.assertEqual(status, 200)
        self.assertIsNone(json.loads(body)["active_workspace_id"])

        # Advance fake clock past idle session expiry -> 401
        self.now[0] += SESSION_IDLE_SECONDS + 5
        status, _, body = self.request("GET", f"{API_PREFIX}/tasks")
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body), {"error": "authentication_required"})

    def test_read_only_enforcement_rejects_all_mutation_methods_and_routes(self):
        ids = self._seed_workspace_a_tasks()
        task_id = ids["pending"]
        before = self.orch.get_task("alice", task_id)

        for method, path, payload in (
            ("POST", f"{API_PREFIX}/tasks", {"goal": "create"}),
            ("POST", f"{API_PREFIX}/tasks/{task_id}/approve", {"revision": 1}),
            ("POST", f"{API_PREFIX}/tasks/{task_id}/cancel", {}),
            ("POST", f"{API_PREFIX}/tasks/{task_id}/simulate", {}),
            ("PUT", f"{API_PREFIX}/tasks/{task_id}", {"state": "cancelled"}),
            ("DELETE", f"{API_PREFIX}/tasks/{task_id}", None),
            ("DELETE", f"{API_PREFIX}/tasks", None),
            ("PATCH", f"{API_PREFIX}/tasks/{task_id}", {"state": "queued"}),
            ("OPTIONS", f"{API_PREFIX}/tasks", None),
            ("HEAD", f"{API_PREFIX}/tasks", None),
        ):
            status, headers, body = self.request(method, path, payload)
            self.assertEqual(status, 405, (method, path))
            if method in {"POST", "PUT", "DELETE"}:
                self.assertEqual(headers.get("Allow"), "GET", (method, path))
            if method != "HEAD":
                self.assertEqual(json.loads(body)["error"], "method_not_allowed")

        after = self.orch.get_task("alice", task_id)
        self.assertEqual(after.state, before.state)
        self.assertEqual(after.updated_at, before.updated_at)

    def test_pagination_bounds_and_invalid_or_identity_query_parameters(self):
        for i in range(5):
            self.orch.create_task("alice", "ws-a", f"Paginated task {i}", idempotency_key=f"page_task_key_{i:04d}")

        status, _, body = self.request("GET", f"{API_PREFIX}/tasks?limit=2&offset=0")
        self.assertEqual(status, 200)
        page1 = json.loads(body)
        self.assertEqual(len(page1["tasks"]), 2)
        self.assertTrue(page1["pagination"]["has_more"])
        self.assertEqual(page1["pagination"]["total"], 5)

        status, _, body = self.request("GET", f"{API_PREFIX}/tasks?limit=2&offset=4")
        self.assertEqual(status, 200)
        page3 = json.loads(body)
        self.assertEqual(len(page3["tasks"]), 1)
        self.assertFalse(page3["pagination"]["has_more"])

        # Event pagination
        task_id = page1["tasks"][0]["id"]
        planner = _two_step_planner()
        plan = self.orch.propose_plan("alice", task_id, planner, idempotency_key="ev_page_plan_key_0001")
        self.orch.approve_plan("alice", task_id, plan.revision, plan.plan_sha256, idempotency_key="ev_page_app_key_0001")
        status, _, body = self.request("GET", f"{API_PREFIX}/tasks/{task_id}/events?limit=2&offset=0")
        self.assertEqual(status, 200)
        ev_page = json.loads(body)
        self.assertEqual(len(ev_page["events"]), 2)
        self.assertTrue(ev_page["pagination"]["has_more"])

        # Invalid/out-of-bounds/client-identity query parameters must return 400 invalid_query
        for bad_path in (
            f"{API_PREFIX}/tasks?limit=0",
            f"{API_PREFIX}/tasks?limit=51",
            f"{API_PREFIX}/tasks?offset=100",
            f"{API_PREFIX}/tasks?status=running",
            f"{API_PREFIX}/tasks?include_history=yes",
            f"{API_PREFIX}/tasks?actor_id=bob",
            f"{API_PREFIX}/tasks?workspace_id=ws-b",
            f"{API_PREFIX}/tasks/{task_id}?actor_id=bob",
            f"{API_PREFIX}/tasks/{task_id}/plans?limit=21",
            f"{API_PREFIX}/tasks/{task_id}/events?limit=101",
            f"{API_PREFIX}/tasks/{task_id}/events?offset=401",
        ):
            status, _, body = self.request("GET", bad_path)
            self.assertEqual(status, 400, bad_path)
            self.assertEqual(json.loads(body), {"error": "invalid_query"})

    def test_untrusted_text_sanitization_and_no_sensitive_leaks_in_responses_or_logs(self):
        xss_goal = "Review <img src=x onerror=alert(1)> \u202eSECRET_GOAL_TEXT_991\u202c report"
        task = self.orch.create_task("alice", "ws-a", xss_goal, idempotency_key="xss_task_key_0001")

        with self.assertLogs("prototypes.m03_b1.service", level=logging.DEBUG) as logs:
            logging.getLogger("prototypes.m03_b1.service").debug("probe")
            status, _, body = self.request("GET", f"{API_PREFIX}/tasks/{task.id}")

        self.assertEqual(status, 200)
        raw = body.decode("utf-8")
        payload = json.loads(body)
        # Bidi override chars stripped; HTML stays plain JSON text; no filesystem paths leaked.
        self.assertNotIn("\u202e", payload["task"]["goal"])
        self.assertNotIn("\u202c", payload["task"]["goal"])
        self.assertIn("<img src=x onerror=alert(1)>", payload["task"]["goal"])
        self.assertNotIn(str(self.base), raw)
        self.assertNotIn(str(self.db_path), raw)
        self.assertNotIn("principal_id", payload["task"])
        self.assertNotIn("actor_id", payload["task"])
        joined_logs = "\n".join(logs.output)
        self.assertNotIn("SECRET_GOAL_TEXT_991", joined_logs)

    def test_state_changes_between_requests_and_corrupt_evidence_fails_closed(self):
        task = self.orch.create_task("alice", "ws-a", "Dynamic state task", idempotency_key="dyn_task_key_0001")
        plan = self.orch.propose_plan("alice", task.id, _two_step_planner(), idempotency_key="dyn_plan_key_0001")

        status, _, body = self.request("GET", f"{API_PREFIX}/tasks")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["tasks"][0]["display_status"], "pending_approval")

        # Mutate via backend service between list and detail reads
        self.orch.approve_plan("alice", task.id, plan.revision, plan.plan_sha256, idempotency_key="dyn_app_key_0001")
        self.orch.simulate_next_step("alice", task.id, _two_step_worker(), idempotency_key="dyn_sim_key_0001")

        status, _, body = self.request("GET", f"{API_PREFIX}/tasks/{task.id}")
        self.assertEqual(status, 200)
        detail = json.loads(body)["task"]
        self.assertEqual(detail["display_status"], "simulated")
        self.assertEqual(detail["state"], "simulating")

        # Tamper with plan digest in SQLite -> fail closed with 503 task_evidence_corrupt
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("UPDATE orchestration_plans SET plan_sha256=? WHERE task_id=?", ("0" * 64, task.id))
            conn.commit()
        finally:
            conn.close()

        status, _, body = self.request("GET", f"{API_PREFIX}/tasks/{task.id}/plans")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {"error": "task_evidence_corrupt"})

    def test_blocked_repository_task_reports_blocked_status_and_capabilities_without_paths(self):
        approved_root = self.base / "approved_repos"
        approved_root.mkdir()
        repo_path = approved_root / "sample_repo"
        repo_path.mkdir()
        subprocess.run(["git", "init", "-q", str(repo_path)], check=True)
        subprocess.run(["git", "-C", str(repo_path), "config", "user.name", "Synthetic"], check=True)
        subprocess.run(["git", "-C", str(repo_path), "config", "user.email", "synthetic@example.invalid"], check=True)
        (repo_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo_path), "add", "app.py"], check=True)
        subprocess.run(["git", "-C", str(repo_path), "commit", "-qm", "initial"], check=True)

        root_rec = self.db.register_approved_root("alice", "ws-a", approved_root)
        repos = DeveloperRepositoryService(self.db)
        reg = repos.register("alice", "ws-a", root_rec.id, repo_path, confirmed=True)
        repo = repos.index("alice", "ws-a", reg.id)
        policy = CodingCapabilityPolicy(self.db)
        policy.grant("alice", "ws-a", repo.id, "alice", CodingCapability.READ_REPOSITORY_METADATA, confirmed=True)
        src_grant = policy.grant("alice", "ws-a", repo.id, "alice", CodingCapability.READ_INDEXED_SOURCE, confirmed=True)

        service = TaskOrchestrationService(self.db, repos, policy)
        task = service.create_task(
            "alice", "ws-a", "Inspect repository source safely",
            repository_id=repo.id,
            requested_capabilities=(CodingCapability.READ_INDEXED_SOURCE.value,),
            idempotency_key="blocked_repo_task_0001",
        )
        plan = service.propose_plan(
            "alice", task.id,
            DeterministicFakePlanner(TaskPlanProposal((
                PlannedSubtask(
                    "read_src", "Read source", "Inspect indexed app.py", "Source reviewed",
                    required_capabilities=(CodingCapability.READ_INDEXED_SOURCE.value,),
                ),
            ))),
            idempotency_key="blocked_repo_plan_0001",
        )
        service.approve_plan("alice", task.id, plan.revision, plan.plan_sha256, idempotency_key="blocked_repo_app_0001")
        policy.revoke("alice", src_grant.id, confirmed=True)
        worker = DeterministicFakeWorker({"read_src": (SimulationStep(100, "SIMULATED", True),)})
        service.simulate_next_step("alice", task.id, worker, idempotency_key="blocked_repo_step_0001")

        status, _, body = self.request("GET", f"{API_PREFIX}/tasks?status=blocked")
        self.assertEqual(status, 200)
        listed = json.loads(body)
        self.assertEqual(listed["summary"]["blocked"], 1)
        self.assertEqual(listed["summary"]["interrupted_or_blocked"], 1)
        self.assertEqual([t["id"] for t in listed["tasks"]], [task.id])
        self.assertNotIn(str(repo_path), body.decode("utf-8"))

        status, _, body = self.request("GET", f"{API_PREFIX}/tasks/{task.id}")
        self.assertEqual(status, 200)
        detail = json.loads(body)["task"]
        self.assertEqual(detail["display_status"], "blocked")
        self.assertEqual(detail["capability_controls"]["blocked"], [CodingCapability.READ_INDEXED_SOURCE.value])
        self.assertNotIn(str(repo_path), body.decode("utf-8"))
        self.assertNotIn(str(approved_root), body.decode("utf-8"))

    def test_static_task_control_state_module_is_served_with_security_headers(self):
        status, headers, body = self.request("GET", "/task-control-state.mjs", include_cookie=False)
        self.assertEqual(status, 200)
        self.assertIn("text/javascript", headers["Content-Type"])
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertIn(b"applyTaskListResponse", body)


if __name__ == "__main__":
    unittest.main()
