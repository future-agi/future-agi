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
const SCREENSHOT_PATH = "/tmp/agents-playground-versions-smoke.png";
const DEBUG = process.env.DEBUG_AGENT_SMOKE === "1";

async function main() {
  const auth = await createAuthenticatedContext();
  const marker = auth.runId.replace(/[^a-z0-9]/gi, "").slice(0, 18);
  const graphName = `browser agent versions ${marker}`;
  const sourceNodeName = `version_source_${marker}`.slice(0, 80);
  const targetNodeName = `version_target_${marker}`.slice(0, 80);
  const browserEditedSourceNodeName = `version_source_${marker}_edited`.slice(
    0,
    80,
  );
  const restoreNodeName = `version_restore_${marker}`.slice(0, 80);
  const sourcePromptText = "Write one versioned fact about {{topic}}.";
  const firstCommitMessage = `browser save v1 ${auth.runId}`;
  const browserCommitMessage = `browser save v2 ${auth.runId}`;
  const restoreCommitMessage = `api activate v3 ${auth.runId}`;
  const graphNames = [graphName];
  const promptNames = [
    sourceNodeName,
    targetNodeName,
    browserEditedSourceNodeName,
    restoreNodeName,
  ];
  let graphId = null;
  let browserCreatedVersionId = null;
  let restoreVersionId = null;
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

    const preDeleteAudit = await loadAgentGraphDbAudit({ graphId });
    assertAgentGraphPreDeleteAudit(preDeleteAudit, {
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });

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
      graph_name: graphName,
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
    await installBrowserHelpers(page);
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
      const method = request.method();
      const pathName = new URL(url).pathname;
      agentRequests.push(`${method} ${pathName}`);
      if (!["POST", "PATCH", "PUT", "DELETE"].includes(method)) return;

      if (
        method === "POST" &&
        pathName.endsWith(`/agent-playground/graphs/${graphId}/versions/`)
      ) {
        expectedMutations.push(`browser-create-v2 ${method} ${url}`);
        return;
      }
      if (
        method === "PUT" &&
        pathName.endsWith(
          `/agent-playground/graphs/${graphId}/versions/${setup.draftVersionId}/`,
        )
      ) {
        expectedMutations.push(`save-v1 ${method} ${url}`);
        return;
      }
      if (
        browserCreatedVersionId &&
        method === "PUT" &&
        pathName.endsWith(
          `/agent-playground/graphs/${graphId}/versions/${browserCreatedVersionId}/`,
        )
      ) {
        expectedMutations.push(`save-v2 ${method} ${url}`);
        return;
      }
      if (
        method === "POST" &&
        pathName.endsWith(
          `/agent-playground/graphs/${graphId}/versions/${setup.draftVersionId}/activate/`,
        )
      ) {
        expectedMutations.push(`restore-v1 ${method} ${url}`);
        return;
      }
      unexpectedMutations.push(`${method} ${url}`);
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
      logStep("open draft version in builder");
      await openBuilderVersion(page, {
        graphId,
        versionId: setup.draftVersionId,
      });
      await waitForVisibleText(page, graphName, { exact: true });
      await waitForVisibleText(page, "Agent Builder", { exact: true });
      await waitForVisibleText(page, "Version 1", { exact: true });
      await waitForVisibleText(page, "Draft", { exact: true });
      await waitForVisibleText(page, sourceNodeName, { exact: true });
      await waitForVisibleText(page, targetNodeName, { exact: true });
      evidence.initial_rendered_edge_count = await waitForRenderedEdgeCount(
        page,
        1,
      );

      logStep("save initial draft as active version 1");
      const firstSaveResponse = waitForVersionSaveResponse(page, {
        graphId,
        versionId: setup.draftVersionId,
      });
      await openSaveAgentDialog(page, firstCommitMessage);
      const firstSavedVersion = unwrapBrowserResult(
        await firstSaveResponse.then((response) => response.json()),
      );
      assert(
        firstSavedVersion?.id === setup.draftVersionId &&
          firstSavedVersion.status === "active",
        "Saving Version 1 did not return the active initial version.",
      );
      assert(
        firstSavedVersion.commit_message === firstCommitMessage,
        "Saving Version 1 did not persist the commit message.",
      );
      await waitForNoVisibleText(page, "Draft", { exact: true });
      await waitForNoVisibleText(page, "Commit Message", { exact: true });

      logStep("create browser draft version 2 from active prompt edit");
      const sourceNodeDetailResponse = page
        .waitForResponse(
          (response) =>
            response.request().method() === "GET" &&
            response
              .url()
              .includes(
                `/agent-playground/graphs/${graphId}/versions/${setup.draftVersionId}/nodes/${setup.sourceNode.id}/`,
              ) &&
            response.status() < 400,
          { timeout: 10000 },
        )
        .catch(() => null);
      await clickCanvasNode(page, sourceNodeName);
      await waitForVisibleText(page, "Prompt Name");
      const sourceNodeDetailResult = await sourceNodeDetailResponse;
      evidence.browser_source_node_detail_status =
        sourceNodeDetailResult?.status() || "cached_or_inline";
      logStep("source prompt drawer loaded");
      await waitForInputValue(page, sourceNodeName);
      await waitForVisibleText(page, "gpt-4o-mini");
      await replaceInputValue(
        page,
        sourceNodeName,
        browserEditedSourceNodeName,
      );
      logStep("source prompt name edited");

      const browserDraftCreateResponse = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          response
            .url()
            .endsWith(`/agent-playground/graphs/${graphId}/versions/`) &&
          response.status() < 400,
        { timeout: 60000 },
      );
      await waitForEnabledButton(page, "Save prompt");
      logStep("click save prompt for browser draft create");
      await clickVisibleText(page, "Save prompt", { exact: true });
      const browserCreatedVersion = unwrapBrowserResult(
        await browserDraftCreateResponse.then((response) => response.json()),
      );
      logStep("browser draft create response received");
      browserCreatedVersionId = browserCreatedVersion?.id;
      assert(
        isUuid(browserCreatedVersionId),
        "Browser draft create returned no version id.",
      );
      assert(
        browserCreatedVersion.status === "draft",
        "Browser draft create did not return draft status.",
      );
      assert(
        versionNodesContain(browserCreatedVersion, browserEditedSourceNodeName),
        "Browser draft create response did not include the edited prompt node.",
      );
      const browserVersionLabel = `Version ${
        browserCreatedVersion.version_number || 2
      }`;
      await page.waitForFunction(
        ({ graphId: expectedGraphId, versionId }) =>
          window.location.pathname.endsWith(
            `/dashboard/agents/playground/${expectedGraphId}/build`,
          ) && window.location.search.includes(`version=${versionId}`),
        { timeout: 30000 },
        { graphId, versionId: browserCreatedVersionId },
      );
      await waitForVisibleText(page, browserVersionLabel, { exact: true });
      await waitForVisibleText(page, "Draft", { exact: true });
      await waitForVisibleText(page, browserEditedSourceNodeName, {
        exact: true,
      });
      evidence.browser_created_draft = {
        version_id: browserCreatedVersionId,
        status: browserCreatedVersion.status,
        version_number: browserCreatedVersion.version_number,
        edited_source_node_name: browserEditedSourceNodeName,
      };

      const activeV1AfterBrowserCreate = await loadAgentVersion({
        auth,
        graphId,
        versionId: setup.draftVersionId,
      });
      assert(
        activeV1AfterBrowserCreate.status === "active",
        "Browser-created draft should leave Version 1 active until another version is promoted.",
      );
      evidence.version_1_status_after_browser_draft_create =
        activeV1AfterBrowserCreate.status;

      logStep("save browser-created draft as active version 2");
      const browserSaveResponse = waitForVersionSaveResponse(page, {
        graphId,
        versionId: browserCreatedVersionId,
      });
      await openSaveAgentDialog(page, browserCommitMessage);
      const browserSavedVersion = unwrapBrowserResult(
        await browserSaveResponse.then((response) => response.json()),
      );
      assert(
        browserSavedVersion?.id === browserCreatedVersionId &&
          browserSavedVersion.status === "active",
        "Saving browser-created Version 2 did not return active status.",
      );
      assert(
        browserSavedVersion.commit_message === browserCommitMessage,
        "Saving browser-created Version 2 did not persist the commit message.",
      );
      await waitForNoVisibleText(page, "Draft", { exact: true });
      await waitForNoVisibleText(page, "Commit Message", { exact: true });

      const inactiveV1AfterBrowserSave = await loadAgentVersion({
        auth,
        graphId,
        versionId: setup.draftVersionId,
      });
      assert(
        inactiveV1AfterBrowserSave.status === "inactive",
        "Saving browser-created Version 2 did not move Version 1 to inactive.",
      );
      evidence.browser_saved_version = {
        version_id: browserCreatedVersionId,
        status: browserSavedVersion.status,
        commit_message: browserSavedVersion.commit_message,
        version_1_status_after_save: inactiveV1AfterBrowserSave.status,
      };

      logStep("create temporary active version 3 through API");
      const createdDraft = await auth.client.post(
        apiPath("/agent-playground/graphs/{id}/versions/", { id: graphId }),
        {
          commit_message: restoreCommitMessage,
          nodes: [
            {
              id: randomUUID(),
              type: "atomic",
              name: restoreNodeName,
              node_template_id: setup.nodeTemplateId,
              position: { x: 90, y: 220 },
              prompt_template: {
                messages: [
                  {
                    id: "browser-version-restore",
                    role: "user",
                    content: [
                      {
                        type: "text",
                        text: "Respond with one sentence about {{topic}}.",
                      },
                    ],
                  },
                ],
                response_format: "text",
                model: "gpt-4o-mini",
                temperature: 0,
                metadata: {
                  api_journey: "agents-playground-versions-smoke",
                },
              },
            },
          ],
        },
      );
      restoreVersionId = createdDraft?.id;
      assert(
        isUuid(restoreVersionId),
        "Temporary version create returned no id.",
      );
      assert(
        createdDraft.status === "draft",
        "Temporary version create did not return draft status.",
      );
      assert(
        versionNodesContain(createdDraft, restoreNodeName),
        "Temporary version create response did not include the restore node.",
      );
      evidence.api_created_restore_setup = {
        version_id: restoreVersionId,
        status: createdDraft.status,
        restore_node_name: restoreNodeName,
      };

      logStep("activate temporary version 3 for restore setup");
      const secondSavedVersion = await auth.client.patch(
        apiPath("/agent-playground/graphs/{id}/versions/{version_id}/", {
          id: graphId,
          version_id: restoreVersionId,
        }),
        {
          status: "active",
          commit_message: restoreCommitMessage,
        },
      );
      assert(
        secondSavedVersion?.id === restoreVersionId &&
          secondSavedVersion.status === "active",
        "Saving Version 3 did not return the active restore setup version.",
      );
      assert(
        secondSavedVersion.commit_message === restoreCommitMessage,
        "Activating Version 3 did not persist the commit message.",
      );
      const restoreVersionLabel = `Version ${
        secondSavedVersion.version_number || 3
      }`;
      await openBuilderVersion(page, {
        graphId,
        versionId: restoreVersionId,
      });
      await waitForVisibleText(page, restoreVersionLabel, { exact: true });
      await waitForNoVisibleText(page, "Draft", { exact: true });
      await waitForVisibleText(page, restoreNodeName, { exact: true });

      const inactiveV1Detail = await loadAgentVersion({
        auth,
        graphId,
        versionId: setup.draftVersionId,
      });
      assert(
        inactiveV1Detail.status === "inactive",
        "Saving Version 3 did not keep Version 1 inactive.",
      );

      logStep("restore version 1 from changelog");
      const versionsResponse = page.waitForResponse(
        (response) =>
          response
            .url()
            .includes(`/agent-playground/graphs/${graphId}/versions/`) &&
          !response.url().includes(`/${restoreVersionId}/`) &&
          response.status() < 400,
        { timeout: 60000 },
      );
      await clickVisibleText(page, "Changelog", { exact: true });
      await versionsResponse;
      await page.waitForFunction(
        () => window.location.pathname.endsWith("/changelog"),
        { timeout: 30000 },
      );
      await waitForVisibleText(page, restoreVersionLabel, { exact: true });
      await waitForVisibleText(page, "Version 1", { exact: true });
      await waitForVisibleText(page, firstCommitMessage);
      await waitForVisibleText(page, restoreCommitMessage);

      const versionOneDetailResponse = page.waitForResponse(
        (response) =>
          response
            .url()
            .includes(
              `/agent-playground/graphs/${graphId}/versions/${setup.draftVersionId}/`,
            ) && response.status() < 400,
        { timeout: 60000 },
      );
      await clickVisibleLabelText(page, "Version 1");
      await versionOneDetailResponse;
      await waitForVisibleText(page, sourceNodeName, { exact: true });
      await waitForVisibleText(page, targetNodeName, { exact: true });
      await waitForVisibleText(page, "Restore", { exact: true });

      const restoreResponse = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          response
            .url()
            .includes(
              `/agent-playground/graphs/${graphId}/versions/${setup.draftVersionId}/activate/`,
            ) &&
          response.status() < 400,
        { timeout: 60000 },
      );
      await clickVisibleText(page, "Restore", { exact: true });
      const restoredVersion = unwrapBrowserResult(
        await restoreResponse.then((response) => response.json()),
      );
      assert(
        restoredVersion?.id === setup.draftVersionId &&
          restoredVersion.status === "active",
        "Changelog restore did not reactivate Version 1.",
      );
      await page.waitForFunction(
        ({ graphId: expectedGraphId, versionId }) =>
          window.location.pathname.endsWith(
            `/dashboard/agents/playground/${expectedGraphId}/build`,
          ) && window.location.search.includes(`version=${versionId}`),
        { timeout: 30000 },
        { graphId, versionId: setup.draftVersionId },
      );
      await waitForVisibleText(page, "Version 1", { exact: true });
      await waitForVisibleText(page, sourceNodeName, { exact: true });
      await waitForVisibleText(page, targetNodeName, { exact: true });
      await waitForNoVisibleText(page, browserEditedSourceNodeName, {
        exact: true,
      });
      await waitForRenderedEdgeCount(page, 1);

      const finalV1Detail = await loadAgentVersion({
        auth,
        graphId,
        versionId: setup.draftVersionId,
      });
      const finalBrowserVersionDetail = await loadAgentVersion({
        auth,
        graphId,
        versionId: browserCreatedVersionId,
      });
      const finalRestoreVersionDetail = await loadAgentVersion({
        auth,
        graphId,
        versionId: restoreVersionId,
      });
      assert(
        finalV1Detail.status === "active" &&
          finalBrowserVersionDetail.status === "inactive" &&
          finalRestoreVersionDetail.status === "inactive",
        "Version restore readback did not leave Version 1 active, the browser-created version inactive, and the restore setup version inactive.",
      );
      assert(
        versionNodesContain(
          finalBrowserVersionDetail,
          browserEditedSourceNodeName,
        ),
        "Browser-created Version 2 readback lost the edited source node.",
      );
      evidence.browser_version_lifecycle = {
        version_1_id: setup.draftVersionId,
        browser_version_id: browserCreatedVersionId,
        restore_setup_version_id: restoreVersionId,
        version_1_status_after_restore: finalV1Detail.status,
        browser_version_status_after_restore: finalBrowserVersionDetail.status,
        restore_setup_version_status_after_restore:
          finalRestoreVersionDetail.status,
        version_1_commit_message: finalV1Detail.commit_message,
        restore_setup_commit_message: finalRestoreVersionDetail.commit_message,
      };
      const preGraphDeleteAudit = await loadAgentGraphDbAudit({ graphId });
      assert(
        preGraphDeleteAudit.version_visible === 3,
        "Agent graph should have three visible versions before graph delete.",
      );
      assert(
        preGraphDeleteAudit.node_visible === 5,
        "Agent graph should have five visible nodes before graph delete.",
      );
      assert(
        preGraphDeleteAudit.node_connection_visible === 2,
        "Agent graph should have two visible node connections before graph delete.",
      );
      assert(
        preGraphDeleteAudit.edge_visible === 2,
        "Agent graph should have two visible edges before graph delete.",
      );
      evidence.pre_graph_delete_audit = preGraphDeleteAudit;

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
        `Agent playground version smoke fired unexpected mutations: ${unexpectedMutations.join("; ")}`,
      );
      assert(
        expectedMutations.length === 4,
        `Expected save-v1, browser-create-v2, save-v2, and restore-v1 mutations, saw ${expectedMutations.length}.`,
      );
      evidence.expected_mutations = expectedMutations;
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

    const postDeleteAudit = await loadAgentGraphDbAudit({ graphId });
    assertAgentGraphPostDeleteAudit(postDeleteAudit);
    evidence.public_delete_status = deletedGraphDetail.status;
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
  process.stderr.write(`[agents-versions-smoke] ${message}\n`);
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
      description: `Disposable browser version graph ${auth.runId}`,
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
            id: "browser-version-source",
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
        metadata: { api_journey: "agents-playground-versions-smoke" },
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
            id: "browser-version-target",
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
        metadata: { api_journey: "agents-playground-versions-smoke" },
      },
    },
  );
  assert(isUuid(targetNode.id), "Target node create did not return an id.");
  assert(
    targetNode.node_connection?.source_node_id === sourceNode.id,
    "Target node did not create a source node connection.",
  );

  return {
    graphId,
    draftVersionId,
    sourceNode,
    targetNode,
    nodeTemplateId: llmTemplate.id,
  };
}

async function loadAgentVersion({ auth, graphId, versionId }) {
  return auth.client.get(
    apiPath("/agent-playground/graphs/{id}/versions/{version_id}/", {
      id: graphId,
      version_id: versionId,
    }),
  );
}

async function openBuilderVersion(page, { graphId, versionId }) {
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
          `/agent-playground/graphs/${graphId}/versions/${versionId}/`,
        ) && response.status() < 400,
    { timeout: 60000 },
  );
  await page.goto(
    `${APP_BASE}/dashboard/agents/playground/${graphId}/build?version=${versionId}`,
    { waitUntil: "domcontentloaded" },
  );
  await Promise.all([graphDetailResponse, versionDetailResponse]);
  await page.waitForFunction(
    ({ graphId: expectedGraphId, versionId: expectedVersionId }) =>
      window.location.pathname.endsWith(
        `/dashboard/agents/playground/${expectedGraphId}/build`,
      ) && window.location.search.includes(`version=${expectedVersionId}`),
    { timeout: 30000 },
    { graphId, versionId },
  );
}

async function openSaveAgentDialog(page, commitMessage) {
  await waitForEnabledButton(page, "Save Agent");
  await clickVisibleText(page, "Save Agent", { exact: true });
  await waitForVisibleText(page, "Save Agent", { exact: true });
  await waitForVisibleText(page, "Commit Message", { exact: true });
  await replaceInputByLabel(page, "Commit Message", commitMessage);
  await waitForEnabledButton(page, "Save");
  await clickVisibleText(page, "Save", { exact: true });
}

function waitForVersionSaveResponse(page, { graphId, versionId }) {
  return page.waitForResponse(
    (response) =>
      response.request().method() === "PUT" &&
      response
        .url()
        .includes(
          `/agent-playground/graphs/${graphId}/versions/${versionId}/`,
        ) &&
      response.status() < 400,
    { timeout: 60000 },
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

async function installBrowserHelpers(page) {
  await page.evaluateOnNewDocument(() => {
    window.isVisibleElement = (element) => {
      if (!element) return false;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    };
    window.setNativeInputValue = (input, value) => {
      const prototype =
        input instanceof window.HTMLTextAreaElement
          ? window.HTMLTextAreaElement.prototype
          : window.HTMLInputElement.prototype;
      const valueSetter = Object.getOwnPropertyDescriptor(
        prototype,
        "value",
      )?.set;
      valueSetter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    };
    window.findInputByVisibleLabel = (expectedLabel) => {
      const normalized = (value) => String(value || "").trim();
      const label = Array.from(document.querySelectorAll("label")).find(
        (candidate) =>
          window.isVisibleElement(candidate) &&
          normalized(candidate.textContent) === expectedLabel,
      );
      if (!label) return null;
      if (label.htmlFor) {
        const direct = document.getElementById(label.htmlFor);
        if (direct?.matches?.("input,textarea")) return direct;
      }
      return (
        label
          .closest(".MuiFormControl-root,.MuiTextField-root,form,body")
          ?.querySelector("input,textarea") || null
      );
    };
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
      return Array.from(document.querySelectorAll("body *")).some((element) => {
        if (!window.isVisibleElement(element)) return false;
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
      return !Array.from(document.querySelectorAll("body *")).some(
        (element) => {
          if (!window.isVisibleElement(element)) return false;
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

async function waitForInputValue(page, value, timeout = 30000) {
  await page.waitForFunction(
    (expectedValue) =>
      Array.from(document.querySelectorAll("input,textarea")).some(
        (input) => input.value === expectedValue,
      ),
    { timeout },
    value,
  );
}

async function replaceInputByLabel(page, label, nextValue) {
  await page.waitForFunction(
    (expectedLabel) => window.findInputByVisibleLabel(expectedLabel) !== null,
    { timeout: 30000 },
    label,
  );
  await page.evaluate(
    ({ label: expectedLabel, nextValue: replacementValue }) => {
      const input = window.findInputByVisibleLabel(expectedLabel);
      if (!input) throw new Error(`Input label not found: ${expectedLabel}`);
      window.setNativeInputValue(input, replacementValue);
    },
    { label, nextValue },
  );
  await waitForInputValue(page, nextValue);
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

async function clickCanvasNode(page, nodeLabel) {
  const point = await page.evaluate((expectedLabel) => {
    const node = Array.from(
      document.querySelectorAll(".react-flow__node"),
    ).find(
      (candidate) =>
        window.isVisibleElement(candidate) &&
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

async function clickVisibleText(page, text, { exact = false } = {}) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
      return Array.from(
        document.querySelectorAll("button,[role='tab'],a"),
      ).some((element) => {
        if (!window.isVisibleElement(element)) return false;
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
      const element = Array.from(
        document.querySelectorAll("button,[role='tab'],a"),
      ).find((candidate) => {
        if (!window.isVisibleElement(candidate)) return false;
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
    (expectedText) =>
      Array.from(document.querySelectorAll("label")).some(
        (element) =>
          window.isVisibleElement(element) &&
          String(element.textContent || "")
            .trim()
            .includes(expectedText),
      ),
    { timeout: 30000 },
    text,
  );
  await page.evaluate((expectedText) => {
    const element = Array.from(document.querySelectorAll("label")).find(
      (candidate) =>
        window.isVisibleElement(candidate) &&
        String(candidate.textContent || "")
          .trim()
          .includes(expectedText),
    );
    element.click();
  }, text);
}

function unwrapBrowserResult(payload) {
  return payload?.result || payload?.data?.result || payload;
}

function versionNodesContain(version, expectedName) {
  return (version?.nodes || []).some(
    (node) =>
      node?.name === expectedName ||
      node?.data?.label === expectedName ||
      node?.label === expectedName,
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
  assert(
    audit.version_visible === 1,
    "Agent graph should start with one visible version.",
  );
  assert(
    audit.node_visible === 2,
    "Agent graph should start with two visible nodes.",
  );
  assert(
    audit.node_connection_visible === 1,
    "Agent graph should start with one visible node connection.",
  );
  assert(
    audit.edge_visible === 1,
    "Agent graph should start with one visible edge.",
  );
  assert(
    audit.prompt_template_node_visible === 2,
    "Agent graph should start with two visible prompt-template node links.",
  );
  assert(
    audit.graph_dataset_visible === 1,
    "Agent graph should start with one visible graph dataset link.",
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

main().catch((error) => {
  process.stderr.write(`${error?.stack || error}\n`);
  process.exitCode = 1;
});
