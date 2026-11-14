import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/settings-global-integrations-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const listPayload = await auth.client.get(apiPath("/integrations/connections/"), {
    query: { page_number: 0, page_size: 20 },
  });
  const connections = Array.isArray(listPayload?.connections)
    ? listPayload.connections
    : [];
  assert(connections.length > 0, "No integration connection is available for global settings smoke.");
  const connection = connections[0];
  const displayName =
    connection.display_name || connection.external_project_name || "Unnamed";
  const hostUrl = connection.host_url || "-";
  const traceLabel = `${Number(connection.total_traces_synced || 0).toLocaleString()} traces`;

  const detail = await auth.client.get(
    apiPath("/integrations/connections/{id}/", { id: connection.id }),
  );
  const logsPayload = await auth.client.get(apiPath("/integrations/sync-logs/"), {
    query: { connection_id: connection.id, page_number: 0, page_size: 10 },
  });

  const apiFailures = [];
  const pageErrors = [];
  const unexpectedMutations = [];
  const evidence = {
    connection_id: connection.id,
    display_name: displayName,
    host_url: hostUrl,
    status: connection.status,
    sync_log_count: logsPayload?.metadata?.total_count ?? 0,
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
      url.includes("/integrations/connections/") &&
      ["POST", "PATCH", "PUT", "DELETE"].includes(request.method())
    ) {
      unexpectedMutations.push(`${request.method()} ${url}`);
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (
      (url.includes("/integrations/connections/") ||
        url.includes("/integrations/sync-logs/")) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const listResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/integrations/connections/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(`${APP_BASE}/dashboard/settings/integrations`, {
      waitUntil: "domcontentloaded",
    });
    await listResponse;

    await waitForVisibleText(page, "Integrations", { exact: true });
    await waitForVisibleText(page, "Connect external observability platforms to import traces");
    await waitForVisibleText(page, "Available Platforms", { exact: true });
    await waitForVisibleText(page, displayName);
    await waitForVisibleText(page, hostUrl);
    await waitForVisibleText(page, traceLabel);
    if (connection.status) {
      await waitForVisibleText(page, startCase(connection.status), { exact: true });
    }

    const detailResponse = page.waitForResponse(
      (response) =>
        response.url().includes(`/integrations/connections/${connection.id}/`) &&
        response.status() < 400,
      { timeout: 60000 },
    );
    const logsResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/integrations/sync-logs/") &&
        response.url().includes(connection.id) &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await Promise.all([
      detailResponse,
      logsResponse,
      clickVisibleText(page, displayName),
    ]);

    await page.waitForFunction(
      (expectedId) => window.location.pathname.endsWith(`/settings/integrations/${expectedId}`),
      { timeout: 30000 },
      connection.id,
    );
    await waitForVisibleText(page, "Back to Integrations", { exact: true });
    await waitForVisibleText(page, detail.display_name, { exact: true });
    await waitForVisibleText(page, detail.host_url, { exact: true });
    await waitForVisibleText(page, "Public Key", { exact: true });
    await waitForVisibleText(page, "Secret Key", { exact: true });
    await waitForVisibleText(page, detail.public_key_display, { exact: true });
    await waitForVisibleText(page, detail.secret_key_display, { exact: true });
    await waitForVisibleText(page, "Sync Status", { exact: true });
    await waitForVisibleText(page, "Sync History", { exact: true });
    await waitForVisibleText(page, "Time", { exact: true });
    await waitForVisibleText(page, "Traces", { exact: true });
    await waitForVisibleText(page, "Spans", { exact: true });
    await waitForVisibleText(page, "Scores", { exact: true });
    await waitForVisibleText(page, "Status", { exact: true });

    await waitForNoVisibleText(page, "Unnamed", { exact: true });
    await waitForNoVisibleText(page, "Invalid Date");
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Read-only global integrations smoke fired mutations: ${unexpectedMutations.join("; ")}`,
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

async function clickVisibleText(page, text) {
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
      return Array.from(document.querySelectorAll("body *")).some(
        (candidate) =>
          isVisible(candidate) &&
          String(candidate.textContent || "").includes(expectedText) &&
          Boolean(candidate.closest(".MuiCardActionArea-root,button,a,[role='button']")),
      );
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
        String(candidate.textContent || "").includes(expectedText) &&
        Boolean(candidate.closest(".MuiCardActionArea-root,button,a,[role='button']")),
    );
    element.closest(".MuiCardActionArea-root,button,a,[role='button']").click();
  }, text);
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

function startCase(value) {
  return String(value || "")
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part[0].toUpperCase() + part.slice(1).toLowerCase())
    .join(" ");
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
