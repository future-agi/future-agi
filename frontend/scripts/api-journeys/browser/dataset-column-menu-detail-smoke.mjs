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
const DATASET_PREFIX = "ui_column_menu_";
const SCREENSHOT_PATH = "/tmp/dataset-column-menu-detail-smoke.png";
const PLAN_LIMIT_SCREENSHOT_PATH =
  "/tmp/dataset-column-menu-detail-plan-limited-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/dataset-column-menu-detail-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const datasetName = `${DATASET_PREFIX}${suffix}`;
  const renamedColumnName = `Column menu ${suffix}`;
  const retainedValue = `column menu retained ${suffix}`;
  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const modelHubRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let datasetId = null;
  let deletedColumnId = null;

  await cleanupDatasetsByPrefix(auth.client, DATASET_PREFIX, cleanupEvidence);

  try {
    const source = await createSourceDataset(auth.client, {
      datasetName,
      retainedValue,
    });
    if (source.skipped) {
      console.log(JSON.stringify(source.output, null, 2));
      return;
    }
    datasetId = source.datasetId;
    deletedColumnId = source.columnTwo.id;

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
        if (
          !isAllowedMutation(request.method(), url, datasetId, deletedColumnId)
        ) {
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
    await waitForVisibleText(page, retainedValue, { exact: true });
    await waitForVisibleText(page, "Column 2", { exact: true });

    await openColumnHeaderMenu(page, "Column 2");
    await clickMenuOption(page, "Edit Column Name");
    await waitForVisibleText(page, "Edit Column Name", { exact: true });
    await setInputByLabel(page, "Column Name", renamedColumnName);

    const renameResponse = await waitForResponseDuring(
      page,
      "rename dataset column",
      (response) =>
        response
          .url()
          .includes(
            `/model-hub/develops/${datasetId}/update_column_name/${deletedColumnId}/`,
          ) && response.request().method() === "PUT",
      () => clickDialogButton(page, "Save"),
    );
    const renamePayload = await responseJson(renameResponse);

    if (renameResponse.status() === 429) {
      await page.screenshot({
        path: PLAN_LIMIT_SCREENSHOT_PATH,
        fullPage: true,
      });
      console.log(
        JSON.stringify(
          {
            status: "skipped",
            reason: "dataset column rename is plan-limited",
            app_base: APP_BASE,
            api_base: auth.apiBase,
            organization_id: auth.organizationId,
            workspace_id: auth.workspaceId,
            response_status: renameResponse.status(),
            response_message:
              renamePayload?.message ||
              renamePayload?.detail ||
              renamePayload?.error ||
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
      renameResponse.status() >= 200 && renameResponse.status() < 300,
      `Rename column returned HTTP ${renameResponse.status()}: ${JSON.stringify(
        renamePayload,
      )}`,
    );
    await waitForColumnNames(auth.client, datasetId, [renamedColumnName]);
    await waitForNoVisibleExactText(page, "Edit Column Name");
    await waitForVisibleText(page, renamedColumnName, { exact: true });

    await openColumnHeaderMenu(page, renamedColumnName);
    await clickMenuOption(page, "Delete Column");
    await waitForVisibleText(
      page,
      "Are you sure you want to delete this column?",
      {
        exact: true,
      },
    );

    const deleteResponse = await waitForResponseDuring(
      page,
      "delete dataset column",
      (response) =>
        response
          .url()
          .includes(
            `/model-hub/develops/${datasetId}/delete_column/${deletedColumnId}/`,
          ) && response.request().method() === "DELETE",
      () => clickDialogButton(page, "Delete"),
    );
    const deletePayload = await responseJson(deleteResponse);

    assert(
      deleteResponse.status() >= 200 && deleteResponse.status() < 300,
      `Delete column returned HTTP ${deleteResponse.status()}: ${JSON.stringify(
        deletePayload,
      )}`,
    );
    await waitForColumnNames(
      auth.client,
      datasetId,
      ["Column 1"],
      [renamedColumnName],
    );
    await waitForNoVisibleExactText(page, renamedColumnName);
    await waitForVisibleText(page, "Column 1", { exact: true });

    const table = await getDatasetTable(auth.client, datasetId);
    const rows = asArray(table.table).filter((row) => row?.row_id);
    const columns = asArray(table.column_config);
    const columnOne = columns.find((column) => column?.name === "Column 1");
    assert(rows.length === 1, "Column menu dataset lost its only row.");
    assert(columns.length === 1, "Deleted column still appears in metadata.");
    assert(columnOne?.id, "Retained Column 1 metadata was missing.");
    assert(
      !columns.some((column) => column?.id === deletedColumnId),
      "Deleted column id still appears in active column_config.",
    );
    assert(
      rows.some((row) => cellValueFor(row, columnOne.id) === retainedValue),
      "Retained column value did not survive column delete.",
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
          renamed_column_id: deletedColumnId,
          renamed_column_name: renamedColumnName,
          remaining_columns: columns.map((column) => column.name),
          rows_after_delete: rows.length,
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
    if (datasetId) {
      await deleteDatasets(auth.client, [datasetId], cleanupEvidence).catch(
        (error) => {
          cleanupEvidence.push({
            cleanup: "delete column-menu dataset after failure",
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

async function createSourceDataset(client, { datasetName, retainedValue }) {
  let created;
  try {
    created = await client.post(
      apiPath("/model-hub/develops/create-dataset-manually/"),
      {
        dataset_name: datasetName,
        number_of_rows: 1,
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

  let table = await getDatasetTable(client, datasetId);
  const columns = asArray(table.column_config);
  const rows = asArray(table.table).filter((row) => row?.row_id);
  const columnOne = columns.find((column) => column?.name === "Column 1");
  const columnTwo = columns.find((column) => column?.name === "Column 2");
  assert(rows.length === 1, "Column menu dataset did not have one row.");
  assert(columnOne?.id, "Column menu dataset was missing Column 1.");
  assert(columnTwo?.id, "Column menu dataset was missing Column 2.");

  await updateCell(
    client,
    datasetId,
    rows[0].row_id,
    columnOne.id,
    retainedValue,
  );

  table = await getDatasetTable(client, datasetId);
  const updatedRows = asArray(table.table).filter((row) => row?.row_id);
  assert(
    updatedRows.some(
      (row) => cellValueFor(row, columnOne.id) === retainedValue,
    ),
    "Retained value did not round-trip before column menu actions.",
  );

  return { datasetId, columnOne, columnTwo };
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

async function waitForColumnNames(
  client,
  datasetId,
  expectedNames,
  absentNames = [],
) {
  const deadline = Date.now() + 60000;
  let lastNames = [];
  while (Date.now() < deadline) {
    const table = await getDatasetTable(client, datasetId);
    lastNames = asArray(table.column_config).map((column) => column?.name);
    const hasExpected = expectedNames.every((name) => lastNames.includes(name));
    const hasNoAbsent = absentNames.every((name) => !lastNames.includes(name));
    if (hasExpected && hasNoAbsent) return;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(
    `Timed out waiting for column names. Last names: ${lastNames.join(", ")}`,
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
    cleanup: "delete column-menu dataset",
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

async function openColumnHeaderMenu(page, columnName) {
  await waitForVisibleText(page, columnName, { exact: true });
  const clickTarget = await page.evaluate((expectedColumnName) => {
    const headers = window
      .visibleElements(".ag-header-cell")
      .filter((header) =>
        Array.from(header.querySelectorAll("*")).some(
          (element) =>
            window.normalizeText(element.textContent) === expectedColumnName,
        ),
      );
    const header =
      headers.find((candidate) => candidate.querySelector("button")) ||
      headers[0];
    const button = header?.querySelector("button");
    if (!button || button.disabled) return null;
    const rect = button.getBoundingClientRect();
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  }, columnName);
  assert(clickTarget, `Could not find column menu button for ${columnName}.`);
  await page.mouse.click(clickTarget.x, clickTarget.y);
  await waitForVisibleText(page, "Edit Column Name", { exact: true });
}

async function clickMenuOption(page, label) {
  await waitForVisibleText(page, label, { exact: true });
  const clickTarget = await page.evaluate((expectedLabel) => {
    const selectors = [
      ".ag-menu-option",
      '[role="menuitem"]',
      "li.MuiMenuItem-root",
      "button",
    ];
    for (const selector of selectors) {
      const element = window
        .visibleElements(selector)
        .find(
          (candidate) =>
            window.normalizeText(candidate.textContent) === expectedLabel &&
            candidate.getAttribute("aria-disabled") !== "true" &&
            !candidate.disabled,
        );
      if (element) {
        const rect = element.getBoundingClientRect();
        return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
      }
    }
    return null;
  }, label);
  assert(clickTarget, `Could not click menu option: ${label}`);
  await page.mouse.click(clickTarget.x, clickTarget.y);
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

function isAllowedMutation(method, rawUrl, datasetId, columnId) {
  const path = new URL(rawUrl).pathname;
  return (
    (method === "PUT" &&
      path ===
        `/model-hub/develops/${datasetId}/update_column_name/${columnId}/`) ||
    (method === "DELETE" &&
      path === `/model-hub/develops/${datasetId}/delete_column/${columnId}/`)
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
