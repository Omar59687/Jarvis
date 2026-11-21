import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  applyTaskDetailResponse,
  applyTaskEventsPageResponse,
  applyTaskListResponse,
  clearTaskControlState,
  createTaskControlState,
  formatEventType,
  formatPlanApprovalState,
  formatSimulatedProgress,
  formatSubtaskState,
  formatSummaryCount,
  formatTaskStatus,
  sanitizeTaskText,
  taskControlError,
} from "../prototypes/m03_b1/task-control-state.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");

function sampleListPayload(workspaceId = "ws-a") {
  return {
    workspace_id: workspaceId,
    read_only: true,
    real_execution_enabled: false,
    summary: {
      total_visible: 3,
      awaiting_approval: 1,
      blocked: 0,
      interrupted: 1,
      interrupted_or_blocked: 1,
      draft: 0,
      cancelled: 0,
      simulated: 1,
      stale: 0,
      window_limit: 100,
      window_truncated: false,
    },
    pagination: {
      limit: 20,
      offset: 0,
      total: 3,
      returned: 1,
      has_more: false,
      status_filter: "all",
      include_history: true,
    },
    tasks: [
      {
        id: "task-1",
        workspace_id: workspaceId,
        goal: "Verify simulated task view",
        state: "simulating",
        display_status: "simulated",
        execution_mode: "simulated",
        is_simulated: true,
        current_plan_revision: 1,
        approved_plan_revision: 1,
      },
    ],
  };
}

test("initial state shows placeholder dash rather than fake summary counts", () => {
  const state = createTaskControlState();
  assert.equal(state.summary, null);
  assert.equal(formatSummaryCount(state.summary?.totalVisible), "—");
  assert.equal(formatSummaryCount(undefined), "—");
  assert.equal(formatSummaryCount(-1), "—");
  assert.equal(formatSummaryCount(0), "0");
  assert.equal(formatSummaryCount(4), "4");
});

test("list and detail responses enforce workspace scoping, read_only flag, and generation freshness", () => {
  const state = createTaskControlState();
  state.generation += 1;
  const gen = state.generation;

  // Cross-workspace payload is rejected
  const wrongWs = applyTaskListResponse(state, {
    generation: gen,
    workspaceId: "ws-a",
    payload: sampleListPayload("ws-b"),
  });
  assert.equal(wrongWs.applied, false);
  assert.equal(state.tasks.length, 0);

  // Non-read-only payload is rejected
  const nonReadOnly = sampleListPayload("ws-a");
  nonReadOnly.read_only = false;
  assert.equal(applyTaskListResponse(state, { generation: gen, workspaceId: "ws-a", payload: nonReadOnly }).applied, false);

  // Valid payload applies cleanly
  const ok = applyTaskListResponse(state, {
    generation: gen,
    workspaceId: "ws-a",
    payload: sampleListPayload("ws-a"),
  });
  assert.equal(ok.applied, true);
  assert.equal(state.summary.totalVisible, 3);
  assert.equal(state.summary.awaitingApproval, 1);
  assert.equal(state.tasks.length, 1);

  // Detail response applies for matching workspace & task
  state.detailGeneration += 1;
  const dGen = state.detailGeneration;
  const detailResult = applyTaskDetailResponse(state, {
    generation: gen,
    detailGeneration: dGen,
    workspaceId: "ws-a",
    taskId: "task-1",
    detailPayload: {
      workspace_id: "ws-a",
      read_only: true,
      task: { id: "task-1", workspace_id: "ws-a", goal: "Goal", state: "simulating", display_status: "simulated" },
    },
    plansPayload: { workspace_id: "ws-a", task_id: "task-1", read_only: true, plans: [] },
    eventsPayload: { workspace_id: "ws-a", task_id: "task-1", read_only: true, events: [{ id: 1, event_type: "task_created" }] },
  });
  assert.equal(detailResult.applied, true);
  assert.equal(state.detail.id, "task-1");

  // Workspace switch clears all sensitive task state and invalidates in-flight responses
  clearTaskControlState(state);
  assert.equal(state.workspaceId, null);
  assert.equal(state.summary, null);
  assert.deepEqual(state.tasks, []);
  assert.equal(state.detail, null);
  assert.deepEqual(state.plans, []);
  assert.deepEqual(state.events, []);
  assert.equal(
    applyTaskListResponse(state, { generation: gen, workspaceId: "ws-a", payload: sampleListPayload("ws-a") }).applied,
    false,
  );
  assert.equal(
    applyTaskEventsPageResponse(state, {
      generation: gen,
      detailGeneration: dGen,
      workspaceId: "ws-a",
      taskId: "task-1",
      eventsPayload: { workspace_id: "ws-a", task_id: "task-1", read_only: true, events: [] },
    }).applied,
    false,
  );
});

test("untrusted task text sanitization strips control and bidi override chars while preserving literal HTML as inert text", () => {
  const payload = "Audit \u202e<script>alert(1)</script>\u202c\x00\x07 line1\r\nline2";
  const multi = sanitizeTaskText(payload, 200);
  assert.equal(multi.includes("\u202e"), false);
  assert.equal(multi.includes("\u202c"), false);
  assert.equal(multi.includes("\x00"), false);
  assert.equal(multi.includes("\x07"), false);
  assert.match(multi, /<script>alert\(1\)<\/script>/);
  assert.equal(sanitizeTaskText("a".repeat(500), 50).length, 50);
  assert.equal(sanitizeTaskText("line1\nline2", 100, { singleLine: true }), "line1 line2");
});

test("simulated progress and status labels are unambiguous in English and Arabic", () => {
  assert.match(formatTaskStatus("simulated", "simulating", "en"), /Simulated/);
  assert.match(formatTaskStatus("simulated", "simulation_completed", "en"), /no real execution/i);
  assert.match(formatTaskStatus("simulated", "simulation_completed", "ar"), /بلا تنفيذ فعلي/);
  assert.match(formatTaskStatus("pending_approval", "awaiting_approval", "en"), /Pending approval/);
  assert.match(formatTaskStatus("pending_approval", "awaiting_approval", "ar"), /بانتظار الموافقة/);
  assert.match(formatSubtaskState("simulated_completed", "en"), /SIMULATED/);
  assert.match(formatSubtaskState("simulated_completed", "ar"), /محاكاة/);
  assert.match(formatSimulatedProgress(50, true, "en"), /50% \(SIMULATED ONLY\)/);
  assert.match(formatSimulatedProgress(50, true, "ar"), /50% \(محاكاة فقط\)/);
  assert.match(formatSimulatedProgress(0, false, "en"), /Plan only/);
  assert.match(formatPlanApprovalState("approved", { isCurrent: true, isApproved: true }, "en"), /simulation only/i);
  assert.match(formatEventType("task_interrupted", "en"), /no auto-resume/i);
  assert.match(taskControlError("manage_permission_required", "en"), /Workspace manager authorization/);
  assert.match(taskControlError("manage_permission_required", "ar"), /مدير مساحة العمل/);
});

test("Task Control Center markup and rendering remain strictly read-only and free of innerHTML", () => {
  const html = fs.readFileSync(path.join(repoRoot, "prototypes", "m03_b1", "index.html"), "utf8");
  const start = html.indexOf('<section id="task-control-center"');
  assert.ok(start > 0, "index.html must include #task-control-center section");
  const end = html.indexOf("</section>", html.indexOf("task-events-pagination", start));
  const section = html.slice(start, end);

  assert.match(section, /READ-ONLY/);
  assert.match(section, /data-en="Task Control Center"/);
  assert.match(section, /data-ar="مركز التحكم بالمهام"/);
  // Ensure no task mutation controls exist inside Task Control Center
  assert.doesNotMatch(section, /\bApprove\b/i);
  assert.doesNotMatch(section, /\bReject\b/i);
  assert.doesNotMatch(section, /\bDelete\b/i);
  assert.doesNotMatch(section, /\bCancel task\b/i);
  assert.doesNotMatch(section, /\bExecute\b/i);
  assert.doesNotMatch(section, /\bResume\b/i);

  const appJs = fs.readFileSync(path.join(repoRoot, "prototypes", "m03_b1", "app.js"), "utf8");
  const ccStart = appJs.indexOf("function setTaskStatusCopy(");
  const ccEnd = appJs.indexOf("function clearEphemeralWorkspaceView(");
  assert.ok(ccStart > 0 && ccEnd > ccStart, "Task Control Center functions must exist in app.js");
  const ccCode = appJs.slice(ccStart, ccEnd);
  assert.doesNotMatch(ccCode, /innerHTML|outerHTML|insertAdjacentHTML/);
  assert.doesNotMatch(ccCode, /method:\s*["'](POST|PUT|DELETE|PATCH)["']/i);
});
