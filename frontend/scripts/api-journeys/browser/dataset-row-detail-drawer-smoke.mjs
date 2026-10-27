/* eslint-disable no-console */
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const DATASET_PREFIX = "ui_row_detail_drawer_";
const SCREENSHOT_PATH = "/tmp/dataset-row-detail-drawer-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/dataset-row-detail-drawer-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const datasetName = `${DATASET_PREFIX}${suffix}`;
  const currentValue = `drawer row 30 ${suffix}`;
  const currentAuxValue = `drawer row 30 aux ${suffix}`;
  const nextValue = `drawer row 31 ${suffix}`;
  const nextAuxValue = `drawer row 31 aux ${suffix}`;
  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const modelHubRequests = [];
  const browserRequests = [];
  const browserPostReads = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let datasetId = null;

  await cleanupDatasetsByPrefix(auth.client, DATASET_PREFIX, cleanupEvidence);

  try {
    const source = await createSourceDataset(auth.client, {
      datasetName,
      currentValue,
      currentAuxValue,
      nextValue,
      nextAuxValue,
    });
    if (source.skipped) {
      console.log(JSON.stringify(source.output, null, 2));
      return;
    }
    datasetId = source.datasetId;
    const { currentRow, nextRow, columnOne, columnTwo } = source;

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
      const requestKey = `${request.method()} ${url}`;
      modelHubRequests.push(requestKey);
      if (MUTATION_METHODS.has(request.method())) {
        browserRequests.push(requestKey);
        if (isAllowedReadPost(request.method(), url, datasetId)) {
          browserPostReads.push(requestKey);
        } else {
          unexpectedMutations.push(requestKey);
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
    await waitForVisibleText(page, "Column 1", { exact: true });
    await clickGridCellContainingText(page, currentValue);

    await waitForVisibleText(page, "Datapoint-30", { exact: true });
    await waitForVisibleText(page, currentValue, { exact: true });
    await waitForVisibleText(page, currentAuxValue, { exact: true });

    const [rowDataResponse, cellDataResponse] = await waitForResponsesDuring(
      page,
      "dataset drawer next-row navigation",
      [
        (response) =>
          response
            .url()
            .includes(`/model-hub/develops/${datasetId}/get-row-data/`) &&
          response.request().method() === "POST" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/model-hub/develops/get-cell-data/") &&
          response.request().method() === "POST" &&
          response.status() < 400,
      ],
      () => page.keyboard.press("j"),
    );
    const rowDataPayload = await responseJson(rowDataResponse);
    const cellDataPayload = await responseJson(cellDataResponse);

    await waitForVisibleText(page, "Datapoint-31", { exact: true });
    await waitForVisibleText(page, nextValue, { exact: true });
    await waitForVisibleText(page, nextAuxValue, { exact: true });

    const nextIds =
      rowDataPayload?.result?.next?.row_id ??
      rowDataPayload?.result?.next?.rowId ??
      [];
    assert(
      asArray(nextIds).includes(nextRow.row_id),
      `get-row-data did not return row 31 as a next id: ${JSON.stringify(
        rowDataPayload,
      )}`,
    );
    const nextCellData = cellDataPayload?.result?.[nextRow.row_id];
    assert(
      nextCellData?.[columnOne.id]?.cell_value === nextValue ||
        nextCellData?.[columnOne.id]?.cellValue === nextValue,
      "get-cell-data response did not include row 31 Column 1 value.",
    );
    assert(
      nextCellData?.[columnTwo.id]?.cell_value === nextAuxValue ||
        nextCellData?.[columnTwo.id]?.cellValue === nextAuxValue,
      "get-cell-data response did not include row 31 Column 2 value.",
    );

    const directRowData = await auth.client.post(
      apiPath("/model-hub/develops/{dataset_id}/get-row-data/", {
        dataset_id: datasetId,
      }),
      { row_id: currentRow.row_id },
    );
    const directCellData = await auth.client.post(
      apiPath("/model-hub/develops/get-cell-data/"),
      {
        row_ids: [nextRow.row_id],
        column_ids: [columnOne.id, columnTwo.id],
      },
    );
    assert(
      asArray(
        directRowData?.next?.row_id ?? directRowData?.next?.rowId,
      ).includes(nextRow.row_id),
      "Direct get-row-data readback did not include row 31.",
    );
    assert(
      directCellData?.[nextRow.row_id]?.[columnOne.id]?.cell_value ===
        nextValue ||
        directCellData?.[nextRow.row_id]?.[columnOne.id]?.cellValue ===
          nextValue,
      "Direct get-cell-data readback lost row 31 Column 1 value.",
    );

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      unexpectedMutations.length === 0,
      `Unexpected Model Hub mutations: ${unexpectedMutations
        .map(maskRequest)
        .join(", ")}`,
    );
    assert(
      !apiFailures.some((failure) => !failure.startsWith("429 ")),
      `Unexpected Model Hub API failures: ${apiFailures.join(", ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    const finalDatasetId = datasetId;
    await deleteDatasets(auth.client, [datasetId], cleanupEvidence);
    datasetId = null;

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          dataset_id: finalDatasetId,
          dataset_name: datasetName,
          current_row_id: currentRow.row_id,
          next_row_id: nextRow.row_id,
          columns: [columnOne.id, columnTwo.id],
          browser_post_reads: browserPostReads.map(maskRequest),
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
    throw error;
  } finally {
    if (datasetId) {
      await deleteDatasets(auth.client, [datasetId], cleanupEvidence).catch(
        (error) => {
          cleanupEvidence.push({
            cleanup: "delete row-detail drawer dataset after failure",
            status: "failed",
            error: error.message,
          });
        },
      );
    }
    await cleanupDatasetsByPrefix(auth.client, DATASET_PREFIX, cleanupEvidence);
    if (browser) await browser.close();
  }
}

async function createSourceDataset(
  client,
  { datasetName, currentValue, currentAuxValue, nextValue, nextAuxValue },
) {
  let created;
  try {
    created = await client.post(
      apiPath("/model-hub/develops/create-dataset-manually/"),
      {
        dataset_name: datasetName,
        number_of_rows: 31,
        number_of_columns: 2,
      },
    );
  } catch (error) {
    if (error?.status === 429) {
      return {
        skipped: true,
        output: {
          status: "skipped",
          reason: "source manual dataset creation is plan-limited",
          response_status: error.status,
        },
      };
    }
    throw error;
  }
  const datasetId = created?.dataset_id;
  assert(datasetId, "Manual dataset create did not return dataset_id.");

  const table = await getDatasetTable(client, datasetId);
  const columns = asArray(table.column_config);
  const rows = asArray(table.table).filter((row) => row?.row_id);
  const columnOne = columns.find((column) => column?.name === "Column 1");
  const columnTwo = columns.find((column) => column?.name === "Column 2");
  assert(rows.length === 31, "Row-detail drawer dataset did not have 31 rows.");
  assert(columnOne?.id, "Row-detail drawer dataset was missing Column 1.");
  assert(columnTwo?.id, "Row-detail drawer dataset was missing Column 2.");

  const currentRow = rows[29];
  const nextRow = rows[30];
  await updateCell(
    client,
    datasetId,
    currentRow.row_id,
    columnOne.id,
    currentValue,
  );
  await updateCell(
    client,
    datasetId,
    currentRow.row_id,
    columnTwo.id,
    currentAuxValue,
  );
  await updateCell(client, datasetId, nextRow.row_id, columnOne.id, nextValue);
  await updateCell(
    client,
    datasetId,
    nextRow.row_id,
    columnTwo.id,
    nextAuxValue,
  );

  const updatedTable = await getDatasetTable(client, datasetId);
  const updatedRows = asArray(updatedTable.table).filter((row) => row?.row_id);
  const updatedCurrentRow = updatedRows.find(
    (row) => row?.row_id === currentRow.row_id,
  );
  const updatedNextRow = updatedRows.find(
    (row) => row?.row_id === nextRow.row_id,
  );
  assert(
    cellValueFor(updatedCurrentRow, columnOne.id) === currentValue,
    "Current row Column 1 did not round-trip before drawer open.",
  );
  assert(
    cellValueFor(updatedNextRow, columnOne.id) === nextValue,
    "Next row Column 1 did not round-trip before drawer navigation.",
  );

  return {
    datasetId,
    currentRow,
    nextRow,
    columnOne,
    columnTwo,
  };
}

async function updateCell(client, datasetId, rowId, columnId, value) {
  await client.post(
    apiPath("/model-hub/develops/{dataset_id}/update_cell_value/", {
      dataset_id: datasetId,
    }),
    {
      row_id: rowId,
      column_id: columnId,
      new_value: value,
    },
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
  if (direct && typeof direct === "object" && "cellValue" in direct) {
    return direct.cellValue;
  }
  return direct;
}

async function cleanupDatasetsByPrefix(client, prefix, evidence) {
  const listPayload = await client.get(
    apiPath("/model-hub/develops/get-datasets/"),
    {
      query: { page: 0, page_size: 100 },
    },
  );
  const ids = asArray(listPayload)
    .filter((dataset) => String(dataset?.name || "").startsWith(prefix))
    .map((dataset) => dataset.id)
    .filter(Boolean);
  if (ids.length) {
    await deleteDatasets(client, ids, evidence);
  }
}

async function deleteDatasets(client, datasetIds, evidence) {
  if (!datasetIds.length) return;
  await client.delete(apiPath("/model-hub/develops/delete_dataset/"), {
    body: { dataset_ids: datasetIds },
    okStatuses: [200, 404],
  });
  evidence.push({
    cleanup: "delete row-detail drawer dataset",
    status: "passed",
    dataset_ids: datasetIds,
  });
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
      if (organizationId) {
        sessionStorage.setItem("organizationId", organizationId);
      }
      if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
      if (user?.id) {
        sessionStorage.setItem("futureagi-current-user-id", user.id);
      }
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

async function clickGridCellContainingText(page, text) {
  await page.waitForSelector(".ag-body-viewport", { timeout: 30000 });
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const clicked = await page.evaluate((targetText) => {
      const cells = window.visibleElements(
        ".ag-center-cols-container .ag-cell, .ag-pinned-left-cols-container .ag-cell",
      );
      const cell = cells.find((element) =>
        window.normalizeText(element.textContent).includes(targetText),
      );
      if (cell) {
        cell.scrollIntoView({ block: "center", inline: "center" });
        window.dispatchClick(cell);
        return true;
      }
      const viewport =
        document.querySelector(".ag-body-viewport") ||
        document.querySelector(".ag-center-cols-viewport");
      if (viewport) {
        viewport.scrollTop += 220;
        viewport.dispatchEvent(new Event("scroll", { bubbles: true }));
      }
      return false;
    }, text);
    if (clicked) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Could not find visible grid cell containing ${text}`);
}

async function responseJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function isModelHubApiUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.pathname.startsWith("/model-hub/");
  } catch {
    return false;
  }
}

function isAllowedReadPost(method, url, datasetId) {
  if (method !== "POST") return false;
  const pathname = new URL(url).pathname;
  return (
    pathname === `/model-hub/develops/${datasetId}/get-row-data/` ||
    pathname === "/model-hub/develops/get-cell-data/"
  );
}

function maskRequest(value) {
  const urlPattern = /(https?:\/\/[^/]+)(\/[^ ]*)/g;
  return value.replace(urlPattern, "$2");
}

function browserExecutablePath() {
  return (
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  );
}

main().catch((error) => {
  if (error?.name === "SkipJourney") {
    console.log(JSON.stringify({ status: "skipped", reason: error.reason }));
    process.exit(0);
  }
  console.error(error);
  process.exit(1);
});
