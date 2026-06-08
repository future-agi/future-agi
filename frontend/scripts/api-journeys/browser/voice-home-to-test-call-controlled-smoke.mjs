/* eslint-disable no-console */
import { Buffer } from "node:buffer";
import { createRequire } from "node:module";
import process from "node:process";
import { assert } from "../lib/api-client.mjs";
import { getActivationStateFixture } from "../../../src/sections/onboarding-home/fixtures/activation-state.fixtures.js";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const AGENT_ID = "00000000-0000-4000-8000-000000000401";
const VERSION_ID = "00000000-0000-4000-8000-000000000402";
const SCENARIO_ID = "00000000-0000-4000-8000-000000000403";
const TEST_ID = "00000000-0000-4000-8000-000000000404";
const EXECUTION_ID = "00000000-0000-4000-8000-000000000405";
const CALL_ID = "00000000-0000-4000-8000-000000000406";
const USER_ID = "00000000-0000-4000-8000-000000000106";
const ORGANIZATION_ID = "00000000-0000-4000-8000-000000000206";
const WORKSPACE_ID = "00000000-0000-4000-8000-000000000306";
const HOME_SCREENSHOT_PATH =
  process.env.VOICE_HOME_CONTROLLED_SCREENSHOT ||
  "/tmp/voice-home-start-controlled-smoke.png";
const SCREENSHOT_PATH =
  process.env.VOICE_HOME_TO_TEST_CALL_CONTROLLED_SCREENSHOT ||
  "/tmp/voice-home-to-test-call-controlled-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/voice-home-to-test-call-controlled-smoke-failure.png";
const SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY =
  "futureagi.setup_quick_start_attribution";
const VOICE_QUICK_START_ATTRIBUTION = {
  quickStartGoal: "connect_voice_ai_agent",
  quickStartId: "voice",
  quickStartPrimaryPath: "voice",
};
const VOICE_QUICK_START_PARAMS = {
  quick_start_goal: "connect_voice_ai_agent",
  quick_start_id: "voice",
  quick_start_primary_path: "voice",
};
const VOICE_QUICK_START_QUERY = new URLSearchParams(
  VOICE_QUICK_START_PARAMS,
).toString();

const state = {
  hasRunStarted: false,
};

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
  const allRequests = [];
  const consoleMessages = [];
  const pageErrors = [];
  const requestFailures = [];
  const activationEventPosts = [];
  const createTestPosts = [];
  const runTestPosts = [];
  const stubbedApiRequests = [];
  const evidence = {};

  await installControlledWebSocket(page);
  await installBrowserState(page, auth);
  await installRuntime(page, auth, {
    activationEventPosts,
    allRequests,
    createTestPosts,
    runTestPosts,
    stubbedApiRequests,
  });

  page.on("response", (response) => {
    const url = response.url();
    if (isStubbedApiUrl(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("requestfailed", (request) => {
    const url = request.url();
    if (
      url.includes("us-assets.i.posthog.com") ||
      url.includes("google.com/recaptcha")
    ) {
      return;
    }
    requestFailures.push(
      `${request.method()} ${url} ${request.failure()?.errorText}`,
    );
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) {
      consoleMessages.push(`${message.type()}: ${message.text()}`);
    }
  });

  try {
    await page.goto(
      `${APP_BASE}/dashboard/home?source=setup_org&${VOICE_QUICK_START_QUERY}`,
      { waitUntil: "domcontentloaded" },
    );
    await waitForVisibleText(page, "Test a voice agent", { exact: true });
    await waitForVisibleText(page, "Hear how a call goes", { exact: true });
    await waitForVisibleText(
      page,
      "Run a test call so there is a real conversation to review.",
      { exact: true },
    );
    await waitForVisibleText(page, "Run call", { exact: true });
    evidence.home_route = await currentRelativeUrl(page);
    assertVoiceQuickStartParams(evidence.home_route, "Home route");
    evidence.home_cta_href = await visibleLinkHref(page, "Run call", {
      exact: true,
    });
    assertVoiceQuickStartParams(evidence.home_cta_href, "Home CTA");
    assertHomeVoiceCta(evidence.home_cta_href);
    await page.screenshot({ path: HOME_SCREENSHOT_PATH, fullPage: true });
    evidence.home_screenshot = HOME_SCREENSHOT_PATH;

    await clickVisibleText(page, "Run call", { exact: true });
    await waitForPath(page, "/dashboard/simulate/test");
    await waitForSearchParam(page, "onboarding", "create-test-call");
    await waitForVisibleText(page, "Create voice test call", { exact: true });
    await waitForVisibleText(page, "Create the first voice test call", {
      exact: true,
    });
    await dismissDestinationTourIfPresent(page);
    await waitForVisibleText(page, "Name the voice test call", {
      exact: true,
    });
    evidence.create_test_call_route = await currentRelativeUrl(page);
    assertVoiceQuickStartParams(
      evidence.create_test_call_route,
      "create-test-call route",
    );

    await clickVisibleText(page, "Next", { exact: true });
    await waitForVisibleText(page, "Choose your scenarios", { exact: true });
    await clickVisibleText(page, "Checkout voice support call");
    await clickVisibleText(page, "Next", { exact: true });
    await waitForVisibleText(page, "Confirm the review path", {
      exact: true,
    });
    await clickVisibleText(page, "Next", { exact: true });
    await waitForVisibleText(page, "Review test call setup", { exact: true });
    await waitForVisibleText(page, "Success criteria comes after transcript review");
    await clickVisibleText(page, "Create test call", { exact: true });

    await waitForPath(page, `/dashboard/simulate/test/${TEST_ID}/runs`);
    await waitForSearchParam(page, "onboarding", "run-test-call");
    evidence.run_test_call_route = await currentRelativeUrl(page);
    assertVoiceQuickStartParams(
      evidence.run_test_call_route,
      "run-test-call route",
    );
    await waitForVisibleText(page, "Run a voice test call", { exact: true });
    await waitForVisibleText(page, "Run one test call with the selected voice agent.");
    await dismissDestinationTourIfPresent(page);
    await clickSelector(page, '[data-tour-anchor="voice_test_call_button"]');

    await waitForPath(
      page,
      `/dashboard/simulate/test/${TEST_ID}/${EXECUTION_ID}/call-details`,
      45000,
    );
    await waitForSearchParam(page, "onboarding", "review-voice-call");
    await waitForSearchParam(page, "call_id", CALL_ID);
    evidence.review_call_route = await currentRelativeUrl(page);
    assertVoiceQuickStartParams(evidence.review_call_route, "review route");
    await waitForVisibleText(page, "Review the voice test call", {
      exact: true,
    });
    await waitForVisibleText(page, "Inspect the transcript, recording, latency, and outcome.");
    await dismissDestinationTourIfPresent(page);
    await waitForVisibleText(page, "Add success criteria", { exact: true });
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    await waitForCondition(
      () =>
        eventNames(activationEventPosts).includes(
          "voice_test_call_completed",
        ),
      "Expected voice_test_call_completed activation event.",
    );
    await waitForCondition(
      () => eventNames(activationEventPosts).includes("voice_call_reviewed"),
      "Expected voice_call_reviewed activation event.",
    );
    assert(
      createTestPosts.length === 1,
      `Expected one create-test request, got ${createTestPosts.length}.`,
    );
    assertCreateTestPayload(createTestPosts[0]);
    assert(
      runTestPosts.length === 1,
      `Expected one run-test request, got ${runTestPosts.length}.`,
    );

    await clickVisibleText(page, "Add success criteria", { exact: true });
    await waitForPath(page, `/dashboard/simulate/test/${TEST_ID}/runs`);
    await waitForSearchParam(page, "onboarding", "success-criteria");
    evidence.success_criteria_route = await currentRelativeUrl(page);
    assertVoiceQuickStartParams(
      evidence.success_criteria_route,
      "success criteria route",
    );

    const activationEventNames = eventNames(activationEventPosts);
    assert(
      activationEventNames.includes("onboarding_voice_route_focus_viewed"),
      "Voice route focus event was not recorded.",
    );
    assertVoiceActivationMetadata(activationEventPosts);
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
            voice_activation_events: activationEventPosts.map((payload) => ({
              event_name: payload.event_name,
              stage: payload.stage,
              quick_start_goal: payload.metadata?.quick_start_goal,
              quick_start_id: payload.metadata?.quick_start_id,
              quick_start_primary_path:
                payload.metadata?.quick_start_primary_path,
            })),
            create_test_request_count: createTestPosts.length,
            run_test_request_count: runTestPosts.length,
            stubbed_api_request_count: stubbedApiRequests.length,
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    const browserState = await page
      .evaluate(() => ({
        currentUrl: `${window.location.pathname}${window.location.search}`,
        lastClicked: window.__voiceSmokeLastClicked || null,
        targetClickCount: window.__voiceSmokeTargetClickCount || 0,
        elementAtLastClick: window.__voiceSmokeLastClicked
          ? (() => {
              const element = document.elementFromPoint(
                window.__voiceSmokeLastClicked.x,
                window.__voiceSmokeLastClicked.y,
              );
              return element
                ? {
                    tagName: element.tagName,
                    text: window.normalizeText(element.textContent),
                    closestButtonText: window.normalizeText(
                      element.closest("button")?.textContent,
                    ),
                    pointerEvents: window.getComputedStyle(element)
                      .pointerEvents,
                  }
                : null;
            })()
          : null,
        buttons: Array.from(document.querySelectorAll("button")).map(
          (button) => ({
            text: window.normalizeText(button.textContent),
            disabled:
              button.hasAttribute("disabled") ||
              button.getAttribute("aria-disabled") === "true" ||
              button.classList.contains("Mui-disabled"),
          }),
        ),
      }))
      .catch(() => null);
    await page
      .screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
    error.message = `${error.message}\nActivation events: ${eventNames(
      activationEventPosts,
    ).join(", ")}\nPage errors: ${pageErrors.join(
      "; ",
    )}\nConsole messages: ${consoleMessages
      .slice(-20)
      .join(
        "; ",
      )}\nRequest failures: ${requestFailures.join(
      "; ",
    )}\nAll POST requests: ${allRequests
      .filter((entry) => entry.startsWith("POST "))
      .join(
        ", ",
    )}\nStubbed API requests: ${stubbedApiRequests
      .slice(-40)
      .join(", ")}\nBrowser state: ${JSON.stringify(
      browserState,
    )}\nFailure screenshot: ${FAILURE_SCREENSHOT_PATH}`;
    throw error;
  } finally {
    await browser.close();
  }
}

async function installRuntime(
  page,
  auth,
  {
    activationEventPosts,
    allRequests,
    createTestPosts,
    runTestPosts,
    stubbedApiRequests,
  },
) {
  await page.setRequestInterception(true);
  page.on("request", async (request) => {
    const url = new URL(request.url());
    const normalizedPath = slashPath(url.pathname);
    allRequests.push(`${request.method()} ${url.origin}${normalizedPath}`);

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
              name: "Voice onboarding org",
              display_name: "Voice onboarding org",
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
            name: "Voice onboarding workspace",
            display_name: "Voice onboarding workspace",
            organization_id: ORGANIZATION_ID,
          },
        ],
      });
      return;
    }

    if (normalizedPath === "/accounts/activation-state/") {
      await respondJson(request, {
        status: true,
        result: voiceRunCallActivationState(auth),
      });
      return;
    }

    if (normalizedPath === "/accounts/activation-events/") {
      const payload = parseJsonPostData(request.postData());
      activationEventPosts.push(payload);
      await respondJson(request, {
        status: true,
        result: {
          event_id: "00000000-0000-4000-8000-000000000196",
          event_name: payload?.event_name || "onboarding_event",
          activation_state: voiceRunCallActivationState(auth),
        },
      });
      return;
    }

    if (
      normalizedPath === "/simulate/agent-definitions/" &&
      request.method() === "GET"
    ) {
      await respondJson(request, paginated([voiceAgentDefinition()]));
      return;
    }

    if (
      normalizedPath === `/simulate/agent-definitions/${AGENT_ID}/versions/` &&
      request.method() === "GET"
    ) {
      await respondJson(request, paginated([voiceAgentVersion()]));
      return;
    }

    if (
      normalizedPath ===
      `/simulate/agent-definitions/${AGENT_ID}/versions/${VERSION_ID}/`
    ) {
      await respondJson(request, voiceAgentVersionDetail());
      return;
    }

    if (
      normalizedPath === "/simulate/scenarios/" &&
      request.method() === "GET"
    ) {
      await respondJson(request, paginated([voiceScenario()]));
      return;
    }

    if (normalizedPath === "/simulate/scenarios/get-columns/") {
      await respondJson(request, { columnConfigs: [] });
      return;
    }

    if (
      normalizedPath === "/simulate/run-tests/create/" &&
      request.method() === "POST"
    ) {
      const payload = parseJsonPostData(request.postData());
      createTestPosts.push(payload);
      await respondJson(request, voiceRunTestDetail());
      return;
    }

    if (
      normalizedPath === `/simulate/run-tests/${TEST_ID}/execute/` &&
      request.method() === "POST"
    ) {
      state.hasRunStarted = true;
      runTestPosts.push(parseJsonPostData(request.postData()));
      await respondJson(request, voiceExecution());
      return;
    }

    if (normalizedPath === "/simulate/run-tests/" && request.method() === "GET") {
      await respondJson(request, paginated([voiceRunTestSummary()]));
      return;
    }

    if (normalizedPath === `/simulate/run-tests/${TEST_ID}/`) {
      await respondJson(request, voiceRunTestDetail());
      return;
    }

    if (normalizedPath === `/simulate/run-tests/${TEST_ID}/executions/`) {
      await respondJson(
        request,
        paginated(state.hasRunStarted ? [voiceExecution()] : []),
      );
      return;
    }

    if (normalizedPath === `/simulate/run-tests/${TEST_ID}/scenarios/`) {
      await respondJson(request, paginated([voiceScenario()]));
      return;
    }

    if (normalizedPath === `/simulate/run-tests/${TEST_ID}/call-executions/`) {
      await respondJson(request, paginated([voiceCallLog()]));
      return;
    }

    if (normalizedPath === `/simulate/test-executions/${EXECUTION_ID}/`) {
      await respondJson(request, executionDetailGrid());
      return;
    }

    if (
      normalizedPath === `/simulate/test-executions/${EXECUTION_ID}/kpis/`
    ) {
      await respondJson(request, voiceKpis());
      return;
    }

    if (
      normalizedPath ===
      `/simulate/test-executions/${EXECUTION_ID}/performance-summary/`
    ) {
      await respondJson(request, {
        pass_rate: 100,
        total_test_runs: 1,
        failed_runs: 0,
      });
      return;
    }

    if (normalizedPath === `/simulate/call-executions/${CALL_ID}/`) {
      await respondJson(request, voiceCallDetail());
      return;
    }

    if (
      normalizedPath === `/simulate/call-executions/${CALL_ID}/logs/` ||
      normalizedPath === `/simulate/call-executions/${EXECUTION_ID}/logs/`
    ) {
      await respondJson(request, { results: [] });
      return;
    }

    if (
      normalizedPath ===
        `/simulate/call-executions/${CALL_ID}/branch-analysis/` ||
      normalizedPath ===
        `/simulate/call-executions/${EXECUTION_ID}/branch-analysis/`
    ) {
      await respondJson(request, {
        analysis: {
          current_path: ["Greeting", "Issue triage"],
          expected_path: ["Greeting", "Issue triage", "Resolution"],
          new_nodes: [],
          new_edges: [],
          analysis_summary: "The call reached issue triage and needs criteria.",
        },
      });
      return;
    }

    if (normalizedPath.startsWith("/accounts/")) {
      await respondJson(request, { status: true, result: {} });
      return;
    }

    if (normalizedPath.startsWith("/simulate/")) {
      await respondJson(request, simulateFallbackResponse(normalizedPath));
      return;
    }

    if (normalizedPath.startsWith("/model-hub/")) {
      await respondJson(request, emptyPaginatedResponse());
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
      sessionStorage.setItem("organizationName", "Voice onboarding org");
      sessionStorage.setItem("organizationDisplayName", "Voice onboarding org");
      sessionStorage.setItem("organizationRole", "Owner");
      sessionStorage.setItem("workspaceId", workspaceId);
      sessionStorage.setItem("workspaceName", "Voice onboarding workspace");
      sessionStorage.setItem(
        "workspaceDisplayName",
        "Voice onboarding workspace",
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
      quickStartAttribution: VOICE_QUICK_START_ATTRIBUTION,
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      setupQuickStartStorageKey: SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );
}

function voiceRunCallActivationState(auth) {
  const activationState = getActivationStateFixture("observeNoSetup");
  const actionHref = `/dashboard/simulate/test?from=onboarding&onboarding=create-test-call&agent_definition_id=${AGENT_ID}`;
  const pathHref = "/dashboard/home?path=voice";
  return {
    ...activationState,
    request_id: "voice_home_to_test_call_controlled_smoke",
    organization_id: auth.organizationId,
    workspace_id: auth.workspaceId,
    user_id: auth.user.id,
    goal: "connect_voice_ai_agent",
    primary_path: "voice",
    stage: "run_voice_test_call",
    progress: {
      build: "complete",
      test: "selected",
      observe: "available",
      ship: "available",
      improve: "available",
    },
    recommended_action: {
      id: "run_voice_test_call",
      kind: "test",
      title: "Run test call",
      description: "Run one call so the transcript and outcome are reviewable.",
      href: actionHref,
      cta_label: "Run call",
      estimated_minutes: 3,
      priority: 100,
      blocked: false,
      blocked_reason: null,
      requires_permission: null,
      completion_event: "voice_test_call_completed",
      is_sample: false,
      route_available: true,
      fallback_href: pathHref,
      analytics: {
        event_name: "onboarding_recommended_action_clicked",
        source: "home",
        target_path: "voice",
      },
    },
    available_paths: [
      {
        id: "voice",
        label: "Connect a voice AI agent",
        description: "Run or review a call with clear success criteria.",
        status: "selected",
        href: pathHref,
        is_available: true,
        blocked_reason: null,
        requires_permission: null,
        first_action_id: "run_voice_test_call",
      },
    ],
    feature_flags: {
      ...activationState.feature_flags,
      onboarding_voice_path: true,
      onboarding_voice_route_modes: true,
    },
    route_availability: {
      ...activationState.route_availability,
      path_voice: {
        href: pathHref,
        is_available: true,
        reason: null,
      },
      run_voice_test_call: {
        href: actionHref,
        is_available: true,
        reason: null,
      },
    },
    sample_project: {
      ...activationState.sample_project,
      available: false,
    },
    signals: {
      ...activationState.signals,
      voice_agents: 1,
      voice_simulations: state.hasRunStarted ? 1 : 0,
      voice_calls: state.hasRunStarted ? 1 : 0,
      voice_reviews: 0,
    },
  };
}

function voiceAgentDefinition() {
  return {
    id: AGENT_ID,
    agentName: "Checkout voice agent",
    agent_name: "Checkout voice agent",
    agentType: "voice",
    agent_type: "voice",
    versions: [voiceAgentVersion()],
    agent_versions: [voiceAgentVersion()],
  };
}

function voiceAgentVersion() {
  return {
    id: VERSION_ID,
    versionNameDisplay: "v1",
    version_name_display: "v1",
    name: "v1",
    configuration_snapshot: voiceConfigurationSnapshot(),
    configurationSnapshot: voiceConfigurationSnapshot(),
  };
}

function voiceAgentVersionDetail() {
  return {
    id: VERSION_ID,
    agent_definition: AGENT_ID,
    name: "v1",
    configuration_snapshot: voiceConfigurationSnapshot(),
    configurationSnapshot: voiceConfigurationSnapshot(),
  };
}

function voiceConfigurationSnapshot() {
  return {
    agent_type: "voice",
    agentType: "voice",
    provider: "livekit",
    livekitUrl: "wss://voice.example",
    livekit_url: "wss://voice.example",
    livekitApiKey: "key",
    livekit_api_key: "key",
    livekitApiSecret: "secret",
    livekit_api_secret: "secret",
    livekitAgentName: "checkout-agent",
    livekit_agent_name: "checkout-agent",
  };
}

function voiceScenario() {
  return {
    id: SCENARIO_ID,
    name: "Checkout voice support call",
    description: "Customer asks whether the checkout issue is resolved.",
    datasetRows: 1,
    dataset_rows: 1,
    scenarioType: "dataset",
    scenario_type: "dataset",
    source: "controlled-smoke",
    status: "Completed",
    agent: {
      id: "persona-1",
      name: "Checkout customer",
      prompt: "Ask whether the checkout issue is resolved.",
    },
  };
}

function voiceRunTestSummary() {
  return {
    id: TEST_ID,
    name: "First voice test call",
    created_at: new Date().toISOString(),
    source_type: "agent_definition",
    agent_definition: "Checkout voice agent",
    agent_version: "v1",
    run_count: state.hasRunStarted ? 1 : 0,
  };
}

function voiceRunTestDetail() {
  return {
    id: TEST_ID,
    name: "First voice test call",
    description: "",
    source_type: "agent_definition",
    sourceType: "agent_definition",
    agent_definition: AGENT_ID,
    agentDefinition: AGENT_ID,
    agent_definition_detail: voiceAgentDefinition(),
    agentDefinitionDetail: voiceAgentDefinition(),
    agent_version: voiceAgentVersion(),
    agentVersion: voiceAgentVersion(),
    scenarios: [SCENARIO_ID],
    scenarios_detail: [voiceScenario()],
    scenariosDetail: [voiceScenario()],
    simulate_eval_configs_detail: [],
    simulateEvalConfigsDetail: [],
    created_at: new Date().toISOString(),
  };
}

function voiceExecution() {
  return {
    id: EXECUTION_ID,
    name: "Voice test call run",
    status: "completed",
    run_status: "completed",
    created_at: new Date().toISOString(),
    started_at: new Date().toISOString(),
    completed_at: new Date().toISOString(),
    duration: 42,
    total_calls: 1,
    connected_calls: 1,
    failed_calls: 0,
    call_id: CALL_ID,
    call_execution_id: CALL_ID,
    agent_definition: "Checkout voice agent",
    agent_version: "v1",
  };
}

function voiceCallLog() {
  return {
    ...voiceExecution(),
    id: CALL_ID,
    test_execution_id: EXECUTION_ID,
    transcript: "Customer asks about checkout. Agent confirms resolution.",
    recording_url: null,
    overall_score: 88,
  };
}

function voiceCallDetail() {
  return {
    id: CALL_ID,
    test_execution_id: EXECUTION_ID,
    status: "completed",
    transcript: "Customer asks about checkout. Agent confirms resolution.",
    recording_url: null,
    started_at: new Date().toISOString(),
    duration: 42,
  };
}

function executionDetailGrid() {
  return {
    count: 1,
    current_page: 1,
    next: null,
    previous: null,
    results: [
      {
        id: CALL_ID,
        trace_id: CALL_ID,
        status: "completed",
        call_transcript:
          "Customer asks about checkout. Agent confirms resolution.",
        call: {
          transcript:
            "Customer asks about checkout. Agent confirms resolution.",
        },
      },
    ],
    column_order: [
      {
        field: "status",
        headerName: "Status",
        type: "scenario_dataset_column",
        dataType: "text",
      },
      {
        field: "call_transcript",
        headerName: "Transcript",
        type: "scenario_dataset_column",
        dataType: "text",
      },
    ],
    status: "completed",
  };
}

function voiceKpis() {
  return {
    agent_type: "voice",
    total_calls: 1,
    connected_calls: 1,
    failed_calls: 0,
    calls_connected_percentage: 100,
    total_duration: 42,
    avg_response: 1.2,
    avg_user_interruption_count: 0,
    avg_ai_interruption_count: 0,
    agent_talk_percentage: 48,
    customer_talk_percentage: 52,
    is_inbound: true,
  };
}

function simulateFallbackResponse(path) {
  if (path.includes("/eval-summary/")) return {};
  if (path.includes("/call-executions/")) return { results: [] };
  if (path.includes("/test-executions/")) return emptyPaginatedResponse();
  return emptyPaginatedResponse();
}

function assertCreateTestPayload(payload) {
  assert(payload?.name === "First voice test call", "Unexpected test name.");
  assert(
    payload?.agent_definition_id === AGENT_ID,
    "Create payload lost the voice agent id.",
  );
  assert(
    payload?.agent_version === VERSION_ID,
    "Create payload lost the agent version.",
  );
  assert(
    Array.isArray(payload?.scenario_ids) &&
      payload.scenario_ids.includes(SCENARIO_ID),
    "Create payload did not include the selected voice scenario.",
  );
  assert(
    Array.isArray(payload?.evaluations_config) &&
      payload.evaluations_config.length === 0,
    "Voice test-call setup should not require pre-added success criteria.",
  );
}

function assertHomeVoiceCta(href) {
  const url = new URL(href, APP_BASE);
  assert(
    url.pathname === "/dashboard/simulate/test",
    `Unexpected Home voice CTA path: ${href}`,
  );
  assert(
    url.searchParams.get("from") === "onboarding",
    `Home voice CTA lost onboarding source: ${href}`,
  );
  assert(
    url.searchParams.get("onboarding") === "create-test-call",
    `Home voice CTA should create a test call: ${href}`,
  );
  assert(
    url.searchParams.get("agent_definition_id") === AGENT_ID,
    `Home voice CTA lost agent id: ${href}`,
  );
  assert(
    url.searchParams.get("journey_step") === "run_voice_test_call",
    `Home voice CTA lost journey step: ${href}`,
  );
  assert(
    url.searchParams.get("tour_anchor") === "voice_test_call_button",
    `Home voice CTA lost tour anchor: ${href}`,
  );
}

function assertVoiceQuickStartParams(href, label) {
  const url = new URL(href, APP_BASE);
  Object.entries(VOICE_QUICK_START_PARAMS).forEach(([key, value]) => {
    assert(
      url.searchParams.get(key) === value,
      `${label} lost ${key}: ${href}`,
    );
  });
}

function assertVoiceActivationMetadata(activationEventPosts) {
  const voiceEvents = activationEventPosts.filter((payload) =>
    [
      "onboarding_voice_route_focus_viewed",
      "voice_test_call_completed",
      "voice_call_reviewed",
    ].includes(payload?.event_name),
  );
  assert(voiceEvents.length >= 3, "Expected at least three voice events.");
  voiceEvents.forEach((payload) => {
    assert(
      payload?.metadata?.quick_start_goal === "connect_voice_ai_agent",
      `${payload.event_name} lost quick_start_goal.`,
    );
    assert(
      payload?.metadata?.quick_start_id === "voice",
      `${payload.event_name} lost quick_start_id.`,
    );
    assert(
      payload?.metadata?.quick_start_primary_path === "voice",
      `${payload.event_name} lost quick_start_primary_path.`,
    );
  });
}

function eventNames(activationEventPosts) {
  return activationEventPosts
    .map((payload) => payload?.event_name || payload?.eventName)
    .filter(Boolean);
}

async function waitForPath(page, path, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname === expectedPath,
    { timeout },
    path,
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

async function dismissDestinationTourIfPresent(page) {
  const hasGotIt = await visibleTextExists(page, "Got it");
  if (!hasGotIt) return;
  await clickVisibleText(page, "Got it");
  await page
    .waitForFunction(
      () =>
        !window.visibleElements().some((element) => {
          const textContent = window.normalizeText(element.textContent);
          return textContent.includes("Got it");
        }),
      { timeout: 10000 },
    )
    .catch(() => null);
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

async function clickVisibleText(page, text, { exact = false } = {}) {
  await waitForVisibleText(page, text, { exact });
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const isDisabled = (element) =>
        element.hasAttribute("disabled") ||
        element.getAttribute("aria-disabled") === "true" ||
        element.classList.contains("Mui-disabled");
      return window
        .visibleElements("*")
        .some((element) => {
          const textContent = window.normalizeText(element.textContent);
          const matches = exactMatch
            ? textContent === expectedText
            : textContent.includes(expectedText);
          const clickable = element.closest(
            "button,a,[role='button'],[role='menuitem'],.MuiMenuItem-root,li",
          );
          return matches && clickable && !isDisabled(clickable);
        });
    },
    { timeout: 30000 },
    { text, exact },
  );
  const targetPoint = await page.evaluate(
    ({ text: expectedText, exact: exactMatch }) => {
      const isDisabled = (element) =>
        element.hasAttribute("disabled") ||
        element.getAttribute("aria-disabled") === "true" ||
        element.classList.contains("Mui-disabled");
      const matches = window
        .visibleElements("*")
        .filter((element) => {
          const textContent = window.normalizeText(element.textContent);
          const matchesText = exactMatch
            ? textContent === expectedText
            : textContent.includes(expectedText);
          const clickable = element.closest(
            "button,a,[role='button'],[role='menuitem'],.MuiMenuItem-root,li",
          );
          return matchesText && clickable && !isDisabled(clickable);
        })
        .map((element) =>
          element.closest(
            "button,a,[role='button'],[role='menuitem'],.MuiMenuItem-root,li",
          ),
        );
      const uniqueMatches = [...new Set(matches)];
      const target = uniqueMatches[uniqueMatches.length - 1];
      if (!target) return null;
      target.scrollIntoView({ block: "center", inline: "center" });
      window.__voiceSmokeTargetClickCount = 0;
      target.addEventListener(
        "click",
        () => {
          window.__voiceSmokeTargetClickCount =
            (window.__voiceSmokeTargetClickCount || 0) + 1;
        },
        { once: false },
      );
      const rect = target.getBoundingClientRect();
      window.__voiceSmokeLastClicked = {
        tagName: target.tagName,
        text: window.normalizeText(target.textContent),
        disabled: isDisabled(target),
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
      };
      return {
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
      };
    },
    { text, exact },
  );
  assert(targetPoint, `Could not click visible text: ${text}`);
  await page.mouse.click(targetPoint.x, targetPoint.y);
}

async function clickSelector(page, selector, timeout = 30000) {
  await page.waitForSelector(selector, { visible: true, timeout });
  const targetPoint = await page.evaluate((targetSelector) => {
    const target = document.querySelector(targetSelector);
    if (!target) return null;
    target.scrollIntoView({ block: "center", inline: "center" });
    const rect = target.getBoundingClientRect();
    window.__voiceSmokeLastClicked = {
      selector: targetSelector,
      tagName: target.tagName,
      text: window.normalizeText(target.textContent),
      disabled:
        target.hasAttribute("disabled") ||
        target.getAttribute("aria-disabled") === "true" ||
        target.classList.contains("Mui-disabled"),
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
  }, selector);
  assert(targetPoint, `Could not click selector: ${selector}`);
  await page.focus(selector);
  await page.mouse.click(targetPoint.x, targetPoint.y);
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

async function currentRelativeUrl(page) {
  return page.evaluate(
    () => `${window.location.pathname}${window.location.search}`,
  );
}

async function waitForCondition(predicate, message, timeout = 30000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (predicate()) return;
    await new Promise((resolve) => {
      setTimeout(resolve, 100);
    });
  }
  assert(false, message);
}

function createStubbedAuthenticatedContext() {
  const user = {
    id: USER_ID,
    email: "voice-onboarding-smoke@example.com",
    name: "Voice Onboarding Smoke",
    remember_me: true,
    onboarding_completed: true,
    requires_org_setup: false,
    organization_id: ORGANIZATION_ID,
    organization_role: "Owner",
    org_level: 4,
    default_workspace_id: WORKSPACE_ID,
    default_workspace_name: "Voice onboarding workspace",
    default_workspace_display_name: "Voice onboarding workspace",
    default_workspace_role: "Owner",
    ws_level: 4,
    goals: ["connect_voice_ai_agent"],
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

function paginated(results) {
  return {
    count: results.length,
    current_page: 1,
    next: null,
    previous: null,
    results,
    total_pages: 1,
  };
}

function emptyPaginatedResponse() {
  return paginated([]);
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
    path.startsWith("/simulate/") ||
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
