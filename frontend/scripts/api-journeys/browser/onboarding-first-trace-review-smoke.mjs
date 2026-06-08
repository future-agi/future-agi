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
  process.env.ONBOARDING_FIRST_TRACE_SCREENSHOT ||
  `/tmp/onboarding-first-trace-review-smoke-${VIEWPORT_NAME}.png`;
const STUB_AUTH = envFlag("ONBOARDING_SMOKE_STUB_AUTH");
const SETUP_PROVIDER = normalizeSetupValue(
  process.env.ONBOARDING_SMOKE_SETUP_PROVIDER,
);
const SETUP_LANGUAGE = normalizeSetupValue(
  process.env.ONBOARDING_SMOKE_SETUP_LANGUAGE,
);
const SETUP_PACKAGE_LABEL = setupPackageLabel({
  setupLanguage: SETUP_LANGUAGE,
  setupProvider: SETUP_PROVIDER,
});

async function main() {
  assert(STUB_AUTH, "Set ONBOARDING_SMOKE_STUB_AUTH=1 for this smoke.");

  const auth = createStubbedAuthenticatedContext();
  const activationEventPosts = [];
  const activationStateRequests = [];
  const activationStateResponses = [];
  const apiFailures = [];
  const evalTemplatePosts = [];
  const evalTemplateUpdates = [];
  const pageErrors = [];
  const requestFailures = [];
  const traceDetailRequests = [];
  let firstTraceReady = false;

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
    activationStateResponses,
    evalTemplatePosts,
    evalTemplateUpdates,
    getFirstTraceReady: () => firstTraceReady,
    requestFailures,
    traceDetailRequests,
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
    if (isFirstTraceSmokeApiUrl(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("requestfailed", (request) => {
    requestFailures.push(
      `${request.method()} ${request.url()} ${request.failure()?.errorText}`,
    );
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await page.goto(
      `${APP_BASE}/dashboard/home?${homeOnboardingSearch().toString()}`,
      {
        waitUntil: "domcontentloaded",
      },
    );
    await page.waitForFunction(
      () =>
        window.location.pathname === "/dashboard/home" &&
        new URLSearchParams(window.location.search).get("source") ===
          "onboarding",
      { timeout: 30000 },
    );

    await expectSelector(page, '[data-testid="waiting-for-signal-panel"]');
    await expectVisibleText(
      page,
      SETUP_PACKAGE_LABEL
        ? `Send one ${SETUP_PACKAGE_LABEL} trace`
        : "Send one trace",
      { exact: true },
    );
    await expectVisibleText(page, "Projects: 1 · Traces: 0", { exact: true });
    await expectVisibleText(page, "Send trace", { exact: true });

    firstTraceReady = true;
    await clickVisibleText(page, "Check again");

    await expectSelector(page, '[data-testid="first-signal-panel"]');
    await expectVisibleText(page, "First trace received", { exact: true });
    await expectVisibleText(page, "trace-1", { exact: true });
    await expectVisibleText(page, "Not reviewed", { exact: true });

    const reviewTraceHref = await visibleLinkHrefByText(page, "Review trace", {
      rootSelector: '[data-testid="first-signal-panel"]',
    });
    const reviewTraceUrl = new URL(reviewTraceHref, APP_BASE);
    assert(
      reviewTraceUrl.pathname === "/dashboard/observe/observe-1/trace/trace-1",
      `Unexpected review trace path: ${reviewTraceHref}`,
    );
    assert(
      reviewTraceUrl.searchParams.get("source") === "onboarding" &&
        reviewTraceUrl.searchParams.get("onboarding") === "review-first-trace",
      `Unexpected review trace params: ${reviewTraceHref}`,
    );
    assertSetupContext(reviewTraceUrl.searchParams, reviewTraceHref);

    await clickVisibleText(page, "Review trace", {
      rootSelector: '[data-testid="first-signal-panel"]',
    });
    await page.waitForFunction(
      ({ setupLanguage, setupProvider }) => {
        const params = new URLSearchParams(window.location.search);
        const routeMatched =
          window.location.pathname ===
            "/dashboard/observe/observe-1/trace/trace-1" &&
          params.get("source") === "onboarding" &&
          params.get("onboarding") === "review-first-trace";
        const setupMatched =
          (!setupProvider || params.get("provider") === setupProvider) &&
          (!setupLanguage || params.get("language") === setupLanguage);
        return routeMatched && setupMatched;
      },
      { timeout: 30000 },
      {
        setupLanguage: SETUP_LANGUAGE,
        setupProvider: SETUP_PROVIDER,
      },
    );

    await waitForCondition(
      () =>
        activationEventPosts.some(
          (payload) =>
            payload?.event_name === "trace_detail_opened" &&
            payload?.primary_path === "observe" &&
            payload?.stage === "review_first_trace" &&
            payload?.artifact_type === "trace" &&
            payload?.artifact_id === "trace-1" &&
            payload?.project_id === "observe-1",
        ),
      "Trace detail activation event was not posted.",
    );
    await expectVisibleText(page, "Trace", { exact: true });
    await expectVisibleText(page, "trace-1");
    await expectVisibleText(
      page,
      SETUP_PACKAGE_LABEL
        ? `${SETUP_PACKAGE_LABEL} trace received`
        : "First trace received",
      { exact: true },
    );
    await expectVisibleText(page, "Create quality check", { exact: true });
    await clickVisibleText(page, "Create quality check");
    await page.waitForFunction(
      ({ setupLanguage, setupProvider }) => {
        const params = new URLSearchParams(window.location.search);
        const routeMatched =
          window.location.pathname ===
            "/dashboard/evaluations/create/eval-draft-1" &&
          params.get("source") === "onboarding" &&
          params.get("step") === "run" &&
          params.get("source_type") === "trace_project" &&
          params.get("source_id") === "observe-1";
        const setupMatched =
          (!setupProvider || params.get("provider") === setupProvider) &&
          (!setupLanguage || params.get("language") === setupLanguage);
        return routeMatched && setupMatched;
      },
      { timeout: 30000 },
      {
        setupLanguage: SETUP_LANGUAGE,
        setupProvider: SETUP_PROVIDER,
      },
    );
    await expectSelector(page, '[data-testid="eval-onboarding-focus"]');
    await expectVisibleText(page, "Eval setup", { exact: true });
    await expectVisibleText(page, "Run quality check on trace project", {
      exact: true,
    });
    await expectVisibleText(
      page,
      SETUP_PACKAGE_LABEL
        ? `${SETUP_PACKAGE_LABEL} trace project ready`
        : "Trace project ready",
      { exact: true },
    );
    await expectVisibleText(
      page,
      SETUP_PACKAGE_LABEL
        ? `Run the saved quality check on ${SETUP_PACKAGE_LABEL} traces.`
        : "Run the saved quality check on this trace project.",
      { exact: true },
    );
    await waitForCondition(
      () =>
        activationEventPosts.some(
          (payload) =>
            payload?.event_name === "onboarding_eval_source_selected" &&
            payload?.primary_path === "evals" &&
            payload?.stage === "create_eval_dataset" &&
            payload?.artifact_type === "observe_project" &&
            payload?.artifact_id === "observe-1",
        ),
      "Eval source selection activation event was not posted.",
      30000,
    );
    await waitForCondition(
      () =>
        activationEventPosts.some(
          (payload) =>
            payload?.event_name === "eval_scorer_created" &&
            payload?.primary_path === "evals" &&
            payload?.stage === "add_eval_scorer" &&
            payload?.artifact_type === "eval_scorer" &&
            payload?.artifact_id === "eval-draft-1",
        ),
      "Starter eval scorer activation event was not posted.",
      30000,
    );
    await waitForCondition(
      () =>
        activationEventPosts.some(
          (payload) =>
            payload?.event_name === "onboarding_eval_route_focus_viewed" &&
            payload?.primary_path === "evals" &&
            payload?.stage === "run_eval" &&
            payload?.artifact_type === "eval" &&
            payload?.artifact_id === "observe-1" &&
            payload?.metadata?.draft_id === "eval-draft-1",
        ),
      "Eval run-step focus activation event was not posted.",
      30000,
    );

    assert(
      activationStateResponses
        .map((response) => response.stage)
        .join(" -> ") === "waiting_for_first_trace -> review_first_trace",
      `Unexpected activation sequence: ${activationStateResponses
        .map((response) => response.stage)
        .join(" -> ")}`,
    );
    assert(
      traceDetailRequests.length === 1,
      `Expected one trace detail request, got ${traceDetailRequests.length}`,
    );
    assert(
      evalTemplatePosts.length === 1,
      `Expected one eval draft create request, got ${evalTemplatePosts.length}`,
    );
    assert(
      evalTemplateUpdates.length === 1,
      `Expected one eval draft update request, got ${evalTemplateUpdates.length}`,
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
          viewport: VIEWPORT_NAME,
          evidence: {
            activation_event_posts: activationEventPosts.map((payload) => ({
              artifact_id: payload.artifact_id,
              artifact_type: payload.artifact_type,
              event_name: payload.event_name,
              primary_path: payload.primary_path,
              project_id: payload.project_id,
              stage: payload.stage,
            })),
            activation_state_requests: activationStateRequests,
            activation_state_sequence: activationStateResponses.map(
              (response) => response.stage,
            ),
            create_evaluator_url: relativeUrl(page.url()),
            eval_template_posts: evalTemplatePosts,
            eval_template_updates: evalTemplateUpdates,
            review_trace_href: reviewTraceHref,
            screenshot: SCREENSHOT_PATH,
            trace_detail_requests: traceDetailRequests,
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
            activation_event_posts: activationEventPosts,
            activation_state_requests: activationStateRequests,
            activation_state_responses: activationStateResponses,
            api_failures: apiFailures,
            body_text: await safeBodyText(page),
            eval_template_posts: evalTemplatePosts,
            eval_template_updates: evalTemplateUpdates,
            page_errors: pageErrors,
            request_failures: requestFailures.slice(-20),
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

function normalizeSetupValue(value) {
  return typeof value === "string" ? value.trim().toLowerCase() : "";
}

function homeOnboardingSearch() {
  const params = new URLSearchParams();
  params.set("source", "onboarding");
  if (SETUP_PROVIDER) params.set("provider", SETUP_PROVIDER);
  if (SETUP_LANGUAGE) params.set("language", SETUP_LANGUAGE);
  return params;
}

function assertSetupContext(params, href) {
  if (SETUP_PROVIDER) {
    assert(
      params.get("provider") === SETUP_PROVIDER,
      `Expected provider ${SETUP_PROVIDER} in href: ${href}`,
    );
  }
  if (SETUP_LANGUAGE) {
    assert(
      params.get("language") === SETUP_LANGUAGE,
      `Expected language ${SETUP_LANGUAGE} in href: ${href}`,
    );
  }
}

function setupPackageLabel({ setupLanguage, setupProvider } = {}) {
  const providerLabels = {
    anthropic: "Anthropic",
    bedrock: "Amazon Bedrock",
    langchain: "LangChain",
    llama_index: "LlamaIndex",
    llamaindex: "LlamaIndex",
    mcp: "MCP",
    openai: "OpenAI",
    openai_agents: "OpenAI Agents",
  };
  const languageLabels = {
    python: "Python",
    typescript: "TypeScript",
  };
  return [providerLabels[setupProvider], languageLabels[setupLanguage]]
    .filter(Boolean)
    .join(" ");
}

async function installRuntime(
  page,
  auth,
  {
    activationEventPosts,
    activationStateRequests,
    activationStateResponses,
    evalTemplatePosts,
    evalTemplateUpdates,
    getFirstTraceReady,
    requestFailures,
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
      await respondJson(request, auth.user);
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

    if (normalizedPath === "/accounts/activation-state/") {
      const activationState = stubbedActivationState(auth, {
        firstTraceReady: getFirstTraceReady(),
      });
      activationStateRequests.push(Object.fromEntries(url.searchParams));
      activationStateResponses.push({
        request_id: activationState.request_id,
        stage: activationState.stage,
      });
      await respondJson(request, {
        status: true,
        result: activationState,
      });
      return;
    }

    if (normalizedPath === "/accounts/activation-events/") {
      const payload = parseJsonPostData(request.postData());
      activationEventPosts.push(payload);
      const isTraceReview = payload?.event_name === "trace_detail_opened";
      await respondJson(request, {
        status: true,
        result: {
          event_id: "00000000-0000-4000-8000-000000000288",
          event_name: isTraceReview
            ? "trace_reviewed"
            : payload?.event_name || "onboarding_home_viewed",
          activation_state: stubbedActivationState(auth, {
            firstTraceReady: !isTraceReview,
            fixtureName: isTraceReview
              ? "observeNeedsEvaluator"
              : "observeFirstTraceReady",
          }),
        },
      });
      return;
    }

    if (normalizedPath === "/tracer/trace/trace-1/") {
      traceDetailRequests.push(`${request.method()} ${normalizedPath}`);
      await respondJson(request, traceDetailResponse());
      return;
    }

    if (normalizedPath === "/tracer/saved-views/") {
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
      normalizedPath === "/model-hub/eval-templates/create-v2/" &&
      request.method() === "POST"
    ) {
      evalTemplatePosts.push(parseJsonPostData(request.postData()));
      await respondJson(request, {
        status: true,
        result: {
          id: "eval-draft-1",
        },
      });
      return;
    }

    if (normalizedPath === "/model-hub/eval-templates/eval-draft-1/detail/") {
      await respondJson(request, {
        status: true,
        result: {
          id: "eval-draft-1",
          config: {},
          eval_type: "agent",
          output_type_normalized: "pass_fail",
          pass_threshold: 0.5,
        },
      });
      return;
    }

    if (
      normalizedPath === "/model-hub/eval-templates/eval-draft-1/update/" &&
      request.method() === "PUT"
    ) {
      evalTemplateUpdates.push(parseJsonPostData(request.postData()));
      await respondJson(request, {
        status: true,
        result: {
          id: "eval-draft-1",
        },
      });
      return;
    }

    try {
      await request.continue();
    } catch (error) {
      requestFailures.push(
        `${request.method()} ${request.url()} ${error.message}`,
      );
    }
  });
}

function stubbedActivationState(
  auth,
  { firstTraceReady = false, fixtureName = null } = {},
) {
  const selectedFixture =
    fixtureName ||
    (firstTraceReady ? "observeFirstTraceReady" : "observeWaitingForTrace");
  const activationState = getActivationStateFixture(selectedFixture);
  const firstTraceReviewHref =
    "/dashboard/observe/observe-1/trace/trace-1?source=onboarding&onboarding=review-first-trace";
  const recommendedAction =
    selectedFixture === "observeFirstTraceReady" &&
    activationState.recommended_action
      ? {
          ...activationState.recommended_action,
          href: firstTraceReviewHref,
        }
      : activationState.recommended_action;
  const routeAvailability =
    selectedFixture === "observeFirstTraceReady"
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
    request_id: `first_trace_review_smoke_${selectedFixture}`,
    organization_id: auth.organizationId,
    workspace_id: auth.workspaceId,
    user_id: auth.user.id,
  };
}

function traceDetailResponse() {
  const startedAt = "2026-05-26T15:04:00Z";
  const endedAt = "2026-05-26T15:04:01Z";
  const span = {
    observation_span: {
      id: "span-1",
      trace: "trace-1",
      project: "observe-1",
      parent_span_id: null,
      name: "chat.completions.create",
      observation_type: "llm",
      status: "OK",
      start_time: startedAt,
      end_time: endedAt,
      latency_ms: 420,
      total_tokens: 42,
      prompt_tokens: 28,
      completion_tokens: 14,
      cost: 0.00042,
      model: "gpt-4o-mini",
      provider: "openai",
      input: "Summarize the customer request.",
      output: "The customer needs setup guidance.",
      span_attributes: {
        "gen_ai.request.model": "gpt-4o-mini",
        "gen_ai.system": "openai",
      },
      tags: [],
    },
    children: [],
    eval_scores: [],
    annotations: [],
  };

  return {
    status: true,
    result: {
      trace: {
        id: "trace-1",
        project: "observe-1",
        project_version: null,
        name: "First checkout trace",
        metadata: {
          onboarding: true,
        },
        input: {
          prompt: "Summarize the customer request.",
        },
        output: {
          answer: "The customer needs setup guidance.",
        },
        error: null,
        session: null,
        external_id: "first-checkout-trace",
        tags: [],
      },
      observation_spans: [span],
      summary: {
        total_spans: 1,
        total_duration_ms: 420,
        total_tokens: 42,
        total_prompt_tokens: 28,
        total_completion_tokens: 14,
        total_cost: 0.00042,
        error_count: 0,
        span_type_counts: {
          llm: 1,
        },
      },
      graph: {
        nodes: [
          {
            id: "span-1",
            name: "chat.completions.create",
            type: "llm",
            latency_ms: 420,
            tokens: 42,
            status: "OK",
          },
        ],
        edges: [],
      },
    },
  };
}

function createStubbedAuthenticatedContext() {
  const userId = "00000000-0000-4000-8000-000000000121";
  const organizationId = "00000000-0000-4000-8000-000000000221";
  const workspaceId = "00000000-0000-4000-8000-000000000321";
  const user = {
    id: userId,
    email: "first-trace-smoke@example.com",
    name: "First Trace Smoke",
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
  return (
    path.startsWith("/accounts/") ||
    path.startsWith("/model-hub/eval-templates/") ||
    path.startsWith("/tracer/trace/") ||
    path.startsWith("/tracer/saved-views/")
  );
}

function isFirstTraceSmokeApiUrl(url) {
  return (
    url.includes("/accounts/activation-state/") ||
    url.includes("/accounts/activation-events/") ||
    url.includes("/model-hub/eval-templates/") ||
    url.includes("/tracer/trace/trace-1/") ||
    url.includes("/tracer/saved-views/")
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
