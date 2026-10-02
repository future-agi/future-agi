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
const SCREENSHOT_PATH = "/tmp/error-feed-linear-connect-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const linearTeams = await auth.client.get(
    apiPath("/tracer/feed/integrations/linear/teams/"),
  );
  assert(
    linearTeams?.connected === false,
    "Linear is connected in this workspace; this smoke only covers the safe no-integration guard.",
  );

  const feed = await auth.client.get(apiPath("/tracer/feed/issues/"), {
    query: {
      time_range_days: 90,
      sort_by: "last_seen",
      sort_dir: "desc",
      limit: 20,
      offset: 0,
    },
  });
  const row = asArray(feed).find((candidate) => {
    return (
      candidate?.cluster_id &&
      candidate?.error?.name &&
      !candidate.external_issue_url &&
      !candidate.externalIssueUrl
    );
  });
  assert(
    row,
    "No unlinked Error Feed issue was available for the Linear guard smoke.",
  );

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

  try {
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installAuthState(page, auth);
    await page.evaluateOnNewDocument(() => {
      window.__apiJourneyVisibleText = () => {
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
        return Array.from(document.querySelectorAll("body *"))
          .filter(isVisible)
          .map((element) => String(element.textContent || "").trim())
          .filter(Boolean);
      };
    });

    page.on("request", (request) => {
      const url = request.url();
      if (isLinearMutationUrl(url) && request.method() === "POST") {
        unexpectedMutations.push(`${request.method()} ${url}`);
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isRelevantUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponseDuring(
      page,
      "Error Feed detail load",
      (response) =>
        response
          .url()
          .includes(
            `/tracer/feed/issues/${encodeURIComponent(row.cluster_id)}/`,
          ) && response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/error-feed/${row.cluster_id}`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await expectVisibleText(page, row.error.name);
    await expectVisibleText(page, "Integrations", { exact: true });
    await expectVisibleText(page, "Linear", { exact: true });
    await expectVisibleText(page, "Not connected", { exact: true });
    await expectVisibleText(page, "Connect", { exact: true });

    await waitForResponseDuring(
      page,
      "settings integrations navigation",
      (response) =>
        response.url().includes("/integrations/connections/") &&
        response.status() < 400,
      () => clickVisibleText(page, "Connect", { exact: true }),
    );
    await page.waitForFunction(
      () => window.location.pathname === "/dashboard/settings/integrations",
      { timeout: 30000 },
    );
    await expectVisibleText(page, "Integrations", { exact: true });
    await expectVisibleText(page, "Available Platforms", { exact: true });
    await expectNoVisibleText(page, "Invalid Date");

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Disconnected Linear Connect fired issue mutations: ${unexpectedMutations.join("; ")}`,
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
            linear_connected: linearTeams.connected,
            linear_team_count: asArray(linearTeams.teams).length,
            navigated_to: "/dashboard/settings/integrations",
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

async function installAuthState(page, auth) {
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
}

async function waitForResponseDuring(page, label, predicate, action) {
  try {
    const [response] = await Promise.all([
      page.waitForResponse(predicate, { timeout: 60000 }),
      action(),
    ]);
    return response;
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
}

async function expectVisibleText(page, text, { exact = false } = {}) {
  await page.waitForFunction(
    ({ expectedText, exactMatch }) => {
      return window.__apiJourneyVisibleText().some((value) => {
        return exactMatch
          ? value === expectedText
          : value.includes(expectedText);
      });
    },
    { timeout: 30000 },
    { expectedText: text, exactMatch: exact },
  );
}

async function expectNoVisibleText(page, text) {
  const found = await page.evaluate((expectedText) => {
    return window
      .__apiJourneyVisibleText()
      .some((value) => value.includes(expectedText));
  }, text);
  assert(!found, `Unexpected visible text: ${text}`);
}

async function clickVisibleText(page, text, { exact = false } = {}) {
  await expectVisibleText(page, text, { exact });
  const clicked = await page.evaluate(
    ({ expectedText, exactMatch }) => {
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
      const element = Array.from(document.querySelectorAll("body *"))
        .filter(isVisible)
        .find((candidate) => {
          const value = String(candidate.textContent || "").trim();
          return exactMatch
            ? value === expectedText
            : value.includes(expectedText);
        });
      if (!element) return false;
      element.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
      element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      return true;
    },
    { expectedText: text, exactMatch: exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

function isRelevantUrl(url) {
  return (
    url.includes("/tracer/feed/") ||
    url.includes("/integrations/connections/") ||
    url.includes("/integrations/sync-logs/")
  );
}

function isLinearMutationUrl(url) {
  return (
    url.includes("/tracer/feed/issues/") &&
    url.includes("/create-linear-issue/")
  );
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
