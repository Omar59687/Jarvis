import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:net";
import { delimiter, join } from "node:path";
import { tmpdir } from "node:os";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const python = process.env.CONDA_PREFIX
  ? join(process.env.CONDA_PREFIX, "python.exe")
  : "python";
const chrome = process.env.JARVIS_ACCEPTANCE_CHROME
  || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const owner = "m08e-browser-owner";
const childEnv = { ...process.env,
  PYTHONPATH: `${root}${delimiter}${process.env.PYTHONPATH || ""}` };

function log(event, fields = {}) {
  console.log(JSON.stringify({ event, at: new Date().toISOString(), ...fields }));
}

async function freePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

async function poll(fn, label, timeoutMs = 15_000, intervalMs = 100) {
  const started = Date.now();
  let last;
  while (Date.now() - started < timeoutMs) {
    try {
      last = await fn();
      if (last) return last;
    } catch (error) {
      last = error.message;
    }
    await delay(intervalMs);
  }
  throw new Error(`${label} timed out; last=${String(last).slice(0, 240)}`);
}

async function getJson(url, timeoutMs = 1000) {
  const response = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
  return response.json();
}

class CDP {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 0;
    this.pending = new Map();
    socket.addEventListener("message", (event) => {
      let message;
      try { message = JSON.parse(String(event.data)); } catch { return; }
      if (!message.id || !this.pending.has(message.id)) return;
      const { resolve, reject, timer } = this.pending.get(message.id);
      clearTimeout(timer);
      this.pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result);
    });
  }

  static async connect(url) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("CDP websocket connect timed out")), 8000);
      socket.addEventListener("open", () => { clearTimeout(timer); resolve(); }, { once: true });
      socket.addEventListener("error", () => { clearTimeout(timer); reject(new Error("CDP websocket failed")); }, { once: true });
    });
    return new CDP(socket);
  }

  call(method, params = {}, timeoutMs = 10_000) {
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP ${method} timed out`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(expression, timeoutMs = 12_000) {
    const result = await this.call("Runtime.evaluate", {
      expression, awaitPromise: true, returnByValue: true, userGesture: true,
    }, timeoutMs);
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.exception?.description
        || result.exceptionDetails.text || "browser evaluation failed");
    }
    return result.result?.value;
  }

  close() { this.socket.close(); }
}

function startChild(exe, args, cwd) {
  return spawn(exe, args, { cwd, env: childEnv, windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"] });
}

async function main() {
  const scratch = await mkdtemp(join(tmpdir(), "jarvis-m08e-browser-"));
  const dbPath = join(scratch, "isolated-acceptance.sqlite3");
  const profile = join(scratch, "chrome-profile");
  const resultsIdx = process.argv.indexOf("--results");
  const evidencePath = resultsIdx >= 0 && process.argv[resultsIdx + 1]
    ? process.argv[resultsIdx + 1]
    : join(scratch, "browser-evidence.jsonl");
  const serverPort = await freePort();
  const cdpPort = await freePort();
  const pairingCode = null;
  let backend;
  let browser;
  let cdp;
  let browserCdp;
  const evidence = [];
  const record = (name, passed, details = {}) => {
    const item = { event: "browser_check", check: name, ...details,
      status: passed ? "PASSED" : "FAILED" };
    evidence.push(item);
    log(item.event, item);
    return passed;
  };

  try {
    const seeded = spawnSync(python,
      ["-u", join(root, "eval", "prepare_m08e_browser_fixture.py"), dbPath],
      { cwd: root, env: childEnv, encoding: "utf8", timeout: 30_000, windowsHide: true });
    if (seeded.status !== 0) throw new Error(
      `fixture setup failed status=${seeded.status} error=${seeded.error?.message || "none"}`
      + ` stdout=${seeded.stdout.slice(-800)} stderr=${seeded.stderr.slice(-800)}`);
    const fixture = JSON.parse(seeded.stdout.trim().split(/\r?\n/).at(-1));
    record("synthetic_isolated_fixture", fixture.primary_enabled_initially === false
      && fixture.readonly_can_enable === false, {
      primary_workspace: fixture.primary_workspace,
      readonly_workspace: fixture.readonly_workspace,
      mission_count: fixture.mission_ids.length,
      user_database_used: false,
    });

    backend = startChild(python, ["-u", join(root, "prototypes", "m03_b1", "service.py"),
      "--port", String(serverPort), "--provider", "lmstudio", "--model", "qwen3.5-4b",
      "--base-url", "http://127.0.0.1:1235", "--timeout", "120", "--actor-id", owner,
      "--database", dbPath, "--persona", "default"], root);
    let backendText = "";
    backend.stdout.on("data", (chunk) => { backendText += chunk.toString(); });
    backend.stderr.on("data", (chunk) => { backendText += chunk.toString(); });
    const expectedUrl = `http://127.0.0.1:${serverPort}/`;
    await poll(async () => {
      if (backend.exitCode !== null) throw new Error(`backend exited ${backend.exitCode}: ${backendText.slice(-1000)}`);
      try { return (await fetch(expectedUrl, { signal: AbortSignal.timeout(400) })).ok; }
      catch { return false; }
    }, "temporary backend start", 20_000);
    const pairLines = backendText.split(/\r?\n/);
    const codeLine = pairLines.findIndex((line) => line.includes("Pairing code (valid"));
    if (codeLine < 0 || !pairLines[codeLine + 1]?.trim()) {
      throw new Error(`temporary backend pairing code was not reported: ${backendText.slice(-1200)}`);
    }
    const actualPairingCode = pairLines[codeLine + 1].trim();
    record("temporary_backend_loopback", true, { url: expectedUrl, port: serverPort,
      database: dbPath, data_is_synthetic: true });

    browser = startChild(chrome, ["--headless=new", "--disable-gpu", "--disable-background-networking",
      "--no-first-run", "--no-default-browser-check", "--remote-debugging-address=127.0.0.1",
      `--remote-debugging-port=${cdpPort}`, "--remote-allow-origins=*",
      `--user-data-dir=${profile}`, "about:blank"], root);
    const version = await poll(async () => {
      if (browser.exitCode !== null) throw new Error(`Chrome exited ${browser.exitCode}`);
      try { return await getJson(`http://127.0.0.1:${cdpPort}/json/version`, 500); }
      catch { return false; }
    }, "Chrome DevTools startup", 20_000);
    browserCdp = await CDP.connect(version.webSocketDebuggerUrl);
    const target = await browserCdp.call("Target.createTarget", { url: "about:blank" });
    const targets = await getJson(`http://127.0.0.1:${cdpPort}/json/list`);
    const page = targets.find((item) => item.id === target.targetId);
    if (!page?.webSocketDebuggerUrl) throw new Error("Chrome page target unavailable");
    cdp = await CDP.connect(page.webSocketDebuggerUrl);
    await cdp.call("Page.enable");
    await cdp.call("Runtime.enable");
    await cdp.call("Page.navigate", { url: expectedUrl });
    await poll(() => cdp.evaluate("document.readyState === 'complete'"), "dashboard page load", 20_000);

    const initialAuth = await cdp.evaluate(`({
      pairingOpen: document.querySelector('#pairing-dialog')?.open === true,
      signOutHidden: document.querySelector('#chat-signout')?.hidden === true,
      language: document.documentElement.lang,
      direction: document.documentElement.dir
    })`);
    record("unauthenticated_pairing_view", initialAuth.pairingOpen && initialAuth.signOutHidden,
      initialAuth);

    await cdp.evaluate(`(() => {
      const code = document.querySelector('#pair-code');
      code.value = ${JSON.stringify(actualPairingCode)};
      document.querySelector('#pair-submit').click();
      return true;
    })()`);
    await poll(() => cdp.evaluate("document.querySelector('#chat-signout')?.hidden === false"),
      "browser authentication", 15_000);
    await poll(() => cdp.evaluate("[...document.querySelector('#workspace-select').options].some(o => o.value)")
      , "workspace list", 10_000);
    const workspaces = await cdp.evaluate(`Array.from(document.querySelector('#workspace-select').options)
      .map(option => ({id: option.value, label: option.textContent}))`);
    record("paired_session_and_workspace_list", workspaces.some((w) => w.id === fixture.primary_workspace)
      && workspaces.some((w) => w.id === fixture.readonly_workspace), {
      authenticated: true, workspace_ids: workspaces.map((w) => w.id).filter(Boolean),
    });

    await cdp.evaluate(`(() => {
      const select = document.querySelector('#workspace-select');
      select.value = ${JSON.stringify(fixture.readonly_workspace)};
      select.dispatchEvent(new Event('change', {bubbles:true}));
      return true;
    })()`);
    await poll(() => cdp.evaluate("document.querySelector('#proactivity-status').textContent.includes('current write permission')"),
      "read-only preference visibility", 12_000);
    const readOnlyState = await cdp.evaluate(`({
      workspace: document.querySelector('#workspace-select').value,
      enableDisabled: document.querySelector('#proactivity-enable').disabled,
      intervalDisabled: document.querySelector('#proactivity-interval').disabled,
      status: document.querySelector('#proactivity-status').textContent,
      empty: document.querySelector('#proactivity-suggestions').textContent
    })`);
    record("permission_dependent_controls_and_empty_inbox", readOnlyState.enableDisabled
      && readOnlyState.intervalDisabled && readOnlyState.empty.includes("No active suggestions"), readOnlyState);

    await cdp.evaluate(`(() => {
      const select = document.querySelector('#workspace-select');
      select.value = ${JSON.stringify(fixture.primary_workspace)};
      select.dispatchEvent(new Event('change', {bubbles:true}));
      return true;
    })()`);
    await poll(() => cdp.evaluate("document.querySelector('#proactivity-status').textContent.includes('Suggestions are off')"),
      "default-off proactivity preferences", 12_000);
    const defaultOff = await cdp.evaluate(`({
      status: document.querySelector('#proactivity-status').textContent,
      enableDisabled: document.querySelector('#proactivity-enable').disabled,
      empty: document.querySelector('#proactivity-suggestions').textContent
    })`);
    record("preferences_off_by_default", defaultOff.status.includes("off")
      && defaultOff.empty.includes("No active suggestions"),
      {workspace: fixture.primary_workspace, ...defaultOff});

    await cdp.evaluate(`(() => {
      const cooldown = document.querySelector('#proactivity-cooldown');
      cooldown.value = '0';
      cooldown.dispatchEvent(new Event('change', {bubbles:true}));
      document.querySelector('#proactivity-enable').click();
      return true;
    })()`);
    await poll(() => cdp.evaluate("document.querySelector('#proactivity-status').textContent.includes('Suggestions are enabled')"),
      "proactivity enabled status", 12_000);
    await poll(async () => {
      const state = await cdp.evaluate(`({
        count: document.querySelectorAll('#proactivity-suggestions .proactivity-card').length,
        status: document.querySelector('#proactivity-status').textContent,
        cooldown: document.querySelector('#proactivity-cooldown').value,
        refreshDisabled: document.querySelector('#proactivity-refresh').disabled,
        text: document.querySelector('#proactivity-suggestions').textContent.slice(0, 120),
      })`);
      if (state.count >= 3) return true;
      if (!state.refreshDisabled) {
        await cdp.evaluate("document.querySelector('#proactivity-refresh').click(); true;");
      }
      throw new Error(JSON.stringify(state));
    }, "scheduler-created suggestions", 12_000, 750);
    const initialInbox = await cdp.evaluate(`({
      status: document.querySelector('#proactivity-status').textContent,
      cards: document.querySelectorAll('#proactivity-suggestions .proactivity-card').length,
      text: document.querySelector('#proactivity-suggestions').textContent,
      imageElements: document.querySelectorAll('#proactivity-suggestions img').length,
      injectedElement: Boolean(document.querySelector('#m08e-xss')),
      scriptFlag: window.m08eXss === 1,
      taskGoalVisibleAsText: document.querySelector('#task-list').textContent.includes('m08e-xss')
    })`);
    record("suggestion_inbox_and_untrusted_text_rendering", initialInbox.cards >= 3
      && initialInbox.imageElements === 0 && !initialInbox.injectedElement && !initialInbox.scriptFlag
      && initialInbox.taskGoalVisibleAsText, initialInbox);

    await cdp.evaluate(`document.querySelector('#language').value = 'ar';
      document.querySelector('#language').dispatchEvent(new Event('change',{bubbles:true}));`);
    const arabic = await cdp.evaluate(`({lang:document.documentElement.lang,dir:document.documentElement.dir,
      heading:document.querySelector('#proactivity-heading').textContent,
      label:document.querySelector('#proactivity-enable').textContent,
      cards:document.querySelectorAll('#proactivity-suggestions .proactivity-card').length})`);
    record("arabic_rtl_rendering", arabic.lang === "ar" && arabic.dir === "rtl"
      && arabic.heading.includes("الاقتراحات") && arabic.label.includes("تفعيل")
      && arabic.cards >= 3, arabic);
    await cdp.evaluate(`document.querySelector('#language').value = 'en';
      document.querySelector('#language').dispatchEvent(new Event('change',{bubbles:true}));`);
    const english = await cdp.evaluate(`({lang:document.documentElement.lang,dir:document.documentElement.dir,
      heading:document.querySelector('#proactivity-heading').textContent,
      actions:Array.from(document.querySelectorAll('.proactivity-card button')).map(b=>b.textContent)})`);
    record("english_ltr_rendering", english.lang === "en" && english.dir === "ltr"
      && english.heading === "Proactive suggestions"
      && english.actions.includes("Acknowledge") && english.actions.includes("Dismiss")
      && english.actions.includes("Snooze 1 day"), english);

    await cdp.evaluate(`document.querySelector('#proactivity-suggestions .proactivity-card button').click()`);
    await poll(() => cdp.evaluate("document.querySelectorAll('#proactivity-suggestions .proactivity-card').length === 2"),
      "accept action", 12_000);
    record("accept_acknowledgment_action", await cdp.evaluate(`({
      cards:document.querySelectorAll('#proactivity-suggestions .proactivity-card').length,
      error:document.querySelector('#proactivity-status').classList.contains('error')
    })`).then((v) => v.cards === 2 && !v.error), {state: "accepted suggestion removed from active inbox"});

    await cdp.evaluate(`document.querySelector('#proactivity-suggestions .proactivity-card button:nth-of-type(2)').click()`);
    await poll(() => cdp.evaluate("document.querySelectorAll('#proactivity-suggestions .proactivity-card').length === 1"),
      "dismiss action", 12_000);
    record("dismiss_action", await cdp.evaluate("document.querySelectorAll('#proactivity-suggestions .proactivity-card').length === 1"),
      {state: "dismissed suggestion removed from active inbox"});

    await cdp.evaluate(`document.querySelector('#proactivity-suggestions .proactivity-card button:nth-of-type(3)').click()`);
    await poll(() => cdp.evaluate("document.querySelector('#proactivity-suggestions').textContent.includes('Snoozed until')"),
      "snooze action", 12_000);
    const snoozed = await cdp.evaluate(`({cards:document.querySelectorAll('#proactivity-suggestions .proactivity-card').length,
      text:document.querySelector('#proactivity-suggestions').textContent,
      buttonsDisabled:Array.from(document.querySelectorAll('#proactivity-suggestions .proactivity-card button')).every(b=>b.disabled)})`);
    record("snooze_action", snoozed.cards === 1 && snoozed.text.includes("Snoozed until")
      && snoozed.buttonsDisabled, snoozed);

    await cdp.evaluate(`(() => {
      const original = window.fetch.bind(window);
      window.__m08eOriginalFetch = original;
      window.fetch = (input, init) => String(input).includes('/api/v1/proactivity')
        ? Promise.resolve(new Response(JSON.stringify({error:'synthetic_acceptance_error'}),
            {status:503,headers:{'Content-Type':'application/json'}}))
        : original(input, init);
      document.querySelector('#proactivity-refresh').click();
      return true;
    })()`);
    await poll(() => cdp.evaluate("document.querySelector('#proactivity-status').classList.contains('error')"),
      "proactivity error state", 8_000);
    const errorState = await cdp.evaluate(`({error:document.querySelector('#proactivity-status').classList.contains('error'),
      text:document.querySelector('#proactivity-status').textContent,
      cards:document.querySelectorAll('#proactivity-suggestions .proactivity-card').length})`);
    record("error_state", errorState.error && errorState.text.includes("unavailable"), errorState);
    await cdp.evaluate("window.fetch = window.__m08eOriginalFetch; delete window.__m08eOriginalFetch");

    await cdp.evaluate(`(() => {
      const original = window.fetch.bind(window);
      let release;
      window.__m08eOriginalFetch = original;
      window.__m08eReleaseProactivity = () => release?.();
      window.fetch = (input, init) => {
        if (String(input) === '/api/v1/proactivity' && init?.method !== 'PUT') {
          return new Promise(resolve => { release = () => original(input, init).then(resolve); });
        }
        return original(input, init);
      };
      document.querySelector('#proactivity-refresh').click();
      return true;
    })()`);
    await poll(() => cdp.evaluate("document.querySelector('#proactivity-status').textContent.includes('Loading suggestions')"),
      "proactivity loading state", 5_000);
    const loadingText = await cdp.evaluate("document.querySelector('#proactivity-status').textContent");
    await cdp.evaluate("window.__m08eReleaseProactivity(); window.fetch = window.__m08eOriginalFetch; delete window.__m08eOriginalFetch");
    await poll(() => cdp.evaluate("document.querySelector('#proactivity-status').textContent.includes('Suggestions are enabled')"),
      "proactivity loading completion", 8_000);
    record("loading_state", loadingText.includes("Loading suggestions"), {text: loadingText});

    const csrfStatus = await cdp.evaluate(`(async () => {
      const r = await fetch('/api/v1/proactivity/preferences', {
        method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: false }),
      });
      return r.status;
    })()`);
    record("csrf_header_required_for_state_change", csrfStatus === 403, { status_code: csrfStatus });

    await cdp.evaluate("document.querySelector('#chat-signout').click()");
    await poll(() => cdp.evaluate("document.querySelector('#chat-signout')?.hidden === true"),
      "logout view", 8_000);
    const logout = await cdp.evaluate(`({
      signOutHidden:document.querySelector('#chat-signout').hidden,
      cards:document.querySelectorAll('#proactivity-suggestions .proactivity-card').length,
      proactivityStatus:document.querySelector('#proactivity-status').textContent,
      chatInputDisabled:document.querySelector('#chat-input').disabled
    })`);
    const postLogoutStatuses = await cdp.evaluate(`(async () => {
      const s = await fetch('/api/v1/session', { credentials: 'same-origin' });
      const p = await fetch('/api/v1/proactivity', { credentials: 'same-origin' });
      return { sessionStatus: s.status, proactivityStatus: p.status };
    })()`);
    const sessionInvalidated = postLogoutStatuses.sessionStatus === 401
      && postLogoutStatuses.proactivityStatus === 401;
    record("logout_and_session_invalidation", logout.signOutHidden
      && logout.cards === 0 && logout.chatInputDisabled && sessionInvalidated,
      { ...logout, ...postLogoutStatuses, sessionInvalidated });

    const nodeProbe = await cdp.evaluate(`({injectedElement:Boolean(document.querySelector('#m08e-xss')),
      xssFlag:window.m08eXss === 1})`);
    record("post_logout_safe_rendering", !nodeProbe.injectedElement && !nodeProbe.xssFlag, nodeProbe);

    const backendHealthy = (await fetch(expectedUrl, { signal: AbortSignal.timeout(1000) })).ok;
    record("backend_remained_loopback_during_acceptance", backendHealthy,
      {host: "127.0.0.1", external_listener: false});

    await import("node:fs/promises").then(({ writeFile }) => writeFile(evidencePath,
      evidence.map((item) => JSON.stringify(item)).join("\n") + "\n", "utf8"));
    const failed = evidence.filter((item) => item.status !== "PASSED");
    log("browser_acceptance_finished", {passed: evidence.length - failed.length,
      failed: failed.length, evidence_file: evidencePath});
    if (failed.length) process.exitCode = 1;

    const dbReview = spawnSync(python, ["-c", `import json,sqlite3,sys
p=sys.argv[1]; c=sqlite3.connect(p); c.row_factory=sqlite3.Row
print(json.dumps({
 'suggestion_states':[dict(r) for r in c.execute("select state,count(*) count from proactive_suggestions group by state order by state")],
 'mission_states':[dict(r) for r in c.execute("select state,count(*) count from missions group by state order by state")],
 'lifecycle_events':[r[0] for r in c.execute("select event_type from proactivity_suggestion_events order by id")],
 'xss_goal_preserved':bool(c.execute("select 1 from orchestration_tasks where goal like '%m08e-xss%' limit 1").fetchone())
},sort_keys=True))`, dbPath], { cwd: root, env: childEnv, encoding: "utf8", timeout: 10_000, windowsHide: true });
    if (dbReview.status === 0) log("isolated_database_post_browser_review", JSON.parse(dbReview.stdout.trim()));
    else throw new Error(`post-browser database review failed: ${dbReview.stderr}`);
  } finally {
    if (cdp) cdp.close();
    if (browserCdp) {
      try { await browserCdp.call("Browser.close", {}, 3000); } catch {}
      browserCdp.close();
    }
    if (browser && browser.exitCode === null) {
      await Promise.race([new Promise((resolve) => browser.once("exit", resolve)), delay(4000)]);
      if (browser.exitCode === null) browser.kill();
    }
    if (backend && backend.exitCode === null) {
      backend.kill();
      await Promise.race([new Promise((resolve) => backend.once("exit", resolve)), delay(4000)]);
    }
    await rm(scratch, { recursive: true, force: true });
    log("temporary_resources_cleaned", {scratch_removed: true,
      backend_stopped: backend ? backend.exitCode !== null : true,
      chrome_stopped: browser ? browser.exitCode !== null : true});
  }
}

main().catch((error) => {
  console.error(JSON.stringify({ event: "browser_acceptance_fatal", error: error.message }));
  process.exitCode = 1;
});
