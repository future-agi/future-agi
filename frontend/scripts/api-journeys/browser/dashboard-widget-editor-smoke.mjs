/* eslint-disable no-console */
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/dashboard-widget-editor-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const runSuffix = auth.runId;
  const dashboardName = `QA widget editor ${runSuffix}`;
  const dashboardDescription = "Disposable dashboard for widget editor smoke.";
  const widgetName = `QA latency widget ${runSuffix}`;
  const updatedWidgetName = `QA latency widget edited ${runSuffix}`;
  const createdWidgetIds = new Set();
  let dashboardId = null;
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
  const metricRows = asArray(metricInventory?.metrics || metricInventory);
  assert(
    metricRows.some((metric) => metric?.name === "latency"),
    "Dashboard metric catalog did not include latency.",
  );

  const createdDashboard = await auth.client.post(
    apiPath("/tracer/dashboard/"),
    {
      name: dashboardName,
      description: dashboardDescription,
    },
  );
  dashboardId = createdDashboard?.id;
  assert(isUuid(dashboardId), "Dashboard create did not return a UUID id.");

  const evidence = {
    dashboard_id: dashboardId,
    dashboard_name: dashboardName,
    screenshot: SCREENSHOT_PATH,
    metric_catalog_latency_count: metricRows.filter(
      (metric) => metric?.name === "latency",
    ).length,
  };
  const apiFailures = [];
  const previewFailures = [];
  const pageErrors = [];
  const browserMutations = [];

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
      isDashboardWidgetMutationUrl(url, dashboardId) ||
      url.includes("/tracer/dashboard/query/")
    ) {
      browserMutations.push(`${request.method()} ${url}`);
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (url.includes("/tracer/dashboard/query/") && response.status() >= 400) {
      previewFailures.push(`${response.status()} ${url}`);
      return;
    }
    if (isDashboardEditorApiUrl(url, dashboardId) && response.status() >= 400) {
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
    await expectVisibleText(page, "Chart", { exact: true });
    await fillWidgetTitle(page, "Untitled widget", widgetName);
    await clickVisibleText(page, "Select Metric", { exact: true });
    await fillMetricSearch(page, "latency");
    await clickVisibleText(page, "Latency", { exact: true });
    await expectVisibleText(page, "Latency", { exact: true });

    await waitForResponseDuring(
      page,
      "create widget",
      (response) =>
        response.request().method() === "POST" &&
        response.url().includes(`/tracer/dashboard/${dashboardId}/widgets/`) &&
        !response.url().includes("/preview/") &&
        response.status() < 400,
      () => clickButtonText(page, "Save"),
    );

    const createdWidget = await waitForWidgetByName(
      auth.client,
      dashboardId,
      widgetName,
    );
    createdWidgetIds.add(createdWidget.id);
    evidence.created_widget_id = createdWidget.id;
    assertWidgetShape(createdWidget, {
      name: widgetName,
      chartType: "line",
      metricName: "latency",
    });
    await expectVisibleText(page, "Saved", { exact: true });
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    await fillWidgetTitle(page, widgetName, updatedWidgetName);
    await waitForSaveButton(page);
    await waitForResponseDuring(
      page,
      "update widget",
      (response) =>
        response.request().method() === "PATCH" &&
        response
          .url()
          .includes(
            `/tracer/dashboard/${dashboardId}/widgets/${createdWidget.id}/`,
          ) &&
        response.status() < 400,
      () => clickButtonText(page, "Save"),
    );
    const updatedWidget = await waitForWidgetByName(
      auth.client,
      dashboardId,
      updatedWidgetName,
    );
    assert(
      updatedWidget.id === createdWidget.id,
      "Widget rename created a second widget instead of updating the saved widget.",
    );
    assertWidgetShape(updatedWidget, {
      name: updatedWidgetName,
      chartType: "line",
      metricName: "latency",
    });

    await page.evaluate(() => {
      window.confirm = () => true;
    });
    await clickTopbarMoreButton(page);
    await waitForResponseDuring(
      page,
      "delete widget",
      (response) =>
        response.request().method() === "DELETE" &&
        response
          .url()
          .includes(
            `/tracer/dashboard/${dashboardId}/widgets/${createdWidget.id}/`,
          ) &&
        response.status() < 400,
      () => clickVisibleText(page, "Delete", { exact: true }),
    );
    createdWidgetIds.delete(createdWidget.id);
    await page.waitForFunction(
      (id) => window.location.pathname.endsWith(`/dashboard/dashboards/${id}`),
      { timeout: 30000 },
      dashboardId,
    );
    await expectVisibleText(page, "No widgets yet", { exact: true });
    const detailAfterDelete = await auth.client.get(
      apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
    );
    assert(
      !asArray(detailAfterDelete.widgets).some(
        (widget) => widget.id === createdWidget.id,
      ),
      "Deleted widget still appeared in dashboard detail.",
    );

    await auth.client.delete(
      apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
    );
    dashboardDeleted = true;
    evidence.deleted_widget_id = createdWidget.id;
    evidence.preview_failures_ignored = previewFailures.length;
    evidence.browser_mutation_count = browserMutations.length;

    assert(
      apiFailures.length === 0,
      `Dashboard editor API failures: ${apiFailures.join("; ")}`,
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
  } finally {
    await browser.close();
    for (const widgetId of createdWidgetIds) {
      if (!dashboardId) continue;
      try {
        await auth.client.delete(
          apiPath("/tracer/dashboard/{dashboard_pk}/widgets/{id}/", {
            dashboard_pk: dashboardId,
            id: widgetId,
          }),
        );
      } catch {
        // Best-effort cleanup; deleting the dashboard below also cascades.
      }
    }
    if (!dashboardDeleted && dashboardId) {
      await auth.client.delete(
        apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
      );
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

function assertWidgetShape(widget, { name, chartType, metricName }) {
  const queryConfig = widget.query_config || widget.queryConfig || {};
  const chartConfig = widget.chart_config || widget.chartConfig || {};
  assert(widget.name === name, `Widget name mismatch for ${name}.`);
  assert(
    (chartConfig.chart_type || chartConfig.chartType) === chartType,
    `Widget chart type was not ${chartType}.`,
  );
  assert(
    asArray(queryConfig.metrics).some(
      (metric) => metric.name === metricName || metric.id === metricName,
    ),
    `Widget query_config did not include metric ${metricName}.`,
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

async function fillMetricSearch(page, query) {
  const selector = 'input[placeholder^="Search metrics"]';
  await page.waitForSelector(selector, { visible: true, timeout: 10000 });
  await page.click(selector, { clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.type(selector, query);
  await page.waitForFunction(
    () => {
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
        return element.textContent.trim() === "Latency";
      });
    },
    { timeout: 15000 },
  );
}

async function clickTopbarMoreButton(page) {
  await page.waitForFunction(
    () => {
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
      const buttons = Array.from(document.querySelectorAll("button"));
      return buttons.some((button) => {
        if (!isVisible(button)) return false;
        const rect = button.getBoundingClientRect();
        return (
          !button.textContent.trim() &&
          rect.top < 90 &&
          rect.right > window.innerWidth - 360
        );
      });
    },
    { timeout: 10000 },
  );
  await page.evaluate(() => {
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
    const buttons = Array.from(document.querySelectorAll("button"));
    const button = buttons.find((candidate) => {
      if (!isVisible(candidate)) return false;
      const rect = candidate.getBoundingClientRect();
      return (
        !candidate.textContent.trim() &&
        rect.top < 90 &&
        rect.right > window.innerWidth - 360
      );
    });
    if (!button) throw new Error("Topbar more button was not found.");
    button.click();
  });
  await expectVisibleText(page, "Delete", { exact: true });
}

async function waitForSaveButton(page) {
  await page.waitForFunction(
    () => {
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
        (button) => isVisible(button) && button.textContent.trim() === "Save",
      );
    },
    { timeout: 5000 },
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

function isDashboardEditorApiUrl(url, dashboardId) {
  if (!dashboardId) return false;
  return (
    url.includes(`/tracer/dashboard/${dashboardId}/`) ||
    url.includes("/tracer/dashboard/metrics/")
  );
}

function isDashboardWidgetMutationUrl(url, dashboardId) {
  if (!dashboardId) return false;
  return url.includes(`/tracer/dashboard/${dashboardId}/widgets/`);
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
