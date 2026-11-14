import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  CleanupStack,
  createAuthenticatedContext,
} from "../lib/api-client.mjs";
import { queuePath } from "../lib/fixtures.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/observe-annotations-feedback-drawer-smoke.png";
const QUEUE_SCREENSHOT_PATH = "/tmp/observe-annotation-row-new-tab-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/observe-annotations-feedback-drawer-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const cleanup = new CleanupStack();
  const cleanupEvidence = [];
  const runId = auth.runId;
  const sample = await resolveObserveDrawerSample(auth.client);
  const labelName = `OBS-007 drawer smoke ${runId}`;
  const annotationText = `OBS-007 browser annotation ${runId}`;
  const spanNote = `OBS-007 browser span note ${runId}`;
  const expectedScoreValue = { text: annotationText };
  const apiFailures = [];
  const pageErrors = [];
  const observedFeedbackActions = [];
  let browser;
  let context;
  let caughtError = null;
  let cleanupFailures = [];

  const evidence = {
    project_id: sample.project.id,
    project_name: sample.project.name || null,
    trace_id: sample.traceId,
    span_id: sample.spanId,
    custom_eval_config_id: sample.evalConfigId,
    eval_names: sample.evalNames,
  };

  try {
    const queue = await resolveDefaultQueue(auth.client, sample.project.id);
    evidence.queue_id = queue.id;

    const queueEntriesBefore = await getObservationSpanQueueEntries(
      auth.client,
      sample.spanId,
    );
    const defaultEntryBefore = queueEntriesBefore.find(
      (entry) => String(entry?.queue?.id) === String(queue.id),
    );
    const preexistingQueueItemId = defaultEntryBefore?.item?.id || null;

    const label = await createTextLabel(auth.client, labelName);
    evidence.label_id = label.id;
    cleanup.defer("delete OBS-007 browser temporary label", () =>
      ignoreNotFound(() =>
        auth.client.delete(
          apiPath("/model-hub/annotations-labels/{id}/", { id: label.id }),
          { okStatuses: [200, 204, 404] },
        ),
      ),
    );

    await auth.client.post(
      apiPath("/model-hub/annotation-queues/{id}/add-label/", { id: queue.id }),
      { label_id: label.id, required: false },
    );
    cleanup.defer("remove OBS-007 browser label from default queue", () =>
      ignoreNotFound(() =>
        auth.client.post(
          apiPath("/model-hub/annotation-queues/{id}/remove-label/", {
            id: queue.id,
          }),
          { label_id: label.id },
          { okStatuses: [200, 400, 404] },
        ),
      ),
    );

    const annotationResult = await auth.client.post(
      apiPath("/tracer/observation-span/add_annotations/"),
      {
        observation_span_id: sample.spanId,
        annotation_values: { [label.id]: annotationText },
        notes: spanNote,
      },
    );
    assert(
      asArray(annotationResult?.failed_labels).length === 0,
      `Legacy add_annotations returned failures: ${JSON.stringify(
        annotationResult?.failed_labels,
      )}`,
    );

    const sourceScores = asArray(
      await auth.client.get(apiPath("/model-hub/scores/for-source/"), {
        query: { source_type: "observation_span", source_id: sample.spanId },
      }),
    );
    const createdScore = sourceScores.find(
      (score) =>
        String(scoreLabelId(score)) === String(label.id) &&
        valuesEqual(score.value, expectedScoreValue),
    );
    assert(
      createdScore?.id,
      "Scores for-source did not include browser annotation.",
    );
    const queueItemId =
      createdScore.queue_item?.id ||
      createdScore.queue_item ||
      createdScore.queue_item_id;
    assert(
      queueItemId,
      "Browser annotation score did not attach to a queue item.",
    );
    evidence.score_id = createdScore.id;
    evidence.queue_item_id = queueItemId;

    if (!preexistingQueueItemId) {
      cleanup.defer("delete OBS-007 browser temporary default queue item", () =>
        ignoreNotFound(() =>
          auth.client.delete(
            queuePath(
              "/model-hub/annotation-queues/{queue_id}/items/{id}/",
              queue.id,
              { id: queueItemId },
            ),
            { okStatuses: [200, 204, 404] },
          ),
        ),
      );
    }
    cleanup.defer("delete OBS-007 browser score and span note", async () => {
      await auth.client.post(
        apiPath("/model-hub/scores/bulk/"),
        {
          source_type: "observation_span",
          source_id: sample.spanId,
          queue_item_id: queueItemId,
          scores: [
            { label_id: label.id, value: expectedScoreValue, notes: "" },
          ],
          span_notes: "",
          span_notes_source_id: sample.spanId,
        },
        { okStatuses: [200, 400, 404] },
      );
      await ignoreNotFound(() =>
        auth.client.delete(
          apiPath("/model-hub/scores/{id}/", { id: createdScore.id }),
          { okStatuses: [200, 204, 404] },
        ),
      );
    });

    const feedbackValue = "0.42";
    const feedbackExplanation = `OBS-007 browser feedback explanation ${runId}`;
    const feedbackImprovement = `OBS-007 browser feedback improvement ${runId}`;
    const createdFeedback = await auth.client.post(
      apiPath("/tracer/observation-span/submit_feedback/"),
      {
        observation_span_id: sample.spanId,
        custom_eval_config_id: sample.evalConfigId,
        feedback_value: feedbackValue,
        feedback_explanation: feedbackExplanation,
        feedback_improvement: feedbackImprovement,
      },
    );
    const feedbackId = createdFeedback?.feedback_id || createdFeedback?.id;
    assert(feedbackId, "submit_feedback did not return feedback_id.");
    evidence.feedback_id = feedbackId;
    cleanup.defer("delete OBS-007 browser feedback", () =>
      ignoreNotFound(() =>
        auth.client.delete(
          apiPath("/model-hub/feedback/{id}/", { id: feedbackId }),
          {
            okStatuses: [200, 204, 404],
          },
        ),
      ),
    );

    await auth.client.post(
      apiPath("/tracer/observation-span/submit_feedback_action_type/"),
      {
        observation_span_id: sample.spanId,
        custom_eval_config_id: sample.evalConfigId,
        feedback_id: feedbackId,
        action_type: "retune",
      },
    );
    observedFeedbackActions.push({
      route: "/tracer/observation-span/submit_feedback_action_type/",
      feedback_id_present: true,
    });

    browser = await chromium.launch({
      channel: process.env.PLAYWRIGHT_CHANNEL || "chrome",
      headless: process.env.HEADLESS !== "0",
    });
    context = await browser.newContext({
      viewport: { width: 1440, height: 950 },
    });
    await context.addInitScript(
      ({ tokens, organizationId, workspaceId, user }) => {
        try {
          localStorage.setItem("accessToken", tokens.access);
          localStorage.setItem("refreshToken", tokens.refresh || "");
          localStorage.setItem("rememberMe", "true");
          localStorage.setItem("initial-render", "done");
          if (organizationId)
            sessionStorage.setItem("organizationId", organizationId);
          if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
          if (user?.id)
            sessionStorage.setItem("futureagi-current-user-id", user.id);
        } catch {
          // Some browser-created transient documents disallow storage access.
        }
      },
      {
        tokens: auth.tokens,
        organizationId: auth.organizationId,
        workspaceId: auth.workspaceId,
        user: auth.user,
      },
    );

    const page = await context.newPage();
    context.on("page", (nextPage) =>
      monitorPage(nextPage, apiFailures, pageErrors),
    );
    monitorPage(page, apiFailures, pageErrors);

    const url = buildDrawerUrl(sample.project.id, sample.traceId);
    const traceResponse = page.waitForResponse(
      (response) =>
        response.url().includes(`/tracer/trace/${sample.traceId}/`) &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(url, { waitUntil: "domcontentloaded" });
    await traceResponse;
    await page.getByText(sample.spanId, { exact: true }).first().waitFor({
      state: "visible",
      timeout: 60000,
    });

    await page.getByRole("tab", { name: "Evals" }).click();
    await page.getByText("Evaluation metric", { exact: true }).waitFor({
      state: "visible",
      timeout: 30000,
    });
    for (const evalName of sample.evalNames.slice(0, 2)) {
      await page.getByText(evalName, { exact: true }).first().waitFor({
        state: "visible",
        timeout: 30000,
      });
    }

    await page.evaluate(() => {
      if (window.__obsOriginalOpen) return;
      window.__obsOpenedUrls = [];
      window.__obsOriginalOpen = window.open;
      window.open = (...args) => {
        window.__obsOpenedUrls.push(args);
        return window.__obsOriginalOpen.apply(window, args);
      };
    });
    const firstEvalName = sample.evalNames[0];
    const evalNameLocator = page
      .getByText(firstEvalName, { exact: true })
      .first();
    const evalRowState = await evalNameLocator.evaluate((node) => {
      const row = node.closest('[role="row"], tr') || node;
      return {
        cursor: getComputedStyle(row).cursor,
        text: row.textContent || node.textContent || "",
      };
    });
    const evalPageCountBeforeClick = context.pages().length;
    await evalNameLocator.click();
    await page.waitForTimeout(750);
    const evalOpenedUrls = await page.evaluate(() => window.__obsOpenedUrls);
    assert(
      context.pages().length === evalPageCountBeforeClick,
      `Eval row opened a new tab unexpectedly: ${JSON.stringify(evalRowState)}`,
    );
    assert(
      evalOpenedUrls.length === 0,
      `Eval row called window.open unexpectedly: ${JSON.stringify(
        evalOpenedUrls,
      )}`,
    );
    evidence.eval_row_no_queue_tab = {
      eval_name: firstEvalName,
      row_cursor: evalRowState.cursor,
      opened_url_count: evalOpenedUrls.length,
    };

    const addFeedbackCount = await page
      .getByText("Add Feedback", {
        exact: true,
      })
      .count();
    evidence.current_drawer_add_feedback_controls = addFeedbackCount;

    await page.getByRole("tab", { name: "Annotations" }).click();
    await page
      .getByRole("columnheader", { name: "Label", exact: true })
      .waitFor({
        state: "visible",
        timeout: 30000,
      });
    await page.getByText(labelName, { exact: true }).first().waitFor({
      state: "visible",
      timeout: 30000,
    });
    await page.getByText(annotationText).first().waitFor({
      state: "visible",
      timeout: 30000,
    });
    await page.getByText(spanNote).first().waitFor({
      state: "visible",
      timeout: 30000,
    });

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    const annotationTable = page.locator("table").filter({
      has: page.getByRole("columnheader", { name: "Label", exact: true }),
    });
    const annotationRow = annotationTable
      .getByRole("row")
      .filter({ hasText: labelName })
      .first();
    const annotationRowState = await annotationRow.evaluate((row) => ({
      cursor: getComputedStyle(row).cursor,
      text: row.innerText,
    }));
    assert(
      annotationRowState.cursor === "pointer",
      `Annotation row is not linkable: ${JSON.stringify(annotationRowState)}`,
    );
    const rowPopup = context.waitForEvent("page", {
      predicate: (openedPage) => openedPage !== page,
      timeout: 30000,
    });
    const queueDetailResponse = context.waitForEvent("response", {
      predicate: (response) =>
        response
          .url()
          .includes(
            `/model-hub/annotation-queues/${queue.id}/items/${queueItemId}/annotate-detail/`,
          ) && response.status() < 400,
      timeout: 60000,
    });
    const queueTraceResponse = context.waitForEvent("response", {
      predicate: (response) =>
        response.url().includes(`/tracer/trace/${sample.traceId}/`) &&
        response.status() < 400,
      timeout: 60000,
    });
    let queuePage;
    let queueDetail;
    let queueTraceDetail;
    try {
      [queuePage, queueDetail, queueTraceDetail] = await Promise.all([
        rowPopup,
        queueDetailResponse,
        queueTraceResponse,
        annotationRow.click(),
      ]).then(([popup, detail, traceDetail]) => [popup, detail, traceDetail]);
    } catch (error) {
      const openedUrls = await page.evaluate(() => window.__obsOpenedUrls);
      throw new Error(
        `Annotation row click did not open queue item; row=${JSON.stringify(
          annotationRowState,
        )}; openedUrls=${JSON.stringify(openedUrls)}; cause=${error.message}`,
      );
    }
    await queuePage.waitForLoadState("domcontentloaded");
    await queuePage.waitForURL(
      (currentUrl) =>
        currentUrl.pathname ===
          `/dashboard/annotations/queues/${queue.id}/annotate` &&
        currentUrl.searchParams.get("itemId") === String(queueItemId),
      { timeout: 30000 },
    );
    await queuePage.getByText(labelName, { exact: true }).first().waitFor({
      state: "visible",
      timeout: 60000,
    });
    await queuePage.getByText(sample.spanId, { exact: true }).first().waitFor({
      state: "visible",
      timeout: 60000,
    });
    await queuePage.waitForFunction(
      ({ expectedAnnotationText, expectedSpanNote }) => {
        const values = [...document.querySelectorAll("input, textarea")].map(
          (element) => element.value,
        );
        return (
          values.includes(expectedAnnotationText) &&
          values.includes(expectedSpanNote)
        );
      },
      {
        expectedAnnotationText: annotationText,
        expectedSpanNote: spanNote,
      },
      { timeout: 30000 },
    );
    await queuePage.screenshot({ path: QUEUE_SCREENSHOT_PATH, fullPage: true });
    evidence.annotation_row_new_tab = {
      expected_queue_id: queue.id,
      expected_queue_item_id: String(queueItemId),
      actual_url: queuePage.url(),
      annotate_detail_status: queueDetail.status(),
      trace_detail_status: queueTraceDetail.status(),
      source_context_visible: true,
      screenshot: QUEUE_SCREENSHOT_PATH,
    };
    evidence.feedback_action_routes = observedFeedbackActions;

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
        },
        null,
        2,
      ),
    );
  } catch (error) {
    if (context) {
      const pages = context.pages();
      await pages[0]
        ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .catch(() => null);
      await Promise.all(
        pages.slice(1).map((openPage, index) =>
          openPage
            .screenshot({
              path: `/tmp/observe-annotations-feedback-drawer-smoke-failure-page-${index + 1}.png`,
              fullPage: true,
            })
            .catch(() => null),
        ),
      );
    }
    caughtError = error;
  } finally {
    cleanupFailures = await cleanup.run(cleanupEvidence);
    if (cleanupEvidence.length > 0) {
      console.error(JSON.stringify({ cleanup: cleanupEvidence }, null, 2));
    }
    if (context) await context.close();
    if (browser) await browser.close();
  }
  if (cleanupFailures.length > 0) {
    throw new Error(
      `Cleanup failures: ${cleanupFailures
        .map((failure) => `${failure.label}: ${failure.error}`)
        .join("; ")}`,
    );
  }
  if (caughtError) {
    throw caughtError;
  }
}

async function resolveObserveDrawerSample(client) {
  const preferredProjectId =
    process.env.OBSERVE_FEEDBACK_PROJECT_ID || process.env.OBSERVE_PROJECT_ID;
  const preferredTraceId = process.env.OBSERVE_FEEDBACK_TRACE_ID;
  const preferredSpanId = process.env.OBSERVE_FEEDBACK_SPAN_ID;
  const preferredEvalConfigId = process.env.OBSERVE_FEEDBACK_EVAL_CONFIG_ID;

  if (preferredProjectId && preferredTraceId) {
    return sampleFromTraceDetail(client, {
      project: { id: preferredProjectId, name: "env observe drawer project" },
      traceId: preferredTraceId,
      spanId: preferredSpanId,
      evalConfigId: preferredEvalConfigId,
    });
  }

  const knownProjectId = "2e512463-65ef-461f-a5f5-db4b7bc0b90a";
  const knownTraceId = "267e0be7-9b57-46d8-9044-c8525dc42c89";
  try {
    return await sampleFromTraceDetail(client, {
      project: { id: knownProjectId, name: "test-google-adk" },
      traceId: knownTraceId,
    });
  } catch {
    // Fall through to workspace discovery below.
  }

  const projects = asArray(
    await client.get(apiPath("/tracer/project/list_projects/"), {
      query: { page_number: 0, page_size: 50 },
    }),
  );
  for (const project of projects) {
    if (!project?.id) continue;
    const list = await client.get(
      apiPath("/tracer/trace/list_traces_of_session/"),
      {
        query: {
          project_id: project.id,
          page_number: 0,
          page_size: 25,
          filters: JSON.stringify([sixMonthCreatedAtFilter()]),
        },
      },
    );
    const traces = asArray(list.table || list);
    for (const trace of traces) {
      const traceId = trace.trace_id || trace.id;
      if (!traceId) continue;
      try {
        return await sampleFromTraceDetail(client, { project, traceId });
      } catch {
        // Keep searching until we find a trace with evals on a selectable span.
      }
    }
  }

  throw new Error("No observe trace with evals was found for browser smoke.");
}

async function sampleFromTraceDetail(
  client,
  { project, traceId, spanId, evalConfigId },
) {
  const detail = await client.get(
    apiPath("/tracer/trace/{id}/", { id: traceId }),
  );
  const rootEntries = asArray(detail?.observation_spans).length
    ? asArray(detail.observation_spans)
    : [detail?.root || detail?.data || detail?.trace || detail];
  const rows = rootEntries.flatMap((entry) => flattenTraceEntries(entry));
  for (const row of rows) {
    const currentSpanId = row.spanId;
    if (spanId && String(currentSpanId) !== String(spanId)) continue;
    const evalRows = asArray(row.entry?.eval_scores);
    const selectedEval =
      evalRows.find(
        (item) =>
          !evalConfigId ||
          String(
            item.eval_config_id || item.custom_eval_config_id || item.id,
          ) === String(evalConfigId),
      ) || null;
    if (!currentSpanId || !selectedEval) continue;
    const selectedEvalId =
      selectedEval.eval_config_id ||
      selectedEval.custom_eval_config_id ||
      selectedEval.id;
    if (!selectedEvalId) continue;
    return {
      project,
      traceId,
      spanId: currentSpanId,
      evalConfigId: selectedEvalId,
      evalNames: uniqueNonEmpty(
        evalRows.map(
          (item) => item.eval_name || item.name || item.eval_config_id,
        ),
      ),
    };
  }
  throw new Error(`Trace ${traceId} did not include a span with eval_scores.`);
}

function flattenTraceEntries(rootEntry) {
  const rows = [];
  function walk(entry) {
    if (!entry || typeof entry !== "object") return;
    const span = entry.observation_span || entry.span || entry;
    const spanId = span?.id || span?.span_id;
    rows.push({ entry, spanId });
    for (const child of asArray(entry.children)) walk(child);
  }
  walk(rootEntry);
  return rows;
}

async function resolveDefaultQueue(client, projectId) {
  const queues = asArray(
    await client.get(apiPath("/model-hub/annotation-queues/"), {
      query: { limit: 100 },
    }),
  );
  const existing = queues.find(
    (queue) => queue?.is_default && String(queue.project) === String(projectId),
  );
  if (existing?.id) return existing;

  const payload = await client.post(
    apiPath("/model-hub/annotation-queues/get-or-create-default/"),
    { project_id: projectId },
  );
  return payload?.queue || payload;
}

async function createTextLabel(client, labelName) {
  const created = await client.post(apiPath("/model-hub/annotations-labels/"), {
    name: labelName,
    type: "text",
    description: "Temporary label for observe drawer browser smoke.",
    settings: {
      placeholder: "Observe drawer smoke",
      min_length: 0,
      max_length: 500,
    },
    allow_notes: true,
  });
  if (created?.id) return created;

  const rows = asArray(
    await client.get(apiPath("/model-hub/annotations-labels/"), {
      query: { search: labelName },
    }),
  );
  const found = rows.find((label) => label.name === labelName);
  assert(found?.id, "Temporary annotation label create did not return id.");
  return found;
}

async function getObservationSpanQueueEntries(client, spanId) {
  return asArray(
    await client.get(apiPath("/model-hub/annotation-queues/for-source/"), {
      query: { source_type: "observation_span", source_id: spanId },
    }),
  );
}

function buildDrawerUrl(projectId, traceId) {
  const params = new URLSearchParams();
  params.set("primaryTraceDateFilter", JSON.stringify(sixMonthDateFilter()));
  params.set("traceDetailDrawerOpen", JSON.stringify({ traceId, filters: [] }));
  return `${APP_BASE}/dashboard/observe/${projectId}/llm-tracing?${params}`;
}

function sixMonthDateFilter() {
  const start = new Date();
  start.setMonth(start.getMonth() - 6);
  const end = new Date();
  end.setDate(end.getDate() + 1);
  return {
    dateFilter: [toDateOnly(start), toDateOnly(end)],
    dateOption: "6M",
  };
}

function sixMonthCreatedAtFilter() {
  const range = sixMonthDateFilter();
  return {
    column_id: "created_at",
    filter_config: {
      filter_type: "datetime",
      filter_op: "between",
      filter_value: range.dateFilter,
    },
  };
}

function toDateOnly(date) {
  return date.toISOString().slice(0, 10);
}

function scoreLabelId(score) {
  return (
    score?.label_id ||
    score?.annotation_label_id ||
    score?.label?.id ||
    score?.label ||
    null
  );
}

function valuesEqual(left, right) {
  return JSON.stringify(normalizeJsonValue(left)) === JSON.stringify(right);
}

function normalizeJsonValue(value) {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function uniqueNonEmpty(values) {
  return [
    ...new Set(values.map((value) => String(value || "")).filter(Boolean)),
  ];
}

function isObservedLocalEndpoint(url) {
  return [
    "/tracer/trace/",
    "/tracer/observation-span/",
    "/model-hub/scores/",
    "/model-hub/annotation-queues/",
  ].some((pathName) => url.includes(pathName));
}

function monitorPage(page, apiFailures, pageErrors) {
  page.on("response", (response) => {
    const url = response.url();
    if (
      isObservedLocalEndpoint(url) &&
      response.status() >= 400 &&
      !url.includes("/tracer/dashboard/cost/")
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) =>
    pageErrors.push(`${page.url()}: ${error.stack || error.message}`),
  );
}

async function ignoreNotFound(fn) {
  try {
    return await fn();
  } catch (error) {
    const message = String(error?.message || "").toLowerCase();
    if (
      error?.status === 404 ||
      message.includes("not found") ||
      message.includes("not_found") ||
      message.includes("no annotationslabels matches") ||
      message.includes("no feedback matches")
    ) {
      return null;
    }
    throw error;
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
