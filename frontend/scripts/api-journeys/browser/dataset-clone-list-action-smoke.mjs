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
const DATASET_PREFIX = "ui_clone_dataset_";
const SCREENSHOT_PATH = "/tmp/dataset-clone-list-action-smoke.png";
const PLAN_LIMIT_SCREENSHOT_PATH =
  "/tmp/dataset-clone-list-action-plan-limited-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/dataset-clone-list-action-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const sourceName = `${DATASET_PREFIX}source_${suffix}`;
  const cloneName = `${DATASET_PREFIX}clone_${suffix}`;
  const firstValue = `clone source first ${suffix}`;
  const secondValue = `clone source second ${suffix}`;
  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const modelHubRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let sourceDatasetId = null;
  let clonedDatasetId = null;

  await cleanupDatasetsByPrefix(auth.client, DATASET_PREFIX, cleanupEvidence);

  try {
    const source = await createSourceDataset(auth.client, {
      sourceName,
      firstValue,
      secondValue,
    });
    if (source.skipped) {
      console.log(JSON.stringify(source.output, null, 2));
      return;
    }
    sourceDatasetId = source.datasetId;

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
        if (!isAllowedMutation(request.method(), url, sourceDatasetId)) {
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

    await setSearchInput(page, sourceName);
    await waitForVisibleText(page, sourceName, { exact: true });
    await selectDatasetRowByName(page, sourceName);
    await waitForVisibleText(page, "1 Selected", { exact: true });
    await clickVisibleText(page, "Duplicate", { exact: true });
    await waitForVisibleText(page, "Duplicate Dataset", { exact: true });
    await setInputByLabel(page, "Enter Dataset Name", cloneName);

    const cloneResponse = await waitForResponseDuring(
      page,
      "dataset clone",
      (response) =>
        response
          .url()
          .includes(`/model-hub/develops/clone-dataset/${sourceDatasetId}/`) &&
        response.request().method() === "POST",
      () => clickVisibleButton(page, "Create"),
    );
    const clonePayload = await responseJson(cloneResponse);

    if (cloneResponse.status() === 429) {
      await page.screenshot({
        path: PLAN_LIMIT_SCREENSHOT_PATH,
        fullPage: true,
      });
      console.log(
        JSON.stringify(
          {
            status: "skipped",
            reason: "dataset clone is plan-limited",
            app_base: APP_BASE,
            api_base: auth.apiBase,
            organization_id: auth.organizationId,
            workspace_id: auth.workspaceId,
            response_status: cloneResponse.status(),
            response_message:
              clonePayload?.message ||
              clonePayload?.detail ||
              clonePayload?.error ||
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
      cloneResponse.status() >= 200 && cloneResponse.status() < 300,
      `Clone returned HTTP ${cloneResponse.status()}: ${JSON.stringify(
        clonePayload,
      )}`,
    );

    clonedDatasetId =
      clonePayload?.result?.dataset_id ||
      clonePayload?.result?.datasetId ||
      clonePayload?.dataset_id ||
      null;
    assert(clonedDatasetId, "Clone response omitted dataset_id.");

    await waitForNoVisibleExactText(page, "Duplicate Dataset");
    await setSearchInput(page, cloneName);
    await waitForVisibleText(page, cloneName, { exact: true, timeout: 60000 });

    const listedClone = await findDatasetByIdOrName(auth.client, {
      id: clonedDatasetId,
      name: cloneName,
    });
    assert(
      listedClone?.id === clonedDatasetId && listedClone?.name === cloneName,
      "Cloned dataset was not visible in get-datasets readback.",
    );

    const clonedTable = await getDatasetTable(auth.client, clonedDatasetId);
    const clonedRows = asArray(clonedTable.table).filter((row) => row?.row_id);
    const clonedColumns = asArray(clonedTable.column_config);
    const clonedColumnOne = clonedColumns.find(
      (column) => column?.name === "Column 1",
    );
    const clonedColumnTwo = clonedColumns.find(
      (column) => column?.name === "Column 2",
    );
    assert(clonedRows.length === 2, "Cloned dataset did not have 2 rows.");
    assert(clonedColumnOne?.id, "Cloned dataset was missing Column 1.");
    assert(clonedColumnTwo?.id, "Cloned dataset was missing Column 2.");
    assert(
      clonedColumnOne.id !== source.columnOne.id &&
        clonedColumnTwo.id !== source.columnTwo.id,
      "Cloned dataset reused source column ids.",
    );
    assert(
      clonedRows.every(
        (row) =>
          row.row_id !== source.rows[0].row_id &&
          row.row_id !== source.rows[1].row_id,
      ),
      "Cloned dataset reused source row ids.",
    );
    assert(
      clonedRows.some(
        (row) => cellValueFor(row, clonedColumnOne.id) === firstValue,
      ),
      "Cloned dataset did not preserve first source value.",
    );
    assert(
      clonedRows.some(
        (row) => cellValueFor(row, clonedColumnTwo.id) === secondValue,
      ),
      "Cloned dataset did not preserve second source value.",
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

    const finalClonedDatasetId = clonedDatasetId;
    await deleteDatasets(
      auth.client,
      [clonedDatasetId, sourceDatasetId],
      cleanupEvidence,
    );
    clonedDatasetId = null;
    sourceDatasetId = null;

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          source_dataset_id: source.datasetId,
          cloned_dataset_id: finalClonedDatasetId,
          source_dataset_name: sourceName,
          cloned_dataset_name: cloneName,
          rows_cloned: clonedRows.length,
          columns_cloned: clonedColumns.length,
          cloned_values: [firstValue, secondValue],
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
    if (page) {
      await page.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true });
      console.error(`failure_screenshot=${FAILURE_SCREENSHOT_PATH}`);
    }
    throw error;
  } finally {
    const remainingIds = [clonedDatasetId, sourceDatasetId].filter(Boolean);
    if (remainingIds.length) {
      await deleteDatasets(auth.client, remainingIds, cleanupEvidence).catch(
        (error) => {
          cleanupEvidence.push({
            cleanup: "delete clone datasets after failure",
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
  { sourceName, firstValue, secondValue },
) {
  let created;
  try {
    created = await client.post(
      apiPath("/model-hub/develops/create-dataset-manually/"),
      {
        dataset_name: sourceName,
        number_of_rows: 2,
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
  assert(datasetId, "Source manual dataset create did not return dataset_id.");

  let table = await getDatasetTable(client, datasetId);
  const columns = asArray(table.column_config);
  const rows = asArray(table.table).filter((row) => row?.row_id);
  const columnOne = columns.find((column) => column?.name === "Column 1");
  const columnTwo = columns.find((column) => column?.name === "Column 2");
  assert(rows.length === 2, "Source dataset did not have 2 rows.");
  assert(columnOne?.id, "Source dataset was missing Column 1.");
  assert(columnTwo?.id, "Source dataset was missing Column 2.");

  await updateCell(client, datasetId, rows[0].row_id, columnOne.id, firstValue);
  await updateCell(
    client,
    datasetId,
    rows[1].row_id,
    columnTwo.id,
    secondValue,
  );

  table = await getDatasetTable(client, datasetId);
  const updatedRows = asArray(table.table).filter((row) => row?.row_id);
  assert(
    updatedRows.some((row) => cellValueFor(row, columnOne.id) === firstValue),
    "Source first value did not round-trip before clone.",
  );
  assert(
    updatedRows.some((row) => cellValueFor(row, columnTwo.id) === secondValue),
    "Source second value did not round-trip before clone.",
  );

  return { datasetId, rows, columnOne, columnTwo };
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
  return direct;
}

async function findDatasetByIdOrName(client, { id, name }) {
  const listPayload = await client.get(
    apiPath("/model-hub/develops/get-datasets/"),
    {
      query: { page: 0, page_size: 100, search_text: name },
    },
  );
  return asArray(listPayload).find(
    (dataset) => dataset?.id === id || dataset?.name === name,
  );
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
    cleanup: "delete clone datasets",
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

async function setSearchInput(page, value) {
  await waitForResponseDuring(
    page,
    `dataset list search ${value}`,
    (response) =>
      response.url().includes("/model-hub/develops/get-datasets/") &&
      response.request().method() === "GET" &&
      response.status() < 400,
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

async function setInputByLabel(page, label, value, timeout = 30000) {
  await waitForVisibleText(page, label, { timeout });
  const updated = await page.evaluate(
    ({ label: expectedLabel, value: nextValue }) => {
      const labels = Array.from(document.querySelectorAll("label"));
      const labelElement = labels.find((candidate) =>
        window.normalizeText(candidate.textContent).includes(expectedLabel),
      );
      const formControl =
        labelElement?.closest(".MuiFormControl-root") ||
        labelElement?.parentElement;
      const input = formControl?.querySelector("input,textarea");
      if (!input || input.disabled) return false;
      input.focus();
      window.setNativeValue(input, nextValue);
      input.blur();
      return true;
    },
    { label, value },
  );
  assert(updated, `Could not set input: ${label}`);
}

async function selectDatasetRowByName(page, datasetName) {
  await waitForVisibleText(page, datasetName, { exact: true });
  const checkboxBox = await page.evaluate((expectedName) => {
    const rows = window.visibleElements('[role="row"], .MuiDataGrid-row');
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
      const rows = window.visibleElements('[role="row"], .MuiDataGrid-row');
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

function isAllowedMutation(method, rawUrl, sourceDatasetId) {
  const path = new URL(rawUrl).pathname;
  return (
    method === "POST" &&
    path === `/model-hub/develops/clone-dataset/${sourceDatasetId}/`
  );
}

function maskRequest(rawRequest) {
  const [method, rawUrl] = rawRequest.split(" ");
  const url = new URL(rawUrl);
  return `${method} ${url.pathname}`;
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
