/* eslint-disable no-console */
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
const SCREENSHOT_PATH = "/tmp/alerts-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const list = await auth.client.get(apiPath("/tracer/user-alerts/list_monitors/"), {
    query: {
      page_number: 0,
      page_size: 10,
      sort_by: "created_at",
      sort_direction: "desc",
    },
  });
  const rows = asArray(list.table || list);
  assert(rows.length > 0, "Alerts preflight returned no rows.");

  const alert = rows.find((row) => row?.id && row?.name) || rows[0];
  assert(isUuid(alert.id), "Selected alert omitted a valid id.");
  assert(String(alert.name || "").trim(), "Selected alert omitted a name.");

  const detail = await auth.client.get(
    apiPath("/tracer/user-alerts/{id}/details/", { id: alert.id }),
  );
  assert(detail?.id === alert.id, "Alert detail preflight id mismatch.");

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
      isAlertApiUrl(url) &&
      ["POST", "PATCH", "PUT", "DELETE"].includes(request.method())
    ) {
      unexpectedMutations.push(`${request.method()} ${url}`);
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (isAlertApiUrl(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await waitForResponseDuring(
      page,
      "initial Alerts load",
      (response) =>
        response.url().includes("/tracer/user-alerts/list_monitors/") &&
        response.status() < 400,
      () => page.goto(`${APP_BASE}/dashboard/alerts`, { waitUntil: "domcontentloaded" }),
    );
    await page.waitForFunction(
      () => window.location.pathname === "/dashboard/alerts",
      { timeout: 30000 },
    );
    await expectVisibleText(page, "Alerts", { exact: true });
    await expectVisibleText(page, "New Alert", { exact: true });
    await expectVisibleText(page, "Alert Type", { exact: true });
    await expectVisibleText(page, "Status", { exact: true });
    await expectVisibleText(page, "Last Triggered", { exact: true });
    await expectVisibleText(page, alert.name, { exact: true });
    if (alert.metric_type) {
      await expectVisibleText(page, alert.metric_type, { exact: true });
    }

    const searchTerm = String(alert.name).trim();
    await waitForResponseDuring(
      page,
      "alert search",
      (response) => {
        if (
          !response.url().includes("/tracer/user-alerts/list_monitors/") ||
          response.status() >= 400
        ) {
          return false;
        }
        const url = new URL(response.url());
        return url.searchParams.get("search_text") === searchTerm;
      },
      () => typeSearch(page, searchTerm),
    );
    await expectVisibleText(page, alert.name, { exact: true });

    await waitForResponsesDuring(
      page,
      "alert details drawer",
      [
        (response) =>
          response.url().includes(`/tracer/user-alerts/${alert.id}/details/`) &&
          response.status() < 400,
        (response) =>
          response.url().includes(`/tracer/user-alerts/${alert.id}/graph/`) &&
          response.status() < 400,
      ],
      () => clickVisibleRowText(page, alert.name),
    );
    await expectVisibleText(page, alert.name, { exact: true });
    await expectVisibleText(page, "Alert Rule Details", { exact: true });
    await expectVisibleText(page, "Condition 1", { exact: true });
    await expectVisibleText(page, "Emails sent to", { exact: true });
    await expectVisibleText(page, detail.is_mute ? "Unmute" : "Mute", { exact: true });
    await expectVisibleText(page, "Duplicate", { exact: true });
    await expectVisibleText(page, "View Trace", { exact: true });
    await expectVisibleText(page, "Edit Rule", { exact: true });
    await expectNoVisibleText(page, "Invalid Date");
    await expectNoVisibleText(page, "undefined");

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(apiFailures.length === 0, `Alert API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Read-only Alerts smoke fired mutations: ${unexpectedMutations.join("; ")}`,
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
            alert_id: alert.id,
            alert_name: alert.name,
            project_id: detail.project,
            workspace_id: detail.workspace,
            metric_type: alert.metric_type,
            detail_metric_type: detail.metric_type,
            is_mute: detail.is_mute,
            list_total: list.metadata?.total_rows || rows.length,
            detail_log_total: detail.logs?.metadata?.total_rows || 0,
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

async function typeSearch(page, value) {
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

function isAlertApiUrl(url) {
  return (
    url.includes("/tracer/user-alerts/") ||
    url.includes("/tracer/user-alert-logs/")
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
