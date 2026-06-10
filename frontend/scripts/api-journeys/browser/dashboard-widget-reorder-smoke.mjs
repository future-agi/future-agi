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
const SCREENSHOT_PATH = "/tmp/dashboard-widget-reorder-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/dashboard-widget-reorder-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const runSuffix = auth.runId;
  const dashboardName = `TH-4812 widget reorder ${runSuffix}`;
  const widgetNames = [
    `TH-4812 reorder first ${runSuffix}`,
    `TH-4812 reorder second ${runSuffix}`,
    `TH-4812 reorder third ${runSuffix}`,
  ];

  let dashboardId = null;
  let dashboardDeleted = false;
  const createdWidgets = [];

  const dashboard = await auth.client.post(apiPath("/tracer/dashboard/"), {
    name: dashboardName,
    description: "Disposable dashboard for widget reorder browser smoke.",
  });
  dashboardId = dashboard?.id;
  assert(isUuid(dashboardId), "Dashboard create did not return a UUID id.");

  for (let index = 0; index < widgetNames.length; index += 1) {
    const widget = await auth.client.post(
      apiPath("/tracer/dashboard/{dashboard_pk}/widgets/", {
        dashboard_pk: dashboardId,
      }),
      {
        name: widgetNames[index],
        description: `Disposable widget ${index + 1} for reorder actions.`,
        position: index,
        width: 4,
        height: 300,
        query_config: {},
        chart_config: { chart_type: "line" },
      },
    );
    assert(isUuid(widget?.id), "Widget create did not return a UUID id.");
    createdWidgets.push(widget);
  }

  const apiFailures = [];
  const pageErrors = [];
  const reorderRequests = [];
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
      request.method() === "POST" &&
      request
        .url()
        .includes(`/tracer/dashboard/${dashboardId}/widgets/reorder/`)
    ) {
      reorderRequests.push(request.url());
    }
  });
  page.on("response", (response) => {
    if (
      isDashboardApiUrl(response.url(), dashboardId) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${response.url()}`);
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
    for (const widget of createdWidgets) {
      await waitForWidgetCard(page, widget.id);
      await expectVisibleText(page, widget.name, { exact: true });
    }

    const initialUiOrder = await readVisibleWidgetOrder(page);
    assert(
      namesEqual(initialUiOrder, widgetNames),
      `Initial UI order was wrong: ${initialUiOrder.join(" > ")}`,
    );

    const expectedOrder = [
      createdWidgets[1].id,
      createdWidgets[2].id,
      createdWidgets[0].id,
    ];
    const expectedNames = [widgetNames[1], widgetNames[2], widgetNames[0]];

    await waitForResponseDuring(
      page,
      "widget reorder",
      (response) =>
        response.request().method() === "POST" &&
        response
          .url()
          .includes(`/tracer/dashboard/${dashboardId}/widgets/reorder/`) &&
        response.status() < 400,
      () => dragWidgetToRowEnd(page, createdWidgets[0].id),
    );

    const reorderedWidgets = await waitForWidgetOrder(
      auth.client,
      dashboardId,
      expectedOrder,
    );
    assert(
      reorderedWidgets.every((widget) => widget.width === 4),
      "Reorder unexpectedly changed the three-widget row widths.",
    );

    await waitForUiWidgetOrder(page, expectedNames);
    const positionAudit = await loadDashboardWidgetPositionAudit({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      dashboardId,
      expectedWidgetIds: expectedOrder,
    });
    assertPositionAudit(positionAudit, {
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      dashboardId,
      expectedWidgetIds: expectedOrder,
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
    });
    assertCleanupAudit(cleanupAudit, {
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      dashboardId,
      expectedWidgetCount: createdWidgets.length,
    });

    assert(
      apiFailures.length === 0,
      `Dashboard reorder API failures: ${apiFailures.join("; ")}`,
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
            widget_ids: createdWidgets.map((widget) => widget.id),
            initial_order: widgetNames,
            reordered_order: expectedNames,
            reorder_request_count: reorderRequests.length,
            screenshot: SCREENSHOT_PATH,
            position_audit: positionAudit,
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

async function dragWidgetToRowEnd(page, widgetId) {
  await waitForWidgetCard(page, widgetId);
  const startPoint = await page.evaluate((id) => {
    const card = document.querySelector(`[data-widget-id="${id}"]`);
    if (!card) return null;
    const header =
      card.querySelector(".MuiCardContent-root > div:first-child") || card;
    const headerRect = header.getBoundingClientRect();
    return {
      startX: headerRect.left + 8,
      startY: headerRect.top + headerRect.height / 2,
    };
  }, widgetId);
  assert(startPoint, `Unable to compute drag start for widget ${widgetId}.`);

  await page.mouse.move(startPoint.startX, startPoint.startY);
  await page.mouse.down();
  await page.mouse.move(startPoint.startX + 60, startPoint.startY, {
    steps: 8,
  });
  await delay(150);
  const targetPoint = await page.evaluate(() => {
    const widgetRects = Array.from(
      document.querySelectorAll("[data-widget-id]"),
    ).map((element) => {
      const rect = element.getBoundingClientRect();
      return {
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
      };
    });
    if (!widgetRects.length) return null;
    const rowLeft = Math.min(...widgetRects.map((rect) => rect.left));
    const rowRight = Math.max(...widgetRects.map((rect) => rect.right));
    const rowTop = Math.min(...widgetRects.map((rect) => rect.top));
    const rowBottom = Math.max(...widgetRects.map((rect) => rect.bottom));
    const candidates = Array.from(document.querySelectorAll("body div"))
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return {
          element,
          left: rect.left,
          right: rect.right,
          top: rect.top,
          bottom: rect.bottom,
          width: rect.width,
          height: rect.height,
        };
      })
      .filter((candidate) => {
        if (candidate.element.closest("[data-widget-id]")) return false;
        if (candidate.width < 18 || candidate.width > 42) return false;
        if (candidate.height < 100) return false;
        if (candidate.right < rowLeft - 48) return false;
        if (candidate.left > rowRight + 64) return false;
        if (candidate.bottom < rowTop || candidate.top > rowBottom) {
          return false;
        }
        return true;
      })
      .sort((left, right) => right.left - left.left);
    const target = candidates[0];
    if (!target) {
      return {
        targetX: Math.min(window.innerWidth - 8, rowRight + 10),
        targetY: rowTop + (rowBottom - rowTop) / 2,
      };
    }
    return {
      targetX: target.left + target.width / 2,
      targetY: target.top + target.height / 2,
    };
  });
  assert(targetPoint, "Unable to compute widget row-end drop target.");
  await page.mouse.move(targetPoint.targetX, targetPoint.targetY, {
    steps: 24,
  });
  await delay(150);
  await page.mouse.up();
}

async function waitForWidgetCard(page, widgetId) {
  await page.waitForSelector(`[data-widget-id="${widgetId}"]`, {
    visible: true,
    timeout: 30000,
  });
}

async function readVisibleWidgetOrder(page) {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll("[data-widget-id]")).map((element) => {
      const title = element.querySelector(".MuiTypography-subtitle2");
      return (title?.textContent || element.textContent || "").trim();
    }),
  );
}

async function waitForUiWidgetOrder(page, expectedNames) {
  let lastOrder = [];
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const order = await readVisibleWidgetOrder(page);
    lastOrder = order;
    if (namesEqual(order, expectedNames)) return order;
    await delay(250);
  }
  throw new Error(
    `UI widget order never matched. Expected ${expectedNames.join(
      " > ",
    )}; saw ${lastOrder.join(" > ")}`,
  );
}

async function waitForWidgetOrder(client, dashboardId, expectedIds) {
  let lastOrder = [];
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const detail = await client.get(
      apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
    );
    const widgets = asArray(detail.widgets).slice().sort(sortByPosition);
    lastOrder = widgets.map((widget) => widget.id);
    if (idsEqual(lastOrder, expectedIds)) return widgets;
    await delay(250);
  }
  throw new Error(
    `Dashboard detail never reflected widget reorder. Expected ${expectedIds.join(
      " > ",
    )}; saw ${lastOrder.join(" > ")}`,
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

async function captureFailureDiagnostics(page, error) {
  const diagnostics = await page
    .evaluate(() => ({
      url: window.location.href,
      title: document.title,
      bodyText: document.body?.innerText?.slice(0, 1500) || "",
      widgets: Array.from(document.querySelectorAll("[data-widget-id]")).map(
        (element) => ({
          id: element.getAttribute("data-widget-id"),
          text: element.textContent.trim().slice(0, 300),
          rect: (() => {
            const { x, y, width, height } = element.getBoundingClientRect();
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
        diagnostics,
      },
      null,
      2,
    ),
  );
}

function isDashboardApiUrl(url, dashboardId) {
  return (
    Boolean(dashboardId) &&
    (url.includes(`/tracer/dashboard/${dashboardId}/`) ||
      url.includes("/tracer/dashboard/query/"))
  );
}

async function loadDashboardWidgetPositionAudit({
  organizationId,
  workspaceId,
  dashboardId,
  expectedWidgetIds,
}) {
  const expectedRows = expectedWidgetIds
    .map((id, index) => `(${sqlUuid(id)}, ${index}, 4)`)
    .join(",\n");
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(dashboardId)} AS dashboard_id
),
expected(widget_id, expected_position, expected_width) AS (
  VALUES
    ${expectedRows}
),
actual AS (
  SELECT
    widget.id AS widget_id,
    widget.position,
    widget.width,
    widget.deleted,
    dashboard.workspace_id,
    workspace.organization_id
  FROM tracer_dashboardwidget widget
  JOIN tracer_dashboard dashboard ON dashboard.id = widget.dashboard_id
  JOIN accounts_workspace workspace ON workspace.id = dashboard.workspace_id
  JOIN requested ON requested.dashboard_id = dashboard.id
  WHERE widget.id IN (SELECT widget_id FROM expected)
)
SELECT json_build_object(
  'dashboard_id', (SELECT dashboard_id::text FROM requested),
  'organization_id', (SELECT organization_id::text FROM requested),
  'workspace_id', (SELECT workspace_id::text FROM requested),
  'row_count', (SELECT count(*) FROM actual),
  'active_row_count', (SELECT count(*) FROM actual WHERE deleted = false),
  'ordered_widget_ids', (
    SELECT json_agg(widget_id::text ORDER BY position)
    FROM actual
  ),
  'position_matches', NOT EXISTS (
    SELECT 1
    FROM expected
    LEFT JOIN actual ON actual.widget_id = expected.widget_id
    WHERE actual.widget_id IS NULL
      OR actual.position <> expected.expected_position
      OR actual.width <> expected.expected_width
      OR actual.deleted <> false
      OR actual.organization_id <> (SELECT organization_id FROM requested)
      OR actual.workspace_id <> (SELECT workspace_id FROM requested)
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
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(dashboardId)} AS dashboard_id
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
widget_rows AS (
  SELECT widget.id, widget.deleted, widget.deleted_at IS NOT NULL AS deleted_at_set
  FROM tracer_dashboardwidget widget
  JOIN requested ON requested.dashboard_id = widget.dashboard_id
)
SELECT json_build_object(
  'dashboard_id', (SELECT id::text FROM dashboard_row),
  'dashboard_workspace_id', (SELECT workspace_id::text FROM dashboard_row),
  'dashboard_organization_id', (SELECT organization_id::text FROM dashboard_row),
  'dashboard_deleted', COALESCE((SELECT deleted FROM dashboard_row), false),
  'dashboard_deleted_at_set', COALESCE((SELECT deleted_at_set FROM dashboard_row), false),
  'widget_row_count', (SELECT count(*) FROM widget_rows),
  'active_widget_count', (SELECT count(*) FROM widget_rows WHERE deleted = false),
  'soft_deleted_widget_count', (
    SELECT count(*)
    FROM widget_rows
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

function assertPositionAudit(
  audit,
  { organizationId, workspaceId, dashboardId, expectedWidgetIds },
) {
  assert(
    audit?.dashboard_id === dashboardId,
    "Position audit returned wrong dashboard id.",
  );
  assert(
    audit?.organization_id === organizationId,
    "Position audit returned wrong organization id.",
  );
  assert(
    audit?.workspace_id === workspaceId,
    "Position audit returned wrong workspace id.",
  );
  assert(
    Number(audit?.row_count) === expectedWidgetIds.length &&
      Number(audit?.active_row_count) === expectedWidgetIds.length,
    "Position audit did not find the expected active widget rows.",
  );
  assert(
    idsEqual(audit?.ordered_widget_ids || [], expectedWidgetIds),
    "Position audit returned the wrong persisted widget order.",
  );
  assert(
    audit?.position_matches === true,
    "Position audit did not match expected position and width values.",
  );
}

function assertCleanupAudit(
  audit,
  { organizationId, workspaceId, dashboardId, expectedWidgetCount },
) {
  assert(
    audit?.dashboard_id === dashboardId,
    "Cleanup audit returned wrong dashboard id.",
  );
  assert(
    audit?.dashboard_organization_id === organizationId,
    "Cleanup audit returned wrong organization id.",
  );
  assert(
    audit?.dashboard_workspace_id === workspaceId,
    "Cleanup audit returned wrong workspace id.",
  );
  assert(
    audit?.dashboard_deleted === true &&
      audit?.dashboard_deleted_at_set === true,
    "Dashboard cleanup did not soft-delete the dashboard.",
  );
  assert(
    Number(audit?.widget_row_count) === expectedWidgetCount &&
      Number(audit?.active_widget_count) === 0 &&
      Number(audit?.soft_deleted_widget_count) === expectedWidgetCount,
    "Dashboard cleanup did not soft-delete the expected child widgets.",
  );
}

function sqlUuid(value) {
  assert(isUuid(value), `Expected UUID for SQL literal, received ${value}`);
  return `'${String(value).replace(/'/g, "''")}'::uuid`;
}

function sortByPosition(left, right) {
  return (left.position ?? 0) - (right.position ?? 0);
}

function idsEqual(actual, expected) {
  return (
    actual.length === expected.length &&
    actual.every((value, index) => value === expected[index])
  );
}

function namesEqual(actual, expected) {
  return (
    actual.length === expected.length &&
    actual.every((value, index) => value === expected[index])
  );
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
