import test from "node:test";
import assert from "node:assert/strict";
import {
  applyProactivityResponse,
  beginProactivityLoad,
  clearProactivityState,
  createProactivityState,
  proactivityActionAvailable,
} from "../prototypes/m03_b1/proactivity-ui-state.mjs";

test("workspace change fences a pending inbox response and clears old cards", () => {
  const state = createProactivityState();
  const first = beginProactivityLoad(state, "workspace-a");
  assert.equal(applyProactivityResponse(state, first, "workspace-a", {
    workspace_id: "workspace-a",
    preferences: { enabled: true, can_enable: true },
    suggestions: [{ id: "one", workspace_id: "workspace-a", reason_code: "mission_aged", state: "active" }],
  }), true);
  const pending = beginProactivityLoad(state, "workspace-b");
  assert.equal(applyProactivityResponse(state, first, "workspace-a", {
    workspace_id: "workspace-a", preferences: { enabled: true, can_enable: true },
    suggestions: [{ id: "old", workspace_id: "workspace-a", reason_code: "task_blocked", state: "active" }],
  }), false);
  assert.equal(applyProactivityResponse(state, pending, "workspace-b", {
    workspace_id: "workspace-b", preferences: { enabled: false, can_enable: true },
    suggestions: [],
  }), true);
  assert.deepEqual(state.suggestions, []);
  assert.equal(state.workspaceId, "workspace-b");
});

test("logout invalidates pending reads and removes actionable suggestions", () => {
  const state = createProactivityState();
  const pending = beginProactivityLoad(state, "workspace-a");
  clearProactivityState(state);
  assert.equal(applyProactivityResponse(state, pending, "workspace-a", {
    workspace_id: "workspace-a", preferences: { enabled: true, can_enable: true },
    suggestions: [{ id: "one", workspace_id: "workspace-a", reason_code: "mission_aged", state: "active" }],
  }), false);
  assert.equal(proactivityActionAvailable(state, "one"), false);
  assert.equal(state.suggestions.length, 0);
});

test("only active scoped suggestions can receive UI actions", () => {
  const state = createProactivityState();
  const generation = beginProactivityLoad(state, "workspace-a");
  applyProactivityResponse(state, generation, "workspace-a", {
    workspace_id: "workspace-a", preferences: { enabled: true, can_enable: true },
    suggestions: [
      { id: "active", workspace_id: "workspace-a", reason_code: "mission_aged", state: "active" },
      { id: "invalid", workspace_id: "workspace-a", reason_code: "mission_aged", state: "retracted" },
    ],
  });
  assert.equal(proactivityActionAvailable(state, "active"), true);
  assert.equal(proactivityActionAvailable(state, "invalid"), false);
});
