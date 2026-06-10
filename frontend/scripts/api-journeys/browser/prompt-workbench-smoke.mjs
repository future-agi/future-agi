/* eslint-disable no-console */
import { execFile as execFileCallback } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  envFlag,
  isUuid,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFile = promisify(execFileCallback);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const FOLDER_SCREENSHOT_PATH = "/tmp/prompt-workbench-folder-smoke.png";
const FOLDER_ACTION_MENU_SCREENSHOT_PATH =
  "/tmp/prompt-workbench-folder-action-menu-smoke.png";
const FOLDER_RENAMED_SCREENSHOT_PATH =
  "/tmp/prompt-workbench-folder-renamed-smoke.png";
const FOLDER_DELETED_SCREENSHOT_PATH =
  "/tmp/prompt-workbench-folder-deleted-smoke.png";
const DETAIL_SCREENSHOT_PATH = "/tmp/prompt-workbench-detail-smoke.png";
const EVALUATION_SCREENSHOT_PATH = "/tmp/prompt-workbench-evaluation-smoke.png";
const MODEL_PICKER_SCREENSHOT_PATH =
  "/tmp/prompt-workbench-model-picker-smoke.png";
const RUN_OUTPUT_SCREENSHOT_PATH = "/tmp/prompt-workbench-run-output-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/prompt-workbench-smoke-failure.png";
const RUN_PROMPT_UI = envFlag("API_JOURNEY_PROMPT_UI_RUN");

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = shortRunId(auth.runId);
  const folderName = `ui_prompt_folder_${suffix}`;
  const renamedFolderName = `ui_prompt_folder_renamed_${suffix}`;
  const promptName = `ui prompt workbench ${suffix}`;
  const evalConfigName = `ui_prompt_eval_config_${suffix}`;
  const promptText = `Customer {{customer}} asks for the readiness phrase ${auth.runId}. Use text {{text}} and expected hint {{expected}}.`;
  const safeModel = await findSafePromptRunModel(auth.client);
  let folderId = null;
  let promptId = null;
  let evalConfig = null;
  let browser = null;
  let caughtError = null;
  let uiDeletedFolder = false;
  let runAudit = null;
  let deleteAudit = null;
  let hardCleanup = null;

  const apiFailures = [];
  const pageErrors = [];
  const promptRequests = [];
  const promptSocketStatuses = [];
  const evidence = {
    folder_name: folderName,
    folder_renamed_name: renamedFolderName,
    prompt_name: promptName,
    eval_config_name: evalConfigName,
    safe_model: safeModel.model_name,
    safe_provider: safeModel.providers,
    safe_model_available: safeModel.isAvailable,
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
    await installBrowserState(page, auth);

    page.on("request", (request) => {
      if (isPromptWorkbenchApiUrl(request.url())) {
        promptRequests.push(`${request.method()} ${request.url()}`);
      }
    });
    page.on("websocket", (socket) => {
      if (!socket.url().includes("/ws/prompt-stream/")) return;
      promptRequests.push("WS /ws/prompt-stream/");
      socket.on("framesent", (event) =>
        trackSocketPayload(promptSocketStatuses, event, "sent"),
      );
      socket.on("framereceived", (event) =>
        trackSocketPayload(promptSocketStatuses, event, "received"),
      );
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isPromptWorkbenchApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponsesDuring(
      page,
      "initial prompt workbench load",
      [
        (response) =>
          response.url().includes("/model-hub/prompt-folders/") &&
          response.status() < 400,
        (response) =>
          response.url().includes("/model-hub/prompt-executions/") &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/workbench/all`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/workbench/all");
    await waitForVisibleText(page, "All Prompts", { exact: true });
    await waitForVisibleText(page, "My templates", { exact: true });
    await waitForVisibleText(page, "New Folder", { exact: true });
    await waitForVisibleText(page, "Create prompt", { exact: true });

    const folderResponse = await waitForResponseDuring(
      page,
      "UI folder create",
      (response) =>
        response.url().includes("/model-hub/prompt-folders/") &&
        response.request().method() === "POST" &&
        response.status() < 400,
      async () => {
        await clickVisibleText(page, "New Folder", { exact: true });
        await waitForVisibleText(page, "Create new folder", { exact: true });
        await typeIntoDialogInput(page, folderName);
        await clickVisibleText(page, "Create", { exact: true });
      },
    );
    const folderPayload = unwrapResult(await responseJson(folderResponse));
    folderId =
      folderPayload?.id ||
      folderPayload?.uuid ||
      (await parseWorkbenchFolderId(page));
    if (!isUuid(folderId)) {
      await waitForFunction(page, () =>
        /^\/dashboard\/workbench\/[0-9a-f-]{36}$/i.test(
          window.location.pathname,
        ),
      );
      folderId = await parseWorkbenchFolderId(page);
    }
    assert(
      isUuid(folderId),
      "Workbench UI folder create did not expose a UUID id.",
    );
    evidence.folder_id = folderId;
    await waitForPath(page, `/dashboard/workbench/${folderId}`);
    await waitForVisibleText(page, folderName, { exact: true });

    await clickFolderActionMenu(page, { folderId, folderName });
    await waitForVisibleText(page, "Rename", { exact: true });
    await waitForVisibleText(page, "Delete", { exact: true });
    await page.screenshot({
      path: FOLDER_ACTION_MENU_SCREENSHOT_PATH,
      fullPage: true,
    });
    evidence.folder_action_menu_screenshot = FOLDER_ACTION_MENU_SCREENSHOT_PATH;

    await clickVisibleMenuItem(page, "Rename");
    await waitForVisibleText(page, "Rename folder", { exact: true });
    await waitForResponseDuring(
      page,
      "UI folder rename",
      (response) =>
        response.url().includes(`/model-hub/prompt-folders/${folderId}/`) &&
        response.request().method() === "PATCH" &&
        response.status() < 400,
      async () => {
        await typeIntoDialogInput(page, renamedFolderName);
        await clickDialogButton(page, "Save");
      },
    );
    await waitForVisibleText(page, renamedFolderName);
    await assertPromptFolderReadback(auth.client, {
      folderId,
      name: renamedFolderName,
    });
    await page.screenshot({
      path: FOLDER_RENAMED_SCREENSHOT_PATH,
      fullPage: true,
    });
    evidence.folder_renamed_screenshot = FOLDER_RENAMED_SCREENSHOT_PATH;

    promptId = await createWorkbenchPrompt(auth.client, {
      folderId,
      name: promptName,
      runId: auth.runId,
      promptText,
      model: safeModel,
    });
    evidence.prompt_id = promptId;
    await commitWorkbenchPromptVersion(auth.client, {
      promptId,
      runId: auth.runId,
    });
    const seededOutputAudit = await seedPromptWorkbenchOutputDb({
      promptId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assert(
      Number(seededOutputAudit.updated_count) === 1 &&
        Number(seededOutputAudit.output_count) === 1 &&
        seededOutputAudit.is_draft === false,
      `Prompt Workbench output seed failed: ${JSON.stringify(
        seededOutputAudit,
      )}`,
    );
    evidence.prompt_version_committed = true;
    evidence.db_seeded_output_count = Number(seededOutputAudit.output_count);
    evalConfig = await createPromptEvaluationConfig(auth.client, {
      promptId,
      name: evalConfigName,
      suffix,
    });
    const evaluationApiReadback = await assertPromptEvaluationApiReadback(
      auth.client,
      {
        promptId,
        evalConfig,
      },
    );
    evidence.eval_config_id = evalConfig.id;
    evidence.eval_template_id = evalConfig.templateId;
    evidence.eval_template_name = evalConfig.templateName;
    evidence.eval_template_source = evalConfig.templateSource;
    evidence.eval_required_keys = evalConfig.requiredKeys;
    evidence.eval_mapping = evalConfig.mapping;
    evidence.eval_params = evalConfig.expectedParams;
    evidence.evaluation_api_status = evaluationApiReadback.status;
    evidence.evaluation_api_variable_text = evaluationApiReadback.textValue;

    await waitForResponseDuring(
      page,
      "folder prompt list",
      (response) => {
        if (
          !response.url().includes("/model-hub/prompt-executions/") ||
          response.status() >= 400
        ) {
          return false;
        }
        const url = new URL(response.url());
        return url.searchParams.get("prompt_folder") === folderId;
      },
      () =>
        page.goto(`${APP_BASE}/dashboard/workbench/${folderId}`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForVisibleText(page, renamedFolderName);
    await waitForVisibleText(page, promptName, { exact: true });
    await waitForVisibleText(page, "No.of prompts: 1", { exact: true });
    await page.screenshot({ path: FOLDER_SCREENSHOT_PATH, fullPage: true });
    evidence.folder_screenshot = FOLDER_SCREENSHOT_PATH;

    await waitForResponseDuring(
      page,
      "global prompt search",
      (response) => {
        if (
          !response.url().includes("/model-hub/prompt-executions/") ||
          response.status() >= 400
        ) {
          return false;
        }
        const url = new URL(response.url());
        return (
          url.searchParams.get("name") === promptName &&
          url.searchParams.get("send_all") === "true"
        );
      },
      () => typeSearch(page, promptName),
    );
    await waitForVisibleText(page, promptName, { exact: true });
    await waitForVisibleText(page, renamedFolderName);

    await waitForResponseDuring(
      page,
      "prompt detail load",
      (response) =>
        response.url().includes(`/model-hub/prompt-templates/${promptId}/`) &&
        response.status() < 400,
      () => clickPromptItem(page, promptId, promptName),
    );
    await waitForPath(page, `/dashboard/workbench/create/${promptId}`);
    await waitForVisibleText(page, promptName);
    await waitForVisibleText(page, "Playground", { exact: true });
    await waitForVisibleText(page, "Evaluation", { exact: true });
    await waitForVisibleText(page, "Metrics", { exact: true });
    await waitForEditorText(page, ["Customer", "customer", auth.runId]);
    await waitForNoVisibleText(page, "Invalid Date");
    await page.screenshot({ path: DETAIL_SCREENSHOT_PATH, fullPage: true });
    evidence.detail_screenshot = DETAIL_SCREENSHOT_PATH;

    const evaluationResponse = await waitForResponseDuring(
      page,
      "prompt evaluation tab data",
      (response) =>
        response
          .url()
          .includes(`/model-hub/prompt-templates/${promptId}/evaluations/`) &&
        response.request().method() === "GET" &&
        response.status() < 400,
      () => clickVisibleText(page, "Evaluation", { exact: true }),
    );
    const evaluationPayload = unwrapResult(
      await responseJson(evaluationResponse),
    );
    const evaluationUiReadback = assertPromptEvaluationPayload(
      evaluationPayload,
      { evalConfig, requireMessages: false },
    );
    await waitForPromptEvaluationGrid(page, {
      evalConfigName,
      textValue: evaluationUiReadback.textValue,
    });
    await waitForNoVisibleText(page, "Invalid Date");
    await waitForNoVisibleText(page, "undefined");
    await page.screenshot({
      path: EVALUATION_SCREENSHOT_PATH,
      fullPage: true,
    });
    evidence.evaluation_screenshot = EVALUATION_SCREENSHOT_PATH;
    evidence.evaluation_grid_visible = true;
    evidence.evaluation_ui_status = evaluationUiReadback.status;
    evidence.evaluation_ui_variable_text = evaluationUiReadback.textValue;

    await clickVisibleText(page, "Playground", { exact: true });
    await waitForEditorText(page, ["Customer", "customer", auth.runId]);

    await openModelPickerAndSelectSafeModel(page, safeModel);
    await page.screenshot({
      path: MODEL_PICKER_SCREENSHOT_PATH,
      fullPage: true,
    });
    evidence.model_picker_screenshot = MODEL_PICKER_SCREENSHOT_PATH;
    assert(
      modelParameterRequestObserved(promptRequests, safeModel),
      "Model parameter endpoint did not load for the safe model.",
    );
    if (safeModel.isAvailable === false) {
      evidence.model_picker_selection_skipped =
        "No configured chat model was available in this local workspace.";
      await page.keyboard.press("Escape");
    } else {
      await clickVisibleMenuItemContaining(page, safeModel.model_name);
      await page.waitForFunction(
        () =>
          window.visibleElements('input[placeholder="Select model"]').length ===
          0,
        { timeout: 30000 },
      );
      evidence.model_picker_selection_verified = true;
    }
    evidence.model_parameters_request_observed = true;

    await clickVisibleText(page, "Text output", { exact: true });
    await waitForVisibleText(page, "JSON output", { exact: true });
    await waitForVisibleText(page, "Create custom schema", { exact: true });
    evidence.response_format_menu_visible = true;
    await page.keyboard.press("Escape");

    await clickVisibleText(page, "Mustache", { exact: true });
    await waitForVisibleText(page, "Jinja", { exact: true });
    evidence.template_format_menu_visible = true;
    await page.keyboard.press("Escape");

    if (RUN_PROMPT_UI) {
      await clickVisibleText(page, "Run Prompt", { exact: true });
      await waitForPromptOutput(page);
      await waitForPromptOutputMetadata(page);
      runAudit = await auditPromptRunOutputDb({
        promptId,
        organizationId: auth.organizationId,
        workspaceId: auth.workspaceId,
        modelName: safeModel.model_name,
      });
      assert(
        Number(runAudit.output_count) > 0 &&
          Number(runAudit.metadata_count) > 0 &&
          runAudit.model_name === safeModel.model_name &&
          runAudit.is_draft === false,
        `Prompt run output audit failed: ${JSON.stringify(runAudit)}`,
      );
      await page.screenshot({
        path: RUN_OUTPUT_SCREENSHOT_PATH,
        fullPage: true,
      });
      evidence.run_output_screenshot = RUN_OUTPUT_SCREENSHOT_PATH;
      evidence.ui_run_output_visible = true;
      evidence.ui_run_output_metadata_visible = true;
      evidence.db_run_output_count = Number(runAudit.output_count);
      evidence.db_run_metadata_count = Number(runAudit.metadata_count);
      evidence.db_run_is_draft = runAudit.is_draft;
      evidence.websocket_statuses = uniqueValues(promptSocketStatuses);
      evidence.run_output_transport = evidence.websocket_statuses.length
        ? "prompt_stream_websocket"
        : "http_run_status_poll_fallback";
    } else {
      evidence.ui_run_output_skipped =
        "Set API_JOURNEY_PROMPT_UI_RUN=1 when the local websocket server is available.";
    }

    await waitForResponseDuring(
      page,
      "folder prompt list before delete",
      (response) => {
        if (
          !response.url().includes("/model-hub/prompt-executions/") ||
          response.status() >= 400
        ) {
          return false;
        }
        const url = new URL(response.url());
        return url.searchParams.get("prompt_folder") === folderId;
      },
      () =>
        page.goto(`${APP_BASE}/dashboard/workbench/${folderId}`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForVisibleText(page, renamedFolderName);
    await waitForVisibleText(page, promptName, { exact: true });
    await clickFolderActionMenu(page, {
      folderId,
      folderName: renamedFolderName,
    });
    await clickVisibleMenuItem(page, "Delete");
    await waitForVisibleText(page, "Delete folder", { exact: true });
    await waitForVisibleText(page, renamedFolderName);
    await waitForResponseDuring(
      page,
      "UI folder delete",
      (response) =>
        response.url().includes(`/model-hub/prompt-folders/${folderId}/`) &&
        response.request().method() === "DELETE" &&
        response.status() < 400,
      () => clickDialogButton(page, "Delete"),
    );
    uiDeletedFolder = true;
    await waitForPath(page, "/dashboard/workbench/all");
    await waitForNoVisibleText(page, renamedFolderName);
    await assertPromptFolderAbsentFromList(auth.client, {
      folderId,
      name: renamedFolderName,
    });
    deleteAudit = await auditDeletedPromptFolderDb({
      folderId,
      promptId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assert(
      Number(deleteAudit.folder_row_count) === 1 &&
        deleteAudit.folder_deleted_at_set === true,
      `Folder delete audit failed: ${JSON.stringify(deleteAudit)}`,
    );
    assert(
      Number(deleteAudit.prompt_row_count) === 1 &&
        deleteAudit.prompt_deleted_at_set === true &&
        Number(deleteAudit.version_row_count) > 0 &&
        deleteAudit.version_deleted_at_set === true,
      `Folder delete cascade audit failed: ${JSON.stringify(deleteAudit)}`,
    );
    assert(
      Number(deleteAudit.eval_config_row_count) === 1 &&
        deleteAudit.eval_config_deleted_at_set === true,
      `Folder delete eval config cascade audit failed: ${JSON.stringify(
        deleteAudit,
      )}`,
    );
    hardCleanup = await hardDeletePromptWorkbenchFixtureDb({
      folderId,
      promptId,
      evalConfigId: evalConfig?.id,
      createdEvalTemplateId: evalConfig?.createdTemplateId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assert(
      Number(hardCleanup.remaining_folder_count) === 0 &&
        Number(hardCleanup.remaining_prompt_count) === 0 &&
        Number(hardCleanup.remaining_version_count) === 0 &&
        Number(hardCleanup.remaining_eval_config_count) === 0 &&
        Number(hardCleanup.remaining_eval_template_count) === 0,
      `Hard cleanup left prompt workbench rows behind: ${JSON.stringify(hardCleanup)}`,
    );
    await page.screenshot({
      path: FOLDER_DELETED_SCREENSHOT_PATH,
      fullPage: true,
    });
    evidence.folder_deleted_screenshot = FOLDER_DELETED_SCREENSHOT_PATH;
    evidence.ui_folder_deleted = true;
    evidence.folder_deleted_at_set = deleteAudit.folder_deleted_at_set;
    evidence.cascade_prompt_deleted_at_set = deleteAudit.prompt_deleted_at_set;
    evidence.cascade_versions_deleted_at_set =
      deleteAudit.version_deleted_at_set;
    evidence.cascade_eval_config_deleted_at_set =
      deleteAudit.eval_config_deleted_at_set;
    evidence.hard_cleanup_remaining_folder_count = Number(
      hardCleanup.remaining_folder_count,
    );
    evidence.hard_cleanup_remaining_prompt_count = Number(
      hardCleanup.remaining_prompt_count,
    );
    evidence.hard_cleanup_remaining_version_count = Number(
      hardCleanup.remaining_version_count,
    );
    evidence.hard_cleanup_remaining_eval_config_count = Number(
      hardCleanup.remaining_eval_config_count,
    );
    evidence.hard_cleanup_remaining_eval_template_count = Number(
      hardCleanup.remaining_eval_template_count,
    );

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence,
          prompt_request_count: promptRequests.length,
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
          prompt_requests: promptRequests,
        },
        null,
        2,
      ),
    );
    if (browser) {
      const pages = await browser.pages();
      const page = pages[pages.length - 1];
      await page
        ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .catch(() => null);
    }
  } finally {
    if (browser) await browser.close();
    if (!hardCleanup) {
      await cleanupPromptTemplate(auth.client, promptId).catch((error) => {
        caughtError = appendCleanupError(caughtError, error);
      });
      await cleanupPromptFolder(auth.client, folderId).catch((error) => {
        caughtError = appendCleanupError(caughtError, error);
      });
      if (folderId || promptId) {
        await hardDeletePromptWorkbenchFixtureDb({
          folderId,
          promptId,
          evalConfigId: evalConfig?.id,
          createdEvalTemplateId: evalConfig?.createdTemplateId,
          organizationId: auth.organizationId,
          workspaceId: auth.workspaceId,
        }).catch((error) => {
          caughtError = appendCleanupError(caughtError, error);
        });
      }
    } else if (!uiDeletedFolder) {
      await cleanupPromptFolder(auth.client, folderId).catch((error) => {
        caughtError = appendCleanupError(caughtError, error);
      });
    }
  }

  if (caughtError) throw caughtError;
}

async function findSafePromptRunModel(client) {
  const options = await client.get(
    apiPath("/model-hub/develops/retrieve_run_prompt_options/"),
  );
  const models = payloadArray(options, "models");
  const isPreferredChatModel = (model) => {
    const name = model?.model_name || model?.modelName || model?.name;
    const provider = model?.providers || model?.provider;
    const type = model?.type || model?.mode || model?.model_type;
    return (
      name === "gpt-4o-mini" &&
      provider === "openai" &&
      (type === "chat" || type === "llm" || !type)
    );
  };
  const isAvailableChatModel = (model) => {
    const type = model?.type || model?.mode || model?.model_type;
    const isAvailable = model?.is_available ?? model?.isAvailable;
    return (type === "chat" || type === "llm" || !type) && isAvailable === true;
  };
  const preferredModel = models.find(isPreferredChatModel);
  const safeModel =
    (preferredModel && isAvailableChatModel(preferredModel)
      ? preferredModel
      : null) ||
    models.find(isAvailableChatModel) ||
    preferredModel ||
    models.find((model) => {
      const type = model?.type || model?.mode || model?.model_type;
      return type === "chat" || type === "llm" || !type;
    });
  assert(
    safeModel,
    "Prompt Workbench browser smoke requires at least one chat/LLM model option.",
  );
  return normalizeModelOption(safeModel);
}

async function createWorkbenchPrompt(
  client,
  { folderId, name, runId, promptText, model },
) {
  const modelDetail = normalizeModelDetail(model);
  const variableNames = promptWorkbenchVariableNames();
  const promptConfig = [
    {
      messages: [
        {
          role: "system",
          content: [{ type: "text", text: "You are a concise assistant." }],
        },
        {
          role: "user",
          content: [{ type: "text", text: promptText }],
        },
      ],
      configuration: {
        model: model.model_name,
        model_detail: modelDetail,
        max_tokens: 16,
        temperature: 0,
        top_p: 1,
        response_format: "text",
        output_format: "string",
        template_format: "mustache",
      },
      placeholders: [],
    },
  ];
  const created = await client.post(
    apiPath("/model-hub/prompt-templates/create-draft/"),
    {
      name,
      description: "Prompt Workbench browser smoke candidate.",
      prompt_folder: folderId,
      variable_names: variableNames,
      metadata: { source: "api-journey-browser", run_id: runId },
      prompt_config: promptConfig,
    },
  );
  const promptId =
    created?.id || created?.root_template || created?.rootTemplate;
  assert(isUuid(promptId), "Workbench prompt create did not return a UUID id.");
  await client.post(
    apiPath("/model-hub/prompt-templates/{id}/run_template/", {
      id: promptId,
    }),
    {
      name,
      version: "v1",
      is_run: false,
      variable_names: variableNames,
      placeholders: {},
      evaluation_configs: [],
      prompt_config: promptConfig,
    },
  );
  return promptId;
}

function promptWorkbenchVariableNames() {
  return {
    customer: ["Ada"],
    text: ["one two three four"],
    expected: ["a short answer"],
  };
}

async function commitWorkbenchPromptVersion(client, { promptId, runId }) {
  await client.post(
    apiPath("/model-hub/prompt-templates/{id}/commit/", { id: promptId }),
    {
      version_name: "v1",
      message: `Prompt Workbench browser smoke ${runId}`,
      is_draft: false,
      set_default: true,
    },
  );
  const committed = await client.get(
    apiPath("/model-hub/prompt-templates/{id}/", { id: promptId }),
  );
  assert(
    committed?.is_draft === false,
    "Prompt Workbench commit did not mark v1 as non-draft.",
  );
  return committed;
}

async function seedPromptWorkbenchOutputDb({
  promptId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(promptId), "Prompt output seed requires a prompt UUID.");
  const sql = `
WITH updated AS (
  UPDATE model_hub_promptversion v
  SET
    output = '["Seeded output for prompt Workbench evaluation smoke."]'::jsonb,
    metadata = '[{"source":"api-journey-browser","transport":"db-seed"}]'::jsonb
  FROM model_hub_prompttemplate p
  WHERE v.original_template_id = p.id
    AND p.id = ${sqlUuid(promptId)}
    AND p.organization_id = ${sqlUuid(organizationId)}
    AND p.workspace_id = ${sqlUuid(workspaceId)}
    AND v.template_version = 'v1'
    AND p.deleted = false
    AND v.deleted = false
  RETURNING v.id, v.output, v.metadata, v.is_draft
)
SELECT json_build_object(
  'updated_count', (SELECT count(*) FROM updated),
  'output_count', COALESCE((SELECT jsonb_array_length(output::jsonb) FROM updated LIMIT 1), 0),
  'metadata_count', COALESCE((SELECT jsonb_array_length(metadata::jsonb) FROM updated LIMIT 1), 0),
  'is_draft', (SELECT is_draft FROM updated LIMIT 1)
);
`;
  return runPostgresJson(sql);
}

async function createPromptEvaluationConfig(
  client,
  { promptId, name, suffix },
) {
  let evalTemplate = null;
  let evalConfigId = null;
  try {
    evalTemplate = await resolvePromptEvalTemplateForConfig(client, suffix);
    const mapping = buildPromptEvalConfigMapping(evalTemplate.requiredKeys);
    const createdConfig = await client.post(
      apiPath("/model-hub/prompt-templates/{id}/update-evaluation-configs/", {
        id: promptId,
      }),
      {
        id: evalTemplate.id,
        name,
        mapping,
        config: { params: evalTemplate.params },
        is_run: false,
      },
    );
    evalConfigId = createdConfig?.prompt_eval_config_id;
    assert(
      isUuid(evalConfigId),
      "Prompt Workbench eval config create did not return prompt_eval_config_id.",
    );

    const configsPayload = await client.get(
      apiPath("/model-hub/prompt-templates/{id}/evaluation-configs/", {
        id: promptId,
      }),
    );
    const configRows = payloadArray(
      configsPayload?.evaluation_configs,
      "evaluation_configs",
    );
    const configRow = configRows.find((row) => row?.id === evalConfigId);
    assert(configRow, "Prompt Workbench eval config readback was missing.");
    assert(
      configRow.name === name,
      "Prompt Workbench eval config readback returned the wrong name.",
    );
    assert(
      configRow.eval_template_id === evalTemplate.id,
      "Prompt Workbench eval config used the wrong eval template.",
    );
    assertPromptEvalMapping(configRow.mapping, mapping);
    assertPromptEvalParams(configRow.params, evalTemplate.expectedParams);

    return {
      id: evalConfigId,
      name,
      templateId: evalTemplate.id,
      templateName: evalTemplate.name,
      templateSource: evalTemplate.source,
      createdTemplateId:
        evalTemplate.source === "created" ? evalTemplate.id : null,
      requiredKeys: evalTemplate.requiredKeys,
      mapping,
      params: evalTemplate.params,
      expectedParams: evalTemplate.expectedParams,
    };
  } catch (error) {
    if (evalConfigId && promptId) {
      await cleanupPromptEvaluationConfig(client, promptId, evalConfigId).catch(
        () => null,
      );
    }
    if (evalTemplate?.source === "created") {
      await cleanupCreatedEvalTemplate(client, evalTemplate.id).catch(
        () => null,
      );
    }
    throw error;
  }
}

async function assertPromptEvaluationApiReadback(
  client,
  { promptId, evalConfig },
) {
  const evaluationData = await client.get(
    apiPath("/model-hub/prompt-templates/{id}/evaluations/", {
      id: promptId,
    }),
    {
      query: {
        versions: JSON.stringify(["v1"]),
        show_var: true,
        show_prompts: true,
      },
    },
  );
  return assertPromptEvaluationPayload(evaluationData, { evalConfig });
}

function assertPromptEvaluationPayload(
  payload,
  { evalConfig, requireMessages = true },
) {
  const versionData = payload?.v1;
  assert(versionData, "Prompt Workbench evaluations did not return v1 data.");
  const textVariable = payload?.variables?.text;
  const textValue = Array.isArray(textVariable)
    ? textVariable[0]
    : textVariable;
  assert(
    textValue === "one two three four",
    "Prompt Workbench evaluations did not include the seeded text variable.",
  );
  const evalNames = payloadArray(versionData.eval_names, "eval_names");
  const evalRow = evalNames.find((row) => String(row?.id) === evalConfig.id);
  assert(
    evalRow,
    "Prompt Workbench evaluations did not include the created eval config.",
  );
  assert(
    evalRow.name === evalConfig.name,
    "Prompt Workbench evaluations returned the wrong eval config name.",
  );
  assertPromptEvalMapping(evalRow.mapping, evalConfig.mapping);
  const status = versionData.eval_status?.[evalConfig.id];
  assert(
    typeof status === "string" && status.length > 0,
    "Prompt Workbench evaluations did not expose eval_status for the config.",
  );
  assert(
    Array.isArray(versionData.eval_output?.[evalConfig.id]),
    "Prompt Workbench evaluations did not expose eval_output for the config.",
  );
  if (requireMessages) {
    assert(
      Array.isArray(versionData.messages),
      "Prompt Workbench evaluations show_prompts readback omitted messages.",
    );
  }
  return { status, textValue };
}

async function resolvePromptEvalTemplateForConfig(client, suffix) {
  const systemTemplate = await findEvalTemplateDetailByName(
    client,
    "word_count_in_range",
  );
  if (systemTemplate) {
    const paramsConfig = promptEvalParamsForTemplate(systemTemplate);
    return {
      id: systemTemplate.id,
      name: systemTemplate.name,
      source: "system",
      requiredKeys: promptEvalRequiredKeys(systemTemplate),
      ...paramsConfig,
    };
  }

  const name = `ui_prompt_eval_${suffix}`;
  const created = await client.post(
    apiPath("/model-hub/eval-templates/create-v2/"),
    {
      name,
      eval_type: "llm",
      instructions:
        "Assess {{output}} against {{expected}} and return Passed or Failed.",
      model: "turing_large",
      output_type: "pass_fail",
      pass_threshold: 0.5,
      description:
        "Temporary prompt eval template for Workbench browser smoke.",
      tags: ["api-journey", "prompt-workbench"],
      check_internet: false,
    },
  );
  assert(
    isUuid(created?.id),
    "Fallback Workbench eval template create did not return a UUID id.",
  );
  const detail = await client.get(
    apiPath("/model-hub/eval-templates/{template_id}/detail/", {
      template_id: created.id,
    }),
  );
  return {
    id: created.id,
    name: detail?.name || name,
    source: "created",
    requiredKeys: promptEvalRequiredKeys(detail),
    params: {},
    expectedParams: {},
    schemaKeys: [],
  };
}

async function findEvalTemplateDetailByName(client, name) {
  const payload = await client.post(
    apiPath("/model-hub/eval-templates/list/"),
    {
      page: 0,
      page_size: 10,
      owner_filter: "all",
      search: name,
      sort_by: "updated_at",
      sort_order: "desc",
    },
  );
  const row = payloadArray(payload?.items, "items").find(
    (item) => item?.name === name && isUuid(item?.id),
  );
  if (!row) return null;
  return client.get(
    apiPath("/model-hub/eval-templates/{template_id}/detail/", {
      template_id: row.id,
    }),
  );
}

function promptEvalRequiredKeys(detail) {
  return payloadArray(
    detail?.required_keys ||
      detail?.eval_required_keys ||
      detail?.config?.required_keys,
    "required_keys",
  ).filter((key) => typeof key === "string" && key.length > 0);
}

function promptEvalParamsForTemplate(detail) {
  const schema =
    detail?.config?.function_params_schema ||
    detail?.function_params_schema ||
    detail?.config?.config ||
    {};
  if (schema.min_words && schema.max_words) {
    return {
      params: { min_words: "1", max_words: "20" },
      expectedParams: { min_words: 1, max_words: 20 },
      schemaKeys: ["min_words", "max_words"],
    };
  }
  if (schema.k) {
    return {
      params: { k: "3" },
      expectedParams: { k: 3 },
      schemaKeys: ["k"],
    };
  }
  return {
    params: {},
    expectedParams: {},
    schemaKeys: Object.keys(schema),
  };
}

function buildPromptEvalConfigMapping(requiredKeys) {
  const fallbackByKey = {
    expected: "expected",
    expected_output: "expected",
    ground_truth: "expected",
    hypothesis: "text",
    input: "text",
    output: "text",
    reference: "expected",
    response: "text",
    text: "text",
  };
  const mapping = {};
  for (const key of requiredKeys) {
    mapping[key] = fallbackByKey[key] || "text";
  }
  return mapping;
}

function assertPromptEvalMapping(actual, expected) {
  for (const [key, value] of Object.entries(expected)) {
    assert(
      actual?.[key] === value,
      `Prompt Workbench eval config mapping did not preserve ${key}.`,
    );
  }
}

function assertPromptEvalParams(actual, expected) {
  for (const [key, value] of Object.entries(expected)) {
    assert(
      actual?.[key] === value,
      `Prompt Workbench eval config params did not preserve normalized ${key}.`,
    );
  }
}

async function cleanupPromptEvaluationConfig(client, promptId, evalConfigId) {
  if (!promptId || !evalConfigId) return null;
  try {
    return await client.delete(
      apiPath("/model-hub/prompt-templates/{id}/delete-evaluation-config/", {
        id: promptId,
      }),
      { query: { id: evalConfigId } },
    );
  } catch (error) {
    const message = String(error?.message || "").toLowerCase();
    if (
      error?.status === 404 ||
      message.includes("does not exist") ||
      message.includes("not found")
    ) {
      return null;
    }
    throw error;
  }
}

async function cleanupCreatedEvalTemplate(client, evalTemplateId) {
  if (!evalTemplateId) return null;
  try {
    return await client.post(
      apiPath("/model-hub/eval-templates/bulk-delete/"),
      { template_ids: [evalTemplateId] },
      { okStatuses: [200, 404] },
    );
  } catch (error) {
    const message = String(error?.message || "").toLowerCase();
    if (message.includes("not found") || message.includes("does not exist")) {
      return null;
    }
    throw error;
  }
}

function normalizeModelOption(model) {
  const modelName = model?.model_name || model?.modelName || model?.name;
  const providers = model?.providers || model?.provider;
  const type = model?.type || model?.mode || model?.model_type || "chat";
  const logoUrl = model?.logoUrl || model?.logo_url || "";
  const isAvailable = model?.is_available ?? model?.isAvailable;
  return {
    ...model,
    model_name: modelName,
    modelName,
    providers,
    type,
    logoUrl,
    logo_url: logoUrl,
    is_available: isAvailable,
    isAvailable,
  };
}

function normalizeModelDetail(model) {
  return {
    model_name: model.model_name,
    modelName: model.model_name,
    providers: model.providers,
    type: model.type || "chat",
    logoUrl: model.logoUrl || "",
    logo_url: model.logoUrl || "",
    is_available: model.is_available,
    isAvailable: model.isAvailable,
  };
}

function payloadArray(payload, key) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.[key])) return payload[key];
  if (Array.isArray(payload?.result?.[key])) return payload.result[key];
  if (Array.isArray(payload?.data?.[key])) return payload.data[key];
  if (Array.isArray(payload?.results?.[key])) return payload.results[key];
  return asArray(payload);
}

async function cleanupPromptTemplate(client, promptId) {
  if (!promptId) return;
  try {
    await client.post(
      apiPath("/model-hub/prompt-templates/bulk-delete/"),
      { ids: [promptId] },
      { okStatuses: [200, 404] },
    );
    console.error(`cleanup prompt workbench prompt: ${promptId}`);
  } catch (error) {
    if (String(error?.message || "").includes("No valid ids provided")) return;
    throw error;
  }
}

async function cleanupPromptFolder(client, folderId) {
  if (!folderId) return;
  await client.delete(
    apiPath("/model-hub/prompt-folders/{id}/", { id: folderId }),
    {
      okStatuses: [200, 204, 404],
    },
  );
  console.error(`cleanup prompt workbench folder: ${folderId}`);
}

async function assertPromptFolderReadback(client, { folderId, name }) {
  const folder = unwrapResult(
    await client.get(
      apiPath("/model-hub/prompt-folders/{id}/", { id: folderId }),
    ),
  );
  assert(
    folder?.id === folderId,
    "Prompt folder detail returned the wrong id.",
  );
  assert(
    folder?.name === name,
    `Prompt folder detail returned ${folder?.name} instead of ${name}.`,
  );
}

async function assertPromptFolderAbsentFromList(client, { folderId, name }) {
  const folders = asArray(
    await client.get(apiPath("/model-hub/prompt-folders/")),
  );
  assert(
    !folders.some((folder) => folder?.id === folderId || folder?.name === name),
    "Deleted prompt folder was still visible in the folder list.",
  );
}

async function auditPromptRunOutputDb({
  promptId,
  organizationId,
  workspaceId,
  modelName,
}) {
  assert(isUuid(promptId), "Prompt run audit requires a prompt UUID.");
  const sql = `
WITH target_versions AS (
  SELECT
    id,
    template_version,
    output,
    metadata,
    prompt_config_snapshot,
    is_draft
  FROM model_hub_promptversion
  WHERE original_template_id = ${sqlUuid(promptId)}
    AND deleted = false
  ORDER BY created_at DESC
)
SELECT json_build_object(
  'prompt_row_count', (
    SELECT count(*)
    FROM model_hub_prompttemplate
    WHERE id = ${sqlUuid(promptId)}
      AND organization_id = ${sqlUuid(organizationId)}
      AND workspace_id = ${sqlUuid(workspaceId)}
      AND deleted = false
  ),
  'version_count', (SELECT count(*) FROM target_versions),
  'output_count', COALESCE((
    SELECT jsonb_array_length(output)
    FROM target_versions
    WHERE template_version = 'v1'
    LIMIT 1
  ), 0),
  'metadata_count', COALESCE((
    SELECT jsonb_array_length(metadata)
    FROM target_versions
    WHERE template_version = 'v1'
    LIMIT 1
  ), 0),
  'model_name', (
    SELECT prompt_config_snapshot->'configuration'->>'model'
    FROM target_versions
    WHERE template_version = 'v1'
    LIMIT 1
  ),
  'expected_model_name', ${sqlString(modelName)},
  'is_draft', (
    SELECT is_draft
    FROM target_versions
    WHERE template_version = 'v1'
    LIMIT 1
  )
);
`;
  return runPostgresJson(sql);
}

async function auditDeletedPromptFolderDb({
  folderId,
  promptId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(folderId), "Folder delete audit requires a folder UUID.");
  assert(isUuid(promptId), "Folder delete audit requires a prompt UUID.");
  const sql = `
WITH target_folder AS (
  SELECT id, deleted, deleted_at
  FROM model_hub_prompt_folder
  WHERE id = ${sqlUuid(folderId)}
    AND organization_id = ${sqlUuid(organizationId)}
    AND workspace_id = ${sqlUuid(workspaceId)}
),
target_prompts AS (
  SELECT id, deleted, deleted_at
  FROM model_hub_prompttemplate
  WHERE id = ${sqlUuid(promptId)}
    AND prompt_folder_id = ${sqlUuid(folderId)}
    AND organization_id = ${sqlUuid(organizationId)}
    AND workspace_id = ${sqlUuid(workspaceId)}
),
target_versions AS (
  SELECT id, deleted, deleted_at
  FROM model_hub_promptversion
  WHERE original_template_id IN (SELECT id FROM target_prompts)
),
target_eval_configs AS (
  SELECT id, deleted, deleted_at
  FROM model_hub_promptevalconfig
  WHERE prompt_template_id IN (SELECT id FROM target_prompts)
)
SELECT json_build_object(
  'folder_row_count', (SELECT count(*) FROM target_folder),
  'folder_deleted_at_set', COALESCE((
    SELECT bool_and(deleted = true AND deleted_at IS NOT NULL)
    FROM target_folder
  ), false),
  'prompt_row_count', (SELECT count(*) FROM target_prompts),
  'prompt_deleted_at_set', COALESCE((
    SELECT bool_and(deleted = true AND deleted_at IS NOT NULL)
    FROM target_prompts
  ), false),
  'version_row_count', (SELECT count(*) FROM target_versions),
  'version_deleted_at_set', COALESCE((
    SELECT bool_and(deleted = true AND deleted_at IS NOT NULL)
    FROM target_versions
  ), false),
  'eval_config_row_count', (SELECT count(*) FROM target_eval_configs),
  'eval_config_deleted_at_set', COALESCE((
    SELECT bool_and(deleted = true AND deleted_at IS NOT NULL)
    FROM target_eval_configs
  ), false)
);
`;
  return runPostgresJson(sql);
}

async function hardDeletePromptWorkbenchFixtureDb({
  folderId,
  promptId,
  evalConfigId,
  createdEvalTemplateId,
  organizationId,
  workspaceId,
}) {
  if (!folderId && !promptId && !evalConfigId && !createdEvalTemplateId) {
    return {
      remaining_folder_count: 0,
      remaining_prompt_count: 0,
      remaining_version_count: 0,
      remaining_eval_config_count: 0,
      remaining_eval_template_count: 0,
    };
  }
  const folderPredicate = folderId ? `id = ${sqlUuid(folderId)}` : "false";
  const promptPredicate = promptId ? `id = ${sqlUuid(promptId)}` : "false";
  const evalConfigPredicate = evalConfigId
    ? `id = ${sqlUuid(evalConfigId)}`
    : "false";
  const evalTemplatePredicate = createdEvalTemplateId
    ? `id = ${sqlUuid(createdEvalTemplateId)}`
    : "false";
  const sql = `
BEGIN;
CREATE TEMP TABLE _pwb_folder_ids(id uuid) ON COMMIT DROP;
INSERT INTO _pwb_folder_ids
SELECT id
FROM model_hub_prompt_folder
WHERE organization_id = ${sqlUuid(organizationId)}
  AND workspace_id = ${sqlUuid(workspaceId)}
  AND (${folderPredicate});

CREATE TEMP TABLE _pwb_prompt_ids(id uuid) ON COMMIT DROP;
INSERT INTO _pwb_prompt_ids
SELECT id
FROM model_hub_prompttemplate
WHERE organization_id = ${sqlUuid(organizationId)}
  AND workspace_id = ${sqlUuid(workspaceId)}
  AND (
    ${promptPredicate}
    OR prompt_folder_id IN (SELECT id FROM _pwb_folder_ids)
  );

CREATE TEMP TABLE _pwb_version_ids(id uuid) ON COMMIT DROP;
INSERT INTO _pwb_version_ids
SELECT id
FROM model_hub_promptversion
WHERE original_template_id IN (SELECT id FROM _pwb_prompt_ids);

CREATE TEMP TABLE _pwb_eval_config_ids(id uuid) ON COMMIT DROP;
INSERT INTO _pwb_eval_config_ids
SELECT id
FROM model_hub_promptevalconfig
WHERE ${evalConfigPredicate}
  OR prompt_template_id IN (SELECT id FROM _pwb_prompt_ids);

CREATE TEMP TABLE _pwb_eval_template_ids(id uuid) ON COMMIT DROP;
INSERT INTO _pwb_eval_template_ids
SELECT id
FROM model_hub_evaltemplate
WHERE ${evalTemplatePredicate};

DELETE FROM model_hub_promptversion_labels
WHERE promptversion_id IN (SELECT id FROM _pwb_version_ids);
DELETE FROM model_hub_prompttemplate_collaborators
WHERE prompttemplate_id IN (SELECT id FROM _pwb_prompt_ids);
DELETE FROM model_hub_promptevalconfig
WHERE id IN (SELECT id FROM _pwb_eval_config_ids);
DELETE FROM model_hub_promptversion
WHERE id IN (SELECT id FROM _pwb_version_ids);
DELETE FROM model_hub_prompttemplate
WHERE id IN (SELECT id FROM _pwb_prompt_ids);
DELETE FROM model_hub_prompt_folder
WHERE id IN (SELECT id FROM _pwb_folder_ids);
DELETE FROM model_hub_evaltemplate
WHERE id IN (SELECT id FROM _pwb_eval_template_ids);

SELECT json_build_object(
  'remaining_folder_count', (
    SELECT count(*) FROM model_hub_prompt_folder
    WHERE id IN (SELECT id FROM _pwb_folder_ids)
  ),
  'remaining_prompt_count', (
    SELECT count(*) FROM model_hub_prompttemplate
    WHERE id IN (SELECT id FROM _pwb_prompt_ids)
  ),
  'remaining_version_count', (
    SELECT count(*) FROM model_hub_promptversion
    WHERE id IN (SELECT id FROM _pwb_version_ids)
  ),
  'remaining_eval_config_count', (
    SELECT count(*) FROM model_hub_promptevalconfig
    WHERE id IN (SELECT id FROM _pwb_eval_config_ids)
  ),
  'remaining_eval_template_count', (
    SELECT count(*) FROM model_hub_evaltemplate
    WHERE id IN (SELECT id FROM _pwb_eval_template_ids)
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
  const { stdout } = await execFile(
    "docker",
    ["exec", container, "psql", "-qAt", "-U", user, "-d", database, "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const jsonLine = stdout
    .trim()
    .split(/\r?\n/)
    .reverse()
    .find((line) => line.trim().startsWith("{"));
  assert(jsonLine, "Postgres DB command returned no JSON output.");
  return JSON.parse(jsonLine);
}

function sqlUuid(value) {
  assert(isUuid(value), `Expected UUID, got ${value}`);
  return `'${String(value).replaceAll("'", "''")}'::uuid`;
}

function sqlString(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`;
}

function appendCleanupError(caughtError, cleanupError) {
  if (!caughtError) return cleanupError;
  caughtError.message = `${caughtError.message}; cleanup failed: ${cleanupError.message}`;
  return caughtError;
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

async function waitForFunction(page, fn, timeout = 30000) {
  await page.waitForFunction(fn, { timeout });
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

async function waitForNoVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) =>
      !window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      }),
    { timeout },
    { text, exact },
  );
}

async function waitForEditorText(page, fragments, timeout = 30000) {
  await page.waitForFunction(
    (expectedFragments) => {
      const editors = window.visibleElements(".ql-editor");
      return editors.some((editor) => {
        const textContent = window.normalizeText(editor.textContent);
        return expectedFragments.every((fragment) =>
          textContent.includes(fragment),
        );
      });
    },
    { timeout },
    fragments,
  );
}

async function waitForPromptEvaluationGrid(
  page,
  { evalConfigName, textValue },
  timeout = 30000,
) {
  await waitForVisibleText(page, "Show Variables", { exact: true, timeout });
  await waitForVisibleText(page, "Add Evaluations", { exact: true, timeout });
  await page.waitForFunction(
    ({ expectedEvalName, expectedTextValue }) => {
      const grid =
        document.querySelector(".prompt-evaluation-gird") ||
        document.querySelector(".prompt-variable-gird");
      if (!grid) return false;
      const text = window.normalizeText(grid.textContent);
      return (
        text.includes("{{text}}") &&
        text.includes(expectedTextValue) &&
        text.includes(expectedEvalName) &&
        text.includes("v1") &&
        (text.includes("NA") || text.includes("Status:"))
      );
    },
    { timeout },
    { expectedEvalName: evalConfigName, expectedTextValue: textValue },
  );
}

async function openModelPickerAndSelectSafeModel(page, model) {
  await waitForVisibleText(page, model.model_name, { exact: true });
  await clickModelPickerButton(page, model.model_name);
  await page.waitForSelector('input[placeholder="Select model"]', {
    timeout: 30000,
  });
  await waitForResponseDuring(
    page,
    "model picker search",
    (response) => {
      if (
        !response.url().includes("/model-hub/api/models_list/") ||
        response.status() >= 400
      ) {
        return false;
      }
      const url = new URL(response.url());
      return url.searchParams.get("search") === model.model_name;
    },
    () => typeModelSearch(page, model.model_name),
  );
  await waitForVisibleMenuItemText(page, model.model_name);
  if (model.isAvailable !== false) {
    await waitForNoVisibleText(page, "Configure an api key", { timeout: 5000 });
  }
}

async function clickModelPickerButton(page, modelName) {
  const labelPoint = await page.evaluate((expectedText) => {
    const label = window.visibleElements().find((candidate) => {
      return window.normalizeText(candidate.textContent) === expectedText;
    });
    if (!label) return null;
    const rect = label.getBoundingClientRect();
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
  }, modelName);
  assert(labelPoint, `Could not locate model picker label ${modelName}.`);
  await page.mouse.click(labelPoint.x, labelPoint.y);

  const opened = await page
    .waitForSelector('input[placeholder="Select model"]', { timeout: 1500 })
    .then(() => true)
    .catch(() => false);
  if (opened) return;

  const chevronPoint = await page.evaluate((expectedText) => {
    const label = window.visibleElements().find((candidate) => {
      return window.normalizeText(candidate.textContent) === expectedText;
    });
    if (!label) return null;
    const labelRect = label.getBoundingClientRect();
    const labelCenterY = labelRect.top + labelRect.height / 2;
    const button = window.visibleElements("button").find((candidate) => {
      const rect = candidate.getBoundingClientRect();
      const text = window.normalizeText(candidate.textContent);
      return (
        rect.left > labelRect.right &&
        rect.left < labelRect.right + 180 &&
        Math.abs(rect.top + rect.height / 2 - labelCenterY) < 24 &&
        text !== "Params"
      );
    });
    if (!button) return null;
    const rect = button.getBoundingClientRect();
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
  }, modelName);
  assert(chevronPoint, `Could not locate model picker chevron ${modelName}.`);
  await page.mouse.click(chevronPoint.x, chevronPoint.y);
}

async function waitForVisibleMenuItemText(page, text, timeout = 30000) {
  await page.waitForFunction(
    (expectedText) =>
      window.visibleElements('[role="menuitem"]').some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return (
          textContent === expectedText || textContent.includes(expectedText)
        );
      }),
    { timeout },
    text,
  );
}

async function clickVisibleMenuItemContaining(page, text) {
  await waitForVisibleMenuItemText(page, text);
  const clicked = await page.evaluate((expectedText) => {
    const menuItems = window.visibleElements('[role="menuitem"]');
    const exactMatch = menuItems.find((candidate) => {
      const textContent = window.normalizeText(candidate.textContent);
      return textContent === expectedText;
    });
    const menuItem =
      exactMatch ||
      menuItems.find((candidate) => {
        const textContent = window.normalizeText(candidate.textContent);
        return textContent.includes(expectedText);
      });
    if (!menuItem || menuItem.getAttribute("aria-disabled") === "true") {
      return false;
    }
    menuItem.dispatchEvent(
      new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
    );
    menuItem.dispatchEvent(
      new MouseEvent("mouseup", { bubbles: true, cancelable: true }),
    );
    menuItem.dispatchEvent(
      new MouseEvent("click", { bubbles: true, cancelable: true }),
    );
    return true;
  }, text);
  assert(clicked, `Could not click menu item containing ${text}.`);
}

async function typeModelSearch(page, value) {
  const selector = 'input[placeholder="Select model"]';
  await page.waitForSelector(selector, { timeout: 30000 });
  await page.click(selector);
  await page.keyboard.down(modifierKey());
  await page.keyboard.press("A");
  await page.keyboard.up(modifierKey());
  await page.keyboard.press("Backspace");
  await page.type(selector, value);
}

async function waitForPromptOutput(page) {
  await page.waitForFunction(
    () =>
      window.visibleElements(".streaming-text").some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return textContent.length > 0;
      }),
    { timeout: 120000 },
  );
}

async function waitForPromptOutputMetadata(page) {
  await page.waitForFunction(
    () =>
      window.visibleElements(".prompt-output-container").some((element) => {
        const textContent = window.normalizeText(element.textContent);
        const hasResponseTime = /\b\d+(?:\.\d+)?s\b/.test(textContent);
        const hasCost =
          textContent.includes("<0.1") || /\b\d+\.\d+\b/.test(textContent);
        const hasTokenCount = /\b\d+\b/.test(textContent);
        return hasResponseTime && hasCost && hasTokenCount;
      }),
    { timeout: 120000 },
  );
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
        element?.closest("button,a,[role='button'],[role='menuitem']") ||
        element;
      if (!clickable || clickable.disabled) return false;
      clickable.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
      );
      clickable.dispatchEvent(
        new MouseEvent("mouseup", { bubbles: true, cancelable: true }),
      );
      clickable.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
      return true;
    },
    { text, exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

async function clickFolderActionMenu(page, { folderId, folderName }) {
  await page.waitForFunction(
    ({ id, name }) =>
      window
        .visibleElements(`a[href$="/dashboard/workbench/${id}"]`)
        .some((anchor) =>
          window.normalizeText(anchor.textContent).includes(name),
        ),
    { timeout: 30000 },
    { id: folderId, name: folderName },
  );
  const clicked = await page.evaluate(
    ({ id, name }) => {
      const anchor = window
        .visibleElements(`a[href$="/dashboard/workbench/${id}"]`)
        .find((candidate) =>
          window.normalizeText(candidate.textContent).includes(name),
        );
      const button = Array.from(anchor?.querySelectorAll("button") || [])
        .reverse()
        .find((candidate) => !candidate.disabled);
      if (!button) return false;
      button.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
      );
      button.dispatchEvent(
        new MouseEvent("mouseup", { bubbles: true, cancelable: true }),
      );
      button.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
      return true;
    },
    { id: folderId, name: folderName },
  );
  assert(clicked, `Could not click folder action menu: ${folderName}.`);
}

async function clickVisibleMenuItem(page, text) {
  await page.waitForFunction(
    (expectedText) =>
      window.visibleElements().some((element) => {
        const menuItem = element.closest('[role="menuitem"]');
        return (
          menuItem &&
          window.normalizeText(menuItem.textContent) === expectedText
        );
      }),
    { timeout: 30000 },
    text,
  );
  const clicked = await page.evaluate((expectedText) => {
    const element = window.visibleElements().find((candidate) => {
      const menuItem = candidate.closest('[role="menuitem"]');
      return (
        menuItem && window.normalizeText(menuItem.textContent) === expectedText
      );
    });
    const menuItem = element?.closest('[role="menuitem"]');
    if (!menuItem) return false;
    menuItem.dispatchEvent(
      new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
    );
    menuItem.dispatchEvent(
      new MouseEvent("mouseup", { bubbles: true, cancelable: true }),
    );
    menuItem.dispatchEvent(
      new MouseEvent("click", { bubbles: true, cancelable: true }),
    );
    return true;
  }, text);
  assert(clicked, `Could not click menu item ${text}.`);
}

async function clickDialogButton(page, text) {
  await page.waitForFunction(
    (expectedText) => {
      const dialog = document.querySelector('[role="dialog"]');
      if (!dialog) return false;
      return window.visibleElements().some((element) => {
        if (!dialog.contains(element)) return false;
        const button = element.closest("button,[role='button']");
        return (
          button &&
          dialog.contains(button) &&
          !button.disabled &&
          window.normalizeText(button.textContent) === expectedText
        );
      });
    },
    { timeout: 30000 },
    text,
  );
  const clicked = await page.evaluate((expectedText) => {
    const dialog = document.querySelector('[role="dialog"]');
    const element = window.visibleElements().find((candidate) => {
      if (!dialog?.contains(candidate)) return false;
      const button = candidate.closest("button,[role='button']");
      return (
        button &&
        dialog.contains(button) &&
        !button.disabled &&
        window.normalizeText(button.textContent) === expectedText
      );
    });
    const button = element?.closest("button,[role='button']");
    if (!button) return false;
    button.dispatchEvent(
      new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
    );
    button.dispatchEvent(
      new MouseEvent("mouseup", { bubbles: true, cancelable: true }),
    );
    button.dispatchEvent(
      new MouseEvent("click", { bubbles: true, cancelable: true }),
    );
    return true;
  }, text);
  assert(clicked, `Could not click dialog button ${text}.`);
}

async function typeIntoDialogInput(page, value) {
  await page.waitForFunction(
    () => {
      const dialog = document.querySelector('[role="dialog"]');
      return Boolean(
        dialog &&
          window
            .visibleElements("input")
            .some((input) => dialog.contains(input) && !input.disabled),
      );
    },
    { timeout: 30000 },
  );
  const inputs = await page.$$('[role="dialog"] input');
  for (const input of inputs) {
    const visible = await input.evaluate((element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        !element.disabled &&
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    });
    if (!visible) continue;
    await input.click({ clickCount: 3 });
    await page.keyboard.down(modifierKey());
    await page.keyboard.press("A");
    await page.keyboard.up(modifierKey());
    await page.keyboard.press("Backspace");
    await page.keyboard.type(value);
    return;
  }
  throw new Error("No visible dialog input found.");
}

async function typeSearch(page, value) {
  const selector = 'input[placeholder="Search in prompts"]';
  await page.waitForSelector(selector, { timeout: 30000 });
  await page.click(selector);
  await page.keyboard.down(modifierKey());
  await page.keyboard.press("A");
  await page.keyboard.up(modifierKey());
  await page.keyboard.press("Backspace");
  await page.type(selector, value);
}

async function clickPromptItem(page, promptId, promptName) {
  await page.waitForFunction(
    ({ id, name }) =>
      window
        .visibleElements(`a[href$="/dashboard/workbench/create/${id}"]`)
        .some((anchor) =>
          window.normalizeText(anchor.textContent).includes(name),
        ),
    { timeout: 30000 },
    { id: promptId, name: promptName },
  );
  const clicked = await page.evaluate(
    ({ id, name }) => {
      const anchor = window
        .visibleElements(`a[href$="/dashboard/workbench/create/${id}"]`)
        .find((candidate) =>
          window.normalizeText(candidate.textContent).includes(name),
        );
      if (!anchor) return false;
      anchor.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
      );
      anchor.dispatchEvent(
        new MouseEvent("mouseup", { bubbles: true, cancelable: true }),
      );
      anchor.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
      return true;
    },
    { id: promptId, name: promptName },
  );
  assert(clicked, `Could not click prompt item: ${promptName}.`);
}

async function responseJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function unwrapResult(body) {
  if (body && Object.prototype.hasOwnProperty.call(body, "result")) {
    return body.result;
  }
  if (body && Object.prototype.hasOwnProperty.call(body, "data")) {
    return unwrapResult(body.data);
  }
  return body;
}

function trackSocketPayload(statuses, event, direction) {
  const payload = typeof event === "string" ? event : event?.payload;
  if (!payload) return;
  try {
    const body = JSON.parse(payload);
    const status = body?.streaming_status || body?.type;
    if (status) statuses.push(`${direction}:${status}`);
  } catch {
    // Ignore non-JSON control frames.
  }
}

function uniqueValues(values) {
  return Array.from(new Set(values));
}

function modelParameterRequestObserved(requests, model) {
  return requests.some((requestLabel) => {
    const match = requestLabel.match(/https?:\/\/\S+$/);
    if (!match) return false;
    try {
      const url = new URL(match[0]);
      return (
        url.pathname.endsWith("/model-hub/api/model_parameters/") &&
        url.searchParams.get("model") === model.model_name &&
        url.searchParams.get("provider") === model.providers
      );
    } catch {
      return false;
    }
  });
}

async function parseWorkbenchFolderId(page) {
  const pathname = await page.evaluate(() => window.location.pathname);
  const match = pathname.match(/\/dashboard\/workbench\/([0-9a-f-]{36})$/i);
  return match?.[1] || null;
}

function isPromptWorkbenchApiUrl(url) {
  return (
    url.includes("/model-hub/prompt-") || url.includes("/model-hub/api/model_")
  );
}

function shortRunId(runId) {
  return String(runId || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .slice(-8);
}

function modifierKey() {
  return process.platform === "darwin" ? "Meta" : "Control";
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
