import { createRequire } from "node:module";
import process from "node:process";
import { assert, envFlag } from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3035";
const API_BASE = process.env.API_BASE || "http://127.0.0.1:8011";
const SCREENSHOT_PATH =
  process.env.SIGNUP_QUICK_START_SCREENSHOT ||
  "/tmp/signup-quick-start-smoke.png";
const REQUIRE_REAL_SIGNUP = envFlag("ONBOARDING_REAL_SIGNUP");
const ALLOW_REMOTE = envFlag("ONBOARDING_REAL_SIGNUP_ALLOW_REMOTE");

async function main() {
  assert(REQUIRE_REAL_SIGNUP, "Set ONBOARDING_REAL_SIGNUP=1 for this smoke.");
  assertLocalUrl(APP_BASE, "APP_BASE");
  assertLocalUrl(API_BASE, "API_BASE");

  const runId = `${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
  const user = {
    email:
      process.env.ONBOARDING_SIGNUP_EMAIL ||
      `onboarding-smoke+${runId}@futureagi.com`,
    fullName: "Onboarding Smoke",
    password: process.env.ONBOARDING_SIGNUP_PASSWORD || "SecurePass123!",
  };
  const evidence = {
    activationEventPosts: [],
    activationStateRequests: [],
    apiFailures: [],
    onboardingPosts: [],
    sampleProjectPosts: [],
    sampleProjectResponses: [],
    setupPosts: [],
    signupPosts: [],
    tokenPosts: [],
    traceDetailRequests: [],
  };
  const pageErrors = [];

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });

  const page = await browser.newPage();
  await page.setCacheEnabled(false);
  await page.setBypassServiceWorker(true);
  page.on("request", (request) => {
    const url = safeUrl(request.url());
    if (!url || url.origin !== new URL(API_BASE).origin) return;
    const path = slashPath(url.pathname);
    if (path === "/accounts/signup/" && request.method() === "POST") {
      evidence.signupPosts.push(
        redactSensitiveAuth(parseJsonPostData(request)),
      );
    }
    if (path === "/accounts/token/" && request.method() === "POST") {
      evidence.tokenPosts.push(redactSensitiveAuth(parseJsonPostData(request)));
    }
    if (path === "/accounts/onboarding/" && request.method() === "POST") {
      evidence.onboardingPosts.push(parseJsonPostData(request.postData()));
    }
    if (path === "/accounts/team/users/" && request.method() === "POST") {
      evidence.setupPosts.push(parseJsonPostData(request.postData()));
    }
    if (path === "/accounts/activation-state/") {
      evidence.activationStateRequests.push(
        Object.fromEntries(url.searchParams),
      );
    }
    if (
      path === "/accounts/activation-events/" &&
      request.method() === "POST"
    ) {
      evidence.activationEventPosts.push(parseJsonPostData(request.postData()));
    }
    if (path === "/accounts/sample-project/" && request.method() === "POST") {
      evidence.sampleProjectPosts.push(parseJsonPostData(request.postData()));
    }
    if (/^\/tracer\/trace\/[^/]+\/$/.test(path) && request.method() === "GET") {
      evidence.traceDetailRequests.push(path);
    }
  });
  page.on("response", async (response) => {
    const url = safeUrl(response.url());
    const path = url ? slashPath(url.pathname) : null;
    if (
      url &&
      url.origin === new URL(API_BASE).origin &&
      isTrackedApiPath(path) &&
      response.status() >= 400
    ) {
      evidence.apiFailures.push(`${response.status()} ${url.pathname}`);
    }
    if (
      url &&
      url.origin === new URL(API_BASE).origin &&
      path === "/accounts/sample-project/" &&
      response.status() < 400
    ) {
      try {
        evidence.sampleProjectResponses.push(await response.json());
      } catch {
        evidence.sampleProjectResponses.push({ parse_error: true });
      }
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await page.goto(`${APP_BASE}/auth/jwt/register`, {
      waitUntil: "domcontentloaded",
    });
    await expectVisibleText(page, "Create an account");

    await page.type('input[placeholder="Enter fullname"]', user.fullName);
    await page.type('input[placeholder="Enter Email address"]', user.email);
    await page.type('input[placeholder="Create password"]', user.password);
    await clickVisibleButtonText(page, "Create account and continue");

    await expectVisibleText(page, "What's your role");
    await clickVisibleButtonText(page, "Connect observability first");
    await expectVisibleText(page, "Invite your team later");
    await expectVisibleText(page, "Continue now and review the first signal");

    const organizationNameInput = 'input[placeholder="Add organization name"]';
    await page.click(organizationNameInput, { clickCount: 3 });
    await page.keyboard.press("Backspace");
    await page.type(organizationNameInput, "Smoke Org");
    await clickVisibleButtonText(page, "Continue to onboarding");

    await page.waitForFunction(
      () =>
        window.location.pathname === "/dashboard/home" &&
        new URLSearchParams(window.location.search).get("source") ===
          "setup_org",
      { timeout: 45000 },
    );
    await expectVisibleText(page, "Connect observability", { timeout: 45000 });
    await expectVisibleTestId(page, "observe-setup-panel", { timeout: 45000 });
    await expectVisibleText(page, "Connect one observe project", {
      exact: true,
      timeout: 45000,
    });
    const observeCtaHref = await expectVisibleActionHref(
      page,
      "Connect observability",
      "/dashboard/observe?setup=true&source=onboarding",
      { timeout: 45000 },
    );
    await clickVisibleActionHref(
      page,
      "Connect observability",
      "/dashboard/observe?setup=true&source=onboarding",
    );
    await page.waitForFunction(
      () =>
        window.location.pathname === "/dashboard/observe" &&
        new URLSearchParams(window.location.search).get("setup") === "true" &&
        new URLSearchParams(window.location.search).get("source") ===
          "onboarding",
      { timeout: 45000 },
    );
    await expectVisibleTestId(page, "observe-onboarding-focus", {
      timeout: 45000,
    });
    await expectVisibleText(page, "Observe onboarding", { timeout: 45000 });
    await expectVisibleText(page, "Setup", { timeout: 45000 });
    await expectVisibleText(page, "Connect Observe to your app", {
      timeout: 45000,
    });
    await expectVisibleText(
      page,
      "Install tracing, load your keys, and send one real or test request.",
      { timeout: 45000 },
    );
    await expectVisibleText(page, "Install", { timeout: 45000 });
    await expectVisibleText(page, "Trace", { timeout: 45000 });
    await expectVisibleText(page, "Review", { timeout: 45000 });
    await expectVisibleText(page, "Review setup", { timeout: 45000 });
    await expectVisibleText(page, "Install Dependencies", { timeout: 45000 });
    await expectVisibleText(page, "Load API keys", { timeout: 45000 });
    await expectVisibleText(page, "Setup Telemetry", { timeout: 45000 });
    await expectVisibleText(page, "Setup Instrumentation", { timeout: 45000 });
    await expectNoVisibleText(page, "Code not available");
    const observeSetupUrl = page.url();
    await expectVisibleText(page, "Open sample trace", { timeout: 45000 });
    await clickVisibleButtonText(page, "Open sample trace", 45000);
    await page.waitForFunction(
      () => {
        const segments = window.location.pathname.split("/").filter(Boolean);
        const params = new URLSearchParams(window.location.search);
        return (
          segments.length === 5 &&
          segments[0] === "dashboard" &&
          segments[1] === "observe" &&
          segments[3] === "trace" &&
          params.get("sample") === "true" &&
          params.get("from") === "onboarding"
        );
      },
      { timeout: 45000 },
    );
    await expectVisibleText(page, "Trace", { exact: true, timeout: 45000 });
    const sampleTraceUrl = page.url();
    await expectVisibleText(page, "Sample trace review", { timeout: 45000 });
    await expectVisibleText(page, "Connect your app", {
      exact: true,
      timeout: 45000,
    });
    await waitForCondition(
      () => evidence.sampleProjectPosts.length === 1,
      "Expected one sample-project POST.",
    );
    await waitForCondition(
      () => evidence.traceDetailRequests.length >= 1,
      "Expected trace detail request for sample trace.",
    );
    await clickVisibleButtonText(page, "Connect your app", 45000);
    await page.waitForFunction(
      () =>
        window.location.pathname === "/dashboard/observe" &&
        new URLSearchParams(window.location.search).get("setup") === "true" &&
        new URLSearchParams(window.location.search).get("source") ===
          "sample_trace_review",
      { timeout: 45000 },
    );

    const browserState = await page.evaluate(() => ({
      initialRender: localStorage.getItem("initial-render"),
      organizationId: sessionStorage.getItem("organizationId"),
      redirectUrl: localStorage.getItem("redirectUrl"),
      workspaceId: sessionStorage.getItem("workspaceId"),
    }));

    assert(evidence.signupPosts.length === 1, "Expected one signup POST.");
    assert(evidence.tokenPosts.length === 1, "Expected one token POST.");
    assert(
      evidence.onboardingPosts.length === 1,
      "Expected one onboarding POST.",
    );
    assert(
      evidence.onboardingPosts[0]?.role === "AI Builder",
      `Expected quick-start role, got ${evidence.onboardingPosts[0]?.role}`,
    );
    assert(
      evidence.onboardingPosts[0]?.goals?.includes("Monitor LLMs and Agents"),
      `Expected observe quick-start goal, got ${JSON.stringify(
        evidence.onboardingPosts[0]?.goals,
      )}`,
    );
    assert(evidence.setupPosts.length === 1, "Expected one setup POST.");
    assert(
      evidence.sampleProjectPosts[0]?.source === "observe_setup_onboarding",
      `Expected sample source observe_setup_onboarding, got ${evidence.sampleProjectPosts[0]?.source}`,
    );
    assert(
      evidence.sampleProjectPosts[0]?.reason === "setup_observe",
      `Expected sample reason setup_observe, got ${evidence.sampleProjectPosts[0]?.reason}`,
    );
    assert(
      evidence.activationEventPosts.some(
        (payload) =>
          payload?.event_name === "sample_trace_detail_opened" &&
          payload?.primary_path === "sample" &&
          payload?.stage === "review_first_trace" &&
          payload?.is_sample === true,
      ),
      "Expected sample trace detail activation event.",
    );
    assert(
      evidence.activationEventPosts.some(
        (payload) =>
          payload?.event_name === "sample_to_real_setup_clicked" &&
          payload?.primary_path === "sample" &&
          payload?.stage === "connect_real_data" &&
          payload?.is_sample === true,
      ),
      "Expected sample to real setup activation event.",
    );
    assert(
      evidence.activationStateRequests.some(
        (request) => request.source === "setup_org",
      ),
      "Expected activation-state request with source=setup_org.",
    );
    assert(
      browserState.initialRender === "done",
      `Expected initial-render=done, got ${browserState.initialRender}`,
    );
    assert(
      browserState.redirectUrl === null,
      `Expected redirectUrl to be cleared, got ${browserState.redirectUrl}`,
    );
    assert(
      browserState.organizationId,
      "Expected organizationId in browser session storage.",
    );
    assert(
      browserState.workspaceId,
      "Expected workspaceId in browser session storage.",
    );
    assert(
      evidence.apiFailures.length === 0,
      `API failures: ${evidence.apiFailures.join("; ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: API_BASE,
          evidence: {
            activation_state_requests: evidence.activationStateRequests,
            browser_state: browserState,
            email: user.email,
            onboarding_post: evidence.onboardingPosts[0],
            observe_cta_href: observeCtaHref,
            observe_setup_url: observeSetupUrl,
            sample_project_post: evidence.sampleProjectPosts[0],
            sample_trace_activation_event: evidence.activationEventPosts.find(
              (payload) => payload?.event_name === "sample_trace_detail_opened",
            ),
            sample_to_real_setup_event: evidence.activationEventPosts.find(
              (payload) =>
                payload?.event_name === "sample_to_real_setup_clicked",
            ),
            sample_trace_url: sampleTraceUrl,
            real_setup_return_url: page.url(),
            screenshot: SCREENSHOT_PATH,
            setup_post: evidence.setupPosts[0],
            signup_post: evidence.signupPosts[0],
            token_post: evidence.tokenPosts[0],
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
          api_base: API_BASE,
          diagnostic: {
            ...evidence,
            body_text: await safeBodyText(page),
            page_errors: pageErrors,
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

function assertLocalUrl(value, name) {
  if (ALLOW_REMOTE) return;
  const url = new URL(value);
  const localHosts = new Set(["127.0.0.1", "localhost", "[::1]"]);
  assert(
    localHosts.has(url.hostname),
    `${name} must be localhost unless ONBOARDING_REAL_SIGNUP_ALLOW_REMOTE=1.`,
  );
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

async function expectNoVisibleText(page, text, { timeout = 30000 } = {}) {
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
      return !Array.from(document.querySelectorAll("body *")).some(
        (element) =>
          isVisible(element) &&
          normalized(element.textContent).includes(expectedText),
      );
    },
    { timeout },
    text,
  );
}

async function expectVisibleTestId(page, testId, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (expectedTestId) => {
      const element = document.querySelector(
        `[data-testid="${expectedTestId}"]`,
      );
      if (!element) return false;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    },
    { timeout },
    testId,
  );
}

async function expectVisibleActionHref(
  page,
  text,
  expectedPath,
  { timeout = 30000 } = {},
) {
  const handle = await page.waitForFunction(
    ({ expectedText, expectedHrefPath }) => {
      const normalized = (value) => String(value || "").trim();
      const isVisible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0 &&
          !element.disabled
        );
      };
      const action = Array.from(document.querySelectorAll("a, button")).find(
        (element) => {
          if (!isVisible(element)) return false;
          if (normalized(element.textContent) !== expectedText) return false;
          const href = element.getAttribute("href") || element.href;
          if (!href) return false;
          const url = new URL(href, window.location.origin);
          return `${url.pathname}${url.search}` === expectedHrefPath;
        },
      );
      return action?.getAttribute("href") || action?.href || false;
    },
    { timeout },
    { expectedText: text, expectedHrefPath: expectedPath },
  );
  const href = await handle.jsonValue();
  await handle.dispose();
  return href;
}

async function clickVisibleActionHref(
  page,
  text,
  expectedPath,
  timeout = 30000,
) {
  await page.waitForFunction(
    ({ expectedText, expectedHrefPath }) => {
      const normalized = (value) => String(value || "").trim();
      const isVisible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0 &&
          !element.disabled
        );
      };
      return Array.from(document.querySelectorAll("a, button")).some(
        (element) => {
          if (!isVisible(element)) return false;
          if (normalized(element.textContent) !== expectedText) return false;
          const href = element.getAttribute("href") || element.href;
          if (!href) return false;
          const url = new URL(href, window.location.origin);
          return `${url.pathname}${url.search}` === expectedHrefPath;
        },
      );
    },
    { timeout },
    { expectedText: text, expectedHrefPath: expectedPath },
  );
  await page.evaluate(
    ({ expectedText, expectedHrefPath }) => {
      const normalized = (value) => String(value || "").trim();
      const action = Array.from(document.querySelectorAll("a, button")).find(
        (element) => {
          const style = window.getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          if (
            style.visibility === "hidden" ||
            style.display === "none" ||
            rect.width <= 0 ||
            rect.height <= 0 ||
            element.disabled ||
            normalized(element.textContent) !== expectedText
          ) {
            return false;
          }
          const href = element.getAttribute("href") || element.href;
          if (!href) return false;
          const url = new URL(href, window.location.origin);
          return `${url.pathname}${url.search}` === expectedHrefPath;
        },
      );
      action.click();
    },
    { expectedText: text, expectedHrefPath: expectedPath },
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
          rect.height > 0 &&
          !element.disabled
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

function parseJsonPostData(requestOrData) {
  const value =
    typeof requestOrData === "string"
      ? requestOrData
      : requestOrData.postData();
  if (!value) return {};
  try {
    return JSON.parse(value);
  } catch {
    return {};
  }
}

function redactSensitiveAuth(value) {
  if (!value || typeof value !== "object") return value;
  return {
    ...value,
    password: value.password ? "[redacted]" : value.password,
    recaptcha_response: value.recaptcha_response
      ? "[redacted]"
      : value.recaptcha_response,
  };
}

function safeUrl(value) {
  try {
    return new URL(value);
  } catch {
    return null;
  }
}

function slashPath(path) {
  if (!path || path.endsWith("/")) return path || "/";
  return `${path}/`;
}

function isTrackedApiPath(path) {
  return (
    path === "/accounts/signup/" ||
    path === "/accounts/token/" ||
    path === "/accounts/onboarding/" ||
    path === "/accounts/team/users/" ||
    path === "/accounts/sample-project/" ||
    path === "/accounts/activation-events/" ||
    path === "/accounts/activation-state/" ||
    path === "/accounts/user-info/" ||
    /^\/tracer\/trace\/[^/]+\/$/.test(path)
  );
}

async function waitForCondition(condition, message, timeout = 30000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeout) {
    if (condition()) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(message);
}

async function safeBodyText(page) {
  try {
    return String(
      await page.evaluate(() => document.body?.innerText || ""),
    ).slice(0, 1600);
  } catch (error) {
    return error.message;
  }
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH)
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  if (process.platform === "linux") return "/usr/bin/google-chrome";
  throw new Error(
    "Set PUPPETEER_EXECUTABLE_PATH or CHROME_PATH to a Chrome executable.",
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
