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
const QUICK_START_KEY =
  process.env.ONBOARDING_SMOKE_SETUP_QUICK_START ||
  (envFlag("ONBOARDING_SMOKE_SETUP_SAMPLE_PREVIEW")
    ? "sample_preview"
    : "observe");
const SCREENSHOT_PATH =
  process.env.SETUP_ORG_COMPLETION_SCREENSHOT ||
  `/tmp/setup-org-completion-smoke-${VIEWPORT_NAME}-${QUICK_START_KEY}.png`;
const PICK_SCREENSHOT_PATH = process.env.SETUP_ORG_PICK_SCREENSHOT || "";
const STUB_AUTH = envFlag("ONBOARDING_SMOKE_STUB_AUTH");
const SAMPLE_QUICK_START_METADATA = {
  quick_start_goal: "explore_sample_data",
  quick_start_id: "sample_preview",
  quick_start_primary_path: "sample",
};

const QUICK_STARTS = {
  observe: {
    buttonText: "Connect your agent",
    expectedActionText: "Create Observe project",
    expectedAttribution: {
      quick_start_goal: "monitor_production_ai_app",
      quick_start_id: "observe",
      quick_start_primary_path: "observe",
    },
    expectedGoal: "monitor_production_ai_app",
    fixture: "newWorkspaceNoGoal",
  },
  sample_preview: {
    buttonText: "Preview sample screens",
    expectedAttribution: {
      quick_start_goal: "explore_sample_data",
      quick_start_id: "sample_preview",
      quick_start_primary_path: "sample",
    },
    expectedGoal: null,
    fixture: "sampleFirstRunStart",
  },
  prompt: {
    buttonText: "Test prompts or agent prompts",
    expectedActionText: "Create prompt",
    expectedAttribution: {
      quick_start_goal: "improve_prompts",
      quick_start_id: "prompt",
      quick_start_primary_path: "prompt",
    },
    expectedGoal: "improve_prompts",
    fixture: "promptNoPrompt",
  },
  agent: {
    buttonText: "Prototype agent",
    expectedActionText: "Create agent",
    expectedAttribution: {
      quick_start_goal: "build_ai_agent",
      quick_start_id: "agent",
      quick_start_primary_path: "agent",
    },
    expectedGoal: "build_ai_agent",
    fixture: "agentNoAgent",
  },
  gateway: {
    buttonText: "Set up gateway",
    expectedActionText: "Add provider",
    expectedAttribution: {
      quick_start_goal: "control_model_traffic",
      quick_start_id: "gateway",
      quick_start_primary_path: "gateway",
    },
    expectedGoal: "control_model_traffic",
    fixture: "gatewayNoProvider",
  },
  evals: {
    buttonText: "Test AI using simulation",
    expectedActionText: "Create dataset",
    expectedAttribution: {
      quick_start_goal: "evaluate_quality",
      quick_start_id: "evals",
      quick_start_primary_path: "evals",
    },
    expectedGoal: "evaluate_quality",
    activationState: pathFocusActivationState,
    primaryPath: "evals",
  },
};

const REQUESTED_QUICK_START = QUICK_STARTS[QUICK_START_KEY];
const SAMPLE_PREVIEW_GUARD = QUICK_START_KEY === "sample_preview";
const EXPECT_SAMPLE_HANDOFF = false;
const QUICK_START = SAMPLE_PREVIEW_GUARD
  ? QUICK_STARTS.observe
  : REQUESTED_QUICK_START;

async function main() {
  assert(STUB_AUTH, "Set ONBOARDING_SMOKE_STUB_AUTH=1 for this smoke.");
  assert(
    REQUESTED_QUICK_START,
    `Unsupported ONBOARDING_SMOKE_SETUP_QUICK_START=${QUICK_START_KEY}`,
  );

  const auth = createStubbedAuthenticatedContext();
  const apiFailures = [];
  const pageErrors = [];
  const consoleMessages = [];
  const onboardingPosts = [];
  const requestFailures = [];
  const setupPosts = [];
  const activationEventPosts = [];
  const activationStateRequests = [];
  const sampleProjectPosts = [];
  const sampleProjectResponses = [];
  const traceDetailRequests = [];
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
    activationEventPosts,
    activationStateRequests,
    getSetupCompleted: () => setupCompleted,
    onboardingPosts,
    onSetupComplete: () => {
      setupCompleted = true;
    },
    sampleProjectPosts,
    sampleProjectResponses,
    setupPosts,
    traceDetailRequests,
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
  page.on("console", (message) => {
    if (message.type() === "error" || message.type() === "warning") {
      consoleMessages.push(`${message.type()}: ${message.text()}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("requestfailed", (request) => {
    requestFailures.push(
      `${request.failure()?.errorText || "failed"} ${request.url()}`,
    );
  });

  try {
    await page.goto(`${APP_BASE}/auth/jwt/setup-org?step=0`, {
      waitUntil: "domcontentloaded",
    });
    await expectVisibleText(page, "What do you want to set up first?");
    await expectVisibleText(
      page,
      "Pick the closest product job. We will save it and open a checklist with the first action highlighted.",
    );
    if (SAMPLE_PREVIEW_GUARD) {
      const samplePreviewVisible = await isVisibleButtonText(
        page,
        QUICK_STARTS.sample_preview.buttonText,
      );
      assert(
        !samplePreviewVisible,
        "Sample preview must not be selectable before a real setup task.",
      );
      await expectVisibleText(
        page,
        "Sample screens are available after setup starts.",
      );
    }
    const quickStartInitiallyVisible = await isVisibleButtonText(
      page,
      QUICK_START.buttonText,
    );
    assert(
      quickStartInitiallyVisible,
      `Expected ${QUICK_START.buttonText} to be visible on setup-org.`,
    );
    if (PICK_SCREENSHOT_PATH) {
      await page.screenshot({ path: PICK_SCREENSHOT_PATH, fullPage: true });
    }
    await page.evaluate(() => {
      localStorage.setItem("redirectUrl", "/dashboard/observe?project=stale");
    });
    await clickVisibleButtonText(page, QUICK_START.buttonText);
    let homeParams = null;
    let setupOrgHomeUrl = null;
    let sampleTraceUrl = null;
    setupOrgHomeUrl = await waitForSetupOrgHomeRoute(
      page,
      QUICK_START.expectedAttribution,
      { timeout: 30000 },
    );
    homeParams = paramsObject(setupOrgHomeUrl);
    assertExpectedAttribution(homeParams, {
      ...QUICK_START.expectedAttribution,
      source: "setup_org",
    });

    await expectVisibleTestId(page, "onboarding-home-view");
    if (EXPECT_SAMPLE_HANDOFF) {
      await expectVisibleText(page, "Preview sample data", { exact: true });
      await expectVisibleText(page, "Sample data is a preview");
      await expectVisibleText(page, "Open sample trace");
      await clickVisibleButtonText(page, "Open sample trace");
      await waitForSampleTraceRoute(page, { timeout: 30000 });
      sampleTraceUrl = relativeUrl(page.url());
      assert(
        hasSampleQuickStartParams(sampleTraceUrl),
        `Expected sample trace URL quick-start attribution, got ${sampleTraceUrl}`,
      );
      await expectVisibleText(page, "Trace", { exact: true });
      await expectVisibleText(page, "Sample trace review");
      await expectVisibleText(page, "Connect your app", { exact: true });
    } else {
      await expectVisibleText(page, QUICK_START.buttonText, {
        exact: true,
      });
      await expectVisibleText(
        page,
        `Start with ${QUICK_START.expectedActionText}`,
      );
      await expectVisibleText(page, "Start here", { exact: true });
      await expectVisibleText(page, "What happens next", { exact: true });
      await expectVisibleText(page, "Step 1 of");
      await expectVisibleText(page, QUICK_START.expectedActionText, {
        exact: true,
      });
      await waitForNoVisibleText(page, "Show full path");
      await expectNoSelector(page, '[data-testid="sample-project-panel"]');

      await waitForCondition(
        () => activationStateRequests.length === 1,
        `Expected one activation-state request, got ${activationStateRequests.length}`,
      );
      homeParams = activationStateRequests[0];
      assertExpectedAttribution(homeParams, {
        ...QUICK_START.expectedAttribution,
        source: "setup_org",
      });
    }

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
    if (EXPECT_SAMPLE_HANDOFF) {
      assert(
        onboardingPosts.length === 0,
        "Sample preview must not save onboarding or complete setup.",
      );
    } else {
      assert(onboardingPosts.length === 1, "Expected one onboarding POST.");
      assert(
        onboardingPosts[0]?.role === "AI Builder",
        `Expected quick-start role, got ${onboardingPosts[0]?.role}`,
      );
      assert(
        onboardingPosts[0]?.goals?.includes(QUICK_START.expectedGoal),
        `Expected ${QUICK_START.expectedGoal} quick-start goal, got ${JSON.stringify(
          onboardingPosts[0]?.goals,
        )}`,
      );
    }
    assert(
      setupPosts.length === 0,
      "Expected no setup organization POST on product-loop quick start.",
    );
    if (EXPECT_SAMPLE_HANDOFF) {
      await waitForCondition(
        () => sampleProjectPosts.length === 1,
        "Expected one setup-org sample-project POST.",
      );
      await waitForCondition(
        () => traceDetailRequests.length === 1,
        "Expected one sample trace detail request.",
      );
      await waitForCondition(
        () =>
          activationEventPosts.some(
            (payload) =>
              payload?.event_name === "sample_trace_detail_opened" &&
              payload?.primary_path === "sample" &&
              payload?.stage === "review_first_trace" &&
              payload?.artifact_id === "trace-smoke-1" &&
              payload?.is_sample === true &&
              hasSampleQuickStartMetadata(payload),
          ),
        "Sample trace detail activation event was not posted with sample quick-start metadata.",
      );
      assert(
        sampleProjectPosts[0]?.source === "setup_org",
        `Expected sample source setup_org, got ${sampleProjectPosts[0]?.source}`,
      );
      assert(
        sampleProjectPosts[0]?.reason === "sample_preview",
        `Expected sample reason sample_preview, got ${sampleProjectPosts[0]?.reason}`,
      );
      assert(
        sampleProjectPosts[0]?.open_after_create === true,
        `Expected open_after_create=true, got ${sampleProjectPosts[0]?.open_after_create}`,
      );
      assertExpectedAttribution(
        sampleProjectPosts[0],
        QUICK_START.expectedAttribution,
      );
      assert(
        sampleProjectResponses[0]?.activation_state?.is_activated === false,
        `Sample preview must not activate the workspace; got ${sampleProjectResponses[0]?.activation_state?.is_activated}`,
      );
      assert(
        !sampleProjectResponses[0]?.activation_state?.signals
          ?.first_observe_id &&
          !sampleProjectResponses[0]?.activation_state?.signals?.first_trace_id,
        "Sample preview must not expose real observe or trace identifiers.",
      );
    } else {
      assert(
        activationStateRequests.length === 1,
        `Expected one activation-state request, got ${activationStateRequests.length}`,
      );
      assertExpectedAttribution(activationStateRequests[0], {
        ...QUICK_START.expectedAttribution,
        source: "setup_org",
      });
    }
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
            console_messages: consoleMessages,
            home_params: homeParams,
            onboarding_post: onboardingPosts[0],
            request_failures: requestFailures,
            sample_project_post: sampleProjectPosts[0],
            sample_project_response: sampleProjectResponses[0],
            sample_trace_activation_event: activationEventPosts.find(
              (payload) => payload?.event_name === "sample_trace_detail_opened",
            ),
            sample_trace_entry: EXPECT_SAMPLE_HANDOFF
              ? {
                  clicks_after_quick_start: 1,
                  quick_start_goal: "explore_sample_data",
                  quick_start_id: "sample_preview",
                  quick_start_primary_path: "sample",
                  source: "setup_org",
                }
              : null,
            sample_trace_url: sampleTraceUrl,
            screenshot: SCREENSHOT_PATH,
            setup_org_home_url: setupOrgHomeUrl,
            setup_posts: setupPosts,
            setup_quick_start: SAMPLE_PREVIEW_GUARD
              ? "sample_preview_guarded_to_observe"
              : QUICK_START_KEY,
            trace_detail_requests: traceDetailRequests,
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
            activation_event_posts: activationEventPosts,
            api_failures: apiFailures,
            body_text: await safeBodyText(page),
            console_messages: consoleMessages,
            page_errors: pageErrors,
            request_failures: requestFailures,
            sample_project_posts: sampleProjectPosts,
            setup_quick_start: QUICK_START_KEY,
            onboarding_posts: onboardingPosts,
            setup_posts: setupPosts,
            trace_detail_requests: traceDetailRequests,
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

function assertExpectedAttribution(actual, expected) {
  for (const [key, value] of Object.entries(expected)) {
    assert(
      actual?.[key] === value,
      `Expected ${key}=${value}, got ${actual?.[key]} in ${JSON.stringify(
        actual,
      )}`,
    );
  }
}

async function installRuntime(
  page,
  auth,
  {
    activationEventPosts,
    activationStateRequests,
    getSetupCompleted,
    onboardingPosts,
    onSetupComplete,
    sampleProjectPosts,
    sampleProjectResponses,
    setupPosts,
    traceDetailRequests,
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
      const payload = parseJsonPostData(request.postData());
      activationEventPosts.push(payload);
      await respondJson(request, {
        status: true,
        result: {
          event_id: "00000000-0000-4000-8000-000000000199",
          event_name: payload?.event_name || "onboarding_event",
          activation_state: stubbedActivationState(auth),
        },
      });
      return;
    }

    if (normalizedPath === "/accounts/sample-project/") {
      const payload = parseJsonPostData(request.postData());
      sampleProjectPosts.push(payload);
      const result = sampleProjectOpenResponse(auth);
      sampleProjectResponses.push(result);
      await respondJson(request, {
        status: true,
        result,
      });
      return;
    }

    if (normalizedPath === "/tracer/trace/trace-smoke-1/") {
      traceDetailRequests.push(normalizedPath);
      await respondJson(request, traceDetailResponse());
      return;
    }

    await request.continue();
  });
}

function stubbedActivationState(auth) {
  const activationState = QUICK_START.activationState
    ? QUICK_START.activationState(QUICK_START)
    : getActivationStateFixture(QUICK_START.fixture);

  return {
    ...activationState,
    organization_id: auth.organizationId,
    request_id: "setup_org_completion_smoke",
    user_id: auth.user.id,
    workspace_id: auth.workspaceId,
  };
}

function pathFocusActivationState(profile) {
  const activationState = getActivationStateFixture("observeNoSetup");
  const details = pathFocusDetails(profile.primaryPath);
  const pathHref = `/dashboard/home?path=${profile.primaryPath}`;

  return {
    ...activationState,
    goal: details.goal,
    primary_path: profile.primaryPath,
    stage: details.stage,
    progress: {
      build: "selected",
      test: "not_started",
      observe: "available",
      ship: "available",
      improve: "available",
    },
    recommended_action: {
      id: details.actionId,
      kind: details.actionKind,
      title: details.actionTitle,
      description: details.actionDescription,
      href: details.href,
      cta_label: details.cta,
      estimated_minutes: 3,
      priority: 100,
      blocked: false,
      blocked_reason: null,
      requires_permission: details.requiresPermission,
      completion_event: details.completionEvent,
      is_sample: false,
      route_available: true,
      fallback_href: "/dashboard/get-started",
      analytics: {
        event_name: "onboarding_recommended_action_clicked",
        source: "home",
        target_path: profile.primaryPath,
      },
    },
    available_paths: [
      {
        id: profile.primaryPath,
        label: details.pathLabel,
        description: details.pathDescription,
        status: "selected",
        href: pathHref,
        is_available: true,
        blocked_reason: null,
        requires_permission: details.requiresPermission,
        first_action_id: details.actionId,
      },
    ],
    feature_flags: {
      ...activationState.feature_flags,
      [details.flagName]: true,
    },
    route_availability: {
      ...activationState.route_availability,
      [`path_${profile.primaryPath}`]: {
        href: pathHref,
        is_available: true,
        reason: null,
      },
      [details.actionId]: {
        href: details.href,
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

function pathFocusDetails(primaryPath) {
  const details = {
    evals: {
      goal: "evaluate_quality",
      stage: "create_eval_dataset",
      pathLabel: "Evaluate quality",
      pathDescription: "Create a small eval and review the first failure.",
      flagName: "onboarding_eval_path",
      actionId: "create_eval_dataset",
      actionKind: "setup",
      actionTitle: "Create eval source",
      actionDescription: "Add a focused dataset or trace source.",
      cta: "Create dataset",
      href: "/dashboard/evaluations/create?source=onboarding&step=dataset",
      requiresPermission: "evals:write",
      completionEvent: "eval_dataset_created",
    },
    voice: {
      goal: "connect_voice_ai_agent",
      stage: "create_voice_agent",
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
      requiresPermission: "voice:write",
      completionEvent: "voice_agent_created",
    },
  };
  const selected = details[primaryPath];
  assert(selected, `Unsupported path focus quick start: ${primaryPath}`);
  return selected;
}

function sampleProjectOpenResponse(auth) {
  const activationState = getActivationStateFixture("sampleTraceReady");
  const entryRoute =
    "/dashboard/observe/observe-smoke-project/trace/trace-smoke-1?sample=true&from=onboarding";

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
      last_opened_at: "2026-05-30T10:00:00Z",
      real_setup_href: "/dashboard/observe?setup=true&source=onboarding",
    },
    activation_state: {
      ...activationState,
      is_activated: false,
      organization_id: auth.organizationId,
      request_id: "setup_org_sample_preview_smoke",
      user_id: auth.user.id,
      workspace_id: auth.workspaceId,
      signals: {
        ...activationState.signals,
        first_observe_id: null,
        first_trace_id: null,
        observe_projects: 0,
        traces: 0,
      },
      sample_project: {
        ...activationState.sample_project,
        available: true,
        created: true,
        status: "ready_for_observe",
        href: entryRoute,
        entry_route: entryRoute,
        entry_routes: [entryRoute],
      },
    },
  };
}

function traceDetailResponse() {
  return {
    status: true,
    result: {
      trace: {
        id: "trace-smoke-1",
        project: "observe-smoke-project",
        name: "Sample checkout trace",
        input: {
          prompt: "Summarize the customer request.",
        },
        output: {
          answer: "The customer needs setup guidance.",
        },
        external_id: "sample-checkout-trace",
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

async function waitForCondition(
  condition,
  message,
  { interval = 100, timeout = 30000 } = {},
) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeout) {
    if (condition()) return;
    await new Promise((resolve) => {
      setTimeout(resolve, interval);
    });
  }
  throw new Error(message);
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

async function waitForSetupOrgHomeRoute(
  page,
  expectedParams,
  { timeout = 30000 } = {},
) {
  const handle = await page.waitForFunction(
    (expected) => {
      if (window.location.pathname !== "/dashboard/home") return false;
      const searchParams = new URLSearchParams(window.location.search);
      if (searchParams.get("source") !== "setup_org") return false;
      const matched = Object.entries(expected).every(
        ([key, value]) => searchParams.get(key) === value,
      );
      if (!matched) return false;
      return `${window.location.pathname}${window.location.search}${window.location.hash}`;
    },
    { timeout },
    expectedParams,
  );
  return handle.jsonValue();
}

async function expectVisibleTestId(page, testId, timeout = 30000) {
  await page.waitForSelector(`[data-testid="${testId}"]`, {
    visible: true,
    timeout,
  });
}

async function expectNoSelector(page, selector, timeout = 30000) {
  await page.waitForFunction(
    (targetSelector) => !document.querySelector(targetSelector),
    { timeout },
    selector,
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

async function waitForNoVisibleText(page, text, { timeout = 30000 } = {}) {
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

async function clickVisibleButtonText(page, text, timeout = 30000) {
  const handle = await page.waitForFunction(
    (expectedText) => {
      const normalized = (value) => String(value || "").trim();
      const matchesButton = (element) =>
        normalized(element.getAttribute("aria-label")) === expectedText ||
        normalized(element.getAttribute("aria-label")).includes(expectedText) ||
        normalized(element.textContent) === expectedText ||
        normalized(element.textContent).includes(expectedText);
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
      return Array.from(
        document.querySelectorAll("button, [role='button']"),
      ).find((element) => isVisible(element) && matchesButton(element));
    },
    { timeout },
    text,
  );
  if (!handle.asElement()) {
    throw new Error(`Button not found: ${text}`);
  }
  await handle.evaluate((element) => {
    element.click();
  });
  await handle.dispose();
}

async function isVisibleButtonText(page, text) {
  return page.evaluate((expectedText) => {
    const normalized = (value) => String(value || "").trim();
    const matchesButton = (element) =>
      normalized(element.getAttribute("aria-label")) === expectedText ||
      normalized(element.getAttribute("aria-label")).includes(expectedText) ||
      normalized(element.textContent) === expectedText ||
      normalized(element.textContent).includes(expectedText);
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
    return Array.from(
      document.querySelectorAll("button, [role='button']"),
    ).some((element) => isVisible(element) && matchesButton(element));
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
  return path.startsWith("/accounts/") || path.startsWith("/tracer/trace/");
}

function isSetupSmokeApiUrl(url) {
  return (
    url.includes("/accounts/user-info/") ||
    url.includes("/accounts/onboarding/") ||
    url.includes("/accounts/team/users/") ||
    url.includes("/accounts/activation-state/") ||
    url.includes("/accounts/activation-events/") ||
    url.includes("/accounts/sample-project/") ||
    url.includes("/tracer/trace/trace-smoke-1/")
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
    if (url) return Object.fromEntries(url.searchParams);
    try {
      return JSON.parse(value);
    } catch {
      return {};
    }
  }
  if (value instanceof URLSearchParams) {
    return Object.fromEntries(value);
  }
  if (typeof value === "object") {
    return value;
  }
  return {};
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
