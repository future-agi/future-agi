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
const SCREENSHOT_PATH = "/tmp/tasks-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const list = await auth.client.get(
    apiPath("/tracer/eval-task/list_eval_tasks_with_project_name/"),
    {
      query: {
        page_number: 0,
        page_size: 10,
        sort_params: JSON.stringify([
          { column_id: "created_at", direction: "desc" },
        ]),
      },
    },
  );
  const rows = asArray(list.table || list);
  assert(rows.length > 0, "Tasks preflight returned no rows.");

  const task =
    rows.find((row) => row?.id && row?.name && row?.filters_applied?.project_id) ||
    rows.find((row) => row?.id && row?.name);
  assert(task, "Tasks preflight did not find a selectable task.");
  const taskProjectId = task.project_id || task.filters_applied?.project_id;
  assert(isUuid(task.id), "Selected task omitted a valid id.");
  assert(String(task.name || "").trim(), "Selected task omitted a name.");

  const detail = await auth.client.get(
    apiPath("/tracer/eval-task/get_eval_details/"),
    { query: { eval_id: task.id } },
  );
  assert(detail?.id === task.id, "Task detail preflight id mismatch.");

  const taskApiFailures = [];
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
      isTaskApiUrl(url) &&
      ["POST", "PATCH", "PUT", "DELETE"].includes(request.method())
    ) {
      unexpectedMutations.push(`${request.method()} ${url}`);
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (isTaskApiUrl(url) && response.status() >= 400) {
      taskApiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await waitForResponseDuring(
      page,
      "initial Tasks load",
      (response) =>
        response.url().includes("/tracer/eval-task/list_eval_tasks_with_project_name/") &&
        response.status() < 400,
      () => page.goto(`${APP_BASE}/dashboard/tasks`, { waitUntil: "domcontentloaded" }),
    );
    await page.waitForFunction(
      () => window.location.pathname === "/dashboard/tasks",
      { timeout: 30000 },
    );
    await expectVisibleText(page, "Tasks", { exact: true });
    await expectVisibleText(page, "Create Task", { exact: true });
    await expectVisibleText(page, "Task Name", { exact: true });
    await expectVisibleText(page, "Project", { exact: true });

    const searchTerm = String(task.name).trim();
    await waitForResponseDuring(
      page,
      "task search",
      (response) => {
        if (
          !response.url().includes("/tracer/eval-task/list_eval_tasks_with_project_name/") ||
          response.status() >= 400
        ) {
          return false;
        }
        const url = new URL(response.url());
        return url.searchParams.get("name") === searchTerm;
      },
      () => typeSearch(page, searchTerm),
    );
    await expectVisibleText(page, task.name, { exact: true });

    await waitForResponseDuring(
      page,
      "task detail navigation",
      (response) => {
        if (
          !response.url().includes("/tracer/eval-task/get_eval_details/") ||
          response.status() >= 400
        ) {
          return false;
        }
        const url = new URL(response.url());
        return url.searchParams.get("eval_id") === task.id;
      },
      () => clickVisibleRowText(page, task.name),
    );
    await page.waitForFunction(
      (taskId) => window.location.pathname.endsWith(`/dashboard/tasks/${taskId}`),
      { timeout: 30000 },
      task.id,
    );
    await expectVisibleText(page, task.name, { exact: true });
    await expectVisibleText(page, "Details", { exact: true });
    await expectVisibleText(page, "Logs", { exact: true });
    await expectVisibleText(page, "Usage", { exact: true });
    await expectNoVisibleText(page, "Invalid Date");

    await waitForResponseDuring(
      page,
      "task logs tab",
      (response) => {
        if (
          !response.url().includes("/tracer/eval-task/get_eval_task_logs/") ||
          response.status() >= 400
        ) {
          return false;
        }
        const url = new URL(response.url());
        return url.searchParams.get("eval_task_id") === task.id;
      },
      () => clickVisibleText(page, "Logs"),
    );
    await expectVisibleText(page, "Successful", { exact: true });
    await expectVisibleText(page, "Errors", { exact: true });

    await waitForResponseDuring(
      page,
      "task usage tab",
      (response) => {
        if (
          !response.url().includes("/tracer/eval-task/get_usage/") ||
          response.status() >= 400
        ) {
          return false;
        }
        const url = new URL(response.url());
        return url.searchParams.get("eval_task_id") === task.id;
      },
      () => clickVisibleText(page, "Usage"),
    );
    await expectVisibleText(page, "Evaluation Runs", { exact: true });
    await expectVisibleText(page, "Runs");
    await expectNoVisibleText(page, "Invalid Date");

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(taskApiFailures.length === 0, `Task API failures: ${taskApiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Read-only Tasks smoke fired mutations: ${unexpectedMutations.join("; ")}`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence: {
            task_id: task.id,
            task_name: task.name,
            project_id: taskProjectId,
            project_name: task.project_name,
            list_total: list.metadata?.total_rows || rows.length,
            detail_eval_count: asArray(detail.evals_applied).length,
            screenshot: SCREENSHOT_PATH,
          },
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

async function typeSearch(page, value) {
  const selector = 'input[placeholder="Search tasks..."]';
  await page.waitForSelector(selector, { timeout: 30000 });
  await page.click(selector);
  await page.keyboard.down(modifierKey());
  await page.keyboard.press("A");
  await page.keyboard.up(modifierKey());
  await page.keyboard.press("Backspace");
  await page.type(selector, value);
}

async function expectVisibleText(page, text, { exact = false, timeout = 30000 } = {}) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      return window.__apiJourneyVisibleElements().some((element) => {
        const textContent = window.__apiJourneyNormalizeText(element.textContent);
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
      return !window.__apiJourneyVisibleElements().some((element) =>
        window.__apiJourneyNormalizeText(element.textContent).includes(expectedText),
      );
    },
    { timeout },
    text,
  );
}

async function clickVisibleText(page, text) {
  await page.waitForFunction(
    (expectedText) =>
      window.__apiJourneyVisibleElements().some(
        (element) =>
          window.__apiJourneyNormalizeText(element.textContent) === expectedText,
      ),
    { timeout: 30000 },
    text,
  );
  const clicked = await page.evaluate((expectedText) => {
    const element = window.__apiJourneyVisibleElements().find(
      (candidate) =>
        window.__apiJourneyNormalizeText(candidate.textContent) === expectedText,
    );
    const clickable =
      element?.closest("button,[role='tab'],[role='button'],a") || element;
    if (!clickable) return false;
    clickable.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    clickable.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    clickable.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return true;
  }, text);
  assert(clicked, `Could not click visible text ${text}.`);
}

async function clickVisibleRowText(page, text) {
  await page.waitForFunction(
    (expectedText) =>
      window.__apiJourneyVisibleElements().some((element) => {
        if (window.__apiJourneyNormalizeText(element.textContent) !== expectedText) {
          return false;
        }
        return Boolean(
          element.closest("tr,[role='row'],.MuiTableRow-root,[data-row-id]"),
        );
      }),
    { timeout: 30000 },
    text,
  );
  const clicked = await page.evaluate((expectedText) => {
    const element = window.__apiJourneyVisibleElements().find((candidate) => {
      if (window.__apiJourneyNormalizeText(candidate.textContent) !== expectedText) {
        return false;
      }
      return Boolean(
        candidate.closest("tr,[role='row'],.MuiTableRow-root,[data-row-id]"),
      );
    });
    const row = element?.closest("tr,[role='row'],.MuiTableRow-root,[data-row-id]");
    if (!row) return false;
    row.click();
    return true;
  }, text);
  assert(clicked, `Could not click row text ${text}.`);
}

function isTaskApiUrl(url) {
  return (
    url.includes("/tracer/eval-task/") ||
    url.includes("/tracer/custom-eval-config/")
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
