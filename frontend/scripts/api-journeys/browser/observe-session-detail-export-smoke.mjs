/* eslint-disable no-console */
import { Buffer } from "node:buffer";
import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const DETAIL_SCREENSHOT_PATH = "/tmp/observe-session-detail-smoke.png";
const EVALS_SCREENSHOT_PATH = "/tmp/observe-session-evals-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/observe-session-detail-export-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const fixture = await createSessionDetailFixture(auth);
  const downloadDir = path.join(
    "/tmp",
    `observe-session-downloads-${fixture.safeSuffix}`,
  );
  let cleanupDone = false;
  let browser = null;
  let page = null;
  const apiFailures = [];
  const pageErrors = [];
  const sessionRequests = [];
  const evidence = {
    project_id: fixture.projectId,
    project_name: fixture.projectName,
    project_version_id: fixture.projectVersionId,
    session_id: fixture.sessionId,
    session_name: fixture.sessionName,
    trace_id: fixture.traceId,
    span_id: fixture.spanId,
    eval_log_id: fixture.evalLogId,
    eval_config_name: fixture.evalConfigName,
    seed_audit: fixture.seedAudit,
    download_dir: downloadDir,
  };

  await fs.rm(downloadDir, { recursive: true, force: true });
  await fs.mkdir(downloadDir, { recursive: true });

  try {
    evidence.api_preflight = await preflightSessionApis(auth, fixture);

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 980 },
      args: ["--no-sandbox"],
    });
    page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await allowDownloads(page, downloadDir);
    await installRuntimeConfig(page, auth);
    await installBrowserState(page, auth);

    page.on("request", (request) => {
      if (!isObserveSessionApiUrl(request.url())) return;
      sessionRequests.push(`${request.method()} ${request.url()}`);
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isObserveApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    const listResponse = await waitForResponseDuring(
      page,
      "initial observe session list",
      sessionListResponse(fixture.projectId),
      () =>
        page.goto(
          `${APP_BASE}/dashboard/observe/${fixture.projectId}/sessions`,
          { waitUntil: "domcontentloaded" },
        ),
    );
    const listBody = await readResponseJson(listResponse);
    evidence.browser_list_count = asArray(
      listBody?.result?.table || listBody?.table || listBody,
    ).length;

    await waitForPath(page, `/dashboard/observe/${fixture.projectId}/sessions`);
    await waitForVisibleText(page, fixture.projectName);
    await waitForSessionGridRow(page, fixture.sessionName);

    const detailResponse = await waitForResponseDuring(
      page,
      "open observe session drawer",
      sessionDetailResponse(fixture.sessionId),
      () => clickSessionGridRow(page, fixture.sessionName),
    );
    const detailBody = await readResponseJson(detailResponse);
    const drawerRows = asArray(detailBody?.result?.response);
    assert(
      drawerRows.some((row) => row.trace_id === fixture.traceId),
      "Browser drawer detail response omitted the disposable trace.",
    );

    await waitForVisibleText(page, "Session", { exact: true });
    await waitForVisibleText(page, fixture.sessionId);
    await waitForVisibleText(page, "Session History", { exact: true });
    await waitForVisibleText(page, "Evals", { exact: true });
    await waitForVisibleText(page, "Human", { exact: true });
    await waitForVisibleText(page, "AI", { exact: true });
    await waitForVisibleText(page, "browser smoke input");
    await waitForVisibleText(page, "browser smoke output");
    await page.screenshot({ path: DETAIL_SCREENSHOT_PATH, fullPage: true });
    evidence.detail_screenshot = DETAIL_SCREENSHOT_PATH;

    const evalLogsResponse = await waitForResponseDuring(
      page,
      "observe session eval logs",
      sessionEvalLogsResponse(fixture.sessionId),
      () => clickTab(page, "Evals"),
    );
    const evalLogsBody = await readResponseJson(evalLogsResponse);
    const evalItems = asArray(evalLogsBody?.result?.items);
    assert(
      evalItems.some((item) => item.id === fixture.evalLogId),
      "Browser eval-log response omitted the seeded session eval.",
    );
    await waitForVisibleText(page, fixture.evalConfigName, { exact: true });
    await waitForVisibleText(page, "1.00", { exact: true });
    await waitForVisibleText(page, "Passed", { exact: true });
    await waitForVisibleText(page, fixture.evalReason);
    await page.screenshot({ path: EVALS_SCREENSHOT_PATH, fullPage: true });
    evidence.evals_screenshot = EVALS_SCREENSHOT_PATH;

    await page.keyboard.press("Escape");
    await waitForNoVisibleText(page, fixture.evalReason);

    const exportResponse = await waitForResponseDuring(
      page,
      "observe session CSV export",
      sessionExportResponse(fixture.projectId),
      () => clickExportCsvButton(page),
    );
    assert(
      exportResponse.status() === 200,
      `Session CSV export returned HTTP ${exportResponse.status()}.`,
    );
    const csvPath = await waitForDownloadedFile(downloadDir, ".csv");
    const csvText = await fs.readFile(csvPath, "utf8");
    assertSessionCsv(csvText, fixture);
    evidence.download = {
      csv: csvPath,
      bytes: Buffer.byteLength(csvText),
    };

    const cleanupAudit = await cleanupSessionDetailFixture(fixture);
    cleanupDone = true;
    evidence.cleanup = cleanupAudit;
    assertCleanedSessionFixture(cleanupAudit);

    assert(
      apiFailures.length === 0,
      `Observe session API failures: ${apiFailures.join("; ")}`,
    );
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
          observe_session_request_count: sessionRequests.length,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await page
      ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
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
          observe_session_requests: sessionRequests,
          failure_screenshot: FAILURE_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    if (browser) await browser.close();
    if (!cleanupDone) {
      await cleanupSessionDetailFixture(fixture).catch((error) => {
        console.error(`Cleanup failed: ${error.message}`);
      });
    }
  }
}

async function createSessionDetailFixture(auth) {
  const safeSuffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const projectName = `TH-4812 observe sessions ${safeSuffix}`;
  const sessionName = `TH-4812 session detail ${safeSuffix}`;
  const traceName = `TH-4812 trace detail ${safeSuffix}`;
  const spanId = `th4812_session_span_${safeSuffix}`;
  const evalConfigName = `TH-4812 session eval ${safeSuffix}`;
  const evalReason = `TH-4812 session eval passed ${safeSuffix}`;

  const createdProject = await auth.client.post(apiPath("/tracer/project/"), {
    name: projectName,
    model_type: "GenerativeLLM",
    trace_type: "observe",
    metadata: { source: "browser-smoke", run_id: safeSuffix },
  });
  const projectId = createdProject.project_id || createdProject.id;
  assert(isUuid(projectId), "Session detail project create returned no id.");

  const projectVersion = await auth.client.post(
    apiPath("/tracer/project-version/"),
    {
      project: projectId,
      name: `TH-4812 observe run ${safeSuffix}`,
      metadata: { source: "browser-smoke", run_id: safeSuffix },
    },
  );
  const projectVersionId =
    projectVersion.project_version_id || projectVersion.id;
  assert(
    isUuid(projectVersionId),
    "Session detail project-version create returned no id.",
  );

  const createdSession = await auth.client.post(
    apiPath("/tracer/trace-session/"),
    {
      project: projectId,
      name: sessionName,
      bookmarked: false,
    },
  );
  const sessionId = createdSession.id || createdSession.trace_session_id;
  assert(
    isUuid(sessionId),
    "Session detail trace-session create returned no id.",
  );

  const trace = await auth.client.post(
    apiPath("/tracer/trace/"),
    traceWritePayload({
      projectId,
      projectVersionId,
      sessionId,
      name: traceName,
      runId: safeSuffix,
      marker: "target",
    }),
  );
  const traceId = trace.id || trace.trace_id || trace.trace?.id;
  assert(isUuid(traceId), "Session detail trace create returned no id.");

  const now = Date.now();
  const spanPayload = observationSpanWritePayload({
    id: spanId,
    projectId,
    projectVersionId,
    traceId,
    name: `TH-4812 span detail ${safeSuffix}`,
    runId: safeSuffix,
    startTime: new Date(now - 750).toISOString(),
    endTime: new Date(now).toISOString(),
    metadata: {
      source: "browser-smoke",
      run_id: safeSuffix,
      session_id: sessionId,
      marker: "session-detail",
    },
  });
  spanPayload.latency_ms = 750;
  spanPayload.prompt_tokens = 11;
  spanPayload.completion_tokens = 13;
  spanPayload.total_tokens = 24;
  spanPayload.cost = 0.024;
  spanPayload.span_attributes = {
    th_4812_marker: `session-detail-${safeSuffix}`,
  };

  const bulkSpanResult = await auth.client.post(
    apiPath("/tracer/observation-span/bulk_create/"),
    { observation_spans: [spanPayload] },
  );
  const createdSpanIds = asArray(bulkSpanResult?.["Observation Span IDs"]);
  assert(
    createdSpanIds.includes(spanId),
    "Session detail observation span was not bulk-created.",
  );
  await seedObserveClickHouseSpans({
    organizationId: auth.organizationId,
    traceSessionId: sessionId,
    payloads: [spanPayload],
  });

  const evalSeed = await insertSessionEvalFixtureDb({
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
    projectId,
    sessionId,
    evalConfigName,
    evalReason,
    safeSuffix,
  });

  const seedAudit = await loadSessionDetailDbAudit({
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
    projectId,
    projectVersionId,
    sessionId,
    traceId,
    spanId,
    evalTemplateId: evalSeed.eval_template_id,
    evalConfigId: evalSeed.eval_config_id,
    evalLogId: evalSeed.eval_log_id,
  });
  assertSeededSessionFixture(seedAudit, {
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
    projectId,
    projectVersionId,
    sessionId,
    traceId,
    spanId,
    evalLogId: evalSeed.eval_log_id,
    evalConfigName,
  });

  return {
    safeSuffix,
    projectId,
    projectName,
    projectVersionId,
    sessionId,
    sessionName,
    traceId,
    traceName,
    spanId,
    evalTemplateId: evalSeed.eval_template_id,
    evalConfigId: evalSeed.eval_config_id,
    evalLogId: evalSeed.eval_log_id,
    evalConfigName,
    evalReason,
    seedAudit,
  };
}

async function preflightSessionApis(auth, fixture) {
  const listSessions = await auth.client.get(
    apiPath("/tracer/trace-session/list_sessions/"),
    {
      query: {
        project_id: fixture.projectId,
        page_number: 0,
        page_size: 10,
        filters: JSON.stringify([]),
      },
    },
  );
  const sessionRows = asArray(listSessions?.table || listSessions);
  assert(
    sessionRows.some((row) => row.session_id === fixture.sessionId),
    "Trace-session list_sessions did not include the disposable session.",
  );

  const detail = await auth.client.get(
    apiPath("/tracer/trace-session/{id}/", { id: fixture.sessionId }),
  );
  assert(
    detail?.session_metadata?.session_id === fixture.sessionId,
    "Trace-session detail returned wrong session id.",
  );
  assert(
    asArray(detail?.response).some((row) => row.trace_id === fixture.traceId),
    "Trace-session detail did not include the disposable trace.",
  );

  const evalLogs = await auth.client.get(
    apiPath("/tracer/trace-session/{id}/eval_logs/", {
      id: fixture.sessionId,
    }),
    { query: { page: 0, page_size: 5 } },
  );
  const evalItems = asArray(evalLogs?.items || evalLogs);
  assert(
    evalItems.some(
      (item) =>
        item.id === fixture.evalLogId &&
        item.session_id === fixture.sessionId &&
        item.eval_name === fixture.evalConfigName,
    ),
    "Trace-session eval_logs did not include the seeded named session eval.",
  );

  const csv = await auth.client.get(
    apiPath("/tracer/trace-session/get_trace_session_export_data/"),
    {
      query: {
        project_id: fixture.projectId,
        filters: JSON.stringify([]),
      },
    },
  );
  assertSessionCsv(csv, fixture);

  return {
    list_count: sessionRows.length,
    detail_trace_count: asArray(detail?.response).length,
    eval_count: Number(evalLogs?.total || evalItems.length),
    csv_bytes: Buffer.byteLength(csv),
  };
}

function traceWritePayload({
  projectId,
  projectVersionId,
  sessionId,
  name,
  runId,
  marker,
}) {
  return {
    project: projectId,
    project_version: projectVersionId,
    session: sessionId,
    name,
    metadata: { source: "browser-smoke", run_id: runId, trace_marker: marker },
    input: {
      prompt: `browser smoke trace ${marker}`,
      shared: `browser smoke trace shared ${runId}`,
    },
    output: { response: `browser smoke trace response ${marker}` },
    error: null,
    tags: ["browser-smoke", `trace-${marker}`],
  };
}

function observationSpanWritePayload({
  id,
  projectId,
  projectVersionId,
  traceId,
  name,
  runId,
  startTime,
  endTime,
  metadata,
}) {
  return {
    id,
    project: projectId,
    project_version: projectVersionId,
    trace: traceId,
    name,
    observation_type: "llm",
    start_time: startTime,
    end_time: endTime,
    input: { messages: [{ role: "user", content: "browser smoke input" }] },
    output: {
      choices: [{ message: { content: "browser smoke output" } }],
    },
    model: "browser-smoke-model",
    prompt_tokens: 2,
    completion_tokens: 3,
    total_tokens: 5,
    latency_ms: 123,
    cost: 0,
    status: "OK",
    status_message: "browser smoke span",
    tags: ["browser-smoke"],
    metadata: metadata || { source: "browser-smoke", run_id: runId },
  };
}

async function insertSessionEvalFixtureDb({
  organizationId,
  workspaceId,
  projectId,
  sessionId,
  evalConfigName,
  evalReason,
  safeSuffix,
}) {
  const evalTemplateId = randomUUID();
  const evalConfigId = randomUUID();
  const evalLogId = randomUUID();
  const evalTaskId = randomUUID();
  const templateName = `th-4812-session-eval-${safeSuffix}`.slice(0, 200);
  const sql = `
WITH inserted_template AS (
  INSERT INTO model_hub_evaltemplate (
    id,
    name,
    description,
    owner,
    eval_tags,
    config,
    organization_id,
    workspace_id,
    eval_id,
    criteria,
    choices,
    multi_choice,
    model,
    proxy_agi,
    visible_ui,
    output_type_normalized,
    pass_threshold,
    template_type,
    eval_type,
    allow_edit,
    allow_copy,
    error_localizer_enabled,
    aggregation_enabled,
    aggregation_function,
    composite_child_axis,
    created_at,
    updated_at,
    deleted
  )
  VALUES (
    ${sqlUuid(evalTemplateId)},
    ${sqlString(templateName)},
    ${sqlString("Disposable TH-4812 session eval template.")},
    'system',
    ARRAY['browser-smoke', 'th-4812']::varchar(100)[],
    ${sqlJson({ output: "Pass/Fail" })},
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)},
    0,
    ${sqlString("Pass if the TH-4812 browser smoke session is valid.")},
    '[]'::jsonb,
    false,
    NULL,
    true,
    true,
    'pass_fail',
    0.5,
    'single',
    'llm',
    false,
    true,
    false,
    true,
    'weighted_avg',
    '',
    now(),
    now(),
    false
  )
  RETURNING id
),
inserted_config AS (
  INSERT INTO tracer_custom_eval_config (
    id,
    name,
    config,
    mapping,
    eval_template_id,
    project_id,
    filters,
    error_localizer,
    model,
    created_at,
    updated_at,
    deleted
  )
  VALUES (
    ${sqlUuid(evalConfigId)},
    ${sqlString(evalConfigName)},
    ${sqlJson({ output: "Pass/Fail" })},
    ${sqlJson({ input: "session" })},
    ${sqlUuid(evalTemplateId)},
    ${sqlUuid(projectId)},
    ${sqlJson({})},
    false,
    'turing_flash',
    now(),
    now(),
    false
  )
  RETURNING id
),
inserted_eval_log AS (
  INSERT INTO tracer_eval_logger (
    id,
    trace_id,
    observation_span_id,
    trace_session_id,
    target_type,
    custom_eval_config_id,
    eval_task_id,
    output_bool,
    eval_explanation,
    error,
    output_str_list,
    results_tags,
    eval_tags,
    results_explanation,
    created_at,
    updated_at,
    deleted
  )
  VALUES (
    ${sqlUuid(evalLogId)},
    NULL,
    NULL,
    ${sqlUuid(sessionId)},
    'session',
    ${sqlUuid(evalConfigId)},
    ${sqlString(evalTaskId)},
    true,
    ${sqlString(evalReason)},
    false,
    '[]'::jsonb,
    '[]'::jsonb,
    '[]'::jsonb,
    '{}'::jsonb,
    now(),
    now(),
    false
  )
  RETURNING id
)
SELECT json_build_object(
  'eval_template_id', ${sqlString(evalTemplateId)},
  'eval_config_id', ${sqlString(evalConfigId)},
  'eval_log_id', ${sqlString(evalLogId)}
);
`;
  return runPostgresJson(sql);
}

async function loadSessionDetailDbAudit({
  organizationId,
  workspaceId,
  projectId,
  projectVersionId,
  sessionId,
  traceId,
  spanId,
  evalTemplateId,
  evalConfigId,
  evalLogId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuid(projectVersionId)} AS project_version_id,
    ${sqlUuid(sessionId)} AS session_id,
    ${sqlUuid(traceId)} AS trace_id,
    ${sqlString(spanId)} AS span_id,
    ${sqlUuid(evalTemplateId)} AS eval_template_id,
    ${sqlUuid(evalConfigId)} AS eval_config_id,
    ${sqlUuid(evalLogId)} AS eval_log_id
)
SELECT json_build_object(
  'project_id', project.id::text,
  'project_organization_id', project.organization_id::text,
  'project_workspace_id', project.workspace_id::text,
  'project_deleted', project.deleted,
  'project_version_id', pv.id::text,
  'project_version_project_id', pv.project_id::text,
  'session_count', (
    SELECT count(*) FROM trace_session session, requested r
    WHERE session.id = r.session_id
      AND session.project_id = r.project_id
      AND session.deleted = false
  ),
  'trace_count', (
    SELECT count(*) FROM tracer_trace trace, requested r
    WHERE trace.id = r.trace_id
      AND trace.project_id = r.project_id
      AND trace.session_id = r.session_id
      AND trace.deleted = false
  ),
  'span_count', (
    SELECT count(*) FROM tracer_observation_span span, requested r
    WHERE span.id = r.span_id
      AND span.trace_id = r.trace_id
      AND span.project_id = r.project_id
      AND span.project_version_id = r.project_version_id
      AND span.deleted = false
  ),
  'eval_template_count', (
    SELECT count(*) FROM model_hub_evaltemplate template, requested r
    WHERE template.id = r.eval_template_id
      AND template.organization_id = r.organization_id
      AND template.workspace_id = r.workspace_id
      AND template.deleted = false
  ),
  'eval_config_count', (
    SELECT count(*) FROM tracer_custom_eval_config config, requested r
    WHERE config.id = r.eval_config_id
      AND config.eval_template_id = r.eval_template_id
      AND config.project_id = r.project_id
      AND config.deleted = false
  ),
  'eval_log_count', (
    SELECT count(*) FROM tracer_eval_logger eval_log, requested r
    WHERE eval_log.id = r.eval_log_id
      AND eval_log.trace_session_id = r.session_id
      AND eval_log.custom_eval_config_id = r.eval_config_id
      AND eval_log.target_type = 'session'
      AND eval_log.deleted = false
  ),
  'eval_config_name', (
    SELECT config.name FROM tracer_custom_eval_config config, requested r
    WHERE config.id = r.eval_config_id
  )
)
FROM requested r
JOIN tracer_project project ON project.id = r.project_id
JOIN tracer_project_version pv ON pv.id = r.project_version_id;
`;
  return runPostgresJson(sql);
}

function assertSeededSessionFixture(
  audit,
  {
    organizationId,
    workspaceId,
    projectId,
    projectVersionId,
    sessionId,
    traceId,
    spanId,
    evalLogId,
    evalConfigName,
  },
) {
  assert(audit?.project_id === projectId, "Seed audit project id mismatch.");
  assert(
    audit?.project_organization_id === organizationId,
    "Seed audit organization mismatch.",
  );
  assert(
    audit?.project_workspace_id === workspaceId,
    "Seed audit workspace mismatch.",
  );
  assert(
    audit?.project_version_id === projectVersionId &&
      audit?.project_version_project_id === projectId,
    "Seed audit project-version mismatch.",
  );
  assert(Number(audit?.session_count) === 1, `Seed audit missed ${sessionId}.`);
  assert(Number(audit?.trace_count) === 1, `Seed audit missed ${traceId}.`);
  assert(Number(audit?.span_count) === 1, `Seed audit missed ${spanId}.`);
  assert(
    Number(audit?.eval_template_count) === 1,
    "Seed audit missed eval template.",
  );
  assert(
    Number(audit?.eval_config_count) === 1 &&
      audit?.eval_config_name === evalConfigName,
    "Seed audit missed named eval config.",
  );
  assert(
    Number(audit?.eval_log_count) === 1,
    `Seed audit missed eval log ${evalLogId}.`,
  );
}

async function cleanupSessionDetailFixture(fixture) {
  await hardDeleteObserveClickHouseSpans({
    traceIds: [fixture.traceId],
    spanIds: [fixture.spanId],
  });
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(fixture.projectId)} AS project_id,
    ${sqlUuid(fixture.projectVersionId)} AS project_version_id,
    ${sqlUuid(fixture.sessionId)} AS session_id,
    ${sqlUuidArray([fixture.traceId])} AS trace_ids,
    ${sqlStringArray([fixture.spanId])} AS span_ids,
    ${sqlUuid(fixture.evalTemplateId)} AS eval_template_id,
    ${sqlUuid(fixture.evalConfigId)} AS eval_config_id,
    ${sqlUuid(fixture.evalLogId)} AS eval_log_id
),
deleted_eval_logs AS (
  DELETE FROM tracer_eval_logger eval_log
  USING requested r
  WHERE eval_log.id = r.eval_log_id
     OR eval_log.trace_session_id = r.session_id
     OR eval_log.trace_id = ANY(r.trace_ids)
  RETURNING eval_log.id
),
deleted_custom_eval_configs AS (
  DELETE FROM tracer_custom_eval_config config
  USING requested r
  WHERE config.id = r.eval_config_id
  RETURNING config.id
),
deleted_eval_templates AS (
  DELETE FROM model_hub_evaltemplate template
  USING requested r
  WHERE template.id = r.eval_template_id
  RETURNING template.id
),
deleted_replay_sessions AS (
  DELETE FROM tracer_replaysession replay
  USING requested r
  WHERE replay.project_id = r.project_id
  RETURNING replay.id
),
target_annotation_queues AS (
  SELECT queue.id
  FROM model_hub_annotationqueue queue, requested r
  WHERE queue.project_id = r.project_id
),
deleted_annotation_queue_labels AS (
  DELETE FROM model_hub_annotationqueuelabel queue_label
  USING target_annotation_queues queue
  WHERE queue_label.queue_id = queue.id
  RETURNING queue_label.id
),
deleted_annotation_queue_annotators AS (
  DELETE FROM model_hub_annotationqueueannotator annotator
  USING target_annotation_queues queue
  WHERE annotator.queue_id = queue.id
  RETURNING annotator.id
),
deleted_automation_rules AS (
  DELETE FROM model_hub_automationrule rule
  USING target_annotation_queues queue
  WHERE rule.queue_id = queue.id
  RETURNING rule.id
),
deleted_queue_items AS (
  DELETE FROM model_hub_queueitem item
  USING target_annotation_queues queue
  WHERE item.queue_id = queue.id
  RETURNING item.id
),
deleted_annotation_queues AS (
  DELETE FROM model_hub_annotationqueue queue
  USING requested r
  WHERE queue.project_id = r.project_id
  RETURNING queue.id
),
deleted_spans AS (
  DELETE FROM tracer_observation_span span
  USING requested r
  WHERE span.id = ANY(r.span_ids) OR span.trace_id = ANY(r.trace_ids)
  RETURNING span.id
),
deleted_traces AS (
  DELETE FROM tracer_trace trace
  USING requested r
  WHERE trace.id = ANY(r.trace_ids) OR trace.session_id = r.session_id
  RETURNING trace.id
),
deleted_sessions AS (
  DELETE FROM trace_session session
  USING requested r
  WHERE session.id = r.session_id
  RETURNING session.id
),
deleted_project_versions AS (
  DELETE FROM tracer_project_version pv
  USING requested r
  WHERE pv.id = r.project_version_id
  RETURNING pv.id
),
deleted_projects AS (
  DELETE FROM tracer_project project
  USING requested r
  WHERE project.id = r.project_id
  RETURNING project.id
)
SELECT json_build_object(
  'deleted_eval_log_count', (SELECT count(*) FROM deleted_eval_logs),
  'deleted_custom_eval_config_count', (SELECT count(*) FROM deleted_custom_eval_configs),
  'deleted_eval_template_count', (SELECT count(*) FROM deleted_eval_templates),
  'deleted_replay_session_count', (SELECT count(*) FROM deleted_replay_sessions),
  'deleted_annotation_queue_label_count', (SELECT count(*) FROM deleted_annotation_queue_labels),
  'deleted_annotation_queue_annotator_count', (SELECT count(*) FROM deleted_annotation_queue_annotators),
  'deleted_automation_rule_count', (SELECT count(*) FROM deleted_automation_rules),
  'deleted_queue_item_count', (SELECT count(*) FROM deleted_queue_items),
  'deleted_annotation_queue_count', (SELECT count(*) FROM deleted_annotation_queues),
  'deleted_span_count', (SELECT count(*) FROM deleted_spans),
  'deleted_trace_count', (SELECT count(*) FROM deleted_traces),
  'deleted_session_count', (SELECT count(*) FROM deleted_sessions),
  'deleted_project_version_count', (SELECT count(*) FROM deleted_project_versions),
  'deleted_project_count', (SELECT count(*) FROM deleted_projects),
  'remaining_eval_log_count', CASE
    WHEN (SELECT count(*) FROM deleted_eval_logs) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_eval_logger eval_log, requested r
      WHERE eval_log.id = r.eval_log_id
         OR eval_log.trace_session_id = r.session_id
         OR eval_log.trace_id = ANY(r.trace_ids)
    )
  END,
  'remaining_custom_eval_config_count', CASE
    WHEN (SELECT count(*) FROM deleted_custom_eval_configs) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_custom_eval_config config, requested r
      WHERE config.id = r.eval_config_id
    )
  END,
  'remaining_eval_template_count', CASE
    WHEN (SELECT count(*) FROM deleted_eval_templates) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM model_hub_evaltemplate template, requested r
      WHERE template.id = r.eval_template_id
    )
  END,
  'remaining_annotation_queue_count', CASE
    WHEN (SELECT count(*) FROM deleted_annotation_queues) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM model_hub_annotationqueue queue, requested r
      WHERE queue.project_id = r.project_id
    )
  END,
  'remaining_span_count', CASE
    WHEN (SELECT count(*) FROM deleted_spans) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_observation_span span, requested r
      WHERE span.id = ANY(r.span_ids) OR span.trace_id = ANY(r.trace_ids)
    )
  END,
  'remaining_trace_count', CASE
    WHEN (SELECT count(*) FROM deleted_traces) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_trace trace, requested r
      WHERE trace.id = ANY(r.trace_ids) OR trace.session_id = r.session_id
    )
  END,
  'remaining_session_count', CASE
    WHEN (SELECT count(*) FROM deleted_sessions) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM trace_session session, requested r
      WHERE session.id = r.session_id
    )
  END,
  'remaining_project_version_count', CASE
    WHEN (SELECT count(*) FROM deleted_project_versions) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_project_version pv, requested r
      WHERE pv.id = r.project_version_id
    )
  END,
  'remaining_project_count', CASE
    WHEN (SELECT count(*) FROM deleted_projects) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_project project, requested r
      WHERE project.id = r.project_id
    )
  END
);
`;
  return runPostgresJson(sql);
}

function assertCleanedSessionFixture(audit) {
  for (const key of [
    "remaining_eval_log_count",
    "remaining_custom_eval_config_count",
    "remaining_eval_template_count",
    "remaining_annotation_queue_count",
    "remaining_span_count",
    "remaining_trace_count",
    "remaining_session_count",
    "remaining_project_version_count",
    "remaining_project_count",
  ]) {
    assert(Number(audit?.[key]) === 0, `Cleanup left ${key}: ${audit?.[key]}.`);
  }
}

async function seedObserveClickHouseSpans({
  organizationId,
  traceSessionId = null,
  payloads,
}) {
  const rows = asArray(payloads).filter((payload) =>
    String(payload?.id || "").trim(),
  );
  if (!rows.length) return;
  const values = rows
    .map((payload) =>
      observeClickHouseSpanValue(payload, {
        organizationId,
        traceSessionId,
      }),
    )
    .join(",\n");
  await runClickHouseSql(`
INSERT INTO spans (
  project_id,
  observation_type,
  service_name,
  start_time,
  trace_id,
  id,
  parent_span_id,
  name,
  end_time,
  latency_ms,
  org_id,
  project_version_id,
  trace_session_id,
  status,
  status_message,
  model,
  provider,
  gen_ai_system,
  gen_ai_operation,
  operation_name,
  prompt_tokens,
  completion_tokens,
  total_tokens,
  cost,
  attrs_string,
  attrs_number,
  attrs_bool,
  attributes_extra,
  resource_attrs,
  metadata,
  input,
  output,
  tags,
  span_events,
  eval_status,
  semconv_source,
  is_deleted,
  _version
) VALUES
${values};
`);
}

function observeClickHouseSpanValue(
  payload,
  { organizationId, traceSessionId },
) {
  const metadata = payload.metadata || {};
  const attrs = Object.entries(payload.span_attributes || {})
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => [String(key), String(value)]);
  const attrsString = attrs.length
    ? `map(${attrs
        .flatMap(([key, value]) => [chString(key), chString(value)])
        .join(", ")})`
    : "CAST(map(), 'Map(String, String)')";
  const sessionId =
    traceSessionId ||
    payload.trace_session_id ||
    payload.session_id ||
    metadata.session_id ||
    null;
  return `(
  toUUID(${chString(payload.project)}),
  ${chString(payload.observation_type || "llm")},
  'api-journey',
  parseDateTime64BestEffort(${chString(payload.start_time || new Date().toISOString())}, 6, 'UTC'),
  ${chString(payload.trace)},
  ${chString(payload.id)},
  ${chString(payload.parent_span_id || "")},
  ${chString(payload.name || payload.id)},
  parseDateTime64BestEffort(${chString(payload.end_time || new Date().toISOString())}, 6, 'UTC'),
  ${Number(payload.latency_ms || 0)},
  ${chNullableUuid(organizationId)},
  ${chNullableUuid(payload.project_version)},
  ${chNullableUuid(sessionId)},
  ${chString(payload.status || "OK")},
  ${chString(payload.status_message || "")},
  ${chString(payload.model || "api-journey-model")},
  'futureagi',
  'futureagi',
  'chat',
  ${chString(payload.operation_name || "chat")},
  ${Number(payload.prompt_tokens || 0)},
  ${Number(payload.completion_tokens || 0)},
  ${Number(payload.total_tokens || 0)},
  ${Number(payload.cost || 0)},
  ${attrsString},
  CAST(map(), 'Map(String, Float64)'),
  CAST(map(), 'Map(String, UInt8)'),
  ${chJsonString(metadata)},
  ${chJson({ service: "api-journey" })},
  ${chJson(metadata)},
  ${chJsonString(payload.input || {})},
  ${chJsonString(payload.output || {})},
  ${chJsonString(payload.tags || [])},
  ${chJsonString(payload.span_events || [])},
  ${chString(payload.eval_status || "")},
  'traceai',
  0,
  toUnixTimestamp64Nano(now64(9, 'UTC'))
)`;
}

async function hardDeleteObserveClickHouseSpans({
  traceIds = [],
  spanIds = [],
  bestEffort = true,
}) {
  const conditions = [];
  const traces = asArray(traceIds).filter(Boolean);
  const spans = asArray(spanIds).filter(Boolean);
  if (traces.length) {
    conditions.push(`trace_id IN (${traces.map(chString).join(", ")})`);
  }
  if (spans.length) {
    conditions.push(`id IN (${spans.map(chString).join(", ")})`);
  }
  if (!conditions.length) return;
  const sql = `SET mutations_sync = 2; ALTER TABLE spans DELETE WHERE ${conditions.join(
    " OR ",
  )}`;
  if (!bestEffort) {
    await runClickHouseSql(sql);
    return;
  }
  try {
    await runClickHouseSql(sql);
  } catch {
    // ClickHouse cleanup is best-effort; Postgres is the authoritative audit.
  }
}

async function runClickHouseSql(sql) {
  const container = await resolveClickHouseContainer();
  const database = process.env.API_JOURNEY_CLICKHOUSE_DB || "default";
  const guardedSql = `SET ignore_materialized_views_with_dropped_target_table = 1;\n${sql}`;
  await execFileAsync(
    "docker",
    [
      "exec",
      container,
      "clickhouse-client",
      "--database",
      database,
      "--multiquery",
      "--query",
      guardedSql,
    ],
    { maxBuffer: 10 * 1024 * 1024 },
  );
}

async function resolveClickHouseContainer() {
  if (process.env.API_JOURNEY_CLICKHOUSE_CONTAINER) {
    return process.env.API_JOURNEY_CLICKHOUSE_CONTAINER;
  }
  const candidates = [
    "ws2-clickhouse",
    "futureagi-ws2-clickhouse-1",
    "clickhouse",
    "futureagi-clickhouse-1",
  ];
  for (const candidate of candidates) {
    try {
      await execFileAsync("docker", [
        "inspect",
        "--type",
        "container",
        candidate,
      ]);
      return candidate;
    } catch {
      // Try the next local compose naming convention.
    }
  }
  return "ws2-clickhouse";
}

async function allowDownloads(page, downloadDir) {
  const client = await page.target().createCDPSession();
  await client
    .send("Page.setDownloadBehavior", {
      behavior: "allow",
      downloadPath: downloadDir,
    })
    .catch(() =>
      client.send("Browser.setDownloadBehavior", {
        behavior: "allow",
        downloadPath: downloadDir,
      }),
    );
}

async function waitForDownloadedFile(
  downloadDir,
  extension,
  { timeout = 30000 } = {},
) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const entries = await fs.readdir(downloadDir).catch(() => []);
    const matches = entries
      .filter(
        (entry) => entry.endsWith(extension) && !entry.endsWith(".crdownload"),
      )
      .sort();
    if (matches.length > 0) {
      const fullPath = path.join(downloadDir, matches[matches.length - 1]);
      const stats = await fs.stat(fullPath);
      if (stats.size > 0) return fullPath;
    }
    await delay(250);
  }
  throw new Error(
    `Timed out waiting for ${extension} download in ${downloadDir}`,
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

async function installBrowserState(page, auth) {
  await page.evaluateOnNewDocument(() => {
    window.normalizeText = (value) => String(value || "").trim();
    window.dispatchClick = (element) => {
      element.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
      );
      element.dispatchEvent(
        new MouseEvent("mouseup", { bubbles: true, cancelable: true }),
      );
      element.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
    };
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
    window.findSessionsExportButtonInPage = () => {
      const buttons = window
        .visibleElements("button")
        .filter((button) => {
          const rect = button.getBoundingClientRect();
          return (
            rect.top < 60 &&
            rect.left > window.innerWidth - 380 &&
            rect.width <= 44 &&
            rect.height <= 44 &&
            !button.disabled &&
            !window.normalizeText(button.textContent)
          );
        })
        .sort(
          (a, b) =>
            a.getBoundingClientRect().left - b.getBoundingClientRect().left,
        );
      return buttons[1] || null;
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

function sessionListResponse(projectId) {
  return (response) => {
    const url = new URL(response.url());
    return (
      url.pathname === "/tracer/trace-session/list_sessions/" &&
      response.request().method() === "GET" &&
      response.status() < 400 &&
      url.searchParams.get("project_id") === projectId
    );
  };
}

function sessionDetailResponse(sessionId) {
  return (response) => {
    const url = new URL(response.url());
    return (
      url.pathname === `/tracer/trace-session/${sessionId}/` &&
      response.request().method() === "GET" &&
      response.status() < 400
    );
  };
}

function sessionEvalLogsResponse(sessionId) {
  return (response) => {
    const url = new URL(response.url());
    return (
      url.pathname === `/tracer/trace-session/${sessionId}/eval_logs/` &&
      response.request().method() === "GET" &&
      response.status() < 400 &&
      url.searchParams.get("page") === "0"
    );
  };
}

function sessionExportResponse(projectId) {
  return (response) => {
    const url = new URL(response.url());
    return (
      url.pathname === "/tracer/trace-session/get_trace_session_export_data/" &&
      response.request().method() === "GET" &&
      response.status() < 400 &&
      url.searchParams.get("project_id") === projectId
    );
  };
}

async function waitForResponseDuring(page, label, predicate, action) {
  const responsePromise = page.waitForResponse(predicate, { timeout: 60000 });
  try {
    await action();
  } catch (error) {
    responsePromise.catch(() => null);
    throw error;
  }
  try {
    return await responsePromise;
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
}

async function readResponseJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
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

async function waitForSessionGridRow(page, rowLabel) {
  await page.waitForFunction(
    (expectedLabel) =>
      window
        .visibleElements(".ag-center-cols-container [role='row']")
        .some((row) => row.textContent.includes(expectedLabel)),
    { timeout: 60000 },
    rowLabel,
  );
}

async function clickSessionGridRow(page, rowLabel) {
  await waitForSessionGridRow(page, rowLabel);
  const box = await page.evaluate((expectedLabel) => {
    const row = window
      .visibleElements(".ag-center-cols-container [role='row']")
      .find((candidate) => candidate.textContent.includes(expectedLabel));
    if (!row) return null;
    const rect = row.getBoundingClientRect();
    return {
      x: rect.left + Math.min(240, rect.width / 2),
      y: rect.top + rect.height / 2,
    };
  }, rowLabel);
  assert(box, `Could not find session grid row: ${rowLabel}`);
  await page.mouse.click(box.x, box.y);
}

async function clickTab(page, label, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
  const clicked = await page.evaluate((expectedLabel) => {
    const tab = window
      .visibleElements('button[role="tab"]')
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedLabel &&
          !candidate.disabled,
      );
    if (!tab) return false;
    window.dispatchClick(tab);
    return true;
  }, label);
  assert(clicked, `Could not click tab: ${label}`);
}

async function clickExportCsvButton(page) {
  await page.waitForFunction(
    () => {
      const fallbackButton = window.findSessionsExportButtonInPage();
      if (fallbackButton) return true;
      return window.visibleElements(".component-iconify").some((icon) => {
        const iconName =
          icon.getAttribute("icon") || icon.getAttribute("data-icon") || "";
        const button = icon.closest("button");
        return (
          iconName === "mdi:download-outline" && button && !button.disabled
        );
      });
    },
    { timeout: 30000 },
  );
  const clicked = await page.evaluate(() => {
    const icon = window
      .visibleElements(".component-iconify")
      .find((candidate) => {
        const iconName =
          candidate.getAttribute("icon") ||
          candidate.getAttribute("data-icon") ||
          "";
        const button = candidate.closest("button");
        return (
          iconName === "mdi:download-outline" && button && !button.disabled
        );
      });
    const button =
      icon?.closest("button") || window.findSessionsExportButtonInPage();
    if (!button) return false;
    window.dispatchClick(button);
    return true;
  });
  assert(clicked, "Could not click Sessions export CSV button.");
}

function assertSessionCsv(text, fixture) {
  assert(typeof text === "string", "Session export did not return text.");
  const csv = text.trim();
  assert(csv.includes("session_id"), "Session export CSV missing header.");
  assert(
    csv.includes(fixture.sessionId),
    "Session export CSV missing session id.",
  );
  assert(
    csv.includes(fixture.sessionName),
    "Session export CSV missing session name.",
  );
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
  const line = stdout.trim().split(/\r?\n/).find(Boolean);
  assert(line, "Postgres DB audit returned no JSON output.");
  return JSON.parse(line);
}

function sqlUuid(value) {
  assert(isUuid(value), `Expected UUID value, got ${value}.`);
  return `'${value}'::uuid`;
}

function sqlUuidArray(values) {
  const rows = asArray(values);
  assert(rows.length > 0, "SQL UUID array must not be empty.");
  return `ARRAY[${rows.map(sqlUuid).join(",")}]::uuid[]`;
}

function sqlStringArray(values) {
  const rows = asArray(values);
  assert(rows.length > 0, "SQL text array must not be empty.");
  return `ARRAY[${rows.map(sqlString).join(",")}]::text[]`;
}

function sqlString(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function sqlJson(value) {
  return `${sqlString(JSON.stringify(value ?? null))}::jsonb`;
}

function chString(value) {
  return `'${String(value ?? "")
    .replaceAll("\\", "\\\\")
    .replaceAll("'", "\\'")}'`;
}

function chJson(value) {
  return `CAST(${chString(JSON.stringify(value ?? {}))}, 'JSON')`;
}

function chJsonString(value) {
  return chString(JSON.stringify(value ?? null));
}

function chNullableUuid(value) {
  return isUuid(value) ? `toUUID(${chString(value)})` : "NULL";
}

function isObserveApiUrl(url) {
  return url.includes("/tracer/");
}

function isObserveSessionApiUrl(url) {
  return url.includes("/tracer/trace-session/");
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

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
