import { createRequire } from "node:module";
import { execFile as execFileCallback } from "node:child_process";
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
import { queryWithFilters } from "../lib/fixtures.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFile = promisify(execFileCallback);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/observe-add-evals-task-draft-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/observe-add-evals-task-draft-smoke-failure.png";

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const sample = await resolveTraceSample(auth.client, auth.workspaceId);
  const taskName = `api journey browser linked trace task ${auth.runId}`;
  const traceListRequests = [];
  const apiFailures = [];
  const pageErrors = [];
  const evidence = {};
  let cleanupAudit = null;
  let caughtError = null;
  let cleanupError = null;
  let createdTaskId = null;
  let requestPhase = "observe";

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();

  try {
    await preparePage(page, auth, sample, { taskName });
    page.on("request", (request) => {
      const url = request.url();
      if (isTraceListUrl(url)) {
        traceListRequests.push({
          url,
          method: request.method(),
          referer: request.headers().referer || "",
          phase: requestPhase,
        });
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isRelevantApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponseDuring(
      page,
      "Observe trace list with seeded trace filter",
      (response) =>
        isTraceListUrl(response.url()) &&
        response.status() < 400 &&
        hasTraceFilter(response.url(), sample.traceId),
      () =>
        page.goto(
          `${APP_BASE}/dashboard/observe/${sample.project.id}/llm-tracing?selectedTab=trace`,
          { waitUntil: "domcontentloaded" },
        ),
    );
    await page.waitForFunction(
      (projectId) =>
        window.location.pathname ===
        `/dashboard/observe/${projectId}/llm-tracing`,
      { timeout: 30000 },
      sample.project.id,
    );
    await expectVisibleText(page, "Add Evals", { exact: true });

    requestPhase = "task-create";
    await clickButtonWithText(page, "Add Evals");
    await page.waitForFunction(
      () => window.location.pathname === "/dashboard/tasks/create",
      { timeout: 30000 },
    );
    await expectVisibleText(page, "Create Task");
    await expectVisibleText(page, "Task Name");
    await expectVisibleText(page, "Run evaluations on", { exact: true });
    await expectVisibleText(page, sample.traceId.slice(0, 8));
    await page.waitForFunction(
      () => document.body.textContent.includes("Live Preview"),
      { timeout: 30000 },
    );
    await expectNoVisibleText(page, "Invalid Date");

    const draftEvidence = await readTaskDraft(page);
    assert(
      draftEvidence.project === sample.project.id,
      `Task draft project mismatch: ${JSON.stringify(draftEvidence)}`,
    );
    assert(
      draftEvidence.rowType === "traces",
      `Task draft rowType mismatch: ${JSON.stringify(draftEvidence)}`,
    );
    assert(
      draftEvidence.returnTo?.includes(
        `/dashboard/observe/${sample.project.id}/llm-tracing`,
      ),
      `Task draft returnTo missing Observe path: ${JSON.stringify(draftEvidence)}`,
    );
    assert(
      draftEvidence.traceFilter?.filterConfig?.filterValue === sample.traceId,
      `Task draft did not preserve trace_id filter: ${JSON.stringify(
        draftEvidence,
      )}`,
    );

    const previewRequest = traceListRequests.find(
      (request) =>
        request.phase === "task-create" &&
        hasTraceFilter(request.url, sample.traceId),
    );
    assert(
      previewRequest,
      "Task create page did not request trace preview with linked trace_id filter.",
    );

    const createResponse = await waitForResponseDuring(
      page,
      "linked task browser create",
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
    await expectVisibleText(page, "Open source", { exact: true });
    await expectNoVisibleText(page, "Invalid Date");

    const createdDetail = await auth.client.get(
      apiPath("/tracer/eval-task/get_eval_details/"),
      {
        query: { eval_id: createdTaskId },
      },
    );
    assert(
      createdDetail?.id === createdTaskId,
      "Created linked task detail id mismatch.",
    );
    assert(
      createdDetail?.name === taskName,
      "Created linked task detail name mismatch.",
    );
    assert(
      asArray(createdDetail?.filters_applied?.trace_id).includes(
        sample.traceId,
      ),
      "Created linked task detail did not preserve trace_id filter.",
    );
    assert(
      asArray(createdDetail?.evals_applied).some(
        (evalItem) => evalItem?.id === sample.seedEval.id,
      ),
      "Created linked task detail did not include the seeded eval config.",
    );

    await waitForResponseDuring(
      page,
      "task detail Open source trace load",
      (response) =>
        isTraceDetailUrl(response.url(), sample.traceId) &&
        response.status() < 400,
      () => clickButtonWithText(page, "Open source"),
    );
    await page.waitForFunction(
      (projectId, traceId) =>
        window.location.pathname ===
        `/dashboard/observe/${projectId}/trace/${traceId}`,
      { timeout: 30000 },
      sample.project.id,
      sample.traceId,
    );
    await expectVisibleText(page, "Trace ID");
    await expectVisibleText(page, sample.traceId);
    await expectNoVisibleText(page, "Invalid Date");

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    Object.assign(evidence, {
      project_id: sample.project.id,
      project_name: sample.project.name || null,
      trace_id: sample.traceId,
      span_id: sample.spanId,
      seed_task_id: sample.seedTask?.id || null,
      seed_eval_config_id: sample.seedEval.id,
      created_task_id: createdTaskId,
      task_name: taskName,
      draft_id: draftEvidence.draftId,
      draft_row_type: draftEvidence.rowType,
      draft_trace_filter_value:
        draftEvidence.traceFilter?.filterConfig?.filterValue,
      task_preview_trace_request: previewRequest.url,
      detail_source_path: `/dashboard/observe/${sample.project.id}/trace/${sample.traceId}`,
      screenshot: SCREENSHOT_PATH,
    });
  } catch (error) {
    caughtError = error;
    await page
      .screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
  } finally {
    await browser.close();
    if (createdTaskId) {
      try {
        await auth.client.post(
          apiPath("/tracer/eval-task/mark_eval_tasks_deleted/"),
          {
            eval_task_ids: [createdTaskId],
          },
        );
        cleanupAudit = await deleteEvalTaskDbArtifacts({
          taskId: createdTaskId,
        });
        assert(
          Number(cleanupAudit.remaining_task_count) === 0,
          `Linked task cleanup left task residue: ${JSON.stringify(cleanupAudit)}`,
        );
        evidence.cleanup = cleanupAudit;
      } catch (error) {
        cleanupError = error;
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

async function resolveTraceSample(client, workspaceId) {
  const preferredProjectId = process.env.OBSERVE_PROJECT_ID;
  const preferredTraceId = process.env.OBSERVE_TRACE_ID;
  const preferredEvalConfigId = process.env.OBSERVE_EVAL_CONFIG_ID;

  if (preferredProjectId && preferredTraceId && preferredEvalConfigId) {
    const project = await loadProjectIfCurrentWorkspace(
      client,
      { id: preferredProjectId, name: "env observe project" },
      workspaceId,
      true,
    );
    assert(
      project,
      "OBSERVE_PROJECT_ID did not resolve to an observe project.",
    );
    return sampleFromTraceDetail(client, {
      project,
      traceId: preferredTraceId,
      seedEval: { id: preferredEvalConfigId, name: "env eval config" },
    });
  }

  const seeds = await loadEvalTaskSeeds(client, preferredProjectId);
  for (const seed of seeds) {
    const projectId = seed.project_id || seed.filters_applied?.project_id;
    if (!isUuid(seed?.id) || !isUuid(projectId)) continue;
    const seedDetail = await client.get(
      apiPath("/tracer/eval-task/get_eval_details/"),
      {
        query: { eval_id: seed.id },
      },
    );
    const seedEval = asArray(seedDetail?.evals_applied)[0];
    if (!seedEval?.id) continue;

    const project = await loadProjectIfCurrentWorkspace(
      client,
      { id: projectId, name: seed.project_name },
      workspaceId,
      Boolean(preferredProjectId),
    );
    if (!project) continue;

    const traceList = await client.get(
      queryWithFilters(apiPath("/tracer/trace/list_traces_of_session/"), [], {
        project_id: project.id,
        page_number: 0,
        page_size: 25,
      }),
    );
    for (const trace of asArray(traceList).filter(
      (row) => row?.trace_id || row?.id,
    )) {
      const traceId = trace.trace_id || trace.id;
      if (preferredTraceId && traceId !== preferredTraceId) continue;
      try {
        return await sampleFromTraceDetail(client, {
          project,
          traceId,
          seedTask: seed,
          seedEval,
        });
      } catch {
        // Some legacy rows are missing span detail; keep looking for a full trace.
      }
    }
  }

  throw new Error(
    "No current-workspace observe trace with span detail and eval config seed was found.",
  );
}

async function loadEvalTaskSeeds(client, preferredProjectId) {
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
  return asArray(list.table || list).filter((row) => {
    const projectId = row?.project_id || row?.filters_applied?.project_id;
    if (!row?.id || !isUuid(projectId)) return false;
    return !preferredProjectId || projectId === preferredProjectId;
  });
}

async function loadProjectIfCurrentWorkspace(
  client,
  projectRow,
  workspaceId,
  allowEnvOverride,
) {
  const detail = await client.get(
    apiPath("/tracer/project/{id}/", { id: projectRow.id }),
  );
  if (
    !allowEnvOverride &&
    workspaceId &&
    detail?.workspace &&
    String(detail.workspace) !== String(workspaceId)
  ) {
    return null;
  }
  if (detail?.trace_type && detail.trace_type !== "observe") return null;
  if (detail?.source === "simulator") return null;
  return { id: projectRow.id, name: detail?.name || projectRow.name };
}

async function sampleFromTraceDetail(
  client,
  { project, traceId, seedTask, seedEval },
) {
  const detail = await client.get(
    apiPath("/tracer/trace/{id}/", { id: traceId }),
  );
  const entries = asArray(detail?.observation_spans);
  const flatEntries = entries.flatMap((entry) => flattenTraceEntries(entry));
  const selected =
    flatEntries.find((row) => !row.span?.parent_span_id) || flatEntries[0];
  assert(
    selected?.span?.id,
    `Trace ${traceId} did not include a visible observation span.`,
  );
  return {
    project,
    traceId,
    spanId: selected.span.id,
    spanCount: flatEntries.length,
    seedTask,
    seedEval,
  };
}

function flattenTraceEntries(rootEntry) {
  const rows = [];
  function walk(entry) {
    if (!entry || typeof entry !== "object") return;
    const span = entry.observation_span || entry.span || entry;
    if (span?.id) rows.push({ entry, span });
    for (const child of asArray(entry.children)) walk(child);
  }
  walk(rootEntry);
  return rows;
}

async function preparePage(page, auth, sample, { taskName }) {
  await page.setBypassServiceWorker(true);
  await installRuntimeConfig(page, auth);
  await page.evaluateOnNewDocument(() => {
    window.__apiJourneyNormalizeText = (value) => String(value || "").trim();
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
      projectId,
      traceId,
      seedEval,
      taskName,
    }) => {
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
      localStorage.setItem(
        `observe-filters-${projectId}`,
        JSON.stringify({
          tabType: "traces",
          filters: [
            {
              id: `api-journey-trace-${traceId}`,
              column_id: "trace_id",
              display_name: "Trace ID",
              filter_config: {
                filter_type: "text",
                filter_op: "equals",
                filter_value: traceId,
              },
            },
          ],
          extra_filters: [],
        }),
      );
    },
    {
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      user: auth.user,
      projectId: sample.project.id,
      traceId: sample.traceId,
      seedEval: sample.seedEval,
      taskName,
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

async function expectVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      return window.__apiJourneyVisibleElements().some((element) => {
        const textContent = window.__apiJourneyNormalizeText(
          element.textContent,
        );
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
          window
            .__apiJourneyNormalizeText(element.textContent)
            .includes(expectedText),
        );
    },
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
  const clicked = await page.evaluate((expectedText) => {
    const element = window.__apiJourneyVisibleElements().find((candidate) => {
      const button = candidate.closest("button,[role='button'],a");
      return (
        button &&
        window.__apiJourneyNormalizeText(button.textContent) === expectedText
      );
    });
    const button = element?.closest("button,[role='button'],a");
    if (!button) return false;
    button.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return true;
  }, text);
  assert(clicked, `Could not click button ${text}.`);
}

function isRelevantApiUrl(url) {
  return (
    url.includes("/tracer/trace/list_traces_of_session/") ||
    url.includes("/tracer/observation-span/get_eval_attributes_list/") ||
    url.includes("/tracer/project/")
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
  if (parsed.searchParams.get("project_id")) {
    const filters = parseFilters(parsed.searchParams.get("filters"));
    return filters.some(
      (filter) =>
        filter?.column_id === "trace_id" &&
        filterValueMatches(filter?.filter_config?.filter_value, traceId),
    );
  }
  return false;
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
  assert(text, "Postgres DB cleanup returned no JSON output.");
  return JSON.parse(text);
}

function sqlUuid(value) {
  assert(isUuid(value), `Expected UUID, got ${value}`);
  return `'${String(value).replaceAll("'", "''")}'::uuid`;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
