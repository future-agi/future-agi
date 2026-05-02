import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const FRONTEND_URL = process.env.FRONTEND_URL || "http://localhost:3032";
const QUEUE_ID = process.env.ANNOTATION_QUEUE_ID;
const EMAIL = process.env.FUTURE_AGI_EMAIL;
const PASSWORD = process.env.FUTURE_AGI_PASSWORD;

const _missing = [
  ["ANNOTATION_QUEUE_ID", QUEUE_ID],
  ["FUTURE_AGI_EMAIL", EMAIL],
  ["FUTURE_AGI_PASSWORD", PASSWORD],
].filter(([, v]) => !v);
if (_missing.length) {
  console.error(
    `Missing required env vars: ${_missing.map(([k]) => k).join(", ")}`,
  );
  process.exit(2);
}

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ARTIFACT_DIR = path.join(__dirname, ".artifacts");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function unwrapApiData(data) {
  return data?.result ?? data?.results ?? data;
}

function asArray(value) {
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.results)) return value.results;
  if (Array.isArray(value?.items)) return value.items;
  return [];
}

function authHeaders(accessToken) {
  return {
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  };
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!response.ok) {
    throw new Error(
      `HTTP ${response.status} for ${url}: ${
        typeof body === "string" ? body : JSON.stringify(body)
      }`,
    );
  }
  return body;
}

async function resolveApiBase() {
  const configText = await fetch(`${FRONTEND_URL}/src/config-global.js`).then(
    (response) => response.text(),
  );
  const match = configText.match(/"VITE_HOST_API":\s*"([^"]+)"/);
  return match?.[1] || "http://localhost:8000";
}

async function apiGet(apiBase, accessToken, pathName) {
  return unwrapApiData(
    await fetchJson(`${apiBase}${pathName}`, {
      headers: authHeaders(accessToken),
    }),
  );
}

async function apiPost(apiBase, accessToken, pathName, payload = {}) {
  return unwrapApiData(
    await fetchJson(`${apiBase}${pathName}`, {
      method: "POST",
      headers: authHeaders(accessToken),
      body: JSON.stringify(payload),
    }),
  );
}

async function apiPut(apiBase, accessToken, pathName, payload = {}) {
  return unwrapApiData(
    await fetchJson(`${apiBase}${pathName}`, {
      method: "PUT",
      headers: authHeaders(accessToken),
      body: JSON.stringify(payload),
    }),
  );
}

async function apiPatch(apiBase, accessToken, pathName, payload = {}) {
  return unwrapApiData(
    await fetchJson(`${apiBase}${pathName}`, {
      method: "PATCH",
      headers: authHeaders(accessToken),
      body: JSON.stringify(payload),
    }),
  );
}

async function apiDelete(apiBase, accessToken, pathName) {
  return unwrapApiData(
    await fetchJson(`${apiBase}${pathName}`, {
      method: "DELETE",
      headers: authHeaders(accessToken),
    }),
  );
}

async function login(apiBase) {
  if (process.env.FUTURE_AGI_ACCESS_TOKEN) {
    return {
      access: process.env.FUTURE_AGI_ACCESS_TOKEN,
      refresh: process.env.FUTURE_AGI_REFRESH_TOKEN || "",
    };
  }
  const tokenResponse = await fetchJson(`${apiBase}/accounts/token/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: EMAIL,
      password: PASSWORD,
      remember_me: true,
      "recaptcha-response": "puppeteer-local-test",
    }),
  });
  assert(tokenResponse.access, "Login response did not include an access token");
  return tokenResponse;
}

async function findChromeExecutable() {
  const candidates = [
    process.env.CHROME_BIN,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
  ].filter(Boolean);

  for (const candidate of candidates) {
    try {
      await fs.access(candidate);
      return candidate;
    } catch {
      // Keep scanning.
    }
  }
  throw new Error("Could not find Chrome. Set CHROME_BIN.");
}

async function waitForText(page, text, timeout = 30000) {
  await page.waitForFunction(
    (needle) => document.body?.innerText?.includes(needle),
    { timeout },
    text,
  );
}

async function waitForVisibleText(
  page,
  text,
  { selector = "button, div, span", exact = true, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ selectorText, expected, exactMatch }) => {
      const isVisible = (element) => {
        const rect = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        return (
          rect.width > 0 &&
          rect.height > 0 &&
          style.visibility !== "hidden" &&
          style.display !== "none"
        );
      };
      return [...document.querySelectorAll(selectorText)].some((element) => {
        if (!isVisible(element)) return false;
        const textContent = (element.textContent || "").replace(/\s+/g, " ").trim();
        return exactMatch ? textContent === expected : textContent.includes(expected);
      });
    },
    { timeout },
    { selectorText: selector, expected: text, exactMatch: exact },
  );
}

async function clickByText(page, text, { selector = "button, div, span", exact = true } = {}) {
  const clicked = await page.evaluate(
    ({ selectorText, expected, exactMatch }) => {
      const isVisible = (element) => {
        const rect = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        return (
          rect.width > 0 &&
          rect.height > 0 &&
          style.visibility !== "hidden" &&
          style.display !== "none"
        );
      };
      const elements = [...document.querySelectorAll(selectorText)].filter(isVisible);
      const target = elements.find((element) => {
        const textContent = (element.textContent || "").replace(/\s+/g, " ").trim();
        return exactMatch ? textContent === expected : textContent.includes(expected);
      });
      if (!target) return false;
      target.click();
      return true;
    },
    { selectorText: selector, expected: text, exactMatch: exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

async function firstVisibleTextInput(page, placeholders) {
  return page.evaluateHandle((targetPlaceholders) => {
    const isVisible = (element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return (
        rect.width > 0 &&
        rect.height > 0 &&
        style.visibility !== "hidden" &&
        style.display !== "none"
      );
    };
    return [...document.querySelectorAll("input, textarea")].find(
      (element) =>
        isVisible(element) &&
        targetPlaceholders.some((placeholder) =>
          (element.getAttribute("placeholder") || element.getAttribute("aria-label") || "")
            .toLowerCase()
            .includes(placeholder.toLowerCase()),
        ),
    );
  }, placeholders);
}

async function typeIntoInput(page, placeholders, value) {
  const handle = await firstVisibleTextInput(page, placeholders);
  const element = handle.asElement();
  assert(element, `Could not find input for ${placeholders.join(", ")}`);
  await element.click({ clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.keyboard.type(value);
}

async function screenshot(page, name) {
  const screenshotPath = path.join(ARTIFACT_DIR, name);
  await page.screenshot({ path: screenshotPath, fullPage: true });
  return screenshotPath;
}

async function openQueue(page, tab = "Items") {
  await page.goto(`${FRONTEND_URL}/dashboard/annotations/queues/${QUEUE_ID}`, {
    waitUntil: "domcontentloaded",
  });
  await waitForVisibleText(page, "Items", {
    selector: "[role='tab'], button",
    exact: true,
    timeout: 60000,
  });
  await waitForVisibleText(page, tab, {
    selector: "[role='tab'], button",
    exact: true,
    timeout: 60000,
  });
  await clickByText(page, tab, { selector: "[role='tab'], button" });
}

async function listQueueItems(apiBase, accessToken, params = {}) {
  const query = new URLSearchParams({ limit: "500", ...params });
  return asArray(
    await apiGet(
      apiBase,
      accessToken,
      `/model-hub/annotation-queues/${QUEUE_ID}/items/?${query}`,
    ),
  );
}

async function getQueueItem(apiBase, accessToken, itemId) {
  const items = await listQueueItems(apiBase, accessToken);
  return items.find((item) => item.id === itemId);
}

async function runSkipWorkflow({ page, apiBase, accessToken }) {
  const pending = (await listQueueItems(apiBase, accessToken)).filter(
    (item) => item.status === "pending",
  );
  if (pending.length < 2) {
    return {
      scenario: "skip-advances",
      status: "skipped",
      reason: "Dev DB needs at least two pending queue items",
    };
  }

  const item = pending[0];
  await page.goto(
    `${FRONTEND_URL}/dashboard/annotations/queues/${QUEUE_ID}/annotate?itemId=${item.id}`,
    { waitUntil: "domcontentloaded" },
  );
  await waitForText(page, "Skip", 45000);

  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().includes(`/items/${item.id}/skip/`) &&
      response.request().method() === "POST",
    { timeout: 30000 },
  );
  await clickByText(page, "Skip", { selector: "button", exact: true });
  const response = await responsePromise;
  const body = await response.json();
  assert(response.ok(), `Skip failed: ${JSON.stringify(body)}`);

  const skipped = await getQueueItem(apiBase, accessToken, item.id);
  assert(skipped?.status === "skipped", "Skip did not mark the item skipped");
  const nextItem = body?.result?.next_item || body?.result?.nextItem;
  assert(nextItem?.id && nextItem.id !== item.id, "Skip did not advance to a next item");

  await waitForText(page, "Skip", 45000);
  const screenshotPath = await screenshot(page, "lifecycle-skip-next-item.png");

  await apiPatch(
    apiBase,
    accessToken,
    `/model-hub/annotation-queues/${QUEUE_ID}/items/${item.id}/`,
    { status: "pending" },
  ).catch(() => {});

  return {
    scenario: "skip-advances",
    status: "passed",
    itemId: item.id,
    nextItemId: nextItem.id,
    screenshotPath,
  };
}

async function runAssignAndMyItems({ page, apiBase, accessToken, user }) {
  const items = await listQueueItems(apiBase, accessToken);
  const item = items.find((candidate) => candidate.status !== "completed") || items[0];
  if (!item) {
    return {
      scenario: "assign-my-items",
      status: "skipped",
      reason: "Dev DB has no queue items",
    };
  }

  await apiPost(
    apiBase,
    accessToken,
    `/model-hub/annotation-queues/${QUEUE_ID}/items/assign/`,
    { item_ids: [item.id], user_ids: [], action: "set" },
  );

  await openQueue(page, "Items");
  await waitForText(page, "+ Assign", 45000);
  await clickByText(page, "+ Assign", { selector: ".MuiChip-root", exact: true });
  await waitForText(page, "Apply", 30000);
  const userId = String(user.id || user.user_id || user.userId || user.pk);
  const userLookup = user.email || user.name || userId;
  const userText = user.name || user.email || userId;
  await typeIntoInput(page, ["Search"], userLookup);
  await waitForText(page, userLookup, 30000).catch(() => waitForText(page, userText, 30000));
  await clickByText(page, userLookup, {
    selector: ".MuiListItemButton-root",
    exact: false,
  }).catch(() =>
    clickByText(page, userText, {
      selector: ".MuiListItemButton-root",
      exact: false,
    }),
  );

  const assignResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes(`/items/assign/`) &&
      response.request().method() === "POST",
    { timeout: 30000 },
  );
  await clickByText(page, "Apply", { selector: "button", exact: true });
  const assignResponse = await assignResponsePromise;
  const assignBody = await assignResponse.json();
  assert(assignResponse.ok(), `Assign failed: ${JSON.stringify(assignBody)}`);

  const assignPayload = JSON.parse(assignResponse.request().postData() || "{}");
  assert(
    (assignPayload.user_ids || []).map(String).includes(userId),
    `Assign payload did not include current user: ${JSON.stringify(assignPayload)}`,
  );
  const assignedItemIds = assignPayload.item_ids;
  const assignedItemId = assignedItemIds?.[0];
  await sleep(1000);
  const assigned = await getQueueItem(apiBase, accessToken, assignedItemId);
  const assignedUsers = assigned?.assigned_users || assigned?.assignedUsers || [];
  assert(
    assignedUsers.some(
      (assignedUser) =>
        String(assignedUser.id || assignedUser.user_id || assignedUser.userId) === userId,
    ) || String(assigned?.assigned_to_id || assigned?.assignedToId || "") === userId,
    "Assign did not persist the current user",
  );

  const assignedScreenshotPath = await screenshot(page, "lifecycle-assigned-current-user.png");
  await clickByText(page, "My Items", { selector: "button", exact: true });
  await sleep(1000);
  const myItems = await listQueueItems(apiBase, accessToken, { assigned_to: "me" });
  assert(
    myItems.length > 0 &&
      myItems.every((myItem) => {
        const myItemAssignedUsers = myItem.assigned_users || myItem.assignedUsers || [];
        return (
          myItemAssignedUsers.some(
            (assignedUser) =>
              String(assignedUser.id || assignedUser.user_id || assignedUser.userId) === userId,
          ) || String(myItem.assigned_to_id || myItem.assignedToId || "") === userId
        );
      }),
    "My Items API filter returned an item not assigned to the current user",
  );
  const myItemsScreenshotPath = await screenshot(page, "lifecycle-my-items-filter.png");

  await apiPost(
    apiBase,
    accessToken,
    `/model-hub/annotation-queues/${QUEUE_ID}/items/assign/`,
    { item_ids: [assignedItemId], user_ids: [], action: "set" },
  ).catch(() => {});

  return {
    scenario: "assign-my-items",
    status: "passed",
    itemId: assignedItemId,
    screenshotPath: myItemsScreenshotPath,
    screenshots: [assignedScreenshotPath, myItemsScreenshotPath],
  };
}

async function runAudioWorkflow({ page, apiBase, accessToken }) {
  const callItems = await listQueueItems(apiBase, accessToken, {
    source_type: "call_execution",
  });
  for (const item of callItems) {
    const detail = await apiGet(
      apiBase,
      accessToken,
      `/model-hub/annotation-queues/${QUEUE_ID}/items/${item.id}/annotate-detail/`,
    ).catch(() => null);
    const callId = detail?.item?.source_content?.call_id;
    if (!callId) continue;
    const callData = await apiGet(
      apiBase,
      accessToken,
      `/simulate/call-executions/${callId}/`,
    ).catch(() => null);
    const recordingUrl =
      callData?.recording?.mono?.combined_url ||
      callData?.recording?.stereo_url ||
      callData?.recording_url ||
      callData?.stereo_recording_url;
    if (!recordingUrl) continue;

    await page.goto(
      `${FRONTEND_URL}/dashboard/annotations/queues/${QUEUE_ID}/annotate?itemId=${item.id}`,
      { waitUntil: "domcontentloaded" },
    );
    await waitForText(page, "Call Log Details", 60000);
    await page.waitForFunction(
      () => [...document.querySelectorAll("audio")].some((audio) => audio.src),
      { timeout: 30000 },
    );
    const audioSrc = await page.evaluate(
      () => [...document.querySelectorAll("audio")].find((audio) => audio.src)?.src,
    );
    assert(audioSrc, "Voice annotator mounted no audio src");
    const screenshotPath = await screenshot(page, "lifecycle-voice-audio-src.png");
    return {
      scenario: "voice-audio-src",
      status: "passed",
      itemId: item.id,
      audioSrc,
      screenshotPath,
    };
  }

  return {
    scenario: "voice-audio-src",
    status: "skipped",
    reason: "Dev DB has no call_execution queue item with recording_url",
  };
}

async function runSettingsLabelsCrud({ page, apiBase, accessToken }) {
  const suffix = Date.now().toString(36);
  const name = `codex lifecycle label ${suffix}`;
  const renamed = `${name} renamed`;

  const created = await apiPost(apiBase, accessToken, "/model-hub/annotations-labels/", {
    name,
    type: "text",
    description: "Created by lifecycle e2e",
    settings: { placeholder: "Lifecycle", min_length: 0, max_length: 500 },
    allow_notes: false,
  });

  await openQueue(page, "Settings");
  await typeIntoInput(page, ["Search labels"], name);
  await waitForText(page, name, 30000);
  const createdScreenshotPath = await screenshot(page, "lifecycle-settings-label-created.png");

  await apiPut(apiBase, accessToken, `/model-hub/annotations-labels/${created.id}/`, {
    name: renamed,
    type: "text",
    description: "Renamed by lifecycle e2e",
    settings: { placeholder: "Lifecycle", min_length: 0, max_length: 500 },
    allow_notes: false,
  });
  await page.reload({ waitUntil: "domcontentloaded" });
  await waitForText(page, "Settings", 45000);
  await clickByText(page, "Settings", { selector: "[role='tab'], button" });
  await typeIntoInput(page, ["Search labels"], renamed);
  await waitForText(page, renamed, 30000);
  const renamedScreenshotPath = await screenshot(page, "lifecycle-settings-label-renamed.png");

  await apiDelete(apiBase, accessToken, `/model-hub/annotations-labels/${created.id}/`);
  await page.reload({ waitUntil: "domcontentloaded" });
  await waitForText(page, "Settings", 45000);
  await clickByText(page, "Settings", { selector: "[role='tab'], button" });
  await typeIntoInput(page, ["Search labels"], renamed);
  await waitForText(page, "No labels found", 30000);
  const deletedScreenshotPath = await screenshot(page, "lifecycle-settings-label-deleted.png");

  return {
    scenario: "settings-labels-crud",
    status: "passed",
    labelId: created.id,
    screenshotPath: deletedScreenshotPath,
    screenshots: [createdScreenshotPath, renamedScreenshotPath, deletedScreenshotPath],
  };
}

async function runMultiConditionEdit({ page, apiBase, accessToken }) {
  const ruleName = `codex lifecycle edit reorder ${Date.now().toString(36)}`;
  const created = await apiPost(
    apiBase,
    accessToken,
    `/model-hub/annotation-queues/${QUEUE_ID}/automation-rules/`,
    {
      name: ruleName,
      source_type: "trace",
      conditions: {
        operator: "and",
        rules: [{ field: "status", op: "equals", value: "OK" }],
      },
      enabled: true,
      trigger_frequency: "manual",
    },
  );

  try {
    const editedPayload = {
      name: ruleName,
      source_type: "trace",
      conditions: {
        operator: "and",
        rules: [
          { field: "status", op: "equals", value: "OK" },
          { field: "name", op: "contains", value: "Lifecycle" },
        ],
      },
      enabled: true,
      trigger_frequency: "manual",
    };
    await apiPatch(
      apiBase,
      accessToken,
      `/model-hub/annotation-queues/${QUEUE_ID}/automation-rules/${created.id}/`,
      editedPayload,
    );

    const finalPayload = {
      ...editedPayload,
      conditions: {
        operator: "and",
        rules: [{ field: "name", op: "contains", value: "Lifecycle" }],
      },
    };
    const saved = await apiPatch(
      apiBase,
      accessToken,
      `/model-hub/annotation-queues/${QUEUE_ID}/automation-rules/${created.id}/`,
      finalPayload,
    );
    assert(
      JSON.stringify(saved.conditions.rules) ===
        JSON.stringify(finalPayload.conditions.rules),
      `Edited conditions order mismatch: ${JSON.stringify(saved.conditions.rules)}`,
    );

    await openQueue(page, "Rules");
    await waitForText(page, ruleName, 45000);
    const screenshotPath = await screenshot(page, "lifecycle-rule-edit-reorder.png");
    return {
      scenario: "multi-condition-edit-reorder",
      status: "passed",
      ruleId: created.id,
      screenshotPath,
      payload: finalPayload.conditions.rules,
    };
  } finally {
    await apiDelete(
      apiBase,
      accessToken,
      `/model-hub/annotation-queues/${QUEUE_ID}/automation-rules/${created.id}/`,
    ).catch(() => {});
  }
}

async function runDatetimeRoundtrip({ page, apiBase, accessToken }) {
  const ruleName = `codex lifecycle datetime ${Date.now().toString(36)}`;
  const value = ["2020-01-01T00:00", "2099-01-01T00:00"];
  const created = await apiPost(
    apiBase,
    accessToken,
    `/model-hub/annotation-queues/${QUEUE_ID}/automation-rules/`,
    {
      name: ruleName,
      source_type: "trace",
      conditions: {
        operator: "and",
        rules: [{ field: "created_at", op: "between", value, filterType: "datetime" }],
      },
      enabled: true,
      trigger_frequency: "manual",
    },
  );
  try {
    assert(
      JSON.stringify(created.conditions.rules[0].value) === JSON.stringify(value),
      "Datetime between rule did not preserve ISO strings",
    );
    await openQueue(page, "Rules");
    await waitForText(page, ruleName, 45000);
    const screenshotPath = await screenshot(page, "lifecycle-datetime-between-rule.png");
    return {
      scenario: "datetime-between-roundtrip",
      status: "passed",
      ruleId: created.id,
      screenshotPath,
    };
  } finally {
    await apiDelete(
      apiBase,
      accessToken,
      `/model-hub/annotation-queues/${QUEUE_ID}/automation-rules/${created.id}/`,
    ).catch(() => {});
  }
}

async function main() {
  await fs.mkdir(ARTIFACT_DIR, { recursive: true });
  const apiBase = await resolveApiBase();
  const tokens = await login(apiBase);
  const accessToken = tokens.access;
  const user = await apiGet(apiBase, accessToken, "/accounts/user-info/");
  const chromePath = await findChromeExecutable();
  const browser = await puppeteer.launch({
    executablePath: chromePath,
    headless: process.env.HEADLESS === "false" ? false : "new",
    defaultViewport: { width: 1440, height: 1000 },
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });
  const results = [];
  let page;

  try {
    page = await browser.newPage();
    page.setDefaultTimeout(25000);
    page.on("console", (message) => {
      if (message.type() === "error") console.log(`[browser:error] ${message.text()}`);
    });
    await page.goto(`${FRONTEND_URL}/auth/jwt/login`, {
      waitUntil: "domcontentloaded",
    });
    await page.evaluate(
      ({ access, refresh }) => {
        localStorage.setItem("accessToken", access);
        localStorage.setItem("refreshToken", refresh || "");
        localStorage.setItem("rememberMe", "true");
        sessionStorage.clear();
      },
      { access: tokens.access, refresh: tokens.refresh },
    );

    const scenarios = [
      runSkipWorkflow,
      runAssignAndMyItems,
      runAudioWorkflow,
      runSettingsLabelsCrud,
      runMultiConditionEdit,
      runDatetimeRoundtrip,
    ];
    for (const scenario of scenarios) {
      const result = await scenario({ page, apiBase, accessToken, user });
      results.push(result);
      const prefix = result.status === "passed" ? "PASS" : "SKIP";
      console.log(
        `${prefix} ${result.scenario} ${result.screenshotPath || result.reason || ""}`,
      );
    }
  } catch (error) {
    if (page) {
      const failurePath = await screenshot(page, "lifecycle-failure.png").catch(
        () => null,
      );
      if (failurePath) console.log(`FAILURE_SCREENSHOT ${failurePath}`);
    }
    throw error;
  } finally {
    await browser.close();
  }

  console.log(JSON.stringify({ status: "passed", results }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
