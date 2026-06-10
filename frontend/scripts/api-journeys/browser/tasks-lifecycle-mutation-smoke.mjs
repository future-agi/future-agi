import { execFile as execFileCallback } from "node:child_process";
import { randomUUID } from "node:crypto";
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
const SCREENSHOT_PATH = "/tmp/tasks-lifecycle-mutation-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/tasks-lifecycle-mutation-smoke-failure.png";

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const seed = await resolveTaskSeed(auth.client, {
    workspaceId: auth.workspaceId,
    runId: auth.runId,
  });
  const taskName = `api journey browser task lifecycle ${auth.runId}`;
  const draftId = randomUUID();
  const pageErrors = [];
  const consoleIssues = [];
  const taskApiFailures = [];
  const taskApiRequests = [];
  const evidence = {};
  let caughtError = null;
  let cleanupError = null;
  let createdTaskId = null;

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();

  try {
    await preparePage(page, auth, { seed, taskName, draftId });
    page.on("request", (request) => {
      const url = request.url();
      if (isTaskRelevantApiUrl(url)) {
        taskApiRequests.push(`${request.method()} ${url}`);
        if (taskApiRequests.length > 80) taskApiRequests.shift();
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isTaskRelevantApiUrl(url) && response.status() >= 400) {
        taskApiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("console", (message) => {
      if (["error", "warning"].includes(message.type())) {
        consoleIssues.push(`${message.type()}: ${message.text()}`);
        if (consoleIssues.length > 40) consoleIssues.shift();
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponseDuring(
      page,
      "Tasks list load",
      (response) =>
        response
          .url()
          .includes("/tracer/eval-task/list_eval_tasks_with_project_name/") &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/tasks`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await page.waitForFunction(
      () => window.location.pathname === "/dashboard/tasks",
      { timeout: 30000 },
    );
    await expectVisibleText(page, "Tasks", { exact: true });
    await expectVisibleText(page, "Create Task", { exact: true });

    await clickButtonWithText(page, "Create Task");
    await page.waitForFunction(
      () => window.location.pathname === "/dashboard/tasks/create",
      { timeout: 30000 },
    );

    await waitForResponseDuring(
      page,
      "Task create draft preview",
      (response) =>
        isTaskPreviewListUrl(response.url()) && response.status() < 400,
      () =>
        page.goto(
          `${APP_BASE}/dashboard/tasks/create?project=${seed.project.id}&draft=${draftId}`,
          { waitUntil: "domcontentloaded" },
        ),
    );
    await expectVisibleText(page, taskName);
    await expectVisibleText(page, seed.evalConfig.name);
    await expectVisibleText(page, "Run evaluations on", { exact: true });
    await expectVisibleText(page, "Live Preview");
    await expectNoVisibleText(page, "Invalid Date");

    const createResponse = await waitForResponseDuring(
      page,
      "Task create submit",
      (response) =>
        response.request().method() === "POST" &&
        isEvalTaskCreateUrl(response.url()) &&
        response.status() < 400,
      () => clickButtonWithText(page, "Create Task"),
    );
    const createBody = await createResponse.json();
    createdTaskId = createBody?.result?.id || createBody?.id;
    assert(
      isUuid(createdTaskId),
      `Create Task did not return a task id: ${JSON.stringify(createBody)}`,
    );
    await page.waitForFunction(
      (taskId) =>
        window.location.pathname.endsWith(`/dashboard/tasks/${taskId}`),
      { timeout: 30000 },
      createdTaskId,
    );
    await expectVisibleText(page, taskName);
    await expectVisibleText(page, "Details", { exact: true });
    await expectVisibleText(page, seed.evalConfig.name);
    await expectNoVisibleText(page, "Invalid Date");

    const createdDetail = await auth.client.get(
      apiPath("/tracer/eval-task/get_eval_details/"),
      { query: { eval_id: createdTaskId } },
    );
    assert(createdDetail?.name === taskName, "Created task name mismatch.");
    assert(
      createdDetail?.project_id === seed.project.id,
      "Created task project mismatch.",
    );
    assert(
      Number(createdDetail?.sampling_rate) === 100,
      "Created task sampling rate mismatch.",
    );
    assert(
      asArray(createdDetail?.evals_applied).some(
        (evalItem) => evalItem?.id === seed.evalConfig.id,
      ),
      "Created task detail omitted seeded eval config.",
    );
    const createdEval = asArray(createdDetail?.evals_applied).find(
      (evalItem) => evalItem?.id === seed.evalConfig.id,
    );
    const evalTemplateId = resolveEvalTemplateId(
      createdEval || seed.evalConfig,
    );
    assert(
      isUuid(evalTemplateId),
      `Created task detail omitted eval template id: ${JSON.stringify(
        createdEval || seed.evalConfig,
      )}`,
    );

    await expectVisibleText(page, `Open evaluation ${seed.evalConfig.name}`);
    const evalDetailResponse = await waitForResponseDuring(
      page,
      "Task detail eval clickthrough",
      (response) =>
        response.request().method() === "GET" &&
        response
          .url()
          .includes(`/model-hub/eval-templates/${evalTemplateId}/detail/`) &&
        response.status() < 400,
      () =>
        clickButtonByAriaLabel(page, `Open evaluation ${seed.evalConfig.name}`),
    );
    const evalDetailBody = await evalDetailResponse.json();
    const evalDetailName =
      evalDetailBody?.result?.name ||
      evalDetailBody?.name ||
      seed.evalConfig.templateName ||
      seed.evalConfig.name;
    await page.waitForFunction(
      (id) => window.location.pathname === `/dashboard/evaluations/${id}`,
      { timeout: 30000 },
      evalTemplateId,
    );
    await expectVisibleText(page, evalDetailName);
    await expectVisibleText(page, "Eval Details", { exact: true });
    await expectNoVisibleText(page, "Invalid Date");
    await waitForResponseDuring(
      page,
      "Task detail reload after eval clickthrough",
      (response) =>
        response.url().includes("/tracer/eval-task/get_eval_details/") &&
        response.url().includes(`eval_id=${createdTaskId}`) &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/tasks/${createdTaskId}`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await expectVisibleText(page, taskName);
    await expectVisibleText(page, seed.evalConfig.name);

    await clickButtonWithText(page, "25%");
    const updateResponse = await waitForResponseDuring(
      page,
      "Task detail update submit",
      (response) =>
        response.request().method() === "PATCH" &&
        response.url().includes("/tracer/eval-task/update_eval_task/"),
      async () => {
        await clickButtonWithText(page, "Save");
        await expectVisibleText(page, "Update Task", { exact: true });
        await clickDialogButtonWithText(page, "Run task");
      },
    );
    const updateBody = await updateResponse.json();
    assert(
      updateResponse.status() < 400,
      `Task update failed with HTTP ${updateResponse.status()}: ${JSON.stringify(
        updateBody,
      )}`,
    );
    assert(
      (updateBody?.result?.task_id || updateBody?.task_id) === createdTaskId,
      `Task update response id mismatch: ${JSON.stringify(updateBody)}`,
    );
    const updatedDetail = await auth.client.get(
      apiPath("/tracer/eval-task/get_eval_details/"),
      { query: { eval_id: createdTaskId } },
    );
    assert(
      Number(updatedDetail?.sampling_rate) === 25,
      `Task update did not persist sampling rate 25: ${JSON.stringify({
        sampling_rate: updatedDetail?.sampling_rate,
      })}`,
    );

    await waitForResponseDuring(
      page,
      "Task list after mutation",
      (response) =>
        response
          .url()
          .includes("/tracer/eval-task/list_eval_tasks_with_project_name/") &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/tasks`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForResponseDuring(
      page,
      "Task search after mutation",
      (response) => {
        if (
          !response
            .url()
            .includes("/tracer/eval-task/list_eval_tasks_with_project_name/") ||
          response.status() >= 400
        ) {
          return false;
        }
        const url = new URL(response.url());
        return url.searchParams.get("name") === taskName;
      },
      () => typeSearch(page, taskName),
    );
    await expectVisibleText(page, taskName);
    await selectTaskRow(page, taskName);
    await expectVisibleText(page, "1 selected");
    await clickButtonWithText(page, "Delete");
    await expectVisibleText(page, "Delete Task", { exact: true });
    await waitForResponseDuring(
      page,
      "Task list delete submit",
      (response) =>
        response.request().method() === "POST" &&
        response.url().includes("/tracer/eval-task/mark_eval_tasks_deleted/") &&
        response.status() < 400,
      () => clickDialogButtonWithText(page, "Delete"),
    );

    const publicDeleteAudit = await loadEvalTaskLifecycleAudit({
      taskId: createdTaskId,
    });
    assert(
      publicDeleteAudit.task_deleted === true,
      `Public delete did not mark the task deleted: ${JSON.stringify(
        publicDeleteAudit,
      )}`,
    );

    assert(
      taskApiFailures.length === 0,
      `Task API failures: ${taskApiFailures.join("; ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    Object.assign(evidence, {
      seed_source: seed.source,
      seed_task_id: seed.task?.id || null,
      seed_project_id: seed.project.id,
      seed_project_name: seed.project.name,
      seed_eval_config_id: seed.evalConfig.id,
      seed_eval_template_id: evalTemplateId,
      seed_eval_template_name: evalDetailName,
      created_task_id: createdTaskId,
      task_name: taskName,
      row_type: seed.rowType,
      created_sampling_rate: createdDetail.sampling_rate,
      updated_sampling_rate: updatedDetail.sampling_rate,
      eval_clickthrough_path: `/dashboard/evaluations/${evalTemplateId}`,
      public_deleted: publicDeleteAudit.task_deleted,
      screenshot: SCREENSHOT_PATH,
    });
  } catch (error) {
    caughtError = error;
    const clickLog = await page
      .evaluate(() => window.__apiJourneyClicks || [])
      .catch(() => []);
    caughtError.message = appendFailureDiagnostics(caughtError.message, {
      taskApiRequests,
      taskApiFailures,
      pageErrors,
      consoleIssues,
      clickLog,
    });
    await page
      .screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
  } finally {
    await browser.close();
    if (createdTaskId) {
      try {
        const cleanupAudit = await deleteEvalTaskDbArtifacts({
          taskId: createdTaskId,
        });
        assert(
          Number(cleanupAudit.remaining_task_count) === 0,
          `Task lifecycle cleanup left task residue: ${JSON.stringify(
            cleanupAudit,
          )}`,
        );
        evidence.cleanup = cleanupAudit;
      } catch (error) {
        cleanupError = error;
      }
    }
    if (seed.createdEvalConfigId) {
      try {
        const seedCleanupAudit = await deleteCustomEvalConfigDbArtifacts({
          evalConfigId: seed.createdEvalConfigId,
        });
        assert(
          Number(seedCleanupAudit.remaining_custom_eval_config_count) === 0,
          `Task lifecycle seed cleanup left custom eval config residue: ${JSON.stringify(
            seedCleanupAudit,
          )}`,
        );
        evidence.seed_cleanup = seedCleanupAudit;
      } catch (error) {
        cleanupError = cleanupError || error;
      }
    }
    if (seed.createdEvalTemplateId) {
      try {
        await auth.client.post(
          apiPath("/model-hub/eval-templates/bulk-delete/"),
          { template_ids: [seed.createdEvalTemplateId] },
          { okStatuses: [200, 404] },
        );
        evidence.seed_eval_template_cleanup = {
          template_id: seed.createdEvalTemplateId,
          status: "deleted",
        };
      } catch (error) {
        cleanupError = cleanupError || error;
      }
    }
  }

  if (caughtError || cleanupError) {
    if (caughtError && cleanupError) {
      caughtError.message = `${caughtError.message}; cleanup failed: ${cleanupError.message}`;
      throw caughtError;
    }
    throw caughtError || cleanupError;
  }

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
}

async function resolveTaskSeed(client, { workspaceId, runId }) {
  const list = await client.get(
    apiPath("/tracer/eval-task/list_eval_tasks_with_project_name/"),
    {
      query: {
        page_number: 0,
        page_size: 50,
        sort_params: JSON.stringify([
          { column_id: "created_at", direction: "desc" },
        ]),
      },
    },
  );
  const rows = asArray(list.table || list).filter(
    (row) =>
      row?.id && isUuid(row.project_id || row.filters_applied?.project_id),
  );
  for (const task of rows) {
    const projectId = task.project_id || task.filters_applied?.project_id;
    const project = await loadProjectIfCurrentWorkspace(client, {
      id: projectId,
      name: task.project_name,
      workspaceId,
    });
    if (!project) continue;

    const detail = await client.get(
      apiPath("/tracer/eval-task/get_eval_details/"),
      {
        query: { eval_id: task.id },
      },
    );
    const evalConfig = asArray(detail?.evals_applied).find((item) =>
      isUuid(item?.id),
    );
    if (!evalConfig) continue;
    return {
      source: "existing_task",
      task,
      project,
      evalConfig,
      rowType: normalizeRowType(detail?.row_type),
    };
  }
  return createDisposableTaskSeed(client, { workspaceId, runId });
}

async function createDisposableTaskSeed(client, { workspaceId, runId }) {
  const project = await resolveCurrentWorkspaceObserveProject(
    client,
    workspaceId,
  );
  const evalTemplate = await resolveTaskEvalTemplateForConfig(client, runId);
  const name = `api journey task eval ${shortRunId(runId)}`;
  const created = await client.post(apiPath("/tracer/custom-eval-config/"), {
    eval_template: evalTemplate.id,
    name,
    config: { params: evalTemplate.params },
    mapping: evalTemplate.mapping,
    project: project.id,
    filters: {},
    error_localizer: false,
  });
  const evalConfigId = created?.id;
  assert(
    isUuid(evalConfigId),
    `Custom eval config seed did not return a UUID id: ${JSON.stringify(
      created,
    )}`,
  );

  return {
    source: `generated_${evalTemplate.source}`,
    task: null,
    project,
    evalConfig: {
      id: evalConfigId,
      name,
      template_id: evalTemplate.id,
      templateId: evalTemplate.id,
      templateName: evalTemplate.name,
      mapping: evalTemplate.mapping,
      config: { params: evalTemplate.expectedParams },
    },
    rowType: "spans",
    createdEvalConfigId: evalConfigId,
    createdEvalTemplateId:
      evalTemplate.source === "created" ? evalTemplate.id : null,
  };
}

async function resolveCurrentWorkspaceObserveProject(client, workspaceId) {
  const list = await client.get(apiPath("/tracer/project/list_projects/"), {
    query: { project_type: "observe", page_number: 0, page_size: 100 },
  });
  const projects = asArray(list).filter((row) => row?.id && isUuid(row.id));
  for (const row of projects) {
    const project = await loadProjectIfCurrentWorkspace(client, {
      id: row.id,
      name: row.name,
      workspaceId,
    });
    if (project) return project;
  }
  throw new Error("No current-workspace observe project seed was found.");
}

async function resolveTaskEvalTemplateForConfig(client, runId) {
  const systemTemplate = await findEvalTemplateDetailByName(
    client,
    "word_count_in_range",
  );
  if (systemTemplate) {
    const requiredKeys = evalTemplateRequiredKeys(systemTemplate);
    const paramsConfig = evalTemplateParamsForConfig(systemTemplate, {
      min_words: "1",
      max_words: "20",
      k: "3",
    });
    return {
      id: systemTemplate.id,
      name: systemTemplate.name,
      source: "system",
      requiredKeys,
      mapping: buildEvalConfigMapping(requiredKeys),
      params: paramsConfig.submitted,
      expectedParams: paramsConfig.expected,
    };
  }

  const name = `api_journey_task_eval_${shortRunId(runId)}`;
  const created = await client.post(
    apiPath("/model-hub/eval-templates/create-v2/"),
    {
      name,
      eval_type: "code",
      code: [
        "def evaluate(output=None, expected=None, **kwargs):",
        "    return True",
      ].join("\n"),
      code_language: "python",
      output_type: "pass_fail",
      pass_threshold: 0.5,
      description: "Temporary task lifecycle browser smoke eval template.",
      tags: ["api-journey", "tasks-lifecycle"],
    },
  );
  assert(
    isUuid(created?.id),
    `Fallback eval template seed did not return a UUID id: ${JSON.stringify(
      created,
    )}`,
  );
  const detail = await client.get(
    apiPath("/model-hub/eval-templates/{template_id}/detail/", {
      template_id: created.id,
    }),
  );
  const requiredKeys = evalTemplateRequiredKeys(detail);
  return {
    id: created.id,
    name: detail?.name || name,
    source: "created",
    requiredKeys,
    mapping: buildEvalConfigMapping(requiredKeys),
    params: {},
    expectedParams: {},
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
  const row = asArray(payload?.items).find(
    (item) => item?.name === name && isUuid(item?.id),
  );
  if (!row) return null;
  return client.get(
    apiPath("/model-hub/eval-templates/{template_id}/detail/", {
      template_id: row.id,
    }),
  );
}

function evalTemplateRequiredKeys(detail) {
  return asArray(
    detail?.required_keys ||
      detail?.eval_required_keys ||
      detail?.config?.required_keys,
  ).filter((key) => typeof key === "string" && key.length > 0);
}

function evalTemplateParamsForConfig(detail, values = {}) {
  const schema = detail?.config?.function_params_schema || {};
  const submitted = {};
  const expected = {};
  for (const [key, definition] of Object.entries(schema)) {
    const fallback = definition?.default;
    const rawValue = Object.prototype.hasOwnProperty.call(values, key)
      ? values[key]
      : fallback;
    if (rawValue === undefined || rawValue === null) continue;
    submitted[key] = rawValue;
    expected[key] =
      definition?.type === "integer" || definition?.type === "number"
        ? Number(rawValue)
        : rawValue;
  }
  return { submitted, expected };
}

function buildEvalConfigMapping(requiredKeys) {
  const fallbackByKey = {
    actual_json: "input",
    expected: "expected",
    expected_json: "expected",
    expected_output: "expected",
    ground_truth: "expected",
    hypothesis: "input",
    input: "input",
    output: "output",
    query: "input",
    reference: "expected",
    response: "output",
    text: "input",
  };
  const mapping = {};
  for (const key of requiredKeys) {
    mapping[key] = fallbackByKey[key] || "input";
  }
  return mapping;
}

function appendFailureDiagnostics(
  message,
  { taskApiRequests, taskApiFailures, pageErrors, consoleIssues, clickLog },
) {
  const parts = [message];
  if (taskApiFailures.length > 0) {
    parts.push(`api failures: ${taskApiFailures.slice(-10).join("; ")}`);
  }
  if (pageErrors.length > 0) {
    parts.push(`page errors: ${pageErrors.slice(-5).join("; ")}`);
  }
  if (consoleIssues.length > 0) {
    parts.push(`console issues: ${consoleIssues.slice(-8).join("; ")}`);
  }
  if (clickLog.length > 0) {
    parts.push(
      `recent clicks: ${clickLog
        .slice(-12)
        .map(
          (entry) =>
            `${entry.text || entry.ariaLabel || "(blank)"}@${entry.path}${
              entry.disabled ? "[disabled]" : ""
            }`,
        )
        .join("; ")}`,
    );
  }
  if (taskApiRequests.length > 0) {
    parts.push(
      `recent task API requests: ${taskApiRequests.slice(-20).join("; ")}`,
    );
  }
  return parts.join(" | ");
}

async function loadProjectIfCurrentWorkspace(
  client,
  { id, name, workspaceId },
) {
  const detail = await client.get(apiPath("/tracer/project/{id}/", { id }));
  if (
    workspaceId &&
    detail?.workspace &&
    String(detail.workspace) !== String(workspaceId)
  ) {
    return null;
  }
  if (detail?.trace_type && detail.trace_type !== "observe") return null;
  if (detail?.source === "simulator") return null;
  return { id, name: detail?.name || name || id };
}

async function preparePage(page, auth, { seed, taskName, draftId }) {
  const startDate = formatDateForInput(new Date(Date.now() - 30 * 864e5));
  const endDate = formatDateForInput(new Date());
  await page.setBypassServiceWorker(true);
  await installRuntimeConfig(page, auth);
  await page.evaluateOnNewDocument(() => {
    window.__apiJourneyClicks = [];
    document.addEventListener(
      "click",
      (event) => {
        const button = event.target.closest?.("button,[role='button'],a");
        if (!button) return;
        window.__apiJourneyClicks.push({
          text: window.__apiJourneyNormalizeText(button.textContent),
          ariaLabel: button.getAttribute("aria-label") || "",
          disabled: Boolean(button.disabled),
          path: window.location.pathname,
        });
        if (window.__apiJourneyClicks.length > 40) {
          window.__apiJourneyClicks.shift();
        }
      },
      true,
    );
    window.__apiJourneyNormalizeText = (value) => String(value || "").trim();
    window.__apiJourneyElementText = (element) => {
      const values = [element.textContent];
      if ("value" in element) values.push(element.value);
      values.push(element.getAttribute?.("aria-label"));
      return values
        .map((value) => window.__apiJourneyNormalizeText(value))
        .filter(Boolean)
        .join(" ");
    };
    window.__apiJourneyVisibleElements = () => {
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
      return Array.from(document.querySelectorAll("body *")).filter(isVisible);
    };
  });
  await page.evaluateOnNewDocument(
    ({
      tokens,
      organizationId,
      workspaceId,
      user,
      draftId,
      seed,
      taskName,
      startDate,
      endDate,
    }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      if (organizationId)
        sessionStorage.setItem("organizationId", organizationId);
      if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
      if (user?.id)
        sessionStorage.setItem("futureagi-current-user-id", user.id);
      localStorage.setItem(
        `task-draft-${draftId}`,
        JSON.stringify({
          savedAt: Date.now(),
          values: {
            name: taskName,
            project: seed.project.id,
            rowType: seed.rowType,
            filters: [],
            spansLimit: 100000,
            samplingRate: 100,
            evalsDetails: [seed.evalConfig],
            startDate,
            endDate,
            runType: "continuous",
          },
        }),
      );
    },
    {
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      user: auth.user,
      draftId,
      seed,
      taskName,
      startDate,
      endDate,
    },
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

async function expectVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      return window.__apiJourneyVisibleElements().some((element) => {
        const textContent = window.__apiJourneyElementText(element);
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

async function expectNoVisibleText(page, text, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (expectedText) => {
      return !window
        .__apiJourneyVisibleElements()
        .some((element) =>
          window.__apiJourneyElementText(element).includes(expectedText),
        );
    },
    { timeout },
    text,
  );
}

async function clickButtonWithText(page, text) {
  await clickScopedButtonWithText(page, text, "body");
}

async function clickDialogButtonWithText(page, text) {
  await clickScopedButtonWithText(page, text, '[role="dialog"]');
}

async function clickButtonByAriaLabel(page, label) {
  await page.waitForFunction(
    (expectedLabel) =>
      window.__apiJourneyVisibleElements().some((element) => {
        const button = element.closest("button,[role='button'],a");
        return button?.getAttribute("aria-label") === expectedLabel;
      }),
    { timeout: 30000 },
    label,
  );
  const box = await page.evaluate((expectedLabel) => {
    const element = window.__apiJourneyVisibleElements().find((candidate) => {
      const button = candidate.closest("button,[role='button'],a");
      return button?.getAttribute("aria-label") === expectedLabel;
    });
    const button = element?.closest("button,[role='button'],a");
    if (!button) return null;
    button.scrollIntoView({ block: "center", inline: "center" });
    const rect = button.getBoundingClientRect();
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
  }, label);
  assert(box, `Could not click button with aria-label ${label}.`);
  await page.mouse.click(box.x, box.y);
}

async function clickScopedButtonWithText(page, text, scopeSelector) {
  await page.waitForFunction(
    ({ expectedText, scopeSelector }) => {
      const scope = document.querySelector(scopeSelector);
      if (!scope) return false;
      return window.__apiJourneyVisibleElements().some((element) => {
        if (!scope.contains(element)) return false;
        const button = element.closest("button,[role='button'],a");
        return (
          button &&
          scope.contains(button) &&
          window.__apiJourneyNormalizeText(button.textContent) === expectedText
        );
      });
    },
    { timeout: 30000 },
    { expectedText: text, scopeSelector },
  );
  const box = await page.evaluate(
    ({ expectedText, scopeSelector }) => {
      const scope = document.querySelector(scopeSelector);
      const element = window.__apiJourneyVisibleElements().find((candidate) => {
        if (!scope?.contains(candidate)) return false;
        const button = candidate.closest("button,[role='button'],a");
        return (
          button &&
          scope.contains(button) &&
          window.__apiJourneyNormalizeText(button.textContent) === expectedText
        );
      });
      const button = element?.closest("button,[role='button'],a");
      if (!button) return null;
      button.scrollIntoView({ block: "center", inline: "center" });
      const rect = button.getBoundingClientRect();
      return {
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
      };
    },
    { expectedText: text, scopeSelector },
  );
  assert(box, `Could not click button ${text}.`);
  await page.mouse.click(box.x, box.y);
}

async function typeSearch(page, text) {
  await page.waitForSelector('input[placeholder="Search tasks..."]', {
    timeout: 30000,
  });
  await page.click('input[placeholder="Search tasks..."]', { clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.type('input[placeholder="Search tasks..."]', text, { delay: 1 });
}

async function selectTaskRow(page, taskName) {
  await page.waitForFunction(
    (name) =>
      Array.from(document.querySelectorAll('[role="row"]')).some((row) =>
        window.__apiJourneyNormalizeText(row.textContent).includes(name),
      ),
    { timeout: 30000 },
    taskName,
  );
  const selected = await page.evaluate((name) => {
    const row = Array.from(document.querySelectorAll('[role="row"]')).find(
      (candidate) =>
        window.__apiJourneyNormalizeText(candidate.textContent).includes(name),
    );
    const checkbox =
      row?.querySelector('input[type="checkbox"]') ||
      row?.querySelector('[role="checkbox"]');
    if (!checkbox) return false;
    checkbox.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    checkbox.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    checkbox.click();
    return true;
  }, taskName);
  assert(selected, `Could not select task row ${taskName}.`);
}

async function loadEvalTaskLifecycleAudit({ taskId }) {
  const sql = `
WITH requested AS (
  SELECT ${sqlUuid(taskId)} AS task_id
),
task_row AS (
  SELECT *
  FROM tracer_eval_task task
  JOIN requested r ON task.id = r.task_id
)
SELECT json_build_object(
  'task_exists', EXISTS (SELECT 1 FROM task_row),
  'task_deleted', (SELECT deleted FROM task_row),
  'task_status', (SELECT status FROM task_row),
  'task_name', (SELECT name FROM task_row),
  'sampling_rate', (SELECT sampling_rate FROM task_row)
);
`;
  return runPostgresJson(sql);
}

async function deleteEvalTaskDbArtifacts({ taskId }) {
  const sql = `
WITH requested AS (
  SELECT ${sqlUuid(taskId)} AS task_id
),
deleted_task_evals AS (
  DELETE FROM tracer_eval_task_evals
  WHERE evaltask_id = (SELECT task_id FROM requested)
  RETURNING evaltask_id
),
deleted_task_loggers AS (
  DELETE FROM tracer_eval_task_logger
  WHERE eval_task_id = (SELECT task_id FROM requested)
  RETURNING id
),
deleted_eval_logs AS (
  DELETE FROM tracer_eval_logger
  WHERE eval_task_id = (SELECT task_id::text FROM requested)
  RETURNING id
),
deleted_tasks AS (
  DELETE FROM tracer_eval_task
  WHERE id = (SELECT task_id FROM requested)
  RETURNING id
)
SELECT json_build_object(
  'deleted_task_eval_rows', (SELECT count(*) FROM deleted_task_evals),
  'deleted_task_logger_rows', (SELECT count(*) FROM deleted_task_loggers),
  'deleted_eval_log_rows', (SELECT count(*) FROM deleted_eval_logs),
  'deleted_task_rows', (SELECT count(*) FROM deleted_tasks)
);
`;
  const cleanup = await runPostgresJson(sql);
  const residueSql = `
WITH requested AS (
  SELECT ${sqlUuid(taskId)} AS task_id
)
SELECT json_build_object(
  'remaining_task_count', (
    SELECT count(*) FROM tracer_eval_task
    WHERE id = (SELECT task_id FROM requested)
  ),
  'remaining_task_logger_count', (
    SELECT count(*) FROM tracer_eval_task_logger
    WHERE eval_task_id = (SELECT task_id FROM requested)
  ),
  'remaining_eval_log_count', (
    SELECT count(*) FROM tracer_eval_logger
    WHERE eval_task_id = (SELECT task_id::text FROM requested)
  )
);
`;
  const residue = await runPostgresJson(residueSql);
  return { ...cleanup, ...residue };
}

async function deleteCustomEvalConfigDbArtifacts({ evalConfigId }) {
  const sql = `
WITH requested AS (
  SELECT ${sqlUuid(evalConfigId)} AS eval_config_id
),
deleted_custom_eval_configs AS (
  DELETE FROM tracer_custom_eval_config
  WHERE id = (SELECT eval_config_id FROM requested)
  RETURNING id
)
SELECT json_build_object(
  'deleted_custom_eval_config_rows', (
    SELECT count(*) FROM deleted_custom_eval_configs
  )
);
`;
  const cleanup = await runPostgresJson(sql);
  const residueSql = `
WITH requested AS (
  SELECT ${sqlUuid(evalConfigId)} AS eval_config_id
)
SELECT json_build_object(
  'remaining_custom_eval_config_count', (
    SELECT count(*)
    FROM tracer_custom_eval_config
    WHERE id = (SELECT eval_config_id FROM requested)
  )
);
`;
  const residue = await runPostgresJson(residueSql);
  return { ...cleanup, ...residue };
}

async function runPostgresJson(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
  const user = process.env.API_JOURNEY_DB_USER || "user";
  const database = process.env.API_JOURNEY_DB_NAME || "tfc";
  const { stdout } = await execFile(
    "docker",
    ["exec", container, "psql", "-U", user, "-d", database, "-At", "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const text = stdout.trim();
  assert(text, "Postgres DB audit returned no JSON output.");
  return JSON.parse(text);
}

function isTaskRelevantApiUrl(url) {
  return (
    url.includes("/tracer/eval-task/") ||
    url.includes("/model-hub/eval-templates/") ||
    url.includes("/tracer/project/") ||
    url.includes("/tracer/trace/") ||
    url.includes("/tracer/observation-span/")
  );
}

function isEvalTaskCreateUrl(url) {
  const parsed = new URL(url);
  return parsed.pathname === "/tracer/eval-task/";
}

function isTaskPreviewListUrl(url) {
  return (
    url.includes("/tracer/trace/list_traces_of_session/") ||
    url.includes("/tracer/observation-span/list_spans_observe/") ||
    url.includes("/tracer/trace-session/list_sessions/")
  );
}

function normalizeRowType(value) {
  return ["spans", "traces", "sessions"].includes(value) ? value : "spans";
}

function resolveEvalTemplateId(evalItem) {
  return (
    evalItem?.templateId ||
    evalItem?.template_id ||
    evalItem?.eval_template ||
    evalItem?.evalTemplate?.id
  );
}

function shortRunId(runId) {
  return String(runId || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .slice(-8);
}

function formatDateForInput(date) {
  return date.toISOString().slice(0, 10);
}

function sqlUuid(value) {
  assert(isUuid(value), `Expected UUID, got ${value}`);
  return `'${String(value).replaceAll("'", "''")}'::uuid`;
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  }
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  if (process.platform === "linux") return "/usr/bin/google-chrome";
  return undefined;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
