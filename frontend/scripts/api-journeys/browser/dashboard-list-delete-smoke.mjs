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
const SCREENSHOT_PATH = "/tmp/dashboard-list-delete-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/dashboard-list-delete-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const runSuffix = auth.runId;
  const dashboardName = `TH-4812 list delete ${runSuffix}`;
  const dashboardDescription =
    "Disposable dashboard for list delete browser smoke.";
  const widgetName = `TH-4812 delete widget ${runSuffix}`;
  let dashboardId = null;
  let widgetId = null;
  let dashboardDeleted = false;

  const dashboard = await auth.client.post(apiPath("/tracer/dashboard/"), {
    name: dashboardName,
    description: dashboardDescription,
  });
  dashboardId = dashboard?.id;
  assert(isUuid(dashboardId), "Dashboard create did not return a UUID id.");

  const widget = await auth.client.post(
    apiPath("/tracer/dashboard/{dashboard_pk}/widgets/", {
      dashboard_pk: dashboardId,
    }),
    {
      name: widgetName,
      description: "Disposable child widget for dashboard list delete smoke.",
      position: 0,
      width: 12,
      height: 320,
      query_config: {},
      chart_config: { chart_type: "line" },
    },
  );
  widgetId = widget?.id;
  assert(isUuid(widgetId), "Widget create did not return a UUID id.");

  const apiFailures = [];
  const pageErrors = [];
  const deleteRequests = [];
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
    if (
      request.method() === "DELETE" &&
      request.url().includes(`/tracer/dashboard/${dashboardId}/`)
    ) {
      deleteRequests.push(request.url());
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (isDashboardApiUrl(url, dashboardId) && response.status() >= 400) {
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
        response.url().endsWith("/tracer/dashboard/") &&
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

    await clickDashboardRowDelete(page, dashboardName);
    await expectVisibleText(page, "Delete Dashboard", { exact: true });
    await expectVisibleText(page, dashboardName);

    await waitForResponseDuring(
      page,
      "dashboard delete",
      (response) =>
        response.request().method() === "DELETE" &&
        response.url().includes(`/tracer/dashboard/${dashboardId}/`) &&
        response.status() < 400,
      () => clickDialogDeleteButton(page),
    );
    dashboardDeleted = true;

    await waitForDashboardGoneFromList(page, dashboardName);
    await expectVisibleText(page, "No dashboards match your search", {
      exact: true,
    });
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    await waitForCondition(
      () => deleteRequests.length === 1,
      "single dashboard DELETE request",
    );
    const listAfterDelete = asArray(
      await auth.client.get(apiPath("/tracer/dashboard/")),
    );
    assert(
      !listAfterDelete.some((candidate) => candidate.id === dashboardId),
      "Deleted dashboard still appeared in dashboard list API.",
    );

    const deletedDetail = await fetchRawApi(auth, {
      pathName: apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
    });
    assert(
      [400, 404].includes(deletedDetail.status),
      `Deleted dashboard detail returned unexpected HTTP ${deletedDetail.status}.`,
    );

    const deletedAudit = await loadDashboardDeleteAudit({
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
      `Dashboard list delete API failures: ${apiFailures.join("; ")}`,
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
            dashboard_name: dashboardName,
            delete_request_count: deleteRequests.length,
            deleted_detail_status: deletedDetail.status,
            screenshot: SCREENSHOT_PATH,
            deleted_audit: deletedAudit,
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await captureFailureDiagnostics(page, error, {
      dashboardId,
      widgetId,
      deleteRequests,
      apiFailures,
      pageErrors,
    });
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
  try {
    await action();
  } catch (error) {
    responsePromise.catch(() => null);
    throw error;
  }
  try {
    return await responsePromise;
  } catch (error) {
    throw new Error(`Timed out waiting for ${label}: ${error.message}`);
  }
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

async function clickDashboardRowDelete(page, dashboardName) {
  const target = await page.evaluate((expectedName) => {
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
    const nameElements = Array.from(document.querySelectorAll("body *")).filter(
      (element) =>
        isVisible(element) && element.textContent.trim() === expectedName,
    );
    for (const nameElement of nameElements) {
      let row = nameElement.parentElement;
      for (let depth = 0; row && depth < 8; depth += 1) {
        const rowText = row.textContent || "";
        const buttons = Array.from(row.querySelectorAll("button"));
        const rowRect = row.getBoundingClientRect();
        if (
          rowText.includes(expectedName) &&
          rowText.includes("widget") &&
          buttons.length > 0 &&
          rowRect.width > 300
        ) {
          const button = buttons.at(-1);
          const buttonRect = button.getBoundingClientRect();
          return {
            rowX: rowRect.left + rowRect.width / 2,
            rowY: rowRect.top + rowRect.height / 2,
            buttonX: buttonRect.left + buttonRect.width / 2,
            buttonY: buttonRect.top + buttonRect.height / 2,
          };
        }
        row = row.parentElement;
      }
    }
    return null;
  }, dashboardName);
  assert(
    target,
    `Unable to find dashboard row delete button for ${dashboardName}`,
  );
  await page.mouse.move(target.rowX, target.rowY);
  await page.mouse.move(target.buttonX, target.buttonY);
  await page.mouse.click(target.buttonX, target.buttonY);
}

async function clickDialogDeleteButton(page) {
  const clicked = await page.evaluate(() => {
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
    const dialogs = Array.from(document.querySelectorAll("[role='dialog']"));
    for (const dialog of dialogs) {
      if (!isVisible(dialog)) continue;
      const button = Array.from(dialog.querySelectorAll("button")).find(
        (candidate) =>
          isVisible(candidate) && candidate.textContent.trim() === "Delete",
      );
      if (button) {
        button.click();
        return true;
      }
    }
    return false;
  });
  assert(clicked, "Unable to click confirmation dialog Delete button.");
}

async function waitForDashboardGoneFromList(page, dashboardName) {
  await page.waitForFunction(
    (expectedName) => {
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
      return !Array.from(document.querySelectorAll("body *")).some(
        (element) =>
          isVisible(element) && element.textContent.trim() === expectedName,
      );
    },
    { timeout: 30000 },
    dashboardName,
  );
}

async function waitForCondition(predicate, label, timeout = 30000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeout) {
    if (predicate()) return;
    await delay(100);
  }
  throw new Error(`Timed out waiting for ${label}.`);
}

async function fetchRawApi(auth, { pathName, method = "GET" }) {
  const response = await fetch(new URL(pathName, auth.apiBase), {
    method,
    headers: {
      Authorization: `Bearer ${auth.tokens.access}`,
      "X-Organization-Id": auth.organizationId,
      "X-Workspace-Id": auth.workspaceId,
    },
  });
  const text = await response.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  return { status: response.status, body };
}

async function loadDashboardDeleteAudit({
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
),
dashboard_widgets AS (
  SELECT
    widget.id,
    widget.deleted,
    widget.deleted_at IS NOT NULL AS deleted_at_set
  FROM tracer_dashboardwidget widget
  JOIN requested ON requested.dashboard_id = widget.dashboard_id
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
  'widget_row_count', (SELECT count(*) FROM dashboard_widgets),
  'active_widget_count', (
    SELECT count(*)
    FROM dashboard_widgets
    WHERE deleted = false
  ),
  'soft_deleted_widget_count', (
    SELECT count(*)
    FROM dashboard_widgets
    WHERE deleted = true AND deleted_at_set = true
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
    "Dashboard list delete did not soft-delete the dashboard.",
  );
  assert(
    audit?.widget_deleted === true && audit?.widget_deleted_at_set === true,
    "Dashboard list delete did not soft-delete the child widget.",
  );
  assert(
    Number(audit?.widget_row_count) === 1 &&
      Number(audit?.active_widget_count) === 0 &&
      Number(audit?.soft_deleted_widget_count) === 1,
    "Dashboard list delete did not leave exactly one soft-deleted child widget.",
  );
}

function isDashboardApiUrl(url, dashboardId) {
  return (
    url.includes("/tracer/dashboard/") &&
    (url.endsWith("/tracer/dashboard/") ||
      (Boolean(dashboardId) &&
        url.includes(`/tracer/dashboard/${dashboardId}/`)))
  );
}

async function captureFailureDiagnostics(page, error, evidence) {
  const diagnostics = await page
    .evaluate(() => ({
      url: window.location.href,
      title: document.title,
      bodyText: document.body?.innerText?.slice(0, 1600) || "",
      dialogs: Array.from(document.querySelectorAll("[role='dialog']")).map(
        (dialog) => dialog.textContent.trim(),
      ),
      buttons: Array.from(document.querySelectorAll("button")).map((button) => {
        const { x, y, width, height } = button.getBoundingClientRect();
        return {
          text: button.textContent.trim(),
          ariaLabel: button.getAttribute("aria-label"),
          title: button.getAttribute("title"),
          rect: { x, y, width, height },
        };
      }),
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
        evidence,
        diagnostics,
      },
      null,
      2,
    ),
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
