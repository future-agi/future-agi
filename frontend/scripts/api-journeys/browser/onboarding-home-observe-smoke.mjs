/* eslint-disable no-console */
import { Buffer } from "node:buffer";
import { createRequire } from "node:module";
import process from "node:process";
import {
  assert,
  createAuthenticatedContext,
  envFlag,
} from "../lib/api-client.mjs";
import { getActivationStateFixture } from "../../../src/sections/onboarding-home/fixtures/activation-state.fixtures.js";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const VIEWPORT_NAME = process.env.ONBOARDING_SMOKE_VIEWPORT || "desktop";
const EXISTING_PROJECT = envFlag("ONBOARDING_SMOKE_EXISTING_PROJECT");
const EXISTING_TRACE =
  EXISTING_PROJECT && envFlag("ONBOARDING_SMOKE_EXISTING_TRACE");
const OPEN_SAMPLE_HOME = envFlag("ONBOARDING_SMOKE_OPEN_SAMPLE");
const POST_AHA_HOME = envFlag("ONBOARDING_SMOKE_POST_AHA_HOME");
const FEATURE_DISABLED_HOME = envFlag("ONBOARDING_SMOKE_FEATURE_DISABLED_HOME");
const SCREENSHOT_PATH =
  process.env.ONBOARDING_HOME_OBSERVE_SCREENSHOT ||
  `/tmp/onboarding-home-observe-smoke-${VIEWPORT_NAME}${
    EXISTING_PROJECT ? "-existing-project" : ""
  }${EXISTING_TRACE ? "-first-trace" : ""}${
    OPEN_SAMPLE_HOME ? "-sample-open" : ""
  }${
    POST_AHA_HOME ? "-post-aha-fallback" : ""
  }${FEATURE_DISABLED_HOME ? "-get-started-fallback" : ""}.png`;
const HOME_SCREENSHOT_PATH =
  process.env.ONBOARDING_HOME_SCREENSHOT ||
  SCREENSHOT_PATH.replace(/\.png$/, "-home.png");
const STUB_AUTH = envFlag("ONBOARDING_SMOKE_STUB_AUTH");
const STUB_ONBOARDING = process.env.ONBOARDING_SMOKE_STUB_ONBOARDING !== "0";

async function main() {
  const auth = STUB_AUTH
    ? createStubbedAuthenticatedContext()
    : await createAuthenticatedContext();
  const apiFailures = [];
  const pageErrors = [];
  const activationEventPosts = [];
  const activationStateRequests = [];
  const sampleProjectPosts = [];
  const apiRequests = [];
  const consoleMessages = [];
  const networkRequests = [];
  const requestFailures = [];
  const stubbedApiRequests = [];
  const evidence = {
    stub_auth: STUB_AUTH,
    stub_onboarding: STUB_ONBOARDING,
    viewport: VIEWPORT_NAME,
    existing_project: EXISTING_PROJECT,
    existing_trace: EXISTING_TRACE,
    open_sample_home: OPEN_SAMPLE_HOME,
    post_aha_home: POST_AHA_HOME,
    feature_disabled_home: FEATURE_DISABLED_HOME,
  };

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
    activationEventPosts,
    activationStateRequests,
    apiRequests,
    networkRequests,
    sampleProjectPosts,
    stubbedApiRequests,
  });
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
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
    if (isOnboardingSmokeApiUrl(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) {
      consoleMessages.push(`${message.type()}: ${message.text()}`);
    }
  });
  page.on("requestfailed", (request) => {
    requestFailures.push(
      `${request.method()} ${request.url()} ${request.failure()?.errorText}`,
    );
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    if (FEATURE_DISABLED_HOME) {
      await page.goto(`${APP_BASE}/dashboard/home?source=onboarding`, {
        waitUntil: "domcontentloaded",
      });
      await page.waitForFunction(
        () =>
          window.location.pathname === "/dashboard/home" &&
          new URLSearchParams(window.location.search).get("source") ===
            "onboarding",
        { timeout: 30000 },
      );

      await expectSelector(page, '[data-testid="onboarding-home-view"]');
      await expectVisibleText(page, "Start with the setup checklist", {
        exact: true,
      });
      await expectVisibleText(
        page,
        "The existing setup checklist is available for this workspace.",
        { exact: true },
      );
      await expectVisibleText(page, "Open Get Started", { exact: true });
      const getStartedHref = await visibleLinkHrefByText(
        page,
        "Open Get Started",
        { rootSelector: '[data-testid="onboarding-primary-action"]' },
      );
      assert(
        getStartedHref === "/dashboard/get-started",
        `Unexpected Get Started fallback CTA href: ${getStartedHref}`,
      );
      evidence.get_started_href = getStartedHref;
      await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
      evidence.screenshot = SCREENSHOT_PATH;
      evidence.activation_state_requests = activationStateRequests.length;
      assert(
        apiFailures.length === 0,
        `API failures: ${apiFailures.join("; ")}`,
      );
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
      return;
    }

    if (POST_AHA_HOME) {
      await page.goto(`${APP_BASE}/dashboard/home?source=onboarding`, {
        waitUntil: "domcontentloaded",
      });
      await page.waitForFunction(
        () =>
          window.location.pathname === "/dashboard/home" &&
          new URLSearchParams(window.location.search).get("source") ===
            "onboarding",
        { timeout: 30000 },
      );

      await expectSelector(page, '[data-testid="first-loop-complete-panel"]');
      await expectVisibleText(page, "Aha moment reached", { exact: true });
      await expectVisibleText(page, "Your first quality loop is live", {
        exact: true,
      });
      await expectVisibleText(
        page,
        "Open the current loop next. Daily quality will take over when a reviewable signal is available.",
        { exact: true },
      );
      const hasDailyQualityAction = await visibleActionExists(
        page,
        "Review daily quality",
        { rootSelector: '[data-testid="first-loop-complete-panel"]' },
      );
      assert(
        !hasDailyQualityAction,
        "Post-Aha fallback should not show a Daily Quality action when the route is unavailable.",
      );
      const openObserveHref = await visibleLinkHrefByText(
        page,
        "Open observe",
        {
          rootSelector: '[data-testid="first-loop-complete-panel"]',
        },
      );
      assert(
        openObserveHref === "/dashboard/observe/observe-1",
        `Unexpected post-Aha Observe CTA href: ${openObserveHref}`,
      );
      evidence.post_aha_open_observe_href = openObserveHref;
      await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
      evidence.screenshot = SCREENSHOT_PATH;
      evidence.activation_state_requests = activationStateRequests.length;
      assert(
        apiFailures.length === 0,
        `API failures: ${apiFailures.join("; ")}`,
      );
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
      return;
    }

    await page.goto(`${APP_BASE}/dashboard/home?source=setup_org`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(
      () =>
        window.location.pathname === "/dashboard/home" &&
        new URLSearchParams(window.location.search).get("source") ===
          "setup_org",
      { timeout: 30000 },
    );

    await expectSelector(page, '[data-testid="observe-setup-panel"]');
    await expectSelector(page, '[data-testid="sample-project-panel"]');
    await expectVisibleText(page, "Fastest path to Aha", { exact: true });
    await expectVisibleText(page, "Preview the quality loop first", {
      exact: true,
    });
    await expectVisibleText(page, "Quality issue", { exact: true });
    await expectVisibleText(page, "Connect one observe project", {
      exact: true,
    });
    await expectVisibleText(page, "Create observe project", { exact: true });
    await expectVisibleText(page, "Send one trace", { exact: true });
    await expectVisibleText(page, "Review the signal", { exact: true });
    await page.screenshot({ path: HOME_SCREENSHOT_PATH, fullPage: true });
    evidence.home_screenshot = HOME_SCREENSHOT_PATH;

    if (OPEN_SAMPLE_HOME) {
      await clickVisibleText(page, "Open sample trace", {
        rootSelector: '[data-testid="sample-project-panel"]',
      });
      await page.waitForFunction(
        () => {
          const params = new URLSearchParams(window.location.search);
          return (
            window.location.pathname ===
              "/dashboard/observe/observe-smoke-project/trace/trace-smoke-1" &&
            params.get("source") === "onboarding" &&
            params.get("onboarding") === "review-sample-trace" &&
            params.get("sample") === "true"
          );
        },
        { timeout: 30000 },
      );
      await waitForCondition(
        () =>
          sampleProjectPosts.some(
            (payload) =>
              payload?.path === "observe" &&
              payload?.source === "onboarding_home" &&
              payload?.reason === "connect_observability" &&
              payload?.open_after_create === true,
          ),
        "Sample project open request was not posted with the expected onboarding payload.",
        30000,
      );
      await waitForCondition(
        () =>
          stubbedApiRequests.some((entry) =>
            entry.includes("/tracer/trace/trace-smoke-1/"),
          ),
        "Sample trace detail was not requested after opening sample data.",
        30000,
      );
      await waitForCondition(
        () =>
          activationEventPosts.some(
            (payload) =>
              payload?.event_name === "sample_trace_detail_opened" &&
              payload?.primary_path === "sample" &&
              payload?.artifact_id === "trace-smoke-1" &&
              payload?.is_sample === true,
          ),
        "Sample trace detail activation event was not posted.",
        30000,
      );
      await expectVisibleText(page, "Sample trace review", { exact: true });
      await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
      evidence.screenshot = SCREENSHOT_PATH;
      evidence.sample_review_url = relativeUrl(page.url());
      evidence.sample_project_posts = sampleProjectPosts;
      evidence.activation_state_requests = activationStateRequests.length;
      evidence.activation_event_posts = activationEventPosts.map((payload) => ({
        event_name: payload.event_name,
        primary_path: payload.primary_path,
        stage: payload.stage,
        is_sample: payload.is_sample,
      }));
      evidence.trace_detail_requests = stubbedApiRequests.filter((entry) =>
        entry.includes("/tracer/trace/trace-smoke-1/"),
      );
      assert(
        apiFailures.length === 0,
        `API failures: ${apiFailures.join("; ")}`,
      );
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
      return;
    }

    const homeCtaHref = await visibleLinkHrefByText(
      page,
      "Connect observability",
      { rootSelector: '[data-testid="observe-setup-panel"]' },
    );
    assert(
      homeCtaHref === "/dashboard/observe?setup=true&source=onboarding",
      `Unexpected Home CTA href: ${homeCtaHref}`,
    );
    evidence.home_cta_href = homeCtaHref;

    await clickVisibleText(page, "Connect observability", {
      rootSelector: '[data-testid="observe-setup-panel"]',
    });
    await page.waitForFunction(
      () =>
        window.location.pathname === "/dashboard/observe" &&
        new URLSearchParams(window.location.search).get("setup") === "true" &&
        new URLSearchParams(window.location.search).get("source") ===
          "onboarding",
      { timeout: 30000 },
    );

    await expectSelector(page, '[data-testid="observe-onboarding-focus"]');
    await expectVisibleText(page, "Observe onboarding", { exact: true });
    await expectVisibleText(page, "Connect Observe to your app", {
      exact: true,
    });
    const observeSetupActionTexts = await visibleActionTexts(page, {
      rootSelector: '[data-testid="observe-onboarding-focus"]',
    });
    if (!EXISTING_PROJECT) {
      assert(
        observeSetupActionTexts.includes("Open sample trace"),
        `Expected sample trace to remain available. Actions: ${observeSetupActionTexts.join(
          ", ",
        )}`,
      );
      assert(
        observeSetupActionTexts.at(-1) === "Review setup",
        `Expected real setup to remain primary. Actions: ${observeSetupActionTexts.join(
          ", ",
        )}`,
      );
      evidence.observe_setup_actions = observeSetupActionTexts;
    }
    if (EXISTING_PROJECT) {
      await expectVisibleText(page, "Open first trace step", { exact: true });
      await clickVisibleText(page, "Open first trace step", {
        rootSelector: '[data-testid="observe-onboarding-focus"]',
      });
      await page.waitForFunction(
        () => {
          const params = new URLSearchParams(window.location.search);
          return (
            window.location.pathname ===
              "/dashboard/observe/observe-smoke-project/llm-tracing" &&
            params.get("source") === "onboarding" &&
            params.get("onboarding") === "send-first-trace" &&
            params.get("selectedTab") === "trace"
          );
        },
        { timeout: 30000 },
      );
      evidence.first_trace_step_url = relativeUrl(page.url());
      await expectSelector(page, '[data-testid="observe-onboarding-focus"]');
      const firstTraceStepActions = await visibleActionTexts(page, {
        rootSelector: '[data-testid="observe-onboarding-focus"]',
      });
      assert(
        firstTraceStepActions.includes("Refresh traces"),
        `Expected refresh action to remain available. Actions: ${firstTraceStepActions.join(
          ", ",
        )}`,
      );
      assert(
        firstTraceStepActions.at(-1) ===
          (EXISTING_TRACE ? "Review trace" : "Open setup"),
        `Unexpected first-trace primary action. Actions: ${firstTraceStepActions.join(
          ", ",
        )}`,
      );
      evidence.first_trace_step_actions = firstTraceStepActions;
      if (EXISTING_TRACE) {
        await expectVisibleText(page, "First trace received", {
          exact: true,
        });
        await expectVisibleText(page, "Review trace", { exact: true });
        await clickVisibleText(page, "Review trace", {
          rootSelector: '[data-testid="observe-onboarding-focus"]',
        });
        await page.waitForFunction(
          () => {
            const params = new URLSearchParams(window.location.search);
            return (
              window.location.pathname ===
                "/dashboard/observe/observe-smoke-project/trace/trace-smoke-1" &&
              params.get("source") === "onboarding" &&
              params.get("onboarding") === "review-first-trace"
            );
          },
          { timeout: 30000 },
        );
        await waitForCondition(
          () =>
            stubbedApiRequests.some((entry) =>
              entry.includes("/tracer/trace/list_traces_of_session/"),
            ),
          "Trace list was not requested for first trace verification.",
          30000,
        );
        evidence.first_trace_review_url = relativeUrl(page.url());
      } else {
        await expectVisibleText(page, "Send the first trace", { exact: true });
      }
      await waitForCondition(
        () =>
          activationEventPosts.some(
            (payload) =>
              payload?.event_name === "onboarding_observe_route_focus_viewed" &&
              payload?.primary_path === "observe" &&
              payload?.stage === "waiting_for_first_trace" &&
              payload?.project_id === "observe-smoke-project" &&
              payload?.metadata?.route_mode === "send-first-trace",
          ),
        "Observe first trace step activation event was not posted.",
        30000,
      );
    } else {
      await expectVisibleText(page, "Checking for your first trace", {
        exact: true,
      });
      await expectVisibleText(page, "Install Dependencies", { exact: true });
      await expectVisibleText(page, "Setup Telemetry", { exact: true });
      await expectVisibleText(page, "Setup Instrumentation", { exact: true });
    }
    await waitForNoVisibleText(page, "Invalid Date");

    await waitForCondition(
      () =>
        activationEventPosts.some(
          (payload) =>
            payload?.event_name === "onboarding_observe_route_focus_viewed" &&
            payload?.primary_path === "observe" &&
            payload?.stage === "connect_observability" &&
            payload?.metadata?.route_mode === "setup-observe",
        ),
      "Observe setup focus activation event was not posted.",
    );

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;
    evidence.activation_state_requests = activationStateRequests.length;
    evidence.activation_event_posts = activationEventPosts.map((payload) => ({
      event_name: payload.event_name,
      primary_path: payload.primary_path,
      stage: payload.stage,
      route_mode: payload.metadata?.route_mode,
    }));
    evidence.trace_list_requests = stubbedApiRequests.filter((entry) =>
      entry.includes("/tracer/trace/list_traces_of_session/"),
    );

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
  } catch (error) {
    const diagnostic = await captureFailureDiagnostic(page);
    diagnostic.activation_state_requests = activationStateRequests;
    diagnostic.activation_event_posts = activationEventPosts;
    diagnostic.sample_project_posts = sampleProjectPosts;
    diagnostic.api_requests = apiRequests;
    diagnostic.network_requests = networkRequests.slice(-80);
    diagnostic.request_failures = requestFailures.slice(-20);
    diagnostic.console_messages = consoleMessages.slice(-20);
    diagnostic.stubbed_api_requests = stubbedApiRequests;
    console.error(
      JSON.stringify(
        {
          status: "failed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          diagnostic,
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
    activationEventPosts,
    activationStateRequests,
    apiRequests,
    networkRequests,
    sampleProjectPosts,
    stubbedApiRequests,
  },
) {
  await page.setRequestInterception(true);
  page.on("request", async (request) => {
    const url = new URL(request.url());
    const path = url.pathname;
    const normalizedPath = slashPath(path);
    if (
      ["document", "fetch", "xhr", "script"].includes(request.resourceType())
    ) {
      networkRequests.push(
        `${request.resourceType()} ${request.method()} ${url.origin}${normalizedPath}${url.search}`,
      );
    }
    if (isApiPath(normalizedPath)) {
      apiRequests.push(`${request.method()} ${normalizedPath}`);
    }

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

    if (STUB_AUTH && normalizedPath === "/accounts/user-info/") {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
      await respondJson(request, auth.user);
      return;
    }

    if (STUB_AUTH && normalizedPath === "/accounts/organization/list/") {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
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

    if (STUB_AUTH && normalizedPath === "/accounts/workspace/list/") {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
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

    if (STUB_ONBOARDING && normalizedPath === "/accounts/activation-state/") {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
      activationStateRequests.push(Object.fromEntries(url.searchParams));
      await respondJson(request, {
        status: true,
        result: stubbedActivationState(auth),
      });
      return;
    }

    if (STUB_ONBOARDING && normalizedPath === "/accounts/activation-events/") {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
      const payload = parseJsonPostData(request.postData());
      activationEventPosts.push(payload);
      await respondJson(request, {
        status: true,
        result: {
          event_id: "00000000-0000-4000-8000-000000000188",
          event_name: payload?.event_name || "onboarding_home_viewed",
          activation_state: stubbedActivationState(auth),
        },
      });
      return;
    }

    if (STUB_ONBOARDING && normalizedPath === "/accounts/sample-project/") {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
      const payload = parseJsonPostData(request.postData());
      sampleProjectPosts.push(payload);
      await respondJson(request, {
        status: true,
        result: sampleProjectOpenResponse(auth),
      });
      return;
    }

    if (
      STUB_ONBOARDING &&
      normalizedPath === "/tracer/project/list_projects/"
    ) {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
      await respondJson(request, {
        status: true,
        result: {
          metadata: {
            total_rows: EXISTING_PROJECT ? 1 : 0,
            page_number: 0,
            page_size: 25,
          },
          projects: EXISTING_PROJECT
            ? [
                {
                  id: "observe-distractor-project",
                  name: "Older observe project",
                  project_type: "observe",
                  source: "prototype",
                  trace_type: "observe",
                },
                {
                  id: "observe-smoke-project",
                  name: "Observe smoke project",
                  project_type: "observe",
                  source: "prototype",
                  trace_type: "observe",
                },
              ]
            : [],
        },
      });
      return;
    }

    if (
      STUB_ONBOARDING &&
      normalizedPath === "/tracer/project/project_sdk_code/"
    ) {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
      await respondJson(request, {
        status: true,
        result: observeSetupCodeBlock(),
      });
      return;
    }

    if (
      STUB_ONBOARDING &&
      normalizedPath === "/tracer/project/observe-smoke-project/"
    ) {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
      await respondJson(request, {
        status: true,
        result: {
          id: "observe-smoke-project",
          name: "Observe smoke project",
          project_type: "observe",
          trace_type: "observe",
          source: "prototype",
        },
      });
      return;
    }

    if (
      STUB_ONBOARDING &&
      normalizedPath === "/tracer/project/list_project_ids/"
    ) {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
      await respondJson(request, {
        status: true,
        result: {
          projects: EXISTING_PROJECT
            ? [
                {
                  id: "observe-distractor-project",
                  name: "Older observe project",
                  trace_type: "observe",
                },
                {
                  id: "observe-smoke-project",
                  name: "Observe smoke project",
                  trace_type: "observe",
                },
              ]
            : [],
        },
      });
      return;
    }

    if (STUB_ONBOARDING && normalizedPath === "/tracer/dashboard/metrics/") {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
      await respondJson(request, dashboardMetricsResponse());
      return;
    }

    if (
      STUB_ONBOARDING &&
      normalizedPath === "/tracer/trace/get_graph_methods/"
    ) {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
      await respondJson(request, {
        status: true,
        result: {
          data: [],
        },
      });
      return;
    }

    if (STUB_ONBOARDING && normalizedPath === "/tracer/saved-views/") {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
      await respondJson(request, {
        status: true,
        result: {
          custom_views: [],
          customViews: [],
        },
      });
      return;
    }

    if (
      STUB_ONBOARDING &&
      normalizedPath === "/tracer/trace/list_traces_of_session/"
    ) {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
      await respondJson(request, {
        status: true,
        result: {
          config: traceListConfig(),
          metadata: {
            total_rows: EXISTING_TRACE ? 1 : 0,
            page_number: 0,
            page_size: 100,
          },
          table: EXISTING_TRACE ? [traceListRow()] : [],
        },
      });
      return;
    }

    if (
      STUB_ONBOARDING &&
      normalizedPath === "/tracer/observation-span/get_eval_attributes_list/"
    ) {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
      await respondJson(request, {
        status: true,
        result: [],
      });
      return;
    }

    if (STUB_ONBOARDING && normalizedPath === "/tracer/trace/trace-smoke-1/") {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
      await respondJson(request, traceDetailResponse());
      return;
    }

    await request.continue();
  });
}

function stubbedActivationState(auth, { firstTraceReady = false } = {}) {
  const fixtureName = FEATURE_DISABLED_HOME
    ? "featureDisabled"
    : POST_AHA_HOME
      ? "observeFirstLoopComplete"
      : firstTraceReady
        ? "observeFirstTraceReady"
        : "observeNoSetup";
  const activationState = getActivationStateFixture(fixtureName);
  if (FEATURE_DISABLED_HOME || POST_AHA_HOME) {
    return {
      ...activationState,
      request_id: "onboarding_home_observe_smoke",
      organization_id: auth.organizationId,
      workspace_id: auth.workspaceId,
      user_id: auth.user.id,
      route_availability: POST_AHA_HOME
        ? {
            ...activationState.route_availability,
            daily_quality_home: {
              href: "/dashboard/home?mode=daily-quality",
              is_available: false,
              isAvailable: false,
              reason: "feature_disabled",
            },
          }
        : activationState.route_availability,
    };
  }
  const firstTraceReviewHref =
    "/dashboard/observe/observe-smoke-project/trace/trace-smoke-1?source=onboarding&onboarding=review-first-trace";
  const recommendedAction =
    firstTraceReady && activationState.recommended_action
      ? {
          ...activationState.recommended_action,
          href: firstTraceReviewHref,
        }
      : activationState.recommended_action;
  const routeAvailability = firstTraceReady
    ? {
        ...activationState.route_availability,
        observe_trace_detail: {
          ...activationState.route_availability?.observe_trace_detail,
          href: firstTraceReviewHref,
        },
      }
    : activationState.route_availability;

  return {
    ...activationState,
    recommended_action: recommendedAction,
    route_availability: routeAvailability,
    request_id: "onboarding_home_observe_smoke",
    organization_id: auth.organizationId,
    workspace_id: auth.workspaceId,
    user_id: auth.user.id,
    signals: {
      ...activationState.signals,
      observe_projects: EXISTING_PROJECT
        ? 2
        : activationState.signals?.observe_projects,
      traces: firstTraceReady ? 1 : activationState.signals?.traces,
      first_observe_id: EXISTING_PROJECT
        ? "observe-smoke-project"
        : activationState.signals?.first_observe_id,
      first_trace_id: firstTraceReady
        ? "trace-smoke-1"
        : activationState.signals?.first_trace_id,
    },
  };
}

function sampleProjectOpenResponse(auth) {
  const activationState = getActivationStateFixture("observeWaitingWithSample");
  const entryRoute =
    "/dashboard/observe/observe-smoke-project/trace/trace-smoke-1?source=onboarding&onboarding=review-sample-trace&sample=true";

  return {
    sample_project: {
      available: true,
      created: true,
      status: "ready_for_observe",
      href: entryRoute,
      version: "sample-observe-v1",
      is_hidden: false,
      hidden_reason: null,
      manifest_id: "observe-quality-loop",
      manifest_version: "2026-05-26.1",
      label: "Sample",
      entry_route: entryRoute,
      entry_routes: [entryRoute],
      missing_artifacts: [],
      last_opened_at: "2026-05-29T05:00:00Z",
      real_setup_href: "/dashboard/observe?setup=true&source=onboarding",
    },
    activation_state: {
      ...activationState,
      request_id: "onboarding_home_sample_open_smoke",
      organization_id: auth.organizationId,
      workspace_id: auth.workspaceId,
      user_id: auth.user.id,
      sample_project: {
        ...activationState.sample_project,
        created: true,
        status: "ready_for_observe",
        href: entryRoute,
        entry_route: entryRoute,
        entry_routes: [entryRoute],
      },
    },
  };
}

function observeSetupCodeBlock() {
  return {
    installationGuide: {
      Python: "pip install futureagi",
      TypeScript: "npm install futureagi",
    },
    keys: {
      Python: "FUTURE_AGI_API_KEY='your-key'",
      TypeScript: "const apiKey = 'your-key';",
    },
    projectAddCode: {
      Python: "from futureagi import trace\ntrace('first-request')",
      TypeScript: "import { trace } from 'futureagi';\ntrace('first-request');",
    },
    instruments: {
      openai: {
        name: "OpenAI",
        logo: "/favicon/logo.svg",
        Python: {
          code: "from futureagi import instrument_openai\ninstrument_openai()",
          github: "https://github.com/future-agi",
        },
        TypeScript: {
          code: "import { instrumentOpenAI } from 'futureagi';\ninstrumentOpenAI();",
          github: "https://github.com/future-agi",
        },
      },
    },
  };
}

function traceListConfig() {
  return [
    {
      id: "trace_id",
      name: "Trace ID",
      type: "string",
      is_visible: true,
    },
    {
      id: "name",
      name: "Name",
      type: "string",
      is_visible: true,
    },
    {
      id: "latency_ms",
      name: "Latency",
      type: "number",
      is_visible: true,
    },
  ];
}

function dashboardMetricsResponse() {
  return {
    status: true,
    result: {
      metrics: [
        {
          category: "system_metric",
          displayName: "Latency",
          name: "latency",
          type: "number",
        },
        {
          category: "system_metric",
          displayName: "Tokens",
          name: "tokens",
          type: "number",
        },
      ],
    },
  };
}

function traceListRow() {
  return {
    trace_id: "trace-smoke-1",
    name: "First checkout trace",
    project_id: "observe-smoke-project",
    start_time: "2026-05-26T15:04:00Z",
    end_time: "2026-05-26T15:04:01Z",
    latency_ms: 420,
    total_tokens: 42,
    total_cost: 0.00042,
    status: "OK",
  };
}

function traceDetailResponse() {
  return {
    status: true,
    result: {
      trace: {
        id: "trace-smoke-1",
        project: "observe-smoke-project",
        name: "First checkout trace",
        input: {
          prompt: "Summarize the customer request.",
        },
        output: {
          answer: "The customer needs setup guidance.",
        },
        external_id: "first-checkout-trace",
        tags: [],
      },
      observation_spans: [],
      summary: {
        total_spans: 0,
        total_duration_ms: 420,
        total_tokens: 42,
        total_cost: 0.00042,
      },
      graph: {
        nodes: [],
        edges: [],
      },
    },
  };
}

function createStubbedAuthenticatedContext() {
  const userId = "00000000-0000-4000-8000-000000000101";
  const organizationId = "00000000-0000-4000-8000-000000000201";
  const workspaceId = "00000000-0000-4000-8000-000000000301";
  const user = {
    id: userId,
    email: "onboarding-smoke@example.com",
    name: "Onboarding Smoke",
    remember_me: true,
    onboarding_completed: true,
    requires_org_setup: false,
    organization_id: organizationId,
    organization_role: "Owner",
    org_level: 4,
    default_workspace_id: workspaceId,
    default_workspace_name: "Onboarding smoke workspace",
    default_workspace_display_name: "Onboarding smoke workspace",
    default_workspace_role: "Owner",
    ws_level: 4,
    goals: ["monitor_production_ai_app"],
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

function relativeUrl(value) {
  const url = new URL(value);
  return `${url.pathname}${url.search}`;
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

async function waitForNoVisibleText(page, text, { timeout = 10000 } = {}) {
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
      return !Array.from(document.querySelectorAll("body *")).some(
        (element) =>
          isVisible(element) && element.textContent?.includes(expectedText),
      );
    },
    { timeout },
    text,
  );
}

async function visibleLinkHrefByText(page, text, { rootSelector } = {}) {
  await expectVisibleText(page, text, { exact: true });
  return page.evaluate(
    ({ expectedText, selector }) => {
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
      const root = selector ? document.querySelector(selector) : document.body;
      const element = Array.from(
        root?.querySelectorAll("a[href],button,[role='button']") || [],
      ).find(
        (candidate) =>
          isVisible(candidate) &&
          normalized(candidate.textContent) === expectedText,
      );
      const link = element?.tagName === "A" ? element : element?.closest("a");
      return link?.getAttribute("href") || null;
    },
    { expectedText: text, selector: rootSelector },
  );
}

async function visibleActionExists(page, text, { rootSelector } = {}) {
  return page.evaluate(
    ({ expectedText, selector }) => {
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
      const root = selector ? document.querySelector(selector) : document.body;
      return Array.from(
        root?.querySelectorAll("a[href],button,[role='button']") || [],
      ).some(
        (candidate) =>
          isVisible(candidate) &&
          normalized(candidate.textContent) === expectedText,
      );
    },
    { expectedText: text, selector: rootSelector },
  );
}

async function visibleActionTexts(page, { rootSelector } = {}) {
  return page.evaluate((selector) => {
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
    const root = selector ? document.querySelector(selector) : document.body;
    return Array.from(
      root?.querySelectorAll("a[href],button,[role='button']") || [],
    )
      .filter(isVisible)
      .map((element) => normalized(element.textContent))
      .filter(Boolean);
  }, rootSelector);
}

async function clickVisibleText(page, text, { rootSelector } = {}) {
  await expectVisibleText(page, text, { exact: true });
  const clicked = await page.evaluate(
    ({ expectedText, selector }) => {
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
      const root = selector ? document.querySelector(selector) : document.body;
      const element = Array.from(
        root?.querySelectorAll("button,[role='button'],a") || [],
      ).find(
        (candidate) =>
          isVisible(candidate) &&
          normalized(candidate.textContent) === expectedText,
      );
      const clickable = element?.closest("button,[role='button'],a") || element;
      if (!clickable) return false;
      clickable.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
      clickable.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      clickable.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      return true;
    },
    { expectedText: text, selector: rootSelector },
  );
  assert(clicked, `Could not click visible text ${text}.`);
}

async function waitForCondition(fn, message, timeout = 10000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeout) {
    if (fn()) return;
    await new Promise((resolve) => {
      setTimeout(resolve, 100);
    });
  }
  throw new Error(message);
}

async function captureFailureDiagnostic(page) {
  try {
    return {
      url: page.url(),
      title: await page.title(),
      body_text: String(
        await page.evaluate(() => document.body?.innerText || ""),
      ).slice(0, 1200),
    };
  } catch (error) {
    return {
      error: error.message,
    };
  }
}

function isStubbedApiPath(path) {
  return (
    path.startsWith("/accounts/") ||
    path.startsWith("/tracer/project/") ||
    path.startsWith("/tracer/dashboard/") ||
    path.startsWith("/tracer/saved-views/") ||
    path.startsWith("/tracer/trace/") ||
    path.startsWith("/tracer/observation-span/get_eval_attributes_list/")
  );
}

function isApiPath(path) {
  return (
    path.startsWith("/accounts/") ||
    path.startsWith("/tracer/") ||
    path.startsWith("/usage/")
  );
}

function isOnboardingSmokeApiUrl(url) {
  return (
    url.includes("/accounts/activation-state/") ||
    url.includes("/accounts/activation-events/") ||
    url.includes("/tracer/project/list_projects/") ||
    url.includes("/tracer/project/project_sdk_code/") ||
    url.includes("/tracer/project/observe-smoke-project/") ||
    url.includes("/tracer/project/list_project_ids/") ||
    url.includes("/tracer/dashboard/metrics/") ||
    url.includes("/tracer/trace/get_graph_methods/") ||
    url.includes("/tracer/trace/trace-smoke-1/") ||
    url.includes("/tracer/saved-views/") ||
    url.includes("/tracer/trace/list_traces_of_session/") ||
    url.includes("/tracer/observation-span/get_eval_attributes_list/")
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
