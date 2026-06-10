import { execFile as execFileCallback } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";
import {
  CleanupStack,
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
const SCREENSHOT_PATH = "/tmp/error-feed-smoke.png";

async function main() {
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
  const errorName = row.error?.name || row.cluster_id;
  let initialStats = null;
  const apiFailures = [];
  const pageErrors = [];
  const unexpectedMutations = [];
  let caughtError = null;
  let cleanupError = null;
  let result = null;

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  await page.setBypassServiceWorker(true);
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
    if (
      isErrorFeedApiUrl(url) &&
      ["POST", "PATCH", "PUT", "DELETE"].includes(request.method())
    ) {
      unexpectedMutations.push(`${request.method()} ${url}`);
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (isErrorFeedApiUrl(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const initialStatsResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        isErrorFeedStatsUrl(response.url()) &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await waitForResponseDuring(
      page,
      "initial Error Feed load",
      (response) =>
        response.request().method() === "GET" &&
        isErrorFeedListUrl(response.url()) &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/feed`, {
          waitUntil: "domcontentloaded",
        }),
    );
    initialStats = await parseStatsResponse(await initialStatsResponsePromise);
    await page.waitForFunction(
      () => window.location.pathname === "/dashboard/error-feed",
      { timeout: 30000 },
    );
    await expectVisibleText(page, "Error Feed", { exact: true });
    await expectVisibleText(page, "Severity", { exact: true });
    await expectVisibleText(page, "Last seen", { exact: true });
    assert(
      Number(initialStats.totalErrors) >= 1,
      `Expected Error Feed stats totalErrors >= 1, got ${JSON.stringify(
        initialStats,
      )}`,
    );
    await expectErrorFeedStat(page, "totalErrors", initialStats.totalErrors);
    await expectErrorFeedStat(page, "escalating", initialStats.escalating);
    await expectVisibleText(page, "Users affected", { exact: true });

    await waitForResponseDuring(
      page,
      "time range filter",
      (response) =>
        response.request().method() === "GET" &&
        isErrorFeedListUrl(response.url()) &&
        response.url().includes("time_range_days=90") &&
        response.status() < 400,
      () => selectComboboxOption(page, "Last 7 days", "Last 90 days"),
    );
    await expectVisibleText(page, errorName, { exact: false });
    await sleep(500);

    await waitForResponseDuring(
      page,
      "severity filter",
      (response) =>
        response.request().method() === "GET" &&
        isErrorFeedListUrl(response.url()) &&
        response
          .url()
          .includes(`severity=${encodeURIComponent(row.severity)}`) &&
        response.status() < 400,
      () =>
        selectComboboxOption(
          page,
          "All Severities",
          severityLabel(row.severity),
        ),
    );
    await expectVisibleText(page, errorName, { exact: false });

    await clickVisibleRowText(page, errorName);
    await page.waitForFunction(
      (clusterId) =>
        window.location.pathname.endsWith(`/dashboard/error-feed/${clusterId}`),
      { timeout: 30000 },
      row.cluster_id,
    );
    await expectVisibleText(page, "Overview", { exact: true });
    await expectVisibleText(page, "Traces", { exact: true });
    await expectVisibleText(page, "Timeline", { exact: true });
    await expectNoVisibleText(page, "Invalid Date");

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Read-only Error Feed smoke fired mutations: ${unexpectedMutations.join("; ")}`,
    );

    result = {
      status: "passed",
      app_base: APP_BASE,
      api_base: auth.apiBase,
      organization_id: auth.organizationId,
      workspace_id: auth.workspaceId,
      evidence: {
        cluster_id: row.cluster_id,
        project_id: row.project_id,
        severity: row.severity,
        stats: initialStats,
        screenshot: SCREENSHOT_PATH,
        fixture: fixtureEvidence,
        cleanup: cleanupEvidence,
      },
    };
  } catch (error) {
    caughtError = error;
    throw error;
  } finally {
    await browser.close();
    const cleanupFailures = await prepared.cleanup.run(cleanupEvidence);
    if (cleanupFailures.length > 0 && !caughtError) {
      cleanupError = new Error(
        `Error Feed smoke cleanup failed: ${JSON.stringify(cleanupFailures)}`,
      );
    }
  }
  if (cleanupError) throw cleanupError;
  console.log(JSON.stringify(result, null, 2));
}

export async function prepareErrorFeedRow({
  client,
  organizationId,
  workspaceId,
  runId,
  evidence,
}) {
  assert(
    isUuid(organizationId),
    "Authenticated context did not resolve an organization id.",
  );
  assert(
    isUuid(workspaceId),
    "Authenticated context did not resolve a workspace id.",
  );

  const cleanup = new CleanupStack();
  const forceFixture = envFlag("ERROR_FEED_FORCE_FIXTURE");
  let seededFixture = null;
  let list = { data: [], total: 0, limit: 0, offset: 0 };
  let rows = [];

  try {
    if (!forceFixture) {
      list = await loadErrorFeedList(client, { limit: 5 });
      rows = asArray(list);
    }

    if (forceFixture || rows.length === 0) {
      requireMutations();
      const project = await resolveErrorFeedProject({
        client,
        cleanup,
        organizationId,
        workspaceId,
        runId,
        evidence,
      });
      seededFixture = await seedDisposableErrorFeedFixture({
        organizationId,
        workspaceId,
        projectId: project.id,
        runId,
      });
      cleanup.defer("hard delete Error Feed browser fixture", async () => {
        const cleanupAudit =
          await hardDeleteDisposableErrorFeedFixture(seededFixture);
        assertErrorFeedFixtureCleanup(cleanupAudit);
      });
      evidence.push({
        error_feed_fixture_source: "seeded-db-fixture",
        cluster_id: seededFixture.cluster_id,
        project_id: seededFixture.project_id,
        trace_id: seededFixture.trace_id,
      });
      list = await loadErrorFeedList(client, { limit: 25 });
      rows = asArray(list);
    }

    const row = seededFixture
      ? rows.find(
          (candidate) => candidate.cluster_id === seededFixture.cluster_id,
        )
      : rows[0];
    assert(
      row,
      seededFixture
        ? `Seeded Error Feed cluster ${seededFixture.cluster_id} was not returned by the feed list.`
        : "Error Feed preflight returned no rows.",
    );

    return { cleanup, list, row, rows, seededFixture };
  } catch (error) {
    const cleanupFailures = await cleanup.run([]);
    if (cleanupFailures.length > 0) {
      error.message = `${error.message}; fixture cleanup failures: ${JSON.stringify(
        cleanupFailures,
      )}`;
    }
    throw error;
  }
}

async function loadErrorFeedList(client, { limit }) {
  return client.get(apiPath("/tracer/feed/issues/"), {
    query: {
      time_range_days: 90,
      sort_by: "last_seen",
      sort_dir: "desc",
      limit,
      offset: 0,
    },
  });
}

async function seedDisposableErrorFeedProject({
  organizationId,
  workspaceId,
  runId,
}) {
  assert(
    isUuid(organizationId),
    "organizationId must be a UUID for Error Feed project fixture.",
  );
  assert(
    isUuid(workspaceId),
    "workspaceId must be a UUID for Error Feed project fixture.",
  );

  const projectId = randomUUID();
  const suffix = journeySafeId(runId);
  const projectName = `api journey error feed project ${suffix}`;
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(projectId)} AS project_id,
    ${sqlString(projectName)} AS project_name
),
workspace_row AS (
  SELECT workspace.id, workspace.organization_id
  FROM accounts_workspace workspace
  JOIN requested r ON workspace.id = r.workspace_id
  WHERE workspace.organization_id = r.organization_id
    AND workspace.is_active = true
),
inserted_project AS (
  INSERT INTO tracer_project (
    id,
    created_at,
    updated_at,
    deleted,
    model_type,
    name,
    trace_type,
    metadata,
    config,
    session_config,
    source,
    organization_id,
    workspace_id,
    tags
  )
  SELECT
    r.project_id,
    NOW(),
    NOW(),
    false,
    'GenerativeLLM',
    r.project_name,
    'observe',
    ${sqlString(JSON.stringify({ source: "api-journey", run_id: runId }))}::jsonb,
    '[]'::jsonb,
    '[]'::jsonb,
    'prototype',
    r.organization_id,
    workspace_row.id,
    ${sqlString(JSON.stringify(["api-journey", "error-feed"]))}::jsonb
  FROM requested r
  JOIN workspace_row ON true
  RETURNING id, name
)
SELECT json_build_object(
  'project_created', EXISTS (SELECT 1 FROM inserted_project),
  'project_id', (SELECT id::text FROM inserted_project),
  'project_name', (SELECT name FROM inserted_project)
);
`;
  const project = await runPostgresJson(sql);
  assert(
    project?.project_created === true && isUuid(project?.project_id),
    `Error Feed disposable project seed failed: ${JSON.stringify(project)}`,
  );
  return project;
}

async function hardDeleteDisposableErrorFeedProject(project) {
  assert(
    isUuid(project?.project_id),
    "Error Feed disposable project cleanup requires project_id.",
  );
  const sql = `
WITH requested AS (
  SELECT ${sqlUuid(project.project_id)} AS project_id
),
deleted_project AS (
  DELETE FROM tracer_project project
  USING requested r
  WHERE project.id = r.project_id
  RETURNING project.id
)
SELECT json_build_object(
  'deleted_project_count', (SELECT count(*) FROM deleted_project),
  'remaining_project_count', CASE
    WHEN (SELECT count(*) FROM deleted_project) > 0 THEN 0
    ELSE (
      SELECT count(*)
      FROM tracer_project project
      JOIN requested r ON project.id = r.project_id
    )
  END
);
`;
  return runPostgresJson(sql);
}

async function seedDisposableErrorFeedFixture({
  organizationId,
  workspaceId,
  projectId,
  runId,
}) {
  assert(
    isUuid(organizationId),
    "organizationId must be a UUID for Error Feed fixture.",
  );
  assert(
    isUuid(workspaceId),
    "workspaceId must be a UUID for Error Feed fixture.",
  );
  assert(isUuid(projectId), "projectId must be a UUID for Error Feed fixture.");

  const traceId = randomUUID();
  const analysisId = randomUUID();
  const detailId = randomUUID();
  const groupId = randomUUID();
  const scanResultId = randomUUID();
  const scanIssueId = randomUUID();
  const membershipId = randomUUID();
  const clusterId = `EF${randomUUID().replaceAll("-", "").slice(0, 16)}`;
  const suffix = journeySafeId(runId);
  const title = `api journey error feed ${suffix} ${clusterId}`;
  const rootCause =
    "Tool output was not checked before the final response, producing a stale answer.";
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(projectId)} AS project_id
),
project_row AS (
  SELECT project.id, project.organization_id, project.workspace_id
  FROM tracer_project project
  JOIN requested r ON project.id = r.project_id
  WHERE project.deleted = false
    AND project.organization_id = r.organization_id
    AND project.workspace_id = r.workspace_id
),
inserted_trace AS (
  INSERT INTO tracer_trace (
    id,
    created_at,
    updated_at,
    deleted,
    project_id,
    name,
    metadata,
    input,
    output,
    error,
    tags,
    error_analysis_status
  )
  SELECT
    ${sqlUuid(traceId)},
    NOW() - INTERVAL '2 minutes',
    NOW(),
    false,
    project_row.id,
    ${sqlString(`${title} trace`)},
    ${sqlString(JSON.stringify({ source: "api-journey", run_id: runId }))}::jsonb,
    ${sqlString(
      JSON.stringify({ prompt: "Need shipping status for order A-100" }),
    )}::jsonb,
    ${sqlString(
      JSON.stringify({ response: "The order shipped yesterday." }),
    )}::jsonb,
    ${sqlString(
      JSON.stringify({ name: "ToolValidationError", message: title }),
    )}::jsonb,
    ${sqlString(JSON.stringify(["api-journey", "error-feed"]))}::jsonb,
    'completed'
  FROM project_row
  RETURNING id, project_id
),
inserted_analysis AS (
  INSERT INTO tracer_trace_error_analysis (
    id,
    created_at,
    updated_at,
    deleted,
    trace_id,
    project_id,
    analysis_date,
    agent_version,
    memory_enhanced,
    overall_score,
    total_errors,
    high_impact_errors,
    medium_impact_errors,
    low_impact_errors,
    recommended_priority,
    insights,
    memory_context,
    grouped_errors_count
  )
  SELECT
    ${sqlUuid(analysisId)},
    NOW() - INTERVAL '90 seconds',
    NOW(),
    false,
    inserted_trace.id,
    inserted_trace.project_id,
    NOW() - INTERVAL '90 seconds',
    'api-journey',
    false,
    0.25,
    1,
    1,
    0,
    0,
    'HIGH',
    ${sqlString("Disposable Error Feed fixture for API journey coverage.")},
    '{}'::jsonb,
    1
  FROM inserted_trace
  RETURNING id, trace_id, project_id
),
inserted_group AS (
  INSERT INTO tracer_trace_error_group (
    id,
    created_at,
    updated_at,
    deleted,
    project_id,
    source,
    issue_group,
    issue_category,
    fix_layer,
    title,
    status,
    cluster_id,
    error_type,
    total_events,
    unique_traces,
    unique_users,
    first_seen,
    last_seen,
    error_ids,
    combined_impact,
    combined_description,
    error_count,
    trace_impact,
    priority
  )
  SELECT
    ${sqlUuid(groupId)},
    NOW() - INTERVAL '80 seconds',
    NOW(),
    false,
    inserted_trace.project_id,
    'scanner',
    'Tool Failures',
    'Language-only',
    'Tools',
    ${sqlString(title)},
    'escalating',
    ${sqlString(clusterId)},
    'Tool Failures > Language-only > Missing tool validation',
    1,
    1,
    0,
    NOW() - INTERVAL '80 seconds',
    NOW(),
    ${sqlString(JSON.stringify(["E001"]))}::jsonb,
    'HIGH',
    ${sqlString(
      "The assistant answered from stale state after the tool failed.",
    )},
    1,
    ${sqlString("High user impact because the final answer is incorrect.")},
    'high'
  FROM inserted_trace
  RETURNING id, cluster_id, project_id
),
inserted_detail AS (
  INSERT INTO tracer_trace_error_detail (
    id,
    created_at,
    updated_at,
    deleted,
    analysis_id,
    error_id,
    cluster_id,
    category,
    impact,
    urgency_to_fix,
    location_spans,
    evidence_snippets,
    description,
    root_causes,
    recommendation,
    immediate_fix,
    trace_impact,
    trace_assessment,
    llm_analysis,
    memory_enhanced
  )
  SELECT
    ${sqlUuid(detailId)},
    NOW() - INTERVAL '70 seconds',
    NOW(),
    false,
    inserted_analysis.id,
    'E001',
    ${sqlString(clusterId)},
    'Tool Failures > Language-only > Missing tool validation',
    'HIGH',
    'IMMEDIATE',
    '[]'::jsonb,
    ${sqlString(
      JSON.stringify([
        "The final response used stale shipping data after the tool error.",
      ]),
    )}::jsonb,
    ${sqlString("Tool result failure was not handled before responding.")},
    ${sqlString(JSON.stringify([rootCause]))}::jsonb,
    ${sqlString("Check the tool status and retry or surface a bounded error.")},
    ${sqlString("Add a guard that blocks final answers after tool failure.")},
    ${sqlString("Incorrect customer-facing shipment status.")},
    ${sqlString("The trace should be treated as failed.")},
    ${sqlString("Synthetic local fixture for Error Feed root-cause readback.")},
    false
  FROM inserted_analysis
  RETURNING id
),
inserted_scan_result AS (
  INSERT INTO tracer_trace_scan_result (
    id,
    created_at,
    updated_at,
    deleted,
    trace_id,
    project_id,
    status,
    has_issues,
    key_moments,
    meta,
    scan_version
  )
  SELECT
    ${sqlUuid(scanResultId)},
    NOW() - INTERVAL '65 seconds',
    NOW(),
    false,
    inserted_trace.id,
    inserted_trace.project_id,
    'completed',
    true,
    ${sqlString(
      JSON.stringify([
        {
          kevinified: "Tool failed but answer continued.",
          verbatim: "tool_status=failed; final_answer=shipped",
        },
      ]),
    )}::jsonb,
    ${sqlString(
      JSON.stringify({
        turn_count: 2,
        tools_available: ["shipping.lookup"],
        tools_called: [],
      }),
    )}::jsonb,
    'api-journey'
  FROM inserted_trace
  RETURNING id, trace_id
),
inserted_scan_issue AS (
  INSERT INTO tracer_trace_scan_issue (
    id,
    created_at,
    updated_at,
    deleted,
    scan_result_id,
    category,
    "group",
    fix_layer,
    confidence,
    brief,
    cluster_id
  )
  SELECT
    ${sqlUuid(scanIssueId)},
    NOW() - INTERVAL '60 seconds',
    NOW(),
    false,
    inserted_scan_result.id,
    'Language-only',
    'Tool Failures',
    'Tools',
    'H',
    ${sqlString("The trace skipped a required shipping.lookup tool check.")},
    inserted_group.id
  FROM inserted_scan_result
  CROSS JOIN inserted_group
  RETURNING id
),
inserted_membership AS (
  INSERT INTO tracer_error_cluster_traces (
    id,
    created_at,
    updated_at,
    deleted,
    trace_id,
    span_id,
    cluster_id,
    scan_issue_id,
    eval_logger_id
  )
  SELECT
    ${sqlUuid(membershipId)},
    NOW() - INTERVAL '55 seconds',
    NOW(),
    false,
    inserted_trace.id,
    NULL,
    inserted_group.id,
    inserted_scan_issue.id,
    NULL
  FROM inserted_trace
  CROSS JOIN inserted_group
  CROSS JOIN inserted_scan_issue
  RETURNING id
)
SELECT json_build_object(
  'project_visible', EXISTS (SELECT 1 FROM project_row),
  'project_id', (SELECT project_id::text FROM requested),
  'trace_id', (SELECT id::text FROM inserted_trace),
  'analysis_id', (SELECT id::text FROM inserted_analysis),
  'detail_id', (SELECT id::text FROM inserted_detail),
  'group_id', (SELECT id::text FROM inserted_group),
  'cluster_id', (SELECT cluster_id FROM inserted_group),
  'scan_result_id', (SELECT id::text FROM inserted_scan_result),
  'scan_issue_id', (SELECT id::text FROM inserted_scan_issue),
  'membership_id', (SELECT id::text FROM inserted_membership)
);
`;
  const fixture = await runPostgresJson(sql);
  assert(
    fixture?.project_visible === true && fixture?.cluster_id === clusterId,
    `Error Feed fixture seed failed: ${JSON.stringify(fixture)}`,
  );
  return fixture;
}

async function hardDeleteDisposableErrorFeedFixture(fixture) {
  const ids = {
    traceId: fixture?.trace_id,
    analysisId: fixture?.analysis_id,
    detailId: fixture?.detail_id,
    groupId: fixture?.group_id,
    scanResultId: fixture?.scan_result_id,
    scanIssueId: fixture?.scan_issue_id,
    membershipId: fixture?.membership_id,
  };
  for (const [name, value] of Object.entries(ids)) {
    assert(isUuid(value), `Error Feed cleanup requires ${name}.`);
  }

  const deleteSql = `
WITH requested AS (
  SELECT
    ${sqlUuid(ids.traceId)} AS trace_id,
    ${sqlUuid(ids.analysisId)} AS analysis_id,
    ${sqlUuid(ids.detailId)} AS detail_id,
    ${sqlUuid(ids.groupId)} AS group_id,
    ${sqlUuid(ids.scanResultId)} AS scan_result_id,
    ${sqlUuid(ids.scanIssueId)} AS scan_issue_id,
    ${sqlUuid(ids.membershipId)} AS membership_id
),
deleted_membership AS (
  DELETE FROM tracer_error_cluster_traces membership
  USING requested r
  WHERE membership.id = r.membership_id
  RETURNING membership.id
),
deleted_scan_issue AS (
  DELETE FROM tracer_trace_scan_issue issue
  USING requested r
  WHERE issue.id = r.scan_issue_id
  RETURNING issue.id
),
deleted_scan_result AS (
  DELETE FROM tracer_trace_scan_result scan_result
  USING requested r
  WHERE scan_result.id = r.scan_result_id
  RETURNING scan_result.id
),
deleted_detail AS (
  DELETE FROM tracer_trace_error_detail detail
  USING requested r
  WHERE detail.id = r.detail_id
  RETURNING detail.id
),
deleted_analysis AS (
  DELETE FROM tracer_trace_error_analysis analysis
  USING requested r
  WHERE analysis.id = r.analysis_id
  RETURNING analysis.id
),
deleted_group AS (
  DELETE FROM tracer_trace_error_group groups
  USING requested r
  WHERE groups.id = r.group_id
  RETURNING groups.id
),
deleted_trace AS (
  DELETE FROM tracer_trace trace
  USING requested r
  WHERE trace.id = r.trace_id
  RETURNING trace.id
)
SELECT json_build_object(
  'deleted_membership_count', (SELECT count(*) FROM deleted_membership),
  'deleted_scan_issue_count', (SELECT count(*) FROM deleted_scan_issue),
  'deleted_scan_result_count', (SELECT count(*) FROM deleted_scan_result),
  'deleted_detail_count', (SELECT count(*) FROM deleted_detail),
  'deleted_analysis_count', (SELECT count(*) FROM deleted_analysis),
  'deleted_group_count', (SELECT count(*) FROM deleted_group),
  'deleted_trace_count', (SELECT count(*) FROM deleted_trace)
);
`;
  const deleted = await runPostgresJson(deleteSql);
  const auditSql = `
WITH requested AS (
  SELECT
    ${sqlUuid(ids.traceId)} AS trace_id,
    ${sqlUuid(ids.analysisId)} AS analysis_id,
    ${sqlUuid(ids.detailId)} AS detail_id,
    ${sqlUuid(ids.groupId)} AS group_id,
    ${sqlUuid(ids.scanResultId)} AS scan_result_id,
    ${sqlUuid(ids.scanIssueId)} AS scan_issue_id,
    ${sqlUuid(ids.membershipId)} AS membership_id
)
SELECT json_build_object(
  'remaining_trace_count', (
    SELECT count(*) FROM tracer_trace trace, requested r WHERE trace.id = r.trace_id
  ),
  'remaining_analysis_count', (
    SELECT count(*) FROM tracer_trace_error_analysis analysis, requested r WHERE analysis.id = r.analysis_id
  ),
  'remaining_detail_count', (
    SELECT count(*) FROM tracer_trace_error_detail detail, requested r WHERE detail.id = r.detail_id
  ),
  'remaining_group_count', (
    SELECT count(*) FROM tracer_trace_error_group groups, requested r WHERE groups.id = r.group_id
  ),
  'remaining_scan_result_count', (
    SELECT count(*) FROM tracer_trace_scan_result scan_result, requested r WHERE scan_result.id = r.scan_result_id
  ),
  'remaining_scan_issue_count', (
    SELECT count(*) FROM tracer_trace_scan_issue issue, requested r WHERE issue.id = r.scan_issue_id
  ),
  'remaining_membership_count', (
    SELECT count(*) FROM tracer_error_cluster_traces membership, requested r WHERE membership.id = r.membership_id
  )
);
`;
  return { ...deleted, ...(await runPostgresJson(auditSql)) };
}

function assertErrorFeedFixtureCleanup(cleanupAudit) {
  assert(
    Number(cleanupAudit.remaining_trace_count) === 0 &&
      Number(cleanupAudit.remaining_analysis_count) === 0 &&
      Number(cleanupAudit.remaining_detail_count) === 0 &&
      Number(cleanupAudit.remaining_group_count) === 0 &&
      Number(cleanupAudit.remaining_scan_result_count) === 0 &&
      Number(cleanupAudit.remaining_scan_issue_count) === 0 &&
      Number(cleanupAudit.remaining_membership_count) === 0,
    `Error Feed fixture cleanup left rows behind: ${JSON.stringify(
      cleanupAudit,
    )}`,
  );
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
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlString(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function journeySafeId(value) {
  return String(value || Date.now().toString(36)).replace(
    /[^a-zA-Z0-9_-]/g,
    "_",
  );
}

async function resolveErrorFeedProject({
  client,
  cleanup,
  organizationId,
  workspaceId,
  runId,
  evidence,
}) {
  if (process.env.ERROR_FEED_PROJECT_ID) {
    assert(
      isUuid(process.env.ERROR_FEED_PROJECT_ID),
      "ERROR_FEED_PROJECT_ID must be a UUID.",
    );
    evidence.push({
      endpoint: "error feed project env",
      project_id: process.env.ERROR_FEED_PROJECT_ID,
    });
    return { id: process.env.ERROR_FEED_PROJECT_ID };
  }

  const payload = await client.get(apiPath("/tracer/project/list_projects/"), {
    query: { page_number: 0, page_size: 100 },
  });
  const projects = asArray(payload).filter(
    (candidate) => isUuid(candidate?.id) || isUuid(candidate?.project_id),
  );
  const project =
    projects.find((candidate) => candidate.trace_type === "observe") ||
    projects[0];
  if (project) {
    const projectId = project.id || project.project_id;
    evidence.push({
      endpoint: "error feed project list",
      project_id: projectId,
      trace_type: project.trace_type || null,
    });
    return { ...project, id: projectId };
  }

  const disposableProject = await seedDisposableErrorFeedProject({
    organizationId,
    workspaceId,
    runId,
  });
  cleanup.defer("hard delete Error Feed disposable project", async () => {
    const cleanupAudit =
      await hardDeleteDisposableErrorFeedProject(disposableProject);
    assert(
      Number(cleanupAudit.remaining_project_count) === 0,
      `Error Feed disposable project cleanup left rows behind: ${JSON.stringify(
        cleanupAudit,
      )}`,
    );
  });
  evidence.push({
    error_feed_project_source: "seeded-db-project",
    project_id: disposableProject.project_id,
    project_name: disposableProject.project_name,
  });
  return {
    id: disposableProject.project_id,
    name: disposableProject.project_name,
  };
}

export async function installRuntimeConfig(page, auth) {
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
    await Promise.all([
      page.waitForResponse(predicate, { timeout: 60000 }),
      action(),
    ]);
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
}

async function selectComboboxOption(page, currentText, optionText) {
  await page.waitForSelector('[role="combobox"]', { timeout: 30000 });
  const combos = await page.$$('[role="combobox"]');
  let combo = null;
  for (const candidate of combos) {
    const { text, visible } = await candidate.evaluate((el) => {
      const style = window.getComputedStyle(el);
      return {
        text: (el.textContent || "").trim(),
        visible:
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          el.getClientRects().length > 0,
      };
    });
    if (!visible) continue;
    if (text === currentText) {
      combo = candidate;
      break;
    }
  }
  assert(combo, `Could not find combobox ${currentText}.`);
  await combo.click();
  await page.waitForFunction(
    (targetText) =>
      Array.from(document.querySelectorAll('[role="option"]')).some(
        (candidate) => {
          const style = window.getComputedStyle(candidate);
          return (
            style.visibility !== "hidden" &&
            style.display !== "none" &&
            candidate.getClientRects().length > 0 &&
            (candidate.textContent || "").trim() === targetText
          );
        },
      ),
    { timeout: 30000 },
    optionText,
  );
  const clicked = await page.evaluate((targetText) => {
    const options = Array.from(
      document.querySelectorAll('[role="option"]'),
    ).filter((candidate) => {
      const style = window.getComputedStyle(candidate);
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        candidate.getClientRects().length > 0
      );
    });
    const option = options.find(
      (candidate) => (candidate.textContent || "").trim() === targetText,
    );
    if (!option) return false;
    option.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    option.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    option.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return true;
  }, optionText);
  assert(clicked, `Could not select option ${optionText}.`);
}

async function clickVisibleRowText(page, text) {
  await page.waitForFunction(
    (targetText) =>
      Array.from(document.querySelectorAll("tr")).some((row) =>
        row.textContent.includes(targetText),
      ),
    { timeout: 30000 },
    text,
  );
  await page.evaluate((targetText) => {
    const row = Array.from(document.querySelectorAll("tr")).find((candidate) =>
      candidate.textContent.includes(targetText),
    );
    row?.dispatchEvent(
      new MouseEvent("click", { bubbles: true, cancelable: true }),
    );
  }, text);
}

async function expectVisibleText(page, text, { exact = false } = {}) {
  await page.waitForFunction(
    ({ targetText, exactMatch }) =>
      Array.from(document.querySelectorAll("body *")).some((el) => {
        const style = window.getComputedStyle(el);
        const visible =
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          el.getClientRects().length > 0;
        if (!visible) return false;
        const value = (el.textContent || "").trim();
        return exactMatch ? value === targetText : value.includes(targetText);
      }),
    { timeout: 30000 },
    { targetText: text, exactMatch: exact },
  );
}

async function expectNoVisibleText(page, text) {
  const found = await page.evaluate((targetText) => {
    return Array.from(document.querySelectorAll("body *")).some((el) => {
      const style = window.getComputedStyle(el);
      const visible =
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        el.getClientRects().length > 0;
      return visible && (el.textContent || "").includes(targetText);
    });
  }, text);
  assert(!found, `Unexpected visible text: ${text}`);
}

async function parseStatsResponse(response) {
  const payload = await response.json();
  const stats = payload?.result || payload?.data?.result || payload?.data || {};
  return {
    totalErrors: Number(stats.totalErrors ?? stats.total_errors ?? 0),
    escalating: Number(stats.escalating ?? 0),
    acknowledged: Number(stats.acknowledged ?? 0),
    forReview: Number(stats.forReview ?? stats.for_review ?? 0),
    resolved: Number(stats.resolved ?? 0),
    affectedUsers: Number(stats.affectedUsers ?? stats.affected_users ?? 0),
  };
}

async function expectErrorFeedStat(page, key, expectedValue) {
  await page.waitForFunction(
    ({ statKey, value }) => {
      const el = document.querySelector(
        `[data-testid="error-feed-stat-${statKey}"]`,
      );
      if (!el) return false;
      const text = (el.textContent || "").replaceAll(",", "");
      return text.includes(String(value));
    },
    { timeout: 30000 },
    { statKey: key, value: Number(expectedValue) },
  );
}

function isErrorFeedListUrl(url) {
  return stripQuery(url).endsWith("/tracer/feed/issues/");
}

function isErrorFeedStatsUrl(url) {
  return stripQuery(url).endsWith("/tracer/feed/issues/stats/");
}

function stripQuery(url) {
  return String(url || "").split("?")[0];
}

function isErrorFeedApiUrl(url) {
  return (
    url.includes("/tracer/feed/") ||
    url.includes("/tracer/trace-error-analysis/")
  );
}

function severityLabel(value) {
  return String(value || "")
    .replace(/^./, (char) => char.toUpperCase())
    .trim();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function browserExecutablePath() {
  return (
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  );
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
