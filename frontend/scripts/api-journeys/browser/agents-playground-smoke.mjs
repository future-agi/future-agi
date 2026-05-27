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
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/agents-playground-smoke.png";
const DEBUG = process.env.DEBUG_AGENT_SMOKE === "1";

async function main() {
  const auth = await createAuthenticatedContext();
  const marker = auth.runId.replace(/[^a-z0-9]/gi, "").slice(0, 18);
  const graphName = `browser agent ${marker}`;
  const sourceNodeName = `browser_source_${marker}`.slice(0, 80);
  const targetNodeName = `browser_target_${marker}`.slice(0, 80);
  const graphNames = [graphName];
  const promptNames = [sourceNodeName, targetNodeName];
  let graphId = null;
  let publicDeleteStatus = null;
  let cleanupAudit = null;

  try {
    const setup = await createDisposableAgentGraph({
      auth,
      graphName,
      sourceNodeName,
      targetNodeName,
    });
    graphId = setup.graphId;

    const preDeleteAudit = await loadAgentGraphDbAudit({ graphId });
    assertAgentGraphPreDeleteAudit(preDeleteAudit, {
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });

    const listPayload = await auth.client.get(
      apiPath("/agent-playground/graphs/"),
      {
        query: {
          search: graphName,
          pinned_ids: graphId,
          page_number: 1,
          page_size: 10,
        },
      },
    );
    const graphRows = Array.isArray(listPayload?.graphs)
      ? listPayload.graphs
      : [];
    const graphRow = graphRows.find((row) => row.id === graphId);
    assert(
      graphRow,
      "Agent graph list/search did not include disposable graph.",
    );
    assert(
      graphRow.active_version_id === setup.draftVersionId ||
        graphRow.active_version?.id === setup.draftVersionId,
      "Agent list row did not expose the active draft version id.",
    );

    const apiFailures = [];
    const pageErrors = [];
    const unexpectedMutations = [];
    const agentRequests = [];
    const evidence = {
      graph_id: graphId,
      draft_version_id: setup.draftVersionId,
      source_node_id: setup.sourceNode.id,
      target_node_id: setup.targetNode.id,
      graph_name: graphName,
      search_result_count: graphRows.length,
      pre_delete_audit: preDeleteAudit,
    };

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

    page.on("request", (request) => {
      const url = request.url();
      if (!isAgentPlaygroundApiUrl(url)) return;
      agentRequests.push(`${request.method()} ${new URL(url).pathname}`);
      if (["POST", "PATCH", "PUT", "DELETE"].includes(request.method())) {
        unexpectedMutations.push(`${request.method()} ${url}`);
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isAgentPlaygroundApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    try {
      logStep("open agents list");
      const initialListResponse = page.waitForResponse(
        (response) => isGraphListResponse(response) && response.status() < 400,
        { timeout: 60000 },
      );
      await page.goto(`${APP_BASE}/dashboard/agents`, {
        waitUntil: "domcontentloaded",
      });
      await initialListResponse;

      logStep("verify agents list");
      await waitForVisibleText(page, "Agent Playground", { exact: true });
      await waitForVisibleText(
        page,
        "Break down complex tasks into sequential steps that build upon each other",
      );
      await waitForVisibleText(page, "Agent Name", { exact: true });
      await waitForVisibleText(page, "No. of nodes", { exact: true });
      await waitForVisibleText(page, "Created by", { exact: true });
      await waitForInputPlaceholder(page, "Search");

      const searchTerm = graphName.slice(0, 18);
      logStep("search disposable agent");
      const searchResponse = page.waitForResponse(
        (response) => {
          if (!isGraphListResponse(response) || response.status() >= 400)
            return false;
          const url = new URL(response.url());
          return url.searchParams.get("search") === searchTerm;
        },
        { timeout: 60000 },
      );
      await typeIntoSearchInput(page, searchTerm);
      await searchResponse;
      await waitForVisibleText(page, graphName, { exact: true });
      await waitForVisibleText(page, "2", { exact: true });

      logStep("open disposable agent");
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
              `/agent-playground/graphs/${graphId}/versions/${setup.draftVersionId}/`,
            ) && response.status() < 400,
        { timeout: 60000 },
      );
      await Promise.all([
        graphDetailResponse,
        versionDetailResponse,
        clickVisibleRowText(page, graphName),
      ]);

      await page.waitForFunction(
        ({ graphId: expectedGraphId, versionId }) =>
          window.location.pathname.endsWith(
            `/dashboard/agents/playground/${expectedGraphId}/build`,
          ) && window.location.search.includes(`version=${versionId}`),
        { timeout: 30000 },
        { graphId, versionId: setup.draftVersionId },
      );
      await waitForVisibleText(page, graphName, { exact: true });
      await waitForVisibleText(page, "Agent", { exact: true });
      await waitForVisibleText(page, "Version 1", { exact: true });
      await waitForVisibleText(page, "Draft", { exact: true });
      await waitForVisibleText(page, "Agent Builder", { exact: true });
      await waitForVisibleText(page, "Changelog", { exact: true });
      await waitForVisibleText(page, "Executions", { exact: true });
      await waitForVisibleText(page, "LLM Prompt", { exact: true });
      await waitForVisibleText(page, sourceNodeName, { exact: true });
      await waitForVisibleText(page, targetNodeName, { exact: true });
      await waitForSelectorWithSize(page, ".react-flow");

      logStep("open changelog");
      const versionsResponse = page.waitForResponse(
        (response) =>
          response
            .url()
            .includes(`/agent-playground/graphs/${graphId}/versions/`) &&
          !response.url().includes(`/${setup.draftVersionId}/`) &&
          response.status() < 400,
        { timeout: 60000 },
      );
      await clickVisibleText(page, "Changelog", { exact: true });
      await versionsResponse;
      await page.waitForFunction(
        () => window.location.pathname.endsWith("/changelog"),
        { timeout: 30000 },
      );
      await waitForVisibleText(page, "Version 1", { exact: true });
      await waitForVisibleText(page, sourceNodeName, { exact: true });
      await waitForVisibleText(page, targetNodeName, { exact: true });

      logStep("open executions");
      const executionsResponse = page.waitForResponse(
        (response) =>
          response
            .url()
            .includes(`/agent-playground/graphs/${graphId}/executions/`) &&
          response.status() < 400,
        { timeout: 60000 },
      );
      await clickVisibleText(page, "Executions", { exact: true });
      await executionsResponse;
      await page.waitForFunction(
        () => window.location.pathname.endsWith("/executions"),
        { timeout: 30000 },
      );
      await waitForVisibleText(page, "No executions yet", { exact: true });
      await waitForVisibleText(
        page,
        "Run your workflow from the Agent Builder to see results here",
        { exact: true },
      );
      await waitForNoVisibleText(page, "Invalid Date");
      await waitForNoVisibleText(page, "undefined", { exact: true });

      logStep("capture screenshot");
      await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
      evidence.screenshot = SCREENSHOT_PATH;
      evidence.agent_request_count = agentRequests.length;

      assert(
        apiFailures.length === 0,
        `API failures: ${apiFailures.join("; ")}`,
      );
      assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
      assert(
        unexpectedMutations.length === 0,
        `Read-only agent playground smoke fired mutations: ${unexpectedMutations.join("; ")}`,
      );
    } finally {
      await browser.close();
    }

    await auth.client.post(apiPath("/agent-playground/graphs/delete/"), {
      ids: [graphId],
    });
    const deletedGraphDetail = await expectApiError(
      () =>
        auth.client.get(
          apiPath("/agent-playground/graphs/{id}/", { id: graphId }),
        ),
      [404],
      "Deleted agent graph detail unexpectedly succeeded.",
    );
    publicDeleteStatus = deletedGraphDetail.status;

    const postDeleteAudit = await loadAgentGraphDbAudit({ graphId });
    assertAgentGraphPostDeleteAudit(postDeleteAudit);
    evidence.public_delete_status = publicDeleteStatus;
    evidence.post_delete_audit = postDeleteAudit;

    cleanupAudit = await hardDeleteAgentGraph({
      graphId,
      graphNames,
      promptNames,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assert(
      Number(cleanupAudit.remaining_graphs) === 0,
      "Agent graph hard cleanup left graph rows behind.",
    );
    assert(
      Number(cleanupAudit.remaining_prompt_templates) === 0,
      "Agent graph hard cleanup left prompt templates behind.",
    );
    evidence.cleanup = cleanupAudit;

    writeJsonLine(process.stdout, {
      status: "passed",
      app_base: APP_BASE,
      api_base: auth.apiBase,
      organization_id: auth.organizationId,
      workspace_id: auth.workspaceId,
      evidence,
    });
  } catch (error) {
    if (graphId) {
      try {
        cleanupAudit = await hardDeleteAgentGraph({
          graphId,
          graphNames,
          promptNames,
          organizationId: auth.organizationId,
          workspaceId: auth.workspaceId,
        });
        writeJsonLine(process.stderr, { cleanup_after_error: cleanupAudit });
      } catch (cleanupError) {
        process.stderr.write(
          `Agent graph cleanup failed after error: ${cleanupError?.stack || cleanupError}\n`,
        );
      }
    }
    throw error;
  }
}

function writeJsonLine(stream, value) {
  stream.write(`${JSON.stringify(value, null, 2)}\n`);
}

function logStep(message) {
  if (!DEBUG) return;
  process.stderr.write(`[agents-smoke] ${message}\n`);
}

async function createDisposableAgentGraph({
  auth,
  graphName,
  sourceNodeName,
  targetNodeName,
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
      description: `Disposable browser agent graph ${auth.runId}`,
    },
  );
  const graphId = createdGraph.id;
  const draftVersionId = createdGraph.active_version?.id;
  assert(isUuid(graphId), "Agent graph create did not return a graph id.");
  assert(
    isUuid(draftVersionId),
    "Agent graph create did not return an active draft version id.",
  );

  const sourceNode = await auth.client.post(
    apiPath("/agent-playground/graphs/{id}/versions/{version_id}/nodes/", {
      id: graphId,
      version_id: draftVersionId,
    }),
    {
      id: randomUUID(),
      type: "atomic",
      name: sourceNodeName,
      node_template_id: llmTemplate.id,
      position: { x: 40, y: 120 },
      prompt_template: {
        messages: [
          {
            id: "browser-source",
            role: "user",
            content: [
              {
                type: "text",
                text: "Write one test fact about {{topic}}.",
              },
            ],
          },
        ],
        response_format: "text",
        model: "gpt-4o-mini",
        temperature: 0,
        metadata: { api_journey: "agents-playground-smoke" },
      },
    },
  );
  assert(isUuid(sourceNode.id), "Source node create did not return an id.");

  const targetNode = await auth.client.post(
    apiPath("/agent-playground/graphs/{id}/versions/{version_id}/nodes/", {
      id: graphId,
      version_id: draftVersionId,
    }),
    {
      id: randomUUID(),
      type: "atomic",
      name: targetNodeName,
      node_template_id: llmTemplate.id,
      source_node_id: sourceNode.id,
      position: { x: 360, y: 120 },
      prompt_template: {
        messages: [
          {
            id: "browser-target",
            role: "user",
            content: [
              {
                type: "text",
                text: `Summarize the source answer: {{${sourceNodeName}.response}}`,
              },
            ],
          },
        ],
        response_format: "text",
        model: "gpt-4o-mini",
        temperature: 0,
        metadata: { api_journey: "agents-playground-smoke" },
      },
    },
  );
  assert(isUuid(targetNode.id), "Target node create did not return an id.");
  assert(
    targetNode.node_connection?.source_node_id === sourceNode.id,
    "Target node did not create a source node connection.",
  );

  return { graphId, draftVersionId, sourceNode, targetNode };
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
          if (exactMatch) return textContent === expectedText;
          return textContent.includes(expectedText);
        },
      );
    },
    { timeout },
    { text, exact },
  );
}

async function waitForInputPlaceholder(page, placeholder, timeout = 30000) {
  await page.waitForFunction(
    (expectedPlaceholder) =>
      Array.from(document.querySelectorAll("input")).some(
        (input) => input.placeholder === expectedPlaceholder,
      ),
    { timeout },
    placeholder,
  );
}

async function waitForSelectorWithSize(page, selector, timeout = 30000) {
  await page.waitForFunction(
    (targetSelector) => {
      const element = document.querySelector(targetSelector);
      if (!element) return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    },
    { timeout },
    selector,
  );
}

async function typeIntoSearchInput(page, value) {
  await page.waitForSelector('input[placeholder="Search"]', { timeout: 30000 });
  await page.click('input[placeholder="Search"]');
  await page.keyboard.down(modifierKey());
  await page.keyboard.press("A");
  await page.keyboard.up(modifierKey());
  await page.keyboard.press("Backspace");
  await page.type('input[placeholder="Search"]', value);
}

async function clickVisibleRowText(page, text) {
  await page.waitForFunction(
    (expectedText) => {
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
        if (String(element.textContent || "").trim() !== expectedText)
          return false;
        return Boolean(
          element.closest("tr,[role='row'],.MuiTableRow-root,[data-row-id]"),
        );
      });
    },
    { timeout: 30000 },
    text,
  );
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
    const element = Array.from(document.querySelectorAll("body *")).find(
      (candidate) =>
        isVisible(candidate) &&
        String(candidate.textContent || "").trim() === expectedText &&
        Boolean(
          candidate.closest("tr,[role='row'],.MuiTableRow-root,[data-row-id]"),
        ),
    );
    const row = element.closest(
      "tr,[role='row'],.MuiTableRow-root,[data-row-id]",
    );
    row.click();
  }, text);
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
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
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
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      });
      element.click();
    },
    { text, exact },
  );
}

async function expectApiError(fn, expectedStatuses, message) {
  try {
    await fn();
  } catch (error) {
    if (expectedStatuses.includes(error.status)) {
      return { status: error.status, body: error.body };
    }
    throw error;
  }
  throw new Error(message);
}

async function loadAgentGraphDbAudit({ graphId }) {
  const sql = `
WITH target_graph AS (
  SELECT id, organization_id, workspace_id, deleted
  FROM agent_playground_graph
  WHERE id = ${sqlUuid(graphId)}
),
target_versions AS (
  SELECT id, deleted FROM agent_playground_graph_version
  WHERE graph_id IN (SELECT id FROM target_graph)
),
target_nodes AS (
  SELECT id, deleted FROM agent_playground_node
  WHERE graph_version_id IN (SELECT id FROM target_versions)
),
target_datasets AS (
  SELECT gd.id AS graph_dataset_id, gd.dataset_id, gd.deleted AS graph_dataset_deleted
  FROM agent_playground_graph_dataset gd
  WHERE gd.graph_id IN (SELECT id FROM target_graph)
)
SELECT json_build_object(
  'graph_total', (SELECT count(*) FROM target_graph),
  'graph_visible', (SELECT count(*) FROM target_graph WHERE deleted = false),
  'graph_deleted', COALESCE((SELECT bool_or(deleted) FROM target_graph), false),
  'organization_id', (SELECT organization_id FROM target_graph LIMIT 1),
  'workspace_id', (SELECT workspace_id FROM target_graph LIMIT 1),
  'version_total', (SELECT count(*) FROM target_versions),
  'version_visible', (SELECT count(*) FROM target_versions WHERE deleted = false),
  'node_total', (SELECT count(*) FROM target_nodes),
  'node_visible', (SELECT count(*) FROM target_nodes WHERE deleted = false),
  'port_total', (
    SELECT count(*) FROM agent_playground_port
    WHERE node_id IN (SELECT id FROM target_nodes)
  ),
  'port_visible', (
    SELECT count(*) FROM agent_playground_port
    WHERE node_id IN (SELECT id FROM target_nodes) AND deleted = false
  ),
  'node_connection_total', (
    SELECT count(*) FROM agent_playground_node_connection
    WHERE graph_version_id IN (SELECT id FROM target_versions)
  ),
  'node_connection_visible', (
    SELECT count(*) FROM agent_playground_node_connection
    WHERE graph_version_id IN (SELECT id FROM target_versions) AND deleted = false
  ),
  'edge_total', (
    SELECT count(*) FROM agent_playground_edge
    WHERE graph_version_id IN (SELECT id FROM target_versions)
  ),
  'edge_visible', (
    SELECT count(*) FROM agent_playground_edge
    WHERE graph_version_id IN (SELECT id FROM target_versions) AND deleted = false
  ),
  'prompt_template_node_total', (
    SELECT count(*) FROM agent_playground_prompt_template_node
    WHERE node_id IN (SELECT id FROM target_nodes)
  ),
  'prompt_template_node_visible', (
    SELECT count(*) FROM agent_playground_prompt_template_node
    WHERE node_id IN (SELECT id FROM target_nodes) AND deleted = false
  ),
  'graph_dataset_total', (SELECT count(*) FROM target_datasets),
  'graph_dataset_visible', (
    SELECT count(*) FROM target_datasets WHERE graph_dataset_deleted = false
  )
);
`;
  return runPostgresJson(sql);
}

function assertAgentGraphPreDeleteAudit(
  audit,
  { organizationId, workspaceId },
) {
  assert(audit.graph_visible === 1, "Created agent graph was not DB-visible.");
  assert(
    audit.organization_id === organizationId,
    "Created agent graph organization_id mismatch.",
  );
  assert(
    audit.workspace_id === workspaceId,
    "Created agent graph workspace_id mismatch.",
  );
  assert(audit.version_visible >= 1, "Agent graph had no visible version.");
  assert(
    audit.node_visible === 2,
    "Agent graph should have two visible nodes.",
  );
  assert(
    audit.port_visible >= 4,
    "Agent graph should have visible node ports.",
  );
  assert(
    audit.node_connection_visible === 1,
    "Agent graph should have one visible node connection.",
  );
  assert(
    audit.edge_visible === 1,
    "Agent graph should have one visible edge after node connection.",
  );
  assert(
    audit.prompt_template_node_visible === 2,
    "Agent graph should have two visible prompt-template node links.",
  );
  assert(
    audit.graph_dataset_visible === 1,
    "Agent graph should have one visible graph dataset link.",
  );
}

function assertAgentGraphPostDeleteAudit(audit) {
  assert(
    audit.graph_total === 1,
    "Deleted agent graph row was missing from DB.",
  );
  assert(audit.graph_visible === 0, "Deleted agent graph remained visible.");
  assert(audit.graph_deleted === true, "Agent graph was not soft-deleted.");
  assert(
    audit.version_visible === 0,
    "Deleted agent graph versions remained visible.",
  );
  assert(
    audit.node_visible === 0,
    "Deleted agent graph nodes remained visible.",
  );
  assert(
    audit.port_visible === 0,
    "Deleted agent graph ports remained visible.",
  );
  assert(
    audit.node_connection_visible === 0,
    "Deleted agent graph node connections remained visible.",
  );
  assert(
    audit.edge_visible === 0,
    "Deleted agent graph edges remained visible.",
  );
  assert(
    audit.prompt_template_node_visible === 0,
    "Deleted agent graph prompt-template node links remained visible.",
  );
  assert(
    audit.graph_dataset_visible === 0,
    "Deleted agent graph dataset link remained visible.",
  );
}

async function hardDeleteAgentGraph({
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

function sqlTextLiteral(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`;
}

function sqlTextArray(values) {
  const rows = values || [];
  assert(rows.length > 0, "SQL text array cannot be empty.");
  return `ARRAY[${rows.map((value) => sqlTextLiteral(value)).join(", ")}]::text[]`;
}

function isGraphListResponse(response) {
  try {
    const url = new URL(response.url());
    return url.pathname.endsWith("/agent-playground/graphs/");
  } catch {
    return false;
  }
}

function isAgentPlaygroundApiUrl(url) {
  return url.includes("/agent-playground/");
}

function modifierKey() {
  return process.platform === "darwin" ? "Meta" : "Control";
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

main().catch((error) => {
  process.stderr.write(`${error?.stack || error}\n`);
  process.exitCode = 1;
});
