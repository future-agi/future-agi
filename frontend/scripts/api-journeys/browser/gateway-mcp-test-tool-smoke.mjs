/* eslint-disable no-console */
import { Buffer } from "node:buffer";
import { execFile as execFileCallback } from "node:child_process";
import fs from "node:fs/promises";
import http from "node:http";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFile = promisify(execFileCallback);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/gateway-mcp-test-tool-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/gateway-mcp-test-tool-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);
const MCP_SERVER_ID = process.env.GATEWAY_MCP_TEST_SERVER_ID || "th4812";
const TOOL_ORIGINAL_NAME = "echo";
const TOOL_NAME = `${MCP_SERVER_ID}_${TOOL_ORIGINAL_NAME}`;
const TOOL_ARGUMENTS = {
  input: "hello from the MCP browser smoke",
  count: 3,
  flag: true,
};

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
  let gatewayHarness = null;
  let mockMCP = null;
  let cleanupOrgConfig = null;
  let gatewayWasRestored = false;
  const evidence = {};

  try {
    const gateways = asArray(
      await auth.client.get(apiPath("/agentcc/gateways/")),
    );
    assert(gateways.length > 0, "Gateway list returned no configured gateway.");
    evidence.gateway_id =
      gateways.find((gateway) => gateway.id === "default")?.id ||
      gateways[0].id ||
      "default";

    const baseline = await prepareOrgConfigRestorer(auth.client);
    cleanupOrgConfig = baseline.cleanup;
    evidence.original_org_config_id = baseline.originalActiveConfig.id;
    evidence.original_org_config_version =
      baseline.originalActiveConfig.version;

    mockMCP = await startMockMCPServer();
    evidence.mock_mcp_server = {
      host_url: mockMCP.hostUrl,
      container_url: mockMCP.containerUrl,
    };

    gatewayHarness = await startDisposableGateway(mockMCP.port);
    evidence.gateway_harness = gatewayHarness.evidence;

    evidence.org_mcp_server_update = await responseResult(
      await auth.client.post(
        apiPath("/agentcc/gateways/{id}/update-mcp-server/", {
          id: evidence.gateway_id,
        }),
        {
          server_id: MCP_SERVER_ID,
          config: {
            url: mockMCP.containerUrl,
            transport: "http",
            auth: { type: "none" },
            tools_cache_ttl: "5m",
          },
        },
      ),
    );

    evidence.backend_tools = await waitForBackendTool(
      auth.client,
      evidence.gateway_id,
      TOOL_NAME,
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
        const mutation = `${request.method()} ${url}`;
        browserMutations.push(mutation);
        if (!isAllowedBrowserMutation(request.method(), url)) {
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
      "initial Gateway MCP playground load",
      [
        gatewayListResponse(),
        gatewayConfigResponse(evidence.gateway_id),
        mcpStatusResponse(evidence.gateway_id),
        mcpToolsResponse(evidence.gateway_id),
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway/mcp/playground`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway/mcp/playground");
    await waitForVisibleText(page, "MCP Tools", { exact: true });
    await waitForVisibleText(page, "Tool Selection", { exact: true });
    await selectAutocompleteByTestId(
      page,
      "mcp-playground-tool-input",
      TOOL_NAME,
    );
    await waitForVisibleText(page, "Arguments (JSON)", { exact: true });
    await setTextareaByTestId(
      page,
      "mcp-playground-arguments-input",
      JSON.stringify(TOOL_ARGUMENTS, null, 2),
    );

    const [testToolResponse] = await waitForResponsesDuring(
      page,
      "execute MCP Test Tool through browser",
      [testMCPToolResponse(evidence.gateway_id)],
      () => clickByTestId(page, "mcp-playground-execute-button"),
    );
    evidence.test_tool_response = await responseResult(testToolResponse);
    assertMCPTestResult(evidence.test_tool_response);

    await waitForVisibleText(page, "Result", { exact: true });
    await waitForVisibleText(page, `echo:${TOOL_ARGUMENTS.input}`);
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    const mockCalls = mockMCP.requests.filter(
      (request) => request.method === "tools/call",
    );
    assert(
      mockCalls.length === 1,
      `Expected one upstream tools/call, saw ${mockCalls.length}: ${JSON.stringify(
        mockMCP.requests,
      )}`,
    );
    evidence.mock_mcp_tool_call = mockCalls[0];

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Unexpected Gateway MCP Test Tool browser mutations: ${unexpectedMutations.join(
        "; ",
      )}`,
    );
    assert(
      browserMutations.length === 1,
      `Expected one Test Tool browser mutation, saw ${browserMutations.length}: ${browserMutations.join(
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
    if (cleanupOrgConfig) {
      try {
        evidence.org_config_cleanup = await cleanupOrgConfig();
      } catch (error) {
        cleanupError = error;
        evidence.org_config_cleanup = {
          status: "failed",
          error: error.message,
        };
      }
    }
    if (gatewayHarness) {
      try {
        evidence.gateway_cleanup = await gatewayHarness.cleanup();
        gatewayWasRestored = Boolean(evidence.gateway_cleanup.original_started);
      } catch (error) {
        cleanupError = cleanupError || error;
        evidence.gateway_cleanup = { status: "failed", error: error.message };
      }
    }
    if (gatewayWasRestored && cleanupOrgConfig) {
      try {
        evidence.original_gateway_reload = await responseResult(
          await auth.client.post(
            apiPath("/agentcc/gateways/{id}/reload/", {
              id: evidence.gateway_id,
            }),
            {},
          ),
        );
      } catch (error) {
        cleanupError = cleanupError || error;
        evidence.original_gateway_reload = {
          status: "failed",
          error: error.message,
        };
      }
    }
    if (mockMCP) {
      try {
        await mockMCP.close();
      } catch (error) {
        cleanupError = cleanupError || error;
        evidence.mock_mcp_cleanup = { status: "failed", error: error.message };
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

async function startMockMCPServer() {
  const requests = [];
  const server = http.createServer(async (request, response) => {
    if (request.method !== "POST" || request.url !== "/mcp") {
      response.writeHead(404, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: "not found" }));
      return;
    }

    const rawBody = await readRequestBody(request);
    let message;
    try {
      message = JSON.parse(rawBody || "{}");
    } catch {
      response.writeHead(400, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: "invalid json" }));
      return;
    }

    const params = parseRpcParams(message.params);
    requests.push({ method: message.method, params });

    if (!message.id) {
      response.writeHead(202);
      response.end();
      return;
    }

    const headers = {
      "Content-Type": "application/json",
      "MCP-Session-Id": "th4812-mcp-session",
    };
    response.writeHead(200, headers);
    response.end(JSON.stringify(createMCPResponse(message)));
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "0.0.0.0", resolve);
  });

  const { port } = server.address();
  return {
    port,
    requests,
    hostUrl: `http://127.0.0.1:${port}`,
    containerUrl: `http://host.docker.internal:${port}`,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

function createMCPResponse(message) {
  const base = { jsonrpc: "2.0", id: message.id };
  if (message.method === "initialize") {
    return {
      ...base,
      result: {
        protocolVersion: "2025-11-25",
        capabilities: { tools: {}, resources: {}, prompts: {} },
        serverInfo: { name: "th4812-mcp-smoke", version: "1.0.0" },
      },
    };
  }
  if (message.method === "tools/list") {
    return {
      ...base,
      result: {
        tools: [
          {
            name: TOOL_ORIGINAL_NAME,
            description: "Echo arguments for the Gateway MCP Test Tool smoke.",
            inputSchema: {
              type: "object",
              properties: {
                input: { type: "string" },
                count: { type: "integer" },
                flag: { type: "boolean" },
              },
              required: ["input"],
            },
          },
        ],
      },
    };
  }
  if (message.method === "tools/call") {
    const params = parseRpcParams(message.params);
    return {
      ...base,
      result: {
        content: [
          {
            type: "text",
            text: `echo:${params.arguments?.input || ""}:${params.arguments?.count}`,
          },
        ],
      },
    };
  }
  if (message.method === "resources/list") {
    return {
      ...base,
      result: {
        resources: [
          {
            uri: "futureagi://th4812/mcp-smoke",
            name: "TH-4812 MCP Smoke Resource",
            description: "Disposable resource exposed by the MCP smoke server.",
            mimeType: "text/plain",
          },
        ],
      },
    };
  }
  if (message.method === "prompts/list") {
    return {
      ...base,
      result: {
        prompts: [
          {
            name: "th4812_mcp_prompt",
            description: "Disposable prompt exposed by the MCP smoke server.",
            arguments: [{ name: "topic", required: false }],
          },
        ],
      },
    };
  }
  return { ...base, result: {} };
}

function parseRpcParams(params) {
  if (!params) return {};
  if (typeof params === "string") return JSON.parse(params);
  return params;
}

async function readRequestBody(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

async function startDisposableGateway(mockPort) {
  if (process.env.GATEWAY_MCP_ALLOW_GATEWAY_RESTART !== "1") {
    throw new Error(
      "Set GATEWAY_MCP_ALLOW_GATEWAY_RESTART=1 to run this smoke; it temporarily stops ws2-agentcc-gateway and restores it in cleanup.",
    );
  }

  const originalContainer =
    process.env.GATEWAY_MCP_ORIGINAL_CONTAINER || "ws2-agentcc-gateway";
  const tempContainer =
    process.env.GATEWAY_MCP_TEMP_CONTAINER || "th4812-gateway-mcp-test-tool";
  const serviceAlias =
    process.env.GATEWAY_MCP_SERVICE_ALIAS || "agentcc-gateway";
  const hostPort = process.env.GATEWAY_MCP_HOST_PORT || "8095";
  const adminToken = process.env.AGENTCC_ADMIN_TOKEN || "agentcc-admin-secret";

  const originalInspect = await dockerInspect(originalContainer);
  const originalWasRunning = Boolean(originalInspect.State?.Running);
  const originalImage =
    process.env.GATEWAY_MCP_IMAGE || originalInspect.Config?.Image;
  assert(originalImage, `Could not resolve image for ${originalContainer}.`);
  const network =
    process.env.GATEWAY_MCP_DOCKER_NETWORK ||
    Object.entries(originalInspect.NetworkSettings?.Networks || {}).find(
      ([, value]) => asArray(value?.Aliases).includes(serviceAlias),
    )?.[0] ||
    Object.keys(originalInspect.NetworkSettings?.Networks || {})[0];
  assert(network, `Could not resolve docker network for ${originalContainer}.`);

  const configPath = path.join(
    process.env.GATEWAY_MCP_CONFIG_DIR ||
      path.resolve(process.cwd(), "agentcc-gateway"),
    `futureagi-mcp-test-gateway-${process.pid}.yaml`,
  );
  await fs.writeFile(
    configPath,
    gatewayConfigYaml({ mockPort, adminToken }),
    "utf8",
  );

  await docker(["rm", "-f", tempContainer], { ignoreFailure: true });
  if (originalWasRunning) {
    await docker(["stop", originalContainer], { timeout: 30000 });
  }

  try {
    await docker(
      [
        "run",
        "-d",
        "--name",
        tempContainer,
        "--network",
        network,
        "--network-alias",
        serviceAlias,
        "--add-host",
        "host.docker.internal:host-gateway",
        "-p",
        `${hostPort}:8080`,
        "-e",
        `AGENTCC_ADMIN_TOKEN=${adminToken}`,
        "-v",
        `${configPath}:/app/config.yaml:ro`,
        originalImage,
        "--config",
        "/app/config.yaml",
      ],
      { timeout: 30000 },
    );
    const ready = await waitForDisposableGatewayReady({ hostPort, adminToken });
    return {
      evidence: {
        mode: "disposable-docker-gateway",
        original_container: originalContainer,
        original_was_running: originalWasRunning,
        temp_container: tempContainer,
        image: originalImage,
        network,
        service_alias: serviceAlias,
        host_port: hostPort,
        ready,
      },
      cleanup: async () => {
        await docker(["rm", "-f", tempContainer], { ignoreFailure: true });
        const cleanup = {
          status: "passed",
          temp_container_removed: true,
          original_started: false,
        };
        if (originalWasRunning) {
          await docker(["start", originalContainer], { timeout: 30000 });
          cleanup.original_started = true;
          cleanup.original_health =
            await waitForOriginalGatewayHealth(hostPort);
        }
        await fs.rm(configPath, { force: true });
        return cleanup;
      },
    };
  } catch (error) {
    const logs = await dockerLogs(tempContainer);
    await docker(["rm", "-f", tempContainer], { ignoreFailure: true });
    if (originalWasRunning) {
      await docker(["start", originalContainer], {
        timeout: 30000,
        ignoreFailure: true,
      });
    }
    await fs.rm(configPath, { force: true });
    if (logs)
      error.message = `${error.message}\nDisposable gateway logs:\n${logs}`;
    throw error;
  }
}

function gatewayConfigYaml({ mockPort, adminToken }) {
  return `server:
  port: 8080
  host: "0.0.0.0"
admin:
  token: "${adminToken}"
logging:
  level: "info"
  format: "json"
mcp:
  enabled: true
  endpoint: "/mcp"
  max_agent_depth: 10
  tool_call_timeout: 10s
  session_ttl: 30m
  separator: "_"
  servers:
    ${MCP_SERVER_ID}:
      url: "http://host.docker.internal:${mockPort}"
      transport: "http"
      auth:
        type: "none"
      tools_cache_ttl: 5m
`;
}

async function waitForDisposableGatewayReady({ hostPort, adminToken }) {
  const baseUrl = `http://127.0.0.1:${hostPort}`;
  let lastError = "";
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const health = await fetch(`${baseUrl}/healthz`);
      if (!health.ok) throw new Error(`health ${health.status}`);
      const toolsResponse = await fetch(`${baseUrl}/-/mcp/tools`, {
        headers: { Authorization: `Bearer ${adminToken}` },
      });
      if (!toolsResponse.ok) {
        throw new Error(`mcp tools ${toolsResponse.status}`);
      }
      const tools = await toolsResponse.json();
      if (asArray(tools).some((tool) => tool.name === TOOL_NAME)) {
        return {
          health: "ok",
          tool_count: asArray(tools).length,
          tool_name: TOOL_NAME,
        };
      }
      lastError = `MCP tools did not include ${TOOL_NAME}: ${JSON.stringify(
        tools,
      )}`;
    } catch (error) {
      lastError = error.message;
    }
    await delay(500);
  }
  throw new Error(`Disposable gateway did not become ready: ${lastError}`);
}

async function waitForOriginalGatewayHealth(hostPort) {
  const baseUrl = `http://127.0.0.1:${hostPort}`;
  let lastError = "";
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const health = await fetch(`${baseUrl}/healthz`);
      if (health.ok) return { status: "healthy" };
      lastError = `health ${health.status}`;
    } catch (error) {
      lastError = error.message;
    }
    await delay(500);
  }
  return { status: "unknown", error: lastError };
}

async function dockerInspect(container) {
  const { stdout } = await docker(["inspect", container], { timeout: 15000 });
  return JSON.parse(stdout)[0];
}

async function docker(args, { timeout = 20000, ignoreFailure = false } = {}) {
  try {
    return await execFile("docker", args, { timeout });
  } catch (error) {
    if (ignoreFailure) return { stdout: "", stderr: error.stderr || "" };
    throw error;
  }
}

async function dockerLogs(container) {
  try {
    const { stdout, stderr } = await docker(["logs", container], {
      timeout: 10000,
    });
    return `${stdout || ""}${stderr || ""}`.trim();
  } catch {
    return "";
  }
}

async function waitForBackendTool(client, gatewayId, toolName) {
  let lastTools = [];
  for (let attempt = 0; attempt < 40; attempt += 1) {
    lastTools = asArray(
      await client.get(
        apiPath("/agentcc/gateways/{id}/mcp-tools/", { id: gatewayId }),
      ),
    );
    if (lastTools.some((tool) => tool?.name === toolName)) return lastTools;
    await delay(500);
  }
  throw new Error(
    `Backend MCP tools did not include ${toolName}: ${JSON.stringify(lastTools)}`,
  );
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

async function selectAutocompleteByTestId(page, testId, value) {
  const selector = `[data-testid="${cssEscape(testId)}"]`;
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector);
  await page.keyboard.type(value);
  await page.waitForFunction(
    (expectedValue) =>
      window
        .visibleElements("[role='option'],li")
        .some(
          (element) =>
            window.normalizeText(element.textContent) === expectedValue,
        ),
    { timeout: 30000 },
    value,
  );
  const selected = await page.evaluate((expectedValue) => {
    const option = window
      .visibleElements("[role='option'],li")
      .find(
        (element) =>
          window.normalizeText(element.textContent) === expectedValue,
      );
    if (!option) return false;
    window.dispatchClick(option);
    return true;
  }, value);
  assert(selected, `Could not select MCP tool ${value}`);
  await waitForInputValue(page, testId, value);
}

async function setTextareaByTestId(page, testId, value) {
  const updated = await page.evaluate(
    ({ expectedTestId, nextValue }) => {
      const element = document.querySelector(
        `[data-testid="${CSS.escape(expectedTestId)}"]`,
      );
      if (!element || element.disabled) return false;
      element.focus();
      window.setNativeValue(element, nextValue);
      element.blur();
      return true;
    },
    { expectedTestId: testId, nextValue: value },
  );
  assert(updated, `Could not set textarea ${testId}`);
}

async function clickByTestId(page, testId) {
  const selector = `button[data-testid="${cssEscape(testId)}"]`;
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  const clicked = await page.evaluate((expectedSelector) => {
    const element = document.querySelector(expectedSelector);
    if (!element || element.disabled) return null;
    element.scrollIntoView({ block: "center", inline: "center" });
    window.__mcpExecuteClickCount = 0;
    element.addEventListener(
      "click",
      () => {
        window.__mcpExecuteClickCount += 1;
      },
      { once: true },
    );
    element.click();
    return {
      event_count: window.__mcpExecuteClickCount,
      active_element: document.activeElement?.tagName || "",
    };
  }, selector);
  assert(clicked, `Could not click ${testId}`);
  return clicked;
}

async function waitForInputValue(page, testId, value, timeout = 30000) {
  await page.waitForFunction(
    ({ expectedTestId, expectedValue }) => {
      const element = document.querySelector(
        `[data-testid="${CSS.escape(expectedTestId)}"]`,
      );
      return element?.value === expectedValue;
    },
    { timeout },
    { expectedTestId: testId, expectedValue: value },
  );
}

async function responseResult(response) {
  if (typeof response?.json === "function") {
    const data = await response.json();
    return data?.result ?? data;
  }
  return response?.result ?? response;
}

function assertMCPTestResult(result) {
  const contentText = asArray(result?.content)
    .map((part) => part?.text || JSON.stringify(part))
    .join("\n");
  assert(
    result?.server === MCP_SERVER_ID,
    `Unexpected MCP server: ${result?.server}`,
  );
  assert(
    result?.is_error !== true,
    `MCP result was an error: ${JSON.stringify(result)}`,
  );
  assert(
    contentText.includes(
      `echo:${TOOL_ARGUMENTS.input}:${TOOL_ARGUMENTS.count}`,
    ),
    `Unexpected MCP content: ${contentText}`,
  );
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

function testMCPToolResponse(gatewayId) {
  return (response) =>
    response.url().includes(`/agentcc/gateways/${gatewayId}/test-mcp-tool/`) &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function isGatewayApiUrl(url) {
  return url.includes("/agentcc/");
}

function isAllowedBrowserMutation(method, url) {
  return method === "POST" && url.includes("/test-mcp-tool/");
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

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
