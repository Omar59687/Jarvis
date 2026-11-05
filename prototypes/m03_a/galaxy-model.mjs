export const NODES = Object.freeze([
  { id: "atlas", kind: "space", color: "#72ddf4", label: { en: "Atlas workspace", ar: "مساحة أطلس" }, description: { en: "A fictional workspace at the center of this sample knowledge map.", ar: "مساحة عمل خيالية في مركز خريطة المعرفة التجريبية." }, source: { en: "Sample workspace", ar: "مساحة عمل تجريبية" } },
  { id: "project", kind: "project", color: "#72ddf4", label: { en: "Northstar project", ar: "مشروع نورث ستار" }, description: { en: "A made-up project linked to planning notes and a reference folder.", ar: "مشروع خيالي مرتبط بملاحظات التخطيط ومجلد مرجعي." }, source: { en: "Sample project", ar: "مشروع تجريبي" } },
  { id: "folder", kind: "folder", color: "#aa91ff", label: { en: "Research folder", ar: "مجلد البحث" }, description: { en: "A fictional folder containing two sample reference files.", ar: "مجلد خيالي يحتوي على ملفين مرجعيين تجريبيين." }, source: { en: "Sample folder", ar: "مجلد تجريبي" } },
  { id: "brief", kind: "file", color: "#b9a8ff", label: { en: "Launch brief.md", ar: "ملخص الإطلاق.md" }, description: { en: "Synthetic document with a fictional launch outline.", ar: "مستند اصطناعي يتضمن مخطط إطلاق خياليًا." }, source: { en: "Sample file", ar: "ملف تجريبي" } },
  { id: "map", kind: "file", color: "#b9a8ff", label: { en: "Concept map.pdf", ar: "خريطة المفاهيم.pdf" }, description: { en: "Synthetic document showing imaginary project relationships.", ar: "مستند اصطناعي يعرض علاقات مشروع تخيلية." }, source: { en: "Sample file", ar: "ملف تجريبي" } },
  { id: "memory", kind: "memory", color: "#82e9c6", label: { en: "Approved memory: quiet hours", ar: "ذاكرة معتمدة: ساعات الهدوء" }, description: { en: "Fictional approved memory: the sample team prefers quiet focus hours.", ar: "ذاكرة معتمدة خيالية: يفضّل الفريق التجريبي ساعات تركيز هادئة." }, source: { en: "Synthetic approved memory", ar: "ذاكرة معتمدة اصطناعية" } },
  { id: "note", kind: "note", color: "#f0c879", label: { en: "Planning note", ar: "ملاحظة تخطيط" }, description: { en: "Fictional note connecting the sample project with its launch brief.", ar: "ملاحظة خيالية تربط المشروع التجريبي بملخص الإطلاق." }, source: { en: "Synthetic note", ar: "ملاحظة اصطناعية" } },
  { id: "archive", kind: "folder", color: "#aa91ff", label: { en: "Archive folder", ar: "مجلد الأرشيف" }, description: { en: "An imaginary folder linked to one sample memory.", ar: "مجلد تخيلي مرتبط بذاكرة تجريبية واحدة." }, source: { en: "Sample folder", ar: "مجلد تجريبي" } }
]);
export const EDGES = Object.freeze([
  ["atlas", "project"], ["atlas", "folder"], ["atlas", "memory"], ["project", "folder"],
  ["project", "note"], ["folder", "brief"], ["folder", "map"], ["brief", "note"],
  ["memory", "archive"], ["map", "project"]
].map(Object.freeze));
export function connectedNodeIds(edges, selectedId) {
  if (!selectedId) return new Set();
  const result = new Set([selectedId]);
  for (const [a, b] of edges) {
    if (a === selectedId) result.add(b);
    if (b === selectedId) result.add(a);
  }
  return result;
}
export function searchNodeIds(nodes, query, language = "en") {
  const normalized = String(query).trim().toLocaleLowerCase(language);
  if (!normalized) return new Set(nodes.map(({ id }) => id));
  return new Set(nodes.filter(({ label, kind }) => `${label[language] ?? label.en} ${kind}`
    .toLocaleLowerCase(language).includes(normalized)).map(({ id }) => id));
}
export function classifyNode(nodeId, nodes = NODES) {
  return nodes.find(({ id }) => id === nodeId) ?? null;
}
