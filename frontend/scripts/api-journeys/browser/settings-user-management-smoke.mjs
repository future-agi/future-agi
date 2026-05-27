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
const SCREENSHOT_PATH = "/tmp/settings-user-management-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const email = currentUserEmail(auth.user);
  const userId = currentUserId(auth.user);
  assert(email, "Authenticated user did not include an email.");
  assert(userId, "Authenticated user did not include an id.");

  const memberPayload = await auth.client.get(
    apiPath("/accounts/organization/members/"),
    { query: { search: email, page: 1, limit: 10 } },
  );
  const memberRows = asArray(memberPayload);
  const memberRow = memberRows.find(
    (row) => row?.id === userId || row?.email?.toLowerCase() === email.toLowerCase(),
  );
  assert(memberRow, "RBAC member list did not return the current user by email.");

  const apiFailures = [];
  const pageErrors = [];
  const evidence = {
    email,
    user_id: userId,
    member_status: memberRow.status,
    member_org_level: memberRow.org_level,
    member_type: memberRow.type,
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
      (url.includes("/accounts/organization/members/") ||
        url.includes("/accounts/workspace/list/") ||
        url.includes("/accounts/user-info/")) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const initialMembersResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/accounts/organization/members/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(`${APP_BASE}/dashboard/settings/user-management`, {
      waitUntil: "domcontentloaded",
    });
    await initialMembersResponse;

    await waitForVisibleText(page, "Members", { exact: true });
    await page.waitForSelector('input[placeholder="Search by name or email"]', {
      visible: true,
      timeout: 30000,
    });
    await waitForVisibleText(page, "Invite User", { exact: true });

    await waitForVisibleText(page, email);
    await waitForVisibleText(page, "Organisation Role", { exact: true });
    await waitForVisibleText(page, "Workspaces", { exact: true });
    await waitForVisibleText(page, "Status", { exact: true });

    const searchedMembersResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/accounts/organization/members/") &&
        response.url().includes(`search=${encodeURIComponent(email)}`) &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.click('input[placeholder="Search by name or email"]', {
      clickCount: 3,
    });
    await page.type('input[placeholder="Search by name or email"]', email);
    await searchedMembersResponse;
    await waitForVisibleText(page, email);

    await clickVisibleText(page, "Invite User", { exact: true });
    await waitForVisibleText(page, "Invite new users", { exact: true });
    await waitForVisibleText(page, "Emails", { exact: true });

    await waitForNoVisibleText(page, "Invalid Date");
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;
    await page.keyboard.press("Escape");

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
      const element = Array.from(document.querySelectorAll("button, [role='button']")).find(
        (candidate) => {
          if (!isVisible(candidate)) return false;
          const textContent = normalized(candidate.textContent);
          if (exactMatch) return textContent === expectedText;
          return textContent.includes(expectedText);
        },
      );
      if (!element) throw new Error(`No visible clickable text: ${expectedText}`);
      element.click();
    },
    { text, exact },
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
