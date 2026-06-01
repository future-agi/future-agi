/* eslint-disable no-console */
import { Buffer } from "node:buffer";
import { createRequire } from "node:module";
import process from "node:process";
import { assert } from "../lib/api-client.mjs";
import { getActivationStateFixture } from "../../../src/sections/onboarding-home/fixtures/activation-state.fixtures.js";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const EVAL_DRAFT_ID = "00000000-0000-4000-8000-000000000621";
const USER_ID = "00000000-0000-4000-8000-000000000103";
const ORGANIZATION_ID = "00000000-0000-4000-8000-000000000203";
const WORKSPACE_ID = "00000000-0000-4000-8000-000000000303";
const HOME_SCREENSHOT_PATH =
  process.env.EVAL_HOME_CONTROLLED_SCREENSHOT ||
  "/tmp/eval-home-start-controlled-smoke.png";
const SCREENSHOT_PATH =
  process.env.EVAL_HOME_TO_CREATE_CONTROLLED_SCREENSHOT ||
  "/tmp/eval-home-to-create-controlled-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/eval-home-to-create-controlled-smoke-failure.png";
const SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY =
  "futureagi.setup_quick_start_attribution";
const EVAL_QUICK_START_ATTRIBUTION = {
  quickStartGoal: "evaluate_quality",
  quickStartId: "evals",
  quickStartPrimaryPath: "evals",
};
const EVAL_QUICK_START_PARAMS = {
  quick_start_goal: "evaluate_quality",
  quick_start_id: "evals",
  quick_start_primary_path: "evals",
};
const EVAL_QUICK_START_QUERY = new URLSearchParams(
  EVAL_QUICK_START_PARAMS,
).toString();

async function main() {
  const auth = createStubbedAuthenticatedContext();
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  await page.setCacheEnabled(false);
  await page.setBypassServiceWorker(true);

  const apiFailures = [];
  const pageErrors = [];
  const requestFailures = [];
  const activationEventPosts = [];
  const evalRequests = [];
  const stubbedApiRequests = [];
  const evidence = {};

  await installControlledWebSocket(page);
  await installBrowserState(page, auth);
  await installRuntime(page, auth, {
    activationEventPosts,
    evalRequests,
    stubbedApiRequests,
  });

  page.on("response", (response) => {
    const url = response.url();
    if (isStubbedApiUrl(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("requestfailed", (request) => {
    if (request.url().includes("us-assets.i.posthog.com")) return;
    requestFailures.push(
      `${request.method()} ${request.url()} ${request.failure()?.errorText}`,
    );
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await page.goto(
      `${APP_BASE}/dashboard/home?source=setup_org&${EVAL_QUICK_START_QUERY}`,
      { waitUntil: "domcontentloaded" },
    );
    await waitForVisibleText(page, "Test AI using simulation", {
      exact: true,
    });
    await waitForVisibleText(
      page,
      "Start with: Create dataset. Then: Add scorer.",
    );
    await waitForVisibleText(page, "First action", { exact: true });
    await waitForVisibleText(page, "Step 1 of 5", { exact: true });
    await waitForVisibleText(page, "Create dataset", { exact: true });
    await waitForVisibleText(page, "Next steps", { exact: true });
    evidence.home_route = await currentRelativeUrl(page);
    assertEvalQuickStartParams(evidence.home_route, "Home route");
    evidence.home_cta_href = await visibleLinkHref(page, "Create dataset", {
      exact: true,
    });
    assertEvalQuickStartParams(evidence.home_cta_href, "Home CTA");
    assertHomeEvalCta(evidence.home_cta_href);
    await page.screenshot({ path: HOME_SCREENSHOT_PATH, fullPage: true });
    evidence.home_screenshot = HOME_SCREENSHOT_PATH;

    await clickVisibleButtonText(page, "Create dataset", { exact: true });
    await waitForPath(page, `/dashboard/evaluations/create/${EVAL_DRAFT_ID}`);
    await waitForSearchParam(page, "source", "onboarding");
    await waitForSearchParam(page, "journey_step", "create_eval_dataset");
    await waitForSearchParam(page, "tour_anchor", "eval_dataset_button");
    evidence.eval_create_route = await currentRelativeUrl(page);
    assertEvalQuickStartParams(evidence.eval_create_route, "Eval create route");
    await expectSelector(page, '[data-testid="eval-onboarding-focus"]');
    await waitForVisibleText(page, "Eval onboarding", { exact: true });
    await waitForVisibleText(page, "Create the eval source", { exact: true });
    await waitForVisibleText(
      page,
      "Choose the data or trace source before adding the scorer.",
      { exact: true },
    );
    await waitForVisibleText(page, "Create evaluation", { exact: true });
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    const focusEvents = activationEventPosts.filter(
      (payload) => payload?.event_name === "onboarding_eval_route_focus_viewed",
    );
    assert(
      focusEvents.length >= 1,
      "Expected eval route focus activation event to be recorded.",
    );
    assertEvalQuickStartMetadata(focusEvents[0], "Eval route focus event");
    assert(
      evalRequests.includes("POST /model-hub/eval-templates/create-v2/"),
      "Expected eval draft create request.",
    );
    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(
      pageErrors.length === 0,
      `Browser page errors: ${pageErrors.join("; ")}`,
    );
    assert(
      requestFailures.length === 0,
      `Request failures: ${requestFailures.join("; ")}`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          mode: "controlled",
          organization_id: ORGANIZATION_ID,
          workspace_id: WORKSPACE_ID,
          evidence: {
            ...evidence,
            activation_event_names: activationEventPosts.map(
              (payload) => payload?.event_name,
            ),
            eval_activation_events: activationEventPosts.map((payload) => ({
              event_name: payload?.event_name,
              quick_start_goal: payload?.metadata?.quick_start_goal,
              quick_start_id: payload?.metadata?.quick_start_id,
              quick_start_primary_path:
                payload?.metadata?.quick_start_primary_path,
            })),
            eval_request_count: evalRequests.length,
            stubbed_api_request_count: stubbedApiRequests.length,
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await page.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true });
    error.message = `${error.message}\nFailure screenshot: ${FAILURE_SCREENSHOT_PATH}`;
    throw error;
  } finally {
    await browser.close();
  }
}

async function installRuntime(
  page,
  auth,
  { activationEventPosts, evalRequests, stubbedApiRequests },
) {
  await page.setRequestInterception(true);
  page.on("request", async (request) => {
    const url = new URL(request.url());
    const normalizedPath = slashPath(url.pathname);

    if (isStubbedApiPath(normalizedPath)) {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
      if (normalizedPath.startsWith("/model-hub/eval-templates/")) {
        evalRequests.push(`${request.method()} ${normalizedPath}`);
      }
    }

    if (normalizedPath === "/config.js/") {
      await respondJavascript(
        request,
        `window.__FUTURE_AGI_CONFIG__ = ${JSON.stringify({
          VITE_HOST_API: APP_BASE,
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
      await respondJson(request, auth.user);
      return;
    }

    if (normalizedPath === "/accounts/organization/list/") {
      await respondJson(request, {
        status: true,
        result: {
          organizations: [
            {
              id: ORGANIZATION_ID,
              name: "Eval onboarding org",
              display_name: "Eval onboarding org",
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
            id: WORKSPACE_ID,
            name: "Eval onboarding workspace",
            display_name: "Eval onboarding workspace",
            organization_id: ORGANIZATION_ID,
          },
        ],
      });
      return;
    }

    if (normalizedPath === "/accounts/activation-state/") {
      await respondJson(request, {
        status: true,
        result: evalHomeActivationState(auth),
      });
      return;
    }

    if (normalizedPath === "/accounts/activation-events/") {
      const payload = parseJsonPostData(request.postData());
      activationEventPosts.push(payload);
      await respondJson(request, {
        status: true,
        result: {
          event_id: "00000000-0000-4000-8000-000000000190",
          event_name: payload?.event_name || "onboarding_event",
          activation_state: evalHomeActivationState(auth),
        },
      });
      return;
    }

    if (
      normalizedPath === "/model-hub/eval-templates/create-v2/" &&
      request.method() === "POST"
    ) {
      await respondJson(request, {
        status: true,
        result: { id: EVAL_DRAFT_ID },
      });
      return;
    }

    if (
      normalizedPath === `/model-hub/eval-templates/${EVAL_DRAFT_ID}/detail/` &&
      request.method() === "GET"
    ) {
      await respondJson(request, {
        status: true,
        result: evalTemplateDetail(),
      });
      return;
    }

    if (normalizedPath.startsWith("/accounts/")) {
      await respondJson(request, { status: true, result: {} });
      return;
    }

    if (normalizedPath.startsWith("/model-hub/")) {
      await respondJson(request, evalFallbackResponse(normalizedPath));
      return;
    }

    request.continue();
  });
}

async function installControlledWebSocket(page) {
  await page.evaluateOnNewDocument(() => {
    class ControlledWebSocket {
      constructor(url) {
        this.url = String(url || "");
        this.readyState = ControlledWebSocket.CONNECTING;
        this.onopen = null;
        this.onmessage = null;
        this.onerror = null;
        this.onclose = null;
        setTimeout(() => {
          this.readyState = ControlledWebSocket.OPEN;
          this.onopen?.({ target: this });
        }, 0);
      }

      send() {}

      close() {
        if (this.readyState === ControlledWebSocket.CLOSED) return;
        this.readyState = ControlledWebSocket.CLOSED;
        this.onclose?.({ code: 1000, reason: "controlled", target: this });
      }

      addEventListener(type, listener) {
        this[`on${type}`] = listener;
      }

      removeEventListener(type, listener) {
        if (this[`on${type}`] === listener) this[`on${type}`] = null;
      }
    }
    ControlledWebSocket.CONNECTING = 0;
    ControlledWebSocket.OPEN = 1;
    ControlledWebSocket.CLOSING = 2;
    ControlledWebSocket.CLOSED = 3;
    window.WebSocket = ControlledWebSocket;
  });
}

async function installBrowserState(page, auth) {
  await page.evaluateOnNewDocument(() => {
    window.normalizeText = (value) =>
      String(value || "")
        .replace(/\s+/g, " ")
        .trim();
    window.visibleElements = (selector = "body *") => {
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
      return Array.from(document.querySelectorAll(selector)).filter(isVisible);
    };
  });
  await page.evaluateOnNewDocument(
    ({
      quickStartAttribution,
      setupQuickStartStorageKey,
      tokens,
      organizationId,
      workspaceId,
      user,
    }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      sessionStorage.setItem("organizationId", organizationId);
      sessionStorage.setItem("organizationName", "Eval onboarding org");
      sessionStorage.setItem("organizationDisplayName", "Eval onboarding org");
      sessionStorage.setItem("organizationRole", "Owner");
      sessionStorage.setItem("workspaceId", workspaceId);
      sessionStorage.setItem("workspaceName", "Eval onboarding workspace");
      sessionStorage.setItem(
        "workspaceDisplayName",
        "Eval onboarding workspace",
      );
      sessionStorage.setItem("workspaceRole", "Owner");
      sessionStorage.setItem("currentUserId", user.id);
      sessionStorage.setItem("futureagi-current-user-id", user.id);
      sessionStorage.setItem(
        setupQuickStartStorageKey,
        JSON.stringify(quickStartAttribution),
      );
    },
    {
      quickStartAttribution: EVAL_QUICK_START_ATTRIBUTION,
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      setupQuickStartStorageKey: SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );
}

function evalHomeActivationState(auth) {
  const activationState = getActivationStateFixture("observeNoSetup");
  const actionHref =
    "/dashboard/evaluations/create?source=onboarding&step=dataset";

  return {
    ...activationState,
    request_id: "eval_home_to_create_controlled_smoke",
    organization_id: auth.organizationId,
    workspace_id: auth.workspaceId,
    user_id: auth.user.id,
    goal: "evaluate_quality",
    primary_path: "evals",
    stage: "create_eval_dataset",
    progress: {
      build: "selected",
      test: "not_started",
      observe: "not_started",
      ship: "not_started",
      improve: "not_started",
    },
    recommended_action: {
      id: "create_eval_dataset",
      kind: "setup",
      title: "Create eval source",
      description: "Add a focused dataset or trace source.",
      href: actionHref,
      cta_label: "Create dataset",
      estimated_minutes: 3,
      priority: 100,
      blocked: false,
      blocked_reason: null,
      requires_permission: "evals:write",
      completion_event: "eval_dataset_created",
      is_sample: false,
      route_available: true,
      fallback_href: "/dashboard/get-started",
      analytics: {
        event_name: "onboarding_recommended_action_clicked",
        source: "home",
        target_path: "evals",
      },
    },
    fallback_action: {
      ...activationState.fallback_action,
      title: "Open Get Started",
      href: "/dashboard/get-started",
    },
    available_paths: [
      {
        id: "evals",
        label: "Test AI using simulation",
        description: "Create a small eval and review the first failure.",
        status: "selected",
        href: "/dashboard/home?path=evals",
        is_available: true,
        blocked_reason: null,
        requires_permission: null,
        first_action_id: "create_eval_dataset",
      },
    ],
    feature_flags: {
      ...activationState.feature_flags,
      onboarding_eval_path: true,
    },
    route_availability: {
      ...activationState.route_availability,
      path_evals: {
        href: "/dashboard/home?path=evals",
        is_available: true,
        reason: null,
      },
      create_eval_dataset: {
        href: actionHref,
        is_available: true,
        reason: null,
      },
    },
    sample_project: {
      ...activationState.sample_project,
      available: false,
    },
  };
}

function evalTemplateDetail() {
  return {
    id: EVAL_DRAFT_ID,
    name: "Eval onboarding smoke",
    description: "",
    eval_type: "agent",
    output_type: "pass_fail",
    output_type_normalized: "pass_fail",
    pass_threshold: 0.5,
    config: {
      code: "",
      language: "python",
      model: "turing_large",
      messages: [{ role: "system", content: "" }],
      data_injection: { variables_only: true },
    },
    is_draft: true,
    eval_tags: [],
  };
}

function evalFallbackResponse(path) {
  if (path.includes("versions")) {
    return { status: true, result: [] };
  }
  if (path.includes("usage") || path.includes("feedback")) {
    return { status: true, result: { count: 0, results: [] } };
  }
  return { status: true, result: {} };
}

function assertHomeEvalCta(href) {
  const url = new URL(href, APP_BASE);
  assert(
    url.pathname === "/dashboard/evaluations/create",
    `Expected Home eval CTA to open eval create, got ${url.pathname}`,
  );
  const expectedParams = {
    journey_step: "create_eval_dataset",
    source: "onboarding",
    tour_anchor: "eval_dataset_button",
  };
  Object.entries(expectedParams).forEach(([key, expected]) => {
    const actual = url.searchParams.get(key);
    assert(
      actual === expected,
      `Expected Home eval CTA ${key}=${expected}, got ${actual}`,
    );
  });
}

function assertEvalQuickStartParams(route, label) {
  const url = new URL(route, APP_BASE);
  for (const [key, expected] of Object.entries(EVAL_QUICK_START_PARAMS)) {
    const actual = url.searchParams.get(key);
    assert(
      actual === expected,
      `Expected ${label} ${key}=${expected}, got ${actual} in ${route}`,
    );
  }
}

function assertEvalQuickStartMetadata(payload, label) {
  const metadata = payload?.metadata || {};
  for (const [key, expected] of Object.entries(EVAL_QUICK_START_PARAMS)) {
    assert(
      metadata?.[key] === expected,
      `Expected ${label} metadata ${key}=${expected}, got ${metadata?.[key]}`,
    );
  }
}

async function clickVisibleButtonText(
  page,
  text,
  { exact = false, occurrence = "first", rootSelector = "body" } = {},
) {
  await page.waitForFunction(
    ({ expectedText, exactMatch, root }) => {
      const rootElements = Array.from(document.querySelectorAll(root));
      if (rootElements.length === 0) return false;
      return window.visibleElements("*").some((element) => {
        if (
          !rootElements.some((rootElement) => rootElement.contains(element))
        ) {
          return false;
        }
        const textContent = window.normalizeText(element.textContent);
        const matches = exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
        const clickable = element.closest(
          "button,a,[role='button'],[role='menuitem'],.MuiMenuItem-root",
        );
        return matches && clickable && !clickable.disabled;
      });
    },
    { timeout: 30000 },
    { expectedText: text, exactMatch: exact, root: rootSelector },
  );
  const clicked = await page.evaluate(
    ({ expectedText, exactMatch, occurrenceName, root }) => {
      const rootElements = Array.from(document.querySelectorAll(root));
      const candidates = window
        .visibleElements("*")
        .filter((element) => {
          if (
            !rootElements.some((rootElement) => rootElement.contains(element))
          ) {
            return false;
          }
          const textContent = window.normalizeText(element.textContent);
          const matches = exactMatch
            ? textContent === expectedText
            : textContent.includes(expectedText);
          const clickable = element.closest(
            "button,a,[role='button'],[role='menuitem'],.MuiMenuItem-root",
          );
          return matches && clickable && !clickable.disabled;
        })
        .map((element) =>
          element.closest(
            "button,a,[role='button'],[role='menuitem'],.MuiMenuItem-root",
          ),
        );
      const uniqueCandidates = [...new Set(candidates)];
      const clickable =
        occurrenceName === "last"
          ? uniqueCandidates[uniqueCandidates.length - 1]
          : uniqueCandidates[0];
      if (!clickable) return false;
      clickable.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
      );
      clickable.dispatchEvent(
        new MouseEvent("mouseup", { bubbles: true, cancelable: true }),
      );
      clickable.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
      return true;
    },
    {
      expectedText: text,
      exactMatch: exact,
      occurrenceName: occurrence,
      root: rootSelector,
    },
  );
  assert(clicked, `Could not click visible button text: ${text}`);
}

async function waitForPath(page, pathname, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname === expectedPath,
    { timeout },
    pathname,
  );
}

async function waitForSearchParam(page, key, value, timeout = 30000) {
  await page.waitForFunction(
    ({ expectedKey, expectedValue }) =>
      new URLSearchParams(window.location.search).get(expectedKey) ===
      expectedValue,
    { timeout },
    { expectedKey: key, expectedValue: value },
  );
}

async function expectSelector(page, selector, timeout = 30000) {
  await page.waitForSelector(selector, { visible: true, timeout });
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) =>
      window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      }),
    { timeout },
    { text, exact },
  );
}

async function currentRelativeUrl(page) {
  return page.evaluate(
    () => `${window.location.pathname}${window.location.search}`,
  );
}

async function visibleLinkHref(page, text, { exact = false } = {}) {
  await waitForVisibleText(page, text, { exact });
  const href = await page.evaluate(
    ({ expectedText, exactMatch }) => {
      const elements = window.visibleElements().filter((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
      const link = elements
        .map((element) => element.closest("a"))
        .find(Boolean);
      return link?.getAttribute("href") || "";
    },
    { expectedText: text, exactMatch: exact },
  );
  assert(href, `Could not find visible link href for text: ${text}`);
  return href;
}

function createStubbedAuthenticatedContext() {
  const user = {
    id: USER_ID,
    email: "eval-onboarding-smoke@example.com",
    name: "Eval Onboarding Smoke",
    remember_me: true,
    onboarding_completed: true,
    requires_org_setup: false,
    organization_id: ORGANIZATION_ID,
    organization_role: "Owner",
    org_level: 4,
    default_workspace_id: WORKSPACE_ID,
    default_workspace_name: "Eval onboarding workspace",
    default_workspace_display_name: "Eval onboarding workspace",
    default_workspace_role: "Owner",
    ws_level: 4,
    goals: ["evaluate_quality"],
  };
  return {
    apiBase: APP_BASE,
    organizationId: ORGANIZATION_ID,
    workspaceId: WORKSPACE_ID,
    user,
    tokens: {
      access: fakeJwt(USER_ID),
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
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return normalized.endsWith("/") ? normalized : `${normalized}/`;
}

function isStubbedApiPath(path) {
  return (
    path === "/config.js/" ||
    path.startsWith("/accounts/") ||
    path.startsWith("/model-hub/")
  );
}

function isStubbedApiUrl(value) {
  try {
    return isStubbedApiPath(slashPath(new URL(value).pathname));
  } catch {
    return false;
  }
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
  process.exit(1);
});
