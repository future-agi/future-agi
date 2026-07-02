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
const SCREENSHOT_PATH = "/tmp/settings-mcp-server-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const config = await auth.client.get(apiPath("/mcp/config/"));
  const toolConfig = config.tool_config || {};
  const availableGroups = Array.isArray(toolConfig.available_groups)
    ? toolConfig.available_groups
    : [];
  const enabledGroups = Array.isArray(toolConfig.enabled_groups)
    ? toolConfig.enabled_groups
    : [];
  assert(config?.mcp_url, "MCP config omitted mcp_url.");
  assert(availableGroups.length > 0, "MCP config returned no available groups.");

  const groupBadge =
    enabledGroups.length === availableGroups.length
      ? "All enabled"
      : `${enabledGroups.length} of ${availableGroups.length}`;

  const apiFailures = [];
  const pageErrors = [];
  const mutationRequests = [];
  const evidence = {
    workspace_id: auth.workspaceId,
    connection_id: config.id,
    mcp_url: config.mcp_url,
    enabled_group_count: enabledGroups.length,
    available_group_count: availableGroups.length,
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
      url.includes("/mcp/config/tool-groups/") &&
      request.method() !== "GET"
    ) {
      mutationRequests.push(`${request.method()} ${url}`);
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (url.includes("/mcp/config/") && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const configResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/mcp/config/") && response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(`${APP_BASE}/dashboard/settings/mcp-server`, {
      waitUntil: "domcontentloaded",
    });
    await configResponse;

    await waitForVisibleText(page, "MCP Server", { exact: true });
    await waitForVisibleText(page, "Connect Your IDE", { exact: true });
    await waitForVisibleText(page, "Authentication happens automatically via OAuth");
    await waitForInputValue(page, config.mcp_url);
    await waitForVisibleText(page, "Cursor", { exact: true });
    await waitForVisibleText(page, "Claude Code", { exact: true });
    await waitForVisibleText(page, "VS Code", { exact: true });
    await waitForVisibleText(page, "Tool Groups", { exact: true });
    await waitForVisibleText(page, groupBadge, { exact: true });

    await clickVisibleText(page, "Tool Groups");
    const visibleGroups = availableGroups.slice(0, 5);
    for (const group of visibleGroups) {
      await waitForVisibleText(page, group.name, { exact: true });
    }
    await waitForNoVisibleText(page, "eval templates and groups");
    await waitForNoVisibleText(page, "Invalid Date");
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;
    evidence.visible_group_names = visibleGroups.map((group) => group.name);

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      mutationRequests.length === 0,
      `MCP settings page fired mutations on initial render: ${mutationRequests.join("; ")}`,
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
          Boolean(candidate.closest("button,a,[role='button']")),
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
        Boolean(candidate.closest("button,a,[role='button']")),
    );
    const target = element?.closest("button,a,[role='button']") || element;
    target?.click();
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
  { exact = false, timeout = 10000 } = {},
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

async function waitForInputValue(page, expectedValue, timeout = 30000) {
  await page.waitForFunction(
    (value) =>
      Array.from(document.querySelectorAll("input, textarea")).some(
        (element) => element.value === value,
      ),
    { timeout },
    expectedValue,
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
  process.exit(1);
});
