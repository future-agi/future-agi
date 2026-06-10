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
const SCREENSHOT_PATH = "/tmp/dashboard-widget-aggregation-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/dashboard-widget-aggregation-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const runSuffix = auth.runId;
  const dashboardName = `TH-4812 widget aggregation ${runSuffix}`;
  const widgetName = `TH-4812 p90 widget ${runSuffix}`;
  let dashboardId = null;
  let widgetId = null;
  let dashboardDeleted = false;

  const metricInventory = await auth.client.get(
    apiPath("/tracer/dashboard/metrics/"),
    {
      query: {
        category: "system_metric",
        source: "traces",
        search: "latency",
        page: 1,
        page_size: 20,
      },
    },
  );
  const latencyMetric = asArray(
    metricInventory?.metrics || metricInventory,
  ).find((metric) => metric?.name === "latency");
  assert(latencyMetric, "Dashboard metric catalog did not include latency.");

  const dashboard = await auth.client.post(apiPath("/tracer/dashboard/"), {
    name: dashboardName,
    description: "Disposable dashboard for widget aggregation smoke.",
  });
  dashboardId = dashboard?.id;
  assert(isUuid(dashboardId), "Dashboard create did not return a UUID id.");

  const apiFailures = [];
  const pageErrors = [];
  const queryEvents = [];
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

  page.on("response", (response) => {
    const url = response.url();
    const request = response.request();
    if (
      request.method() === "POST" &&
      url.includes("/tracer/dashboard/query/")
    ) {
      queryEvents.push({
        status: response.status(),
        payload: readJsonPostData(request),
      });
      return;
    }
    if (
      isDashboardApiUrl(url, dashboardId) &&
      response.status() >= 400 &&
      !url.includes("/widgets/preview/")
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
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
    await expectVisibleText(page, "No widgets yet", { exact: true });

    await waitForResponseDuring(
      page,
      "open widget editor",
      (response) =>
        response.request().method() === "GET" &&
        response.url().includes("/tracer/dashboard/metrics/") &&
        response.status() < 400,
      () => clickButtonText(page, "Add Widget"),
    );
    await page.waitForFunction(
      (id) =>
        window.location.pathname.endsWith(
          `/dashboard/dashboards/${id}/widget/new`,
        ),
      { timeout: 30000 },
      dashboardId,
    );

    await expectVisibleText(page, "Query", { exact: true });
    await fillWidgetTitle(page, "Untitled widget", widgetName);
    await selectMuiSelectOption(page, "Day", "Hour");
    await selectMuiSelectOption(page, "Line", "Metric");

    await clickVisibleText(page, "Select Metric", { exact: true });
    await fillPickerSearch(page, "Search metrics", "latency", "Latency");
    await clickVisibleText(page, "Latency", { exact: true });
    await expectVisibleText(page, "Latency", { exact: true });

    await selectMetricAggregation(page, "Average", "90th Percentile");

    await waitForCondition(
      () =>
        queryEvents.some(
          (event) =>
            event.status >= 200 &&
            event.status < 400 &&
            event.payload?.granularity === "hour" &&
            hasMetricAggregation(event.payload, "latency", "p90"),
        ),
      "successful preview query with hourly p90 latency metric",
    );

    const createResponse = await waitForResponseDuring(
      page,
      "create aggregation widget",
      (response) =>
        response.request().method() === "POST" &&
        response.url().includes(`/tracer/dashboard/${dashboardId}/widgets/`) &&
        !response.url().includes("/preview/") &&
        response.status() < 400,
      () => clickButtonText(page, "Save"),
    );
    const createPayload = await createResponse.json();
    widgetId = createPayload?.result?.id;
    assert(isUuid(widgetId), "Widget create response did not return a UUID.");
    await expectVisibleText(page, "Saved", { exact: true });

    const widget = await waitForWidgetByName(
      auth.client,
      dashboardId,
      widgetName,
    );
    assert(widget.id === widgetId, "Saved widget id did not match API result.");
    assertWidgetShape(widget);

    const configAudit = await loadWidgetConfigAudit({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      dashboardId,
      widgetId,
    });
    assertWidgetConfigAudit(configAudit, {
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      dashboardId,
      widgetId,
    });

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    await auth.client.delete(
      apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
    );
    dashboardDeleted = true;
    const cleanupAudit = await loadDashboardCleanupAudit({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      dashboardId,
      widgetId,
    });
    assertCleanupAudit(cleanupAudit, {
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      dashboardId,
      widgetId,
    });

    assert(
      apiFailures.length === 0,
      `Dashboard aggregation API failures: ${apiFailures.join("; ")}`,
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
            chart_type: "metric",
            metric_name: "latency",
            aggregation: "p90",
            granularity: "hour",
            preview_query_count: queryEvents.length,
            screenshot: SCREENSHOT_PATH,
            config_audit: configAudit,
            cleanup_audit: cleanupAudit,
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await captureFailureDiagnostics(page, error);
    throw error;
  } finally {
    await browser.close();
    if (!dashboardDeleted && dashboardId) {
      await auth.client
        .delete(apiPath("/tracer/dashboard/{id}/", { id: dashboardId }))
        .catch(() => null);
    }
  }
}

async function waitForWidgetByName(client, dashboardId, name) {
  let lastWidgets = [];
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const detail = await client.get(
      apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
    );
    const widgets = asArray(detail.widgets);
    lastWidgets = widgets;
    const match = widgets.find((widget) => widget.name === name);
    if (match) return match;
    await delay(250);
  }
  throw new Error(
    `Dashboard detail never contained widget ${name}. Widgets: ${lastWidgets
      .map((widget) => widget.name)
      .join(", ")}`,
  );
}

function assertWidgetShape(widget) {
  const queryConfig = widget.query_config || widget.queryConfig || {};
  const chartConfig = widget.chart_config || widget.chartConfig || {};
  assert(
    (chartConfig.chart_type || chartConfig.chartType) === "metric",
    "Widget chart_config did not persist metric chart type.",
  );
  assert(
    queryConfig.granularity === "hour",
    "Widget query_config did not persist hourly granularity.",
  );
  assert(
    asArray(queryConfig.metrics).some(
      (metric) =>
        (metric.name === "latency" || metric.id === "latency") &&
        metric.aggregation === "p90",
    ),
    "Widget query_config did not include p90 latency metric.",
  );
}

async function fillWidgetTitle(page, currentText, nextText) {
  await clickVisibleText(page, currentText, { exact: true });
  const selector = 'input[placeholder="Untitled widget"]';
  await page.waitForSelector(selector, { visible: true, timeout: 10000 });
  await page.click(selector, { clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.type(selector, nextText);
  await page.keyboard.press("Enter");
  await expectVisibleText(page, nextText, { exact: true });
}

async function selectMuiSelectOption(page, currentLabel, nextLabel) {
  const opened = await page.evaluate((expected) => {
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
    const select = Array.from(
      document.querySelectorAll(".MuiSelect-select"),
    ).find(
      (element) =>
        isVisible(element) && element.textContent.trim().includes(expected),
    );
    if (!select) return false;
    select.dispatchEvent(
      new MouseEvent("mousedown", {
        bubbles: true,
        cancelable: true,
        view: window,
      }),
    );
    return true;
  }, currentLabel);
  assert(opened, `Unable to open selector showing ${currentLabel}.`);
  await clickVisibleText(page, nextLabel, { exact: true });
  await expectVisibleText(page, nextLabel, { exact: false });
}

async function selectMetricAggregation(page, currentLabel, nextLabel) {
  const opened = await page.evaluate((expected) => {
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
      (element) =>
        isVisible(element) && element.textContent.trim() === expected,
    );
    if (!chip) return false;
    chip.click();
    return true;
  }, currentLabel);
  assert(opened, `Unable to open aggregation picker showing ${currentLabel}.`);
  await clickVisibleText(page, "Percentile", { exact: true });
  await clickVisibleText(page, nextLabel, { exact: true });
  await expectVisibleText(page, nextLabel, { exact: true });
}

async function fillPickerSearch(page, placeholderPrefix, query, expectedText) {
  const selector = `input[placeholder^="${placeholderPrefix}"]`;
  await page.waitForSelector(selector, { visible: true, timeout: 10000 });
  await page.click(selector, { clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.type(selector, query);
  await expectVisibleText(page, expectedText, { exact: true });
}

async function waitForResponseDuring(page, label, predicate, action) {
  const responsePromise = page.waitForResponse(predicate, { timeout: 30000 });
  await action();
  try {
    return await responsePromise;
  } catch (error) {
    throw new Error(`Timed out waiting for ${label}: ${error.message}`);
  }
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

async function clickVisibleText(page, text, { exact = false } = {}) {
  await expectVisibleText(page, text, { exact });
  const clicked = await page.evaluate(
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
      const elements = Array.from(document.querySelectorAll("body *")).filter(
        (element) => {
          if (!isVisible(element)) return false;
          const value = element.textContent.trim();
          return exactMatch ? value === expected : value.includes(expected);
        },
      );
      const target =
        elements.find((element) =>
          ["BUTTON", "A", "LI"].includes(element.tagName),
        ) || elements[elements.length - 1];
      if (!target) return false;
      target.click();
      return true;
    },
    { expected: text, exactMatch: exact },
  );
  assert(clicked, `Unable to click visible text: ${text}`);
}

async function clickButtonText(page, text) {
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
      return Array.from(document.querySelectorAll("button")).some(
        (button) => isVisible(button) && button.textContent.trim() === expected,
      );
    },
    { timeout: 30000 },
    text,
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
    const button = Array.from(document.querySelectorAll("button")).find(
      (candidate) =>
        isVisible(candidate) && candidate.textContent.trim() === expected,
    );
    if (!button) return false;
    button.click();
    return true;
  }, text);
  assert(clicked, `Unable to click button: ${text}`);
}

async function waitForCondition(predicate, label, timeoutMs = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (predicate()) return;
    await delay(250);
  }
  throw new Error(`Timed out waiting for ${label}.`);
}

async function captureFailureDiagnostics(page, error) {
  const diagnostics = await page
    .evaluate(() => ({
      url: window.location.href,
      title: document.title,
      bodyText: document.body?.innerText?.slice(0, 1500) || "",
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
        diagnostics,
      },
      null,
      2,
    ),
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

async function loadWidgetConfigAudit({
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
widget_row AS (
  SELECT
    widget.id,
    widget.query_config,
    widget.chart_config,
    widget.deleted,
    dashboard.workspace_id,
    workspace.organization_id
  FROM tracer_dashboardwidget widget
  JOIN tracer_dashboard dashboard ON dashboard.id = widget.dashboard_id
  JOIN accounts_workspace workspace ON workspace.id = dashboard.workspace_id
  JOIN requested ON requested.widget_id = widget.id
)
SELECT json_build_object(
  'dashboard_id', (SELECT dashboard_id::text FROM requested),
  'widget_id', (SELECT widget_id::text FROM requested),
  'organization_id', (SELECT organization_id::text FROM requested),
  'workspace_id', (SELECT workspace_id::text FROM requested),
  'row_count', (SELECT count(*) FROM widget_row),
  'active_row_count', (SELECT count(*) FROM widget_row WHERE deleted = false),
  'chart_type', (
    SELECT chart_config ->> 'chart_type'
    FROM widget_row
  ),
  'granularity', (
    SELECT query_config ->> 'granularity'
    FROM widget_row
  ),
  'time_preset', (
    SELECT query_config -> 'time_range' ->> 'preset'
    FROM widget_row
  ),
  'metric_names', (
    SELECT json_agg(metric ->> 'name')
    FROM widget_row, jsonb_array_elements(query_config -> 'metrics') metric
  ),
  'metric_aggregations', (
    SELECT json_agg(metric ->> 'aggregation')
    FROM widget_row, jsonb_array_elements(query_config -> 'metrics') metric
  ),
  'scope_matches', EXISTS (
    SELECT 1
    FROM widget_row
    WHERE organization_id = (SELECT organization_id FROM requested)
      AND workspace_id = (SELECT workspace_id FROM requested)
      AND deleted = false
  )
)
FROM requested;
`;
  return runPostgresJson(sql);
}

async function loadDashboardCleanupAudit({
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
  SELECT widget.id, widget.deleted, widget.deleted_at IS NOT NULL AS deleted_at_set
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
  'widget_deleted', COALESCE((SELECT deleted FROM widget_row), false),
  'widget_deleted_at_set', COALESCE((SELECT deleted_at_set FROM widget_row), false)
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

function assertWidgetConfigAudit(
  audit,
  { organizationId, workspaceId, dashboardId, widgetId },
) {
  assert(
    audit?.dashboard_id === dashboardId,
    "Audit returned wrong dashboard.",
  );
  assert(audit?.widget_id === widgetId, "Audit returned wrong widget.");
  assert(
    audit?.organization_id === organizationId,
    "Audit returned wrong organization.",
  );
  assert(
    audit?.workspace_id === workspaceId,
    "Audit returned wrong workspace.",
  );
  assert(
    Number(audit?.row_count) === 1 && Number(audit?.active_row_count) === 1,
    "Audit did not find exactly one active widget row.",
  );
  assert(audit?.chart_type === "metric", "Audit did not find metric chart.");
  assert(
    audit?.granularity === "hour",
    "Audit did not find hourly granularity.",
  );
  assert(audit?.time_preset === "30D", "Audit did not find 30D time preset.");
  assert(
    asArray(audit?.metric_names).includes("latency"),
    "Audit did not find latency metric.",
  );
  assert(
    asArray(audit?.metric_aggregations).includes("p90"),
    "Audit did not find p90 aggregation.",
  );
  assert(audit?.scope_matches === true, "Audit scope check failed.");
}

function assertCleanupAudit(
  audit,
  { organizationId, workspaceId, dashboardId, widgetId },
) {
  assert(
    audit?.dashboard_id === dashboardId,
    "Cleanup audit returned wrong dashboard.",
  );
  assert(audit?.widget_id === widgetId, "Cleanup audit returned wrong widget.");
  assert(
    audit?.dashboard_organization_id === organizationId,
    "Cleanup audit returned wrong organization.",
  );
  assert(
    audit?.dashboard_workspace_id === workspaceId,
    "Cleanup audit returned wrong workspace.",
  );
  assert(
    audit?.dashboard_deleted === true &&
      audit?.dashboard_deleted_at_set === true,
    "Cleanup audit did not find soft-deleted dashboard.",
  );
  assert(
    audit?.widget_deleted === true && audit?.widget_deleted_at_set === true,
    "Cleanup audit did not find soft-deleted widget.",
  );
}

function hasMetricAggregation(payload, metricName, aggregation) {
  return asArray(payload?.metrics).some(
    (metric) =>
      (metric.name === metricName || metric.id === metricName) &&
      metric.aggregation === aggregation,
  );
}

function readJsonPostData(request) {
  try {
    return JSON.parse(request.postData() || "{}");
  } catch {
    return {};
  }
}

function isDashboardApiUrl(url, dashboardId) {
  return (
    Boolean(dashboardId) &&
    (url.includes(`/tracer/dashboard/${dashboardId}/`) ||
      url.includes("/tracer/dashboard/metrics/"))
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
