/* eslint-disable no-console */
import { Buffer } from "node:buffer";
import { createRequire } from "node:module";
import process from "node:process";
import { assert } from "../lib/api-client.mjs";
import { getActivationStateFixture } from "../../../src/sections/onboarding-home/fixtures/activation-state.fixtures.js";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const START_FROM_HOME = process.env.PROMPT_FIRST_RUN_START_FROM_HOME === "1";
const PROMPT_ID = "00000000-0000-4000-8000-000000000421";
const USER_ID = "00000000-0000-4000-8000-000000000101";
const ORGANIZATION_ID = "00000000-0000-4000-8000-000000000201";
const WORKSPACE_ID = "00000000-0000-4000-8000-000000000301";
const EVAL_TEMPLATE_ID = "00000000-0000-4000-8000-000000000521";
const EVAL_VERSION_ID = "00000000-0000-4000-8000-000000000522";
const EVAL_CONFIG_ID = "00000000-0000-4000-8000-000000000523";
const SCREENSHOT_PATH =
  process.env.PROMPT_FIRST_RUN_CONTROLLED_SCREENSHOT ||
  "/tmp/prompt-first-run-controlled-smoke.png";
const HOME_SCREENSHOT_PATH =
  process.env.PROMPT_HOME_CONTROLLED_SCREENSHOT ||
  "/tmp/prompt-home-start-controlled-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/prompt-first-run-controlled-smoke-failure.png";
const SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY =
  "futureagi.setup_quick_start_attribution";
const PROMPT_QUICK_START_ATTRIBUTION = {
  quickStartGoal: "improve_prompts",
  quickStartId: "prompt",
  quickStartPrimaryPath: "prompt",
};
const PROMPT_QUICK_START_PARAMS = {
  quick_start_goal: "improve_prompts",
  quick_start_id: "prompt",
  quick_start_primary_path: "prompt",
};
const PROMPT_QUICK_START_QUERY = new URLSearchParams(
  PROMPT_QUICK_START_PARAMS,
).toString();

const state = {
  promptName: "Prompt onboarding smoke",
  defaultVersion: null,
  evaluationConfigs: [],
  versions: {
    v1: newPromptVersion({
      version: "v1",
      isDraft: true,
      messages: blankMessages(),
    }),
  },
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
  const pageErrors = [];
  const requestFailures = [];
  const activationEventPosts = [];
  const evalConfigPosts = [];
  const promptRequests = [];
  const stubbedApiRequests = [];
  const evidence = {
    prompt_id: PROMPT_ID,
    prompt_name: state.promptName,
  };

  await installControlledWebSocket(page);
  await installBrowserState(page, auth);
  await installRuntime(page, auth, {
    activationEventPosts,
    evalConfigPosts,
    promptRequests,
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
    if (START_FROM_HOME) {
      await page.goto(
        `${APP_BASE}/dashboard/home?source=setup_org&${PROMPT_QUICK_START_QUERY}`,
        { waitUntil: "domcontentloaded" },
      );
      await waitForVisibleText(page, "Test prompts or agent prompts", {
        exact: true,
      });
      await waitForVisibleText(
        page,
        "Start with: Create prompt. Then: Run test.",
      );
      await waitForVisibleText(page, "Step 1 of 6", { exact: true });
      await waitForVisibleText(page, "Next steps", { exact: true });
      await waitForVisibleText(page, "Create prompt", { exact: true });
      evidence.home_route = await currentRelativeUrl(page);
      assertPromptQuickStartParams(evidence.home_route, "Home route");
      evidence.home_cta_href = await visibleLinkHref(page, "Create prompt", {
        exact: true,
      });
      assertPromptQuickStartParams(evidence.home_cta_href, "Home CTA");
      assertHomePromptCta(evidence.home_cta_href);
      await page.screenshot({ path: HOME_SCREENSHOT_PATH, fullPage: true });
      evidence.home_screenshot = HOME_SCREENSHOT_PATH;
      await clickVisibleButtonText(page, "Create prompt", { exact: true });
    } else {
      await page.goto(
        `${APP_BASE}/dashboard/workbench/all?source=onboarding&action=create-prompt&${PROMPT_QUICK_START_QUERY}`,
        { waitUntil: "domcontentloaded" },
      );
    }
    await waitForVisibleText(page, "Create prompt", { exact: true });
    if (
      !(await visibleTextExists(page, "Create a new prompt", { exact: true }))
    ) {
      await clickVisibleText(page, "Create prompt", { exact: true });
    }
    await waitForVisibleText(page, "Create a new prompt", { exact: true });
    await clickVisibleText(page, "Start from scratch", { exact: true });
    await waitForPath(page, `/dashboard/workbench/create/${PROMPT_ID}`);
    assert(
      (await searchParamValue(page, "onboarding")) === "run-test",
      "Prompt creation did not continue to the guided run-test route.",
    );
    await waitForSearchParam(page, "onboarding", "run-test");
    await expectSelector(page, '[data-testid="prompt-onboarding-focus"]');
    await waitForVisibleText(page, "Run one prompt test", { exact: true });
    evidence.create_route = await currentRelativeUrl(page);
    assertPromptQuickStartParams(evidence.create_route, "create route");

    await typeIntoPromptEditor(
      page,
      1,
      "Summarize this support ticket and identify the clearest next action.",
    );
    await clickSelector(page, '[data-tour-anchor="prompt_run_test_button"]');
    await waitForSearchParam(page, "onboarding", "save-version");
    await waitForVisibleText(page, "Save the prompt baseline", {
      exact: true,
    });
    evidence.first_run_route = await currentRelativeUrl(page);
    assertPromptQuickStartParams(evidence.first_run_route, "first run route");

    await commitCurrentOnboardingVersion(page, {
      message: "Baseline support-ticket prompt",
    });
    await waitForSearchParam(
      page,
      "journey_step",
      "create_second_prompt_version",
    );
    await waitForVisibleText(page, "Create a second version", { exact: true });
    evidence.baseline_commit_route = await currentRelativeUrl(page);
    assertPromptQuickStartParams(
      evidence.baseline_commit_route,
      "baseline commit route",
    );

    await dismissTourIfPresent(page);
    await clickVisibleText(page, "Create second version", { exact: true });
    await waitForSearchParam(page, "onboarding", "run-test");
    await waitForVisibleText(page, "Run one prompt test", { exact: true });
    await dismissTourIfPresent(page);
    await waitForVisibleText(page, "V2", { exact: true });
    await typeIntoPromptEditor(
      page,
      -1,
      "Rewrite this support-ticket reply with a concise customer-facing resolution plan.",
    );
    await clickSelector(page, '[data-tour-anchor="prompt_run_test_button"]');
    await waitForSearchParam(page, "onboarding", "save-version");
    await waitForVisibleText(page, "Save the prompt baseline", {
      exact: true,
    });
    evidence.second_run_route = await currentRelativeUrl(page);
    assertPromptQuickStartParams(evidence.second_run_route, "second run route");

    await commitCurrentOnboardingVersion(page, {
      message: "Second support-ticket prompt",
    });
    await waitForSearchParam(page, "journey_step", "compare_prompt_versions");
    await waitForVisibleText(page, "Compare prompt versions", { exact: true });
    evidence.second_commit_route = await currentRelativeUrl(page);
    assertPromptQuickStartParams(
      evidence.second_commit_route,
      "second commit route",
    );

    await dismissTourIfPresent(page);
    await clickVisibleText(page, "Open version history", { exact: true });
    await waitForVisibleText(page, "History", { exact: true });
    await clickVisibleText(page, "Select to compare", { exact: true });
    await clickSelector(
      page,
      '[data-testid="prompt-version-compare-checkbox-v2"]',
    );
    await clickVisibleText(page, "Compare", { exact: true });
    await waitForSearchParam(page, "onboarding", "add-failure");
    await waitForSearchParam(page, "tab", "Evaluation");
    await waitForVisibleText(page, "Capture a failure example", {
      exact: true,
    });
    await clickSelector(page, '[data-tour-anchor="prompt_add_example_button"]');
    await expectSelector(page, '[data-testid="prompt-failure-capture-focus"]');
    await waitForVisibleText(page, "Capture the prompt failure", {
      exact: true,
    });
    await waitForVisibleText(page, "Add Evaluation", { exact: true });
    const selectedVersions = JSON.parse(
      (await searchParamValue(page, "selected-versions")) || "[]",
    );
    assert(
      selectedVersions.length === 2,
      "Failure capture did not keep both compared prompt versions.",
    );
    evidence.failure_capture_route = await currentRelativeUrl(page);
    assertPromptQuickStartParams(
      evidence.failure_capture_route,
      "failure capture route",
    );

    await clickSelector(
      page,
      '[data-testid="prompt-failure-capture-focus"] button',
    );
    await waitForVisibleText(page, "All Evaluations", { exact: true });
    await waitForVisibleText(page, "No evaluations added", { exact: true });
    await clickVisibleButtonText(page, "Add Evaluations", {
      exact: true,
      rootSelector: ".MuiDrawer-paper",
    });
    await waitForVisibleText(page, "Select Evaluation", { exact: true });
    await waitForVisibleText(page, "Failure detector", { exact: true });
    await clickVisibleButtonText(page, "Add", {
      exact: true,
      rootSelector: ".MuiDrawer-paper",
    });
    await waitForVisibleText(page, "Variable Mapping");
    await waitForVisibleText(page, "model_output", { exact: true });
    await clickVisibleButtonText(page, "Add Evaluation", {
      exact: true,
      occurrence: "last",
      rootSelector: ".MuiDrawer-paper",
    });
    await waitForSearchParam(page, "onboarding", "metrics");
    await waitForSearchParam(page, "tab", "Metrics");
    await expectSelector(
      page,
      '[data-testid="prompt-metrics-onboarding-focus"]',
    );
    await waitForVisibleText(page, "Review the prompt quality signal", {
      exact: true,
    });
    await waitForVisibleText(page, "Evaluation run is queued", {
      exact: true,
    });
    assert(
      !(await visibleTextExists(
        page,
        "Add prompt to begin monitoring performance indicators",
        { exact: true },
      )),
      "Prompt onboarding metrics showed the generic add-prompt empty state.",
    );
    evidence.metrics_route = await currentRelativeUrl(page);
    assertPromptQuickStartParams(evidence.metrics_route, "metrics route");

    assert(evalConfigPosts.length === 1, "Evaluation config POST missing.");
    const evalConfigPayload = evalConfigPosts[0];
    assert(
      evalConfigPayload.id === EVAL_TEMPLATE_ID,
      "Evaluation config POST used the wrong eval template.",
    );
    assert(
      evalConfigPayload.name?.startsWith("failure_detector_workbench_"),
      "Evaluation config POST used the wrong eval name.",
    );
    assert(
      evalConfigPayload.mapping?.model_output === "output_prompt",
      "Evaluation config POST did not map model_output to prompt output.",
    );
    assert(
      evalConfigPayload.is_run === true,
      "Evaluation config POST did not request an immediate run.",
    );
    assert(
      Array.isArray(evalConfigPayload.version_to_run) &&
        evalConfigPayload.version_to_run.includes("v1") &&
        evalConfigPayload.version_to_run.includes("v2"),
      "Evaluation config POST did not run against both compared prompt versions.",
    );

    await clickVisibleButtonText(page, "Finish loop", {
      exact: true,
      rootSelector: '[data-testid="prompt-metrics-onboarding-focus"]',
    });
    await waitForPath(page, "/dashboard/home");
    await waitForSearchParam(page, "mode", "daily-quality");
    await waitForSearchParam(
      page,
      "target_event",
      "first_quality_loop_completed",
    );
    evidence.finish_loop_route = await currentRelativeUrl(page);
    assertPromptQuickStartParams(evidence.finish_loop_route, "finish route");

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    const activationEventNames = activationEventPosts
      .map((payload) => payload?.event_name)
      .filter(Boolean);
    assert(
      activationEventNames.includes("prompt_comparison_completed"),
      "Prompt comparison completion event was not recorded.",
    );
    assert(
      activationEventNames.includes("first_quality_loop_completed"),
      "Prompt first quality loop completion event was not recorded.",
    );
    const promptActivationEvents = activationEventPosts.filter((payload) =>
      ["prompt_comparison_completed", "first_quality_loop_completed"].includes(
        payload?.event_name,
      ),
    );
    assert(
      promptActivationEvents.length === 2,
      `Expected two prompt activation events, got ${promptActivationEvents.length}.`,
    );
    promptActivationEvents.forEach((payload) => {
      assertPromptQuickStartMetadata(payload, payload?.event_name);
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
          start_from_home: START_FROM_HOME,
          workspace_id: WORKSPACE_ID,
          evidence: {
            ...evidence,
            activation_event_names: activationEventNames,
            prompt_activation_events: promptActivationEvents.map((payload) => ({
              event_name: payload.event_name,
              quick_start_goal: payload.metadata?.quick_start_goal,
              quick_start_id: payload.metadata?.quick_start_id,
              quick_start_primary_path:
                payload.metadata?.quick_start_primary_path,
            })),
            prompt_request_count: promptRequests.length,
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
          prompt_requests: promptRequests,
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

async function commitCurrentOnboardingVersion(page, { message }) {
  await clickSelector(page, '[data-tour-anchor="prompt_save_version_button"]');
  await waitForVisibleText(page, "Commit changes to prompt", { exact: true });
  await page.waitForSelector(
    'textarea[placeholder="Enter a commit message for this version..."]',
    { timeout: 30000 },
  );
  await page.type(
    'textarea[placeholder="Enter a commit message for this version..."]',
    message,
  );
  await clickVisibleText(page, "Commit and set as a default version", {
    exact: true,
  });
}

async function installRuntime(
  page,
  auth,
  { activationEventPosts, evalConfigPosts, promptRequests, stubbedApiRequests },
) {
  await page.setRequestInterception(true);
  page.on("request", async (request) => {
    const url = new URL(request.url());
    const normalizedPath = slashPath(url.pathname);

    if (isStubbedApiPath(normalizedPath)) {
      stubbedApiRequests.push(`${request.method()} ${normalizedPath}`);
      if (normalizedPath.startsWith("/model-hub/prompt")) {
        promptRequests.push(`${request.method()} ${normalizedPath}`);
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
            id: WORKSPACE_ID,
            name: "Onboarding smoke workspace",
            display_name: "Onboarding smoke workspace",
            organization_id: ORGANIZATION_ID,
          },
        ],
      });
      return;
    }

    if (normalizedPath === "/accounts/activation-events/") {
      const payload = parseJsonPostData(request.postData());
      activationEventPosts.push(payload);
      const fixtureName =
        payload?.event_name === "first_quality_loop_completed"
          ? "promptActivated"
          : "promptComparisonComplete";
      await respondJson(request, {
        status: true,
        result: {
          event_id: "00000000-0000-4000-8000-000000000188",
          event_name: payload?.event_name || "onboarding_home_viewed",
          activation_state: stubbedActivationState(auth, fixtureName),
        },
      });
      return;
    }

    if (normalizedPath === "/accounts/activation-state/") {
      const fixtureName =
        START_FROM_HOME &&
        url.searchParams.get("source") === "setup_org" &&
        url.searchParams.get("quick_start_id") === "prompt"
          ? "promptNoPrompt"
          : "promptComparisonComplete";
      await respondJson(request, {
        status: true,
        result: stubbedActivationState(auth, fixtureName),
      });
      return;
    }

    if (normalizedPath === "/model-hub/prompt-folders/") {
      await respondJson(request, { status: true, result: [] });
      return;
    }

    if (
      normalizedPath === "/model-hub/prompt-templates/create-draft/" &&
      request.method() === "POST"
    ) {
      await respondJson(request, {
        status: true,
        result: {
          id: PROMPT_ID,
          rootTemplate: PROMPT_ID,
          root_template: PROMPT_ID,
        },
      });
      return;
    }

    if (normalizedPath === `/model-hub/prompt-templates/${PROMPT_ID}/`) {
      await respondJson(request, promptDetail());
      return;
    }

    if (
      normalizedPath ===
        `/model-hub/prompt-templates/${PROMPT_ID}/add-new-draft/` &&
      request.method() === "POST"
    ) {
      state.versions.v2 = newPromptVersion({
        version: "v2",
        isDraft: true,
        messages: blankMessages(),
      });
      await respondJson(request, {
        status: true,
        result: [versionSummary(state.versions.v2)],
      });
      return;
    }

    if (
      normalizedPath === `/model-hub/prompt-templates/${PROMPT_ID}/commit/` &&
      request.method() === "POST"
    ) {
      const payload = parseJsonPostData(request.postData());
      const versionName = payload?.version_name || "v1";
      const version = state.versions[versionName];
      if (version) {
        version.isDraft = false;
        version.isDefault = Boolean(payload?.set_default);
        version.commitMessage = payload?.message || "Prompt onboarding commit";
        version.output = version.output?.length
          ? version.output
          : [`Controlled output for ${versionName}`];
        version.updatedAt = new Date().toISOString();
        if (payload?.set_default) {
          state.defaultVersion = versionName;
          for (const [key, eachVersion] of Object.entries(state.versions)) {
            if (key !== versionName) eachVersion.isDefault = false;
          }
        }
      }
      await respondJson(request, { status: true, result: version || {} });
      return;
    }

    if (normalizedPath === "/model-hub/prompt-history-executions/") {
      await respondJson(request, {
        count: committedHistoryVersions().length,
        next: null,
        current_page: 1,
        results: committedHistoryVersions(),
      });
      return;
    }

    if (
      normalizedPath ===
      `/model-hub/prompt-templates/${PROMPT_ID}/compare-versions/`
    ) {
      const versions = Object.values(state.versions).map(versionRecord);
      await respondJson(request, {
        status: true,
        result: { data: versions },
      });
      return;
    }

    if (
      normalizedPath === `/model-hub/prompt-templates/${PROMPT_ID}/evaluations/`
    ) {
      await respondJson(request, {
        status: true,
        result: evaluationGridData(url),
      });
      return;
    }

    if (
      normalizedPath ===
      `/model-hub/prompt-templates/${PROMPT_ID}/evaluation-configs/`
    ) {
      await respondJson(request, {
        status: true,
        result: {
          evaluationConfigs: state.evaluationConfigs,
          template_id: PROMPT_ID,
          template_name: state.promptName,
        },
      });
      return;
    }

    if (
      normalizedPath === `/model-hub/develops/${PROMPT_ID}/get_evals_list/` &&
      request.method() === "GET"
    ) {
      await respondJson(request, {
        status: true,
        result: {
          eval_recommendations: [],
          evalRecommendations: [],
          evals: [evalTemplateCatalogRow()],
        },
      });
      return;
    }

    if (
      normalizedPath ===
        `/model-hub/eval-templates/${EVAL_TEMPLATE_ID}/detail/` &&
      request.method() === "GET"
    ) {
      await respondJson(request, {
        status: true,
        result: evalTemplateDetail(),
      });
      return;
    }

    if (
      normalizedPath ===
        `/model-hub/eval-templates/${EVAL_TEMPLATE_ID}/versions/` &&
      request.method() === "GET"
    ) {
      await respondJson(request, {
        status: true,
        result: {
          template_id: EVAL_TEMPLATE_ID,
          total: 1,
          versions: [
            {
              id: EVAL_VERSION_ID,
              is_default: true,
              model: "turing_large",
              version_number: 1,
            },
          ],
        },
      });
      return;
    }

    if (
      normalizedPath ===
        `/model-hub/prompt-templates/${PROMPT_ID}/update-evaluation-configs/` &&
      request.method() === "POST"
    ) {
      const payload = parseJsonPostData(request.postData());
      evalConfigPosts.push(payload);
      state.evaluationConfigs = [
        {
          config: payload.config || {},
          eval_required_keys: ["model_output"],
          eval_template_id: payload.id,
          eval_type: "llm",
          error_localizer: payload.error_localizer || false,
          id: EVAL_CONFIG_ID,
          mapping: payload.mapping || {},
          name: payload.name,
          output_type: "pass_fail",
          status: "running",
          template_id: payload.id,
          version_to_run: payload.version_to_run || [],
        },
      ];
      await respondJson(request, {
        status: true,
        result: {
          message:
            "Evaluation configuration updated successfully and evaluation started",
          prompt_eval_config_id: EVAL_CONFIG_ID,
          versions: payload.version_to_run || [],
        },
      });
      return;
    }

    if (normalizedPath === "/model-hub/custom-models/list/") {
      await respondJson(request, {
        count: 1,
        next: null,
        current_page: 1,
        results: [
          {
            id: "model-smoke-gpt-4o-mini",
            modelName: "gpt-4o-mini",
            providers: "openai",
            isAvailable: true,
            logoUrl: "",
            type: "chat",
          },
        ],
      });
      return;
    }

    if (normalizedPath === "/model-hub/api/model_parameters/") {
      await respondJson(request, { status: true, result: [] });
      return;
    }

    if (normalizedPath === "/model-hub/response_schema/") {
      await respondJson(request, {
        status: true,
        result: [{ id: "string", name: "String", value: "string" }],
      });
      return;
    }

    if (normalizedPath.startsWith("/accounts/")) {
      await respondJson(request, { status: true, result: {} });
      return;
    }

    if (normalizedPath.startsWith("/model-hub/")) {
      await respondJson(request, fallbackModelHubResponse(normalizedPath));
      return;
    }

    if (normalizedPath.startsWith("/tracer/")) {
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

      send(rawPayload) {
        if (!this.url.includes("/ws/prompt-stream/")) return;
        let payload = {};
        try {
          payload = JSON.parse(rawPayload);
        } catch {
          payload = {};
        }
        const version = payload.version || "v1";
        const sessionUuid = `prompt-smoke-${version}-${Date.now()}`;
        const output =
          version === "v2"
            ? "Second version gives a clearer support-ticket resolution plan."
            : "Baseline version summarizes the support ticket.";
        const metadata = {
          cost: { total_cost: 0.0001 },
          usage: { total_tokens: 42 },
          response_time: 120,
        };
        const messages = [
          {
            type: "run_prompt",
            streaming_status: "started",
            session_uuid: sessionUuid,
            version,
            result_index: 0,
            num_results: 1,
            output_format: "string",
          },
          {
            type: "run_prompt",
            streaming_status: "running",
            session_uuid: sessionUuid,
            version,
            result_index: 0,
            num_results: 1,
            chunk: output,
            output_format: "string",
          },
          {
            type: "run_prompt",
            streaming_status: "completed",
            session_uuid: sessionUuid,
            version,
            result_index: 0,
            num_results: 1,
            metadata,
            output_format: "string",
          },
          {
            type: "run_prompt",
            streaming_status: "all_completed",
            session_uuid: sessionUuid,
            version,
            result_index: 0,
            num_results: 1,
            metadata,
            output_format: "string",
          },
        ];
        messages.forEach((message, index) => {
          setTimeout(
            () => {
              this.onmessage?.({ data: JSON.stringify(message), target: this });
              if (index === messages.length - 1) {
                this.close();
              }
            },
            60 * (index + 1),
          );
        });
      }

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
      sessionStorage.setItem(
        setupQuickStartStorageKey,
        JSON.stringify(quickStartAttribution),
      );
    },
    {
      quickStartAttribution: PROMPT_QUICK_START_ATTRIBUTION,
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      setupQuickStartStorageKey: SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );
}

function promptDetail() {
  const version = state.versions.v1;
  return {
    id: PROMPT_ID,
    root_template: PROMPT_ID,
    name: state.promptName,
    is_draft: version.isDraft,
    version: version.version,
    last_saved: version.updatedAt,
    is_default: version.isDefault,
    labels: [],
    original_template: PROMPT_ID,
    template_version: version.version,
    variable_names: {},
    placeholders: {},
    prompt_config: [
      {
        messages: version.messages,
        configuration: modelConfiguration(),
        placeholders: [],
      },
    ],
    output: version.output,
    metadata: version.output.map(() => outputMetadata()),
  };
}

function committedHistoryVersions() {
  return Object.values(state.versions)
    .filter((version) => !version.isDraft)
    .sort((a, b) => (a.version < b.version ? 1 : -1))
    .map(versionRecord);
}

function versionRecord(version) {
  return {
    id: `${PROMPT_ID}-${version.version}`,
    template_name: state.promptName,
    template_version: version.version,
    is_draft: version.isDraft,
    is_default: version.isDefault,
    updated_at: version.updatedAt,
    created_at: version.createdAt,
    labels: [],
    variable_names: {},
    prompt_config_snapshot: {
      messages: version.messages,
      configuration: modelConfiguration(),
      placeholders: [],
    },
    output: version.output,
    metadata: version.output.map(() => outputMetadata()),
    commitMessage: version.commitMessage,
    commit_message: version.commitMessage,
  };
}

function versionSummary(version) {
  return {
    id: `${PROMPT_ID}-${version.version}`,
    template_version: version.version,
    updated_at: version.updatedAt,
    is_default: version.isDefault,
    is_draft: version.isDraft,
  };
}

function newPromptVersion({ version, isDraft, messages }) {
  const now = new Date().toISOString();
  return {
    version,
    isDraft,
    isDefault: false,
    createdAt: now,
    updatedAt: now,
    commitMessage: "",
    messages,
    output: [],
  };
}

function blankMessages() {
  return [
    {
      role: "system",
      content: [{ type: "text", text: "" }],
    },
    {
      role: "user",
      content: [{ type: "text", text: "" }],
    },
  ];
}

function modelConfiguration() {
  return {
    model: "gpt-4o-mini",
    model_detail: {
      model_name: "gpt-4o-mini",
      providers: "openai",
      is_available: true,
      logo_url: "",
      type: "chat",
    },
    output_format: "string",
    tool_choice: "",
    tools: [],
    model_type: "llm",
    template_format: "mustache",
  };
}

function outputMetadata() {
  return {
    cost: { total_cost: 0.0001 },
    usage: { total_tokens: 42 },
    response_time: 120,
  };
}

function evaluationGridData(url) {
  const versionsParam = url.searchParams.get("versions");
  let versions = ["v1"];
  try {
    versions = JSON.parse(versionsParam || '["v1"]');
  } catch {
    versions = ["v1"];
  }
  return versions.reduce(
    (acc, version) => {
      acc[version] = {
        eval_names: [],
        eval_output: {},
        eval_status: {},
        input: ["support ticket"],
        messages: state.versions[version]?.messages || blankMessages(),
        model_detail: modelConfiguration().model_detail,
        output: state.versions[version]?.output || [],
      };
      return acc;
    },
    { variables: {} },
  );
}

function evalTemplateCatalogRow() {
  return {
    created_by_name: "System",
    description: "Checks whether the saved prompt output captures the failure.",
    eval_required_keys: ["model_output"],
    eval_template_name: "Failure detector",
    eval_type: "llm",
    id: EVAL_TEMPLATE_ID,
    is_model_required: true,
    model: "turing_large",
    name: "Failure detector",
    output_type: "pass_fail",
    owner: "system",
    template_type: "single",
    type: "futureagi_built",
  };
}

function evalTemplateDetail() {
  return {
    check_internet: false,
    config: {
      required_keys: ["model_output"],
    },
    description: "Checks whether the saved prompt output captures the failure.",
    error_localizer_enabled: false,
    eval_type: "llm",
    id: EVAL_TEMPLATE_ID,
    instructions: "Return Passed or Failed for {{model_output}}.",
    model: "turing_large",
    name: "Failure detector",
    output_type: "pass_fail",
    owner: "system",
    pass_threshold: 0.5,
    required_keys: ["model_output"],
    template_type: "single",
  };
}

function stubbedActivationState(auth, fixtureName) {
  const activationState = getActivationStateFixture(fixtureName);
  return {
    ...activationState,
    request_id: "prompt_first_run_controlled_smoke",
    organization_id: auth.organizationId,
    workspace_id: auth.workspaceId,
    user_id: auth.user.id,
  };
}

function fallbackModelHubResponse(path) {
  if (path.includes("list") || path.includes("labels")) {
    return { count: 0, next: null, current_page: 1, results: [] };
  }
  return { status: true, result: {} };
}

async function typeIntoPromptEditor(page, index, text) {
  const selector = ".prompt-editor-card .ql-editor";
  await page.waitForFunction(
    ({ selector: editorSelector, index: requestedIndex }) => {
      const editors = window.visibleElements(editorSelector);
      const normalizedIndex =
        requestedIndex < 0 ? editors.length + requestedIndex : requestedIndex;
      return Boolean(editors[normalizedIndex]);
    },
    { timeout: 30000 },
    { selector, index },
  );
  const editors = await page.$$(selector);
  const targetIndex = index < 0 ? editors.length + index : index;
  const editor = editors[targetIndex];
  assert(editor, `Prompt editor ${index} not found.`);
  await editor.click();
  await page.keyboard.down(modifierKey());
  await page.keyboard.press("A");
  await page.keyboard.up(modifierKey());
  await page.keyboard.press("Backspace");
  await editor.type(text);
}

async function clickSelector(page, selector, timeout = 30000) {
  await page.waitForSelector(selector, { visible: true, timeout });
  await page.click(selector);
}

async function clickVisibleButtonText(
  page,
  text,
  {
    exact = false,
    occurrence = "first",
    rootSelector = "body",
    timeout = 30000,
  } = {},
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
    { timeout },
    { expectedText: text, exactMatch: exact, root: rootSelector },
  );
  const clicked = await page.evaluate(
    ({ expectedText, exactMatch, occurrenceName, root }) => {
      const rootElements = Array.from(document.querySelectorAll(root));
      if (rootElements.length === 0) return false;
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

async function searchParamValue(page, key) {
  return page.evaluate(
    (paramKey) => new URLSearchParams(window.location.search).get(paramKey),
    key,
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

async function dismissTourIfPresent(page) {
  if (await visibleTextExists(page, "Got it", { exact: true })) {
    await clickVisibleText(page, "Got it", { exact: true, timeout: 1000 });
  }
}

async function clickVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await waitForVisibleText(page, text, { exact, timeout });
  const clicked = await page.evaluate(
    ({ text: expectedText, exact: exactMatch }) => {
      const elements = window.visibleElements().filter((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
      const element =
        elements.find((candidate) => {
          const clickable = candidate.closest(
            "button,a,[role='button'],[role='menuitem'],.MuiMenuItem-root",
          );
          return clickable && !clickable.disabled;
        }) || elements[0];
      const clickable =
        element?.closest(
          "button,a,[role='button'],[role='menuitem'],.MuiMenuItem-root",
        ) || element;
      if (!clickable || clickable.disabled) return false;
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
    { text, exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
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

function assertHomePromptCta(href) {
  const url = new URL(href, APP_BASE);
  assert(
    url.pathname === "/dashboard/workbench/all",
    `Expected Home prompt CTA to open workbench, got ${url.pathname}`,
  );
  const expectedParams = {
    action: "create-prompt",
    journey_step: "start_prompt",
    source: "onboarding",
    tour_anchor: "prompt_create_button",
  };
  Object.entries(expectedParams).forEach(([key, expected]) => {
    const actual = url.searchParams.get(key);
    assert(
      actual === expected,
      `Expected Home prompt CTA ${key}=${expected}, got ${actual}`,
    );
  });
}

function assertPromptQuickStartParams(route, label) {
  const url = new URL(route, APP_BASE);
  for (const [key, expected] of Object.entries(PROMPT_QUICK_START_PARAMS)) {
    const actual = url.searchParams.get(key);
    assert(
      actual === expected,
      `Expected ${label} ${key}=${expected}, got ${actual} in ${route}`,
    );
  }
}

function assertPromptQuickStartMetadata(payload, label) {
  const metadata = payload?.metadata || {};
  for (const [key, expected] of Object.entries(PROMPT_QUICK_START_PARAMS)) {
    assert(
      metadata?.[key] === expected,
      `Expected ${label} metadata ${key}=${expected}, got ${metadata?.[key]}`,
    );
  }
}

function createStubbedAuthenticatedContext() {
  const user = {
    id: USER_ID,
    email: "onboarding-smoke@example.com",
    name: "Onboarding Smoke",
    remember_me: true,
    onboarding_completed: true,
    requires_org_setup: false,
    organization_id: ORGANIZATION_ID,
    organization_role: "Owner",
    org_level: 4,
    default_workspace_id: WORKSPACE_ID,
    default_workspace_name: "Onboarding smoke workspace",
    default_workspace_display_name: "Onboarding smoke workspace",
    default_workspace_role: "Owner",
    ws_level: 4,
    goals: ["test_prompts"],
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
    path.startsWith("/model-hub/") ||
    path.startsWith("/tracer/")
  );
}

function isStubbedApiUrl(value) {
  try {
    return isStubbedApiPath(slashPath(new URL(value).pathname));
  } catch {
    return false;
  }
}

function modifierKey() {
  return process.platform === "darwin" ? "Meta" : "Control";
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
