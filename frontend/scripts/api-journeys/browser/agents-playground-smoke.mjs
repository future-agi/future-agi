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
  const editedSourceNodeName = `${sourceNodeName}_edited`.slice(0, 80);
  const targetNodeName = `browser_target_${marker}`.slice(0, 80);
  const connectedNodeName = "llm_prompt_node_1";
  const sourcePromptText = "Write one test fact about {{topic}}.";
  const variableInitialValue = `browser variable initial ${auth.runId}`;
  const variableUpdatedValue = `browser variable updated ${auth.runId}`;
  const executionInputValue = `browser execution input ${auth.runId}`;
  const executionOutputValue = `browser execution output ${auth.runId}`;
  const graphExecutionId = randomUUID();
  const nodeExecutionId = randomUUID();
  const inputDataId = randomUUID();
  const outputDataId = randomUUID();
  const graphNames = [graphName];
  const promptNames = [
    sourceNodeName,
    editedSourceNodeName,
    targetNodeName,
    connectedNodeName,
  ];
  let graphId = null;
  let publicDeleteStatus = null;
  let cleanupAudit = null;

  try {
    const setup = await createDisposableAgentGraph({
      auth,
      graphName,
      sourceNodeName,
      targetNodeName,
      sourcePromptText,
    });
    graphId = setup.graphId;

    const variableSetup = await seedGraphVariable({
      auth,
      graphId,
      versionId: setup.draftVersionId,
      columnName: "topic",
      value: variableInitialValue,
    });

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
    const expectedMutations = [];
    const agentRequests = [];
    const evidence = {
      graph_id: graphId,
      draft_version_id: setup.draftVersionId,
      source_node_id: setup.sourceNode.id,
      target_node_id: setup.targetNode.id,
      node_connection_id: setup.targetNode.node_connection?.id,
      graph_name: graphName,
      search_result_count: graphRows.length,
      variable_setup: variableSetup,
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
        if (
          request.method() === "POST" &&
          url.includes(
            `/agent-playground/graphs/${graphId}/versions/${setup.draftVersionId}/nodes/`,
          )
        ) {
          expectedMutations.push(`${request.method()} ${url}`);
          return;
        }
        if (
          request.method() === "PATCH" &&
          url.includes(
            `/agent-playground/graphs/${graphId}/versions/${setup.draftVersionId}/nodes/${setup.sourceNode.id}/`,
          )
        ) {
          expectedMutations.push(`${request.method()} ${url}`);
          return;
        }
        if (
          request.method() === "PUT" &&
          url.includes(
            `/agent-playground/graphs/${graphId}/dataset/cells/${variableSetup.cell_id}/`,
          )
        ) {
          expectedMutations.push(`${request.method()} ${url}`);
          return;
        }
        unexpectedMutations.push(`${request.method()} ${url}`);
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
      evidence.rendered_edge_count = await waitForRenderedEdgeCount(page, 1);

      logStep("save source prompt drawer");
      const sourceNodeDetailResponse = page.waitForResponse(
        (response) =>
          response.request().method() === "GET" &&
          response
            .url()
            .includes(
              `/agent-playground/graphs/${graphId}/versions/${setup.draftVersionId}/nodes/${setup.sourceNode.id}/`,
            ) &&
          response.status() < 400,
        { timeout: 60000 },
      );
      await clickCanvasNode(page, sourceNodeName);
      const sourceNodeDetail = nodePayloadFromResponse(
        await sourceNodeDetailResponse.then((response) => response.json()),
      );
      const sourcePromptTemplate = promptTemplateFromNode(sourceNodeDetail);
      assert(
        sourcePromptTemplate?.model === "gpt-4o-mini",
        "Prompt node detail did not return the seeded model.",
      );
      assert(
        promptTemplateMessagesInclude(sourcePromptTemplate, sourcePromptText),
        "Prompt node detail did not return the seeded message.",
      );
      await waitForVisibleText(page, "Prompt Name");
      await waitForInputValue(page, sourceNodeName);
      await waitForPromptEditorText(page, ["Write one test fact", "topic"]);
      await waitForVisibleText(page, "gpt-4o-mini");
      await replaceInputValue(page, sourceNodeName, editedSourceNodeName);

      const sourceNodePatchResponse = page.waitForResponse(
        (response) =>
          response.request().method() === "PATCH" &&
          response
            .url()
            .includes(
              `/agent-playground/graphs/${graphId}/versions/${setup.draftVersionId}/nodes/${setup.sourceNode.id}/`,
            ) &&
          response.status() < 400,
        { timeout: 60000 },
      );
      await waitForEnabledButton(page, "Save prompt");
      await clickVisibleText(page, "Save prompt", { exact: true });
      const patchedSourceNode = nodePayloadFromResponse(
        await sourceNodePatchResponse.then((response) => response.json()),
      );
      const patchedPromptTemplate = promptTemplateFromNode(patchedSourceNode);
      assert(
        patchedSourceNode?.name === editedSourceNodeName,
        "Prompt drawer save did not return the edited prompt name.",
      );
      assert(
        patchedPromptTemplate?.model === "gpt-4o-mini",
        "Prompt drawer save lost the seeded model.",
      );
      assert(
        promptTemplateMessagesInclude(patchedPromptTemplate, sourcePromptText),
        "Prompt drawer save lost the seeded prompt message.",
      );
      await waitForVisibleText(page, editedSourceNodeName, { exact: true });
      const sourceNodeReadback = await loadAgentNode({
        auth,
        graphId,
        versionId: setup.draftVersionId,
        nodeId: setup.sourceNode.id,
      });
      const readbackPromptTemplate = promptTemplateFromNode(sourceNodeReadback);
      assert(
        sourceNodeReadback?.name === editedSourceNodeName,
        "Prompt drawer save did not persist the edited prompt name.",
      );
      assert(
        readbackPromptTemplate?.model === "gpt-4o-mini",
        "Prompt drawer readback lost the seeded model.",
      );
      assert(
        promptTemplateMessagesInclude(readbackPromptTemplate, sourcePromptText),
        "Prompt drawer readback lost the seeded prompt message.",
      );
      evidence.prompt_drawer_save = {
        node_id: setup.sourceNode.id,
        original_name: sourceNodeName,
        updated_name: editedSourceNodeName,
        model: readbackPromptTemplate.model,
        message_verified: true,
      };

      logStep("add connected node from builder");
      const connectedNodeCreateResponse = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          response
            .url()
            .includes(
              `/agent-playground/graphs/${graphId}/versions/${setup.draftVersionId}/nodes/`,
            ) &&
          response.status() < 400,
        { timeout: 60000 },
      );
      await addConnectedBlankPromptFromNode(page, targetNodeName);
      const connectedNodePayload = await connectedNodeCreateResponse.then(
        (response) => response.json(),
      );
      const connectedNode = connectedNodePayload?.result;
      const connectedNodeConnection =
        connectedNode?.node_connection || connectedNode?.nodeConnection;
      assert(
        connectedNode?.name === connectedNodeName,
        "Builder connected-node add did not create the expected prompt node.",
      );
      assert(
        connectedNodeConnection?.source_node_id === setup.targetNode.id,
        "Builder connected-node add did not persist the expected source node.",
      );
      assert(
        connectedNodeConnection?.target_node_id === connectedNode.id,
        "Builder connected-node add did not persist the expected target node.",
      );
      await waitForVisibleText(page, connectedNodeName, { exact: true });
      evidence.rendered_edge_count_after_connected_add =
        await waitForRenderedEdgeCount(page, 2);
      const postBuilderMutationAudit = await loadAgentGraphDbAudit({ graphId });
      assert(
        postBuilderMutationAudit.node_visible === 3,
        "Builder connected-node add did not leave three visible nodes.",
      );
      assert(
        postBuilderMutationAudit.node_connection_visible === 2,
        "Builder connected-node add did not leave two visible node connections.",
      );
      assert(
        postBuilderMutationAudit.prompt_template_node_visible === 3,
        "Builder connected-node add did not create a visible prompt-template node link.",
      );
      evidence.browser_connected_node = {
        node_id: connectedNode.id,
        node_name: connectedNode.name,
        node_connection_id: connectedNodeConnection.id,
        source_node_id: connectedNodeConnection.source_node_id,
        target_node_id: connectedNodeConnection.target_node_id,
        post_mutation_audit: postBuilderMutationAudit,
      };

      logStep("edit global variable drawer");
      const graphDatasetResponse = page.waitForResponse(
        (response) =>
          response
            .url()
            .includes(`/agent-playground/graphs/${graphId}/dataset/`) &&
          response.status() < 400,
        { timeout: 60000 },
      );
      await clickVisibleText(page, "Add input variables", { exact: true });
      await graphDatasetResponse;
      await waitForVisibleText(page, "Variables", { exact: true });
      await waitForVisibleText(
        page,
        "Define values for your prompt variables",
        {
          exact: true,
        },
      );
      await waitForVisibleText(page, "{{topic}}", { exact: true });
      await waitForInputValue(page, variableInitialValue);

      await replaceInputValue(page, variableInitialValue, variableUpdatedValue);
      await waitForEnabledButton(page, "Save");
      const variableUpdateResponse = page.waitForResponse(
        (response) =>
          response
            .url()
            .includes(
              `/agent-playground/graphs/${graphId}/dataset/cells/${variableSetup.cell_id}/`,
            ) && response.status() < 400,
        { timeout: 60000 },
      );
      await clickVisibleText(page, "Save", { exact: true });
      await variableUpdateResponse;
      await waitForVisibleText(page, "Variables saved successfully");

      const variableReadback = await loadGraphVariable({
        auth,
        graphId,
        versionId: setup.draftVersionId,
        columnName: "topic",
      });
      assert(
        variableReadback.value === variableUpdatedValue,
        "Browser variable drawer save did not persist the topic value.",
      );
      evidence.variable_drawer = {
        ...variableReadback,
        initial_value: variableInitialValue,
        updated_value: variableUpdatedValue,
      };

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
      await waitForVisibleText(page, editedSourceNodeName, { exact: true });
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

      logStep("seed execution fixture");
      const inputPort = findPort(setup.sourceNode, {
        direction: "input",
        displayName: "topic",
      });
      const outputPort = findPort(setup.sourceNode, {
        direction: "output",
        displayName: "response",
      });
      assert(inputPort?.id, "Source node did not expose topic input.");
      assert(outputPort?.id, "Source node did not expose response output.");

      const seedAudit = await seedAgentExecutionFixture({
        graphExecutionId,
        nodeExecutionId,
        inputDataId,
        outputDataId,
        graphVersionId: setup.draftVersionId,
        nodeId: setup.sourceNode.id,
        inputPortId: inputPort.id,
        outputPortId: outputPort.id,
        inputValue: executionInputValue,
        outputValue: executionOutputValue,
      });
      assert(
        seedAudit.graph_execution_visible === 1 &&
          seedAudit.node_execution_visible === 1 &&
          seedAudit.execution_data_visible === 2,
        "Seeded browser execution fixture was not DB-visible.",
      );
      evidence.seeded_execution = {
        graph_execution_id: graphExecutionId,
        node_execution_id: nodeExecutionId,
        input_port_id: inputPort.id,
        output_port_id: outputPort.id,
        seed_audit: seedAudit,
      };

      logStep("reload populated executions");
      const populatedExecutionsResponse = page.waitForResponse(
        (response) =>
          response
            .url()
            .includes(`/agent-playground/graphs/${graphId}/executions/`) &&
          response.status() < 400,
        { timeout: 60000 },
      );
      const executionDetailResponse = page.waitForResponse(
        (response) =>
          response
            .url()
            .includes(
              `/agent-playground/graphs/${graphId}/executions/${graphExecutionId}/`,
            ) && response.status() < 400,
        { timeout: 60000 },
      );
      const nodeExecutionDetailResponse = page.waitForResponse(
        (response) =>
          response
            .url()
            .includes(
              `/agent-playground/executions/${graphExecutionId}/nodes/${nodeExecutionId}/`,
            ) && response.status() < 400,
        { timeout: 60000 },
      );
      await page.reload({ waitUntil: "domcontentloaded" });
      await populatedExecutionsResponse;
      await executionDetailResponse;
      await nodeExecutionDetailResponse;

      await waitForNoVisibleText(page, "No executions yet", { exact: true });
      await waitForVisibleText(page, "Success", { exact: true });
      await waitForVisibleText(page, "Agent flow results", { exact: true });
      await waitForVisibleText(page, "Output", { exact: true });
      await waitForVisibleText(page, executionOutputValue);
      await waitForNoVisibleText(page, "Invalid Date");
      await waitForNoVisibleText(page, "undefined", { exact: true });

      await clickVisibleLabelText(page, "Show inputs");
      await waitForVisibleText(page, "Input", { exact: true });
      await waitForVisibleText(page, executionInputValue);

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
        `Agent playground smoke fired unexpected mutations: ${unexpectedMutations.join("; ")}`,
      );
      assert(
        expectedMutations.length === 3,
        `Expected one prompt save, one browser node add mutation, and one variable update mutation, saw ${expectedMutations.length}.`,
      );
      evidence.expected_mutation_count = expectedMutations.length;
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
    const executionPostDeleteAudit = await loadAgentExecutionDbAudit({
      graphId,
      graphExecutionId,
      nodeExecutionId,
    });
    assert(
      executionPostDeleteAudit.graph_execution_visible === 0 &&
        executionPostDeleteAudit.node_execution_visible === 0 &&
        executionPostDeleteAudit.execution_data_visible === 0,
      "Public graph delete left browser execution rows visible.",
    );
    evidence.public_delete_status = publicDeleteStatus;
    evidence.post_delete_audit = postDeleteAudit;
    evidence.execution_post_delete_audit = executionPostDeleteAudit;

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
  sourcePromptText,
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
                text: sourcePromptText,
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
  assert(
    readback.value === value,
    "Graph variable seed did not persist the requested value.",
  );
  return {
    ...readback,
    initial_value: value,
  };
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

async function loadAgentNode({ auth, graphId, versionId, nodeId }) {
  return nodePayloadFromResponse(
    await auth.client.get(
      apiPath(
        "/agent-playground/graphs/{id}/versions/{version_id}/nodes/{node_id}/",
        {
          id: graphId,
          version_id: versionId,
          node_id: nodeId,
        },
      ),
    ),
  );
}

function nodePayloadFromResponse(payload) {
  return payload?.result || payload?.node || payload;
}

function promptTemplateFromNode(node) {
  return node?.prompt_template || node?.promptTemplate || null;
}

function promptTemplateMessagesInclude(promptTemplate, expectedText) {
  const normalizedExpected = String(expectedText).replace(/\s+/g, " ").trim();
  return (promptTemplate?.messages || []).some((message) => {
    const content = Array.isArray(message.content)
      ? message.content
          .map((block) => block?.text || "")
          .join("")
          .replace(/\s+/g, " ")
          .trim()
      : String(message.content || "")
          .replace(/\s+/g, " ")
          .trim();
    return content === normalizedExpected;
  });
}

function getCellColumnId(cell) {
  return cell?.columnId || cell?.column_id;
}

function findPort(node, { direction, displayName }) {
  return (node.ports || []).find(
    (port) =>
      port.direction === direction &&
      (displayName ? port.display_name === displayName : true),
  );
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

async function waitForInputValue(page, value, timeout = 30000) {
  await page.waitForFunction(
    (expectedValue) =>
      Array.from(document.querySelectorAll("input")).some(
        (input) => input.value === expectedValue,
      ),
    { timeout },
    value,
  );
}

async function replaceInputValue(page, currentValue, nextValue) {
  await waitForInputValue(page, currentValue);
  await page.evaluate(
    ({ currentValue: expectedValue, nextValue: replacementValue }) => {
      const input = Array.from(document.querySelectorAll("input")).find(
        (candidate) => candidate.value === expectedValue,
      );
      if (!input) throw new Error("Input value not found.");
      const valueSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      valueSetter.call(input, replacementValue);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    },
    { currentValue, nextValue },
  );
  await waitForInputValue(page, nextValue);
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

async function waitForRenderedEdgeCount(page, minimumCount, timeout = 30000) {
  await page.waitForFunction(
    (expectedCount) => {
      const edges = Array.from(document.querySelectorAll(".react-flow__edge"));
      const renderedEdges = edges.filter((edge) => {
        const path = edge.querySelector("path");
        return path?.getAttribute("d");
      });
      return renderedEdges.length >= expectedCount;
    },
    { timeout },
    minimumCount,
  );
  return page.evaluate(
    () =>
      Array.from(document.querySelectorAll(".react-flow__edge")).filter(
        (edge) => edge.querySelector("path")?.getAttribute("d"),
      ).length,
  );
}

async function clickCanvasNode(page, nodeLabel) {
  const point = await page.evaluate((expectedLabel) => {
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
    const node = Array.from(
      document.querySelectorAll(".react-flow__node"),
    ).find(
      (candidate) =>
        isVisible(candidate) &&
        String(candidate.textContent || "").includes(expectedLabel),
    );
    if (!node) return null;
    const rect = node.getBoundingClientRect();
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
  }, nodeLabel);
  assert(point, `Could not find canvas node ${nodeLabel}.`);
  await page.mouse.click(point.x, point.y);
}

async function waitForPromptEditorText(page, terms, timeout = 30000) {
  const expectedTerms = Array.isArray(terms) ? terms : [terms];
  await page.waitForFunction(
    (termsToFind) => {
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
      return Array.from(document.querySelectorAll(".ql-editor")).some(
        (editor) => {
          if (!isVisible(editor)) return false;
          const quillText = editor.__quill?.getText?.() || "";
          const text = `${editor.textContent || ""} ${quillText}`;
          return termsToFind.every((term) => text.includes(term));
        },
      );
    },
    { timeout },
    expectedTerms,
  );
}

async function addConnectedBlankPromptFromNode(page, sourceNodeLabel) {
  await clickCanvasNodeAddButton(page, sourceNodeLabel);
  await clickNodeTemplateFromOpenPopper(page, "LLM Prompt");
  await waitForVisibleText(page, "Add Blank Prompt", { exact: true });
  await clickVisibleText(page, "Add Blank Prompt", { exact: true });
}

async function clickCanvasNodeAddButton(page, nodeLabel) {
  const point = await page.evaluate((expectedLabel) => {
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
    const node = Array.from(
      document.querySelectorAll(".react-flow__node"),
    ).find(
      (candidate) =>
        isVisible(candidate) &&
        String(candidate.textContent || "").includes(expectedLabel),
    );
    if (!node) return null;
    const nodeRect = node.getBoundingClientRect();
    const candidates = Array.from(node.querySelectorAll("*"))
      .map((element) => ({
        element,
        rect: element.getBoundingClientRect(),
        style: window.getComputedStyle(element),
      }))
      .filter(({ rect, style }) => {
        return (
          rect.width >= 20 &&
          rect.width <= 34 &&
          rect.height >= 20 &&
          rect.height <= 34 &&
          rect.left > nodeRect.right - 8 &&
          rect.top > nodeRect.top - 40 &&
          rect.top < nodeRect.bottom + 40 &&
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          style.pointerEvents !== "none"
        );
      })
      .sort((a, b) => b.rect.left - a.rect.left);
    const target = candidates[0];
    if (!target) return null;
    return {
      x: target.rect.left + target.rect.width / 2,
      y: target.rect.top + target.rect.height / 2,
    };
  }, nodeLabel);
  assert(point, `Could not find Add button for node ${nodeLabel}.`);
  await page.mouse.click(point.x, point.y);
  await waitForNodeTemplatePopper(page, "LLM Prompt");
}

async function waitForNodeTemplatePopper(page, text, timeout = 30000) {
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
      return Array.from(document.querySelectorAll(".MuiPaper-root")).some(
        (paper) =>
          isVisible(paper) &&
          String(paper.textContent || "").includes(expectedText) &&
          !String(paper.textContent || "").includes("Add Blank Prompt"),
      );
    },
    { timeout },
    text,
  );
}

async function clickNodeTemplateFromOpenPopper(page, text) {
  await waitForNodeTemplatePopper(page, text);
  const clicked = await page.evaluate((expectedText) => {
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
    const paper = Array.from(document.querySelectorAll(".MuiPaper-root")).find(
      (candidate) =>
        isVisible(candidate) &&
        String(candidate.textContent || "").includes(expectedText) &&
        !String(candidate.textContent || "").includes("Add Blank Prompt"),
    );
    if (!paper) return false;
    const label = Array.from(paper.querySelectorAll("*")).find(
      (candidate) =>
        isVisible(candidate) &&
        normalized(candidate.textContent) === expectedText,
    );
    const clickable = label?.closest(".MuiBox-root") || label;
    if (!clickable) return false;
    clickable.click();
    return true;
  }, text);
  assert(clicked, `Could not click node template ${text}.`);
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

async function clickVisibleLabelText(page, text) {
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
      return Array.from(document.querySelectorAll("label")).some(
        (element) =>
          isVisible(element) &&
          String(element.textContent || "")
            .trim()
            .includes(expectedText),
      );
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
    const element = Array.from(document.querySelectorAll("label")).find(
      (candidate) =>
        isVisible(candidate) &&
        String(candidate.textContent || "")
          .trim()
          .includes(expectedText),
    );
    element.click();
  }, text);
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

async function seedAgentExecutionFixture({
  graphExecutionId,
  nodeExecutionId,
  inputDataId,
  outputDataId,
  graphVersionId,
  nodeId,
  inputPortId,
  outputPortId,
  inputValue,
  outputValue,
}) {
  const sql = `
INSERT INTO agent_playground_graph_execution (
  id, graph_version_id, status, input_payload, output_payload,
  started_at, completed_at, created_at, updated_at, deleted
) VALUES (
  ${sqlUuid(graphExecutionId)},
  ${sqlUuid(graphVersionId)},
  'success',
  jsonb_build_object('topic', ${sqlTextLiteral(inputValue)}),
  jsonb_build_object('response', ${sqlTextLiteral(outputValue)}),
  now() - interval '7 seconds',
  now(),
  now(),
  now(),
  false
);

INSERT INTO agent_playground_node_execution (
  id, graph_execution_id, node_id, status,
  started_at, completed_at, created_at, updated_at, deleted
) VALUES (
  ${sqlUuid(nodeExecutionId)},
  ${sqlUuid(graphExecutionId)},
  ${sqlUuid(nodeId)},
  'success',
  now() - interval '7 seconds',
  now(),
  now(),
  now(),
  false
);

INSERT INTO agent_playground_execution_data (
  id, node_execution_id, node_id, port_id, payload,
  validation_errors, is_valid, created_at, updated_at, deleted
) VALUES
  (
    ${sqlUuid(inputDataId)},
    ${sqlUuid(nodeExecutionId)},
    ${sqlUuid(nodeId)},
    ${sqlUuid(inputPortId)},
    to_jsonb(${sqlTextLiteral(inputValue)}::text),
    NULL,
    true,
    now(),
    now(),
    false
  ),
  (
    ${sqlUuid(outputDataId)},
    ${sqlUuid(nodeExecutionId)},
    ${sqlUuid(nodeId)},
    ${sqlUuid(outputPortId)},
    to_jsonb(${sqlTextLiteral(outputValue)}::text),
    NULL,
    true,
    now(),
    now(),
    false
  );

${agentExecutionAuditSql({ graphExecutionId, nodeExecutionId })}
`;
  return runPostgresJson(sql);
}

async function loadAgentExecutionDbAudit({
  graphId,
  graphExecutionId,
  nodeExecutionId,
}) {
  const sql = `
SELECT json_build_object(
  'graph_visible', (
    SELECT count(*) FROM agent_playground_graph
    WHERE id = ${sqlUuid(graphId)} AND deleted = false
  ),
  ${agentExecutionAuditFields({ graphExecutionId, nodeExecutionId })}
);
`;
  return runPostgresJson(sql);
}

function agentExecutionAuditSql({ graphExecutionId, nodeExecutionId }) {
  return `SELECT json_build_object(
  ${agentExecutionAuditFields({ graphExecutionId, nodeExecutionId })}
);`;
}

function agentExecutionAuditFields({ graphExecutionId, nodeExecutionId }) {
  return `
  'graph_execution_visible', (
    SELECT count(*) FROM agent_playground_graph_execution
    WHERE id = ${sqlUuid(graphExecutionId)} AND deleted = false
  ),
  'node_execution_visible', (
    SELECT count(*) FROM agent_playground_node_execution
    WHERE id = ${sqlUuid(nodeExecutionId)} AND deleted = false
  ),
  'execution_data_visible', (
    SELECT count(*) FROM agent_playground_execution_data
    WHERE node_execution_id = ${sqlUuid(nodeExecutionId)} AND deleted = false
  )`;
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
