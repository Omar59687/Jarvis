export function createProactivityState() {
  return {
    generation: 0,
    workspaceId: null,
    enabled: false,
    canEnable: false,
    preferences: null,
    suggestions: [],
    status: "idle",
  };
}

export function beginProactivityLoad(state, workspaceId) {
  state.generation += 1;
  state.workspaceId = workspaceId || null;
  state.status = workspaceId ? "loading" : "idle";
  return state.generation;
}

export function applyProactivityResponse(state, generation, workspaceId, payload) {
  if (generation !== state.generation || workspaceId !== state.workspaceId) return false;
  if (!payload || payload.workspace_id !== workspaceId
      || !payload.preferences || !Array.isArray(payload.suggestions)) return false;
  state.enabled = payload.preferences.enabled === true;
  state.canEnable = payload.preferences.can_enable === true;
  state.preferences = { ...payload.preferences };
  state.suggestions = payload.suggestions.filter((item) => item
    && typeof item.id === "string"
    && item.workspace_id === workspaceId
    && ["active", "snoozed"].includes(item.state)
    && typeof item.reason_code === "string");
  state.status = "ready";
  return true;
}

export function clearProactivityState(state, workspaceId = null) {
  state.generation += 1;
  state.workspaceId = workspaceId || null;
  state.enabled = false;
  state.canEnable = false;
  state.preferences = null;
  state.suggestions = [];
  state.status = "idle";
}

export function proactivityActionAvailable(state, suggestionId) {
  return state.status === "ready" && state.canEnable
    && state.suggestions.some((item) => item.id === suggestionId);
}
