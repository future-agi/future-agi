/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { createRequire } from "node:module";
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
const SCREENSHOT_PATH =
  process.env.ANNOTATION_QUEUE_METRICS_SCREENSHOT ||
  "/tmp/annotation-queue-metrics-panels-smoke.png";
const FAILURE_SCREENSHOT_PATH = SCREENSHOT_PATH.replace(
  /\.png$/,
  "-failure.png",
);
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  const auth = await createAuthenticatedContext();
  const apiFailures = [];
  const pageErrors = [];
  const modelHubRequests = [];
  const browserMutations = [];
  let browser = null;
  let page = null;
  let caughtError = null;

  try {
    const selection = await selectMetricsQueue(auth);
    const expected = buildExpectedUiValues(selection);

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });
    page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installBrowserState(page, auth);

    page.on("request", (request) => {
      if (!isAnnotationQueueApiUrl(request.url())) return;
      modelHubRequests.push(`${request.method()} ${request.url()}`);
      if (MUTATION_METHODS.has(request.method())) {
        browserMutations.push(`${request.method()} ${request.url()}`);
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isAnnotationQueueApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponseDuring(
      page,
      "annotation queue progress load",
      (response) =>
        isQueueMetricResponse(response, selection.queue.id, "progress"),
      () =>
        page.goto(
          `${APP_BASE}/dashboard/annotations/queues/${selection.queue.id}`,
          {
            waitUntil: "domcontentloaded",
          },
        ),
    );
    await waitForPathIncludes(
      page,
      `/dashboard/annotations/queues/${selection.queue.id}`,
    );
    await waitForVisibleText(page, selection.queue.name, { exact: true });
    await waitForVisibleText(page, expected.overallProgressText);
    await waitForVisibleText(page, expected.progressPctText, { exact: true });

    await waitForResponseDuring(
      page,
      "annotation queue analytics tab",
      (response) =>
        isQueueMetricResponse(response, selection.queue.id, "analytics"),
      () => clickTab(page, "Analytics"),
    );
    await waitForVisibleText(page, "Total Items", { exact: true });
    await waitForVisibleText(page, "Completed", { exact: true });
    await waitForVisibleText(page, "Completion Rate", { exact: true });
    await waitForVisibleText(page, expected.analyticsTotalText, {
      exact: true,
    });
    await waitForVisibleText(page, expected.analyticsCompletedText, {
      exact: true,
    });
    await waitForVisibleText(page, expected.completionRateText, {
      exact: true,
    });
    await waitForVisibleText(page, "Status Breakdown", { exact: true });
    if (selection.dbAudit.label_count > 0) {
      await waitForVisibleText(page, "Label Distribution", { exact: true });
    }
    if (asArray(selection.analytics.annotator_performance).length > 0) {
      await waitForVisibleText(page, "Annotator Performance", { exact: true });
    }

    await waitForResponseDuring(
      page,
      "annotation queue agreement tab",
      (response) =>
        isQueueMetricResponse(response, selection.queue.id, "agreement"),
      () => clickTab(page, "Agreement"),
    );
    await waitForVisibleText(page, "Overall Agreement", { exact: true });
    await waitForVisibleText(page, expected.agreementPctText, { exact: true });
    if (Object.keys(selection.agreement.labels || {}).length > 0) {
      await waitForVisibleText(page, "Per-Label Agreement", { exact: true });
    }
    if (asArray(selection.agreement.annotator_pairs).length > 0) {
      await waitForVisibleText(page, "Annotator Pair Agreement", {
        exact: true,
      });
    }

    const pageText = await page.evaluate(() => document.body.innerText || "");
    assert(
      !/(Invalid Date|undefined|null)/.test(pageText),
      "Metrics page rendered invalid placeholder text.",
    );

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      browserMutations.length === 0,
      `Unexpected browser mutations: ${browserMutations
        .map(maskRequest)
        .join(", ")}`,
    );
    assert(
      apiFailures.length === 0,
      `Unexpected annotation queue API failures: ${apiFailures.join(", ")}`,
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
          queue_id: selection.queue.id,
          queue_name: selection.queue.name,
          progress: {
            total: selection.progress.total,
            completed: selection.progress.completed,
            skipped: selection.progress.skipped,
            progress_pct: selection.progress.progress_pct,
          },
          analytics: {
            total: selection.analytics.total,
            status_breakdown: selection.analytics.status_breakdown,
            label_count: Object.keys(
              selection.analytics.label_distribution || {},
            ).length,
            annotator_count: asArray(selection.analytics.annotator_performance)
              .length,
          },
          agreement: {
            overall_agreement: selection.agreement.overall_agreement,
            label_count: Object.keys(selection.agreement.labels || {}).length,
            pair_count: asArray(selection.agreement.annotator_pairs).length,
          },
          db_audit: selection.dbAudit,
          model_hub_request_count: modelHubRequests.length,
          browser_mutations: browserMutations.map(maskRequest),
          screenshot: SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    caughtError = error;
    const domDebug = page
      ? await page
          .evaluate(() => ({
            url: window.location.href,
            text: document.body?.innerText?.slice(0, 2500) || "",
          }))
          .catch(() => null)
      : null;
    if (page) {
      await page
        .screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .catch(() => null);
    }
    console.error(
      JSON.stringify(
        {
          status: "failed",
          error: error.message,
          dom: domDebug,
          api_failures: apiFailures.map(maskRequest),
          browser_mutations: browserMutations.map(maskRequest),
          screenshot: FAILURE_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
  } finally {
    if (browser) await browser.close();
  }

  if (caughtError) throw caughtError;
}

async function selectMetricsQueue(auth) {
  const configuredQueueId = process.env.ANNOTATION_METRICS_QUEUE_ID;
  const candidates = configuredQueueId
    ? [
        {
          queue_id: configuredQueueId,
          queue_name: "configured",
          active_items: null,
          active_scores: null,
          comparable_item_labels: null,
        },
      ]
    : await loadMetricQueueCandidatesDb({
        organizationId: auth.organizationId,
        workspaceId: auth.workspaceId,
      });
  assert(
    candidates.length > 0,
    "No local annotation queue metrics candidates were found.",
  );

  const failures = [];
  for (const candidate of candidates) {
    try {
      const queue = await auth.client.get(
        apiPath("/model-hub/annotation-queues/{id}/", {
          id: candidate.queue_id,
        }),
      );
      const [progress, analytics, agreement, dbAudit] = await Promise.all([
        auth.client.get(
          apiPath("/model-hub/annotation-queues/{id}/progress/", {
            id: queue.id,
          }),
        ),
        auth.client.get(
          apiPath("/model-hub/annotation-queues/{id}/analytics/", {
            id: queue.id,
          }),
        ),
        auth.client.get(
          apiPath("/model-hub/annotation-queues/{id}/agreement/", {
            id: queue.id,
          }),
        ),
        loadMetricQueueDbAudit(queue.id),
      ]);
      assert(
        Number(progress.total || 0) > 0,
        `Metrics candidate ${queue.id} has no progress rows.`,
      );
      assert(
        Number(analytics.total || 0) === Number(progress.total || 0),
        `Metrics candidate ${queue.id} analytics/progress totals disagree.`,
      );
      assert(
        Object.keys(agreement.labels || {}).length > 0,
        `Metrics candidate ${queue.id} agreement returned no labels.`,
      );
      return {
        candidate,
        queue,
        progress,
        analytics,
        agreement,
        dbAudit,
      };
    } catch (error) {
      failures.push({
        queue_id: candidate.queue_id,
        error: error.message,
        status: error.status || null,
      });
    }
  }

  throw new Error(
    `No accessible queue rendered all metrics endpoints: ${JSON.stringify(
      failures,
    )}`,
  );
}

function buildExpectedUiValues({ progress, analytics, agreement }) {
  const completed = Number(progress.completed || 0);
  const total = Number(progress.total || 0);
  const pending = Number(progress.pending || 0);
  const inProgress = Number(progress.in_progress || 0);
  const inReview = Number(progress.in_review || 0);
  const skipped = Number(progress.skipped || 0);
  const parts = [`Overall: ${completed}/${total} completed`];
  if (pending > 0) parts.push(`${pending} pending`);
  if (inProgress > 0) parts.push(`${inProgress} in progress`);
  if (inReview > 0) parts.push(`${inReview} in review`);
  if (skipped > 0) parts.push(`${skipped} skipped`);
  const statusBreakdown = analytics.status_breakdown || {};
  const completionRate = total
    ? Math.round((Number(statusBreakdown.completed || 0) / total) * 100)
    : 0;
  return {
    overallProgressText: parts.join(" · "),
    progressPctText: `${progress.progress_pct ?? 0}%`,
    analyticsTotalText: String(total),
    analyticsCompletedText: String(statusBreakdown.completed || 0),
    completionRateText: `${completionRate}%`,
    agreementPctText:
      agreement.overall_agreement === null ||
      agreement.overall_agreement === undefined
        ? "N/A"
        : `${(Number(agreement.overall_agreement) * 100).toFixed(1)}%`,
  };
}

async function loadMetricQueueCandidatesDb({ organizationId, workspaceId }) {
  const sql = `
WITH score_counts AS (
  SELECT
    qi.queue_id,
    COUNT(s.id) AS active_scores,
    COUNT(DISTINCT s.annotator_id) FILTER (WHERE s.annotator_id IS NOT NULL) AS annotators
  FROM model_hub_queueitem qi
  JOIN model_hub_score s ON s.queue_item_id = qi.id
  WHERE qi.deleted = FALSE
    AND s.deleted = FALSE
  GROUP BY qi.queue_id
),
comparable AS (
  SELECT
    queue_id,
    COUNT(*) AS comparable_item_labels
  FROM (
    SELECT qi.queue_id, s.queue_item_id, s.label_id
    FROM model_hub_score s
    JOIN model_hub_queueitem qi ON qi.id = s.queue_item_id
    WHERE s.deleted = FALSE
      AND qi.deleted = FALSE
    GROUP BY qi.queue_id, s.queue_item_id, s.label_id
    HAVING COUNT(*) >= 2
  ) grouped
  GROUP BY queue_id
),
ranked AS (
  SELECT
    q.id::text AS queue_id,
    q.name AS queue_name,
    COUNT(DISTINCT qi.id) FILTER (WHERE qi.deleted = FALSE) AS active_items,
    COALESCE(sc.active_scores, 0) AS active_scores,
    COALESCE(sc.annotators, 0) AS annotators,
    COALESCE(c.comparable_item_labels, 0) AS comparable_item_labels
  FROM model_hub_annotationqueue q
  LEFT JOIN model_hub_queueitem qi ON qi.queue_id = q.id
  LEFT JOIN score_counts sc ON sc.queue_id = q.id
  LEFT JOIN comparable c ON c.queue_id = q.id
  WHERE q.deleted = FALSE
    AND q.status = 'active'
    AND q.organization_id = ${sqlUuid(organizationId)}
    AND q.workspace_id = ${sqlUuid(workspaceId)}
  GROUP BY q.id, q.name, sc.active_scores, sc.annotators, c.comparable_item_labels
  HAVING COUNT(DISTINCT qi.id) FILTER (WHERE qi.deleted = FALSE) > 0
  ORDER BY
    COALESCE(c.comparable_item_labels, 0) DESC,
    COALESCE(sc.active_scores, 0) DESC,
    COUNT(DISTINCT qi.id) FILTER (WHERE qi.deleted = FALSE) DESC
  LIMIT 10
)
SELECT COALESCE(json_agg(row_to_json(ranked)), '[]'::json)::text FROM ranked;
`;
  return asArray(await runPostgresJson(sql));
}

async function loadMetricQueueDbAudit(queueId) {
  const sql = `
WITH items AS (
  SELECT id, status, review_status
  FROM model_hub_queueitem
  WHERE queue_id = ${sqlUuid(queueId)}
    AND deleted = FALSE
),
scores AS (
  SELECT s.id, s.queue_item_id, s.label_id, s.annotator_id
  FROM model_hub_score s
  JOIN items ON items.id = s.queue_item_id
  WHERE s.deleted = FALSE
),
comparable AS (
  SELECT COUNT(*) AS comparable_item_labels
  FROM (
    SELECT queue_item_id, label_id
    FROM scores
    GROUP BY queue_item_id, label_id
    HAVING COUNT(*) >= 2
  ) grouped
)
SELECT json_build_object(
  'active_item_count', (SELECT COUNT(*) FROM items),
  'active_score_count', (SELECT COUNT(*) FROM scores),
  'label_count', (SELECT COUNT(DISTINCT label_id) FROM scores),
  'annotator_count', (SELECT COUNT(DISTINCT annotator_id) FROM scores WHERE annotator_id IS NOT NULL),
  'comparable_item_labels', (SELECT comparable_item_labels FROM comparable),
  'pending_item_count', (SELECT COUNT(*) FROM items WHERE status = 'pending'),
  'completed_item_count', (SELECT COUNT(*) FROM items WHERE status = 'completed'),
  'in_review_item_count', (SELECT COUNT(*) FROM items WHERE review_status = 'pending_review'),
  'skipped_item_count', (SELECT COUNT(*) FROM items WHERE status = 'skipped')
);
`;
  return runPostgresJson(sql);
}

async function runPostgresJson(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
  const user = process.env.API_JOURNEY_DB_USER || "user";
  const database = process.env.API_JOURNEY_DB_NAME || "tfc";
  const { stdout } = await execFileAsync(
    "docker",
    ["exec", container, "psql", "-U", user, "-d", database, "-At", "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const text = stdout.trim();
  assert(text, "Postgres metrics audit returned no JSON output.");
  return JSON.parse(text);
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
    window.normalizeText = (value) =>
      String(value || "")
        .replace(/\s+/g, " ")
        .trim();
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
      if (user?.id) {
        sessionStorage.setItem("futureagi-current-user-id", user.id);
        sessionStorage.setItem("currentUserId", user.id);
      }
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

async function waitForPathIncludes(page, path, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname.includes(expectedPath),
    { timeout },
    path,
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

async function clickTab(page, label) {
  await waitForVisibleText(page, label, { exact: true });
  const clicked = await page.evaluate((expectedLabel) => {
    const tab = window
      .visibleElements('button[role="tab"]')
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedLabel &&
          !candidate.disabled,
      );
    if (!tab) return false;
    tab.click();
    return true;
  }, label);
  assert(clicked, `Could not click tab: ${label}`);
}

function isQueueMetricResponse(response, queueId, metricName) {
  if (response.request().method() !== "GET") return false;
  const url = new URL(response.url());
  if (!isAnnotationQueueApiUrl(response.url())) return false;
  if (response.status() >= 400) return false;
  return (
    url.pathname === `/model-hub/annotation-queues/${queueId}/${metricName}/`
  );
}

function isAnnotationQueueApiUrl(rawUrl) {
  const url = new URL(rawUrl);
  return (
    url.origin ===
      new URL(process.env.API_BASE || "http://localhost:8003").origin &&
    url.pathname.startsWith("/model-hub/annotation-queues/")
  );
}

function maskRequest(rawRequest) {
  const [method, rawUrl] = rawRequest.split(" ");
  const url = new URL(rawUrl);
  return `${method} ${url.pathname}`;
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
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
