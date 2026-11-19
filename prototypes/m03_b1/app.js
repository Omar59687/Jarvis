import { NODES, EDGES, connectedNodeIds, searchNodeIds, classifyNode, createFrameDriver } from "/galaxy-model.mjs";
import { chatInputPlaceholder } from "/chat-ui-state.mjs";

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
const developerRepoSelect = $("#developer-repo");
const developerState = $("#developer-state");
const developerSearchForm = $("#developer-search-form");
const developerSearch = $("#developer-search");
const developerResults = $("#developer-results");
const developerSource = $("#developer-source");
const developerChatForm = $("#developer-chat-form");
const developerQuestion = $("#developer-question");
const developerAsk = $("#developer-ask");
const developerAnswer = $("#developer-answer");
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
let chatStateKey = "checking";
let activeWorkspaceId = null;
let workspaceServiceUnavailable = false;
let latestEvidenceRequestId = null;
let latestCitations = [];
let reviewCandidate = null;
let latestEvidenceResponse = null;
let developerRepositories = [];
let activeDeveloperRepoId = null;
let developerBusy = false;
let developerStatus = ["Pair and select a workspace to load repositories.", "اربط الجلسة واختر مساحة عمل لتحميل المستودعات."];

const CHAT_STATUS = {
  checking: ["Checking local session…", "جارٍ التحقق من الجلسة المحلية…"],
  pairing: ["Pair this browser to start", "اربط هذا المتصفح للبدء"],
  ready: ["Configured local model · single-turn · no history saved", "نموذج محلي مضبوط · محادثة مستقلة · لا يُحفظ السجل"],
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
  if (sceneState) sceneState.refreshLabels();
}

function setChatState(key, state = "checking") {
  chatStateKey = key;
  chatState.dataset.state = state;
  let text = CHAT_STATUS[key] ?? CHAT_STATUS.failed;
  if (configuredModel && configuredProvider && (key === "ready" || key === "sending")) {
    text = key === "ready"
      ? [`${configuredProvider} · ${configuredModel} · single-turn · no history saved`,
        `${configuredProvider} · ${configuredModel} · محادثة مستقلة · لا يُحفظ السجل`]
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

function updateDeveloperControls() {
  const readyRepo = developerRepositories.find((repo) => repo.id === activeDeveloperRepoId
    && repo.index_status === "ready");
  const enabled = authenticated && Boolean(activeWorkspaceId) && Boolean(readyRepo) && !developerBusy;
  developerSearch.disabled = !enabled;
  developerSearchForm.querySelector("button[type=submit]").disabled = !enabled;
  developerQuestion.disabled = !enabled;
  developerAsk.disabled = !enabled;
  developerRepoSelect.disabled = !authenticated || !activeWorkspaceId || !developerRepositories.length;
}

async function loadDeveloperRepositories() {
  clearDeveloperResults();
  developerRepositories = [];
  activeDeveloperRepoId = null;
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
  activeDeveloperRepoId = developerRepoSelect.value || null;
  clearDeveloperResults();
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
  }
  updateDeveloperControls();
}

async function searchDeveloperFiles(event) {
  event.preventDefault();
  if (!activeDeveloperRepoId || developerBusy) return;
  const query = developerSearch.value.trim();
  if (!query || query.length > 256) return;
  clearDeveloperResults();
  setDeveloperState("Searching the indexed snapshot…", "جارٍ البحث في النسخة المفهرسة…");
  try {
    const response = await fetch(`/api/v1/developer/repositories/${encodeURIComponent(activeDeveloperRepoId)}/files?q=${encodeURIComponent(query)}`,
      { credentials: "same-origin", cache: "no-store" });
    const value = await responseJson(response);
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
      detail.textContent = `${result.language}${symbols ? ` · ${symbols}` : " · symbols unknown"}`;
      button.append(name, detail);
      button.addEventListener("click", () => openDeveloperSource(result.id));
      developerResults.append(button);
    }
    setDeveloperState(value.results.length ? "Select a result to view its indexed source." : "No indexed source matched this query.",
      value.results.length ? "اختر نتيجة لعرض المصدر المفهرس." : "لم يطابق البحث أي مصدر مفهرس.");
  } catch (_error) {
    setDeveloperState("Repository search failed safely.", "تعذر البحث في المستودع بأمان.");
  }
}

async function openDeveloperSource(fileId) {
  if (!activeDeveloperRepoId || typeof fileId !== "string") return;
  developerSource.hidden = true;
  try {
    const response = await fetch(`/api/v1/developer/repositories/${encodeURIComponent(activeDeveloperRepoId)}/files/${encodeURIComponent(fileId)}`,
      { credentials: "same-origin", cache: "no-store" });
    const value = await responseJson(response);
    if (response.status === 401) return markSessionExpired();
    if (!response.ok || value.workspace_id !== activeWorkspaceId || value.source?.id !== fileId) {
      throw new Error("source_unavailable");
    }
    const source = value.source;
    developerSource.textContent = `${source.relative_path} · ${source.commit_sha.slice(0, 12)} · lines ${source.start_line}-${source.end_line}\n${source.content}`;
    developerSource.hidden = false;
  } catch (_error) {
    setDeveloperState("This indexed source is no longer available.", "لم يعد هذا المصدر المفهرس متاحًا.");
  }
}

async function askDeveloper(event) {
  event.preventDefault();
  if (!authenticated || !csrfToken || !activeWorkspaceId || !activeDeveloperRepoId || developerBusy) return;
  const message = developerQuestion.value.trim();
  if (!message || message.length > 1000) return;
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
      body: JSON.stringify({ repository_id: activeDeveloperRepoId, message })
    });
    const value = await responseJson(response);
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
      card.textContent = `[${citation.label}] ${citation.evidence_type || "source"} · ${citation.relative_path} · ${citation.commit_sha.slice(0, 12)} · ${citation.start_line}-${citation.end_line}`;
      developerAnswer.append(card);
    }
  } catch (error) {
    developerAnswer.textContent = error.name === "AbortError"
      ? (language === "ar" ? "انتهت مهلة سؤال المطور." : "Developer question timed out.")
      : (language === "ar" ? "تعذر إكمال السؤال بأمان." : "The developer question could not be completed safely.");
  } finally {
    clearTimeout(timeout);
    developerBusy = false;
    updateDeveloperControls();
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
    activeWorkspaceId = null;
    developerRepositories = [];
    activeDeveloperRepoId = null;
    developerRepoSelect.replaceChildren();
    clearDeveloperResults();
    setDeveloperState("Pair and select a workspace to load repositories.", "اربط الجلسة واختر مساحة عمل لتحميل المستودعات.");
    workspaceServiceUnavailable = false;
    workspaceSelect.replaceChildren();
    clearEphemeralWorkspaceView();
    setWorkspaceState("Pair this browser to load authorized workspaces.", "اربط هذا المتصفح لتحميل مساحات العمل المصرح بها.");
  }
  if (authenticated) {
    setChatState("ready", "ready");
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
}

function markSessionExpired() {
  setAuthenticated(false, null, null, null, false);
  setChatState("restart_required", "error");
  setStatusLine([
    "Session expired. Restart the PowerShell service, then reload to pair again.",
    "انتهت الجلسة. أعد تشغيل خدمة PowerShell ثم أعد تحميل الصفحة للربط مجددًا."
  ], true);
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
      body: JSON.stringify({ content, user_authored,
        expected_workspace_id: activeWorkspaceId,
        ...(userAuthored ? {} : { evidence_request_id: latestEvidenceRequestId, citation_labels: labels }) })
    });
    const value = await responseJson(response);
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

async function responseJson(response) {
  try {
    const value = await response.json();
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch (_error) {
    return {};
  }
}

async function loadWorkspaces(preferredWorkspaceId = null) {
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
    } else {
      workspaceSelect.value = "";
      activeWorkspaceId = null;
      workspaceServiceUnavailable = false;
      chatInput.disabled = !authenticated;
      chatSend.disabled = !authenticated;
      createCandidateOpen.disabled = true;
      setWorkspaceState("Workspace APIs are unavailable. General chat remains available.", "واجهات مساحات العمل غير متاحة. تظل الدردشة العامة متاحة.");
      await loadDeveloperRepositories();
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
}

async function selectWorkspace() {
  if (!authenticated || !csrfToken || sending) return;
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
    chatInput.focus();
  } catch (_error) {
    setPairError(language === "ar" ? "تعذر الاتصال بالخدمة المحلية." : "Could not connect to the local service.");
  } finally {
    pairSubmit.disabled = false;
  }
}

async function sendChat(event) {
  event.preventDefault();
  if (!authenticated || sending || !csrfToken || workspaceServiceUnavailable) return;
  const message = chatInput.value.trim();
  if (!message || message.length > 1000) return;
  sending = true;
  chatInput.disabled = true;
  chatSend.disabled = true;
  workspaceSelect.disabled = true;
  setChatState("sending", "busy");
  setStatusLine("");
  appendChatEntry("user", message);
  const pending = appendChatEntry("assistant pending", language === "ar" ? "جارٍ إنشاء رد محلي…" : "Generating a local response…");
  chatInput.value = "";
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 310000);
  try {
    const response = await fetch("/api/v1/chat", {
      method: "POST", credentials: "same-origin", cache: "no-store", signal: controller.signal,
      headers: { "Content-Type": "application/json", "X-Jarvis-CSRF": csrfToken },
      body: JSON.stringify({ message })
    });
    const value = await responseJson(response);
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
  } catch (error) {
    pending.classList.remove("pending");
    pending.classList.add("error");
    pending.textContent = error.name === "AbortError"
      ? (language === "ar" ? "انتهت مهلة انتظار النموذج المحلي." : "Timed out waiting for the local model.")
      : errorMessage("default");
    setChatState("unavailable", "error");
  } finally {
    clearTimeout(timeout);
    sending = false;
    chatInput.disabled = !authenticated;
    chatSend.disabled = !authenticated;
    workspaceSelect.disabled = !authenticated;
    if (authenticated) chatInput.focus();
  }
}

async function disconnectBrowser() {
  if (!authenticated || !csrfToken) return;
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
developerRepoSelect.addEventListener("change", selectDeveloperRepository);
developerSearchForm.addEventListener("submit", searchDeveloperFiles);
developerChatForm.addEventListener("submit", askDeveloper);
search.addEventListener("input", updateSearch);
pairForm.addEventListener("submit", pairBrowser);
chatForm.addEventListener("submit", sendChat);
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
  if (event.key === "/" && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) { event.preventDefault(); search.focus(); }
});
applyLanguage("en");
restoreSession();
initGalaxy().catch(() => useFallback());
