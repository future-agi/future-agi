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
const SCREENSHOT_PATH = "/tmp/observe-projects-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const list = await auth.client.get(apiPath("/tracer/project/list_projects/"), {
    query: {
      project_type: "observe",
      page_number: 0,
      page_size: 25,
      sort_by: "updated_at",
      sort_direction: "desc",
    },
  });
  const projects = asArray(list);
  assert(projects.length > 0, "Observe project list returned no projects.");

  const { project, detail } = await selectCurrentWorkspaceProject(auth, projects);
  const searchTerm = String(project.name || "").slice(0, 8).trim();
  assert(searchTerm, "Selected observe project name could not produce a search term.");

  const evidence = {
    project_id: project.id,
    project_name: project.name,
    project_workspace: detail.workspace,
    project_count: list.metadata?.total_rows || projects.length,
    visible_null_workspace_rows: await countNullWorkspaceRows(auth, projects),
    last_30_days_vol: project.last_30_days_vol,
    daily_volume_points: asArray(project.daily_volume).length,
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
  await installRuntimeConfig(page, auth);
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      if (organizationId) sessionStorage.setItem("organizationId", organizationId);
      if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
      if (user?.id) sessionStorage.setItem("futureagi-current-user-id", user.id);
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
      isObserveProjectApiUrl(url) &&
      ["POST", "PATCH", "PUT", "DELETE"].includes(request.method())
    ) {
      unexpectedMutations.push(`${request.method()} ${url}`);
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (isObserveProjectApiUrl(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const listResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/tracer/project/list_projects/") &&
        response.url().includes("project_type=observe") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(`${APP_BASE}/dashboard/observe`, { waitUntil: "domcontentloaded" });
    await listResponse;

    await expectVisibleText(page, "Tracing", { exact: true });
    await expectVisibleText(page, "Project", { exact: true });
    await expectVisibleText(page, "Alerts", { exact: true });
    await expectVisibleText(page, "Volume (30d)", { exact: true });
    await expectVisibleText(page, "Tags", { exact: true });
    await expectVisibleText(page, "Last Active", { exact: true });
    await expectVisibleText(page, project.name, { exact: true });
    await page.waitForSelector('input[placeholder="Search"]', { timeout: 30000 });

    const searchResponse = page.waitForResponse(
      (response) => {
        if (
          !response.url().includes("/tracer/project/list_projects/") ||
          response.status() >= 400
        ) {
          return false;
        }
        const url = new URL(response.url());
        return url.searchParams.get("name") === searchTerm;
      },
      { timeout: 60000 },
    );
    await typeSearch(page, searchTerm);
    await searchResponse;
    await expectVisibleText(page, project.name, { exact: true });

    const detailResponse = page.waitForResponse(
      (response) =>
        response.url().includes(`/tracer/project/${project.id}/`) &&
        response.status() < 400,
      { timeout: 60000 },
    );
    const traceListResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/tracer/trace/list_traces_of_session/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await Promise.all([
      detailResponse,
      traceListResponse,
      clickVisibleRowText(page, project.name),
    ]);

    await page.waitForFunction(
      (projectId) =>
        window.location.pathname.endsWith(`/dashboard/observe/${projectId}/llm-tracing`),
      { timeout: 30000 },
      project.id,
    );
    await expectVisibleText(page, "Traces");
    await expectVisibleText(page, "Filter", { exact: true });
    await expectVisibleText(page, "Past");
    await expectNoVisibleText(page, "Invalid Date");

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Read-only observe project smoke fired mutations: ${unexpectedMutations.join("; ")}`,
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

async function selectCurrentWorkspaceProject(auth, projects) {
  for (const project of projects) {
    if (!isUuid(project?.id)) continue;
    const detail = await auth.client.get(
      apiPath("/tracer/project/{id}/", { id: project.id }),
    );
    if (detail?.trace_type === "observe" && detail?.workspace === auth.workspaceId) {
      return { project, detail };
    }
  }
  throw new Error("No current-workspace observe project was found on the first page.");
}

async function countNullWorkspaceRows(auth, projects) {
  let count = 0;
  for (const project of projects) {
    const detail = await auth.client.get(
      apiPath("/tracer/project/{id}/", { id: project.id }),
    );
    if (detail?.workspace == null) count += 1;
  }
  return count;
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

async function typeSearch(page, value) {
  await page.waitForSelector('input[placeholder="Search"]', { timeout: 30000 });
  await page.click('input[placeholder="Search"]');
  await page.keyboard.down(modifierKey());
  await page.keyboard.press("A");
  await page.keyboard.up(modifierKey());
  await page.keyboard.press("Backspace");
  await page.type('input[placeholder="Search"]', value);
}

async function expectVisibleText(page, text, { exact = false, timeout = 30000 } = {}) {
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

async function expectNoVisibleText(page, text, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (expectedText) => {
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
        (element) => isVisible(element) && element.textContent?.includes(expectedText),
      );
    },
    { timeout },
    text,
  );
}

async function clickVisibleRowText(page, text) {
  await page.waitForFunction(
    (expectedText) => {
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
        if (String(element.textContent || "").trim() !== expectedText) return false;
        return Boolean(
          element.closest("tr,[role='row'],.MuiTableRow-root,[data-row-id]"),
        );
      });
    },
    { timeout: 30000 },
    text,
  );
  await page.evaluate((expectedText) => {
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
        String(candidate.textContent || "").trim() === expectedText &&
        Boolean(candidate.closest("tr,[role='row'],.MuiTableRow-root,[data-row-id]")),
    );
    const row = element.closest("tr,[role='row'],.MuiTableRow-root,[data-row-id]");
    row.click();
  }, text);
}

function isObserveProjectApiUrl(url) {
  return (
    url.includes("/tracer/project/list_projects/") ||
    url.includes("/tracer/project/") ||
    url.includes("/tracer/trace/list_traces_of_session/") ||
    url.includes("/tracer/dashboard/metrics/")
  );
}

function modifierKey() {
  return process.platform === "darwin" ? "Meta" : "Control";
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH) return process.env.PUPPETEER_EXECUTABLE_PATH;
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
