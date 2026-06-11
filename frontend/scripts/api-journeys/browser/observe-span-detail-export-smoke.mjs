/* eslint-disable no-console */
import { Buffer } from "node:buffer";
import { execFile } from "node:child_process";
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
const DETAIL_SCREENSHOT_PATH = "/tmp/observe-span-detail-smoke.png";
const TAG_SCREENSHOT_PATH = "/tmp/observe-span-tag-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/observe-span-detail-export-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const fixture = await createSpanDetailFixture(auth);
  const downloadDir = path.join(
    "/tmp",
    `observe-span-downloads-${fixture.safeSuffix}`,
  );
  let cleanupDone = false;
  let browser = null;
  let page = null;
  const apiFailures = [];
  const pageErrors = [];
  const spanRequests = [];
  const evidence = {
    project_id: fixture.projectId,
    project_name: fixture.projectName,
    project_version_id: fixture.projectVersionId,
    trace_id: fixture.traceId,
    trace_name: fixture.traceName,
    span_id: fixture.spanId,
    span_name: fixture.spanName,
    tag_name: fixture.tagName,
    seed_audit: fixture.seedAudit,
    download_dir: downloadDir,
  };

  await fs.rm(downloadDir, { recursive: true, force: true });
  await fs.mkdir(downloadDir, { recursive: true });

  try {
    evidence.api_preflight = await preflightSpanApis(auth, fixture);

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
      if (!isObserveSpanApiUrl(request.url())) return;
      spanRequests.push(`${request.method()} ${request.url()}`);
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
      "initial observe span list",
      spanListResponse(fixture.projectId),
      () =>
        page.goto(
          `${APP_BASE}/dashboard/observe/${fixture.projectId}/llm-tracing?tab=spans&selectedTab=spans`,
          {
            waitUntil: "domcontentloaded",
          },
        ),
    );
    const listBody = await readResponseJson(listResponse);
    const listRows = asArray(listBody?.result?.table || listBody?.table);
    evidence.browser_list_count = listRows.length;
    assert(
      listRows.some(
        (row) =>
          row.span_id === fixture.spanId &&
          row.trace_id === fixture.traceId &&
          row.span_name === fixture.spanName,
      ),
      "Browser span list response omitted the disposable span.",
    );

    await waitForPath(
      page,
      `/dashboard/observe/${fixture.projectId}/llm-tracing`,
    );
    await waitForVisibleText(page, fixture.projectName);
    await waitForSpanGridRow(page, fixture.spanName);

    const detailResponse = await waitForResponseDuring(
      page,
      "open observe span drawer",
      traceDetailResponse(fixture.traceId),
      () => clickSpanGridRow(page, fixture.spanName),
    );
    const detailBody = await readResponseJson(detailResponse);
    const detailSpans = flattenTraceEntries(detailBody?.result || detailBody);
    assert(
      detailSpans.some((row) => row.span?.id === fixture.spanId),
      "Browser span drawer trace response omitted the disposable span.",
    );

    await waitForVisibleText(page, fixture.spanName);
    await waitForVisibleText(page, fixture.spanId);
    await waitForVisibleText(page, "browser span input");
    await waitForVisibleText(page, "browser span output");
    await waitForVisibleText(page, "browser-span-model", { exact: false });
    await page.screenshot({ path: DETAIL_SCREENSHOT_PATH, fullPage: true });
    evidence.detail_screenshot = DETAIL_SCREENSHOT_PATH;

    const tagResponse = await waitForResponseDuring(
      page,
      "observe span tag update",
      spanTagUpdateResponse(fixture.spanId),
      () => addInlineSpanTag(page, fixture.tagName),
    );
    const tagBody = await readResponseJson(tagResponse);
    const updatedTags = asArray(tagBody?.result?.tags || tagBody?.tags);
    assert(
      updatedTags.some((tag) => tagName(tag) === fixture.tagName),
      "Span update-tags response omitted the browser-created tag.",
    );
    await waitForVisibleText(page, fixture.tagName, { exact: true });
    evidence.tag_audit = await loadSpanTagDbAudit({
      projectId: fixture.projectId,
      traceId: fixture.traceId,
      spanId: fixture.spanId,
      tagName: fixture.tagName,
    });
    assert(
      evidence.tag_audit?.tag_present === true,
      `Span tag DB audit failed: ${JSON.stringify(evidence.tag_audit)}`,
    );
    await page.screenshot({ path: TAG_SCREENSHOT_PATH, fullPage: true });
    evidence.tag_screenshot = TAG_SCREENSHOT_PATH;

    const postTagListResponse = await waitForResponseDuring(
      page,
      "return to observe span list",
      spanListResponse(fixture.projectId),
      () => gotoObserveSpanList(page, fixture.projectId),
    );
    const postTagListBody = await readResponseJson(postTagListResponse);
    const postTagListRows = asArray(
      postTagListBody?.result?.table || postTagListBody?.table,
    );
    assert(
      postTagListRows.some((row) => row.span_id === fixture.spanId),
      "Post-tag span list response omitted the disposable span.",
    );
    await waitForSpanGridRow(page, fixture.spanName);

    const exportResponse = await waitForResponseDuring(
      page,
      "observe span CSV export",
      spanExportResponse(fixture.projectId),
      () => clickExportCsvButton(page),
    );
    assert(
      exportResponse.status() === 200,
      `Span CSV export returned HTTP ${exportResponse.status()}.`,
    );
    const csvPath = await waitForDownloadedFile(downloadDir, ".csv");
    const csvText = await fs.readFile(csvPath, "utf8");
    assertSpanCsv(csvText, fixture);
    evidence.download = {
      csv: csvPath,
      bytes: Buffer.byteLength(csvText),
    };

    const cleanupAudit = await cleanupSpanDetailFixture(fixture);
    cleanupDone = true;
    evidence.cleanup = cleanupAudit;
    assertCleanedSpanFixture(cleanupAudit);

    assert(
      apiFailures.length === 0,
      `Observe span API failures: ${apiFailures.join("; ")}`,
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
          observe_span_request_count: spanRequests.length,
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
          observe_span_requests: spanRequests,
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
      await cleanupSpanDetailFixture(fixture).catch((error) => {
        console.error(`Cleanup failed: ${error.message}`);
      });
    }
  }
}

async function createSpanDetailFixture(auth) {
  const safeSuffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const projectName = `TH-4812 observe spans ${safeSuffix}`;
  const traceName = `TH-4812 trace spans ${safeSuffix}`;
  const spanId = `th4812_span_${safeSuffix}`;
  const spanName = `TH-4812 span detail ${safeSuffix}`;
  const tagName = `th-4812-span-${safeSuffix}`;

  const createdProject = await auth.client.post(apiPath("/tracer/project/"), {
    name: projectName,
    model_type: "GenerativeLLM",
    trace_type: "observe",
    metadata: { source: "browser-smoke", run_id: safeSuffix },
  });
  const projectId = createdProject.project_id || createdProject.id;
  assert(isUuid(projectId), "Span detail project create returned no id.");

  const projectVersion = await auth.client.post(
    apiPath("/tracer/project-version/"),
    {
      project: projectId,
      name: `TH-4812 span run ${safeSuffix}`,
      metadata: { source: "browser-smoke", run_id: safeSuffix },
    },
  );
  const projectVersionId =
    projectVersion.project_version_id || projectVersion.id;
  assert(
    isUuid(projectVersionId),
    "Span detail project-version create returned no id.",
  );

  const trace = await auth.client.post(
    apiPath("/tracer/trace/"),
    traceWritePayload({
      projectId,
      projectVersionId,
      name: traceName,
      runId: safeSuffix,
    }),
  );
  const traceId = trace.id || trace.trace_id || trace.trace?.id;
  assert(isUuid(traceId), "Span detail trace create returned no id.");

  const now = Date.now();
  const spanPayload = observationSpanWritePayload({
    id: spanId,
    projectId,
    projectVersionId,
    traceId,
    name: spanName,
    runId: safeSuffix,
    startTime: new Date(now - 940).toISOString(),
    endTime: new Date(now).toISOString(),
    metadata: {
      source: "browser-smoke",
      run_id: safeSuffix,
      marker: "span-detail",
    },
  });
  spanPayload.latency_ms = 940;
  spanPayload.prompt_tokens = 17;
  spanPayload.completion_tokens = 19;
  spanPayload.total_tokens = 36;
  spanPayload.cost = 0.036;
  spanPayload.span_attributes = {
    th_4812_marker: `span-detail-${safeSuffix}`,
    th_4812_browser_span: spanName,
  };

  const bulkSpanResult = await auth.client.post(
    apiPath("/tracer/observation-span/bulk_create/"),
    { observation_spans: [spanPayload] },
  );
  const createdSpanIds = asArray(bulkSpanResult?.["Observation Span IDs"]);
  assert(
    createdSpanIds.includes(spanId),
    "Span detail observation span was not bulk-created.",
  );
  await seedObserveClickHouseSpans({
    organizationId: auth.organizationId,
    payloads: [spanPayload],
  });

  const seedAudit = await loadSpanDetailDbAudit({
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
    projectId,
    projectVersionId,
    traceId,
    spanId,
  });
  assertSeededSpanFixture(seedAudit, {
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
    projectId,
    projectVersionId,
    traceId,
    spanId,
  });

  return {
    safeSuffix,
    projectId,
    projectName,
    projectVersionId,
    traceId,
    traceName,
    spanId,
    spanName,
    tagName,
    seedAudit,
  };
}

async function preflightSpanApis(auth, fixture) {
  const listSpans = await auth.client.get(
    apiPath("/tracer/observation-span/list_spans_observe/"),
    {
      query: {
        project_id: fixture.projectId,
        page_number: 0,
        page_size: 10,
        filters: JSON.stringify([]),
      },
    },
  );
  const spanRows = asArray(listSpans?.table || listSpans);
  assert(
    spanRows.some(
      (row) =>
        row.span_id === fixture.spanId &&
        row.trace_id === fixture.traceId &&
        row.span_name === fixture.spanName,
    ),
    "Observation span list_spans_observe did not include the disposable span.",
  );

  const traceDetail = await auth.client.get(
    apiPath("/tracer/trace/{id}/", { id: fixture.traceId }),
  );
  const detailSpans = flattenTraceEntries(traceDetail);
  assert(
    detailSpans.some(
      (row) =>
        row.span?.id === fixture.spanId && row.span?.name === fixture.spanName,
    ),
    "Trace detail did not include the disposable observation span.",
  );

  const spanDetail = await auth.client.get(
    apiPath("/tracer/observation-span/{id}/", { id: fixture.spanId }),
  );
  const spanDetailRow = spanDetail?.observation_span || spanDetail;
  assert(
    spanDetailRow?.id === fixture.spanId,
    "Observation span detail returned wrong span id.",
  );

  const csv = await auth.client.get(
    apiPath("/tracer/observation-span/get_spans_export_data/"),
    {
      query: {
        project_id: fixture.projectId,
        filters: JSON.stringify([]),
      },
    },
  );
  assertSpanCsv(csv, fixture);

  return {
    list_count: spanRows.length,
    trace_detail_span_count: detailSpans.length,
    export_bytes: Buffer.byteLength(csv),
  };
}

function traceWritePayload({ projectId, projectVersionId, name, runId }) {
  return {
    project: projectId,
    project_version: projectVersionId,
    name,
    metadata: { source: "browser-smoke", run_id: runId },
    input: {
      prompt: "browser span trace input",
      shared: `browser span trace shared ${runId}`,
    },
    output: { response: "browser span trace output" },
    error: null,
    tags: ["browser-smoke", "span-detail"],
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
    input: { messages: [{ role: "user", content: "browser span input" }] },
    output: {
      choices: [{ message: { content: "browser span output" } }],
    },
    model: "browser-span-model",
    provider: "futureagi",
    prompt_tokens: 2,
    completion_tokens: 3,
    total_tokens: 5,
    latency_ms: 123,
    cost: 0,
    status: "OK",
    status_message: "browser span status",
    tags: ["browser-smoke", "span-detail"],
    metadata: metadata || { source: "browser-smoke", run_id: runId },
  };
}

async function loadSpanDetailDbAudit({
  organizationId,
  workspaceId,
  projectId,
  projectVersionId,
  traceId,
  spanId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuid(projectVersionId)} AS project_version_id,
    ${sqlUuid(traceId)} AS trace_id,
    ${sqlString(spanId)} AS span_id
)
SELECT json_build_object(
  'project_id', project.id::text,
  'project_organization_id', project.organization_id::text,
  'project_workspace_id', project.workspace_id::text,
  'project_deleted', project.deleted,
  'project_version_id', pv.id::text,
  'project_version_project_id', pv.project_id::text,
  'trace_count', (
    SELECT count(*) FROM tracer_trace trace, requested r
    WHERE trace.id = r.trace_id
      AND trace.project_id = r.project_id
      AND trace.deleted = false
  ),
  'span_count', (
    SELECT count(*) FROM tracer_observation_span span, requested r
    WHERE span.id = r.span_id
      AND span.trace_id = r.trace_id
      AND span.project_id = r.project_id
      AND span.project_version_id = r.project_version_id
      AND span.deleted = false
  )
)
FROM requested r
JOIN tracer_project project ON project.id = r.project_id
JOIN tracer_project_version pv ON pv.id = r.project_version_id;
`;
  return runPostgresJson(sql);
}

async function loadSpanTagDbAudit({ projectId, traceId, spanId, tagName }) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuid(traceId)} AS trace_id,
    ${sqlString(spanId)} AS span_id,
    ${sqlString(tagName)} AS tag_name
)
SELECT json_build_object(
  'span_id', span.id,
  'trace_id', span.trace_id::text,
  'project_id', span.project_id::text,
  'tags', span.tags,
  'tag_present', EXISTS (
    SELECT 1
    FROM jsonb_array_elements(
      CASE
        WHEN jsonb_typeof(span.tags::jsonb) = 'array' THEN span.tags::jsonb
        ELSE '[]'::jsonb
      END
    ) tag
    WHERE
      CASE
        WHEN jsonb_typeof(tag) = 'string' THEN trim(both '"' from tag::text)
        ELSE tag->>'name'
      END = requested.tag_name
  )
)
FROM requested
JOIN tracer_observation_span span ON span.id = requested.span_id
WHERE span.trace_id = requested.trace_id
  AND span.project_id = requested.project_id;
`;
  return runPostgresJson(sql);
}

function assertSeededSpanFixture(
  audit,
  { organizationId, workspaceId, projectId, projectVersionId, traceId, spanId },
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
  assert(Number(audit?.trace_count) === 1, `Seed audit missed ${traceId}.`);
  assert(Number(audit?.span_count) === 1, `Seed audit missed ${spanId}.`);
}

async function cleanupSpanDetailFixture(fixture) {
  await hardDeleteObserveClickHouseSpans({
    traceIds: [fixture.traceId],
    spanIds: [fixture.spanId],
  });
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(fixture.projectId)} AS project_id,
    ${sqlUuid(fixture.projectVersionId)} AS project_version_id,
    ${sqlUuidArray([fixture.traceId])} AS trace_ids,
    ${sqlStringArray([fixture.spanId])} AS span_ids
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
  WHERE trace.id = ANY(r.trace_ids)
  RETURNING trace.id
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
  'deleted_annotation_queue_label_count', (SELECT count(*) FROM deleted_annotation_queue_labels),
  'deleted_annotation_queue_annotator_count', (SELECT count(*) FROM deleted_annotation_queue_annotators),
  'deleted_automation_rule_count', (SELECT count(*) FROM deleted_automation_rules),
  'deleted_queue_item_count', (SELECT count(*) FROM deleted_queue_items),
  'deleted_annotation_queue_count', (SELECT count(*) FROM deleted_annotation_queues),
  'deleted_span_count', (SELECT count(*) FROM deleted_spans),
  'deleted_trace_count', (SELECT count(*) FROM deleted_traces),
  'deleted_project_version_count', (SELECT count(*) FROM deleted_project_versions),
  'deleted_project_count', (SELECT count(*) FROM deleted_projects),
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
      WHERE trace.id = ANY(r.trace_ids)
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

function assertCleanedSpanFixture(audit) {
  for (const key of [
    "remaining_annotation_queue_count",
    "remaining_span_count",
    "remaining_trace_count",
    "remaining_project_version_count",
    "remaining_project_count",
  ]) {
    assert(Number(audit?.[key]) === 0, `Cleanup left ${key}: ${audit?.[key]}.`);
  }
}

async function seedObserveClickHouseSpans({ organizationId, payloads }) {
  const rows = asArray(payloads).filter((payload) =>
    String(payload?.id || "").trim(),
  );
  if (!rows.length) return;
  const values = rows
    .map((payload) => observeClickHouseSpanValue(payload, { organizationId }))
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

function observeClickHouseSpanValue(payload, { organizationId }) {
  const metadata = payload.metadata || {};
  const attrs = Object.entries(payload.span_attributes || {})
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => [String(key), String(value)]);
  const attrsString = attrs.length
    ? `map(${attrs
        .flatMap(([key, value]) => [chString(key), chString(value)])
        .join(", ")})`
    : "CAST(map(), 'Map(String, String)')";
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
  NULL,
  ${chString(payload.status || "OK")},
  ${chString(payload.status_message || "")},
  ${chString(payload.model || "api-journey-model")},
  ${chString(payload.provider || "futureagi")},
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
    window.findObserveExportButtonInPage = () => {
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

function spanListResponse(projectId) {
  return (response) => {
    const url = new URL(response.url());
    return (
      url.pathname === "/tracer/observation-span/list_spans_observe/" &&
      response.request().method() === "GET" &&
      response.status() < 400 &&
      url.searchParams.get("project_id") === projectId
    );
  };
}

async function gotoObserveSpanList(page, projectId) {
  await page.goto(
    `${APP_BASE}/dashboard/observe/${projectId}/llm-tracing?tab=spans&selectedTab=spans`,
    { waitUntil: "domcontentloaded" },
  );
  await waitForPath(page, `/dashboard/observe/${projectId}/llm-tracing`);
}

function traceDetailResponse(traceId) {
  return (response) => {
    const url = new URL(response.url());
    return (
      url.pathname === `/tracer/trace/${traceId}/` &&
      response.request().method() === "GET" &&
      response.status() < 400
    );
  };
}

function spanTagUpdateResponse(spanId) {
  return (response) => {
    const url = new URL(response.url());
    return (
      url.pathname === "/tracer/observation-span/update-tags/" &&
      response.request().method() === "POST" &&
      response.status() < 400 &&
      response.request().postData()?.includes(spanId)
    );
  };
}

function spanExportResponse(projectId) {
  return (response) => {
    const url = new URL(response.url());
    return (
      url.pathname === "/tracer/observation-span/get_spans_export_data/" &&
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

async function waitForSpanGridRow(page, rowLabel) {
  await page.waitForFunction(
    (expectedLabel) =>
      window
        .visibleElements(".ag-center-cols-container [role='row']")
        .some((row) => row.textContent.includes(expectedLabel)),
    { timeout: 60000 },
    rowLabel,
  );
}

async function clickSpanGridRow(page, rowLabel) {
  await waitForSpanGridRow(page, rowLabel);
  const box = await page.evaluate((expectedLabel) => {
    const row = window
      .visibleElements(".ag-center-cols-container [role='row']")
      .find((candidate) => candidate.textContent.includes(expectedLabel));
    if (!row) return null;
    const rect = row.getBoundingClientRect();
    return {
      x: rect.left + Math.min(110, rect.width / 2),
      y: rect.top + rect.height / 2,
    };
  }, rowLabel);
  assert(box, `Could not find span grid row: ${rowLabel}`);
  await page.mouse.click(box.x, box.y);
}

async function addInlineSpanTag(page, tagNameValue) {
  const clicked = await page.evaluate(() => {
    const candidates = window
      .visibleElements()
      .filter((element) => {
        const text = window.normalizeText(element.textContent);
        const rect = element.getBoundingClientRect();
        return (
          text === "tag" &&
          rect.width > 20 &&
          rect.width < 80 &&
          rect.height <= 32 &&
          element.querySelector(".component-iconify")
        );
      })
      .sort(
        (a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top,
      );
    const target = candidates[0];
    if (!target) return false;
    window.dispatchClick(target);
    return true;
  });
  assert(clicked, "Could not click inline span tag control.");
  await page.waitForSelector('input[placeholder="tag name"]', {
    visible: true,
    timeout: 30000,
  });
  await page.type('input[placeholder="tag name"]', tagNameValue);
  await page.keyboard.press("Enter");
}

async function clickExportCsvButton(page) {
  await page.waitForFunction(
    () => {
      const fallbackButton = window.findObserveExportButtonInPage();
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
      icon?.closest("button") || window.findObserveExportButtonInPage();
    if (!button) return false;
    window.dispatchClick(button);
    return true;
  });
  assert(clicked, "Could not click Spans export CSV button.");
}

function flattenTraceEntries(detail) {
  const entries = asArray(
    detail?.observation_spans || detail?.trace?.observation_spans,
  );
  const rows = [];
  function walk(entry) {
    if (!entry || typeof entry !== "object") return;
    const span = entry.observation_span || entry.span || entry;
    if (span?.id) rows.push({ entry, span });
    for (const child of asArray(entry.children)) walk(child);
  }
  for (const entry of entries) walk(entry);
  return rows;
}

function tagName(tag) {
  if (typeof tag === "string") return tag;
  return tag?.name || "";
}

function assertSpanCsv(text, fixture) {
  assert(typeof text === "string", "Span export did not return text.");
  const csv = text.trim();
  assert(csv.includes("span_id"), "Span export CSV missing span_id header.");
  assert(csv.includes("trace_id"), "Span export CSV missing trace_id header.");
  assert(csv.includes(fixture.spanId), "Span export CSV missing span id.");
  assert(csv.includes(fixture.traceId), "Span export CSV missing trace id.");
  assert(csv.includes(fixture.spanName), "Span export CSV missing span name.");
  assert(
    csv.includes("browser-span-model"),
    "Span export CSV missing model value.",
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

function isObserveSpanApiUrl(url) {
  return url.includes("/tracer/observation-span/");
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
