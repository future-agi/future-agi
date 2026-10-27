import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  skip,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.OBSERVE_ANNOTATION_LABEL_COLUMNS_SCREENSHOT ||
  "/tmp/observe-annotation-label-columns-smoke.png";
const MOCK_AUTH =
  process.env.OBSERVE_ANNOTATION_LABEL_COLUMNS_MOCK_AUTH === "1";

const LABELS = [
  {
    id: "00000000-0000-4000-8000-000000000001",
    name: "Quality",
    type: "star",
    settings: { no_of_stars: 5 },
  },
  {
    id: "00000000-0000-4000-8000-000000000002",
    name: "Satisfied",
    type: "thumbs_up_down",
    settings: {},
  },
  {
    id: "00000000-0000-4000-8000-000000000003",
    name: "Score",
    type: "numeric",
    settings: { min: 0, max: 10, step_size: 1 },
  },
  {
    id: "00000000-0000-4000-8000-000000000004",
    name: "Topic",
    type: "categorical",
    settings: {
      options: [{ label: "Billing" }, { label: "Support" }],
    },
  },
  {
    id: "00000000-0000-4000-8000-000000000005",
    name: "Writing assistance",
    type: "text",
    settings: {},
  },
];

async function main() {
  const auth = MOCK_AUTH
    ? createMockAuthContext()
    : await createAuthenticatedContext();
  const project = MOCK_AUTH
    ? mockProject()
    : await resolveObserveProject(auth.client);
  const apiFailures = [];
  const pageErrors = [];

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  await page.setBypassServiceWorker(true);

  if (MOCK_AUTH) {
    try {
      await runMockModuleSmoke(page, auth, project);
    } finally {
      await browser.close();
    }
    return;
  }

  const authStorage = {
    tokens: auth.tokens,
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
    user: auth.user,
  };

  await page.evaluateOnNewDocument(seedAuthStorage, authStorage);

  await page.setRequestInterception(true);
  let mockedTraceListRequests = 0;
  const observedRequestPaths = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (
      url.pathname.includes("/accounts/") ||
      url.pathname.includes("/tracer/") ||
      url.pathname.includes("/model-hub/")
    ) {
      observedRequestPaths.push(url.pathname);
    }
    if (MOCK_AUTH && url.pathname.includes("/accounts/user-info")) {
      request.respond(jsonResponse(mockUserInfo()));
      return;
    }
    if (MOCK_AUTH && url.pathname.includes("/accounts/workspace/list")) {
      request.respond(jsonResponse(mockWorkspaces()));
      return;
    }
    if (MOCK_AUTH && url.pathname.includes("/tracer/project/list_projects")) {
      request.respond(jsonResponse(mockProjectList(project)));
      return;
    }
    if (MOCK_AUTH && url.pathname.includes(`/tracer/project/${project.id}`)) {
      request.respond(jsonResponse({ status: true, result: project }));
      return;
    }
    if (MOCK_AUTH && url.pathname.includes("/tracer/dashboard/metrics")) {
      request.respond(jsonResponse({ status: true, result: [] }));
      return;
    }
    if (MOCK_AUTH && url.pathname.includes("/tracer/graph")) {
      request.respond(jsonResponse({ status: true, result: [] }));
      return;
    }
    if (url.pathname.includes("/tracer/trace/list_traces_of_session")) {
      mockedTraceListRequests += 1;
      request.respond(jsonResponse(mockTraceList(project.id)));
      return;
    }
    if (url.pathname.includes("/model-hub/annotations-labels")) {
      request.respond(jsonResponse(mockLabels()));
      return;
    }
    request.continue();
  });
  page.on("response", (response) => {
    const url = response.url();
    if (
      (url.includes("/tracer/trace/list_traces_of_session/") ||
        url.includes("/model-hub/annotations-labels/")) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await page.goto(APP_BASE, { waitUntil: "domcontentloaded" });
    await page.evaluate(seedAuthStorage, authStorage);
    await page.goto(`${APP_BASE}/dashboard/observe/${project.id}/llm-tracing`, {
      waitUntil: "domcontentloaded",
    });

    await waitUntil(() => mockedTraceListRequests > 0, 60000);
    assert(
      mockedTraceListRequests > 0,
      `Trace list request was not intercepted by the annotation label column smoke. Observed API paths: ${[
        ...new Set(observedRequestPaths),
      ].join(", ")}`,
    );

    await page.waitForFunction(
      (names) =>
        names.every((name) =>
          [
            ...document.querySelectorAll(
              ".ag-header-cell-text, [role='columnheader']",
            ),
          ]
            .map((node) => node.textContent || "")
            .some((text) => text.includes(name)),
        ),
      { timeout: 60000 },
      LABELS.map((label) => label.name),
    );

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          screenshot: SCREENSHOT_PATH,
          evidence: {
            project_id: project.id,
            label_names: LABELS.map((label) => label.name),
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    if (apiFailures.length || pageErrors.length) {
      console.error(
        JSON.stringify(
          {
            apiFailures,
            pageErrors,
          },
          null,
          2,
        ),
      );
    }
    await page
      .screenshot({
        path: SCREENSHOT_PATH.replace(/\.png$/, "-failure.png"),
        fullPage: true,
      })
      .catch(() => null);
    throw error;
  } finally {
    await browser.close();
  }
}

async function runMockModuleSmoke(page, auth, project) {
  await page.goto(APP_BASE, { waitUntil: "domcontentloaded" });
  const generatedColumns = await page.evaluate(async (labels) => {
    const { generateAnnotationColumnsForTracing } = await import(
      "/src/sections/projects/LLMTracing/common.js"
    );
    const items = labels.map((label) => ({
      id: label.id,
      name: label.name,
      groupBy: "Annotation Metrics",
      annotationLabelType: label.type,
      settings: label.settings,
      annotators: label.type === "text" ? null : {},
    }));
    return generateAnnotationColumnsForTracing(items).map((column) => ({
      field: column.field,
      headerName: column.headerName,
      displayName: column.headerComponentParams?.displayName || "",
    }));
  }, LABELS);

  const missingLabels = LABELS.filter(
    (label) =>
      !generatedColumns.some(
        (column) =>
          column.headerName === label.name || column.displayName === label.name,
      ),
  );
  assert(
    missingLabels.length === 0,
    `Missing annotation label columns: ${missingLabels
      .map((label) => label.name)
      .join(", ")}`,
  );

  await page.setContent(`
    <!doctype html>
    <html>
      <body style="margin:0;background:#0b0f14;color:#f6f7f9;font-family:Inter,Arial,sans-serif;">
        <main style="padding:32px;">
          <h1 style="font-size:24px;margin:0 0 16px;">TH-5194 Annotation Label Columns</h1>
          <p style="color:#aab0bb;margin:0 0 24px;">Headless browser smoke generated these columns from the real LLM tracing column module.</p>
          <div style="display:grid;grid-template-columns:repeat(5,minmax(150px,1fr));gap:12px;">
            ${generatedColumns
              .map(
                (column) =>
                  `<section style="border:1px solid #303846;border-radius:8px;padding:14px;background:#121821;">
                    <div style="font-size:12px;color:#8791a1;">${escapeHtml(column.field)}</div>
                    <div style="font-size:16px;font-weight:600;margin-top:8px;">${escapeHtml(
                      column.displayName || column.headerName,
                    )}</div>
                  </section>`,
              )
              .join("")}
          </div>
        </main>
      </body>
    </html>
  `);
  await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

  console.log(
    JSON.stringify(
      {
        status: "passed",
        mode: "module-smoke",
        app_base: APP_BASE,
        api_base: auth.apiBase,
        screenshot: SCREENSHOT_PATH,
        evidence: {
          project_id: project.id,
          label_names: LABELS.map((label) => label.name),
          generated_columns: generatedColumns,
        },
      },
      null,
      2,
    ),
  );
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function seedAuthStorage({ tokens, organizationId, workspaceId, user }) {
  localStorage.setItem("accessToken", tokens.access);
  localStorage.setItem("refreshToken", tokens.refresh || "");
  localStorage.setItem("rememberMe", "true");
  localStorage.setItem("initial-render", "done");
  if (organizationId) sessionStorage.setItem("organizationId", organizationId);
  if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
  if (user?.id) {
    sessionStorage.setItem("futureagi-current-user-id", user.id);
    sessionStorage.setItem("currentUserId", user.id);
  }
}

async function resolveObserveProject(client) {
  if (process.env.OBSERVE_PROJECT_ID) {
    return { id: process.env.OBSERVE_PROJECT_ID, name: "env observe project" };
  }
  const projects = asArray(
    await client.get(apiPath("/tracer/project/list_projects/"), {
      query: { page_number: 0, page_size: 100 },
    }),
  );
  const project = projects.find(
    (candidate) =>
      candidate?.id &&
      (!candidate.trace_type ||
        candidate.trace_type === "observe" ||
        candidate.trace_type === "experiment"),
  );
  if (!project?.id) {
    skip("No observe project found for annotation label column smoke.");
  }
  return project;
}

function createMockAuthContext() {
  return {
    client: null,
    tokens: {
      access: makeMockJwt(),
      refresh: "",
    },
    apiBase: process.env.API_BASE || "http://localhost:8003",
    organizationId: "00000000-0000-4000-8000-000000000201",
    workspaceId: "00000000-0000-4000-8000-000000000202",
    user: {
      id: "00000000-0000-4000-8000-000000000203",
      email: "observe-label-columns-smoke@futureagi.local",
      name: "Observe Label Columns Smoke",
    },
  };
}

function makeMockJwt() {
  const header = base64UrlEncode({ alg: "HS256", typ: "JWT" });
  const payload = base64UrlEncode({
    user_id: "00000000-0000-4000-8000-000000000203",
    exp: Math.floor(Date.now() / 1000) + 3600,
  });
  return `${header}.${payload}.observe-label-columns-smoke`;
}

function base64UrlEncode(value) {
  return Buffer.from(JSON.stringify(value))
    .toString("base64")
    .replace(/=/g, "")
    .replace(/\+/g, "-")
    .replace(/\//g, "_");
}

function mockProject() {
  return {
    id:
      process.env.OBSERVE_PROJECT_ID || "00000000-0000-4000-8000-000000000301",
    name: "TH-5194 Label Column Smoke",
    source: "observe",
    trace_type: "observe",
  };
}

function mockUserInfo() {
  return createMockAuthContext().user;
}

function mockWorkspaces() {
  const auth = createMockAuthContext();
  return {
    status: true,
    results: [
      {
        id: auth.workspaceId,
        name: "Default Workspace",
        organization_id: auth.organizationId,
      },
    ],
  };
}

function mockProjectList(project) {
  return {
    status: true,
    results: [project],
    count: 1,
    next: null,
    previous: null,
  };
}

function mockTraceList(projectId) {
  const config = [
    {
      id: "trace_name",
      name: "Trace",
      group_by: "Trace",
      is_visible: true,
      output_type: "text",
    },
    ...LABELS.map((label) => ({
      id: label.id,
      name: label.name,
      group_by: "Annotation Metrics",
      is_visible: true,
      output_type: label.type === "text" ? "text" : "float",
      reverse_output: false,
      annotation_label_type: label.type,
      settings: label.settings,
      annotators: label.type === "text" ? null : {},
    })),
  ];

  return {
    status: true,
    result: {
      metadata: { total_rows: 1 },
      config,
      table: [
        {
          trace_id: "00000000-0000-4000-8000-000000000101",
          project_id: projectId,
          trace_name: "TH-5194 label column smoke",
          input: "hello",
          output: "world",
          created_at: new Date(0).toISOString(),
          start_time: new Date(0).toISOString(),
          status: "OK",
          [LABELS[0].id]: { score: 5, annotators: {} },
          [LABELS[1].id]: { thumbs_up: 1, thumbs_down: 0, annotators: {} },
          [LABELS[2].id]: { score: 8, annotators: {} },
          [LABELS[3].id]: { Billing: 1, Support: 0, annotators: {} },
        },
      ],
    },
  };
}

function mockLabels() {
  return {
    count: LABELS.length,
    next: null,
    previous: null,
    total_pages: 1,
    current_page: 1,
    results: LABELS,
  };
}

function jsonResponse(body) {
  return {
    status: 200,
    headers: {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
      "Access-Control-Allow-Headers":
        "authorization,content-type,x-organization-id,x-workspace-id",
    },
    contentType: "application/json",
    body: JSON.stringify(body),
  };
}

async function waitUntil(predicate, timeoutMs) {
  const startedAt = Date.now();
  while (!predicate()) {
    if (Date.now() - startedAt > timeoutMs) return;
    await new Promise((resolve) => {
      setTimeout(resolve, 100);
    });
  }
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
  if (error?.name === "SkipJourney") {
    console.log(JSON.stringify({ status: "skipped", reason: error.reason }));
    return;
  }
  console.error(error);
  process.exitCode = 1;
});
