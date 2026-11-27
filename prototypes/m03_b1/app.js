import { NODES, EDGES, connectedNodeIds, searchNodeIds, classifyNode, createFrameDriver } from "/galaxy-model.mjs";
import { chatInputPlaceholder, memoryProposalPayload, memoryProposalError } from "/chat-ui-state.mjs";
import {
  TASK_EVENTS_LIMIT,
  TASK_LIST_LIMIT,
  TASK_PLANS_LIMIT,
  applyTaskDetailResponse,
  applyTaskEventsPageResponse,
  applyTaskListResponse,
  beginTaskDetailLoad,
  beginTaskListLoad,
  clearTaskControlState,
  clearTaskDetailState,
  createTaskControlState,
  formatEventType,
  formatPlanApprovalState,
  formatSimulatedProgress,
  formatSubtaskState,
  formatSummaryCount,
  formatTaskStatus,
  sanitizeTaskText,
  taskControlError,
} from "/task-control-state.mjs";
import {
  applyVoiceStatus,
  canStartPushToTalk,
  createVoiceState,
  detectReplyLanguage,
  encodePcm16Wav,
  interruptVoiceState,
  resetVoiceStateForSessionEnd,
  toggleVoiceMute,
  toggleVoiceTranscript,
  transitionVoiceState,
  voiceErrorMessage,
  voiceLanguageUnavailableError,
  voiceStateLabel,
} from "/voice-ui-state.mjs";
import {
  applyProactivityResponse,
  beginProactivityLoad,
  clearProactivityState,
  createProactivityState,
  proactivityActionAvailable,
} from "/proactivity-ui-state.mjs";

const $ = (selector) => document.querySelector(selector);
const languageSelect = $("#language");
const nodeList = $("#node-list");
const details = $("#node-details");
const emptyDetails = $("#selection-empty");
const canvasHost = $("#galaxy-canvas");
const fallback = $("#fallback-graph");
const search = $("#graph-search");
const chatForm = $("#chat-form");
const chatInput = $("#chat-input");
const chatSend = $("#chat-send");
const chatState = $("#chat-state");
const chatTranscript = $("#chat-transcript");
const chatStatusLine = $("#chat-status-line");
const voicePttButton = $("#voice-ptt");
const voicePttLabel = $("#voice-ptt-label");
const voiceStateBadge = $("#voice-state");
const voiceStateText = $("#voice-state-label");
const voicePostureBadge = $("#voice-posture");
const voicePlayButton = $("#voice-play");
const voiceStopButton = $("#voice-stop");
const voiceMuteButton = $("#voice-mute");
const voiceTranscriptToggle = $("#voice-transcript-toggle");
const voiceTranscriptBox = $("#voice-transcript-box");
const voiceRecognizedText = $("#voice-recognized-text");
const voiceSpokenMeta = $("#voice-spoken-meta");
const voiceErrorLine = $("#voice-error-line");
const pairingDialog = $("#pairing-dialog");
const pairForm = $("#pair-form");
const pairCode = $("#pair-code");
const pairSubmit = $("#pair-submit");
const pairError = $("#pair-error");
const pairStatus = $("#pair-status");
const signOut = $("#chat-signout");
const workspaceSelect = $("#workspace-select");
const workspaceState = $("#workspace-state");
const evidencePanel = $("#evidence-panel");
const citationList = $("#citation-list");
const evidenceOutcome = $("#evidence-outcome");
const createCandidateOpen = $("#candidate-create-open");
const candidateEditorDialog = $("#candidate-editor-dialog");
const candidateReviewDialog = $("#candidate-review-dialog");
const candidateEditorForm = $("#candidate-editor-form");
const candidateContent = $("#candidate-content");
const candidateUserAuthored = $("#candidate-user-authored");
const candidateSources = $("#candidate-sources");
const candidateSourceOptions = $("#candidate-source-options");
const pendingCandidates = $("#pending-candidates");
const memoryStatus = $("#memory-status");
const conversationMemoryPanel = $("#conversation-memory-panel");
const conversationMemoryForm = $("#conversation-memory-form");
const conversationMemoryEnabled = $("#conversation-memory-enabled");
const conversationMemoryRetention = $("#conversation-memory-retention-days");
const conversationMemorySave = $("#conversation-memory-save");
const conversationMemoryRefresh = $("#conversation-memory-refresh");
const conversationMemoryStatus = $("#conversation-memory-status");
const conversationMemoryList = $("#conversation-memory-list");
const conversationMemoryDetail = $("#conversation-memory-detail");
const conversationMemoryDetailTitle = $("#conversation-memory-detail-title");
const conversationMemoryTurns = $("#conversation-memory-turns");
const conversationMemoryDetailClose = $("#conversation-memory-detail-close");
const developerRepoSelect = $("#developer-repo");
const developerState = $("#developer-state");
const fileChangePanel = $("#file-change-panel");
const fileChangeForm = $("#file-change-form");
const fileChangeModelForm = $("#file-change-model-form");
const fileChangeRequest = $("#file-change-request");
const fileChangeModelPaths = $("#file-change-model-paths");
const fileChangePath = $("#file-change-path");
const fileChangeOperation = $("#file-change-operation");
const fileChangeContent = $("#file-change-content");
const fileChangeStatus = $("#file-change-status");
const fileChangeList = $("#file-change-list");
const fileChangeReview = $("#file-change-review");
const fileChangeReviewStatus = $("#file-change-review-status");
const fileChangeDigest = $("#file-change-digest");
const fileChangeDiff = $("#file-change-diff");
const fileChangeDeleteRow = $("#file-change-delete-confirm-row");
const fileChangeDeleteConfirm = $("#file-change-delete-confirm");
const fileChangeAudit = $("#file-change-audit");
const fileChangeOutput = $("#file-change-output");
const fileChangeApprove = $("#file-change-approve");
const fileChangeReject = $("#file-change-reject");
const developerSearchForm = $("#developer-search-form");
const developerSearch = $("#developer-search");
const developerResults = $("#developer-results");
const developerSource = $("#developer-source");
const developerChatForm = $("#developer-chat-form");
const developerQuestion = $("#developer-question");
const developerAsk = $("#developer-ask");
const developerAnswer = $("#developer-answer");
const developerGraph = $("#developer-graph");
const developerGraphState = $("#developer-graph-state");
const developerGraphList = $("#developer-graph-list");
const developerNodeDetail = $("#developer-node-detail");
const developerCompareBase = $("#developer-compare-base");
const developerCompareTarget = $("#developer-compare-target");
const developerCompareButton = $("#developer-compare");
const developerChangeState = $("#developer-change-state");
const developerChangesList = $("#developer-changes-list");
const developerIncludeChanges = $("#developer-include-changes");
const taskSummaryTotal = $("#task-summary-total");
const taskSummaryApproval = $("#task-summary-approval");
const taskSummaryAttention = $("#task-summary-attention");
const taskStatusFilter = $("#task-status-filter");
const taskIncludeHistory = $("#task-include-history");
const taskIncludeArchived = $("#task-include-archived");
const taskRefreshButton = $("#task-refresh");
const taskStatusMessage = $("#task-status-message");
const taskList = $("#task-list");
const taskListPagination = $("#task-list-pagination");
const taskListPrev = $("#task-list-prev");
const taskListNext = $("#task-list-next");
const taskListPageInfo = $("#task-list-page-info");
const taskDetailEmpty = $("#task-detail-empty");
const taskDetailContent = $("#task-detail-content");
const taskDetailStatus = $("#task-detail-status");
const taskDetailArchivedBadge = $("#task-detail-archived-badge");
const taskDetailModeBanner = $("#task-detail-mode-banner");
const taskDetailGoal = $("#task-detail-goal");
const taskDetailMeta = $("#task-detail-meta");
const taskDetailSummary = $("#task-detail-summary");
const taskDetailCapabilities = $("#task-detail-capabilities");
const taskDetailPlans = $("#task-detail-plans");
const taskDetailEvents = $("#task-detail-events");
const taskEventsPagination = $("#task-events-pagination");
const taskEventsPrev = $("#task-events-prev");
const taskEventsNext = $("#task-events-next");
const taskEventsPageInfo = $("#task-events-page-info");
const proactivityInterval = $("#proactivity-interval");
const proactivityCooldown = $("#proactivity-cooldown");
const proactivityEnable = $("#proactivity-enable");
const proactivitySave = $("#proactivity-save");
const proactivityStop = $("#proactivity-stop");
const proactivityRefresh = $("#proactivity-refresh");
const proactivityStatus = $("#proactivity-status");
const proactivitySuggestions = $("#proactivity-suggestions");
let language = "en";
let selectedId = null;
let visibleIds = searchNodeIds(NODES, "", language);
let reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let sceneState = null;
let csrfToken = null;
let authenticated = false;
let configuredModel = null;
let configuredProvider = null;
let sending = false;
let voiceChatInFlight = false;
let chatStateKey = "checking";
let activeWorkspaceId = null;
let conversationMemoryCanWrite = false;
let conversationMemoryLoadGeneration = 0;
let voiceState = createVoiceState();
let voiceMediaStream = null;
let voiceAudioContext = null;
let voiceSourceNode = null;
let voiceProcessorNode = null;
let voiceSilenceGain = null;
let voiceCapturedChunks = [];
let voiceCaptureSampleRate = 16000;
let voiceCaptureStopTimer = null;
let voiceAbortController = null;
let voiceMicAcquisitionPending = false;
let voiceAudioElement = null;
let voiceAudioObjectUrl = null;
let voiceCachedReplyKey = null;
let workspaceServiceUnavailable = false;
let latestEvidenceRequestId = null;
let latestCitations = [];
let reviewCandidate = null;
let latestEvidenceResponse = null;
let developerRepositories = [];
let activeDeveloperRepoId = null;
let developerBusy = false;
let developerGraphData = null;
let developerCommits = [];
let selectedDeveloperNodeId = null;
let developerLoadGeneration = 0;
let developerStatus = ["Pair and select a workspace to load repositories.", "اربط الجلسة واختر مساحة عمل لتحميل المستودعات."];
let developerGraphStatus = ["Select a ready repository to load its indexed structure.", "اختر مستودعًا جاهزًا لتحميل بنيته المفهرسة."];
let developerChangeStatus = ["", ""];
let activeFileChange = null;
let fileChangeGeneration = 0;
const taskState = createTaskControlState();
const proactivityState = createProactivityState();
let taskStatusCopy = [
  "Pair this browser and select an authorized workspace to inspect tasks.",
  "اربط هذا المتصفح واختر مساحة عمل مصرحًا بها لفحص المهام.",
  false,
];

const CHAT_STATUS = {
  checking: ["Checking local session…", "جارٍ التحقق من الجلسة المحلية…"],
  pairing: ["Pair this browser to start", "اربط هذا المتصفح للبدء"],
  ready: ["Configured local model · persistent history is opt-in", "نموذج محلي مضبوط · سجل المحادثات اختياري"],
  sending: ["Waiting for the configured local model…", "بانتظار النموذج المحلي المضبوط…"],
  unavailable: ["Local chat service is unavailable", "خدمة الدردشة المحلية غير متاحة"],
  expired: ["Local session expired · pair again", "انتهت الجلسة المحلية · أعد الربط"],
  restart_required: ["Session ended · restart the service to pair again", "انتهت الجلسة · أعد تشغيل الخدمة للربط مجددًا"],
  failed: ["Chat request failed · no data was saved", "فشل طلب الدردشة · لم تُحفظ البيانات"]
};
const CHAT_ERRORS = {
  model_not_installed: ["The configured model is unavailable. Check its name and local installation, then restart Jarvis.", "النموذج المضبوط غير متاح. تحقق من اسمه وتثبيته محليًا ثم أعد تشغيل جارفس."],
  model_unavailable: ["Could not reach the configured local model service. Check that it is running locally.", "تعذر الوصول إلى خدمة النموذج المحلية المضبوطة. تحقق من تشغيلها محليًا."],
  generation_timeout: ["Local model timed out. Try again when it is ready.", "انتهت مهلة النموذج المحلي. حاول مجددًا بعد جاهزيته."],
  rate_limited: ["Too many requests. Wait a moment before sending another message.", "طلبات كثيرة. انتظر قليلًا قبل إرسال رسالة أخرى."],
  chat_in_progress: ["A local response is already being generated.", "يجري إنشاء رد محلي بالفعل."],
  generation_failed: ["The local model could not complete this request.", "تعذر على النموذج المحلي إكمال هذا الطلب."],
  workspace_unavailable: ["This workspace is no longer available to this account. Choose another workspace.", "لم تعد مساحة العمل متاحة لهذا الحساب. اختر مساحة أخرى."],
  workspace_selection_required: ["Choose a workspace or use general chat.", "اختر مساحة عمل أو استخدم الدردشة العامة."],
  workspace_selection_changed: ["Workspace selection changed while this request was running. Send it again in the selected workspace.", "تغيّر اختيار مساحة العمل أثناء تنفيذ الطلب. أرسله مجددًا في المساحة المحددة."],
  evidence_unavailable: ["Workspace evidence is temporarily unavailable.", "أدلة مساحة العمل غير متاحة مؤقتًا."],
  write_permission_required: ["This workspace does not allow memory changes.", "لا تسمح مساحة العمل هذه بتعديل الذاكرة."],
  invalid_candidate: ["The memory proposal is invalid or too large.", "مقترح الذاكرة غير صالح أو كبير جدًا."],
  candidate_rejected: ["The memory proposal was rejected. Check its source and workspace permissions.", "رُفض مقترح الذاكرة. تحقق من مصدره وصلاحيات مساحة العمل."],
  candidate_unavailable: ["This memory candidate is unavailable.", "مقترح الذاكرة هذا غير متاح."],
  memory_service_unavailable: ["The memory review service is unavailable.", "خدمة مراجعة الذاكرة غير متاحة."],
  conversation_memory_unavailable: ["Persistent conversation memory is temporarily unavailable. No reply was saved.", "الذاكرة الدائمة للمحادثات غير متاحة مؤقتًا. لم يُحفظ أي رد."],
  conversation_memory_changed: ["Conversation memory changed during this request. Please send it again.", "تغيّرت ذاكرة المحادثة أثناء هذا الطلب. أعد إرساله."],
  csrf_rejected: ["The local session check failed. Reload and reconnect.", "فشل التحقق من الجلسة المحلية. أعد التحميل والاتصال."],
  candidate_changed: ["This candidate changed or is no longer pending. Reload the review list.", "تغيّر هذا المقترح أو لم يعد معلّقًا. أعد تحميل قائمة المراجعة."],
  explicit_review_required: ["Review the displayed candidate before confirming.", "راجع المقترح المعروض قبل التأكيد."],
  evidence_selection_invalid: ["Choose active document citations from the latest answer.", "اختر استشهادات مستندات نشطة من أحدث إجابة."],
  evidence_selection_required: ["Select an active document source or mark this as your own note.", "اختر مصدر مستند نشطًا أو حدّد أنها ملاحظتك الشخصية."],
  workspace_list_unavailable: ["Workspace authorization is temporarily unavailable. Clear the selection or reload before asking a question.", "تفويض مساحة العمل غير متاح مؤقتًا. امسح الاختيار أو أعد التحميل قبل طرح سؤال."],
  default: ["The request was rejected. Reload this page and pair again if needed.", "رُفض الطلب. أعد تحميل الصفحة وأعد الربط إذا لزم الأمر."]
};

function localized(value) { return value?.[language] ?? value?.en ?? ""; }
function updateChatInputPlaceholder() {
  chatInput.placeholder = chatInputPlaceholder({
    authenticated,
    workspaceId: activeWorkspaceId,
    language,
  });
}

function applyLanguage(next) {
  language = next;
  document.documentElement.lang = language;
  document.documentElement.dir = language === "ar" ? "rtl" : "ltr";
  canvasHost.setAttribute("aria-label", language === "ar"
    ? "مجرة معرفة اصطناعية تفاعلية. اختر العقد من قائمة العقد التجريبية."
    : "Interactive synthetic knowledge galaxy. Select nodes from the sample node list.");
  for (const element of document.querySelectorAll("[data-en][data-ar]")) element.textContent = element.dataset[language];
  for (const element of document.querySelectorAll("[data-en-placeholder][data-ar-placeholder]")) {
    element.placeholder = element.dataset[`${language}Placeholder`];
  }
  for (const element of document.querySelectorAll("[data-en-title][data-ar-title]")) element.title = element.dataset[`${language}Title`];
  for (const element of document.querySelectorAll("[data-en-aria-label][data-ar-aria-label]")) {
    element.setAttribute("aria-label", element.dataset[`${language}AriaLabel`]);
  }
  updateChatInputPlaceholder();
  setDeveloperState(...developerStatus);
  setDeveloperGraphState(...developerGraphStatus);
  setDeveloperChangeState(...developerChangeStatus);
  renderProactivity();
  if (developerGraphData) {
    renderDeveloperGraphList(developerGraphData);
    renderDeveloperGraph(developerGraphData);
    const selectedNode = developerGraphData.nodes.find((node) => node.id === selectedDeveloperNodeId);
    if (selectedNode) renderDeveloperNodeDetail(selectedNode);
  }
  $("#motion-toggle").setAttribute("aria-pressed", String(!reduceMotion));
  $("#motion-label").textContent = language === "ar"
    ? (reduceMotion ? "الحركة مخفّضة" : "الحركة مفعّلة")
    : (reduceMotion ? "Motion reduced" : "Motion on");
  visibleIds = searchNodeIds(NODES, search.value, language);
  renderNodeList();
  renderDetails();
  renderFallback();
  setChatState(chatStateKey, chatState.dataset.state || "checking");
  if (workspaceState.dataset[language]) workspaceState.textContent = workspaceState.dataset[language];
  if (chatStatusLine.dataset.en || chatStatusLine.dataset.ar) {
    setStatusLine([chatStatusLine.dataset.en ?? "", chatStatusLine.dataset.ar ?? ""],
      chatStatusLine.classList.contains("error"));
  }
  if (workspaceSelect.options.length && workspaceSelect.options[0].value === "") {
    workspaceSelect.options[0].textContent = language === "ar" ? "دردشة عامة · بلا مساحة عمل" : "General chat · no workspace";
  }
  if (latestEvidenceResponse) renderEvidence(latestEvidenceResponse);
  if (activeWorkspaceId) loadPendingCandidates();
  if (memoryStatus.textContent) memoryStatus.textContent = "";
  renderTaskControlCenter();
  renderVoiceUI();
  if (sceneState) sceneState.refreshLabels();
}

function setChatState(key, state = "checking") {
  chatStateKey = key;
  chatState.dataset.state = state;
  let text = CHAT_STATUS[key] ?? CHAT_STATUS.failed;
  if (configuredModel && configuredProvider && (key === "ready" || key === "sending")) {
    text = key === "ready"
      ? [`${configuredProvider} · ${configuredModel} · session context · not saved`,
        `${configuredProvider} · ${configuredModel} · سياق الجلسة · لا يُحفظ`]
      : [`Waiting for ${configuredProvider} · ${configuredModel}…`,
        `بانتظار ${configuredProvider} · ${configuredModel}…`];
  }
  chatState.querySelector("span").textContent = text[language === "ar" ? 1 : 0];
}

function setStatusLine(message = "", isError = false) {
  if (Array.isArray(message)) {
    chatStatusLine.dataset.en = message[0];
    chatStatusLine.dataset.ar = message[1];
    message = message[language === "ar" ? 1 : 0];
  } else if (message) {
    chatStatusLine.dataset[language] = message;
  } else {
    delete chatStatusLine.dataset.en;
    delete chatStatusLine.dataset.ar;
  }
  chatStatusLine.hidden = !message;
  chatStatusLine.textContent = message;
  chatStatusLine.classList.toggle("error", isError);
}

function setWorkspaceState(en, ar) {
  workspaceState.dataset.en = en;
  workspaceState.dataset.ar = ar;
  workspaceState.textContent = language === "ar" ? ar : en;
}

function setDeveloperState(en, ar = en) {
  developerStatus = [en, ar];
  developerState.dataset.en = en;
  developerState.dataset.ar = ar;
  developerState.textContent = language === "ar" ? ar : en;
}

function clearDeveloperResults() {
  developerResults.replaceChildren();
  developerSource.replaceChildren();
  developerSource.hidden = true;
  developerAnswer.replaceChildren();
}

function clearDeveloperGraph() {
  developerGraphData = null;
  developerCommits = [];
  selectedDeveloperNodeId = null;
  developerGraph.replaceChildren();
  developerGraph.hidden = true;
  developerGraphList.replaceChildren();
  developerNodeDetail.textContent = "";
  developerCompareBase.replaceChildren();
  developerCompareTarget.replaceChildren();
  developerCompareBase.disabled = true;
  developerCompareTarget.disabled = true;
  developerCompareButton.disabled = true;
  developerIncludeChanges.checked = false;
  developerIncludeChanges.disabled = true;
  developerChangesList.replaceChildren();
  setDeveloperGraphState("Select a ready repository to load its indexed structure.",
    "اختر مستودعًا جاهزًا لتحميل بنيته المفهرسة.");
  setDeveloperChangeState("", "");
}

function setDeveloperGraphState(en, ar = en) {
  developerGraphStatus = [en, ar];
  developerGraphState.dataset.en = en;
  developerGraphState.dataset.ar = ar;
  developerGraphState.textContent = language === "ar" ? ar : en;
}

function setDeveloperChangeState(en, ar = en) {
  developerChangeStatus = [en, ar];
  developerChangeState.dataset.en = en;
  developerChangeState.dataset.ar = ar;
  developerChangeState.textContent = language === "ar" ? ar : en;
}

function updateDeveloperControls() {
  const readyRepo = developerRepositories.find((repo) => repo.id === activeDeveloperRepoId
    && repo.index_status === "ready");
  const enabled = authenticated && Boolean(activeWorkspaceId) && Boolean(readyRepo) && !developerBusy;
  developerSearch.disabled = !enabled;
  developerSearchForm.querySelector("button[type=submit]").disabled = !enabled;
  developerQuestion.disabled = !enabled;
  developerAsk.disabled = !enabled;
  developerRepoSelect.disabled = !authenticated || !activeWorkspaceId || !developerRepositories.length;
  developerCompareBase.disabled = !enabled || developerCommits.length === 0;
  developerCompareTarget.disabled = !enabled || developerCommits.length === 0;
  developerCompareButton.disabled = !enabled || developerCommits.length < 2;
  developerIncludeChanges.disabled = !enabled || developerCommits.length < 2;
}

async function loadDeveloperRepositories() {
  developerLoadGeneration += 1;
  clearDeveloperResults();
  clearDeveloperGraph();
  developerRepositories = [];
  activeDeveloperRepoId = null;
  resetFileChangePanel();
  developerRepoSelect.replaceChildren();
  const initial = document.createElement("option");
  initial.value = "";
  initial.textContent = language === "ar" ? "لا توجد مستودعات" : "No repositories";
  developerRepoSelect.append(initial);
  if (!authenticated || !activeWorkspaceId) {
    setDeveloperState("Pair and select a workspace to load repositories.", "اربط الجلسة واختر مساحة عمل لتحميل المستودعات.");
    updateDeveloperControls();
    return;
  }
  setDeveloperState("Loading authorized repositories…", "جارٍ تحميل المستودعات المصرح بها…");
  try {
    const response = await fetch("/api/v1/developer/repositories", {
      credentials: "same-origin", cache: "no-store"
    });
    const value = await responseJson(response);
    if (response.status === 401) {
      markSessionExpired();
      return;
    }
    if (!response.ok || value.workspace_id !== activeWorkspaceId || !Array.isArray(value.repositories)) {
      throw new Error(value.error || "developer_service_unavailable");
    }
    developerRepositories = value.repositories.filter((repo) => repo && typeof repo.id === "string"
      && typeof repo.name === "string" && repo.workspace_id === activeWorkspaceId);
    developerRepoSelect.replaceChildren();
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = language === "ar" ? "اختر مستودعًا" : "Select a repository";
    developerRepoSelect.append(empty);
    for (const repo of developerRepositories) {
      const option = document.createElement("option");
      option.value = repo.id;
      option.textContent = repo.name;
      option.dir = "auto";
      developerRepoSelect.append(option);
    }
    if (developerRepositories.length) {
      setDeveloperState("Choose a registered repository.", "اختر مستودعًا مسجلًا.");
    } else {
      setDeveloperState("No repository is registered in this workspace. Use the trusted terminal registration steps.",
        "لا يوجد مستودع مسجل في مساحة العمل. استخدم خطوات التسجيل من الطرفية الموثوقة.");
    }
  } catch (error) {
    setDeveloperState("Developer repository service is unavailable.", "خدمة مستودعات المطورين غير متاحة.");
  } finally {
    updateDeveloperControls();
  }
}

function selectDeveloperRepository() {
  developerLoadGeneration += 1;
  activeDeveloperRepoId = developerRepoSelect.value || null;
  clearDeveloperResults();
  clearDeveloperGraph();
  resetFileChangePanel();
  const repo = developerRepositories.find((item) => item.id === activeDeveloperRepoId);
  if (!repo) {
    setDeveloperState("Choose a registered repository.", "اختر مستودعًا مسجلًا.");
  } else if (repo.index_status !== "ready") {
    setDeveloperState(`Index is ${repo.index_status}. Re-index from the trusted terminal.`,
      `حالة الفهرس ${repo.index_status}. أعد الفهرسة من الطرفية الموثوقة.`);
  } else {
    const commit = typeof repo.commit_sha === "string" ? repo.commit_sha.slice(0, 12) : "unknown";
    setDeveloperState(`Indexed ${repo.branch || "detached HEAD"} · ${commit}`,
      `تمت الفهرسة · ${commit}`);
    loadDeveloperGraphAndCommits(repo);
    loadFileChangeList();
  }
  updateDeveloperControls();
}

function resetFileChangePanel() {
  fileChangeGeneration += 1;
  activeFileChange = null;
  fileChangePanel.hidden = !authenticated || !activeWorkspaceId || !activeDeveloperRepoId;
  fileChangeReview.hidden = true;
  fileChangeList.replaceChildren();
  fileChangeAudit.replaceChildren();
  fileChangeForm.reset();
  fileChangeContent.required = fileChangeOperation.value !== "delete";
  fileChangeDeleteConfirm.checked = false;
  setFileChangeStatus("", "");
}

function setFileChangeStatus(en, ar = en) {
  fileChangeStatus.dataset.en = en;
  fileChangeStatus.dataset.ar = ar;
  fileChangeStatus.textContent = language === "ar" ? ar : en;
}

function renderFileChangeProposal(proposal) {
  activeFileChange = proposal;
  fileChangeReview.hidden = false;
  fileChangeReviewStatus.textContent = proposal.status;
  fileChangeDigest.textContent = proposal.patch_sha256;
  fileChangeDiff.textContent = proposal.diff;
  const hasDelete = proposal.operations?.some((item) => item.operation === "delete");
  fileChangeDeleteRow.hidden = !hasDelete;
  fileChangeDeleteConfirm.checked = false;
  const pending = proposal.status === "pending_approval";
  fileChangeApprove.disabled = !pending;
  fileChangeReject.disabled = !pending;
  fileChangeAudit.replaceChildren();
  fileChangeOutput.replaceChildren();
  for (const entry of proposal.audit ?? []) {
    const item = document.createElement("li");
    item.textContent = `${entry.created_at} | ${entry.event_type}`;
    fileChangeAudit.append(item);
  }
}

async function loadFileChangeList() {
  const generation = ++fileChangeGeneration;
  const workspace = activeWorkspaceId;
  const repositoryId = activeDeveloperRepoId;
  fileChangePanel.hidden = !authenticated || !workspace || !repositoryId;
  fileChangeList.replaceChildren();
  fileChangeReview.hidden = true;
  if (fileChangePanel.hidden) return;
  try {
    const response = await fetch(`/api/v1/coding/file-changes?repository_id=${encodeURIComponent(repositoryId)}`, {
      credentials: "same-origin", cache: "no-store"
    });
    const value = await responseJson(response);
    if (generation !== fileChangeGeneration || workspace !== activeWorkspaceId
        || repositoryId !== activeDeveloperRepoId) return;
    if (!response.ok || value.workspace_id !== workspace || !Array.isArray(value.proposals)) {
      throw new Error(value.error || "file_change_list_unavailable");
    }
    for (const proposal of value.proposals) {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "small-action";
      button.textContent = `${proposal.status} | ${proposal.id.slice(0, 12)} | ${proposal.patch_sha256.slice(0, 12)}`;
      button.addEventListener("click", () => loadFileChange(proposal.id));
      item.append(button);
      fileChangeList.append(item);
    }
  } catch {
    if (generation === fileChangeGeneration) setFileChangeStatus(
      "File change review is unavailable or this repository has no proposal grant.");
  }
}

async function loadFileChange(proposalId) {
  const generation = ++fileChangeGeneration;
  const workspace = activeWorkspaceId;
  const repositoryId = activeDeveloperRepoId;
  try {
    const response = await fetch(`/api/v1/coding/file-changes/${encodeURIComponent(proposalId)}`, {
      credentials: "same-origin", cache: "no-store"
    });
    const value = await responseJson(response);
    if (generation !== fileChangeGeneration || workspace !== activeWorkspaceId
        || repositoryId !== activeDeveloperRepoId) return;
    if (!response.ok || value.workspace_id !== workspace || value.repository_id !== repositoryId) {
      throw new Error(value.error || "file_change_unavailable");
    }
    renderFileChangeProposal(value);
    setFileChangeStatus("Review the complete diff, targets, and digest before deciding.");
    if (value.status === "applied") {
      const outputResponse = await fetch(`/api/v1/coding/file-changes/${encodeURIComponent(proposalId)}/output`, {
        credentials: "same-origin", cache: "no-store"
      });
      const output = await responseJson(outputResponse);
      if (generation !== fileChangeGeneration || workspace !== activeWorkspaceId
          || repositoryId !== activeDeveloperRepoId) return;
      if (!outputResponse.ok || output.workspace_id !== workspace || output.repository_id !== repositoryId
          || output.source_repository_changed !== false || !Array.isArray(output.files)) {
        throw new Error(output.error || "file_change_output_unavailable");
      }
      for (const file of output.files) {
        const section = document.createElement("section");
        const heading = document.createElement("h5");
        heading.textContent = `${file.operation}: ${file.path}`;
        section.append(heading);
        const pre = document.createElement("pre");
        pre.className = "file-change-diff";
        pre.dir = "ltr";
        pre.textContent = file.content === null ? "(deleted from this task workspace)" : file.content;
        section.append(pre);
        fileChangeOutput.append(section);
      }
    }
  } catch {
    if (generation === fileChangeGeneration) setFileChangeStatus(
      "This file change is unavailable or repository access was revoked.");
  }
}

async function submitModelFileChange(event) {
  event.preventDefault();
  if (!authenticated || !csrfToken || !activeWorkspaceId || !activeDeveloperRepoId) return;
  const workspace = activeWorkspaceId;
  const repositoryId = activeDeveloperRepoId;
  const paths = fileChangeModelPaths.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
  if (paths.length < 1 || paths.length > 5 || new Set(paths).size !== paths.length) {
    setFileChangeStatus("Choose one to five unique indexed file paths.");
    return;
  }
  setFileChangeStatus("Asking the configured local model for a bounded proposal...");
  try {
    const response = await fetch("/api/v1/coding/file-changes", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Jarvis-CSRF": csrfToken },
      body: JSON.stringify({ repository_id: repositoryId, request: fileChangeRequest.value, paths })
    });
    const value = await responseJson(response);
    if (workspace !== activeWorkspaceId || repositoryId !== activeDeveloperRepoId) return;
    if (!response.ok || value.workspace_id !== workspace || value.repository_id !== repositoryId) {
      throw new Error(value.error || "local_proposal_failed");
    }
    renderFileChangeProposal(value);
    setFileChangeStatus("Local model proposal saved. Review the complete diff; no file has changed.");
    await loadFileChangeList();
    await loadFileChange(value.id);
  } catch (error) {
    if (workspace === activeWorkspaceId && repositoryId === activeDeveloperRepoId) {
      setFileChangeStatus(`Local proposal failed or was denied: ${String(error?.message || "unknown")}`);
    }
  }
}

async function submitFileChange(event) {
  event.preventDefault();
  if (!authenticated || !csrfToken || !activeWorkspaceId || !activeDeveloperRepoId) return;
  const workspace = activeWorkspaceId;
  const repositoryId = activeDeveloperRepoId;
  const operation = fileChangeOperation.value;
  const content = operation === "delete" ? null : fileChangeContent.value;
  setFileChangeStatus("Preparing a bounded diff...");
  try {
    const response = await fetch("/api/v1/coding/file-changes", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Jarvis-CSRF": csrfToken },
      body: JSON.stringify({ repository_id: repositoryId, operations: [{
        path: fileChangePath.value, operation, content
      }] })
    });
    const value = await responseJson(response);
    if (workspace !== activeWorkspaceId || repositoryId !== activeDeveloperRepoId) return;
    if (!response.ok || value.workspace_id !== workspace || value.repository_id !== repositoryId) {
      throw new Error(value.error || "file_change_proposal_failed");
    }
    renderFileChangeProposal(value);
    setFileChangeStatus("Proposal saved for explicit review. No file has changed.");
    fileChangeContent.value = "";
    await loadFileChangeList();
    await loadFileChange(value.id);
  } catch (error) {
    if (workspace === activeWorkspaceId && repositoryId === activeDeveloperRepoId) {
      setFileChangeStatus(String(error?.message || "Proposal denied. Check path, content, and repository grants."));
    }
  }
}

async function decideFileChange(action) {
  if (!activeFileChange || !csrfToken || !activeWorkspaceId || !activeDeveloperRepoId) return;
  const proposal = activeFileChange;
  const workspace = activeWorkspaceId;
  const repositoryId = activeDeveloperRepoId;
  const hasDelete = proposal.operations?.some((item) => item.operation === "delete");
  if (action === "approve" && hasDelete && !fileChangeDeleteConfirm.checked) {
    setFileChangeStatus("Check the delete-specific confirmation before approving.");
    return;
  }
  if (action === "approve" && !window.confirm("Only the displayed change will be applied. Have you reviewed and approved it?")) return;
  setFileChangeStatus(action === "approve" ? "Applying the approved change..." : "Rejecting proposal...");
  try {
    const response = await fetch(`/api/v1/coding/file-changes/${encodeURIComponent(proposal.id)}/${action}`, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Jarvis-CSRF": csrfToken },
      body: JSON.stringify({ patch_sha256: proposal.patch_sha256, confirmed: true,
        delete_confirmed: action === "approve" && hasDelete && fileChangeDeleteConfirm.checked })
    });
    const value = await responseJson(response);
    if (workspace !== activeWorkspaceId || repositoryId !== activeDeveloperRepoId) return;
    if (!response.ok || value.workspace_id !== workspace || value.repository_id !== repositoryId) {
      throw new Error(value.error || "file_change_decision_failed");
    }
    renderFileChangeProposal(value);
    setFileChangeStatus(action === "approve" ? "Approved change applied and audit recorded."
      : "Proposal rejected; repository files were not changed.");
    await loadFileChangeList();
    await loadFileChange(proposal.id);
  } catch (error) {
    if (workspace === activeWorkspaceId && repositoryId === activeDeveloperRepoId) {
      setFileChangeStatus(`Operation denied or recovery needed: ${String(error?.message || "unknown")}`);
      await loadFileChange(proposal.id);
    }
  }
}

function renderDeveloperGraph(data) {
  const svgNS = "http://www.w3.org/2000/svg";
  developerGraph.replaceChildren();
  const shown = data.nodes.slice(0, 30);
  const visibleIds = new Set(shown.map((node) => node.id));
  const positions = new Map();
  shown.forEach((node, index) => positions.set(node.id, {
    x: 55 + (index % 5) * 132,
    y: 34 + Math.floor(index / 5) * 62
  }));
  const selected = selectedDeveloperNodeId;
  const connected = new Set();
  for (const edge of data.edges) {
    if (edge.source === selected) connected.add(edge.target);
    if (edge.target === selected) connected.add(edge.source);
  }
  for (const edge of data.edges) {
    if (!visibleIds.has(edge.source) || !visibleIds.has(edge.target)) continue;
    const from = positions.get(edge.source);
    const to = positions.get(edge.target);
    if (!from || !to) continue;
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("x1", String(from.x)); line.setAttribute("y1", String(from.y));
    line.setAttribute("x2", String(to.x)); line.setAttribute("y2", String(to.y));
    line.dataset.edge = edge.type;
    if (selected && edge.source !== selected && edge.target !== selected) line.style.opacity = "0.2";
    developerGraph.append(line);
  }
  for (const node of shown) {
    const point = positions.get(node.id);
    const group = document.createElementNS(svgNS, "g");
    group.dataset.node = node.id;
    const circle = document.createElementNS(svgNS, "circle");
    circle.setAttribute("cx", String(point.x)); circle.setAttribute("cy", String(point.y));
    circle.setAttribute("r", node.type === "repository" ? "13" : "9");
    circle.dataset.type = node.type;
    circle.setAttribute("aria-pressed", String(selected === node.id));
    circle.addEventListener("click", () => focusDeveloperNode(node.id));
    if (selected && node.id !== selected && !connected.has(node.id)) group.style.opacity = "0.35";
    const title = document.createElementNS(svgNS, "title");
    title.textContent = `${localizeDeveloperValue(node.type)}: ${node.label}`;
    const label = document.createElementNS(svgNS, "text");
    label.setAttribute("x", String(point.x + 13)); label.setAttribute("y", String(point.y + 3));
    label.textContent = node.label.length > 20 ? `${node.label.slice(0, 19)}…` : node.label;
    group.append(title, circle, label);
    developerGraph.append(group);
  }
  developerGraph.hidden = false;
}

function focusDeveloperNode(nodeId) {
  selectedDeveloperNodeId = nodeId;
  const node = developerGraphData?.nodes.find((item) => item.id === nodeId);
  if (!node) return;
  renderDeveloperNodeDetail(node);
  developerGraphList.querySelectorAll("button[data-node-id]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.nodeId === nodeId));
  });
  if (node.file_id) openDeveloperSource(node.file_id);
  renderDeveloperGraph(developerGraphData);
}

function renderDeveloperNodeDetail(node) {
  const adjacent = developerGraphData.edges
    .filter((edge) => edge.source === node.id || edge.target === node.id)
    .map((edge) => `${localizeDeveloperValue(edge.type)}${edge.confidence === "lexical_partial"
      ? (language === "ar" ? " (جزئي)" : " (partial)") : ""}`);
  const relations = [...new Set(adjacent)];
  const detail = `${localizeDeveloperValue(node.type)}: ${node.label}${node.relative_path ? ` · ${node.relative_path}` : ""}`
    + (relations.length ? ` · ${relations.join(", ")}` : "");
  developerNodeDetail.textContent = detail;
}

function renderDeveloperGraphList(data) {
  developerGraphList.replaceChildren();
  for (const node of data.nodes) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.nodeId = node.id;
    button.setAttribute("aria-pressed", String(selectedDeveloperNodeId === node.id));
    const title = document.createElement("span");
    title.textContent = `${localizeDeveloperValue(node.type)}: ${node.label}`;
    const detail = document.createElement("small");
    const analysisStatus = node.analysis_status ? localizeDeveloperValue(node.analysis_status) : "";
    const confidence = node.confidence === "lexical_partial"
      ? (language === "ar" ? "ثقة جزئية" : "partial confidence") : node.confidence || "";
    detail.textContent = `${node.relative_path || node.commit_sha?.slice(0, 12) || ""}${analysisStatus ? ` · ${analysisStatus}` : ""}${confidence ? ` · ${confidence}` : ""}`;
    button.append(title, detail);
    button.addEventListener("click", () => focusDeveloperNode(node.id));
    item.append(button);
    developerGraphList.append(item);
  }
}

function localizeDeveloperValue(value) {
  if (language !== "ar") return value;
  const labels = {
    repository: "المستودع", directory: "مجلد", file: "ملف", symbol: "رمز", module: "وحدة",
    contains: "يحتوي", defines: "يعرّف", imports: "يستورد", references: "يشير إلى",
    added: "مضاف", modified: "معدّل", deleted: "محذوف", type_changed: "تغيّر النوع",
    source: "مصدر", git_change: "تغيير Git", code: "شفرة", documentation: "توثيق",
    ast_line_overlap: "تقاطع أسطر AST", partial_line_overlap: "تقاطع أسطر جزئي",
    unavailable: "غير متاح",
    complete: "مكتمل", partial: "جزئي", failed: "فشل التحليل", unsupported: "غير مدعوم", unknown: "غير معروف"
  };
  return labels[value] || value;
}

function populateDeveloperCommitSelect(select, commits, selected) {
  select.replaceChildren();
  for (const commit of commits) {
    const option = document.createElement("option");
    option.value = commit.commit_sha;
    option.textContent = `${commit.commit_sha.slice(0, 12)} · ${commit.date} · ${commit.subject}`;
    option.dir = "auto";
    select.append(option);
  }
  if (selected) select.value = selected;
}

async function loadDeveloperGraphAndCommits(repo) {
  const generation = developerLoadGeneration;
  const workspace = activeWorkspaceId;
  setDeveloperGraphState("Loading the authorized indexed graph…", "جارٍ تحميل الرسم المفهرس المصرح به…");
  setDeveloperChangeState("Loading recent indexed commits…", "جارٍ تحميل الالتزامات المفهرسة الأخيرة…");
  try {
    const prefix = `/api/v1/developer/repositories/${encodeURIComponent(repo.id)}`;
    const [graphResponse, commitsResponse] = await Promise.all([
      fetch(`${prefix}/graph?limit=300`, { credentials: "same-origin", cache: "no-store" }),
      fetch(`${prefix}/commits`, { credentials: "same-origin", cache: "no-store" })
    ]);
    const [graph, commitsValue] = await Promise.all([responseJson(graphResponse), responseJson(commitsResponse)]);
    if (generation !== developerLoadGeneration || workspace !== activeWorkspaceId || repo.id !== activeDeveloperRepoId) return;
    if (graphResponse.status === 401 || commitsResponse.status === 401) return markSessionExpired();
    if (graphResponse.status === 409 || commitsResponse.status === 409) {
      setDeveloperGraphState("Repository index is stale. Re-index it from the trusted terminal.",
        "فهرس المستودع قديم. أعد فهرسته من الطرفية الموثوقة.");
      setDeveloperChangeState("Re-index before viewing commits.", "أعد الفهرسة قبل عرض الالتزامات.");
      return;
    }
    if (!graphResponse.ok || !commitsResponse.ok || graph.workspace_id !== workspace
        || graph.repository_id !== repo.id || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges)
        || commitsValue.workspace_id !== workspace || commitsValue.repository_id !== repo.id
        || !Array.isArray(commitsValue.commits)) throw new Error("graph_unavailable");
    developerGraphData = graph;
    developerCommits = commitsValue.commits.filter((item) => item && /^[0-9a-f]{40,64}$/.test(item.commit_sha));
    renderDeveloperGraphList(graph);
    renderDeveloperGraph(graph);
    setDeveloperGraphState(`${graph.nodes.length} graph nodes · ${graph.edges.length} relationships · ${graph.commit_sha.slice(0, 12)}${graph.truncated ? " · limited view" : ""}`,
      `${graph.nodes.length} عقدة · ${graph.edges.length} علاقة · ${graph.commit_sha.slice(0, 12)}${graph.truncated ? " · عرض محدود" : ""}`);
    populateDeveloperCommitSelect(developerCompareTarget, developerCommits, developerCommits[0]?.commit_sha);
    populateDeveloperCommitSelect(developerCompareBase, developerCommits,
      developerCommits[1]?.commit_sha || developerCommits[0]?.commit_sha);
    if (developerCommits.length > 1) {
      setDeveloperChangeState("Choose a base and target commit to inspect changes.",
        "اختر الالتزام الأساسي والهدف لفحص التغييرات.");
    } else {
      setDeveloperChangeState("Only one indexed-history commit is available for comparison.",
        "يتوفر التزام واحد فقط من السجل المفهرس للمقارنة.");
    }
  } catch (_error) {
    if (generation === developerLoadGeneration) {
      setDeveloperGraphState("Repository graph is unavailable.", "رسم المستودع غير متاح.");
      setDeveloperChangeState("Commit history is unavailable.", "سجل الالتزامات غير متاح.");
    }
  } finally {
    if (generation === developerLoadGeneration) updateDeveloperControls();
  }
}

async function compareDeveloperCommits() {
  if (!activeDeveloperRepoId || developerBusy) return;
  const generation = developerLoadGeneration;
  const workspace = activeWorkspaceId;
  const repositoryId = activeDeveloperRepoId;
  const base = developerCompareBase.value;
  const target = developerCompareTarget.value;
  if (!base || !target) return;
  developerChangesList.replaceChildren();
  setDeveloperChangeState("Comparing commit snapshots…", "جارٍ مقارنة لقطات الالتزام…");
  try {
    const response = await fetch(`/api/v1/developer/repositories/${encodeURIComponent(repositoryId)}/changes?base=${encodeURIComponent(base)}&target=${encodeURIComponent(target)}`,
      { credentials: "same-origin", cache: "no-store" });
    const value = await responseJson(response);
    if (generation !== developerLoadGeneration || workspace !== activeWorkspaceId
        || repositoryId !== activeDeveloperRepoId) return;
    if (response.status === 401) return markSessionExpired();
    if (response.status === 409) {
      setDeveloperChangeState("Repository index is stale. Re-index before comparing.",
        "فهرس المستودع قديم. أعد الفهرسة قبل المقارنة.");
      return;
    }
    if (!response.ok || value.workspace_id !== activeWorkspaceId || value.repository_id !== activeDeveloperRepoId
        || value.base_commit !== base || value.target_commit !== target || !Array.isArray(value.changes)) {
      throw new Error("comparison_failed");
    }
    for (const change of value.changes) {
      const article = document.createElement("article");
      article.className = "developer-result";
      const title = document.createElement("strong");
      title.textContent = `${localizeDeveloperValue(change.change_type)}: ${change.relative_path}`;
      const meta = document.createElement("small");
      const symbolHint = Array.isArray(change.symbol_hints) && change.symbol_hints.length
        ? (language === "ar"
          ? ` · الأسطر المتغيرة تتقاطع مع ${change.symbol_hints.join(", ")} (${localizeDeveloperValue(change.symbol_status)})`
          : ` · changed lines overlap ${change.symbol_hints.join(", ")} (${localizeDeveloperValue(change.symbol_status)})`) : "";
      meta.textContent = `${change.commit_sha.slice(0, 12)} · ${change.start_line}-${change.end_line} · ${localizeDeveloperValue(change.evidence_type)}${symbolHint}`;
      const excerpt = document.createElement("pre");
      excerpt.textContent = change.excerpt;
      excerpt.dir = "ltr";
      article.append(title, meta, excerpt);
      developerChangesList.append(article);
    }
    if (value.changes.length) {
      setDeveloperChangeState(`${value.changes.length} changed files · commit-pinned diff evidence.${value.truncated ? " Limited by local bounds." : ""}`,
        `${value.changes.length} ملفات متغيرة · دليل فرق مرتبط بالالتزام.${value.truncated ? " عُرضت نتيجة محدودة." : ""}`);
    } else {
      setDeveloperChangeState("No changes between these commits.", "لا توجد تغييرات بين هذين الالتزامين.");
    }
  } catch (_error) {
    if (generation === developerLoadGeneration) {
      setDeveloperChangeState("The comparison could not be completed safely.", "تعذر إكمال المقارنة بأمان.");
    }
  }
}

async function searchDeveloperFiles(event) {
  event.preventDefault();
  if (!activeDeveloperRepoId || developerBusy) return;
  const query = developerSearch.value.trim();
  if (!query || query.length > 256) return;
  const generation = developerLoadGeneration;
  const workspace = activeWorkspaceId;
  const repositoryId = activeDeveloperRepoId;
  clearDeveloperResults();
  setDeveloperState("Searching the indexed snapshot…", "جارٍ البحث في النسخة المفهرسة…");
  try {
    const response = await fetch(`/api/v1/developer/repositories/${encodeURIComponent(repositoryId)}/files?q=${encodeURIComponent(query)}`,
      { credentials: "same-origin", cache: "no-store" });
    const value = await responseJson(response);
    if (generation !== developerLoadGeneration || workspace !== activeWorkspaceId
        || repositoryId !== activeDeveloperRepoId) return;
    if (response.status === 401) return markSessionExpired();
    if (response.status === 409 && value.error === "repository_index_stale") {
      setDeveloperState("Repository changed. Re-index it from the trusted terminal before searching.",
        "تغيّر المستودع. أعد فهرسته من الطرفية الموثوقة قبل البحث.");
      return;
    }
    if (!response.ok || value.workspace_id !== activeWorkspaceId
        || value.repository?.id && value.repository.id !== activeDeveloperRepoId
        || !Array.isArray(value.results)) throw new Error("search_failed");
    for (const result of value.results) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "developer-result";
      const name = document.createElement("strong");
      name.textContent = result.relative_path;
      const detail = document.createElement("small");
      const symbols = Array.isArray(result.symbols) ? result.symbols.map((symbol) => symbol.qualified_name).join(", ") : "";
      const analysis = language === "ar"
        ? (result.analysis_status === "complete" ? "تحليل AST مكتمل"
          : result.analysis_status === "partial" ? "محلل جزئي" : "بيانات غير معروفة")
        : (result.analysis_status === "complete" ? "AST complete"
          : result.analysis_status === "partial" ? "partial parser" : "metadata unknown");
      detail.textContent = `${result.language} · ${analysis}${symbols ? ` · ${symbols}` : ""}`;
      button.append(name, detail);
      button.addEventListener("click", () => openDeveloperSource(result.id));
      developerResults.append(button);
    }
    setDeveloperState(value.results.length ? "Select a result to view its indexed source." : "No indexed source matched this query.",
      value.results.length ? "اختر نتيجة لعرض المصدر المفهرس." : "لم يطابق البحث أي مصدر مفهرس.");
  } catch (_error) {
    if (generation === developerLoadGeneration) {
      setDeveloperState("Repository search failed safely.", "تعذر البحث في المستودع بأمان.");
    }
  }
}

async function openDeveloperSource(fileId) {
  if (!activeDeveloperRepoId || typeof fileId !== "string") return;
  const generation = developerLoadGeneration;
  const workspace = activeWorkspaceId;
  const repositoryId = activeDeveloperRepoId;
  developerSource.hidden = true;
  try {
    const response = await fetch(`/api/v1/developer/repositories/${encodeURIComponent(repositoryId)}/files/${encodeURIComponent(fileId)}`,
      { credentials: "same-origin", cache: "no-store" });
    const value = await responseJson(response);
    if (generation !== developerLoadGeneration || workspace !== activeWorkspaceId
        || repositoryId !== activeDeveloperRepoId) return;
    if (response.status === 401) return markSessionExpired();
    if (!response.ok || value.workspace_id !== activeWorkspaceId || value.source?.id !== fileId) {
      throw new Error("source_unavailable");
    }
    const source = value.source;
    developerSource.textContent = `${source.relative_path} · ${source.commit_sha.slice(0, 12)} · lines ${source.start_line}-${source.end_line}\n${source.content}`;
    developerSource.hidden = false;
  } catch (_error) {
    if (generation === developerLoadGeneration) {
      setDeveloperState("This indexed source is no longer available.", "لم يعد هذا المصدر المفهرس متاحًا.");
    }
  }
}

async function askDeveloper(event) {
  event.preventDefault();
  if (!authenticated || !csrfToken || !activeWorkspaceId || !activeDeveloperRepoId || developerBusy) return;
  const message = developerQuestion.value.trim();
  if (!message || message.length > 1000) return;
  const generation = developerLoadGeneration;
  const workspace = activeWorkspaceId;
  const repositoryId = activeDeveloperRepoId;
  const payload = { repository_id: activeDeveloperRepoId, message };
  if (developerIncludeChanges.checked) {
    if (!developerCompareBase.value || !developerCompareTarget.value) {
      setDeveloperChangeState("Choose both commits before including the comparison.",
        "اختر الالتزامين قبل إدراج المقارنة.");
      return;
    }
    payload.comparison_base = developerCompareBase.value;
    payload.comparison_target = developerCompareTarget.value;
  }
  developerBusy = true;
  updateDeveloperControls();
  clearDeveloperResults();
  developerAnswer.textContent = language === "ar" ? "جارٍ فحص الأدلة المحلية…" : "Checking local repository evidence…";
  developerAnswer.dir = "auto";
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 310000);
  try {
    const response = await fetch("/api/v1/developer/chat", {
      method: "POST", credentials: "same-origin", cache: "no-store", signal: controller.signal,
      headers: { "Content-Type": "application/json", "X-Jarvis-CSRF": csrfToken },
      body: JSON.stringify(payload)
    });
    const value = await responseJson(response);
    if (generation !== developerLoadGeneration || workspace !== activeWorkspaceId
        || repositoryId !== activeDeveloperRepoId) return;
    if (response.status === 401) return markSessionExpired();
    if (response.status === 409 && value.error === "repository_index_stale") {
      developerAnswer.textContent = language === "ar" ? "تغير المستودع. أعد فهرسته من الطرفية ثم أعد السؤال." : "Repository changed. Re-index it in the trusted terminal, then ask again.";
      return;
    }
    if (!response.ok || value.workspace_id !== activeWorkspaceId || value.repository_id !== activeDeveloperRepoId
        || value.model !== configuredModel || value.provider !== configuredProvider
        || typeof value.reply !== "string" || value.reply.length > 12000 || !Array.isArray(value.citations)) {
      throw new Error("developer_chat_failed");
    }
    developerAnswer.textContent = value.reply;
    for (const citation of value.citations) {
      if (citation.workspace_id !== activeWorkspaceId || citation.repository_id !== activeDeveloperRepoId
          || typeof citation.relative_path !== "string" || typeof citation.commit_sha !== "string") continue;
      const card = document.createElement("span");
      card.className = "developer-citation";
      card.textContent = `[${citation.label}] ${localizeDeveloperValue(citation.evidence_type || "source")} · ${citation.relative_path} · ${citation.commit_sha.slice(0, 12)} · ${citation.start_line}-${citation.end_line}`;
      developerAnswer.append(card);
    }
  } catch (error) {
    if (generation === developerLoadGeneration) {
      developerAnswer.textContent = error.name === "AbortError"
        ? (language === "ar" ? "انتهت مهلة سؤال المطور." : "Developer question timed out.")
        : (language === "ar" ? "تعذر إكمال السؤال بأمان." : "The developer question could not be completed safely.");
    }
  } finally {
    clearTimeout(timeout);
    developerBusy = false;
    updateDeveloperControls();
  }
}

function setTaskStatusCopy(en, ar, isError = false) {
  taskStatusCopy = [en, ar, Boolean(isError)];
  taskStatusMessage.textContent = language === "ar" ? ar : en;
  taskStatusMessage.classList.toggle("error", Boolean(isError));
}

function updateTaskControls() {
  const ready = authenticated && Boolean(activeWorkspaceId) && !workspaceServiceUnavailable;
  const busy = taskState.loadingList;
  taskStatusFilter.disabled = !ready || busy;
  taskIncludeHistory.disabled = !ready || busy;
  taskIncludeArchived.disabled = !ready || busy;
  taskRefreshButton.disabled = !ready || busy;
  const page = taskState.pagination;
  const showListPages = Boolean(ready && page && (page.offset > 0 || page.hasMore));
  taskListPagination.hidden = !showListPages;
  taskListPrev.disabled = !showListPages || busy || !page || page.offset <= 0;
  taskListNext.disabled = !showListPages || busy || !page || !page.hasMore;
  const evPage = taskState.eventsPagination;
  const showEventPages = Boolean(ready && taskState.detail && evPage && (evPage.offset > 0 || evPage.has_more));
  taskEventsPagination.hidden = !showEventPages;
  taskEventsPrev.disabled = !showEventPages || taskState.loadingDetail || !evPage || evPage.offset <= 0;
  taskEventsNext.disabled = !showEventPages || taskState.loadingDetail || !evPage || !evPage.has_more;
}

function renderTaskSummary() {
  taskSummaryTotal.textContent = formatSummaryCount(taskState.summary?.totalVisible);
  taskSummaryApproval.textContent = formatSummaryCount(taskState.summary?.awaitingApproval);
  taskSummaryAttention.textContent = formatSummaryCount(taskState.summary?.interruptedOrBlocked);
}

function renderTaskList() {
  taskList.replaceChildren();
  const ar = language === "ar";
  if (!authenticated || !activeWorkspaceId || workspaceServiceUnavailable || !taskState.summary) {
    taskListPageInfo.textContent = "";
    updateTaskControls();
    return;
  }
  if (!taskState.tasks.length) {
    const item = document.createElement("li");
    const empty = document.createElement("p");
    empty.className = "task-detail-empty";
    empty.textContent = ar
      ? "لا توجد مهام مطابقة في مساحة العمل المحددة."
      : "No matching tasks in the selected workspace.";
    item.append(empty);
    taskList.append(item);
  } else {
    for (const task of taskState.tasks) {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = task.is_archived ? "task-item-button is-archived" : "task-item-button";
      button.dataset.taskId = task.id;
      button.dataset.archived = task.is_archived ? "true" : "false";
      button.setAttribute("aria-pressed", String(taskState.selectedTaskId === task.id));

      const top = document.createElement("div");
      top.className = "task-item-top";
      const statusPill = document.createElement("span");
      statusPill.className = "task-status-pill";
      statusPill.dataset.status = task.display_status;
      statusPill.textContent = formatTaskStatus(task.display_status, task.state, language);
      top.append(statusPill);
      if (task.is_archived) {
        const archivedTag = document.createElement("span");
        archivedTag.className = "task-archived-badge";
        archivedTag.textContent = ar ? "مؤرشفة" : "ARCHIVED";
        top.append(archivedTag);
      }
      if (task.is_simulated) {
        const simTag = document.createElement("span");
        simTag.className = "task-sim-banner";
        simTag.textContent = ar ? "محاكاة فقط" : "SIMULATED";
        top.append(simTag);
      }

      const goal = document.createElement("span");
      goal.className = "task-item-goal";
      goal.dir = "auto";
      const cleanGoal = sanitizeTaskText(task.goal, 300, { singleLine: true });
      goal.textContent = task.goal_truncated ? `${cleanGoal}…` : cleanGoal;

      const meta = document.createElement("span");
      meta.className = "task-item-meta";
      const revText = task.current_plan_revision > 0
        ? (ar ? `مراجعة الخطة ${task.current_plan_revision}` : `Plan rev ${task.current_plan_revision}`)
        : (ar ? "بلا مراجعة خطة" : "No plan revision");
      const approvedText = task.approved_plan_revision
        ? (ar ? ` · المعتمدة ${task.approved_plan_revision}` : ` · approved rev ${task.approved_plan_revision}`)
        : "";
      const archivedText = task.is_archived ? (ar ? " · مؤرشفة" : " · archived") : "";
      const expiredText = task.is_expired ? (ar ? " · منتهية" : " · expired") : "";
      meta.textContent = `${task.id.slice(0, 10)} · ${revText}${approvedText}${archivedText}${expiredText}`;

      button.append(top, goal, meta);
      button.addEventListener("click", () => selectTask(task.id));
      item.append(button);
      taskList.append(item);
    }
  }
  const page = taskState.pagination;
  if (page && page.total > 0) {
    const start = page.offset + 1;
    const end = page.offset + page.returned;
    taskListPageInfo.textContent = ar
      ? `عرض ${start}–${end} من ${page.total}`
      : `Showing ${start}–${end} of ${page.total}`;
  } else {
    taskListPageInfo.textContent = "";
  }
  updateTaskControls();
}

function renderTaskEvents() {
  taskDetailEvents.replaceChildren();
  const ar = language === "ar";
  if (!taskState.events.length) {
    const li = document.createElement("li");
    li.className = "task-detail-empty";
    li.textContent = ar ? "لا توجد أحداث دورة حياة مسجلة." : "No lifecycle events recorded.";
    taskDetailEvents.append(li);
  } else {
    for (const ev of taskState.events) {
      const li = document.createElement("li");
      li.className = "task-event-item";
      const head = document.createElement("div");
      head.className = "task-event-head";
      const title = document.createElement("strong");
      title.textContent = formatEventType(ev.event_type, language);
      const when = document.createElement("span");
      when.className = "task-event-meta";
      when.textContent = sanitizeTaskText(ev.created_at || "", 40, { singleLine: true });
      head.append(title, when);
      li.append(head);

      const metaParts = [];
      if (typeof ev.plan_revision === "number") {
        metaParts.push(ar ? `مراجعة ${ev.plan_revision}` : `rev ${ev.plan_revision}`);
      }
      if (ev.simulated_only) {
        metaParts.push(ar ? "محاكاة فقط" : "SIMULATED ONLY");
      }
      if (ev.snapshot_sha256_prefix) {
        metaParts.push(`sha256:${sanitizeTaskText(ev.snapshot_sha256_prefix, 16, { singleLine: true })}`);
      }
      if (ev.details && typeof ev.details === "object") {
        for (const [k, v] of Object.entries(ev.details)) {
          if (typeof v === "string" && v) {
            metaParts.push(`${sanitizeTaskText(k, 32, { singleLine: true })}=${sanitizeTaskText(v, 80, { singleLine: true })}`);
          }
        }
      }
      if (metaParts.length) {
        const meta = document.createElement("span");
        meta.className = "task-event-meta";
        meta.textContent = metaParts.join(" · ");
        li.append(meta);
      }
      taskDetailEvents.append(li);
    }
  }
  const evPage = taskState.eventsPagination;
  if (evPage && (evPage.offset > 0 || evPage.has_more)) {
    const start = evPage.offset + 1;
    const end = evPage.offset + evPage.returned;
    taskEventsPageInfo.textContent = ar
      ? `الأحداث ${start}–${end}${evPage.has_more ? "+" : ""}`
      : `Events ${start}–${end}${evPage.has_more ? "+" : ""}`;
  } else {
    taskEventsPageInfo.textContent = "";
  }
  updateTaskControls();
}

function renderTaskDetail() {
  const ar = language === "ar";
  const detail = taskState.detail;
  if (!detail) {
    taskDetailContent.hidden = true;
    taskDetailContent.classList.remove("is-archived");
    taskDetailArchivedBadge.hidden = true;
    taskDetailArchivedBadge.textContent = "";
    taskDetailEmpty.hidden = false;
    taskDetailEmpty.textContent = taskState.detailErrorCode
      ? taskControlError(taskState.detailErrorCode, language)
      : (ar
        ? "اختر مهمة لفحص مراجعات الخطة والتقدم المحاكى والتبعيات وسجل دورة الحياة."
        : "Select a task to inspect its plan revisions, simulated progress, dependencies, and lifecycle history.");
    taskDetailCapabilities.replaceChildren();
    taskDetailPlans.replaceChildren();
    taskDetailEvents.replaceChildren();
    updateTaskControls();
    return;
  }

  taskDetailEmpty.hidden = true;
  taskDetailContent.hidden = false;
  taskDetailContent.classList.toggle("is-archived", Boolean(detail.is_archived));
  taskDetailStatus.dataset.status = detail.display_status;
  taskDetailStatus.textContent = formatTaskStatus(detail.display_status, detail.state, language);
  if (detail.is_archived) {
    taskDetailArchivedBadge.hidden = false;
    taskDetailArchivedBadge.textContent = ar ? "مؤرشفة · قراءة فقط" : "ARCHIVED · READ-ONLY";
  } else {
    taskDetailArchivedBadge.hidden = true;
    taskDetailArchivedBadge.textContent = "";
  }
  taskDetailModeBanner.textContent = detail.is_simulated
    ? (ar ? "تقدم محاكى فقط · بلا تنفيذ فعلي" : "SIMULATED PROGRESS ONLY · NO REAL EXECUTION")
    : (ar ? "خطة فقط · بلا تنفيذ فعلي" : "PLAN ONLY · NO REAL EXECUTION");
  taskDetailGoal.textContent = sanitizeTaskText(detail.goal, 1000);

  const metaParts = [
    `${ar ? "المعرف" : "ID"}: ${detail.id.slice(0, 12)}`,
    `${ar ? "الوضع" : "Mode"}: ${sanitizeTaskText(detail.execution_mode, 32, { singleLine: true })}`,
    `${ar ? "مراجعات الخطة" : "Plan revisions"}: ${detail.plan_revision_count ?? 0}`,
  ];
  if (detail.repository_commit) {
    metaParts.push(`${ar ? "الالتزام" : "Commit"}: ${sanitizeTaskText(detail.repository_commit, 12, { singleLine: true })}`);
  }
  if (detail.is_archived) {
    const archivedTime = typeof detail.archived_at === "string" && detail.archived_at
      ? `: ${sanitizeTaskText(detail.archived_at, 40, { singleLine: true })}`
      : "";
    metaParts.push(`${ar ? "أُرشفت" : "Archived"}${archivedTime}`);
  }
  if (detail.is_expired) {
    metaParts.push(ar ? "منتهية الصلاحية" : "Expired");
  }
  taskDetailMeta.textContent = metaParts.join(" · ");

  if (detail.result_summary) {
    taskDetailSummary.textContent = sanitizeTaskText(detail.result_summary, 400);
    taskDetailSummary.hidden = false;
  } else {
    taskDetailSummary.textContent = "";
    taskDetailSummary.hidden = true;
  }

  taskDetailCapabilities.replaceChildren();
  const caps = detail.capability_controls || {};
  const disabledLabel = ar ? "معطّل (قراءة فقط)" : "Disabled (read-only)";
  const enabledLabel = ar ? "مفعّل" : "Enabled";
  const noneLabel = ar ? "لا يوجد" : "None";
  const capRows = [
    [ar ? "الصلاحيات المطلوبة" : "Requested capabilities",
      Array.isArray(caps.requested) && caps.requested.length ? caps.requested.join(", ") : noneLabel],
    [ar ? "الصلاحيات الممنوحة" : "Granted capabilities",
      Array.isArray(caps.granted) && caps.granted.length ? caps.granted.join(", ") : noneLabel],
    [ar ? "الصلاحيات المحظورة" : "Blocked capabilities",
      Array.isArray(caps.blocked) && caps.blocked.length ? caps.blocked.join(", ") : noneLabel],
    [ar ? "التنفيذ الفعلي" : "Real execution", caps.execution_enabled ? enabledLabel : disabledLabel],
    [ar ? "كتابة الملفات / العمليات / الشبكة" : "File writes / processes / network",
      (caps.file_write_enabled || caps.process_execution_enabled || caps.network_enabled)
        ? enabledLabel : disabledLabel],
  ];
  for (const [labelText, valueText] of capRows) {
    const row = document.createElement("div");
    row.className = "task-cap-row";
    const labelEl = document.createElement("span");
    labelEl.textContent = labelText;
    const valueEl = document.createElement("strong");
    valueEl.textContent = sanitizeTaskText(String(valueText), 240, { singleLine: true });
    row.append(labelEl, valueEl);
    taskDetailCapabilities.append(row);
  }

  taskDetailPlans.replaceChildren();
  if (!taskState.plans.length) {
    const noPlans = document.createElement("p");
    noPlans.className = "task-detail-empty";
    noPlans.textContent = ar ? "لا توجد مراجعات خطة مسجلة لهذه المهمة بعد." : "No plan revisions recorded for this task yet.";
    taskDetailPlans.append(noPlans);
  } else {
    for (const plan of taskState.plans) {
      const card = document.createElement("article");
      card.className = "task-plan-card";
      const head = document.createElement("div");
      head.className = "task-plan-head";
      const revTitle = document.createElement("strong");
      revTitle.textContent = ar ? `المراجعة #${plan.revision}` : `Revision #${plan.revision}`;
      const approvalBadge = document.createElement("span");
      approvalBadge.className = "task-status-pill";
      approvalBadge.dataset.status = plan.approval_state === "awaiting_approval"
        ? "pending_approval" : (plan.approval_state === "approved" ? "simulated" : "cancelled");
      approvalBadge.textContent = formatPlanApprovalState(
        plan.approval_state,
        { isCurrent: plan.is_current_revision, isApproved: plan.is_approved_revision },
        language,
      );
      head.append(revTitle, approvalBadge);

      const digest = document.createElement("div");
      digest.className = "task-plan-hash";
      digest.textContent = `sha256:${sanitizeTaskText(plan.plan_sha256 || "", 16, { singleLine: true })} · ${
        ar ? `${plan.subtask_count} مهام فرعية · ${plan.dependency_count} تبعيات`
          : `${plan.subtask_count} subtasks · ${plan.dependency_count} dependencies`
      }`;
      card.append(head, digest);

      const subList = document.createElement("ul");
      subList.className = "task-subtask-list";
      for (const sub of plan.subtasks || []) {
        const subLi = document.createElement("li");
        subLi.className = "task-subtask-card";
        const subHead = document.createElement("div");
        subHead.className = "task-subtask-head";
        const subTitle = document.createElement("strong");
        subTitle.dir = "auto";
        subTitle.textContent = `${sanitizeTaskText(sub.id, 40, { singleLine: true })}: ${sanitizeTaskText(sub.title, 120, { singleLine: true })}`;
        const subState = document.createElement("span");
        subState.className = "task-status-pill";
        subState.dataset.status = sub.is_simulated ? "simulated" : (sub.state === "blocked" ? "blocked" : "draft");
        subState.textContent = formatSubtaskState(sub.state, language);
        subHead.append(subTitle, subState);

        const subGoal = document.createElement("p");
        subGoal.className = "task-subtask-goal";
        subGoal.dir = "auto";
        subGoal.textContent = sanitizeTaskText(sub.goal, 400);

        const progressRow = document.createElement("div");
        progressRow.className = "task-progress-row";
        const progressEl = document.createElement("progress");
        progressEl.className = "task-progress-bar";
        progressEl.max = 100;
        progressEl.value = typeof sub.progress_percent === "number"
          ? Math.max(0, Math.min(100, sub.progress_percent)) : 0;
        const progressLabel = document.createElement("span");
        progressLabel.textContent = formatSimulatedProgress(sub.progress_percent, sub.is_simulated, language);
        progressRow.append(progressEl, progressLabel);

        subLi.append(subHead, subGoal, progressRow);

        const deps = Array.isArray(sub.dependencies) && sub.dependencies.length
          ? `${ar ? "تعتمد على" : "Depends on"}: ${sub.dependencies.map((d) => sanitizeTaskText(d, 40, { singleLine: true })).join(", ")}`
          : (ar ? "تعتمد على: لا توجد تبعيات" : "Depends on: none");
        const capsText = Array.isArray(sub.required_capabilities) && sub.required_capabilities.length
          ? ` · ${ar ? "الصلاحيات" : "Capabilities"}: ${sub.required_capabilities.map((c) => sanitizeTaskText(String(c), 60, { singleLine: true })).join(", ")}`
          : "";
        const depsNote = document.createElement("p");
        depsNote.className = "task-deps-note";
        depsNote.textContent = `${deps}${capsText}`;
        subLi.append(depsNote);

        if (sub.result_summary) {
          const subSummary = document.createElement("p");
          subSummary.className = "task-deps-note";
          subSummary.dir = "auto";
          subSummary.textContent = sanitizeTaskText(sub.result_summary, 300);
          subLi.append(subSummary);
        }
        subList.append(subLi);
      }
      card.append(subList);
      taskDetailPlans.append(card);
    }
  }
  renderTaskEvents();
}

function renderTaskControlCenter() {
  setTaskStatusCopy(taskStatusCopy[0], taskStatusCopy[1], taskStatusCopy[2]);
  renderTaskSummary();
  renderTaskList();
  renderTaskDetail();
}

function clearTaskControlCenter({ en, ar, isError = false, preserveFilter = false } = {}) {
  clearTaskControlState(taskState, { preserveFilter });
  if (!preserveFilter) {
    taskStatusFilter.value = "all";
    taskIncludeHistory.checked = true;
    taskIncludeArchived.checked = false;
  }
  const defaultEn = authenticated
    ? "Select an authorized workspace to inspect its tasks."
    : "Pair this browser and select an authorized workspace to inspect tasks.";
  const defaultAr = authenticated
    ? "اختر مساحة عمل مصرحًا بها لفحص مهامها."
    : "اربط هذا المتصفح واختر مساحة عمل مصرحًا بها لفحص المهام.";
  setTaskStatusCopy(en ?? defaultEn, ar ?? defaultAr, isError);
  renderTaskSummary();
  renderTaskList();
  renderTaskDetail();
}

async function loadTaskList({ offset = 0, refreshDetail = false, clearMissingDetail = false } = {}) {
  if (!authenticated || !activeWorkspaceId || workspaceServiceUnavailable) {
    clearTaskControlCenter();
    return;
  }
  const workspace = activeWorkspaceId;
  const previousSelected = refreshDetail ? taskState.selectedTaskId : null;
  const hadLoadingDetail = taskState.loadingDetail;
  const generation = beginTaskListLoad(taskState, { offset });
  if (hadLoadingDetail) {
    renderTaskDetail();
  }
  updateTaskControls();
  setTaskStatusCopy("Loading authorized workspace tasks…", "جارٍ تحميل مهام مساحة العمل المصرح بها…", false);
  try {
    const query = new URLSearchParams({
      limit: String(TASK_LIST_LIMIT),
      offset: String(taskState.listOffset),
      include_history: taskState.includeHistory ? "true" : "false",
      include_archived: taskState.includeArchived ? "true" : "false",
      status: taskState.statusFilter,
    });
    const response = await fetch(`/api/v1/tasks?${query.toString()}`, {
      credentials: "same-origin",
      cache: "no-store",
    });
    const value = await responseJson(response);
    if (generation !== taskState.generation || workspace !== activeWorkspaceId) return;
    if (response.status === 401) {
      clearTaskControlCenter();
      markSessionExpired();
      return;
    }
    if (response.status === 403) {
      clearTaskControlCenter({
        en: taskControlError(value.error || "manage_permission_required", "en"),
        ar: taskControlError(value.error || "manage_permission_required", "ar"),
        isError: true,
        preserveFilter: true,
      });
      return;
    }
    if (response.status === 404 && value.error === "workspace_unavailable") {
      activeWorkspaceId = null;
      clearEphemeralWorkspaceView();
      await loadWorkspaces(null).catch(() => {});
      return;
    }
    if (response.status === 409 && value.error === "workspace_selection_changed") {
      clearTaskControlCenter();
      await restoreSession();
      return;
    }
    if (!response.ok) {
      throw new Error(value.error || "task_service_unavailable");
    }
    const applied = applyTaskListResponse(taskState, {
      generation,
      workspaceId: workspace,
      payload: value,
      clearMissingDetail,
    });
    if (!applied.applied) {
      throw new Error("task_service_unavailable");
    }
    const totalVisible = taskState.summary.totalVisible;
    const truncatedSuffixEn = taskState.summary.windowTruncated
      ? ` (capped at latest ${taskState.summary.windowLimit} tasks)` : "";
    const truncatedSuffixAr = taskState.summary.windowTruncated
      ? ` (محدودة بأحدث ${taskState.summary.windowLimit} مهمة)` : "";
    setTaskStatusCopy(
      totalVisible
        ? `Read-only view · ${totalVisible} visible task(s) in this workspace${truncatedSuffixEn}.`
        : "Read-only view · No tasks recorded in this workspace yet.",
      totalVisible
        ? `عرض للقراءة فقط · ${totalVisible} مهمة مرئية في مساحة العمل${truncatedSuffixAr}.`
        : "عرض للقراءة فقط · لا توجد مهام مسجلة في مساحة العمل هذه بعد.",
      false,
    );
    renderTaskSummary();
    renderTaskList();
    if (previousSelected) {
      await selectTask(previousSelected);
    } else {
      renderTaskDetail();
    }
  } catch (error) {
    if (generation === taskState.generation) {
      const code = error?.message || "task_service_unavailable";
      clearTaskControlCenter({
        en: taskControlError(code, "en"),
        ar: taskControlError(code, "ar"),
        isError: true,
        preserveFilter: true,
      });
    }
  } finally {
    if (generation === taskState.generation) {
      taskState.loadingList = false;
      updateTaskControls();
    }
  }
}

async function selectTask(taskId) {
  if (!authenticated || !activeWorkspaceId || workspaceServiceUnavailable || typeof taskId !== "string") return;
  const workspace = activeWorkspaceId;
  const { generation, detailGeneration } = beginTaskDetailLoad(taskState, taskId);
  renderTaskList();
  taskDetailContent.hidden = true;
  taskDetailEmpty.hidden = false;
  taskDetailEmpty.textContent = language === "ar" ? "جارٍ تحميل تفاصيل المهمة…" : "Loading task details…";
  updateTaskControls();
  try {
    const encoded = encodeURIComponent(taskId);
    const [detailResp, plansResp, eventsResp] = await Promise.all([
      fetch(`/api/v1/tasks/${encoded}`, { credentials: "same-origin", cache: "no-store" }),
      fetch(`/api/v1/tasks/${encoded}/plans?limit=${TASK_PLANS_LIMIT}&offset=0`, { credentials: "same-origin", cache: "no-store" }),
      fetch(`/api/v1/tasks/${encoded}/events?limit=${TASK_EVENTS_LIMIT}&offset=0`, { credentials: "same-origin", cache: "no-store" }),
    ]);
    const [detailPayload, plansPayload, eventsPayload] = await Promise.all([
      responseJson(detailResp),
      responseJson(plansResp),
      responseJson(eventsResp),
    ]);
    if (generation !== taskState.generation || detailGeneration !== taskState.detailGeneration
        || workspace !== activeWorkspaceId) return;
    if (detailResp.status === 401 || plansResp.status === 401 || eventsResp.status === 401) {
      clearTaskControlCenter();
      markSessionExpired();
      return;
    }
    if (detailResp.status === 403 || plansResp.status === 403 || eventsResp.status === 403) {
      clearTaskControlCenter({
        en: taskControlError("manage_permission_required", "en"),
        ar: taskControlError("manage_permission_required", "ar"),
        isError: true,
        preserveFilter: true,
      });
      return;
    }
    if (detailResp.status === 409 || plansResp.status === 409 || eventsResp.status === 409) {
      clearTaskControlCenter();
      await restoreSession();
      return;
    }
    if (detailResp.status === 404 || plansResp.status === 404 || eventsResp.status === 404) {
      const errCode = detailPayload.error || plansPayload.error || eventsPayload.error || "task_unavailable";
      if (errCode === "workspace_unavailable") {
        activeWorkspaceId = null;
        clearEphemeralWorkspaceView();
        await loadWorkspaces(null).catch(() => {});
        return;
      }
      clearTaskDetailState(taskState, { detailErrorCode: "task_unavailable" });
      renderTaskDetail();
      await loadTaskList({ offset: taskState.listOffset });
      return;
    }
    if (!detailResp.ok || !plansResp.ok || !eventsResp.ok) {
      const errCode = detailPayload.error || plansPayload.error || eventsPayload.error || "task_service_unavailable";
      throw new Error(errCode);
    }
    const applied = applyTaskDetailResponse(taskState, {
      generation,
      detailGeneration,
      workspaceId: workspace,
      taskId,
      detailPayload,
      plansPayload,
      eventsPayload,
    });
    if (!applied.applied) {
      throw new Error("task_service_unavailable");
    }
    renderTaskList();
    renderTaskDetail();
  } catch (error) {
    if (generation === taskState.generation && detailGeneration === taskState.detailGeneration) {
      clearTaskDetailState(taskState, { detailErrorCode: error?.message || "task_service_unavailable" });
      renderTaskDetail();
    }
  } finally {
    if (generation === taskState.generation && detailGeneration === taskState.detailGeneration) {
      taskState.loadingDetail = false;
      updateTaskControls();
    }
  }
}

async function loadTaskEventsPage(offset) {
  if (!authenticated || !activeWorkspaceId || !taskState.selectedTaskId || taskState.loadingDetail) return;
  const workspace = activeWorkspaceId;
  const taskId = taskState.selectedTaskId;
  const generation = taskState.generation;
  const detailGeneration = taskState.detailGeneration;
  taskState.loadingDetail = true;
  updateTaskControls();
  try {
    const safeOffset = Math.max(0, offset);
    const response = await fetch(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/events?limit=${TASK_EVENTS_LIMIT}&offset=${safeOffset}`,
      { credentials: "same-origin", cache: "no-store" },
    );
    const payload = await responseJson(response);
    if (generation !== taskState.generation || detailGeneration !== taskState.detailGeneration
        || workspace !== activeWorkspaceId) return;
    if (response.status === 401) {
      clearTaskControlCenter();
      markSessionExpired();
      return;
    }
    if (response.status === 403) {
      clearTaskControlCenter({
        en: taskControlError("manage_permission_required", "en"),
        ar: taskControlError("manage_permission_required", "ar"),
        isError: true,
        preserveFilter: true,
      });
      return;
    }
    if (response.status === 409) {
      clearTaskControlCenter();
      await restoreSession();
      return;
    }
    if (response.status === 404) {
      if (payload.error === "workspace_unavailable") {
        activeWorkspaceId = null;
        clearEphemeralWorkspaceView();
        await loadWorkspaces(null).catch(() => {});
        return;
      }
      clearTaskDetailState(taskState, { detailErrorCode: "task_unavailable" });
      renderTaskDetail();
      await loadTaskList({ offset: taskState.listOffset });
      return;
    }
    if (!response.ok) throw new Error(payload.error || "task_service_unavailable");
    const applied = applyTaskEventsPageResponse(taskState, {
      generation, detailGeneration, workspaceId: workspace, taskId, eventsPayload: payload,
    });
    if (applied.applied) {
      taskState.eventsOffset = safeOffset;
      renderTaskEvents();
    }
  } catch (error) {
    if (generation === taskState.generation && detailGeneration === taskState.detailGeneration) {
      setTaskStatusCopy(
        taskControlError(error?.message || "task_service_unavailable", "en"),
        taskControlError(error?.message || "task_service_unavailable", "ar"),
        true,
      );
    }
  } finally {
    if (generation === taskState.generation && detailGeneration === taskState.detailGeneration) {
      taskState.loadingDetail = false;
      updateTaskControls();
    }
  }
}

function clearEphemeralWorkspaceView() {
  if (candidateEditorDialog.open) candidateEditorDialog.close();
  if (candidateReviewDialog.open) candidateReviewDialog.close();
  reviewCandidate = null;
  chatTranscript.replaceChildren();
  chatTranscript.hidden = true;
  setStatusLine("");
  citationList.replaceChildren();
  evidencePanel.hidden = true;
  latestEvidenceRequestId = null;
  latestCitations = [];
  latestEvidenceResponse = null;
  pendingCandidates.replaceChildren();
  memoryStatus.textContent = "";
  createCandidateOpen.disabled = !authenticated || !activeWorkspaceId;
  developerQuestion.value = "";
  developerSearch.value = "";
  clearDeveloperResults();
  developerLoadGeneration += 1;
  clearDeveloperGraph();
  clearTaskControlCenter();
  clearConversationMemoryPanel();
  clearProactivityState(proactivityState);
  renderProactivity();
  interruptVoice({ clearReply: true });
}

function setPairError(message = "") {
  pairError.textContent = message;
  pairError.hidden = !message;
}

function setAuthenticated(value, token = null, model = null, provider = null, showPairing = true) {
  authenticated = Boolean(value);
  csrfToken = authenticated && typeof token === "string" ? token : null;
  configuredModel = authenticated && typeof model === "string" ? model : null;
  configuredProvider = authenticated && typeof provider === "string" ? provider : null;
  chatInput.disabled = !authenticated || sending;
  chatSend.disabled = !authenticated || sending;
  signOut.hidden = !authenticated;
  workspaceSelect.disabled = !authenticated;
  if (!authenticated) {
    teardownVoiceForSessionEnd();
    activeWorkspaceId = null;
    developerRepositories = [];
    activeDeveloperRepoId = null;
    developerRepoSelect.replaceChildren();
    clearDeveloperResults();
    developerLoadGeneration += 1;
    clearDeveloperGraph();
    setDeveloperState("Pair and select a workspace to load repositories.", "اربط الجلسة واختر مساحة عمل لتحميل المستودعات.");
    workspaceServiceUnavailable = false;
    workspaceSelect.replaceChildren();
    clearEphemeralWorkspaceView();
    setWorkspaceState("Pair this browser to load authorized workspaces.", "اربط هذا المتصفح لتحميل مساحات العمل المصرح بها.");
  }
  if (authenticated) {
    setChatState("ready", "ready");
    if (value.persistence_paused === true) {
      setStatusLine([
        "Saved history is read-only because workspace write access is unavailable. This turn remains session-only.",
        "السجل المحفوظ للقراءة فقط لعدم توفر صلاحية الكتابة في مساحة العمل. تبقى هذه الرسالة ضمن الجلسة فقط."
      ], true);
    }
    if (pairingDialog.open) pairingDialog.close();
  } else {
    chatTranscript.replaceChildren();
    chatTranscript.hidden = true;
    setStatusLine("");
    if (showPairing) {
      setChatState("pairing", "checking");
      if (!pairingDialog.open) pairingDialog.showModal();
      queueMicrotask(() => pairCode.focus());
    }
  }
  updateChatInputPlaceholder();
  renderVoiceUI();
}

function markSessionExpired() {
  setAuthenticated(false, null, null, null, false);
  setChatState("restart_required", "error");
  setStatusLine([
    "Session expired. Restart the PowerShell service, then reload to pair again.",
    "انتهت الجلسة. أعد تشغيل خدمة PowerShell ثم أعد تحميل الصفحة للربط مجددًا."
  ], true);
}

function releaseMicrophone() {
  if (voiceCaptureStopTimer !== null) {
    clearTimeout(voiceCaptureStopTimer);
    voiceCaptureStopTimer = null;
  }
  if (voiceProcessorNode) {
    try {
      voiceProcessorNode.onaudioprocess = null;
      voiceProcessorNode.disconnect();
    } catch (_error) {
      // Ignore already-disconnected nodes.
    }
    voiceProcessorNode = null;
  }
  if (voiceSourceNode) {
    try {
      voiceSourceNode.disconnect();
    } catch (_error) {
      // Ignore already-disconnected nodes.
    }
    voiceSourceNode = null;
  }
  if (voiceSilenceGain) {
    try {
      voiceSilenceGain.disconnect();
    } catch (_error) {
      // Ignore already-disconnected nodes.
    }
    voiceSilenceGain = null;
  }
  if (voiceMediaStream) {
    for (const track of voiceMediaStream.getTracks()) {
      try {
        track.stop();
      } catch (_error) {
        // Best-effort track stop.
      }
    }
    voiceMediaStream = null;
  }
  if (voiceAudioContext) {
    const context = voiceAudioContext;
    voiceAudioContext = null;
    if (typeof context.close === "function" && context.state !== "closed") {
      context.close().catch(() => {});
    }
  }
}

function revokeVoiceAudioObjectUrl() {
  if (voiceAudioObjectUrl) {
    URL.revokeObjectURL(voiceAudioObjectUrl);
    voiceAudioObjectUrl = null;
  }
  voiceCachedReplyKey = null;
}

function stopVoicePlayback({ revokeAudio = false } = {}) {
  if (voiceAudioElement) {
    voiceAudioElement.onended = null;
    voiceAudioElement.onerror = null;
    try {
      voiceAudioElement.pause();
      voiceAudioElement.currentTime = 0;
    } catch (_error) {
      // Ignore playback stop errors.
    }
    if (revokeAudio) {
      voiceAudioElement.src = "";
      voiceAudioElement = null;
    }
  }
  if (revokeAudio) {
    revokeVoiceAudioObjectUrl();
  }
}

function abortVoiceRequests() {
  if (voiceAbortController) {
    try {
      voiceAbortController.abort();
    } catch (_error) {
      // Ignore abort errors.
    }
    voiceAbortController = null;
  }
}

function interruptVoice({ clearReply = false } = {}) {
  abortVoiceRequests();
  releaseMicrophone();
  voiceCapturedChunks = [];
  stopVoicePlayback({ revokeAudio: clearReply });
  voiceState = interruptVoiceState(voiceState, { clearReply });
  renderVoiceUI();
}

function teardownVoiceForSessionEnd() {
  abortVoiceRequests();
  releaseMicrophone();
  voiceCapturedChunks = [];
  stopVoicePlayback({ revokeAudio: true });
  voiceState = resetVoiceStateForSessionEnd(voiceState);
  renderVoiceUI();
}

function renderVoiceUI() {
  if (!voiceStateBadge || !voicePttButton) return;
  const listening = voiceState.status === "listening";
  const requestingPermission = voiceState.status === "requesting_permission";
  const active = requestingPermission || listening || voiceState.status === "transcribing"
    || voiceState.status === "thinking" || voiceState.status === "speaking";

  voiceStateBadge.dataset.state = voiceState.status;
  voiceStateText.textContent = voiceStateLabel(voiceState.status, language);

  if (voicePostureBadge) {
    const backendBits = [];
    if (voiceState.sttBackend) backendBits.push(`STT: ${voiceState.sttBackend}`);
    if (voiceState.ttsBackend) backendBits.push(`TTS: ${voiceState.ttsBackend}`);
    const suffix = backendBits.length ? ` · ${backendBits.join(" · ")}` : "";
    voicePostureBadge.textContent = (language === "ar"
      ? "اضغط للتحدث · محلي فقط · لا يُحفظ الصوت"
      : "PUSH-TO-TALK · LOCAL ONLY · NO AUDIO SAVED") + suffix;
  }

  const canStart = canStartPushToTalk(voiceState, {
    authenticated,
    sending,
    workspaceServiceUnavailable,
  });
  voicePttButton.disabled = !(requestingPermission || listening || canStart);
  voicePttButton.setAttribute("aria-pressed", String(listening || requestingPermission));
  voicePttLabel.textContent = requestingPermission
    ? (language === "ar" ? "إلغاء الطلب" : "Cancel request")
    : listening
      ? (language === "ar" ? "إيقاف الإملاء" : "Stop mic")
      : (language === "ar" ? "تحدث" : "Talk");
  voicePttButton.title = !authenticated
    ? (language === "ar" ? "اربط المتصفح أولًا" : "Pair browser first")
    : !voiceState.sttConfigured
      ? (language === "ar" ? "التحويل المحلي للكلام غير مهيأ" : "Local speech-to-text is not configured")
      : (language === "ar" ? "اضغط للتحدث محليًا" : "Push to talk locally");

  voicePlayButton.disabled = !authenticated || !voiceState.ttsConfigured
    || !voiceState.lastReplyText || listening || voiceState.status === "transcribing"
    || voiceState.status === "thinking";
  voicePlayButton.textContent = language === "ar" ? "تشغيل" : "Play";

  voiceStopButton.disabled = !active;
  voiceStopButton.textContent = language === "ar" ? "إيقاف" : "Stop";

  voiceMuteButton.setAttribute("aria-pressed", String(voiceState.muted));
  voiceMuteButton.textContent = voiceState.muted
    ? (language === "ar" ? "مكتوم" : "Muted")
    : (language === "ar" ? "كتم" : "Mute");

  voiceTranscriptToggle.setAttribute("aria-pressed", String(voiceState.showTranscript));
  voiceTranscriptToggle.textContent = voiceState.showTranscript
    ? (language === "ar" ? "إخفاء النص" : "Hide transcript")
    : (language === "ar" ? "إظهار النص" : "Show transcript");

  const hasTranscriptContent = Boolean(voiceState.lastTranscript || voiceState.lastReplyText);
  voiceTranscriptBox.hidden = !voiceState.showTranscript || !hasTranscriptContent;
  voiceRecognizedText.textContent = voiceState.lastTranscript
    ? `${language === "ar" ? "المسموع:" : "Heard:"} ${voiceState.lastTranscript}`
    : "";
  voiceSpokenMeta.textContent = voiceState.lastReplyText
    ? (voiceState.ttsConfigured
      ? (language === "ar"
        ? `آخر رد جاهز للتشغيل المحلي (${voiceState.ttsBackend ?? "محلي"}).`
        : `Latest reply ready for local playback (${voiceState.ttsBackend ?? "local"}).`)
      : (language === "ar"
        ? "الرد النصي متاح. توليد الصوت المحلي غير مهيأ."
        : "Text reply available. Local speech synthesis is not configured."))
    : "";

  if (voiceState.status === "error" && voiceState.errorCode) {
    voiceErrorLine.textContent = voiceErrorMessage(voiceState.errorCode, language);
    voiceErrorLine.hidden = false;
  } else {
    voiceErrorLine.textContent = "";
    voiceErrorLine.hidden = true;
  }
}

async function loadVoiceStatus() {
  if (!authenticated) {
    teardownVoiceForSessionEnd();
    return;
  }
  const statusSessionToken = csrfToken;
  const statusGeneration = voiceState.generation;
  try {
    const response = await fetch("/api/v1/voice/status", { credentials: "same-origin", cache: "no-store" });
    if (response.status === 401) {
      markSessionExpired();
      return;
    }
    if (!response.ok) {
      voiceState = applyVoiceStatus(voiceState, null);
      renderVoiceUI();
      return;
    }
    const payload = await responseJson(response);
    if (!authenticated || csrfToken !== statusSessionToken
        || voiceState.generation !== statusGeneration) return;
    voiceState = applyVoiceStatus(voiceState, payload);
  } catch (_error) {
    if (!authenticated || csrfToken !== statusSessionToken
        || voiceState.generation !== statusGeneration) return;
    voiceState = applyVoiceStatus(voiceState, null);
  }
  renderVoiceUI();
}

async function handlePushToTalkClick() {
  if (!authenticated || workspaceServiceUnavailable) return;
  if (voiceState.status === "requesting_permission") {
    interruptVoice({ clearReply: false });
    return;
  }
  if (voiceState.status === "listening") {
    await stopListeningAndTranscribe();
    return;
  }
  if (voiceState.status === "speaking") {
    interruptVoice({ clearReply: false });
  } else if (voiceState.status === "thinking") {
    interruptVoice({ clearReply: false });
  }
  if (!canStartPushToTalk(voiceState, { authenticated, sending, workspaceServiceUnavailable })) {
    return;
  }
  await startVoiceListening();
}

async function startVoiceListening() {
  if (voiceMicAcquisitionPending) return;
  if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
    voiceState = transitionVoiceState(voiceState, "error", { errorCode: "mic_unavailable" });
    renderVoiceUI();
    return;
  }
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (typeof AudioCtx !== "function") {
    voiceState = transitionVoiceState(voiceState, "error", { errorCode: "mic_unavailable" });
    renderVoiceUI();
    return;
  }
  stopVoicePlayback({ revokeAudio: false });
  releaseMicrophone();
  voiceCapturedChunks = [];
  const generation = voiceState.generation;
  const sessionToken = csrfToken;
  const workspaceId = activeWorkspaceId;
  voiceMicAcquisitionPending = true;
  voiceState = transitionVoiceState(voiceState, "requesting_permission");
  renderVoiceUI();

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
  } catch (error) {
    voiceMicAcquisitionPending = false;
    if (generation !== voiceState.generation || !authenticated || sessionToken !== csrfToken
        || workspaceId !== activeWorkspaceId) return;
    const denied = error && (error.name === "NotAllowedError" || error.name === "PermissionDeniedError");
    voiceState = transitionVoiceState(voiceState, "error", {
      errorCode: denied ? "mic_permission_denied" : "mic_unavailable",
    });
    renderVoiceUI();
    return;
  }
  voiceMicAcquisitionPending = false;

  if (!authenticated || sessionToken !== csrfToken || workspaceId !== activeWorkspaceId
      || generation !== voiceState.generation) {
    for (const track of stream.getTracks()) track.stop();
    return;
  }

  try {
    voiceMediaStream = stream;
    voiceAudioContext = new AudioCtx();
    if (voiceAudioContext.state === "suspended") {
      await voiceAudioContext.resume();
    }
    voiceCaptureSampleRate = voiceAudioContext.sampleRate || 16000;
    voiceSourceNode = voiceAudioContext.createMediaStreamSource(stream);
    voiceProcessorNode = voiceAudioContext.createScriptProcessor(4096, 1, 1);
    voiceSilenceGain = voiceAudioContext.createGain();
    voiceSilenceGain.gain.value = 0;

    const maxSamples = Math.ceil(voiceCaptureSampleRate * voiceState.maxAudioSeconds);
    let capturedSamples = 0;
    voiceProcessorNode.onaudioprocess = (event) => {
      if (voiceState.status !== "listening") return;
      const channel = event.inputBuffer.getChannelData(0);
      if (!channel || !channel.length) return;
      voiceCapturedChunks.push(new Float32Array(channel));
      capturedSamples += channel.length;
      if (capturedSamples >= maxSamples) {
        stopListeningAndTranscribe().catch(() => {});
      }
    };
    voiceSourceNode.connect(voiceProcessorNode);
    voiceProcessorNode.connect(voiceSilenceGain);
    voiceSilenceGain.connect(voiceAudioContext.destination);

    voiceState = transitionVoiceState(voiceState, "listening");
    renderVoiceUI();

    voiceCaptureStopTimer = setTimeout(() => {
      if (voiceState.status === "listening") {
        stopListeningAndTranscribe().catch(() => {});
      }
    }, Math.max(1000, Math.floor(voiceState.maxAudioSeconds * 1000)));
  } catch (_error) {
    if (generation !== voiceState.generation || !authenticated || sessionToken !== csrfToken
        || workspaceId !== activeWorkspaceId) {
      releaseMicrophone();
      return;
    }
    releaseMicrophone();
    voiceCapturedChunks = [];
    voiceState = transitionVoiceState(voiceState, "error", { errorCode: "mic_unavailable" });
    renderVoiceUI();
  }
}

async function stopListeningAndTranscribe() {
  if (voiceState.status !== "listening") return;
  const chunks = voiceCapturedChunks;
  const sampleRate = voiceCaptureSampleRate;
  voiceCapturedChunks = [];
  releaseMicrophone();
  const generation = voiceState.generation;
  const sessionToken = csrfToken;
  const workspaceId = activeWorkspaceId;

  let encoded;
  try {
    encoded = encodePcm16Wav(chunks, sampleRate, {
      maxBytes: voiceState.maxAudioBytes,
      maxSeconds: voiceState.maxAudioSeconds,
    });
  } catch (error) {
    voiceState = transitionVoiceState(voiceState, "error", {
      errorCode: error?.code || "invalid_audio",
    });
    renderVoiceUI();
    return;
  }

  voiceState = transitionVoiceState(voiceState, "transcribing");
  renderVoiceUI();

  abortVoiceRequests();
  const controller = new AbortController();
  voiceAbortController = controller;
  const timeout = setTimeout(() => controller.abort(), 90000);
  try {
    const response = await fetch("/api/v1/voice/transcribe", {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
      headers: {
        "Content-Type": "audio/wav",
        "X-Jarvis-CSRF": csrfToken,
        "X-Jarvis-Voice-Language": "auto",
      },
      body: encoded.wavBytes,
    });
    const value = await responseJson(response);
    if (!isVoiceOperationCurrent(generation, sessionToken, workspaceId)) return;
    if (response.status === 401) {
      markSessionExpired();
      return;
    }
    if (!response.ok) {
      voiceState = transitionVoiceState(voiceState, "error", {
        errorCode: value.error || "transcription_failed",
      });
      renderVoiceUI();
      return;
    }
    const transcript = typeof value.text === "string" ? value.text.trim() : "";
    if (!transcript) {
      voiceState = transitionVoiceState(voiceState, "error", { errorCode: "empty_transcript" });
      renderVoiceUI();
      return;
    }
    const replyLang = value.language === "ar" || value.language === "en"
      ? value.language
      : detectReplyLanguage(transcript, language);
    voiceState = transitionVoiceState(voiceState, "thinking", {
      transcript,
      replyLanguage: replyLang,
    });
    renderVoiceUI();
    await sendChatMessage(transcript, { fromVoice: true, voiceGeneration: generation,
      voiceSessionToken: sessionToken, voiceWorkspaceId: workspaceId });
  } catch (error) {
    if (!isVoiceOperationCurrent(generation, sessionToken, workspaceId)) return;
    voiceState = transitionVoiceState(voiceState, "error", {
      errorCode: error?.name === "AbortError" ? "transcription_timeout" : "transcription_failed",
    });
    renderVoiceUI();
  } finally {
    clearTimeout(timeout);
    if (voiceAbortController === controller) voiceAbortController = null;
  }
}

function isVoiceOperationCurrent(generation, sessionToken, workspaceId) {
  return authenticated && csrfToken === sessionToken && activeWorkspaceId === workspaceId
    && voiceState.generation === generation;
}

async function playSpokenReply({ text = voiceState.lastReplyText, replyLanguage = voiceState.lastReplyLanguage, voiceGeneration = voiceState.generation } = {}) {
  if (!authenticated || !csrfToken || !text) return;
  if (!voiceState.ttsConfigured) {
    voiceState = transitionVoiceState(voiceState, "error", { errorCode: "voice_tts_not_configured" });
    renderVoiceUI();
    return;
  }
  const targetLang = detectReplyLanguage(text, replyLanguage || language);
  const sessionToken = csrfToken;
  const workspaceId = activeWorkspaceId;
  const cacheKey = `${targetLang}:${text}`;

  stopVoicePlayback({ revokeAudio: false });

  const languageError = voiceLanguageUnavailableError(voiceState, targetLang);
  if (languageError) {
    abortVoiceRequests();
    voiceState = transitionVoiceState(voiceState, "error", { errorCode: languageError });
    renderVoiceUI();
    return;
  }

  if (voiceAudioObjectUrl && voiceCachedReplyKey === cacheKey) {
    await startAudioElementPlayback(voiceAudioObjectUrl, voiceGeneration);
    return;
  }

  abortVoiceRequests();
  voiceState = transitionVoiceState(voiceState, "thinking");
  renderVoiceUI();
  const controller = new AbortController();
  voiceAbortController = controller;
  const timeout = setTimeout(() => controller.abort(), 90000);
  try {
    const response = await fetch("/api/v1/voice/synthesize", {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        "X-Jarvis-CSRF": csrfToken,
      },
      body: JSON.stringify({ text, language: targetLang }),
    });
    if (!isVoiceOperationCurrent(voiceGeneration, sessionToken, workspaceId)) return;
    if (response.status === 401) {
      markSessionExpired();
      return;
    }
    if (!response.ok) {
      const errPayload = await responseJson(response);
      voiceState = transitionVoiceState(voiceState, "error", {
        errorCode: errPayload.error || "synthesis_failed",
      });
      renderVoiceUI();
      return;
    }
    const contentType = (response.headers.get("Content-Type") || "").toLowerCase();
    if (!contentType.startsWith("audio/wav")) {
      voiceState = transitionVoiceState(voiceState, "error", { errorCode: "synthesis_failed" });
      renderVoiceUI();
      return;
    }
    const wavBlob = await response.blob();
    if (!isVoiceOperationCurrent(voiceGeneration, sessionToken, workspaceId)) return;
    revokeVoiceAudioObjectUrl();
    voiceAudioObjectUrl = URL.createObjectURL(wavBlob);
    voiceCachedReplyKey = cacheKey;
    voiceState = transitionVoiceState(voiceState, "speaking", {
      hasReplayAudio: true,
      replyLanguage: targetLang,
    });
    renderVoiceUI();
    await startAudioElementPlayback(voiceAudioObjectUrl, voiceGeneration);
  } catch (error) {
    if (!isVoiceOperationCurrent(voiceGeneration, sessionToken, workspaceId)) return;
    voiceState = transitionVoiceState(voiceState, "error", {
      errorCode: error?.name === "AbortError" ? "synthesis_timeout" : "synthesis_failed",
    });
    renderVoiceUI();
  } finally {
    clearTimeout(timeout);
    if (voiceAbortController === controller) voiceAbortController = null;
  }
}

async function startAudioElementPlayback(objectUrl, voiceGeneration) {
  if (voiceGeneration !== voiceState.generation) return;
  const audio = new Audio();
  audio.preload = "auto";
  audio.muted = voiceState.muted;
  audio.src = objectUrl;
  voiceAudioElement = audio;

  audio.onended = () => {
    if (voiceAudioElement === audio && voiceGeneration === voiceState.generation) {
      voiceState = transitionVoiceState(voiceState, "stopped");
      renderVoiceUI();
    }
  };
  audio.onerror = () => {
    if (voiceAudioElement === audio && voiceGeneration === voiceState.generation) {
      voiceState = transitionVoiceState(voiceState, "error", { errorCode: "playback_failed" });
      renderVoiceUI();
    }
  };

  voiceState = transitionVoiceState(voiceState, "speaking", { hasReplayAudio: true });
  renderVoiceUI();
  try {
    await audio.play();
  } catch (_error) {
    if (voiceGeneration === voiceState.generation) {
      voiceState = transitionVoiceState(voiceState, "error", { errorCode: "playback_failed" });
      renderVoiceUI();
    }
  }
}

function appendChatEntry(kind, text) {
  const entry = document.createElement("div");
  entry.className = `chat-entry ${kind}`;
  entry.textContent = text;
  entry.dir = "auto";
  chatTranscript.append(entry);
  chatTranscript.hidden = false;
  chatTranscript.scrollTop = chatTranscript.scrollHeight;
  return entry;
}

function errorMessage(code) {
  const proposalMessage = memoryProposalError(code, language);
  if (proposalMessage) return proposalMessage;
  const translations = CHAT_ERRORS[code] ?? CHAT_ERRORS.default;
  return translations[language === "ar" ? 1 : 0];
}

function outcomeText(outcome) {
  const text = {
    valid_citations: ["Sources are linked to this answer.", "المصادر مرتبطة بهذه الإجابة."],
    no_authorized_evidence: ["No authorized evidence was found; Jarvis abstained.", "لم يُعثر على دليل مصرّح به؛ امتنع جارفس عن الإجابة."],
    model_abstention: ["The model could not answer from the available evidence.", "لم يتمكن النموذج من الإجابة استنادًا إلى الأدلة المتاحة."],
    missing_citation: ["The model response was withheld because it had no citation.", "حُجبت إجابة النموذج لأنها لم تتضمن استشهادًا."],
    invalid_citations: ["The model response was withheld because a citation was invalid.", "حُجبت إجابة النموذج بسبب استشهاد غير صالح."]
  };
  const values = text[outcome] ?? ["Workspace answer", "إجابة مساحة العمل"];
  return values[language === "ar" ? 1 : 0];
}

function renderEvidence(value = null) {
  citationList.replaceChildren();
  latestEvidenceRequestId = value?.evidence_request_id ?? null;
  latestCitations = Array.isArray(value?.citations) ? value.citations : [];
  if (!value || typeof value.outcome !== "string") {
    evidencePanel.hidden = true;
    return;
  }
  evidencePanel.hidden = false;
  evidenceOutcome.textContent = outcomeText(value.outcome);
  const sources = value.outcome === "invalid_citations"
    ? (Array.isArray(value.evidence) ? value.evidence : []) : latestCitations;
  if (!sources.length) {
    const empty = document.createElement("p");
    empty.className = "memory-note";
    empty.textContent = language === "ar" ? "لا توجد مصادر لعرضها." : "No sources to display.";
    citationList.append(empty);
    return;
  }
  for (const source of sources) {
    const card = document.createElement("article");
    card.className = "citation-card";
    const meta = document.createElement("div");
    meta.className = "citation-meta";
    const label = document.createElement("strong");
    label.textContent = `[${source.label ?? "?"}]`;
    const kind = document.createElement("span");
    const isMemory = source.type === "memory";
    kind.textContent = isMemory
      ? (language === "ar" ? "ذاكرة معتمدة" : "Approved memory")
      : (language === "ar" ? "مستند نشط" : "Active document");
    meta.append(label, kind);
    if (source.source_status) {
      const status = document.createElement("span");
      const statuses = {
        historical_sources: language === "ar" ? "مصدر تاريخي" : "Historical source",
        active_sources: language === "ar" ? "مصدر نشط" : "Active source",
        active_document: language === "ar" ? "مستند نشط" : "Active document",
        user_authored: language === "ar" ? "مكتوبة من المستخدم" : "User-authored"
      };
      status.textContent = statuses[source.source_status] ?? source.source_status;
      meta.append(status);
    }
    if (source.relative_path) {
      const path = document.createElement("span");
      path.className = "citation-path";
      path.dir = "ltr";
      path.textContent = source.relative_path;
      meta.append(path);
    }
    card.append(meta);
    if (source.excerpt) {
      const excerpt = document.createElement("p");
      excerpt.className = "citation-excerpt";
      excerpt.dir = "auto";
      excerpt.textContent = source.excerpt;
      card.append(excerpt);
    }
    for (const linked of source.memory_sources ?? []) {
      const provenance = document.createElement("p");
      provenance.className = "citation-excerpt";
      provenance.textContent = `${language === "ar" ? "لقطة المصدر" : "Source snapshot"}: ${linked.relative_path ?? ""}`;
      provenance.dir = "auto";
      card.append(provenance);
    }
    citationList.append(card);
  }
}

async function loadPendingCandidates() {
  pendingCandidates.replaceChildren();
  if (!authenticated || !activeWorkspaceId) return;
  try {
    const response = await fetch("/api/v1/memory-candidates", { credentials: "same-origin", cache: "no-store" });
    const value = await responseJson(response);
    if (response.status === 401) {
      markSessionExpired();
      return;
    }
    if (!response.ok || !Array.isArray(value.candidates)) throw new Error(value.error || "candidate_unavailable");
    if (value.workspace_id !== activeWorkspaceId) {
      await restoreSession();
      return;
    }
    if (!value.candidates.length) {
      const empty = document.createElement("p");
      empty.className = "memory-note";
      empty.textContent = language === "ar" ? "لا توجد مقترحات معلّقة." : "No pending proposals.";
      pendingCandidates.append(empty);
      return;
    }
    for (const candidate of value.candidates) renderCandidateCard(candidate);
  } catch (error) {
    memoryStatus.textContent = errorMessage(error.message);
    if (error.message === "workspace_unavailable") {
      activeWorkspaceId = null;
      clearEphemeralWorkspaceView();
      await loadWorkspaces(null).catch(() => {});
    }
  }
}

function renderCandidateCard(candidate) {
  if (!candidate || typeof candidate.id !== "string" || typeof candidate.content !== "string") return;
  const card = document.createElement("article");
  card.className = "candidate-card";
  const content = document.createElement("p");
  content.dir = "auto";
  content.textContent = candidate.content;
  card.append(content);
  const meta = document.createElement("p");
  meta.className = "memory-note";
  meta.textContent = `${candidate.provenance_kind} · ${language === "ar" ? "الإصدار" : "revision"} ${candidate.revision}`;
  card.append(meta);
  for (const source of candidate.evidence ?? []) {
    const path = document.createElement("p");
    path.className = "citation-path";
    path.dir = "ltr";
    path.textContent = source.relative_path;
    card.append(path);
  }
  const actions = document.createElement("div");
  actions.className = "candidate-actions";
  const review = document.createElement("button");
  review.type = "button";
  review.className = "small-action";
  review.textContent = language === "ar" ? "راجع" : "Review";
  review.addEventListener("click", () => openCandidateReview(candidate.id));
  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "small-action";
  edit.textContent = language === "ar" ? "حرّر" : "Edit";
  edit.addEventListener("click", () => showCandidateEditor(card, candidate));
  actions.append(edit, review);
  card.append(actions);
  pendingCandidates.append(card);
}

function showCandidateEditor(card, candidate) {
  const form = document.createElement("form");
  form.className = "candidate-edit-form";
  const textarea = document.createElement("textarea");
  textarea.maxLength = 4000;
  textarea.required = true;
  textarea.dir = "auto";
  textarea.value = candidate.content;
  const save = document.createElement("button");
  save.type = "submit";
  save.className = "small-action";
  save.textContent = language === "ar" ? "احفظ التعديل" : "Save edit";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "small-action";
  cancel.textContent = language === "ar" ? "إلغاء" : "Cancel";
  cancel.addEventListener("click", () => form.remove());
  form.append(textarea, save, cancel);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    save.disabled = true;
    try {
      const response = await fetch(`/api/v1/memory-candidates/${encodeURIComponent(candidate.id)}`, {
        method: "PUT", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json", "X-Jarvis-CSRF": csrfToken },
        body: JSON.stringify({ expected_revision: candidate.revision, content: textarea.value,
          retain_existing_sources: true, expected_workspace_id: activeWorkspaceId })
      });
      const value = await responseJson(response);
      if (!response.ok) throw new Error(value.error || "candidate_changed");
      form.remove();
      await loadPendingCandidates();
    } catch (error) {
      memoryStatus.textContent = errorMessage(error.message);
      save.disabled = false;
    }
  });
  card.append(form);
  textarea.focus();
}

async function openCandidateReview(candidateId) {
  if (!authenticated || !activeWorkspaceId) return;
  $("#candidate-review-status").textContent = "";
  try {
    const response = await fetch(`/api/v1/memory-candidates/${encodeURIComponent(candidateId)}`, {
      credentials: "same-origin", cache: "no-store"
    });
    const value = await responseJson(response);
    if (!response.ok || !value.candidate) throw new Error(value.error || "candidate_changed");
    if (value.candidate.workspace_id !== activeWorkspaceId) {
      await restoreSession();
      return;
    }
    reviewCandidate = value.candidate;
    $("#review-candidate-content").textContent = reviewCandidate.content;
    const sources = $("#review-candidate-sources");
    sources.replaceChildren();
    const provenance = document.createElement("p");
    provenance.className = "review-source";
    provenance.textContent = `${language === "ar" ? "المنشأ" : "Provenance"}: ${reviewCandidate.provenance_kind}`;
    sources.append(provenance);
    for (const source of reviewCandidate.evidence ?? []) {
      const row = document.createElement("p");
      row.className = "review-source";
      row.dir = "auto";
      row.textContent = `${source.relative_path} · ${source.source_sha256.slice(0, 12)}`;
      sources.append(row);
    }
    candidateReviewDialog.showModal();
    $("#candidate-approve").focus();
  } catch (error) {
    memoryStatus.textContent = errorMessage(error.message);
  }
}

async function decideCandidate(action) {
  if (!reviewCandidate || !csrfToken) return;
  const buttons = [$("#candidate-approve"), $("#candidate-reject")];
  buttons.forEach((button) => { button.disabled = true; });
  $("#candidate-review-status").textContent = language === "ar" ? "جارٍ حفظ القرار…" : "Saving your decision…";
  try {
    const response = await fetch(`/api/v1/memory-candidates/${encodeURIComponent(reviewCandidate.id)}/${action}`, {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/json", "X-Jarvis-CSRF": csrfToken },
      body: JSON.stringify({ expected_revision: reviewCandidate.revision,
        content_sha256: reviewCandidate.content_sha256,
        evidence_sha256: reviewCandidate.evidence_sha256, confirmed_snapshot: true,
        expected_workspace_id: activeWorkspaceId })
    });
    const value = await responseJson(response);
    if (!response.ok) throw new Error(value.error || "candidate_changed");
    candidateReviewDialog.close();
    reviewCandidate = null;
    memoryStatus.textContent = action === "approve"
      ? (language === "ar" ? "تمت الموافقة على الذاكرة." : "Memory approved.")
      : (language === "ar" ? "رُفض المقترح." : "Proposal rejected.");
    await loadPendingCandidates();
  } catch (error) {
    $("#candidate-review-status").textContent = errorMessage(error.message);
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function openCandidateEditor() {
  if (!authenticated || !activeWorkspaceId) return;
  candidateEditorForm.reset();
  $("#candidate-editor-status").textContent = "";
  candidateContent.value = "";
  const latestAssistant = [...chatTranscript.querySelectorAll(".chat-entry.assistant:not(.pending):not(.error)")].at(-1);
  if (latestAssistant) candidateContent.value = latestAssistant.textContent.replace(/\[[DM][1-9][0-9]*\]/g, "").trim();
  candidateSourceOptions.replaceChildren();
  for (const citation of latestCitations.filter((item) => item.type === "document" && item.label)) {
    const label = document.createElement("label");
    label.className = "source-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = citation.label;
    const description = document.createElement("span");
    description.textContent = `[${citation.label}] ${citation.relative_path ?? citation.document_id ?? ""}`;
    description.dir = "auto";
    label.append(checkbox, description);
    candidateSourceOptions.append(label);
  }
  candidateSources.hidden = candidateSourceOptions.childElementCount === 0;
  candidateEditorDialog.showModal();
  candidateContent.focus();
}

async function submitMemoryCandidate(event) {
  event.preventDefault();
  if (!authenticated || !activeWorkspaceId || !csrfToken) return;
  const content = candidateContent.value.trim();
  const labels = [...candidateSourceOptions.querySelectorAll("input:checked")].map((item) => item.value);
  const userAuthored = candidateUserAuthored.checked;
  if (!content || (!userAuthored && (!latestEvidenceRequestId || !labels.length))) {
    $("#candidate-editor-status").textContent = errorMessage("evidence_selection_required");
    return;
  }
  const submit = $("#candidate-editor-submit");
  submit.disabled = true;
  try {
    const response = await fetch("/api/v1/memory-candidates", {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/json", "X-Jarvis-CSRF": csrfToken },
      body: JSON.stringify(memoryProposalPayload({
        content, userAuthored, workspaceId: activeWorkspaceId,
        evidenceRequestId: latestEvidenceRequestId, citationLabels: labels,
      }))
    });
    const value = await responseJson(response);
    if (response.status === 401) {
      markSessionExpired();
      throw new Error(value.error || "authentication_required");
    }
    if (!response.ok) throw new Error(value.error || "candidate_rejected");
    candidateEditorDialog.close();
    memoryStatus.textContent = language === "ar"
      ? "أُنشئ مقترح معلّق. راجعه قبل الموافقة."
      : "Pending proposal created. Review it before approval.";
    await loadPendingCandidates();
  } catch (error) {
    $("#candidate-editor-status").textContent = errorMessage(error.message);
  } finally {
    submit.disabled = false;
  }
}

function renderProactivity() {
  const ar = language === "ar";
  const ready = authenticated && Boolean(activeWorkspaceId)
    && !workspaceServiceUnavailable && proactivityState.status === "ready";
  const busy = proactivityState.status === "loading" || proactivityState.status === "saving";
  proactivityInterval.disabled = !ready || busy || !proactivityState.canEnable;
  proactivityCooldown.disabled = !ready || busy || !proactivityState.canEnable;
  proactivityEnable.disabled = !ready || busy || !proactivityState.canEnable || proactivityState.enabled;
  proactivitySave.disabled = !ready || busy || !proactivityState.canEnable;
  proactivityStop.disabled = !ready || busy || !proactivityState.canEnable || !proactivityState.enabled;
  proactivityRefresh.disabled = !authenticated || !activeWorkspaceId || busy;
  proactivityStatus.classList.toggle("error", proactivityState.status === "error");
  if (proactivityState.preferences) {
    proactivityInterval.value = String(proactivityState.preferences.check_interval_seconds);
    proactivityCooldown.value = String(proactivityState.preferences.cooldown_seconds);
  }
  if (busy) {
    proactivityStatus.textContent = ar ? "جارٍ تحميل الاقتراحات…" : "Loading suggestions…";
  } else if (!ready) {
    proactivityStatus.textContent = !authenticated
      ? (ar ? "اربط الجلسة لعرض إعدادات الاقتراحات." : "Pair this browser to view suggestion settings.")
      : !activeWorkspaceId
        ? (ar ? "اختر مساحة عمل لعرض الاقتراحات." : "Select a workspace to view suggestions.")
        : (ar ? "تعذر تحميل إعدادات الاقتراحات." : "Suggestion settings are unavailable.");
  } else if (!proactivityState.canEnable) {
    proactivityStatus.textContent = ar
      ? "تتطلب الاقتراحات إذن الكتابة الحالي في مساحة العمل."
      : "Suggestions require current write permission in this workspace.";
  } else {
    proactivityStatus.textContent = proactivityState.enabled
      ? (ar ? "الاقتراحات مفعّلة لهذه المساحة." : "Suggestions are enabled for this workspace.")
      : (ar ? "الاقتراحات متوقفة. فعّلها صراحةً للبدء." : "Suggestions are off. Explicitly enable them to begin.");
  }
  proactivitySuggestions.replaceChildren();
  if (ready && proactivityState.suggestions.length === 0) {
    const empty = document.createElement("p");
    empty.className = "proactivity-empty";
    empty.textContent = ar ? "لا توجد اقتراحات نشطة في هذه المساحة." : "No active suggestions in this workspace.";
    proactivitySuggestions.append(empty);
  }
  const reasonLabels = {
    mission_aged: ["Unfinished mission", "بعثة غير مكتملة"],
    mission_blocked: ["Blocked mission", "بعثة متوقفة"],
    mission_paused: ["Paused mission", "بعثة معلّقة"],
    mission_interrupted: ["Interrupted mission", "بعثة منقطعة"],
    task_blocked: ["Blocked task", "مهمة متوقفة"],
    task_interrupted: ["Interrupted task", "مهمة منقطعة"],
    legacy: ["Existing suggestion", "اقتراح موجود"],
  };
  for (const suggestion of proactivityState.suggestions) {
    const card = document.createElement("article");
    card.className = "proactivity-card";
    const heading = document.createElement("h3");
    heading.textContent = (reasonLabels[suggestion.reason_code] || reasonLabels.legacy)[ar ? 1 : 0];
    const evidence = document.createElement("p");
    evidence.className = "proactivity-evidence";
    evidence.textContent = `${suggestion.source_kind} · ${suggestion.source_task_id}`
      + (suggestion.source_mission_id ? ` · ${suggestion.source_mission_id}` : "");
    card.append(heading, evidence);
    if (suggestion.state === "snoozed" && suggestion.snoozed_until) {
      const snoozed = document.createElement("p");
      snoozed.className = "proactivity-note";
      snoozed.textContent = ar ? `مؤجل حتى ${suggestion.snoozed_until}` : `Snoozed until ${suggestion.snoozed_until}`;
      card.append(snoozed);
    }
    const actions = document.createElement("div");
    actions.className = "proactivity-actions";
    for (const [action, label] of [["accept", ["Acknowledge", "إقرار"]], ["dismiss", ["Dismiss", "تجاهل"]], ["snooze", ["Snooze 1 day", "تأجيل يوم"]]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "small-action";
      button.textContent = label[ar ? 1 : 0];
      button.disabled = !proactivityState.canEnable || suggestion.state === "snoozed"
        || !proactivityActionAvailable(proactivityState, suggestion.id);
      button.addEventListener("click", () => actOnSuggestion(suggestion.id, action));
      actions.append(button);
    }
    card.append(actions);
    proactivitySuggestions.append(card);
  }
}

async function loadProactivity() {
  const workspace = activeWorkspaceId;
  const generation = beginProactivityLoad(proactivityState, workspace);
  renderProactivity();
  if (!authenticated || !workspace) return;
  try {
    const response = await fetch("/api/v1/proactivity", {
      credentials: "same-origin", cache: "no-store",
    });
    const value = await responseJson(response);
    if (response.status === 401) { markSessionExpired(); return; }
    if (!response.ok) throw new Error(value.error || "proactivity_unavailable");
    applyProactivityResponse(proactivityState, generation, workspace, value);
  } catch (_error) {
    if (generation === proactivityState.generation && workspace === activeWorkspaceId) {
      proactivityState.status = "error";
    }
  }
  renderProactivity();
}

async function saveProactivityPreference(enabled) {
  if (!authenticated || !csrfToken || !activeWorkspaceId || !proactivityState.canEnable) return;
  const workspace = activeWorkspaceId;
  const generation = proactivityState.generation;
  proactivityState.status = "saving";
  renderProactivity();
  try {
    const response = await fetch("/api/v1/proactivity/preferences", {
      method: "PUT", credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/json", "X-Jarvis-CSRF": csrfToken },
      body: JSON.stringify({ enabled,
        check_interval_seconds: Number(proactivityInterval.value),
        cooldown_seconds: Number(proactivityCooldown.value) }),
    });
    const value = await responseJson(response);
    if (response.status === 401) { markSessionExpired(); return; }
    if (!response.ok) throw new Error(value.error || "proactivity_unavailable");
  } catch (_error) {
    if (generation === proactivityState.generation && workspace === activeWorkspaceId) {
      proactivityState.status = "error";
      renderProactivity();
      return;
    }
  }
  if (workspace === activeWorkspaceId) await loadProactivity();
}

async function actOnSuggestion(suggestionId, action) {
  if (!authenticated || !csrfToken || !activeWorkspaceId
      || !proactivityActionAvailable(proactivityState, suggestionId)) return;
  const workspace = activeWorkspaceId;
  const generation = proactivityState.generation;
  const payload = action === "snooze" ? { snooze_seconds: 86400 } : {};
  try {
    const response = await fetch(`/api/v1/proactivity/suggestions/${encodeURIComponent(suggestionId)}/${action}`, {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/json", "X-Jarvis-CSRF": csrfToken },
      body: JSON.stringify(payload),
    });
    const value = await responseJson(response);
    if (response.status === 401) { markSessionExpired(); return; }
    if (!response.ok) throw new Error(value.error || "suggestion_unavailable");
  } catch (_error) {
    if (generation === proactivityState.generation && workspace === activeWorkspaceId) {
      proactivityStatus.classList.add("error");
      proactivityStatus.textContent = language === "ar"
        ? "تعذر تحديث الاقتراح. حدّث القائمة وحاول مجددًا."
        : "Could not update the suggestion. Refresh the inbox and try again.";
    }
    return;
  }
  if (generation === proactivityState.generation && workspace === activeWorkspaceId) await loadProactivity();
}

async function responseJson(response) {
  try {
    const value = await response.json();
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch (_error) {
    return {};
  }
}

function conversationMemoryText(en, ar) {
  return language === "ar" ? ar : en;
}

function setConversationMemoryStatus(en, ar, isError = false) {
  conversationMemoryStatus.textContent = conversationMemoryText(en, ar);
  conversationMemoryStatus.classList.toggle("error", isError);
}

function clearConversationMemoryPanel() {
  conversationMemoryLoadGeneration += 1;
  conversationMemoryPanel.hidden = true;
  delete conversationMemoryPanel.dataset.enabled;
  conversationMemoryCanWrite = false;
  conversationMemoryEnabled.checked = false;
  conversationMemoryEnabled.disabled = true;
  conversationMemoryRetention.disabled = true;
  conversationMemorySave.disabled = true;
  conversationMemoryRefresh.disabled = true;
  conversationMemoryStatus.textContent = "";
  conversationMemoryList.replaceChildren();
  conversationMemoryTurns.replaceChildren();
  conversationMemoryDetail.hidden = true;
}

function updateConversationMemoryControls() {
  const wasEnabled = Boolean(conversationMemoryPanel.dataset.enabled === "true");
  conversationMemoryEnabled.disabled = !conversationMemoryCanWrite && !wasEnabled;
  conversationMemoryRetention.disabled = !conversationMemoryCanWrite;
  conversationMemorySave.disabled = !conversationMemoryCanWrite
    && !(wasEnabled && !conversationMemoryEnabled.checked);
}

function renderConversationMemoryList(conversations) {
  conversationMemoryList.replaceChildren();
  conversationMemoryDetail.hidden = true;
  conversationMemoryTurns.replaceChildren();
  if (!conversations.length) {
    const empty = document.createElement("li");
    empty.className = "hint";
    empty.textContent = conversationMemoryText("No saved conversations in this workspace.", "لا توجد محادثات محفوظة في مساحة العمل هذه.");
    conversationMemoryList.append(empty);
    return;
  }
  for (const conversation of conversations) {
    if (!conversation || typeof conversation.id !== "string") continue;
    const item = document.createElement("li");
    item.className = "conversation-memory-item";
    const open = document.createElement("button");
    open.type = "button";
    open.className = "conversation-memory-open";
    open.textContent = conversation.preview || conversationMemoryText("Saved conversation", "محادثة محفوظة");
    const meta = document.createElement("span");
    const updated = new Date(conversation.updated_at);
    const date = Number.isNaN(updated.valueOf()) ? "" : updated.toLocaleString(language === "ar" ? "ar" : "en-GB");
    meta.textContent = `${Number(conversation.turn_count) || 0} · ${date}`;
    open.append(meta);
    open.addEventListener("click", () => loadConversationMemoryDetail(conversation.id));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "small-action reject-action";
    remove.textContent = conversationMemoryText("Delete", "حذف");
    remove.addEventListener("click", () => deleteSavedConversation(conversation.id));
    item.append(open, remove);
    conversationMemoryList.append(item);
  }
}

function clearConversationTranscriptAfterDeletion() {
  chatTranscript.replaceChildren();
  chatTranscript.hidden = true;
  latestEvidenceRequestId = null;
  latestCitations = [];
  latestEvidenceResponse = null;
  renderEvidence(null);
  interruptVoice({ clearReply: true });
}

async function loadConversationMemory() {
  if (!authenticated || !csrfToken || !activeWorkspaceId || workspaceServiceUnavailable) {
    clearConversationMemoryPanel();
    return;
  }
  const generation = ++conversationMemoryLoadGeneration;
  const workspaceId = activeWorkspaceId;
  conversationMemoryPanel.hidden = false;
  conversationMemoryRefresh.disabled = true;
  setConversationMemoryStatus("Loading saved-conversation settings…", "جارٍ تحميل إعدادات المحادثات المحفوظة…");
  try {
    const response = await fetch("/api/v1/conversation-memory", {
      credentials: "same-origin", cache: "no-store",
    });
    const value = await responseJson(response);
    if (generation !== conversationMemoryLoadGeneration || workspaceId !== activeWorkspaceId) return;
    if (response.status === 401) { markSessionExpired(); return; }
    if (!response.ok || !value.settings || !Array.isArray(value.conversations)) {
      throw new Error(value.error || "conversation_memory_unavailable");
    }
    const settings = value.settings;
    conversationMemoryPanel.dataset.enabled = String(settings.enabled === true);
    conversationMemoryCanWrite = settings.can_write === true;
    conversationMemoryEnabled.checked = settings.enabled === true;
    conversationMemoryRetention.value = String([7, 30, 90, 180, 365].includes(settings.retention_days)
      ? settings.retention_days : 30);
    updateConversationMemoryControls();
    conversationMemoryRefresh.disabled = false;
    renderConversationMemoryList(settings.enabled === true ? value.conversations : []);
    if (settings.enabled === true && !conversationMemoryCanWrite) {
      setConversationMemoryStatus("Enabled, but workspace write access is unavailable. New turns remain session-only; you can still revoke consent.", "مفعّل، لكن صلاحية الكتابة في مساحة العمل غير متاحة. تبقى الرسائل الجديدة ضمن الجلسة، ويمكنك إلغاء الموافقة.");
    } else if (settings.enabled === true) {
      setConversationMemoryStatus("Enabled for this workspace. Future text and recognized voice turns are saved.", "مفعّل لمساحة العمل هذه. تُحفظ الرسائل النصية والصوتية المتعرّف عليها مستقبلًا.");
    } else if (!conversationMemoryCanWrite) {
      setConversationMemoryStatus("Off. Workspace write access is required to opt in.", "متوقف. يلزم إذن الكتابة في مساحة العمل لتفعيله.");
    } else {
      setConversationMemoryStatus("Off. Nothing is saved until you explicitly opt in.", "متوقف. لا يُحفظ شيء حتى تفعيله صراحةً.");
    }
  } catch (error) {
    if (generation !== conversationMemoryLoadGeneration || workspaceId !== activeWorkspaceId) return;
    conversationMemoryRefresh.disabled = false;
    updateConversationMemoryControls();
    setConversationMemoryStatus(errorMessage(error.message), "تعذّر تحميل إعدادات الذاكرة الدائمة.", true);
  }
}

async function loadConversationMemoryDetail(conversationId) {
  const generation = conversationMemoryLoadGeneration;
  const workspaceId = activeWorkspaceId;
  try {
    const response = await fetch(`/api/v1/conversation-memory/${encodeURIComponent(conversationId)}`, {
      credentials: "same-origin", cache: "no-store",
    });
    const value = await responseJson(response);
    if (generation !== conversationMemoryLoadGeneration || workspaceId !== activeWorkspaceId) return;
    if (response.status === 401) { markSessionExpired(); return; }
    if (!response.ok || !value.conversation || !Array.isArray(value.conversation.turns)) {
      throw new Error(value.error || "conversation_memory_unavailable");
    }
    conversationMemoryTurns.replaceChildren();
    for (const turn of value.conversation.turns) {
      const item = document.createElement("li");
      item.className = "conversation-memory-turn";
      for (const [role, text] of [[conversationMemoryText("You", "أنت"), turn.user], ["Jarvis", turn.assistant]]) {
        const block = document.createElement("div");
        const label = document.createElement("strong");
        label.textContent = `${role}: `;
        const content = document.createElement("p");
        content.textContent = typeof text === "string" ? text : "";
        block.append(label, content);
        item.append(block);
      }
      conversationMemoryTurns.append(item);
    }
    const expires = new Date(value.conversation.expires_at);
    conversationMemoryDetailTitle.textContent = conversationMemoryText("Expires", "ينتهي") + ": "
      + (Number.isNaN(expires.valueOf()) ? "" : expires.toLocaleString(language === "ar" ? "ar" : "en-GB"));
    conversationMemoryDetail.hidden = false;
  } catch (error) {
    if (generation !== conversationMemoryLoadGeneration || workspaceId !== activeWorkspaceId) return;
    setConversationMemoryStatus(errorMessage(error.message), "تعذّر تحميل المحادثة.", true);
  }
}

async function deleteSavedConversation(conversationId) {
  const confirmed = window.confirm(conversationMemoryText(
    "Delete this saved conversation? It will also clear this workspace's current session context.",
    "هل تريد حذف هذه المحادثة؟ سيؤدي ذلك أيضًا إلى مسح سياق الجلسة الحالية لمساحة العمل هذه."
  ));
  if (!confirmed || !csrfToken || !activeWorkspaceId) return;
  try {
    const response = await fetch(`/api/v1/conversation-memory/conversations/${encodeURIComponent(conversationId)}`, {
      method: "DELETE", credentials: "same-origin", cache: "no-store",
      headers: { "X-Jarvis-CSRF": csrfToken },
    });
    const value = await responseJson(response);
    if (response.status === 401) { markSessionExpired(); return; }
    if (!response.ok) throw new Error(value.error || "conversation_memory_unavailable");
    clearConversationTranscriptAfterDeletion();
    setConversationMemoryStatus("Saved conversation deleted.", "حُذفت المحادثة المحفوظة.");
    await loadConversationMemory();
  } catch (error) {
    setConversationMemoryStatus(errorMessage(error.message), "تعذّر حذف المحادثة.", true);
  }
}

async function saveConversationMemoryPreference(event) {
  event.preventDefault();
  if (!csrfToken || !activeWorkspaceId) return;
  const wasEnabled = conversationMemoryPanel.dataset.enabled === "true";
  const enabled = conversationMemoryEnabled.checked;
  if (enabled && !conversationMemoryCanWrite) return;
  if (wasEnabled && !enabled && !window.confirm(conversationMemoryText(
    "Turn off saved conversations and delete all saved conversations in this workspace? This also revokes consent.",
    "هل تريد إيقاف حفظ المحادثات وحذف جميع المحادثات المحفوظة في مساحة العمل هذه؟ سيؤدي ذلك إلى إلغاء الموافقة."
  ))) {
    conversationMemoryEnabled.checked = true;
    return;
  }
  conversationMemorySave.disabled = true;
  try {
    const response = await fetch("/api/v1/conversation-memory/settings", {
      method: "PUT", credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/json", "X-Jarvis-CSRF": csrfToken },
      body: JSON.stringify({ enabled, retention_days: Number(conversationMemoryRetention.value) }),
    });
    const value = await responseJson(response);
    if (response.status === 401) { markSessionExpired(); return; }
    if (!response.ok || !value.settings) throw new Error(value.error || "conversation_memory_unavailable");
    if (wasEnabled && !enabled) clearConversationTranscriptAfterDeletion();
    await loadConversationMemory();
    if (enabled && !wasEnabled) {
      setConversationMemoryStatus("Enabled. Only future completed turns are saved; earlier session history was not copied.", "تم التفعيل. تُحفظ الرسائل المكتملة مستقبلًا فقط؛ لم يُنسخ سجل الجلسة السابق.");
    } else if (!enabled) {
      setConversationMemoryStatus("Consent revoked and saved conversations deleted.", "أُلغيت الموافقة وحُذفت المحادثات المحفوظة.");
    } else {
      setConversationMemoryStatus("Preference saved.", "حُفظ التفضيل.");
    }
  } catch (error) {
    setConversationMemoryStatus(errorMessage(error.message), "تعذّر حفظ التفضيل.", true);
    updateConversationMemoryControls();
  }
}

async function loadWorkspaces(preferredWorkspaceId = null) {
  clearConversationMemoryPanel();
  workspaceSelect.disabled = true;
  const response = await fetch("/api/v1/workspaces", { credentials: "same-origin", cache: "no-store" });
  const value = await responseJson(response);
  if (response.status === 401) {
    markSessionExpired();
    return;
  }
  if (response.status === 503) {
    const general = document.createElement("option");
    general.value = "";
    general.textContent = language === "ar" ? "دردشة عامة · بلا مساحة عمل" : "General chat · no workspace";
    workspaceSelect.replaceChildren(general);
    workspaceSelect.disabled = false;
    if (typeof preferredWorkspaceId === "string" && preferredWorkspaceId) {
      const unavailable = document.createElement("option");
      unavailable.value = preferredWorkspaceId;
      unavailable.textContent = language === "ar" ? "المساحة النشطة · تعذر التحقق" : "Active workspace · authorization unavailable";
      workspaceSelect.append(unavailable);
      workspaceSelect.value = preferredWorkspaceId;
      activeWorkspaceId = preferredWorkspaceId;
      workspaceServiceUnavailable = true;
      clearEphemeralWorkspaceView();
      createCandidateOpen.disabled = true;
      chatInput.disabled = true;
      chatSend.disabled = true;
      setWorkspaceState("Workspace authorization is unavailable. Clear the selection or reload before asking.", "تفويض مساحة العمل غير متاح. امسح الاختيار أو أعد التحميل قبل السؤال.");
      await loadDeveloperRepositories();
      clearTaskControlCenter();
      clearProactivityState(proactivityState);
      renderProactivity();
    } else {
      workspaceSelect.value = "";
      activeWorkspaceId = null;
      workspaceServiceUnavailable = false;
      chatInput.disabled = !authenticated;
      chatSend.disabled = !authenticated;
      createCandidateOpen.disabled = true;
      setWorkspaceState("Workspace APIs are unavailable. General chat remains available.", "واجهات مساحات العمل غير متاحة. تظل الدردشة العامة متاحة.");
      await loadDeveloperRepositories();
      clearTaskControlCenter();
      clearProactivityState(proactivityState);
      renderProactivity();
    }
    return;
  }
  if (!response.ok || !Array.isArray(value.workspaces)) throw new Error("workspace_list_unavailable");
  const general = document.createElement("option");
  general.value = "";
  general.textContent = language === "ar" ? "دردشة عامة · بلا مساحة عمل" : "General chat · no workspace";
  workspaceSelect.replaceChildren(general);
  for (const workspace of value.workspaces) {
    if (!workspace || typeof workspace.id !== "string" || typeof workspace.name !== "string") continue;
    const option = document.createElement("option");
    option.value = workspace.id;
    option.textContent = workspace.name;
    option.dir = "auto";
    workspaceSelect.append(option);
  }
  const previousWorkspaceId = activeWorkspaceId;
  const selected = typeof preferredWorkspaceId === "string"
    && [...workspaceSelect.options].some((option) => option.value === preferredWorkspaceId)
    ? preferredWorkspaceId : "";
  workspaceSelect.value = selected;
  workspaceSelect.disabled = false;
  if (previousWorkspaceId !== (selected || null)) clearEphemeralWorkspaceView();
  activeWorkspaceId = selected || null;
  updateChatInputPlaceholder();
  workspaceServiceUnavailable = false;
  chatInput.disabled = !authenticated || sending;
  chatSend.disabled = !authenticated || sending;
  createCandidateOpen.disabled = !activeWorkspaceId;
  if (activeWorkspaceId) {
    setWorkspaceState("Workspace evidence and approved memories are enabled.", "أدلة مساحة العمل والذكريات المعتمدة مفعّلة.");
    await loadPendingCandidates();
  } else {
    setWorkspaceState("No workspace selected · general chat only", "لم تُحدد مساحة · دردشة عامة فقط");
    pendingCandidates.replaceChildren();
  }
  await loadDeveloperRepositories();
  if (activeWorkspaceId) {
    await loadTaskList({ offset: 0 });
  } else {
    clearTaskControlCenter();
  }
  if (activeWorkspaceId) await loadConversationMemory();
  if (activeWorkspaceId) await loadProactivity();
}

async function selectWorkspace() {
  if (!authenticated || !csrfToken || (sending && !voiceChatInFlight)) return;
  interruptVoice({ clearReply: true });
  workspaceSelect.disabled = true;
  const selected = workspaceSelect.value;
  try {
    const response = await fetch(selected ? "/api/v1/session/workspace" : "/api/v1/session/workspace", {
      method: selected ? "PUT" : "DELETE", credentials: "same-origin", cache: "no-store",
      headers: selected
        ? { "Content-Type": "application/json", "X-Jarvis-CSRF": csrfToken }
        : { "X-Jarvis-CSRF": csrfToken },
      ...(selected ? { body: JSON.stringify({ workspace_id: selected }) } : {})
    });
    const value = await responseJson(response);
    if (response.status === 401) {
      markSessionExpired();
      return;
    }
    if (!response.ok) throw new Error(value.error || "workspace_unavailable");
    activeWorkspaceId = selected || null;
    updateChatInputPlaceholder();
    workspaceServiceUnavailable = false;
    clearEphemeralWorkspaceView();
    chatInput.disabled = !authenticated || sending;
    chatSend.disabled = !authenticated || sending;
    if (activeWorkspaceId) {
      setWorkspaceState("Workspace evidence and approved memories are enabled.", "أدلة مساحة العمل والذكريات المعتمدة مفعّلة.");
      createCandidateOpen.disabled = false;
      await loadPendingCandidates();
    } else {
      setWorkspaceState("General chat only · no workspace is selected.", "دردشة عامة فقط · لم تُحدد مساحة عمل.");
    }
    await loadDeveloperRepositories();
    if (activeWorkspaceId) {
      await loadTaskList({ offset: 0 });
    } else {
      clearTaskControlCenter();
    }
    if (activeWorkspaceId) await loadConversationMemory();
    else clearConversationMemoryPanel();
    if (activeWorkspaceId) await loadProactivity();
    else {
      clearProactivityState(proactivityState);
      renderProactivity();
    }
  } catch (error) {
    if (error.message === "workspace_selection_changed") {
      await restoreSession();
    } else {
      setStatusLine(CHAT_ERRORS[error.message] ?? CHAT_ERRORS.default, true);
      if (error.message === "workspace_unavailable") activeWorkspaceId = null;
      await loadWorkspaces(activeWorkspaceId).catch(() => {});
    }
  } finally {
    workspaceSelect.disabled = !authenticated;
  }
}

async function restoreSession() {
  setChatState("checking", "checking");
  try {
    const response = await fetch("/api/v1/session", { credentials: "same-origin", cache: "no-store" });
    const value = await responseJson(response);
    if (response.status === 401) {
      setChatState("pairing", "checking");
      setAuthenticated(false);
      return;
    }
    if (!response.ok || value.authenticated !== true || typeof value.csrf_token !== "string") {
      throw new Error("session_unavailable");
    }
    if (typeof value.model !== "string" || !value.model || typeof value.provider !== "string" || !value.provider) {
      throw new Error("session_unavailable");
    }
    setAuthenticated(true, value.csrf_token, value.model, value.provider);
    chatInput.disabled = true;
    chatSend.disabled = true;
    await loadWorkspaces(value.active_workspace_id);
    await loadVoiceStatus();
  } catch (_error) {
    setAuthenticated(false, null, null, null, false);
    setChatState("unavailable", "error");
    setPairError(language === "ar" ? "تعذر الاتصال بخدمة جارفس المحلية. تحقق من نافذة PowerShell." : "Could not reach the local Jarvis service. Check the PowerShell window.");
  }
}

async function pairBrowser(event) {
  event.preventDefault();
  if (pairSubmit.disabled) return;
  pairSubmit.disabled = true;
  setPairError("");
  pairStatus.textContent = language === "ar" ? "جارٍ التحقق…" : "Pairing…";
  try {
    const response = await fetch("/api/v1/session/pair", {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pairing_code: pairCode.value })
    });
    const value = await responseJson(response);
    if (!response.ok || value.authenticated !== true || typeof value.csrf_token !== "string") {
      const message = response.status === 401
        ? (language === "ar" ? "الرمز غير صحيح أو انتهت صلاحيته. أعد تشغيل الخدمة للحصول على رمز جديد." : "Code was rejected or expired. Restart the service for a new code.")
        : errorMessage(value.error);
      setPairError(message);
      pairStatus.textContent = "";
      return;
    }
    pairCode.value = "";
    pairForm.reset();
    pairStatus.textContent = "";
    setPairError("");
    if (typeof value.model !== "string" || !value.model || typeof value.provider !== "string" || !value.provider) {
      throw new Error("pairing_unavailable");
    }
    setAuthenticated(true, value.csrf_token, value.model, value.provider);
    chatInput.disabled = true;
    chatSend.disabled = true;
    await loadWorkspaces(null);
    await loadVoiceStatus();
    chatInput.focus();
  } catch (_error) {
    setPairError(language === "ar" ? "تعذر الاتصال بالخدمة المحلية." : "Could not connect to the local service.");
  } finally {
    pairSubmit.disabled = false;
  }
}

async function sendChat(event) {
  event.preventDefault();
  await sendChatMessage(chatInput.value.trim(), { fromVoice: false });
}

async function sendChatMessage(message, { fromVoice = false, voiceGeneration = voiceState.generation,
  voiceSessionToken = csrfToken, voiceWorkspaceId = activeWorkspaceId } = {}) {
  if (!authenticated || sending || !csrfToken || workspaceServiceUnavailable) return;
  if (!message || message.length > 1000) return;
  sending = true;
  if (fromVoice) voiceChatInFlight = true;
  chatInput.disabled = true;
  chatSend.disabled = true;
  workspaceSelect.disabled = !fromVoice;
  setChatState("sending", "busy");
  setStatusLine("");
  renderVoiceUI();
  const userEntry = appendChatEntry("user", message);
  const pending = appendChatEntry("assistant pending", language === "ar" ? "جارٍ إنشاء رد محلي…" : "Generating a local response…");
  if (!fromVoice) chatInput.value = "";
  const controller = new AbortController();
  if (fromVoice) voiceAbortController = controller;
  const timeout = setTimeout(() => controller.abort(), 310000);
  let replyToSpeak = null;
  let replyLanguage = language;
  try {
    const payload = fromVoice ? { message, voice_mode: true } : { message };
    const response = await fetch("/api/v1/chat", {
      method: "POST", credentials: "same-origin", cache: "no-store", signal: controller.signal,
      headers: { "Content-Type": "application/json", "X-Jarvis-CSRF": csrfToken },
      body: JSON.stringify(payload)
    });
    const value = await responseJson(response);
    if (fromVoice && !isVoiceOperationCurrent(voiceGeneration, voiceSessionToken, voiceWorkspaceId)) {
      pending.remove();
      userEntry.remove();
      return;
    }
    if (response.status === 401) {
      pending.remove();
      markSessionExpired();
      return;
    }
    if (!response.ok) {
      if (value.error === "workspace_selection_changed") {
        pending.remove();
        await restoreSession();
        return;
      }
      pending.classList.remove("pending");
      pending.classList.add("error");
      pending.textContent = errorMessage(value.error);
      setChatState("failed", "error");
      if (fromVoice && voiceGeneration === voiceState.generation) {
        voiceState = transitionVoiceState(voiceState, "error", { errorCode: "default" });
      }
      if (value.error === "workspace_unavailable") {
        activeWorkspaceId = null;
        clearEphemeralWorkspaceView();
        await loadWorkspaces(null).catch(() => {});
      }
      return;
    }
    if (activeWorkspaceId && value.workspace_id !== activeWorkspaceId) {
      pending.remove();
      await restoreSession();
      return;
    }
    if (value.model !== configuredModel || value.provider !== configuredProvider
        || typeof value.reply !== "string" || !value.reply.trim() || value.reply.length > 16000) {
      throw new Error("invalid_response");
    }
    pending.classList.remove("pending");
    pending.textContent = value.reply;
    if (activeWorkspaceId) {
      latestEvidenceResponse = value;
      renderEvidence(value);
      createCandidateOpen.disabled = !activeWorkspaceId;
    } else {
      latestEvidenceResponse = null;
      renderEvidence(null);
    }
    setChatState("ready", "ready");
    revokeVoiceAudioObjectUrl();
    replyLanguage = detectReplyLanguage(value.reply, language);
    replyToSpeak = value.reply;
    if (fromVoice && voiceGeneration === voiceState.generation) {
      voiceState = transitionVoiceState(voiceState, "stopped", {
        replyText: value.reply,
        replyLanguage,
        hasReplayAudio: false,
      });
    } else {
      voiceState = {
        ...voiceState,
        lastReplyText: value.reply,
        lastReplyLanguage: replyLanguage,
        hasReplayAudio: false,
      };
    }
  } catch (error) {
    if (fromVoice && voiceGeneration !== voiceState.generation) {
      pending.remove();
      userEntry.remove();
      setChatState("ready", "ready");
      return;
    }
    pending.classList.remove("pending");
    pending.classList.add("error");
    pending.textContent = error.name === "AbortError"
      ? (language === "ar" ? "انتهت مهلة انتظار النموذج المحلي." : "Timed out waiting for the local model.")
      : errorMessage("default");
    setChatState("unavailable", "error");
    if (fromVoice && voiceGeneration === voiceState.generation) {
      voiceState = transitionVoiceState(voiceState, "error", { errorCode: "default" });
    }
  } finally {
    clearTimeout(timeout);
    if (voiceAbortController === controller) voiceAbortController = null;
    sending = false;
    if (fromVoice) voiceChatInFlight = false;
    chatInput.disabled = !authenticated;
    chatSend.disabled = !authenticated;
    workspaceSelect.disabled = !authenticated;
    renderVoiceUI();
    if (authenticated && !fromVoice) chatInput.focus();
  }
  if (fromVoice && replyToSpeak && authenticated && voiceGeneration === voiceState.generation
      && voiceState.ttsConfigured && !voiceState.muted) {
    await playSpokenReply({ text: replyToSpeak, replyLanguage, voiceGeneration });
  }
}

async function disconnectBrowser() {
  if (!authenticated || !csrfToken) return;
  teardownVoiceForSessionEnd();
  signOut.disabled = true;
  try {
    await fetch("/api/v1/session", {
      method: "DELETE", credentials: "same-origin", cache: "no-store",
      headers: { "X-Jarvis-CSRF": csrfToken }
    });
  } catch (_error) {
    // Clear local UI state even if the best-effort revoke response is lost.
  } finally {
    signOut.disabled = false;
    setAuthenticated(false, null, null, null, false);
    setChatState("restart_required", "error");
    setStatusLine(["Session disconnected. Restart the service, then reload to pair again.",
      "أُبطلت الجلسة. أعد تشغيل الخدمة ثم أعد تحميل الصفحة للربط مجددًا."], true);
  }
}

function renderNodeList() {
  nodeList.replaceChildren();
  const nodes = NODES.filter(({ id }) => visibleIds.has(id));
  $("#node-count").textContent = String(nodes.length).padStart(2, "0");
  $("#empty-search").hidden = nodes.length > 0;
  for (const node of nodes) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("aria-pressed", String(selectedId === node.id));
    button.setAttribute("aria-label", localized(node.label));
    button.innerHTML = `<span class="node-kind" aria-hidden="true" style="color:${node.color};background:${node.color}"></span><span class="node-name"></span>`;
    button.querySelector(".node-name").textContent = localized(node.label);
    button.addEventListener("click", () => selectNode(node.id));
    item.append(button);
    nodeList.append(item);
  }
}

function renderDetails() {
  const node = classifyNode(selectedId);
  emptyDetails.hidden = Boolean(node);
  details.hidden = !node;
  if (!node) return;
  $("#detail-title").textContent = localized(node.label);
  $("#detail-type").textContent = ({ space: language === "ar" ? "مساحة عمل" : "Workspace", project: language === "ar" ? "مشروع" : "Project", folder: language === "ar" ? "مجلد" : "Folder", file: language === "ar" ? "ملف" : "File", memory: language === "ar" ? "ذاكرة معتمدة" : "Approved memory", note: language === "ar" ? "ملاحظة" : "Note" })[node.kind];
  $("#detail-description").textContent = localized(node.description);
  $("#detail-source").textContent = localized(node.source);
  $("#detail-glyph").style.color = node.color;
  $("#connection-list").replaceChildren();
  const connected = [...connectedNodeIds(EDGES, selectedId)].filter((id) => id !== selectedId).map(classifyNode);
  $("#connection-count").textContent = String(connected.length).padStart(2, "0");
  for (const item of connected) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `↗  ${localized(item.label)}`;
    button.addEventListener("click", () => selectNode(item.id));
    $("#connection-list").append(button);
  }
}

function renderFallback() {
  fallback.replaceChildren();
  for (const node of NODES.filter(({ id }) => visibleIds.has(id))) {
    const button = document.createElement("button");
    const neighborhood = connectedNodeIds(EDGES, selectedId);
    button.type = "button";
    button.className = `${neighborhood.has(node.id) ? "connected " : ""}${selectedId === node.id ? "selected" : ""}`;
    button.textContent = localized(node.label);
    button.setAttribute("aria-pressed", String(selectedId === node.id));
    button.addEventListener("click", () => selectNode(node.id));
    fallback.append(button);
  }
}

function selectNode(id) {
  selectedId = id;
  renderNodeList();
  renderDetails();
  renderFallback();
  sceneState?.select(id);
}

function updateSearch() {
  visibleIds = searchNodeIds(NODES, search.value, language);
  renderNodeList();
  renderFallback();
  sceneState?.setVisible(visibleIds);
  if (selectedId && !visibleIds.has(selectedId)) {
    selectedId = null;
    renderNodeList(); renderDetails(); renderFallback(); sceneState?.select(null);
  }
}

function makeLabelTexture(THREE, label, color) {
  const canvas = document.createElement("canvas");
  canvas.width = 512; canvas.height = 96;
  const context = canvas.getContext("2d");
  context.font = "600 26px Segoe UI, Tahoma, Arial";
  context.textAlign = "center"; context.textBaseline = "middle";
  context.shadowColor = color; context.shadowBlur = 16; context.fillStyle = "#e6eff8";
  let shown = label;
  while (context.measureText(shown).width > 480 && shown.length > 8) shown = `${shown.slice(0, -5)}…`;
  context.fillText(shown, 256, 48);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

async function initGalaxy() {
  const THREE = await import("/vendor/three.module.js");
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.6));
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  canvasHost.replaceChildren(renderer.domElement);
  renderer.domElement.setAttribute("aria-hidden", "true");
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(42, 1, .1, 100);
  camera.position.set(0, 0, 11);
  const orbit = new THREE.Group();
  scene.add(orbit);
  const coords = new Map([
    ["atlas", [0, 0, 0]], ["project", [-2.7, 1.2, -.2]], ["folder", [2.4, 1.45, .1]],
    ["brief", [3.3, -.55, -.4]], ["map", [1.8, -2, .5]], ["memory", [-2.7, -1.25, .15]],
    ["note", [-.65, 2.35, -.5]], ["archive", [-3.8, -2.2, -.25]]
  ]);
  const objects = new Map();
  const nodeMeshes = new Map();
  const lineObjects = [];
  const positions = NODES.map((node) => new THREE.Vector3(...coords.get(node.id)));
  const stars = new THREE.BufferGeometry();
  const starPoints = new Float32Array(280 * 3);
  for (let i = 0; i < 280; i++) {
    starPoints[i * 3] = (Math.random() - .5) * 16;
    starPoints[i * 3 + 1] = (Math.random() - .5) * 10;
    starPoints[i * 3 + 2] = (Math.random() - .5) * 7 - 2;
  }
  stars.setAttribute("position", new THREE.BufferAttribute(starPoints, 3));
  const starField = new THREE.Points(stars, new THREE.PointsMaterial({ color: 0x7890ad, size: .018, transparent: true, opacity: .52 }));
  orbit.add(starField);
  for (const node of NODES) {
    const position = coords.get(node.id);
    const color = new THREE.Color(node.color);
    const group = new THREE.Group();
    group.position.set(...position);
    const radius = node.id === "atlas" ? .21 : node.kind === "memory" ? .16 : .14;
    const sphere = new THREE.Mesh(new THREE.IcosahedronGeometry(radius, 2), new THREE.MeshBasicMaterial({ color }));
    const halo = new THREE.Mesh(new THREE.SphereGeometry(radius * 1.85, 16, 16), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .095, depthWrite: false }));
    group.add(halo, sphere);
    const label = new THREE.Sprite(new THREE.SpriteMaterial({ map: makeLabelTexture(THREE, localized(node.label), node.color), transparent: true, depthWrite: false }));
    label.position.set(0, -.34, 0); label.scale.set(1.65, .31, 1); group.add(label);
    group.userData.nodeId = node.id;
    orbit.add(group); nodeMeshes.set(node.id, { group, sphere, halo, label, node }); objects.set(node.id, group);
  }
  for (const [a, b] of EDGES) {
    const geometry = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...coords.get(a)), new THREE.Vector3(...coords.get(b))]);
    const material = new THREE.LineBasicMaterial({ color: 0x6688a6, transparent: true, opacity: .22 });
    const line = new THREE.Line(geometry, material); orbit.add(line); lineObjects.push({ a, b, line });
  }
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let dragging = false; let moved = false; let lastX = 0; let lastY = 0;
  let target = new THREE.Vector3();
  let desiredCamera = new THREE.Vector3(0, 0, 11);
  let distance = 11;
  let frameDriver = null;
  const focusPoint = (id) => id && objects.has(id)
    ? objects.get(id).getWorldPosition(new THREE.Vector3())
    : new THREE.Vector3();
  const resize = () => {
    const rect = canvasHost.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    renderer.setSize(rect.width, rect.height, false);
    camera.aspect = rect.width / rect.height; camera.updateProjectionMatrix();
    frameDriver?.invalidate();
  };
  const resizeObserver = new ResizeObserver(resize); resizeObserver.observe(canvasHost); resize();
  const applyEmphasis = () => {
    const neighbors = connectedNodeIds(EDGES, selectedId);
    for (const [id, item] of nodeMeshes) {
      const visible = visibleIds.has(id);
      const emphasized = !selectedId || neighbors.has(id);
      item.group.visible = visible;
      item.sphere.material.color.set(id === selectedId ? 0xffffff : item.node.color);
      item.sphere.scale.setScalar(id === selectedId ? 1.32 : 1);
      item.halo.material.opacity = id === selectedId ? .24 : emphasized ? .12 : .025;
      item.label.material.opacity = emphasized ? 1 : .24;
    }
    for (const { a, b, line } of lineObjects) {
      const on = visibleIds.has(a) && visibleIds.has(b);
      line.visible = on;
      line.material.color.set(selectedId && (a === selectedId || b === selectedId) ? 0x72ddf4 : 0x6688a6);
      line.material.opacity = selectedId ? ((a === selectedId || b === selectedId) ? .78 : .065) : .22;
    }
  };
  const select = (id) => {
    selectedId = id;
    if (id && objects.has(id)) {
      orbit.updateMatrixWorld(true);
      const p = focusPoint(id);
      target.copy(p);
      desiredCamera.set(p.x, p.y, p.z + distance);
    } else { target.set(0, 0, 0); desiredCamera.set(0, 0, distance); }
    if (reduceMotion) camera.position.copy(desiredCamera);
    camera.lookAt(target);
    applyEmphasis();
    frameDriver?.invalidate();
  };
  renderer.domElement.addEventListener("pointerdown", (event) => { dragging = true; moved = false; lastX = event.clientX; lastY = event.clientY; renderer.domElement.setPointerCapture(event.pointerId); });
  renderer.domElement.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    const dx = event.clientX - lastX; const dy = event.clientY - lastY;
    if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
    orbit.rotation.y += dx * .004; orbit.rotation.x = Math.max(-.65, Math.min(.65, orbit.rotation.x + dy * .003));
    lastX = event.clientX; lastY = event.clientY;
    frameDriver?.invalidate();
  });
  renderer.domElement.addEventListener("pointerup", (event) => {
    dragging = false;
    if (moved) return;
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObjects([...nodeMeshes.values()].map((item) => item.sphere), false);
    const hit = hits[0]?.object.parent.userData.nodeId;
    if (hit) selectNode(hit);
  });
  renderer.domElement.addEventListener("wheel", (event) => {
    event.preventDefault(); distance = Math.max(6, Math.min(15, distance + event.deltaY * .006));
    const focus = focusPoint(selectedId);
    desiredCamera.set(focus.x, focus.y, focus.z + distance);
    if (reduceMotion) camera.position.copy(desiredCamera);
    camera.lookAt(focus);
    frameDriver?.invalidate();
  }, { passive: false });
  const drawFrame = () => {
    if (!reduceMotion) starField.rotation.y += .00012;
    if (selectedId) {
      orbit.updateMatrixWorld(true);
      target.copy(focusPoint(selectedId));
      desiredCamera.set(target.x, target.y, target.z + distance);
    }
    if (!reduceMotion) camera.position.lerp(desiredCamera, .085);
    else camera.position.copy(desiredCamera);
    camera.lookAt(target);
    renderer.render(scene, camera);
  };
  frameDriver = createFrameDriver(drawFrame);
  frameDriver.setReducedMotion(reduceMotion);
  const refreshLabels = () => {
    for (const item of nodeMeshes.values()) {
      item.label.material.map?.dispose(); item.label.material.map = makeLabelTexture(THREE, localized(item.node.label), item.node.color); item.label.material.needsUpdate = true;
    }
    frameDriver.invalidate();
  };
  sceneState = {
    select,
    setVisible: () => { applyEmphasis(); frameDriver.invalidate(); },
    refreshLabels,
    setMotion: (enabled) => { reduceMotion = enabled; frameDriver.setReducedMotion(enabled); },
    reset: () => { orbit.rotation.set(0, 0, 0); distance = 11; select(null); },
    dispose: () => { frameDriver.dispose(); resizeObserver.disconnect(); renderer.dispose(); }
  };
  applyEmphasis();
  $("#render-status").innerHTML = `<i></i><span>${language === "ar" ? "معاينة ثلاثية الأبعاد" : "3D PREVIEW"}</span>`;
}

function useFallback() {
  canvasHost.hidden = true;
  fallback.hidden = false;
  $("#render-status").innerHTML = `<i></i><span>${language === "ar" ? "عرض مبسط" : "SIMPLE VIEW"}</span>`;
  renderFallback();
}

languageSelect.addEventListener("change", () => applyLanguage(languageSelect.value));
workspaceSelect.addEventListener("change", selectWorkspace);
conversationMemoryForm.addEventListener("submit", saveConversationMemoryPreference);
conversationMemoryRefresh.addEventListener("click", loadConversationMemory);
conversationMemoryEnabled.addEventListener("change", updateConversationMemoryControls);
conversationMemoryDetailClose.addEventListener("click", () => { conversationMemoryDetail.hidden = true; });
developerRepoSelect.addEventListener("change", selectDeveloperRepository);
fileChangeModelForm?.addEventListener("submit", submitModelFileChange);
fileChangeForm.addEventListener("submit", submitFileChange);
fileChangeApprove.addEventListener("click", () => decideFileChange("approve"));
fileChangeReject.addEventListener("click", () => decideFileChange("reject"));
fileChangeOperation.addEventListener("change", () => { fileChangeContent.required = fileChangeOperation.value !== "delete"; });
developerCompareButton.addEventListener("click", compareDeveloperCommits);
developerSearchForm.addEventListener("submit", searchDeveloperFiles);
developerChatForm.addEventListener("submit", askDeveloper);
proactivityEnable.addEventListener("click", () => saveProactivityPreference(true));
proactivitySave.addEventListener("click", () => saveProactivityPreference(proactivityState.enabled));
proactivityStop.addEventListener("click", () => saveProactivityPreference(false));
proactivityRefresh.addEventListener("click", loadProactivity);
taskStatusFilter.addEventListener("change", () => {
  taskState.statusFilter = taskStatusFilter.value || "all";
  loadTaskList({ offset: 0, clearMissingDetail: true });
});
taskIncludeHistory.addEventListener("change", () => {
  taskState.includeHistory = Boolean(taskIncludeHistory.checked);
  loadTaskList({ offset: 0, clearMissingDetail: true });
});
taskIncludeArchived.addEventListener("change", () => {
  taskState.includeArchived = Boolean(taskIncludeArchived.checked);
  loadTaskList({ offset: 0, clearMissingDetail: true });
});
taskRefreshButton.addEventListener("click", () => {
  loadTaskList({ offset: taskState.listOffset, refreshDetail: true });
});
taskListPrev.addEventListener("click", () => {
  const prevOffset = Math.max(0, (taskState.pagination?.offset ?? 0) - TASK_LIST_LIMIT);
  loadTaskList({ offset: prevOffset });
});
taskListNext.addEventListener("click", () => {
  if (!taskState.pagination?.hasMore) return;
  loadTaskList({ offset: (taskState.pagination.offset ?? 0) + TASK_LIST_LIMIT });
});
taskEventsPrev.addEventListener("click", () => {
  const prevOffset = Math.max(0, (taskState.eventsPagination?.offset ?? 0) - TASK_EVENTS_LIMIT);
  loadTaskEventsPage(prevOffset);
});
taskEventsNext.addEventListener("click", () => {
  if (!taskState.eventsPagination?.has_more) return;
  loadTaskEventsPage((taskState.eventsPagination.offset ?? 0) + TASK_EVENTS_LIMIT);
});
search.addEventListener("input", updateSearch);
pairForm.addEventListener("submit", pairBrowser);
chatForm.addEventListener("submit", sendChat);
voicePttButton.addEventListener("click", () => { handlePushToTalkClick(); });
voicePlayButton.addEventListener("click", () => { playSpokenReply(); });
voiceStopButton.addEventListener("click", () => { interruptVoice({ clearReply: false }); });
voiceMuteButton.addEventListener("click", () => {
  voiceState = toggleVoiceMute(voiceState);
  if (voiceAudioElement) voiceAudioElement.muted = voiceState.muted;
  renderVoiceUI();
});
voiceTranscriptToggle.addEventListener("click", () => {
  voiceState = toggleVoiceTranscript(voiceState);
  renderVoiceUI();
});
window.addEventListener("pagehide", () => { teardownVoiceForSessionEnd(); });
window.addEventListener("beforeunload", () => { teardownVoiceForSessionEnd(); });
signOut.addEventListener("click", disconnectBrowser);
createCandidateOpen.addEventListener("click", openCandidateEditor);
candidateEditorForm.addEventListener("submit", submitMemoryCandidate);
candidateUserAuthored.addEventListener("change", () => {
  candidateSources.hidden = candidateUserAuthored.checked || candidateSourceOptions.childElementCount === 0;
});
$("#candidate-editor-cancel").addEventListener("click", () => candidateEditorDialog.close());
$("#candidate-review-close").addEventListener("click", () => candidateReviewDialog.close());
$("#candidate-approve").addEventListener("click", () => decideCandidate("approve"));
$("#candidate-reject").addEventListener("click", () => decideCandidate("reject"));
candidateReviewDialog.addEventListener("close", () => { reviewCandidate = null; });
pairingDialog.addEventListener("cancel", (event) => event.preventDefault());
$("#reset-view").addEventListener("click", () => { selectedId = null; renderNodeList(); renderDetails(); renderFallback(); sceneState?.reset(); });
$("#motion-toggle").addEventListener("click", (event) => {
  reduceMotion = !reduceMotion;
  event.currentTarget.setAttribute("aria-pressed", String(!reduceMotion));
  $("#motion-label").textContent = language === "ar" ? (reduceMotion ? "الحركة مخفّضة" : "الحركة مفعّلة") : (reduceMotion ? "Motion reduced" : "Motion on");
  sceneState?.setMotion(reduceMotion);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && (voiceState.status === "listening" || voiceState.status === "speaking")) {
    interruptVoice({ clearReply: false });
    return;
  }
  if (event.key === "/" && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) { event.preventDefault(); search.focus(); }
});
applyLanguage("en");
restoreSession();
initGalaxy().catch(() => useFallback());
