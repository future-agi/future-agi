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
  isUuid,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFile = promisify(execFileCallback);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/prompt-workbench-eval-drawer-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/prompt-workbench-eval-drawer-smoke-failure.png";

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = shortRunId(auth.runId);
  const promptName = `ui prompt eval drawer ${suffix}`;
  const evalConfigName = `pwb_${suffix}`;
  const variableNames = {
    text: ["one two three four"],
    expected: ["short answer"],
  };
  const promptConfig = [
    buildPromptConfig(
      "You answer in one short sentence.",
      "Use this text: {{text}}. Expected hint: {{expected}}.",
    ),
  ];
  let browser = null;
  let promptId = null;
  let evalConfigId = null;
  let caughtError = null;
  let evalConfigDeleted = false;
  let hardCleanup = null;

  const apiFailures = [];
  const pageErrors = [];
  const browserMutations = [];
  const evidence = {
    prompt_name: promptName,
    eval_config_name: evalConfigName,
  };

  const evalTemplate = await findEvalTemplateDetailByName(
    auth.client,
    "word_count_in_range",
  );
  assert(
    evalTemplate?.id && isUuid(evalTemplate.id),
    "System eval word_count_in_range was not available.",
  );
  const evalTemplateInfo = {
    id: evalTemplate.id,
    name: evalTemplate.name,
    requiredKeys: promptEvalRequiredKeys(evalTemplate),
  };
  evidence.eval_template_id = evalTemplateInfo.id;
  evidence.eval_template_name = evalTemplateInfo.name;
  evidence.eval_required_keys = evalTemplateInfo.requiredKeys;
  assert(
    evalTemplateInfo.requiredKeys.includes("text"),
    "word_count_in_range did not expose the expected text required key.",
  );

  try {
    promptId = await createWorkbenchPrompt(auth.client, {
      name: promptName,
      runId: auth.runId,
      variableNames,
      promptConfig,
    });
    evidence.prompt_id = promptId;
    await saveWorkbenchPromptDraft(auth.client, {
      promptId,
      name: promptName,
      variableNames,
      promptConfig,
    });
    const setupPromptRun = await runAndCommitWorkbenchPrompt(auth.client, {
      promptId,
      name: promptName,
      variableNames,
      promptConfig,
      runId: auth.runId,
    });
    evidence.prompt_version = setupPromptRun.detail.version;
    evidence.prompt_is_draft = setupPromptRun.detail.is_draft;
    evidence.prompt_run_status = setupPromptRun.status;
    evidence.prompt_run_output_preview = previewText(setupPromptRun.outputs[0]);

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
      const url = request.url();
      if (isPromptEvalMutation(url, request.method())) {
        browserMutations.push({
          method: request.method(),
          url: redactUrl(url),
          body: safeJson(request.postData()),
        });
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isPromptWorkbenchApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponseDuring(
      page,
      "prompt detail load",
      (response) =>
        response.url().includes(`/model-hub/prompt-templates/${promptId}/`) &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/workbench/create/${promptId}`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, `/dashboard/workbench/create/${promptId}`);
    await waitForVisibleText(page, promptName);
    await waitForVisibleText(page, "Playground", { exact: true });
    await waitForVisibleText(page, "Evaluation", { exact: true });
    await waitForNoVisibleText(page, "Draft", { exact: true });
    await waitForVisibleText(page, setupPromptRun.visibleOutputSnippet);

    await waitForResponseDuring(
      page,
      "evaluation tab data load",
      (response) =>
        response
          .url()
          .includes(`/model-hub/prompt-templates/${promptId}/evaluations/`) &&
        response.status() < 400,
      () => clickVisibleText(page, "Evaluation", { exact: true }),
    );
    await waitForSelector(page, '[data-testid="workbench-evaluation-grid"]');
    await waitForVisibleText(page, "{{text}}", { exact: true });
    await waitForVisibleText(page, "{{expected}}", { exact: true });

    await clickByTestId(page, "workbench-add-evaluations-button");
    await waitForVisibleText(page, "All Evaluations", { exact: true });
    await waitForVisibleText(page, "No evaluations added", { exact: true });
    await clickByTestId(page, "evaluation-drawer-empty-add-evaluations");
    await waitForVisibleText(page, "Select Evaluation", { exact: true });
    await waitForResponseDuring(
      page,
      "eval picker search",
      (response) =>
        response.url().includes("/model-hub/eval-templates/list/") &&
        response.request().method() === "POST" &&
        response.status() < 400,
      () =>
        typeSearchInput(page, "Search evaluations...", "word_count_in_range"),
    );
    await clickByTestId(page, `eval-picker-add-${evalTemplateInfo.id}`);
    await waitForVisibleText(page, "word_count_in_range");
    await setTextFieldByTestId(page, "eval-picker-name-input", evalConfigName);
    await ensureMappingValue(page, "text", "text");
    await fillIfVisibleByLabel(page, "min_words", "1");
    await fillIfVisibleByLabel(page, "max_words", "20");

    const createResponse = await waitForResponseDuring(
      page,
      "workbench eval config create",
      (response) =>
        response
          .url()
          .includes(
            `/model-hub/prompt-templates/${promptId}/update-evaluation-configs/`,
          ) &&
        response.request().method() === "POST" &&
        response.status() < 400,
      () =>
        clickByTestId(page, "eval-picker-save-evaluation", { preferDom: true }),
    );
    const createRequest = safeJson(createResponse.request().postData());
    assert(
      createRequest?.id === evalTemplateInfo.id,
      "Workbench browser eval config create used the wrong eval template.",
    );
    assert(
      createRequest?.mapping?.text === "text",
      `Workbench browser eval config mapping was ${JSON.stringify(
        createRequest?.mapping,
      )}.`,
    );
    const createPayload = unwrapResult(await responseJson(createResponse));
    evalConfigId = createPayload?.prompt_eval_config_id;
    assert(
      isUuid(evalConfigId),
      "Workbench browser eval config create did not return prompt_eval_config_id.",
    );
    evidence.eval_config_id = evalConfigId;
    evidence.create_request = {
      id: createRequest.id,
      name: createRequest.name,
      mapping: createRequest.mapping,
      params:
        createRequest?.config?.params ||
        createRequest?.params ||
        createRequest?.config?.run_config?.params ||
        {},
      is_run: createRequest.is_run,
      version_to_run: createRequest.version_to_run,
    };
    await clickByTestId(page, "evaluation-drawer-close", { preferDom: true });
    await waitForNoVisibleText(page, "All Evaluations", { exact: true });

    const configsPayload = await auth.client.get(
      apiPath("/model-hub/prompt-templates/{id}/evaluation-configs/", {
        id: promptId,
      }),
    );
    const configRow = payloadArray(
      configsPayload?.evaluation_configs,
      "evaluation_configs",
    ).find((row) => row?.id === evalConfigId);
    assert(configRow, "Workbench evaluation-configs did not include row.");
    assert(
      configRow.name === evalConfigName,
      "Workbench evaluation-configs returned the wrong config name.",
    );
    assertPromptEvalMapping(configRow.mapping, { text: "text" });

    await waitForVisibleText(page, evalConfigName);
    await waitForSelector(
      page,
      `[data-testid="workbench-eval-cell-${evalConfigId}-v1-0"]`,
    );
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    const runResponse = await runWorkbenchEvalCell(page, {
      evalConfigId,
      version: "v1",
      rowIndex: 0,
      promptId,
    });
    const runRequest = safeJson(runResponse.request().postData());
    assert(
      Array.isArray(runRequest?.prompt_eval_config_ids) &&
        runRequest.prompt_eval_config_ids.includes(evalConfigId),
      "Workbench eval run request did not include the created eval config id.",
    );
    assert(
      Array.isArray(runRequest?.version_to_run) &&
        runRequest.version_to_run.includes("v1"),
      "Workbench eval run request did not include v1.",
    );
    assert(
      runRequest?.run_index === 0,
      "Workbench eval cell run request did not include row index 0.",
    );
    evidence.run_request = runRequest;

    const statusReadback = await waitForPromptEvalStatus(auth.client, {
      promptId,
      evalConfigId,
    });
    evidence.eval_status_after_run = statusReadback.status;
    evidence.eval_output_after_run = statusReadback.output;

    const activeAudit = await loadPromptEvalConfigDbAudit({
      promptId,
      evalConfigId,
      evalTemplateId: evalTemplateInfo.id,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assertPromptEvalConfigDbAudit(activeAudit, {
      promptId,
      evalConfigId,
      evalTemplateId: evalTemplateInfo.id,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      mapping: { text: "text" },
      expectedDeleted: false,
    });
    evidence.db_active_config_status = activeAudit.status;

    await deletePromptEvalConfigIfPresent(auth.client, promptId, evalConfigId);
    evalConfigDeleted = true;
    const deletedAudit = await loadPromptEvalConfigDbAudit({
      promptId,
      evalConfigId,
      evalTemplateId: evalTemplateInfo.id,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assertPromptEvalConfigDbAudit(deletedAudit, {
      promptId,
      evalConfigId,
      evalTemplateId: evalTemplateInfo.id,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      mapping: { text: "text" },
      expectedDeleted: true,
    });
    evidence.eval_config_deleted_at_set = deletedAudit.deleted_at_set;

    await cleanupPromptTemplate(auth.client, promptId);
    hardCleanup = await hardDeletePromptWorkbenchFixtureDb({
      promptId,
      evalConfigId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assert(
      Number(hardCleanup.remaining_prompt_count) === 0 &&
        Number(hardCleanup.remaining_version_count) === 0 &&
        Number(hardCleanup.remaining_eval_config_count) === 0,
      `Hard cleanup left prompt eval drawer rows behind: ${JSON.stringify(
        hardCleanup,
      )}`,
    );
    evidence.hard_cleanup = hardCleanup;

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
          browser_mutations: browserMutations,
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
          browser_mutations: browserMutations,
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
    if (evalConfigId && promptId && !evalConfigDeleted) {
      await deletePromptEvalConfigIfPresent(auth.client, promptId, evalConfigId)
        .then(() => {
          evalConfigDeleted = true;
        })
        .catch((error) => {
          caughtError = appendCleanupError(caughtError, error);
        });
    }
    await cleanupPromptTemplate(auth.client, promptId).catch((error) => {
      caughtError = appendCleanupError(caughtError, error);
    });
    if (promptId || evalConfigId) {
      await hardDeletePromptWorkbenchFixtureDb({
        promptId,
        evalConfigId,
        organizationId: auth.organizationId,
        workspaceId: auth.workspaceId,
      }).catch((error) => {
        caughtError = appendCleanupError(caughtError, error);
      });
    }
  }

  if (caughtError) throw caughtError;
}

async function createWorkbenchPrompt(
  client,
  { name, runId, variableNames, promptConfig },
) {
  const created = await client.post(
    apiPath("/model-hub/prompt-templates/create-draft/"),
    {
      name,
      description: "Prompt Workbench eval drawer browser smoke candidate.",
      variable_names: variableNames,
      metadata: { source: "api-journey-browser", run_id: runId },
      prompt_config: promptConfig,
    },
  );
  const promptId =
    created?.id || created?.root_template || created?.rootTemplate;
  assert(isUuid(promptId), "Workbench prompt create did not return a UUID id.");
  return promptId;
}

async function saveWorkbenchPromptDraft(
  client,
  { promptId, name, variableNames, promptConfig },
) {
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
}

async function runAndCommitWorkbenchPrompt(
  client,
  { promptId, name, variableNames, promptConfig, runId },
) {
  await client.post(
    apiPath("/model-hub/prompt-templates/{id}/run_template/", {
      id: promptId,
    }),
    {
      name,
      version: "v1",
      is_run: "prompt",
      variable_names: variableNames,
      placeholders: {},
      evaluation_configs: [],
      prompt_config: promptConfig,
    },
  );

  const runStatus = await waitForWorkbenchPromptRunOutput(client, {
    promptId,
    version: "v1",
  });

  await client.post(
    apiPath("/model-hub/prompt-templates/{id}/commit/", { id: promptId }),
    {
      version_name: "v1",
      message: `Prompt eval drawer setup ${runId}`,
      is_draft: false,
      set_default: true,
    },
  );

  const detail = await waitForWorkbenchPromptDetailOutput(client, {
    promptId,
  });
  assert(
    detail?.is_draft === false,
    "Workbench prompt setup did not commit v1 before browser open.",
  );
  return {
    ...runStatus,
    detail,
    visibleOutputSnippet: visibleTextSnippet(detail.output?.[0]),
  };
}

async function waitForWorkbenchPromptRunOutput(client, { promptId, version }) {
  const started = Date.now();
  let lastPayload = null;
  while (Date.now() - started < 120000) {
    const payload = await client.get(
      apiPath("/model-hub/prompt-templates/{id}/get-run-status/", {
        id: promptId,
      }),
      { query: { template_version: version } },
    );
    lastPayload = payload;
    const outputs = promptRunOutputs(payload);
    if (outputs.length > 0) {
      return {
        status: String(unwrapResult(payload)?.status || ""),
        outputs,
      };
    }
    const unwrapped = unwrapResult(payload);
    const errorMessage =
      unwrapped?.error_message || unwrapped?.executions_result?.error_message;
    if (String(unwrapped?.status || "").toLowerCase() === "failed") {
      throw new Error(
        `Workbench prompt setup run failed: ${errorMessage || "unknown error"}`,
      );
    }
    if (errorMessage) {
      throw new Error(`Workbench prompt setup run failed: ${errorMessage}`);
    }
    await delay(2500);
  }
  throw new Error(
    `Timed out waiting for workbench prompt setup output: ${JSON.stringify(
      lastPayload,
    )}`,
  );
}

async function waitForWorkbenchPromptDetailOutput(client, { promptId }) {
  const started = Date.now();
  let lastDetail = null;
  while (Date.now() - started < 60000) {
    lastDetail = await client.get(
      apiPath("/model-hub/prompt-templates/{id}/", { id: promptId }),
    );
    if (Array.isArray(lastDetail?.output) && lastDetail.output.length > 0) {
      return lastDetail;
    }
    await delay(1000);
  }
  throw new Error(
    `Timed out waiting for workbench prompt detail output: ${JSON.stringify(
      lastDetail,
    )}`,
  );
}

function buildPromptConfig(systemText, userText) {
  return {
    messages: [
      {
        role: "system",
        content: [{ type: "text", text: systemText }],
      },
      {
        role: "user",
        content: [{ type: "text", text: userText }],
      },
    ],
    configuration: {
      model: "gpt-4o-mini",
      model_detail: {
        model_name: "gpt-4o-mini",
        modelName: "gpt-4o-mini",
        providers: "openai",
        type: "chat",
        is_available: true,
        isAvailable: true,
      },
      max_tokens: 16,
      temperature: 0,
      top_p: 1,
      response_format: "text",
      output_format: "string",
      template_format: "mustache",
    },
    placeholders: [],
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

async function runWorkbenchEvalCell(page, { evalConfigId, version, rowIndex }) {
  const cellSelector = `[data-testid="workbench-eval-cell-${evalConfigId}-${version}-${rowIndex}"]`;
  const runSelector = `[data-testid="workbench-eval-run-${evalConfigId}-${version}-${rowIndex}"]`;
  await waitForSelector(page, cellSelector);
  await page.hover(cellSelector);
  await page.waitForSelector(runSelector, { visible: true, timeout: 30000 });
  return waitForResponseDuring(
    page,
    "workbench eval cell run",
    (response) =>
      response.url().includes("/run-evals-on-multiple-versions/") &&
      response.request().method() === "POST" &&
      response.status() < 400,
    () => page.click(runSelector),
  );
}

async function waitForPromptEvalStatus(client, { promptId, evalConfigId }) {
  const started = Date.now();
  let lastStatus = null;
  let lastOutput = null;
  while (Date.now() - started < 120000) {
    const payload = await client.get(
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
    const v1 = payload?.v1 || {};
    lastStatus = v1.eval_status?.[evalConfigId] || null;
    lastOutput = v1.eval_output?.[evalConfigId] || null;
    if (lastStatus && !["NotStarted", "Not Started"].includes(lastStatus)) {
      return { status: lastStatus, output: lastOutput };
    }
    await delay(2000);
  }
  throw new Error(
    `Timed out waiting for workbench eval status; last=${lastStatus}, output=${JSON.stringify(
      lastOutput,
    )}`,
  );
}

async function deletePromptEvalConfigIfPresent(client, promptId, evalConfigId) {
  if (!promptId || !evalConfigId) return null;
  try {
    return await client.delete(
      apiPath("/model-hub/prompt-templates/{id}/delete-evaluation-config/", {
        id: promptId,
      }),
      { query: { id: evalConfigId }, okStatuses: [200, 404] },
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
    const message = String(error?.message || "").toLowerCase();
    if (message.includes("no valid ids provided")) return;
    throw error;
  }
}

async function loadPromptEvalConfigDbAudit({
  promptId,
  evalConfigId,
  evalTemplateId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(promptId), "promptId must be a UUID for DB audit.");
  assert(isUuid(evalConfigId), "evalConfigId must be a UUID for DB audit.");
  assert(isUuid(evalTemplateId), "evalTemplateId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const workspaceFilter = workspaceId
    ? `AND p.workspace_id = ${sqlUuid(workspaceId)}`
    : "";
  const sql = `
WITH prompt AS (
  SELECT id, name, organization_id, workspace_id, deleted
  FROM model_hub_prompttemplate p
  WHERE p.id = ${sqlUuid(promptId)}
    AND p.organization_id = ${sqlUuid(organizationId)}
    ${workspaceFilter}
),
config AS (
  SELECT
    id,
    name,
    prompt_template_id,
    eval_template_id,
    mapping,
    config,
    status,
    error_localizer,
    deleted,
    deleted_at
  FROM model_hub_promptevalconfig
  WHERE id = ${sqlUuid(evalConfigId)}
),
eval_template AS (
  SELECT id, name, owner, config
  FROM model_hub_evaltemplate
  WHERE id = ${sqlUuid(evalTemplateId)}
)
SELECT json_build_object(
  'prompt_id', p.id::text,
  'prompt_name', p.name,
  'prompt_organization_id', p.organization_id::text,
  'prompt_workspace_id', p.workspace_id::text,
  'prompt_deleted', p.deleted,
  'config_id', c.id::text,
  'config_name', c.name,
  'config_prompt_template_id', c.prompt_template_id::text,
  'eval_template_id', c.eval_template_id::text,
  'eval_template_name', et.name,
  'eval_template_owner', et.owner,
  'mapping', c.mapping,
  'config', c.config,
  'status', c.status,
  'error_localizer', c.error_localizer,
  'deleted', c.deleted,
  'deleted_at_set', c.deleted_at IS NOT NULL
)
FROM prompt p
LEFT JOIN config c ON c.prompt_template_id = p.id
LEFT JOIN eval_template et ON et.id = c.eval_template_id;
`;
  return runPostgresJson(sql);
}

function assertPromptEvalConfigDbAudit(
  audit,
  {
    promptId,
    evalConfigId,
    evalTemplateId,
    organizationId,
    workspaceId,
    mapping,
    expectedDeleted,
  },
) {
  assert(
    audit?.prompt_id === promptId,
    "Prompt eval DB audit prompt id mismatch.",
  );
  assert(
    audit?.config_id === evalConfigId,
    "Prompt eval DB audit config id mismatch.",
  );
  assert(
    audit?.config_prompt_template_id === promptId,
    "Prompt eval DB audit config prompt id mismatch.",
  );
  assert(
    audit?.eval_template_id === evalTemplateId,
    "Prompt eval DB audit eval template id mismatch.",
  );
  assert(
    audit.prompt_organization_id === organizationId,
    "Prompt eval DB audit organization mismatch.",
  );
  if (workspaceId) {
    assert(
      audit.prompt_workspace_id === workspaceId,
      "Prompt eval DB audit workspace mismatch.",
    );
  }
  assert(
    audit.deleted === expectedDeleted,
    "Prompt eval DB audit deleted state mismatch.",
  );
  if (expectedDeleted) {
    assert(
      audit.deleted_at_set === true,
      "Deleted prompt eval config missing deleted_at.",
    );
  }
  assertPromptEvalMapping(audit.mapping, mapping);
}

async function hardDeletePromptWorkbenchFixtureDb({
  promptId,
  evalConfigId,
  organizationId,
  workspaceId,
}) {
  if (!promptId && !evalConfigId) {
    return {
      remaining_prompt_count: 0,
      remaining_version_count: 0,
      remaining_eval_config_count: 0,
    };
  }
  const promptPredicate = promptId ? `id = ${sqlUuid(promptId)}` : "false";
  const evalConfigPredicate = evalConfigId
    ? `id = ${sqlUuid(evalConfigId)}`
    : "false";
  const sql = `
BEGIN;
CREATE TEMP TABLE _pwb_prompt_ids(id uuid) ON COMMIT DROP;
INSERT INTO _pwb_prompt_ids
SELECT id
FROM model_hub_prompttemplate
WHERE organization_id = ${sqlUuid(organizationId)}
  AND workspace_id = ${sqlUuid(workspaceId)}
  AND (${promptPredicate});

CREATE TEMP TABLE _pwb_eval_config_ids(id uuid) ON COMMIT DROP;
INSERT INTO _pwb_eval_config_ids
SELECT id
FROM model_hub_promptevalconfig
WHERE ${evalConfigPredicate}
   OR prompt_template_id IN (SELECT id FROM _pwb_prompt_ids);

CREATE TEMP TABLE _pwb_version_ids(id uuid) ON COMMIT DROP;
INSERT INTO _pwb_version_ids
SELECT id
FROM model_hub_promptversion
WHERE original_template_id IN (SELECT id FROM _pwb_prompt_ids);

DELETE FROM model_hub_promptevalconfig
WHERE id IN (SELECT id FROM _pwb_eval_config_ids);
DELETE FROM model_hub_promptversion_labels
WHERE promptversion_id IN (SELECT id FROM _pwb_version_ids);
DELETE FROM model_hub_prompttemplate_collaborators
WHERE prompttemplate_id IN (SELECT id FROM _pwb_prompt_ids);
DELETE FROM model_hub_promptversion
WHERE id IN (SELECT id FROM _pwb_version_ids);
DELETE FROM model_hub_prompttemplate
WHERE id IN (SELECT id FROM _pwb_prompt_ids);

SELECT json_build_object(
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

function assertPromptEvalMapping(actual, expected) {
  for (const [key, value] of Object.entries(expected)) {
    assert(
      actual?.[key] === value,
      `Prompt eval config mapping did not preserve ${key}.`,
    );
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

async function waitForPath(page, pathname, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname === expectedPath,
    { timeout },
    pathname,
  );
}

async function waitForSelector(page, selector, timeout = 30000) {
  await page.waitForSelector(selector, { visible: true, timeout });
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

async function clickByTestId(page, testId, { preferDom = false } = {}) {
  const selector = `[data-testid="${testId}"]`;
  await waitForSelector(page, selector);
  await page.waitForFunction(
    (sel) => {
      const element = document.querySelector(sel);
      const button = element?.closest("button") || element;
      return Boolean(
        element &&
          button &&
          !button.disabled &&
          button.getAttribute("aria-disabled") !== "true",
      );
    },
    { timeout: 30000 },
    selector,
  );
  await page.evaluate((sel) => {
    const element = document.querySelector(sel);
    element?.scrollIntoView({ block: "center", inline: "center" });
  }, selector);
  if (preferDom) {
    await dispatchClickBySelector(page, selector, testId);
    return;
  }
  try {
    await page.click(selector);
  } catch {
    await dispatchClickBySelector(page, selector, testId);
  }
}

async function dispatchClickBySelector(page, selector, label) {
  const clicked = await page.evaluate((sel) => {
    const element = document.querySelector(sel);
    const clickable =
      element?.closest("button,a,[role='button'],[role='menuitem']") || element;
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
  }, selector);
  assert(clicked, `Could not click selector: ${label}`);
}

async function typeSearchInput(page, placeholder, value) {
  const selector = `input[placeholder="${placeholder}"]`;
  await waitForSelector(page, selector);
  await page.click(selector);
  await selectAll(page);
  await page.keyboard.press("Backspace");
  await page.type(selector, value);
}

async function setTextFieldByTestId(page, testId, value) {
  const selector = `[data-testid="${testId}"] input`;
  await waitForSelector(page, selector);
  await page.evaluate(
    (sel, nextValue) => {
      const input = document.querySelector(sel);
      if (!input) return;
      input.focus();
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      setter?.call(input, nextValue);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      input.blur();
    },
    selector,
    value,
  );
  await page.waitForFunction(
    ({ sel, expected }) => document.querySelector(sel)?.value === expected,
    { timeout: 30000 },
    { sel: selector, expected: value },
  );
}

async function ensureMappingValue(page, variable, value) {
  const selector = `[data-testid="eval-mapping-input-${variable}"] input`;
  await waitForSelector(page, selector);
  const current = await page.$eval(selector, (input) => input.value);
  if (current === value) return;
  await page.click(selector);
  await selectAll(page);
  await page.keyboard.press("Backspace");
  await page.type(selector, value);
  await page.keyboard.press("Enter");
  await page.waitForFunction(
    ({ sel, expected }) => document.querySelector(sel)?.value === expected,
    { timeout: 30000 },
    { sel: selector, expected: value },
  );
}

async function fillIfVisibleByLabel(page, label, value) {
  const selector = await page.evaluate((expectedLabel) => {
    const visible = window.visibleElements();
    const labelElement = visible.find(
      (element) => window.normalizeText(element.textContent) === expectedLabel,
    );
    const inputId = labelElement?.getAttribute("for");
    if (inputId && document.getElementById(inputId)) {
      return `#${window.CSS.escape(inputId)}`;
    }
    const root = labelElement?.closest(".MuiFormControl-root");
    if (root?.querySelector("input")) {
      const input = root.querySelector("input");
      if (!input.id) input.id = `api-journey-${expectedLabel}`;
      return `#${window.CSS.escape(input.id)}`;
    }
    return null;
  }, label);
  if (!selector) return false;
  await page.evaluate(
    (sel, nextValue) => {
      const input = document.querySelector(sel);
      if (!input) return;
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      setter?.call(input, nextValue);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    },
    selector,
    value,
  );
  return true;
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

function promptRunOutputs(payload) {
  const output = unwrapResult(payload)?.executions_result?.output;
  if (Array.isArray(output)) {
    return output.filter((item) => item !== null && item !== undefined);
  }
  if (output !== null && output !== undefined) return [output];
  return [];
}

function previewText(value) {
  return normalizeOutputText(value).slice(0, 120);
}

function visibleTextSnippet(value) {
  const normalized = normalizeOutputText(value);
  const snippet = normalized.slice(0, Math.min(60, normalized.length));
  assert(
    snippet.length > 0,
    "Workbench prompt setup output was empty after normalization.",
  );
  return snippet;
}

function normalizeOutputText(value) {
  return (typeof value === "string" ? value : JSON.stringify(value))
    .replace(/\s+/g, " ")
    .trim();
}

function payloadArray(payload, key) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.[key])) return payload[key];
  if (Array.isArray(payload?.result?.[key])) return payload.result[key];
  if (Array.isArray(payload?.data?.[key])) return payload.data[key];
  if (Array.isArray(payload?.results?.[key])) return payload.results[key];
  return asArray(payload);
}

function safeJson(value) {
  if (!value) return null;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function isPromptEvalMutation(url, method) {
  if (!["POST", "DELETE"].includes(method)) return false;
  return (
    url.includes("/update-evaluation-configs/") ||
    url.includes("/run-evals-on-multiple-versions/") ||
    url.includes("/delete-evaluation-config/")
  );
}

function isPromptWorkbenchApiUrl(url) {
  return (
    url.includes("/model-hub/prompt-") ||
    url.includes("/model-hub/eval/") ||
    url.includes("/model-hub/eval-templates/")
  );
}

function redactUrl(url) {
  try {
    const parsed = new URL(url);
    return `${parsed.pathname}${parsed.search}`;
  } catch {
    return url;
  }
}

function sqlUuid(value) {
  assert(isUuid(value), `Expected UUID, got ${value}`);
  return `'${String(value).replaceAll("'", "''")}'::uuid`;
}

function appendCleanupError(caughtError, cleanupError) {
  if (!caughtError) return cleanupError;
  caughtError.message = `${caughtError.message}; cleanup failed: ${cleanupError.message}`;
  return caughtError;
}

async function selectAll(page) {
  const modifier = process.platform === "darwin" ? "Meta" : "Control";
  await page.keyboard.down(modifier);
  await page.keyboard.press("A");
  await page.keyboard.up(modifier);
}

function shortRunId(runId) {
  return String(runId || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .slice(-8);
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
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
