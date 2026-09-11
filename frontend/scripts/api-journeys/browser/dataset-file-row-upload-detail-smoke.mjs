/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
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
const DATASET_PREFIX = "ui_file_row_upload_";
const SCREENSHOT_PATH = "/tmp/dataset-file-row-upload-detail-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/dataset-file-row-upload-detail-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const csvFileName = `dataset-file-row-upload-${suffix}.csv`;
  const csvPath = join(
    await mkdtemp(join(tmpdir(), "dataset-file-upload-")),
    csvFileName,
  );
  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const modelHubRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let datasetId = null;
  let tmpDir = null;
  let caughtError = null;

  try {
    tmpDir = csvPath.slice(0, csvPath.length - csvFileName.length - 1);
    await hardDeleteDatasetFixturesByPrefix(DATASET_PREFIX, cleanupEvidence);
    const fixture = await seedDatasetFixture({
      runId: auth.runId,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assert(
      fixture.fixture_created === true,
      "Dataset file-row upload fixture was not seeded.",
    );
    datasetId = fixture.dataset_id;

    const csvContent = [
      `${fixture.input_column_name},${fixture.new_column_name}`,
      `${fixture.imported_input_one},${fixture.imported_extra_one}`,
      `${fixture.imported_input_two},${fixture.imported_extra_two}`,
      "",
    ].join("\n");
    await writeFile(csvPath, csvContent, "utf8");

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });
    page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installBrowserState(page, auth);

    page.on("request", (request) => {
      const url = request.url();
      if (!isModelHubApiUrl(url)) return;
      modelHubRequests.push(`${request.method()} ${url}`);
      if (MUTATION_METHODS.has(request.method())) {
        const mutation = `${request.method()} ${url}`;
        browserMutations.push(mutation);
        if (!isAllowedMutation(request.method(), url)) {
          unexpectedMutations.push(mutation);
        }
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isModelHubApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponsesDuring(
      page,
      "dataset detail table load",
      [
        (response) =>
          response
            .url()
            .includes(`/model-hub/develops/${datasetId}/get-dataset-table/`) &&
          response.request().method() === "GET" &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/develop/${datasetId}?tab=data`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPathIncludes(page, `/dashboard/develop/${datasetId}`);
    await waitForVisibleText(page, fixture.existing_input_one, {
      exact: true,
    });
    await waitForVisibleText(page, fixture.input_column_name, { exact: true });

    await clickVisibleText(page, "Add Row", { exact: true });
    await waitForVisibleText(page, "Add Rows", { exact: true });
    await clickVisibleText(page, "Upload a file (JSONl/ JSON/ CSV)", {
      exact: true,
    });
    await waitForVisibleText(page, "Upload a File", { exact: true });

    const input = await page.waitForSelector('input[type="file"]', {
      timeout: 30000,
    });
    await input.uploadFile(csvPath);
    await waitForVisibleText(page, csvFileName, { exact: true });

    const uploadResponse = await waitForResponseDuring(
      page,
      "dataset row CSV upload",
      (response) =>
        response.url().includes("/model-hub/develops/add_rows_from_file/") &&
        response.request().method() === "POST",
      () => clickButtonWithMouse(page, "Done"),
    );
    const uploadPayload = await responseJson(uploadResponse);
    assert(
      uploadResponse.status() >= 200 && uploadResponse.status() < 300,
      `Add rows from file returned HTTP ${uploadResponse.status()}: ${JSON.stringify(
        uploadPayload,
      )}`,
    );

    await waitForTableValues(auth.client, datasetId, [
      fixture.imported_input_one,
      fixture.imported_input_two,
      fixture.imported_extra_one,
      fixture.imported_extra_two,
    ]);
    await waitForVisibleText(page, fixture.imported_input_one, { exact: true });
    await waitForVisibleText(page, fixture.imported_extra_two, { exact: true });
    await waitForNoVisibleText(page, "Invalid Date");
    await waitForNoVisibleText(page, "undefined");

    const table = await getDatasetTable(auth.client, datasetId);
    const rows = asArray(table.table).filter((row) => row?.row_id);
    const columns = asArray(table.column_config);
    const inputColumn = columns.find(
      (column) => column?.name === fixture.input_column_name,
    );
    const extraColumn = columns.find(
      (column) => column?.name === fixture.new_column_name,
    );
    assert(inputColumn?.id, "Input column missing after file-row upload.");
    assert(extraColumn?.id, "New CSV column missing after file-row upload.");
    assert(rows.length === 4, "File-row upload did not produce four rows.");
    assert(
      rows.some(
        (row) =>
          cellValueFor(row, inputColumn.id) === fixture.imported_input_one,
      ) &&
        rows.some(
          (row) =>
            cellValueFor(row, extraColumn.id) === fixture.imported_extra_two,
        ),
      "Uploaded CSV values did not round-trip through the table API.",
    );

    const dbAudit = await loadDatasetFileRowUploadAudit({
      fixture,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assertDatasetFileRowUploadAudit(dbAudit, fixture, auth);

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      unexpectedMutations.length === 0,
      `Unexpected Model Hub mutations: ${unexpectedMutations
        .map(maskRequest)
        .join(", ")}`,
    );
    assert(
      apiFailures.length === 0,
      `Unexpected Model Hub API failures: ${apiFailures.join(", ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    await deleteDataset(auth.client, datasetId, cleanupEvidence);
    const deleteAudit = await loadDatasetDeleteAudit(datasetId);
    assert(
      deleteAudit.active_dataset_count === 0 &&
        deleteAudit.deleted_dataset_count === 1 &&
        deleteAudit.deleted_at_count === 1,
      `Dataset delete audit mismatch: ${JSON.stringify(deleteAudit)}`,
    );
    cleanupEvidence.push({
      cleanup: "verify public dataset delete",
      status: "passed",
      audit: deleteAudit,
    });
    datasetId = null;

    const hardCleanup = await hardDeleteDatasetFixturesByPrefix(
      DATASET_PREFIX,
      cleanupEvidence,
    );
    assert(
      hardCleanup.remaining_dataset_count === 0,
      `Hard cleanup left dataset fixture rows: ${JSON.stringify(hardCleanup)}`,
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
          uploaded_file_name: csvFileName,
          rows_after_upload: rows.length,
          columns_after_upload: columns.length,
          db_audit: dbAudit,
          browser_mutations: browserMutations.map(maskRequest),
          model_hub_request_count: modelHubRequests.length,
          screenshot: SCREENSHOT_PATH,
          cleanup: cleanupEvidence,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    caughtError = error;
    const domDebug = page
      ? await collectDomDebug(page).catch(() => null)
      : null;
    console.error(
      JSON.stringify(
        {
          status: "failed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          dataset_id: datasetId,
          browser_mutations: browserMutations.map(maskRequest),
          model_hub_request_count: modelHubRequests.length,
          api_failures: apiFailures.map(maskRequest),
          page_errors: pageErrors,
          dom_debug: domDebug,
        },
        null,
        2,
      ),
    );
    if (page) {
      await page
        .screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .then(() => {
          console.error(`failure_screenshot=${FAILURE_SCREENSHOT_PATH}`);
        })
        .catch(() => null);
    }
  } finally {
    if (browser) await browser.close();
    if (datasetId) {
      await deleteDataset(auth.client, datasetId, cleanupEvidence).catch(
        (error) => {
          caughtError = appendCleanupError(caughtError, error);
        },
      );
    }
    await hardDeleteDatasetFixturesByPrefix(
      DATASET_PREFIX,
      cleanupEvidence,
    ).catch((error) => {
      caughtError = appendCleanupError(caughtError, error);
    });
    if (tmpDir)
      await rm(tmpDir, { recursive: true, force: true }).catch(() => null);
  }

  if (caughtError) throw caughtError;
}

async function collectDomDebug(page) {
  return page.evaluate(() => {
    const visibleText = (selector) =>
      window
        .visibleElements(selector)
        .map((element) => window.normalizeText(element.textContent))
        .filter(Boolean);
    const doneButton = window
      .visibleElements("button")
      .find((button) => window.normalizeText(button.textContent) === "Done");
    const rect = doneButton?.getBoundingClientRect();
    const topElement =
      rect &&
      document.elementFromPoint(
        rect.left + rect.width / 2,
        rect.top + rect.height / 2,
      );
    return {
      form_count: document.querySelectorAll("form").length,
      done_button: doneButton
        ? {
            disabled: doneButton.disabled,
            type: doneButton.getAttribute("type"),
            pointer_events: window.getComputedStyle(doneButton).pointerEvents,
            top_element_tag: topElement?.tagName || null,
            top_element_text: topElement
              ? window.normalizeText(topElement.textContent).slice(0, 120)
              : null,
          }
        : null,
      file_input_count: document.querySelectorAll('input[type="file"]').length,
      file_input_files: Array.from(
        document.querySelectorAll('input[type="file"]'),
      ).map((input) => input.files?.length || 0),
      helper_text: visibleText(".MuiFormHelperText-root"),
      dialog_text: visibleText('[role="dialog"]').slice(0, 5),
    };
  });
}

async function seedDatasetFixture({ runId, organizationId, workspaceId }) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB seed.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB seed.");
  const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  const datasetId = randomUUID();
  const inputColumnId = randomUUID();
  const rowOneId = randomUUID();
  const rowTwoId = randomUUID();
  const datasetName = `${DATASET_PREFIX}${suffix}`;
  const inputColumnName = "input";
  const newColumnName = "file_extra";
  const existingInputOne = `existing file row one ${suffix}`;
  const existingInputTwo = `existing file row two ${suffix}`;
  const importedInputOne = `uploaded file row one ${suffix}`;
  const importedInputTwo = `uploaded file row two ${suffix}`;
  const importedExtraOne = `uploaded extra one ${suffix}`;
  const importedExtraTwo = `uploaded extra two ${suffix}`;
  const columnConfig = {
    [inputColumnId]: { is_visible: true, is_frozen: null },
  };
  const workspaceSql = workspaceId ? sqlUuid(workspaceId) : "NULL::uuid";
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
    ARRAY[${sqlTextLiteral(inputColumnId)}]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${workspaceSql},
    'build',
    ${sqlJsonLiteral(columnConfig)},
    '{}'::jsonb,
    '{}'::jsonb,
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
    ${sqlUuid(inputColumnId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(inputColumnName)},
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
  VALUES
    (
      ${sqlUuid(rowOneId)},
      now() - interval '2 minutes',
      now() - interval '2 minutes',
      false,
      NULL::timestamptz,
      4,
      ${sqlUuid(datasetId)},
      '{}'::jsonb
    ),
    (
      ${sqlUuid(rowTwoId)},
      now() - interval '1 minute',
      now() - interval '1 minute',
      false,
      NULL::timestamptz,
      5,
      ${sqlUuid(datasetId)},
      '{}'::jsonb
    )
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
  VALUES
    (
      ${sqlUuid(randomUUID())},
      now(),
      now(),
      false,
      NULL::timestamptz,
      ${sqlTextLiteral(existingInputOne)},
      ${sqlUuid(inputColumnId)},
      ${sqlUuid(datasetId)},
      ${sqlUuid(rowOneId)},
      'pass',
      '{}'::jsonb,
      '{}'::jsonb,
      '{}'::jsonb,
      NULL::integer,
      NULL::integer,
      NULL::double precision
    ),
    (
      ${sqlUuid(randomUUID())},
      now(),
      now(),
      false,
      NULL::timestamptz,
      ${sqlTextLiteral(existingInputTwo)},
      ${sqlUuid(inputColumnId)},
      ${sqlUuid(datasetId)},
      ${sqlUuid(rowTwoId)},
      'pass',
      '{}'::jsonb,
      '{}'::jsonb,
      '{}'::jsonb,
      NULL::integer,
      NULL::integer,
      NULL::double precision
    )
  RETURNING id
)
SELECT json_build_object(
  'fixture_created', (SELECT count(*) FROM inserted_dataset) = 1,
  'dataset_id', ${sqlUuid(datasetId)}::text,
  'dataset_name', ${sqlTextLiteral(datasetName)},
  'input_column_id', ${sqlUuid(inputColumnId)}::text,
  'input_column_name', ${sqlTextLiteral(inputColumnName)},
  'new_column_name', ${sqlTextLiteral(newColumnName)},
  'existing_input_one', ${sqlTextLiteral(existingInputOne)},
  'existing_input_two', ${sqlTextLiteral(existingInputTwo)},
  'imported_input_one', ${sqlTextLiteral(importedInputOne)},
  'imported_input_two', ${sqlTextLiteral(importedInputTwo)},
  'imported_extra_one', ${sqlTextLiteral(importedExtraOne)},
  'imported_extra_two', ${sqlTextLiteral(importedExtraTwo)},
  'inserted_row_count', (SELECT count(*) FROM inserted_rows),
  'inserted_cell_count', (SELECT count(*) FROM inserted_cells)
);
`;
  return runPostgresJson(sql);
}

async function waitForTableValues(client, datasetId, expectedValues) {
  const deadline = Date.now() + 60000;
  let lastValues = [];
  while (Date.now() < deadline) {
    const table = await getDatasetTable(client, datasetId);
    const columns = asArray(table.column_config);
    const rows = asArray(table.table).filter((row) => row?.row_id);
    lastValues = rows.flatMap((row) =>
      columns.map((column) => cellValueFor(row, column.id)).filter(Boolean),
    );
    if (expectedValues.every((value) => lastValues.includes(value))) return;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(
    `Timed out waiting for uploaded CSV values; saw ${JSON.stringify(
      lastValues,
    )}`,
  );
}

async function getDatasetTable(client, datasetId) {
  return client.get(
    apiPath("/model-hub/develops/{dataset_id}/get-dataset-table/", {
      dataset_id: datasetId,
    }),
    { query: { current_page_index: 0, page_size: 50 } },
  );
}

function cellValueFor(row, columnId) {
  if (!row || !columnId) return undefined;
  const direct = row[columnId];
  if (direct && typeof direct === "object" && "cell_value" in direct) {
    return direct.cell_value;
  }
  return direct;
}

async function deleteDataset(client, datasetId, evidence) {
  await client.delete(apiPath("/model-hub/develops/delete_dataset/"), {
    body: { dataset_ids: [datasetId] },
    okStatuses: [200, 404],
  });
  evidence.push({
    cleanup: "public delete dataset file-row upload fixture",
    status: "passed",
    dataset_id: datasetId,
  });
}

async function loadDatasetFileRowUploadAudit({
  fixture,
  organizationId,
  workspaceId,
}) {
  const workspaceCheck = workspaceId
    ? `'workspace_id', (SELECT workspace_id::text FROM dataset_row)`
    : `'workspace_id', NULL`;
  const sql = `
WITH dataset_row AS (
  SELECT id, organization_id, workspace_id, column_order, column_config
  FROM model_hub_dataset
  WHERE id = ${sqlUuid(fixture.dataset_id)}
),
rows AS (
  SELECT id, "order"
  FROM model_hub_row
  WHERE dataset_id = ${sqlUuid(fixture.dataset_id)}
    AND deleted = false
),
columns AS (
  SELECT id, name
  FROM model_hub_column
  WHERE dataset_id = ${sqlUuid(fixture.dataset_id)}
    AND deleted = false
),
new_column AS (
  SELECT id
  FROM columns
  WHERE name = ${sqlTextLiteral(fixture.new_column_name)}
  LIMIT 1
),
input_column AS (
  SELECT id
  FROM columns
  WHERE name = ${sqlTextLiteral(fixture.input_column_name)}
  LIMIT 1
)
SELECT json_build_object(
  'dataset_id', (SELECT id::text FROM dataset_row),
  'organization_id', (SELECT organization_id::text FROM dataset_row),
  ${workspaceCheck},
  'row_count', (SELECT count(*) FROM rows),
  'column_count', (SELECT count(*) FROM columns),
  'column_order_count', (
    SELECT COALESCE(array_length(column_order, 1), 0)
    FROM dataset_row
  ),
  'column_config_keys', (
    SELECT count(*)
    FROM dataset_row, jsonb_object_keys(column_config)
  ),
  'max_order', COALESCE((SELECT max("order") FROM rows), -1),
  'imported_input_values', (
    SELECT COALESCE(json_agg(c.value ORDER BY r."order"), '[]'::json)
    FROM rows r
    JOIN input_column ic ON true
    JOIN model_hub_cell c
      ON c.row_id = r.id
     AND c.column_id = ic.id
     AND c.deleted = false
    WHERE r."order" >= 6
  ),
  'imported_extra_values', (
    SELECT COALESCE(json_agg(c.value ORDER BY r."order"), '[]'::json)
    FROM rows r
    JOIN new_column nc ON true
    JOIN model_hub_cell c
      ON c.row_id = r.id
     AND c.column_id = nc.id
     AND c.deleted = false
    WHERE r."order" >= 6
  ),
  'existing_blank_cells_for_new_column', (
    SELECT count(*)
    FROM rows r
    JOIN new_column nc ON true
    JOIN model_hub_cell c
      ON c.row_id = r.id
     AND c.column_id = nc.id
     AND c.deleted = false
    WHERE r."order" < 6
      AND COALESCE(c.value, '') = ''
  ),
  'active_cell_count', (
    SELECT count(*)
    FROM model_hub_cell c
    WHERE c.dataset_id = ${sqlUuid(fixture.dataset_id)}
      AND c.deleted = false
  )
);
`;
  const audit = await runPostgresJson(sql);
  assert(
    audit.organization_id === organizationId,
    "Dataset file-row upload organization audit mismatch.",
  );
  if (workspaceId) {
    assert(
      audit.workspace_id === workspaceId,
      "Dataset file-row upload workspace audit mismatch.",
    );
  }
  return audit;
}

function assertDatasetFileRowUploadAudit(audit, fixture) {
  assert(Number(audit.row_count) === 4, "File-row upload row count mismatch.");
  assert(
    Number(audit.column_count) === 2,
    "File-row upload column count mismatch.",
  );
  assert(
    Number(audit.column_order_count) === 2 &&
      Number(audit.column_config_keys) === 2,
    "File-row upload did not persist new column order/config.",
  );
  assert(
    Number(audit.max_order) === 7,
    "File-row upload did not append after current max row order.",
  );
  assert(
    JSON.stringify(audit.imported_input_values) ===
      JSON.stringify([fixture.imported_input_one, fixture.imported_input_two]),
    "File-row upload input values did not persist in row order.",
  );
  assert(
    JSON.stringify(audit.imported_extra_values) ===
      JSON.stringify([fixture.imported_extra_one, fixture.imported_extra_two]),
    "File-row upload extra values did not persist in row order.",
  );
  assert(
    Number(audit.existing_blank_cells_for_new_column) === 2,
    "File-row upload did not backfill existing rows for the new CSV column.",
  );
  assert(
    Number(audit.active_cell_count) === 8,
    "File-row upload active cell count mismatch.",
  );
}

async function loadDatasetDeleteAudit(datasetId) {
  const sql = `
SELECT json_build_object(
  'active_dataset_count', count(*) FILTER (WHERE deleted = false),
  'deleted_dataset_count', count(*) FILTER (WHERE deleted = true),
  'deleted_at_count', count(*) FILTER (WHERE deleted_at IS NOT NULL)
)
FROM model_hub_dataset
WHERE id = ${sqlUuid(datasetId)};
`;
  return runPostgresJson(sql);
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
      cleanup: "hard delete dataset file-row upload fixtures",
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

async function waitForResponseDuring(page, label, predicate, action) {
  try {
    const [response] = await Promise.all([
      page.waitForResponse(predicate, { timeout: 60000 }),
      action(),
    ]);
    return response;
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
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

async function waitForPathIncludes(page, pathname, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname.includes(expectedPath),
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

async function waitForNoVisibleText(page, text, timeout = 30000) {
  await page.waitForFunction(
    (expectedText) =>
      !window
        .visibleElements()
        .some((element) =>
          window.normalizeText(element.textContent).includes(expectedText),
        ),
    { timeout },
    text,
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
        elements.find((candidate) => candidate.closest("a,[role='button']")) ||
        elements[0];
      const clickable =
        element?.closest(
          "button,a,[role='button'],[role='menuitem'],label,tr",
        ) || element;
      if (!clickable || clickable.disabled) return false;
      window.dispatchClick(clickable);
      return true;
    },
    { text, exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

async function clickButtonWithMouse(page, label, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
  const buttons = await page.$$("button");
  for (const button of buttons) {
    const isTarget = await button.evaluate((element, expectedLabel) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0 &&
        !element.disabled &&
        window.normalizeText(element.textContent) === expectedLabel
      );
    }, label);
    if (!isTarget) continue;
    await button.evaluate((element) => element.click());
    await new Promise((resolve) => setTimeout(resolve, 250));
    await button.click({ delay: 25 });
    await new Promise((resolve) => setTimeout(resolve, 250));
    await button.evaluate((element) => {
      const form = element.closest("form");
      if (form?.requestSubmit) {
        form.requestSubmit(element);
      }
    });
    return;
  }
  throw new Error(`Could not click visible button: ${label}`);
}

async function responseJson(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function isModelHubApiUrl(rawUrl) {
  const url = new URL(rawUrl);
  return (
    url.origin ===
      new URL(process.env.API_BASE || "http://localhost:8003").origin &&
    (url.pathname.startsWith("/model-hub/develops/") ||
      url.pathname.startsWith("/model-hub/datasets/") ||
      url.pathname.startsWith("/model-hub/dataset/"))
  );
}

function isAllowedMutation(method, rawUrl) {
  const path = new URL(rawUrl).pathname;
  return (
    method === "POST" && path === "/model-hub/develops/add_rows_from_file/"
  );
}

function maskRequest(rawRequest) {
  const [method, rawUrl] = rawRequest.split(" ");
  const url = new URL(rawUrl);
  return `${method} ${url.pathname}`;
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlTextLiteral(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function sqlJsonLiteral(value) {
  return `${sqlTextLiteral(JSON.stringify(value))}::jsonb`;
}

function appendCleanupError(currentError, cleanupError) {
  if (!currentError) return cleanupError;
  currentError.message = `${currentError.message}; cleanup failed: ${cleanupError.message}`;
  return currentError;
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
