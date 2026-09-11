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
const SCREENSHOT_PATH = "/tmp/gateway-monitoring-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/gateway-monitoring-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  const auth = await createAuthenticatedContext();
  const apiFailures = [];
  const pageErrors = [];
  const gatewayRequests = [];
  const browserMutations = [];
  let browser = null;
  let page = null;
  let caughtError = null;
  let cleanupError = null;
  let cleanup = null;
  let cleanupEvidence = null;
  let evidence = {};

  try {
    const baseline = await prepareOrgConfigRestorer(auth.client);
    cleanup = baseline.cleanup;
    evidence = await preflightMonitoring(auth.client, auth.runId, baseline);

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
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
      "initial Gateway monitoring load",
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
          response.url().includes("/agentcc/gateways/") &&
          response.url().includes("/providers/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/agentcc/analytics/overview/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway/monitoring`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway/monitoring");

    for (const label of [
      "Monitoring",
      "Configure alert rules and notification channels",
      "Create Rule",
      "Add Channel",
      "Live Metrics",
      "Alert Rules",
      "Channels",
      "Request Rate",
      "Error Rate",
      "Avg Latency",
      "Total Cost",
      "Provider Status",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    if (evidence.sample_provider_name) {
      await waitForVisibleText(page, evidence.sample_provider_name, {
        exact: true,
      });
    } else {
      await waitForVisibleText(page, "No provider status data available.", {
        exact: true,
      });
    }

    await clickVisibleText(page, "Create Rule", { exact: true });
    for (const label of [
      "Create Alert Rule",
      "Rule Name",
      "Metric",
      "Condition",
      "Threshold",
      "Window",
      "Severity",
      "Cancel",
    ]) {
      await waitForVisibleText(page, label);
    }
    await clickDialogButton(page, "Cancel");
    await waitForNoVisibleText(page, "Rule Name", { exact: true });

    await clickVisibleText(page, "Alert Rules", { exact: true });
    await waitForPath(page, "/dashboard/gateway/monitoring/rules");
    for (const label of [
      "Rule",
      "Metric",
      "Condition",
      "Threshold",
      "Window",
      "Severity",
      "Status",
      evidence.rule_name,
      "error_rate",
      ">=",
      "99.9",
      "15m",
      "warning",
      "Disabled",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    await clickVisibleText(page, "Add Channel", { exact: true });
    for (const label of [
      "Add Notification Channel",
      "Channel Name",
      "Type",
      "URL / Endpoint",
      "Cancel",
    ]) {
      await waitForVisibleText(page, label);
    }
    await clickDialogButton(page, "Cancel");
    await waitForNoVisibleText(page, "Channel Name", { exact: true });

    await clickVisibleText(page, "Channels", { exact: true });
    await waitForPath(page, "/dashboard/gateway/monitoring/channels");
    for (const label of [
      evidence.channel_name,
      "webhook",
      "Disabled",
      "Endpoint",
      "Severity Filter",
      "warning",
      "Description",
      evidence.channel_description,
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    await waitForVisibleText(page, "https://example.com/");

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      browserMutations.length === 0,
      `Unexpected browser monitoring mutations: ${browserMutations.join("; ")}`,
    );
  } catch (error) {
    caughtError = error;
    await page
      ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
  } finally {
    if (browser) await browser.close();
    if (cleanup) {
      try {
        cleanupEvidence = await cleanup();
        evidence.cleanup = cleanupEvidence;
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

async function preflightMonitoring(client, runId, baseline) {
  const gateways = asArray(await client.get(apiPath("/agentcc/gateways/")));
  assert(gateways.length > 0, "Gateway list returned no configured gateway.");
  const gatewayId =
    gateways.find((gateway) => gateway.id === "default")?.id ||
    gateways[0].id ||
    "default";

  const suffix = String(runId || Date.now())
    .replace(/[^a-z0-9]/gi, "_")
    .toLowerCase();
  const ruleName = `browser_smoke_monitoring_rule_${suffix}`;
  const channelName = `browser_smoke_monitoring_channel_${suffix}`;
  const channelDescription = `Browser smoke disabled channel ${runId}`;
  const ruleDescription = `Browser smoke disabled rule ${runId}`;

  const updateResult = await client.post(
    apiPath("/agentcc/gateways/{id}/update-config/", { id: gatewayId }),
    {
      alerting: {
        rules: {
          [ruleName]: {
            name: ruleName,
            metric: "error_rate",
            condition: ">=",
            threshold: 99.9,
            window: "15m",
            severity: "warning",
            enabled: false,
            channels: [channelName],
            description: ruleDescription,
          },
        },
        channels: {
          [channelName]: {
            name: channelName,
            type: "webhook",
            url: "https://example.com/futureagi-monitoring-smoke",
            enabled: false,
            severity_filter: "warning",
            description: channelDescription,
          },
        },
      },
    },
  );

  const gatewayConfig = await client.get(
    apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
  );
  const savedRules = extractAlertRules(gatewayConfig);
  const savedChannels = extractChannels(gatewayConfig);
  const savedRule = savedRules.find((rule) => rule.name === ruleName);
  const savedChannel = savedChannels.find(
    (channel) => channel.name === channelName,
  );
  assert(
    savedRule?.enabled === false && savedRule?.threshold === 99.9,
    "Gateway config did not expose the disabled smoke alert rule.",
  );
  assert(
    savedChannel?.enabled === false &&
      savedChannel?.url === "https://example.com/futureagi-monitoring-smoke",
    "Gateway config did not expose the disabled smoke channel.",
  );

  const end = new Date().toISOString();
  const start = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
  const [health, providerHealth, overview] = await Promise.all([
    client.post(
      apiPath("/agentcc/gateways/{id}/health_check/", { id: gatewayId }),
    ),
    client.get(apiPath("/agentcc/gateways/{id}/providers/", { id: gatewayId })),
    client.get(apiPath("/agentcc/analytics/overview/"), {
      query: { gateway_id: gatewayId, start, end },
    }),
  ]);
  const providerList = normalizeProviderList(providerHealth?.providers);

  return {
    gateway_id: gatewayId,
    original_org_config_id: baseline.originalActiveConfig.id,
    original_org_config_version: baseline.originalActiveConfig.version,
    update_config_version: updateResult.version,
    update_config_gateway_synced: updateResult.gateway_synced,
    rule_name: ruleName,
    rule_description: ruleDescription,
    channel_name: channelName,
    channel_description: channelDescription,
    health_status: health?.status,
    health_provider_count: health?.provider_count,
    health_model_count: health?.model_count,
    provider_count: providerList.length,
    sample_provider_name: providerList[0]?.name || null,
    analytics_total_requests: unwrapAnalyticsValue(overview?.total_requests),
    analytics_error_rate: unwrapAnalyticsValue(overview?.error_rate),
    analytics_avg_latency_ms: unwrapAnalyticsValue(overview?.avg_latency_ms),
    analytics_total_cost: unwrapAnalyticsValue(overview?.total_cost),
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

function extractAlertRules(config) {
  const alerting = config?.alerting || config?.alerts || {};
  const rules = alerting.rules || alerting.alert_rules || [];
  if (Array.isArray(rules)) return rules;
  if (rules && typeof rules === "object") {
    return Object.entries(rules).map(([name, cfg]) => ({
      name,
      ...(cfg && typeof cfg === "object" ? cfg : {}),
    }));
  }
  return [];
}

function extractChannels(config) {
  const alerting = config?.alerting || config?.alerts || {};
  const channels = alerting.channels || alerting.notification_channels || [];
  if (Array.isArray(channels)) return channels;
  if (channels && typeof channels === "object") {
    return Object.entries(channels).map(([name, cfg]) => ({
      name,
      ...(cfg && typeof cfg === "object" ? cfg : {}),
    }));
  }
  return [];
}

function normalizeProviderList(providers) {
  if (Array.isArray(providers)) {
    return providers.map((provider) => ({
      ...provider,
      name: provider.name || provider.provider_name || provider.id,
    }));
  }
  if (providers && typeof providers === "object") {
    return Object.entries(providers).map(([name, info]) => ({
      name,
      ...(info && typeof info === "object" ? info : {}),
    }));
  }
  return [];
}

function unwrapAnalyticsValue(value) {
  if (value && typeof value === "object" && "value" in value) {
    return value.value;
  }
  return value;
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

async function waitForResponseDuring(page, label, predicate, action) {
  try {
    const [response] = await Promise.all([
      page.waitForResponse(predicate, { timeout: 60000 }),
      action(),
    ]);
    return response;
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
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

async function waitForNoVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) =>
      !window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      }),
    { timeout },
    { text, exact },
  );
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

async function clickDialogButton(page, label, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
  const clicked = await page.evaluate((expectedLabel) => {
    const dialog = window.visibleElements("[role='dialog']").at(-1);
    if (!dialog) return false;
    const button = Array.from(dialog.querySelectorAll("button")).find(
      (candidate) =>
        window.normalizeText(candidate.textContent) === expectedLabel &&
        !candidate.disabled,
    );
    if (!button) return false;
    window.dispatchClick(button);
    return true;
  }, label);
  assert(clicked, `Could not click dialog button: ${label}`);
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
