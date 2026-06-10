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
const MONTH_SWITCH_SCREENSHOT_PATH =
  "/tmp/settings-workspace-usage-month-count-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const now = new Date();
  const month = now.getMonth() + 1;
  const year = now.getFullYear();
  const currentPeriod = buildPeriod(month, year);
  const previousPeriod = previousMonthPeriod(currentPeriod);

  const usageSummary = await auth.client.get(
    apiPath("/usage/workspace-usage-summary/"),
    { query: { month, year } },
  );
  const workspaceRows = asArray(usageSummary?.workspaces);
  const activeWorkspace = workspaceRows.find(
    (row) => row?.id === auth.workspaceId,
  );
  assert(
    activeWorkspace,
    "Workspace usage API did not include active workspace.",
  );

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
    current_month_label: currentPeriod.label,
    previous_month: previousPeriod.month,
    previous_year: previousPeriod.year,
    previous_month_label: previousPeriod.label,
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
    const currentUsageResponse = waitForUsageSummaryResponse(
      page,
      currentPeriod,
    );
    const currentEvalResponse = waitForEvalSummaryResponse(
      page,
      currentPeriod,
      auth.workspaceId,
    );

    await page.goto(
      `${APP_BASE}/dashboard/settings/workspace/${auth.workspaceId}/usage`,
      { waitUntil: "domcontentloaded" },
    );
    const [currentUsage, currentEval] = await Promise.all([
      currentUsageResponse,
      currentEvalResponse,
    ]);
    evidence.current_usage_query = queryEvidence(currentUsage);
    evidence.current_eval_query = queryEvidence(currentEval);

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
    await waitForInputValue(page, 'input[placeholder="Select month"]', {
      value: currentPeriod.label,
    });
    await waitForMetricCards(page);
    await waitForGridHeader(page, "Cost");
    await waitForAgGridSettled(page);
    await waitForNoVisibleText(page, "Invalid Date");
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    await clickVisibleTab(page, "Count");
    await waitForGridHeader(page, "Count");
    evidence.count_tab_header_visible = true;

    const previousUsageResponse = waitForUsageSummaryResponse(
      page,
      previousPeriod,
    );
    const previousEvalResponse = waitForEvalSummaryResponse(
      page,
      previousPeriod,
      auth.workspaceId,
    );
    await clickMonthOption(page, previousPeriod.label);
    const [previousUsage, previousEval] = await Promise.all([
      previousUsageResponse,
      previousEvalResponse,
    ]);
    evidence.previous_usage_query = queryEvidence(previousUsage);
    evidence.previous_eval_query = queryEvidence(previousEval);

    await waitForInputValue(page, 'input[placeholder="Select month"]', {
      value: previousPeriod.label,
    });
    await waitForMetricCards(page);
    await waitForGridHeader(page, "Count");
    await waitForAgGridSettled(page);

    await waitForNoVisibleText(page, "Invalid Date");
    await page.screenshot({
      path: MONTH_SWITCH_SCREENSHOT_PATH,
      fullPage: true,
    });
    evidence.month_switch_screenshot = MONTH_SWITCH_SCREENSHOT_PATH;

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

function buildPeriod(month, year) {
  const date = new Date(Date.UTC(year, month - 1, 1));
  const monthName = new Intl.DateTimeFormat("en-US", {
    month: "long",
    timeZone: "UTC",
  }).format(date);
  return {
    month,
    year,
    label: `${monthName} ${year}`,
  };
}

function previousMonthPeriod(period) {
  const date = new Date(Date.UTC(period.year, period.month - 2, 1));
  return buildPeriod(date.getUTCMonth() + 1, date.getUTCFullYear());
}

function matchesUsageEndpoint(response, endpointPath, period, workspaceId) {
  if (response.status() >= 400) return false;
  let url;
  try {
    url = new URL(response.url());
  } catch {
    return false;
  }
  if (url.pathname !== endpointPath) return false;
  const month = Number(url.searchParams.get("month"));
  const year = Number(url.searchParams.get("year"));
  if (month !== period.month || year !== period.year) return false;
  if (workspaceId) {
    return url.searchParams.get("workspace_id") === workspaceId;
  }
  return true;
}

async function waitForUsageSummaryResponse(page, period) {
  return page.waitForResponse(
    (response) =>
      matchesUsageEndpoint(response, "/usage/workspace-usage-summary/", period),
    { timeout: 60000 },
  );
}

async function waitForEvalSummaryResponse(page, period, workspaceId) {
  return page.waitForResponse(
    (response) =>
      matchesUsageEndpoint(
        response,
        "/usage/workspace-eval-summary/",
        period,
        workspaceId,
      ),
    { timeout: 60000 },
  );
}

function queryEvidence(response) {
  const url = new URL(response.url());
  return {
    path: url.pathname,
    month: Number(url.searchParams.get("month")),
    year: Number(url.searchParams.get("year")),
    workspace_id: url.searchParams.get("workspace_id"),
    status: response.status(),
  };
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

async function clickMonthOption(page, label) {
  await page.click('input[placeholder="Select month"]');
  await waitForVisibleText(page, label, { exact: true });
  await clickVisibleText(page, label, { exact: true });
}

async function clickVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await waitForVisibleText(page, text, { exact, timeout });
  await page.evaluate(
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
      const target = Array.from(document.querySelectorAll("body *")).find(
        (element) => {
          if (!isVisible(element)) return false;
          const textContent = normalized(element.textContent);
          if (exactMatch) return textContent === expectedText;
          return textContent.includes(expectedText);
        },
      );
      if (!target) {
        throw new Error(`Visible text not found for click: ${expectedText}`);
      }
      target.click();
    },
    { text, exact },
  );
}

async function clickVisibleTab(page, label) {
  await page.evaluate((expectedLabel) => {
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
    const tab = Array.from(document.querySelectorAll('[role="tab"]')).find(
      (element) =>
        isVisible(element) && normalized(element.textContent) === expectedLabel,
    );
    if (!tab) throw new Error(`Visible tab not found: ${expectedLabel}`);
    tab.click();
  }, label);
}

async function waitForGridHeader(page, headerName, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (expectedHeader) => {
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
      return Array.from(document.querySelectorAll(".ag-header-cell-text")).some(
        (element) =>
          isVisible(element) &&
          normalized(element.textContent) === expectedHeader,
      );
    },
    { timeout },
    headerName,
  );
}

async function waitForInputValue(
  page,
  selector,
  { value, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ selector: inputSelector, value: expectedValue }) => {
      const input = document.querySelector(inputSelector);
      return input?.value === expectedValue;
    },
    { timeout },
    { selector, value },
  );
}

async function waitForMetricCards(page) {
  await waitForNoVisibleSelector(page, ".MuiSkeleton-root");
  await waitForVisibleText(page, "Credits used", { exact: true });
  await waitForVisibleText(page, "Cost of traces (Count)", { exact: true });
  await waitForVisibleText(page, "Cost of evaluations (Count)", {
    exact: true,
  });
  await waitForVisibleText(page, "Cost of error localization (Count)", {
    exact: true,
  });
  await waitForVisibleText(page, "Cost of agent compass (Count)", {
    exact: true,
  });
  await waitForVisibleText(page, "Simulate (Count)", { exact: true });
}

async function waitForAgGridSettled(page, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    () => {
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
      const loadingSelectors = [
        '[class*="ag-skeleton"]',
        ".ag-overlay-loading-wrapper",
        ".ag-loading",
      ];
      return !loadingSelectors.some((selector) =>
        Array.from(document.querySelectorAll(selector)).some((element) =>
          isVisible(element),
        ),
      );
    },
    { timeout },
  );
}

async function waitForNoVisibleSelector(
  page,
  selector,
  { timeout = 30000 } = {},
) {
  await page.waitForFunction(
    (targetSelector) => {
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
      return !Array.from(document.querySelectorAll(targetSelector)).some(
        (element) => isVisible(element),
      );
    },
    { timeout },
    selector,
  );
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
