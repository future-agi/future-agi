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
const ADD_SCREENSHOT_PATH = "/tmp/gateway-mcp-server-add-smoke.png";
const EDIT_SCREENSHOT_PATH = "/tmp/gateway-mcp-server-edit-smoke.png";
const GUARDRAILS_SCREENSHOT_PATH = "/tmp/gateway-mcp-guardrails-save-smoke.png";
const DELETE_SCREENSHOT_PATH = "/tmp/gateway-mcp-server-delete-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/gateway-mcp-mutation-smoke-failure.png";
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
        const mutation = `${request.method()} ${url}`;
        browserMutations.push(mutation);
        if (!isAllowedMCPMutation(request.method(), url)) {
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
      "initial Gateway MCP mutation load",
      [
        gatewayListResponse(),
        gatewayConfigResponse(evidence.gateway_id),
        mcpStatusResponse(evidence.gateway_id),
        mcpToolsResponse(evidence.gateway_id),
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway/mcp/servers`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway/mcp/servers");

    for (const label of [
      "MCP Tools",
      "Manage Model Context Protocol servers, tools, and guardrails",
      "Add Server",
      "Servers",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    await clickVisibleText(page, "Add Server", { exact: true });
    await waitForVisibleText(page, "Add MCP Server", { exact: true });
    await setInputByLabel(page, "Server ID", evidence.server_id);
    await setInputByLabel(page, "URL", evidence.server_url);
    await selectOptionByLabel(page, "Auth Type", "Bearer Token");
    await setInputByLabel(page, "Bearer Token", evidence.fake_token);
    await setInputByLabel(page, "Tools Cache TTL", evidence.add_cache_ttl);

    const [addServerResponse] = await waitForResponsesDuring(
      page,
      "add MCP server through browser",
      [updateMCPServerResponse(evidence.gateway_id), gatewayConfigResponse()],
      () => clickDialogButton(page, "Add Server"),
    );
    evidence.add_server_response = await responseResult(addServerResponse);
    await waitForTextGone(page, "Add MCP Server");

    const configAfterAdd = await auth.client.get(
      apiPath("/agentcc/gateways/{id}/config/", { id: evidence.gateway_id }),
    );
    const addedServer = configAfterAdd?.mcp?.servers?.[evidence.server_id];
    assert(
      addedServer?.url === evidence.server_url &&
        addedServer?.auth?.type === "bearer" &&
        addedServer?.tools_cache_ttl === evidence.add_cache_ttl,
      `MCP server add did not persist expected config: ${JSON.stringify(
        redactServerConfig(addedServer),
      )}`,
    );
    evidence.added_server = {
      server_id: evidence.server_id,
      url: addedServer.url,
      auth_type: addedServer.auth.type,
      tools_cache_ttl: addedServer.tools_cache_ttl,
    };

    for (const label of [
      evidence.server_id,
      "Configured",
      "URL",
      evidence.server_url,
      "HTTP",
      "Auth: bearer",
      `Cache TTL: ${evidence.add_cache_ttl}`,
      "Edit",
      "Delete",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    await page.screenshot({ path: ADD_SCREENSHOT_PATH, fullPage: true });
    evidence.add_screenshot = ADD_SCREENSHOT_PATH;

    await clickCardButton(page, evidence.server_id, "Edit");
    await waitForVisibleText(page, "Edit MCP Server", { exact: true });
    await waitForVisibleValue(page, evidence.server_id);
    await setInputByLabel(page, "URL", evidence.updated_server_url);
    await setInputByLabel(page, "Tools Cache TTL", evidence.edit_cache_ttl);

    const [editServerResponse] = await waitForResponsesDuring(
      page,
      "edit MCP server through browser",
      [updateMCPServerResponse(evidence.gateway_id), gatewayConfigResponse()],
      () => clickDialogButton(page, "Update Server"),
    );
    evidence.edit_server_response = await responseResult(editServerResponse);
    await waitForTextGone(page, "Edit MCP Server");

    const configAfterEdit = await auth.client.get(
      apiPath("/agentcc/gateways/{id}/config/", { id: evidence.gateway_id }),
    );
    const editedServer = configAfterEdit?.mcp?.servers?.[evidence.server_id];
    assert(
      editedServer?.url === evidence.updated_server_url &&
        editedServer?.auth?.type === "bearer" &&
        editedServer?.tools_cache_ttl === evidence.edit_cache_ttl,
      `MCP server edit did not persist expected config: ${JSON.stringify(
        redactServerConfig(editedServer),
      )}`,
    );
    evidence.edited_server = {
      server_id: evidence.server_id,
      url: editedServer.url,
      auth_type: editedServer.auth.type,
      tools_cache_ttl: editedServer.tools_cache_ttl,
    };
    await waitForVisibleText(page, evidence.updated_server_url, {
      exact: true,
    });
    await waitForVisibleText(page, `Cache TTL: ${evidence.edit_cache_ttl}`, {
      exact: true,
    });
    await page.screenshot({ path: EDIT_SCREENSHOT_PATH, fullPage: true });
    evidence.edit_screenshot = EDIT_SCREENSHOT_PATH;

    await clickVisibleText(page, "Guardrails", { exact: true });
    await waitForPath(page, "/dashboard/gateway/mcp/guardrails");
    await setSwitchByLabel(page, "Enable MCP Guardrails", true);
    await setSwitchByLabel(
      page,
      "Validate tool inputs (check for injection patterns)",
      true,
    );
    await setSwitchByLabel(page, "Validate tool outputs", true);
    await enterAutocompleteValue(
      page,
      "Type a tool name and press Enter...",
      evidence.blocked_tool,
    );
    await enterAutocompleteValue(
      page,
      "Type a server ID or select from connected...",
      evidence.server_id,
    );
    await enterAutocompleteValue(
      page,
      "Type a regex pattern and press Enter...",
      evidence.custom_pattern,
    );
    await setInputByLabel(page, "Tool Name", evidence.rate_limit_tool);
    await setInputByLabel(page, "Max/min", String(evidence.rate_limit));
    await clickVisibleText(page, "Add", { exact: true });
    await waitForVisibleText(page, `${evidence.rate_limit}/min`, {
      exact: true,
    });

    const [guardrailsResponse] = await waitForResponsesDuring(
      page,
      "save MCP guardrails through browser",
      [
        updateMCPGuardrailsResponse(evidence.gateway_id),
        gatewayConfigResponse(),
      ],
      () => clickVisibleText(page, "Save Changes", { exact: true }),
    );
    evidence.guardrails_response = await responseResult(guardrailsResponse);

    const configAfterGuardrails = await auth.client.get(
      apiPath("/agentcc/gateways/{id}/config/", { id: evidence.gateway_id }),
    );
    const guardrails = configAfterGuardrails?.mcp?.guardrails || {};
    assert(
      guardrails.enabled === true &&
        guardrails.validate_inputs === true &&
        guardrails.validate_outputs === true &&
        asArray(guardrails.blocked_tools).includes(evidence.blocked_tool) &&
        asArray(guardrails.allowed_servers).includes(evidence.server_id) &&
        asArray(guardrails.custom_patterns).includes(evidence.custom_pattern) &&
        guardrails.tool_rate_limits?.[evidence.rate_limit_tool] ===
          evidence.rate_limit,
      `MCP guardrails did not persist browser values: ${JSON.stringify(
        guardrails,
      )}`,
    );
    evidence.saved_guardrails = {
      enabled: guardrails.enabled,
      validate_inputs: guardrails.validate_inputs,
      validate_outputs: guardrails.validate_outputs,
      blocked_tools: guardrails.blocked_tools,
      allowed_servers: guardrails.allowed_servers,
      custom_patterns: guardrails.custom_patterns,
      tool_rate_limit: guardrails.tool_rate_limits[evidence.rate_limit_tool],
    };
    await waitForTextGone(page, "You have unsaved changes.");
    await page.screenshot({
      path: GUARDRAILS_SCREENSHOT_PATH,
      fullPage: true,
    });
    evidence.guardrails_screenshot = GUARDRAILS_SCREENSHOT_PATH;

    const tools = asArray(
      await auth.client.get(
        apiPath("/agentcc/gateways/{id}/mcp-tools/", {
          id: evidence.gateway_id,
        }),
      ),
    );
    evidence.tool_count = tools.length;
    evidence.test_tool_skipped_reason =
      tools.length === 0
        ? "No MCP tools registered by the local gateway, so browser Test Tool remains open."
        : "";

    await clickVisibleText(page, "Servers", { exact: true });
    await waitForPath(page, "/dashboard/gateway/mcp/servers");
    await clickCardButton(page, evidence.server_id, "Delete");
    await waitForVisibleText(page, "Remove MCP Server", { exact: true });

    const [deleteServerResponse] = await waitForResponsesDuring(
      page,
      "delete MCP server through browser",
      [removeMCPServerResponse(evidence.gateway_id), gatewayConfigResponse()],
      () => clickDialogButton(page, "Remove"),
    );
    evidence.delete_server_response =
      await responseResult(deleteServerResponse);
    await waitForTextGone(page, "Remove MCP Server");

    const configAfterDelete = await auth.client.get(
      apiPath("/agentcc/gateways/{id}/config/", { id: evidence.gateway_id }),
    );
    assert(
      !configAfterDelete?.mcp?.servers?.[evidence.server_id],
      `MCP server delete left server in config: ${JSON.stringify(
        configAfterDelete?.mcp?.servers?.[evidence.server_id],
      )}`,
    );
    evidence.deleted_server = {
      server_id: evidence.server_id,
      absent_from_config: true,
    };
    await waitForVisibleText(
      page,
      'No MCP servers configured. Click "Add Server" to connect an upstream MCP tool server.',
      { exact: true },
    );
    await page.screenshot({ path: DELETE_SCREENSHOT_PATH, fullPage: true });
    evidence.delete_screenshot = DELETE_SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Unexpected Gateway MCP browser mutations: ${unexpectedMutations.join(
        "; ",
      )}`,
    );
    assert(
      browserMutations.length === 4,
      `Expected four MCP browser mutations, saw ${browserMutations.length}: ${browserMutations.join(
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

async function preflightMCP(client, baseline, runId) {
  const gateways = asArray(await client.get(apiPath("/agentcc/gateways/")));
  assert(gateways.length > 0, "Gateway list returned no configured gateway.");
  const gatewayId =
    gateways.find((gateway) => gateway.id === "default")?.id ||
    gateways[0].id ||
    "default";
  const suffix = String(runId || Date.now())
    .replace(/[^a-z0-9]/gi, "_")
    .toLowerCase();
  const shortSuffix = suffix.slice(-8);

  return {
    gateway_id: gatewayId,
    original_org_config_id: baseline.originalActiveConfig.id,
    original_org_config_version: baseline.originalActiveConfig.version,
    server_id: `ui_mcp_server_${suffix}`,
    server_url: `https://example.com/futureagi-mcp-${shortSuffix}`,
    updated_server_url: `https://example.com/futureagi-mcp-${shortSuffix}-edited`,
    fake_token: `fake_mcp_token_${shortSuffix}`,
    add_cache_ttl: "11m",
    edit_cache_ttl: "17m",
    blocked_tool: `dangerous_ui_mcp_${shortSuffix}`,
    rate_limit_tool: `ui_mcp_lookup_${shortSuffix}`,
    custom_pattern: `(?i)ui_mcp_secret_${shortSuffix}`,
    rate_limit: 9,
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

async function setInputByLabel(page, label, value, timeout = 30000) {
  await waitForVisibleText(page, label, { timeout });
  const updated = await page.evaluate(
    ({ label: expectedLabel, value: nextValue }) => {
      const labels = window.visibleElements("label");
      const labelElement = labels.find((candidate) => {
        const text = window.normalizeText(candidate.textContent);
        return (
          text === expectedLabel ||
          text.replace(/\s*\*$/, "") === expectedLabel ||
          text.includes(expectedLabel)
        );
      });
      const formControl =
        labelElement?.closest(".MuiFormControl-root") ||
        labelElement?.parentElement;
      const input = formControl?.querySelector("input,textarea");
      if (!input || input.disabled) return false;
      input.focus();
      window.setNativeValue(input, nextValue);
      input.blur();
      return true;
    },
    { label, value },
  );
  assert(updated, `Could not set input: ${label}`);
}

async function setSwitchByLabel(page, label, checked, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
  const updated = await page.evaluate(
    ({ label: expectedLabel, checked: expectedChecked }) => {
      const labelElement = window
        .visibleElements("label")
        .find((candidate) =>
          window.normalizeText(candidate.textContent).includes(expectedLabel),
        );
      const checkbox = labelElement?.querySelector("input[type='checkbox']");
      if (!checkbox || checkbox.disabled) return false;
      if (checkbox.checked !== expectedChecked) {
        window.dispatchClick(labelElement);
      }
      return true;
    },
    { label, checked },
  );
  assert(updated, `Could not set switch: ${label}`);
}

async function selectOptionByLabel(page, label, optionText, timeout = 30000) {
  await waitForVisibleText(page, label, { timeout });
  const opened = await page.evaluate((expectedLabel) => {
    const labels = window.visibleElements("label");
    const labelElement = labels.find((candidate) => {
      const text = window.normalizeText(candidate.textContent);
      return (
        text === expectedLabel ||
        text.replace(/\s*\*$/, "") === expectedLabel ||
        text.includes(expectedLabel)
      );
    });
    const formControl =
      labelElement?.closest(".MuiFormControl-root") ||
      labelElement?.parentElement;
    const combo =
      formControl?.querySelector("[role='combobox']") ||
      formControl?.querySelector("input");
    if (!combo) return false;
    window.dispatchClick(combo);
    return true;
  }, label);
  assert(opened, `Could not open select: ${label}`);
  await page.waitForFunction(
    (expectedOption) =>
      window
        .visibleElements("[role='option'],li")
        .some(
          (element) =>
            window.normalizeText(element.textContent) === expectedOption,
        ),
    { timeout },
    optionText,
  );
  const selected = await page.evaluate((expectedOption) => {
    const option = window
      .visibleElements("[role='option'],li")
      .find(
        (element) =>
          window.normalizeText(element.textContent) === expectedOption,
      );
    if (!option) return false;
    window.dispatchClick(option);
    return true;
  }, optionText);
  assert(selected, `Could not select ${optionText} for ${label}`);
}

async function enterAutocompleteValue(page, placeholder, value) {
  await page.waitForFunction(
    (expectedPlaceholder) =>
      window
        .visibleElements("input")
        .some((input) => input.placeholder === expectedPlaceholder),
    { timeout: 30000 },
    placeholder,
  );
  const selector = `input[placeholder="${cssEscape(placeholder)}"]`;
  await page.focus(selector);
  await page.keyboard.type(value);
  await page.keyboard.press("Enter");
  await waitForVisibleText(page, value, { exact: true });
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

async function clickCardButton(page, cardText, buttonText, timeout = 30000) {
  await waitForVisibleText(page, cardText, { exact: true, timeout });
  const clicked = await page.evaluate(
    ({ cardText: expectedCardText, buttonText: expectedButtonText }) => {
      const card = window
        .visibleElements()
        .find(
          (element) =>
            window.normalizeText(element.textContent) === expectedCardText,
        )
        ?.closest(".MuiCard-root");
      if (!card) return false;
      const button = Array.from(card.querySelectorAll("button")).find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedButtonText &&
          !candidate.disabled,
      );
      if (!button) return false;
      window.dispatchClick(button);
      return true;
    },
    { cardText, buttonText },
  );
  assert(clicked, `Could not click ${buttonText} for card ${cardText}`);
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

async function responseResult(response) {
  const data = await response.json();
  return data?.result ?? data;
}

function redactServerConfig(config) {
  if (!config || typeof config !== "object") return config;
  return {
    ...config,
    auth: config.auth ? { ...config.auth, token: "[redacted]" } : config.auth,
  };
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

function mcpStatusResponse(gatewayId) {
  return (response) =>
    response.url().includes(`/agentcc/gateways/${gatewayId}/mcp-status/`) &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function mcpToolsResponse(gatewayId) {
  return (response) =>
    response.url().includes(`/agentcc/gateways/${gatewayId}/mcp-tools/`) &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function updateMCPServerResponse(gatewayId) {
  return (response) =>
    response
      .url()
      .includes(`/agentcc/gateways/${gatewayId}/update-mcp-server/`) &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function updateMCPGuardrailsResponse(gatewayId) {
  return (response) =>
    response
      .url()
      .includes(`/agentcc/gateways/${gatewayId}/update-mcp-guardrails/`) &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function removeMCPServerResponse(gatewayId) {
  return (response) =>
    response
      .url()
      .includes(`/agentcc/gateways/${gatewayId}/remove-mcp-server/`) &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function isGatewayApiUrl(url) {
  return url.includes("/agentcc/");
}

function isAllowedMCPMutation(method, url) {
  return (
    method === "POST" &&
    (url.includes("/update-mcp-server/") ||
      url.includes("/update-mcp-guardrails/") ||
      url.includes("/remove-mcp-server/"))
  );
}

function cssEscape(value) {
  return String(value).replace(/["\\]/g, "\\$&");
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
