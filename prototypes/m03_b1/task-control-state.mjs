const BIDI_AND_CONTROL_RE = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u00ad\u061c\u200b\u200e\u200f\u2028-\u202e\u2060-\u2069\ufeff]/g;

export const TASK_LIST_LIMIT = 20;
export const TASK_PLANS_LIMIT = 10;
export const TASK_EVENTS_LIMIT = 25;

export const ALLOWED_STATUS_FILTERS = Object.freeze([
  "all",
  "draft",
  "pending_approval",
  "blocked",
  "interrupted",
  "cancelled",
  "simulated",
  "stale",
]);

export function sanitizeTaskText(value, maxChars = 400, { singleLine = false } = {}) {
  if (typeof value !== "string") return "";
  let text = value.replace(BIDI_AND_CONTROL_RE, "");
  if (singleLine) {
    text = text.replace(/[\r\n\t]+/g, " ").replace(/\s+/g, " ").trim();
  } else {
    text = text.replace(/\r\n?/g, "\n").replace(/\t/g, " ").trim();
  }
  return text.length > maxChars ? text.slice(0, maxChars) : text;
}

export function createTaskControlState() {
  return {
    workspaceId: null,
    generation: 0,
    detailGeneration: 0,
    statusFilter: "all",
    includeHistory: true,
    listOffset: 0,
    summary: null,
    pagination: null,
    tasks: [],
    selectedTaskId: null,
    detail: null,
    plans: [],
    plansPagination: null,
    events: [],
    eventsPagination: null,
    eventsOffset: 0,
    errorCode: null,
    detailErrorCode: null,
    loadingList: false,
    loadingDetail: false,
  };
}

export function clearTaskDetailState(state, { detailErrorCode = null } = {}) {
  state.detailGeneration += 1;
  state.selectedTaskId = null;
  state.detail = null;
  state.plans = [];
  state.plansPagination = null;
  state.events = [];
  state.eventsPagination = null;
  state.eventsOffset = 0;
  state.detailErrorCode = detailErrorCode;
  state.loadingDetail = false;
  return state;
}

export function clearTaskControlState(state, { preserveFilter = false } = {}) {
  state.generation += 1;
  state.workspaceId = null;
  if (!preserveFilter) {
    state.statusFilter = "all";
    state.includeHistory = true;
  }
  state.listOffset = 0;
  state.summary = null;
  state.pagination = null;
  state.tasks = [];
  state.errorCode = null;
  state.loadingList = false;
  clearTaskDetailState(state);
  return state;
}

export function beginTaskListLoad(state, { offset = 0 } = {}) {
  state.generation += 1;
  // Superseding the list generation invalidates any in-flight detail/event fetch
  // so it cannot strand loadingDetail=true or overwrite newer UI state.
  if (state.loadingDetail) {
    state.detailGeneration += 1;
    state.loadingDetail = false;
    if (!state.detail) {
      state.selectedTaskId = null;
    }
  }
  state.listOffset = Math.max(0, offset);
  state.loadingList = true;
  return state.generation;
}

export function beginTaskDetailLoad(state, taskId) {
  state.detailGeneration += 1;
  state.selectedTaskId = taskId;
  state.eventsOffset = 0;
  state.detailErrorCode = null;
  state.loadingDetail = true;
  return { generation: state.generation, detailGeneration: state.detailGeneration };
}

function nonNegativeInt(value) {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

export function normalizeTaskSummary(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const totalVisible = nonNegativeInt(raw.total_visible);
  const awaitingApproval = nonNegativeInt(raw.awaiting_approval);
  const blocked = nonNegativeInt(raw.blocked);
  const interrupted = nonNegativeInt(raw.interrupted);
  const interruptedOrBlocked = nonNegativeInt(raw.interrupted_or_blocked);
  if (totalVisible === null || awaitingApproval === null
      || blocked === null || interrupted === null || interruptedOrBlocked === null) {
    return null;
  }
  return {
    totalVisible,
    awaitingApproval,
    blocked,
    interrupted,
    interruptedOrBlocked,
    draft: nonNegativeInt(raw.draft) ?? 0,
    cancelled: nonNegativeInt(raw.cancelled) ?? 0,
    simulated: nonNegativeInt(raw.simulated) ?? 0,
    stale: nonNegativeInt(raw.stale) ?? 0,
    windowLimit: nonNegativeInt(raw.window_limit) ?? 100,
    windowTruncated: Boolean(raw.window_truncated),
  };
}

export function formatSummaryCount(value) {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? String(value) : "—";
}

export function applyTaskListResponse(
  state,
  { generation, workspaceId, payload, clearMissingDetail = false },
) {
  if (generation !== state.generation || !workspaceId || !payload || typeof payload !== "object") {
    return { applied: false, reason: "stale_or_invalid" };
  }
  if (payload.workspace_id !== workspaceId || payload.read_only !== true || !Array.isArray(payload.tasks)) {
    return { applied: false, reason: "workspace_or_contract_mismatch" };
  }
  const summary = normalizeTaskSummary(payload.summary);
  if (!summary) {
    return { applied: false, reason: "invalid_summary" };
  }
  const pagination = payload.pagination && typeof payload.pagination === "object" ? {
    limit: nonNegativeInt(payload.pagination.limit) ?? TASK_LIST_LIMIT,
    offset: nonNegativeInt(payload.pagination.offset) ?? 0,
    total: nonNegativeInt(payload.pagination.total) ?? 0,
    returned: nonNegativeInt(payload.pagination.returned) ?? 0,
    hasMore: Boolean(payload.pagination.has_more),
    statusFilter: ALLOWED_STATUS_FILTERS.includes(payload.pagination.status_filter)
      ? payload.pagination.status_filter : "all",
    includeHistory: payload.pagination.include_history !== false,
  } : null;

  const validTasks = payload.tasks.filter((task) => (
    task && typeof task === "object"
    && typeof task.id === "string" && task.id.length > 0
    && task.workspace_id === workspaceId
    && typeof task.state === "string"
    && typeof task.display_status === "string"
  ));

  state.workspaceId = workspaceId;
  state.summary = summary;
  state.pagination = pagination;
  state.tasks = validTasks;
  state.errorCode = null;
  state.loadingList = false;
  if (state.detail && state.detail.workspace_id !== workspaceId) {
    clearTaskDetailState(state);
  } else if (clearMissingDetail && state.selectedTaskId
      && !validTasks.some((task) => task.id === state.selectedTaskId)) {
    clearTaskDetailState(state);
  } else if (state.selectedTaskId && !state.detail) {
    state.selectedTaskId = null;
  }
  return { applied: true, reason: null };
}

export function applyTaskDetailResponse(
  state,
  { generation, detailGeneration, workspaceId, taskId, detailPayload, plansPayload, eventsPayload },
) {
  if (generation !== state.generation || detailGeneration !== state.detailGeneration) {
    return { applied: false, reason: "stale" };
  }
  if (!workspaceId || !taskId || !detailPayload || detailPayload.read_only !== true
      || detailPayload.workspace_id !== workspaceId
      || !detailPayload.task || detailPayload.task.id !== taskId
      || detailPayload.task.workspace_id !== workspaceId) {
    return { applied: false, reason: "detail_mismatch" };
  }
  if (!plansPayload || plansPayload.read_only !== true
      || plansPayload.workspace_id !== workspaceId || plansPayload.task_id !== taskId
      || !Array.isArray(plansPayload.plans)) {
    return { applied: false, reason: "plans_mismatch" };
  }
  if (!eventsPayload || eventsPayload.read_only !== true
      || eventsPayload.workspace_id !== workspaceId || eventsPayload.task_id !== taskId
      || !Array.isArray(eventsPayload.events)) {
    return { applied: false, reason: "events_mismatch" };
  }
  state.selectedTaskId = taskId;
  state.detail = detailPayload.task;
  state.plans = plansPayload.plans;
  state.plansPagination = plansPayload.pagination ?? null;
  state.events = eventsPayload.events;
  state.eventsPagination = eventsPayload.pagination ?? null;
  state.detailErrorCode = null;
  state.loadingDetail = false;
  return { applied: true, reason: null };
}

export function applyTaskEventsPageResponse(
  state,
  { generation, detailGeneration, workspaceId, taskId, eventsPayload },
) {
  if (generation !== state.generation || detailGeneration !== state.detailGeneration) {
    return { applied: false, reason: "stale" };
  }
  if (!eventsPayload || eventsPayload.read_only !== true
      || eventsPayload.workspace_id !== workspaceId || eventsPayload.task_id !== taskId
      || !Array.isArray(eventsPayload.events)) {
    return { applied: false, reason: "events_mismatch" };
  }
  state.events = eventsPayload.events;
  state.eventsPagination = eventsPayload.pagination ?? null;
  return { applied: true, reason: null };
}

export function formatTaskStatus(displayStatus, rawState, language = "en") {
  const ar = language === "ar";
  if (rawState === "queued") {
    return ar ? "معتمد · بانتظار المحاكاة" : "Approved · Simulation queued";
  }
  if (rawState === "simulating") {
    return ar ? "محاكاة · قيد التقدم" : "Simulated · In progress";
  }
  if (rawState === "simulation_completed") {
    return ar ? "محاكاة · مكتملة (بلا تنفيذ فعلي)" : "Simulated · Complete (no real execution)";
  }
  const labels = {
    draft: ["Draft · Plan only", "مسودة · خطة فقط"],
    pending_approval: ["Pending approval", "بانتظار الموافقة"],
    blocked: ["Blocked", "محظورة"],
    interrupted: ["Interrupted", "متوقفة مؤقتًا"],
    cancelled: ["Cancelled", "ملغاة"],
    simulated: ["Simulated", "محاكاة"],
    stale: ["Stale snapshot", "لقطة قديمة"],
  };
  const pair = labels[displayStatus];
  if (pair) return pair[ar ? 1 : 0];
  return sanitizeTaskText(displayStatus || rawState || "unknown", 60, { singleLine: true });
}

export function formatPlanApprovalState(approvalState, { isCurrent = false, isApproved = false } = {}, language = "en") {
  const ar = language === "ar";
  const states = {
    awaiting_approval: ["Pending approval", "بانتظار الموافقة"],
    approved: ["Approved plan (simulation only)", "خطة معتمدة (محاكاة فقط)"],
    rejected: ["Rejected", "مرفوضة"],
    superseded: ["Superseded", "مستبدلة"],
  };
  const base = (states[approvalState] ?? [sanitizeTaskText(approvalState, 40, { singleLine: true }),
    sanitizeTaskText(approvalState, 40, { singleLine: true })])[ar ? 1 : 0];
  const tags = [];
  if (isCurrent) tags.push(ar ? "الحالية" : "current");
  if (isApproved) tags.push(ar ? "المعتمدة" : "approved");
  return tags.length ? `${base} · ${tags.join(" / ")}` : base;
}

export function formatSubtaskState(state, language = "en") {
  const ar = language === "ar";
  const labels = {
    pending: ["Pending (plan only)", "معلقة (خطة فقط)"],
    simulating: ["SIMULATED · In progress", "محاكاة · قيد التقدم"],
    simulated_completed: ["SIMULATED · Complete", "محاكاة · مكتملة"],
    simulated_failed: ["SIMULATED · Failed", "محاكاة · فشلت"],
    blocked: ["Blocked", "محظورة"],
    cancelled: ["Cancelled", "ملغاة"],
  };
  const pair = labels[state];
  return pair ? pair[ar ? 1 : 0] : sanitizeTaskText(state, 40, { singleLine: true });
}

export function formatSimulatedProgress(percent, isSimulated, language = "en") {
  const safePercent = typeof percent === "number" && Number.isFinite(percent)
    ? Math.max(0, Math.min(100, Math.round(percent))) : 0;
  const ar = language === "ar";
  if (isSimulated || safePercent > 0) {
    return ar ? `${safePercent}% (محاكاة فقط)` : `${safePercent}% (SIMULATED ONLY)`;
  }
  return ar ? `${safePercent}% (خطة فقط)` : `${safePercent}% (Plan only)`;
}

export function formatEventType(eventType, language = "en") {
  const ar = language === "ar";
  const labels = {
    task_created: ["Task created", "أُنشئت المهمة"],
    plan_proposed: ["Plan revision proposed", "اقتُرحت مراجعة خطة"],
    plan_approved: ["Plan approved (no real execution)", "تمت الموافقة على الخطة (بلا تنفيذ فعلي)"],
    plan_rejected: ["Plan rejected", "رُفضت الخطة"],
    simulation_progress: ["Simulated progress recorded", "سُجل تقدم محاكاة"],
    simulation_completed: ["Simulation completed (no real execution)", "اكتملت المحاكاة (بلا تنفيذ فعلي)"],
    task_blocked: ["Task blocked by capability policy", "حُظرت المهمة بسياسة الصلاحيات"],
    task_interrupted: ["Task interrupted (no auto-resume)", "توقفت المهمة (بلا استئناف تلقائي)"],
    simulation_resumed: ["Simulation resumed explicitly", "استُؤنفت المحاكاة صراحةً"],
    task_cancelled: ["Task cancelled", "أُلغيت المهمة"],
  };
  const pair = labels[eventType];
  return pair ? pair[ar ? 1 : 0] : sanitizeTaskText(eventType, 60, { singleLine: true });
}

export function taskControlError(code, language = "en") {
  const ar = language === "ar";
  const messages = {
    authentication_required: [
      "Your local session expired. Restart the PowerShell service and pair again.",
      "انتهت جلستك المحلية. أعد تشغيل خدمة PowerShell وأعد الربط.",
    ],
    workspace_selection_required: [
      "Select an authorized workspace to inspect its tasks.",
      "اختر مساحة عمل مصرحًا بها لفحص مهامها.",
    ],
    workspace_unavailable: [
      "This workspace is no longer available to your account.",
      "لم تعد مساحة العمل متاحة لحسابك.",
    ],
    workspace_selection_changed: [
      "Workspace selection changed while loading tasks. Refreshing view.",
      "تغيّر اختيار مساحة العمل أثناء تحميل المهام. جارٍ تحديث العرض.",
    ],
    manage_permission_required: [
      "Workspace manager authorization is required to inspect tasks in this workspace.",
      "يلزم تفويض مدير مساحة العمل لفحص المهام في هذه المساحة.",
    ],
    task_unavailable: [
      "This task is no longer available in the selected workspace.",
      "لم تعد هذه المهمة متاحة في مساحة العمل المحددة.",
    ],
    task_evidence_corrupt: [
      "Task evidence failed integrity verification and was withheld safely.",
      "فشل التحقق من سلامة أدلة المهمة فحُجبت بأمان.",
    ],
    task_service_unavailable: [
      "Task orchestration service is temporarily unavailable.",
      "خدمة تنسيق المهام غير متاحة مؤقتًا.",
    ],
    invalid_query: [
      "The task filter or pagination parameters were rejected.",
      "رُفضت معاملات التصفية أو الترقيم للمهام.",
    ],
    method_not_allowed: [
      "Task Control Center is strictly read-only in this milestone.",
      "مركز التحكم بالمهام للقراءة فقط في هذه المرحلة.",
    ],
    default: [
      "Could not load tasks safely.",
      "تعذر تحميل المهام بأمان.",
    ],
  };
  const pair = messages[code] ?? messages.default;
  return pair[ar ? 1 : 0];
}
