"use strict";

const language = document.querySelector("#language");
const health = document.querySelector("#health");
const mockMessage = document.querySelector("#mock-message");

function applyLanguage(value) {
  const isArabic = value === "ar";
  document.documentElement.lang = isArabic ? "ar" : "en";
  document.documentElement.dir = isArabic ? "rtl" : "ltr";
  for (const element of document.querySelectorAll("[data-en][data-ar]")) {
    element.textContent = element.dataset[value];
  }
  for (const element of document.querySelectorAll("[data-en-placeholder][data-ar-placeholder]")) {
    element.placeholder = element.dataset[`${value}Placeholder`];
  }
  document.querySelectorAll("#workspace option").forEach((option, index) => {
    option.textContent = option.dataset[value];
  });
}

language.addEventListener("change", () => applyLanguage(language.value));
document.querySelectorAll("#search-form, #chat-form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    mockMessage.textContent = language.value === "ar"
      ? "هذا نموذج واجهة فقط؛ لم يتم إرسال السؤال إلى خدمة Jarvis."
      : "Mock UI only; the question was not sent to a Jarvis service.";
  });
});

async function checkHealth() {
  try {
    const response = await fetch("/health", { credentials: "omit", cache: "no-store" });
    const value = await response.json();
    if (!response.ok || value.prototype !== "m03-a" || value.data_connected !== false) {
      throw new Error("unexpected health response");
    }
    health.textContent = language.value === "ar" ? "الخدمة التجريبية تعمل" : "Prototype service is running";
    health.dataset.state = "ok";
  } catch (_error) {
    health.textContent = language.value === "ar" ? "الخدمة غير متاحة" : "Prototype service unavailable";
    health.dataset.state = "error";
  }
}

applyLanguage("en");
checkHealth();
