/* eslint-disable no-console */
import { Buffer } from "node:buffer";
import { createRequire } from "node:module";
import process from "node:process";
import { assert } from "../lib/api-client.mjs";
import { getActivationStateFixture } from "../../../src/sections/onboarding-home/fixtures/activation-state.fixtures.js";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const AGENT_ID = "agent-1";
const VERSION_ID = "version-1";
const NODE_TEMPLATE_ID = "node-template-llm";
const USER_ID = "00000000-0000-4000-8000-000000000104";
const ORGANIZATION_ID = "00000000-0000-4000-8000-000000000204";
const WORKSPACE_ID = "00000000-0000-4000-8000-000000000304";
const HOME_SCREENSHOT_PATH =
  process.env.AGENT_HOME_CONTROLLED_SCREENSHOT ||
  "/tmp/agent-home-start-controlled-smoke.png";
const SCREENSHOT_PATH =
  process.env.AGENT_HOME_TO_CREATE_CONTROLLED_SCREENSHOT ||
  "/tmp/agent-home-to-create-controlled-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/agent-home-to-create-controlled-smoke-failure.png";
const SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY =
  "futureagi.setup_quick_start_attribution";
const AGENT_QUICK_START_ATTRIBUTION = {
  quickStartGoal: "build_ai_agent",
  quickStartId: "agent",
  quickStartPrimaryPath: "agent",
};
const AGENT_QUICK_START_PARAMS = {
  quick_start_goal: "build_ai_agent",
  quick_start_id: "agent",
  quick_start_primary_path: "agent",
};
const AGENT_QUICK_START_QUERY = new URLSearchParams(
  AGENT_QUICK_START_PARAMS,
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
  const graphCreatePosts = [];
  const nodeCreatePosts = [];
  const agentRequests = [];
  const stubbedApiRequests = [];
  const evidence = {};

  await installControlledWebSocket(page);
  await installBrowserState(page, auth);
  await installRuntime(page, auth, {
    activationEventPosts,
    agentRequests,
    graphCreatePosts,
    nodeCreatePosts,
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

  try {
    await page.goto(
      `${APP_BASE}/dashboard/home?source=setup_org&${AGENT_QUICK_START_QUERY}`,
      { waitUntil: "domcontentloaded" },
    );
    await waitForVisibleText(page, "Watch your agent handle a hard call", {
      exact: true,
    });
    await waitForVisibleText(
      page,
      "Run your agent on a real scenario, see where it failed, and lock in coverage. Current step: Stand up an agent you can run. After it is done, Home will show Give it a prompt and a model.",
    );
    await waitForVisibleText(page, "Current step", { exact: true });
    await waitForVisibleText(page, "Step 1 of 6", { exact: true });
    await waitForVisibleText(page, "Stand up an agent you can run", {
      exact: true,
    });
    await waitForVisibleText(page, "Create agent", { exact: true });
    await waitForVisibleText(page, "Later steps", { exact: true });
    await waitForVisibleText(page, "Give it a prompt and a model", {
      exact: true,
    });
    evidence.home_route = await currentRelativeUrl(page);
    assertAgentQuickStartParams(evidence.home_route, "Home route");
    evidence.home_cta_href = await visibleLinkHref(page, "Create agent", {
      exact: true,
    });
    assertAgentQuickStartParams(evidence.home_cta_href, "Home CTA");
    assertHomeAgentCta(evidence.home_cta_href);
    await page.screenshot({ path: HOME_SCREENSHOT_PATH, fullPage: true });
    evidence.home_screenshot = HOME_SCREENSHOT_PATH;

    await clickVisibleButtonText(page, "Create agent", { exact: true });
    await waitForPath(page, "/dashboard/agents");
    await waitForSearchParam(page, "onboarding", "create");
    await waitForSearchParam(page, "journey_step", "create_agent");
    await waitForSearchParam(page, "tour_anchor", "agent_create_button");
    await expectSelector(page, '[data-testid="agent-onboarding-focus"]');
    await waitForVisibleText(page, "Create the first agent", { exact: true });
    await waitForVisibleText(page, "Create one agent workflow");
    evidence.agent_create_route = await currentRelativeUrl(page);
    assertAgentQuickStartParams(
      evidence.agent_create_route,
      "Agent create route",
    );

    await clickVisibleButtonText(page, "Create Agent", { exact: true });
    await waitForPath(page, `/dashboard/agents/playground/${AGENT_ID}/build`);
    await waitForSearchParam(page, "version", VERSION_ID);
    await waitForSearchParam(page, "onboarding", "run-scenario");
    await waitForSearchParam(page, "journey_step", "add_agent_node");
    await waitForSearchParam(page, "tour_anchor", "agent_add_node_button");
    evidence.agent_builder_route = await currentRelativeUrl(page);
    assertAgentQuickStartParams(
      evidence.agent_builder_route,
      "Agent builder route",
    );
    await expectSelector(page, '[data-testid="agent-onboarding-focus"]');
    await waitForVisibleText(page, "Add a starter prompt", {
      exact: true,
    });
    await waitForVisibleText(page, "Add starter prompt", { exact: true });
    await waitForVisibleText(page, "LLM Prompt", { exact: true });
    await waitForVisibleText(page, "Agent Builder", { exact: true });

    await clickVisibleButtonText(page, "Add starter prompt", { exact: true });
    await waitForSearchParam(page, "journey_step", "run_agent_scenario");
    await waitForVisibleText(page, "Run one test scenario", {
      exact: true,
    });
    await assertNoVisibleText(page, "Step 3 of 6", { exact: true });
    await waitForVisibleText(page, "outdated pricing");
    await waitForVisibleText(page, "Save agent and run scenario", {
      exact: true,
    });
    evidence.agent_run_ready_route = await currentRelativeUrl(page);
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assertStarterPromptNodeCreate(nodeCreatePosts[0]);
    await clickVisibleButtonText(page, "Save agent and run scenario", {
      exact: true,
    });
    await waitForVisibleText(page, "Save and run", { exact: true });
    await waitForVisibleText(
      page,
      "This saves the starter prompt as the first version, then runs one scenario.",
      { exact: true },
    );
    await assertNoVisibleText(page, "Unsaved Changes", { exact: true });
    await assertNoVisibleText(page, "Node not configured", { exact: true });
    evidence.save_and_run_modal_opened = true;

    await waitForCondition(
      () =>
        activationEventPosts.some(
          (item) => item?.event_name === "agent_created",
        ),
      "Expected agent_created activation event.",
    );
    await waitForCondition(
      () =>
        activationEventPosts.some(
          (item) => item?.event_name === "agent_node_added",
        ),
      "Expected agent_node_added activation event.",
    );
    assert(
      graphCreatePosts.length === 1,
      `Expected one graph create request, got ${graphCreatePosts.length}.`,
    );
    assert(
      nodeCreatePosts.length === 1,
      `Expected one agent node create request, got ${nodeCreatePosts.length}.`,
    );
    const agentCreatedEvent = activationEventPosts.find(
      (payload) => payload?.event_name === "agent_created",
    );
    const agentNodeAddedEvent = activationEventPosts.find(
      (payload) => payload?.event_name === "agent_node_added",
    );
    assertAgentCreatedEvent(agentCreatedEvent);
    assertAgentNodeAddedEvent(agentNodeAddedEvent);
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
            activation_event_names: activationEventPosts.map(
              (payload) => payload?.event_name,
            ),
            agent_activation_events: activationEventPosts.map((payload) => ({
              event_name: payload?.event_name,
              quick_start_goal: payload?.metadata?.quick_start_goal,
              quick_start_id: payload?.metadata?.quick_start_id,
              quick_start_primary_path:
                payload?.metadata?.quick_start_primary_path,
            })),
            agent_request_count: agentRequests.length,
            graph_create_count: graphCreatePosts.length,
            node_create_count: nodeCreatePosts.length,
            stubbed_api_request_count: stubbedApiRequests.length,
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await page.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true });
    error.message = `${error.message}\nActivation events: ${activationEventPosts
      .map((payload) => payload?.event_name || payload?.eventName || "unknown")
      .join(
        ", ",
      )}\nPage errors: ${pageErrors.join("; ")}\nRequest failures: ${requestFailures.join("; ")}\nStubbed API requests: ${stubbedApiRequests
      .slice(-20)
      .join(", ")}\nFailure screenshot: ${FAILURE_SCREENSHOT_PATH}`;
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
    agentRequests,
    graphCreatePosts,
    nodeCreatePosts,
    stubbedApiRequests,
  },
) {
  await page.setRequestInterception(true);
  page.on("request", async (request) => {
    const url = new URL(request.url());
    const normalizedPath = slashPath(url.pathname);

    if (isStubbedApiPath(normalizedPath)) {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
      if (normalizedPath.startsWith("/agent-playground/")) {
        agentRequests.push(`${request.method()} ${normalizedPath}`);
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
              name: "Agent onboarding org",
              display_name: "Agent onboarding org",
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
            name: "Agent onboarding workspace",
            display_name: "Agent onboarding workspace",
            organization_id: ORGANIZATION_ID,
          },
        ],
      });
      return;
    }

    if (normalizedPath === "/accounts/activation-state/") {
      await respondJson(request, {
        status: true,
        result: agentHomeActivationState(auth),
      });
      return;
    }

    if (normalizedPath === "/accounts/activation-events/") {
      const payload = parseJsonPostData(request.postData());
      activationEventPosts.push(payload);
      await respondJson(request, {
        status: true,
        result: {
          event_id: "00000000-0000-4000-8000-000000000191",
          event_name: payload?.event_name || "onboarding_event",
          activation_state: agentCreatedActivationState(auth),
        },
      });
      return;
    }

    if (
      normalizedPath === "/agent-playground/graphs/" &&
      request.method() === "GET"
    ) {
      await respondJson(request, {
        status: true,
        result: {
          graphs: graphCreatePosts.length ? [agentGraph()] : [],
          metadata: { count: graphCreatePosts.length ? 1 : 0 },
        },
      });
      return;
    }

    if (
      normalizedPath === "/agent-playground/graphs/" &&
      request.method() === "POST"
    ) {
      graphCreatePosts.push(parseJsonPostData(request.postData()));
      await respondJson(request, {
        status: true,
        result: agentGraph(),
      });
      return;
    }

    if (normalizedPath === `/agent-playground/graphs/${AGENT_ID}/`) {
      await respondJson(request, {
        status: true,
        result: agentGraph(),
      });
      return;
    }

    if (
      normalizedPath ===
      `/agent-playground/graphs/${AGENT_ID}/versions/${VERSION_ID}/`
    ) {
      await respondJson(request, {
        status: true,
        result: agentVersionDetail(),
      });
      return;
    }

    if (
      normalizedPath ===
        `/agent-playground/graphs/${AGENT_ID}/versions/${VERSION_ID}/nodes/` &&
      request.method() === "POST"
    ) {
      const payload = parseJsonPostData(request.postData());
      nodeCreatePosts.push(payload);
      await respondJson(request, {
        status: true,
        result: agentNode(payload),
      });
      return;
    }

    if (
      normalizedPath.startsWith(
        `/agent-playground/graphs/${AGENT_ID}/versions/${VERSION_ID}/nodes/`,
      ) &&
      normalizedPath.endsWith("/possible-edge-mappings/")
    ) {
      await respondJson(request, {
        status: true,
        result: [],
      });
      return;
    }

    if (
      normalizedPath.startsWith(
        `/agent-playground/graphs/${AGENT_ID}/versions/${VERSION_ID}/nodes/`,
      ) &&
      request.method() === "GET"
    ) {
      await respondJson(request, {
        status: true,
        result: agentNode(nodeCreatePosts[0]),
      });
      return;
    }

    if (
      normalizedPath ===
      `/agent-playground/graphs/${AGENT_ID}/referenceable-graphs/`
    ) {
      await respondJson(request, {
        status: true,
        result: { graphs: [] },
      });
      return;
    }

    if (normalizedPath === "/agent-playground/node-templates/") {
      await respondJson(request, {
        status: true,
        result: {
          node_templates: [
            {
              id: NODE_TEMPLATE_ID,
              name: "llm_prompt",
              display_name: "LLM Prompt",
              description: "Add a starter prompt to the agent workflow.",
            },
          ],
        },
      });
      return;
    }

    if (normalizedPath.startsWith("/accounts/")) {
      await respondJson(request, { status: true, result: {} });
      return;
    }

    if (normalizedPath === "/model-hub/api/model_parameters/") {
      await respondJson(request, {
        status: true,
        result: { responseFormat: [] },
      });
      return;
    }

    if (normalizedPath === "/model-hub/response_schema/") {
      await respondJson(request, { results: [] });
      return;
    }

    if (normalizedPath.startsWith("/model-hub/")) {
      await respondJson(request, emptyPaginatedResponse());
      return;
    }

    if (normalizedPath.startsWith("/agent-playground/")) {
      await respondJson(request, { status: true, result: {} });
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
      sessionStorage.setItem("organizationName", "Agent onboarding org");
      sessionStorage.setItem("organizationDisplayName", "Agent onboarding org");
      sessionStorage.setItem("organizationRole", "Owner");
      sessionStorage.setItem("workspaceId", workspaceId);
      sessionStorage.setItem("workspaceName", "Agent onboarding workspace");
      sessionStorage.setItem(
        "workspaceDisplayName",
        "Agent onboarding workspace",
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
      quickStartAttribution: AGENT_QUICK_START_ATTRIBUTION,
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      setupQuickStartStorageKey: SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );
}

function agentHomeActivationState(auth) {
  const activationState = getActivationStateFixture("agentNoAgent");
  return {
    ...activationState,
    request_id: "agent_home_to_create_controlled_smoke",
    organization_id: auth.organizationId,
    workspace_id: auth.workspaceId,
    user_id: auth.user.id,
    sample_project: {
      ...activationState.sample_project,
      available: false,
    },
  };
}

function agentCreatedActivationState(auth) {
  const activationState = getActivationStateFixture("agentCreatedNoRun");
  return {
    ...activationState,
    request_id: "agent_home_to_create_controlled_smoke_created",
    organization_id: auth.organizationId,
    workspace_id: auth.workspaceId,
    user_id: auth.user.id,
    agent: {
      ...activationState.agent,
      agent_id: AGENT_ID,
      agent_version_id: VERSION_ID,
      has_agent: true,
      has_agent_version: true,
    },
    signals: {
      ...activationState.signals,
      agent_id: AGENT_ID,
      agent_version_id: VERSION_ID,
      agents: 1,
    },
    sample_project: {
      ...activationState.sample_project,
      available: false,
    },
  };
}

function agentGraph() {
  return {
    id: AGENT_ID,
    name: "Agent onboarding smoke",
    description: "Created during controlled onboarding smoke.",
    active_version_id: VERSION_ID,
    node_count: 0,
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
    created_by: {
      id: USER_ID,
      name: "Agent Onboarding Smoke",
      email: "agent-onboarding-smoke@example.com",
    },
    active_version: {
      id: VERSION_ID,
      version_number: 1,
      status: "draft",
    },
  };
}

function agentVersionDetail() {
  return {
    id: VERSION_ID,
    graph_id: AGENT_ID,
    graphId: AGENT_ID,
    version_number: 1,
    status: "draft",
    nodes: [],
    nodeConnections: [],
  };
}

function agentNode(payload = {}) {
  const promptTemplate = payload.prompt_template || {};
  return {
    id: payload.id || "agent-node-1",
    type: payload.type || "atomic",
    name: payload.name || "LLM Prompt",
    nodeTemplateId: payload.node_template_id || NODE_TEMPLATE_ID,
    position: payload.position || { x: 120, y: 120 },
    ports: payload.ports || [],
    promptTemplate: {
      promptTemplateId: "prompt-template-agent-node-1",
      promptVersionId: "prompt-version-agent-node-1",
      messages: promptTemplate.messages || [],
      model: promptTemplate.model || null,
      model_detail: promptTemplate.model_detail || null,
      response_format: promptTemplate.response_format || "text",
      template_format: promptTemplate.template_format || "mustache",
      tools: promptTemplate.tools || [],
      tool_choice: promptTemplate.tool_choice || "auto",
      temperature: promptTemplate.temperature ?? 0,
    },
  };
}

function assertStarterPromptNodeCreate(payload) {
  const promptTemplate = payload?.prompt_template;
  assert(promptTemplate, "Expected prompt_template payload for starter node.");
  assert(
    promptTemplate.model === "gpt-4o-mini",
    `Expected starter prompt model gpt-4o-mini, got ${promptTemplate.model}.`,
  );
  assert(
    promptTemplate.response_format === "text",
    `Expected starter prompt response_format text, got ${promptTemplate.response_format}.`,
  );
  const userMessage = promptTemplate.messages?.find(
    (message) => message.role === "user",
  );
  const userText = userMessage?.content?.find(
    (item) => item.type === "text",
  )?.text;
  assert(
    userText?.includes("outdated pricing"),
    `Expected starter user prompt to include outdated pricing, got ${userText}.`,
  );
}

function assertHomeAgentCta(href) {
  const url = new URL(href, APP_BASE);
  assert(
    url.pathname === "/dashboard/agents",
    `Expected Home agent CTA to open agent create, got ${url.pathname}`,
  );
  const expectedParams = {
    journey_step: "create_agent",
    onboarding: "create",
    tour_anchor: "agent_create_button",
  };
  Object.entries(expectedParams).forEach(([key, expected]) => {
    const actual = url.searchParams.get(key);
    assert(
      actual === expected,
      `Expected Home agent CTA ${key}=${expected}, got ${actual}`,
    );
  });
}

function assertAgentQuickStartParams(route, label) {
  const url = new URL(route, APP_BASE);
  for (const [key, expected] of Object.entries(AGENT_QUICK_START_PARAMS)) {
    const actual = url.searchParams.get(key);
    assert(
      actual === expected,
      `Expected ${label} ${key}=${expected}, got ${actual} in ${route}`,
    );
  }
}

function assertAgentCreatedEvent(payload) {
  assert(payload?.event_name === "agent_created", "Missing agent_created.");
  assert(payload?.primary_path === "agent", "Unexpected agent primary path.");
  assert(payload?.stage === "create_agent", "Unexpected agent stage.");
  assert(payload?.source === "agent_playground", "Unexpected agent source.");
  assert(payload?.artifact_type === "agent", "Unexpected artifact type.");
  assert(payload?.artifact_id === AGENT_ID, "Unexpected agent artifact id.");
  for (const [key, expected] of Object.entries(AGENT_QUICK_START_PARAMS)) {
    const actual = payload?.metadata?.[key];
    assert(
      actual === expected,
      `Expected agent_created metadata ${key}=${expected}, got ${actual}`,
    );
  }
}

function assertAgentNodeAddedEvent(payload) {
  assert(
    payload?.event_name === "agent_node_added",
    "Missing agent_node_added.",
  );
  assert(payload?.primary_path === "agent", "Unexpected node primary path.");
  assert(payload?.stage === "add_agent_node", "Unexpected node stage.");
  assert(payload?.source === "agent_playground", "Unexpected node source.");
  assert(
    payload?.artifact_type === "agent_node",
    "Unexpected node artifact type.",
  );
  assert(payload?.metadata?.agent_id === AGENT_ID, "Unexpected node agent id.");
  assert(
    payload?.metadata?.version_id === VERSION_ID,
    "Unexpected node version id.",
  );
  for (const [key, expected] of Object.entries(AGENT_QUICK_START_PARAMS)) {
    const actual = payload?.metadata?.[key];
    assert(
      actual === expected,
      `Expected agent_node_added metadata ${key}=${expected}, got ${actual}`,
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

async function assertNoVisibleText(page, text, { exact = false } = {}) {
  const found = await page.evaluate(
    ({ text: expectedText, exact: exactMatch }) =>
      window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      }),
    { text, exact },
  );
  assert(!found, `Unexpected visible text: ${text}`);
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
    email: "agent-onboarding-smoke@example.com",
    name: "Agent Onboarding Smoke",
    remember_me: true,
    onboarding_completed: true,
    requires_org_setup: false,
    organization_id: ORGANIZATION_ID,
    organization_role: "Owner",
    org_level: 4,
    default_workspace_id: WORKSPACE_ID,
    default_workspace_name: "Agent onboarding workspace",
    default_workspace_display_name: "Agent onboarding workspace",
    default_workspace_role: "Owner",
    ws_level: 4,
    goals: ["build_ai_agent"],
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
    path.startsWith("/agent-playground/") ||
    path.startsWith("/model-hub/")
  );
}

function emptyPaginatedResponse() {
  return {
    count: 0,
    current_page: 1,
    next: null,
    previous: null,
    results: [],
    total_pages: 1,
  };
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
