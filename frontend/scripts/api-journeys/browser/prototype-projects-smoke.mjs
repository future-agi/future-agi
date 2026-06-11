import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/prototype-projects-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const listPayload = await auth.client.get(apiPath("/tracer/project/"), {
    query: {
      project_type: "experiment",
      page_number: 0,
      page_size: 20,
      sort_by: "created_at",
      sort_direction: "desc",
    },
  });
  const projects = Array.isArray(listPayload?.projects)
    ? listPayload.projects
    : [];
  assert(projects.length > 0, "Prototype project list returned no projects.");

  const project =
    projects.find(
      (candidate) =>
        candidate?.workspace === auth.workspaceId &&
        Number(candidate?.run_count || 0) > 0,
    ) || projects.find((candidate) => Number(candidate?.run_count || 0) > 0);
  assert(project, "No prototype project with runs is available for browser smoke.");
  assert(isUuid(project.id), "Selected prototype project did not include a valid id.");

  const detail = await auth.client.get(
    apiPath("/tracer/project/{id}/", { id: project.id }),
  );
  assert(detail?.id === project.id, "Prototype detail preflight id mismatch.");
  const runList = await auth.client.get(apiPath("/tracer/project-version/list_runs/"), {
    query: {
      project_id: project.id,
      page_number: 0,
      page_size: 10,
      filters: [],
      sort_params: [],
    },
  });
  const firstRun = (runList?.table || []).find((row) => isUuid(row?.id));
  assert(firstRun, "Selected prototype project did not return a run row.");

  const apiFailures = [];
  const pageErrors = [];
  const unexpectedMutations = [];
  const searchTerm = String(project.name || "").slice(0, 8);
  const evidence = {
    project_id: project.id,
    project_name: project.name,
    project_workspace: project.workspace,
    project_count: listPayload.total_count || projects.length,
    visible_null_workspace_rows: projects.filter((row) => row.workspace == null).length,
    run_count: runList?.metadata?.total_rows || 0,
    first_run_id: firstRun.id,
    first_run_name: firstRun.run_name,
  };

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
      isPrototypeApiUrl(url) &&
      ["POST", "PATCH", "PUT", "DELETE"].includes(request.method())
    ) {
      unexpectedMutations.push(`${request.method()} ${url}`);
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (isPrototypeApiUrl(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const listResponse = page.waitForResponse(
      (response) =>
        isProjectListResponse(response) &&
        response.url().includes("project_type=experiment") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(`${APP_BASE}/dashboard/prototype`, {
      waitUntil: "domcontentloaded",
    });
    await listResponse;

    await waitForVisibleText(page, "Prototype", { exact: true });
    await waitForVisibleText(page, "Create a project to experiment on your model");
    await waitForVisibleText(page, "Project Name", { exact: true });
    await waitForVisibleText(page, "No. of Datapoints", { exact: true });
    await waitForVisibleText(page, "No. of Runs", { exact: true });
    await waitForVisibleText(page, project.name, { exact: true });
    await waitForInputPlaceholder(page, "Search");

    const searchResponse = page.waitForResponse(
      (response) => {
        if (!isProjectListResponse(response) || response.status() >= 400) return false;
        const url = new URL(response.url());
        return url.searchParams.get("name") === searchTerm;
      },
      { timeout: 60000 },
    );
    await typeIntoSearchInput(page, searchTerm);
    await searchResponse;
    await waitForVisibleText(page, project.name, { exact: true });

    const detailResponse = page.waitForResponse(
      (response) =>
        response.url().includes(`/tracer/project/${project.id}/`) &&
        response.status() < 400,
      { timeout: 60000 },
    );
    const runListResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/tracer/project-version/list_runs/") &&
        response.url().includes(project.id) &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await Promise.all([
      detailResponse,
      runListResponse,
      clickVisibleRowText(page, project.name),
    ]);

    await page.waitForFunction(
      (projectId) => window.location.pathname.endsWith(`/dashboard/prototype/${projectId}`),
      { timeout: 30000 },
      project.id,
    );
    await waitForVisibleText(page, "Back", { exact: true });
    await waitForVisibleText(page, project.name, { exact: true });
    await waitForVisibleText(page, "All runs", { exact: true });
    await waitForVisibleText(page, "Export", { exact: true });
    await waitForVisibleText(page, "Configure", { exact: true });
    await waitForVisibleText(page, "Share", { exact: true });
    await waitForVisibleText(page, "Run Name");
    await waitForVisibleText(page, "Avg. Cost");
    await waitForVisibleText(page, "Avg. Latency");
    await waitForVisibleText(page, firstRun.run_name);
    await waitForNoVisibleText(page, "Invalid Date");

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Read-only prototype smoke fired mutations: ${unexpectedMutations.join("; ")}`,
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

async function waitForVisibleText(
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

async function waitForNoVisibleText(
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
      return !Array.from(document.querySelectorAll("body *")).some((element) => {
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

async function waitForInputPlaceholder(page, placeholder, timeout = 30000) {
  await page.waitForFunction(
    (expectedPlaceholder) =>
      Array.from(document.querySelectorAll("input")).some(
        (input) => input.placeholder === expectedPlaceholder,
      ),
    { timeout },
    placeholder,
  );
}

async function typeIntoSearchInput(page, value) {
  await page.waitForSelector('input[placeholder="Search"]', { timeout: 30000 });
  await page.click('input[placeholder="Search"]');
  await page.keyboard.down(modifierKey());
  await page.keyboard.press("A");
  await page.keyboard.up(modifierKey());
  await page.keyboard.press("Backspace");
  await page.type('input[placeholder="Search"]', value);
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

function isProjectListResponse(response) {
  try {
    const url = new URL(response.url());
    return url.pathname.endsWith("/tracer/project/");
  } catch {
    return false;
  }
}

function isPrototypeApiUrl(url) {
  return (
    url.includes("/tracer/project/") ||
    url.includes("/tracer/project-version/")
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
