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
import {
  browserExecutablePath,
  installRuntimeConfig,
  prepareErrorFeedRow,
} from "./error-feed-smoke.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFile = promisify(execFileCallback);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/error-feed-add-evals-task-draft-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/error-feed-add-evals-task-draft-smoke-failure.png";

async function main() {
  requireMutations();
  assert(
    envFlag("ERROR_FEED_FORCE_FIXTURE"),
    "Set ERROR_FEED_FORCE_FIXTURE=1 so this mutation smoke only touches a disposable Error Feed fixture.",
  );

  const auth = await createAuthenticatedContext();
  const fixtureEvidence = [];
  const cleanupEvidence = [];
  const prepared = await prepareErrorFeedRow({
    client: auth.client,
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
    runId: auth.runId,
    evidence: fixtureEvidence,
  });
  const row = prepared.row;
  const clusterId = row.cluster_id;
  const projectId = row.project_id;
  const traceId = prepared.seededFixture?.trace_id || row.trace_id;
  assert(
    isUuid(projectId),
    `Error Feed row missing project_id: ${JSON.stringify(row)}`,
  );
  assert(
    isUuid(traceId),
    `Error Feed row missing trace_id: ${JSON.stringify(row)}`,
  );

  const seedEval = await createDisposableEvalConfig(auth.client, {
    projectId,
    runId: auth.runId,
  });
  const taskName = `api journey error feed linked task ${auth.runId}`;
  const apiFailures = [];
  const pageErrors = [];
  const traceListRequests = [];
  const evidence = {};
  let browser = null;
  let caughtError = null;
  let cleanupError = null;
  let createdTaskId = null;
  let requestPhase = "error-feed";
  let result = null;

  try {
    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 980 },
      args: ["--no-sandbox"],
    });
    const page = await browser.newPage();
    await preparePage(page, auth, { seedEval, taskName });

    page.on("request", (request) => {
      const url = request.url();
      if (isTraceListUrl(url)) {
        traceListRequests.push({
          url,
          method: request.method(),
          phase: requestPhase,
        });
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isRelevantApiUrl(url) && response.status() >= 400) {
        apiFailures.push(
          `${response.status()} ${response.request().method()} ${url}`,
        );
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponseDuring(
      page,
      "Error Feed linked-task detail load",
      (response) =>
        response.request().method() === "GET" &&
        isIssueDetailUrl(response.url(), clusterId) &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/error-feed/${clusterId}`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, `/dashboard/error-feed/${clusterId}`);
    await expectVisibleText(page, row.error.name);
    await expectVisibleText(page, "Add Evals", { exact: true });
    await expectNoVisibleText(page, "Invalid Date");

    requestPhase = "task-create";
    await clickButtonWithText(page, "Add Evals");
    await waitForPath(page, "/dashboard/tasks/create");
    await expectVisibleText(page, "Create Task", { exact: true });
    await expectVisibleText(page, taskName);
    await expectVisibleText(page, seedEval.name);
    await expectVisibleText(page, "Run evaluations on", { exact: true });
    await expectVisibleText(page, traceId.slice(0, 8));
    await expectVisibleText(page, "Live Preview");
    await expectNoVisibleText(page, "Invalid Date");

    const draftEvidence = await readTaskDraft(page);
    assert(
      draftEvidence.project === projectId,
      `Task draft project mismatch: ${JSON.stringify(draftEvidence)}`,
    );
    assert(
      draftEvidence.rowType === "traces",
      `Task draft rowType mismatch: ${JSON.stringify(draftEvidence)}`,
    );
    assert(
      draftEvidence.returnTo === `/dashboard/error-feed/${clusterId}`,
      `Task draft returnTo mismatch: ${JSON.stringify(draftEvidence)}`,
    );
    assert(
      draftEvidence.traceFilter?.filterConfig?.filterValue === traceId,
      `Task draft did not preserve trace_id filter: ${JSON.stringify(
        draftEvidence,
      )}`,
    );
    assert(
      draftEvidence.evalConfigIds.includes(seedEval.id),
      `Task draft omitted seeded eval config: ${JSON.stringify(draftEvidence)}`,
    );

    const previewRequest = traceListRequests.find(
      (request) =>
        request.phase === "task-create" && hasTraceFilter(request.url, traceId),
    );
    assert(
      previewRequest,
      "Task create page did not request trace preview with Error Feed trace_id filter.",
    );

    const createResponse = await waitForResponseDuring(
      page,
      "Error Feed linked task create",
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
    await waitForPath(page, `/dashboard/tasks/${createdTaskId}`);
    await expectVisibleText(page, taskName);
    await expectVisibleText(page, "Open source", { exact: true });
    await expectNoVisibleText(page, "Invalid Date");

    const createdDetail = await auth.client.get(
      apiPath("/tracer/eval-task/get_eval_details/"),
      { query: { eval_id: createdTaskId } },
    );
    assert(createdDetail?.id === createdTaskId, "Created task id mismatch.");
    assert(createdDetail?.name === taskName, "Created task name mismatch.");
    assert(
      createdDetail?.project_id === projectId,
      "Created task project mismatch.",
    );
    assert(
      asArray(createdDetail?.filters_applied?.trace_id).includes(traceId),
      "Created task detail did not preserve Error Feed trace_id filter.",
    );
    assert(
      asArray(createdDetail?.evals_applied).some(
        (evalItem) => evalItem?.id === seedEval.id,
      ),
      "Created task detail did not include the seeded eval config.",
    );

    await waitForResponseDuring(
      page,
      "Error Feed linked task Open source trace load",
      (response) =>
        response.request().method() === "GET" &&
        isTraceDetailUrl(response.url(), traceId) &&
        response.status() < 400,
      () => clickButtonWithText(page, "Open source"),
    );
    await waitForPath(page, `/dashboard/observe/${projectId}/trace/${traceId}`);
    await expectVisibleText(page, "Trace ID");
    await expectVisibleText(page, traceId);
    await expectNoVisibleText(page, "Invalid Date");

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    Object.assign(evidence, {
      cluster_id: clusterId,
      project_id: projectId,
      trace_id: traceId,
      seed_eval_config_id: seedEval.id,
      seed_eval_template_id: seedEval.templateId,
      task_name: taskName,
      created_task_id: createdTaskId,
      draft_id: draftEvidence.draftId,
      draft_trace_filter_value:
        draftEvidence.traceFilter?.filterConfig?.filterValue,
      task_preview_trace_request: previewRequest.url,
      detail_source_path: `/dashboard/observe/${projectId}/trace/${traceId}`,
      screenshot: SCREENSHOT_PATH,
      fixture: fixtureEvidence,
      cleanup: cleanupEvidence,
    });

    result = {
      status: "passed",
      app_base: APP_BASE,
      api_base: auth.apiBase,
      organization_id: auth.organizationId,
      workspace_id: auth.workspaceId,
      evidence,
    };
  } catch (error) {
    caughtError = error;
    if (browser) {
      const pages = await browser.pages().catch(() => []);
      await pages[0]
        ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .catch(() => null);
    }
  } finally {
    if (browser) await browser.close();
    if (createdTaskId) {
      try {
        await auth.client.post(
          apiPath("/tracer/eval-task/mark_eval_tasks_deleted/"),
          { eval_task_ids: [createdTaskId] },
          { okStatuses: [200, 404] },
        );
        const cleanupAudit = await deleteEvalTaskDbArtifacts({
          taskId: createdTaskId,
        });
        assert(
          Number(cleanupAudit.remaining_task_count) === 0,
          `Error Feed linked task cleanup left task residue: ${JSON.stringify(
            cleanupAudit,
          )}`,
        );
        evidence.task_cleanup = cleanupAudit;
      } catch (error) {
        cleanupError = cleanupError || error;
      }
    }
    try {
      const seedCleanupAudit = await deleteCustomEvalConfigDbArtifacts({
        evalConfigId: seedEval.id,
      });
      assert(
        Number(seedCleanupAudit.remaining_custom_eval_config_count) === 0,
        `Error Feed linked task seed cleanup left custom eval config residue: ${JSON.stringify(
          seedCleanupAudit,
        )}`,
      );
      evidence.seed_cleanup = seedCleanupAudit;
    } catch (error) {
      cleanupError = cleanupError || error;
    }
    if (seedEval.createdEvalTemplateId) {
      try {
        await auth.client.post(
          apiPath("/model-hub/eval-templates/bulk-delete/"),
          { template_ids: [seedEval.createdEvalTemplateId] },
          { okStatuses: [200, 404] },
        );
        evidence.seed_eval_template_cleanup = {
          template_id: seedEval.createdEvalTemplateId,
          status: "deleted",
        };
      } catch (error) {
        cleanupError = cleanupError || error;
      }
    }
    const cleanupFailures = await prepared.cleanup.run(cleanupEvidence);
    if (cleanupFailures.length > 0) {
      cleanupError =
        cleanupError ||
        new Error(
          `Error Feed linked task fixture cleanup failed: ${JSON.stringify(
            cleanupFailures,
          )}`,
        );
    }
  }

  if (caughtError || cleanupError) {
    if (caughtError && cleanupError) {
      caughtError.message = `${caughtError.message}; cleanup failed: ${cleanupError.message}`;
      throw caughtError;
    }
    throw caughtError || cleanupError;
  }

  console.log(JSON.stringify(result, null, 2));
}

async function createDisposableEvalConfig(client, { projectId, runId }) {
  const evalTemplate = await resolveTaskEvalTemplateForConfig(client, runId);
  const name = `api journey error feed eval ${shortRunId(runId)}`;
  const created = await client.post(apiPath("/tracer/custom-eval-config/"), {
    eval_template: evalTemplate.id,
    name,
    config: { params: evalTemplate.params },
    mapping: evalTemplate.mapping,
    project: projectId,
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
    id: evalConfigId,
    name,
    template_id: evalTemplate.id,
    templateId: evalTemplate.id,
    templateName: evalTemplate.name,
    mapping: evalTemplate.mapping,
    config: { params: evalTemplate.expectedParams },
    createdEvalTemplateId:
      evalTemplate.source === "created" ? evalTemplate.id : null,
  };
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

  const name = `api_journey_error_feed_eval_${shortRunId(runId)}`;
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
      description:
        "Temporary Error Feed linked-task browser smoke eval template.",
      tags: ["api-journey", "error-feed"],
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

async function preparePage(page, auth, { seedEval, taskName }) {
  await page.setBypassServiceWorker(true);
  await installRuntimeConfig(page, auth);
  await page.evaluateOnNewDocument(() => {
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
    ({ tokens, organizationId, workspaceId, user, seedEval, taskName }) => {
      const originalSetItem = Storage.prototype.setItem;
      Storage.prototype.setItem = function patchedSetItem(key, value) {
        if (
          this === localStorage &&
          typeof key === "string" &&
          key.startsWith("task-draft-")
        ) {
          try {
            const parsed = JSON.parse(value);
            const values = parsed?.values || {};
            value = JSON.stringify({
              ...parsed,
              values: {
                ...values,
                name: taskName,
                samplingRate: 100,
                evalsDetails: [seedEval],
              },
            });
          } catch {
            // Keep the original value if the app changes the draft shape.
          }
        }
        return originalSetItem.call(this, key, value);
      };
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
      seedEval,
      taskName,
    },
  );
}

async function readTaskDraft(page) {
  const url = new URL(page.url());
  const draftId = url.searchParams.get("draft");
  assert(draftId, `Task create URL did not include a draft id: ${page.url()}`);
  return page.evaluate((id) => {
    const raw = localStorage.getItem(`task-draft-${id}`);
    const parsed = raw ? JSON.parse(raw) : null;
    const values = parsed?.values || {};
    const traceFilter = (values.filters || []).find(
      (filter) =>
        filter?.property === "trace_id" || filter?.propertyId === "trace_id",
    );
    return {
      draftId: id,
      project: values.project,
      rowType: values.rowType,
      name: values.name,
      evalConfigIds: (values.evalsDetails || []).map((item) => item?.id),
      returnTo: new URL(window.location.href).searchParams.get("returnTo"),
      traceFilter,
    };
  }, draftId);
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

async function waitForPath(page, expectedPath) {
  await page.waitForFunction(
    (pathName) => window.location.pathname === pathName,
    { timeout: 30000 },
    expectedPath,
  );
}

async function expectVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ expectedText, exactMatch }) =>
      window.__apiJourneyVisibleElements().some((element) => {
        const textContent = window.__apiJourneyElementText(element);
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      }),
    { timeout },
    { expectedText: text, exactMatch: exact },
  );
}

async function expectNoVisibleText(page, text, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (expectedText) =>
      !window
        .__apiJourneyVisibleElements()
        .some((element) =>
          window.__apiJourneyElementText(element).includes(expectedText),
        ),
    { timeout },
    text,
  );
}

async function clickButtonWithText(page, text) {
  await page.waitForFunction(
    (expectedText) =>
      window.__apiJourneyVisibleElements().some((element) => {
        const button = element.closest("button,[role='button'],a");
        return (
          button &&
          window.__apiJourneyNormalizeText(button.textContent) === expectedText
        );
      }),
    { timeout: 30000 },
    text,
  );
  const box = await page.evaluate((expectedText) => {
    const element = window.__apiJourneyVisibleElements().find((candidate) => {
      const button = candidate.closest("button,[role='button'],a");
      return (
        button &&
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
  }, text);
  assert(box, `Could not click button ${text}.`);
  await page.mouse.click(box.x, box.y);
}

function isRelevantApiUrl(url) {
  return (
    url.includes("/tracer/feed/") ||
    url.includes("/tracer/eval-task/") ||
    url.includes("/model-hub/eval-templates/") ||
    url.includes("/tracer/project/") ||
    url.includes("/tracer/trace/") ||
    url.includes("/tracer/observation-span/")
  );
}

function isIssueDetailUrl(url, clusterId) {
  return stripQuery(url).endsWith(
    `/tracer/feed/issues/${encodeURIComponent(clusterId)}/`,
  );
}

function isTraceListUrl(url) {
  return url.includes("/tracer/trace/list_traces_of_session/");
}

function isEvalTaskCreateUrl(url) {
  const parsed = new URL(url);
  return parsed.pathname === "/tracer/eval-task/";
}

function isTraceDetailUrl(url, traceId) {
  const parsed = new URL(url);
  return parsed.pathname === `/tracer/trace/${traceId}/`;
}

function hasTraceFilter(url, traceId) {
  const parsed = new URL(url);
  if (!parsed.searchParams.get("project_id")) return false;
  const filters = parseFilters(parsed.searchParams.get("filters"));
  return filters.some(
    (filter) =>
      filter?.column_id === "trace_id" &&
      filterValueMatches(filter?.filter_config?.filter_value, traceId),
  );
}

function parseFilters(raw) {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function filterValueMatches(value, expected) {
  if (Array.isArray(value)) return value.map(String).includes(String(expected));
  return String(value) === String(expected);
}

function stripQuery(url) {
  return String(url || "").split("?")[0];
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

function sqlUuid(value) {
  assert(isUuid(value), `Expected UUID, got ${value}`);
  return `'${String(value).replaceAll("'", "''")}'::uuid`;
}

function shortRunId(runId) {
  return String(runId || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .slice(-8);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
