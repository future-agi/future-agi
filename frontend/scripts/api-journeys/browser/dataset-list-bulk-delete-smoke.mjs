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
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const DATASET_PREFIX = "ui_dataset_list_bulk_";
const SCREENSHOT_PATH = "/tmp/dataset-list-bulk-delete-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/dataset-list-bulk-delete-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);
let expectedApiOrigin = new URL(process.env.API_BASE || "http://localhost:8003")
  .origin;

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  expectedApiOrigin = new URL(auth.apiBase).origin;
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const searchText = DATASET_PREFIX + suffix;
  const datasets = [
    {
      key: "low",
      name: `${DATASET_PREFIX}${suffix}_alpha_low`,
      rows: 1,
    },
    {
      key: "middle",
      name: `${DATASET_PREFIX}${suffix}_middle`,
      rows: 2,
    },
    {
      key: "high",
      name: `${DATASET_PREFIX}${suffix}_zulu_high`,
      rows: 3,
    },
  ];
  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const modelHubRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let createdDatasetIds = [];
  let uiDeletedDatasetIds = [];

  await hardDeleteDatasetFixturesByPrefix(DATASET_PREFIX, cleanupEvidence);

  try {
    const created = [];
    for (const spec of datasets) {
      const dataset = await createManualDataset(auth.client, spec);
      if (dataset.skipped) {
        console.log(JSON.stringify(dataset.output, null, 2));
        return;
      }
      created.push({ ...spec, id: dataset.datasetId });
      createdDatasetIds.push(dataset.datasetId);
    }

    await assertApiListReadback(auth.client, created, searchText);

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
      modelHubRequests.push(`${request.method()} ${maskUrl(url)}`);
      if (MUTATION_METHODS.has(request.method())) {
        const mutation = {
          method: request.method(),
          url,
          body: parseJsonBody(request.postData()),
        };
        browserMutations.push(mutation);
        if (!isAllowedMutation(request.method(), url)) {
          unexpectedMutations.push(`${request.method()} ${maskUrl(url)}`);
        }
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isModelHubApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${maskUrl(url)}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponsesDuring(
      page,
      "initial datasets list load",
      [
        (response) =>
          isDatasetListResponse(response) &&
          response.request().method() === "GET" &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/develop`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/develop");

    await setSearchInput(page, searchText);
    await waitForDatasetRows(
      page,
      created.map((dataset) => dataset.name),
    );

    await sortDatasetList(page, "Datapoints", {
      columnId: "number_of_datapoints",
      type: "ascending",
    });
    await waitForDatasetRowOrder(
      page,
      created.map((dataset) => dataset.name),
    );

    await sortDatasetList(page, "Datapoints", {
      columnId: "number_of_datapoints",
      type: "descending",
    });
    await waitForDatasetRowOrder(
      page,
      [...created].reverse().map((dataset) => dataset.name),
    );

    const deleteTargets = [created[2], created[1]];
    const remaining = created[0];
    for (const dataset of deleteTargets) {
      await selectDatasetRowByName(page, dataset.name);
    }
    await waitForVisibleText(page, "2 Selected", { exact: true });
    await clickVisibleText(page, "Delete", { exact: true });
    await waitForVisibleText(page, "Delete Datasets", { exact: true });
    await waitForVisibleText(page, "selected 2 datasets");

    const [, deleteResponse] = await waitForResponsesDuring(
      page,
      "bulk dataset delete",
      [
        (response) =>
          isDeleteDatasetResponse(response) &&
          response.request().method() === "DELETE" &&
          response.status() < 400,
        (response) =>
          isDatasetListResponse(response) &&
          response.request().method() === "GET" &&
          response.status() < 400 &&
          response.url().includes(`search_text=${searchText}`),
      ],
      () => clickDialogButton(page, "Delete"),
    );
    await responseJson(deleteResponse);
    uiDeletedDatasetIds = deleteTargets.map((dataset) => dataset.id);
    createdDatasetIds = createdDatasetIds.filter(
      (datasetId) => !uiDeletedDatasetIds.includes(datasetId),
    );

    const deleteMutation = browserMutations.find(
      (mutation) =>
        mutation.method === "DELETE" &&
        new URL(mutation.url).pathname ===
          "/model-hub/develops/delete_dataset/",
    );
    assert(deleteMutation, "Browser did not issue bulk delete_dataset.");
    assertSameMembers(
      asArray(deleteMutation.body?.dataset_ids),
      uiDeletedDatasetIds,
      "Bulk delete request did not contain the selected dataset ids.",
    );

    await waitForDatasetRows(page, [remaining.name]);
    await waitForNoVisibleExactText(page, deleteTargets[0].name);
    await waitForNoVisibleExactText(page, deleteTargets[1].name);

    const listAfterDelete = await listDatasets(auth.client, {
      search: searchText,
    });
    assert(
      listAfterDelete.length === 1 && listAfterDelete[0]?.id === remaining.id,
      `Post-delete API list did not return only the remaining fixture: ${JSON.stringify(
        listAfterDelete,
      )}`,
    );

    const dbAudit = await loadDatasetListAudit({
      prefix: DATASET_PREFIX + suffix,
      deletedIds: uiDeletedDatasetIds,
      remainingId: remaining.id,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assertDatasetListAudit(dbAudit, {
      expectedTotal: created.length,
      expectedDeleted: uiDeletedDatasetIds.length,
      expectedWorkspaceMatches: auth.workspaceId ? created.length : null,
    });

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      unexpectedMutations.length === 0,
      `Unexpected Model Hub mutations: ${unexpectedMutations.join(", ")}`,
    );
    assert(
      apiFailures.length === 0,
      `Unexpected Model Hub API failures: ${apiFailures.join(", ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    await deleteDatasets(auth.client, createdDatasetIds, cleanupEvidence);
    createdDatasetIds = [];
    const hardCleanup = await hardDeleteDatasetFixturesByPrefix(
      DATASET_PREFIX,
      cleanupEvidence,
    );
    assert(
      hardCleanup.remaining_dataset_count === 0,
      `Hard cleanup left dataset list fixtures: ${JSON.stringify(hardCleanup)}`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          datasets: created.map(({ id, name, rows }) => ({ id, name, rows })),
          deleted_dataset_ids: uiDeletedDatasetIds,
          remaining_dataset_id: remaining.id,
          db_audit: dbAudit,
          browser_mutations: browserMutations.map(sanitizeMutation),
          model_hub_request_count: modelHubRequests.length,
          screenshot: SCREENSHOT_PATH,
          cleanup: cleanupEvidence,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    if (page) {
      await page.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true });
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
          model_hub_request_count: modelHubRequests.length,
        },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    if (createdDatasetIds.length) {
      await deleteDatasets(
        auth.client,
        createdDatasetIds,
        cleanupEvidence,
      ).catch((error) => {
        cleanupEvidence.push({
          cleanup: "public delete dataset list fixtures after failure",
          status: "failed",
          error: error.message,
        });
      });
    }
    await hardDeleteDatasetFixturesByPrefix(
      DATASET_PREFIX,
      cleanupEvidence,
    ).catch((error) => {
      cleanupEvidence.push({
        cleanup: "hard delete dataset list fixtures after failure",
        status: "failed",
        error: error.message,
      });
    });
    if (browser) await browser.close();
  }
}

async function createManualDataset(client, { name, rows }) {
  try {
    const created = await client.post(
      apiPath("/model-hub/develops/create-dataset-manually/"),
      {
        dataset_name: name,
        number_of_rows: rows,
        number_of_columns: 1,
      },
    );
    const datasetId = created?.dataset_id;
    assert(datasetId, "Manual dataset create did not return dataset_id.");
    const table = await getDatasetTable(client, datasetId);
    const tableRows = asArray(table.table).filter((row) => row?.row_id);
    assert(
      tableRows.length === rows,
      `Manual dataset ${name} created ${tableRows.length} rows instead of ${rows}.`,
    );
    return { datasetId };
  } catch (error) {
    if (error?.status === 429) {
      return {
        skipped: true,
        output: {
          status: "skipped",
          reason: "manual dataset creation is plan-limited",
          response_status: error.status,
        },
      };
    }
    throw error;
  }
}

async function assertApiListReadback(client, created, searchText) {
  const byNameAsc = await listDatasets(client, {
    search: searchText,
    sort: [{ column_id: "name", type: "ascending" }],
  });
  for (const dataset of created) {
    assert(
      byNameAsc.some(
        (candidate) =>
          candidate?.id === dataset.id && candidate?.name === dataset.name,
      ),
      `Dataset ${dataset.name} was missing from API search readback.`,
    );
  }

  const byDatapointsDesc = await listDatasets(client, {
    search: searchText,
    sort: [{ column_id: "number_of_datapoints", type: "descending" }],
  });
  const fixtureRows = byDatapointsDesc.filter((dataset) =>
    created.some((fixture) => fixture.id === dataset.id),
  );
  assert(
    fixtureRows.length === created.length,
    "API sorted readback did not return all fixture datasets.",
  );
  const counts = fixtureRows.map((dataset) => dataset.number_of_datapoints);
  assert(
    counts.join(",") === "3,2,1",
    `API datapoint sort returned unexpected counts: ${counts.join(",")}`,
  );
}

async function listDatasets(client, { search, sort = null } = {}) {
  const query = {
    page: 0,
    page_size: 100,
  };
  if (search) query.search_text = search;
  if (sort) query.sort = JSON.stringify(sort);
  return asArray(
    await client.get(apiPath("/model-hub/develops/get-datasets/"), { query }),
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

async function deleteDatasets(client, datasetIds, evidence) {
  const ids = datasetIds.filter(Boolean);
  if (!ids.length) return;
  await client.delete(apiPath("/model-hub/develops/delete_dataset/"), {
    body: { dataset_ids: ids },
    okStatuses: [200, 404],
  });
  evidence.push({
    cleanup: "public delete dataset list fixtures",
    status: "passed",
    dataset_ids: ids,
  });
}

async function loadDatasetListAudit({
  prefix,
  deletedIds,
  remainingId,
  organizationId,
  workspaceId,
}) {
  const workspaceCheck = workspaceId
    ? `'workspace_id_match_count', count(*) FILTER (WHERE workspace_id = ${sqlUuid(
        workspaceId,
      )})`
    : `'workspace_id_match_count', 0`;
  const sql = `
WITH fixture_datasets AS (
  SELECT id, name, deleted, deleted_at, organization_id, workspace_id
  FROM model_hub_dataset
  WHERE name LIKE ${sqlTextLiteral(`${prefix}%`)}
)
SELECT json_build_object(
  'dataset_count', count(*),
  'organization_id_match_count', count(*) FILTER (
    WHERE organization_id = ${sqlUuid(organizationId)}
  ),
  ${workspaceCheck},
  'deleted_count', count(*) FILTER (WHERE deleted = true),
  'deleted_at_count', count(*) FILTER (WHERE deleted_at IS NOT NULL),
  'selected_deleted_count', count(*) FILTER (
    WHERE id IN (${sqlUuidList(deletedIds)}) AND deleted = true
  ),
  'remaining_active_count', count(*) FILTER (
    WHERE id = ${sqlUuid(remainingId)} AND deleted = false
  ),
  'active_count', count(*) FILTER (WHERE deleted = false)
)
FROM fixture_datasets;
`;
  return runPostgresJson(sql);
}

function assertDatasetListAudit(
  audit,
  { expectedTotal, expectedDeleted, expectedWorkspaceMatches },
) {
  assert(
    Number(audit.dataset_count) === expectedTotal,
    `DB audit expected ${expectedTotal} fixtures, got ${audit.dataset_count}.`,
  );
  assert(
    Number(audit.organization_id_match_count) === expectedTotal,
    "DB audit found a fixture outside the active organization.",
  );
  if (expectedWorkspaceMatches !== null) {
    assert(
      Number(audit.workspace_id_match_count) === expectedWorkspaceMatches,
      "DB audit found a fixture outside the active workspace.",
    );
  }
  assert(
    Number(audit.selected_deleted_count) === expectedDeleted &&
      Number(audit.deleted_count) === expectedDeleted &&
      Number(audit.deleted_at_count) === expectedDeleted,
    `DB audit did not confirm selected bulk delete: ${JSON.stringify(audit)}`,
  );
  assert(
    Number(audit.remaining_active_count) === 1 &&
      Number(audit.active_count) === 1,
    `DB audit did not keep exactly one active fixture: ${JSON.stringify(
      audit,
    )}`,
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
      cleanup: "hard delete dataset list fixtures",
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
    window.setNativeValue = (element, value) => {
      const prototype =
        element instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
      descriptor.set.call(element, value);
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
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

async function waitForResponsesDuring(page, label, predicates, action) {
  try {
    const results = await Promise.all([
      action(),
      ...predicates.map((predicate) =>
        page.waitForResponse(predicate, { timeout: 60000 }),
      ),
    ]);
    return results;
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`, { cause: error });
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

async function waitForNoVisibleExactText(page, text, timeout = 30000) {
  await page.waitForFunction(
    (expectedText) =>
      !window
        .visibleElements()
        .some(
          (element) =>
            window.normalizeText(element.textContent) === expectedText,
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
        element?.closest("button,a,[role='button'],[role='menuitem'],tr") ||
        element;
      if (!clickable || clickable.disabled) return false;
      window.dispatchClick(clickable);
      return true;
    },
    { text, exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

async function clickDialogButton(page, label, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
  const clicked = await page.evaluate((expectedLabel) => {
    const dialogs = window.visibleElements('[role="dialog"], .MuiDialog-root');
    const buttons = dialogs.flatMap((dialog) =>
      Array.from(dialog.querySelectorAll("button")),
    );
    const button = buttons.find(
      (candidate) =>
        window.normalizeText(candidate.textContent) === expectedLabel &&
        !candidate.disabled,
    );
    if (!button) return false;
    window.dispatchClick(button);
    return true;
  }, label);
  assert(clicked, `Could not click dialog button: ${label}`);
}

async function setSearchInput(page, value) {
  await waitForResponsesDuring(
    page,
    `dataset list search ${value}`,
    [
      (response) => {
        if (!isDatasetListResponse(response) || response.status() >= 400) {
          return false;
        }
        const url = new URL(response.url());
        return url.searchParams.get("search_text") === value;
      },
    ],
    () => setInputByPlaceholder(page, "Search", value),
  );
}

async function setInputByPlaceholder(
  page,
  placeholder,
  value,
  timeout = 30000,
) {
  await page.waitForFunction(
    (expectedPlaceholder) =>
      window
        .visibleElements("input,textarea")
        .some((element) => element.placeholder === expectedPlaceholder),
    { timeout },
    placeholder,
  );
  const updated = await page.evaluate(
    ({ placeholder: expectedPlaceholder, value: nextValue }) => {
      const input = window
        .visibleElements("input,textarea")
        .find((element) => element.placeholder === expectedPlaceholder);
      if (!input || input.disabled) return false;
      input.focus();
      window.setNativeValue(input, nextValue);
      input.blur();
      return true;
    },
    { placeholder, value },
  );
  assert(updated, `Could not set input placeholder: ${placeholder}`);
}

async function sortDatasetList(page, header, { columnId, type }) {
  await waitForResponsesDuring(
    page,
    `dataset list sort ${columnId} ${type}`,
    [
      (response) => {
        if (!isDatasetListResponse(response) || response.status() >= 400) {
          return false;
        }
        const rawSort = new URL(response.url()).searchParams.get("sort");
        if (!rawSort) return false;
        try {
          const parsed = JSON.parse(rawSort);
          return parsed.some(
            (item) => item?.column_id === columnId && item?.type === type,
          );
        } catch {
          return false;
        }
      },
    ],
    () => clickColumnHeader(page, header),
  );
}

async function clickColumnHeader(page, header, timeout = 30000) {
  await waitForVisibleText(page, header, { exact: true, timeout });
  const clicked = await page.evaluate((expectedHeader) => {
    const headerElement = window
      .visibleElements('[role="columnheader"]')
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedHeader,
      );
    if (!headerElement) return false;
    window.dispatchClick(headerElement);
    return true;
  }, header);
  assert(clicked, `Could not click column header: ${header}`);
}

async function waitForDatasetRows(page, names, timeout = 30000) {
  await page.waitForFunction(
    (expectedNames) => {
      const visibleNames = window
        .visibleElements(".MuiDataGrid-row")
        .map((row) => window.normalizeText(row.textContent));
      return expectedNames.every((name) =>
        visibleNames.some((rowText) => rowText.includes(name)),
      );
    },
    { timeout },
    names,
  );
}

async function waitForDatasetRowOrder(page, names, timeout = 30000) {
  await page.waitForFunction(
    (expectedNames) => {
      const rowTexts = window
        .visibleElements(".MuiDataGrid-row")
        .map((row) => window.normalizeText(row.textContent));
      const indexes = expectedNames.map((name) =>
        rowTexts.findIndex((rowText) => rowText.includes(name)),
      );
      if (indexes.some((index) => index < 0)) return false;
      return indexes.every((index, i) => i === 0 || indexes[i - 1] < index);
    },
    { timeout },
    names,
  );
}

async function selectDatasetRowByName(page, datasetName) {
  await waitForVisibleText(page, datasetName, { exact: true });
  const checkboxBox = await page.evaluate((expectedName) => {
    const rows = window.visibleElements(".MuiDataGrid-row");
    const row = rows.find((candidate) =>
      window.normalizeText(candidate.textContent).includes(expectedName),
    );
    const checkbox = row?.querySelector('input[type="checkbox"]');
    if (!checkbox || checkbox.disabled) return null;
    const rect = checkbox.getBoundingClientRect();
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
  }, datasetName);
  assert(checkboxBox, `Could not find dataset row checkbox: ${datasetName}`);
  await page.mouse.click(checkboxBox.x, checkboxBox.y);
  await page.waitForFunction(
    (expectedName) => {
      const rows = window.visibleElements(".MuiDataGrid-row");
      const row = rows.find((candidate) =>
        window.normalizeText(candidate.textContent).includes(expectedName),
      );
      return Boolean(row?.querySelector('input[type="checkbox"]')?.checked);
    },
    { timeout: 30000 },
    datasetName,
  );
}

async function responseJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function isDatasetListResponse(response) {
  const url = new URL(response.url());
  return (
    isModelHubApiUrl(response.url()) &&
    url.pathname === "/model-hub/develops/get-datasets/"
  );
}

function isDeleteDatasetResponse(response) {
  const url = new URL(response.url());
  return (
    isModelHubApiUrl(response.url()) &&
    url.pathname === "/model-hub/develops/delete_dataset/"
  );
}

function isModelHubApiUrl(rawUrl) {
  const url = new URL(rawUrl);
  return (
    url.origin === expectedApiOrigin &&
    (url.pathname.startsWith("/model-hub/develops/") ||
      url.pathname.startsWith("/model-hub/datasets/") ||
      url.pathname.startsWith("/model-hub/dataset/"))
  );
}

function isAllowedMutation(method, rawUrl) {
  const path = new URL(rawUrl).pathname;
  return method === "DELETE" && path === "/model-hub/develops/delete_dataset/";
}

function parseJsonBody(rawBody) {
  if (!rawBody) return null;
  try {
    return JSON.parse(rawBody);
  } catch {
    return rawBody;
  }
}

function assertSameMembers(actual, expected, message) {
  const actualSorted = actual.map(String).sort();
  const expectedSorted = expected.map(String).sort();
  assert(
    actualSorted.length === expectedSorted.length &&
      actualSorted.every((value, index) => value === expectedSorted[index]),
    message,
  );
}

function sanitizeMutation(mutation) {
  return {
    method: mutation.method,
    path: maskUrl(mutation.url),
    body: mutation.body,
  };
}

function maskUrl(rawUrl) {
  const url = new URL(rawUrl);
  return `${url.pathname}${url.search ? "?<query>" : ""}`;
}

function sqlUuid(value) {
  return `${sqlTextLiteral(value)}::uuid`;
}

function sqlUuidList(values) {
  if (!values.length) return "NULL::uuid";
  return values.map(sqlUuid).join(", ");
}

function sqlTextLiteral(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
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
