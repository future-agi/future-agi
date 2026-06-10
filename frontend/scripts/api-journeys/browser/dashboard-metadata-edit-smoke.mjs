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

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/dashboard-metadata-edit-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/dashboard-metadata-edit-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const runSuffix = auth.runId;
  const dashboardName = `TH-4812 metadata edit ${runSuffix}`;
  const dashboardDescription =
    "Disposable dashboard for metadata edit browser smoke.";
  const updatedName = `TH-4812 metadata renamed ${runSuffix}`;
  const updatedDescription =
    "Updated dashboard description saved by the metadata edit browser smoke.";
  let dashboardId = null;
  let dashboardDeleted = false;

  const dashboard = await auth.client.post(apiPath("/tracer/dashboard/"), {
    name: dashboardName,
    description: dashboardDescription,
  });
  dashboardId = dashboard?.id;
  assert(isUuid(dashboardId), "Dashboard create did not return a UUID id.");

  const apiFailures = [];
  const pageErrors = [];
  const patchRequests = [];
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
      request.method() === "PATCH" &&
      url.includes(`/tracer/dashboard/${dashboardId}/`)
    ) {
      patchRequests.push(readJsonPostData(request));
    }
    if (isDashboardApiUrl(url, dashboardId) && response.status() >= 400) {
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
    await expectVisibleText(page, dashboardDescription, { exact: true });
    await expectVisibleText(page, "No widgets yet", { exact: true });

    const titleResponse = await waitForResponseDuring(
      page,
      "dashboard title patch",
      (response) =>
        response.request().method() === "PATCH" &&
        response.url().includes(`/tracer/dashboard/${dashboardId}/`) &&
        response.status() < 400,
      async () => {
        await editInlineText(page, dashboardName, updatedName);
        await page.keyboard.press("Enter");
      },
    );
    assertPatchPayload(await parseJsonResponse(titleResponse), {
      name: updatedName,
    });
    await expectVisibleText(page, updatedName, { exact: true });

    const descriptionResponse = await waitForResponseDuring(
      page,
      "dashboard description patch",
      (response) =>
        response.request().method() === "PATCH" &&
        response.url().includes(`/tracer/dashboard/${dashboardId}/`) &&
        response.status() < 400,
      async () => {
        await editInlineText(page, dashboardDescription, updatedDescription, {
          multiline: true,
        });
        await clickVisibleText(page, "Default", { exact: true });
      },
    );
    assertPatchPayload(await parseJsonResponse(descriptionResponse), {
      description: updatedDescription,
    });
    await expectVisibleText(page, updatedDescription, { exact: true });

    await waitForCondition(
      () =>
        patchRequests.some((request) => request?.name === updatedName) &&
        patchRequests.some(
          (request) => request?.description === updatedDescription,
        ),
      "dashboard metadata PATCH payloads",
    );

    const readback = await auth.client.get(
      apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
    );
    assert(readback?.name === updatedName, "API readback returned old name.");
    assert(
      readback?.description === updatedDescription,
      "API readback returned old description.",
    );
    assert(
      Array.isArray(readback?.widgets) && readback.widgets.length === 0,
      "Metadata edit unexpectedly created dashboard widgets.",
    );

    const metadataAudit = await loadDashboardMetadataAudit({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      dashboardId,
    });
    assertMetadataAudit(metadataAudit, {
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      dashboardId,
      name: updatedName,
      description: updatedDescription,
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
    });

    assert(
      apiFailures.length === 0,
      `Dashboard metadata API failures: ${apiFailures.join("; ")}`,
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
            old_name: dashboardName,
            new_name: updatedName,
            old_description: dashboardDescription,
            new_description: updatedDescription,
            patch_payloads: patchRequests,
            screenshot: SCREENSHOT_PATH,
            metadata_audit: metadataAudit,
            cleanup_audit: cleanupAudit,
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await captureFailureDiagnostics(page, error, patchRequests);
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

async function editInlineText(
  page,
  currentText,
  nextText,
  { multiline = false } = {},
) {
  await clickInlineDisplayText(page, currentText);
  const field = await waitForEditableByValue(page, {
    selector: multiline ? "textarea" : "input",
    value: currentText,
  });
  await field.click({ clickCount: 3 });
  await page.keyboard.press("Backspace");
  await field.type(nextText);
}

async function clickInlineDisplayText(page, text) {
  await expectVisibleText(page, text, { exact: true });
  const clicked = await page.evaluate((expected) => {
    const candidates = Array.from(document.querySelectorAll("body *")).filter(
      (element) => {
        if (["INPUT", "TEXTAREA"].includes(element.tagName)) return false;
        if ((element.textContent || "").trim() !== expected) return false;
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      },
    );
    candidates.sort((left, right) => {
      const leftStyle = window.getComputedStyle(left);
      const rightStyle = window.getComputedStyle(right);
      const leftFontSize = Number.parseFloat(leftStyle.fontSize) || 0;
      const rightFontSize = Number.parseFloat(rightStyle.fontSize) || 0;
      if (leftFontSize !== rightFontSize) {
        return rightFontSize - leftFontSize;
      }
      const leftRect = left.getBoundingClientRect();
      const rightRect = right.getBoundingClientRect();
      return (
        rightRect.width * rightRect.height - leftRect.width * leftRect.height
      );
    });
    const target = candidates[0];
    if (!target) return false;
    target.click();
    return true;
  }, text);
  assert(clicked, `Unable to click inline display text: ${text}`);
}

async function waitForEditableByValue(page, { selector, value }) {
  await page.waitForFunction(
    ({ selector: fieldSelector, expected }) => {
      return Array.from(document.querySelectorAll(fieldSelector)).some(
        (element) =>
          isVisibleElement(element) &&
          (element.value || "").trim() === expected.trim(),
      );

      function isVisibleElement(element) {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      }
    },
    { timeout: 10000 },
    { selector, expected: value },
  );

  const fields = await page.$$(selector);
  for (const field of fields) {
    const matches = await field.evaluate((element, expected) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0 &&
        (element.value || "").trim() === expected.trim()
      );
    }, value);
    if (matches) return field;
    await field.dispose();
  }
  throw new Error(`Unable to find editable field containing ${value}.`);
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

async function waitForCondition(predicate, label, timeoutMs = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (predicate()) return;
    await delay(250);
  }
  throw new Error(`Timed out waiting for ${label}.`);
}

async function parseJsonResponse(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function assertPatchPayload(payload, expectedFields) {
  const result = payload?.result || payload;
  for (const [key, expected] of Object.entries(expectedFields)) {
    assert(
      result?.[key] === expected,
      `Dashboard PATCH response did not include updated ${key}.`,
    );
  }
}

function readJsonPostData(request) {
  try {
    return JSON.parse(request.postData() || "{}");
  } catch {
    return {};
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

async function loadDashboardMetadataAudit({
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
    dashboard.name,
    dashboard.description,
    dashboard.deleted,
    dashboard.deleted_at IS NOT NULL AS deleted_at_set
  FROM tracer_dashboard dashboard
  JOIN accounts_workspace workspace ON workspace.id = dashboard.workspace_id
  JOIN requested ON requested.dashboard_id = dashboard.id
)
SELECT json_build_object(
  'dashboard_id', (SELECT id::text FROM dashboard_row),
  'dashboard_workspace_id', (SELECT workspace_id::text FROM dashboard_row),
  'dashboard_organization_id', (SELECT organization_id::text FROM dashboard_row),
  'name', (SELECT name FROM dashboard_row),
  'description', (SELECT description FROM dashboard_row),
  'dashboard_deleted', COALESCE((SELECT deleted FROM dashboard_row), false),
  'dashboard_deleted_at_set', COALESCE((SELECT deleted_at_set FROM dashboard_row), false),
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
)
SELECT json_build_object(
  'dashboard_id', (SELECT id::text FROM dashboard_row),
  'dashboard_workspace_id', (SELECT workspace_id::text FROM dashboard_row),
  'dashboard_organization_id', (SELECT organization_id::text FROM dashboard_row),
  'dashboard_deleted', COALESCE((SELECT deleted FROM dashboard_row), false),
  'dashboard_deleted_at_set', COALESCE((SELECT deleted_at_set FROM dashboard_row), false),
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

function assertMetadataAudit(
  audit,
  { organizationId, workspaceId, dashboardId, name, description },
) {
  assert(
    audit?.dashboard_id === dashboardId,
    "Metadata audit returned wrong dashboard.",
  );
  assert(
    audit?.dashboard_organization_id === organizationId,
    "Metadata audit returned wrong organization.",
  );
  assert(
    audit?.dashboard_workspace_id === workspaceId,
    "Metadata audit returned wrong workspace.",
  );
  assert(audit?.name === name, "Metadata audit returned old dashboard name.");
  assert(
    audit?.description === description,
    "Metadata audit returned old dashboard description.",
  );
  assert(
    audit?.dashboard_deleted === false &&
      audit?.dashboard_deleted_at_set === false,
    "Metadata audit found dashboard deleted before cleanup.",
  );
  assert(
    Number(audit?.active_widget_count) === 0,
    "Metadata edit unexpectedly left active dashboard widgets.",
  );
}

function assertCleanupAudit(
  audit,
  { organizationId, workspaceId, dashboardId },
) {
  assert(
    audit?.dashboard_id === dashboardId,
    "Cleanup audit returned wrong dashboard.",
  );
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
    Number(audit?.active_widget_count) === 0,
    "Dashboard cleanup left active widgets behind.",
  );
}

function isDashboardApiUrl(url, dashboardId) {
  return (
    Boolean(dashboardId) && url.includes(`/tracer/dashboard/${dashboardId}/`)
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

async function captureFailureDiagnostics(page, error, patchRequests) {
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
        patch_payloads: patchRequests,
        diagnostics,
      },
      null,
      2,
    ),
  );
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
