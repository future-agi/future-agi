/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  currentUserId,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.SIMULATE_SCENARIOS_SCREENSHOT ||
  "/tmp/simulate-scenarios-detail-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const userId = currentUserId(auth.user);
  assert(
    isUuid(userId),
    "Authenticated user payload did not include a UUID id.",
  );

  const runId = auth.runId.replace(/[^a-z0-9-]/gi, "-");
  const namePrefix = `browser scenario ${runId}`;
  const scenarioName = `${namePrefix} completed`;

  await hardDeleteScenarioFixtures({
    namePrefix,
    organizationId: auth.organizationId,
  });

  const seeded = await seedScenarioFixture({
    namePrefix,
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
    userId,
  });

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
    const page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installAuthState(page, auth);

    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("response", (response) => {
      const url = response.url();
      const tracksScenario =
        url.includes("/simulate/scenarios/") ||
        url.includes(
          `/model-hub/develops/${seeded.dataset_id}/get-dataset-table/`,
        );
      if (!tracksScenario) return;
      const status = response.status();
      observedResponses.push(`${response.request().method()} ${status} ${url}`);
      if (status >= 400) apiFailures.push(`${status} ${url}`);
    });

    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.request().method() === "GET" &&
          response.url().includes("/simulate/scenarios/") &&
          response.status() < 400,
        { timeout: 60000 },
      ),
      page.goto(`${APP_BASE}/dashboard/simulate/scenarios`, {
        waitUntil: "domcontentloaded",
      }),
    ]);

    await expectVisibleText(page, "Scenarios");
    await searchScenarios(page, scenarioName);
    await expectVisibleText(page, scenarioName);
    await expectNoVisibleText(page, "Invalid Date");
    await expectNoVisibleText(page, "undefined");
    await expectNoVisibleText(page, "Unknown");

    const [detailResponse, datasetResponse] = await Promise.all([
      page.waitForResponse(
        (response) =>
          response.request().method() === "GET" &&
          response
            .url()
            .includes(`/simulate/scenarios/${seeded.scenario_id}/`) &&
          response.status() < 400,
        { timeout: 60000 },
      ),
      page.waitForResponse(
        (response) =>
          response.request().method() === "GET" &&
          response
            .url()
            .includes(
              `/model-hub/develops/${seeded.dataset_id}/get-dataset-table/`,
            ) &&
          response.url().includes("current_page_index=") &&
          response.status() < 400,
        { timeout: 60000 },
      ),
      clickScenarioRow(page, seeded.scenario_id),
    ]);

    const detail = await detailResponse.json();
    assert(detail?.id === seeded.scenario_id, "Scenario detail id mismatch.");
    assert(
      detail?.scenario_type === "dataset",
      "Scenario detail type mismatch.",
    );
    assert(detail?.dataset_rows === 2, "Scenario detail row count mismatch.");
    assert(
      detail?.agent_type === "text",
      "Scenario detail agent type mismatch.",
    );

    const datasetBody = await datasetResponse.json();
    const table = asArray(datasetBody?.result);
    assert(
      table.length === 2,
      "Dataset preview table did not return two rows.",
    );

    await expectVisibleText(page, "All Scenarios");
    await expectVisibleText(page, scenarioName);
    await expectVisibleText(page, "Agent Type");
    await expectVisibleText(page, "Chat");
    await expectVisibleText(page, "Scenario Type");
    await expectVisibleText(page, "Dataset");
    await expectVisibleText(page, "No of Datapoints");
    await expectVisibleText(page, "2");
    await expectVisibleText(page, "Prompt");
    await expectVisibleText(
      page,
      "temporary simulator agent for scenario browser coverage",
    );
    await expectVisibleText(page, "Generated scenarios");
    await expectVisibleText(page, `${namePrefix} input one`);
    await expectVisibleText(page, `${namePrefix} expected one`);
    await page.waitForSelector(".react-flow", {
      visible: true,
      timeout: 30000,
    });
    await expectNoVisibleText(page, "Invalid Date");
    await expectNoVisibleText(page, "undefined");
    await expectNoVisibleText(page, "Unknown");

    const apiMatches = await findScenariosBySearch(auth.client, scenarioName);
    assert(
      apiMatches.length === 1,
      "Scenario API list did not return fixture.",
    );
    assert(
      apiMatches[0]?.dataset_rows === 2,
      "Scenario API list row count mismatch.",
    );

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      apiFailures.length === 0,
      `Scenario API failures during browser smoke: ${apiFailures.join("; ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    const dbCleanup = await hardDeleteScenarioFixtures({
      namePrefix,
      organizationId: auth.organizationId,
    });

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence: {
            scenario_id: seeded.scenario_id,
            dataset_id: seeded.dataset_id,
            observed_responses: observedResponses,
            db_cleanup: dbCleanup,
            screenshot: SCREENSHOT_PATH,
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await hardDeleteScenarioFixtures({
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

async function searchScenarios(page, text) {
  await Promise.all([
    page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        response.url().includes("/simulate/scenarios/") &&
        response.url().includes("search="),
      { timeout: 60000 },
    ),
    setInputValue(page, 'input[placeholder="Search"]', text),
  ]);
}

async function clickScenarioRow(page, scenarioId) {
  const selector = `[role="row"][data-id="${cssEscape(scenarioId)}"]`;
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

async function findScenariosBySearch(client, search) {
  const result = await client.get(apiPath("/simulate/scenarios/"), {
    query: { page: 1, limit: 100, search },
  });
  return asArray(result).filter((scenario) => scenario?.name === search);
}

async function seedScenarioFixture({
  namePrefix,
  organizationId,
  workspaceId,
  userId,
}) {
  const agentDefinitionId = randomUUID();
  const simulatorAgentId = randomUUID();
  const datasetId = randomUUID();
  const inputColumnId = randomUUID();
  const expectedColumnId = randomUUID();
  const rowOneId = randomUUID();
  const rowTwoId = randomUUID();
  const scenarioId = randomUUID();
  const graphId = randomUUID();
  const cellIds = [randomUUID(), randomUUID(), randomUUID(), randomUUID()];
  const columnConfig = {
    [inputColumnId]: {
      name: "input",
      type: "text",
      description: "User input",
    },
    [expectedColumnId]: {
      name: "expected",
      type: "text",
      description: "Expected output",
    },
  };

  const sql = `
WITH inserted_agent AS (
  INSERT INTO simulate_agent_definition (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    agent_name,
    contact_number,
    inbound,
    description,
    assistant_id,
    language,
    websocket_url,
    websocket_headers,
    organization_id,
    provider,
    workspace_id,
    agent_type,
    api_key,
    authentication_method,
    languages,
    model,
    model_details
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(agentDefinitionId)},
    ${sqlString(`${namePrefix} agent`)},
    NULL,
    true,
    ${sqlString("Temporary text agent definition for scenario browser coverage.")},
    NULL,
    'en',
    NULL,
    '{}'::jsonb,
    ${sqlUuid(organizationId)},
    NULL,
    ${sqlUuid(workspaceId)},
    'text',
    NULL,
    'api_key',
    ARRAY['en']::varchar[],
    'gpt-4o-mini',
    ${sqlJson({ source: "browser-smoke", fixture: "scenario-detail" })}
  )
  RETURNING id
),
inserted_simulator_agent AS (
  INSERT INTO simulator_agents (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    prompt,
    voice_provider,
    voice_name,
    interrupt_sensitivity,
    conversation_speed,
    finished_speaking_sensitivity,
    model,
    llm_temperature,
    max_call_duration_in_minutes,
    initial_message_delay,
    initial_message,
    organization_id,
    workspace_id
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(simulatorAgentId)},
    ${sqlString(`${namePrefix} simulator`)},
    ${sqlString("You are a temporary simulator agent for scenario browser coverage.")},
    'elevenlabs',
    'marissa',
    0.5,
    1.0,
    0.5,
    'gpt-4o-mini',
    0.7,
    30,
    0,
    '',
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)}
  )
  RETURNING id
),
inserted_dataset AS (
  INSERT INTO model_hub_dataset (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    column_order,
    model_type,
    organization_id,
    column_config,
    source,
    dataset_config,
    user_id,
    synthetic_dataset_config,
    workspace_id,
    eval_reasons,
    eval_reason_status
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(datasetId)},
    ${sqlString(`${namePrefix} source dataset`)},
    ARRAY[${sqlString(inputColumnId)}, ${sqlString(expectedColumnId)}]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${sqlJson(columnConfig)},
    'scenario',
    '{}'::jsonb,
    ${sqlUuid(userId)},
    '{}'::jsonb,
    ${sqlUuid(workspaceId)},
    '[]'::jsonb,
    'pending'
  )
  RETURNING id
),
inserted_columns AS (
  INSERT INTO model_hub_column (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    data_type,
    source,
    source_id,
    dataset_id,
    metadata,
    status
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(inputColumnId)},
      'input',
      'text',
      'OTHERS',
      NULL,
      ${sqlUuid(datasetId)},
      ${sqlJson({ description: "User input" })},
      'Completed'
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(expectedColumnId)},
      'expected',
      'text',
      'OTHERS',
      NULL,
      ${sqlUuid(datasetId)},
      ${sqlJson({ description: "Expected output" })},
      'Completed'
    )
  RETURNING id
),
inserted_rows AS (
  INSERT INTO model_hub_row (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    "order",
    dataset_id,
    metadata
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(rowOneId)},
      0,
      ${sqlUuid(datasetId)},
      ${sqlJson({ source: "browser-smoke", row: 1 })}
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(rowTwoId)},
      1,
      ${sqlUuid(datasetId)},
      ${sqlJson({ source: "browser-smoke", row: 2 })}
    )
  RETURNING id
),
inserted_cells AS (
  INSERT INTO model_hub_cell (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    value,
    column_id,
    dataset_id,
    row_id,
    status,
    value_infos,
    feedback_info,
    column_metadata
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(cellIds[0])},
      ${sqlString(`${namePrefix} input one`)},
      ${sqlUuid(inputColumnId)},
      ${sqlUuid(datasetId)},
      ${sqlUuid(rowOneId)},
      'pass',
      '[]'::jsonb,
      '{}'::jsonb,
      '{}'::jsonb
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(cellIds[1])},
      ${sqlString(`${namePrefix} expected one`)},
      ${sqlUuid(expectedColumnId)},
      ${sqlUuid(datasetId)},
      ${sqlUuid(rowOneId)},
      'pass',
      '[]'::jsonb,
      '{}'::jsonb,
      '{}'::jsonb
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(cellIds[2])},
      ${sqlString(`${namePrefix} input two`)},
      ${sqlUuid(inputColumnId)},
      ${sqlUuid(datasetId)},
      ${sqlUuid(rowTwoId)},
      'pass',
      '[]'::jsonb,
      '{}'::jsonb,
      '{}'::jsonb
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(cellIds[3])},
      ${sqlString(`${namePrefix} expected two`)},
      ${sqlUuid(expectedColumnId)},
      ${sqlUuid(datasetId)},
      ${sqlUuid(rowTwoId)},
      'pass',
      '[]'::jsonb,
      '{}'::jsonb,
      '{}'::jsonb
    )
  RETURNING id
),
inserted_scenario AS (
  INSERT INTO simulate_scenarios (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    source,
    scenario_type,
    organization_id,
    dataset_id,
    description,
    workspace_id,
    metadata,
    simulator_agent_id,
    status,
    agent_definition_id,
    source_type,
    prompt_template_id,
    prompt_version_id
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(scenarioId)},
    ${sqlString(`${namePrefix} completed`)},
    ${sqlString(`${namePrefix} source`)},
    'dataset',
    ${sqlUuid(organizationId)},
    ${sqlUuid(datasetId)},
    ${sqlString("Temporary completed scenario for browser coverage.")},
    ${sqlUuid(workspaceId)},
    ${sqlJson({ source: "browser-smoke", fixture: "scenario-detail" })},
    ${sqlUuid(simulatorAgentId)},
    'Completed',
    ${sqlUuid(agentDefinitionId)},
    'agent_definition',
    NULL,
    NULL
  )
  RETURNING id
),
inserted_graph AS (
  INSERT INTO simulate_scenario_graph (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    description,
    version,
    is_active,
    graph_config,
    organization_id,
    scenario_id
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(graphId)},
    ${sqlString(`${namePrefix} graph`)},
    ${sqlString("Temporary scenario graph for browser coverage.")},
    1,
    true,
    ${sqlJson({
      graph_data: {
        nodes: [{ id: "start", type: "start" }],
        edges: [],
      },
      source: "browser-smoke",
    })},
    ${sqlUuid(organizationId)},
    ${sqlUuid(scenarioId)}
  )
  RETURNING id
)
SELECT json_build_object(
  'agent_definition_id', ${sqlString(agentDefinitionId)},
  'simulator_agent_id', ${sqlString(simulatorAgentId)},
  'dataset_id', ${sqlString(datasetId)},
  'scenario_id', ${sqlString(scenarioId)},
  'graph_id', ${sqlString(graphId)},
  'inserted_agent_count', (SELECT count(*) FROM inserted_agent),
  'inserted_dataset_count', (SELECT count(*) FROM inserted_dataset),
  'inserted_column_count', (SELECT count(*) FROM inserted_columns),
  'inserted_row_count', (SELECT count(*) FROM inserted_rows),
  'inserted_cell_count', (SELECT count(*) FROM inserted_cells),
  'inserted_scenario_count', (SELECT count(*) FROM inserted_scenario),
  'inserted_graph_count', (SELECT count(*) FROM inserted_graph)
);
`;
  const result = await runPostgresJson(sql);
  assert(
    Number(result.inserted_scenario_count) === 1 &&
      Number(result.inserted_row_count) === 2 &&
      Number(result.inserted_cell_count) === 4,
    `Failed to seed disposable scenario fixture rows: ${JSON.stringify(result)}`,
  );
  return result;
}

async function hardDeleteScenarioFixtures({ namePrefix, organizationId }) {
  const sql = `
WITH target_scenarios AS (
  SELECT id, dataset_id, simulator_agent_id, agent_definition_id
  FROM simulate_scenarios
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
target_datasets AS (
  SELECT id
  FROM model_hub_dataset
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND (
      name LIKE ${sqlString(`${namePrefix}%`)}
      OR id IN (
        SELECT dataset_id FROM target_scenarios WHERE dataset_id IS NOT NULL
      )
    )
),
target_simulator_agents AS (
  SELECT id
  FROM simulator_agents
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND (
      name LIKE ${sqlString(`${namePrefix}%`)}
      OR id IN (
        SELECT simulator_agent_id
        FROM target_scenarios
        WHERE simulator_agent_id IS NOT NULL
      )
    )
),
target_agents AS (
  SELECT id
  FROM simulate_agent_definition
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND (
      agent_name LIKE ${sqlString(`${namePrefix}%`)}
      OR id IN (
        SELECT agent_definition_id
        FROM target_scenarios
        WHERE agent_definition_id IS NOT NULL
      )
    )
),
target_graphs AS (
  SELECT id
  FROM simulate_scenario_graph
  WHERE scenario_id IN (SELECT id FROM target_scenarios)
),
target_cells AS (
  SELECT id
  FROM model_hub_cell
  WHERE dataset_id IN (SELECT id FROM target_datasets)
),
target_rows AS (
  SELECT id
  FROM model_hub_row
  WHERE dataset_id IN (SELECT id FROM target_datasets)
),
target_columns AS (
  SELECT id
  FROM model_hub_column
  WHERE dataset_id IN (SELECT id FROM target_datasets)
),
deleted_cells AS (
  DELETE FROM model_hub_cell c
  USING target_cells target
  WHERE c.id = target.id
  RETURNING c.id
),
deleted_rows AS (
  DELETE FROM model_hub_row r
  USING target_rows target
  WHERE r.id = target.id
  RETURNING r.id
),
deleted_columns AS (
  DELETE FROM model_hub_column c
  USING target_columns target
  WHERE c.id = target.id
  RETURNING c.id
),
deleted_graphs AS (
  DELETE FROM simulate_scenario_graph g
  USING target_graphs target
  WHERE g.id = target.id
  RETURNING g.id
),
deleted_scenarios AS (
  DELETE FROM simulate_scenarios s
  USING target_scenarios target
  WHERE s.id = target.id
  RETURNING s.id
),
deleted_simulator_agents AS (
  DELETE FROM simulator_agents a
  USING target_simulator_agents target
  WHERE a.id = target.id
  RETURNING a.id
),
deleted_agents AS (
  DELETE FROM simulate_agent_definition a
  USING target_agents target
  WHERE a.id = target.id
  RETURNING a.id
),
deleted_datasets AS (
  DELETE FROM model_hub_dataset d
  USING target_datasets target
  WHERE d.id = target.id
  RETURNING d.id
)
SELECT json_build_object(
  'deleted_scenario_count', (SELECT count(*) FROM deleted_scenarios),
  'deleted_graph_count', (SELECT count(*) FROM deleted_graphs),
  'deleted_dataset_count', (SELECT count(*) FROM deleted_datasets),
  'deleted_column_count', (SELECT count(*) FROM deleted_columns),
  'deleted_row_count', (SELECT count(*) FROM deleted_rows),
  'deleted_cell_count', (SELECT count(*) FROM deleted_cells),
  'deleted_agent_count', (SELECT count(*) FROM deleted_agents),
  'deleted_simulator_agent_count', (SELECT count(*) FROM deleted_simulator_agents),
  'remaining_scenario_count',
    (SELECT count(*) FROM target_scenarios) - (SELECT count(*) FROM deleted_scenarios),
  'remaining_graph_count',
    (SELECT count(*) FROM target_graphs) - (SELECT count(*) FROM deleted_graphs),
  'remaining_dataset_count',
    (SELECT count(*) FROM target_datasets) - (SELECT count(*) FROM deleted_datasets),
  'remaining_agent_count',
    (SELECT count(*) FROM target_agents) - (SELECT count(*) FROM deleted_agents),
  'remaining_simulator_agent_count',
    (SELECT count(*) FROM target_simulator_agents) - (SELECT count(*) FROM deleted_simulator_agents)
);
`;
  return runPostgresJson(sql);
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
  assert(text, "Postgres DB audit returned no JSON output.");
  return JSON.parse(text);
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlString(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function sqlJson(value) {
  return `${sqlString(JSON.stringify(value ?? null))}::jsonb`;
}

function cssEscape(value) {
  return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
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
