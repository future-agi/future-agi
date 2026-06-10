/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
import process from "node:process";
import { fileURLToPath } from "node:url";
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
const backendRoot = fileURLToPath(
  new URL("../../../../futureagi/", import.meta.url),
);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const DATASET_PREFIX = "ui_local_file_create_";
const SCREENSHOT_PATH = "/tmp/dataset-local-file-create-smoke.png";
const PLAN_LIMIT_SCREENSHOT_PATH =
  "/tmp/dataset-local-file-create-plan-limited-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/dataset-local-file-create-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  const datasetName = `${DATASET_PREFIX}${suffix}`;
  const csvFileName = `${datasetName}.csv`;
  const tmpDir = await mkdtemp(join(tmpdir(), "dataset-local-file-create-"));
  const csvPath = join(tmpDir, csvFileName);
  const inputOne = `ui local file input one ${suffix}`;
  const inputTwo = `ui local file input two ${suffix}`;
  const expectedOne = `ui local file expected one ${suffix}`;
  const expectedTwo = `ui local file expected two ${suffix}`;
  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const modelHubRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let datasetId = null;
  let caughtError = null;

  await writeFile(
    csvPath,
    `input,expected\n${inputOne},${expectedOne}\n${inputTwo},${expectedTwo}\n`,
    "utf8",
  );

  try {
    await hardDeleteDatasetFixturesByPrefix(DATASET_PREFIX, cleanupEvidence);

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
      "initial datasets list load",
      [
        (response) =>
          response.url().includes("/model-hub/develops/get-datasets/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/develop`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/develop");

    await waitForVisibleText(page, "Add Dataset", { exact: true });
    await clickVisibleText(page, "Add Dataset", { exact: true });
    await waitForVisibleText(page, "Add dataset", { exact: true });
    await clickVisibleText(page, "Upload a file (JSON, CSV)", {
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
      "local-file dataset upload",
      (response) =>
        response
          .url()
          .includes("/model-hub/develops/create-dataset-from-local-file/") &&
        response.request().method() === "POST",
      () => clickVisibleButton(page, "Save"),
    );
    const uploadPayload = await responseJson(uploadResponse);

    if (uploadResponse.status() === 429) {
      await page.screenshot({
        path: PLAN_LIMIT_SCREENSHOT_PATH,
        fullPage: true,
      });
      console.log(
        JSON.stringify(
          {
            status: "skipped",
            reason: "local-file dataset creation is plan-limited",
            app_base: APP_BASE,
            api_base: auth.apiBase,
            organization_id: auth.organizationId,
            workspace_id: auth.workspaceId,
            response_status: uploadResponse.status(),
            response_message:
              uploadPayload?.message ||
              uploadPayload?.detail ||
              uploadPayload?.error ||
              null,
            screenshot: PLAN_LIMIT_SCREENSHOT_PATH,
            browser_mutations: browserMutations.map(maskRequest),
            cleanup: cleanupEvidence,
          },
          null,
          2,
        ),
      );
      return;
    }

    assert(
      uploadResponse.status() >= 200 && uploadResponse.status() < 300,
      `Local-file dataset upload returned HTTP ${uploadResponse.status()}: ${JSON.stringify(
        uploadPayload,
      )}`,
    );

    const created = uploadPayload?.result || uploadPayload || {};
    datasetId = created.dataset_id || created.datasetId || null;
    assert(datasetId, "Local-file dataset upload response omitted dataset_id.");
    assert(
      created.dataset_name === datasetName,
      "Local-file dataset upload returned a different dataset name.",
    );
    assert(
      created.processing_status === "queued",
      "Local-file dataset upload did not return queued status.",
    );
    assert(
      Number(created.estimated_rows) === 2 &&
        Number(created.estimated_columns) === 2,
      "Local-file dataset upload returned unexpected row/column estimates.",
    );

    await waitForPathIncludes(page, `/dashboard/develop/${datasetId}`, 30000);

    const queuedProgress = await getDatasetCreationProgress(
      auth.client,
      datasetId,
    );
    assert(
      queuedProgress.processing_status === "queued" ||
        queuedProgress.processing_status === "completed",
      "Local-file progress returned an unexpected status after upload.",
    );
    assert(
      queuedProgress.original_filename === csvFileName,
      "Local-file progress did not expose the uploaded filename.",
    );

    const queuedAudit = await loadLocalFileDatasetAudit({
      datasetId,
      datasetName,
      fileName: csvFileName,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assertLocalFileQueuedAudit(queuedAudit, {
      datasetId,
      datasetName,
      fileName: csvFileName,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      expectedProcessingStatus: queuedProgress.processing_status,
    });

    let workerResult = null;
    if (queuedProgress.processing_status !== "completed") {
      workerResult = await materializeLocalFileDataset(datasetId);
      assert(
        workerResult.processing_status === "completed",
        "Local-file worker did not complete the browser-created dataset.",
      );
    }

    const completedProgress = await waitForCompletedProgress(
      auth.client,
      datasetId,
    );
    assert(
      completedProgress.original_filename === csvFileName,
      "Completed progress did not preserve the uploaded filename.",
    );

    await waitForTableValues(auth.client, datasetId, [
      inputOne,
      inputTwo,
      expectedOne,
      expectedTwo,
    ]);
    await waitForResponsesDuring(
      page,
      "completed local-file dataset detail table load",
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
    await waitForVisibleText(page, inputOne, { exact: true, timeout: 60000 });
    await waitForVisibleText(page, expectedTwo, {
      exact: true,
      timeout: 60000,
    });
    await waitForNoVisibleText(page, "Invalid Date");
    await waitForNoVisibleText(page, "undefined");

    const table = await getDatasetTable(auth.client, datasetId);
    const rows = asArray(table.table).filter((row) => row?.row_id);
    const columns = asArray(table.column_config);
    assert(rows.length === 2, "Local-file upload did not produce two rows.");
    assert(
      columns.some((column) => column?.name === "input") &&
        columns.some((column) => column?.name === "expected"),
      "Local-file upload did not expose input/expected columns.",
    );

    const completedAudit = await loadLocalFileDatasetAudit({
      datasetId,
      datasetName,
      fileName: csvFileName,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assertLocalFileCompletedAudit(completedAudit, {
      datasetId,
      datasetName,
      fileName: csvFileName,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      inputValues: [inputOne, inputTwo],
      expectedValues: [expectedOne, expectedTwo],
    });

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      unexpectedMutations.length === 0,
      `Unexpected Model Hub mutations: ${unexpectedMutations
        .map(maskRequest)
        .join(", ")}`,
    );
    assert(
      apiFailures.length === 0,
      `Unexpected Model Hub API failures: ${apiFailures
        .map(maskRequest)
        .join(", ")}`,
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
      cleanup: "verify public local-file dataset delete",
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
      `Hard cleanup left local-file dataset fixtures: ${JSON.stringify(
        hardCleanup,
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
          dataset_id: completedAudit.dataset_id,
          dataset_name: completedAudit.dataset_name,
          uploaded_file_name: csvFileName,
          initial_processing_status: queuedProgress.processing_status,
          completed_processing_status: completedProgress.processing_status,
          rows_after_worker: Number(completedAudit.row_count),
          columns_after_worker: Number(completedAudit.column_count),
          cells_after_worker: Number(completedAudit.cell_count),
          worker_result: workerResult,
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
    return {
      path: window.location.pathname,
      visible_buttons: visibleText("button").slice(0, 20),
      dialog_text: visibleText('[role="dialog"]').slice(0, 8),
      file_input_count: document.querySelectorAll('input[type="file"]').length,
      file_input_files: Array.from(
        document.querySelectorAll('input[type="file"]'),
      ).map((input) => input.files?.length || 0),
      helper_text: visibleText(".MuiFormHelperText-root"),
    };
  });
}

async function getDatasetCreationProgress(client, datasetId) {
  return client.get(
    apiPath("/model-hub/develops/dataset-creation-progress/{dataset_id}/", {
      dataset_id: datasetId,
    }),
  );
}

async function waitForCompletedProgress(client, datasetId) {
  const deadline = Date.now() + 60000;
  let lastProgress = null;
  while (Date.now() < deadline) {
    lastProgress = await getDatasetCreationProgress(client, datasetId);
    if (lastProgress.processing_status === "completed") return lastProgress;
    if (lastProgress.processing_status === "failed") {
      throw new Error(
        `Local-file dataset processing failed: ${JSON.stringify(lastProgress)}`,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(
    `Timed out waiting for local-file dataset completion; last progress ${JSON.stringify(
      lastProgress,
    )}`,
  );
}

async function materializeLocalFileDataset(datasetId) {
  assert(isUuid(datasetId), "datasetId must be a UUID for worker run.");
  const script = `
import json
from model_hub.models.develop_dataset import Cell, Dataset
from model_hub.models.choices import CellStatus, DataTypeChoices
from model_hub.views.datasets.create.file_upload import process_dataset_from_file

dataset = Dataset.objects.get(id=${JSON.stringify(datasetId)})
dataset_config = dataset.dataset_config or {}
process_dataset_from_file.run_sync(
    str(dataset.id),
    dataset_config["file_url"],
    dataset_config["original_filename"],
)
dataset.refresh_from_db()
media_cell_count = Cell.objects.filter(
    dataset=dataset,
    deleted=False,
    status=CellStatus.RUNNING.value,
    column__data_type__in=[
        DataTypeChoices.IMAGE.value,
        DataTypeChoices.IMAGES.value,
        DataTypeChoices.AUDIO.value,
        DataTypeChoices.DOCUMENT.value,
    ],
).count()
print(json.dumps({
    "dataset_id": str(dataset.id),
    "processing_status": (dataset.dataset_config or {}).get("file_processing_status"),
    "completed_columns": (dataset.dataset_config or {}).get("completed_columns"),
    "error_columns": (dataset.dataset_config or {}).get("error_columns"),
    "media_dispatch_skipped": media_cell_count == 0,
}))
`;
  return runBackendShellJson(script);
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
    `Timed out waiting for local-file table values; saw ${JSON.stringify(
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
    cleanup: "public delete local-file dataset fixture",
    status: "passed",
    dataset_id: datasetId,
  });
}

async function loadLocalFileDatasetAudit({
  datasetId,
  datasetName,
  fileName,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(datasetId), "datasetId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH dataset_row AS (
  SELECT id, name, organization_id, workspace_id, dataset_config
  FROM model_hub_dataset
  WHERE id = ${sqlUuid(datasetId)}
    AND name = ${sqlTextLiteral(datasetName)}
    AND deleted = false
),
rows AS (
  SELECT id, "order"
  FROM model_hub_row
  WHERE dataset_id = ${sqlUuid(datasetId)}
    AND deleted = false
),
columns AS (
  SELECT id, name, status
  FROM model_hub_column
  WHERE dataset_id = ${sqlUuid(datasetId)}
    AND deleted = false
)
SELECT COALESCE((
  SELECT json_build_object(
    'dataset_id', d.id::text,
    'dataset_name', d.name,
    'organization_id', d.organization_id::text,
    'workspace_id', d.workspace_id::text,
    'dataset_source_local', COALESCE((d.dataset_config->>'dataset_source_local')::boolean, false),
    'processing_status', d.dataset_config->>'file_processing_status',
    'original_filename', d.dataset_config->>'original_filename',
    'file_url_set', COALESCE(d.dataset_config->>'file_url', '') <> '',
    'estimated_rows', d.dataset_config->>'estimated_rows',
    'estimated_columns', d.dataset_config->>'estimated_columns',
    'total_rows', d.dataset_config->>'total_rows',
    'total_columns', d.dataset_config->>'total_columns',
    'completed_columns', d.dataset_config->>'completed_columns',
    'error_columns', d.dataset_config->>'error_columns',
    'completed_at_set', d.dataset_config ? 'file_processing_completed_at',
    'row_count', (SELECT count(*) FROM rows),
    'column_count', (SELECT count(*) FROM columns),
    'cell_count', (
      SELECT count(*)
      FROM model_hub_cell c
      WHERE c.dataset_id = d.id
        AND c.deleted = false
    ),
    'column_names', (
      SELECT COALESCE(json_agg(name ORDER BY name), '[]'::json)
      FROM columns
    ),
    'column_statuses', (
      SELECT COALESCE(json_object_agg(name, status), '{}'::json)
      FROM columns
    ),
    'input_values', (
      SELECT COALESCE(json_agg(c.value ORDER BY r."order"), '[]'::json)
      FROM rows r
      JOIN columns col ON col.name = 'input'
      JOIN model_hub_cell c
        ON c.row_id = r.id
       AND c.column_id = col.id
       AND c.deleted = false
    ),
    'expected_values', (
      SELECT COALESCE(json_agg(c.value ORDER BY r."order"), '[]'::json)
      FROM rows r
      JOIN columns col ON col.name = 'expected'
      JOIN model_hub_cell c
        ON c.row_id = r.id
       AND c.column_id = col.id
       AND c.deleted = false
    )
  )
  FROM dataset_row d
), '{}'::json);
`;
  const audit = await runPostgresJson(sql);
  assert(
    audit.organization_id === organizationId,
    "Local-file dataset audit organization mismatch.",
  );
  if (workspaceId) {
    assert(
      audit.workspace_id === workspaceId,
      "Local-file dataset audit workspace mismatch.",
    );
  }
  assert(
    audit.original_filename === fileName,
    "Local-file dataset audit original filename mismatch.",
  );
  return audit;
}

function assertLocalFileQueuedAudit(
  audit,
  {
    datasetId,
    datasetName,
    expectedProcessingStatus,
    fileName,
    organizationId,
    workspaceId,
  },
) {
  assertBaseLocalFileAudit(audit, {
    datasetId,
    datasetName,
    fileName,
    organizationId,
    workspaceId,
  });
  assert(
    audit.processing_status === expectedProcessingStatus,
    "Local-file queued audit processing status mismatch.",
  );
  if (expectedProcessingStatus === "queued") {
    assert(
      Number(audit.row_count) === 0 &&
        Number(audit.column_count) === 0 &&
        Number(audit.cell_count) === 0,
      "Queued local-file dataset materialized rows before worker completion.",
    );
  }
}

function assertLocalFileCompletedAudit(
  audit,
  {
    datasetId,
    datasetName,
    expectedValues,
    fileName,
    inputValues,
    organizationId,
    workspaceId,
  },
) {
  assertBaseLocalFileAudit(audit, {
    datasetId,
    datasetName,
    fileName,
    organizationId,
    workspaceId,
  });
  assert(
    audit.processing_status === "completed",
    "Local-file completed audit processing status mismatch.",
  );
  assert(
    Number(audit.total_rows) === 2 &&
      Number(audit.total_columns) === 2 &&
      Number(audit.row_count) === 2 &&
      Number(audit.column_count) === 2 &&
      Number(audit.cell_count) === 4,
    "Local-file completed audit row/column/cell counts mismatch.",
  );
  assert(
    Number(audit.completed_columns) === 2 &&
      Number(audit.error_columns) === 0 &&
      audit.completed_at_set === true,
    "Local-file completed audit status metadata mismatch.",
  );
  assert(
    JSON.stringify(audit.column_names) ===
      JSON.stringify(["expected", "input"]),
    "Local-file completed audit column names mismatch.",
  );
  assert(
    JSON.stringify(audit.input_values) === JSON.stringify(inputValues),
    "Local-file completed audit input values mismatch.",
  );
  assert(
    JSON.stringify(audit.expected_values) === JSON.stringify(expectedValues),
    "Local-file completed audit expected values mismatch.",
  );
}

function assertBaseLocalFileAudit(
  audit,
  { datasetId, datasetName, fileName, organizationId, workspaceId },
) {
  assert(
    audit?.dataset_id === datasetId,
    "Local-file dataset audit dataset id mismatch.",
  );
  assert(
    audit.dataset_name === datasetName,
    "Local-file dataset audit dataset name mismatch.",
  );
  assert(
    audit.organization_id === organizationId,
    "Local-file dataset audit organization mismatch.",
  );
  if (workspaceId) {
    assert(
      audit.workspace_id === workspaceId,
      "Local-file dataset audit workspace mismatch.",
    );
  }
  assert(
    audit.dataset_source_local === true,
    "Local-file dataset audit did not preserve dataset_source_local.",
  );
  assert(
    audit.original_filename === fileName,
    "Local-file dataset audit original filename mismatch.",
  );
  assert(
    audit.file_url_set === true,
    "Local-file dataset audit did not persist file_url.",
  );
  assert(
    Number(audit.estimated_rows) === 2 && Number(audit.estimated_columns) === 2,
    "Local-file dataset audit estimated row/column counts mismatch.",
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
      cleanup: "hard delete local-file dataset fixtures",
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

async function runBackendShellJson(script) {
  let stdout;
  if (process.env.API_JOURNEY_BACKEND_CONTAINER) {
    const container = process.env.API_JOURNEY_BACKEND_CONTAINER;
    const python = process.env.API_JOURNEY_BACKEND_PYTHON || "python";
    ({ stdout } = await execFileAsync(
      "docker",
      [
        "exec",
        "-w",
        "/app/backend",
        container,
        python,
        "manage.py",
        "shell",
        "-c",
        script,
      ],
      { maxBuffer: 20 * 1024 * 1024 },
    ));
  } else {
    ({ stdout } = await execFileAsync(
      "uv",
      ["run", "python", "manage.py", "shell", "-c", script],
      {
        cwd: process.env.API_JOURNEY_BACKEND_DIR || backendRoot,
        env: {
          ...process.env,
          EE_LICENSE_KEY: process.env.EE_LICENSE_KEY || "test-license-key",
          PGBOUNCER_HOST: process.env.PGBOUNCER_HOST || "127.0.0.1",
          PGBOUNCER_PORT: process.env.PGBOUNCER_PORT || "5436",
          REDIS_URL: process.env.REDIS_URL || "redis://127.0.0.1:6382/0",
          REDIS_CACHE_URL:
            process.env.REDIS_CACHE_URL || "redis://127.0.0.1:6382/0",
        },
        maxBuffer: 20 * 1024 * 1024,
      },
    ));
  }
  const lines = stdout
    .trim()
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const jsonLine = [...lines].reverse().find((line) => line.startsWith("{"));
  assert(jsonLine, "Backend shell returned no JSON output.");
  return JSON.parse(jsonLine);
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

async function waitForPath(page, pathname, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname === expectedPath,
    { timeout },
    pathname,
  );
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

async function clickVisibleButton(page, label, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
  const clicked = await page.evaluate((expectedLabel) => {
    const button = window
      .visibleElements("button")
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedLabel &&
          !candidate.disabled,
      );
    if (!button) return false;
    window.dispatchClick(button);
    return true;
  }, label);
  assert(clicked, `Could not click visible button: ${label}`);
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
    method === "POST" &&
    /\/model-hub\/develops\/create-dataset-from-local-file\/?$/.test(path)
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
