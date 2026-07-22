/* eslint-disable no-console */
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/gateway-settings-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/gateway-settings-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);
const ALERT_PREFIX = "Browser Settings Smoke";

async function main() {
  const auth = await createAuthenticatedContext();
  const apiFailures = [];
  const pageErrors = [];
  const gatewayRequests = [];
  const browserMutations = [];
  const cleanupActions = [];
  let browser = null;
  let page = null;
  let caughtError = null;
  let cleanupError = null;
  let evidence = {};

  try {
    const baseline = await prepareOrgConfigRestorer(auth.client);
    cleanupActions.push({
      label: "restore org config",
      fn: baseline.cleanup,
    });
    evidence = await preflightSettings(
      auth.client,
      baseline,
      auth.runId,
      cleanupActions,
    );

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
        browserMutations.push(`${request.method()} ${url}`);
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (!isGatewayApiUrl(url) || response.status() < 400) return;
      apiFailures.push(`${response.status()} ${url}`);
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponsesDuring(
      page,
      "initial Gateway Settings load",
      [
        (response) =>
          response.url().includes("/agentcc/gateways/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/agentcc/gateways/") &&
          response.url().includes("/config/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/agentcc/email-alerts/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
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
      "Server Configuration",
      "Gateway Info",
      "Logging",
      "Cost Tracking",
      "Email Alerts",
      "Add Alert",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    if (evidence.alert_id) {
      for (const label of [
        evidence.alert_name,
        "ops@example.com",
        "2 events",
        "smtp",
      ]) {
        await waitForVisibleText(page, label, { exact: true });
      }
    } else {
      await waitForVisibleText(page, "No email alerts configured", {
        exact: true,
      });
      await waitForVisibleText(
        page,
        "Set up email alerts to get notified about budget overages",
      );
    }

    await clickVisibleText(page, "Add Alert", { exact: true });
    for (const label of [
      "New Email Alert",
      "Alert Name",
      "Recipients",
      "Alert Events",
      "Budget Exceeded",
      "Email Provider",
      "API Key",
      "From Email",
      "Cooldown (minutes)",
      "Alert is active",
      "Create",
    ]) {
      await waitForVisibleText(page, label);
    }
    await clickVisibleText(page, "Cancel", { exact: true });
    await waitForTextGone(page, "New Email Alert");

    if (evidence.alert_id) {
      await clickRowAction(page, evidence.alert_name, 0);
      await waitForVisibleText(page, "Edit Email Alert", { exact: true });
      for (const label of [
        "Send Test",
        "Update",
        "SMTP Host",
        "SMTP Port",
        "Username",
        "Password",
      ]) {
        await waitForVisibleText(page, label);
      }
      for (const value of [
        evidence.alert_name,
        "smtp.example.com",
        "2525",
        "ops-user",
        "alerts@example.com",
        "15",
      ]) {
        await waitForVisibleValue(page, value);
      }
      await clickVisibleText(page, "Cancel", { exact: true });
      await waitForTextGone(page, "Edit Email Alert");
    }

    await clickVisibleText(page, "Org Config", { exact: true });
    await waitForPath(page, "/dashboard/gateway/settings/org-config");
    for (const label of [
      "Organization Config",
      `Version ${evidence.config_version}`,
      "Custom Config Active",
      "View History",
      "Edit Config",
      "Providers",
      "Guardrail Checks",
      "Routing Strategy",
      "Cache",
      "latency based",
      "memory",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    await clickVisibleText(page, "View History", { exact: true });
    await waitForVisibleText(page, "Config History", { exact: true });
    await waitForVisibleText(page, `v${evidence.config_version}`, {
      exact: true,
    });
    await waitForVisibleText(page, "Active", { exact: true });
    await clickVisibleText(page, "View", { exact: true });
    await waitForVisibleText(page, `Config v${evidence.config_version}`, {
      exact: true,
    });
    await waitForVisibleText(page, "latency_based");
    await clickVisibleText(page, "Close", { exact: true });
    await waitForTextGone(page, `Config v${evidence.config_version}`);
    await clickDrawerClose(page);
    await waitForTextGone(page, "Config History");

    await clickVisibleText(page, "Edit Config", { exact: true });
    for (const label of [
      "Edit Organization Config",
      "Providers",
      "Guardrails",
      "Routing",
      "Cache",
      "Rate Limiting",
      "Budgets",
      "Security",
      "Save & Activate",
    ]) {
      await waitForVisibleText(page, label);
    }
    await waitForVisiblePlaceholder(page, "Change description (optional)");
    await clickVisibleText(page, "Cancel", { exact: true });
    await waitForTextGone(page, "Edit Organization Config");

    await clickVisibleText(page, "Batch Jobs", { exact: true });
    await waitForPath(page, "/dashboard/gateway/settings/batch-jobs");
    for (const label of [
      "Batch API",
      "Submit Batch Job",
      "Batch Jobs",
      'No batch jobs submitted yet. Click "Submit Batch Job" to get started.',
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    await clickVisibleText(page, "Submit Batch Job", { exact: true });
    for (const label of [
      "Requests (JSON array of chat completion request objects)",
      "Max Concurrency: 5",
      "Each request is sent through the gateway pipeline",
      "Submit",
    ]) {
      await waitForVisibleText(page, label);
    }
    await clickVisibleText(page, "Cancel", { exact: true });
    await waitForTextGone(
      page,
      "Requests (JSON array of chat completion request objects)",
    );

    await clickVisibleText(page, "Full Config (Read-Only)", { exact: true });
    await waitForPath(page, "/dashboard/gateway/settings/full-config");
    await waitForVisibleText(page, "Full Gateway Configuration", {
      exact: true,
    });
    await waitForVisiblePlaceholder(page, "Search config...");
    await typeIntoPlaceholder(page, "Search config...", evidence.marker);
    await waitForVisibleText(page, "1 match", { exact: true });

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      browserMutations.length === 0,
      `Unexpected browser settings mutations: ${browserMutations.join("; ")}`,
    );
  } catch (error) {
    caughtError = error;
    await page
      ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
  } finally {
    if (browser) await browser.close();
    try {
      evidence.cleanup = await runCleanup(cleanupActions);
    } catch (error) {
      cleanupError = error;
      evidence.cleanup = { status: "failed", error: error.message };
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

async function preflightSettings(client, baseline, runId, cleanupActions) {
  const gateways = asArray(await client.get(apiPath("/agentcc/gateways/")));
  assert(gateways.length > 0, "Gateway list returned no configured gateway.");
  const gatewayId =
    gateways.find((gateway) => gateway.id === "default")?.id ||
    gateways[0].id ||
    "default";

  const suffix = String(runId || Date.now().toString(36)).replace(
    /[^a-z0-9]/gi,
    "_",
  );
  const marker = `browser_settings_marker_${suffix}`;
  const alertName = `${ALERT_PREFIX} ${suffix}`;
  const staleAlertIds = await deleteStaleBrowserAlerts(client);

  const updateResult = await client.post(
    apiPath("/agentcc/gateways/{id}/update-config/", { id: gatewayId }),
    {
      routing: { strategy: "latency_based" },
      cache: { enabled: true, backend: "memory", default_ttl: 300 },
      cost_tracking: { enabled: true },
      audit: {
        enabled: true,
        min_severity: "info",
        categories: [marker],
        sinks: [{ type: "stdout", level: "info" }],
      },
    },
  );
  assert(
    updateResult?.version,
    "Gateway update-config did not return a config version.",
  );

  const gatewayConfig = await client.get(
    apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
  );
  assert(
    gatewayConfig.routing?.strategy === "latency_based" &&
      gatewayConfig.cache?.enabled === true &&
      gatewayConfig.cache?.backend === "memory" &&
      gatewayConfig.cost_tracking?.enabled === true &&
      asArray(gatewayConfig.audit?.categories).includes(marker),
    "Gateway config did not expose the disposable Settings config values.",
  );

  let alert = null;
  let emailAlertMode = "created";
  let emailAlertCreateError = null;
  try {
    alert = await client.post(apiPath("/agentcc/email-alerts/"), {
      name: alertName,
      recipients: ["ops@example.com"],
      events: ["budget.exceeded", "error.occurred"],
      provider: "smtp",
      provider_config: {
        host: "smtp.example.com",
        port: 2525,
        username: "ops-user",
        password: "browser-smoke-password",
        from_email: "alerts@example.com",
        use_tls: true,
      },
      cooldown_minutes: 15,
      is_active: false,
    });
    assert(alert?.id, "Email alert create did not return an alert id.");
    cleanupActions.push({
      label: `delete email alert ${alert.id}`,
      fn: () =>
        ignoreMissing(() =>
          client.delete(
            apiPath("/agentcc/email-alerts/{id}/", { id: alert.id }),
          ),
        ),
    });

    const alertList = asArray(
      await client.get(apiPath("/agentcc/email-alerts/")),
    );
    const createdAlert = alertList.find((item) => item.id === alert.id);
    assert(
      createdAlert?.name === alertName &&
        createdAlert.provider === "smtp" &&
        createdAlert.cooldown_minutes === 15 &&
        createdAlert.is_active === false,
      "Email alert list did not expose the disposable alert.",
    );
  } catch (error) {
    if (error?.status !== 402) throw error;
    emailAlertMode = "entitlement_denied_empty_state";
    emailAlertCreateError = String(
      error?.body?.message || error?.message || "",
    );
  }

  return {
    gateway_id: gatewayId,
    original_org_config_id: baseline.originalActiveConfig.id,
    original_org_config_version: baseline.originalActiveConfig.version,
    config_version: updateResult.version,
    config_gateway_synced: updateResult.gateway_synced,
    marker,
    email_alert_mode: emailAlertMode,
    email_alert_create_error: emailAlertCreateError,
    alert_id: alert?.id || null,
    alert_name: alertName,
    stale_alert_ids: staleAlertIds,
  };
}

async function deleteStaleBrowserAlerts(client) {
  const deleted = [];
  const alerts = asArray(await client.get(apiPath("/agentcc/email-alerts/")));
  for (const alert of alerts) {
    if (!String(alert?.name || "").startsWith(ALERT_PREFIX)) continue;
    await ignoreMissing(() =>
      client.delete(apiPath("/agentcc/email-alerts/{id}/", { id: alert.id })),
    );
    deleted.push(alert.id);
  }
  return deleted;
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
      await ignoreMissing(() =>
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

async function runCleanup(cleanupActions) {
  const results = [];
  const failures = [];
  for (const action of cleanupActions.reverse()) {
    try {
      const result = await action.fn();
      results.push({ label: action.label, status: "passed", result });
    } catch (error) {
      failures.push({ label: action.label, error: error.message });
      results.push({
        label: action.label,
        status: "failed",
        error: error.message,
      });
    }
  }
  if (failures.length) {
    throw new Error(
      `Cleanup failed: ${failures
        .map((failure) => `${failure.label}: ${failure.error}`)
        .join("; ")}`,
    );
  }
  return { status: "passed", actions: results };
}

async function ignoreMissing(fn) {
  try {
    return await fn();
  } catch (error) {
    const message = String(error?.message || "").toLowerCase();
    if (
      error?.status === 404 ||
      message.includes("not found") ||
      message.includes("does not exist") ||
      message.includes("matches the given query") ||
      message.includes("no agentccemailalert")
    ) {
      return null;
    }
    throw error;
  }
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
      element.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
      );
      element.dispatchEvent(
        new MouseEvent("mouseup", { bubbles: true, cancelable: true }),
      );
      element.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
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

async function waitForVisibleValue(page, value, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (expectedValue) =>
      window.visibleElements().some((element) => {
        if (
          element instanceof HTMLInputElement &&
          element.value === expectedValue
        ) {
          return true;
        }

        const input = element.querySelector?.("input");
        return input?.value === expectedValue;
      }),
    { timeout },
    value,
  );
}

async function waitForVisiblePlaceholder(
  page,
  placeholder,
  { timeout = 30000 } = {},
) {
  await page.waitForFunction(
    (expectedPlaceholder) =>
      window
        .visibleElements("input,textarea")
        .some((element) => element.placeholder === expectedPlaceholder),
    { timeout },
    placeholder,
  );
}

async function typeIntoPlaceholder(page, placeholder, value) {
  await waitForVisiblePlaceholder(page, placeholder);
  const typed = await page.evaluate(
    ({ expectedPlaceholder, text }) => {
      const element = window
        .visibleElements("input,textarea")
        .find((input) => input.placeholder === expectedPlaceholder);
      if (!element) return false;
      element.focus();
      const valueSetter = Object.getOwnPropertyDescriptor(
        element instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype,
        "value",
      ).set;
      valueSetter.call(element, text);
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
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
  const clicked = await page.evaluate(
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
      window.dispatchClick(clickable);
      return true;
    },
    { text, exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

async function clickRowAction(page, rowText, actionIndex) {
  await waitForVisibleText(page, rowText, { exact: true });
  const clicked = await page.evaluate(
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
      window.dispatchClick(button);
      return true;
    },
    { expectedText: rowText, index: actionIndex },
  );
  assert(clicked, `Could not click row action ${actionIndex} for ${rowText}`);
}

async function clickDrawerClose(page) {
  const clicked = await page.evaluate(() => {
    const drawer = window
      .visibleElements('[class*="MuiDrawer-paper"]')
      .find((element) => element.querySelector("button"));
    const button = drawer?.querySelector("button");
    if (!button || button.disabled) return false;
    window.dispatchClick(button);
    return true;
  });
  assert(clicked, "Could not click drawer close button");
}

function isGatewayApiUrl(url) {
  return url.includes("/agentcc/");
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
