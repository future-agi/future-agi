/* eslint-disable no-console */
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const HEALTH_RELOAD_SCREENSHOT_PATH =
  "/tmp/gateway-settings-health-reload-smoke.png";
const ORG_CONFIG_SCREENSHOT_PATH =
  "/tmp/gateway-settings-org-config-save-smoke.png";
const BATCH_SCREENSHOT_PATH = "/tmp/gateway-settings-batch-submit-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/gateway-settings-mutation-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();

  const auth = await createAuthenticatedContext();
  const apiFailures = [];
  const pageErrors = [];
  const gatewayRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let caughtError = null;
  let cleanupError = null;
  let cleanup = null;
  let evidence = {};

  try {
    const baseline = await prepareOrgConfigRestorer(auth.client);
    cleanup = baseline.cleanup;
    evidence = await preflightSettings(auth.client, baseline, auth.runId);

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 980 },
      args: ["--no-sandbox"],
    });
    page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installBrowserState(page, auth);

    page.on("request", (request) => {
      const url = request.url();
      if (!isGatewayApiUrl(url)) return;
      gatewayRequests.push(`${request.method()} ${url}`);
      if (MUTATION_METHODS.has(request.method())) {
        const mutation = `${request.method()} ${url}`;
        browserMutations.push(mutation);
        if (!isAllowedSettingsMutation(request.method(), url)) {
          unexpectedMutations.push(mutation);
        }
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isGatewayApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponsesDuring(
      page,
      "initial Gateway Settings mutation load",
      [
        gatewayListResponse(),
        gatewayConfigResponse(evidence.gateway_id),
        emailAlertsResponse(),
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway/settings`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway/settings");

    for (const label of [
      "Settings",
      "Gateway configuration, health checks, and administration",
      "Health Check",
      "Reload Config",
      "Org Config",
      "Batch Jobs",
      "Full Config (Read-Only)",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    const [healthResponse] = await waitForResponsesDuring(
      page,
      "run Gateway Settings health check",
      [healthCheckResponse(evidence.gateway_id)],
      () => clickVisibleText(page, "Health Check", { exact: true }),
    );
    evidence.health_check = await responseResult(healthResponse);
    assert(
      evidence.health_check?.status === "healthy",
      `Health check did not return healthy: ${JSON.stringify(
        evidence.health_check,
      )}`,
    );
    await waitForVisibleText(page, "Health check complete");

    const [reloadResponse] = await waitForResponsesDuring(
      page,
      "run Gateway Settings reload",
      [reloadConfigResponse(evidence.gateway_id), gatewayConfigResponse()],
      () => clickVisibleText(page, "Reload Config", { exact: true }),
    );
    evidence.reload_config = await responseResult(reloadResponse);
    assert(
      evidence.reload_config?.status === "ok",
      `Reload config did not return ok: ${JSON.stringify(
        evidence.reload_config,
      )}`,
    );
    await waitForVisibleText(page, "Configuration reloaded");
    await page.screenshot({
      path: HEALTH_RELOAD_SCREENSHOT_PATH,
      fullPage: true,
    });
    evidence.health_reload_screenshot = HEALTH_RELOAD_SCREENSHOT_PATH;

    await clickVisibleText(page, "Org Config", { exact: true });
    await waitForPath(page, "/dashboard/gateway/settings/org-config");
    await waitForVisibleText(
      page,
      `Version ${evidence.original_org_config_version}`,
      { exact: true },
    );
    await clickVisibleText(page, "Edit Config", { exact: true });
    await waitForVisibleText(page, "Edit Organization Config", {
      exact: true,
    });
    await typeIntoPlaceholder(
      page,
      "Change description (optional)",
      evidence.change_description,
    );

    const [orgConfigResponse] = await waitForResponsesDuring(
      page,
      "save Gateway Settings org config",
      [orgConfigCreateResponse(), orgConfigActiveResponse()],
      () => clickDialogButton(page, "Save & Activate"),
    );
    evidence.saved_org_config = await responseResult(orgConfigResponse);
    await waitForTextGone(page, "Edit Organization Config");
    await waitForVisibleText(page, "Config saved and activated");
    assert(
      evidence.saved_org_config?.id &&
        evidence.saved_org_config?.version >
          evidence.original_org_config_version,
      `Org config save did not return a new version: ${JSON.stringify(
        evidence.saved_org_config,
      )}`,
    );

    const activeAfterSave = await auth.client.get(
      apiPath("/agentcc/org-configs/active/"),
    );
    assert(
      activeAfterSave?.id === evidence.saved_org_config.id &&
        activeAfterSave?.change_description === evidence.change_description,
      `Saved org config is not active with the browser change description: ${JSON.stringify(
        {
          active_id: activeAfterSave?.id,
          saved_id: evidence.saved_org_config?.id,
          change_description: activeAfterSave?.change_description,
        },
      )}`,
    );
    evidence.active_org_config_after_save = {
      id: activeAfterSave.id,
      version: activeAfterSave.version,
      change_description: activeAfterSave.change_description,
    };
    await waitForVisibleText(page, `Version ${activeAfterSave.version}`, {
      exact: true,
    });
    await waitForVisibleText(page, evidence.change_description);
    await page.screenshot({ path: ORG_CONFIG_SCREENSHOT_PATH, fullPage: true });
    evidence.org_config_screenshot = ORG_CONFIG_SCREENSHOT_PATH;

    await clickVisibleText(page, "Batch Jobs", { exact: true });
    await waitForPath(page, "/dashboard/gateway/settings/batch-jobs");
    await waitForVisibleText(page, "Submit Batch Job", { exact: true });
    await clickVisibleText(page, "Submit Batch Job", { exact: true });
    await waitForVisibleText(page, "Submit Batch Job", { exact: true });
    await waitForVisibleText(
      page,
      "Requests (JSON array of chat completion request objects)",
    );

    const [submitBatchResponse] = await waitForResponsesDuring(
      page,
      "submit Gateway Settings batch job",
      [submitBatchResponseFor(evidence.gateway_id)],
      () => clickDialogButton(page, "Submit"),
    );
    evidence.batch_submit = await responseResult(submitBatchResponse);
    evidence.batch_id =
      evidence.batch_submit?.batch_id || evidence.batch_submit?.id || null;
    assert(
      evidence.batch_id,
      `Batch submit did not return a batch id: ${JSON.stringify(
        evidence.batch_submit,
      )}`,
    );
    await waitForTextGone(
      page,
      "Requests (JSON array of chat completion request objects)",
    );
    await waitForVisibleText(page, evidence.batch_id.slice(0, 12));

    const terminalBatch = await pollBatchTerminal(
      auth.client,
      evidence.gateway_id,
      evidence.batch_id,
    );
    evidence.batch_terminal = {
      batch_id: terminalBatch.batch_id,
      status: terminalBatch.status,
      total: terminalBatch.total,
      summary: terminalBatch.summary,
      result_statuses: asArray(terminalBatch.results).map(
        (item) => item.status,
      ),
    };
    assert(
      terminalBatch.status === "completed" &&
        terminalBatch.summary?.completed === 1,
      `Batch job did not complete successfully: ${JSON.stringify(
        evidence.batch_terminal,
      )}`,
    );
    await waitForVisibleText(page, "completed", {
      exact: true,
      timeout: 60000,
    });
    await clickRowAction(page, evidence.batch_id.slice(0, 12), 0);
    await waitForVisibleText(page, "Batch Details", { exact: true });
    for (const label of [
      "Total",
      "Completed",
      "Failed",
      "Total Cost",
      "Tokens",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    await waitForVisibleText(page, "success", { exact: true });
    await page.screenshot({ path: BATCH_SCREENSHOT_PATH, fullPage: true });
    evidence.batch_screenshot = BATCH_SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Unexpected Gateway Settings browser mutations: ${unexpectedMutations.join(
        "; ",
      )}`,
    );
    assert(
      browserMutations.length === 4,
      `Expected four Gateway Settings browser mutations, saw ${browserMutations.length}: ${browserMutations.join(
        "; ",
      )}`,
    );
    evidence.browser_mutations = browserMutations;
  } catch (error) {
    caughtError = error;
    await page
      ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
  } finally {
    if (browser) await browser.close();
    if (cleanup) {
      try {
        evidence.cleanup = await cleanup();
      } catch (error) {
        cleanupError = error;
        evidence.cleanup = { status: "failed", error: error.message };
      }
    }
  }

  if (caughtError || cleanupError) {
    console.error(
      JSON.stringify(
        {
          status: "failed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence,
          api_failures: apiFailures,
          page_errors: pageErrors,
          gateway_requests: gatewayRequests,
          browser_mutations: browserMutations,
          unexpected_mutations: unexpectedMutations,
          failure_screenshot: FAILURE_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
    throw caughtError || cleanupError;
  }

  console.log(
    JSON.stringify(
      {
        status: "passed",
        app_base: APP_BASE,
        api_base: auth.apiBase,
        organization_id: auth.organizationId,
        workspace_id: auth.workspaceId,
        evidence,
        gateway_request_count: gatewayRequests.length,
        browser_mutations: browserMutations,
      },
      null,
      2,
    ),
  );
}

async function preflightSettings(client, baseline, runId) {
  const gateways = asArray(await client.get(apiPath("/agentcc/gateways/")));
  assert(gateways.length > 0, "Gateway list returned no configured gateway.");
  const gatewayId =
    gateways.find((gateway) => gateway.id === "default")?.id ||
    gateways[0].id ||
    "default";
  const suffix = String(runId || Date.now())
    .replace(/[^a-z0-9]/gi, "_")
    .toLowerCase();

  return {
    gateway_id: gatewayId,
    original_org_config_id: baseline.originalActiveConfig.id,
    original_org_config_version: baseline.originalActiveConfig.version,
    change_description: `Browser Settings mutation ${suffix}`,
  };
}

async function prepareOrgConfigRestorer(client) {
  const originalActiveConfig = await client.get(
    apiPath("/agentcc/org-configs/active/"),
  );
  assert(
    originalActiveConfig?.id && originalActiveConfig?.is_active === true,
    "AgentCC active org config endpoint did not return an active baseline.",
  );
  const beforeConfigIds = new Set(
    collectionRows(await client.get(apiPath("/agentcc/org-configs/")))
      .map((config) => config?.id)
      .filter(Boolean),
  );

  return {
    originalActiveConfig,
    beforeConfigIds,
    cleanup: createOrgConfigRestorer({
      client,
      beforeConfigIds,
      originalActiveConfigId: originalActiveConfig.id,
    }),
  };
}

function collectionRows(value) {
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.results)) return value.results;
  if (Array.isArray(value?.data)) return value.data;
  return asArray(value);
}

function createOrgConfigRestorer({
  client,
  beforeConfigIds,
  originalActiveConfigId,
}) {
  let completed = false;

  return async () => {
    if (completed) return { status: "already-cleaned" };
    const restoreEvidence = {
      status: "passed",
      original_config_id: originalActiveConfigId,
      activated_original: false,
      deleted_config_ids: [],
      deleted_config_versions: [],
    };

    const activeConfig = await client.get(
      apiPath("/agentcc/org-configs/active/"),
    );
    if (activeConfig?.id !== originalActiveConfigId) {
      await client.post(
        apiPath("/agentcc/org-configs/{id}/activate/", {
          id: originalActiveConfigId,
        }),
        {},
      );
      restoreEvidence.activated_original = true;
    }

    const configs = collectionRows(
      await client.get(apiPath("/agentcc/org-configs/")),
    );
    const disposableConfigs = configs.filter(
      (config) =>
        config?.id &&
        config.id !== originalActiveConfigId &&
        !beforeConfigIds.has(config.id),
    );

    for (const config of disposableConfigs) {
      await ignoreNotFound(() =>
        client.delete(apiPath("/agentcc/org-configs/{id}/", { id: config.id })),
      );
      restoreEvidence.deleted_config_ids.push(config.id);
      restoreEvidence.deleted_config_versions.push(config.version);
    }

    const restoredActive = await client.get(
      apiPath("/agentcc/org-configs/active/"),
    );
    assert(
      restoredActive?.id === originalActiveConfigId,
      "AgentCC org config cleanup did not restore the original active config.",
    );

    completed = true;
    return restoreEvidence;
  };
}

async function ignoreNotFound(fn) {
  try {
    return await fn();
  } catch (error) {
    const message = String(error?.message || "").toLowerCase();
    if (
      error?.status === 404 ||
      message.includes("not found") ||
      message.includes("does not exist")
    ) {
      return null;
    }
    throw error;
  }
}

async function pollBatchTerminal(client, gatewayId, batchId) {
  let lastBatch = null;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const batch = await client.get(
      apiPath("/agentcc/gateways/{id}/get-batch/", { id: gatewayId }),
      { query: { batch_id: batchId } },
    );
    lastBatch = batch;
    if (["completed", "cancelled", "failed"].includes(batch?.status)) {
      return batch;
    }
    await sleep(1000);
  }
  throw new Error(
    `Batch ${batchId} did not reach a terminal status: ${JSON.stringify(
      lastBatch,
    )}`,
  );
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

async function installRuntimeConfig(page, auth) {
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/config.js") {
      request.respond({
        status: 200,
        contentType: "application/javascript",
        body: `window.__FUTURE_AGI_CONFIG__ = ${JSON.stringify({
          VITE_HOST_API: auth.apiBase,
          VITE_ASSETS_API: APP_BASE,
        })};`,
      });
      return;
    }
    request.continue();
  });
}

async function installBrowserState(page, auth) {
  await page.evaluateOnNewDocument(() => {
    window.normalizeText = (value) => String(value || "").trim();
    window.dispatchClick = (element) => {
      element.click();
    };
    window.visibleElements = (selector = "body *") => {
      const isVisible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      };
      return Array.from(document.querySelectorAll(selector)).filter(isVisible);
    };
    window.setNativeValue = (element, value) => {
      const prototype =
        element instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
      descriptor.set.call(element, value);
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
    };
  });
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      if (organizationId)
        sessionStorage.setItem("organizationId", organizationId);
      if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
      if (user?.id)
        sessionStorage.setItem("futureagi-current-user-id", user.id);
    },
    {
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );
}

async function waitForResponsesDuring(page, label, predicates, action) {
  try {
    return await Promise.all([
      ...predicates.map((predicate) =>
        page.waitForResponse(predicate, { timeout: 60000 }),
      ),
      action(),
    ]);
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
}

async function waitForPath(page, pathname, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname === expectedPath,
    { timeout },
    pathname,
  );
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) =>
      window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      }),
    { timeout },
    { text, exact },
  );
}

async function waitForTextGone(page, text, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (expectedText) =>
      !window
        .visibleElements()
        .some(
          (element) =>
            window.normalizeText(element.textContent) === expectedText,
        ),
    { timeout },
    text,
  );
}

async function typeIntoPlaceholder(page, placeholder, value) {
  await page.waitForFunction(
    (expectedPlaceholder) =>
      window
        .visibleElements("input,textarea")
        .some((element) => element.placeholder === expectedPlaceholder),
    { timeout: 30000 },
    placeholder,
  );
  const typed = await page.evaluate(
    ({ expectedPlaceholder, text }) => {
      const element = window
        .visibleElements("input,textarea")
        .find((input) => input.placeholder === expectedPlaceholder);
      if (!element || element.disabled) return false;
      element.focus();
      window.setNativeValue(element, text);
      return true;
    },
    { expectedPlaceholder: placeholder, text: value },
  );
  assert(typed, `Could not type into placeholder: ${placeholder}`);
}

async function clickVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await waitForVisibleText(page, text, { exact, timeout });
  const point = await page.evaluate(
    ({ text: expectedText, exact: exactMatch }) => {
      const elements = window.visibleElements().filter((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
      const element =
        elements.find((candidate) => {
          const button = candidate.closest("button");
          return button && !button.disabled;
        }) ||
        elements.find((candidate) => candidate.closest("a,[role='button']")) ||
        elements[0];
      const clickable =
        element?.closest("button,a,[role='button'],[role='menuitem'],tr") ||
        element;
      if (!clickable || clickable.disabled) return false;
      const rect = clickable.getBoundingClientRect();
      return {
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
      };
    },
    { text, exact },
  );
  assert(point, `Could not click visible text: ${text}`);
  await page.mouse.click(point.x, point.y);
}

async function clickDialogButton(page, label, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
  const point = await page.evaluate((expectedLabel) => {
    const dialog = window.visibleElements("[role='dialog']").at(-1);
    if (!dialog) return false;
    const button = Array.from(dialog.querySelectorAll("button")).find(
      (candidate) =>
        window.normalizeText(candidate.textContent) === expectedLabel &&
        !candidate.disabled,
    );
    if (!button) return false;
    const rect = button.getBoundingClientRect();
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
  }, label);
  assert(point, `Could not click dialog button: ${label}`);
  await page.mouse.click(point.x, point.y);
}

async function clickRowAction(page, rowText, actionIndex) {
  await waitForVisibleText(page, rowText);
  const point = await page.evaluate(
    ({ expectedText, index }) => {
      const row = window
        .visibleElements("tr")
        .find((candidate) =>
          window.normalizeText(candidate.textContent).includes(expectedText),
        );
      if (!row) return false;
      const buttons = Array.from(row.querySelectorAll("button")).filter(
        (button) => !button.disabled,
      );
      const button = buttons[index];
      if (!button) return false;
      const rect = button.getBoundingClientRect();
      return {
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
      };
    },
    { expectedText: rowText, index: actionIndex },
  );
  assert(point, `Could not click row action ${actionIndex} for ${rowText}`);
  await page.mouse.click(point.x, point.y);
}

async function responseResult(response) {
  const data = await response.json();
  return data?.result ?? data;
}

function gatewayListResponse() {
  return (response) =>
    response.url().includes("/agentcc/gateways/") &&
    !response.url().includes("/config/") &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function gatewayConfigResponse(gatewayId = "") {
  return (response) =>
    response.url().includes("/agentcc/gateways/") &&
    response.url().includes("/config/") &&
    (!gatewayId ||
      response.url().includes(`/agentcc/gateways/${gatewayId}/`)) &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function emailAlertsResponse() {
  return (response) =>
    response.url().includes("/agentcc/email-alerts/") &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function healthCheckResponse(gatewayId) {
  return (response) =>
    response.url().includes(`/agentcc/gateways/${gatewayId}/health_check/`) &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function reloadConfigResponse(gatewayId) {
  return (response) =>
    response.url().includes(`/agentcc/gateways/${gatewayId}/reload/`) &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function orgConfigCreateResponse() {
  return (response) =>
    response.url().includes("/agentcc/org-configs/") &&
    !response.url().includes("/active/") &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function orgConfigActiveResponse() {
  return (response) =>
    response.url().includes("/agentcc/org-configs/active/") &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function submitBatchResponseFor(gatewayId) {
  return (response) =>
    response.url().includes(`/agentcc/gateways/${gatewayId}/submit-batch/`) &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function isGatewayApiUrl(url) {
  return url.includes("/agentcc/");
}

function isAllowedSettingsMutation(method, url) {
  return (
    method === "POST" &&
    (url.includes("/health_check/") ||
      url.includes("/reload/") ||
      (url.includes("/agentcc/org-configs/") && !url.includes("/activate/")) ||
      url.includes("/submit-batch/"))
  );
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  }
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  if (process.platform === "linux") {
    return "/usr/bin/google-chrome";
  }
  return undefined;
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
