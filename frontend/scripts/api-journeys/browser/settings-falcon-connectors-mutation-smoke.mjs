/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { Buffer } from "node:buffer";
import { createHash } from "node:crypto";
import http from "node:http";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  assert,
  createAuthenticatedContext,
  isUuid,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const CALLBACK_HOST =
  process.env.API_JOURNEY_CALLBACK_HOST || "host.docker.internal";
const SCREENSHOT_PREFIX =
  process.env.SETTINGS_FALCON_CONNECTORS_MUTATION_SCREENSHOT_PREFIX ||
  "/tmp/settings-falcon-connectors-mutation";
const CREATE_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-create-smoke.png`;
const DISCOVER_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-discover-smoke.png`;
const EDIT_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-edit-smoke.png`;
const DELETE_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-delete-smoke.png`;
const FAILURE_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-failure-smoke.png`;
const CLICKABLE_SELECTOR =
  "button,a,[role='button'],[role='option'],[role='menuitem']";

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9-]/gi, "-").slice(0, 24);
  const namePrefix = `ui falcon mutation ${suffix}`;
  const connectorName = `${namePrefix} primary`;
  const editedConnectorName = `${connectorName} edited`;
  const authHeaderName = "X-Falcon-UI-Smoke";
  const rawSecret = `falcon-ui-secret-${suffix}`;
  const toolName = `falcon_ui_tool_${suffix.replace(/-/g, "_")}`;
  const secondaryToolName = `${toolName}_secondary`;
  const tools = [
    {
      name: toolName,
      description: "Temporary Falcon browser mutation tool.",
      inputSchema: {
        type: "object",
        properties: { query: { type: "string" } },
      },
    },
    {
      name: secondaryToolName,
      description: "Temporary Falcon browser mutation tool to disable.",
      inputSchema: { type: "object", properties: {} },
    },
  ];

  await hardDeleteFalconConnectorFixtures({
    namePrefix,
    organizationId: auth.organizationId,
  });

  const mockMcp = await startMockMcpServer({
    tools,
    authHeaderName,
    rawSecret,
  });
  const serverUrl = mockMcp.callbackUrl;
  let browser = null;
  let connectorId = null;
  let deletedViaUi = false;
  let caughtError = null;
  const apiFailures = [];
  const pageErrors = [];
  const mutationRequests = [];
  const mutationPayloads = [];
  const evidence = {
    connector_name: connectorName,
    edited_connector_name: editedConnectorName,
    server_url: serverUrl,
    raw_secret_sha256: sha256(rawSecret),
    mock_mcp_local_url: mockMcp.localUrl,
    mock_mcp_callback_host: CALLBACK_HOST,
  };

  try {
    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });
    const page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installAuthState(page, auth);

    page.on("request", (request) => {
      if (!isFalconConnectorUrl(request.url())) return;
      if (["POST", "PATCH", "PUT", "DELETE"].includes(request.method())) {
        mutationRequests.push(`${request.method()} ${pathname(request.url())}`);
        mutationPayloads.push(sanitizeMutationPayload(request));
      }
    });
    page.on("response", (response) => {
      if (isFalconConnectorUrl(response.url()) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${response.url()}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponseDuring(
      page,
      "Falcon connector list load",
      (response) =>
        response.url().includes("/falcon-ai/mcp-connectors/") &&
        response.request().method() === "GET" &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/settings/falcon-ai-connectors`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForVisibleText(page, "Falcon AI Connectors", { exact: true });
    await waitForVisibleText(page, "Add Connector", { exact: true });

    await clickVisibleText(page, "Add Connector", { exact: true });
    await waitForVisibleText(page, "Add Connector", { exact: true });
    await fillInputByPlaceholder(page, "e.g. Linear, GitHub", connectorName);
    await fillInputByPlaceholder(
      page,
      "https://mcp.example.com/mcp",
      serverUrl,
    );
    await selectComboboxAfterLabel(page, "Authentication", "API Key");
    await fillInputByPlaceholder(page, "X-API-Key", authHeaderName);
    await fillInputByPlaceholder(page, "sk-...", rawSecret);
    await waitForNoVisibleText(page, rawSecret);

    const createResponse = await waitForResponseDuring(
      page,
      "Falcon connector browser create",
      (response) =>
        response.url().includes("/falcon-ai/mcp-connectors/") &&
        response.request().method() === "POST" &&
        response.status() < 400,
      () => clickVisibleText(page, "Create", { exact: true }),
    );
    const createBody = await parseJsonResponse(createResponse);
    connectorId = createBody?.result?.id || createBody?.id;
    assert(isUuid(connectorId), "Browser create did not return connector id.");
    evidence.connector_id = connectorId;
    assertNoPayloadString(createBody, rawSecret, "connector create response");

    await waitForVisibleText(page, connectorName, { exact: true });
    await clickDeepestVisibleText(page, connectorName);
    await waitForVisibleText(page, "Server URL", { exact: true });
    await waitForVisibleText(page, serverUrl, { exact: true });
    await waitForVisibleText(page, "Pending", { exact: true });
    await page.screenshot({ path: CREATE_SCREENSHOT_PATH, fullPage: true });
    evidence.create_screenshot = CREATE_SCREENSHOT_PATH;

    let audit = await loadFalconConnectorDbAudit({
      connectorId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      rawSecret,
    });
    assert(
      audit.connector_count === 1 &&
        audit.name === connectorName &&
        audit.auth_value_is_encrypted === true &&
        audit.auth_value_contains_raw_secret === false &&
        audit.discovered_tool_count === 0 &&
        audit.enabled_tool_count === 0,
      `Post-create DB audit mismatch: ${JSON.stringify(audit)}`,
    );
    evidence.auth_value_hash_after_create = audit.auth_value_hash;

    const testResponse = await waitForResponseDuring(
      page,
      "Falcon connector browser test",
      (response) =>
        response
          .url()
          .includes(`/falcon-ai/mcp-connectors/${connectorId}/test/`) &&
        response.request().method() === "POST" &&
        response.status() < 400,
      () => clickVisibleText(page, "Test Connection", { exact: true }),
    );
    await assertHttpResponseOk(testResponse, "Falcon connector test");
    await waitForVisibleText(page, "Connection test succeeded.", {
      exact: true,
    });
    evidence.test_connection_status = testResponse.status();

    const discoverResponse = await waitForResponseDuring(
      page,
      "Falcon connector browser discover",
      (response) =>
        response
          .url()
          .includes(`/falcon-ai/mcp-connectors/${connectorId}/discover/`) &&
        response.request().method() === "POST" &&
        response.status() < 400,
      () => clickVisibleText(page, "Discover Tools", { exact: true }),
    );
    await assertHttpResponseOk(discoverResponse, "Falcon connector discover");
    await waitForVisibleText(page, "Discovered 2 tools.", { exact: true });
    await waitForVisibleText(page, toolName, { exact: true });
    await waitForVisibleText(page, secondaryToolName, { exact: true });
    await page.screenshot({ path: DISCOVER_SCREENSHOT_PATH, fullPage: true });
    evidence.discover_screenshot = DISCOVER_SCREENSHOT_PATH;

    audit = await loadFalconConnectorDbAudit({
      connectorId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      rawSecret,
    });
    assert(
      audit.is_verified === true &&
        audit.last_error === "" &&
        audit.discovered_tool_count === 2 &&
        audit.enabled_tool_count === 2,
      `Post-discover DB audit mismatch: ${JSON.stringify(audit)}`,
    );

    const toolResponse = await waitForResponseDuring(
      page,
      "Falcon connector browser tool toggle",
      (response) =>
        response
          .url()
          .includes(`/falcon-ai/mcp-connectors/${connectorId}/tools/`) &&
        response.request().method() === "PATCH" &&
        response.status() < 400,
      () => clickSwitchForTool(page, secondaryToolName),
    );
    await assertHttpResponseOk(toolResponse, "Falcon connector tool toggle");
    const toolRequestBody = parseRequestJson(toolResponse.request());
    assert(
      Array.isArray(toolRequestBody?.enabled_tool_names) &&
        toolRequestBody.enabled_tool_names.includes(toolName) &&
        !toolRequestBody.enabled_tool_names.includes(secondaryToolName),
      `Tool toggle sent wrong payload: ${JSON.stringify(toolRequestBody)}`,
    );
    await waitForSwitchChecked(page, secondaryToolName, false);

    audit = await loadFalconConnectorDbAudit({
      connectorId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      rawSecret,
    });
    assert(
      audit.discovered_tool_count === 2 && audit.enabled_tool_count === 1,
      `Post-toggle DB audit mismatch: ${JSON.stringify(audit)}`,
    );
    evidence.enabled_tool_count_after_toggle = audit.enabled_tool_count;

    await clickVisibleText(page, "Edit", { exact: true });
    await waitForVisibleText(page, "Edit Connector", { exact: true });
    await waitForInputValue(page, connectorName);
    await waitForInputValue(page, serverUrl);
    await waitForInputValue(page, authHeaderName);
    await assertPasswordInputsBlank(page);
    await waitForNoVisibleText(page, rawSecret);
    await fillInputByPlaceholder(
      page,
      "e.g. Linear, GitHub",
      editedConnectorName,
    );

    const editResponse = await waitForResponseDuring(
      page,
      "Falcon connector browser edit",
      (response) =>
        response.url().includes(`/falcon-ai/mcp-connectors/${connectorId}/`) &&
        response.request().method() === "PATCH" &&
        response.status() < 400,
      () => clickVisibleText(page, "Save", { exact: true }),
    );
    await assertHttpResponseOk(editResponse, "Falcon connector edit");
    const editRequestBody = parseRequestJson(editResponse.request());
    assert(
      !Object.prototype.hasOwnProperty.call(
        editRequestBody,
        "auth_header_value",
      ),
      `Edit payload should omit blank secret: ${JSON.stringify(editRequestBody)}`,
    );
    await waitForVisibleText(page, editedConnectorName, { exact: true });
    await waitForNoVisibleText(page, rawSecret);
    await page.screenshot({ path: EDIT_SCREENSHOT_PATH, fullPage: true });
    evidence.edit_screenshot = EDIT_SCREENSHOT_PATH;

    audit = await loadFalconConnectorDbAudit({
      connectorId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      rawSecret,
    });
    assert(
      audit.name === editedConnectorName &&
        audit.auth_value_hash === evidence.auth_value_hash_after_create &&
        audit.auth_value_contains_raw_secret === false,
      `Post-edit DB audit mismatch: ${JSON.stringify(audit)}`,
    );
    evidence.auth_hash_preserved_after_edit = true;

    const deleteResponse = await waitForResponseDuring(
      page,
      "Falcon connector browser delete",
      (response) =>
        response.url().includes(`/falcon-ai/mcp-connectors/${connectorId}/`) &&
        response.request().method() === "DELETE" &&
        response.status() < 400,
      () => clickVisibleText(page, "Delete", { exact: true }),
    );
    await assertHttpResponseOk(deleteResponse, "Falcon connector delete", {
      allowNoContent: true,
    });
    deletedViaUi = true;
    await waitForNoVisibleText(page, editedConnectorName, { exact: true });
    await page.screenshot({ path: DELETE_SCREENSHOT_PATH, fullPage: true });
    evidence.delete_screenshot = DELETE_SCREENSHOT_PATH;

    const postDeleteAudit = await loadFalconConnectorDbAudit({
      connectorId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      rawSecret,
      includeDeleted: true,
    });
    assert(
      postDeleteAudit.deleted === true &&
        postDeleteAudit.deleted_at_set === true,
      `Post-delete DB audit mismatch: ${JSON.stringify(postDeleteAudit)}`,
    );
    evidence.public_delete_status = true;

    const cleanup = await hardDeleteFalconConnectorFixtures({
      namePrefix,
      organizationId: auth.organizationId,
    });
    assert(
      cleanup.remaining_connector_count === 0,
      `Falcon connector cleanup left residue: ${JSON.stringify(cleanup)}`,
    );
    evidence.cleanup = cleanup;

    const mockToolListRequests = mockMcp.requests.filter(
      (request) => request.jsonrpc_method === "tools/list",
    );
    assert(
      mockToolListRequests.length >= 2,
      `Expected test and discover to call mock MCP tools/list: ${JSON.stringify(
        mockMcp.requests,
      )}`,
    );
    assert(
      mockToolListRequests.every((request) => request.auth_header_matched),
      `Mock MCP did not receive decrypted auth header on every call: ${JSON.stringify(
        mockToolListRequests,
      )}`,
    );
    evidence.mock_mcp_tools_list_count = mockToolListRequests.length;
    evidence.mock_mcp_auth_header_matched = true;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assertNoPayloadString(mutationPayloads, rawSecret, "mutation evidence");
    evidence.mutation_requests = mutationRequests;
    evidence.mutation_payloads = mutationPayloads;

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    caughtError = error;
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
          mutation_requests: mutationRequests,
          mutation_payloads: mutationPayloads,
          mock_mcp_requests: mockMcp.requests,
        },
        null,
        2,
      ),
    );
    if (browser) {
      const pages = await browser.pages();
      await pages
        .at(-1)
        ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .catch(() => null);
    }
  } finally {
    if (browser) await browser.close();
    await mockMcp.close().catch((error) => {
      caughtError = appendCleanupError(caughtError, error);
    });
    if (!deletedViaUi || connectorId) {
      await hardDeleteFalconConnectorFixtures({
        namePrefix,
        organizationId: auth.organizationId,
      }).catch((error) => {
        caughtError = appendCleanupError(caughtError, error);
      });
    }
  }

  if (caughtError) throw caughtError;
}

async function startMockMcpServer({ tools, authHeaderName, rawSecret }) {
  const requests = [];
  const server = http.createServer(async (request, response) => {
    const bodyText = await readRequestBody(request);
    let body = null;
    try {
      body = bodyText ? JSON.parse(bodyText) : null;
    } catch {
      body = null;
    }

    const authHeaderValue = request.headers[authHeaderName.toLowerCase()];
    requests.push({
      method: request.method,
      url: request.url,
      jsonrpc_method: body?.method || "",
      auth_header_present: Boolean(authHeaderValue),
      auth_header_matched: authHeaderValue === rawSecret,
    });

    if (request.method !== "POST") {
      response.writeHead(405, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: "Method not allowed" }));
      return;
    }

    if (body?.method === "tools/list") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(
        JSON.stringify({
          jsonrpc: "2.0",
          id: body.id ?? 1,
          result: { tools },
        }),
      );
      return;
    }

    if (body?.method === "initialize") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(
        JSON.stringify({
          jsonrpc: "2.0",
          id: body.id ?? 0,
          result: {
            protocolVersion: "2025-03-26",
            capabilities: { tools: {} },
            serverInfo: { name: "api-journey-mock-mcp", version: "1.0.0" },
          },
        }),
      );
      return;
    }

    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(
      JSON.stringify({
        jsonrpc: "2.0",
        id: body?.id ?? 1,
        result: {},
      }),
    );
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "0.0.0.0", resolve);
  });
  const { port } = server.address();
  return {
    localUrl: `http://127.0.0.1:${port}/mcp`,
    callbackUrl: `http://${CALLBACK_HOST}:${port}/mcp`,
    requests,
    close: () =>
      new Promise((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      ),
  };
}

async function readRequestBody(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

async function loadFalconConnectorDbAudit({
  connectorId,
  organizationId,
  workspaceId,
  rawSecret,
}) {
  const sql = `
WITH connector_rows AS (
  SELECT
    connector.id,
    connector.name,
    connector.auth_header_value,
    connector.discovered_tools,
    connector.enabled_tool_names,
    connector.is_verified,
    connector.last_error,
    connector.deleted,
    connector.deleted_at
  FROM falcon_ai_mcpconnector connector
  WHERE connector.id = ${sqlUuid(connectorId)}
    AND connector.organization_id = ${sqlUuid(organizationId)}
    AND connector.workspace_id = ${sqlUuid(workspaceId)}
)
SELECT json_build_object(
  'connector_count', (SELECT count(*) FROM connector_rows),
  'name', COALESCE((SELECT name FROM connector_rows LIMIT 1), ''),
  'auth_value_is_encrypted',
    COALESCE((SELECT auth_header_value LIKE 'enc::%' FROM connector_rows LIMIT 1), false),
  'auth_value_hash',
    COALESCE((SELECT md5(auth_header_value) FROM connector_rows LIMIT 1), ''),
  'auth_value_contains_raw_secret',
    COALESCE((SELECT position(${sqlTextLiteral(rawSecret)} in auth_header_value) > 0 FROM connector_rows LIMIT 1), false),
  'discovered_tool_count',
    COALESCE((SELECT jsonb_array_length(discovered_tools::jsonb) FROM connector_rows LIMIT 1), 0),
  'enabled_tool_count',
    COALESCE((SELECT jsonb_array_length(enabled_tool_names::jsonb) FROM connector_rows LIMIT 1), 0),
  'is_verified',
    COALESCE((SELECT is_verified FROM connector_rows LIMIT 1), false),
  'last_error',
    COALESCE((SELECT last_error FROM connector_rows LIMIT 1), ''),
  'deleted',
    COALESCE((SELECT deleted FROM connector_rows LIMIT 1), false),
  'deleted_at_set',
    COALESCE((SELECT deleted_at IS NOT NULL FROM connector_rows LIMIT 1), false)
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteFalconConnectorFixtures({
  namePrefix,
  organizationId,
}) {
  const deleteSql = `
WITH target_connectors AS (
  SELECT connector.id
  FROM falcon_ai_mcpconnector connector
  WHERE connector.organization_id = ${sqlUuid(organizationId)}
    AND connector.name LIKE ${sqlTextLiteral(`${namePrefix}%`)}
),
deleted_connectors AS (
  DELETE FROM falcon_ai_mcpconnector connector
  USING target_connectors target
  WHERE connector.id = target.id
  RETURNING connector.id
)
SELECT json_build_object(
  'deleted_connector_count', (SELECT count(*) FROM deleted_connectors)
);
`;
  const deleted = await runPostgresJson(deleteSql);
  const residueSql = `
SELECT json_build_object(
  'remaining_connector_count',
    (SELECT count(*) FROM falcon_ai_mcpconnector connector
     WHERE connector.organization_id = ${sqlUuid(organizationId)}
       AND connector.name LIKE ${sqlTextLiteral(`${namePrefix}%`)})
);
`;
  const residue = await runPostgresJson(residueSql);
  return { ...deleted, ...residue };
}

async function runPostgresJson(sql) {
  const container =
    process.env.API_JOURNEY_DB_CONTAINER || "futureagi-ws2-postgres-1";
  const { stdout } = await execFileAsync(
    "docker",
    [
      "exec",
      "-i",
      container,
      "psql",
      "-U",
      "user",
      "-d",
      "tfc",
      "-At",
      "-c",
      sql,
    ],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const text = stdout.trim();
  if (!text) return {};
  return JSON.parse(text.split("\n").at(-1));
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

async function installAuthState(page, auth) {
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
  const responsePromise = page.waitForResponse(predicate, { timeout: 60000 });
  await action();
  try {
    return await responsePromise;
  } catch (error) {
    throw new Error(
      `${label} did not observe expected response: ${error.message}`,
    );
  }
}

async function assertHttpResponseOk(
  response,
  label,
  { allowNoContent = false } = {},
) {
  if (allowNoContent && response.status() === 204) return;
  assert(
    response.status() >= 200 && response.status() < 300,
    `${label} returned HTTP ${response.status()}`,
  );
  const body = await parseJsonResponse(response);
  assertNoPayloadString(body, "falcon-ui-secret-", `${label} response`);
  if (body && Object.prototype.hasOwnProperty.call(body, "status")) {
    assert(body.status !== false, `${label} returned status=false`);
  }
}

async function parseJsonResponse(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function parseRequestJson(request) {
  const text = request.postData() || "";
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
      return Array.from(document.querySelectorAll("body *")).some((element) => {
        if (!isVisible(element)) return false;
        const textContent = normalized(element.textContent);
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      });

      function isVisible(element) {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      }
    },
    { timeout },
    { text, exact },
  );
}

async function waitForNoVisibleText(
  page,
  text,
  { exact = false, timeout = 10000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
      return !Array.from(document.querySelectorAll("body *")).some(
        (element) => {
          if (!isVisible(element)) return false;
          const textContent = normalized(element.textContent);
          if (exactMatch) return textContent === expectedText;
          return textContent.includes(expectedText);
        },
      );

      function isVisible(element) {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      }
    },
    { timeout },
    { text, exact },
  );
}

async function waitForInputValue(page, value, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (expectedValue) =>
      Array.from(document.querySelectorAll("input,textarea")).some(
        (input) => String(input.value || "") === expectedValue,
      ),
    { timeout },
    value,
  );
}

async function fillInputByPlaceholder(page, placeholder, value) {
  const selector = `input[placeholder="${cssEscape(placeholder)}"]`;
  const input = await page.waitForSelector(selector, {
    timeout: 30000,
  });
  await input.click({ clickCount: 3 });
  await page.keyboard.press("Backspace");
  await input.type(value);
  await waitForInputValue(page, value);
}

async function selectComboboxAfterLabel(page, labelText, optionText) {
  const point = await page.evaluate((expectedLabel) => {
    const labels = Array.from(document.querySelectorAll("body *"))
      .filter(
        (element) =>
          isVisible(element) &&
          String(element.textContent || "").trim() === expectedLabel,
      )
      .sort(
        (a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top,
      );
    const label = labels.at(-1);
    if (!label) throw new Error(`Label ${expectedLabel} not found`);
    const labelTop = label.getBoundingClientRect().top;
    const combobox = Array.from(document.querySelectorAll('[role="combobox"]'))
      .filter(
        (element) =>
          isVisible(element) && element.getBoundingClientRect().top > labelTop,
      )
      .sort(
        (a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top,
      )[0];
    if (!combobox) {
      throw new Error(`Combobox after ${expectedLabel} not found`);
    }
    const rect = combobox.getBoundingClientRect();
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };

    function isVisible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    }
  }, labelText);
  await page.mouse.click(point.x, point.y);
  await clickVisibleText(page, optionText, { exact: true });
}

async function clickVisibleText(page, text, { exact = false } = {}) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch, selector }) => {
      const match = (value) => {
        const normalized = String(value || "").trim();
        return exactMatch
          ? normalized === expectedText
          : normalized.includes(expectedText);
      };
      return Array.from(document.querySelectorAll("body *")).some(
        (candidate) =>
          isVisible(candidate) &&
          match(candidate.textContent) &&
          Boolean(candidate.closest(selector)),
      );

      function isVisible(element) {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      }
    },
    { timeout: 30000 },
    { text, exact, selector: CLICKABLE_SELECTOR },
  );
  await page.evaluate(
    ({ text: expectedText, exact: exactMatch, selector }) => {
      const match = (value) => {
        const normalized = String(value || "").trim();
        return exactMatch
          ? normalized === expectedText
          : normalized.includes(expectedText);
      };
      const element = Array.from(document.querySelectorAll("body *")).find(
        (candidate) =>
          isVisible(candidate) &&
          match(candidate.textContent) &&
          Boolean(candidate.closest(selector)),
      );
      const target = element?.closest(selector);
      if (!target) throw new Error(`Clickable text ${expectedText} not found`);
      target.click();

      function isVisible(element) {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      }
    },
    { text, exact, selector: CLICKABLE_SELECTOR },
  );
}

async function clickDeepestVisibleText(page, text) {
  await waitForVisibleText(page, text, { exact: true });
  await page.evaluate((expectedText) => {
    const matches = Array.from(document.querySelectorAll("body *"))
      .filter(
        (element) =>
          isVisible(element) &&
          String(element.textContent || "").trim() === expectedText,
      )
      .sort(
        (a, b) =>
          b.getBoundingClientRect().width - a.getBoundingClientRect().width,
      );
    const element = matches.at(-1);
    const target = element?.closest("button,a,[role='button']") || element;
    if (!target) throw new Error(`Text ${expectedText} not clickable`);
    target.click();

    function isVisible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    }
  }, text);
}

async function clickSwitchForTool(page, toolName) {
  await waitForVisibleText(page, toolName, { exact: true });
  await page.evaluate((expectedToolName) => {
    const textNode = Array.from(document.querySelectorAll("body *")).find(
      (element) =>
        isVisible(element) &&
        String(element.textContent || "").trim() === expectedToolName,
    );
    if (!textNode) throw new Error(`Tool ${expectedToolName} not found`);
    let row = textNode;
    for (let index = 0; index < 8 && row; index += 1) {
      const input = row.querySelector('input[type="checkbox"]');
      if (input) {
        input.click();
        return;
      }
      row = row.parentElement;
    }
    throw new Error(`Switch for ${expectedToolName} not found`);

    function isVisible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    }
  }, toolName);
}

async function waitForSwitchChecked(
  page,
  toolName,
  expectedChecked,
  { timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ expectedToolName, expected }) => {
      const textNode = Array.from(document.querySelectorAll("body *")).find(
        (element) =>
          isVisible(element) &&
          String(element.textContent || "").trim() === expectedToolName,
      );
      if (!textNode) return false;
      let row = textNode;
      for (let index = 0; index < 8 && row; index += 1) {
        const input = row.querySelector('input[type="checkbox"]');
        if (input) return input.checked === expected;
        row = row.parentElement;
      }
      return false;

      function isVisible(element) {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      }
    },
    { timeout },
    { expectedToolName: toolName, expected: expectedChecked },
  );
}

async function assertPasswordInputsBlank(page) {
  const state = await page.evaluate(() => ({
    password_values: Array.from(
      document.querySelectorAll('input[type="password"]'),
    ).map((input) => String(input.value || "")),
  }));
  assert(
    state.password_values.every((value) => value === ""),
    `Password inputs are not blank: ${JSON.stringify(state)}`,
  );
}

function sanitizeMutationPayload(request) {
  const body = parseRequestJson(request);
  if (!body || typeof body !== "object") return {};
  const sanitized = { ...body };
  if (Object.prototype.hasOwnProperty.call(sanitized, "auth_header_value")) {
    sanitized.auth_header_value = sanitized.auth_header_value
      ? "<redacted>"
      : "";
  }
  return {
    method: request.method(),
    path: pathname(request.url()),
    body: sanitized,
  };
}

function isFalconConnectorUrl(url) {
  return url.includes("/falcon-ai/mcp-connectors/");
}

function pathname(url) {
  return new URL(url).pathname;
}

function assertNoPayloadString(payload, needle, label) {
  assert(
    !JSON.stringify(payload).includes(needle),
    `${label} leaked hidden secret material.`,
  );
}

function browserExecutablePath() {
  return (
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  );
}

function sqlUuid(value) {
  assert(isUuid(value), `Expected UUID but received ${value}`);
  return `'${value}'::uuid`;
}

function sqlTextLiteral(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

function sha256(value) {
  return createHash("sha256").update(String(value)).digest("hex");
}

function cssEscape(value) {
  return String(value).replace(/"/g, '\\"');
}

function appendCleanupError(original, cleanupError) {
  if (!original) return cleanupError;
  original.message = `${original.message}; cleanup failed: ${cleanupError.message}`;
  return original;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
