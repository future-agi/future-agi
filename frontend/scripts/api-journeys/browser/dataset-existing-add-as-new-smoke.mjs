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
const DATASET_PREFIX = "ui_existing_dataset_";
const SCREENSHOT_PATH = "/tmp/dataset-existing-add-as-new-smoke.png";
const PLAN_LIMIT_SCREENSHOT_PATH =
  "/tmp/dataset-existing-add-as-new-plan-limited-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/dataset-existing-add-as-new-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const sourceName = `${DATASET_PREFIX}source_${suffix}`;
  const copyName = `${DATASET_PREFIX}copy_${suffix}`;
  const firstValue = `existing source first ${suffix}`;
  const secondValue = `existing source second ${suffix}`;
  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const modelHubRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let sourceDatasetId = null;
  let copiedDatasetId = null;

  await cleanupDatasetsByPrefix(auth.client, DATASET_PREFIX, cleanupEvidence);

  try {
    const source = await createSourceDataset(auth.client, {
      sourceName,
      firstValue,
      secondValue,
    });
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

    await clickVisibleText(page, "Add Dataset", { exact: true });
    await waitForVisibleText(page, "Add dataset", { exact: true });
    await clickVisibleText(
      page,
      "Add from existing model dataset or experiment",
      {
        exact: true,
      },
    );
    await waitForVisibleText(
      page,
      "Add from existing model dataset or experiment",
      {
        exact: true,
      },
    );

    await setInputByLabel(page, "Dataset Name", copyName);
    await selectSearchOptionByLabel(
      page,
      "Choose Datasets or experiments",
      sourceName,
    );
    await waitForVisibleInputValue(page, "Column 1");
    await waitForVisibleInputValue(page, "Column 2");

    const copyResponse = await waitForResponseDuring(
      page,
      "add as new dataset",
      (response) =>
        response.url().includes("/model-hub/develops/add-as-new/") &&
        response.request().method() === "POST",
      () => clickVisibleButton(page, "Add"),
    );
    const copyPayload = await responseJson(copyResponse);

    if (copyResponse.status() === 429) {
      await page.screenshot({
        path: PLAN_LIMIT_SCREENSHOT_PATH,
        fullPage: true,
      });
      console.log(
        JSON.stringify(
          {
            status: "skipped",
            reason: "dataset add-as-new is plan-limited",
            app_base: APP_BASE,
            api_base: auth.apiBase,
            organization_id: auth.organizationId,
            workspace_id: auth.workspaceId,
            response_status: copyResponse.status(),
            response_message:
              copyPayload?.message ||
              copyPayload?.detail ||
              copyPayload?.error ||
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
      copyResponse.status() >= 200 && copyResponse.status() < 300,
      `Add-as-new returned HTTP ${copyResponse.status()}: ${JSON.stringify(
        copyPayload,
      )}`,
    );

    copiedDatasetId =
      copyPayload?.result?.dataset_id || copyPayload?.result?.datasetId || null;
    assert(copiedDatasetId, "Add-as-new response omitted dataset_id.");
    await waitForPathIncludes(page, `/dashboard/develop/${copiedDatasetId}`);
    await waitForAnyVisibleText(page, ["Column 1", "Column 2"], 60000);

    const copiedTable = await getDatasetTable(auth.client, copiedDatasetId);
    const copiedRows = asArray(copiedTable.table).filter((row) => row?.row_id);
    const copiedColumns = asArray(copiedTable.column_config);
    const copiedColumnOne = copiedColumns.find(
      (column) => column?.name === "Column 1",
    );
    const copiedColumnTwo = copiedColumns.find(
      (column) => column?.name === "Column 2",
    );
    assert(copiedRows.length === 2, "Copied dataset did not have 2 rows.");
    assert(copiedColumnOne?.id, "Copied dataset was missing Column 1.");
    assert(copiedColumnTwo?.id, "Copied dataset was missing Column 2.");
    assert(
      copiedColumnOne.id !== source.columnOne.id &&
        copiedColumnTwo.id !== source.columnTwo.id,
      "Copied dataset reused source column ids.",
    );
    assert(
      copiedRows.every(
        (row) =>
          row.row_id !== source.rows[0].row_id &&
          row.row_id !== source.rows[1].row_id,
      ),
      "Copied dataset reused source row ids.",
    );
    assert(
      copiedRows.some(
        (row) => cellValueFor(row, copiedColumnOne.id) === firstValue,
      ),
      "Copied dataset did not preserve first selected-column value.",
    );
    assert(
      copiedRows.some(
        (row) => cellValueFor(row, copiedColumnTwo.id) === secondValue,
      ),
      "Copied dataset did not preserve second selected-column value.",
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

    const finalCopiedDatasetId = copiedDatasetId;
    await deleteDatasets(
      auth.client,
      [copiedDatasetId, sourceDatasetId],
      cleanupEvidence,
    );
    copiedDatasetId = null;
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
          copied_dataset_id: finalCopiedDatasetId,
          source_dataset_name: sourceName,
          copied_dataset_name: copyName,
          rows_copied: copiedRows.length,
          columns_copied: copiedColumns.length,
          copied_values: [firstValue, secondValue],
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
    const remainingIds = [copiedDatasetId, sourceDatasetId].filter(Boolean);
    if (remainingIds.length) {
      await deleteDatasets(auth.client, remainingIds, cleanupEvidence).catch(
        (error) => {
          cleanupEvidence.push({
            cleanup: "delete add-as-new datasets after failure",
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
      console.log(
        JSON.stringify(
          {
            status: "skipped",
            reason: "source manual dataset creation is plan-limited",
            response_status: error.status,
          },
          null,
          2,
        ),
      );
      process.exit(0);
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
    "Source first value did not round-trip before add-as-new.",
  );
  assert(
    updatedRows.some((row) => cellValueFor(row, columnTwo.id) === secondValue),
    "Source second value did not round-trip before add-as-new.",
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
    cleanup: "delete add-as-new datasets",
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

async function waitForAnyVisibleText(page, texts, timeout = 30000) {
  await page.waitForFunction(
    (expectedTexts) =>
      window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return expectedTexts.some((text) => textContent.includes(text));
      }),
    { timeout },
    texts,
  );
}

async function waitForVisibleInputValue(page, value, timeout = 30000) {
  await page.waitForFunction(
    (expectedValue) =>
      window
        .visibleElements("input,textarea")
        .some((element) => element.value === expectedValue),
    { timeout },
    value,
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

async function selectSearchOptionByLabel(page, label, optionText) {
  await waitForVisibleText(page, label, { timeout: 30000 });
  const focused = await page.evaluate((expectedLabel) => {
    const labels = Array.from(document.querySelectorAll("label"));
    const labelElement = labels.find((candidate) =>
      window.normalizeText(candidate.textContent).includes(expectedLabel),
    );
    const formControl =
      labelElement?.closest(".MuiFormControl-root") ||
      labelElement?.parentElement;
    const input = formControl?.querySelector("input");
    if (!input || input.disabled) return false;
    input.focus();
    input.click();
    return true;
  }, label);
  assert(focused, `Could not focus select input: ${label}`);
  await page.keyboard.down(modifierKey());
  await page.keyboard.press("A");
  await page.keyboard.up(modifierKey());
  await page.keyboard.type(optionText);
  await clickMenuItem(page, optionText);
}

async function clickMenuItem(page, text) {
  await page.waitForFunction(
    (expectedText) =>
      window
        .visibleElements('[role="menuitem"],li.MuiMenuItem-root')
        .some(
          (element) =>
            window.normalizeText(element.textContent) === expectedText,
        ),
    { timeout: 30000 },
    text,
  );
  const clicked = await page.evaluate((expectedText) => {
    const item = window
      .visibleElements('[role="menuitem"],li.MuiMenuItem-root')
      .find(
        (element) => window.normalizeText(element.textContent) === expectedText,
      );
    if (!item || item.getAttribute("aria-disabled") === "true") return false;
    window.dispatchClick(item);
    return true;
  }, text);
  assert(clicked, `Could not click menu item: ${text}`);
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
    (method === "POST" && /\/model-hub\/develops\/add-as-new\/?$/.test(path)) ||
    (method === "POST" &&
      path === `/model-hub/develops/${sourceDatasetId}/update_cell_value/`) ||
    (method === "DELETE" &&
      /\/model-hub\/develops\/delete_dataset\/?$/.test(path))
  );
}

function maskRequest(rawRequest) {
  const [method, rawUrl] = rawRequest.split(" ");
  const url = new URL(rawUrl);
  return `${method} ${url.pathname}`;
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
