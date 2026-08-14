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
const DATASET_PREFIX = "ui_merge_existing_";
const SCREENSHOT_PATH = "/tmp/dataset-merge-existing-detail-smoke.png";
const PLAN_LIMIT_SCREENSHOT_PATH =
  "/tmp/dataset-merge-existing-detail-plan-limited-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/dataset-merge-existing-detail-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const sourceName = `${DATASET_PREFIX}source_${suffix}`;
  const targetName = `${DATASET_PREFIX}target_${suffix}`;
  const selectedValue = `merge selected ${suffix}`;
  const unselectedValue = `merge unselected ${suffix}`;
  const targetValue = `merge target ${suffix}`;
  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const modelHubRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let sourceDatasetId = null;
  let targetDatasetId = null;

  await cleanupDatasetsByPrefix(auth.client, DATASET_PREFIX, cleanupEvidence);

  try {
    const source = await createDataset(auth.client, {
      name: sourceName,
      rows: 2,
      columns: 2,
      values: [
        { rowIndex: 0, columnName: "Column 1", value: selectedValue },
        { rowIndex: 1, columnName: "Column 2", value: unselectedValue },
      ],
      purpose: "source dataset",
    });
    sourceDatasetId = source.datasetId;

    const target = await createDataset(auth.client, {
      name: targetName,
      rows: 1,
      columns: 2,
      values: [{ rowIndex: 0, columnName: "Column 1", value: targetValue }],
      purpose: "target dataset",
    });
    targetDatasetId = target.datasetId;

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
      "dataset detail table load",
      [
        (response) =>
          response
            .url()
            .includes(
              `/model-hub/develops/${sourceDatasetId}/get-dataset-table/`,
            ) &&
          response.request().method() === "GET" &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/develop/${sourceDatasetId}?tab=data`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPathIncludes(page, `/dashboard/develop/${sourceDatasetId}`);
    await waitForVisibleText(page, selectedValue, { exact: true });

    await selectGridRowByText(page, selectedValue);
    await clickVisibleText(page, "Add to dataset", { exact: true });
    await waitForVisibleText(page, "Add to Dataset", { exact: true });
    await clickVisibleText(page, "Add to existing dataset", { exact: true });
    await selectSearchOptionByLabel(page, "Dataset", targetName);

    const mergeResponse = await waitForResponseDuring(
      page,
      "merge selected row into existing dataset",
      (response) =>
        response
          .url()
          .includes(`/model-hub/datasets/${sourceDatasetId}/merge/`) &&
        response.request().method() === "POST",
      () => clickDialogButton(page, "Add to Dataset"),
    );
    const mergePayload = await responseJson(mergeResponse);

    if (mergeResponse.status() === 429) {
      await page.screenshot({
        path: PLAN_LIMIT_SCREENSHOT_PATH,
        fullPage: true,
      });
      console.log(
        JSON.stringify(
          {
            status: "skipped",
            reason: "dataset merge is plan-limited",
            app_base: APP_BASE,
            api_base: auth.apiBase,
            organization_id: auth.organizationId,
            workspace_id: auth.workspaceId,
            response_status: mergeResponse.status(),
            response_message:
              mergePayload?.message ||
              mergePayload?.detail ||
              mergePayload?.error ||
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
      mergeResponse.status() >= 200 && mergeResponse.status() < 300,
      `Merge rows returned HTTP ${mergeResponse.status()}: ${JSON.stringify(
        mergePayload,
      )}`,
    );

    await waitForTableRowCount(auth.client, targetDatasetId, 2);
    await waitForVisibleText(page, selectedValue, { exact: true });

    const sourceTable = await getDatasetTable(auth.client, sourceDatasetId);
    const sourceRows = asArray(sourceTable.table).filter((row) => row?.row_id);
    const targetTable = await getDatasetTable(auth.client, targetDatasetId);
    const targetRows = asArray(targetTable.table).filter((row) => row?.row_id);
    const targetColumns = asArray(targetTable.column_config);
    const targetColumnOne = targetColumns.find(
      (column) => column?.name === "Column 1",
    );
    const targetColumnTwo = targetColumns.find(
      (column) => column?.name === "Column 2",
    );

    assert(sourceRows.length === 2, "Source dataset row count changed.");
    assert(targetRows.length === 2, "Target dataset did not receive one row.");
    assert(targetColumnOne?.id, "Target dataset was missing Column 1.");
    assert(targetColumnTwo?.id, "Target dataset was missing Column 2.");
    assert(
      targetRows.some(
        (row) => cellValueFor(row, targetColumnOne.id) === targetValue,
      ),
      "Target dataset lost its original row value.",
    );
    assert(
      targetRows.some(
        (row) => cellValueFor(row, targetColumnOne.id) === selectedValue,
      ),
      "Merged target dataset did not preserve selected source value.",
    );
    assert(
      !targetRows.some(
        (row) => cellValueFor(row, targetColumnTwo.id) === unselectedValue,
      ),
      "Merged target dataset copied an unselected source row.",
    );
    assert(
      targetRows.every((row) => row.row_id !== source.rows[0].row_id),
      "Merged target dataset reused the source row id.",
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

    const finalSourceDatasetId = sourceDatasetId;
    const finalTargetDatasetId = targetDatasetId;
    await deleteDatasets(
      auth.client,
      [targetDatasetId, sourceDatasetId],
      cleanupEvidence,
    );
    sourceDatasetId = null;
    targetDatasetId = null;

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          source_dataset_id: finalSourceDatasetId,
          target_dataset_id: finalTargetDatasetId,
          source_dataset_name: sourceName,
          target_dataset_name: targetName,
          target_rows_after_merge: targetRows.length,
          target_columns_after_merge: targetColumns.length,
          merged_value: selectedValue,
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
    const remainingIds = [targetDatasetId, sourceDatasetId].filter(Boolean);
    if (remainingIds.length) {
      await deleteDatasets(auth.client, remainingIds, cleanupEvidence).catch(
        (error) => {
          cleanupEvidence.push({
            cleanup: "delete merge datasets after failure",
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

async function createDataset(client, { name, rows, columns, values, purpose }) {
  let created;
  try {
    created = await client.post(
      apiPath("/model-hub/develops/create-dataset-manually/"),
      {
        dataset_name: name,
        number_of_rows: rows,
        number_of_columns: columns,
      },
    );
  } catch (error) {
    if (error?.status === 429) {
      console.log(
        JSON.stringify(
          {
            status: "skipped",
            reason: `${purpose} manual dataset creation is plan-limited`,
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
  assert(datasetId, `${purpose} create did not return dataset_id.`);

  let table = await getDatasetTable(client, datasetId);
  const tableRows = asArray(table.table).filter((row) => row?.row_id);
  const tableColumns = asArray(table.column_config);
  assert(
    tableRows.length === rows,
    `${purpose} did not have ${rows} expected rows.`,
  );

  for (const value of values) {
    const row = tableRows[value.rowIndex];
    const column = tableColumns.find(
      (candidate) => candidate?.name === value.columnName,
    );
    assert(row?.row_id, `${purpose} was missing row ${value.rowIndex}.`);
    assert(column?.id, `${purpose} was missing ${value.columnName}.`);
    await updateCell(client, datasetId, row.row_id, column.id, value.value);
  }

  table = await getDatasetTable(client, datasetId);
  const updatedRows = asArray(table.table).filter((row) => row?.row_id);
  const updatedColumns = asArray(table.column_config);
  for (const value of values) {
    const column = updatedColumns.find(
      (candidate) => candidate?.name === value.columnName,
    );
    assert(
      updatedRows.some((row) => cellValueFor(row, column?.id) === value.value),
      `${purpose} value did not round-trip before merge.`,
    );
  }

  return { datasetId, rows: updatedRows, columns: updatedColumns };
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

async function waitForTableRowCount(client, datasetId, expectedRows) {
  const deadline = Date.now() + 60000;
  let lastCount = 0;
  while (Date.now() < deadline) {
    const table = await getDatasetTable(client, datasetId);
    lastCount = asArray(table.table).filter((row) => row?.row_id).length;
    if (lastCount === expectedRows) return;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(
    `Timed out waiting for ${expectedRows} target rows; last count was ${lastCount}.`,
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
    cleanup: "delete merge datasets",
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

async function clickDialogButton(page, label, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
  const clicked = await page.evaluate((expectedLabel) => {
    const dialogs = window.visibleElements('[role="dialog"],.MuiDialog-root');
    for (const dialog of dialogs) {
      const button = Array.from(dialog.querySelectorAll("button")).find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedLabel &&
          !candidate.disabled,
      );
      if (button) {
        window.dispatchClick(button);
        return true;
      }
    }
    return false;
  }, label);
  assert(clicked, `Could not click dialog button: ${label}`);
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

async function selectGridRowByText(page, rowText) {
  await waitForVisibleText(page, rowText, { exact: true });
  const checkboxBox = await page.evaluate((expectedText) => {
    const rows = window.visibleElements(".ag-row");
    const row = rows.find((candidate) =>
      window.normalizeText(candidate.textContent).includes(expectedText),
    );
    if (!row) return null;
    const rowRect = row.getBoundingClientRect();
    const y = rowRect.top + rowRect.height / 2;
    const pinnedCheckboxes = window
      .visibleElements(
        ".ag-pinned-left-cols-container .ag-cell, .ag-pinned-left-cols-container [role='checkbox'], .ag-pinned-left-cols-container input[type='checkbox']",
      )
      .filter((candidate) => {
        const rect = candidate.getBoundingClientRect();
        return y >= rect.top && y <= rect.bottom;
      });
    const checkbox =
      pinnedCheckboxes.find((candidate) =>
        candidate.classList.contains("ag-cell"),
      ) || pinnedCheckboxes[0];
    if (checkbox) {
      const rect = checkbox.getBoundingClientRect();
      return {
        x: rect.left + rect.width / 2,
        y,
      };
    }
    return {
      x: rowRect.left - 24,
      y,
    };
  }, rowText);
  assert(checkboxBox, `Could not find selectable grid row: ${rowText}`);
  await page.mouse.click(checkboxBox.x, checkboxBox.y);
  await page.waitForFunction(
    () =>
      window
        .visibleElements()
        .some(
          (element) =>
            window.normalizeText(element.textContent) === "1 Selected",
        ),
    { timeout: 30000 },
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
    path === `/model-hub/datasets/${sourceDatasetId}/merge/`
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
