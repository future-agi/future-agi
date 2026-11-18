/* eslint-disable no-console */
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
const DETAIL_SCREENSHOT_PATH = "/tmp/eval-detail-smoke.png";
const USAGE_SCREENSHOT_PATH = "/tmp/eval-detail-usage-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/eval-detail-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const evalName = `ui_eval_detail_${shortRunId(auth.runId)}`;
  let evalId = null;
  let browser = null;
  let caughtError = null;

  const apiFailures = [];
  const pageErrors = [];
  const unexpectedMutations = [];
  const evidence = { eval_name: evalName };

  try {
    const created = await createCodeEval(auth.client, evalName);
    evalId = created.id;
    evidence.eval_id = evalId;

    browser = await puppeteer.launch({
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
        isEvalApiUrl(url) &&
        ["POST", "PATCH", "PUT", "DELETE"].includes(request.method())
      ) {
        unexpectedMutations.push(`${request.method()} ${url}`);
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isEvalApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponseDuring(
      page,
      "eval detail load",
      (response) =>
        response.url().includes(`/model-hub/eval-templates/${evalId}/detail/`) &&
        response.status() < 400,
      () => page.goto(`${APP_BASE}/dashboard/evaluations/${evalId}`, {
        waitUntil: "domcontentloaded",
      }),
    );

    await waitForVisibleText(page, evalName, { timeout: 30000 });
    await waitForVisibleText(page, "Eval Details", { exact: true });
    await waitForVisibleText(page, "Usage", { exact: true });
    await waitForVisibleText(page, "Code*", { exact: true });
    await waitForVisibleText(page, "Scoring", { exact: true });
    await waitForVisibleText(page, "Code evaluator returns a score between 0 and 1.");
    await waitForVisibleText(page, "Error Localization", { exact: true });
    await waitForVisibleText(page, "Description", { exact: true });
    await waitForVisibleText(page, "Tags", { exact: true });
    await waitForVisibleText(page, "Custom", { exact: true });
    await waitForVisibleText(page, "Test Evaluation", { exact: true });
    await waitForVisibleText(page, "Save Version", { exact: true });
    await waitForNoVisibleText(page, "Invalid Date", { exact: true });
    await page.screenshot({ path: DETAIL_SCREENSHOT_PATH, fullPage: true });
    evidence.detail_screenshot = DETAIL_SCREENSHOT_PATH;

    await waitForResponsesDuring(
      page,
      "eval usage tab",
      [
        (response) => isUsageResponseFor(response, evalId, "1"),
        (response) => isUsageResponseFor(response, evalId, "25"),
      ],
      () => clickVisibleText(page, "Usage", { exact: true }),
    );
    await waitForVisibleText(page, "Evaluation Logs", { exact: true });
    await waitForVisibleText(page, "Runs");
    await waitForVisibleText(page, "Success");
    await waitForVisibleText(page, "Errors");
    await waitForVisibleText(page, "Pass Rate");
    await waitForVisibleText(page, "No data for this period", { exact: true });
    await waitForVisibleText(page, "No evaluation logs for this period", {
      exact: true,
    });
    await waitForNoVisibleText(page, "Invalid Date", { exact: true });
    await page.screenshot({ path: USAGE_SCREENSHOT_PATH, fullPage: true });
    evidence.usage_screenshot = USAGE_SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Read-only eval detail smoke fired mutations: ${unexpectedMutations.join("; ")}`,
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
  } catch (error) {
    caughtError = error;
    console.error(
      JSON.stringify(
        {
          status: "failed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence,
          api_failures: apiFailures,
          page_errors: pageErrors,
          unexpected_mutations: unexpectedMutations,
        },
        null,
        2,
      ),
    );
    if (browser) {
      const pages = await browser.pages();
      const page = pages[pages.length - 1];
      await page
        ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .catch(() => null);
    }
  } finally {
    if (browser) await browser.close();
    await cleanupEvalTemplate(auth.client, evalId).catch((error) => {
      caughtError = appendCleanupError(caughtError, error);
    });
  }

  if (caughtError) throw caughtError;
}

async function createCodeEval(client, name) {
  const created = await client.post(
    apiPath("/model-hub/eval-templates/create-v2/"),
    {
      name,
      eval_type: "code",
      code: [
        "def evaluate(output=None, expected=None, **kwargs):",
        "    return True",
      ].join("\n"),
      code_language: "python",
      output_type: "pass_fail",
      pass_threshold: 0.5,
      description: "Eval detail browser smoke.",
      tags: ["api-journey", "eval-detail-ui"],
    },
  );
  assert(isUuid(created?.id), "Code eval create did not return a UUID id.");
  return created;
}

async function cleanupEvalTemplate(client, templateId) {
  if (!templateId) return;
  await client.post(
    apiPath("/model-hub/eval-templates/bulk-delete/"),
    { template_ids: [templateId] },
    { okStatuses: [200, 404] },
  );
  console.error(`cleanup eval detail template: ${templateId}`);
}

function appendCleanupError(caughtError, cleanupError) {
  if (!caughtError) return cleanupError;
  caughtError.message = `${caughtError.message}; cleanup failed: ${cleanupError.message}`;
  return caughtError;
}

function shortRunId(runId) {
  return String(runId || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .slice(-8);
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

async function waitForResponsesDuring(page, label, predicates, action) {
  try {
    await Promise.all([
      ...predicates.map((predicate) =>
        page.waitForResponse(predicate, { timeout: 60000 }),
      ),
      action(),
    ]);
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
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
      return !Array.from(document.querySelectorAll("body *")).some(
        (element) => {
          if (!isVisible(element)) return false;
          const textContent = normalized(element.textContent);
          if (exactMatch) return textContent === expectedText;
          return textContent.includes(expectedText);
        },
      );
    },
    { timeout },
    { text, exact },
  );
}

async function clickVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await waitForVisibleText(page, text, { exact, timeout });
  const clicked = await page.evaluate(
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
      const elements = Array.from(document.querySelectorAll("body *")).filter(
        (element) => {
          if (!isVisible(element)) return false;
          const textContent = normalized(element.textContent);
          return exactMatch
            ? textContent === expectedText
            : textContent.includes(expectedText);
        },
      );
      const element =
        elements.find((candidate) => candidate.getAttribute("role") === "tab") ||
        elements[0];
      if (!element) return false;
      element.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
      );
      element.dispatchEvent(
        new MouseEvent("mouseup", { bubbles: true, cancelable: true }),
      );
      element.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
      return true;
    },
    { text, exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

function isEvalApiUrl(url) {
  return (
    url.includes("/model-hub/eval-templates/") ||
    url.includes("/model-hub/eval-playground/") ||
    url.includes("/model-hub/get-eval-logs") ||
    url.includes("/model-hub/get-eval-config")
  );
}

function isUsageResponseFor(response, templateId, pageSize) {
  if (
    !response.url().includes(`/model-hub/eval-templates/${templateId}/usage/`) ||
    response.status() >= 400
  ) {
    return false;
  }
  const url = new URL(response.url());
  return url.searchParams.get("page_size") === pageSize;
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
  process.exit(1);
});
