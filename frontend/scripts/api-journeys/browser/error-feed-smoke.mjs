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
const SCREENSHOT_PATH = "/tmp/error-feed-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const preflight = await auth.client.get(apiPath("/tracer/feed/issues/"), {
    query: {
      time_range_days: 90,
      sort_by: "last_seen",
      sort_dir: "desc",
      limit: 5,
      offset: 0,
    },
  });
  const rows = asArray(preflight);
  assert(rows.length > 0, "Error Feed preflight returned no rows.");
  const row = rows[0];
  const errorName = row.error?.name || row.cluster_id;
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
  await page.setBypassServiceWorker(true);
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
      isErrorFeedApiUrl(url) &&
      ["POST", "PATCH", "PUT", "DELETE"].includes(request.method())
    ) {
      unexpectedMutations.push(`${request.method()} ${url}`);
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (isErrorFeedApiUrl(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await waitForResponseDuring(
      page,
      "initial Error Feed load",
      (response) =>
        response.url().includes("/tracer/feed/issues/") && response.status() < 400,
      () => page.goto(`${APP_BASE}/dashboard/feed`, { waitUntil: "domcontentloaded" }),
    );
    await page.waitForFunction(
      () => window.location.pathname === "/dashboard/error-feed",
      { timeout: 30000 },
    );
    await expectVisibleText(page, "Error Feed", { exact: true });
    await expectVisibleText(page, "Severity", { exact: true });
    await expectVisibleText(page, "Last seen", { exact: true });

    await waitForResponseDuring(
      page,
      "time range filter",
      (response) =>
        response.url().includes("/tracer/feed/issues/") &&
        response.url().includes("time_range_days=90") &&
        response.status() < 400,
      () => selectComboboxOption(page, "Last 7 days", "Last 90 days"),
    );
    await expectVisibleText(page, errorName, { exact: false });
    await sleep(500);

    await waitForResponseDuring(
      page,
      "severity filter",
      (response) =>
        response.url().includes("/tracer/feed/issues/") &&
        response.url().includes(`severity=${encodeURIComponent(row.severity)}`) &&
        response.status() < 400,
      () => selectComboboxOption(page, "All Severities", severityLabel(row.severity)),
    );
    await expectVisibleText(page, errorName, { exact: false });

    await clickVisibleRowText(page, errorName);
    await page.waitForFunction(
      (clusterId) => window.location.pathname.endsWith(`/dashboard/error-feed/${clusterId}`),
      { timeout: 30000 },
      row.cluster_id,
    );
    await expectVisibleText(page, "Overview", { exact: true });
    await expectVisibleText(page, "Traces", { exact: true });
    await expectVisibleText(page, "Timeline", { exact: true });
    await expectNoVisibleText(page, "Invalid Date");

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Read-only Error Feed smoke fired mutations: ${unexpectedMutations.join("; ")}`,
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
            cluster_id: row.cluster_id,
            project_id: row.project_id,
            severity: row.severity,
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

async function selectComboboxOption(page, currentText, optionText) {
  await page.waitForSelector('[role="combobox"]', { timeout: 30000 });
  const combos = await page.$$('[role="combobox"]');
  let combo = null;
  for (const candidate of combos) {
    const { text, visible } = await candidate.evaluate((el) => {
      const style = window.getComputedStyle(el);
      return {
        text: (el.textContent || "").trim(),
        visible:
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          el.getClientRects().length > 0,
      };
    });
    if (!visible) continue;
    if (text === currentText) {
      combo = candidate;
      break;
    }
  }
  assert(combo, `Could not find combobox ${currentText}.`);
  await combo.click();
  await page.waitForFunction(
    (targetText) =>
      Array.from(document.querySelectorAll('[role="option"]')).some((candidate) => {
        const style = window.getComputedStyle(candidate);
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          candidate.getClientRects().length > 0 &&
          (candidate.textContent || "").trim() === targetText
        );
      }),
    { timeout: 30000 },
    optionText,
  );
  const clicked = await page.evaluate((targetText) => {
    const options = Array.from(document.querySelectorAll('[role="option"]')).filter(
      (candidate) => {
        const style = window.getComputedStyle(candidate);
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          candidate.getClientRects().length > 0
        );
      },
    );
    const option = options.find(
      (candidate) => (candidate.textContent || "").trim() === targetText,
    );
    if (!option) return false;
    option.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    option.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    option.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return true;
  }, optionText);
  assert(clicked, `Could not select option ${optionText}.`);
}

async function clickVisibleRowText(page, text) {
  await page.waitForFunction(
    (targetText) =>
      Array.from(document.querySelectorAll("tr")).some((row) =>
        row.textContent.includes(targetText),
      ),
    { timeout: 30000 },
    text,
  );
  await page.evaluate((targetText) => {
    const row = Array.from(document.querySelectorAll("tr")).find((candidate) =>
      candidate.textContent.includes(targetText),
    );
    row?.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
  }, text);
}

async function clickVisibleText(page, text, { exact = false } = {}) {
  await page.waitForFunction(
    ({ targetText, exactMatch }) =>
      Array.from(document.querySelectorAll("body *")).some((el) => {
        const style = window.getComputedStyle(el);
        const visible =
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          el.getClientRects().length > 0;
        if (!visible) return false;
        const value = (el.textContent || "").trim();
        return exactMatch ? value === targetText : value.includes(targetText);
      }),
    { timeout: 30000 },
    { targetText: text, exactMatch: exact },
  );
  await page.evaluate(
    ({ targetText, exactMatch }) => {
      const match = Array.from(document.querySelectorAll("body *")).find((el) => {
        const style = window.getComputedStyle(el);
        const visible =
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          el.getClientRects().length > 0;
        if (!visible) return false;
        const value = (el.textContent || "").trim();
        return exactMatch ? value === targetText : value.includes(targetText);
      });
      match?.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
      match?.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      match?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    },
    { targetText: text, exactMatch: exact },
  );
}

async function expectVisibleText(page, text, { exact = false } = {}) {
  await page.waitForFunction(
    ({ targetText, exactMatch }) =>
      Array.from(document.querySelectorAll("body *")).some((el) => {
        const style = window.getComputedStyle(el);
        const visible =
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          el.getClientRects().length > 0;
        if (!visible) return false;
        const value = (el.textContent || "").trim();
        return exactMatch ? value === targetText : value.includes(targetText);
      }),
    { timeout: 30000 },
    { targetText: text, exactMatch: exact },
  );
}

async function expectNoVisibleText(page, text) {
  const found = await page.evaluate((targetText) => {
    return Array.from(document.querySelectorAll("body *")).some((el) => {
      const style = window.getComputedStyle(el);
      const visible =
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        el.getClientRects().length > 0;
      return visible && (el.textContent || "").includes(targetText);
    });
  }, text);
  assert(!found, `Unexpected visible text: ${text}`);
}

function isErrorFeedApiUrl(url) {
  return url.includes("/tracer/feed/") || url.includes("/tracer/trace-error-analysis/");
}

function severityLabel(value) {
  return String(value || "")
    .replace(/^./, (char) => char.toUpperCase())
    .trim();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function browserExecutablePath() {
  return (
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
