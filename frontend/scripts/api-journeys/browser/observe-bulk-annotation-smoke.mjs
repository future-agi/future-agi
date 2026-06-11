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
  currentUserId,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFile = promisify(execFileCallback);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/observe-bulk-annotation-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/observe-bulk-annotation-smoke-failure.png";
const PROJECT_PREFIX = "TH-4812 observe bulk annotation";
const LABEL_PREFIX = "TH-4812 bulk annotation rating";
const NOTE_PREFIX = "TH-4812 bulk annotation note";
const SPAN_ID_PREFIX = "th4812_bulk_annotation_span_";

async function main() {
  const auth = await createAuthenticatedContext();
  const userId = currentUserId(auth.user);
  assert(isUuid(userId), "Authenticated user id did not resolve.");

  await hardDeleteObserveBulkAnnotationFixturesByPrefix();
  const fixture = await createObserveBulkAnnotationFixture(auth);
  let cleanupDone = false;
  let browser = null;
  let page = null;
  const apiFailures = [];
  const pageErrors = [];
  const evidence = {
    project_id: fixture.projectId,
    project_name: fixture.projectName,
    project_version_id: fixture.projectVersionId,
    trace_id: fixture.traceId,
    trace_name: fixture.traceName,
    span_id: fixture.spanId,
    span_name: fixture.spanName,
    label_id: fixture.labelId,
    label_name: fixture.labelName,
    score_id: fixture.scoreId,
    annotation_note: fixture.noteText,
    seed_audit: fixture.seedAudit,
    annotation_audit: fixture.annotationAudit,
  };

  try {
    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });
    page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installAuthenticatedState(page, auth, fixture.projectId);
    monitorPage(page, { apiFailures, pageErrors });

    const traceResponse = await waitForResponseDuring(
      page,
      "Observe trace detail",
      (response) =>
        response.url().includes(`/tracer/trace/${fixture.traceId}/`) &&
        response.status() < 400,
      () =>
        page.goto(
          `${APP_BASE}/dashboard/observe/${fixture.projectId}/trace/${fixture.traceId}`,
          { waitUntil: "domcontentloaded" },
        ),
    );
    evidence.trace_detail_status = traceResponse.status();

    await waitForPath(
      page,
      `/dashboard/observe/${fixture.projectId}/trace/${fixture.traceId}`,
    );
    await waitForVisibleText(page, "Trace ID :", { exact: true });
    await waitForVisibleText(page, fixture.traceId, { exact: true });
    await waitForVisibleText(page, fixture.spanId, { exact: true });
    await waitForVisibleText(page, "Trace Timeline", { exact: true });

    const scoresResponse = await waitForResponseDuring(
      page,
      "Observe annotation score readback",
      (response) =>
        response.url().includes("/model-hub/scores/for-source/") &&
        response
          .url()
          .includes(`source_id=${encodeURIComponent(fixture.spanId)}`) &&
        response.status() < 400,
      () => clickVisibleText(page, "Annotations"),
    );
    evidence.scores_for_source_status = scoresResponse.status();
    await waitForVisibleText(page, "Label", { exact: true });
    await waitForVisibleText(page, fixture.labelName, { exact: true });
    await waitForVisibleText(page, fixture.noteText, { exact: true });
    await waitForVisibleText(page, "Span Notes", { exact: true });

    const annotationRow = await visibleTableRowText(page, fixture.labelName);
    assert(
      annotationRow && annotationRow.includes("4"),
      `Annotation row did not show the bulk-created rating: ${annotationRow}`,
    );
    evidence.annotation_row_text = annotationRow;

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    const cleanupAudit = await cleanupObserveBulkAnnotationFixture(fixture);
    cleanupDone = true;
    evidence.cleanup = cleanupAudit;
    assertCleanupAudit(cleanupAudit);

    assert(
      apiFailures.length === 0,
      `Observe bulk annotation API failures: ${apiFailures.join("; ")}`,
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
      await cleanupObserveBulkAnnotationFixture(fixture).catch((error) => {
        console.error(`Cleanup failed: ${error.message}`);
      });
    }
  }
}

async function createObserveBulkAnnotationFixture(auth) {
  const safeSuffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const projectName = `${PROJECT_PREFIX} ${safeSuffix}`;
  const traceName = `TH-4812 bulk annotation trace ${safeSuffix}`;
  const spanId = `${SPAN_ID_PREFIX}${safeSuffix}`;
  const spanName = `TH-4812 bulk annotation span ${safeSuffix}`;
  const labelName = `${LABEL_PREFIX} ${safeSuffix}`;
  const noteText = `${NOTE_PREFIX} ${safeSuffix}`;

  const createdProject = await auth.client.post(apiPath("/tracer/project/"), {
    name: projectName,
    model_type: "GenerativeLLM",
    trace_type: "observe",
    metadata: { source: "browser-smoke", marker: "bulk-annotation" },
  });
  const projectId = createdProject.project_id || createdProject.id;
  assert(isUuid(projectId), "Observe project create returned no id.");

  const projectVersion = await auth.client.post(
    apiPath("/tracer/project-version/"),
    {
      project: projectId,
      name: `TH-4812 bulk annotation run ${safeSuffix}`,
      metadata: { source: "browser-smoke", marker: "bulk-annotation" },
    },
  );
  const projectVersionId =
    projectVersion.project_version_id || projectVersion.id;
  assert(
    isUuid(projectVersionId),
    "Observe project-version create returned no id.",
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
  assert(isUuid(traceId), "Observe trace create returned no id.");

  const now = Date.now();
  const spanPayload = observationSpanWritePayload({
    id: spanId,
    projectId,
    projectVersionId,
    traceId,
    name: spanName,
    runId: safeSuffix,
    startTime: new Date(now - 720).toISOString(),
    endTime: new Date(now).toISOString(),
  });
  const bulkSpanResult = await auth.client.post(
    apiPath("/tracer/observation-span/bulk_create/"),
    { observation_spans: [spanPayload] },
  );
  const createdSpanIds = asArray(bulkSpanResult?.["Observation Span IDs"]);
  assert(
    createdSpanIds.includes(spanId),
    "Observe span bulk_create did not include the disposable span.",
  );
  await seedObserveClickHouseSpans({
    organizationId: auth.organizationId,
    payloads: [spanPayload],
  });

  const createdLabel = await auth.client.post(
    apiPath("/model-hub/annotations-labels/"),
    {
      name: labelName,
      type: "star",
      description:
        "Temporary star label for Observe bulk annotation browser smoke.",
      project: projectId,
      settings: { no_of_stars: 5 },
      allow_notes: true,
    },
  );
  const labelId = createdLabel.id;
  assert(isUuid(labelId), "Annotation label create returned no id.");

  const bulkAnnotationResult = await auth.client.post(
    apiPath("/tracer/bulk-annotation/"),
    {
      records: [
        {
          observation_span_id: spanId,
          annotations: [{ annotation_label_id: labelId, value_float: 4 }],
          notes: [{ text: noteText }],
        },
      ],
    },
  );
  assert(
    bulkAnnotationResult?.annotations_created === 1 &&
      bulkAnnotationResult?.notes_created === 1 &&
      bulkAnnotationResult?.errors_count === 0,
    `Bulk annotation write returned unexpected counters: ${JSON.stringify(
      bulkAnnotationResult,
    )}`,
  );

  const userId = currentUserId(auth.user);
  const annotationValues = await auth.client.get(
    apiPath("/tracer/trace-annotation/get_annotation_values/"),
    {
      query: {
        observation_span_id: spanId,
        annotators: JSON.stringify([userId]),
      },
    },
  );
  const createdAnnotation = asArray(annotationValues?.annotations).find(
    (annotation) => annotation.annotation_label_id === labelId,
  );
  assert(
    createdAnnotation?.id,
    "Trace annotation values did not include the bulk-created score.",
  );
  assert(
    Number(createdAnnotation.annotation_value) === 4,
    "Trace annotation values returned the wrong rating.",
  );
  assert(
    asArray(annotationValues?.notes).some((note) => note.notes === noteText),
    "Trace annotation values did not return the span note.",
  );

  const scoreReadback = await auth.client.get(
    apiPath("/model-hub/scores/for-source/"),
    {
      query: { source_type: "observation_span", source_id: spanId },
      unwrap: false,
    },
  );
  const sourceScores = asArray(scoreReadback?.result || scoreReadback);
  assert(
    sourceScores.some(
      (score) =>
        score.id === createdAnnotation.id &&
        String(score.label_id || score.label?.id) === labelId &&
        Number(normalizeScoreValue(score.value)?.rating) === 4,
    ),
    "Scores for-source did not return the bulk-created score.",
  );
  assert(
    asArray(scoreReadback?.span_notes).some((note) => note.notes === noteText),
    "Scores for-source did not return the bulk-created span note.",
  );

  const seedAudit = await loadSeedDbAudit({
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
    projectId,
    projectVersionId,
    traceId,
    spanId,
  });
  assertSeedAudit(seedAudit, {
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
    projectId,
    projectVersionId,
    traceId,
    spanId,
  });

  const annotationAudit = await loadBulkAnnotationDbAudit({
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
    projectId,
    traceId,
    spanId,
    labelId,
    userId,
    noteText,
  });
  assertBulkAnnotationAudit(annotationAudit, {
    workspaceId: auth.workspaceId,
  });

  return {
    projectId,
    projectName,
    projectVersionId,
    traceId,
    traceName,
    spanId,
    spanName,
    labelId,
    labelName,
    scoreId: createdAnnotation.id,
    noteText,
    userId,
    seedAudit,
    annotationAudit,
  };
}

function traceWritePayload({ projectId, projectVersionId, name, runId }) {
  return {
    project: projectId,
    project_version: projectVersionId,
    name,
    metadata: { source: "browser-smoke", run_id: runId },
    input: { prompt: `bulk annotation trace input ${runId}` },
    output: { response: `bulk annotation trace output ${runId}` },
    error: null,
    tags: ["browser-smoke", "bulk-annotation"],
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
    input: { messages: [{ role: "user", content: "bulk annotation input" }] },
    output: {
      choices: [{ message: { content: "bulk annotation output" } }],
    },
    model: "bulk-annotation-model",
    provider: "futureagi",
    prompt_tokens: 11,
    completion_tokens: 13,
    total_tokens: 24,
    latency_ms: 720,
    cost: 0.024,
    status: "OK",
    status_message: "bulk annotation status",
    tags: ["browser-smoke", "bulk-annotation"],
    metadata: {
      source: "browser-smoke",
      marker: "bulk-annotation",
      run_id: runId,
    },
  };
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

async function installAuthenticatedState(page, auth, projectId) {
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user, traceViewKey }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      localStorage.setItem(
        traceViewKey,
        JSON.stringify({ viewMode: "timeline" }),
      );
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
      traceViewKey: `trace-view-default-${projectId}`,
    },
  );
}

function monitorPage(page, { apiFailures, pageErrors }) {
  page.on("response", (response) => {
    const url = response.url();
    if (isObservedLocalEndpoint(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) =>
    pageErrors.push(`${page.url()}: ${error.stack || error.message}`),
  );
}

function isObservedLocalEndpoint(url) {
  return [
    "/tracer/trace/",
    "/tracer/trace-annotation/",
    "/tracer/bulk-annotation/",
    "/tracer/observation-span/",
    "/model-hub/scores/",
  ].some((pathName) => url.includes(pathName));
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

async function waitForPath(page, pathname, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname === expectedPath,
    { timeout },
    pathname,
  );
}

async function clickVisibleText(page, text) {
  await waitForVisibleText(page, text, { exact: true });
  const clicked = await page.evaluate((expectedText) => {
    const element = visibleElements("button, [role='tab'], [role='button']")
      .filter(
        (candidate) => normalizeText(candidate.textContent) === expectedText,
      )
      .sort((a, b) => {
        const aRole = a.getAttribute("role") || "";
        const bRole = b.getAttribute("role") || "";
        return Number(bRole === "tab") - Number(aRole === "tab");
      })[0];
    if (!element) return false;
    element.dispatchEvent(
      new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
    );
    element.dispatchEvent(
      new MouseEvent("mouseup", { bubbles: true, cancelable: true }),
    );
    element.dispatchEvent(
      new MouseEvent("click", { bubbles: true, cancelable: true }),
    );
    return true;

    function normalizeText(value) {
      return String(value || "").trim();
    }
    function visibleElements(selector) {
      return Array.from(document.querySelectorAll(selector)).filter(
        (candidate) => {
          const style = window.getComputedStyle(candidate);
          const rect = candidate.getBoundingClientRect();
          return (
            style.visibility !== "hidden" &&
            style.display !== "none" &&
            rect.width > 0 &&
            rect.height > 0
          );
        },
      );
    }
  }, text);
  assert(clicked, `Could not click visible text ${text}.`);
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalizeText = (value) => String(value || "").trim();
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
        const textContent = normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

async function visibleTableRowText(page, needle) {
  return page.evaluate((expectedText) => {
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
    const row = Array.from(document.querySelectorAll("tr")).find(
      (candidate) =>
        isVisible(candidate) && candidate.textContent?.includes(expectedText),
    );
    return row?.innerText || row?.textContent || "";
  }, needle);
}

function normalizeScoreValue(value) {
  if (typeof value !== "string") return value || {};
  try {
    return JSON.parse(value);
  } catch {
    return {};
  }
}

async function loadSeedDbAudit({
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

function assertSeedAudit(
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

async function loadBulkAnnotationDbAudit({
  organizationId,
  workspaceId,
  projectId,
  traceId,
  spanId,
  labelId,
  userId,
  noteText,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuid(traceId)} AS trace_id,
    ${sqlString(spanId)} AS span_id,
    ${sqlUuid(labelId)} AS label_id,
    ${sqlUuid(userId)} AS user_id,
    ${sqlString(noteText)} AS note_text
),
score_rows AS (
  SELECT
    score.id::text AS id,
    score.observation_span_id,
    score.trace_id::text AS trace_id,
    score.label_id::text AS label_id,
    score.annotator_id::text AS annotator_id,
    score.organization_id::text AS organization_id,
    score.workspace_id::text AS workspace_id,
    score.value,
    score.deleted
  FROM model_hub_score score
  JOIN requested r ON score.observation_span_id = r.span_id
    AND score.label_id = r.label_id
    AND score.annotator_id = r.user_id
),
note_rows AS (
  SELECT
    note.id::text AS id,
    note.span_id,
    note.created_by_annotator,
    note.notes,
    note.deleted
  FROM tracer_spannotes note
  JOIN requested r ON note.span_id = r.span_id
    AND note.created_by_annotator = r.user_id::text
    AND note.notes = r.note_text
),
legacy_rows AS (
  SELECT annotation.id::text AS id
  FROM trace_annotation annotation
  JOIN requested r ON annotation.annotation_label_id = r.label_id
    AND (
      annotation.trace_id = r.trace_id
      OR annotation.observation_span_id = r.span_id
    )
)
SELECT json_build_object(
  'active_score_count', (
    SELECT count(*) FROM score_rows WHERE deleted = false
  ),
  'score_ids', COALESCE((
    SELECT json_agg(id ORDER BY id) FROM score_rows WHERE deleted = false
  ), '[]'::json),
  'score_workspace_ids', COALESCE((
    SELECT json_agg(workspace_id ORDER BY id) FROM score_rows WHERE deleted = false
  ), '[]'::json),
  'score_values', COALESCE((
    SELECT json_agg(value ORDER BY id) FROM score_rows WHERE deleted = false
  ), '[]'::json),
  'active_note_count', (
    SELECT count(*) FROM note_rows WHERE deleted = false
  ),
  'note_ids', COALESCE((
    SELECT json_agg(id ORDER BY id) FROM note_rows WHERE deleted = false
  ), '[]'::json),
  'legacy_trace_annotation_count', (SELECT count(*) FROM legacy_rows)
);
`;
  return runPostgresJson(sql);
}

function assertBulkAnnotationAudit(audit, { workspaceId }) {
  assert(
    Number(audit?.active_score_count) === 1,
    `Bulk annotation audit expected one active score: ${JSON.stringify(audit)}`,
  );
  assert(
    asArray(audit?.score_workspace_ids).every((id) => id === workspaceId),
    "Bulk annotation score did not retain the active workspace id.",
  );
  assert(
    asArray(audit?.score_values).some((value) => Number(value?.rating) === 4),
    "Bulk annotation DB audit did not see the created rating.",
  );
  assert(
    Number(audit?.active_note_count) === 1,
    `Bulk annotation audit expected one active note: ${JSON.stringify(audit)}`,
  );
  assert(
    Number(audit?.legacy_trace_annotation_count) === 0,
    "Bulk annotation should not create legacy trace_annotation rows.",
  );
}

async function cleanupObserveBulkAnnotationFixture(fixture) {
  await hardDeleteObserveClickHouseSpans({
    traceIds: [fixture.traceId],
    spanIds: [fixture.spanId],
  });
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(fixture.projectId)} AS project_id,
    ${sqlUuid(fixture.projectVersionId)} AS project_version_id,
    ${sqlUuid(fixture.traceId)} AS trace_id,
    ${sqlString(fixture.spanId)} AS span_id,
    ${sqlUuid(fixture.labelId)} AS label_id,
    ${sqlUuid(fixture.userId)} AS user_id,
    ${sqlString(fixture.noteText)} AS note_text
),
deleted_scores AS (
  DELETE FROM model_hub_score score
  USING requested r
  WHERE score.id = ${sqlUuid(fixture.scoreId)}
     OR (
       score.observation_span_id = r.span_id
       AND score.label_id = r.label_id
       AND score.annotator_id = r.user_id
     )
  RETURNING score.id
),
deleted_notes AS (
  DELETE FROM tracer_spannotes note
  USING requested r
  WHERE note.span_id = r.span_id
    AND note.created_by_annotator = r.user_id::text
    AND note.notes = r.note_text
  RETURNING note.id
),
deleted_legacy_annotations AS (
  DELETE FROM trace_annotation annotation
  USING requested r
  WHERE annotation.annotation_label_id = r.label_id
    AND (
      annotation.trace_id = r.trace_id
      OR annotation.observation_span_id = r.span_id
    )
  RETURNING annotation.id
),
deleted_label AS (
  DELETE FROM model_hub_annotationslabels label
  USING requested r
  WHERE label.id = r.label_id
  RETURNING label.id
),
deleted_spans AS (
  DELETE FROM tracer_observation_span span
  USING requested r
  WHERE span.id = r.span_id OR span.trace_id = r.trace_id
  RETURNING span.id
),
deleted_traces AS (
  DELETE FROM tracer_trace trace
  USING requested r
  WHERE trace.id = r.trace_id
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
  'deleted_score_count', (SELECT count(*) FROM deleted_scores),
  'deleted_note_count', (SELECT count(*) FROM deleted_notes),
  'deleted_legacy_trace_annotation_count', (SELECT count(*) FROM deleted_legacy_annotations),
  'deleted_label_count', (SELECT count(*) FROM deleted_label),
  'deleted_span_count', (SELECT count(*) FROM deleted_spans),
  'deleted_trace_count', (SELECT count(*) FROM deleted_traces),
  'deleted_project_version_count', (SELECT count(*) FROM deleted_project_versions),
  'deleted_project_count', (SELECT count(*) FROM deleted_projects),
  'remaining_score_count', CASE
    WHEN (SELECT count(*) FROM deleted_scores) > 0 THEN 0
    ELSE (
      SELECT count(*)
      FROM model_hub_score score, requested r
      WHERE score.id = ${sqlUuid(fixture.scoreId)}
         OR (
           score.observation_span_id = r.span_id
           AND score.label_id = r.label_id
           AND score.annotator_id = r.user_id
         )
    )
  END,
  'remaining_note_count', CASE
    WHEN (SELECT count(*) FROM deleted_notes) > 0 THEN 0
    ELSE (
      SELECT count(*)
      FROM tracer_spannotes note, requested r
      WHERE note.span_id = r.span_id
        AND note.created_by_annotator = r.user_id::text
        AND note.notes = r.note_text
    )
  END,
  'remaining_label_count', CASE
    WHEN (SELECT count(*) FROM deleted_label) > 0 THEN 0
    ELSE (
      SELECT count(*)
      FROM model_hub_annotationslabels label, requested r
      WHERE label.id = r.label_id
    )
  END,
  'remaining_span_count', CASE
    WHEN (SELECT count(*) FROM deleted_spans) > 0 THEN 0
    ELSE (
      SELECT count(*)
      FROM tracer_observation_span span, requested r
      WHERE span.id = r.span_id OR span.trace_id = r.trace_id
    )
  END,
  'remaining_trace_count', CASE
    WHEN (SELECT count(*) FROM deleted_traces) > 0 THEN 0
    ELSE (
      SELECT count(*)
      FROM tracer_trace trace, requested r
      WHERE trace.id = r.trace_id
    )
  END,
  'remaining_project_version_count', CASE
    WHEN (SELECT count(*) FROM deleted_project_versions) > 0 THEN 0
    ELSE (
      SELECT count(*)
      FROM tracer_project_version pv, requested r
      WHERE pv.id = r.project_version_id
    )
  END,
  'remaining_project_count', CASE
    WHEN (SELECT count(*) FROM deleted_projects) > 0 THEN 0
    ELSE (
      SELECT count(*)
      FROM tracer_project project, requested r
      WHERE project.id = r.project_id
    )
  END
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteObserveBulkAnnotationFixturesByPrefix() {
  await hardDeleteObserveClickHouseSpansByPrefix(SPAN_ID_PREFIX);
  const sql = `
WITH target_projects AS (
  SELECT project.id
  FROM tracer_project project
  WHERE project.name LIKE ${sqlString(`${PROJECT_PREFIX}%`)}
),
target_traces AS (
  SELECT trace.id
  FROM tracer_trace trace
  WHERE trace.project_id IN (SELECT id FROM target_projects)
),
target_spans AS (
  SELECT span.id
  FROM tracer_observation_span span
  WHERE span.project_id IN (SELECT id FROM target_projects)
     OR span.id LIKE ${sqlString(`${SPAN_ID_PREFIX}%`)}
),
target_labels AS (
  SELECT label.id
  FROM model_hub_annotationslabels label
  WHERE label.name LIKE ${sqlString(`${LABEL_PREFIX}%`)}
),
deleted_scores AS (
  DELETE FROM model_hub_score score
  WHERE score.label_id IN (SELECT id FROM target_labels)
     OR score.observation_span_id IN (SELECT id FROM target_spans)
  RETURNING score.id
),
deleted_notes AS (
  DELETE FROM tracer_spannotes note
  WHERE note.span_id IN (SELECT id FROM target_spans)
     OR note.notes LIKE ${sqlString(`${NOTE_PREFIX}%`)}
  RETURNING note.id
),
deleted_legacy_annotations AS (
  DELETE FROM trace_annotation annotation
  WHERE annotation.annotation_label_id IN (SELECT id FROM target_labels)
     OR annotation.observation_span_id IN (SELECT id FROM target_spans)
     OR annotation.trace_id IN (SELECT id FROM target_traces)
  RETURNING annotation.id
),
deleted_labels AS (
  DELETE FROM model_hub_annotationslabels label
  WHERE label.id IN (SELECT id FROM target_labels)
  RETURNING label.id
),
deleted_spans AS (
  DELETE FROM tracer_observation_span span
  WHERE span.id IN (SELECT id FROM target_spans)
     OR span.trace_id IN (SELECT id FROM target_traces)
  RETURNING span.id
),
deleted_traces AS (
  DELETE FROM tracer_trace trace
  WHERE trace.id IN (SELECT id FROM target_traces)
  RETURNING trace.id
),
deleted_project_versions AS (
  DELETE FROM tracer_project_version pv
  WHERE pv.project_id IN (SELECT id FROM target_projects)
  RETURNING pv.id
),
deleted_projects AS (
  DELETE FROM tracer_project project
  WHERE project.id IN (SELECT id FROM target_projects)
  RETURNING project.id
)
SELECT json_build_object(
  'deleted_score_count', (SELECT count(*) FROM deleted_scores),
  'deleted_note_count', (SELECT count(*) FROM deleted_notes),
  'deleted_legacy_trace_annotation_count', (SELECT count(*) FROM deleted_legacy_annotations),
  'deleted_label_count', (SELECT count(*) FROM deleted_labels),
  'deleted_span_count', (SELECT count(*) FROM deleted_spans),
  'deleted_trace_count', (SELECT count(*) FROM deleted_traces),
  'deleted_project_version_count', (SELECT count(*) FROM deleted_project_versions),
  'deleted_project_count', (SELECT count(*) FROM deleted_projects)
);
`;
  return runPostgresJson(sql);
}

function assertCleanupAudit(audit) {
  for (const key of [
    "remaining_score_count",
    "remaining_note_count",
    "remaining_label_count",
    "remaining_span_count",
    "remaining_trace_count",
    "remaining_project_version_count",
    "remaining_project_count",
  ]) {
    assert(Number(audit?.[key]) === 0, `Cleanup left ${key}: ${audit?.[key]}.`);
  }
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
  const line = stdout.trim().split(/\r?\n/).find(Boolean);
  assert(line, "Postgres DB audit returned no JSON output.");
  return JSON.parse(line);
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
  await runClickHouseDelete(conditions.join(" OR "), { bestEffort });
}

async function hardDeleteObserveClickHouseSpansByPrefix(spanIdPrefix) {
  await runClickHouseDelete(`id LIKE ${chString(`${spanIdPrefix}%`)}`, {
    bestEffort: true,
  });
}

async function runClickHouseDelete(whereClause, { bestEffort = true } = {}) {
  const sql = `SET mutations_sync = 2; ALTER TABLE spans DELETE WHERE ${whereClause}`;
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
  await execFile(
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
      await execFile("docker", ["inspect", "--type", "container", candidate]);
      return candidate;
    } catch {
      // Try the next local compose naming convention.
    }
  }
  return "ws2-clickhouse";
}

function chString(value) {
  return `'${String(value ?? "")
    .replaceAll("\\", "\\\\")
    .replaceAll("'", "\\'")}'`;
}

function chNullableUuid(value) {
  return value ? `toUUID(${chString(value)})` : "NULL";
}

function chJson(value) {
  return chString(JSON.stringify(value ?? {}));
}

function chJsonString(value) {
  return chString(JSON.stringify(value ?? {}));
}

function sqlUuid(value) {
  assert(isUuid(value), `Expected UUID value, got ${value}.`);
  return `'${value}'::uuid`;
}

function sqlString(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
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
  process.exitCode = 1;
});
