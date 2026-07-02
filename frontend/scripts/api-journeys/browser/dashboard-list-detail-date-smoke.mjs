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
import { resolveObserveProject } from "../lib/fixtures.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/dashboard-list-detail-date-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/dashboard-list-detail-date-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const evidence = [];
  const project = await resolveObserveProject(auth.client, evidence);
  const runSuffix = auth.runId;
  const dashboardName = `QA dashboard list detail ${runSuffix}`;
  const dashboardDescription =
    "Disposable dashboard for list/detail/date browser smoke.";
  const widgetName = `QA list detail latency ${runSuffix}`;
  let dashboardId = null;
  let widgetId = null;
  let dashboardDeleted = false;

  const createdDashboard = await auth.client.post(
    apiPath("/tracer/dashboard/"),
    {
      name: dashboardName,
      description: dashboardDescription,
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
      description: "Temporary latency widget for dashboard detail smoke.",
      position: 0,
      width: 12,
      height: 4,
      query_config: dashboardQueryConfig(project.id),
      chart_config: { chart_type: "line" },
    },
  );
  widgetId = createdWidget?.id;
  assert(isUuid(widgetId), "Dashboard widget create did not return a UUID id.");

  const apiFailures = [];
  const pageErrors = [];
  const queryPayloads = [];
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
      request.method() === "POST" &&
      url.includes("/tracer/dashboard/query/")
    ) {
      queryPayloads.push(readJsonPostData(request));
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (isDashboardReadUrl(url, dashboardId) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await waitForResponseDuring(
      page,
      "dashboard list",
      (response) =>
        response.request().method() === "GET" &&
        response.url().includes("/tracer/dashboard/") &&
        !response.url().includes(`/tracer/dashboard/${dashboardId}/`) &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/dashboards`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await expectVisibleText(page, "Dashboard", { exact: true });
    await setSearchInput(page, dashboardName);
    await expectVisibleText(page, dashboardName, { exact: true });
    await expectVisibleText(page, "1 widget", { exact: true });

    await waitForResponseDuring(
      page,
      "dashboard detail",
      (response) =>
        response.request().method() === "GET" &&
        response.url().includes(`/tracer/dashboard/${dashboardId}/`) &&
        response.status() < 400,
      () => clickVisibleText(page, dashboardName, { exact: true }),
    );
    await page.waitForFunction(
      (id) => window.location.pathname.endsWith(`/dashboard/dashboards/${id}`),
      { timeout: 30000 },
      dashboardId,
    );
    await expectVisibleText(page, dashboardName, { exact: true });
    await expectVisibleText(page, dashboardDescription, { exact: true });
    await expectVisibleText(page, widgetName, { exact: true });
    await expectVisibleText(page, "Default", { exact: true });
    await expectVisibleText(page, "7D", { exact: true });
    await expectVisibleText(page, "30D", { exact: true });

    await waitForCondition(
      () => queryPayloads.length > 0,
      "initial dashboard widget query",
    );
    const initialQueryPayload = queryPayloads.at(-1);
    assert(
      initialQueryPayload?.time_range?.preset === "30D",
      "Initial widget query did not use the seeded 30D preset.",
    );

    await clickDateChip(page, "7D");
    await expectDateChipFilled(page, "7D");
    await clickVisibleText(page, "Add Widget", { exact: true });
    await page.waitForFunction(
      (expectedDashboardId) =>
        window.location.pathname.endsWith(
          `/dashboard/dashboards/${expectedDashboardId}/widget/new`,
        ) &&
        new URLSearchParams(window.location.search).get("timePreset") === "7D",
      { timeout: 30000 },
      dashboardId,
    );
    await page.goBack({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(
      (id) => window.location.pathname.endsWith(`/dashboard/dashboards/${id}`),
      { timeout: 30000 },
      dashboardId,
    );
    await expectVisibleText(page, dashboardName, { exact: true });

    await clickDateChip(page, "30D");
    await expectDateChipFilled(page, "30D");

    await clickDateChip(page, "Default");
    await expectDateChipFilled(page, "Default");

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    await page.goBack({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(
      () => window.location.pathname === "/dashboard/dashboards",
      { timeout: 30000 },
    );
    await expectVisibleText(page, dashboardName, { exact: true });

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
    assertDeletedDashboardAudit(deletedAudit, {
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      dashboardId,
      widgetId,
    });

    assert(
      apiFailures.length === 0,
      `Dashboard list/detail API failures: ${apiFailures.join("; ")}`,
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
            query_payload_count: queryPayloads.length,
            initial_query_time_preset:
              initialQueryPayload?.time_range?.preset || null,
            widget_editor_time_preset: "7D",
            deleted_at_set: deletedAudit.dashboard_deleted_at_set,
            widget_deleted_at_set: deletedAudit.widget_deleted_at_set,
            observed_project_resolution: evidence,
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await captureFailureDiagnostics(page, error, queryPayloads);
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

function dashboardQueryConfig(projectId) {
  assert(
    isUuid(projectId),
    "projectId must be a UUID for dashboard query config.",
  );
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
  const responsePromise = page.waitForResponse(predicate, { timeout: 30000 });
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

async function waitForCondition(predicate, label, timeout = 30000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeout) {
    if (predicate()) return;
    await delay(100);
  }
  throw new Error(`Timed out waiting for ${label}.`);
}

async function setSearchInput(page, value) {
  const selector = 'input[placeholder="Search"]';
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector, { clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.type(selector, value);
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
        ) ||
        elements.find((element) => element.closest("[role='button']")) ||
        elements[elements.length - 1];
      if (!target) return false;
      target.click();
      return true;
    },
    { expected: text, exactMatch: exact },
  );
  assert(clicked, `Unable to click visible text: ${text}`);
}

async function captureFailureDiagnostics(page, error, queryPayloads) {
  const diagnostics = await page
    .evaluate(() => ({
      url: window.location.href,
      title: document.title,
      bodyText: document.body?.innerText?.slice(0, 1200) || "",
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
        last_query_payload: queryPayloads.at(-1) || null,
        diagnostics,
      },
      null,
      2,
    ),
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

function isDashboardReadUrl(url, dashboardId) {
  return (
    url.includes("/tracer/dashboard/") &&
    (url.endsWith("/tracer/dashboard/") ||
      url.includes(`/tracer/dashboard/${dashboardId}/`))
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
    widget.deleted_at IS NOT NULL AS deleted_at_set
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

function assertDeletedDashboardAudit(
  audit,
  { organizationId, workspaceId, dashboardId, widgetId },
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
