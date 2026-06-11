/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
  isUuid,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const GRAPH_PREFIX = "ui_agent_list_browser_";
const CREATED_SCREENSHOT_PATH = "/tmp/agents-list-create-smoke.png";
const DELETE_SCREENSHOT_PATH = "/tmp/agents-list-delete-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/agents-list-create-delete-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const marker = auth.runId.replace(/[^a-z0-9]/gi, "").slice(0, 18);
  const uniqueGraphName = `${GRAPH_PREFIX}${marker}`;
  const uniqueGraphDescription = `Browser-created disposable agent ${auth.runId}`;
  const apiFailures = [];
  const pageErrors = [];
  const agentRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  const cleanupEvidence = [];
  let browser = null;
  let page = null;
  let graphId = null;

  await cleanupGraphsByPrefix(auth, GRAPH_PREFIX, cleanupEvidence);

  try {
    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });
    page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installBrowserHelpers(page);
    await installBrowserState(page, auth);

    // The config interceptor owns continue/respond; this listener only records.
    page.on("request", (request) => {
      const url = request.url();
      if (!isAgentPlaygroundApiUrl(url)) return;
      const method = request.method();
      const pathname = new URL(url).pathname;
      agentRequests.push(`${method} ${pathname}`);
      if (!MUTATION_METHODS.has(method)) return;
      browserMutations.push(`${method} ${url}`);
      if (!isAllowedAgentMutation(method, pathname)) {
        unexpectedMutations.push(`${method} ${url}`);
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isAgentPlaygroundApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) =>
      pageErrors.push(error.stack || error.message),
    );

    await waitForResponseDuring(
      page,
      "agent list initial load",
      (response) => isGraphListResponse(response) && response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/agents`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForVisibleText(page, "Agent Playground", { exact: true });
    await waitForVisibleText(page, "Agent Name", { exact: true });
    await waitForVisibleText(page, "Create Agent", { exact: true });

    const createResponse = await waitForResponseDuring(
      page,
      "browser create agent",
      (response) =>
        response.url().endsWith("/agent-playground/graphs/") &&
        response.request().method() === "POST",
      () => clickVisibleButton(page, "Create Agent"),
    );
    const createPayload = await responseJson(createResponse);
    assert(
      createResponse.status() >= 200 && createResponse.status() < 300,
      `Create Agent returned HTTP ${createResponse.status()}: ${JSON.stringify(
        createPayload,
      )}`,
    );
    const createdGraph = unwrapBrowserResult(createPayload);
    graphId = createdGraph?.id;
    const createdVersionId = createdGraph?.active_version?.id;
    assert(isUuid(graphId), "Browser Create Agent did not return a graph id.");
    assert(
      isUuid(createdVersionId),
      "Browser Create Agent did not return an initial active version id.",
    );
    await waitForPathSuffix(
      page,
      `/dashboard/agents/playground/${graphId}/build`,
    );
    await page.waitForFunction(
      (versionId) => window.location.search.includes(`version=${versionId}`),
      { timeout: 30000 },
      createdVersionId,
    );
    await waitForVisibleText(page, "Agent Builder", { exact: true });
    await waitForVisibleText(page, "Add first node", { exact: true });

    const renamedGraph = await auth.client.patch(
      apiPath("/agent-playground/graphs/{id}/", { id: graphId }),
      {
        name: uniqueGraphName,
        description: uniqueGraphDescription,
      },
    );
    assert(
      renamedGraph?.id === graphId && renamedGraph?.name === uniqueGraphName,
      "API metadata patch did not persist the browser-created graph name.",
    );
    const preDeleteAudit = await loadCreatedGraphAudit({
      graphId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assertPreDeleteAudit(preDeleteAudit);
    await page.screenshot({ path: CREATED_SCREENSHOT_PATH, fullPage: true });

    await waitForResponseDuring(
      page,
      "agent list search after browser create",
      (response) => {
        if (!isGraphListResponse(response) || response.status() >= 400) {
          return false;
        }
        const url = new URL(response.url());
        return url.searchParams.get("search") === uniqueGraphName;
      },
      async () => {
        await page.goto(`${APP_BASE}/dashboard/agents`, {
          waitUntil: "domcontentloaded",
        });
        await waitForVisibleText(page, "Agent Playground", { exact: true });
        await typeIntoSearchInput(page, uniqueGraphName);
      },
    );
    await waitForVisibleText(page, uniqueGraphName, { exact: true });
    await selectDataGridRow(page, graphId, uniqueGraphName);
    await waitForVisibleText(page, "1 Selected", { exact: true });
    await clickVisibleButton(page, "Delete");
    await waitForVisibleText(page, "Delete agents", { exact: true });
    await waitForVisibleText(page, "Are you sure you want to delete 1 agent?", {
      exact: true,
    });

    const deleteResponse = await waitForResponseDuring(
      page,
      "browser delete agent",
      (response) =>
        response.url().endsWith("/agent-playground/graphs/delete/") &&
        response.request().method() === "POST",
      () => clickDialogAction(page, "Delete", "Delete agents"),
    );
    const deletePayload = await responseJson(deleteResponse);
    assert(
      deleteResponse.status() >= 200 && deleteResponse.status() < 300,
      `Delete Agent returned HTTP ${deleteResponse.status()}: ${JSON.stringify(
        deletePayload,
      )}`,
    );

    await waitForNoVisibleExactText(page, "1 Selected");
    await waitForNoVisibleExactText(page, uniqueGraphName);
    await page.screenshot({ path: DELETE_SCREENSHOT_PATH, fullPage: true });

    const deletedDetail = await expectMissingGraphDetail(auth.client, graphId);
    const postDeleteAudit = await loadCreatedGraphAudit({
      graphId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assertPostDeleteAudit(postDeleteAudit);
    const hardCleanupAudit = await hardDeleteGraph({ graphId });
    cleanupEvidence.push({
      cleanup: "hard delete browser-created agent graph",
      status: "passed",
      audit: hardCleanupAudit,
    });

    assert(
      unexpectedMutations.length === 0,
      `Unexpected Agent Playground browser mutations: ${unexpectedMutations
        .map(maskRequest)
        .join(", ")}`,
    );
    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    const finalGraphId = graphId;
    graphId = null;
    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          graph_id: finalGraphId,
          initial_version_id: createdVersionId,
          renamed_graph_name: uniqueGraphName,
          deleted_detail: deletedDetail,
          pre_delete_audit: preDeleteAudit,
          post_delete_audit: postDeleteAudit,
          browser_mutations: browserMutations.map(maskRequest),
          agent_request_count: agentRequests.length,
          screenshots: [CREATED_SCREENSHOT_PATH, DELETE_SCREENSHOT_PATH],
          cleanup: cleanupEvidence,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    if (page) {
      await page.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true });
      console.error(`failure_screenshot=${FAILURE_SCREENSHOT_PATH}`);
    }
    throw error;
  } finally {
    if (graphId) {
      await publicDeleteGraphs(auth.client, [graphId]).catch(() => {});
      await hardDeleteGraph({ graphId }).catch((error) => {
        cleanupEvidence.push({
          cleanup: "hard delete browser-created agent graph after failure",
          status: "failed",
          error: error.message,
        });
      });
    }
    await cleanupGraphsByPrefix(auth, GRAPH_PREFIX, cleanupEvidence).catch(
      (error) => {
        cleanupEvidence.push({
          cleanup: "cleanup prefixed agent graphs after run",
          status: "failed",
          error: error.message,
        });
      },
    );
    if (browser) await browser.close();
  }
}

async function cleanupGraphsByPrefix(auth, prefix, evidence) {
  const listPayload = await auth.client.get(
    apiPath("/agent-playground/graphs/"),
    {
      query: {
        search: prefix,
        page_number: 1,
        page_size: 100,
      },
    },
  );
  const graphs = Array.isArray(listPayload?.graphs) ? listPayload.graphs : [];
  const ids = graphs
    .filter((graph) => String(graph?.name || "").startsWith(prefix))
    .map((graph) => graph.id)
    .filter(Boolean);
  if (!ids.length) return;
  await publicDeleteGraphs(auth.client, ids);
  for (const id of ids) {
    await hardDeleteGraph({ graphId: id });
  }
  evidence.push({
    cleanup: "delete prefixed agent graphs",
    status: "passed",
    graph_ids: ids,
  });
}

async function publicDeleteGraphs(client, ids) {
  if (!ids.length) return null;
  return client.post(apiPath("/agent-playground/graphs/delete/"), { ids });
}

async function expectMissingGraphDetail(client, graphId) {
  try {
    return await client.get(
      apiPath("/agent-playground/graphs/{id}/", { id: graphId }),
      {
        okStatuses: [404],
      },
    );
  } catch (error) {
    if (error?.status === 404) return error.body;
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

async function installBrowserHelpers(page) {
  await page.evaluateOnNewDocument(() => {
    window.normalizeText = (value) =>
      String(value || "")
        .replace(/\s+/g, " ")
        .trim();
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
}

async function installBrowserState(page, auth) {
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      if (organizationId) {
        sessionStorage.setItem("organizationId", organizationId);
      }
      if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
      if (user?.id) {
        sessionStorage.setItem("futureagi-current-user-id", user.id);
      }
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

async function waitForPathSuffix(page, pathname, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname.endsWith(expectedPath),
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

async function waitForNoVisibleExactText(page, text, timeout = 30000) {
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

async function clickVisibleButton(page, text) {
  await page.waitForFunction(
    (expectedText) =>
      window
        .visibleElements("button, [role='button']")
        .some(
          (element) =>
            window.normalizeText(element.textContent) === expectedText,
        ),
    { timeout: 30000 },
    text,
  );
  await page.evaluate((expectedText) => {
    const button = window
      .visibleElements("button, [role='button']")
      .find(
        (element) => window.normalizeText(element.textContent) === expectedText,
      );
    button.click();
  }, text);
}

async function clickDialogAction(page, text, dialogTitle) {
  await page.waitForFunction(
    ({ expectedText, expectedDialogTitle }) => {
      const dialog = window
        .visibleElements("[role='dialog']")
        .find((candidate) =>
          window
            .normalizeText(candidate.textContent)
            .includes(expectedDialogTitle),
        );
      if (!dialog) return false;
      const button = Array.from(dialog.querySelectorAll("button")).find(
        (candidate) =>
          !candidate.disabled &&
          window.getComputedStyle(candidate).display !== "none" &&
          window.normalizeText(candidate.textContent) === expectedText,
      );
      return Boolean(button);
    },
    { timeout: 30000 },
    { expectedText: text, expectedDialogTitle: dialogTitle },
  );
  await page.evaluate(
    ({ expectedText, expectedDialogTitle }) => {
      const dialog = window
        .visibleElements("[role='dialog']")
        .find((candidate) =>
          window
            .normalizeText(candidate.textContent)
            .includes(expectedDialogTitle),
        );
      const button = Array.from(dialog.querySelectorAll("button")).find(
        (candidate) =>
          !candidate.disabled &&
          window.getComputedStyle(candidate).display !== "none" &&
          window.normalizeText(candidate.textContent) === expectedText,
      );
      button.click();
    },
    { expectedText: text, expectedDialogTitle: dialogTitle },
  );
}

async function typeIntoSearchInput(page, text) {
  const selector = 'input[placeholder="Search"]';
  await page.waitForSelector(selector, { timeout: 30000 });
  const updated = await page.evaluate(
    ({ selector: targetSelector, value }) => {
      const input = document.querySelector(targetSelector);
      if (!input) return false;
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      ).set;
      setter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return input.value === value;
    },
    { selector, value: text },
  );
  assert(updated, "Could not fill the Agent Playground search input.");
}

async function selectDataGridRow(page, rowId, rowText) {
  await page.waitForFunction(
    ({ expectedId, expectedText }) => {
      const rows = window.visibleElements("[role='row'][data-id]");
      return rows.some((row) => {
        const rowMatchesId = row.getAttribute("data-id") === expectedId;
        const rowMatchesText = window
          .normalizeText(row.textContent)
          .includes(expectedText);
        return rowMatchesId || rowMatchesText;
      });
    },
    { timeout: 30000 },
    { expectedId: rowId, expectedText: rowText },
  );
  const clicked = await page.evaluate(
    ({ expectedId, expectedText }) => {
      const rows = window.visibleElements("[role='row'][data-id]");
      const row = rows.find((candidate) => {
        const rowMatchesId = candidate.getAttribute("data-id") === expectedId;
        const rowMatchesText = window
          .normalizeText(candidate.textContent)
          .includes(expectedText);
        return rowMatchesId || rowMatchesText;
      });
      if (!row) return false;
      const checkbox =
        row.querySelector('input[type="checkbox"]') ||
        row.querySelector('[role="checkbox"]');
      if (!checkbox) return false;
      checkbox.click();
      return true;
    },
    { expectedId: rowId, expectedText: rowText },
  );
  assert(clicked, `Could not click checkbox for graph row ${rowId}.`);
}

async function responseJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function unwrapBrowserResult(payload) {
  if (payload && Object.prototype.hasOwnProperty.call(payload, "result")) {
    return payload.result;
  }
  return payload;
}

async function loadCreatedGraphAudit({ graphId, organizationId, workspaceId }) {
  const sql = `
SELECT json_build_object(
  'graph_total', (
    SELECT count(*) FROM agent_playground_graph
    WHERE id = ${sqlUuid(graphId)}
  ),
  'graph_visible', (
    SELECT count(*) FROM agent_playground_graph
    WHERE id = ${sqlUuid(graphId)} AND deleted = false
  ),
  'graph_deleted', COALESCE((
    SELECT deleted FROM agent_playground_graph
    WHERE id = ${sqlUuid(graphId)}
  ), false),
  'organization_id', (
    SELECT organization_id FROM agent_playground_graph
    WHERE id = ${sqlUuid(graphId)}
  ),
  'workspace_id', (
    SELECT workspace_id FROM agent_playground_graph
    WHERE id = ${sqlUuid(graphId)}
  ),
  'expected_organization_id', ${sqlUuid(organizationId)},
  'expected_workspace_id', ${sqlUuid(workspaceId)},
  'version_total', (
    SELECT count(*) FROM agent_playground_graph_version
    WHERE graph_id = ${sqlUuid(graphId)}
  ),
  'version_visible', (
    SELECT count(*) FROM agent_playground_graph_version
    WHERE graph_id = ${sqlUuid(graphId)} AND deleted = false
  )
);
`;
  return runPostgresJson(sql);
}

function assertPreDeleteAudit(audit) {
  assert(
    audit.graph_total === 1,
    "Browser-created agent graph DB row missing.",
  );
  assert(audit.graph_visible === 1, "Browser-created agent graph not visible.");
  assert(
    audit.organization_id === audit.expected_organization_id,
    "Browser-created agent organization_id mismatch.",
  );
  assert(
    audit.workspace_id === audit.expected_workspace_id,
    "Browser-created agent workspace_id mismatch.",
  );
  assert(
    audit.version_visible >= 1,
    "Browser-created agent has no visible version.",
  );
}

function assertPostDeleteAudit(audit) {
  assert(audit.graph_total === 1, "Deleted agent graph row was missing.");
  assert(audit.graph_visible === 0, "Deleted agent graph remained visible.");
  assert(audit.graph_deleted === true, "Agent graph was not soft-deleted.");
  assert(
    audit.version_visible === 0,
    "Deleted agent graph version remained visible.",
  );
}

async function hardDeleteGraph({ graphId }) {
  const sql = `
BEGIN;
CREATE TEMP TABLE _agt_graph_ids(id uuid) ON COMMIT DROP;
INSERT INTO _agt_graph_ids
SELECT id FROM agent_playground_graph
WHERE id = ${sqlUuid(graphId)};

CREATE TEMP TABLE _agt_version_ids(id uuid) ON COMMIT DROP;
INSERT INTO _agt_version_ids
SELECT id FROM agent_playground_graph_version
WHERE graph_id IN (SELECT id FROM _agt_graph_ids);

CREATE TEMP TABLE _agt_node_ids(id uuid) ON COMMIT DROP;
INSERT INTO _agt_node_ids
SELECT id FROM agent_playground_node
WHERE graph_version_id IN (SELECT id FROM _agt_version_ids);

CREATE TEMP TABLE _agt_dataset_ids(id uuid) ON COMMIT DROP;
INSERT INTO _agt_dataset_ids
SELECT dataset_id FROM agent_playground_graph_dataset
WHERE graph_id IN (SELECT id FROM _agt_graph_ids);

CREATE TEMP TABLE _agt_prompt_template_ids(id uuid) ON COMMIT DROP;
INSERT INTO _agt_prompt_template_ids
SELECT DISTINCT prompt_template_id FROM agent_playground_prompt_template_node
WHERE node_id IN (SELECT id FROM _agt_node_ids);

CREATE TEMP TABLE _agt_prompt_version_ids(id uuid) ON COMMIT DROP;
INSERT INTO _agt_prompt_version_ids
SELECT id FROM model_hub_promptversion
WHERE original_template_id IN (SELECT id FROM _agt_prompt_template_ids);

DELETE FROM model_hub_cell
WHERE dataset_id IN (SELECT id FROM _agt_dataset_ids);
DELETE FROM model_hub_row
WHERE dataset_id IN (SELECT id FROM _agt_dataset_ids);
DELETE FROM model_hub_column
WHERE dataset_id IN (SELECT id FROM _agt_dataset_ids);
DELETE FROM agent_playground_graph_dataset
WHERE graph_id IN (SELECT id FROM _agt_graph_ids);
DELETE FROM model_hub_dataset
WHERE id IN (SELECT id FROM _agt_dataset_ids);

DELETE FROM agent_playground_execution_data
WHERE node_execution_id IN (
  SELECT id FROM agent_playground_node_execution
  WHERE graph_execution_id IN (
    SELECT id FROM agent_playground_graph_execution
    WHERE graph_version_id IN (SELECT id FROM _agt_version_ids)
  )
);
DELETE FROM agent_playground_node_execution
WHERE graph_execution_id IN (
  SELECT id FROM agent_playground_graph_execution
  WHERE graph_version_id IN (SELECT id FROM _agt_version_ids)
);
DELETE FROM agent_playground_graph_execution
WHERE graph_version_id IN (SELECT id FROM _agt_version_ids);

DELETE FROM agent_playground_edge
WHERE graph_version_id IN (SELECT id FROM _agt_version_ids);
DELETE FROM agent_playground_node_connection
WHERE graph_version_id IN (SELECT id FROM _agt_version_ids);
DELETE FROM agent_playground_port
WHERE node_id IN (SELECT id FROM _agt_node_ids);
DELETE FROM agent_playground_prompt_template_node
WHERE node_id IN (SELECT id FROM _agt_node_ids);
DELETE FROM agent_playground_node
WHERE id IN (SELECT id FROM _agt_node_ids);
DELETE FROM agent_playground_graph_version
WHERE id IN (SELECT id FROM _agt_version_ids);
DELETE FROM agent_playground_graph_collaborators
WHERE graph_id IN (SELECT id FROM _agt_graph_ids);
DELETE FROM agent_playground_graph
WHERE id IN (SELECT id FROM _agt_graph_ids);

DELETE FROM model_hub_promptversion_labels
WHERE promptversion_id IN (SELECT id FROM _agt_prompt_version_ids);
DELETE FROM model_hub_prompttemplate_collaborators
WHERE prompttemplate_id IN (SELECT id FROM _agt_prompt_template_ids);
DELETE FROM model_hub_promptversion
WHERE id IN (SELECT id FROM _agt_prompt_version_ids);
DELETE FROM model_hub_prompttemplate
WHERE id IN (SELECT id FROM _agt_prompt_template_ids);

SELECT json_build_object(
  'remaining_graphs', (
    SELECT count(*) FROM agent_playground_graph
    WHERE id = ${sqlUuid(graphId)}
  ),
  'remaining_prompt_templates', (
    SELECT count(*) FROM model_hub_prompttemplate
    WHERE id IN (SELECT id FROM _agt_prompt_template_ids)
  )
);
COMMIT;
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
    ["exec", container, "psql", "-qAt", "-U", user, "-d", database, "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const jsonLine = stdout
    .trim()
    .split(/\r?\n/)
    .reverse()
    .find((line) => line.trim().startsWith("{"));
  assert(jsonLine, "Postgres DB query returned no JSON object.");
  return JSON.parse(jsonLine);
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function isGraphListResponse(response) {
  try {
    return new URL(response.url()).pathname.endsWith(
      "/agent-playground/graphs/",
    );
  } catch {
    return false;
  }
}

function isAgentPlaygroundApiUrl(url) {
  try {
    return new URL(url).pathname.includes("/agent-playground/");
  } catch {
    return false;
  }
}

function isAllowedAgentMutation(method, pathname) {
  return (
    (method === "POST" && pathname === "/agent-playground/graphs/") ||
    (method === "POST" && pathname === "/agent-playground/graphs/delete/")
  );
}

function maskRequest(value) {
  const urlPattern = /(https?:\/\/[^/]+)(\/[^ ]*)/g;
  return value.replace(urlPattern, "$2");
}

function browserExecutablePath() {
  return (
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  );
}

main().catch((error) => {
  if (error?.name === "SkipJourney") {
    console.log(JSON.stringify({ status: "skipped", reason: error.reason }));
    process.exit(0);
  }
  console.error(error);
  process.exit(1);
});
