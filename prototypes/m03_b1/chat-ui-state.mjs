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
