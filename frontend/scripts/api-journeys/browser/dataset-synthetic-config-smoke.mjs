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
  isUuid,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const DATASET_PREFIX = "ui_synthetic_config_";
const CREATE_GUARD_SCREENSHOT_PATH =
  "/tmp/dataset-synthetic-create-guard-smoke.png";
const EDIT_SCREENSHOT_PATH = "/tmp/dataset-synthetic-config-edit-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/dataset-synthetic-config-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const modelHubRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  const uiEvents = [];
  let browser = null;
  let page = null;
  let datasetId = null;

  await hardDeleteDatasetFixturesByPrefix(DATASET_PREFIX, cleanupEvidence);

  try {
    const fixture = await seedSyntheticDatasetFixture({
      runId: auth.runId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assert(
      fixture.fixture_created === true,
      "Synthetic dataset fixture was not seeded.",
    );
    datasetId = fixture.dataset_id;

    const initialConfig = await getSyntheticConfig(auth.client, datasetId);
    assert(
      initialConfig.dataset?.description === fixture.initial_description,
      "Initial synthetic config description did not round-trip through API.",
    );
    assert(
      initialConfig.num_rows === fixture.row_count,
      "Initial synthetic config row count mismatch.",
    );

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 960 },
      args: ["--no-sandbox"],
    });
    page = await createJourneyPage(browser, auth, {
      apiFailures,
      pageErrors,
      modelHubRequests,
      browserMutations,
      unexpectedMutations,
    });

    await verifyCreatePageValidation(page);
    await page.close();
    page = await createJourneyPage(browser, auth, {
      apiFailures,
      pageErrors,
      modelHubRequests,
      browserMutations,
      unexpectedMutations,
    });
    await verifyDirectEditConfig({
      page,
      auth,
      fixture,
      datasetId,
      uiEvents,
    });

    const updatedConfig = await getSyntheticConfig(auth.client, datasetId);
    assert(
      updatedConfig.dataset?.description === fixture.updated_description,
      `Updated synthetic config description mismatch: ${JSON.stringify(
        updatedConfig.dataset,
      )}`,
    );
    assert(
      updatedConfig.num_rows === fixture.row_count,
      "No-regenerate synthetic config update changed row count.",
    );
    assert(
      asArray(updatedConfig.columns).length === 1 &&
        updatedConfig.columns[0]?.name === fixture.column_name,
      "No-regenerate synthetic config update changed column shape.",
    );

    const updateMutation = browserMutations.find((mutation) =>
      mutation.url.includes(
        `/model-hub/develops/${datasetId}/update-synthetic-config/`,
      ),
    );
    assert(updateMutation, "Browser did not PUT update-synthetic-config.");
    assert(
      updateMutation.body?.dataset?.description === fixture.updated_description,
      "Browser PUT did not send the edited synthetic description.",
    );
    assert(
      updateMutation.body?.num_rows === fixture.row_count,
      "Browser PUT did not preserve the fixture row count.",
    );
    assert(
      updateMutation.body?.regenerate !== true,
      "Browser safe edit unexpectedly requested regeneration.",
    );

    const dbAudit = await loadSyntheticDatasetAudit({
      fixture,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assertSyntheticDatasetAudit(dbAudit, fixture);

    await page.screenshot({ path: EDIT_SCREENSHOT_PATH, fullPage: true });

    assert(
      unexpectedMutations.length === 0,
      `Unexpected Model Hub mutations: ${unexpectedMutations.join(", ")}`,
    );
    assert(
      apiFailures.length === 0,
      `Unexpected Model Hub API failures: ${apiFailures.join(", ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    await deleteDataset(auth.client, datasetId, cleanupEvidence);
    datasetId = null;
    const hardCleanup = await hardDeleteDatasetFixturesByPrefix(
      DATASET_PREFIX,
      cleanupEvidence,
    );
    assert(
      hardCleanup.remaining_dataset_count === 0,
      `Hard cleanup left synthetic fixtures: ${JSON.stringify(hardCleanup)}`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          dataset_id: fixture.dataset_id,
          dataset_name: fixture.dataset_name,
          updated_description: fixture.updated_description,
          db_audit: dbAudit,
          browser_mutations: browserMutations.map(sanitizeMutation),
          model_hub_request_count: modelHubRequests.length,
          screenshots: {
            create_guard: CREATE_GUARD_SCREENSHOT_PATH,
            edit: EDIT_SCREENSHOT_PATH,
          },
          cleanup: cleanupEvidence,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    if (page) {
      await page.screenshot({
        path: FAILURE_SCREENSHOT_PATH,
        fullPage: true,
      });
      console.error(`failure_screenshot=${FAILURE_SCREENSHOT_PATH}`);
    }
    console.error(
      JSON.stringify(
        {
          status: "failed",
          api_failures: apiFailures,
          page_errors: pageErrors,
          browser_mutations: browserMutations.map(sanitizeMutation),
          unexpected_mutations: unexpectedMutations,
          ui_events: uiEvents,
          model_hub_request_count: modelHubRequests.length,
        },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    if (datasetId) {
      await deleteDataset(auth.client, datasetId, cleanupEvidence).catch(
        (error) => {
          cleanupEvidence.push({
            cleanup: "public delete synthetic config fixture after failure",
            status: "failed",
            error: error.message,
          });
        },
      );
    }
    await hardDeleteDatasetFixturesByPrefix(
      DATASET_PREFIX,
      cleanupEvidence,
    ).catch((error) => {
      cleanupEvidence.push({
        cleanup: "hard delete synthetic config fixtures after failure",
        status: "failed",
        error: error.message,
      });
    });
    if (browser) await browser.close();
  }
}

async function verifyCreatePageValidation(page) {
  await page.goto(`${APP_BASE}/dashboard/develop/create-synthetic-dataset`, {
    waitUntil: "domcontentloaded",
  });
  await waitForPath(page, "/dashboard/develop/create-synthetic-dataset");
  await waitForVisibleText(page, "Create Synthetic data", { exact: true });
  await setTextFieldByLabel(page, "Name", "Synthetic guard");
  await setTextFieldByLabel(page, "Description", "Synthetic guard");
  await setTextFieldByLabel(page, "Enter No. of rows", "9");
  await waitForVisibleText(page, "Add a minimum of 10 rows", { exact: true });
  await assertButtonDisabled(page, "Next");
  await page.screenshot({
    path: CREATE_GUARD_SCREENSHOT_PATH,
    fullPage: true,
  });
}

async function createJourneyPage(
  browser,
  auth,
  {
    apiFailures,
    pageErrors,
    modelHubRequests,
    browserMutations,
    unexpectedMutations,
  },
) {
  const page = await browser.newPage();
  await page.setBypassServiceWorker(true);
  await installRuntimeConfig(page, auth);
  await installBrowserState(page, auth);

  page.on("request", (request) => {
    const url = request.url();
    if (!isModelHubApiUrl(url)) return;
    modelHubRequests.push(`${request.method()} ${url}`);
    if (MUTATION_METHODS.has(request.method())) {
      const mutation = {
        method: request.method(),
        url,
        body: parseJsonBody(request.postData()),
      };
      browserMutations.push(mutation);
      if (!isAllowedBrowserMutation(request.method(), url)) {
        unexpectedMutations.push(`${request.method()} ${maskUrl(url)}`);
      }
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (
      isModelHubApiUrl(url) &&
      response.status() >= 400 &&
      !isAllowedModelHubApiFailure(response.status(), url)
    ) {
      apiFailures.push(`${response.status()} ${maskUrl(url)}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  return page;
}

async function verifyDirectEditConfig({
  page,
  auth,
  fixture,
  datasetId,
  uiEvents,
}) {
  await waitForResponsesDuring(
    page,
    "direct synthetic edit config load",
    [
      (response) =>
        response
          .url()
          .includes(`/model-hub/develops/${datasetId}/synthetic-config/`) &&
        response.request().method() === "GET" &&
        response.status() < 400,
    ],
    () =>
      page.goto(
        `${APP_BASE}/dashboard/develop/edit-synthetic-dataset/${datasetId}?editMode=true`,
        { waitUntil: "domcontentloaded" },
      ),
  );
  await waitForPath(
    page,
    `/dashboard/develop/edit-synthetic-dataset/${datasetId}`,
  );
  await waitForVisibleText(page, "Configure Dataset", { exact: true });
  await waitForFieldValue(page, "Name", fixture.dataset_name);
  await waitForFieldValue(page, "Description", fixture.initial_description);
  await waitForFieldValue(page, "Enter No. of rows", String(fixture.row_count));

  await setTextFieldByLabel(page, "Description", fixture.updated_description);
  await clickVisibleText(page, "Next", { exact: true });
  await waitForVisibleText(page, "Add Columns", { exact: true });
  await waitForFieldValue(page, "Column Name", fixture.column_name);
  await waitForFieldValue(page, "Column Type", "Text");

  await clickVisibleText(page, "Next", { exact: true });
  await waitForVisibleText(page, "Add description", { exact: true });
  await setFirstTextareaValue(page, fixture.column_description);
  await waitForFirstTextareaValue(page, fixture.column_description);

  uiEvents.push(await snapshotSyntheticUi(page, "before_save_click"));
  const updateResponsePromise = page.waitForResponse(
    (response) =>
      response
        .url()
        .includes(
          `/model-hub/develops/${datasetId}/update-synthetic-config/`,
        ) && response.request().method() === "PUT",
    { timeout: 60000 },
  );
  await clickVisibleButton(page, "Save");
  uiEvents.push(await snapshotSyntheticUi(page, "after_save_click"));
  await waitForDialog(page, "Create Synthetic Dataset");
  uiEvents.push(await snapshotSyntheticUi(page, "after_option_dialog_open"));
  await selectDialogRadioByValue(page, "add_to_existing_dataset");
  uiEvents.push(await snapshotSyntheticUi(page, "after_option_selected"));
  await clickDialogButton(page, "Add");
  uiEvents.push(await snapshotSyntheticUi(page, "after_option_add_click"));
  const updateResponse = await updateResponsePromise;
  const updatePayload = await responseJson(updateResponse);
  assert(
    updateResponse.status() >= 200 && updateResponse.status() < 300,
    `Synthetic config update returned HTTP ${updateResponse.status()}: ${JSON.stringify(
      updatePayload,
    )}`,
  );
  await waitForPath(page, `/dashboard/develop/${datasetId}`);

  const afterSaveConfig = await getSyntheticConfig(auth.client, datasetId);
  assert(
    afterSaveConfig.dataset?.description === fixture.updated_description,
    "Synthetic config API did not reflect the browser save.",
  );
}

async function getSyntheticConfig(client, datasetId) {
  const result = await client.get(
    apiPath("/model-hub/develops/{dataset_id}/synthetic-config/", {
      dataset_id: datasetId,
    }),
  );
  return result?.data || result;
}

async function deleteDataset(client, datasetId, evidence) {
  await client.delete(apiPath("/model-hub/develops/delete_dataset/"), {
    body: { dataset_ids: [datasetId] },
    okStatuses: [200, 404],
  });
  evidence.push({
    cleanup: "public delete synthetic config fixture",
    status: "passed",
    dataset_id: datasetId,
  });
}

async function seedSyntheticDatasetFixture({
  runId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB seed.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB seed.");

  const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  const datasetId = randomUUID();
  const columnId = randomUUID();
  const rowIds = Array.from({ length: 10 }, () => randomUUID());
  const cellIds = Array.from({ length: 10 }, () => randomUUID());
  const datasetName = `${DATASET_PREFIX}${suffix}`;
  const columnName = "synthetic_answer";
  const initialDescription = `initial synthetic description ${suffix}`;
  const updatedDescription = `updated synthetic description ${suffix}`;
  const columnDescription = `answer using the seeded context ${suffix}`;
  const config = {
    dataset: {
      name: datasetName,
      description: initialDescription,
      objective: `objective ${suffix}`,
      patterns: `pattern ${suffix}`,
    },
    num_rows: 10,
    columns: [
      {
        name: columnName,
        data_type: "text",
        description: columnDescription,
        property: {
          min_length: 4,
          max_length: 80,
        },
      },
    ],
  };
  const columnConfig = {
    [columnId]: { is_visible: true, is_frozen: null },
  };
  const workspaceSql = workspaceId ? sqlUuid(workspaceId) : "NULL::uuid";
  const rowValues = rowIds
    .map(
      (rowId, index) => `(
        ${sqlUuid(rowId)},
        now(),
        now(),
        false,
        NULL::timestamptz,
        ${index},
        ${sqlUuid(datasetId)},
        '{}'::jsonb
      )`,
    )
    .join(",\n");
  const cellValues = cellIds
    .map(
      (cellId, index) => `(
        ${sqlUuid(cellId)},
        now(),
        now(),
        false,
        NULL::timestamptz,
        ${sqlTextLiteral(`seeded synthetic value ${suffix} ${index + 1}`)},
        ${sqlUuid(columnId)},
        ${sqlUuid(datasetId)},
        ${sqlUuid(rowIds[index])},
        'pass',
        '{}'::jsonb,
        '{}'::jsonb,
        '{}'::jsonb,
        NULL::integer,
        NULL::integer,
        NULL::double precision
      )`,
    )
    .join(",\n");

  const sql = `
WITH inserted_dataset AS (
  INSERT INTO model_hub_dataset (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    column_order,
    model_type,
    organization_id,
    workspace_id,
    source,
    column_config,
    dataset_config,
    synthetic_dataset_config,
    eval_reasons,
    eval_reason_status
  )
  VALUES (
    ${sqlUuid(datasetId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(datasetName)},
    ARRAY[${sqlTextLiteral(columnId)}]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${workspaceSql},
    'build',
    ${sqlJsonLiteral(columnConfig)},
    '{}'::jsonb,
    ${sqlJsonLiteral(config)},
    '[]'::jsonb,
    'pending'
  )
  RETURNING id
),
inserted_column AS (
  INSERT INTO model_hub_column (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    data_type,
    source,
    source_id,
    dataset_id,
    metadata,
    status
  )
  VALUES (
    ${sqlUuid(columnId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(columnName)},
    'text',
    'OTHERS',
    NULL::varchar,
    ${sqlUuid(datasetId)},
    '{}'::jsonb,
    'Completed'
  )
  RETURNING id
),
inserted_rows AS (
  INSERT INTO model_hub_row (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    "order",
    dataset_id,
    metadata
  )
  VALUES ${rowValues}
  RETURNING id
),
inserted_cells AS (
  INSERT INTO model_hub_cell (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    value,
    column_id,
    dataset_id,
    row_id,
    status,
    value_infos,
    feedback_info,
    column_metadata,
    completion_tokens,
    prompt_tokens,
    response_time
  )
  VALUES ${cellValues}
  RETURNING id
)
SELECT json_build_object(
  'fixture_created', (SELECT count(*) FROM inserted_dataset) = 1,
  'dataset_id', ${sqlUuid(datasetId)}::text,
  'dataset_name', ${sqlTextLiteral(datasetName)},
  'column_id', ${sqlUuid(columnId)}::text,
  'column_name', ${sqlTextLiteral(columnName)},
  'initial_description', ${sqlTextLiteral(initialDescription)},
  'updated_description', ${sqlTextLiteral(updatedDescription)},
  'column_description', ${sqlTextLiteral(columnDescription)},
  'row_count', (SELECT count(*) FROM inserted_rows),
  'cell_count', (SELECT count(*) FROM inserted_cells)
);
`;
  return runPostgresJson(sql);
}

async function loadSyntheticDatasetAudit({
  fixture,
  organizationId,
  workspaceId,
}) {
  const workspaceCheck = workspaceId
    ? `'workspace_id', (SELECT workspace_id::text FROM dataset_row)`
    : `'workspace_id', NULL`;
  const sql = `
WITH dataset_row AS (
  SELECT id, organization_id, workspace_id, column_order, column_config, synthetic_dataset_config
  FROM model_hub_dataset
  WHERE id = ${sqlUuid(fixture.dataset_id)}
),
active_rows AS (
  SELECT id
  FROM model_hub_row
  WHERE dataset_id = ${sqlUuid(fixture.dataset_id)}
    AND deleted = false
),
active_columns AS (
  SELECT id, name, status
  FROM model_hub_column
  WHERE dataset_id = ${sqlUuid(fixture.dataset_id)}
    AND deleted = false
),
active_cells AS (
  SELECT id
  FROM model_hub_cell
  WHERE dataset_id = ${sqlUuid(fixture.dataset_id)}
    AND deleted = false
)
SELECT json_build_object(
  'dataset_id', (SELECT id::text FROM dataset_row),
  'organization_id', (SELECT organization_id::text FROM dataset_row),
  ${workspaceCheck},
  'config_description', (
    SELECT synthetic_dataset_config #>> '{dataset,description}'
    FROM dataset_row
  ),
  'config_num_rows', (
    SELECT (synthetic_dataset_config ->> 'num_rows')::integer
    FROM dataset_row
  ),
  'config_column_name', (
    SELECT synthetic_dataset_config #>> '{columns,0,name}'
    FROM dataset_row
  ),
  'config_column_description', (
    SELECT synthetic_dataset_config #>> '{columns,0,description}'
    FROM dataset_row
  ),
  'active_row_count', (SELECT count(*) FROM active_rows),
  'active_column_count', (SELECT count(*) FROM active_columns),
  'active_cell_count', (SELECT count(*) FROM active_cells),
  'completed_column_count', (
    SELECT count(*) FROM active_columns WHERE status = 'Completed'
  ),
  'running_column_count', (
    SELECT count(*) FROM active_columns WHERE status = 'Running'
  ),
  'column_order_count', (
    SELECT COALESCE(array_length(column_order, 1), 0)
    FROM dataset_row
  ),
  'column_config_keys', (
    SELECT count(*)
    FROM dataset_row, jsonb_object_keys(column_config)
  )
);
`;
  const audit = await runPostgresJson(sql);
  assert(
    audit.organization_id === organizationId,
    "Synthetic fixture organization audit mismatch.",
  );
  if (workspaceId) {
    assert(
      audit.workspace_id === workspaceId,
      "Synthetic fixture workspace audit mismatch.",
    );
  }
  return audit;
}

function assertSyntheticDatasetAudit(audit, fixture) {
  assert(
    audit.config_description === fixture.updated_description,
    "Synthetic config DB description did not persist.",
  );
  assert(
    Number(audit.config_num_rows) === fixture.row_count,
    "Synthetic config DB row count mismatch.",
  );
  assert(
    audit.config_column_name === fixture.column_name,
    "Synthetic config DB column name mismatch.",
  );
  assert(
    audit.config_column_description === fixture.column_description,
    "Synthetic config DB column description mismatch.",
  );
  assert(
    Number(audit.active_row_count) === fixture.row_count,
    "Synthetic no-regenerate edit changed active row count.",
  );
  assert(
    Number(audit.active_column_count) === 1 &&
      Number(audit.active_cell_count) === fixture.cell_count,
    "Synthetic no-regenerate edit changed active column/cell counts.",
  );
  assert(
    Number(audit.completed_column_count) === 1 &&
      Number(audit.running_column_count) === 0,
    "Synthetic no-regenerate edit left existing columns running.",
  );
  assert(
    Number(audit.column_order_count) === 1 &&
      Number(audit.column_config_keys) === 1,
    "Synthetic no-regenerate edit changed column order/config shape.",
  );
}

async function hardDeleteDatasetFixturesByPrefix(prefix, evidence) {
  const sql = `
WITH fixture_datasets AS (
  SELECT id
  FROM model_hub_dataset
  WHERE name LIKE ${sqlTextLiteral(`${prefix}%`)}
),
deleted_cells AS (
  DELETE FROM model_hub_cell
  WHERE dataset_id IN (SELECT id FROM fixture_datasets)
  RETURNING id
),
deleted_rows AS (
  DELETE FROM model_hub_row
  WHERE dataset_id IN (SELECT id FROM fixture_datasets)
  RETURNING id
),
deleted_columns AS (
  DELETE FROM model_hub_column
  WHERE dataset_id IN (SELECT id FROM fixture_datasets)
  RETURNING id
),
deleted_datasets AS (
  DELETE FROM model_hub_dataset
  WHERE id IN (SELECT id FROM fixture_datasets)
  RETURNING id
)
SELECT json_build_object(
  'deleted_cell_count', (SELECT count(*) FROM deleted_cells),
  'deleted_row_count', (SELECT count(*) FROM deleted_rows),
  'deleted_column_count', (SELECT count(*) FROM deleted_columns),
  'deleted_dataset_count', (SELECT count(*) FROM deleted_datasets),
  'remaining_dataset_count', (
    SELECT count(*)
    FROM model_hub_dataset
    WHERE name LIKE ${sqlTextLiteral(`${prefix}%`)}
      AND id NOT IN (SELECT id FROM deleted_datasets)
  )
);
`;
  const result = await runPostgresJson(sql);
  if (
    Number(result.deleted_dataset_count) > 0 ||
    Number(result.remaining_dataset_count) > 0
  ) {
    evidence.push({
      cleanup: "hard delete synthetic config fixtures",
      status:
        Number(result.remaining_dataset_count) === 0 ? "passed" : "failed",
      audit: result,
    });
  }
  return result;
}

async function runPostgresJson(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
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
    window.dispatchClick = (element) => {
      element.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
      );
      element.dispatchEvent(
        new MouseEvent("mouseup", { bubbles: true, cancelable: true }),
      );
      element.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
    };
    window.setNativeValue = (element, value) => {
      const proto =
        element instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      setter.call(element, value);
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
    };
  });
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

async function waitForResponsesDuring(page, label, predicates, action) {
  try {
    return await Promise.all([
      ...predicates.map((predicate) =>
        page.waitForResponse(predicate, { timeout: 60000 }),
      ),
      action(),
    ]);
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
}

async function responseJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

async function waitForPath(page, pathname, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname === expectedPath,
    { timeout },
    pathname,
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
          const button = candidate.closest("button");
          return button && !button.disabled;
        }) ||
        elements.find((candidate) =>
          candidate.closest("button,a,label,[role='button'],[role='menuitem']"),
        ) ||
        elements[0];
      const clickable =
        element?.closest("button,a,label,[role='button'],[role='menuitem']") ||
        element;
      if (!clickable || clickable.disabled) return false;
      window.dispatchClick(clickable);
      return true;
    },
    { text, exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

async function clickVisibleButton(page, label) {
  const buttonHandle = await page.waitForFunction(
    (buttonLabel) =>
      Array.from(document.querySelectorAll("button")).find(
        (button) =>
          window.getComputedStyle(button).visibility !== "hidden" &&
          window.getComputedStyle(button).display !== "none" &&
          button.getBoundingClientRect().width > 0 &&
          button.getBoundingClientRect().height > 0 &&
          !button.disabled &&
          window.normalizeText(button.textContent) === buttonLabel,
      ),
    { timeout: 30000 },
    label,
  );
  const button = buttonHandle.asElement();
  assert(button, `Visible button ${label} was not found.`);
  await button.click();
}

async function waitForDialog(page, title) {
  await page.waitForFunction(
    (expectedTitle) =>
      Array.from(document.querySelectorAll('[role="dialog"]')).some(
        (dialog) =>
          window.getComputedStyle(dialog).visibility !== "hidden" &&
          window.getComputedStyle(dialog).display !== "none" &&
          dialog.getBoundingClientRect().width > 0 &&
          dialog.getBoundingClientRect().height > 0 &&
          window.normalizeText(dialog.textContent).includes(expectedTitle),
      ),
    { timeout: 30000 },
    title,
  );
}

async function selectDialogRadioByValue(page, value) {
  await waitForDialog(page, "Create Synthetic Dataset");
  const selected = await page.evaluate((radioValue) => {
    const dialog = Array.from(
      document.querySelectorAll('[role="dialog"]'),
    ).find((candidate) =>
      window
        .normalizeText(candidate.textContent)
        .includes("Create Synthetic Dataset"),
    );
    const input = dialog?.querySelector(
      `input[type="radio"][value="${radioValue}"]`,
    );
    if (!input || input.disabled) return false;
    input.click();
    return true;
  }, value);
  assert(selected, `Could not select dialog radio ${value}.`);
}

async function clickDialogButton(page, label) {
  await waitForDialog(page, "Create Synthetic Dataset");
  const buttonHandle = await page.waitForFunction(
    (buttonLabel) => {
      const dialog = Array.from(
        document.querySelectorAll('[role="dialog"]'),
      ).find((candidate) =>
        window
          .normalizeText(candidate.textContent)
          .includes("Create Synthetic Dataset"),
      );
      return Array.from(dialog?.querySelectorAll("button") || []).find(
        (candidate) =>
          !candidate.disabled &&
          window.normalizeText(candidate.textContent) === buttonLabel,
      );
    },
    { timeout: 30000 },
    label,
  );
  const button = buttonHandle.asElement();
  assert(button, `Could not click dialog button ${label}.`);
  await button.click();
}

async function snapshotSyntheticUi(page, label) {
  return page.evaluate((eventLabel) => {
    const visibleButtons = window.visibleElements("button").map((button) => ({
      text: window.normalizeText(button.textContent),
      disabled: Boolean(button.disabled),
      type: button.getAttribute("type") || "",
    }));
    const dialogs = window
      .visibleElements('[role="dialog"]')
      .map((dialog) => window.normalizeText(dialog.textContent));
    const alerts = window
      .visibleElements("[role='alert'], .MuiFormHelperText-root")
      .map((element) => window.normalizeText(element.textContent))
      .filter(Boolean);
    return {
      label: eventLabel,
      path: window.location.pathname,
      visible_buttons: visibleButtons,
      dialogs,
      alerts,
      textarea_values: window
        .visibleElements("textarea")
        .map((textarea) => textarea.value),
    };
  }, label);
}

async function setTextFieldByLabel(page, label, value) {
  const filled = await page.evaluate(
    ({ expectedLabel, nextValue }) => {
      const normalizeLabel = (value) =>
        window.normalizeText(value).replace(/\s*\*$/, "");
      const labels = window
        .visibleElements("label")
        .filter(
          (element) => normalizeLabel(element.textContent) === expectedLabel,
        );
      for (const labelElement of labels) {
        const id = labelElement.getAttribute("for");
        const field =
          (id ? document.getElementById(id) : null) ||
          labelElement
            .closest(".MuiFormControl-root")
            ?.querySelector("input, textarea");
        if (!field || field.disabled) continue;
        field.focus();
        window.setNativeValue(field, nextValue);
        field.blur();
        return true;
      }
      return false;
    },
    { expectedLabel: label, nextValue: value },
  );
  assert(filled, `Could not fill field labelled ${label}.`);
}

async function waitForFieldValue(page, label, value, timeout = 30000) {
  await page.waitForFunction(
    ({ expectedLabel, expectedValue }) => {
      const normalizeLabel = (value) =>
        window.normalizeText(value).replace(/\s*\*$/, "");
      const labels = window
        .visibleElements("label")
        .filter(
          (element) => normalizeLabel(element.textContent) === expectedLabel,
        );
      return labels.some((labelElement) => {
        const id = labelElement.getAttribute("for");
        const field =
          (id ? document.getElementById(id) : null) ||
          labelElement
            .closest(".MuiFormControl-root")
            ?.querySelector("input, textarea");
        return String(field?.value || "") === String(expectedValue);
      });
    },
    { timeout },
    { expectedLabel: label, expectedValue: value },
  );
}

async function setFirstTextareaValue(page, value) {
  const updated = await page.evaluate((nextValue) => {
    const textarea = window.visibleElements("textarea")[0];
    if (!textarea || textarea.disabled) return false;
    textarea.focus();
    window.setNativeValue(textarea, nextValue);
    textarea.blur();
    return true;
  }, value);
  assert(updated, "Could not set synthetic column description textarea.");
}

async function waitForFirstTextareaValue(page, value, timeout = 30000) {
  await page.waitForFunction(
    (expectedValue) => {
      const textarea = window.visibleElements("textarea")[0];
      return textarea?.value === expectedValue;
    },
    { timeout },
    value,
  );
}

async function assertButtonDisabled(page, text) {
  const disabled = await page.evaluate((expectedText) => {
    const button = window
      .visibleElements("button")
      .find(
        (element) => window.normalizeText(element.textContent) === expectedText,
      );
    return Boolean(button?.disabled);
  }, text);
  assert(disabled, `${text} button was not disabled.`);
}

function isModelHubApiUrl(url) {
  return url.includes("/model-hub/");
}

function isAllowedBrowserMutation(method, url) {
  if (method === "PUT" && url.includes("/update-synthetic-config/")) {
    return true;
  }
  return false;
}

function isAllowedModelHubApiFailure(status, url) {
  return status === 400 && maskUrl(url) === "/model-hub/knowledge-base/get/";
}

function sanitizeMutation(mutation) {
  return {
    method: mutation.method,
    path: maskUrl(mutation.url),
    keys: Object.keys(mutation.body || {}),
    regenerate: mutation.body?.regenerate === true,
    num_rows: mutation.body?.num_rows ?? null,
    column_count: asArray(mutation.body?.columns).length,
  };
}

function parseJsonBody(value) {
  if (!value) return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function maskUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.pathname;
  } catch {
    return url;
  }
}

function sqlUuid(value) {
  assert(isUuid(value), `Invalid UUID for SQL literal: ${value}`);
  return `'${value}'::uuid`;
}

function sqlTextLiteral(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

function sqlJsonLiteral(value) {
  return `${sqlTextLiteral(JSON.stringify(value))}::jsonb`;
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
