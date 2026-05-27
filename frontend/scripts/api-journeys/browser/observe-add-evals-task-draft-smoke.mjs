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
const SCREENSHOT_PATH = "/tmp/observe-add-evals-task-draft-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/observe-add-evals-task-draft-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const sample = await resolveTraceSample(auth.client, auth.workspaceId);
  const traceListRequests = [];
  const apiFailures = [];
  const pageErrors = [];
  let requestPhase = "observe";

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();

  try {
    await preparePage(page, auth, sample);
    page.on("request", (request) => {
      const url = request.url();
      if (isTraceListUrl(url)) {
        traceListRequests.push({
          url,
          method: request.method(),
          referer: request.headers().referer || "",
          phase: requestPhase,
        });
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isRelevantApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponseDuring(
      page,
      "Observe trace list with seeded trace filter",
      (response) =>
        isTraceListUrl(response.url()) &&
        response.status() < 400 &&
        hasTraceFilter(response.url(), sample.traceId),
      () =>
        page.goto(
          `${APP_BASE}/dashboard/observe/${sample.project.id}/llm-tracing?selectedTab=trace`,
          { waitUntil: "domcontentloaded" },
        ),
    );
    await page.waitForFunction(
      (projectId) =>
        window.location.pathname ===
        `/dashboard/observe/${projectId}/llm-tracing`,
      { timeout: 30000 },
      sample.project.id,
    );
    await expectVisibleText(page, "Add Evals", { exact: true });

    requestPhase = "task-create";
    await clickVisibleText(page, "Add Evals");
    await page.waitForFunction(
      () => window.location.pathname === "/dashboard/tasks/create",
      { timeout: 30000 },
    );
    await expectVisibleText(page, "Create Task");
    await expectVisibleText(page, "Task Name");
    await expectVisibleText(page, "Run evaluations on", { exact: true });
    await expectVisibleText(page, sample.traceId.slice(0, 8));
    await page.waitForFunction(
      () => document.body.textContent.includes("Live Preview"),
      { timeout: 30000 },
    );
    await expectNoVisibleText(page, "Invalid Date");

    const draftEvidence = await readTaskDraft(page);
    assert(
      draftEvidence.project === sample.project.id,
      `Task draft project mismatch: ${JSON.stringify(draftEvidence)}`,
    );
    assert(
      draftEvidence.rowType === "traces",
      `Task draft rowType mismatch: ${JSON.stringify(draftEvidence)}`,
    );
    assert(
      draftEvidence.returnTo?.includes(
        `/dashboard/observe/${sample.project.id}/llm-tracing`,
      ),
      `Task draft returnTo missing Observe path: ${JSON.stringify(draftEvidence)}`,
    );
    assert(
      draftEvidence.traceFilter?.filterConfig?.filterValue === sample.traceId,
      `Task draft did not preserve trace_id filter: ${JSON.stringify(
        draftEvidence,
      )}`,
    );

    const previewRequest = traceListRequests.find(
      (request) =>
        request.phase === "task-create" &&
        hasTraceFilter(request.url, sample.traceId),
    );
    assert(
      previewRequest,
      "Task create page did not request trace preview with linked trace_id filter.",
    );
    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence: {
            project_id: sample.project.id,
            project_name: sample.project.name || null,
            trace_id: sample.traceId,
            span_id: sample.spanId,
            draft_id: draftEvidence.draftId,
            draft_row_type: draftEvidence.rowType,
            draft_trace_filter_value:
              draftEvidence.traceFilter?.filterConfig?.filterValue,
            task_preview_trace_request: previewRequest.url,
            screenshot: SCREENSHOT_PATH,
          },
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
  }
}

async function resolveTraceSample(client, workspaceId) {
  const preferredProjectId = process.env.OBSERVE_PROJECT_ID;
  const preferredTraceId = process.env.OBSERVE_TRACE_ID;

  if (preferredProjectId && preferredTraceId) {
    return sampleFromTraceDetail(client, {
      project: { id: preferredProjectId, name: "env observe project" },
      traceId: preferredTraceId,
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
    for (const trace of asArray(traceList).filter(
      (row) => row?.trace_id || row?.id,
    )) {
      const traceId = trace.trace_id || trace.id;
      try {
        return await sampleFromTraceDetail(client, { project, traceId });
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
  if (detail?.source === "simulator") return null;
  return { id: projectRow.id, name: detail?.name || projectRow.name };
}

async function sampleFromTraceDetail(client, { project, traceId }) {
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

async function preparePage(page, auth, sample) {
  await page.setBypassServiceWorker(true);
  await installRuntimeConfig(page, auth);
  await page.evaluateOnNewDocument(() => {
    window.__apiJourneyNormalizeText = (value) => String(value || "").trim();
    window.__apiJourneyVisibleElements = () => {
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
      return Array.from(document.querySelectorAll("body *")).filter(isVisible);
    };
  });
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user, projectId, traceId }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      if (organizationId)
        sessionStorage.setItem("organizationId", organizationId);
      if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
      if (user?.id)
        sessionStorage.setItem("futureagi-current-user-id", user.id);
      localStorage.setItem(
        `observe-filters-${projectId}`,
        JSON.stringify({
          tabType: "traces",
          filters: [
            {
              id: `api-journey-trace-${traceId}`,
              column_id: "trace_id",
              display_name: "Trace ID",
              filter_config: {
                filter_type: "text",
                filter_op: "equals",
                filter_value: traceId,
              },
            },
          ],
          extra_filters: [],
        }),
      );
    },
    {
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      user: auth.user,
      projectId: sample.project.id,
      traceId: sample.traceId,
    },
  );
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

async function readTaskDraft(page) {
  const url = new URL(page.url());
  const draftId = url.searchParams.get("draft");
  assert(draftId, `Task create URL did not include a draft id: ${page.url()}`);
  return page.evaluate((id) => {
    const raw = localStorage.getItem(`task-draft-${id}`);
    const parsed = raw ? JSON.parse(raw) : null;
    const values = parsed?.values || {};
    const traceFilter = (values.filters || []).find(
      (filter) =>
        filter?.property === "trace_id" || filter?.propertyId === "trace_id",
    );
    return {
      draftId: id,
      project: values.project,
      rowType: values.rowType,
      returnTo: new URL(window.location.href).searchParams.get("returnTo"),
      traceFilter,
    };
  }, draftId);
}

async function waitForResponseDuring(page, label, predicate, action) {
  try {
    await Promise.all([
      page.waitForResponse(predicate, { timeout: 60000 }),
      action(),
    ]);
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
}

async function expectVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      return window.__apiJourneyVisibleElements().some((element) => {
        const textContent = window.__apiJourneyNormalizeText(
          element.textContent,
        );
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

async function expectNoVisibleText(page, text, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (expectedText) => {
      return !window
        .__apiJourneyVisibleElements()
        .some((element) =>
          window
            .__apiJourneyNormalizeText(element.textContent)
            .includes(expectedText),
        );
    },
    { timeout },
    text,
  );
}

async function clickVisibleText(page, text) {
  await page.waitForFunction(
    (expectedText) =>
      window
        .__apiJourneyVisibleElements()
        .some(
          (element) =>
            window.__apiJourneyNormalizeText(element.textContent) ===
            expectedText,
        ),
    { timeout: 30000 },
    text,
  );
  const clicked = await page.evaluate((expectedText) => {
    const element = window
      .__apiJourneyVisibleElements()
      .find(
        (candidate) =>
          window.__apiJourneyNormalizeText(candidate.textContent) ===
          expectedText,
      );
    const clickable = element?.closest("button,[role='button'],a") || element;
    if (!clickable) return false;
    clickable.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    clickable.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    clickable.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return true;
  }, text);
  assert(clicked, `Could not click visible text ${text}.`);
}

function isRelevantApiUrl(url) {
  return (
    url.includes("/tracer/trace/list_traces_of_session/") ||
    url.includes("/tracer/observation-span/get_eval_attributes_list/") ||
    url.includes("/tracer/project/")
  );
}

function isTraceListUrl(url) {
  return url.includes("/tracer/trace/list_traces_of_session/");
}

function hasTraceFilter(url, traceId) {
  const parsed = new URL(url);
  if (parsed.searchParams.get("project_id")) {
    const filters = parseFilters(parsed.searchParams.get("filters"));
    return filters.some(
      (filter) =>
        filter?.column_id === "trace_id" &&
        filterValueMatches(filter?.filter_config?.filter_value, traceId),
    );
  }
  return false;
}

function parseFilters(raw) {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function filterValueMatches(value, expected) {
  if (Array.isArray(value)) return value.map(String).includes(String(expected));
  return String(value) === String(expected);
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH)
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  if (process.platform === "linux") return "/usr/bin/google-chrome";
  return undefined;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
