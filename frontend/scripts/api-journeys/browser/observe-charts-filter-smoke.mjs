/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
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
const SCREENSHOT_PATH = "/tmp/observe-charts-filter-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/observe-charts-filter-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const fixture = await createChartsFilterFixture(auth);
  let cleanupDone = false;
  const evidence = {
    project_id: fixture.projectId,
    project_name: fixture.projectName,
    project_version_id: fixture.projectVersionId,
    session_id: fixture.sessionId,
    trace_a_id: fixture.traceAId,
    trace_b_id: fixture.traceBId,
    span_ids: fixture.spanIds,
    api_baseline_traffic_sum: fixture.baselineSummary.traffic_sum,
    api_trace_filter_traffic_sum: fixture.traceSummary.traffic_sum,
    seed_audit: fixture.seedAudit,
  };
  const apiFailures = [];
  const pageErrors = [];
  const graphRequests = [];

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
    if (isChartsGraphUrl(request.url())) {
      graphRequests.push({
        method: request.method(),
        url: request.url(),
        filters: readFiltersFromGraphUrl(request.url()),
      });
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (
      (isChartsGraphUrl(url) ||
        url.includes("/tracer/dashboard/metrics/") ||
        url.includes("/tracer/dashboard/filter_values/")) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const initialResponse = await waitForGraphResponseDuring(
      page,
      "initial charts graph",
      fixture.projectId,
      (filters) => !hasFilter(filters, "trace_id"),
      () =>
        page.goto(`${APP_BASE}/dashboard/observe/${fixture.projectId}/charts`, {
          waitUntil: "domcontentloaded",
        }),
    );
    const initialSummary = assertObserveChartsGraph(
      await readGraphBody(initialResponse),
      "browser initial chart",
    );
    await waitForChartTotal(
      page,
      "Traffic",
      fixture.baselineSummary.traffic_sum,
    );
    evidence.browser_initial_traffic_sum = initialSummary.traffic_sum;
    evidence.initial_chart_total = await readChartTotal(page, "Traffic");

    await clickVisibleText(page, "Filter");
    await expectVisibleText(page, "Property", { exact: true });
    await clickVisibleText(page, "Property");
    await fillInputByPlaceholder(page, "Search properties...", "Trace ID");
    await clickFilterPropertyOption(page, "trace_id");
    await clickFilterValueTrigger(page, "trace_id");
    await chooseFilterValue(page, fixture.traceAId);
    await page.keyboard.press("Escape");

    const filteredResponse = await waitForGraphResponseDuring(
      page,
      "trace-filtered charts graph",
      fixture.projectId,
      (filters) => hasFilter(filters, "trace_id"),
      () => clickFilterPanelAction(page, "apply"),
    );
    const filteredSummary = assertObserveChartsGraph(
      await readGraphBody(filteredResponse),
      "browser trace-filtered chart",
    );
    await waitForChartTotal(page, "Traffic", fixture.traceSummary.traffic_sum);
    await waitForElementByDataAttribute(
      page,
      "data-filter-chip-column",
      "trace_id",
    );
    evidence.browser_trace_filter_traffic_sum = filteredSummary.traffic_sum;
    evidence.filtered_chart_total = await readChartTotal(page, "Traffic");

    await clickElementByDataAttribute(
      page,
      "data-filter-chips-action",
      "clear",
      "filter chips clear action",
    );
    await waitForElementByDataAttributeToDisappear(
      page,
      "data-filter-chip-column",
      "trace_id",
    );
    await waitForChartTotal(
      page,
      "Traffic",
      fixture.baselineSummary.traffic_sum,
    );
    evidence.browser_cleared_traffic_sum = fixture.baselineSummary.traffic_sum;
    evidence.cleared_chart_total = await readChartTotal(page, "Traffic");

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;
    evidence.browser_graph_request_count = graphRequests.length;
    evidence.browser_graph_filters = graphRequests.map((request) =>
      request.filters.map((filter) => filter.column_id),
    );

    const cleanupAudit = await cleanupChartsFilterFixture(fixture);
    cleanupDone = true;
    evidence.cleanup = cleanupAudit;
    assert(
      cleanupAudit.remaining_project_count === 0 &&
        cleanupAudit.remaining_project_version_count === 0 &&
        cleanupAudit.remaining_session_count === 0 &&
        cleanupAudit.remaining_trace_count === 0 &&
        cleanupAudit.remaining_span_count === 0,
      "Charts filter browser cleanup left disposable residue.",
    );

    assert(
      initialSummary.traffic_sum === fixture.baselineSummary.traffic_sum,
      "Browser initial chart traffic did not match API baseline.",
    );
    assert(
      filteredSummary.traffic_sum === fixture.traceSummary.traffic_sum,
      "Browser trace-filtered chart traffic did not match API trace filter.",
    );
    assert(
      evidence.cleared_chart_total === fixture.baselineSummary.traffic_sum,
      "Browser cleared chart traffic did not return to API baseline.",
    );
    assert(
      apiFailures.length === 0,
      `Observe chart API failures: ${apiFailures.join("; ")}`,
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
  } catch (error) {
    await page
      .screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
    throw error;
  } finally {
    await browser.close();
    if (!cleanupDone) {
      await cleanupChartsFilterFixture(fixture).catch((error) => {
        console.error(`Cleanup failed: ${error.message}`);
      });
    }
  }
}

async function createChartsFilterFixture(auth) {
  const suffix = auth.runId;
  const projectName = `browser charts filters ${suffix}`;
  const createdProject = await auth.client.post(apiPath("/tracer/project/"), {
    name: projectName,
    model_type: "GenerativeLLM",
    trace_type: "observe",
    metadata: { source: "browser-smoke", run_id: suffix },
  });
  const projectId = createdProject.project_id || createdProject.id;
  assert(isUuid(projectId), "Charts filter project create returned no id.");

  const projectVersion = await auth.client.post(
    apiPath("/tracer/project-version/"),
    {
      project: projectId,
      name: `browser charts filters run ${suffix}`,
      metadata: { source: "browser-smoke", run_id: suffix },
    },
  );
  const projectVersionId =
    projectVersion.project_version_id || projectVersion.id;
  assert(
    isUuid(projectVersionId),
    "Charts filter project-version create returned no id.",
  );

  const createdSession = await auth.client.post(
    apiPath("/tracer/trace-session/"),
    {
      project: projectId,
      name: `browser charts filters session ${suffix}`,
      bookmarked: false,
    },
  );
  const sessionId = createdSession.id || createdSession.trace_session_id;
  assert(isUuid(sessionId), "Charts filter session create returned no id.");

  const traceA = await auth.client.post(
    apiPath("/tracer/trace/"),
    traceWritePayload({
      projectId,
      projectVersionId,
      sessionId,
      name: `browser charts filters trace A ${suffix}`,
      runId: suffix,
      marker: "chart-a",
    }),
  );
  const traceAId = traceA.id || traceA.trace_id || traceA.trace?.id;
  assert(isUuid(traceAId), "Charts filter trace A create returned no id.");

  const traceB = await auth.client.post(
    apiPath("/tracer/trace/"),
    traceWritePayload({
      projectId,
      projectVersionId,
      name: `browser charts filters trace B ${suffix}`,
      runId: suffix,
      marker: "chart-b",
    }),
  );
  const traceBId = traceB.id || traceB.trace_id || traceB.trace?.id;
  assert(isUuid(traceBId), "Charts filter trace B create returned no id.");

  const now = new Date();
  const spanAId = `browser_chart_a_${suffix}`;
  const spanAChildId = `browser_chart_a_child_${suffix}`;
  const spanBId = `browser_chart_b_${suffix}`;
  const spanSeeds = [
    {
      id: spanAId,
      traceId: traceAId,
      parentSpanId: null,
      name: `browser chart target ${suffix}`,
      marker: "target",
      latencyMs: 100,
      totalTokens: 5,
      cost: 0.01,
      startTime: new Date(now.getTime() - 600).toISOString(),
    },
    {
      id: spanAChildId,
      traceId: traceAId,
      parentSpanId: spanAId,
      name: `browser chart peer ${suffix}`,
      marker: "peer",
      latencyMs: 200,
      totalTokens: 10,
      cost: 0.02,
      startTime: new Date(now.getTime() - 500).toISOString(),
    },
    {
      id: spanBId,
      traceId: traceBId,
      parentSpanId: null,
      name: `browser chart other ${suffix}`,
      marker: "other",
      latencyMs: 300,
      totalTokens: 20,
      cost: 0.03,
      startTime: new Date(now.getTime() - 400).toISOString(),
    },
  ];
  const spanEnd = now.toISOString();
  const observationSpans = spanSeeds.map((seed) => {
    const payload = observationSpanWritePayload({
      id: seed.id,
      projectId,
      projectVersionId,
      traceId: seed.traceId,
      parentSpanId: seed.parentSpanId,
      name: seed.name,
      runId: suffix,
      startTime: seed.startTime,
      endTime: spanEnd,
      metadata: {
        source: "browser-smoke",
        run_id: suffix,
        chart_filter_marker: seed.marker,
      },
    });
    payload.latency_ms = seed.latencyMs;
    payload.prompt_tokens = Math.floor(seed.totalTokens / 2);
    payload.completion_tokens =
      seed.totalTokens - Math.floor(seed.totalTokens / 2);
    payload.total_tokens = seed.totalTokens;
    payload.cost = seed.cost;
    payload.span_attributes = {
      browser_chart_marker: `${seed.marker}-${suffix}`,
    };
    return payload;
  });
  const bulkSpanResult = await auth.client.post(
    apiPath("/tracer/observation-span/bulk_create/"),
    { observation_spans: observationSpans },
  );
  const createdSpanIds = Array.isArray(bulkSpanResult?.["Observation Span IDs"])
    ? bulkSpanResult["Observation Span IDs"]
    : [];
  for (const seed of spanSeeds) {
    assert(
      createdSpanIds.includes(seed.id),
      `Charts filter span ${seed.marker} was not bulk-created.`,
    );
  }

  const spanIds = [spanAId, spanAChildId, spanBId];
  const seedAudit = await loadChartsFilterDbAudit({
    projectId,
    projectVersionId,
    sessionId,
    traceIds: [traceAId, traceBId],
    spanIds,
  });
  assertChartsFilterDbAudit(seedAudit, {
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
    projectId,
    projectVersionId,
    expectedSpanCount: 3,
    expectedTraceCount: 2,
  });

  const baselineSummary = assertObserveChartsGraph(
    await getObserveChartsGraph(auth.client, projectId, [chartDateFilter(1)]),
    "chart filter baseline",
  );
  const traceSummary = assertObserveChartsGraph(
    await getObserveChartsGraph(auth.client, projectId, [
      chartDateFilter(1),
      systemFilter("trace_id", "text", "equals", traceAId),
    ]),
    "chart trace filter",
  );
  assert(
    baselineSummary.traffic_sum === 3,
    `Baseline chart traffic expected 3 seeded spans, got ${baselineSummary.traffic_sum}.`,
  );
  assert(
    traceSummary.traffic_sum === 2,
    `Trace chart filter expected 2 spans, got ${traceSummary.traffic_sum}.`,
  );

  return {
    projectId,
    projectName,
    projectVersionId,
    sessionId,
    traceAId,
    traceBId,
    spanIds,
    baselineSummary,
    traceSummary,
    seedAudit,
    evalLogIds: [randomUUID()],
  };
}

async function cleanupChartsFilterFixture(fixture) {
  return hardDeleteTraceSessionLifecycleArtifacts({
    projectId: fixture.projectId,
    projectVersionId: fixture.projectVersionId,
    sessionId: fixture.sessionId,
    traceIds: [fixture.traceAId, fixture.traceBId],
    spanIds: fixture.spanIds,
    evalLogIds: fixture.evalLogIds,
  });
}

async function getObserveChartsGraph(client, projectId, filters) {
  return client.get(apiPath("/tracer/project/get_graph_data/"), {
    query: {
      project_id: projectId,
      interval: "day",
      filters: JSON.stringify(filters),
    },
  });
}

function chartDateFilter(days) {
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

function systemFilter(columnId, filterType, filterOp, filterValue) {
  return {
    column_id: columnId,
    filter_config: {
      filter_type: filterType,
      filter_op: filterOp,
      filter_value: filterValue,
    },
  };
}

function assertObserveChartsGraph(graphPayload, label) {
  const graph = graphPayload?.result || graphPayload;
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

async function waitForGraphResponseDuring(
  page,
  label,
  projectId,
  filterPredicate,
  action,
) {
  const responsePromise = page.waitForResponse(
    (response) => {
      if (
        response.request().method() !== "GET" ||
        !isChartsGraphUrl(response.url()) ||
        response.status() >= 400
      ) {
        return false;
      }
      const url = new URL(response.url());
      if (url.searchParams.get("project_id") !== projectId) return false;
      return filterPredicate(readFiltersFromGraphUrl(response.url()));
    },
    { timeout: 60000 },
  );
  await action();
  try {
    return await responsePromise;
  } catch (error) {
    throw new Error(`${label} did not complete: ${error.message}`);
  }
}

async function readGraphBody(response) {
  const body = await response.json();
  return body?.result || body;
}

function isChartsGraphUrl(url) {
  return url.includes("/tracer/project/get_graph_data/");
}

function readFiltersFromGraphUrl(url) {
  const params = new URL(url).searchParams;
  const rawFilters = params.get("filters");
  if (!rawFilters) return [];
  try {
    const parsed = JSON.parse(rawFilters);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function hasFilter(filters, columnId) {
  return asArray(filters).some((filter) => filter?.column_id === columnId);
}

async function chooseFilterValue(page, value) {
  await fillInputByPlaceholder(page, "Search values...", value);
  await new Promise((resolve) => setTimeout(resolve, 250));
  await clickElementByDataAttribute(
    page,
    "data-filter-value-option",
    value,
    `filter value ${value}`,
  );
}

async function clickFilterPropertyOption(page, propertyId) {
  await clickElementByDataAttribute(
    page,
    "data-filter-property-option",
    propertyId,
    `filter property ${propertyId}`,
  );
}

async function clickFilterValueTrigger(page, propertyId) {
  await clickElementByDataAttribute(
    page,
    "data-filter-value-trigger",
    propertyId,
    `filter value trigger ${propertyId}`,
  );
}

async function clickFilterPanelAction(page, action) {
  await clickElementByDataAttribute(
    page,
    "data-filter-panel-action",
    action,
    `filter panel action ${action}`,
  );
}

async function clickElementByDataAttribute(page, attr, value, description) {
  await waitForElementByDataAttribute(page, attr, value);

  const handle = await page.evaluateHandle(
    ({ attr: dataAttr, value: expectedValue }) => {
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
      return (
        Array.from(document.querySelectorAll(`[${dataAttr}]`)).find(
          (element) =>
            isVisible(element) &&
            element.getAttribute(dataAttr) === expectedValue,
        ) || null
      );
    },
    { attr, value },
  );
  const element = handle.asElement();
  assert(element, `Could not find ${description}.`);
  await element.evaluate((node) => node.click());
}

async function waitForElementByDataAttribute(page, attr, value) {
  await page.waitForFunction(
    ({ attr: dataAttr, value: expectedValue }) => {
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
      return Array.from(document.querySelectorAll(`[${dataAttr}]`)).some(
        (element) =>
          isVisible(element) &&
          element.getAttribute(dataAttr) === expectedValue,
      );
    },
    { timeout: 30000 },
    { attr, value },
  );
}

async function waitForElementByDataAttributeToDisappear(page, attr, value) {
  await page.waitForFunction(
    ({ attr: dataAttr, value: expectedValue }) => {
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
      return !Array.from(document.querySelectorAll(`[${dataAttr}]`)).some(
        (element) =>
          isVisible(element) &&
          element.getAttribute(dataAttr) === expectedValue,
      );
    },
    { timeout: 30000 },
    { attr, value },
  );
}

async function waitForChartTotal(page, label, expected) {
  await page.waitForFunction(
    ({ label: chartLabel, expectedTotal }) => {
      const chart = document.querySelector(
        `[data-chart-label="${chartLabel}"]`,
      );
      if (!chart) return false;
      const total = Number(chart.getAttribute("data-chart-total"));
      return total === expectedTotal;
    },
    { timeout: 30000 },
    { label, expectedTotal: expected },
  );
}

async function readChartTotal(page, label) {
  return page.evaluate((chartLabel) => {
    const chart = document.querySelector(`[data-chart-label="${chartLabel}"]`);
    return Number(chart?.getAttribute("data-chart-total"));
  }, label);
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

async function clickVisibleText(page, text) {
  await expectVisibleText(page, text, { exact: true });
  const handle = await page.evaluateHandle((expectedText) => {
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
        candidate.tagName !== "SCRIPT" &&
        candidate.tagName !== "STYLE" &&
        isVisible(candidate) &&
        normalized(candidate.textContent) === expectedText,
    );
    return (
      element?.closest(
        "button,[role='button'],[role='option'],a,[role='tab'],li",
      ) ||
      element ||
      null
    );
  }, text);
  const element = handle.asElement();
  assert(element, `Could not click visible text ${text}.`);
  await element.click();
}

async function fillInputByPlaceholder(page, placeholder, value) {
  await page.waitForFunction(
    (expectedPlaceholder) =>
      Array.from(document.querySelectorAll("input,textarea")).some(
        (element) => {
          const style = window.getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          return (
            element.getAttribute("placeholder") === expectedPlaceholder &&
            style.visibility !== "hidden" &&
            style.display !== "none" &&
            rect.width > 0 &&
            rect.height > 0
          );
        },
      ),
    { timeout: 30000 },
    placeholder,
  );
  const inputHandle = await page.evaluateHandle((expectedPlaceholder) => {
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
    return Array.from(document.querySelectorAll("input,textarea")).find(
      (element) =>
        element.getAttribute("placeholder") === expectedPlaceholder &&
        isVisible(element),
    );
  }, placeholder);
  const input = inputHandle.asElement();
  assert(input, `Could not find input with placeholder ${placeholder}.`);
  await input.click({ clickCount: 3 });
  await page.keyboard.press("Backspace");
  await input.type(value);
  const filled = await page.evaluate(
    ({ expectedPlaceholder, nextValue }) =>
      Array.from(document.querySelectorAll("input,textarea")).some(
        (element) =>
          element.getAttribute("placeholder") === expectedPlaceholder &&
          element.value === nextValue,
      ),
    { expectedPlaceholder: placeholder, nextValue: value },
  );
  assert(filled, `Could not fill input with placeholder ${placeholder}.`);
}

function browserExecutablePath() {
  return (
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  );
}

function traceWritePayload({
  projectId,
  projectVersionId,
  sessionId,
  name,
  runId,
  marker,
}) {
  return {
    project: projectId,
    project_version: projectVersionId,
    ...(sessionId ? { session: sessionId } : {}),
    name,
    metadata: { source: "browser-smoke", run_id: runId, trace_marker: marker },
    input: {
      prompt: `browser smoke trace ${marker}`,
      shared: `browser smoke trace shared ${runId}`,
    },
    output: { response: `browser smoke trace response ${marker}` },
    error: null,
    tags: ["browser-smoke", `trace-${marker}`],
  };
}

function observationSpanWritePayload({
  id,
  projectId,
  projectVersionId,
  traceId,
  parentSpanId,
  name,
  runId,
  startTime,
  endTime,
  metadata = { source: "browser-smoke", run_id: runId },
}) {
  return {
    id,
    project: projectId,
    project_version: projectVersionId,
    trace: traceId,
    ...(parentSpanId ? { parent_span_id: parentSpanId } : {}),
    name,
    observation_type: "llm",
    start_time: startTime,
    end_time: endTime,
    input: { messages: [{ role: "user", content: "browser smoke input" }] },
    output: {
      choices: [{ message: { content: "browser smoke output" } }],
    },
    model: "browser-smoke-model",
    prompt_tokens: 2,
    completion_tokens: 3,
    total_tokens: 5,
    latency_ms: 123,
    cost: 0,
    status: "OK",
    status_message: "browser smoke span",
    tags: ["browser-smoke"],
    metadata,
  };
}

async function loadChartsFilterDbAudit({
  projectId,
  projectVersionId,
  sessionId,
  traceIds,
  spanIds,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuid(projectVersionId)} AS project_version_id,
    ${sqlUuid(sessionId)} AS session_id,
    ${sqlUuidArray(traceIds)} AS trace_ids,
    ${sqlStringArray(spanIds)} AS span_ids
)
SELECT json_build_object(
  'project_id', project.id::text,
  'project_organization_id', project.organization_id::text,
  'project_workspace_id', project.workspace_id::text,
  'project_deleted', project.deleted,
  'project_version_id', pv.id::text,
  'project_version_project_id', pv.project_id::text,
  'session_count', (
    SELECT count(*) FROM trace_session session, requested r
    WHERE session.id = r.session_id
      AND session.project_id = r.project_id
      AND session.deleted = false
  ),
  'trace_count', (
    SELECT count(*) FROM tracer_trace trace, requested r
    WHERE trace.id = ANY(r.trace_ids)
      AND trace.project_id = r.project_id
      AND trace.deleted = false
  ),
  'session_trace_count', (
    SELECT count(*) FROM tracer_trace trace, requested r
    WHERE trace.id = ANY(r.trace_ids)
      AND trace.session_id = r.session_id
      AND trace.deleted = false
  ),
  'span_count', (
    SELECT count(*) FROM tracer_observation_span span, requested r
    WHERE span.id = ANY(r.span_ids)
      AND span.project_id = r.project_id
      AND span.project_version_id = r.project_version_id
      AND span.deleted = false
  ),
  'span_attribute_count', (
    SELECT count(*) FROM tracer_observation_span span, requested r
    WHERE span.id = ANY(r.span_ids)
      AND span.span_attributes ? 'browser_chart_marker'
      AND span.deleted = false
  )
)
FROM requested r
JOIN tracer_project project ON project.id = r.project_id
JOIN tracer_project_version pv ON pv.id = r.project_version_id;
`;
  return runPostgresJson(sql);
}

function assertChartsFilterDbAudit(
  audit,
  {
    organizationId,
    workspaceId,
    projectId,
    projectVersionId,
    expectedSpanCount,
    expectedTraceCount,
  },
) {
  assert(audit?.project_id === projectId, "Chart audit project id mismatch.");
  assert(
    audit?.project_organization_id === organizationId,
    "Chart audit project organization mismatch.",
  );
  assert(
    audit?.project_workspace_id === workspaceId,
    "Chart audit project workspace mismatch.",
  );
  assert(
    audit?.project_version_id === projectVersionId &&
      audit?.project_version_project_id === projectId,
    "Chart audit project-version mismatch.",
  );
  assert(Number(audit?.session_count) === 1, "Chart audit missed session.");
  assert(
    Number(audit?.trace_count) === expectedTraceCount,
    "Chart audit trace count mismatch.",
  );
  assert(
    Number(audit?.session_trace_count) === 1,
    "Chart audit session trace count mismatch.",
  );
  assert(
    Number(audit?.span_count) === expectedSpanCount,
    "Chart audit span count mismatch.",
  );
  assert(
    Number(audit?.span_attribute_count) === expectedSpanCount,
    "Chart audit span attribute count mismatch.",
  );
}

async function hardDeleteTraceSessionLifecycleArtifacts({
  projectId,
  projectVersionId,
  sessionId,
  traceIds,
  spanIds,
  evalLogIds,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuid(projectVersionId)} AS project_version_id,
    ${sqlUuid(sessionId)} AS session_id,
    ${sqlUuidArray(traceIds)} AS trace_ids,
    ${sqlStringArray(spanIds)} AS span_ids,
    ${sqlUuidArray(evalLogIds)} AS eval_log_ids
),
deleted_eval_logs AS (
  DELETE FROM tracer_eval_logger eval_log
  USING requested r
  WHERE eval_log.id = ANY(r.eval_log_ids)
     OR eval_log.trace_session_id = r.session_id
     OR eval_log.trace_id = ANY(r.trace_ids)
  RETURNING eval_log.id
),
deleted_replay_sessions AS (
  DELETE FROM tracer_replaysession replay
  USING requested r
  WHERE replay.project_id = r.project_id
  RETURNING replay.id
),
deleted_spans AS (
  DELETE FROM tracer_observation_span span
  USING requested r
  WHERE span.id = ANY(r.span_ids) OR span.trace_id = ANY(r.trace_ids)
  RETURNING span.id
),
deleted_traces AS (
  DELETE FROM tracer_trace trace
  USING requested r
  WHERE trace.id = ANY(r.trace_ids) OR trace.session_id = r.session_id
  RETURNING trace.id
),
deleted_sessions AS (
  DELETE FROM trace_session session
  USING requested r
  WHERE session.id = r.session_id
  RETURNING session.id
),
deleted_project_versions AS (
  DELETE FROM tracer_project_version pv
  USING requested r
  WHERE pv.id = r.project_version_id
  RETURNING pv.id
),
deleted_projects AS (
  DELETE FROM tracer_project project
  USING requested r
  WHERE project.id = r.project_id
  RETURNING project.id
)
SELECT json_build_object(
  'deleted_eval_log_count', (SELECT count(*) FROM deleted_eval_logs),
  'deleted_replay_session_count', (SELECT count(*) FROM deleted_replay_sessions),
  'deleted_span_count', (SELECT count(*) FROM deleted_spans),
  'deleted_trace_count', (SELECT count(*) FROM deleted_traces),
  'deleted_session_count', (SELECT count(*) FROM deleted_sessions),
  'deleted_project_version_count', (SELECT count(*) FROM deleted_project_versions),
  'deleted_project_count', (SELECT count(*) FROM deleted_projects),
  'remaining_eval_log_count', CASE
    WHEN (SELECT count(*) FROM deleted_eval_logs) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_eval_logger eval_log, requested r
      WHERE eval_log.id = ANY(r.eval_log_ids)
         OR eval_log.trace_session_id = r.session_id
         OR eval_log.trace_id = ANY(r.trace_ids)
    )
  END,
  'remaining_replay_session_count', CASE
    WHEN (SELECT count(*) FROM deleted_replay_sessions) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_replaysession replay, requested r
      WHERE replay.project_id = r.project_id
    )
  END,
  'remaining_span_count', CASE
    WHEN (SELECT count(*) FROM deleted_spans) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_observation_span span, requested r
      WHERE span.id = ANY(r.span_ids) OR span.trace_id = ANY(r.trace_ids)
    )
  END,
  'remaining_trace_count', CASE
    WHEN (SELECT count(*) FROM deleted_traces) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_trace trace, requested r
      WHERE trace.id = ANY(r.trace_ids) OR trace.session_id = r.session_id
    )
  END,
  'remaining_session_count', CASE
    WHEN (SELECT count(*) FROM deleted_sessions) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM trace_session session, requested r
      WHERE session.id = r.session_id
    )
  END,
  'remaining_project_version_count', CASE
    WHEN (SELECT count(*) FROM deleted_project_versions) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_project_version pv, requested r
      WHERE pv.id = r.project_version_id
    )
  END,
  'remaining_project_count', CASE
    WHEN (SELECT count(*) FROM deleted_projects) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_project project, requested r
      WHERE project.id = r.project_id
    )
  END
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
    ["exec", container, "psql", "-qAt", "-U", user, "-d", database, "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const line = stdout.trim().split(/\r?\n/).find(Boolean);
  assert(line, "Postgres DB audit returned no JSON output.");
  return JSON.parse(line);
}

function sqlUuid(value) {
  assert(isUuid(value), `Expected UUID value, got ${value}.`);
  return `'${value}'::uuid`;
}

function sqlUuidArray(values) {
  const rows = asArray(values);
  assert(rows.length > 0, "SQL UUID array must not be empty.");
  return `ARRAY[${rows.map(sqlUuid).join(",")}]::uuid[]`;
}

function sqlStringArray(values) {
  const rows = asArray(values);
  assert(rows.length > 0, "SQL text array must not be empty.");
  return `ARRAY[${rows.map(sqlString).join(",")}]::text[]`;
}

function sqlString(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
