/* eslint-disable no-console */
import { Buffer } from "node:buffer";
import { createRequire } from "node:module";
import process from "node:process";
import {
  assert,
  createAuthenticatedContext,
  envFlag,
} from "../lib/api-client.mjs";
import { PATH_FOCUS_PLANS } from "../../../src/sections/onboarding-home/components/path-focus-plan.js";
import { hrefWithJourneyGuide } from "../../../src/sections/onboarding-home/components/journey-guide-utils.js";
import { getActivationStateFixture } from "../../../src/sections/onboarding-home/fixtures/activation-state.fixtures.js";
import { ONBOARDING_STAGE_COPY } from "../../../src/sections/onboarding-home/onboarding-home.constants.js";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const SETUP_PACKAGE_PROFILES = {
  anthropic: {
    providerLabel: "Anthropic",
    languages: {
      python: {
        languageKey: "Python",
        languageLabel: "Python",
        install: "pip install traceAI-anthropic anthropic",
        code: `from traceai_anthropic import AnthropicInstrumentor

AnthropicInstrumentor().instrument(tracer_provider=trace_provider)`,
        instrumentSnippet: "AnthropicInstrumentor",
        sampleRequestCode: `import os
import anthropic

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

message = client.messages.create(
    model=os.environ.get("ANTHROPIC_MODEL", "your-anthropic-model"),
    max_tokens=256,
    messages=[{"role": "user", "content": "Say hello in one sentence."}],
)

print(message.content)`,
        smokeSnippet: "client.messages.create",
      },
      typescript: {
        languageKey: "TypeScript",
        languageLabel: "TypeScript",
        install:
          "npm install @traceai/fi-core @traceai/anthropic @opentelemetry/instrumentation @anthropic-ai/sdk",
        code: `import { AnthropicInstrumentation } from "@traceai/anthropic";
import { registerInstrumentations } from "@opentelemetry/instrumentation";

const anthropicInstrumentation = new AnthropicInstrumentation({});

registerInstrumentations({
  instrumentations: [anthropicInstrumentation],
  tracerProvider,
});`,
        instrumentSnippet: "AnthropicInstrumentation",
        sampleRequestCode: `import Anthropic from "@anthropic-ai/sdk";

const anthropic = new Anthropic({
  apiKey: process.env.ANTHROPIC_API_KEY,
});

const message = await anthropic.messages.create({
  model: process.env.ANTHROPIC_MODEL || "your-anthropic-model",
  max_tokens: 256,
  messages: [{ role: "user", content: "Say hello in one sentence." }],
});

console.log(message.content);`,
        smokeSnippet: "anthropic.messages.create",
      },
    },
  },
  openai: {
    providerLabel: "OpenAI",
    languages: {
      python: {
        languageKey: "Python",
        languageLabel: "Python",
        install: "pip install traceAI-openai openai",
        code: `from traceai_openai import OpenAIInstrumentor

OpenAIInstrumentor().instrument(tracer_provider=trace_provider)`,
        instrumentSnippet: "OpenAIInstrumentor",
        sampleRequestCode: `from openai import OpenAI

client = OpenAI()

response = client.responses.create(
    model="gpt-4.1-mini",
    input="Say hello in one sentence.",
)

print(response.output_text)`,
        smokeSnippet: "client.responses.create",
      },
      typescript: {
        languageKey: "TypeScript",
        languageLabel: "TypeScript",
        install:
          "npm install @traceai/fi-core @traceai/openai @opentelemetry/instrumentation openai",
        code: `import { OpenAIInstrumentation } from "@traceai/openai";
import { registerInstrumentations } from "@opentelemetry/instrumentation";

const openaiInstrumentation = new OpenAIInstrumentation({});

registerInstrumentations({
  instrumentations: [openaiInstrumentation],
  tracerProvider,
});`,
        instrumentSnippet: "OpenAIInstrumentation",
        sampleRequestCode: `import OpenAI from "openai";

const openai = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
});

const response = await openai.responses.create({
  model: "gpt-4.1-mini",
  input: "Say hello in one sentence.",
});

console.log(response.output_text);`,
        smokeSnippet: "openai.responses.create",
      },
    },
  },
};

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const VIEWPORT_NAME = process.env.ONBOARDING_SMOKE_VIEWPORT || "desktop";
const EXISTING_PROJECT = envFlag("ONBOARDING_SMOKE_EXISTING_PROJECT");
const EXISTING_TRACE =
  EXISTING_PROJECT && envFlag("ONBOARDING_SMOKE_EXISTING_TRACE");
const OPEN_SAMPLE_HOME = envFlag("ONBOARDING_SMOKE_OPEN_SAMPLE");
const POST_ACTIVATION_HOME = envFlag("ONBOARDING_SMOKE_POST_ACTIVATION_HOME");
const FEATURE_DISABLED_HOME = envFlag("ONBOARDING_SMOKE_FEATURE_DISABLED_HOME");
const PATH_FOCUS = process.env.ONBOARDING_SMOKE_PATH_FOCUS || "";
const ASSERT_DESTINATION_TOUR = envFlag(
  "ONBOARDING_SMOKE_ASSERT_DESTINATION_TOUR",
);
const MISSING_DESTINATION_TOUR = envFlag(
  "ONBOARDING_SMOKE_MISSING_DESTINATION_TOUR",
);
const SETUP_PROVIDER = normalizeSetupProvider(
  process.env.ONBOARDING_SMOKE_SETUP_PROVIDER || "openai",
);
const SETUP_LANGUAGE = normalizeSetupLanguage(
  process.env.ONBOARDING_SMOKE_SETUP_LANGUAGE || "python",
  SETUP_PROVIDER,
);
const SETUP_PACKAGE = setupPackageProfile(SETUP_PROVIDER, SETUP_LANGUAGE);
const SETUP_PACKAGE_SUFFIX =
  SETUP_PROVIDER === "openai" && SETUP_LANGUAGE === "python"
    ? ""
    : `-${SETUP_PROVIDER}-${SETUP_LANGUAGE}`;
const SCREENSHOT_PATH =
  process.env.ONBOARDING_HOME_OBSERVE_SCREENSHOT ||
  `/tmp/onboarding-home-observe-smoke-${VIEWPORT_NAME}${SETUP_PACKAGE_SUFFIX}${
    EXISTING_PROJECT ? "-existing-project" : ""
  }${EXISTING_TRACE ? "-first-trace" : ""}${
    OPEN_SAMPLE_HOME ? "-sample-open" : ""
  }${
    POST_ACTIVATION_HOME ? "-post-activation-fallback" : ""
  }${FEATURE_DISABLED_HOME ? "-product-setup-fallback" : ""}${
    PATH_FOCUS ? `-${PATH_FOCUS}-path-focus` : ""
  }${ASSERT_DESTINATION_TOUR ? "-destination-tour" : ""}${
    MISSING_DESTINATION_TOUR ? "-missing-tour-anchor" : ""
  }.png`;
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
    setup_provider: SETUP_PROVIDER,
    setup_language: SETUP_LANGUAGE,
    open_sample_home: OPEN_SAMPLE_HOME,
    post_activation_home: POST_ACTIVATION_HOME,
    post_first_value_home: POST_ACTIVATION_HOME,
    feature_disabled_home: FEATURE_DISABLED_HOME,
    path_focus: PATH_FOCUS,
    assert_destination_tour: ASSERT_DESTINATION_TOUR,
    missing_destination_tour: MISSING_DESTINATION_TOUR,
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
      await expectVisibleText(page, "Continue product setup", {
        exact: true,
      });
      await expectVisibleText(
        page,
        "Product setup is available for this workspace.",
        { exact: true },
      );
      await expectVisibleText(page, "Continue setup", { exact: true });
      const productSetupHref = await visibleLinkHrefByText(
        page,
        "Continue setup",
        { rootSelector: '[data-testid="onboarding-primary-action"]' },
      );
      assert(
        productSetupHref === "/dashboard/observe?setup=true&source=onboarding",
        `Unexpected product setup fallback CTA href: ${productSetupHref}`,
      );
      evidence.product_setup_href = productSetupHref;
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

    if (POST_ACTIVATION_HOME) {
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
      await expectVisibleText(page, "First quality loop complete", {
        exact: true,
      });
      await expectVisibleText(page, "Your first workflow is live", {
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
        "Post-activation fallback should not show a Daily Quality action when the route is unavailable.",
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
        `Unexpected post-activation Observe CTA href: ${openObserveHref}`,
      );
      evidence.post_activation_open_observe_href = openObserveHref;
      evidence.post_first_value_open_observe_href = openObserveHref;
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

    if (MISSING_DESTINATION_TOUR) {
      const missingTourUrl = withDestinationTourParams({
        basePath: "/dashboard/home?source=onboarding",
        journeyStep: "run_gateway_request",
        tourAnchor: "missing_gateway_request_button",
        quickStart: {
          goal: "control_model_traffic",
          id: "gateway",
          primaryPath: "gateway",
        },
      });

      await page.goto(`${APP_BASE}${missingTourUrl}`, {
        waitUntil: "domcontentloaded",
      });
      await page.waitForFunction(
        () => window.location.pathname === "/dashboard/home",
        { timeout: 30000 },
      );
      await assertMissingDestinationTour(page, {
        expectedFallbackHref:
          "/dashboard/home?source=destination_tour_fallback&journey_step=run_gateway_request&tour_anchor=missing_gateway_request_button&quick_start_goal=control_model_traffic&quick_start_id=gateway&quick_start_primary_path=gateway",
      });
      await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
      evidence.screenshot = SCREENSHOT_PATH;
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

    if (PATH_FOCUS) {
      const pathProfile = pathFocusProfile(PATH_FOCUS);
      const pathFocusUrl = ASSERT_DESTINATION_TOUR
        ? withDestinationTourParams({
            basePath: "/dashboard/home?source=onboarding",
            journeyStep: pathProfile.stage,
            tourAnchor: pathProfile.tourAnchor,
            quickStart: {
              goal: pathProfile.goal,
              id: pathProfile.primaryPath,
              primaryPath: pathProfile.primaryPath,
            },
          })
        : "/dashboard/home?source=onboarding";

      await page.goto(`${APP_BASE}${pathFocusUrl}`, {
        waitUntil: "domcontentloaded",
      });
      await page.waitForFunction(
        () =>
          window.location.pathname === "/dashboard/home" &&
          new URLSearchParams(window.location.search).get("source") ===
            "onboarding",
        { timeout: 30000 },
      );

      await expectSelector(
        page,
        `[data-testid="path-focus-panel-${pathProfile.primaryPath}"]`,
      );
      await expectVisibleText(page, pathProfile.stageEyebrow, {
        exact: true,
      });
      await expectVisibleText(page, pathProfile.stageTitle, { exact: true });
      await expectVisibleText(page, pathProfile.stageDescription, {
        exact: true,
      });
      await expectVisibleText(page, pathProfile.panelEyebrow, { exact: true });
      await expectVisibleText(page, pathProfile.panelTitle, { exact: true });
      await expectVisibleText(page, pathProfile.currentStepLabel, {
        exact: true,
      });
      await expectVisibleText(page, pathProfile.currentStepDescription, {
        exact: true,
      });
      await expectVisibleText(page, "Start here", { exact: true });
      await expectVisibleText(page, "What happens next", { exact: true });
      const genericSetupVisible = await visibleActionExists(
        page,
        "Continue setup",
      );
      assert(
        !genericSetupVisible,
        "Path focus state should not show the generic setup action.",
      );
      if (pathProfile.disabled) {
        const isDisabled = await visibleActionDisabled(page, pathProfile.cta, {
          rootSelector: `[data-testid="path-focus-panel-${pathProfile.primaryPath}"]`,
        });
        assert(
          isDisabled,
          `Expected ${pathProfile.cta} CTA to be disabled for unavailable route.`,
        );
        evidence.home_cta_disabled = true;
      } else {
        const ctaHref = await visibleLinkHrefByText(page, pathProfile.cta, {
          rootSelector: `[data-testid="path-focus-panel-${pathProfile.primaryPath}"]`,
        });
        assert(
          ctaHref === pathProfile.expectedHref,
          `Unexpected path-focus CTA href: ${ctaHref}`,
        );
        assert(!ctaHref.startsWith("//"), `Unsafe CTA href: ${ctaHref}`);
        evidence.home_cta_href = ctaHref;
      }
      await waitForNoVisibleText(page, "Invalid Date");
      if (ASSERT_DESTINATION_TOUR) {
        await assertDestinationTour(page, pathProfile);
      }
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

    const observeHomeUrl = ASSERT_DESTINATION_TOUR
      ? withDestinationTourParams({
          basePath: "/dashboard/home?source=setup_org",
          journeyStep: "connect_observability",
          tourAnchor: "observe_create_project_button",
          quickStart: {
            goal: "monitor_production_ai_app",
            id: "observe",
            primaryPath: "observe",
          },
        })
      : "/dashboard/home?source=setup_org";

    await page.goto(`${APP_BASE}${observeHomeUrl}`, {
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
    if (!ASSERT_DESTINATION_TOUR) {
      await expectSelector(page, '[data-testid="sample-project-panel"]');
    }
    await expectVisibleText(page, "Observe", { exact: true });
    if (!ASSERT_DESTINATION_TOUR) {
      await expectVisibleText(page, "Preview sample trace", { exact: true });
    }
    await expectVisibleText(page, "Connect your agent", { exact: true });
    await expectVisibleText(page, "Open package setup");
    if (SETUP_PROVIDER !== "openai") {
      await clickVisibleText(page, SETUP_PACKAGE.providerLabel, {
        rootSelector: '[data-testid="observe-package-picker"]',
      });
    }
    if (SETUP_LANGUAGE !== "python") {
      await clickVisibleText(page, SETUP_PACKAGE.languageLabel, {
        rootSelector: '[data-testid="observe-package-picker"]',
      });
    }
    await expectVisibleText(page, `Open ${SETUP_PACKAGE.label} setup`, {
      exact: true,
    });
    await expectVisibleText(page, "Send first trace", { exact: true });
    await expectVisibleText(page, "Review first trace", { exact: true });
    await expectVisibleText(page, "Create quality check", { exact: true });
    if (ASSERT_DESTINATION_TOUR) {
      await assertDestinationTour(page, observeDestinationTourProfile());
    }
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
      `Open ${SETUP_PACKAGE.label} setup`,
      { rootSelector: '[data-testid="observe-setup-panel"]' },
    );
    const expectedSetupHref = expectedObserveSetupHref({
      language: SETUP_LANGUAGE,
      provider: SETUP_PROVIDER,
    });
    const actualSetupUrl = new URL(homeCtaHref, "http://local");
    const expectedSetupUrl = new URL(expectedSetupHref, "http://local");
    // Subset match: the CTA must carry every required setup/tour param. The
    // destination-tour flow additionally preserves quick-start attribution
    // (quick_start_*) on the href, which is correct and must not fail this check.
    const requiredSetupParamsPresent = [...expectedSetupUrl.searchParams].every(
      ([key, value]) => actualSetupUrl.searchParams.get(key) === value,
    );
    assert(
      actualSetupUrl.pathname === expectedSetupUrl.pathname &&
        requiredSetupParamsPresent,
      `Unexpected Home CTA href: ${homeCtaHref} (expected to include ${expectedSetupHref})`,
    );
    evidence.home_cta_href = homeCtaHref;

    await clickVisibleText(page, `Open ${SETUP_PACKAGE.label} setup`, {
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
    await expectVisibleText(page, "Observe setup", { exact: true });
    await expectVisibleText(page, `Connect ${SETUP_PACKAGE.label}`, {
      exact: true,
    });
    const observeSetupActionTexts = await visibleActionTexts(page, {
      rootSelector: '[data-testid="observe-onboarding-focus"]',
    });
    if (!EXISTING_PROJECT) {
      assert(
        observeSetupActionTexts.at(-1) ===
          `Open ${SETUP_PACKAGE.providerLabel} setup`,
        `Expected ${SETUP_PACKAGE.providerLabel} setup to remain primary. Actions: ${observeSetupActionTexts.join(
          ", ",
        )}`,
      );
      evidence.observe_setup_sample_action_visible =
        observeSetupActionTexts.includes("Open sample trace");
      evidence.observe_setup_actions = observeSetupActionTexts;
    }
    if (EXISTING_PROJECT) {
      const checkForTraceLabel = `Check for ${SETUP_PACKAGE.label} trace`;
      await expectVisibleText(page, checkForTraceLabel, { exact: true });
      await clickVisibleText(page, checkForTraceLabel, {
        rootSelector: '[data-testid="observe-onboarding-focus"]',
      });
      await page.waitForFunction(
        ({ language, provider }) => {
          const params = new URLSearchParams(window.location.search);
          return (
            window.location.pathname ===
              "/dashboard/observe/observe-smoke-project/llm-tracing" &&
            params.get("source") === "onboarding" &&
            params.get("onboarding") === "send-first-trace" &&
            params.get("selectedTab") === "trace" &&
            params.get("provider") === provider &&
            params.get("language") === language
          );
        },
        { timeout: 30000 },
        { language: SETUP_LANGUAGE, provider: SETUP_PROVIDER },
      );
      evidence.first_trace_step_url = relativeUrl(page.url());
      await expectSelector(page, '[data-testid="observe-onboarding-focus"]');
      await expectVisibleText(page, "Send the first trace", { exact: true });
      const firstTraceStepActions = await visibleActionTexts(page, {
        rootSelector: '[data-testid="observe-onboarding-focus"]',
      });
      const checkTraceLabel = `Check for ${SETUP_PACKAGE.label} trace`;
      assert(
        firstTraceStepActions.includes(checkTraceLabel),
        `Expected trace check action to remain available. Actions: ${firstTraceStepActions.join(
          ", ",
        )}`,
      );
      if (!EXISTING_TRACE) {
        assert(
          firstTraceStepActions.includes(
            `Open ${SETUP_PACKAGE.providerLabel} setup`,
          ),
          `Expected setup escape hatch to remain available. Actions: ${firstTraceStepActions.join(
            ", ",
          )}`,
        );
      }
      assert(
        firstTraceStepActions[0] === checkTraceLabel,
        `Unexpected first-trace primary action. Actions: ${firstTraceStepActions.join(
          ", ",
        )}`,
      );
      evidence.first_trace_step_actions = firstTraceStepActions;
      const isFirstTraceReviewRoute = ({ language, provider }) => {
        const params = new URLSearchParams(window.location.search);
        return (
          window.location.pathname ===
            "/dashboard/observe/observe-smoke-project/trace/trace-smoke-1" &&
          params.get("source") === "onboarding" &&
          params.get("onboarding") === "review-first-trace" &&
          params.get("provider") === provider &&
          params.get("language") === language
        );
      };
      let traceReviewAlreadyOpen = await page.evaluate(
        isFirstTraceReviewRoute,
        { language: SETUP_LANGUAGE, provider: SETUP_PROVIDER },
      );
      if (!traceReviewAlreadyOpen) {
        try {
          await clickVisibleText(page, checkTraceLabel, {
            rootSelector: '[data-testid="observe-onboarding-focus"]',
          });
        } catch (error) {
          traceReviewAlreadyOpen = await page.evaluate(
            isFirstTraceReviewRoute,
            {
              language: SETUP_LANGUAGE,
              provider: SETUP_PROVIDER,
            },
          );
          if (!traceReviewAlreadyOpen) throw error;
        }
      }
      if (EXISTING_TRACE) {
        traceReviewAlreadyOpen = await page.evaluate(isFirstTraceReviewRoute, {
          language: SETUP_LANGUAGE,
          provider: SETUP_PROVIDER,
        });
        if (!traceReviewAlreadyOpen) {
          await expectVisibleText(page, "First trace received", {
            exact: true,
          });
          await expectVisibleText(page, "Review trace", { exact: true });
          await clickVisibleText(page, "Review trace", {
            rootSelector: '[data-testid="observe-onboarding-focus"]',
          });
        }
        await page.waitForFunction(
          isFirstTraceReviewRoute,
          { timeout: 30000 },
          { language: SETUP_LANGUAGE, provider: SETUP_PROVIDER },
        );
        await waitForCondition(
          () =>
            stubbedApiRequests.some((entry) =>
              entry.includes("/tracer/trace/list_traces_of_session/"),
            ) ||
            stubbedApiRequests.some((entry) =>
              entry.includes("/tracer/trace/trace-smoke-1/"),
            ),
          "Trace list or trace detail was not requested for first trace verification.",
          30000,
        );
        evidence.first_trace_review_url = relativeUrl(page.url());
        await expectVisibleText(page, "Create quality check", { exact: true });
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
              payload?.metadata?.route_mode === "send-first-trace" &&
              payload?.metadata?.setup_provider === SETUP_PROVIDER &&
              payload?.metadata?.setup_language === SETUP_LANGUAGE,
          ),
        "Observe first trace step activation event was not posted.",
        30000,
      );
    } else {
      await expectVisibleText(page, "Checking for trace", {
        exact: true,
      });
      await expectVisibleText(
        page,
        `1. Install ${SETUP_PACKAGE.providerLabel}`,
        {
          exact: true,
        },
      );
      await expectVisibleText(page, "2. Load Future AGI and provider keys", {
        exact: true,
      });
      await expectVisibleText(
        page,
        `3. Connect ${SETUP_PACKAGE.providerLabel}`,
        { exact: true },
      );
      await expectVisibleText(
        page,
        `4. Run one ${SETUP_PACKAGE.providerLabel} request`,
        {
          exact: true,
        },
      );
      await expectVisibleText(page, "Create quality check", {
        exact: true,
      });
      await expectVisibleText(page, SETUP_PACKAGE.install);
      await expectVisibleText(page, SETUP_PACKAGE.instrumentSnippet);
      await expectVisibleText(page, SETUP_PACKAGE.smokeSnippet);
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
      const firstTraceReady =
        EXISTING_TRACE && payload?.stage === "waiting_for_first_trace";
      await respondJson(request, {
        status: true,
        result: {
          event_id: "00000000-0000-4000-8000-000000000188",
          event_name: payload?.event_name || "onboarding_home_viewed",
          activation_state: stubbedActivationState(auth, { firstTraceReady }),
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
  if (PATH_FOCUS) {
    return pathFocusActivationState(auth, PATH_FOCUS);
  }

  const fixtureName = FEATURE_DISABLED_HOME
    ? "featureDisabled"
    : POST_ACTIVATION_HOME
      ? "observeFirstLoopComplete"
      : firstTraceReady
        ? "observeFirstTraceReady"
        : "observeNoSetup";
  const activationState = getActivationStateFixture(fixtureName);
  if (FEATURE_DISABLED_HOME || POST_ACTIVATION_HOME) {
    return {
      ...activationState,
      request_id: "onboarding_home_observe_smoke",
      organization_id: auth.organizationId,
      workspace_id: auth.workspaceId,
      user_id: auth.user.id,
      route_availability: POST_ACTIVATION_HOME
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

function pathFocusProfile(pathFocus) {
  const profiles = {
    prompt: {
      primaryPath: "prompt",
      goal: "improve_prompts",
      stage: "start_prompt",
      pathLabel: "Test prompts or agent prompts",
      pathDescription: "Run prompt tests and compare output changes.",
      flagName: "onboarding_prompt_path",
      actionId: "start_prompt",
      actionKind: "setup",
      actionTitle: "Create prompt",
      actionDescription:
        "Create one prompt and run it against a focused example.",
      cta: "Create prompt",
      href: "/dashboard/workbench/all?source=onboarding&action=create-prompt",
      completionEvent: "prompt_created",
    },
    agent: {
      primaryPath: "agent",
      goal: "build_ai_agent",
      stage: "run_agent_scenario",
      pathLabel: "Prototype agent",
      pathDescription: "Run a first scenario and inspect the agent trace.",
      flagName: "onboarding_agent_path",
      actionId: "run_agent_scenario",
      actionKind: "test",
      actionTitle: "Run scenario",
      actionDescription: "Run one scenario and inspect what the agent did.",
      cta: "Run scenario",
      href: "/dashboard/agents/playground/agent-1/build?onboarding=run-scenario",
      completionEvent: "agent_prototype_run_completed",
    },
    gateway: {
      primaryPath: "gateway",
      goal: "control_model_traffic",
      stage: "run_gateway_request",
      pathLabel: "Set up gateway",
      pathDescription:
        "Add a provider, create a key, and send a gateway request.",
      flagName: "onboarding_gateway_path",
      actionId: "run_gateway_request",
      actionKind: "test",
      actionTitle: "Send gateway request",
      actionDescription: "Send one request through the gateway.",
      cta: "Send request",
      href: "/dashboard/gateway?onboarding=test-request",
      completionEvent: "gateway_request_sent",
    },
    evals: {
      primaryPath: "evals",
      goal: "evaluate_quality",
      stage: "run_eval",
      stageEyebrow: "Quality run",
      stageTitle: "Run the first quality check",
      stageDescription: "Run it once so the first result is reviewable.",
      pathLabel: "Test AI with Simulation / Evals",
      pathDescription:
        "Choose a source, run a quality check, and fix or finish from the first result.",
      flagName: "onboarding_eval_path",
      actionId: "run_eval",
      actionKind: "test",
      actionTitle: "Run quality check",
      actionDescription: "Run the check once before reviewing the result.",
      cta: "Run check",
      href: "/dashboard/evaluations/create?source=onboarding&step=run",
      completionEvent: "eval_run_completed",
      disabled: false,
    },
    voice: {
      primaryPath: "voice",
      goal: "connect_voice_ai_agent",
      stage: "create_voice_agent",
      stageEyebrow: "Voice",
      stageTitle: "Create a voice agent",
      stageDescription:
        "Create or connect one voice agent before running a test call.",
      pathLabel: "Connect a voice AI agent",
      pathDescription: "Run or review a call with clear success criteria.",
      flagName: "onboarding_voice_path",
      actionId: "create_voice_agent",
      actionKind: "setup",
      actionTitle: "Create voice agent",
      actionDescription:
        "Create or connect one voice agent before the first test call.",
      cta: "Create agent",
      href: "/dashboard/simulate/agent-definitions/create-new-agent-definition?source=onboarding&onboarding=create-voice-agent",
      completionEvent: "voice_agent_created",
      disabled: false,
    },
  };
  const profile = profiles[pathFocus];
  assert(
    profile,
    `Unsupported ONBOARDING_SMOKE_PATH_FOCUS value: ${pathFocus}`,
  );
  const stageCopy = ONBOARDING_STAGE_COPY[profile.stage];
  const plan = PATH_FOCUS_PLANS[profile.primaryPath];
  const currentStep = plan?.steps?.find((step) => step.stage === profile.stage);
  const currentStepIndex = plan?.steps?.findIndex(
    (step) => step.stage === profile.stage,
  );
  assert(stageCopy, `Missing stage copy for ${profile.stage}`);
  assert(plan, `Missing path-focus plan for ${profile.primaryPath}`);
  assert(currentStep, `Missing current step for ${profile.stage}`);
  return {
    ...profile,
    stageEyebrow: stageCopy.eyebrow,
    stageTitle: stageCopy.title,
    stageDescription: stageCopy.description,
    panelEyebrow: plan.eyebrow,
    panelTitle: plan.title,
    currentStepLabel: currentStep.label,
    currentStepDescription: currentStep.description,
    // The plain path-focus scenario navigates Home with NO quick_start params
    // (source=onboarding), so the merged app correctly omits quick_start_* from
    // the CTA href. The destination-tour scenario navigates WITH quick_start in
    // the URL, so the app preserves quick_start_* attribution on the href.
    expectedHref: ASSERT_DESTINATION_TOUR
      ? hrefWithJourneyGuide(
          hrefWithQuickStart(profile.href, {
            goal: profile.goal,
            id: profile.primaryPath,
            primaryPath: profile.primaryPath,
          }),
          currentStep,
        )
      : hrefWithJourneyGuide(profile.href, currentStep),
    nextLabel: plan.steps[currentStepIndex + 1]?.label || null,
    stepCount: plan.steps.length,
    stepNumber: currentStepIndex + 1,
    tourAnchor: currentStep.tourAnchor,
  };
}

function hrefWithQuickStart(href, quickStart) {
  if (!href || !href.startsWith("/") || href.startsWith("//")) return href;
  const [withoutHash, hash] = href.split("#");
  const [pathname, query = ""] = withoutHash.split("?");
  const params = new URLSearchParams(query);
  params.set("quick_start_goal", quickStart.goal);
  params.set("quick_start_id", quickStart.id);
  params.set("quick_start_primary_path", quickStart.primaryPath);
  return `${pathname}?${params.toString()}${hash ? `#${hash}` : ""}`;
}

function pathFocusActivationState(auth, pathFocus) {
  const profile = pathFocusProfile(pathFocus);
  const activationState = getActivationStateFixture("observeNoSetup");
  const pathHref = `/dashboard/home?path=${profile.primaryPath}`;

  return {
    ...activationState,
    request_id: `onboarding_home_${profile.primaryPath}_path_smoke`,
    organization_id: auth.organizationId,
    workspace_id: auth.workspaceId,
    user_id: auth.user.id,
    goal: profile.goal,
    primary_path: profile.primaryPath,
    stage: profile.stage,
    progress: {
      build: "complete",
      test: "selected",
      observe: "not_started",
      ship: "not_started",
      improve: "not_started",
    },
    recommended_action: {
      id: profile.actionId,
      kind: profile.actionKind,
      title: profile.actionTitle,
      description: profile.actionDescription,
      href: profile.href,
      cta_label: profile.cta,
      estimated_minutes: 3,
      priority: 100,
      blocked: false,
      blocked_reason: null,
      requires_permission: null,
      completion_event: profile.completionEvent,
      is_sample: false,
      route_available: !profile.disabled,
      fallback_href: `/dashboard/home?path=${profile.primaryPath}`,
      analytics: {
        event_name: "onboarding_recommended_action_clicked",
        source: "home",
        target_path: profile.primaryPath,
      },
    },
    available_paths: [
      {
        id: profile.primaryPath,
        label: profile.pathLabel,
        description: profile.pathDescription,
        status: "selected",
        href: pathHref,
        is_available: true,
        blocked_reason: null,
        requires_permission: null,
        first_action_id: profile.actionId,
      },
    ],
    feature_flags: {
      ...activationState.feature_flags,
      [profile.flagName]: true,
    },
    route_availability: {
      ...activationState.route_availability,
      [`path_${profile.primaryPath}`]: {
        href: pathHref,
        is_available: true,
        reason: null,
      },
      [profile.actionId]: {
        href: profile.href,
        is_available: !profile.disabled,
        reason: profile.disabled ? "route_not_available" : null,
      },
    },
    sample_project: {
      ...activationState.sample_project,
      available: false,
    },
  };
}

function observeDestinationTourProfile() {
  return {
    goal: "monitor_production_ai_app",
    nextLabel: "Send trace",
    panelTitle: "Observe loop",
    primaryPath: "observe",
    stage: "connect_observability",
    stepCount: 4,
    stepNumber: 1,
    tourAnchor: "observe_create_project_button",
  };
}

function withDestinationTourParams({
  basePath,
  journeyStep,
  quickStart,
  replay = false,
  tourAnchor,
}) {
  const url = new URL(basePath, APP_BASE);
  url.searchParams.set("journey_step", journeyStep);
  url.searchParams.set("tour_anchor", tourAnchor);
  if (quickStart?.goal)
    url.searchParams.set("quick_start_goal", quickStart.goal);
  if (quickStart?.id) url.searchParams.set("quick_start_id", quickStart.id);
  if (quickStart?.primaryPath) {
    url.searchParams.set("quick_start_primary_path", quickStart.primaryPath);
  }
  if (replay) url.searchParams.set("tour_replay", "1");
  return `${url.pathname}${url.search}`;
}

async function assertDestinationTour(page, profile) {
  const popoverSelector = '[data-testid="destination-tour-anchor"]';
  const expectedProgress = `Step ${profile.stepNumber} of ${profile.stepCount}`;
  const expectedPlanLine = profile.nextLabel
    ? `${profile.panelTitle} - Next: ${profile.nextLabel}`
    : profile.panelTitle;
  const expectedPlanHref =
    `/dashboard/home?source=destination_tour_plan&journey_step=${profile.stage}` +
    `&tour_anchor=${profile.tourAnchor}&quick_start_goal=${profile.goal}` +
    `&quick_start_id=${profile.primaryPath}&quick_start_primary_path=${profile.primaryPath}`;

  await expectSelector(page, popoverSelector);
  await expectVisibleText(page, expectedProgress, { exact: true });
  await expectVisibleText(page, expectedPlanLine, { exact: true });
  await expectVisibleText(page, destinationTourLabelForStep(profile.stage), {
    exact: true,
  });
  await expectSelector(
    page,
    `[data-tour-anchor="${profile.tourAnchor}"][data-onboarding-tour-active="true"]`,
  );
  const viewPlanHref = await visibleLinkHrefByText(page, "View plan", {
    rootSelector: popoverSelector,
  });
  assert(
    viewPlanHref === expectedPlanHref,
    `Unexpected destination tour plan href: ${viewPlanHref}`,
  );

  await clickVisibleText(page, "Got it", { rootSelector: popoverSelector });
  await page.waitForFunction(
    () => !document.querySelector('[data-testid="destination-tour-anchor"]'),
    { timeout: 10000 },
  );

  const replayUrl = new URL(page.url());
  replayUrl.searchParams.set("tour_replay", "1");
  await page.goto(replayUrl.toString(), { waitUntil: "domcontentloaded" });
  await expectSelector(page, popoverSelector);
  await expectVisibleText(page, expectedProgress, { exact: true });
}

function destinationTourLabelForStep(journeyStep) {
  return (
    {
      connect_observability: "Connect observability",
      create_agent: "Create agent",
      run_agent_scenario: "Run scenario",
      run_eval: "Run quality check",
      run_gateway_request: "Send request",
      start_prompt: "Create prompt",
      create_voice_agent: "Create agent",
    }[journeyStep] || "Next step"
  );
}

async function assertMissingDestinationTour(page, { expectedFallbackHref }) {
  const recoverySelector = '[data-testid="destination-tour-missing-anchor"]';
  await expectSelector(page, recoverySelector);
  await expectVisibleText(page, "Step 3 of 6", { exact: true });
  await expectVisibleText(page, "Route one request safely", { exact: true });
  await expectVisibleText(page, "Send request", { exact: true });
  await expectVisibleText(
    page,
    "This page changed or is still loading. Return to Home for the latest step, or try finding the action again.",
  );
  const fallbackHref = await visibleLinkHrefByText(page, "Back to Home", {
    rootSelector: recoverySelector,
  });
  assert(
    fallbackHref === expectedFallbackHref,
    `Unexpected missing-anchor fallback href: ${fallbackHref}`,
  );
}

function normalizeSetupProvider(value) {
  const normalizedValue = String(value || "")
    .trim()
    .toLowerCase()
    .replaceAll("-", "_");
  assert(
    SETUP_PACKAGE_PROFILES[normalizedValue],
    `Unsupported ONBOARDING_SMOKE_SETUP_PROVIDER=${value}`,
  );
  return normalizedValue;
}

function normalizeSetupLanguage(value, provider) {
  const normalizedValue = String(value || "")
    .trim()
    .toLowerCase()
    .replaceAll("-", "_");
  const profile = SETUP_PACKAGE_PROFILES[provider];
  assert(profile, `Unsupported setup provider ${provider}`);
  if (profile.languages[normalizedValue]) return normalizedValue;
  return "python";
}

function setupPackageProfile(provider, language) {
  const providerProfile = SETUP_PACKAGE_PROFILES[provider];
  const languageProfile = providerProfile?.languages?.[language];
  assert(
    providerProfile && languageProfile,
    `Unsupported setup package ${provider}/${language}`,
  );
  return {
    ...languageProfile,
    provider,
    language,
    providerLabel: providerProfile.providerLabel,
    label: `${providerProfile.providerLabel} ${languageProfile.languageLabel}`,
  };
}

function expectedObserveSetupHref({ language, provider }) {
  const params = new URLSearchParams({
    setup: "true",
    source: "onboarding",
    provider,
    language,
    tour_anchor: "observe_create_project_button",
    journey_step: "connect_observability",
  });
  return `/dashboard/observe?${params.toString()}`;
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
  const instruments = Object.fromEntries(
    Object.entries(SETUP_PACKAGE_PROFILES).map(
      ([provider, providerProfile]) => [
        provider,
        {
          name: providerProfile.providerLabel,
          logo: "/favicon/logo.svg",
          ...Object.fromEntries(
            Object.values(providerProfile.languages).map((languageProfile) => [
              languageProfile.languageKey,
              {
                code: languageProfile.code,
                github: "https://github.com/future-agi",
                sample_request_code: languageProfile.sampleRequestCode,
              },
            ]),
          ),
        },
      ],
    ),
  );

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
    instruments,
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

async function visibleActionDisabled(page, text, { rootSelector } = {}) {
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
        root?.querySelectorAll("button,[role='button'],a[href]") || [],
      ).find(
        (candidate) =>
          isVisible(candidate) &&
          normalized(candidate.textContent) === expectedText,
      );
      return Boolean(
        element?.disabled ||
          element?.getAttribute("aria-disabled") === "true" ||
          element?.classList.contains("Mui-disabled"),
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
