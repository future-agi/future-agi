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
const SCREENSHOT_PATH = "/tmp/gateway-mcp-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/gateway-mcp-smoke-failure.png";
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
  let evidence = {};

  try {
    const baseline = await prepareOrgConfigRestorer(auth.client);
    cleanup = baseline.cleanup;
    evidence = await preflightMCP(auth.client, baseline, auth.runId);

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
      "initial Gateway MCP load",
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
          response.url().includes("/mcp-status/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/agentcc/gateways/") &&
          response.url().includes("/mcp-tools/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/agentcc/gateways/") &&
          response.url().includes("/mcp-resources/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/agentcc/gateways/") &&
          response.url().includes("/mcp-prompts/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway/mcp`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway/mcp");

    for (const label of [
      "MCP Tools",
      "Manage Model Context Protocol servers, tools, and guardrails",
      "Add Server",
      "Reload Config",
      "Overview",
      "Tools",
      "Servers",
      "Resources",
      "Prompts",
      "Guardrails",
      "Playground",
      "MCP Status",
      "Enabled",
      "Active Sessions",
      "Total Tools",
      "Connected Servers",
      "Server Health",
      "Server ID",
      "Status",
      "Tool Count",
      evidence.server_id,
      "Configured",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    await clickVisibleText(page, "Servers", { exact: true });
    await waitForPath(page, "/dashboard/gateway/mcp/servers");
    for (const label of [
      evidence.server_id,
      "Configured",
      "URL",
      evidence.server_url,
      "HTTP",
      "Auth: none",
      "0 tools",
      "Cache TTL: 5m",
      "Edit",
      "Delete",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    await clickVisibleText(page, "Edit", { exact: true });
    for (const label of [
      "Edit MCP Server",
      "Server ID",
      "Transport",
      "URL",
      "Auth Type",
      "Tools Cache TTL",
      "Cancel",
      "Update Server",
    ]) {
      await waitForVisibleText(page, label);
    }
    await waitForVisibleValue(page, evidence.server_id);
    await waitForVisibleValue(page, evidence.server_url);
    await waitForVisibleValue(page, "5m");
    await clickVisibleText(page, "Cancel", { exact: true });
    await waitForTextGone(page, "Edit MCP Server");

    await clickVisibleText(page, "Tools", { exact: true });
    await waitForPath(page, "/dashboard/gateway/mcp/tools");
    for (const label of [
      "Server",
      "All Servers",
      "No MCP tools registered. Connect an MCP server to discover tools.",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    await waitForVisiblePlaceholder(page, "Search tools...");

    await clickVisibleText(page, "Resources", { exact: true });
    await waitForPath(page, "/dashboard/gateway/mcp/resources");
    await waitForVisiblePlaceholder(page, "Search resources...");
    await waitForVisibleText(
      page,
      "No MCP resources registered. Upstream servers may not expose resources.",
      { exact: true },
    );

    await clickVisibleText(page, "Prompts", { exact: true });
    await waitForPath(page, "/dashboard/gateway/mcp/prompts");
    await waitForVisiblePlaceholder(page, "Search prompts...");
    await waitForVisibleText(
      page,
      "No MCP prompts registered. Upstream servers may not expose prompts.",
      { exact: true },
    );

    await clickVisibleText(page, "Guardrails", { exact: true });
    await waitForPath(page, "/dashboard/gateway/mcp/guardrails");
    for (const label of [
      "General Settings",
      "Enable MCP Guardrails",
      "Validate tool inputs (check for injection patterns)",
      "Validate tool outputs",
      "Blocked Tools",
      'Tools in this list will be blocked from execution. Use the namespaced format (e.g., "server_toolname").',
      evidence.blocked_tool,
      "Allowed Servers",
      evidence.server_id,
      "Custom Injection Patterns",
      evidence.custom_pattern,
      "Per-Tool Rate Limits",
      evidence.rate_limit_tool,
      "7/min",
      "Save Guardrails",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    await clickVisibleText(page, "Playground", { exact: true });
    await waitForPath(page, "/dashboard/gateway/mcp/playground");
    await waitForVisibleText(page, "Tool Selection", { exact: true });
    await waitForVisiblePlaceholder(page, "Select a tool to test...");
    await waitForVisibleText(
      page,
      "Select a tool above to test it. Arguments will be pre-filled from the tool's input schema.",
      { exact: true },
    );

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      browserMutations.length === 0,
      `Unexpected browser MCP mutations: ${browserMutations.join("; ")}`,
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

async function preflightMCP(client, baseline, runId) {
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
  const serverId = `browser_smoke_mcp_${suffix}`;
  const serverUrl = "https://example.com/futureagi-browser-mcp";
  const blockedTool = `dangerous_${serverId}`;
  const rateLimitTool = `${serverId}_lookup`;
  const customPattern = "(?i)secret";

  const mcpServer = await client.post(
    apiPath("/agentcc/gateways/{id}/update-mcp-server/", { id: gatewayId }),
    {
      server_id: serverId,
      config: {
        name: serverId,
        url: serverUrl,
        transport: "http",
        enabled: false,
        timeout_seconds: 3,
        tools_cache_ttl: "5m",
      },
    },
  );
  assert(
    mcpServer?.action === "updated" && mcpServer.server === serverId,
    "Gateway MCP server update did not echo the server/action.",
  );

  const mcpGuardrails = await client.post(
    apiPath("/agentcc/gateways/{id}/update-mcp-guardrails/", { id: gatewayId }),
    {
      config: {
        enabled: true,
        mode: "monitor",
        blocked_tools: [blockedTool],
        allowed_servers: [serverId],
        validate_inputs: true,
        validate_outputs: false,
        custom_patterns: [customPattern],
        tool_rate_limits: {
          [rateLimitTool]: 7,
        },
      },
    },
  );
  assert(
    mcpGuardrails?.action === "updated",
    "Gateway MCP guardrail update did not return updated action.",
  );

  const gatewayConfig = await client.get(
    apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
  );
  assert(
    gatewayConfig.mcp?.servers?.[serverId]?.url === serverUrl &&
      gatewayConfig.mcp?.servers?.[serverId]?.tools_cache_ttl === "5m",
    "Gateway config did not expose the disposable MCP server.",
  );
  assert(
    gatewayConfig.mcp?.guardrails?.enabled === true &&
      asArray(gatewayConfig.mcp?.guardrails?.blocked_tools).includes(
        blockedTool,
      ) &&
      asArray(gatewayConfig.mcp?.guardrails?.allowed_servers).includes(
        serverId,
      ) &&
      gatewayConfig.mcp?.guardrails?.tool_rate_limits?.[rateLimitTool] === 7,
    "Gateway config did not expose disposable MCP guardrail settings.",
  );

  const mcpStatus = await client.get(
    apiPath("/agentcc/gateways/{id}/mcp-status/", { id: gatewayId }),
  );
  const statusServers = asArray(mcpStatus?.servers);
  assert(
    statusServers.some((server) => mcpStatusServerId(server) === serverId),
    "Gateway MCP status did not include the disposable server.",
  );

  const mcpTools = asArray(
    await client.get(
      apiPath("/agentcc/gateways/{id}/mcp-tools/", { id: gatewayId }),
    ),
  );
  const mcpResources = asArray(
    await client.get(
      apiPath("/agentcc/gateways/{id}/mcp-resources/", { id: gatewayId }),
    ),
  );
  const mcpPrompts = asArray(
    await client.get(
      apiPath("/agentcc/gateways/{id}/mcp-prompts/", { id: gatewayId }),
    ),
  );

  return {
    gateway_id: gatewayId,
    original_org_config_id: baseline.originalActiveConfig.id,
    original_org_config_version: baseline.originalActiveConfig.version,
    mcp_server_version: mcpServer.version,
    mcp_guardrails_version: mcpGuardrails.version,
    update_mcp_server_gateway_synced: mcpServer.gateway_synced,
    update_mcp_guardrails_gateway_synced: mcpGuardrails.gateway_synced,
    server_id: serverId,
    server_url: serverUrl,
    blocked_tool: blockedTool,
    rate_limit_tool: rateLimitTool,
    custom_pattern: customPattern,
    status_server_count: statusServers.length,
    tool_count: mcpTools.length,
    resource_count: mcpResources.length,
    prompt_count: mcpPrompts.length,
  };
}

function mcpStatusServerId(server) {
  return server?.server_id || server?.id || server?.name || "";
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
