/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.SIMULATE_AGENT_DEFINITIONS_SCREENSHOT ||
  "/tmp/simulate-agent-definitions-lifecycle-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const runId = auth.runId.replace(/[^a-z0-9-]/gi, "-");
  const namePrefix = `browser agent ${runId}`;
  const createdName = `${namePrefix} created`;
  const initialPrompt =
    "Temporary chat agent definition created by the browser lifecycle smoke.";
  const updatedPrompt =
    "Updated temporary chat agent definition saved by the browser lifecycle smoke.";

  await hardDeleteAgentDefinitionFixtures({
    namePrefix,
    organizationId: auth.organizationId,
  });

  const pageErrors = [];
  const apiFailures = [];
  const mutationResponses = [];
  const mutationPayloads = [];
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });

  let createdId = null;
  let secondVersionId = null;

  try {
    const page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installAuthState(page, auth);

    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("response", (response) => {
      const url = response.url();
      if (!url.includes("/simulate/agent-definitions/")) return;
      const status = response.status();
      if (status >= 400) apiFailures.push(`${status} ${url}`);
      if (
        ["POST", "PUT", "PATCH", "DELETE"].includes(response.request().method())
      ) {
        mutationResponses.push(
          `${response.request().method()} ${status} ${url}`,
        );
      }
    });
    page.on("request", (request) => {
      const url = request.url();
      const method = request.method();
      const isAgentDefinitionMutation =
        url.includes("/simulate/agent-definitions/") &&
        ["POST", "PUT", "PATCH", "DELETE"].includes(method);
      if (!isAgentDefinitionMutation) return;
      const postData = request.postData();
      let body = null;
      try {
        body = postData ? JSON.parse(postData) : null;
      } catch {
        body = null;
      }
      mutationPayloads.push({
        method,
        path: new URL(url).pathname,
        keys: body && typeof body === "object" ? Object.keys(body).sort() : [],
        authentication_method: body?.authentication_method ?? null,
        provider: body?.provider ?? null,
        has_api_key: Object.prototype.hasOwnProperty.call(
          body || {},
          "api_key",
        ),
      });
    });

    await page.goto(`${APP_BASE}/dashboard/simulate/agent-definitions`, {
      waitUntil: "domcontentloaded",
    });
    await expectVisibleText(page, "Agent Definitions");

    const createResponse = await waitForResponseDuring(
      page,
      "agent definition UI create",
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/simulate/agent-definitions/create/") &&
        response.status() < 400,
      async () => {
        await clickButtonByText(page, "Create agent definition");
        await expectVisibleText(page, "Create new agent definition");
        await selectDropdownOption(
          page,
          'input[placeholder="Select agent type"]',
          "Chat",
        );
        await setInputValue(
          page,
          'input[placeholder="Give your agent a clear name"]',
          createdName,
        );
        await clickButtonByText(page, "Next");
        await expectVisibleText(page, "Agent Configuration");
        await clickButtonByText(page, "Next");
        await expectVisibleText(page, "Behavior Configuration");
        await setInputValue(
          page,
          'textarea[placeholder*="system prompt"]',
          initialPrompt,
        );
        await setInputValue(
          page,
          'input[placeholder="My first version"]',
          "Initial browser smoke version",
        );
        await clickButtonByText(page, "Create agent definition");
      },
    );
    const createBody = await createResponse.json();
    createdId = createBody?.agent?.id;
    assert(
      isUuid(createdId),
      "Agent definition create response did not include agent.id.",
    );

    await page.waitForFunction(
      (agentId) => window.location.pathname.includes(agentId),
      { timeout: 60000 },
      createdId,
    );
    await expectVisibleText(page, "Current Configuration");
    await expectVisibleText(page, createdName);
    await expectFieldValue(
      page,
      'textarea[placeholder="Describe the agent\'s purpose and functions"]',
      initialPrompt,
    );
    await expectVisibleText(page, "Agent Configuration");
    await expectVisibleText(page, "Chat Logs");
    await expectNoVisibleText(page, "Invalid Date");
    await expectNoVisibleText(page, "undefined");
    await dismissScenarioHelpIfVisible(page);

    const saveResponse = await waitForResponseDuring(
      page,
      "agent definition UI save new version",
      (response) =>
        response.request().method() === "POST" &&
        response
          .url()
          .includes(
            `/simulate/agent-definitions/${createdId}/versions/create/`,
          ) &&
        response.status() < 400,
      async () => {
        await setInputValue(
          page,
          'textarea[placeholder="Describe the agent\'s purpose and functions"]',
          updatedPrompt,
        );
        await setInputValue(
          page,
          'input[placeholder="Describe your changes"]',
          "Save browser smoke update",
        );
        await clickButtonByText(page, "Save");
      },
    );
    const saveBody = await saveResponse.json();
    secondVersionId = saveBody?.version?.id;
    assert(
      isUuid(secondVersionId),
      "Agent definition save response did not include version.id.",
    );
    assert(
      saveBody?.version?.version_number === 2,
      "Agent definition UI save did not create version v2.",
    );

    await expectFieldValue(
      page,
      'textarea[placeholder="Describe the agent\'s purpose and functions"]',
      updatedPrompt,
    );
    await expectNoVisibleText(page, "Invalid Date");
    await expectNoVisibleText(page, "undefined");
    await dismissScenarioHelpIfVisible(page);

    const detail = await auth.client.get(
      apiPath("/simulate/agent-definitions/{agent_id}/", {
        agent_id: createdId,
      }),
    );
    assert(
      detail?.id === createdId &&
        detail?.version_count === 2 &&
        detail?.active_version?.version_number === 2,
      "Agent definition detail did not reflect the v2 UI edit.",
    );

    await page.goto(`${APP_BASE}/dashboard/simulate/agent-definitions`, {
      waitUntil: "domcontentloaded",
    });
    await expectVisibleText(page, "Agent Definitions");
    await searchAgentDefinitions(page, createdName);
    await expectVisibleText(page, createdName);
    await expectVisibleText(page, "Chat");
    await expectVisibleText(page, "English");
    await expectVisibleText(page, "NA");
    await expectVisibleText(page, "v2");
    await expectNoVisibleText(page, "Invalid Date");
    await expectNoVisibleText(page, "undefined");
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    await selectRowsByText(page, [createdName]);
    await expectVisibleText(page, "1 Selected");
    await waitForResponseDuring(
      page,
      "agent definition UI bulk delete",
      (response) =>
        response.request().method() === "DELETE" &&
        response.url().endsWith("/simulate/agent-definitions/") &&
        response.status() < 400,
      async () => {
        await clickButtonByText(page, "Delete");
        await expectVisibleText(page, "Delete Agent definition");
        await expectVisibleText(
          page,
          `Are you sure you want to delete ${createdName} agent definition?`,
        );
        await clickDialogButtonByText(page, "Delete");
      },
    );

    const remaining = await findAgentDefinitionsBySearch(
      auth.client,
      namePrefix,
    );
    assert(
      remaining.length === 0,
      `Deleted agent definitions still visible in API list: ${remaining
        .map((agent) => agent.agent_name)
        .join(", ")}`,
    );

    assert(
      apiFailures.length === 0,
      `Agent definition API failures during browser smoke: ${apiFailures.join(
        "; ",
      )}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    const dbCleanup = await hardDeleteAgentDefinitionFixtures({
      namePrefix,
      organizationId: auth.organizationId,
    });
    assert(
      Number(dbCleanup.deleted_credential_count) === 0,
      `Chat agent definition lifecycle created credential rows: ${JSON.stringify(
        dbCleanup,
      )}`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence: {
            agent_definition_id: createdId,
            second_version_id: secondVersionId,
            mutation_responses: mutationResponses,
            mutation_payloads: mutationPayloads,
            db_cleanup: dbCleanup,
            screenshot: SCREENSHOT_PATH,
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await hardDeleteAgentDefinitionFixtures({
      namePrefix,
      organizationId: auth.organizationId,
    }).catch(() => null);
    throw error;
  } finally {
    await browser.close();
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

async function searchAgentDefinitions(page, text) {
  await Promise.all([
    page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        response.url().includes("/simulate/agent-definitions/") &&
        response.url().includes("search="),
      { timeout: 60000 },
    ),
    setInputValue(page, 'input[placeholder="Search"]', text),
  ]);
}

async function setInputValue(page, selector, value) {
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.$eval(
    selector,
    (element, nextValue) => {
      const proto =
        element instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      setter.call(element, nextValue);
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
    },
    value,
  );
}

async function selectDropdownOption(page, inputSelector, optionText) {
  await page.waitForSelector(inputSelector, { visible: true, timeout: 30000 });
  await page.click(inputSelector);
  await clickVisibleText(page, optionText, {
    selector: '[role="menu"] li, [role="presentation"] li, li',
  });
}

async function clickButtonByText(page, text) {
  await clickVisibleText(page, text, { selector: "button" });
}

async function clickDialogButtonByText(page, text) {
  await clickVisibleText(page, text, {
    selector: '[role="dialog"] button',
  });
}

async function clickVisibleText(page, text, { selector = "body *" } = {}) {
  await page.waitForFunction(
    (expected, targetSelector) =>
      Array.from(document.querySelectorAll(targetSelector)).some((node) => {
        const rect = node.getBoundingClientRect();
        const style = window.getComputedStyle(node);
        return (
          rect.width > 0 &&
          rect.height > 0 &&
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          String(node.textContent || "")
            .replace(/\s+/g, " ")
            .trim()
            .includes(expected)
        );
      }),
    { timeout: 30000 },
    text,
    selector,
  );
  await page.evaluate(
    (expected, targetSelector) => {
      const candidates = Array.from(document.querySelectorAll(targetSelector))
        .filter((candidate) => {
          const rect = candidate.getBoundingClientRect();
          const style = window.getComputedStyle(candidate);
          return (
            rect.width > 0 &&
            rect.height > 0 &&
            style.visibility !== "hidden" &&
            style.display !== "none" &&
            String(candidate.textContent || "")
              .replace(/\s+/g, " ")
              .trim()
              .includes(expected)
          );
        })
        .sort(
          (a, b) =>
            String(a.textContent || "").length -
            String(b.textContent || "").length,
        );
      const node = candidates[0];
      if (!node) throw new Error(`No visible node found for ${expected}`);
      node.click();
    },
    text,
    selector,
  );
}

async function selectRowsByText(page, labels) {
  const selected = await page.evaluate((expectedLabels) => {
    let count = 0;
    const rows = Array.from(document.querySelectorAll('[role="row"][data-id]'));
    for (const label of expectedLabels) {
      const row = rows.find((candidate) =>
        candidate.innerText?.includes(label),
      );
      if (!row) continue;
      const checkbox = row.querySelector('input[type="checkbox"]');
      if (!checkbox) continue;
      if (!checkbox.checked) checkbox.click();
      count += 1;
    }
    return count;
  }, labels);
  assert(
    selected === labels.length,
    `Selected ${selected} agent definition rows, expected ${labels.length}.`,
  );
}

async function dismissScenarioHelpIfVisible(page) {
  await page.evaluate(() => {
    const dialog = Array.from(
      document.querySelectorAll('[role="dialog"]'),
    ).find((node) => node.innerText?.includes("Create agent scenarios"));
    if (!dialog) return;
    const buttons = Array.from(dialog.querySelectorAll("button"));
    buttons[0]?.click();
  });
}

async function expectVisibleText(page, text) {
  await page.waitForFunction(
    (expected) => document.body?.innerText?.includes(expected),
    { timeout: 30000 },
    text,
  );
}

async function expectFieldValue(page, selector, value) {
  await page.waitForFunction(
    (targetSelector, expectedValue) => {
      const node = document.querySelector(targetSelector);
      return node?.value === expectedValue;
    },
    { timeout: 30000 },
    selector,
    value,
  );
}

async function expectNoVisibleText(page, text) {
  const found = await page.evaluate((expected) =>
    Boolean(document.body?.innerText?.includes(expected)),
  );
  assert(!found, `Unexpected visible text found: ${text}`);
}

async function findAgentDefinitionsBySearch(client, search) {
  const result = await client.get(apiPath("/simulate/agent-definitions/"), {
    query: { page: 1, limit: 100, search },
  });
  return asArray(result).filter((agent) =>
    agent?.agent_name?.startsWith(search),
  );
}

async function hardDeleteAgentDefinitionFixtures({
  namePrefix,
  organizationId,
}) {
  const sql = `
WITH target_agents AS (
  SELECT id
  FROM simulate_agent_definition
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND agent_name LIKE ${sqlString(`${namePrefix}%`)}
),
target_versions AS (
  SELECT id
  FROM simulate_agent_version
  WHERE agent_definition_id IN (SELECT id FROM target_agents)
),
target_credentials AS (
  SELECT id
  FROM simulate_provider_credentials
  WHERE agent_definition_id IN (SELECT id FROM target_agents)
),
deleted_credentials AS (
  DELETE FROM simulate_provider_credentials c
  USING target_credentials target
  WHERE c.id = target.id
  RETURNING c.id
),
deleted_versions AS (
  DELETE FROM simulate_agent_version v
  USING target_versions target
  WHERE v.id = target.id
  RETURNING v.id
),
deleted_agents AS (
  DELETE FROM simulate_agent_definition a
  USING target_agents target
  WHERE a.id = target.id
  RETURNING a.id
)
SELECT json_build_object(
  'deleted_agent_count', (SELECT count(*) FROM deleted_agents),
  'deleted_version_count', (SELECT count(*) FROM deleted_versions),
  'deleted_credential_count', (SELECT count(*) FROM deleted_credentials),
  'remaining_agent_count',
    (SELECT count(*) FROM target_agents) - (SELECT count(*) FROM deleted_agents),
  'remaining_version_count',
    (SELECT count(*) FROM target_versions) - (SELECT count(*) FROM deleted_versions),
  'remaining_credential_count',
    (SELECT count(*) FROM target_credentials) - (SELECT count(*) FROM deleted_credentials)
);
`;
  return runPostgresJson(sql);
}

async function runPostgresJson(sql) {
  const container =
    process.env.API_JOURNEY_DB_CONTAINER || "futureagi-ws2-postgres-1";
  const user = process.env.API_JOURNEY_DB_USER || "user";
  const database = process.env.API_JOURNEY_DB_NAME || "tfc";
  const { stdout } = await execFileAsync(
    "docker",
    ["exec", container, "psql", "-U", user, "-d", database, "-At", "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const text = stdout.trim();
  assert(text, "Postgres DB cleanup returned no JSON output.");
  return JSON.parse(text);
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlString(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  }
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  return "/usr/bin/google-chrome";
}

main()
  .then(() => {
    process.exit(0);
  })
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
