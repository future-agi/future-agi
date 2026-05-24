import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  currentUserEmail,
  currentUserId,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/settings-workspace-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const email = currentUserEmail(auth.user);
  const userId = currentUserId(auth.user);
  assert(email, "Authenticated user did not include an email.");
  assert(userId, "Authenticated user did not include an id.");

  const workspaceRows = asArray(
    await auth.client.get(apiPath("/accounts/workspace/list/")),
  );
  const workspace = workspaceRows.find((row) => row?.id === auth.workspaceId);
  assert(workspace, "Workspace list did not include active workspace.");
  const workspaceName = workspace.display_name || workspace.name;

  const memberRows = asArray(
    await auth.client.get(
      apiPath("/accounts/workspace/{workspace_id}/members/", {
        workspace_id: auth.workspaceId,
      }),
      { query: { search: email, page: 1, limit: 10, filter_status: ["Active"] } },
    ),
  );
  const currentMember = memberRows.find(
    (row) =>
      row?.id === userId ||
      String(row?.email || "").toLowerCase() === email.toLowerCase(),
  );
  assert(currentMember, "Workspace members API did not return current user.");

  const apiFailures = [];
  const pageErrors = [];
  const evidence = {
    email,
    user_id: userId,
    workspace_id: auth.workspaceId,
    workspace_name: workspaceName,
    current_user_ws_level: currentMember.ws_level,
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
      (url.includes("/accounts/workspace/list/") ||
        url.includes(`/accounts/workspace/${auth.workspaceId}/members/`) ||
        url.includes(`/accounts/workspaces/${auth.workspaceId}/`)) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const workspaceListResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/accounts/workspace/list/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(
      `${APP_BASE}/dashboard/settings/workspace/${auth.workspaceId}/general`,
      { waitUntil: "domcontentloaded" },
    );
    await workspaceListResponse;
    await waitForVisibleText(page, "General", { exact: true });
    await waitForVisibleText(page, "Manage workspace settings", { exact: true });
    await waitForVisibleText(page, "Workspace Name", { exact: true });
    await page.waitForSelector("input", { visible: true, timeout: 30000 });
    const inputValue = await page.$eval("input", (element) => element.value);
    assert(
      inputValue === workspaceName,
      `Workspace name input showed ${inputValue}, expected ${workspaceName}.`,
    );
    const saveDisabled = await page.$$eval("button", (buttons) => {
      const saveButton = buttons.find((button) =>
        button.textContent?.includes("Save Changes"),
      );
      return saveButton ? saveButton.disabled : null;
    });
    assert(saveDisabled === true, "Save Changes button was not disabled before edits.");

    const membersResponse = page.waitForResponse(
      (response) =>
        response.url().includes(`/accounts/workspace/${auth.workspaceId}/members/`) &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(
      `${APP_BASE}/dashboard/settings/workspace/${auth.workspaceId}/members`,
      { waitUntil: "domcontentloaded" },
    );
    await membersResponse;
    await waitForVisibleText(page, "Members", { exact: true });
    await waitForVisibleText(page, "Manage workspace members and their roles", {
      exact: true,
    });
    await waitForVisibleText(page, "Workspace Role", { exact: true });
    await waitForVisibleText(page, "Status", { exact: true });
    await waitForVisibleText(page, email);

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
