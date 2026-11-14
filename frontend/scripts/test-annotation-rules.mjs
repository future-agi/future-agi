import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";
import { apiPath } from "../src/api/contracts/api-surface.js";

const FRONTEND_URL = process.env.FRONTEND_URL || "http://localhost:3032";
const QUEUE_ID = process.env.ANNOTATION_QUEUE_ID;
const EMAIL = process.env.FUTURE_AGI_EMAIL;
const PASSWORD = process.env.FUTURE_AGI_PASSWORD;
const KEEP_RULES = process.env.KEEP_RULES === "1";

const _missing = [
  ["ANNOTATION_QUEUE_ID", QUEUE_ID],
  ["FUTURE_AGI_EMAIL", EMAIL],
  ["FUTURE_AGI_PASSWORD", PASSWORD],
].filter(([, v]) => !v);
if (_missing.length) {
  console.error(
    `Missing required env vars: ${_missing.map(([k]) => k).join(", ")}\n` +
      `Example:\n  ANNOTATION_QUEUE_ID=<uuid> FUTURE_AGI_EMAIL=you@x FUTURE_AGI_PASSWORD=*** node scripts/test-annotation-rules.mjs`,
  );
  process.exit(2);
}

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ARTIFACT_DIR = path.join(__dirname, ".artifacts");

const queueApiPath = (template, params = {}) =>
  apiPath(template, { queue_id: QUEUE_ID, ...params });

const withQuery = (pathName, query) => `${pathName}?${query}`;

const SOURCE_SCENARIOS = [
  {
    key: "dataset",
    sourceLabel: "Dataset Row",
    scheduleLabel: "Manually",
    scheduleValue: "manual",
    conditionsScreenshotName: "dataset-user-message-dd-conditions-chip.png",
  },
  {
    key: "trace",
    sourceLabel: "Trace",
    scheduleLabel: "Every hour",
    scheduleValue: "hourly",
    projectRequired: true,
    filter: { propertyName: "Status", value: "ERROR", kind: "choice" },
  },
  {
    key: "trace-free-text-model",
    sourceLabel: "Trace",
    scheduleLabel: "Every hour",
    scheduleValue: "hourly",
    projectRequired: true,
    filter: {
      propertyName: "Model",
      propertyId: "model",
      value: "gpt-4",
      kind: "free-text",
      chipScreenshotName: "trace-model-free-text-chip.png",
    },
    expectedPayload: { columnId: "model", value: "gpt-4" },
  },
  {
    key: "trace-status-apply-no-enter",
    sourceLabel: "Trace",
    scheduleLabel: "Every hour",
    scheduleValue: "hourly",
    projectRequired: true,
    filter: {
      propertyName: "Status",
      propertyId: "status",
      value: "OK",
      kind: "free-text-no-enter",
    },
    expectedPayload: { columnId: "status", value: "OK" },
    conditionsScreenshotName: "trace-status-ok-conditions-chip.png",
  },
  {
    key: "trace-status-custom-value",
    sourceLabel: "Trace",
    scheduleLabel: "Every hour",
    scheduleValue: "hourly",
    projectRequired: true,
    filter: {
      propertyName: "Status",
      propertyId: "status",
      value: "gpt-4",
      kind: "custom-value",
      customValueScreenshotName: "trace-status-custom-value-added.png",
    },
    expectedPayload: { columnId: "status", op: "in", values: ["gpt-4"] },
    conditionsScreenshotName: "trace-status-custom-value-chip.png",
    payloadScreenshotName: "trace-status-custom-value-payload.png",
  },
  {
    key: "trace-status-multi-checkbox",
    sourceLabel: "Trace",
    scheduleLabel: "Every hour",
    scheduleValue: "hourly",
    projectRequired: true,
    filter: {
      propertyName: "Status",
      propertyId: "status",
      value: ["OK", "ERROR"],
      kind: "multi-choice",
      selectedScreenshotName: "trace-status-checkboxes-checked.png",
    },
    expectedPayload: { columnId: "status", op: "in", values: ["OK", "ERROR"] },
    conditionsScreenshotName: "trace-status-multi-checkbox-chip.png",
    payloadScreenshotName: "trace-status-multi-checkbox-payload.png",
  },
  {
    key: "simulation-created-at-between",
    sourceLabel: "Simulation",
    scheduleLabel: "Every hour",
    scheduleValue: "hourly",
    agentDefinitionRequired: true,
    filter: {
      propertyName: "Created At",
      propertyId: "created_at",
      value: ["2020-01-01T00:00", "2099-01-01T00:00"],
      kind: "datetime-between",
    },
    expectedPayload: { columnId: "created_at", value: "2020-01-01T00:00" },
  },
  {
    key: "span",
    sourceLabel: "Span",
    scheduleLabel: "Daily",
    scheduleValue: "daily",
    projectRequired: true,
    filter: { propertyName: "Status", value: "ERROR", kind: "choice" },
  },
  {
    key: "span-status-apply-no-enter",
    sourceLabel: "Span",
    scheduleLabel: "Daily",
    scheduleValue: "daily",
    projectRequired: true,
    filter: {
      propertyName: "Status",
      propertyId: "status",
      value: "OK",
      kind: "free-text-no-enter",
    },
    expectedPayload: { columnId: "status", value: "OK" },
  },
  {
    key: "session",
    sourceLabel: "Session",
    scheduleLabel: "Weekly",
    scheduleValue: "weekly",
    projectRequired: true,
    filter: { propertyName: "Duration", value: "0", kind: "number" },
  },
  {
    key: "session-first-message-apply-no-enter",
    sourceLabel: "Session",
    scheduleLabel: "Weekly",
    scheduleValue: "weekly",
    projectRequired: true,
    filter: {
      propertyName: "First Message",
      propertyId: "first_message",
      value: "OK",
      kind: "free-text-no-enter",
    },
    expectedPayload: { columnId: "first_message", value: "OK" },
  },
  {
    key: "simulation",
    sourceLabel: "Simulation",
    scheduleLabel: "Monthly",
    scheduleValue: "monthly",
    agentDefinitionRequired: true,
    filter: { propertyName: "Status", value: "completed", kind: "choice" },
  },
];

const TEST_RULE_PREFIXES = ["codex e2e ", "qa-", "qa-rule-"];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function datasetDisplayName(dataset) {
  return (
    dataset?.name ||
    dataset?.dataset_name ||
    dataset?.datasetName ||
    dataset?.label ||
    ""
  );
}

function agentDefinitionDisplayName(agent) {
  return agent?.agent_name || agent?.name || agent?.label || "";
}

function columnDisplayName(column) {
  return column?.name || column?.column_name || column?.id || "";
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function normalizeText(text) {
  return String(text || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();
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
      // Continue through candidates.
    }
  }
  throw new Error(
    "Could not find Chrome. Set CHROME_BIN to a Chromium/Chrome executable.",
  );
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

function unwrapApiData(data) {
  return data?.result ?? data?.results ?? data;
}

function authHeaders(accessToken) {
  return {
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  };
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

async function apiDelete(apiBase, accessToken, pathName) {
  const response = await fetch(`${apiBase}${pathName}`, {
    method: "DELETE",
    headers: authHeaders(accessToken),
  });
  if (!response.ok && response.status !== 404) {
    const text = await response.text();
    throw new Error(`HTTP ${response.status} for DELETE ${pathName}: ${text}`);
  }
}

async function cleanupExistingTestRules(apiBase, accessToken) {
  const rules = await apiGet(
    apiBase,
    accessToken,
    queueApiPath("/model-hub/annotation-queues/{queue_id}/automation-rules/"),
  );
  if (!Array.isArray(rules)) return;

  for (const rule of rules) {
    const name = rule?.name || "";
    if (!TEST_RULE_PREFIXES.some((prefix) => name.startsWith(prefix))) {
      continue;
    }
    await apiDelete(
      apiBase,
      accessToken,
      queueApiPath("/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/", {
        id: rule.id,
      }),
    );
    console.log(`CLEANUP existing test rule ${rule.id} ${name}`);
  }
}

async function cleanupCreatedRule(
  apiBase,
  accessToken,
  createdRuleIds,
  ruleId,
) {
  if (KEEP_RULES || !ruleId) return;
  await apiDelete(
    apiBase,
    accessToken,
    queueApiPath("/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/", {
      id: ruleId,
    }),
  );
  const index = createdRuleIds.indexOf(ruleId);
  if (index >= 0) createdRuleIds.splice(index, 1);
  console.log(`CLEANUP created test rule ${ruleId}`);
}

async function cleanupCreatedQueueItems(apiBase, accessToken, items = []) {
  if (KEEP_RULES || !items.length) return;
  const itemIds = [...new Set(items.map((item) => item?.id).filter(Boolean))];
  const chunkSize = 100;
  for (let index = 0; index < itemIds.length; index += chunkSize) {
    const chunk = itemIds.slice(index, index + chunkSize);
    if (!chunk.length) continue;
    await apiPost(
      apiBase,
      accessToken,
      queueApiPath("/model-hub/annotation-queues/{queue_id}/items/bulk-remove/"),
      { item_ids: chunk },
    );
  }
  console.log(`CLEANUP created queue items ${itemIds.length}`);
}

function forgetQueueItems(pendingItems, cleanedItems = []) {
  if (!cleanedItems.length) return;
  const cleanedIds = new Set(cleanedItems.map((item) => item?.id));
  for (let index = pendingItems.length - 1; index >= 0; index -= 1) {
    if (cleanedIds.has(pendingItems[index]?.id)) pendingItems.splice(index, 1);
  }
}

async function login(apiBase) {
  if (process.env.FUTURE_AGI_ACCESS_TOKEN) {
    return {
      access: process.env.FUTURE_AGI_ACCESS_TOKEN,
      refresh: process.env.FUTURE_AGI_REFRESH_TOKEN || "",
    };
  }
  const payload = {
    email: EMAIL,
    password: PASSWORD,
    remember_me: true,
    "recaptcha-response": "puppeteer-local-test",
  };
  const tokenResponse = await fetchJson(`${apiBase}/accounts/token/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  assert(
    tokenResponse.access,
    "Login response did not include an access token",
  );
  return tokenResponse;
}

async function getProject(apiBase, accessToken, queue) {
  const queueProjectId =
    typeof queue?.project === "object" ? queue.project?.id : queue?.project;
  const projectResponse = await apiGet(
    apiBase,
    accessToken,
    withQuery(apiPath("/tracer/project/list_project_ids/"), "project_type=observe"),
  );
  const projects = projectResponse?.projects || [];
  const project =
    projects.find((item) => item.id === queueProjectId) ||
    projects.find((item) => item.name === "fi-convention-test") ||
    projects[0];
  assert(project?.id, "No observe project was available for rule testing");
  return project;
}

async function getAgentDefinition(apiBase, accessToken, queue) {
  const queueAgentId =
    typeof queue?.agent_definition === "object"
      ? queue.agent_definition?.id
      : queue?.agent_definition;
  const response = await apiGet(
    apiBase,
    accessToken,
    "/simulate/agent-definitions/?limit=100",
  );
  const agents = asArray(response);
  const agent =
    agents.find((item) => item.id === queueAgentId) ||
    agents.find((item) => agentDefinitionDisplayName(item)) ||
    agents[0];
  assert(agent?.id, "No agent definition was available for simulation rules");
  return {
    ...agent,
    name: agentDefinitionDisplayName(agent) || agent.id,
  };
}

async function getDatasetWithColumn(apiBase, accessToken) {
  const datasetResponse = await apiGet(
    apiBase,
    accessToken,
    "/model-hub/develops/get-datasets-names/",
  );
  const datasets = datasetResponse?.datasets || [];
  assert(datasets.length > 0, "No datasets were available for rule testing");

  const allowedTypes = new Set([
    "text",
    "integer",
    "float",
    "boolean",
    "datetime",
    "array",
  ]);

  const rankedDatasets = [...datasets].sort((a, b) => {
    const score = (dataset) => {
      const name = datasetDisplayName(dataset).toLowerCase();
      if (name === "conversation dataset") return 2;
      if (name.includes("conversation")) return 1;
      return 0;
    };
    return score(b) - score(a);
  });

  for (const dataset of rankedDatasets) {
    const datasetId = dataset.dataset_id || dataset.datasetId || dataset.id;
    if (!datasetId) continue;
    const params = new URLSearchParams({
      current_page_index: "0",
      filters: "[]",
      sort: "[]",
      page_size: "30",
    });
    const detail = await apiGet(
      apiBase,
      accessToken,
      `/model-hub/develops/${datasetId}/get-dataset-table/?${params}`,
    );
    const columnConfig = detail?.columnConfig || detail?.column_config || [];
    const preferred =
      columnConfig.find((column) => {
        const name = columnDisplayName(column).toLowerCase();
        return name === "user_message" || name === "user message";
      }) ||
      columnConfig.find((column) => column.data_type === "text") ||
      columnConfig.find((column) => allowedTypes.has(column.data_type));
    if (preferred) {
      return {
        dataset: {
          ...dataset,
          name: datasetDisplayName(dataset) || datasetId,
        },
        datasetId,
        column: {
          ...preferred,
          name: columnDisplayName(preferred) || preferred.id,
        },
      };
    }
  }
  throw new Error("No dataset with a filterable column was available");
}

async function visibleText(page) {
  return page.evaluate(() => document.body.innerText);
}

function asArray(value) {
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.results)) return value.results;
  if (Array.isArray(value?.items)) return value.items;
  return [];
}

async function waitForText(page, text, timeout = 15000) {
  await page.waitForFunction(
    (needle) => {
      const normalize = (value) =>
        String(value || "")
          .replace(/[\u200B-\u200D\uFEFF]/g, "")
          .replace(/\s+/g, " ")
          .trim();
      return normalize(document.body.innerText).includes(normalize(needle));
    },
    { timeout },
    text,
  );
}

async function waitForAnyText(page, texts, timeout = 15000) {
  await page.waitForFunction(
    (needles) => {
      const normalize = (value) =>
        String(value || "")
          .replace(/[\u200B-\u200D\uFEFF]/g, "")
          .replace(/\s+/g, " ")
          .trim();
      const body = normalize(document.body.innerText);
      return needles.some((needle) => body.includes(normalize(needle)));
    },
    { timeout },
    texts,
  );
}

async function waitForNoDialog(page) {
  await page.waitForFunction(() => !document.querySelector('[role="dialog"]'), {
    timeout: 15000,
  });
}

async function clickVisibleSelector(page, selector) {
  await page.waitForSelector(selector, { timeout: 15000 });
  const marker = `data-e2e-click-${Date.now()}`;
  const marked = await page.evaluate(
    ({ targetSelector, marker: markerAttribute }) => {
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
      const element = [...document.querySelectorAll(targetSelector)].find(
        isVisible,
      );
      if (!element) return false;
      element.setAttribute(markerAttribute, "true");
      return true;
    },
    { targetSelector: selector, marker },
  );
  assert(marked, `Could not click visible selector: ${selector}`);
  await page.click(`[${marker}="true"]`);
}

async function clickByText(
  page,
  text,
  {
    selector = "button, [role='tab'], [role='option'], li, div, span, p",
    exact = true,
  } = {},
) {
  const targetText = normalizeText(text);
  await page.waitForFunction(
    ({ targetText: expected, selector: targetSelector, exact: exactMatch }) => {
      const normalize = (value) =>
        String(value || "")
          .replace(/[\u200B-\u200D\uFEFF]/g, "")
          .replace(/\s+/g, " ")
          .trim();
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
      return [...document.querySelectorAll(targetSelector)].some((element) => {
        if (!isVisible(element)) return false;
        const textValue = normalize(element.innerText || element.textContent);
        return exactMatch
          ? textValue === expected
          : textValue.includes(expected);
      });
    },
    { timeout: 15000 },
    { targetText, selector, exact },
  );

  const clicked = await page.evaluate(
    ({ targetText: expected, selector: targetSelector, exact: exactMatch }) => {
      const normalize = (value) =>
        String(value || "")
          .replace(/[\u200B-\u200D\uFEFF]/g, "")
          .replace(/\s+/g, " ")
          .trim();
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
      const candidates = [...document.querySelectorAll(targetSelector)].filter(
        (element) => {
          if (!isVisible(element)) return false;
          const textValue = normalize(element.innerText || element.textContent);
          return exactMatch
            ? textValue === expected
            : textValue.includes(expected);
        },
      );
      candidates.sort((a, b) => {
        const aRect = a.getBoundingClientRect();
        const bRect = b.getBoundingClientRect();
        return aRect.width * aRect.height - bRect.width * bRect.height;
      });
      const element = candidates[0];
      if (!element) return false;
      element.click();
      return true;
    },
    { targetText, selector, exact },
  );
  assert(clicked, `Could not click text: ${text}`);
}

async function clickVisibleButtonText(page, text, { exact = true } = {}) {
  const targetText = normalizeText(text);
  const clicked = await page.evaluate(
    ({ targetText: expected, exact: exactMatch }) => {
      const normalize = (value) =>
        String(value || "")
          .replace(/[\u200B-\u200D\uFEFF]/g, "")
          .replace(/\s+/g, " ")
          .trim();
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
      const candidates = [...document.querySelectorAll("button")].filter(
        (element) => {
          if (!isVisible(element)) return false;
          const textValue = normalize(element.innerText || element.textContent);
          return exactMatch
            ? textValue === expected
            : textValue.includes(expected);
        },
      );
      candidates.sort((a, b) => {
        const aRect = a.getBoundingClientRect();
        const bRect = b.getBoundingClientRect();
        return bRect.top - aRect.top || bRect.left - aRect.left;
      });
      const element = candidates[0];
      if (!element) return false;
      element.click();
      return true;
    },
    { targetText, exact },
  );
  assert(clicked, `Could not click visible button text: ${text}`);
}

async function selectMuiOption(page, testId, optionText) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await clickVisibleSelector(page, `[data-testid="${testId}"]`);
    await clickByText(page, optionText, {
      selector: "[role='option'], li, .MuiMenuItem-root",
      exact: true,
    });
    await sleep(300);
    const selectedText = await page.$eval(
      `[data-testid="${testId}"]`,
      (element) => element.innerText || element.textContent || "",
    );
    if (normalizeText(selectedText).includes(optionText)) {
      return;
    }
  }
  const selectedText = await page.$eval(
    `[data-testid="${testId}"]`,
    (element) => element.innerText || element.textContent || "",
  );
  throw new Error(
    `Select ${testId} did not choose "${optionText}". Current value: ${selectedText}`,
  );
}

async function markInputByLabel(page, labelText, marker) {
  await page.waitForFunction(
    (label) =>
      [...document.querySelectorAll("label")].some((element) =>
        element.textContent.includes(label),
      ),
    { timeout: 15000 },
    labelText,
  );
  const marked = await page.evaluate(
    ({ labelText: label, marker: attribute }) => {
      const labels = [...document.querySelectorAll("label")];
      const labelElement = labels.find((element) =>
        element.textContent.includes(label),
      );
      if (!labelElement) return false;
      const inputId = labelElement.getAttribute("for");
      const input = inputId
        ? document.getElementById(inputId)
        : labelElement.closest(".MuiFormControl-root")?.querySelector("input");
      if (!input) return false;
      input.setAttribute(attribute, "true");
      return true;
    },
    { labelText, marker },
  );
  assert(marked, `Could not find input for label: ${labelText}`);
}

async function chooseAutocompleteByLabel(page, labelText, optionText) {
  const marker = `data-e2e-${labelText.toLowerCase().replace(/\W+/g, "-")}`;
  const optionSelector =
    "[role='listbox'] [role='option'], .MuiAutocomplete-popper [role='option']";
  await markInputByLabel(page, labelText, marker);

  for (let attempt = 0; attempt < 4; attempt += 1) {
    await page.click(`[${marker}="true"]`, { clickCount: 3 });
    await page.keyboard.press("Backspace");
    await page.keyboard.type(optionText);

    const hasOption = await page
      .waitForFunction(
        (selector) => {
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
          return [...document.querySelectorAll(selector)].some(isVisible);
        },
        { timeout: 5000 },
        optionSelector,
      )
      .then(() => true)
      .catch(() => false);

    if (!hasOption) {
      await sleep(750);
      continue;
    }

    const optionMarker = `data-e2e-option-${Date.now()}`;
    const selected = await page.evaluate(
      ({ expectedText, selector, marker: optionAttribute }) => {
        const normalize = (value) =>
          String(value || "")
            .replace(/\s+/g, " ")
            .trim();
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
        const options = [...document.querySelectorAll(selector)].filter(
          isVisible,
        );
        const option =
          options.find((element) =>
            normalize(element.innerText || element.textContent).includes(
              expectedText,
            ),
          ) || options[0];
        if (!option) return "";
        const text = normalize(option.innerText || option.textContent);
        option.setAttribute(optionAttribute, "true");
        return text;
      },
      {
        expectedText: optionText,
        selector: optionSelector,
        marker: optionMarker,
      },
    );
    if (!selected) {
      await sleep(750);
      continue;
    }

    await page.click(`[${optionMarker}="true"]`);
    await sleep(500);

    const inputValue = await page.$eval(
      `[${marker}="true"]`,
      (element) => element.value || "",
    );
    if (inputValue) return;
  }

  throw new Error(
    `Autocomplete option was not available for ${labelText}: ${optionText}`,
  );
}

async function markFirstVisibleInput(page, placeholders, marker) {
  const marked = await page.evaluate(
    ({ placeholders: targetPlaceholders, marker: attribute }) => {
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
      const input = [...document.querySelectorAll("input, textarea")].find(
        (element) =>
          isVisible(element) &&
          targetPlaceholders.some((placeholder) =>
            (element.getAttribute("placeholder") || "").includes(placeholder),
          ),
      );
      if (!input) return false;
      input.setAttribute(attribute, "true");
      return true;
    },
    { placeholders, marker },
  );
  return marked;
}

async function typeIntoVisibleInput(page, placeholders, value) {
  const marker = `data-e2e-input-${Date.now()}`;
  await page.waitForFunction(
    (targetPlaceholders) => {
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
      return [...document.querySelectorAll("input, textarea")].some(
        (element) =>
          isVisible(element) &&
          targetPlaceholders.some((placeholder) =>
            (element.getAttribute("placeholder") || "").includes(placeholder),
          ),
      );
    },
    { timeout: 15000 },
    placeholders,
  );
  const marked = await markFirstVisibleInput(page, placeholders, marker);
  assert(marked, `Could not find input with placeholders: ${placeholders}`);
  await page.click(`[${marker}="true"]`, { clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.keyboard.type(String(value));
}

async function trySetFreeSoloPickerValue(
  page,
  value,
  { pressEnter = true } = {},
) {
  const marker = `data-e2e-free-solo-${Date.now()}`;
  const marked = await markFirstVisibleInput(
    page,
    [
      "Type a value",
      "Select or type value",
      "Select or type values",
      "Type or pick",
      "Type or pick value",
    ],
    marker,
  );
  if (!marked) return false;
  await page.click(`[${marker}="true"]`, { clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.keyboard.type(String(value));
  if (pressEnter) {
    await page.keyboard.press("Enter");
  }
  await page.waitForFunction(
    (inputSelector, expectedValue) => {
      const input = document.querySelector(inputSelector);
      return (
        input?.value === expectedValue ||
        [...document.querySelectorAll(".MuiChip-label")].some(
          (element) => element.textContent?.trim() === expectedValue,
        )
      );
    },
    { timeout: 5000 },
    `[${marker}="true"]`,
    String(value),
  );
  return true;
}

async function setFreeSoloPickerValueWithoutEnter(page, value) {
  const added = await trySetFreeSoloPickerValue(page, value, {
    pressEnter: false,
  });
  assert(added, "Could not find a free-text value picker input");
}

async function setFreeSoloPickerValue(page, value) {
  const added = await trySetFreeSoloPickerValue(page, value);
  assert(added, "Could not find a free-text value picker input");
}

async function waitForValueChip(page, field, value) {
  const expectedValues = (Array.isArray(value) ? value : [value]).map(String);
  await page.waitForFunction(
    ({ expectedField, expectedValues: values }) =>
      [...document.querySelectorAll(".MuiChip-root")].some((chip) => {
        const text = chip.textContent || "";
        return (
          text.includes(expectedField) &&
          text.includes("is") &&
          values.every((expectedValue) => text.includes(expectedValue))
        );
      }),
    { timeout: 10000 },
    { expectedField: field, expectedValues },
  );
}

async function setCustomPickerValue(page, value, screenshotName) {
  const marker = `data-e2e-custom-picker-${Date.now()}`;
  const openedInputPicker = await markFirstVisibleInput(
    page,
    ["Select values", "Value"],
    marker,
  );
  if (openedInputPicker) {
    await page.click(`[${marker}="true"]`, { clickCount: 3 });
    await page.keyboard.press("Backspace");
  } else {
    await clickByText(page, "Select values...", {
      selector: "div, span, p",
      exact: false,
    });
  }
  await typeIntoVisibleInput(
    page,
    ["Search values", "Select values", "Value"],
    value,
  );
  const customValueLabels = [
    `+ Add custom value: ${value}`,
    `+ Specify: ${value}`,
  ];
  try {
    await page.waitForFunction(
      (labels) =>
        labels.some((label) => document.body?.innerText?.includes(label)),
      { timeout: 4000 },
      customValueLabels,
    );
    const customValueLabel = await page.evaluate(
      (labels) =>
        labels.find((label) => document.body?.innerText?.includes(label)),
      customValueLabels,
    );
    await clickByText(page, customValueLabel, {
      selector: "div, span, p, li",
      exact: false,
    });
  } catch {
    await clickByText(page, String(value), {
      selector: "div, span, p, li",
      exact: true,
    });
  }
  if (screenshotName) {
    const screenshotPath = path.join(ARTIFACT_DIR, screenshotName);
    await page.screenshot({ path: screenshotPath, fullPage: true });
    return screenshotPath;
  }
  return undefined;
}

async function setChoiceValue(page, value) {
  if (await trySetFreeSoloPickerValue(page, value)) {
    return;
  }

  await clickByText(page, "Select values...", {
    selector: "div, span, p",
    exact: false,
  });
  await clickByText(page, value, {
    selector: "div, span, p",
    exact: true,
  });
}

async function setMultiChoiceValues(page, values, screenshotName) {
  await clickByText(page, "Select values...", {
    selector: "div, span, p",
    exact: false,
  });
  for (const value of values) {
    await clickByText(page, value, {
      selector: "div, span, p",
      exact: true,
    });
  }
  if (screenshotName) {
    const screenshotPath = path.join(ARTIFACT_DIR, screenshotName);
    await page.screenshot({ path: screenshotPath, fullPage: true });
    return screenshotPath;
  }
  return undefined;
}

async function chooseOperator(page, currentLabel, nextLabel) {
  const marker = `data-e2e-operator-select-${Date.now()}`;
  const marked = await page.evaluate(
    ({ label, marker: markerAttribute }) => {
      const normalize = (value) =>
        String(value || "")
          .replace(/[\u200B-\u200D\uFEFF]/g, "")
          .replace(/\s+/g, " ")
          .trim();
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
      const select = [
        ...document.querySelectorAll(".MuiSelect-select, [role='combobox']"),
      ].find(
        (element) =>
          isVisible(element) && normalize(element.innerText) === label,
      );
      if (!select) return false;
      select.setAttribute(markerAttribute, "true");
      return true;
    },
    { label: currentLabel, marker },
  );
  assert(marked, `Could not find operator select: ${currentLabel}`);
  await page.click(`[${marker}="true"]`);
  await clickByText(page, nextLabel, {
    selector: "li, div, span, [role='option']",
    exact: true,
  });
}

async function setDateTimeRange(page, [start, end]) {
  await page.waitForFunction(
    () => {
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
      return (
        [...document.querySelectorAll('input[type="datetime-local"]')].filter(
          isVisible,
        ).length >= 2
      );
    },
    { timeout: 10000 },
  );
  await page.evaluate(
    ({ startValue, endValue }) => {
      const setNativeInputValue = (input, value) => {
        const setter = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype,
          "value",
        )?.set;
        setter?.call(input, value);
      };
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
      const inputs = [
        ...document.querySelectorAll('input[type="datetime-local"]'),
      ]
        .filter(isVisible)
        .slice(0, 2);
      setNativeInputValue(inputs[0], startValue);
      inputs[0].dispatchEvent(new Event("input", { bubbles: true }));
      inputs[0].dispatchEvent(new Event("change", { bubbles: true }));
      setNativeInputValue(inputs[1], endValue);
      inputs[1].dispatchEvent(new Event("input", { bubbles: true }));
      inputs[1].dispatchEvent(new Event("change", { bubbles: true }));
    },
    { startValue: start, endValue: end },
  );
  await page.waitForFunction(
    ({ startValue, endValue }) => {
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
      const inputs = [
        ...document.querySelectorAll('input[type="datetime-local"]'),
      ]
        .filter(isVisible)
        .slice(0, 2);
      return inputs[0]?.value === startValue && inputs[1]?.value === endValue;
    },
    { timeout: 5000 },
    { startValue: start, endValue: end },
  );
}

async function chooseFilterProperty(page, propertyName, propertyId = null) {
  await clickByText(page, "Property", { selector: "button", exact: false });
  const marker = `data-e2e-property-search-${Date.now()}`;
  const marked = await markFirstVisibleInput(
    page,
    ["Search properties"],
    marker,
  );
  assert(marked, "Could not find property picker search input");
  const searchValue = propertyId || propertyName;
  const categories = [
    ...(propertyId === "created_at" || propertyId === "status"
      ? ["System"]
      : []),
    "All",
    "Dataset",
    "System",
    "Attributes",
    "Annotations",
    "Evals",
  ];
  const uniqueCategories = Array.from(new Set(categories));

  for (const categoryLabel of uniqueCategories) {
    await page.evaluate(
      ({ markerAttribute, category }) => {
        const normalize = (value) =>
          String(value || "")
            .replace(/[\u200B-\u200D\uFEFF]/g, "")
            .replace(/\s+/g, " ")
            .trim();
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
        const input =
          document.querySelector(`[${markerAttribute}="true"]`) ||
          [
            ...document.querySelectorAll(
              'input[placeholder="Search properties..."]',
            ),
          ]
            .filter(isVisible)
            .at(-1);
        const paper = input?.closest(".MuiPaper-root");
        const categoryRow = [...(paper?.querySelectorAll("div") || [])].find(
          (element) =>
            isVisible(element) &&
            normalize(element.innerText).startsWith(`${category} `),
        );
        categoryRow?.click();
      },
      { markerAttribute: marker, category: categoryLabel },
    );

    await sleep(150);
    const activeSearchMarker = `data-e2e-active-property-search-${Date.now()}`;
    const focused = await page.evaluate(
      ({ markerAttribute, activeMarker }) => {
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
        const input =
          document.querySelector(`[${markerAttribute}="true"]`) ||
          [
            ...document.querySelectorAll(
              'input[placeholder="Search properties..."]',
            ),
          ]
            .filter(isVisible)
            .at(-1);
        if (!input) return false;
        document
          .querySelectorAll(`[${activeMarker}]`)
          .forEach((element) => element.removeAttribute(activeMarker));
        input.setAttribute(activeMarker, "true");
        return true;
      },
      { markerAttribute: marker, activeMarker: activeSearchMarker },
    );
    assert(focused, "Could not refocus property picker search input");
    await page.click(`[${activeSearchMarker}="true"]`, { clickCount: 3 });
    await page.keyboard.press("Backspace");
    await page.keyboard.type(searchValue);

    const found = await page
      .waitForFunction(
        ({ expectedName, expectedSearchValue }) => {
          const normalize = (value) =>
            String(value || "")
              .replace(/[\u200B-\u200D\uFEFF]/g, "")
              .replace(/\s+/g, " ")
              .trim();
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
          const input = [
            ...document.querySelectorAll(
              'input[placeholder="Search properties..."]',
            ),
          ]
            .filter(isVisible)
            .at(-1);
          const paper = input?.closest(".MuiPaper-root");
          if (!paper) return false;
          if (
            normalize(input.value).toLowerCase() !==
            normalize(expectedSearchValue).toLowerCase()
          ) {
            return false;
          }
          const flexPane = [...paper.children].find((element) => {
            const style = window.getComputedStyle(element);
            return style.display === "flex";
          });
          const optionPane = flexPane?.lastElementChild;
          const options = [...(optionPane?.children || [])].filter(isVisible);
          const exactOption = options.find((element) => {
            const optionText = normalize(element.innerText);
            return (
              optionText === expectedName ||
              optionText.startsWith(`${expectedName} `)
            );
          });
          if (!exactOption) return false;
          document
            .querySelectorAll("[data-e2e-property-option]")
            .forEach((element) =>
              element.removeAttribute("data-e2e-property-option"),
            );
          exactOption.setAttribute("data-e2e-property-option", "true");
          return true;
        },
        { timeout: 3000 },
        {
          expectedName: propertyName,
          expectedSearchValue: searchValue,
        },
      )
      .then(() => true)
      .catch(() => false);

    if (found) {
      await page.click('[data-e2e-property-option="true"]');
      return;
    }
  }

  throw new Error(`Could not select property ${propertyName}`);
}

async function applyFilter(page, filter) {
  await clickVisibleSelector(
    page,
    '[data-testid="automation-rule-filter-button"]',
  );
  await chooseFilterProperty(page, filter.propertyName, filter.propertyId);

  if (filter.kind === "number") {
    await typeIntoVisibleInput(page, ["Value"], filter.value);
  } else if (filter.kind === "choice") {
    await setChoiceValue(page, filter.value);
  } else if (filter.kind === "free-text") {
    filter.customValueScreenshotPath = await setCustomPickerValue(
      page,
      filter.value,
      filter.customValueScreenshotName,
    );
  } else if (filter.kind === "free-text-no-enter") {
    filter.customValueScreenshotPath = await setCustomPickerValue(
      page,
      filter.value,
      filter.customValueScreenshotName,
    );
  } else if (filter.kind === "custom-value") {
    filter.customValueScreenshotPath = await setCustomPickerValue(
      page,
      filter.value,
      filter.customValueScreenshotName,
    );
  } else if (filter.kind === "multi-choice") {
    filter.selectedScreenshotPath = await setMultiChoiceValues(
      page,
      filter.value,
      filter.selectedScreenshotName,
    );
  } else if (filter.kind === "datetime-between") {
    await chooseOperator(page, "on", "between");
    await setDateTimeRange(page, filter.value);
  } else if (filter.kind === "text") {
    const hasDirectInput = await markFirstVisibleInput(
      page,
      ["Value", "Enter text"],
      "data-e2e-rule-value",
    );
    if (hasDirectInput) {
      await page.click('[data-e2e-rule-value="true"]', { clickCount: 3 });
      await page.keyboard.press("Backspace");
      await page.keyboard.type(String(filter.value));
    } else {
      const didSetFreeSolo = await trySetFreeSoloPickerValue(
        page,
        filter.value,
      );
      if (!didSetFreeSolo) {
        await setCustomPickerValue(page, filter.value);
      }
    }
  } else {
    throw new Error(`Unknown filter kind: ${filter.kind}`);
  }

  if (filter.chipScreenshotName) {
    await page
      .waitForFunction(
        () => {
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
          const poppers = [
            ...document.querySelectorAll(".MuiAutocomplete-popper"),
          ].filter(isVisible);
          return poppers.some(
            (popper) =>
              popper.innerText.includes("No suggestions yet") ||
              !popper.innerText.includes("Loading"),
          );
        },
        { timeout: 10000 },
      )
      .catch(() => {});
    filter.chipScreenshotPath = path.join(
      ARTIFACT_DIR,
      filter.chipScreenshotName,
    );
    await page.screenshot({ path: filter.chipScreenshotPath, fullPage: true });
  }

  await clickVisibleButtonText(page, "Apply", { exact: true });
  await sleep(250);
  if (
    filter.kind === "free-text-no-enter" ||
    filter.kind === "custom-value" ||
    filter.kind === "multi-choice" ||
    filter.verifyChipAfterApply
  ) {
    await waitForValueChip(page, filter.propertyName, filter.value);
  }
}

async function captureConditionsChipScreenshot(page, scenario, filter) {
  if (!scenario.conditionsScreenshotName) return undefined;

  await waitForValueChip(page, filter.propertyName, filter.value);

  const screenshotPath = path.join(
    ARTIFACT_DIR,
    scenario.conditionsScreenshotName,
  );
  await page.screenshot({ path: screenshotPath, fullPage: true });
  return screenshotPath;
}

function readPayloadFilterConfig(payload, columnId) {
  const conditions = payload?.conditions || {};
  const filters = [
    ...(Array.isArray(conditions.filter) ? conditions.filter : []),
    ...(Array.isArray(conditions.filters) ? conditions.filters : []),
  ];
  const matchedFilter = filters.find(
    (filter) => (filter.column_id || filter.columnId) === columnId,
  );
  if (matchedFilter) {
    const config =
      matchedFilter.filter_config || matchedFilter.filterConfig || {};
    return {
      op: config.filter_op || config.filterOp,
      value:
        "filter_value" in config
          ? config.filter_value
          : "filterValue" in config
            ? config.filterValue
            : undefined,
    };
  }

  const matchedRule = (conditions.rules || []).find(
    (rule) => rule.field === columnId,
  );
  return matchedRule
    ? { op: matchedRule.op, value: matchedRule.value }
    : { op: undefined, value: undefined };
}

function assertPayloadFilterValue(payload, expectation) {
  if (!expectation) return;
  const { op: actualOp, value: actualValue } = readPayloadFilterConfig(
    payload,
    expectation.columnId,
  );
  if (expectation.op) {
    assert(
      actualOp === expectation.op,
      `Expected payload filter ${expectation.columnId} op ${
        expectation.op
      }, got ${JSON.stringify(actualOp)}`,
    );
  }
  const actualValues = Array.isArray(actualValue) ? actualValue : [actualValue];
  const expectedValues = expectation.values || [expectation.value];
  assert(
    expectedValues.every((expectedValue) =>
      actualValues.includes(expectedValue),
    ),
    `Expected payload filter ${expectation.columnId} to include "${expectedValues.join(
      ", ",
    )}", got ${JSON.stringify(actualValue)}`,
  );
}

async function capturePayloadScreenshot(page, payload, screenshotName) {
  if (!screenshotName) return undefined;
  const screenshotPath = path.join(ARTIFACT_DIR, screenshotName);
  await page.evaluate((requestPayload) => {
    const existing = document.querySelector("[data-e2e-api-payload]");
    existing?.remove();
    const overlay = document.createElement("div");
    overlay.setAttribute("data-e2e-api-payload", "true");
    overlay.style.position = "fixed";
    overlay.style.inset = "24px";
    overlay.style.zIndex = "99999";
    overlay.style.background = "#111";
    overlay.style.color = "#f8f8f2";
    overlay.style.border = "1px solid #555";
    overlay.style.borderRadius = "8px";
    overlay.style.padding = "16px";
    overlay.style.overflow = "auto";
    overlay.style.font = "12px/1.5 monospace";
    overlay.textContent = JSON.stringify(requestPayload, null, 2);
    document.body.appendChild(overlay);
  }, payload);
  await page.screenshot({ path: screenshotPath, fullPage: true });
  await page.evaluate(() => {
    document.querySelector("[data-e2e-api-payload]")?.remove();
  });
  return screenshotPath;
}

async function openRulesTab(page) {
  await page.goto(`${FRONTEND_URL}/dashboard/annotations/queues/${QUEUE_ID}`, {
    waitUntil: "domcontentloaded",
  });
  await waitForText(page, "Rules", 45000);
  await clickByText(page, "Rules", { selector: "[role='tab'], button" });
  await waitForText(page, "Automation Rules", 45000);
  await waitForText(page, "Add Rule", 45000);
}

async function listQueueItems(apiBase, accessToken, sourceType) {
  const params = new URLSearchParams({ limit: "2500" });
  if (sourceType) params.set("source_type", sourceType);
  return asArray(
    await apiGet(
      apiBase,
      accessToken,
      withQuery(queueApiPath("/model-hub/annotation-queues/{queue_id}/items/"), params),
    ),
  );
}

async function getAnnotateDetail(apiBase, accessToken, itemId) {
  return apiGet(
    apiBase,
    accessToken,
    queueApiPath("/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/", {
      id: itemId,
    }),
  );
}

async function getQueueItemForSource(
  apiBase,
  accessToken,
  sourceType,
  sourceId,
) {
  if (!sourceId) return null;
  const params = new URLSearchParams({
    source_type: sourceType,
    source_id: sourceId,
  });
  const queueMatches = asArray(
    await apiGet(
      apiBase,
      accessToken,
      withQuery(apiPath("/model-hub/annotation-queues/for-source/"), params),
    ),
  );
  const match = queueMatches.find((entry) => entry?.queue?.id === QUEUE_ID);
  if (!match?.item?.id) return null;
  return {
    item: match.item,
    detail: await getAnnotateDetail(apiBase, accessToken, match.item.id),
  };
}

async function evaluateRuleAndCollectItems({
  apiBase,
  accessToken,
  ruleId,
  sourceType,
  beforeItemIds,
}) {
  const evaluation = await apiPost(
    apiBase,
    accessToken,
    queueApiPath("/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/evaluate/", {
      id: ruleId,
    }),
    {},
  );
  await sleep(500);
  const afterItems = await listQueueItems(apiBase, accessToken, sourceType);
  const newItems = afterItems.filter((item) => !beforeItemIds.has(item.id));
  return {
    evaluation,
    queueItems: newItems.length > 0 ? newItems : afterItems,
    newQueueItems: newItems,
  };
}

async function createRuleScenario({
  page,
  apiBase,
  accessToken,
  scenario,
  project,
  agentDefinition,
  datasetInfo,
  runId,
}) {
  const ruleName = `codex e2e ${scenario.key} ${scenario.scheduleValue} ${runId}`;
  const sourceTypeByLabel = {
    "Dataset Row": "dataset_row",
    Trace: "trace",
    Span: "observation_span",
    Session: "trace_session",
    Simulation: "call_execution",
  };
  const sourceType = sourceTypeByLabel[scenario.sourceLabel];
  const beforeItems = await listQueueItems(apiBase, accessToken, sourceType);
  const beforeItemIds = new Set(beforeItems.map((item) => item.id));

  await openRulesTab(page);
  await clickByText(page, "Add Rule", { selector: "button" });
  await page.waitForSelector('[data-testid="automation-rule-name-input"]', {
    timeout: 15000,
  });
  await page.type('[data-testid="automation-rule-name-input"]', ruleName);

  await selectMuiOption(
    page,
    "automation-rule-source-select",
    scenario.sourceLabel,
  );

  if (scenario.key === "dataset") {
    await chooseAutocompleteByLabel(page, "Dataset", datasetInfo.dataset.name);
  }

  if (scenario.projectRequired) {
    await chooseAutocompleteByLabel(page, "Project", project.name);
  }

  if (scenario.agentDefinitionRequired) {
    await chooseAutocompleteByLabel(
      page,
      "Agent Definition",
      agentDefinition.name,
    );
  }

  if (scenario.scheduleValue !== "manual") {
    await selectMuiOption(
      page,
      "automation-rule-trigger-select",
      scenario.scheduleLabel,
    );
  }

  const filter =
    scenario.key === "dataset"
      ? {
          propertyName: datasetInfo.column.name,
          value: "a",
          kind:
            datasetInfo.column.data_type === "integer" ||
            datasetInfo.column.data_type === "float"
              ? "number"
              : "text",
        }
      : scenario.filter;

  await applyFilter(page, filter);
  const conditionsScreenshotPath = await captureConditionsChipScreenshot(
    page,
    scenario,
    filter,
  );

  await page.waitForFunction(
    () => {
      const button = document.querySelector(
        '[data-testid="automation-rule-create-submit"]',
      );
      return button && !button.disabled;
    },
    { timeout: 15000 },
  );

  const responsePromise = page.waitForResponse(
    (response) =>
      response
        .url()
        .includes(`/annotation-queues/${QUEUE_ID}/automation-rules/`) &&
      response.request().method() === "POST",
    { timeout: 20000 },
  );
  await clickVisibleSelector(
    page,
    '[data-testid="automation-rule-create-submit"]',
  );
  const createResponse = await responsePromise;
  const createPayload = JSON.parse(createResponse.request().postData() || "{}");
  const createBody = await createResponse.json();
  assert(
    createResponse.status() === 201,
    `Create rule failed for ${scenario.key}: ${JSON.stringify(createBody)}`,
  );
  assert(createBody.id, `Create response missing rule id for ${scenario.key}`);
  assert(
    createBody.trigger_frequency === scenario.scheduleValue,
    `Expected ${scenario.scheduleValue}, got ${createBody.trigger_frequency}`,
  );
  assertPayloadFilterValue(createPayload, scenario.expectedPayload);
  const payloadScreenshotPath = await capturePayloadScreenshot(
    page,
    createPayload,
    scenario.payloadScreenshotName,
  );

  await waitForNoDialog(page);
  await waitForText(page, ruleName);

  const apiRules = await apiGet(
    apiBase,
    accessToken,
    queueApiPath("/model-hub/annotation-queues/{queue_id}/automation-rules/"),
  );
  assert(
    Array.isArray(apiRules) &&
      apiRules.some((rule) => rule.id === createBody.id),
    `Created rule ${createBody.id} was not returned by the rules API`,
  );

  await openRulesTab(page);
  await waitForText(page, ruleName);

  const { evaluation, queueItems, newQueueItems } =
    await evaluateRuleAndCollectItems({
      apiBase,
      accessToken,
      ruleId: createBody.id,
      sourceType,
      beforeItemIds,
    });
  assert(
    !evaluation?.error,
    `Rule evaluation failed for ${scenario.key}: ${evaluation?.error}`,
  );

  const screenshotPath = path.join(
    ARTIFACT_DIR,
    `${scenario.key}-${scenario.scheduleValue}.png`,
  );
  await page.screenshot({ path: screenshotPath, fullPage: true });

  return {
    scenario: scenario.key,
    schedule: scenario.scheduleValue,
    ruleId: createBody.id,
    ruleName,
    screenshotPath,
    chipScreenshotPath: filter.chipScreenshotPath,
    customValueScreenshotPath: filter.customValueScreenshotPath,
    selectedScreenshotPath: filter.selectedScreenshotPath,
    conditionsScreenshotPath,
    payloadScreenshotPath,
    evaluation,
    queueItems,
    newQueueItems,
    sourceType,
  };
}

async function findQueueItemBySource(
  apiBase,
  accessToken,
  sourceType,
  predicate,
) {
  const items = await listQueueItems(apiBase, accessToken, sourceType);
  for (const item of items) {
    try {
      const detail = await getAnnotateDetail(apiBase, accessToken, item.id);
      if (predicate({ item, detail })) return { item, detail };
    } catch {
      // Keep scanning; stale/deleted source rows should not block discovery.
    }
  }
  return null;
}

async function getCallExecutionDetailIfAvailable(apiBase, accessToken, callId) {
  if (!callId) return null;
  try {
    return await apiGet(
      apiBase,
      accessToken,
      `/simulate/call-executions/${callId}/`,
    );
  } catch {
    return null;
  }
}

async function findCompletedCallExecution(apiBase, accessToken, callType) {
  const response = await apiGet(
    apiBase,
    accessToken,
    "/simulate/api/call-executions/?status=completed&limit=500",
  );
  return asArray(response).find((call) => {
    const duration = Number(call.duration_seconds ?? call.duration ?? 0);
    return (
      call?.id &&
      call?.status === "completed" &&
      call?.simulation_call_type === callType &&
      duration > 0
    );
  });
}

async function findRenderableCallExecutionQueueItem({
  apiBase,
  accessToken,
  callType,
}) {
  const items = await listQueueItems(apiBase, accessToken, "call_execution");
  for (const item of items) {
    let detail;
    try {
      detail = await getAnnotateDetail(apiBase, accessToken, item.id);
    } catch {
      continue;
    }
    const content = detail?.item?.source_content || {};
    const preview = detail?.item?.source_preview || item.source_preview || {};
    if (content.status !== "completed") continue;
    if (
      (preview.simulation_call_type || content.simulation_call_type) !==
      callType
    ) {
      continue;
    }
    const callData = await getCallExecutionDetailIfAvailable(
      apiBase,
      accessToken,
      content.call_id,
    );
    if (callData) return { item, detail, callData };
  }
  return null;
}

async function ensureCompletedVoiceCallInQueue(apiBase, accessToken) {
  const existing = await findRenderableCallExecutionQueueItem({
    apiBase,
    accessToken,
    callType: "voice",
  });
  if (existing) {
    return {
      status: "existing",
      queueItem: existing.item,
      detail: existing.detail,
    };
  }

  const call = await findCompletedCallExecution(apiBase, accessToken, "voice");
  if (!call) {
    return { status: "missing" };
  }

  const addResult = await apiPost(
    apiBase,
    accessToken,
    queueApiPath("/model-hub/annotation-queues/{queue_id}/items/add-items/"),
    {
      items: [{ source_type: "call_execution", source_id: call.id }],
    },
  );
  const added =
    (await getQueueItemForSource(
      apiBase,
      accessToken,
      "call_execution",
      call.id,
    )) ||
    (await findQueueItemBySource(
      apiBase,
      accessToken,
      "call_execution",
      ({ detail }) => detail?.item?.source_content?.call_id === call.id,
    ));
  if (added) {
    const callData = await getCallExecutionDetailIfAvailable(
      apiBase,
      accessToken,
      call.id,
    );
    if (!callData) {
      return {
        status: "add-failed",
        call,
        addResult,
        reason: "Added item but call detail endpoint did not resolve",
      };
    }
  }
  return {
    status: added ? (addResult.added > 0 ? "added" : "existing") : "add-failed",
    call,
    addResult,
    queueItem: added?.item,
    detail: added?.detail,
  };
}

async function ensureCompletedChatCallInQueue(apiBase, accessToken) {
  const existing = await findRenderableCallExecutionQueueItem({
    apiBase,
    accessToken,
    callType: "text",
  });
  if (existing) {
    return {
      status: "existing",
      queueItem: existing.item,
      detail: existing.detail,
    };
  }

  const call = await findCompletedCallExecution(apiBase, accessToken, "text");
  if (!call) {
    return { status: "missing" };
  }

  const addResult = await apiPost(
    apiBase,
    accessToken,
    queueApiPath("/model-hub/annotation-queues/{queue_id}/items/add-items/"),
    {
      items: [{ source_type: "call_execution", source_id: call.id }],
    },
  );
  const added = await getQueueItemForSource(
    apiBase,
    accessToken,
    "call_execution",
    call.id,
  );
  return {
    status: added ? (addResult.added > 0 ? "added" : "existing") : "add-failed",
    call,
    addResult,
    queueItem: added?.item,
    detail: added?.detail,
  };
}

async function findTraceSessionCandidate(
  apiBase,
  accessToken,
  preferredProjectId,
) {
  const projectResponse = await apiGet(
    apiBase,
    accessToken,
    withQuery(apiPath("/tracer/project/list_project_ids/"), "project_type=observe"),
  );
  const projects = asArray(projectResponse?.projects);
  const orderedProjects = [
    ...projects.filter((project) => project.id === preferredProjectId),
    ...projects.filter((project) => project.id !== preferredProjectId),
  ];

  for (const project of orderedProjects) {
    try {
      const response = await apiGet(
        apiBase,
        accessToken,
        withQuery(
          apiPath("/tracer/trace-session/list_sessions/"),
          `project_id=${encodeURIComponent(project.id)}&page=1&page_size=5`,
        ),
      );
      const row = asArray(response?.table).find((item) => item?.session_id);
      if (row) {
        return {
          sourceId: row.session_id,
          project,
        };
      }
    } catch {
      // Keep scanning projects; some projects may not have session aggregates.
    }
  }
  return null;
}

async function ensureTraceSessionInQueue(
  apiBase,
  accessToken,
  preferredProjectId,
) {
  const existingItems = await listQueueItems(
    apiBase,
    accessToken,
    "trace_session",
  );
  for (const item of existingItems) {
    try {
      const detail = await getAnnotateDetail(apiBase, accessToken, item.id);
      if (detail?.item?.source_content?.session_id) {
        return { status: "existing", queueItem: item, detail };
      }
    } catch {
      // Keep scanning; stale source rows should not block discovery.
    }
  }

  const candidate = await findTraceSessionCandidate(
    apiBase,
    accessToken,
    preferredProjectId,
  );
  if (!candidate) {
    return { status: "missing" };
  }

  const addResult = await apiPost(
    apiBase,
    accessToken,
    queueApiPath("/model-hub/annotation-queues/{queue_id}/items/add-items/"),
    {
      items: [{ source_type: "trace_session", source_id: candidate.sourceId }],
    },
  );
  const added = await getQueueItemForSource(
    apiBase,
    accessToken,
    "trace_session",
    candidate.sourceId,
  );
  return {
    status: added ? (addResult.added > 0 ? "added" : "existing") : "add-failed",
    candidate,
    addResult,
    queueItem: added?.item,
    detail: added?.detail,
  };
}

async function ensureQueueItemsViaApiRule({
  apiBase,
  accessToken,
  sourceType,
  conditions = {},
}) {
  const beforeItems = await listQueueItems(apiBase, accessToken, sourceType);
  const beforeItemIds = new Set(beforeItems.map((item) => item.id));
  const rule = await fetchJson(
    `${apiBase}${queueApiPath("/model-hub/annotation-queues/{queue_id}/automation-rules/")}`,
    {
      method: "POST",
      headers: authHeaders(accessToken),
      body: JSON.stringify({
        name: `codex e2e annotator-walk ${sourceType} ${Date.now().toString(36)}`,
        source_type: sourceType,
        conditions,
        enabled: true,
        trigger_frequency: "manual",
      }),
    },
  );
  try {
    return await evaluateRuleAndCollectItems({
      apiBase,
      accessToken,
      ruleId: rule.id,
      sourceType,
      beforeItemIds,
    });
  } finally {
    await apiDelete(
      apiBase,
      accessToken,
      queueApiPath("/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/", {
        id: rule.id,
      }),
    );
  }
}

function firstCanonicalKey(fields = {}) {
  return Object.keys(fields)[0];
}

async function waitForAnnotatorShell(page) {
  await waitForAnyText(page, ["Labels", "Submit", "Skip"], 45000);
}

async function assertDatasetRowAnnotator(page, detail) {
  const content = detail.item.source_content || {};
  const key = firstCanonicalKey(content.fields);
  assert(key, "Dataset row annotator detail had no fields");
  await waitForText(page, key, 45000);
  const value = String(content.fields[key] || "");
  if (value) await waitForText(page, value.slice(0, 60), 45000);
}

async function assertInlineTraceAnnotator(page) {
  await waitForAnyText(
    page,
    ["Trace", "Preview", "Log View", "Attributes"],
    60000,
  );
  await waitForAnyText(
    page,
    ["Preview", "Log View", "Attributes", "Events"],
    60000,
  );
}

async function assertSessionAnnotator(page) {
  await waitForAnyText(
    page,
    ["Traces:", "Duration:", "Cost:", "Session"],
    60000,
  );
}

async function assertVoiceTraceAnnotator(page) {
  await waitForAnyText(
    page,
    [
      "Call Log Details",
      "Recording",
      "Transcript",
      "Voice call data not available.",
    ],
    60000,
  );
}

async function assertCallExecutionAnnotator(
  page,
  detail,
  apiBase,
  accessToken,
) {
  const content = detail.item.source_content || {};
  const callData = await apiGet(
    apiBase,
    accessToken,
    `/simulate/call-executions/${content.call_id}/`,
  );
  const isChat =
    content.simulation_call_type === "text" ||
    callData.simulation_call_type === "text";
  await waitForText(
    page,
    isChat ? "Chat Log Details" : "Call Log Details",
    60000,
  );

  const transcript = callData.transcripts || callData.transcript || [];
  const firstTurn = transcript.find((turn) => {
    const text = Array.isArray(turn.messages)
      ? turn.messages.join(" ")
      : turn.content || turn.message || "";
    return normalizeText(text).length > 0;
  });
  if (firstTurn) {
    const text = Array.isArray(firstTurn.messages)
      ? firstTurn.messages.join(" ")
      : firstTurn.content || firstTurn.message || "";
    const needle = normalizeText(text).slice(0, 60);
    const bodyText = normalizeText(await visibleText(page));
    if (!bodyText.includes(needle)) {
      await waitForAnyText(
        page,
        isChat
          ? ["Chat Log Details", "Messages", "Transcript", "Logs"]
          : [
              "Recording",
              "Transcript",
              "Logs",
              "FAGI Simulator",
              "Bot",
              "Voice call data not available.",
            ],
        60000,
      );
      return;
    }
  } else if (!isChat) {
    await waitForAnyText(page, ["Recording", "Transcript"], 60000);
  } else {
    await waitForText(page, "Chat Log Details", 60000);
  }
}

async function collectAnnotatorTargets({
  apiBase,
  accessToken,
  scenarioResults,
}) {
  const itemMap = new Map();
  for (const result of scenarioResults) {
    for (const item of result.queueItems || []) {
      if (item?.id) itemMap.set(item.id, item);
    }
  }
  for (const sourceType of [
    "dataset_row",
    "trace",
    "observation_span",
    "trace_session",
    "call_execution",
  ]) {
    for (const item of await listQueueItems(apiBase, accessToken, sourceType)) {
      if (item?.id) itemMap.set(item.id, item);
    }
  }

  const targets = {};
  for (const item of itemMap.values()) {
    const itemSourceType = item.source_type;
    if (itemSourceType === "dataset_row" && targets.datasetRow) continue;
    if (itemSourceType === "observation_span" && targets.observationSpan) {
      continue;
    }
    if (itemSourceType === "trace_session" && targets.traceSession) continue;
    if (itemSourceType === "trace" && targets.trace && targets.traceVoice) {
      continue;
    }
    if (
      itemSourceType === "call_execution" &&
      targets.callExecutionVoice &&
      targets.callExecutionChat
    ) {
      continue;
    }

    let detail;
    try {
      detail = await getAnnotateDetail(apiBase, accessToken, item.id);
    } catch {
      continue;
    }
    const sourceType = detail?.item?.source_type || item.source_type;
    const content = detail?.item?.source_content || {};
    const preview = detail?.item?.source_preview || item.source_preview || {};

    if (sourceType === "dataset_row" && !targets.datasetRow) {
      targets.datasetRow = { item, detail };
    } else if (sourceType === "trace") {
      if (content.project_source === "simulator" && !targets.traceVoice) {
        targets.traceVoice = { item, detail };
      } else if (content.project_source !== "simulator" && !targets.trace) {
        targets.trace = { item, detail };
      }
    } else if (sourceType === "observation_span" && !targets.observationSpan) {
      targets.observationSpan = { item, detail };
    } else if (sourceType === "trace_session" && !targets.traceSession) {
      targets.traceSession = { item, detail };
    } else if (sourceType === "call_execution") {
      const isCompleted = content.status === "completed";
      const callType =
        preview.simulation_call_type || content.simulation_call_type;
      const callData = isCompleted
        ? await getCallExecutionDetailIfAvailable(
            apiBase,
            accessToken,
            content.call_id,
          )
        : null;
      if (callData && callType === "voice" && !targets.callExecutionVoice) {
        targets.callExecutionVoice = { item, detail, callData };
      } else if (
        callData &&
        callType === "text" &&
        !targets.callExecutionChat
      ) {
        targets.callExecutionChat = { item, detail, callData };
      }
    }
  }
  return targets;
}

async function runAnnotatorWalk({
  page,
  apiBase,
  accessToken,
  project,
  scenarioResults,
}) {
  const cleanupQueueItems = [];
  const completedVoice = await ensureCompletedVoiceCallInQueue(
    apiBase,
    accessToken,
  );
  if (completedVoice.status === "added" && completedVoice.queueItem) {
    cleanupQueueItems.push(completedVoice.queueItem);
  }
  if (completedVoice.status === "added") {
    console.log(
      `DEV_DB_COMPLETED_VOICE_CALL added ${completedVoice.call.id} item ${completedVoice.queueItem?.id}`,
    );
  } else if (completedVoice.status === "existing") {
    console.log(
      `DEV_DB_COMPLETED_VOICE_CALL existing ${completedVoice.queueItem.id}`,
    );
  } else if (completedVoice.status === "missing") {
    console.log("DEV_DB_COMPLETED_VOICE_CALL missing");
  } else {
    console.log(
      `DEV_DB_COMPLETED_VOICE_CALL add-failed ${JSON.stringify(
        completedVoice.addResult || completedVoice.reason,
      )}`,
    );
  }

  const completedChat = await ensureCompletedChatCallInQueue(
    apiBase,
    accessToken,
  );
  if (completedChat.status === "added" && completedChat.queueItem) {
    cleanupQueueItems.push(completedChat.queueItem);
  }
  if (completedChat.status === "added") {
    console.log(
      `DEV_DB_COMPLETED_CHAT_CALL added ${completedChat.call.id} item ${completedChat.queueItem?.id}`,
    );
  } else if (completedChat.status === "existing") {
    console.log(
      `DEV_DB_COMPLETED_CHAT_CALL existing ${completedChat.queueItem.id}`,
    );
  } else if (completedChat.status === "missing") {
    console.log("DEV_DB_COMPLETED_CHAT_CALL missing");
  } else {
    console.log(
      `DEV_DB_COMPLETED_CHAT_CALL add-failed ${JSON.stringify(
        completedChat.addResult || completedChat.reason,
      )}`,
    );
  }

  const traceSession = await ensureTraceSessionInQueue(
    apiBase,
    accessToken,
    project?.id,
  );
  if (traceSession.status === "added" && traceSession.queueItem) {
    cleanupQueueItems.push(traceSession.queueItem);
  }
  if (traceSession.status === "added") {
    console.log(
      `DEV_DB_TRACE_SESSION added ${traceSession.candidate.sourceId} item ${traceSession.queueItem?.id}`,
    );
  } else if (traceSession.status === "existing") {
    console.log(`DEV_DB_TRACE_SESSION existing ${traceSession.queueItem.id}`);
  } else if (traceSession.status === "missing") {
    console.log("DEV_DB_TRACE_SESSION missing");
  } else {
    console.log(
      `DEV_DB_TRACE_SESSION add-failed ${JSON.stringify(
        traceSession.addResult || traceSession.reason,
      )}`,
    );
  }

  const datasetFallback = await ensureQueueItemsViaApiRule({
    apiBase,
    accessToken,
    sourceType: "dataset_row",
  }).catch((error) => {
    console.log(`ANNOTATOR_WALK_DATASET_FALLBACK ${error.message}`);
    return null;
  });
  if (datasetFallback?.newQueueItems?.length) {
    cleanupQueueItems.push(...datasetFallback.newQueueItems);
  }

  try {
    const targets = await collectAnnotatorTargets({
      apiBase,
      accessToken,
      scenarioResults,
    });
    if (completedVoice.queueItem?.id && completedVoice.detail) {
      targets.callExecutionVoice = {
        item: completedVoice.queueItem,
        detail: completedVoice.detail,
      };
    }
    if (completedChat.queueItem?.id && completedChat.detail) {
      targets.callExecutionChat = {
        item: completedChat.queueItem,
        detail: completedChat.detail,
      };
    }
    if (traceSession.queueItem?.id && traceSession.detail) {
      targets.traceSession = {
        item: traceSession.queueItem,
        detail: traceSession.detail,
      };
    }

    const scenarios = [
      {
        key: "annotator-dataset-row",
        target: targets.datasetRow,
        assertFn: (detail) => assertDatasetRowAnnotator(page, detail),
      },
      {
        key: "annotator-trace",
        target: targets.trace,
        assertFn: () => assertInlineTraceAnnotator(page),
      },
      {
        key: "annotator-trace-voice-project",
        target: targets.traceVoice,
        assertFn: () => assertVoiceTraceAnnotator(page),
      },
      {
        key: "annotator-observation-span",
        target: targets.observationSpan,
        assertFn: () => assertInlineTraceAnnotator(page),
      },
      {
        key: "annotator-trace-session",
        target: targets.traceSession,
        assertFn: () => assertSessionAnnotator(page),
      },
      {
        key: "annotator-call-execution-voice",
        target: targets.callExecutionVoice,
        assertFn: (detail) =>
          assertCallExecutionAnnotator(page, detail, apiBase, accessToken),
      },
      {
        key: "annotator-call-execution-chat",
        target: targets.callExecutionChat,
        assertFn: (detail) =>
          assertCallExecutionAnnotator(page, detail, apiBase, accessToken),
      },
    ];

    const walkResults = [];
    for (const scenario of scenarios) {
      if (!scenario.target?.item?.id) {
        const result = {
          scenario: scenario.key,
          status: "skipped",
          reason: "No matching queue item in dev DB",
        };
        walkResults.push(result);
        console.log(`SKIP ${scenario.key} ${result.reason}`);
        continue;
      }

      await page.goto(
        `${FRONTEND_URL}/dashboard/annotations/queues/${QUEUE_ID}/annotate?itemId=${scenario.target.item.id}`,
        { waitUntil: "domcontentloaded" },
      );
      await waitForAnnotatorShell(page);
      await scenario.assertFn(scenario.target.detail);
      const screenshotPath = path.join(ARTIFACT_DIR, `${scenario.key}.png`);
      await page.screenshot({ path: screenshotPath, fullPage: true });
      const result = {
        scenario: scenario.key,
        status: "passed",
        itemId: scenario.target.item.id,
        screenshotPath,
      };
      walkResults.push(result);
      console.log(
        `PASS ${scenario.key} ${scenario.target.item.id} ${screenshotPath}`,
      );
    }

    return walkResults;
  } finally {
    await cleanupCreatedQueueItems(apiBase, accessToken, cleanupQueueItems);
  }
}

async function testCreateRuleNameValidation({ page, datasetInfo, runId }) {
  const ruleName = `codex e2e name validation ${runId}`;
  const nameSelector = '[data-testid="automation-rule-name-input"]';
  const submitSelector = '[data-testid="automation-rule-create-submit"]';
  const submitWrapperSelector =
    '[data-testid="automation-rule-create-submit-wrapper"]';

  await openRulesTab(page);
  await clickByText(page, "Add Rule", { selector: "button" });
  await page.waitForSelector(nameSelector, { timeout: 15000 });
  await page.waitForFunction(
    (selector) => document.activeElement === document.querySelector(selector),
    { timeout: 5000 },
    nameSelector,
  );

  await selectMuiOption(page, "automation-rule-source-select", "Dataset Row");
  await chooseAutocompleteByLabel(page, "Dataset", datasetInfo.dataset.name);
  await applyFilter(page, {
    propertyName: datasetInfo.column.name,
    value: "a",
    kind:
      datasetInfo.column.data_type === "integer" ||
      datasetInfo.column.data_type === "float"
        ? "number"
        : "text",
  });

  await waitForText(page, "Rule name is required");
  await page.waitForFunction(
    (selector) => document.querySelector(selector)?.disabled === true,
    { timeout: 5000 },
    submitSelector,
  );

  await page.hover(submitWrapperSelector);
  await waitForText(page, "Enter a rule name", 5000);
  const screenshotPath = path.join(ARTIFACT_DIR, "name-required-tooltip.png");
  await page.screenshot({ path: screenshotPath, fullPage: true });

  await page.click(nameSelector);
  await page.keyboard.type(ruleName);
  await page.waitForFunction(
    (selector) => {
      const button = document.querySelector(selector);
      return button && !button.disabled;
    },
    { timeout: 15000 },
    submitSelector,
  );

  const responsePromise = page.waitForResponse(
    (response) =>
      response
        .url()
        .includes(`/annotation-queues/${QUEUE_ID}/automation-rules/`) &&
      response.request().method() === "POST",
    { timeout: 20000 },
  );
  await clickVisibleSelector(page, submitSelector);
  const createResponse = await responsePromise;
  const createBody = await createResponse.json();
  assert(
    createResponse.status() === 201,
    `Create rule failed for name validation: ${JSON.stringify(createBody)}`,
  );
  assert(createBody.id, "Create response missing rule id for name validation");

  await waitForNoDialog(page);
  await waitForText(page, ruleName);

  return {
    scenario: "name-validation",
    schedule: "manual",
    ruleId: createBody.id,
    ruleName,
    screenshotPath,
  };
}

async function main() {
  await fs.mkdir(ARTIFACT_DIR, { recursive: true });

  const apiBase = await resolveApiBase();
  const tokens = await login(apiBase);
  const accessToken = tokens.access;
  const queue = await apiGet(
    apiBase,
    accessToken,
    apiPath("/model-hub/annotation-queues/{id}/", { id: QUEUE_ID }),
  );
  await cleanupExistingTestRules(apiBase, accessToken);
  const project = await getProject(apiBase, accessToken, queue);
  const agentDefinition = await getAgentDefinition(apiBase, accessToken, queue);
  const datasetInfo = await getDatasetWithColumn(apiBase, accessToken);
  const chromePath = await findChromeExecutable();
  const browser = await puppeteer.launch({
    executablePath: chromePath,
    headless: process.env.HEADLESS === "false" ? false : "new",
    defaultViewport: { width: 1440, height: 1000 },
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });
  const createdRuleIds = [];
  const pendingQueueItemCleanup = [];
  const results = [];
  let page;

  try {
    page = await browser.newPage();
    page.setDefaultTimeout(20000);
    page.on("console", (message) => {
      const type = message.type();
      if (type === "error") {
        console.log(`[browser:${type}] ${message.text()}`);
      }
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

    const runId = Date.now().toString(36);
    const validationResult = await testCreateRuleNameValidation({
      page,
      datasetInfo,
      runId,
    });
    createdRuleIds.push(validationResult.ruleId);
    results.push(validationResult);
    console.log(
      `PASS ${validationResult.scenario} ${validationResult.schedule} ${validationResult.ruleId} ${validationResult.screenshotPath}`,
    );
    await cleanupCreatedRule(
      apiBase,
      accessToken,
      createdRuleIds,
      validationResult.ruleId,
    );

    for (const scenario of SOURCE_SCENARIOS) {
      const result = await createRuleScenario({
        page,
        apiBase,
        accessToken,
        scenario,
        project,
        agentDefinition,
        datasetInfo,
        runId,
      });
      createdRuleIds.push(result.ruleId);
      pendingQueueItemCleanup.push(...(result.newQueueItems || []));
      results.push(result);
      console.log(
        `PASS ${result.scenario} ${result.schedule} ${result.ruleId} ${result.screenshotPath}`,
      );
      await cleanupCreatedRule(
        apiBase,
        accessToken,
        createdRuleIds,
        result.ruleId,
      );
      await cleanupCreatedQueueItems(
        apiBase,
        accessToken,
        result.newQueueItems,
      );
      forgetQueueItems(pendingQueueItemCleanup, result.newQueueItems);
    }

    const annotatorWalkResults = await runAnnotatorWalk({
      page,
      apiBase,
      accessToken,
      project,
      scenarioResults: results,
    });
    results.push(...annotatorWalkResults);
  } catch (error) {
    if (page) {
      const failurePath = path.join(ARTIFACT_DIR, "failure.png");
      await page
        .screenshot({ path: failurePath, fullPage: true })
        .catch(() => {});
      const bodyText = await visibleText(page).catch(() => "");
      console.log(`FAILURE_SCREENSHOT ${failurePath}`);
      console.log(`FAILURE_BODY ${bodyText.slice(0, 2000)}`);
    }
    throw error;
  } finally {
    await browser.close();
    await cleanupCreatedQueueItems(
      apiBase,
      accessToken,
      pendingQueueItemCleanup,
    );
    if (!KEEP_RULES) {
      for (const ruleId of createdRuleIds) {
        await apiDelete(
          apiBase,
          accessToken,
          queueApiPath("/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/", {
            id: ruleId,
          }),
        );
      }
    }
  }

  console.log(JSON.stringify({ status: "passed", results }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
