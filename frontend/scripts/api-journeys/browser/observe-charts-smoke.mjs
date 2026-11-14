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
const SCREENSHOT_PATH = "/tmp/observe-charts-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const { project, graph } = await selectObserveChartsProject(auth);
  const sevenDayGraph = await auth.client.get(
    apiPath("/tracer/project/get_graph_data/"),
    {
      query: {
        project_id: project.id,
        interval: "day",
        filters: JSON.stringify([dateFilter(7)]),
      },
    },
  );
  const thirtyDaySummary = assertObserveChartsGraph(graph, "30D preflight");
  const sevenDaySummary = assertObserveChartsGraph(
    sevenDayGraph,
    "7D preflight",
  );

  const evalNames = asArray(
    await auth.client.get(apiPath("/tracer/trace/get_eval_names/"), {
      query: { project_id: project.id },
    }),
  );

  const evidence = {
    project_id: project.id,
    project_name: project.name,
    thirty_day_points: thirtyDaySummary,
    seven_day_points: sevenDaySummary,
    evaluation_metric_count: evalNames.length,
  };
  const apiFailures = [];
  const pageErrors = [];
  const unexpectedMutations = [];

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
      isObserveChartsApiUrl(url) &&
      ["POST", "PATCH", "PUT", "DELETE"].includes(request.method())
    ) {
      unexpectedMutations.push(`${request.method()} ${url}`);
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (isObserveChartsApiUrl(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await waitForResponseDuring(
      page,
      "initial observe charts graph",
      (response) =>
        response.request().method() === "GET" &&
        response.url().includes("/tracer/project/get_graph_data/") &&
        response.url().includes(`project_id=${project.id}`) &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/observe/${project.id}/charts`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await page.waitForFunction(
      (projectId) =>
        window.location.pathname.endsWith(
          `/dashboard/observe/${projectId}/charts`,
        ),
      { timeout: 30000 },
      project.id,
    );

    await expectVisibleText(page, project.name, { exact: true });
    await expectVisibleText(page, "Charts", { exact: true });
    await expectVisibleText(page, "Custom", { exact: true });
    await expectVisibleText(page, "Today", { exact: true });
    await expectVisibleText(page, "Yesterday", { exact: true });
    await expectVisibleText(page, "7D", { exact: true });
    await expectVisibleText(page, "30D", { exact: true });
    await expectVisibleText(page, "Refresh", { exact: true });
    await expectVisibleText(page, "View Traces", { exact: true });
    await expectVisibleText(page, "Day", { exact: true });
    await expectVisibleText(page, "System Metrics", { exact: true });
    for (const label of ["Latency", "Tokens", "Traffic", "Cost"]) {
      await expectVisibleText(page, label, { exact: true });
    }
    await expectNoVisibleText(page, "Invalid Date");

    await waitForResponseDuring(
      page,
      "7D charts graph",
      (response) =>
        response.request().method() === "GET" &&
        response.url().includes("/tracer/project/get_graph_data/") &&
        response.url().includes(`project_id=${project.id}`) &&
        response.url().includes("interval=day") &&
        response.status() < 400,
      () => clickVisibleText(page, "7D"),
    );
    await expectVisibleText(page, "System Metrics", { exact: true });

    await waitForResponseDuring(
      page,
      "manual chart refresh",
      (response) =>
        response.request().method() === "GET" &&
        response.url().includes("/tracer/project/get_graph_data/") &&
        response.url().includes(`project_id=${project.id}`) &&
        response.status() < 400,
      () => clickVisibleText(page, "Refresh"),
    );

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    await clickVisibleText(page, "View Traces");
    await page.waitForFunction(
      (projectId) =>
        window.location.pathname.endsWith(
          `/dashboard/observe/${projectId}/llm-tracing`,
        ),
      { timeout: 30000 },
      project.id,
    );
    evidence.trace_navigation_path = await page.evaluate(
      () => `${window.location.pathname}${window.location.search}`,
    );
    assert(
      evidence.trace_navigation_path.includes("primaryTraceDateFilter="),
      "View Traces navigation did not preserve the chart date filter query.",
    );

    assert(
      apiFailures.length === 0,
      `Chart API failures: ${apiFailures.join("; ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Read-only Observe Charts smoke fired mutations: ${unexpectedMutations.join("; ")}`,
    );

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
  }
}

async function selectObserveChartsProject(auth) {
  const list = await auth.client.get(
    apiPath("/tracer/project/list_projects/"),
    {
      query: {
        project_type: "observe",
        page_number: 0,
        page_size: 25,
        sort_by: "updated_at",
        sort_direction: "desc",
      },
    },
  );
  const projects = asArray(list);
  for (const project of projects) {
    if (!isUuid(project?.id)) continue;
    const detail = await auth.client.get(
      apiPath("/tracer/project/{id}/", { id: project.id }),
    );
    if (
      detail?.trace_type !== "observe" ||
      detail?.workspace !== auth.workspaceId
    ) {
      continue;
    }
    const graph = await auth.client.get(
      apiPath("/tracer/project/get_graph_data/"),
      {
        query: {
          project_id: project.id,
          interval: "day",
          filters: JSON.stringify([dateFilter(30)]),
        },
      },
    );
    assertObserveChartsGraph(graph, `project ${project.id}`);
    return { project, graph };
  }
  throw new Error(
    "No current-workspace observe project with chart data was found.",
  );
}

function dateFilter(days) {
  const end = new Date();
  end.setDate(end.getDate() + 1);
  const start = new Date();
  start.setDate(start.getDate() - days);
  return {
    column_id: "created_at",
    filter_config: {
      filter_type: "datetime",
      filter_op: "between",
      filter_value: [start.toISOString(), end.toISOString()],
    },
  };
}

function assertObserveChartsGraph(graph, label) {
  const systemMetrics = graph?.system_metrics || {};
  const summary = {};
  for (const [metric, valueKey] of [
    ["latency", "latency"],
    ["tokens", "tokens"],
    ["traffic", "traffic"],
    ["cost", "cost"],
  ]) {
    const rows = asArray(systemMetrics[metric]);
    assert(rows.length > 0, `${label} chart omitted ${metric} buckets.`);
    for (const row of rows) {
      assert(row?.timestamp, `${label} ${metric} row omitted timestamp.`);
      assert(
        !Number.isNaN(Date.parse(row.timestamp)),
        `${label} ${metric} row returned invalid timestamp ${row.timestamp}.`,
      );
      assert(
        Number.isFinite(Number(row[valueKey] ?? row.value ?? 0)),
        `${label} ${metric} row returned non-numeric value.`,
      );
    }
    summary[`${metric}_points`] = rows.length;
    summary[`${metric}_sum`] = rows.reduce(
      (total, row) => total + Number(row[valueKey] ?? row.value ?? 0),
      0,
    );
  }
  return summary;
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

async function waitForResponseDuring(page, label, predicate, action) {
  const responsePromise = page.waitForResponse(predicate, { timeout: 60000 });
  await action();
  try {
    return await responsePromise;
  } catch (error) {
    throw new Error(`${label} did not complete: ${error.message}`);
  }
}

async function expectVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
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
        const textContent = normalized(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

async function expectNoVisibleText(
  page,
  text,
  { exact = false, timeout = 10000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
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
        (element) => {
          if (!isVisible(element)) return false;
          const textContent = normalized(element.textContent);
          return exactMatch
            ? textContent === expectedText
            : textContent.includes(expectedText);
        },
      );
    },
    { timeout },
    { text, exact },
  );
}

async function clickVisibleText(page, text) {
  await expectVisibleText(page, text, { exact: true });
  const clicked = await page.evaluate((expectedText) => {
    const normalized = (value) => String(value || "").trim();
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
    const element = Array.from(document.querySelectorAll("body *")).find(
      (candidate) =>
        isVisible(candidate) &&
        normalized(candidate.textContent) === expectedText,
    );
    const clickable =
      element?.closest("button,[role='button'],a,[role='tab']") || element;
    if (!clickable) return false;
    clickable.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    clickable.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    clickable.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return true;
  }, text);
  assert(clicked, `Could not click visible text ${text}.`);
}

function isObserveChartsApiUrl(url) {
  return (
    url.includes("/tracer/project/get_graph_data/") ||
    url.includes("/tracer/trace/get_eval_names/") ||
    url.includes("/tracer/charts/fetch_graph/")
  );
}

function browserExecutablePath() {
  return (
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  );
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
