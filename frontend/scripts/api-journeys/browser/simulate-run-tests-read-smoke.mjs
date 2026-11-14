/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.SIMULATE_RUN_TESTS_SCREENSHOT ||
  "/tmp/simulate-run-tests-read-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const runId = auth.runId.replace(/[^a-z0-9-]/gi, "-");
  const namePrefix = `browser sim run ${runId}`;
  const runName = `${namePrefix} read surfaces`;

  await hardDeleteRunTestFixtures({
    namePrefix,
    organizationId: auth.organizationId,
  });

  const seed = await selectSimulationRunTestSeed(auth.client);
  assert(
    seed,
    "No completed text simulation seed with an agent definition, version, and runnable scenario was available.",
  );

  let runTestId = null;
  let testExecutionId = null;
  let callExecutionIds = [];

  const pageErrors = [];
  const apiFailures = [];
  const observedResponses = [];
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });

  try {
    const created = await auth.client.post(
      apiPath("/simulate/run-tests/create/"),
      {
        name: runName,
        description:
          "Temporary run simulation fixture for browser read coverage.",
        agent_definition_id: seed.agentDefinitionId,
        agent_version: seed.agentVersionId,
        scenario_ids: [seed.scenarioId],
        enable_tool_evaluation: false,
      },
    );
    runTestId = created?.id;
    assert(isUuid(runTestId), "Run-test create did not return a UUID id.");

    const chatExecution = await auth.client.post(
      apiPath("/simulate/run-tests/{run_test_id}/chat-execute/", {
        run_test_id: runTestId,
      }),
      {},
    );
    testExecutionId = firstUuid(chatExecution?.execution_id);
    assert(
      testExecutionId,
      "Run-test chat execution did not return a test execution UUID.",
    );

    const batch = await auth.client.post(
      apiPath(
        "/simulate/test-executions/{test_execution_id}/chat/call-executions/batch/",
        { test_execution_id: testExecutionId },
      ),
      {},
    );
    callExecutionIds = asArray(batch?.call_execution_ids).filter(isUuid);
    assert(
      callExecutionIds.length > 0,
      "Chat call-execution batch did not create any call executions.",
    );

    const dbSeed = await markRunExecutionReadable({
      runTestId,
      testExecutionId,
      callExecutionIds,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assert(
      Number(dbSeed.updated_call_count) === callExecutionIds.length,
      `DB fixture update did not touch all calls: ${JSON.stringify(dbSeed)}`,
    );

    const page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installAuthState(page, auth);

    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("response", (response) => {
      const url = response.url();
      const tracksSimulation =
        url.includes("/simulate/run-tests/") ||
        url.includes("/simulate/test-executions/") ||
        url.includes("/simulate/call-executions/");
      if (!tracksSimulation) return;
      const status = response.status();
      observedResponses.push(`${response.request().method()} ${status} ${url}`);
      if (status >= 400) apiFailures.push(`${status} ${url}`);
    });

    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.request().method() === "GET" &&
          response.url().includes("/simulate/run-tests/") &&
          response.status() < 400,
        { timeout: 60000 },
      ),
      page.goto(`${APP_BASE}/dashboard/simulate/test`, {
        waitUntil: "domcontentloaded",
      }),
    ]);

    await expectVisibleText(page, "Run Simulation");
    await searchRunTests(page, runName);
    await expectVisibleText(page, runName);
    await expectVisibleText(page, "Create a Simulation");
    await expectNoVisibleText(page, "Invalid Date");
    await expectNoVisibleText(page, "undefined");

    const [detailResponse, executionsResponse] = await Promise.all([
      page.waitForResponse(
        (response) =>
          response.request().method() === "GET" &&
          responsePath(response) === `/simulate/run-tests/${runTestId}/` &&
          response.status() < 400,
        { timeout: 60000 },
      ),
      page.waitForResponse(
        (response) =>
          response.request().method() === "GET" &&
          response
            .url()
            .includes(`/simulate/run-tests/${runTestId}/executions/`) &&
          response.status() < 400,
        { timeout: 60000 },
      ),
      clickDataGridRow(page, runTestId),
    ]);
    const detail = await detailResponse.json();
    assert(detail?.id === runTestId, "Run-test detail response id mismatch.");
    const executions = await executionsResponse.json();
    assert(
      asArray(executions?.results).some(
        (execution) => execution?.id === testExecutionId,
      ),
      "Run-test executions response did not include the disposable execution.",
    );

    await expectVisibleText(page, runName);
    await expectVisibleText(page, "Simulated runs");
    await expectVisibleText(page, "Logs");
    await expectVisibleText(page, "Analytics");
    await expectVisibleText(page, "Total Chats");
    await expectVisibleText(page, "Run Status");
    await expectVisibleText(page, "Completed");
    await expectNoVisibleText(page, "Invalid Date");
    await expectNoVisibleText(page, "undefined");

    const [kpisResponse, callDetailsResponse] = await Promise.all([
      page.waitForResponse(
        (response) =>
          response.request().method() === "GET" &&
          response
            .url()
            .includes(`/simulate/test-executions/${testExecutionId}/kpis/`) &&
          response.status() < 400,
        { timeout: 60000 },
      ),
      page.waitForResponse(
        (response) =>
          response.request().method() === "GET" &&
          responsePath(response) ===
            `/simulate/test-executions/${testExecutionId}/` &&
          response.status() < 400,
        { timeout: 60000 },
      ),
      clickDataGridRow(page, testExecutionId),
    ]);
    const kpis = await kpisResponse.json();
    assert(
      Number(kpis?.total_calls) === callExecutionIds.length,
      "Execution KPI response did not count the disposable chat calls.",
    );
    const callDetails = await callDetailsResponse.json();
    assert(
      asArray(callDetails?.results).length === callExecutionIds.length,
      "Execution detail response did not return the disposable call rows.",
    );

    await expectVisibleText(page, `Execution : ${testExecutionId}`);
    await expectVisibleText(page, "Chat Details");
    await expectVisibleText(page, "Analytics");
    await expectVisibleText(page, "Optimization Runs");
    await expectVisibleText(page, "Performance Metrics");
    await expectVisibleText(page, `Chats (${callExecutionIds.length})`);
    await expectNoVisibleText(page, "Invalid Date");
    await expectNoVisibleText(page, "undefined");

    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.request().method() === "GET" &&
          response
            .url()
            .includes(
              `/simulate/test-executions/${testExecutionId}/performance-summary/`,
            ) &&
          response.status() < 400,
        { timeout: 60000 },
      ),
      page.goto(
        `${APP_BASE}/dashboard/simulate/test/${runTestId}/${testExecutionId}/performance`,
        { waitUntil: "domcontentloaded" },
      ),
    ]);
    await expectVisibleText(page, "Test Run Performance Metrics");
    await expectVisibleText(page, "Pass Rate");
    await expectVisibleText(page, "Total Test Runs");

    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.request().method() === "GET" &&
          responsePath(response) ===
            `/simulate/run-tests/${runTestId}/eval-summary/` &&
          response.url().includes(`execution_id=${testExecutionId}`) &&
          response.status() < 400,
        { timeout: 60000 },
      ),
      page.goto(
        `${APP_BASE}/dashboard/simulate/test/${runTestId}/${testExecutionId}/analytics`,
        { waitUntil: "domcontentloaded" },
      ),
    ]);
    await expectVisibleText(page, "Analytics");
    await expectNoVisibleText(page, "Invalid Date");
    await expectNoVisibleText(page, "undefined");
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      apiFailures.length === 0,
      `Simulation browser read API failures: ${apiFailures.join("; ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    await publicDeleteRunFixture({
      client: auth.client,
      runTestId,
      testExecutionId,
      callExecutionIds,
    });
    const dbCleanup = await hardDeleteRunTestFixtures({
      namePrefix,
      organizationId: auth.organizationId,
    });
    assert(
      Number(dbCleanup.remaining_run_test_count) === 0 &&
        Number(dbCleanup.remaining_test_execution_count) === 0 &&
        Number(dbCleanup.remaining_call_execution_count) === 0,
      `Run-test hard cleanup left disposable rows behind: ${JSON.stringify(
        dbCleanup,
      )}`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence: {
            seed_run_test_id: seed.runTestId,
            seed_scenario_id: seed.scenarioId,
            run_test_id: runTestId,
            test_execution_id: testExecutionId,
            call_execution_count: callExecutionIds.length,
            observed_responses: observedResponses,
            db_seed: dbSeed,
            db_cleanup: dbCleanup,
            screenshot: SCREENSHOT_PATH,
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await publicDeleteRunFixture({
      client: auth.client,
      runTestId,
      testExecutionId,
      callExecutionIds,
    }).catch(() => null);
    await hardDeleteRunTestFixtures({
      namePrefix,
      organizationId: auth.organizationId,
    }).catch(() => null);
    throw error;
  } finally {
    await browser.close();
  }
}

async function installRuntimeConfig(page, auth) {
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/config.js") {
      request.respond({
        status: 200,
        contentType: "application/javascript",
        body: `window.__FUTURE_AGI_CONFIG__ = ${JSON.stringify({
          VITE_HOST_API: auth.apiBase,
          VITE_ASSETS_API: APP_BASE,
        })};`,
      });
      return;
    }
    request.continue();
  });
}

async function installAuthState(page, auth) {
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      if (organizationId)
        sessionStorage.setItem("organizationId", organizationId);
      if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
      if (user?.id)
        sessionStorage.setItem("futureagi-current-user-id", user.id);
    },
    {
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );
}

async function searchRunTests(page, text) {
  await Promise.all([
    page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        response.url().includes("/simulate/run-tests/") &&
        response.url().includes("search="),
      { timeout: 60000 },
    ),
    setInputValue(page, 'input[placeholder="Search"]', text),
  ]);
}

async function clickDataGridRow(page, rowId) {
  const selector = `[role="row"][data-id="${rowId}"]`;
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector);
}

async function setInputValue(page, selector, value) {
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.$eval(
    selector,
    (element, nextValue) => {
      const proto =
        element instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      setter.call(element, nextValue);
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
    },
    value,
  );
}

async function expectVisibleText(page, text) {
  await page.waitForFunction(
    (expected) => document.body?.innerText?.includes(expected),
    { timeout: 30000 },
    text,
  );
}

async function expectNoVisibleText(page, text) {
  const found = await page.evaluate((expected) =>
    Boolean(document.body?.innerText?.includes(expected)),
  );
  assert(!found, `Unexpected visible text found: ${text}`);
}

async function publicDeleteRunFixture({
  client,
  runTestId,
  testExecutionId,
  callExecutionIds,
}) {
  for (const callExecutionId of callExecutionIds || []) {
    await ignoreNotFound(() =>
      client.delete(
        apiPath("/simulate/call-executions/{call_execution_id}/delete/", {
          call_execution_id: callExecutionId,
        }),
      ),
    );
  }
  if (testExecutionId) {
    await ignoreNotFound(() =>
      client.delete(
        apiPath("/simulate/test-executions/{test_execution_id}/delete/", {
          test_execution_id: testExecutionId,
        }),
      ),
    );
  }
  if (runTestId) {
    await ignoreNotFound(() =>
      client.delete(
        apiPath("/simulate/run-tests/{run_test_id}/delete/", {
          run_test_id: runTestId,
        }),
      ),
    );
  }
}

async function ignoreNotFound(fn) {
  try {
    return await fn();
  } catch (error) {
    const message = String(error?.message || "").toLowerCase();
    if (
      error?.status === 404 ||
      message.includes("not found") ||
      message.includes("does not exist") ||
      (message.includes("no ") && message.includes(" matches "))
    ) {
      return null;
    }
    throw error;
  }
}

async function selectSimulationRunTestSeed(client) {
  const candidates = [];
  for (const source of [
    apiPath("/simulate/run-tests/"),
    apiPath("/simulate/api/run-tests/"),
  ]) {
    for (const page of [1, 2]) {
      const payload = await client.get(source, { query: { limit: 25, page } });
      candidates.push(...collectionRows(payload));
    }
  }

  for (const runTest of candidates) {
    const agentDefinitionId =
      firstUuid(runTest.agent_definition) ||
      firstUuid(runTest.agent_definition_detail?.id);
    const agentVersionId = extractSimulationAgentVersionId(runTest);
    if (!agentDefinitionId || !agentVersionId) continue;
    if (!isTextSimulationAgent(runTest)) continue;

    const scenario = collectionRows(runTest.scenarios_detail).find((item) =>
      isRunnableSimulationScenario(item),
    );
    if (!scenario) continue;

    return {
      runTestId: runTest.id || null,
      agentDefinitionId,
      agentVersionId,
      scenarioId: scenario.id,
    };
  }

  const [agentPayload, scenarioPayload] = await Promise.all([
    client.get(apiPath("/simulate/api/agent-definition-operations/"), {
      query: { limit: 50, page: 1 },
    }),
    client.get(apiPath("/simulate/scenarios/"), {
      query: { limit: 50, page: 1, agent_type: "text" },
    }),
  ]);
  const textAgent = collectionRows(agentPayload).find(
    (agent) =>
      firstUuid(agent?.id) &&
      String(agent?.agent_type || "").toLowerCase() === "text" &&
      extractSimulationAgentVersionId(agent),
  );
  const scenario = collectionRows(scenarioPayload).find((item) =>
    isRunnableSimulationScenario(item),
  );
  if (!textAgent || !scenario) return null;

  return {
    runTestId: null,
    agentDefinitionId: textAgent.id,
    agentVersionId: extractSimulationAgentVersionId(textAgent),
    scenarioId: scenario.id,
  };
}

function collectionRows(value) {
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.results)) return value.results;
  if (Array.isArray(value?.data)) return value.data;
  if (Array.isArray(value?.run_tests)) return value.run_tests;
  if (Array.isArray(value?.test_executions)) return value.test_executions;
  if (Array.isArray(value?.call_executions)) return value.call_executions;
  if (Array.isArray(value?.calls)) return value.calls;
  return asArray(value);
}

function extractSimulationAgentVersionId(value) {
  return (
    firstUuid(value?.agent_version?.id) ||
    firstUuid(value?.agent_version) ||
    firstUuid(value?.latest_version?.id) ||
    firstUuid(value?.agent_definition_detail?.latest_version?.id)
  );
}

function isTextSimulationAgent(value) {
  const agentType = String(
    value?.agent_definition_detail?.agent_type ||
      value?.agent_version?.configuration_snapshot?.agent_type ||
      value?.latest_version?.configuration_snapshot?.agent_type ||
      value?.agent_type ||
      "",
  ).toLowerCase();
  return agentType === "text" || agentType === "chat";
}

function isRunnableSimulationScenario(scenario) {
  if (!firstUuid(scenario?.id)) return false;
  if (String(scenario?.status || "").toLowerCase() !== "completed") {
    return false;
  }
  const datasetId = firstUuid(scenario?.dataset);
  const rowCount = Number(scenario?.dataset_rows ?? scenario?.row_count ?? 0);
  return !datasetId || rowCount > 0;
}

function firstUuid(value) {
  return isUuid(value) ? String(value) : null;
}

function responsePath(response) {
  try {
    return new URL(response.url()).pathname;
  } catch {
    return "";
  }
}

async function markRunExecutionReadable({
  runTestId,
  testExecutionId,
  callExecutionIds,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH target_run AS (
  SELECT id
  FROM simulate_run_test
  WHERE id = ${sqlUuid(runTestId)}
    AND organization_id = ${sqlUuid(organizationId)}
),
target_execution AS (
  SELECT te.id
  FROM simulate_test_execution te
  JOIN target_run rt ON rt.id = te.run_test_id
  WHERE te.id = ${sqlUuid(testExecutionId)}
),
target_calls AS (
  SELECT ce.id
  FROM simulate_call_execution ce
  JOIN target_execution te ON te.id = ce.test_execution_id
  WHERE ce.id = ANY(${sqlUuidArray(callExecutionIds)})
),
updated_calls AS (
  UPDATE simulate_call_execution ce
  SET
    status = 'completed',
    started_at = COALESCE(ce.started_at, now() - interval '15 seconds'),
    completed_at = COALESCE(ce.completed_at, now()),
    ended_at = COALESCE(ce.ended_at, now()),
    duration_seconds = COALESCE(ce.duration_seconds, 15),
    response_time_ms = COALESCE(ce.response_time_ms, 120),
    overall_score = COALESCE(ce.overall_score, 8.5),
    message_count = COALESCE(ce.message_count, 2),
    transcript_available = true,
    conversation_metrics_data = COALESCE(
      ce.conversation_metrics_data,
      ${sqlJson({
        total_tokens: 42,
        input_tokens: 18,
        output_tokens: 24,
        avg_latency_ms: 120,
        turn_count: 2,
        bot_message_count: 1,
        user_message_count: 1,
        csat_score: 4.5,
      })}
    ),
    call_summary = COALESCE(
      ce.call_summary,
      'Browser smoke disposable completed chat.'
    ),
    updated_at = now()
  FROM target_calls target
  WHERE ce.id = target.id
  RETURNING ce.id
),
inserted_messages AS (
  INSERT INTO simulate_chat_message (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    call_execution_id,
    role,
    messages,
    content,
    session_id,
    organization_id,
    workspace_id,
    tool_calls,
    tokens,
    latency_ms
  )
  SELECT
    now(),
    now(),
    false,
    NULL,
    gen_random_uuid(),
    tc.id,
    payload.role,
    payload.messages::jsonb,
    payload.content::jsonb,
    'browser-smoke-' || tc.id::text,
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)},
    '[]'::jsonb,
    payload.tokens,
    payload.latency_ms
  FROM target_calls tc
  CROSS JOIN (
    VALUES
      ('user', '["hello from browser smoke"]', '[{"type":"text","text":"hello from browser smoke"}]', 18, NULL),
      ('assistant', '["temporary response from browser smoke"]', '[{"type":"text","text":"temporary response from browser smoke"}]', 24, 120)
  ) AS payload(role, messages, content, tokens, latency_ms)
  WHERE NOT EXISTS (
    SELECT 1
    FROM simulate_chat_message existing
    WHERE existing.call_execution_id = tc.id
  )
  RETURNING id
),
updated_execution AS (
  UPDATE simulate_test_execution te
  SET
    status = 'completed',
    completed_at = COALESCE(te.completed_at, now()),
    total_calls = ${callExecutionIds.length},
    completed_calls = ${callExecutionIds.length},
    failed_calls = 0,
    picked_up_by_executor = true,
    updated_at = now()
  FROM target_execution target
  WHERE te.id = target.id
  RETURNING te.id
)
SELECT json_build_object(
  'updated_call_count', (SELECT count(*) FROM updated_calls),
  'inserted_message_count', (SELECT count(*) FROM inserted_messages),
  'updated_execution_count', (SELECT count(*) FROM updated_execution)
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteRunTestFixtures({ namePrefix, organizationId }) {
  const sql = `
WITH target_runs AS (
  SELECT id
  FROM simulate_run_test
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
target_executions AS (
  SELECT id
  FROM simulate_test_execution
  WHERE run_test_id IN (SELECT id FROM target_runs)
),
target_calls AS (
  SELECT id
  FROM simulate_call_execution
  WHERE test_execution_id IN (SELECT id FROM target_executions)
),
updated_phone_numbers AS (
  UPDATE simulate_phone_numbers
  SET current_call_execution_id = NULL
  WHERE current_call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_queue_items AS (
  DELETE FROM model_hub_queueitem
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_scores AS (
  DELETE FROM model_hub_score
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_trial_results AS (
  DELETE FROM trial_item_result
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_snapshots AS (
  DELETE FROM simulate_call_execution_snapshot
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_transcripts AS (
  DELETE FROM simulate_call_transcript
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_logs AS (
  DELETE FROM simulate_call_log_entry
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_chat_sessions AS (
  DELETE FROM simulate_chatsimulatorsession
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_chat_messages AS (
  DELETE FROM simulate_chat_message
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_create_call_rows AS (
  DELETE FROM simulate_createcallexecution
  WHERE call_execution_id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_call_executions AS (
  DELETE FROM simulate_call_execution
  WHERE id IN (SELECT id FROM target_calls)
  RETURNING id
),
deleted_optimiser_runs AS (
  DELETE FROM agent_prompt_optimiser_run
  WHERE test_execution_id IN (SELECT id FROM target_executions)
  RETURNING id
),
deleted_test_executions AS (
  DELETE FROM simulate_test_execution
  WHERE id IN (SELECT id FROM target_executions)
  RETURNING id
),
deleted_eval_configs AS (
  DELETE FROM simulate_eval_config
  WHERE run_test_id IN (SELECT id FROM target_runs)
  RETURNING id
),
deleted_replay_sessions AS (
  DELETE FROM tracer_replaysession
  WHERE run_test_id IN (SELECT id FROM target_runs)
  RETURNING id
),
deleted_run_scenarios AS (
  DELETE FROM simulate_run_test_scenarios
  WHERE runtest_id IN (SELECT id FROM target_runs)
  RETURNING id
),
deleted_run_tests AS (
  DELETE FROM simulate_run_test
  WHERE id IN (SELECT id FROM target_runs)
  RETURNING id
)
SELECT json_build_object(
  'deleted_run_test_count', (SELECT count(*) FROM deleted_run_tests),
  'deleted_test_execution_count', (SELECT count(*) FROM deleted_test_executions),
  'deleted_call_execution_count', (SELECT count(*) FROM deleted_call_executions),
  'deleted_chat_message_count', (SELECT count(*) FROM deleted_chat_messages),
  'deleted_transcript_count', (SELECT count(*) FROM deleted_transcripts),
  'deleted_log_count', (SELECT count(*) FROM deleted_logs)
);
`;
  const cleanup = await runPostgresJson(sql);
  const remainingSql = `
WITH target_runs AS (
  SELECT id
  FROM simulate_run_test
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
target_executions AS (
  SELECT id
  FROM simulate_test_execution
  WHERE run_test_id IN (SELECT id FROM target_runs)
),
target_calls AS (
  SELECT id
  FROM simulate_call_execution
  WHERE test_execution_id IN (SELECT id FROM target_executions)
)
SELECT json_build_object(
  'remaining_run_test_count', (SELECT count(*) FROM target_runs),
  'remaining_test_execution_count', (SELECT count(*) FROM target_executions),
  'remaining_call_execution_count', (SELECT count(*) FROM target_calls)
);
`;
  return {
    ...cleanup,
    ...(await runPostgresJson(remainingSql)),
  };
}

async function runPostgresJson(sql) {
  const container =
    process.env.API_JOURNEY_DB_CONTAINER || "futureagi-ws2-postgres-1";
  const user = process.env.API_JOURNEY_DB_USER || "user";
  const database = process.env.API_JOURNEY_DB_NAME || "tfc";
  const { stdout } = await execFileAsync(
    "docker",
    ["exec", container, "psql", "-U", user, "-d", database, "-At", "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const text = stdout.trim();
  assert(text, "Postgres command returned no JSON output.");
  return JSON.parse(text);
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlUuidArray(values) {
  const uuids = asArray(values).filter(isUuid);
  assert(uuids.length === values.length, "SQL UUID array includes non-UUIDs.");
  if (uuids.length === 0) return "ARRAY[]::uuid[]";
  return `ARRAY[${uuids.map((value) => sqlUuid(value)).join(", ")}]::uuid[]`;
}

function sqlString(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function sqlJson(value) {
  return `${sqlString(JSON.stringify(value))}::jsonb`;
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  }
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  return "/usr/bin/google-chrome";
}

main()
  .then(() => {
    process.exit(0);
  })
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
