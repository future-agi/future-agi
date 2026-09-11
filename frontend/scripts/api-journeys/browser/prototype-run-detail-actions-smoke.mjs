/* eslint-disable no-console */
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  isUuid,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const PROJECT_PREFIX = "ui_prototype_run_browser_";
const PROJECT_SCREENSHOT_PATH = "/tmp/prototype-run-actions-smoke.png";
const RUN_SCREENSHOT_PATH = "/tmp/prototype-run-inside-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/prototype-run-actions-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const marker = suffix.slice(0, 18);
  const projectName = `${PROJECT_PREFIX}${marker}`;
  const alphaName = `${projectName}_alpha`;
  const betaName = `${projectName}_beta`;
  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const prototypeRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  const consoleMessages = [];
  let browser = null;
  let page = null;
  let projectId = null;

  await cleanupProjectsByPrefix(auth.client, PROJECT_PREFIX, cleanupEvidence);

  try {
    const seeded = await seedPrototypeRuns({
      client: auth.client,
      projectName,
      alphaName,
      betaName,
      marker,
    });
    projectId = seeded.projectId;

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
      const url = request.url();
      if (!isPrototypeRunApiUrl(url)) return;
      const requestKey = `${request.method()} ${url}`;
      prototypeRequests.push(requestKey);
      if (MUTATION_METHODS.has(request.method())) {
        browserMutations.push(requestKey);
        if (!isAllowedBrowserMutation(request.method(), url)) {
          unexpectedMutations.push(requestKey);
        }
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isPrototypeRunApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("console", (message) => {
      if (["error", "warning"].includes(message.type())) {
        consoleMessages.push(`${message.type()}: ${message.text()}`);
      }
    });

    await waitForResponsesDuring(
      page,
      "prototype run list load",
      [
        (response) =>
          response.url().includes("/tracer/project/") &&
          response.url().includes(projectId) &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/tracer/project-version/list_runs/") &&
          response.url().includes(projectId) &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/prototype/${projectId}`, {
          waitUntil: "domcontentloaded",
        }),
    );

    await waitForVisibleText(page, projectName, { exact: true });
    await waitForVisibleText(page, "All runs", { exact: true });
    await waitForVisibleText(page, "Export", { exact: true });
    await waitForVisibleText(page, "Configure", { exact: true });
    await waitForVisibleText(page, "Choose Winner", { exact: true });
    await waitForVisibleText(page, seeded.alphaRunName);
    await waitForVisibleText(page, seeded.betaRunName);

    const exportResponse = await waitForResponseDuring(
      page,
      "prototype run export",
      (response) =>
        response.url().includes("/tracer/project-version/get_export_data/") &&
        response.request().method() === "POST",
      () => clickVisibleButton(page, "Export"),
    );
    const exportText = await responseText(exportResponse);
    assert(
      exportResponse.status() < 400,
      `Prototype run export returned HTTP ${exportResponse.status()}: ${exportText}`,
    );
    assert(
      exportText.includes(seeded.alphaRunName) &&
        exportText.includes(seeded.betaRunName),
      "Prototype run export response omitted seeded runs.",
    );

    const winnerResponse = await waitForResponseDuring(
      page,
      "prototype choose winner",
      (response) =>
        response
          .url()
          .includes("/tracer/project-version/project_version_winner/") &&
        response.request().method() === "POST",
      async () => {
        await clickVisibleButton(page, "Choose Winner");
        await waitForVisibleText(page, "Winner Settings", { exact: true });
        await waitForVisibleText(page, "Avg Cost", { exact: true });
        await waitForVisibleText(page, "Avg Latency", { exact: true });
        await clickDrawerAction(page, "Choose Winner");
      },
    );
    const winnerPayload = unwrapResponseEnvelope(
      await responseJson(winnerResponse),
    );
    assert(
      winnerPayload?.project_version_winner === seeded.alphaVersionId,
      `Choose Winner selected the wrong run: ${JSON.stringify(winnerPayload)}`,
    );
    await waitForNoVisibleExactText(page, "Winner Settings");

    await selectRunRow(page, seeded.alphaRunName);
    await selectRunRow(page, seeded.betaRunName);
    await waitForVisibleText(page, "2 Selected", { exact: true });

    const compareResponse = await waitForResponseDuring(
      page,
      "prototype compare drawer",
      (response) =>
        response.url().includes("/tracer/trace/compare_traces/") &&
        response.request().method() === "POST" &&
        response.status() < 400,
      () => clickVisibleButton(page, "Compare"),
    );
    const comparePayload = unwrapResponseEnvelope(
      await responseJson(compareResponse),
    );
    const traceComparison =
      comparePayload?.traceComparison || comparePayload?.trace_comparison || {};
    assert(
      Number(comparePayload?.totalTraces ?? comparePayload?.total_traces ?? 0) >
        0,
      `Compare response did not include comparable traces: ${JSON.stringify(
        comparePayload,
      )}`,
    );
    assert(
      Object.keys(traceComparison).length === 2,
      "Compare response did not include both selected runs.",
    );
    await waitForVisibleText(page, "Selected Runs (2)", { exact: true });
    await waitForVisibleText(page, "1 of 1 Row");
    await waitForVisibleText(page, seeded.alphaRunName);
    await waitForVisibleText(page, seeded.betaRunName);
    await waitForVisibleText(page, "System Metrics:", { exact: true });
    await waitForVisibleText(page, "Latency: 100ms", { exact: true });
    await waitForVisibleText(page, "Latency: 300ms", { exact: true });
    await waitForVisibleText(page, "Run Details", { exact: true });
    await waitForVisibleText(page, "Input", { exact: true });
    await waitForVisibleText(page, "Output", { exact: true });
    await page.screenshot({ path: PROJECT_SCREENSHOT_PATH, fullPage: true });

    await closeCompareDrawer(page);
    await waitForNoVisibleExactText(page, "Selected Runs (2)");

    await waitForResponsesDuring(
      page,
      "prototype run-inside load",
      [
        (response) =>
          response
            .url()
            .includes(`/tracer/project-version/${seeded.alphaVersionId}/`) &&
          response.status() < 400,
        (response) =>
          response
            .url()
            .includes("/tracer/project-version/get_run_insights/") &&
          response.url().includes(seeded.alphaVersionId) &&
          response.status() < 400,
        (response) =>
          response.url().includes("/tracer/trace/list_traces/") &&
          response.url().includes(seeded.alphaVersionId) &&
          response.status() < 400,
      ],
      () =>
        page.goto(
          `${APP_BASE}/dashboard/prototype/${projectId}/${seeded.alphaVersionId}`,
          { waitUntil: "domcontentloaded" },
        ),
    );
    await waitForPath(
      page,
      `/dashboard/prototype/${projectId}/${seeded.alphaVersionId}`,
    );
    await waitForVisibleText(page, seeded.alphaName);
    await waitForVisibleText(page, "Run Insights", { exact: true });
    await waitForVisibleText(page, "Average Latency:", { exact: true });
    await waitForVisibleText(page, "100 ms", { exact: true });
    await waitForVisibleText(page, "Avg. Tokens:", { exact: true });
    await waitForVisibleText(page, "5", { exact: true });
    await waitForVisibleText(page, "Trace", { exact: true });
    await waitForVisibleText(page, renderedGridPrefix(seeded.alphaTraceName));

    const spanListResponse = await waitForResponseDuring(
      page,
      "prototype run-inside spans tab",
      (response) =>
        response.url().includes("/tracer/observation-span/list_spans/") &&
        response.url().includes(seeded.alphaVersionId) &&
        response.status() < 400,
      () => clickVisibleTab(page, "Spans"),
    );
    await responseJson(spanListResponse);
    await waitForVisibleText(page, renderedGridPrefix(seeded.alphaSpanName));
    await waitForNoVisibleText(page, "Invalid Date");
    await waitForNoVisibleText(page, "undefined");
    await page.screenshot({ path: RUN_SCREENSHOT_PATH, fullPage: true });

    await deleteProjects(auth.client, [projectId], cleanupEvidence);
    projectId = null;

    assert(
      unexpectedMutations.length === 0,
      `Unexpected prototype browser mutations: ${unexpectedMutations
        .map(maskRequest)
        .join(", ")}`,
    );
    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          project_id: seeded.projectId,
          project_name: projectName,
          alpha_project_version_id: seeded.alphaVersionId,
          beta_project_version_id: seeded.betaVersionId,
          alpha_trace_id: seeded.alphaTraceId,
          beta_trace_id: seeded.betaTraceId,
          alpha_span_id: seeded.alphaSpanId,
          beta_span_id: seeded.betaSpanId,
          browser_mutations: browserMutations.map(maskRequest),
          prototype_request_count: prototypeRequests.length,
          screenshots: [PROJECT_SCREENSHOT_PATH, RUN_SCREENSHOT_PATH],
          cleanup: cleanupEvidence,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    if (page) {
      await page.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true });
      console.error(`failure_screenshot=${FAILURE_SCREENSHOT_PATH}`);
    }
    console.error(
      JSON.stringify(
        {
          prototype_requests: prototypeRequests.map(maskRequest),
          browser_mutations: browserMutations.map(maskRequest),
          api_failures: apiFailures.map(maskRequest),
          page_errors: pageErrors,
          console_messages: consoleMessages.slice(-20),
        },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    if (projectId) {
      await deleteProjects(auth.client, [projectId], cleanupEvidence).catch(
        (error) => {
          cleanupEvidence.push({
            cleanup: "delete prototype project after failure",
            status: "failed",
            error: error.message,
          });
        },
      );
    }
    await cleanupProjectsByPrefix(auth.client, PROJECT_PREFIX, cleanupEvidence);
    if (browser) await browser.close();
  }
}

async function seedPrototypeRuns({
  client,
  projectName,
  alphaName,
  betaName,
  marker,
}) {
  const createdProject = await client.post(apiPath("/tracer/project/"), {
    name: projectName,
    model_type: "GenerativeLLM",
    trace_type: "experiment",
  });
  const projectId = createdProject.project_id || createdProject.projectId;
  assert(isUuid(projectId), "Prototype project create omitted project_id.");

  const alphaVersion = await createProjectVersion(client, {
    projectId,
    name: alphaName,
    metadata: { api_journey: "prototype-run-browser", marker, lane: "alpha" },
  });
  const betaVersion = await createProjectVersion(client, {
    projectId,
    name: betaName,
    metadata: { api_journey: "prototype-run-browser", marker, lane: "beta" },
  });
  assert(alphaVersion.version === "v1", "Alpha run was not created as v1.");
  assert(betaVersion.version === "v2", "Beta run was not created as v2.");

  const alphaSeed = await seedTraceAndSpan(client, {
    projectId,
    projectVersionId: alphaVersion.id,
    marker,
    lane: "alpha",
    latencyMs: 100,
    cost: 0.001,
  });
  const betaSeed = await seedTraceAndSpan(client, {
    projectId,
    projectVersionId: betaVersion.id,
    marker,
    lane: "beta",
    latencyMs: 300,
    cost: 0.004,
  });

  const runList = await client.get(
    apiPath("/tracer/project-version/list_runs/"),
    {
      query: {
        project_id: projectId,
        page_number: 0,
        page_size: 10,
        filters: [],
        sort_params: [],
      },
    },
  );
  const rows = asArray(runList);
  const alphaRunName = `${alphaName} - ${alphaVersion.version}`;
  const betaRunName = `${betaName} - ${betaVersion.version}`;
  assert(
    rows.some(
      (row) => row?.id === alphaVersion.id && row?.run_name === alphaRunName,
    ),
    "Seeded alpha run was missing from list_runs.",
  );
  assert(
    rows.some(
      (row) => row?.id === betaVersion.id && row?.run_name === betaRunName,
    ),
    "Seeded beta run was missing from list_runs.",
  );

  return {
    projectId,
    alphaName,
    betaName,
    alphaVersionId: alphaVersion.id,
    betaVersionId: betaVersion.id,
    alphaRunName,
    betaRunName,
    alphaTraceId: alphaSeed.traceId,
    betaTraceId: betaSeed.traceId,
    alphaSpanId: alphaSeed.spanId,
    betaSpanId: betaSeed.spanId,
    alphaTraceName: alphaSeed.traceName,
    alphaSpanName: alphaSeed.spanName,
  };
}

async function createProjectVersion(client, { projectId, name, metadata }) {
  const created = await client.post(apiPath("/tracer/project-version/"), {
    project: projectId,
    name,
    metadata,
  });
  const projectVersionId = created.project_version_id || created.id;
  assert(
    isUuid(projectVersionId),
    "Project-version create did not return a valid id.",
  );
  assert(
    String(created.version || "").trim(),
    "Project-version omitted version.",
  );
  return { id: projectVersionId, version: created.version };
}

async function seedTraceAndSpan(
  client,
  { projectId, projectVersionId, marker, lane, latencyMs, cost },
) {
  const traceName = `${PROJECT_PREFIX}${marker}_${lane}_trace`;
  const spanName = `${PROJECT_PREFIX}${marker}_${lane}_span`;
  const trace = await client.post(apiPath("/tracer/trace/"), {
    project: projectId,
    project_version: projectVersionId,
    name: traceName,
    input: { prompt: `shared prototype input ${marker}` },
    output: { response: `prototype ${lane} output ${marker}` },
    metadata: { source: "api-journey", marker, lane },
    tags: ["api-journey", lane],
  });
  const traceId = trace.id || trace.trace_id || trace.trace?.id;
  assert(isUuid(traceId), "Trace seed did not return a trace id.");

  const spanId = `ui_prototype_run_${lane}_${marker}`;
  const startTime = new Date(Date.now() - latencyMs).toISOString();
  const endTime = new Date().toISOString();
  const span = await client.post(apiPath("/tracer/observation-span/"), {
    id: spanId,
    project: projectId,
    project_version: projectVersionId,
    trace: traceId,
    name: spanName,
    observation_type: "llm",
    start_time: startTime,
    end_time: endTime,
    input: { messages: [{ role: "user", content: `hello ${marker}` }] },
    output: { choices: [{ message: { content: `hi from ${lane}` } }] },
    model: "api-journey-model",
    prompt_tokens: 2,
    completion_tokens: 3,
    total_tokens: 5,
    latency_ms: latencyMs,
    cost,
    status: "OK",
    tags: ["api-journey", lane],
    metadata: { source: "api-journey", marker, lane },
  });
  assert((span.id || spanId) === spanId, "Span seed returned the wrong id.");
  return { traceId, spanId, traceName, spanName };
}

async function cleanupProjectsByPrefix(client, prefix, evidence) {
  const listPayload = await client.get(apiPath("/tracer/project/"), {
    query: {
      project_type: "experiment",
      page_number: 0,
      page_size: 100,
      sort_by: "created_at",
      sort_direction: "desc",
    },
  });
  const projects = asArray(listPayload?.projects || listPayload).filter(
    (project) => String(project?.name || "").startsWith(prefix),
  );
  const ids = projects.map((project) => project.id).filter(Boolean);
  if (ids.length) await deleteProjects(client, ids, evidence);
}

async function deleteProjects(client, projectIds, evidence) {
  if (!projectIds.length) return;
  await client.delete(apiPath("/tracer/project/"), {
    body: {
      project_ids: projectIds,
      project_type: "experiment",
    },
    okStatuses: [200, 400, 404],
  });
  evidence.push({
    cleanup: "delete prototype project",
    status: "passed",
    project_ids: projectIds,
  });
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

async function waitForResponsesDuring(page, label, predicates, action) {
  try {
    return await Promise.all([
      ...predicates.map((predicate) =>
        page.waitForResponse(predicate, { timeout: 60000 }),
      ),
      action(),
    ]);
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
}

async function waitForPath(page, pathname, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname === expectedPath,
    { timeout },
    pathname,
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

async function waitForNoVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) =>
      !window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      }),
    { timeout },
    { text, exact },
  );
}

async function waitForNoVisibleExactText(page, text, timeout = 30000) {
  await waitForNoVisibleText(page, text, { exact: true, timeout });
}

async function clickVisibleButton(page, text) {
  await clickVisibleElementByText(page, "button, [role='button']", text);
}

async function clickVisibleTab(page, text) {
  await clickVisibleElementByText(page, '[role="tab"], button', text);
}

async function clickDrawerAction(page, text) {
  const clickState = await page.waitForFunction(
    (expectedText) => {
      const drawers = window.visibleElements(".MuiDrawer-paper");
      for (const drawer of drawers) {
        const button = Array.from(drawer.querySelectorAll("button")).find(
          (candidate) =>
            window.getComputedStyle(candidate).display !== "none" &&
            window.normalizeText(candidate.textContent) === expectedText,
        );
        if (!button) continue;
        return {
          disabled: Boolean(button.disabled),
          ariaDisabled: button.getAttribute("aria-disabled"),
          text: window.normalizeText(button.textContent),
        };
      }
      return false;
    },
    { timeout: 30000 },
    text,
  );
  const state = await clickState.jsonValue();
  assert(
    !state.disabled && state.ariaDisabled !== "true",
    `Drawer action "${text}" is disabled: ${JSON.stringify(state)}`,
  );
  const clicked = await page.evaluate((expectedText) => {
    const drawers = window.visibleElements(".MuiDrawer-paper");
    for (const drawer of drawers) {
      const button = Array.from(drawer.querySelectorAll("button")).find(
        (candidate) =>
          window.getComputedStyle(candidate).display !== "none" &&
          window.normalizeText(candidate.textContent) === expectedText,
      );
      if (button) {
        button.scrollIntoView({ block: "center", inline: "center" });
        button.click();
        return true;
      }
    }
    return false;
  }, text);
  assert(clicked, `Could not locate drawer action "${text}".`);
}

async function clickVisibleElementByText(page, selector, text) {
  await page.waitForFunction(
    ({ selector: targetSelector, text: expectedText }) =>
      window
        .visibleElements(targetSelector)
        .some(
          (element) =>
            window.normalizeText(element.textContent) === expectedText,
        ),
    { timeout: 30000 },
    { selector, text },
  );
  const point = await page.evaluate(
    ({ selector: targetSelector, text: expectedText }) => {
      const element = window
        .visibleElements(targetSelector)
        .find(
          (candidate) =>
            window.normalizeText(candidate.textContent) === expectedText,
        );
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    },
    { selector, text },
  );
  assert(point, `Could not locate visible element "${text}".`);
  await page.mouse.click(point.x, point.y);
}

async function selectRunRow(page, runName) {
  const pointHandle = await page.waitForFunction(
    (expectedName) => {
      const bodyRows = window.visibleElements(
        ".ag-center-cols-container .ag-row[row-index], .ag-body-viewport .ag-row[row-index]",
      );
      const dataRow = bodyRows.find((row) =>
        window.normalizeText(row.textContent).includes(expectedName),
      );
      if (!dataRow) return false;

      const rowIndex = dataRow.getAttribute("row-index");
      const rowParts = window.visibleElements(
        `.ag-row[row-index="${rowIndex}"]`,
      );
      const checkbox =
        rowParts
          .flatMap((row) =>
            Array.from(
              row.querySelectorAll(
                'input[type="checkbox"], [role="checkbox"], .ag-checkbox-input-wrapper, .ag-selection-checkbox',
              ),
            ),
          )
          .find((candidate) => {
            const rect = candidate.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          }) || null;
      if (!checkbox) return false;

      const checked =
        checkbox.getAttribute("aria-checked") === "true" ||
        checkbox.classList.contains("ag-checked") ||
        checkbox.closest(".ag-checked");
      if (checked) return { alreadySelected: true };

      const rect = checkbox.getBoundingClientRect();
      return {
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
      };
    },
    { timeout: 30000 },
    runName,
  );
  const point = await pointHandle.jsonValue();
  if (point.alreadySelected) return;
  await page.mouse.click(point.x, point.y);
}

async function closeCompareDrawer(page) {
  const clicked = await page.evaluate(() => {
    const drawer = window
      .visibleElements(".MuiDrawer-paper")
      .find((element) =>
        window.normalizeText(element.textContent).includes("Selected Runs"),
      );
    const closeButton = Array.from(drawer?.querySelectorAll("button") || [])
      .map((button) => ({ button, rect: button.getBoundingClientRect() }))
      .filter(({ rect }) => rect.width > 0 && rect.height > 0 && rect.top < 80)
      .sort((a, b) => b.rect.right - a.rect.right)[0]?.button;
    closeButton?.click();
    return Boolean(closeButton);
  });
  assert(clicked, "Could not locate compare drawer close button.");
}

async function responseJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

async function responseText(response) {
  try {
    return await response.text();
  } catch {
    const buffer = await response.buffer();
    return buffer.toString("utf8");
  }
}

function unwrapResponseEnvelope(payload) {
  if (payload && Object.prototype.hasOwnProperty.call(payload, "result")) {
    return payload.result;
  }
  return payload;
}

function rendererTitleCase(value) {
  return String(value).charAt(0).toUpperCase() + String(value).slice(1);
}

function renderedGridPrefix(value) {
  return rendererTitleCase(value).slice(0, 20);
}

function isPrototypeRunApiUrl(url) {
  try {
    const parsed = new URL(url);
    return (
      parsed.pathname.startsWith("/tracer/project/") ||
      parsed.pathname.startsWith("/tracer/project-version/") ||
      parsed.pathname.startsWith("/tracer/trace/") ||
      parsed.pathname.startsWith("/tracer/observation-span/")
    );
  } catch {
    return false;
  }
}

function isAllowedBrowserMutation(method, url) {
  const pathname = new URL(url).pathname;
  return (
    method === "POST" &&
    [
      "/tracer/project-version/get_export_data/",
      "/tracer/project-version/project_version_winner/",
      "/tracer/trace/compare_traces/",
    ].includes(pathname)
  );
}

function maskRequest(value) {
  const urlPattern = /(https?:\/\/[^/]+)(\/[^ ]*)/g;
  return value.replace(urlPattern, "$2");
}

function browserExecutablePath() {
  return (
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  );
}

main().catch((error) => {
  if (error?.name === "SkipJourney") {
    console.log(JSON.stringify({ status: "skipped", reason: error.reason }));
    process.exit(0);
  }
  console.error(error);
  process.exit(1);
});
