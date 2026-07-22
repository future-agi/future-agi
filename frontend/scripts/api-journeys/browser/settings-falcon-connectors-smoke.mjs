/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.SETTINGS_FALCON_CONNECTORS_SCREENSHOT ||
  "/tmp/settings-falcon-connectors-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  process.env.SETTINGS_FALCON_CONNECTORS_FAILURE_SCREENSHOT ||
  "/tmp/settings-falcon-connectors-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9-]/gi, "-").slice(0, 24);
  const namePrefix = `ui falcon connector ${suffix}`;
  const connectorName = `${namePrefix} primary`;
  const serverUrl = `https://example.com/falcon-ui-connector-${suffix}`;
  const authHeaderName = "X-Falcon-UI-Smoke";
  const rawSecret = `falcon-ui-secret-${suffix}-${randomUUID()}`;
  const toolName = `falcon_ui_tool_${suffix.replace(/-/g, "_")}`;
  const secondaryToolName = `${toolName}_secondary`;

  await hardDeleteFalconConnectorFixtures({
    namePrefix,
    organizationId: auth.organizationId,
  });

  let connectorId = null;
  let browser = null;
  let caughtError = null;
  const apiFailures = [];
  const pageErrors = [];
  const connectorRequests = [];
  const unexpectedMutations = [];
  const evidence = {
    connector_name: connectorName,
    server_url: serverUrl,
    raw_secret_sha256: sha256(rawSecret),
  };

  try {
    const created = await auth.client.post(
      apiPath("/falcon-ai/mcp-connectors/"),
      {
        name: connectorName,
        server_url: serverUrl,
        transport: "streamable_http",
        auth_type: "api_key",
        auth_header_name: authHeaderName,
        auth_header_value: rawSecret,
      },
    );
    connectorId = created.id;
    assert(
      isUuid(connectorId),
      "Falcon connector create did not return a UUID id.",
    );
    assertNoPayloadString(created, rawSecret, "connector create response");
    evidence.connector_id = connectorId;

    const seeded = await seedFalconConnectorTools({
      connectorId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      tools: [
        {
          name: toolName,
          description: "Temporary Falcon UI smoke tool.",
          inputSchema: {
            type: "object",
            properties: { query: { type: "string" } },
          },
        },
        {
          name: secondaryToolName,
          description: "Temporary disabled Falcon UI smoke tool.",
          inputSchema: { type: "object", properties: {} },
        },
      ],
      enabledToolNames: [toolName],
    });
    assert(
      seeded.updated_connector_count === 1 &&
        seeded.discovered_tool_count === 2,
      `Falcon connector tool seed failed: ${JSON.stringify(seeded)}`,
    );
    evidence.seeded_tool_count = seeded.discovered_tool_count;

    const list = await auth.client.get(apiPath("/falcon-ai/mcp-connectors/"));
    assert(
      Array.isArray(list) &&
        list.some((connector) => connector.id === connectorId),
      "Falcon connector list API did not include the disposable connector.",
    );
    assertNoPayloadString(list, rawSecret, "connector list response");

    const detail = await auth.client.get(
      apiPath("/falcon-ai/mcp-connectors/{connector_id}/", {
        connector_id: connectorId,
      }),
    );
    assert(
      detail.id === connectorId &&
        detail.name === connectorName &&
        Array.isArray(detail.discovered_tools) &&
        detail.discovered_tools.length === 2 &&
        Array.isArray(detail.enabled_tool_names) &&
        detail.enabled_tool_names.includes(toolName),
      `Falcon connector detail API did not return seeded tool state: ${JSON.stringify(detail)}`,
    );
    assertNoPayloadString(detail, rawSecret, "connector detail response");

    const preBrowserAudit = await loadFalconConnectorDbAudit({
      connectorId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      rawSecret,
    });
    assert(
      preBrowserAudit.auth_value_is_encrypted === true &&
        preBrowserAudit.auth_value_contains_raw_secret === false &&
        preBrowserAudit.discovered_tool_count === 2 &&
        preBrowserAudit.enabled_tool_count === 1,
      `Falcon connector pre-browser DB audit mismatch: ${JSON.stringify(preBrowserAudit)}`,
    );
    evidence.auth_value_hash_before = preBrowserAudit.auth_value_hash;

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
      connectorRequests.push(`${request.method()} ${request.url()}`);
      if (["POST", "PATCH", "PUT", "DELETE"].includes(request.method())) {
        unexpectedMutations.push(`${request.method()} ${request.url()}`);
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
    await typeIntoPlaceholder(page, "Search connectors...", connectorName);
    await waitForVisibleText(page, connectorName, { exact: true });
    await waitForVisibleText(page, "Connected", { exact: true });

    await waitForResponseDuring(
      page,
      "Falcon connector detail load",
      (response) =>
        response.url().includes(`/falcon-ai/mcp-connectors/${connectorId}/`) &&
        response.request().method() === "GET" &&
        response.status() < 400,
      () => clickDeepestVisibleText(page, connectorName),
    );
    await waitForVisibleText(page, "Server URL", { exact: true });
    await waitForVisibleText(page, serverUrl, { exact: true });
    await waitForVisibleText(page, "Authentication", { exact: true });
    await waitForVisibleText(page, "api_key", { exact: true });
    await waitForVisibleText(page, "Transport", { exact: true });
    await waitForVisibleText(page, "streamable_http", { exact: true });
    await waitForVisibleText(page, "Discovered Tools");
    await waitForVisibleText(page, toolName, { exact: true });
    await waitForVisibleText(page, secondaryToolName, { exact: true });
    await waitForVisibleText(page, "Temporary Falcon UI smoke tool.");
    await waitForNoVisibleText(page, rawSecret);
    await waitForNoVisibleText(page, "Invalid Date");
    await waitForNoVisibleText(page, "undefined");

    await clickVisibleText(page, "Edit", { exact: true });
    await waitForVisibleText(page, "Edit Connector", { exact: true });
    await waitForInputValue(page, connectorName);
    await waitForInputValue(page, serverUrl);
    await waitForInputValue(page, authHeaderName);
    await waitForVisibleText(page, "API Key", { exact: true });
    await waitForVisibleText(page, "Streamable HTTP", { exact: true });
    const formState = await readConnectorFormState(page);
    assert(
      formState.input_values.includes(connectorName) &&
        formState.input_values.includes(serverUrl) &&
        formState.input_values.includes(authHeaderName),
      `Falcon connector edit form did not hydrate display fields: ${JSON.stringify(formState)}`,
    );
    assert(
      formState.password_values.every((value) => value === ""),
      `Falcon connector edit form exposed a hidden credential value: ${JSON.stringify(formState)}`,
    );
    assertNoPayloadString(formState, rawSecret, "connector edit form state");

    await clickVisibleText(page, "Cancel", { exact: true });
    await waitForVisibleText(page, "Discovered Tools");
    await waitForVisibleText(page, toolName, { exact: true });
    await waitForNoVisibleText(page, rawSecret);
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    const postBrowserAudit = await loadFalconConnectorDbAudit({
      connectorId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      rawSecret,
    });
    assert(
      postBrowserAudit.auth_value_hash === preBrowserAudit.auth_value_hash &&
        postBrowserAudit.enabled_tool_count === 1 &&
        postBrowserAudit.discovered_tool_count === 2,
      `Falcon connector browser pass mutated DB state: ${JSON.stringify(postBrowserAudit)}`,
    );
    evidence.auth_hash_preserved_after_cancel = true;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Falcon connector UI smoke fired unexpected mutations: ${unexpectedMutations.join("; ")}`,
    );
    evidence.connector_request_count = connectorRequests.length;

    await auth.client.delete(
      apiPath("/falcon-ai/mcp-connectors/{connector_id}/", {
        connector_id: connectorId,
      }),
    );
    evidence.public_delete_status = true;
    const postDeleteList = await auth.client.get(
      apiPath("/falcon-ai/mcp-connectors/"),
    );
    assert(
      Array.isArray(postDeleteList) &&
        !postDeleteList.some((connector) => connector.id === connectorId),
      "Falcon connector remained visible after public delete.",
    );
    const cleanup = await hardDeleteFalconConnectorFixtures({
      namePrefix,
      organizationId: auth.organizationId,
    });
    assert(
      cleanup.remaining_connector_count === 0,
      `Falcon connector cleanup left residue: ${JSON.stringify(cleanup)}`,
    );
    evidence.cleanup = cleanup;

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
          connector_requests: connectorRequests,
          unexpected_mutations: unexpectedMutations,
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
    await hardDeleteFalconConnectorFixtures({
      namePrefix,
      organizationId: auth.organizationId,
    }).catch((error) => {
      caughtError = appendCleanupError(caughtError, error);
    });
  }

  if (caughtError) throw caughtError;
}

async function seedFalconConnectorTools({
  connectorId,
  organizationId,
  workspaceId,
  tools,
  enabledToolNames,
}) {
  const sql = `
WITH updated AS (
  UPDATE falcon_ai_mcpconnector connector
  SET
    discovered_tools = ${sqlJson(tools)},
    enabled_tool_names = ${sqlJson(enabledToolNames)},
    is_verified = true,
    last_discovery_at = NOW(),
    updated_at = NOW()
  WHERE connector.id = ${sqlUuid(connectorId)}
    AND connector.organization_id = ${sqlUuid(organizationId)}
    AND connector.workspace_id = ${sqlUuid(workspaceId)}
    AND connector.deleted = false
  RETURNING connector.id, connector.discovered_tools, connector.enabled_tool_names
)
SELECT json_build_object(
  'updated_connector_count', (SELECT count(*) FROM updated),
  'discovered_tool_count',
    COALESCE((SELECT jsonb_array_length(discovered_tools::jsonb) FROM updated LIMIT 1), 0),
  'enabled_tool_count',
    COALESCE((SELECT jsonb_array_length(enabled_tool_names::jsonb) FROM updated LIMIT 1), 0)
);
`;
  return runPostgresJson(sql);
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
    connector.auth_header_value,
    connector.discovered_tools,
    connector.enabled_tool_names,
    connector.deleted,
    connector.deleted_at
  FROM falcon_ai_mcpconnector connector
  WHERE connector.id = ${sqlUuid(connectorId)}
    AND connector.organization_id = ${sqlUuid(organizationId)}
    AND connector.workspace_id = ${sqlUuid(workspaceId)}
)
SELECT json_build_object(
  'connector_count', (SELECT count(*) FROM connector_rows),
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

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
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
      return Array.from(document.querySelectorAll("body *")).some((element) => {
        if (!isVisible(element)) return false;
        const textContent = normalized(element.textContent);
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      });
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
      return !Array.from(document.querySelectorAll("body *")).some(
        (element) => {
          if (!isVisible(element)) return false;
          const textContent = normalized(element.textContent);
          if (exactMatch) return textContent === expectedText;
          return textContent.includes(expectedText);
        },
      );
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

async function typeIntoPlaceholder(page, placeholder, value) {
  await page.waitForSelector(`input[placeholder="${cssEscape(placeholder)}"]`, {
    timeout: 30000,
  });
  await page.evaluate(
    ({ placeholder: expectedPlaceholder, value: nextValue }) => {
      const input = Array.from(document.querySelectorAll("input")).find(
        (candidate) =>
          candidate.getAttribute("placeholder") === expectedPlaceholder,
      );
      if (!input) throw new Error(`Input ${expectedPlaceholder} not found`);
      input.focus();
      input.value = "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.value = nextValue;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    },
    { placeholder, value },
  );
}

async function clickVisibleText(page, text, { exact = false } = {}) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
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
      const match = (value) => {
        const normalized = String(value || "").trim();
        return exactMatch
          ? normalized === expectedText
          : normalized.includes(expectedText);
      };
      return Array.from(document.querySelectorAll("body *")).some(
        (candidate) => {
          if (!isVisible(candidate) || !match(candidate.textContent))
            return false;
          return Boolean(candidate.closest("button,a,[role='button']"));
        },
      );
    },
    { timeout: 30000 },
    { text, exact },
  );
  await page.evaluate(
    ({ text: expectedText, exact: exactMatch }) => {
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
          Boolean(candidate.closest("button,a,[role='button']")),
      );
      const target = element?.closest("button,a,[role='button']");
      if (!target) throw new Error(`Clickable text ${expectedText} not found`);
      target.click();
    },
    { text, exact },
  );
}

async function clickDeepestVisibleText(page, text) {
  await waitForVisibleText(page, text, { exact: true });
  await page.evaluate((expectedText) => {
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
  }, text);
}

async function readConnectorFormState(page) {
  return page.evaluate(() => ({
    input_values: Array.from(document.querySelectorAll("input,textarea")).map(
      (input) => String(input.value || ""),
    ),
    password_values: Array.from(
      document.querySelectorAll('input[type="password"]'),
    ).map((input) => String(input.value || "")),
    visible_text: document.body.innerText,
  }));
}

function isFalconConnectorUrl(url) {
  return url.includes("/falcon-ai/mcp-connectors/");
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

function sqlJson(value) {
  return `${sqlTextLiteral(JSON.stringify(value))}::jsonb`;
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
