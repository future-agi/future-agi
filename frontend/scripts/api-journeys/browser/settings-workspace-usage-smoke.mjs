import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/settings-workspace-usage-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const now = new Date();
  const month = now.getMonth() + 1;
  const year = now.getFullYear();

  const usageSummary = await auth.client.get(
    apiPath("/usage/workspace-usage-summary/"),
    { query: { month, year } },
  );
  const workspaceRows = asArray(usageSummary?.workspaces);
  const activeWorkspace = workspaceRows.find((row) => row?.id === auth.workspaceId);
  assert(activeWorkspace, "Workspace usage API did not include active workspace.");

  const evalSummary = await auth.client.get(
    apiPath("/usage/workspace-eval-summary/"),
    { query: { workspace_id: auth.workspaceId, month, year } },
  );
  assert(
    Array.isArray(evalSummary?.evaluations),
    "Workspace eval summary API did not return evaluations array.",
  );
  assert(
    typeof evalSummary?.total?.cost === "number" &&
      Number.isInteger(evalSummary?.total?.count),
    "Workspace eval summary API did not return numeric total cost/count.",
  );

  const apiFailures = [];
  const pageErrors = [];
  const evidence = {
    workspace_id: auth.workspaceId,
    workspace_name: activeWorkspace.name,
    month,
    year,
    workspace_overall_count: activeWorkspace.overall?.count,
    workspace_eval_count: evalSummary.total.count,
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
  page.on("response", (response) => {
    const url = response.url();
    if (
      (url.includes("/usage/workspace-usage-summary/") ||
        url.includes("/usage/workspace-eval-summary/")) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const usageTotalsResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/usage/workspace-usage-summary/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    const evalSummaryResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/usage/workspace-eval-summary/") &&
        response.status() < 400,
      { timeout: 60000 },
    );

    await page.goto(
      `${APP_BASE}/dashboard/settings/workspace/${auth.workspaceId}/usage`,
      { waitUntil: "domcontentloaded" },
    );
    await usageTotalsResponse;
    await evalSummaryResponse;

    await waitForVisibleText(page, "Usage Summary", { exact: true });
    await waitForVisibleText(page, "Workspace usage summary", { exact: true });
    await waitForVisibleText(page, "Month", { exact: true });
    await waitForVisibleText(page, "Evaluation Cost Usage Breakdown", {
      exact: true,
    });
    await waitForVisibleText(page, "Detailed cost usage by evaluation", {
      exact: true,
    });
    await waitForVisibleText(page, "Cost", { exact: true });
    await waitForVisibleText(page, "Count", { exact: true });
    await waitForVisibleText(page, "Evaluations", { exact: true });
    await waitForVisibleText(page, "Total", { exact: true });

    await waitForNoVisibleText(page, "Invalid Date");
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

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
