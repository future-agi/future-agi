/* eslint-disable no-console */
import { Buffer } from "node:buffer";
import { createRequire } from "node:module";
import process from "node:process";
import { assert } from "../lib/api-client.mjs";
import { getActivationStateFixture } from "../../../src/sections/onboarding-home/fixtures/activation-state.fixtures.js";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const GATEWAY_ID = "gateway-1";
const GATEWAY_REQUEST_ID = "req-123";
const GATEWAY_LOG_ID = "log-123";
const USER_ID = "00000000-0000-4000-8000-000000000102";
const ORGANIZATION_ID = "00000000-0000-4000-8000-000000000202";
const WORKSPACE_ID = "00000000-0000-4000-8000-000000000302";
const HOME_SCREENSHOT_PATH =
  process.env.GATEWAY_HOME_CONTROLLED_SCREENSHOT ||
  "/tmp/gateway-home-start-controlled-smoke.png";
const SCREENSHOT_PATH =
  process.env.GATEWAY_HOME_TO_REQUEST_CONTROLLED_SCREENSHOT ||
  "/tmp/gateway-home-to-request-controlled-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/gateway-home-to-request-controlled-smoke-failure.png";
const SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY =
  "futureagi.setup_quick_start_attribution";
const GATEWAY_QUICK_START_ATTRIBUTION = {
  quickStartGoal: "control_model_traffic",
  quickStartId: "gateway",
  quickStartPrimaryPath: "gateway",
};
const GATEWAY_QUICK_START_PARAMS = {
  quick_start_goal: "control_model_traffic",
  quick_start_id: "gateway",
  quick_start_primary_path: "gateway",
};
const GATEWAY_QUICK_START_QUERY = new URLSearchParams(
  GATEWAY_QUICK_START_PARAMS,
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
  const gatewayTestRequests = [];
  const stubbedApiRequests = [];
  const evidence = {};

  await installControlledWebSocket(page);
  await installBrowserState(page, auth);
  await installRuntime(page, auth, {
    activationEventPosts,
    gatewayTestRequests,
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
      `${APP_BASE}/dashboard/home?source=setup_org&${GATEWAY_QUICK_START_QUERY}`,
      { waitUntil: "domcontentloaded" },
    );
    await waitForVisibleText(page, "Set up gateway", { exact: true });
    await waitForVisibleText(page, "Start with Send request.");
    await waitForVisibleText(page, "Start here", { exact: true });
    await waitForVisibleText(page, "Step 3 of 6", { exact: true });
    await waitForVisibleText(page, "Send request", { exact: true });
    await waitForVisibleText(page, "Your setup path", { exact: true });
    evidence.home_route = await currentRelativeUrl(page);
    assertGatewayQuickStartParams(evidence.home_route, "Home route");
    evidence.home_cta_href = await visibleLinkHref(page, "Send request", {
      exact: true,
    });
    assertGatewayQuickStartParams(evidence.home_cta_href, "Home CTA");
    assertHomeGatewayCta(evidence.home_cta_href);
    await page.screenshot({ path: HOME_SCREENSHOT_PATH, fullPage: true });
    evidence.home_screenshot = HOME_SCREENSHOT_PATH;

    await clickVisibleButtonText(page, "Send request", { exact: true });
    await waitForPath(page, "/dashboard/gateway");
    await waitForSearchParam(page, "onboarding", "test-request");
    await expectSelector(page, '[data-testid="gateway-onboarding-focus"]');
    await waitForVisibleText(page, "Send the first gateway request", {
      exact: true,
    });
    await waitForVisibleText(page, "Send test request", { exact: true });
    await expectSelector(page, '[data-tour-anchor="gateway_request_button"]');
    evidence.gateway_request_route = await currentRelativeUrl(page);
    assertGatewayQuickStartParams(
      evidence.gateway_request_route,
      "Gateway request route",
    );

    await clickVisibleButtonText(page, "Send test request", { exact: true });
    await waitForPath(page, "/dashboard/gateway/logs");
    await waitForSearchParam(page, "onboarding", "review-request");
    await waitForSearchParam(page, "request_id", GATEWAY_REQUEST_ID);
    evidence.log_review_route = await currentRelativeUrl(page);
    assertGatewayQuickStartParams(evidence.log_review_route, "Log route");
    await waitForVisibleText(page, "Request Logs", { exact: true });
    await waitForVisibleText(page, "Review the first gateway request", {
      exact: true,
    });
    await waitForVisibleText(page, "Open request detail", { exact: true });

    if (
      !(await visibleTextExists(
        page,
        "Add one control before scaling traffic",
        {
          exact: true,
        },
      ))
    ) {
      await clickVisibleButtonText(page, "Open request detail", {
        exact: true,
      });
    }
    await expectSelector(page, '[data-testid="gateway-policy-handoff"]');
    await waitForVisibleText(page, "Log reviewed", { exact: true });
    await waitForVisibleText(page, "Add one control before scaling traffic", {
      exact: true,
    });
    await waitForVisibleText(page, "Set budget", { exact: true });
    evidence.request_detail_route = await currentRelativeUrl(page);
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(
      gatewayTestRequests.length === 1,
      `Expected one gateway test request, got ${gatewayTestRequests.length}.`,
    );
    const activationEventNames = activationEventPosts
      .map((payload) => payload?.event_name)
      .filter(Boolean);
    assert(
      activationEventNames.includes("gateway_request_seen"),
      "Gateway request event was not recorded.",
    );
    assert(
      activationEventNames.includes("gateway_log_opened"),
      "Gateway log-open event was not recorded.",
    );
    const gatewayActivationEvents = activationEventPosts.filter((payload) =>
      ["gateway_request_seen", "gateway_log_opened"].includes(
        payload?.event_name,
      ),
    );
    gatewayActivationEvents.forEach((payload) => {
      assertGatewayQuickStartMetadata(payload, payload.event_name);
    });
    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
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
            activation_event_names: activationEventNames,
            gateway_activation_events: gatewayActivationEvents.map(
              (payload) => ({
                event_name: payload.event_name,
                quick_start_goal: payload.metadata?.quick_start_goal,
                quick_start_id: payload.metadata?.quick_start_id,
                quick_start_primary_path:
                  payload.metadata?.quick_start_primary_path,
              }),
            ),
            gateway_test_request_count: gatewayTestRequests.length,
            stubbed_api_request_count: stubbedApiRequests.length,
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await page
      .screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
    console.error(
      JSON.stringify(
        {
          status: "failed",
          app_base: APP_BASE,
          evidence,
          api_failures: apiFailures,
          page_errors: pageErrors,
          request_failures: requestFailures,
          activation_events: activationEventPosts,
          gateway_test_requests: gatewayTestRequests,
          failure_screenshot: FAILURE_SCREENSHOT_PATH,
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
  { activationEventPosts, gatewayTestRequests, stubbedApiRequests },
) {
  await page.setRequestInterception(true);
  page.on("request", async (request) => {
    const url = new URL(request.url());
    const normalizedPath = slashPath(url.pathname);

    if (isStubbedApiPath(normalizedPath)) {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
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
              name: "Gateway onboarding org",
              display_name: "Gateway onboarding org",
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
            name: "Gateway onboarding workspace",
            display_name: "Gateway onboarding workspace",
            organization_id: ORGANIZATION_ID,
          },
        ],
      });
      return;
    }

    if (normalizedPath === "/accounts/activation-state/") {
      await respondJson(request, {
        status: true,
        result: stubbedActivationState(auth, "gatewayKeyNoRequest"),
      });
      return;
    }

    if (normalizedPath === "/accounts/activation-events/") {
      const payload = parseJsonPostData(request.postData());
      activationEventPosts.push(payload);
      const fixtureName =
        payload?.event_name === "gateway_request_seen"
          ? "gatewayRequestReady"
          : "gatewayRequestReady";
      await respondJson(request, {
        status: true,
        result: {
          event_id: "00000000-0000-4000-8000-000000000189",
          event_name: payload?.event_name || "gateway_request_seen",
          activation_state: stubbedActivationState(auth, fixtureName, {
            recommendedActionHref: `/dashboard/gateway/logs?onboarding=review-request&request_id=${GATEWAY_REQUEST_ID}`,
          }),
        },
      });
      return;
    }

    if (normalizedPath === "/agentcc/gateways/") {
      await respondJson(request, {
        status: true,
        result: [gatewaySummary()],
      });
      return;
    }

    if (normalizedPath === `/agentcc/gateways/${GATEWAY_ID}/`) {
      await respondJson(request, { status: true, result: gatewaySummary() });
      return;
    }

    if (normalizedPath === `/agentcc/gateways/${GATEWAY_ID}/config/`) {
      await respondJson(request, {
        status: true,
        result: {
          providers: { openai: { enabled: true, models: ["gpt-4o-mini"] } },
          cost_tracking: { enabled: true },
        },
      });
      return;
    }

    if (normalizedPath === `/agentcc/gateways/${GATEWAY_ID}/providers/`) {
      await respondJson(request, {
        status: true,
        result: {
          providers: [
            {
              id: "provider-1",
              name: "openai",
              provider_name: "openai",
              status: "healthy",
              model_count: 2,
              configured: true,
            },
          ],
        },
      });
      return;
    }

    if (normalizedPath === "/agentcc/api-keys/") {
      await respondJson(request, {
        status: true,
        result: [
          {
            id: "key-1",
            gateway_key_id: "key-1",
            name: "Gateway onboarding key",
            key_prefix: "fagi_",
            status: "active",
            allowed_models: ["gpt-4o-mini"],
            allowed_providers: ["openai"],
            created_at: new Date().toISOString(),
          },
        ],
      });
      return;
    }

    if (normalizedPath === "/agentcc/analytics/overview/") {
      await respondJson(request, {
        status: true,
        result: {
          total_requests: 0,
          total_cost: 0,
          avg_latency_ms: null,
          error_rate: 0,
        },
      });
      return;
    }

    if (
      normalizedPath === `/agentcc/gateways/${GATEWAY_ID}/test-playground/` &&
      request.method() === "POST"
    ) {
      gatewayTestRequests.push(parseJsonPostData(request.postData()));
      await respondJson(request, {
        status: true,
        result: gatewayPlaygroundResult(),
      });
      return;
    }

    if (normalizedPath === "/agentcc/request-logs/") {
      await respondJson(request, {
        status: true,
        result: {
          count: 1,
          results: [gatewayRequestLog()],
        },
      });
      return;
    }

    if (normalizedPath === `/agentcc/request-logs/${GATEWAY_LOG_ID}/`) {
      await respondJson(request, {
        status: true,
        result: gatewayRequestLog(),
      });
      return;
    }

    if (normalizedPath === "/agentcc/request-logs/sessions/") {
      await respondJson(request, {
        status: true,
        result: { count: 0, results: [] },
      });
      return;
    }

    if (normalizedPath.startsWith("/accounts/")) {
      await respondJson(request, { status: true, result: {} });
      return;
    }

    if (normalizedPath.startsWith("/agentcc/")) {
      await respondJson(request, gatewayFallbackResponse(normalizedPath));
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
    window.normalizeText = (value) => String(value || "").trim();
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
      sessionStorage.setItem("organizationName", "Gateway onboarding org");
      sessionStorage.setItem(
        "organizationDisplayName",
        "Gateway onboarding org",
      );
      sessionStorage.setItem("organizationRole", "Owner");
      sessionStorage.setItem("workspaceId", workspaceId);
      sessionStorage.setItem("workspaceName", "Gateway onboarding workspace");
      sessionStorage.setItem(
        "workspaceDisplayName",
        "Gateway onboarding workspace",
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
      quickStartAttribution: GATEWAY_QUICK_START_ATTRIBUTION,
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      setupQuickStartStorageKey: SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );
}

function gatewaySummary() {
  return {
    id: GATEWAY_ID,
    name: "Gateway onboarding smoke",
    status: "healthy",
    base_url: `${APP_BASE}/v1`,
    provider_count: 1,
    model_count: 2,
    last_health_check: new Date().toISOString(),
  };
}

function gatewayPlaygroundResult() {
  return {
    status_code: 200,
    body: {
      id: "chatcmpl-gateway-onboarding",
      choices: [
        {
          message: {
            role: "assistant",
            content: "Gateway onboarding request completed.",
          },
        },
      ],
    },
    guardrail_headers: {
      "x-agentcc-request-id": GATEWAY_REQUEST_ID,
    },
    model: "gpt-4o-mini",
    request_id: GATEWAY_REQUEST_ID,
    blocked: false,
    warned: false,
  };
}

function gatewayRequestLog() {
  return {
    id: GATEWAY_LOG_ID,
    request_id: GATEWAY_REQUEST_ID,
    started_at: new Date().toISOString(),
    model: "gpt-4o-mini",
    resolved_model: "gpt-4o-mini",
    provider: "openai",
    status_code: 200,
    latency_ms: 384,
    cost: "0.002",
    input_tokens: 18,
    output_tokens: 12,
    total_tokens: 30,
    session_id: "gateway-onboarding-session",
    is_error: false,
    cache_hit: false,
    fallback_used: false,
    guardrail_triggered: false,
    request_body: {
      model: "gpt-4o-mini",
      messages: [{ role: "user", content: "Say hello through the gateway." }],
    },
    response_body: gatewayPlaygroundResult().body,
    request_headers: { authorization: "Bearer fagi_..." },
    response_headers: { "x-agentcc-request-id": GATEWAY_REQUEST_ID },
    metadata: {
      is_sample: false,
      route: "onboarding",
    },
  };
}

function stubbedActivationState(auth, fixtureName, overrides = {}) {
  const activationState = getActivationStateFixture(fixtureName);
  const recommendedActionHref = overrides.recommendedActionHref;
  return {
    ...activationState,
    request_id: "gateway_home_to_request_controlled_smoke",
    organization_id: auth.organizationId,
    workspace_id: auth.workspaceId,
    user_id: auth.user.id,
    recommended_action: recommendedActionHref
      ? {
          ...activationState.recommended_action,
          href: recommendedActionHref,
        }
      : activationState.recommended_action,
  };
}

function gatewayFallbackResponse(path) {
  if (path.includes("request-logs")) {
    return { status: true, result: { count: 0, results: [] } };
  }
  if (path.includes("analytics")) {
    return { status: true, result: {} };
  }
  return { status: true, result: {} };
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

async function visibleTextExists(page, text, { exact = false } = {}) {
  return page.evaluate(
    ({ text: expectedText, exact: exactMatch }) =>
      window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      }),
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

function assertHomeGatewayCta(href) {
  const url = new URL(href, APP_BASE);
  assert(
    url.pathname === "/dashboard/gateway",
    `Expected Home gateway CTA to open Gateway overview, got ${url.pathname}`,
  );
  const expectedParams = {
    journey_step: "run_gateway_request",
    onboarding: "test-request",
    tour_anchor: "gateway_request_button",
  };
  Object.entries(expectedParams).forEach(([key, expected]) => {
    const actual = url.searchParams.get(key);
    assert(
      actual === expected,
      `Expected Home gateway CTA ${key}=${expected}, got ${actual}`,
    );
  });
}

function assertGatewayQuickStartParams(route, label) {
  const url = new URL(route, APP_BASE);
  for (const [key, expected] of Object.entries(GATEWAY_QUICK_START_PARAMS)) {
    const actual = url.searchParams.get(key);
    assert(
      actual === expected,
      `Expected ${label} ${key}=${expected}, got ${actual} in ${route}`,
    );
  }
}

function assertGatewayQuickStartMetadata(payload, label) {
  const metadata = payload?.metadata || {};
  for (const [key, expected] of Object.entries(GATEWAY_QUICK_START_PARAMS)) {
    assert(
      metadata?.[key] === expected,
      `Expected ${label} metadata ${key}=${expected}, got ${metadata?.[key]}`,
    );
  }
}

function createStubbedAuthenticatedContext() {
  const user = {
    id: USER_ID,
    email: "gateway-onboarding-smoke@example.com",
    name: "Gateway Onboarding Smoke",
    remember_me: true,
    onboarding_completed: true,
    requires_org_setup: false,
    organization_id: ORGANIZATION_ID,
    organization_role: "Owner",
    org_level: 4,
    default_workspace_id: WORKSPACE_ID,
    default_workspace_name: "Gateway onboarding workspace",
    default_workspace_display_name: "Gateway onboarding workspace",
    default_workspace_role: "Owner",
    ws_level: 4,
    goals: ["control_model_traffic"],
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
    path.startsWith("/agentcc/")
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
