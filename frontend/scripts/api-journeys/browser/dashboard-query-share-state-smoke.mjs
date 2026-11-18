/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";

import {
  apiPath,
  assert,
  createAuthenticatedContext,
  isUuid,
} from "../lib/api-client.mjs";
import { resolveObserveProject } from "../lib/fixtures.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/dashboard-query-share-state-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/dashboard-query-share-state-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const evidence = [];
  const project = await resolveObserveProject(auth.client, evidence);
  const queryConfig = dashboardQueryConfig(project.id);
  const dashboardName = `QA dashboard query share ${auth.runId}`;
  const widgetName = `QA query share latency ${auth.runId}`;
  let dashboardId = null;
  let widgetId = null;
  let dashboardDeleted = false;
  let browser = null;
  let page = null;
  const apiFailures = [];
  const pageErrors = [];
  const queryPayloads = [];
  const queryResponses = [];

  const rootQueryResult = await auth.client.post(
    apiPath("/tracer/dashboard/query/"),
    queryConfig,
  );
  assertDashboardQueryResult(rootQueryResult, "root dashboard query");

  try {
    const createdDashboard = await auth.client.post(
      apiPath("/tracer/dashboard/"),
      {
        name: dashboardName,
        description: "Disposable dashboard for query/date/share browser smoke.",
      },
    );
    dashboardId = createdDashboard?.id;
    assert(isUuid(dashboardId), "Dashboard create did not return a UUID id.");

    const createdWidget = await auth.client.post(
      apiPath("/tracer/dashboard/{dashboard_pk}/widgets/", {
        dashboard_pk: dashboardId,
      }),
      {
        name: widgetName,
        description: "Temporary latency widget for query/share smoke.",
        position: 0,
        width: 12,
        height: 4,
        query_config: queryConfig,
        chart_config: { chart_type: "line" },
      },
    );
    widgetId = createdWidget?.id;
    assert(isUuid(widgetId), "Dashboard widget create did not return a UUID.");

    const widgetQueryProbe = await probeQueryEndpoint("widget query", () =>
      auth.client.post(
        apiPath("/tracer/dashboard/{dashboard_pk}/widgets/{id}/query/", {
          dashboard_pk: dashboardId,
          id: widgetId,
        }),
        {},
      ),
    );
    const previewProbe = await probeQueryEndpoint("widget preview", () =>
      auth.client.post(
        apiPath("/tracer/dashboard/{dashboard_pk}/widgets/preview/", {
          dashboard_pk: dashboardId,
        }),
        { query_config: queryConfig },
      ),
    );

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });
    await browser
      .defaultBrowserContext()
      .overridePermissions(APP_BASE, ["clipboard-read", "clipboard-write"])
      .catch(() => null);
    page = await browser.newPage();
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

        window.__dashboardClipboardWrites = [];
        const clipboard = {
          writeText: async (text) => {
            window.__dashboardClipboardWrites.push(String(text));
          },
          readText: async () =>
            window.__dashboardClipboardWrites[
              window.__dashboardClipboardWrites.length - 1
            ] || "",
        };
        Object.defineProperty(Navigator.prototype, "clipboard", {
          configurable: true,
          get: () => clipboard,
        });
      },
      {
        tokens: auth.tokens,
        organizationId: auth.organizationId,
        workspaceId: auth.workspaceId,
        user: auth.user,
      },
    );

    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        request.url().includes("/tracer/dashboard/query/")
      ) {
        queryPayloads.push(readJsonPostData(request));
      }
    });
    page.on("response", (response) => {
      const request = response.request();
      const url = response.url();
      if (
        request.method() === "POST" &&
        url.includes("/tracer/dashboard/query/")
      ) {
        queryResponses.push(response.status());
      }
      if (
        url.includes("/tracer/dashboard/") &&
        (url.includes(`/tracer/dashboard/${dashboardId}/`) ||
          url.endsWith("/tracer/dashboard/")) &&
        response.status() >= 400
      ) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponseDuring(
      page,
      "dashboard detail",
      (response) =>
        response.request().method() === "GET" &&
        response.url().includes(`/tracer/dashboard/${dashboardId}/`) &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/dashboards/${dashboardId}`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await expectVisibleText(page, dashboardName, { exact: true });
    await expectVisibleText(page, widgetName, { exact: true });
    await expectVisibleText(page, "7D", { exact: true });
    await expectVisibleText(page, "30D", { exact: true });

    await waitForCondition(
      () =>
        queryPayloads.some(
          (payload) =>
            hasProject(payload, project.id) &&
            payload?.time_range?.preset === "30D",
        ),
      "initial browser dashboard query payload",
    );
    await waitForCondition(
      () => queryResponses.some((status) => status >= 200 && status < 300),
      "successful browser dashboard query response",
    );

    const before7dCount = queryPayloads.length;
    await clickDateChip(page, "7D");
    await expectDateChipFilled(page, "7D");
    await waitForCondition(
      () =>
        queryPayloads
          .slice(before7dCount)
          .some((payload) => isSevenDayCustomPayload(payload, project.id)),
      "7D browser dashboard query refetch payload",
    );
    const sevenDayPayload = queryPayloads
      .slice(before7dCount)
      .find((payload) => isSevenDayCustomPayload(payload, project.id));

    await clickShareButton(page);
    const copiedUrl = await waitForClipboardWrite(page);
    assert(
      copiedUrl === `${APP_BASE}/dashboard/dashboards/${dashboardId}` ||
        copiedUrl.endsWith(`/dashboard/dashboards/${dashboardId}`),
      `Dashboard share copied unexpected URL: ${copiedUrl}`,
    );

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    await auth.client.delete(
      apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
    );
    dashboardDeleted = true;
    const deletedAudit = await loadDashboardDbAudit({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      dashboardId,
      widgetId,
    });
    assertDashboardDbAudit(deletedAudit, {
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      dashboardId,
      widgetId,
      projectId: project.id,
    });

    assert(
      apiFailures.length === 0,
      `Dashboard API failures: ${apiFailures.join("; ")}`,
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
          evidence: {
            dashboard_id: dashboardId,
            widget_id: widgetId,
            project_id: project.id,
            screenshot: SCREENSHOT_PATH,
            root_query: summarizeQueryResult(rootQueryResult),
            widget_query_probe: widgetQueryProbe,
            widget_preview_probe: previewProbe,
            browser_query_payload_count: queryPayloads.length,
            browser_query_response_statuses: queryResponses,
            seven_day_query_time_range: sevenDayPayload?.time_range || null,
            copied_url: copiedUrl,
            deleted_at_set: deletedAudit.dashboard_deleted_at_set,
            widget_deleted_at_set: deletedAudit.widget_deleted_at_set,
            active_widget_count: Number(deletedAudit.active_widget_count),
            observed_project_resolution: evidence,
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    if (page) {
      await captureFailureDiagnostics(
        page,
        error,
        queryPayloads,
        queryResponses,
      );
    }
    throw error;
  } finally {
    if (browser) await browser.close();
    if (!dashboardDeleted && dashboardId) {
      await auth.client
        .delete(apiPath("/tracer/dashboard/{id}/", { id: dashboardId }))
        .catch(() => null);
    }
  }
}

function dashboardQueryConfig(projectId) {
  assert(isUuid(projectId), "projectId must be a UUID.");
  return {
    workflow: "observability",
    project_ids: [projectId],
    time_range: { preset: "30D" },
    granularity: "day",
    metrics: [
      {
        id: "latency",
        name: "latency",
        display_name: "Latency",
        type: "system_metric",
        source: "traces",
        aggregation: "avg",
        unit: "ms",
      },
    ],
    filters: [],
    breakdowns: [],
  };
}

async function probeQueryEndpoint(label, fn) {
  try {
    const result = await fn();
    assertDashboardQueryResult(result, label);
    return {
      status: "passed",
      http_status: 200,
      result: summarizeQueryResult(result),
    };
  } catch (error) {
    if (isClickHouseDisabled(error)) {
      return {
        status: "clickhouse_disabled",
        http_status: error.status,
        message:
          error.body?.message ||
          error.body?.detail ||
          error.body?.error ||
          error.message,
      };
    }
    throw error;
  }
}

function assertDashboardQueryResult(result, label) {
  assert(result && typeof result === "object", `${label} returned no object.`);
  assert(Array.isArray(result.metrics), `${label} returned no metrics array.`);
  assert(result.metrics.length > 0, `${label} returned zero metrics.`);
  const metric = result.metrics.find((row) => row?.id === "latency");
  assert(metric, `${label} did not return the latency metric.`);
  assert(Array.isArray(metric.series), `${label} latency has no series.`);
  const pointCount = metric.series.reduce(
    (count, series) =>
      count + (Array.isArray(series?.data) ? series.data.length : 0),
    0,
  );
  assert(pointCount > 0, `${label} latency series had no points.`);
}

function summarizeQueryResult(result) {
  const metrics = Array.isArray(result?.metrics) ? result.metrics : [];
  return {
    metric_count: metrics.length,
    metric_ids: metrics.map((metric) => metric?.id).filter(Boolean),
    granularity: result?.granularity || null,
    time_range_start: result?.time_range?.start || null,
    time_range_end: result?.time_range?.end || null,
    series_count: metrics.reduce(
      (count, metric) =>
        count + (Array.isArray(metric?.series) ? metric.series.length : 0),
      0,
    ),
    point_count: metrics.reduce(
      (metricTotal, metric) =>
        metricTotal +
        (Array.isArray(metric?.series)
          ? metric.series.reduce(
              (seriesTotal, series) =>
                seriesTotal +
                (Array.isArray(series?.data) ? series.data.length : 0),
              0,
            )
          : 0),
      0,
    ),
  };
}

function isClickHouseDisabled(error) {
  const values = [
    error?.message,
    error?.body?.message,
    error?.body?.detail,
    error?.body?.error,
    error?.body?.result,
  ]
    .filter(Boolean)
    .map((value) => String(value).toLowerCase());
  return (
    error?.status === 400 &&
    values.some((value) => value.includes("clickhouse is not enabled"))
  );
}

async function installRuntimeConfig(page, auth) {
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = request.url();
    if (url.endsWith("/config.js") || url.endsWith("/runtime-config.js")) {
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
  const responsePromise = page.waitForResponse(predicate, { timeout: 45000 });
  await action();
  try {
    return await responsePromise;
  } catch (error) {
    throw new Error(`Timed out waiting for ${label}: ${error.message}`);
  }
}

function readJsonPostData(request) {
  const body = request.postData();
  if (!body) return null;
  try {
    return JSON.parse(body);
  } catch {
    return null;
  }
}

function hasProject(payload, projectId) {
  return (
    Array.isArray(payload?.project_ids) &&
    payload.project_ids.includes(projectId)
  );
}

function isSevenDayCustomPayload(payload, projectId) {
  if (!hasProject(payload, projectId)) return false;
  const range = payload?.time_range;
  if (!range || Object.prototype.hasOwnProperty.call(range, "preset")) {
    return false;
  }
  const start = Date.parse(range.custom_start || "");
  const end = Date.parse(range.custom_end || "");
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    return false;
  }
  const days = (end - start) / 86_400_000;
  return days > 6.8 && days < 7.2;
}

async function waitForCondition(predicate, label, timeout = 30000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeout) {
    if (predicate()) return;
    await delay(100);
  }
  throw new Error(`Timed out waiting for ${label}.`);
}

async function expectVisibleText(page, text, { exact = false } = {}) {
  await page.waitForFunction(
    ({ expected, exactMatch }) => {
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
        const value = element.textContent.trim();
        return exactMatch ? value === expected : value.includes(expected);
      });
    },
    { timeout: 30000 },
    { expected: text, exactMatch: exact },
  );
}

async function clickDateChip(page, label) {
  await page.waitForFunction(
    (expected) => {
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
      return Array.from(document.querySelectorAll(".MuiChip-root")).some(
        (chip) => isVisible(chip) && chip.textContent.trim() === expected,
      );
    },
    { timeout: 30000 },
    label,
  );
  const clicked = await page.evaluate((expected) => {
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
    const chip = Array.from(document.querySelectorAll(".MuiChip-root")).find(
      (candidate) =>
        isVisible(candidate) && candidate.textContent.trim() === expected,
    );
    if (!chip) return false;
    chip.click();
    return true;
  }, label);
  assert(clicked, `Unable to click date chip: ${label}`);
}

async function expectDateChipFilled(page, label) {
  await page.waitForFunction(
    (expected) => {
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
      return Array.from(document.querySelectorAll(".MuiChip-root")).some(
        (chip) =>
          isVisible(chip) &&
          chip.textContent.trim() === expected &&
          chip.classList.contains("MuiChip-filled"),
      );
    },
    { timeout: 10000 },
    label,
  );
}

async function clickShareButton(page) {
  const result = await page.evaluate(() => {
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
    const buttons = Array.from(document.querySelectorAll("button")).filter(
      isVisible,
    );
    const shareButton =
      buttons.find((button) =>
        button.outerHTML.toLowerCase().includes("share-variant"),
      ) ||
      buttons
        .map((button) => ({ button, rect: button.getBoundingClientRect() }))
        .filter(
          ({ rect }) => rect.top < 130 && rect.left > window.innerWidth / 2,
        )
        .sort((left, right) => left.rect.left - right.rect.left)[0]?.button;
    if (!shareButton) return null;
    const rect = shareButton.getBoundingClientRect();
    shareButton.click();
    return {
      text: shareButton.textContent.trim(),
      rect: {
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
      },
      html: shareButton.outerHTML.slice(0, 250),
    };
  });
  assert(result, "Unable to locate and click dashboard share button.");
  return result;
}

async function waitForClipboardWrite(page) {
  await page.waitForFunction(
    () => window.__dashboardClipboardWrites?.length > 0,
    { timeout: 10000 },
  );
  return page.evaluate(
    () =>
      window.__dashboardClipboardWrites[
        window.__dashboardClipboardWrites.length - 1
      ],
  );
}

async function captureFailureDiagnostics(
  page,
  error,
  queryPayloads,
  queryResponses,
) {
  const diagnostics = await page
    .evaluate(() => ({
      url: window.location.href,
      title: document.title,
      bodyText: document.body?.innerText?.slice(0, 1200) || "",
      clipboardWrites: window.__dashboardClipboardWrites || [],
      chips: Array.from(document.querySelectorAll(".MuiChip-root")).map(
        (chip) => ({
          text: chip.textContent.trim(),
          className: chip.className,
          rect: (() => {
            const { x, y, width, height } = chip.getBoundingClientRect();
            return { x, y, width, height };
          })(),
        }),
      ),
      iconButtons: Array.from(document.querySelectorAll("button"))
        .filter((button) => {
          const style = window.getComputedStyle(button);
          const rect = button.getBoundingClientRect();
          return (
            style.visibility !== "hidden" &&
            style.display !== "none" &&
            rect.width > 0 &&
            rect.height > 0
          );
        })
        .slice(0, 25)
        .map((button) => ({
          text: button.textContent.trim(),
          html: button.outerHTML.slice(0, 160),
          rect: (() => {
            const { x, y, width, height } = button.getBoundingClientRect();
            return { x, y, width, height };
          })(),
        })),
    }))
    .catch((diagnosticError) => ({
      diagnostic_error: diagnosticError.message,
    }));
  await page
    .screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
    .catch(() => null);
  console.error(
    JSON.stringify(
      {
        status: "failed",
        error: error.message,
        screenshot: FAILURE_SCREENSHOT_PATH,
        query_payload_count: queryPayloads.length,
        recent_query_payloads: queryPayloads.slice(-5),
        query_responses: queryResponses,
        diagnostics,
      },
      null,
      2,
    ),
  );
}

async function loadDashboardDbAudit({
  organizationId,
  workspaceId,
  dashboardId,
  widgetId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(dashboardId)} AS dashboard_id,
    ${sqlUuid(widgetId)} AS widget_id
),
dashboard_row AS (
  SELECT
    dashboard.id,
    dashboard.workspace_id,
    workspace.organization_id,
    dashboard.deleted,
    dashboard.deleted_at IS NOT NULL AS deleted_at_set
  FROM tracer_dashboard dashboard
  JOIN accounts_workspace workspace ON workspace.id = dashboard.workspace_id
  JOIN requested ON requested.dashboard_id = dashboard.id
),
widget_row AS (
  SELECT
    widget.id,
    widget.dashboard_id,
    widget.deleted,
    widget.deleted_at IS NOT NULL AS deleted_at_set,
    widget.query_config
  FROM tracer_dashboardwidget widget
  JOIN requested ON requested.widget_id = widget.id
)
SELECT json_build_object(
  'dashboard_id', (SELECT id::text FROM dashboard_row),
  'dashboard_workspace_id', (SELECT workspace_id::text FROM dashboard_row),
  'dashboard_organization_id', (SELECT organization_id::text FROM dashboard_row),
  'dashboard_deleted', COALESCE((SELECT deleted FROM dashboard_row), false),
  'dashboard_deleted_at_set', COALESCE((SELECT deleted_at_set FROM dashboard_row), false),
  'widget_id', (SELECT id::text FROM widget_row),
  'widget_dashboard_id', (SELECT dashboard_id::text FROM widget_row),
  'widget_deleted', COALESCE((SELECT deleted FROM widget_row), false),
  'widget_deleted_at_set', COALESCE((SELECT deleted_at_set FROM widget_row), false),
  'widget_query_project_ids', COALESCE((SELECT query_config::jsonb -> 'project_ids' FROM widget_row), '[]'::jsonb),
  'widget_query_time_preset', (SELECT query_config::jsonb #>> '{time_range,preset}' FROM widget_row),
  'widget_query_metric_count', COALESCE((SELECT jsonb_array_length(query_config::jsonb -> 'metrics') FROM widget_row), 0),
  'active_widget_count', (
    SELECT count(*)
    FROM tracer_dashboardwidget widget
    JOIN requested ON requested.dashboard_id = widget.dashboard_id
    WHERE widget.deleted = false
  )
)
FROM requested;
`;
  return runPostgresJson(sql);
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
  assert(line, "Postgres audit returned no rows.");
  return JSON.parse(line);
}

function assertDashboardDbAudit(
  audit,
  { organizationId, workspaceId, dashboardId, widgetId, projectId },
) {
  assert(
    audit?.dashboard_id === dashboardId,
    "Dashboard DB audit returned wrong dashboard id.",
  );
  assert(
    audit?.dashboard_organization_id === organizationId,
    "Dashboard DB audit returned wrong organization id.",
  );
  assert(
    audit?.dashboard_workspace_id === workspaceId,
    "Dashboard DB audit returned wrong workspace id.",
  );
  assert(
    audit?.widget_id === widgetId && audit?.widget_dashboard_id === dashboardId,
    "Dashboard DB audit returned wrong widget row.",
  );
  assert(
    Array.isArray(audit?.widget_query_project_ids) &&
      audit.widget_query_project_ids.includes(projectId),
    "Dashboard widget query_config did not persist the project id.",
  );
  assert(
    audit?.widget_query_time_preset === "30D",
    "Dashboard widget query_config did not persist the seeded time preset.",
  );
  assert(
    Number(audit?.widget_query_metric_count) === 1,
    "Dashboard widget query_config did not persist one metric.",
  );
  assert(
    audit?.dashboard_deleted === true &&
      audit?.dashboard_deleted_at_set === true,
    "Dashboard delete did not soft-delete the dashboard.",
  );
  assert(
    audit?.widget_deleted === true && audit?.widget_deleted_at_set === true,
    "Dashboard delete did not soft-delete the child widget.",
  );
  assert(
    Number(audit?.active_widget_count) === 0,
    "Dashboard delete left active child widgets behind.",
  );
}

function sqlUuid(value) {
  assert(isUuid(value), `Expected UUID for SQL literal, received ${value}`);
  return `'${String(value).replace(/'/g, "''")}'::uuid`;
}

function browserExecutablePath() {
  return (
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_BIN ||
    process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  );
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
