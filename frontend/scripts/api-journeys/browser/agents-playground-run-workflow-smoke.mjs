import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
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
const SCREENSHOT_PATH = "/tmp/agents-playground-run-workflow-smoke.png";
const DEBUG = process.env.DEBUG_AGENT_RUN_SMOKE === "1";

async function main() {
  requireMutations();

  const auth = await createAuthenticatedContext();
  const marker = auth.runId.replace(/[^a-z0-9]/gi, "").slice(0, 18);
  const graphName = `browser run agent ${marker}`;
  const nodeName = `browser_run_node_${marker}`.slice(0, 80);
  const promptText = "Answer with one short fact about {{topic}}.";
  const inputValue = `browser run input ${auth.runId}`;

  let graphId = null;
  let cleanupAudit = null;
  const evidence = {
    graph_name: graphName,
    node_name: nodeName,
    input_value: inputValue,
  };

  try {
    const setup = await createDisposableAgentGraph({
      auth,
      graphName,
      nodeName,
      promptText,
    });
    graphId = setup.graphId;
    evidence.graph_id = graphId;
    evidence.version_id = setup.versionId;
    evidence.node_id = setup.node.id;

    const variable = await seedGraphVariable({
      auth,
      graphId,
      versionId: setup.versionId,
      columnName: "topic",
      value: inputValue,
    });
    evidence.variable = variable;

    const activeVersion = await auth.client.patch(
      apiPath("/agent-playground/graphs/{id}/versions/{version_id}/", {
        id: graphId,
        version_id: setup.versionId,
      }),
      {
        status: "active",
        commit_message: `browser run workflow ${marker}`,
      },
    );
    assert(
      activeVersion?.id === setup.versionId &&
        activeVersion.status === "active",
      "Run smoke setup did not activate the graph version.",
    );

    const browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });

    const page = await browser.newPage();
    await installRuntimeConfig(page, auth);
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

    const apiFailures = [];
    const pageErrors = [];
    page.on("response", (response) => {
      const url = response.url();
      if (isAgentPlaygroundApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${new URL(url).pathname}`);
      }
    });
    page.on("pageerror", (error) =>
      pageErrors.push(error.stack || error.message),
    );

    try {
      logStep("open active builder");
      const graphDetailResponse = page.waitForResponse(
        (response) =>
          response.url().includes(`/agent-playground/graphs/${graphId}/`) &&
          !response.url().includes("/versions/") &&
          response.status() < 400,
        { timeout: 60000 },
      );
      const versionDetailResponse = page.waitForResponse(
        (response) =>
          response
            .url()
            .includes(
              `/agent-playground/graphs/${graphId}/versions/${setup.versionId}/`,
            ) && response.status() < 400,
        { timeout: 60000 },
      );
      await page.goto(
        `${APP_BASE}/dashboard/agents/playground/${graphId}/build?version=${setup.versionId}`,
        { waitUntil: "domcontentloaded" },
      );
      await Promise.all([graphDetailResponse, versionDetailResponse]);
      await page.waitForFunction(
        ({ graphId: expectedGraphId, versionId }) =>
          window.location.pathname.endsWith(
            `/dashboard/agents/playground/${expectedGraphId}/build`,
          ) && window.location.search.includes(`version=${versionId}`),
        { timeout: 30000 },
        { graphId, versionId: setup.versionId },
      );

      await waitForVisibleText(page, graphName, { exact: true });
      await waitForVisibleText(page, "Agent Builder", { exact: true });
      await waitForVisibleText(page, "Version 1", { exact: true });
      await waitForVisibleText(page, nodeName, { exact: true });
      await waitForNoVisibleText(page, "Draft", { exact: true });
      await waitForEnabledButton(page, "Run Agent Workflow");

      logStep("click run workflow");
      const executeResponsePromise = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          new URL(response.url()).pathname.endsWith(
            `/agent-playground/graphs/${graphId}/dataset/execute/`,
          ),
        { timeout: 60000 },
      );
      await clickVisibleText(page, "Run Agent Workflow", { exact: true });
      const executeResponse = await executeResponsePromise;
      const executeStatus = executeResponse.status();
      const executeBody = await parseJsonResponse(executeResponse);
      const executionIds = executionIdsFromBrowserResponse(executeBody);

      evidence.execute = {
        status: executeStatus,
        body: executeBody,
        execution_ids: executionIds,
      };

      if (executeStatus >= 200 && executeStatus < 300) {
        assert(
          isUuid(executionIds[0]),
          "Browser run execute response did not expose a UUID execution id.",
        );
      } else {
        assert(
          executeStatus === 500,
          `Browser run execute failed with unexpected status ${executeStatus}.`,
        );
      }

      await sleep(1000);
      await waitForNoVisibleText(page, "No execution IDs returned", {
        exact: true,
        timeout: 5000,
      });

      const dispatchAudit = await loadAgentActiveExecutionAudit({ graphId });
      evidence.dispatch_audit = dispatchAudit;
      assert(
        dispatchAudit.execution_count === 1,
        "Browser run did not create exactly one graph execution row.",
      );
      assert(
        isUuid(dispatchAudit.latest_execution_id),
        "Browser run DB audit did not find the created execution id.",
      );
      assert(
        dispatchAudit.latest_input_topic === inputValue,
        "Browser run GraphExecution input payload did not persist the dataset value.",
      );
      if (executeStatus === 500) {
        assert(
          dispatchAudit.latest_status === "failed",
          "Browser run dispatch failure left GraphExecution non-failed.",
        );
        assert(
          String(dispatchAudit.latest_error_message || "").includes(
            "Failed to start graph execution workflow",
          ),
          "Browser run dispatch failure did not persist an actionable error message.",
        );
      }

      await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
      evidence.screenshot = SCREENSHOT_PATH;

      const unexpectedApiFailures = apiFailures.filter(
        (failure) =>
          !failure.endsWith(
            `/agent-playground/graphs/${graphId}/dataset/execute/`,
          ),
      );
      assert(
        unexpectedApiFailures.length === 0,
        `Unexpected Agent Playground API failures: ${unexpectedApiFailures.join(
          "; ",
        )}`,
      );
      assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    } finally {
      await browser.close();
    }

    await auth.client.post(apiPath("/agent-playground/graphs/delete/"), {
      ids: [graphId],
    });
    const postDeleteAudit = await loadAgentActiveExecutionAudit({ graphId });
    evidence.post_delete_audit = postDeleteAudit;
    assert(
      postDeleteAudit.execution_count === 0,
      "Public graph delete left browser run executions visible.",
    );
  } finally {
    if (graphId) {
      cleanupAudit = await hardDeleteAgentGraphResidue({
        graphId,
        graphNames: [graphName],
        promptNames: [nodeName],
        organizationId: auth.organizationId,
        workspaceId: auth.workspaceId,
      });
      evidence.cleanup_audit = cleanupAudit;
      assert(
        cleanupAudit.remaining_graphs === 0 &&
          cleanupAudit.remaining_prompt_templates === 0,
        "Hard cleanup left browser run smoke residue.",
      );
    }
  }

  writeJsonLine(process.stdout, evidence);
}

async function createDisposableAgentGraph({
  auth,
  graphName,
  nodeName,
  promptText,
}) {
  const templatePayload = await auth.client.get(
    apiPath("/agent-playground/node-templates/"),
  );
  const nodeTemplates = Array.isArray(templatePayload?.node_templates)
    ? templatePayload.node_templates
    : [];
  const llmTemplate = nodeTemplates.find((item) => item.name === "llm_prompt");
  assert(llmTemplate?.id, "Agent node templates did not include llm_prompt.");

  const createdGraph = await auth.client.post(
    apiPath("/agent-playground/graphs/"),
    {
      name: graphName,
      description: `Disposable browser run workflow graph ${auth.runId}`,
    },
  );
  const graphId = createdGraph.id;
  const versionId = createdGraph.active_version?.id;
  assert(isUuid(graphId), "Agent graph create did not return a graph id.");
  assert(
    isUuid(versionId),
    "Agent graph create did not return an active version id.",
  );

  const node = await auth.client.post(
    apiPath("/agent-playground/graphs/{id}/versions/{version_id}/nodes/", {
      id: graphId,
      version_id: versionId,
    }),
    {
      id: randomUUID(),
      type: "atomic",
      name: nodeName,
      node_template_id: llmTemplate.id,
      position: { x: 160, y: 120 },
      prompt_template: {
        messages: [
          {
            id: "browser-run-source",
            role: "user",
            content: [{ type: "text", text: promptText }],
          },
        ],
        response_format: "text",
        model: "gpt-4o-mini",
        temperature: 0,
        metadata: { api_journey: "agents-playground-run-workflow-smoke" },
      },
    },
  );
  assert(isUuid(node.id), "Agent node create did not return an id.");

  return { graphId, versionId, node };
}

async function seedGraphVariable({
  auth,
  graphId,
  versionId,
  columnName,
  value,
}) {
  const variable = await loadGraphVariable({
    auth,
    graphId,
    versionId,
    columnName,
  });
  await auth.client.put(
    apiPath("/agent-playground/graphs/{graph_id}/dataset/cells/{cell_id}/", {
      graph_id: graphId,
      cell_id: variable.cell_id,
    }),
    { value },
  );
  const readback = await loadGraphVariable({
    auth,
    graphId,
    versionId,
    columnName,
  });
  assert(readback.value === value, "Graph variable seed did not persist.");
  return readback;
}

async function loadGraphVariable({ auth, graphId, versionId, columnName }) {
  const dataset = await auth.client.get(
    apiPath("/agent-playground/graphs/{graph_id}/dataset/", {
      graph_id: graphId,
    }),
    { query: { version_id: versionId, page: 1, page_size: 10 } },
  );
  const column = (dataset.columns || []).find(
    (item) => item.name === columnName,
  );
  assert(column?.id, `Graph dataset did not expose ${columnName} column.`);
  const row = (dataset.rows || [])[0];
  assert(row?.id, "Graph dataset did not expose a minimum variable row.");
  const cell = (row.cells || []).find(
    (item) => getCellColumnId(item) === column.id,
  );
  assert(cell?.id, `Graph dataset did not expose a ${columnName} cell.`);
  return {
    dataset_id: dataset.dataset_id,
    column_id: column.id,
    row_id: row.id,
    cell_id: cell.id,
    value: cell.value,
  };
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
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
    },
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
          return exactMatch
            ? textContent === expectedText
            : textContent.includes(expectedText);
        },
      );
    },
    { timeout },
    { text, exact },
  );
}

async function waitForEnabledButton(page, text, timeout = 30000) {
  await page.waitForFunction(
    (expectedText) =>
      Array.from(document.querySelectorAll("button")).some((button) => {
        if (button.disabled) return false;
        return String(button.textContent || "").trim() === expectedText;
      }),
    { timeout },
    text,
  );
}

async function clickVisibleText(page, text, { exact = false } = {}) {
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
      return Array.from(
        document.querySelectorAll("button,[role='tab'],a"),
      ).some((element) => {
        if (!isVisible(element)) return false;
        const textContent = normalized(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
    },
    { timeout: 30000 },
    { text, exact },
  );
  await page.evaluate(
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
      const element = Array.from(
        document.querySelectorAll("button,[role='tab'],a"),
      ).find((candidate) => {
        if (!isVisible(candidate)) return false;
        const textContent = normalized(candidate.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
      element.click();
    },
    { text, exact },
  );
}

function executionIdsFromBrowserResponse(body) {
  const result = body?.result || body?.data?.result || body;
  return result?.execution_ids || result?.executionIds || [];
}

async function parseJsonResponse(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

function getCellColumnId(cell) {
  return cell?.columnId || cell?.column_id;
}

async function loadAgentActiveExecutionAudit({ graphId }) {
  const sql = `
WITH target_versions AS (
  SELECT id FROM agent_playground_graph_version
  WHERE graph_id = ${sqlUuid(graphId)}
),
target_executions AS (
  SELECT id, status, input_payload, error_message, created_at
  FROM agent_playground_graph_execution
  WHERE graph_version_id IN (SELECT id FROM target_versions)
    AND deleted = false
),
latest_execution AS (
  SELECT * FROM target_executions
  ORDER BY created_at DESC
  LIMIT 1
)
SELECT json_build_object(
  'execution_count', (SELECT count(*) FROM target_executions),
  'latest_execution_id', (SELECT id::text FROM latest_execution),
  'latest_status', (SELECT status FROM latest_execution),
  'latest_input_topic', (SELECT input_payload->>'topic' FROM latest_execution),
  'latest_error_message', (SELECT error_message FROM latest_execution)
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteAgentGraphResidue({
  graphId,
  graphNames,
  promptNames,
  organizationId,
  workspaceId,
}) {
  const sql = `
BEGIN;
CREATE TEMP TABLE _agt_graph_ids(id uuid) ON COMMIT DROP;
INSERT INTO _agt_graph_ids
SELECT id FROM agent_playground_graph
WHERE id = ${sqlUuid(graphId)}
   OR name = ANY(${sqlTextArray(graphNames)});

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
INSERT INTO _agt_prompt_template_ids
SELECT id FROM model_hub_prompttemplate
WHERE organization_id = ${sqlUuid(organizationId)}
  AND workspace_id = ${sqlUuid(workspaceId)}
  AND name = ANY(${sqlTextArray(promptNames)});

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
    WHERE id = ${sqlUuid(graphId)} OR name = ANY(${sqlTextArray(graphNames)})
  ),
  'remaining_prompt_templates', (
    SELECT count(*) FROM model_hub_prompttemplate
    WHERE organization_id = ${sqlUuid(organizationId)}
      AND workspace_id = ${sqlUuid(workspaceId)}
      AND name = ANY(${sqlTextArray(promptNames)})
  )
);
COMMIT;
`;
  return runPostgresJson(sql);
}

async function runPostgresJson(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
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

function sqlTextLiteral(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`;
}

function sqlTextArray(values) {
  const rows = values || [];
  assert(rows.length > 0, "SQL text array cannot be empty.");
  return `ARRAY[${rows.map((value) => sqlTextLiteral(value)).join(", ")}]::text[]`;
}

function isAgentPlaygroundApiUrl(url) {
  return url.includes("/agent-playground/");
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH)
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  if (process.platform === "linux") return "/usr/bin/google-chrome";
  return undefined;
}

function writeJsonLine(stream, value) {
  stream.write(`${JSON.stringify(value, null, 2)}\n`);
}

function logStep(message) {
  if (!DEBUG) return;
  process.stderr.write(`[agents-run-smoke] ${message}\n`);
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

main().catch((error) => {
  process.stderr.write(`${error?.stack || error}\n`);
  process.exitCode = 1;
});
