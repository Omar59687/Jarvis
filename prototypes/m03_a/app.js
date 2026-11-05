import { NODES, EDGES, connectedNodeIds, searchNodeIds, classifyNode } from "/galaxy-model.mjs";

const $ = (selector) => document.querySelector(selector);
const languageSelect = $("#language");
const nodeList = $("#node-list");
const details = $("#node-details");
const emptyDetails = $("#selection-empty");
const canvasHost = $("#galaxy-canvas");
const fallback = $("#fallback-graph");
const search = $("#graph-search");
let language = "en";
let selectedId = null;
let visibleIds = searchNodeIds(NODES, "", language);
let reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let sceneState = null;

function localized(value) { return value?.[language] ?? value?.en ?? ""; }
function applyLanguage(next) {
  language = next;
  document.documentElement.lang = language;
  document.documentElement.dir = language === "ar" ? "rtl" : "ltr";
  for (const element of document.querySelectorAll("[data-en][data-ar]")) element.textContent = element.dataset[language];
  for (const element of document.querySelectorAll("[data-en-placeholder][data-ar-placeholder]")) {
    element.placeholder = element.dataset[`${language}Placeholder`];
  }
  for (const element of document.querySelectorAll("[data-en-title][data-ar-title]")) element.title = element.dataset[`${language}Title`];
  $("#motion-toggle").setAttribute("aria-pressed", String(!reduceMotion));
  $("#motion-label").textContent = language === "ar"
    ? (reduceMotion ? "الحركة مخفّضة" : "الحركة مفعّلة")
    : (reduceMotion ? "Motion reduced" : "Motion on");
  visibleIds = searchNodeIds(NODES, search.value, language);
  renderNodeList();
  renderDetails();
  renderFallback();
  if (sceneState) sceneState.refreshLabels();
}

function renderNodeList() {
  nodeList.replaceChildren();
  const nodes = NODES.filter(({ id }) => visibleIds.has(id));
  $("#node-count").textContent = String(nodes.length).padStart(2, "0");
  $("#empty-search").hidden = nodes.length > 0;
  for (const node of nodes) {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("aria-pressed", String(selectedId === node.id));
    button.setAttribute("aria-label", localized(node.label));
    button.innerHTML = `<span class="node-kind" aria-hidden="true" style="color:${node.color};background:${node.color}"></span><span class="node-name"></span>`;
    button.querySelector(".node-name").textContent = localized(node.label);
    button.addEventListener("click", () => selectNode(node.id));
    nodeList.append(button);
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
  renderer.domElement.tabIndex = 0;
  renderer.domElement.setAttribute("aria-label", language === "ar" ? "رسم مجرة معرفة اصطناعية ثلاثي الأبعاد. استخدم قائمة العقد للتنقل بلوحة المفاتيح." : "Synthetic 3D knowledge galaxy. Use the node list to navigate by keyboard.");
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
  const focusPoint = (id) => id && objects.has(id)
    ? objects.get(id).getWorldPosition(new THREE.Vector3())
    : new THREE.Vector3();
  const resize = () => {
    const rect = canvasHost.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    renderer.setSize(rect.width, rect.height, false);
    camera.aspect = rect.width / rect.height; camera.updateProjectionMatrix();
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
    if (reduceMotion) renderer.render(scene, camera);
  };
  renderer.domElement.addEventListener("pointerdown", (event) => { dragging = true; moved = false; lastX = event.clientX; lastY = event.clientY; renderer.domElement.setPointerCapture(event.pointerId); });
  renderer.domElement.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    const dx = event.clientX - lastX; const dy = event.clientY - lastY;
    if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
    orbit.rotation.y += dx * .004; orbit.rotation.x = Math.max(-.65, Math.min(.65, orbit.rotation.x + dy * .003));
    lastX = event.clientX; lastY = event.clientY;
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
    if (reduceMotion) renderer.render(scene, camera);
  }, { passive: false });
  renderer.domElement.addEventListener("keydown", (event) => {
    if (event.key === "Escape") selectNode(null);
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectNode(NODES[0].id); }
  });
  let animationId;
  const render = () => {
    if (!reduceMotion) starField.rotation.y += .00012;
    if (selectedId) {
      orbit.updateMatrixWorld(true);
      target.copy(focusPoint(selectedId));
      desiredCamera.set(target.x, target.y, target.z + distance);
    }
    if (!reduceMotion) camera.position.lerp(desiredCamera, .085);
    camera.lookAt(target);
    renderer.render(scene, camera);
    if (!reduceMotion) animationId = requestAnimationFrame(render);
  };
  render();
  const refreshLabels = () => {
    for (const item of nodeMeshes.values()) {
      item.label.material.map?.dispose(); item.label.material.map = makeLabelTexture(THREE, localized(item.node.label), item.node.color); item.label.material.needsUpdate = true;
    }
  };
  sceneState = {
    select, setVisible: () => applyEmphasis(), refreshLabels,
    setMotion: (enabled) => { reduceMotion = enabled; cancelAnimationFrame(animationId); if (!enabled) render(); else { camera.position.copy(desiredCamera); renderer.render(scene, camera); } },
    reset: () => { orbit.rotation.set(0, 0, 0); distance = 11; select(null); },
    dispose: () => { cancelAnimationFrame(animationId); resizeObserver.disconnect(); renderer.dispose(); }
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
search.addEventListener("input", updateSearch);
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
initGalaxy().catch(() => useFallback());
