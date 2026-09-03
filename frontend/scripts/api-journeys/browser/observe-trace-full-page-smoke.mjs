import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  isUuid,
} from "../lib/api-client.mjs";
import { queryWithFilters } from "../lib/fixtures.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const DRAWER_SCREENSHOT_PATH = "/tmp/observe-trace-drawer-parity-smoke.png";
const FULL_PAGE_SCREENSHOT_PATH = "/tmp/observe-trace-full-page-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/observe-trace-full-page-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const sample = await resolveTraceSample(auth.client, auth.workspaceId);
  const apiFailures = [];
  const pageErrors = [];
  const evidence = {
    project_id: sample.project.id,
    project_name: sample.project.name || null,
    trace_id: sample.traceId,
    span_id: sample.spanId,
    detail_span_count: sample.spanCount,
    trace_list_rows: sample.traceListRows,
  };

  const browser = await launchBrowser();
  const pages = [];
  let caughtError = null;
  try {
    const drawerPage = await browser.newPage();
    pages.push(drawerPage);
    await preparePage(drawerPage, auth, sample.project.id);
    monitorPage(drawerPage, apiFailures, pageErrors);
    const drawerResponsePromise = waitForTraceDetail(
      drawerPage,
      sample.traceId,
    );
    await drawerPage.goto(buildDrawerUrl(sample.project.id, sample.traceId), {
      waitUntil: "domcontentloaded",
    });
    const drawerResponse = await drawerResponsePromise;
    await assertTracePageVisible(drawerPage, sample);
    await drawerPage.screenshot({
      path: DRAWER_SCREENSHOT_PATH,
      fullPage: true,
    });
    evidence.drawer = {
      url: drawerPage.url(),
      trace_detail_status: drawerResponse.status(),
      screenshot: DRAWER_SCREENSHOT_PATH,
    };

    const fullPage = await browser.newPage();
    pages.push(fullPage);
    await preparePage(fullPage, auth, sample.project.id);
    monitorPage(fullPage, apiFailures, pageErrors);
    const fullPageResponsePromise = waitForTraceDetail(
      fullPage,
      sample.traceId,
    );
    await fullPage.goto(
      `${APP_BASE}/dashboard/observe/${sample.project.id}/trace/${sample.traceId}`,
      { waitUntil: "domcontentloaded" },
    );
    const fullPageResponse = await fullPageResponsePromise;
    await fullPage.waitForFunction(
      ({ projectId, traceId }) =>
        window.location.pathname ===
        `/dashboard/observe/${projectId}/trace/${traceId}`,
      { timeout: 30000 },
      { projectId: sample.project.id, traceId: sample.traceId },
    );
    await assertTracePageVisible(fullPage, sample);
    await fullPage.screenshot({
      path: FULL_PAGE_SCREENSHOT_PATH,
      fullPage: true,
    });
    evidence.full_page = {
      url: fullPage.url(),
      trace_detail_status: fullPageResponse.status(),
      screenshot: FULL_PAGE_SCREENSHOT_PATH,
    };

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
          evidence,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    caughtError = error;
    await Promise.all(
      pages.map((page, index) =>
        page
          .screenshot({
            path:
              index === 0
                ? FAILURE_SCREENSHOT_PATH
                : `/tmp/observe-trace-full-page-smoke-failure-page-${index + 1}.png`,
            fullPage: true,
          })
          .catch(() => null),
      ),
    );
  } finally {
    await browser.close();
  }

  if (caughtError) throw caughtError;
}

async function resolveTraceSample(client, workspaceId) {
  const preferredProjectId = process.env.OBSERVE_PROJECT_ID;
  const preferredTraceId = process.env.OBSERVE_TRACE_ID;

  if (preferredProjectId && preferredTraceId) {
    return sampleFromTraceDetail(client, {
      project: { id: preferredProjectId, name: "env observe project" },
      traceId: preferredTraceId,
      traceListRows: null,
    });
  }

  const projects = preferredProjectId
    ? [{ id: preferredProjectId, name: "env observe project" }]
    : asArray(
        await client.get(apiPath("/tracer/project/list_projects/"), {
          query: {
            project_type: "observe",
            page_number: 0,
            page_size: 100,
            sort_by: "updated_at",
            sort_direction: "desc",
          },
        }),
      );

  for (const projectRow of projects) {
    if (!isUuid(projectRow?.id)) continue;
    const project = await loadProjectIfCurrentWorkspace(
      client,
      projectRow,
      workspaceId,
      Boolean(preferredProjectId),
    );
    if (!project) continue;

    const traceList = await client.get(
      queryWithFilters(apiPath("/tracer/trace/list_traces_of_session/"), [], {
        project_id: project.id,
        page_number: 0,
        page_size: 25,
      }),
    );
    const traces = asArray(traceList).filter((row) => row?.trace_id || row?.id);
    for (const trace of traces) {
      const traceId = trace.trace_id || trace.id;
      try {
        return await sampleFromTraceDetail(client, {
          project,
          traceId,
          traceListRows: traces.length,
        });
      } catch {
        // Some legacy rows are missing span detail; keep looking for a full trace.
      }
    }
  }

  throw new Error(
    "No current-workspace observe trace with span detail was found.",
  );
}

async function loadProjectIfCurrentWorkspace(
  client,
  projectRow,
  workspaceId,
  allowEnvOverride,
) {
  const detail = await client.get(
    apiPath("/tracer/project/{id}/", { id: projectRow.id }),
  );
  if (
    !allowEnvOverride &&
    workspaceId &&
    detail?.workspace &&
    String(detail.workspace) !== String(workspaceId)
  ) {
    return null;
  }
  if (detail?.trace_type && detail.trace_type !== "observe") return null;
  return { id: projectRow.id, name: detail?.name || projectRow.name };
}

async function sampleFromTraceDetail(
  client,
  { project, traceId, traceListRows },
) {
  const detail = await client.get(
    apiPath("/tracer/trace/{id}/", { id: traceId }),
  );
  const entries = asArray(detail?.observation_spans);
  const flatEntries = entries.flatMap((entry) => flattenTraceEntries(entry));
  const selected =
    flatEntries.find((row) => !row.span?.parent_span_id) || flatEntries[0];
  assert(
    selected?.span?.id,
    `Trace ${traceId} did not include a visible observation span.`,
  );
  return {
    project,
    traceId,
    spanId: selected.span.id,
    spanCount: flatEntries.length,
    traceListRows,
  };
}

function flattenTraceEntries(rootEntry) {
  const rows = [];
  function walk(entry) {
    if (!entry || typeof entry !== "object") return;
    const span = entry.observation_span || entry.span || entry;
    if (span?.id) rows.push({ entry, span });
    for (const child of asArray(entry.children)) walk(child);
  }
  walk(rootEntry);
  return rows;
}

async function preparePage(page, auth, projectId) {
  await page.setBypassServiceWorker(true);
  await installRuntimeConfig(page, auth);
  await installAuthState(page, auth, projectId);
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

async function installAuthState(page, auth, projectId) {
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user, traceViewKey }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      localStorage.setItem(
        traceViewKey,
        JSON.stringify({ viewMode: "timeline" }),
      );
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
      traceViewKey: `trace-view-default-${projectId}`,
    },
  );
}

function waitForTraceDetail(page, traceId) {
  return page.waitForResponse(
    (response) =>
      response.url().includes(`/tracer/trace/${traceId}/`) &&
      response.status() < 400,
    { timeout: 60000 },
  );
}

async function assertTracePageVisible(page, sample) {
  await expectVisibleText(page, "Trace ID :", { exact: true, timeout: 60000 });
  await expectVisibleText(page, sample.traceId, {
    exact: true,
    timeout: 60000,
  });
  await expectVisibleText(page, "Trace Timeline", {
    exact: true,
    timeout: 60000,
  });
  await expectVisibleText(page, sample.spanId, {
    exact: true,
    timeout: 60000,
  });

  const badDateVisible = await hasVisibleText(page, "Invalid Date");
  assert(!badDateVisible, "Trace detail rendered an Invalid Date marker.");
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
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

async function hasVisibleText(page, text) {
  return page.evaluate((expectedText) => {
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
    return Array.from(document.querySelectorAll("body *")).some(
      (element) =>
        isVisible(element) && element.textContent?.includes(expectedText),
    );
  }, text);
}

function buildDrawerUrl(projectId, traceId) {
  const params = new URLSearchParams();
  params.set("primaryTraceDateFilter", JSON.stringify(sixMonthDateFilter()));
  params.set("traceDetailDrawerOpen", JSON.stringify({ traceId, filters: [] }));
  return `${APP_BASE}/dashboard/observe/${projectId}/llm-tracing?${params}`;
}

function sixMonthDateFilter() {
  const start = new Date();
  start.setMonth(start.getMonth() - 6);
  const end = new Date();
  end.setDate(end.getDate() + 1);
  return {
    dateFilter: [toDateOnly(start), toDateOnly(end)],
    dateOption: "6M",
  };
}

function toDateOnly(date) {
  return date.toISOString().slice(0, 10);
}

function monitorPage(page, apiFailures, pageErrors) {
  page.on("response", (response) => {
    const url = response.url();
    if (
      isObservedLocalEndpoint(url) &&
      response.status() >= 400 &&
      !url.includes("/tracer/dashboard/cost/")
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) =>
    pageErrors.push(`${page.url()}: ${error.stack || error.message}`),
  );
}

function isObservedLocalEndpoint(url) {
  return [
    "/tracer/trace/",
    "/tracer/observation-span/",
    "/tracer/saved-views/",
    "/model-hub/scores/",
    "/model-hub/annotation-queues/",
  ].some((pathName) => url.includes(pathName));
}

async function launchBrowser() {
  return puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  }
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  if (process.platform === "linux") {
    return "/usr/bin/google-chrome";
  }
  return undefined;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
