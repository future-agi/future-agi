import { Buffer } from "node:buffer";
import { mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname } from "node:path";
import process from "node:process";
import { assert, envFlag } from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3035";
const API_BASE = process.env.API_BASE || "http://127.0.0.1:8011";
const VIEWPORT_NAME = process.env.ONBOARDING_SMOKE_VIEWPORT || "desktop";
const VIEWPORT = viewportForName(VIEWPORT_NAME);
const SCREENSHOT_PATH =
  process.env.SIGNUP_QUICK_START_SCREENSHOT ||
  `/tmp/signup-quick-start-smoke-${VIEWPORT_NAME}${
    envFlag("ONBOARDING_REAL_SIGNUP_SAMPLE_ONLY") ? "-sample-open" : ""
  }.png`;
const REQUIRE_REAL_SIGNUP = envFlag("ONBOARDING_REAL_SIGNUP");
const ALLOW_REMOTE = envFlag("ONBOARDING_REAL_SIGNUP_ALLOW_REMOTE");
const SAMPLE_ONLY = envFlag("ONBOARDING_REAL_SIGNUP_SAMPLE_ONLY");
const REPORT_OUTPUT = process.env.ONBOARDING_SMOKE_REPORT_OUTPUT || "";
const OBSERVE_QUICK_START_PARAMS = {
  source: "setup_org",
  quick_start_id: "observe",
  quick_start_goal: "monitor_production_ai_app",
  quick_start_primary_path: "observe",
};
const OBSERVE_QUICK_START_METADATA = {
  quick_start_id: OBSERVE_QUICK_START_PARAMS.quick_start_id,
  quick_start_goal: OBSERVE_QUICK_START_PARAMS.quick_start_goal,
  quick_start_primary_path: OBSERVE_QUICK_START_PARAMS.quick_start_primary_path,
};
const OBSERVE_DIRECT_HANDOFF_PARAMS = {
  setup: "true",
  source: "onboarding",
  tour_anchor: "observe_create_project_button",
  journey_step: "connect_observability",
  ...OBSERVE_QUICK_START_METADATA,
};
const SAMPLE_QUICK_START_METADATA = {
  quick_start_id: "sample_preview",
  quick_start_goal: "explore_sample_data",
  quick_start_primary_path: "sample",
};

async function main() {
  assert(REQUIRE_REAL_SIGNUP, "Set ONBOARDING_REAL_SIGNUP=1 for this smoke.");
  assertLocalUrl(APP_BASE, "APP_BASE");
  assertLocalUrl(API_BASE, "API_BASE");
  const smokeMode = SAMPLE_ONLY ? "sample_open" : "full_quality_loop";

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
    evalPlaygroundRequests: [],
    evalPlaygroundResponses: [],
    evalTemplateRequests: [],
    evalTemplateResponses: [],
    evalUsageResponses: [],
    onboardingPosts: [],
    posthogEvents: [],
    sampleProjectPosts: [],
    sampleProjectResponses: [],
    setupPosts: [],
    signupPosts: [],
    tokenPosts: [],
    traceDetailRequests: [],
  };
  const pageErrors = [];
  const preflight = await preflightRealSignupTargets([
    { name: "app", url: APP_BASE },
    { name: "api", url: API_BASE },
  ]);
  const unavailableTarget = preflight.find((target) => !target.reachable);
  if (unavailableTarget) {
    const failureReason =
      unavailableTarget.error || `HTTP ${unavailableTarget.status}`;
    const report = smokeReportPayload({
      status: "failed",
      mode: smokeMode,
      diagnostic: {
        error_message: `Preflight failed for ${unavailableTarget.name} at ${unavailableTarget.url}: ${failureReason}`,
        preflight,
      },
    });
    console.error(JSON.stringify(report, null, 2));
    await writeSmokeReport(report);
    throw new Error(report.diagnostic.error_message);
  }

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: VIEWPORT,
    args: ["--no-sandbox"],
  });

  const page = await browser.newPage();
  await page.setViewport(VIEWPORT);
  await page.evaluateOnNewDocument((apiBase) => {
    window.__FUTURE_AGI_CONFIG__ = {
      ...(window.__FUTURE_AGI_CONFIG__ || {}),
      VITE_HOST_API: apiBase,
    };
  }, API_BASE);
  await page.setCacheEnabled(false);
  await page.setBypassServiceWorker(true);
  await page.setRequestInterception(true);
  const appOrigin = new URL(APP_BASE).origin;
  const apiOrigin = new URL(API_BASE).origin;
  page.on("request", (request) => {
    const url = safeUrl(request.url());
    if (url?.origin === appOrigin && url.pathname === "/config.js") {
      request.respond({
        status: 200,
        contentType: "application/javascript",
        body: `window.__FUTURE_AGI_CONFIG__ = { VITE_HOST_API: ${JSON.stringify(API_BASE)} };`,
      });
      return;
    }
    if (isPostHogCaptureRequest(url, request.method())) {
      evidence.posthogEvents.push(...postHogEventsFromRequest(request));
    }
    if (!url || url.origin !== apiOrigin) {
      request.continue();
      return;
    }
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
    if (isEvalTemplatePath(path) && isMutationMethod(request.method())) {
      evidence.evalTemplateRequests.push({
        method: request.method(),
        path,
        payload: summarizeEvalTemplatePayload(
          parseJsonPostData(request.postData()),
        ),
      });
    }
    if (path === "/model-hub/eval-playground/" && request.method() === "POST") {
      evidence.evalPlaygroundRequests.push(
        summarizeEvalPlaygroundPayload(parseJsonPostData(request.postData())),
      );
    }
    if (/^\/tracer\/trace\/[^/]+\/$/.test(path) && request.method() === "GET") {
      evidence.traceDetailRequests.push(path);
    }
    request.continue();
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
    if (
      url &&
      url.origin === new URL(API_BASE).origin &&
      isEvalTemplatePath(path) &&
      isMutationMethod(response.request().method())
    ) {
      const item = {
        method: response.request().method(),
        path,
        status: response.status(),
      };
      try {
        item.body = await response.json();
      } catch {
        item.body = { parse_error: true };
      }
      evidence.evalTemplateResponses.push(item);
    }
    if (
      url &&
      url.origin === new URL(API_BASE).origin &&
      path === "/model-hub/eval-playground/" &&
      response.request().method() === "POST"
    ) {
      const item = {
        status: response.status(),
      };
      try {
        item.body = summarizeEvalPlaygroundResponse(await response.json());
      } catch {
        item.body = { parse_error: true };
      }
      evidence.evalPlaygroundResponses.push(item);
    }
    if (
      url &&
      url.origin === new URL(API_BASE).origin &&
      /^\/model-hub\/eval-templates\/[^/]+\/usage\/$/.test(path) &&
      response.request().method() === "GET" &&
      response.status() < 400
    ) {
      const item = {
        path,
        status: response.status(),
      };
      try {
        item.body = summarizeEvalUsageResponse(await response.json());
      } catch {
        item.body = { parse_error: true };
      }
      evidence.evalUsageResponses.push(item);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await page.goto(`${APP_BASE}/auth/jwt/register`, {
      waitUntil: "domcontentloaded",
    });
    await expectVisibleText(page, "Create an account");

    await page.waitForSelector('input[placeholder="Enter fullname"]', {
      visible: true,
      timeout: 30000,
    });
    await page.waitForSelector('input[placeholder="Enter Email address"]', {
      visible: true,
      timeout: 30000,
    });
    await page.waitForSelector('input[placeholder="Create password"]', {
      visible: true,
      timeout: 30000,
    });
    await fillVisibleInput(
      page,
      'input[placeholder="Enter fullname"]',
      user.fullName,
    );
    await fillVisibleInput(
      page,
      'input[placeholder="Enter Email address"]',
      user.email,
    );
    await fillVisibleInput(
      page,
      'input[placeholder="Create password"]',
      user.password,
    );
    await clickVisibleButtonText(page, "Create account and continue");

    await expectVisibleText(page, "Start with your first quality loop", {
      timeout: 90000,
    });
    await waitForBrowserFrame();
    await clickVisibleButtonText(
      page,
      SAMPLE_ONLY
        ? "Preview sample trace first"
        : "Connect observability first",
    );

    const setupOrgHomeUrl = null;
    let setupOrgEntryUrl = null;
    if (SAMPLE_ONLY) {
      await waitForSampleTraceRoute(page, { timeout: 45000 });
      setupOrgEntryUrl = relativeUrl(page.url());
      assert(
        hasSampleQuickStartParams(setupOrgEntryUrl),
        `Expected sample trace URL quick-start attribution, got ${setupOrgEntryUrl}`,
      );
    } else {
      await page.waitForFunction(
        () => {
          const params = new URLSearchParams(window.location.search);
          return (
            window.location.pathname === "/dashboard/observe" &&
            params.get("setup") === "true" &&
            params.get("source") === "onboarding" &&
            params.get("tour_anchor") === "observe_create_project_button" &&
            params.get("journey_step") === "connect_observability" &&
            params.get("quick_start_id") === "observe" &&
            params.get("quick_start_goal") === "monitor_production_ai_app" &&
            params.get("quick_start_primary_path") === "observe"
          );
        },
        { timeout: 45000 },
      );
      setupOrgEntryUrl = relativeUrl(page.url());
      assert(
        hasObserveDirectHandoffParams(setupOrgEntryUrl),
        `Expected setup-org direct handoff URL, got ${setupOrgEntryUrl}`,
      );
      assert(
        new URL(page.url()).pathname !== "/dashboard/home",
        `Expected setup quick-start to hand off directly, got ${page.url()}`,
      );
      await expectNoVisibleText(page, "Invite your team later", {
        timeout: 1000,
      });
    }

    const observeCtaHref = null;
    let observeSetupUrl = null;
    let sampleTraceUrl = setupOrgEntryUrl;
    if (!SAMPLE_ONLY) {
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
      await expectVisibleText(page, "Setup Instrumentation", {
        timeout: 45000,
      });
      await expectNoVisibleText(page, "Code not available");
      observeSetupUrl = page.url();
      await expectVisibleText(page, "Open sample trace", { timeout: 45000 });
      await clickVisibleButtonText(page, "Open sample trace", 45000);
      await waitForSampleTraceRoute(page, { timeout: 45000 });
      sampleTraceUrl = relativeUrl(page.url());
    }
    await expectVisibleText(page, "Trace", { exact: true, timeout: 45000 });
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
    if (SAMPLE_ONLY) {
      const authState = await page.evaluate(() => ({
        accessToken: localStorage.getItem("accessToken"),
        initialRender: localStorage.getItem("initial-render"),
        organizationId: sessionStorage.getItem("organizationId"),
        redirectUrl: localStorage.getItem("redirectUrl"),
        workspaceId: sessionStorage.getItem("workspaceId"),
      }));
      const browserState = {
        initialRender: authState.initialRender,
        organizationId: authState.organizationId,
        redirectUrl: authState.redirectUrl,
        workspaceId: authState.workspaceId,
      };
      const apiHeaders = authenticatedApiHeaders(authState);
      const sampleOpenState = await fetchSmokeActivationState(
        apiHeaders,
        "sample_open_smoke",
      );
      assert(
        sampleOpenState.is_activated === false,
        `Sample open must not activate the user; got ${sampleOpenState.is_activated}`,
      );
      assert(
        (sampleOpenState.signals?.observe_projects || 0) === 0,
        `Sample open must not count as a real observe project; got ${sampleOpenState.signals?.observe_projects}`,
      );
      assert(
        (sampleOpenState.signals?.traces || 0) === 0,
        `Sample open must not count as a real trace; got ${sampleOpenState.signals?.traces}`,
      );
      assert(
        !sampleOpenState.signals?.first_observe_id,
        `Sample open must not set first_observe_id; got ${sampleOpenState.signals?.first_observe_id}`,
      );
      assert(
        !sampleOpenState.signals?.first_trace_id,
        `Sample open must not set first_trace_id; got ${sampleOpenState.signals?.first_trace_id}`,
      );
      assert(
        sampleOpenState.sample_project?.created === true,
        "Expected sample project to be created after opening sample trace.",
      );
      assert(
        sampleOpenState.sample_project?.entry_route ||
          sampleOpenState.sample_project?.entry_routes?.length,
        "Expected sample project activation state to expose an entry route.",
      );
      assert(
        evidence.onboardingPosts[0]?.goals?.includes(
          "Explore with sample data",
        ),
        `Expected sample preview quick-start goal, got ${JSON.stringify(
          evidence.onboardingPosts[0]?.goals,
        )}`,
      );
      assert(
        evidence.activationEventPosts.some(
          (payload) =>
            payload?.event_name === "sample_trace_detail_opened" &&
            payload?.primary_path === "sample" &&
            payload?.stage === "review_first_trace" &&
            payload?.is_sample === true &&
            hasSampleQuickStartMetadata(payload),
        ),
        "Expected sample trace detail activation event.",
      );
      assert(
        evidence.sampleProjectPosts[0]?.source === "setup_org",
        `Expected sample source setup_org, got ${evidence.sampleProjectPosts[0]?.source}`,
      );
      assert(
        evidence.sampleProjectPosts[0]?.reason === "sample_preview",
        `Expected sample reason sample_preview, got ${evidence.sampleProjectPosts[0]?.reason}`,
      );
      assert(
        evidence.sampleProjectPosts[0]?.open_after_create === true,
        `Expected sample open_after_create=true, got ${evidence.sampleProjectPosts[0]?.open_after_create}`,
      );
      assert(
        hasSampleQuickStartParams(evidence.sampleProjectPosts[0]),
        `Expected sample project POST quick-start attribution, got ${JSON.stringify(
          evidence.sampleProjectPosts[0],
        )}`,
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
        evidence.apiFailures.length === 0,
        `API failures: ${evidence.apiFailures.join("; ")}`,
      );
      assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

      await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
      const report = smokeReportPayload({
        status: "passed",
        mode: smokeMode,
        evidence: {
          activation_state_requests: evidence.activationStateRequests,
          browser_state: browserState,
          email: user.email,
          onboarding_post: evidence.onboardingPosts[0],
          observe_cta_href: observeCtaHref,
          observe_setup_url: observeSetupUrl,
          sample_open_state: summarizeActivationState(sampleOpenState),
          sample_project_post: evidence.sampleProjectPosts[0],
          sample_project_response: evidence.sampleProjectResponses[0],
          sample_trace_entry: {
            clicks_after_quick_start: 0,
            quick_start_goal: "explore_sample_data",
            quick_start_id: "sample_preview",
            quick_start_primary_path: "sample",
            source: "setup_org",
          },
          setup_quick_start: "sample_preview",
          sample_trace_activation_event: evidence.activationEventPosts.find(
            (payload) => payload?.event_name === "sample_trace_detail_opened",
          ),
          sample_trace_url: sampleTraceUrl,
          setup_org_entry_url: setupOrgEntryUrl,
          setup_org_home_url: setupOrgHomeUrl,
          screenshot: SCREENSHOT_PATH,
          signup_post: evidence.signupPosts[0],
          token_post: evidence.tokenPosts[0],
        },
      });
      await writeSmokeReport(report);
      console.log(JSON.stringify(report, null, 2));
      return;
    }
    await clickVisibleButtonText(page, "Connect your app", 45000);
    await page.waitForFunction(
      () =>
        window.location.pathname === "/dashboard/observe" &&
        new URLSearchParams(window.location.search).get("setup") === "true" &&
        new URLSearchParams(window.location.search).get("source") ===
          "sample_trace_review",
      { timeout: 45000 },
    );
    await expectVisibleTestId(page, "observe-onboarding-focus", {
      timeout: 45000,
    });
    await expectVisibleText(page, "Real data", { timeout: 45000 });
    await expectVisibleText(page, "Connect your app", {
      exact: true,
      timeout: 45000,
    });
    await expectVisibleText(
      page,
      "Use the setup below to send one real or test trace from your app.",
      { timeout: 45000 },
    );
    await expectVisibleText(page, "Sample review", { timeout: 45000 });
    await expectNoVisibleText(page, "Open sample trace");
    const realSetupReturnUrl = page.url();

    const authState = await page.evaluate(() => ({
      accessToken: localStorage.getItem("accessToken"),
      initialRender: localStorage.getItem("initial-render"),
      organizationId: sessionStorage.getItem("organizationId"),
      redirectUrl: localStorage.getItem("redirectUrl"),
      workspaceId: sessionStorage.getItem("workspaceId"),
    }));
    const browserState = {
      initialRender: authState.initialRender,
      organizationId: authState.organizationId,
      redirectUrl: authState.redirectUrl,
      workspaceId: authState.workspaceId,
    };
    const apiHeaders = authenticatedApiHeaders(authState);
    const realProject = await createSmokeObserveProject(apiHeaders, runId);
    const realTrace = await createSmokeTrace(
      apiHeaders,
      realProject.projectId,
      runId,
    );
    const realTraceReviewState = await fetchSmokeActivationState(
      apiHeaders,
      "real_trace_created",
    );
    assert(
      realTraceReviewState.stage === "review_first_trace",
      `Expected review_first_trace after creating a real trace, got ${realTraceReviewState.stage}`,
    );
    assert(
      realTraceReviewState.recommended_action?.href ===
        `/dashboard/observe/${realProject.projectId}/trace/${realTrace.traceId}?source=onboarding&onboarding=review-first-trace`,
      `Expected Review trace href for created trace, got ${realTraceReviewState.recommended_action?.href}`,
    );
    assert(
      realTraceReviewState.recommended_action?.cta_label === "Review trace",
      `Expected Review trace CTA, got ${realTraceReviewState.recommended_action?.cta_label}`,
    );

    await page.goto(`${APP_BASE}/dashboard/home?source=real_trace_created`, {
      waitUntil: "domcontentloaded",
    });
    await expectVisibleTestId(page, "onboarding-home-view", {
      timeout: 45000,
    });
    await expectVisibleText(page, "First trace received", {
      timeout: 45000,
    });
    await expectVisibleTestId(page, "first-signal-panel", { timeout: 45000 });
    await expectVisibleText(page, realTrace.traceId, { timeout: 45000 });
    await expectVisibleText(page, "Not reviewed", { timeout: 45000 });
    await expectVisibleActionHref(
      page,
      "Review trace",
      realTraceReviewState.recommended_action.href,
      { timeout: 45000 },
    );
    const realTraceHomeUrl = page.url();
    await clickVisibleActionHref(
      page,
      "Review trace",
      realTraceReviewState.recommended_action.href,
      45000,
    );
    await page.waitForFunction(
      ({ projectId, traceId }) => {
        const params = new URLSearchParams(window.location.search);
        return (
          window.location.pathname ===
            `/dashboard/observe/${projectId}/trace/${traceId}` &&
          params.get("source") === "onboarding" &&
          params.get("onboarding") === "review-first-trace"
        );
      },
      { timeout: 45000 },
      {
        projectId: realProject.projectId,
        traceId: realTrace.traceId,
      },
    );
    await expectVisibleText(page, "Trace", { exact: true, timeout: 45000 });
    await waitForCondition(
      () =>
        evidence.traceDetailRequests.some(
          (path) => path === `/tracer/trace/${realTrace.traceId}/`,
        ),
      "Expected trace detail request for real trace.",
      45000,
    );
    await waitForCondition(
      () =>
        evidence.activationEventPosts.some(
          (payload) =>
            payload?.event_name === "trace_detail_opened" &&
            payload?.primary_path === "observe" &&
            payload?.stage === "review_first_trace" &&
            payload?.artifact_id === realTrace.traceId &&
            payload?.project_id === realProject.projectId &&
            payload?.is_sample === false &&
            hasObserveQuickStartMetadata(payload),
        ),
      "Expected real trace detail activation event.",
      45000,
    );
    const realTraceReviewUrl = page.url();
    await expectVisibleText(page, "First trace received", { timeout: 45000 });
    await expectVisibleText(
      page,
      "Review spans, latency, cost, and model inputs here. When this signal looks right, create an evaluator.",
      { timeout: 45000 },
    );
    const postReviewState = await waitForSmokeActivationStage(
      apiHeaders,
      "real_trace_reviewed",
      "create_trace_evaluator",
    );
    assert(
      postReviewState.stage === "create_trace_evaluator",
      `Expected create_trace_evaluator after reviewing a real trace, got ${postReviewState.stage}`,
    );
    assert(
      postReviewState.recommended_action?.cta_label === "Create evaluator",
      `Expected Create evaluator CTA, got ${postReviewState.recommended_action?.cta_label}`,
    );
    assert(
      postReviewState.recommended_action?.href ===
        `/dashboard/observe/${realProject.projectId}/llm-tracing?source=onboarding&onboarding=create-evaluator`,
      `Expected focused evaluator route, got ${postReviewState.recommended_action?.href}`,
    );
    await clickVisibleButtonText(page, "Create evaluator", 45000);
    await page.waitForFunction(
      ({ projectId }) => {
        const params = new URLSearchParams(window.location.search);
        return (
          /^\/dashboard\/evaluations\/create\/[^/]+$/.test(
            window.location.pathname,
          ) &&
          params.get("source") === "onboarding" &&
          params.get("step") === "scorer" &&
          params.get("source_type") === "trace_project" &&
          params.get("source_id") === projectId
        );
      },
      { timeout: 45000 },
      { projectId: realProject.projectId },
    );
    await expectVisibleTestId(page, "eval-onboarding-focus", {
      timeout: 45000,
    });
    await expectVisibleText(page, "Eval onboarding", { timeout: 45000 });
    await expectNoVisibleText(page, "Use trace project", { timeout: 45000 });
    await waitForCondition(
      () =>
        evidence.activationEventPosts.some(
          (payload) =>
            payload?.event_name === "onboarding_eval_route_focus_viewed" &&
            payload?.primary_path === "evals" &&
            payload?.stage === "create_eval_dataset" &&
            payload?.source === "eval_create_onboarding" &&
            payload?.artifact_type === "eval" &&
            payload?.artifact_id === realProject.projectId &&
            payload?.metadata?.source_id === realProject.projectId &&
            payload?.metadata?.source_type === "trace_project" &&
            payload?.metadata?.step === "data",
        ),
      "Expected focused eval source activation event.",
      45000,
    );
    const evalCreateOnboardingUrl = page.url();
    await waitForCondition(
      () =>
        evidence.activationEventPosts.some(
          (payload) =>
            payload?.event_name === "onboarding_eval_source_selected" &&
            payload?.primary_path === "evals" &&
            payload?.stage === "create_eval_dataset" &&
            payload?.source === "eval_create_onboarding" &&
            payload?.artifact_type === "observe_project" &&
            payload?.artifact_id === realProject.projectId &&
            payload?.metadata?.source_id === realProject.projectId &&
            payload?.metadata?.source_type === "trace_project" &&
            payload?.metadata?.row_type === "Trace" &&
            payload?.metadata?.surface === "tracing" &&
            payload?.metadata?.step === "data",
        ),
      "Expected eval source selected activation event.",
      45000,
    );
    await waitForCondition(
      () =>
        evidence.activationEventPosts.some(
          (payload) =>
            payload?.event_name === "onboarding_eval_route_focus_viewed" &&
            payload?.primary_path === "evals" &&
            payload?.stage === "add_eval_scorer" &&
            payload?.source === "eval_create_onboarding" &&
            payload?.artifact_type === "eval" &&
            payload?.artifact_id === realProject.projectId &&
            payload?.metadata?.source_id === realProject.projectId &&
            payload?.metadata?.source_type === "trace_project" &&
            payload?.metadata?.step === "scorer",
        ),
      "Expected focused eval scorer activation event.",
      45000,
    );
    const evalScorerOnboardingUrl = page.url();
    await page.waitForFunction(
      ({ projectId }) => {
        const params = new URLSearchParams(window.location.search);
        return (
          /^\/dashboard\/evaluations\/create\/[^/]+$/.test(
            window.location.pathname,
          ) &&
          params.get("source") === "onboarding" &&
          params.get("step") === "run" &&
          params.get("source_type") === "trace_project" &&
          params.get("source_id") === projectId
        );
      },
      { timeout: 45000 },
      { projectId: realProject.projectId },
    );
    await expectNoVisibleText(page, "Save starter scorer", {
      timeout: 45000,
    });
    await expectVisibleTestId(page, "eval-onboarding-focus", {
      timeout: 45000,
    });
    await expectVisibleText(page, "Run", { timeout: 45000 });
    await expectVisibleText(page, "Run the first eval", { timeout: 45000 });
    await expectVisibleText(
      page,
      "Run the scorer once so the first eval result is reviewable.",
      { timeout: 45000 },
    );
    await expectVisibleText(page, "Trace project ready", { timeout: 45000 });
    await expectVisibleText(page, "Run the saved scorer on this source.", {
      timeout: 45000,
    });
    await waitForCondition(
      () =>
        evidence.activationEventPosts.some(
          (payload) =>
            payload?.event_name === "eval_scorer_created" &&
            payload?.primary_path === "evals" &&
            payload?.stage === "add_eval_scorer" &&
            payload?.source === "eval_create_onboarding" &&
            payload?.artifact_type === "eval_scorer" &&
            payload?.metadata?.source_id === realProject.projectId &&
            payload?.metadata?.source_type === "trace_project" &&
            payload?.metadata?.eval_type === "code" &&
            payload?.metadata?.step === "scorer",
        ),
      "Expected eval scorer created activation event.",
      45000,
    );
    await waitForCondition(
      () =>
        evidence.activationEventPosts.some(
          (payload) =>
            payload?.event_name === "onboarding_eval_route_focus_viewed" &&
            payload?.primary_path === "evals" &&
            payload?.stage === "run_eval" &&
            payload?.source === "eval_create_onboarding" &&
            payload?.artifact_type === "eval" &&
            payload?.artifact_id === realProject.projectId &&
            payload?.metadata?.source_id === realProject.projectId &&
            payload?.metadata?.source_type === "trace_project" &&
            payload?.metadata?.step === "run",
        ),
      "Expected focused eval run activation event.",
      45000,
    );
    const evalRunOnboardingUrl = page.url();
    await expectVisibleText(page, "Run first eval", { timeout: 45000 });
    await expectNoVisibleText(page, "Span data not loaded yet", {
      timeout: 60000,
    });
    await clickVisibleButtonText(page, "Run first eval", 45000);
    await waitForCondition(
      () =>
        evidence.evalPlaygroundResponses.some(
          (item) =>
            item.status < 400 &&
            item.body?.status === true &&
            item.body?.result?.log_id,
        ),
      "Expected eval playground run response with a log id.",
      60000,
    );
    const firstEvalRun = evidence.evalPlaygroundResponses.find(
      (item) =>
        item.status < 400 &&
        item.body?.status === true &&
        item.body?.result?.log_id,
    );
    const firstEvalRunId = firstEvalRun?.body?.result?.log_id;
    await page.waitForFunction(
      ({ runId }) => {
        const params = new URLSearchParams(window.location.search);
        return (
          /^\/dashboard\/evaluations\/[^/]+$/.test(window.location.pathname) &&
          params.get("tab") === "usage" &&
          params.get("source") === "onboarding" &&
          params.get("step") === "review" &&
          params.get("run_id") === runId
        );
      },
      { timeout: 60000 },
      { runId: firstEvalRunId },
    );
    await expectVisibleTestId(page, "eval-onboarding-focus", {
      timeout: 45000,
    });
    await expectVisibleText(page, "Review", { timeout: 45000 });
    await expectVisibleText(page, "Review the eval result", {
      timeout: 45000,
    });
    await expectVisibleText(
      page,
      "Inspect failures or summary before deciding what to fix next.",
      { timeout: 45000 },
    );
    await waitForCondition(
      () =>
        evidence.activationEventPosts.some(
          (payload) =>
            payload?.event_name === "eval_run_completed" &&
            payload?.primary_path === "evals" &&
            payload?.stage === "run_eval" &&
            payload?.source === "eval_create_onboarding" &&
            payload?.artifact_type === "eval_run" &&
            payload?.metadata?.source_id === realProject.projectId &&
            payload?.metadata?.source_type === "trace_project" &&
            payload?.metadata?.eval_type === "code" &&
            payload?.metadata?.run_id === firstEvalRunId &&
            payload?.metadata?.step === "run" &&
            hasObserveQuickStartMetadata(payload),
        ),
      "Expected eval run completed activation event.",
      45000,
    );
    await waitForCondition(
      () =>
        evidence.activationEventPosts.some(
          (payload) =>
            payload?.event_name === "onboarding_eval_route_focus_viewed" &&
            payload?.primary_path === "evals" &&
            payload?.stage === "review_eval_failures" &&
            payload?.source === "eval_review_onboarding" &&
            payload?.artifact_type === "eval_run" &&
            payload?.artifact_id === firstEvalRunId &&
            payload?.metadata?.run_id === firstEvalRunId &&
            payload?.metadata?.step === "review" &&
            payload?.metadata?.tab === "usage",
        ),
      "Expected eval review focus activation event.",
      45000,
    );
    await waitForCondition(
      () =>
        evidence.activationEventPosts.some(
          (payload) =>
            payload?.event_name === "eval_failures_reviewed" &&
            payload?.primary_path === "evals" &&
            payload?.stage === "review_eval_failures" &&
            payload?.source === "eval_review_onboarding" &&
            payload?.artifact_type === "eval_run" &&
            payload?.artifact_id === firstEvalRunId &&
            payload?.metadata?.run_id === firstEvalRunId &&
            payload?.metadata?.step === "review" &&
            payload?.metadata?.tab === "usage",
        ),
      "Expected eval result reviewed activation event.",
      60000,
    );
    await waitForCondition(
      () =>
        evidence.evalUsageResponses.some((item) =>
          item.body?.logs?.items?.some((log) => log.id === firstEvalRunId),
        ),
      "Expected usage API response to include the first eval run.",
      60000,
    );
    await expectVisibleText(page, "Next action", { timeout: 60000 });
    const evalReviewOnboardingUrl = page.url();
    const evalId = new URL(evalReviewOnboardingUrl).pathname
      .split("/")
      .filter(Boolean)
      .pop();
    await expectVisibleText(page, "Open source fix", { timeout: 45000 });
    await clickVisibleButtonText(page, "Open source fix", 45000);
    await page.waitForFunction(
      ({ evalId: expectedEvalId, projectId, runId }) => {
        const params = new URLSearchParams(window.location.search);
        return (
          window.location.pathname ===
            `/dashboard/observe/${projectId}/llm-tracing` &&
          params.get("source") === "onboarding" &&
          params.get("step") === "fix-eval-failure" &&
          params.get("source_type") === "trace_project" &&
          params.get("source_id") === projectId &&
          params.get("eval_id") === expectedEvalId &&
          params.get("run_id") === runId
        );
      },
      { timeout: 60000 },
      {
        evalId,
        projectId: realProject.projectId,
        runId: firstEvalRunId,
      },
    );
    await expectVisibleText(
      page,
      "Review the traces or project setup that produced this eval result, then rerun the eval.",
      { timeout: 45000 },
    );
    await expectVisibleText(page, "Rerun eval", { timeout: 45000 });
    const evalSourceFixUrl = page.url();
    const evalRunResponsesBeforeRerun = evidence.evalPlaygroundResponses.length;
    await clickVisibleButtonText(page, "Rerun eval", 45000);
    await page.waitForFunction(
      ({ evalId: expectedEvalId, previousRunId, projectId }) => {
        const params = new URLSearchParams(window.location.search);
        return (
          window.location.pathname ===
            `/dashboard/evaluations/create/${expectedEvalId}` &&
          params.get("source") === "onboarding" &&
          params.get("step") === "run" &&
          params.get("source_type") === "trace_project" &&
          params.get("source_id") === projectId &&
          params.get("rerun_from") === "source_fix" &&
          params.get("previous_run_id") === previousRunId
        );
      },
      { timeout: 60000 },
      {
        evalId,
        previousRunId: firstEvalRunId,
        projectId: realProject.projectId,
      },
    );
    const evalFixRerunUrl = page.url();
    await waitForCondition(
      () =>
        evidence.activationEventPosts.some(
          (payload) =>
            payload?.event_name ===
              "onboarding_eval_source_fix_rerun_clicked" &&
            payload?.primary_path === "evals" &&
            payload?.stage === "eval_next_loop" &&
            payload?.source === "eval_review_onboarding" &&
            payload?.artifact_type === "observe_project" &&
            payload?.metadata?.run_id === firstEvalRunId &&
            payload?.metadata?.rerun_route?.includes(
              "/dashboard/evaluations/create/",
            ),
        ),
      "Expected source-fix rerun clicked activation event.",
      45000,
    );
    await expectVisibleText(page, "Rerun the eval", { timeout: 45000 });
    await expectVisibleText(page, "Rerun eval", { timeout: 45000 });
    await expectNoVisibleText(page, "Span data not loaded yet", {
      timeout: 60000,
    });
    await clickVisibleButtonText(page, "Rerun eval", 45000);
    await waitForCondition(
      () =>
        evidence.evalPlaygroundResponses.length > evalRunResponsesBeforeRerun &&
        evidence.evalPlaygroundResponses.some(
          (item, index) =>
            index >= evalRunResponsesBeforeRerun &&
            item.status < 400 &&
            item.body?.status === true &&
            item.body?.result?.log_id &&
            item.body.result.log_id !== firstEvalRunId,
        ),
      "Expected repair rerun eval playground response with a new log id.",
      60000,
    );
    const repairEvalRun = evidence.evalPlaygroundResponses.find(
      (item, index) =>
        index >= evalRunResponsesBeforeRerun &&
        item.status < 400 &&
        item.body?.status === true &&
        item.body?.result?.log_id &&
        item.body.result.log_id !== firstEvalRunId,
    );
    const repairEvalRunId = repairEvalRun?.body?.result?.log_id;
    await page.waitForFunction(
      ({ previousRunId, runId }) => {
        const params = new URLSearchParams(window.location.search);
        return (
          /^\/dashboard\/evaluations\/[^/]+$/.test(window.location.pathname) &&
          params.get("tab") === "usage" &&
          params.get("source") === "onboarding" &&
          params.get("step") === "review" &&
          params.get("run_id") === runId &&
          params.get("rerun_from") === "source_fix" &&
          params.get("previous_run_id") === previousRunId
        );
      },
      { timeout: 60000 },
      { previousRunId: firstEvalRunId, runId: repairEvalRunId },
    );
    await expectVisibleText(page, "Review the repair attempt", {
      timeout: 45000,
    });
    await waitForCondition(
      () =>
        evidence.evalUsageResponses.some((item) =>
          item.body?.logs?.items?.some((log) => log.id === repairEvalRunId),
        ),
      "Expected usage API response to include the repair rerun.",
      60000,
    );
    await waitForCondition(
      () =>
        evidence.activationEventPosts.some(
          (payload) =>
            payload?.event_name === "onboarding_eval_route_focus_viewed" &&
            payload?.primary_path === "evals" &&
            payload?.stage === "review_eval_failures" &&
            payload?.source === "eval_review_onboarding" &&
            payload?.artifact_type === "eval_run" &&
            payload?.artifact_id === repairEvalRunId &&
            payload?.metadata?.run_id === repairEvalRunId &&
            payload?.metadata?.previous_run_id === firstEvalRunId &&
            payload?.metadata?.rerun_from === "source_fix",
        ),
      "Expected repair rerun review focus activation event.",
      45000,
    );
    await waitForCondition(
      () =>
        evidence.activationEventPosts.some(
          (payload) =>
            payload?.event_name === "onboarding_eval_fix_rerun_completed" &&
            payload?.primary_path === "evals" &&
            payload?.stage === "eval_next_loop" &&
            payload?.source === "eval_review_onboarding" &&
            payload?.artifact_type === "eval_run" &&
            payload?.artifact_id === repairEvalRunId &&
            payload?.metadata?.run_id === repairEvalRunId &&
            payload?.metadata?.previous_run_id === firstEvalRunId &&
            payload?.metadata?.rerun_from === "source_fix",
        ),
      "Expected repair rerun completed activation event.",
      45000,
    );
    await waitForCondition(
      () =>
        evidence.activationEventPosts.some(
          (payload) =>
            payload?.event_name === "onboarding_eval_fix_rerun_reviewed" &&
            payload?.primary_path === "evals" &&
            payload?.stage === "review_eval_failures" &&
            payload?.source === "eval_review_onboarding" &&
            payload?.artifact_type === "eval_run" &&
            payload?.artifact_id === repairEvalRunId &&
            payload?.metadata?.run_id === repairEvalRunId &&
            payload?.metadata?.previous_run_id === firstEvalRunId &&
            payload?.metadata?.rerun_from === "source_fix" &&
            payload?.metadata?.step === "review",
        ),
      "Expected repair rerun reviewed activation event.",
      60000,
    );
    const evalRepairReviewUrl = page.url();
    await expectVisibleText(page, "Continue to quality home", {
      timeout: 45000,
    });
    await clickVisibleButtonText(page, "Continue to quality home", 45000);
    await page.waitForFunction(
      () => {
        const params = new URLSearchParams(window.location.search);
        return (
          window.location.pathname === "/dashboard/home" &&
          params.get("source") === "onboarding" &&
          params.get("target_event") === "first_quality_loop_completed" &&
          params.get("target_route") === "activation_home"
        );
      },
      { timeout: 60000 },
    );
    await waitForCondition(
      () =>
        evidence.activationEventPosts.some(
          (payload) =>
            payload?.event_name === "first_quality_loop_completed" &&
            payload?.primary_path === "evals" &&
            payload?.stage === "activated" &&
            payload?.source === "eval_review_onboarding" &&
            payload?.artifact_type === "eval_run" &&
            payload?.artifact_id === repairEvalRunId &&
            payload?.metadata?.run_id === repairEvalRunId &&
            payload?.metadata?.previous_run_id === firstEvalRunId &&
            payload?.metadata?.rerun_from === "source_fix" &&
            payload?.metadata?.review_outcome === "result_summary_reviewed" &&
            hasObserveQuickStartMetadata(payload),
        ),
      "Expected eval first quality loop completion activation event.",
      45000,
    );
    await expectVisibleTestId(page, "onboarding-home-view", {
      timeout: 45000,
    });
    await expectVisibleTestId(page, "first-loop-complete-panel", {
      timeout: 45000,
    });
    await expectVisibleText(page, "Aha moment reached", {
      timeout: 60000,
    });
    await expectVisibleText(page, "Your first quality loop is live", {
      timeout: 60000,
    });
    const dailyQualityCtaHref = await expectVisibleActionHref(
      page,
      "Review daily quality",
      "/dashboard/home?mode=daily-quality",
      { timeout: 45000 },
    );
    await expectVisibleText(page, "Next best step", {
      timeout: 60000,
    });
    const ahaMomentPostHogEvent = await waitForAhaMomentPostHogEvent(
      evidence.posthogEvents,
    );
    const evalPostRepairHomeUrl = page.url();

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
      evidence.onboardingPosts[0]?.goals?.includes(
        "Monitor a production AI app",
      ),
      `Expected observe quick-start goal, got ${JSON.stringify(
        evidence.onboardingPosts[0]?.goals,
      )}`,
    );
    assert(
      evidence.setupPosts.length === 0,
      "Expected no setup POST on observe quick start.",
    );
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
          payload?.is_sample === true &&
          hasObserveQuickStartMetadata(payload),
      ),
      "Expected sample trace detail activation event.",
    );
    assert(
      evidence.activationEventPosts.some(
        (payload) =>
          payload?.event_name === "sample_to_real_setup_clicked" &&
          payload?.primary_path === "sample" &&
          payload?.stage === "connect_real_data" &&
          payload?.is_sample === true &&
          hasObserveQuickStartMetadata(payload),
      ),
      "Expected sample to real setup activation event.",
    );
    assert(
      evidence.activationEventPosts.some(
        (payload) =>
          payload?.event_name === "onboarding_observe_route_focus_viewed" &&
          payload?.primary_path === "observe" &&
          payload?.stage === "connect_real_data" &&
          payload?.source === "sample_trace_review" &&
          payload?.metadata?.setup_source === "sample_trace_review",
      ),
      "Expected real setup focus event after sample trace review.",
    );
    assert(
      evidence.activationStateRequests.some((request) =>
        hasObserveQuickStartParams(request),
      ),
      `Expected activation-state request with setup-org quick-start attribution, got ${JSON.stringify(
        evidence.activationStateRequests,
      )}`,
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
    const report = smokeReportPayload({
      status: "passed",
      mode: smokeMode,
      evidence: {
        activation_state_requests: evidence.activationStateRequests,
        browser_state: browserState,
        email: user.email,
        onboarding_post: evidence.onboardingPosts[0],
        observe_cta_href: observeCtaHref,
        observe_setup_url: observeSetupUrl,
        eval_create_onboarding_url: evalCreateOnboardingUrl,
        eval_create_focus_event: evidence.activationEventPosts.find(
          (payload) =>
            payload?.event_name === "onboarding_eval_route_focus_viewed" &&
            payload?.stage === "create_eval_dataset" &&
            payload?.artifact_id === realProject.projectId,
        ),
        eval_source_selected_event: evidence.activationEventPosts.find(
          (payload) =>
            payload?.event_name === "onboarding_eval_source_selected" &&
            payload?.artifact_id === realProject.projectId,
        ),
        eval_scorer_focus_event: evidence.activationEventPosts.find(
          (payload) =>
            payload?.event_name === "onboarding_eval_route_focus_viewed" &&
            payload?.stage === "add_eval_scorer" &&
            payload?.artifact_id === realProject.projectId,
        ),
        eval_scorer_onboarding_url: evalScorerOnboardingUrl,
        eval_scorer_created_event: evidence.activationEventPosts.find(
          (payload) =>
            payload?.event_name === "eval_scorer_created" &&
            payload?.metadata?.source_id === realProject.projectId,
        ),
        eval_template_requests: evidence.evalTemplateRequests,
        eval_template_responses: evidence.evalTemplateResponses,
        eval_playground_requests: evidence.evalPlaygroundRequests,
        eval_playground_responses: evidence.evalPlaygroundResponses,
        eval_run_focus_event: evidence.activationEventPosts.find(
          (payload) =>
            payload?.event_name === "onboarding_eval_route_focus_viewed" &&
            payload?.stage === "run_eval" &&
            payload?.artifact_id === realProject.projectId,
        ),
        eval_run_onboarding_url: evalRunOnboardingUrl,
        eval_run_completed_event: evidence.activationEventPosts.find(
          (payload) =>
            payload?.event_name === "eval_run_completed" &&
            payload?.metadata?.run_id === firstEvalRunId,
        ),
        eval_review_focus_event: evidence.activationEventPosts.find(
          (payload) =>
            payload?.event_name === "onboarding_eval_route_focus_viewed" &&
            payload?.stage === "review_eval_failures" &&
            payload?.artifact_id === firstEvalRunId,
        ),
        eval_result_reviewed_event: evidence.activationEventPosts.find(
          (payload) =>
            payload?.event_name === "eval_failures_reviewed" &&
            payload?.artifact_id === firstEvalRunId,
        ),
        eval_review_onboarding_url: evalReviewOnboardingUrl,
        eval_source_fix_url: evalSourceFixUrl,
        eval_fix_rerun_url: evalFixRerunUrl,
        eval_source_fix_rerun_clicked_event: evidence.activationEventPosts.find(
          (payload) =>
            payload?.event_name ===
              "onboarding_eval_source_fix_rerun_clicked" &&
            payload?.metadata?.run_id === firstEvalRunId,
        ),
        eval_fix_rerun_review_focus_event: evidence.activationEventPosts.find(
          (payload) =>
            payload?.event_name === "onboarding_eval_route_focus_viewed" &&
            payload?.artifact_id === repairEvalRunId &&
            payload?.metadata?.rerun_from === "source_fix",
        ),
        eval_fix_rerun_completed_event: evidence.activationEventPosts.find(
          (payload) =>
            payload?.event_name === "onboarding_eval_fix_rerun_completed" &&
            payload?.metadata?.run_id === repairEvalRunId,
        ),
        eval_fix_rerun_reviewed_event: evidence.activationEventPosts.find(
          (payload) =>
            payload?.event_name === "onboarding_eval_fix_rerun_reviewed" &&
            payload?.metadata?.run_id === repairEvalRunId,
        ),
        eval_repair_review_url: evalRepairReviewUrl,
        eval_repair_run_id: repairEvalRunId,
        eval_post_repair_home_url: evalPostRepairHomeUrl,
        daily_quality_cta_href: dailyQualityCtaHref,
        aha_moment_posthog_event: ahaMomentPostHogEvent,
        eval_first_quality_loop_completed_event:
          evidence.activationEventPosts.find(
            (payload) =>
              payload?.event_name === "first_quality_loop_completed" &&
              payload?.metadata?.run_id === repairEvalRunId,
          ),
        eval_source_fix_cta_event: evidence.activationEventPosts.find(
          (payload) =>
            payload?.event_name === "onboarding_eval_source_fix_cta_clicked" &&
            payload?.metadata?.run_id === firstEvalRunId,
        ),
        eval_source_fix_route_event: evidence.activationEventPosts.find(
          (payload) =>
            payload?.event_name === "onboarding_eval_source_fix_route_viewed" &&
            payload?.metadata?.run_id === firstEvalRunId,
        ),
        eval_usage_responses: evidence.evalUsageResponses,
        post_review_state: summarizeActivationState(postReviewState),
        real_observe_project: realProject,
        real_trace: realTrace,
        real_trace_home_url: realTraceHomeUrl,
        real_trace_review_event: evidence.activationEventPosts.find(
          (payload) =>
            payload?.event_name === "trace_detail_opened" &&
            payload?.artifact_id === realTrace.traceId,
        ),
        real_trace_review_state: summarizeActivationState(realTraceReviewState),
        real_trace_review_url: realTraceReviewUrl,
        sample_project_post: evidence.sampleProjectPosts[0],
        sample_trace_activation_event: evidence.activationEventPosts.find(
          (payload) => payload?.event_name === "sample_trace_detail_opened",
        ),
        sample_to_real_setup_event: evidence.activationEventPosts.find(
          (payload) => payload?.event_name === "sample_to_real_setup_clicked",
        ),
        sample_review_return_focus_event: evidence.activationEventPosts.find(
          (payload) =>
            payload?.event_name === "onboarding_observe_route_focus_viewed" &&
            payload?.source === "sample_trace_review",
        ),
        sample_trace_url: sampleTraceUrl,
        setup_org_entry_url: setupOrgEntryUrl,
        setup_org_home_url: setupOrgHomeUrl,
        real_setup_return_url: realSetupReturnUrl,
        screenshot: SCREENSHOT_PATH,
        setup_posts: evidence.setupPosts,
        setup_quick_start: "observe",
        signup_post: evidence.signupPosts[0],
        token_post: evidence.tokenPosts[0],
      },
    });
    await writeSmokeReport(report);
    console.log(JSON.stringify(report, null, 2));
  } catch (error) {
    const report = smokeReportPayload({
      status: "failed",
      mode: smokeMode,
      diagnostic: {
        ...evidence,
        body_text: await safeBodyText(page),
        error_message: error?.message || String(error),
        page_errors: pageErrors,
        preflight,
        signup_form_state: await safeSignupFormState(page),
        url: page.url(),
      },
    });
    console.error(JSON.stringify(report, null, 2));
    await writeSmokeReport(report);
    throw error;
  } finally {
    await browser.close();
  }
}

function smokeReportPayload({ status, mode, evidence, diagnostic }) {
  const report = {
    schema_version: "onboarding-real-signup-smoke-report-2026-05-29.v1",
    source: "onboarding_real_signup_smoke",
    generated_at: new Date().toISOString(),
    status,
    mode,
    app_base: APP_BASE,
    api_base: API_BASE,
    report_output: REPORT_OUTPUT || null,
    viewport: {
      name: VIEWPORT_NAME,
      ...VIEWPORT,
    },
  };

  if (evidence !== undefined) {
    report.evidence = evidence;
  }
  if (diagnostic !== undefined) {
    report.diagnostic = diagnostic;
  }

  return report;
}

async function writeSmokeReport(report) {
  if (!REPORT_OUTPUT) return;
  await mkdir(dirname(REPORT_OUTPUT), { recursive: true });
  await writeFile(
    REPORT_OUTPUT,
    `${JSON.stringify(report, null, 2)}\n`,
    "utf8",
  );
}

function viewportForName(name) {
  if (name === "desktop") {
    return { width: 1440, height: 950, deviceScaleFactor: 1 };
  }
  if (name === "mobile") {
    return {
      width: 390,
      height: 844,
      deviceScaleFactor: 2,
    };
  }
  if (name === "tablet") {
    return { width: 820, height: 1180, deviceScaleFactor: 1 };
  }

  const customSize = /^(\d+)x(\d+)$/.exec(name);
  if (customSize) {
    return {
      width: Number(customSize[1]),
      height: Number(customSize[2]),
      deviceScaleFactor: 1,
    };
  }

  throw new Error(
    `Unknown ONBOARDING_SMOKE_VIEWPORT "${name}". Use desktop, mobile, tablet, or WIDTHxHEIGHT.`,
  );
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

async function preflightRealSignupTargets(targets) {
  return Promise.all(targets.map(preflightTarget));
}

async function preflightTarget({ name, url }) {
  const startedAt = Date.now();
  try {
    const response = await fetch(url, {
      method: "GET",
      redirect: "manual",
      signal: AbortSignal.timeout(5000),
    });
    return {
      name,
      url,
      reachable: true,
      status: response.status,
      duration_ms: Date.now() - startedAt,
    };
  } catch (error) {
    return {
      name,
      url,
      reachable: false,
      error: error?.message || String(error),
      duration_ms: Date.now() - startedAt,
    };
  }
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

async function waitForSampleTraceRoute(page, { timeout = 30000 } = {}) {
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
    { timeout },
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

async function fillVisibleInput(
  page,
  selector,
  value,
  { timeout = 30000 } = {},
) {
  const handle = await page.waitForFunction(
    (targetSelector) => {
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
      return Array.from(document.querySelectorAll(targetSelector)).some(
        isVisible,
      )
        ? Array.from(document.querySelectorAll(targetSelector)).find(isVisible)
        : false;
    },
    { timeout },
    selector,
  );
  const input = handle.asElement();
  assert(input, `Expected visible input: ${selector}`);
  await input.click({ clickCount: 3 });
  await page.keyboard.press("Backspace");
  await input.type(value);
  await handle.dispose();
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
  const handle = await page.waitForFunction(
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
      const button = Array.from(document.querySelectorAll("button")).find(
        (element) =>
          isVisible(element) &&
          normalized(element.textContent) === expectedText,
      );
      return button || false;
    },
    { timeout },
    text,
  );
  const element = handle.asElement();
  assert(element, `Expected visible button for ${text}.`);
  await element.evaluate((button) => {
    button.scrollIntoView({ block: "center", inline: "center" });
  });
  await waitForBrowserFrame();
  await element.click();
  await handle.dispose();
}

async function waitForBrowserFrame() {
  await new Promise((resolve) => setTimeout(resolve, 250));
}

function parseJsonPostData(requestOrData) {
  if (!requestOrData) return {};
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

function isPostHogCaptureRequest(url, method) {
  if (!url || method !== "POST") return false;
  const hostname = url.hostname.toLowerCase();
  const pathname = slashPath(url.pathname);
  return (
    hostname.includes("posthog") &&
    ["/batch/", "/capture/", "/e/"].includes(pathname)
  );
}

function postHogEventsFromRequest(request) {
  const payload = parsePostHogPayload(request.postData());
  return extractPostHogEvents(payload).map(summarizePostHogEvent);
}

function parsePostHogPayload(rawValue) {
  if (!rawValue) return null;
  const directPayload = parseJsonValue(rawValue);
  if (directPayload) return directPayload;

  const params = new URLSearchParams(rawValue);
  const dataValue = params.get("data");
  if (!dataValue) return null;

  const jsonPayload = parseJsonValue(dataValue);
  if (jsonPayload) return jsonPayload;

  try {
    return JSON.parse(Buffer.from(dataValue, "base64").toString("utf8"));
  } catch {
    return null;
  }
}

function parseJsonValue(value) {
  if (!value || typeof value !== "string") return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function extractPostHogEvents(payload) {
  if (!payload) return [];
  if (Array.isArray(payload)) return payload.flatMap(extractPostHogEvents);
  if (Array.isArray(payload.batch)) {
    return payload.batch.flatMap(extractPostHogEvents);
  }
  if (Array.isArray(payload.events)) {
    return payload.events.flatMap(extractPostHogEvents);
  }
  if (typeof payload.event === "string") return [payload];
  return [];
}

const POSTHOG_EVIDENCE_PROPERTY_KEYS = new Set([
  "activated_at",
  "activation_event_name",
  "activation_event_occurred_at",
  "activation_event_path",
  "activation_stage",
  "daily_quality_available",
  "feature_flag_variant",
  "home_mode",
  "is_sample",
  "organization_id",
  "primary_path",
  "quick_start_goal",
  "quick_start_id",
  "quick_start_primary_path",
  "request_id",
  "source",
  "stage",
  "target_event",
  "target_route",
  "user_id",
  "workspace_id",
]);

function summarizePostHogEvent(event = {}) {
  const properties =
    event.properties && typeof event.properties === "object"
      ? Object.fromEntries(
          Object.entries(event.properties).filter(([key, value]) => {
            if (!POSTHOG_EVIDENCE_PROPERTY_KEYS.has(key)) return false;
            return value !== undefined && value !== null && value !== "";
          }),
        )
      : {};

  return {
    event: event.event,
    properties,
  };
}

async function waitForAhaMomentPostHogEvent(posthogEvents) {
  let ahaMomentPostHogEvent = null;
  await waitForCondition(
    () => {
      ahaMomentPostHogEvent = posthogEvents.find(isAhaMomentPostHogEvent);
      return Boolean(ahaMomentPostHogEvent);
    },
    "Expected frontend Aha moment PostHog event with observe quick-start attribution.",
    45000,
  );
  return ahaMomentPostHogEvent;
}

function isAhaMomentPostHogEvent(event) {
  const properties = event?.properties || {};
  return (
    event?.event === "onboarding_aha_moment_reached" &&
    properties.source === "onboarding" &&
    properties.quick_start_goal === "monitor_production_ai_app" &&
    properties.quick_start_id === "observe" &&
    properties.quick_start_primary_path === "observe" &&
    properties.primary_path === "observe" &&
    properties.activation_stage === "activated" &&
    properties.activation_event_name === "first_quality_loop_completed" &&
    properties.activation_event_path === "evals" &&
    properties.daily_quality_available === true &&
    properties.is_sample !== true
  );
}

function authenticatedApiHeaders(authState) {
  assert(authState.accessToken, "Expected accessToken in browser storage.");
  assert(
    authState.organizationId,
    "Expected organizationId in browser storage.",
  );
  assert(authState.workspaceId, "Expected workspaceId in browser storage.");
  return {
    Authorization: `Bearer ${authState.accessToken}`,
    "Content-Type": "application/json",
    "X-Organization-Id": authState.organizationId,
    "X-Workspace-Id": authState.workspaceId,
  };
}

async function createSmokeObserveProject(headers, runId) {
  const payload = await apiPostJson("/tracer/project/", headers, {
    name: `Smoke Real Trace ${runId}`,
    model_type: "GenerativeLLM",
    trace_type: "observe",
  });
  const result = unwrapApiResult(payload);
  const projectId = result?.project_id || result?.id;
  assert(
    projectId,
    `Expected created project id, got ${JSON.stringify(payload)}`,
  );
  return {
    name: result?.name,
    projectId: String(projectId),
  };
}

async function createSmokeTrace(headers, projectId, runId) {
  const payload = await apiPostJson("/tracer/trace/", headers, {
    project: projectId,
    name: "Onboarding smoke real trace",
    metadata: {
      is_sample: false,
      source: "onboarding_smoke",
    },
    input: {
      prompt: "Summarize onboarding smoke",
    },
    output: {
      response: "OK",
    },
    tags: ["onboarding-smoke", runId],
  });
  const result = unwrapApiResult(payload);
  const traceId = result?.id;
  assert(traceId, `Expected created trace id, got ${JSON.stringify(payload)}`);
  return {
    name: result?.name,
    traceId: String(traceId),
  };
}

async function fetchSmokeActivationState(headers, source) {
  const payload = await apiGetJson(
    `/accounts/activation-state/?source=${encodeURIComponent(source)}`,
    headers,
  );
  return unwrapApiResult(payload);
}

async function waitForSmokeActivationStage(headers, source, expectedStage) {
  let latestState = null;
  await waitForCondition(
    async () => {
      latestState = await fetchSmokeActivationState(headers, source);
      return latestState?.stage === expectedStage;
    },
    `Expected activation stage ${expectedStage}, got ${latestState?.stage}`,
    45000,
  );
  return latestState;
}

async function apiPostJson(path, headers, body) {
  return apiJson(path, {
    body: JSON.stringify(body),
    headers,
    method: "POST",
  });
}

async function apiGetJson(path, headers) {
  return apiJson(path, {
    headers,
    method: "GET",
  });
}

async function apiJson(path, options) {
  const response = await fetch(`${API_BASE}${path}`, options);
  const text = await response.text();
  let payload = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { raw: text.slice(0, 500) };
    }
  }
  assert(
    response.status < 400,
    `${options.method} ${path} failed with ${response.status}: ${JSON.stringify(
      payload,
    )}`,
  );
  return payload;
}

function unwrapApiResult(payload) {
  return payload?.result ?? payload;
}

function summarizeActivationState(state) {
  return {
    is_activated: state?.is_activated,
    recommended_action: state?.recommended_action
      ? {
          cta_label: state.recommended_action.cta_label,
          href: state.recommended_action.href,
          id: state.recommended_action.id,
          title: state.recommended_action.title,
        }
      : null,
    sample_project: state?.sample_project
      ? {
          created: state.sample_project.created,
          entry_route: state.sample_project.entry_route,
          status: state.sample_project.status,
        }
      : null,
    signals: state?.signals
      ? {
          first_observe_id: state.signals.first_observe_id,
          first_trace_id: state.signals.first_trace_id,
          observe_projects: state.signals.observe_projects,
          traces: state.signals.traces,
        }
      : null,
    stage: state?.stage,
  };
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
    return new URL(value, APP_BASE);
  } catch {
    return null;
  }
}

function relativeUrl(value) {
  const url = safeUrl(value);
  if (!url) return value;
  return `${url.pathname}${url.search}${url.hash}`;
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
    isEvalTemplatePath(path) ||
    path === "/model-hub/eval-playground/" ||
    /^\/model-hub\/eval-templates\/[^/]+\/usage\/$/.test(path) ||
    /^\/tracer\/trace\/[^/]+\/$/.test(path)
  );
}

function isEvalTemplatePath(path) {
  return (
    path === "/model-hub/eval-templates/create-v2/" ||
    /^\/model-hub\/eval-templates\/[^/]+\/update\/$/.test(path)
  );
}

function isMutationMethod(method) {
  return ["POST", "PUT", "PATCH", "DELETE"].includes(String(method || ""));
}

function summarizeEvalTemplatePayload(payload = {}) {
  return {
    eval_type: payload.eval_type,
    has_code: typeof payload.code === "string" && payload.code.length > 0,
    is_draft: payload.is_draft,
    name: payload.name,
    output_type: payload.output_type,
    publish: payload.publish,
  };
}

function summarizeEvalPlaygroundPayload(payload = {}) {
  const mapping = payload.config?.mapping || payload.mapping || {};
  return {
    has_output_mapping: Boolean(mapping.output),
    model: payload.model,
    span_id: payload.span_id,
    template_id: payload.template_id,
    trace_id: payload.trace_id,
  };
}

function summarizeEvalPlaygroundResponse(payload = {}) {
  const result = payload?.result || {};
  return {
    status: payload?.status,
    result: {
      log_id: result.log_id,
      output: result.output,
      output_type: result.output_type,
      reason: result.reason,
    },
  };
}

function summarizeEvalUsageResponse(payload = {}) {
  const result = payload?.result || {};
  return {
    logs: {
      items: (result.logs?.items || []).slice(0, 5).map((log) => ({
        id: log.id,
        result: log.result,
        score: log.score,
        source: log.source,
        status: log.status,
      })),
      total: result.logs?.total,
    },
    stats: result.stats,
    template_id: result.template_id,
  };
}

function hasObserveQuickStartParams(value) {
  const params = paramsObject(value);
  return Object.entries(OBSERVE_QUICK_START_PARAMS).every(
    ([key, expected]) => params?.[key] === expected,
  );
}

function hasObserveDirectHandoffParams(value) {
  const params = paramsObject(value);
  return Object.entries(OBSERVE_DIRECT_HANDOFF_PARAMS).every(
    ([key, expected]) => params?.[key] === expected,
  );
}

function hasObserveQuickStartMetadata(payload) {
  const metadata = paramsObject(payload?.metadata);
  return Object.entries(OBSERVE_QUICK_START_METADATA).every(
    ([key, expected]) => metadata?.[key] === expected,
  );
}

function hasSampleQuickStartMetadata(payload) {
  const metadata = paramsObject(payload?.metadata);
  return Object.entries(SAMPLE_QUICK_START_METADATA).every(
    ([key, expected]) => metadata?.[key] === expected,
  );
}

function hasSampleQuickStartParams(value) {
  const params = paramsObject(value);
  return Object.entries(SAMPLE_QUICK_START_METADATA).every(
    ([key, expected]) => params?.[key] === expected,
  );
}

function paramsObject(value) {
  if (!value) return {};
  if (typeof value === "string") {
    const url = safeUrl(value);
    if (!url) return {};
    return Object.fromEntries(url.searchParams);
  }
  if (value instanceof URLSearchParams) {
    return Object.fromEntries(value);
  }
  if (typeof value === "object") {
    return value;
  }
  return {};
}

async function waitForCondition(condition, message, timeout = 30000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeout) {
    if (await condition()) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(message);
}

async function safeBodyText(page) {
  try {
    return String(
      await page.evaluate(() => document.body?.innerText || ""),
    ).slice(0, 5000);
  } catch (error) {
    return error.message;
  }
}

async function safeSignupFormState(page) {
  try {
    return await page.evaluate(() => {
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
      return {
        runtime_config: window.__FUTURE_AGI_CONFIG__ || null,
        inputs: Array.from(document.querySelectorAll("input")).map((input) => ({
          name: input.name,
          placeholder: input.getAttribute("placeholder"),
          value: input.type === "password" ? "[redacted]" : input.value,
          visible: isVisible(input),
        })),
        buttons: Array.from(document.querySelectorAll("button")).map(
          (button) => {
            const rect = button.getBoundingClientRect();
            const center = {
              x: rect.left + rect.width / 2,
              y: rect.top + rect.height / 2,
            };
            const topElement = document.elementFromPoint(center.x, center.y);
            return {
              center,
              disabled: button.disabled,
              rect: {
                height: rect.height,
                left: rect.left,
                top: rect.top,
                width: rect.width,
              },
              text: String(button.textContent || "").trim(),
              topElementText: String(topElement?.textContent || "").trim(),
              topElementType: topElement?.tagName || null,
              type: button.type,
              visible: isVisible(button),
            };
          },
        ),
      };
    });
  } catch (error) {
    return { error: error.message };
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
