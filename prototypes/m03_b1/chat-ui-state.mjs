export function chatInputPlaceholder({ authenticated, workspaceId, language }) {
  const locale = language === "ar" ? "ar" : "en";
  if (!authenticated) {
    return locale === "ar"
      ? "اربط هذا المتصفح لبدء دردشة محلية"
      : "Pair this browser to start a local chat";
  }
  if (workspaceId) {
    return locale === "ar"
      ? "اسأل عن أدلة مساحة العمل المحددة"
      : "Ask about evidence in the selected workspace";
  }
  return locale === "ar"
    ? "اسأل جارفس سؤالًا عامًا"
    : "Ask Jarvis a general question";
}

export function memoryProposalPayload({ content, userAuthored, workspaceId, evidenceRequestId, citationLabels }) {
  return {
    content,
    user_authored: userAuthored,
    expected_workspace_id: workspaceId,
    ...(userAuthored ? {} : {
      evidence_request_id: evidenceRequestId,
      citation_labels: [...citationLabels],
    }),
  };
}

export function memoryProposalError(code, language) {
  const messages = {
    authentication_required: ["Your local session expired. Pair this browser with Jarvis again.", "انتهت جلستك المحلية. أعد ربط هذا المتصفح بجارفس."],
    evidence_request_expired: ["These citations expired. Ask Jarvis again to generate fresh citations.", "انتهت صلاحية هذه الاستشهادات. اسأل جارفس مجددًا لإنشاء استشهادات جديدة."],
    evidence_source_changed: ["The source changed after this answer. Ask Jarvis again before proposing a memory.", "تغيّر المصدر بعد هذه الإجابة. اسأل جارفس مجددًا قبل اقتراح ذاكرة."],
    evidence_selection_required: ["Select an active document citation or mark this as your own note.", "اختر استشهادًا من مستند نشط أو حدّد أنها ملاحظتك الشخصية."],
    evidence_selection_invalid: ["Choose active document citations from the latest answer.", "اختر استشهادات مستندات نشطة من أحدث إجابة."],
    workspace_selection_changed: ["The workspace changed. Select it again and retry your proposal.", "تغيّرت مساحة العمل. اخترها مجددًا ثم أعد إرسال المقترح."],
    write_permission_required: ["Memory creation is not authorized in this workspace.", "إنشاء الذاكرة غير مصرح به في مساحة العمل هذه."],
    candidate_changed: ["The candidate changed. Reopen the latest review snapshot.", "تغيّر المقترح. أعد فتح أحدث نسخة للمراجعة."],
    csrf_rejected: ["The local session check failed. Reload and pair again if needed.", "فشل التحقق من الجلسة المحلية. أعد التحميل والربط إذا لزم الأمر."],
    memory_service_unavailable: ["The memory service is temporarily unavailable. Try again shortly.", "خدمة الذاكرة غير متاحة مؤقتًا. حاول مجددًا بعد قليل."],
  };
  const value = messages[code];
  return value ? value[language === "ar" ? 1 : 0] : null;
}
