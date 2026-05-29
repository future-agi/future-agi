/* eslint-disable no-console */
import { Buffer } from "node:buffer";
import { createRequire } from "node:module";
import process from "node:process";
import { assert, envFlag } from "../lib/api-client.mjs";
import { getActivationStateFixture } from "../../../src/sections/onboarding-home/fixtures/activation-state.fixtures.js";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const VIEWPORT_NAME = process.env.ONBOARDING_SMOKE_VIEWPORT || "desktop";
const SCREENSHOT_PATH =
  process.env.SETUP_ORG_COMPLETION_SCREENSHOT ||
  `/tmp/setup-org-completion-smoke-${VIEWPORT_NAME}.png`;
const STUB_AUTH = envFlag("ONBOARDING_SMOKE_STUB_AUTH");

async function main() {
  assert(STUB_AUTH, "Set ONBOARDING_SMOKE_STUB_AUTH=1 for this smoke.");

  const auth = createStubbedAuthenticatedContext();
  const apiFailures = [];
  const pageErrors = [];
  const onboardingPosts = [];
  const setupPosts = [];
  const activationStateRequests = [];
  let setupCompleted = false;

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: viewportForName(VIEWPORT_NAME),
    args: ["--no-sandbox"],
  });

  const page = await browser.newPage();
  await page.setCacheEnabled(false);
  await page.setBypassServiceWorker(true);
  await installRuntime(page, auth, {
    activationStateRequests,
    getSetupCompleted: () => setupCompleted,
    onboardingPosts,
    onSetupComplete: () => {
      setupCompleted = true;
    },
    setupPosts,
  });
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      sessionStorage.setItem("organizationId", organizationId);
      sessionStorage.setItem("organizationName", "Onboarding smoke org");
      sessionStorage.setItem("organizationDisplayName", "Onboarding smoke org");
      sessionStorage.setItem("organizationRole", "Owner");
      sessionStorage.setItem("workspaceId", workspaceId);
      sessionStorage.setItem("workspaceName", "Onboarding smoke workspace");
      sessionStorage.setItem(
        "workspaceDisplayName",
        "Onboarding smoke workspace",
      );
      sessionStorage.setItem("workspaceRole", "Owner");
      sessionStorage.setItem("currentUserId", user.id);
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
    if (isSetupSmokeApiUrl(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await page.goto(`${APP_BASE}/auth/jwt/setup-org?step=0`, {
      waitUntil: "domcontentloaded",
    });
    await expectVisibleText(page, "Start with your first quality loop");
    await page.evaluate(() => {
      localStorage.setItem("redirectUrl", "/dashboard/observe?project=stale");
    });
    await clickVisibleButtonText(page, "Connect observability first");
    await page.waitForFunction(
      () =>
        window.location.pathname === "/dashboard/home" &&
        new URLSearchParams(window.location.search).get("source") ===
          "setup_org",
      { timeout: 30000 },
    );

    await expectSelector(page, '[data-testid="observe-setup-panel"]');
    await expectVisibleText(page, "Connect one observe project", {
      exact: true,
    });

    const browserState = await page.evaluate(() => ({
      initialRender: localStorage.getItem("initial-render"),
      redirectUrl: localStorage.getItem("redirectUrl"),
    }));
    assert(
      browserState.initialRender === "done",
      `Expected initial-render=done, got ${browserState.initialRender}`,
    );
    assert(
      browserState.redirectUrl === null,
      `Expected redirectUrl to be cleared, got ${browserState.redirectUrl}`,
    );
    assert(onboardingPosts.length === 1, "Expected one onboarding POST.");
    assert(
      onboardingPosts[0]?.role === "AI Builder",
      `Expected quick-start role, got ${onboardingPosts[0]?.role}`,
    );
    assert(
      onboardingPosts[0]?.goals?.includes("Monitor LLMs and Agents"),
      `Expected observe quick-start goal, got ${JSON.stringify(
        onboardingPosts[0]?.goals,
      )}`,
    );
    assert(
      setupPosts.length === 0,
      "Expected no setup organization POST on observe quick start.",
    );
    assert(
      activationStateRequests.length === 1,
      `Expected one activation-state request, got ${activationStateRequests.length}`,
    );
    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          evidence: {
            activation_state_requests: activationStateRequests,
            browser_state: browserState,
            onboarding_post: onboardingPosts[0],
            screenshot: SCREENSHOT_PATH,
            setup_posts: setupPosts,
            viewport: VIEWPORT_NAME,
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    console.error(
      JSON.stringify(
        {
          status: "failed",
          app_base: APP_BASE,
          diagnostic: {
            activation_state_requests: activationStateRequests,
            api_failures: apiFailures,
            body_text: await safeBodyText(page),
            page_errors: pageErrors,
            onboarding_posts: onboardingPosts,
            setup_posts: setupPosts,
            url: page.url(),
          },
        },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    await browser.close();
  }
}

async function installRuntime(
  page,
  auth,
  {
    activationStateRequests,
    getSetupCompleted,
    onboardingPosts,
    onSetupComplete,
    setupPosts,
  },
) {
  await page.setRequestInterception(true);
  page.on("request", async (request) => {
    const url = new URL(request.url());
    const normalizedPath = slashPath(url.pathname);

    if (normalizedPath === "/config.js/") {
      await respondJavascript(
        request,
        `window.__FUTURE_AGI_CONFIG__ = ${JSON.stringify({
          VITE_HOST_API: auth.apiBase,
          VITE_ASSETS_API: APP_BASE,
        })};`,
      );
      return;
    }

    if (request.method() === "OPTIONS" && isStubbedApiPath(normalizedPath)) {
      await request.respond({ status: 204, headers: corsHeaders() });
      return;
    }

    if (normalizedPath === "/accounts/user-info/") {
      await respondJson(request, {
        ...auth.user,
        onboarding_completed: getSetupCompleted(),
      });
      return;
    }

    if (normalizedPath === "/accounts/organization/list/") {
      await respondJson(request, {
        status: true,
        result: {
          organizations: [
            {
              id: auth.organizationId,
              name: "Onboarding smoke org",
              display_name: "Onboarding smoke org",
            },
          ],
        },
      });
      return;
    }

    if (normalizedPath === "/accounts/workspace/list/") {
      await respondJson(request, {
        status: true,
        result: [
          {
            id: auth.workspaceId,
            name: "Onboarding smoke workspace",
            display_name: "Onboarding smoke workspace",
            organization_id: auth.organizationId,
          },
        ],
      });
      return;
    }

    if (normalizedPath === "/accounts/onboarding/") {
      if (request.method() === "POST") {
        const payload = parseJsonPostData(request.postData());
        onboardingPosts.push(payload);
        if (
          payload?.role &&
          Array.isArray(payload?.goals) &&
          payload.goals.length
        ) {
          onSetupComplete();
        }
        await respondJson(request, {
          status: true,
          result: {
            data: payload,
            message: "Onboarding data saved successfully",
          },
        });
        return;
      }
      await respondJson(request, {
        status: true,
        result: {
          completed: getSetupCompleted(),
          goals: [],
          role: "",
        },
      });
      return;
    }

    if (normalizedPath === "/accounts/team/users/") {
      if (request.method() === "POST") {
        const payload = parseJsonPostData(request.postData());
        setupPosts.push(payload);
        onSetupComplete();
        await respondJson(request, {
          status: true,
          result: {
            created_members: [],
          },
        });
        return;
      }
      await respondJson(request, {
        status: true,
        result: {
          results: [],
        },
      });
      return;
    }

    if (normalizedPath === "/accounts/activation-state/") {
      activationStateRequests.push(Object.fromEntries(url.searchParams));
      await respondJson(request, {
        status: true,
        result: stubbedActivationState(auth),
      });
      return;
    }

    if (normalizedPath === "/accounts/activation-events/") {
      await respondJson(request, {
        status: true,
        result: {
          event_id: "00000000-0000-4000-8000-000000000199",
        },
      });
      return;
    }

    await request.continue();
  });
}

function stubbedActivationState(auth) {
  return {
    ...getActivationStateFixture("newWorkspaceNoGoal"),
    organization_id: auth.organizationId,
    request_id: "setup_org_completion_smoke",
    user_id: auth.user.id,
    workspace_id: auth.workspaceId,
  };
}

function createStubbedAuthenticatedContext() {
  const userId = "00000000-0000-4000-8000-000000000111";
  const organizationId = "00000000-0000-4000-8000-000000000211";
  const workspaceId = "00000000-0000-4000-8000-000000000311";
  const user = {
    id: userId,
    email: "setup-org-smoke@example.com",
    name: "Setup Org Smoke",
    remember_me: true,
    onboarding_completed: false,
    requires_org_setup: false,
    organization_id: organizationId,
    organization_role: "Owner",
    org_level: 4,
    default_workspace_id: workspaceId,
    default_workspace_name: "Onboarding smoke workspace",
    default_workspace_display_name: "Onboarding smoke workspace",
    default_workspace_role: "Owner",
    ws_level: 4,
    goals: [],
  };
  return {
    apiBase: APP_BASE,
    organizationId,
    workspaceId,
    user,
    tokens: {
      access: fakeJwt(userId),
      refresh: "",
    },
  };
}

function fakeJwt(userId) {
  const header = base64Url({ alg: "none", typ: "JWT" });
  const payload = base64Url({
    exp: Math.floor(Date.now() / 1000) + 3600,
    sub: userId,
    user_id: userId,
  });
  return `${header}.${payload}.signature`;
}

function base64Url(value) {
  return Buffer.from(JSON.stringify(value))
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function parseJsonPostData(value) {
  if (!value) return {};
  try {
    return JSON.parse(value);
  } catch {
    return {};
  }
}

async function respondJson(request, body, status = 200) {
  await request.respond({
    status,
    contentType: "application/json",
    headers: corsHeaders(),
    body: JSON.stringify(body),
  });
}

async function respondJavascript(request, body) {
  await request.respond({
    status: 200,
    contentType: "application/javascript",
    headers: corsHeaders(),
    body,
  });
}

function corsHeaders() {
  return {
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers":
      "authorization,content-type,x-organization-id,x-workspace-id",
    "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
    "Access-Control-Allow-Origin": "*",
  };
}

function slashPath(path) {
  if (!path || path.endsWith("/")) return path || "/";
  return `${path}/`;
}

async function expectSelector(page, selector, timeout = 30000) {
  await page.waitForSelector(selector, { timeout, visible: true });
}

async function expectVisibleText(
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

async function clickVisibleButtonText(page, text, timeout = 30000) {
  await page.waitForFunction(
    (expectedText) => {
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
      return Array.from(document.querySelectorAll("button")).some(
        (element) =>
          isVisible(element) &&
          normalized(element.textContent) === expectedText,
      );
    },
    { timeout },
    text,
  );
  await page.evaluate((expectedText) => {
    const normalized = (value) => String(value || "").trim();
    const button = Array.from(document.querySelectorAll("button")).find(
      (element) => normalized(element.textContent) === expectedText,
    );
    button.click();
  }, text);
}

async function safeBodyText(page) {
  try {
    return String(
      await page.evaluate(() => document.body?.innerText || ""),
    ).slice(0, 1200);
  } catch (error) {
    return error.message;
  }
}

function isStubbedApiPath(path) {
  return path.startsWith("/accounts/");
}

function isSetupSmokeApiUrl(url) {
  return (
    url.includes("/accounts/user-info/") ||
    url.includes("/accounts/onboarding/") ||
    url.includes("/accounts/team/users/") ||
    url.includes("/accounts/activation-state/")
  );
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH)
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  if (process.platform === "linux") return "/usr/bin/google-chrome";
  return undefined;
}

function viewportForName(name) {
  if (name === "mobile") {
    return {
      width: 390,
      height: 844,
      isMobile: true,
      hasTouch: true,
      deviceScaleFactor: 2,
    };
  }
  return { width: 1440, height: 950 };
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
