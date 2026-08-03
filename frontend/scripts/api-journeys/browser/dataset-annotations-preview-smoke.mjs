/* eslint-disable no-console */
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  currentUserId,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const DATASET_PREFIX = "ui_annotation_preview_";
const LABEL_PREFIX = "ui annotation preview label ";
const ANNOTATION_PREFIX = "ui annotation preview ";
const SCREENSHOT_PATH = "/tmp/dataset-annotations-preview-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/dataset-annotations-preview-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const userId = currentUserId(auth.user);
  assert(userId, "Authenticated user response did not include an id.");

  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const datasetName = `${DATASET_PREFIX}${suffix}`;
  const labelName = `${LABEL_PREFIX}${suffix}`;
  const annotationName = `${ANNOTATION_PREFIX}${suffix}`;
  const staticCellValue = `Preview source text ${suffix}`;
  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const modelHubRequests = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let datasetId = null;
  let labelId = null;
  let annotationId = null;

  await cleanupDatasetsByPrefix(auth.client, DATASET_PREFIX, cleanupEvidence);
  await cleanupLabelsByPrefix(auth.client, LABEL_PREFIX, cleanupEvidence);

  try {
    const source = await createSourceDataset(auth.client, {
      datasetName,
      staticCellValue,
    });
    if (source.skipped) {
      console.log(JSON.stringify(source.output, null, 2));
      return;
    }
    datasetId = source.datasetId;
    const rowOrder = rowOrderFor(source.row);

    const label = await createAnnotationLabel(auth.client, labelName);
    labelId = label.id;

    const annotation = await createLegacyAnnotation(auth.client, {
      annotationName,
      datasetId,
      labelId,
      userId,
      staticColumnId: source.columnOne.id,
    });
    annotationId = annotation.id;

    const directPreview = await auth.client.get(
      apiPath("/model-hub/annotations/{id}/annotate_row/", {
        id: annotationId,
      }),
      { query: { row_order: rowOrder } },
    );
    const directPreviewData = directPreview?.data || directPreview;
    assert(
      asArray(directPreviewData?.static_fields).some(
        (field) =>
          field.column_id === source.columnOne.id &&
          field.value === staticCellValue,
      ),
      "Direct annotate_row readback did not include the preview static field.",
    );
    assert(
      asArray(directPreviewData?.label).some(
        (field) => field.label_id === labelId && field.label_name === labelName,
      ),
      "Direct annotate_row readback did not include the created label.",
    );

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
        unexpectedMutations.push(requestKey);
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isModelHubApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    const [annotateRowResponse] = await waitForResponsesDuring(
      page,
      "dataset annotation preview route",
      [
        (response) =>
          response
            .url()
            .includes(`/model-hub/annotations/${annotationId}/annotate_row/`) &&
          response.request().method() === "GET" &&
          response.status() < 400,
      ],
      () =>
        page.goto(
          `${APP_BASE}/dashboard/develop/${datasetId}/preview/${annotationId}?annotationIndex=${
            rowOrder
          }`,
          { waitUntil: "domcontentloaded" },
        ),
    );
    const browserPreview = await responseJson(annotateRowResponse);
    const browserPreviewData = browserPreview?.result?.data;
    assert(
      asArray(browserPreviewData?.static_fields).some(
        (field) => field.value === staticCellValue,
      ),
      "Browser annotate_row response did not include the static field value.",
    );
    assert(
      asArray(browserPreviewData?.label).some(
        (field) => field.label_name === labelName,
      ),
      "Browser annotate_row response did not include the label name.",
    );

    await waitForPathIncludes(
      page,
      `/dashboard/develop/${datasetId}/preview/${annotationId}`,
    );
    await waitForVisibleText(page, "Annotations", { exact: true });
    await waitForVisibleText(page, "Column 1", { exact: true });
    await waitForVisibleText(page, staticCellValue, { exact: true });
    await waitForVisibleText(page, labelName, { exact: true });
    await waitForVisibleText(page, "Row 1 of 1", { exact: true });

    const pageText = await page.evaluate(() => document.body.innerText || "");
    assert(
      !/(Invalid Date|undefined)/.test(pageText),
      "Dataset annotation preview rendered invalid placeholder text.",
    );

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      unexpectedMutations.length === 0,
      `Unexpected browser mutations: ${unexpectedMutations
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

    const finalIds = {
      datasetId,
      annotationId,
      labelId,
    };
    await deleteAnnotation(auth.client, annotationId, cleanupEvidence);
    annotationId = null;
    await deleteLabel(auth.client, labelId, cleanupEvidence);
    labelId = null;
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
          dataset_id: finalIds.datasetId,
          annotation_id: finalIds.annotationId,
          label_id: finalIds.labelId,
          screenshot: SCREENSHOT_PATH,
          model_hub_request_count: modelHubRequests.length,
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
    if (annotationId) {
      await deleteAnnotation(auth.client, annotationId, cleanupEvidence).catch(
        (error) => {
          cleanupEvidence.push({
            cleanup: "delete annotation preview annotation after failure",
            status: "failed",
            error: error.message,
          });
        },
      );
    }
    if (labelId) {
      await deleteLabel(auth.client, labelId, cleanupEvidence).catch(
        (error) => {
          cleanupEvidence.push({
            cleanup: "delete annotation preview label after failure",
            status: "failed",
            error: error.message,
          });
        },
      );
    }
    if (datasetId) {
      await deleteDatasets(auth.client, [datasetId], cleanupEvidence).catch(
        (error) => {
          cleanupEvidence.push({
            cleanup: "delete annotation preview dataset after failure",
            status: "failed",
            error: error.message,
          });
        },
      );
    }
    await cleanupDatasetsByPrefix(auth.client, DATASET_PREFIX, cleanupEvidence);
    await cleanupLabelsByPrefix(auth.client, LABEL_PREFIX, cleanupEvidence);
    if (browser) await browser.close();
  }
}

async function createSourceDataset(client, { datasetName, staticCellValue }) {
  let created;
  try {
    created = await client.post(
      apiPath("/model-hub/develops/create-dataset-manually/"),
      {
        dataset_name: datasetName,
        number_of_rows: 1,
        number_of_columns: 1,
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
  const row = rows[0];
  assert(row?.row_id, "Annotation preview dataset did not have one row.");
  assert(columnOne?.id, "Annotation preview dataset was missing Column 1.");

  await updateCell(
    client,
    datasetId,
    row.row_id,
    columnOne.id,
    staticCellValue,
  );

  table = await getDatasetTable(client, datasetId);
  const updatedRows = asArray(table.table).filter((item) => item?.row_id);
  assert(
    updatedRows.some(
      (item) => cellValueFor(item, columnOne.id) === staticCellValue,
    ),
    "Static annotation preview value did not round-trip before browser open.",
  );

  return {
    datasetId,
    row,
    columnOne,
  };
}

async function createAnnotationLabel(client, labelName) {
  await client.post(apiPath("/model-hub/annotations-labels/"), {
    name: labelName,
    type: "categorical",
    settings: {
      rule_prompt: "Choose whether the answer is acceptable.",
      multi_choice: false,
      options: [{ label: "Pass" }, { label: "Fail" }],
      auto_annotate: false,
      strategy: null,
    },
    allow_notes: false,
  });

  const label = await findLabelByName(client, labelName);
  assert(label?.id, "Created annotation label was not listed.");
  return label;
}

async function createLegacyAnnotation(
  client,
  { annotationName, datasetId, labelId, userId, staticColumnId },
) {
  await client.post(apiPath("/model-hub/annotations/"), {
    name: annotationName,
    dataset: datasetId,
    assigned_users: [userId],
    labels: [{ id: labelId, required: false }],
    responses: 1,
    static_fields: [
      {
        column_id: staticColumnId,
        type: "plain_text",
        view: "default_open",
      },
    ],
  });

  const annotation = await findLegacyAnnotationByName(
    client,
    datasetId,
    annotationName,
  );
  assert(annotation?.id, "Created legacy annotation was not listed.");
  return annotation;
}

async function findLabelByName(client, labelName) {
  const payload = await client.get(apiPath("/model-hub/annotations-labels/"), {
    query: { search: labelName },
  });
  return asArray(payload).find((label) => label?.name === labelName);
}

async function findLegacyAnnotationByName(client, datasetId, annotationName) {
  const payload = await client.get(apiPath("/model-hub/annotations/"), {
    query: { dataset: datasetId },
  });
  return asArray(payload).find(
    (annotation) => annotation?.name === annotationName,
  );
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

function rowOrderFor(row) {
  return row?.order ?? row?.row_order ?? row?.rowOrder ?? 0;
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

async function cleanupLabelsByPrefix(client, prefix, evidence) {
  const listPayload = await client.get(
    apiPath("/model-hub/annotations-labels/"),
    {
      query: { search: prefix },
    },
  );
  const ids = asArray(listPayload)
    .filter((label) => String(label?.name || "").startsWith(prefix))
    .map((label) => label.id)
    .filter(Boolean);
  for (const id of ids) {
    await deleteLabel(client, id, evidence);
  }
}

async function deleteAnnotation(client, annotationId, evidence) {
  if (!annotationId) return;
  await client.delete(
    apiPath("/model-hub/annotations/{id}/", { id: annotationId }),
    {
      okStatuses: [200, 404],
    },
  );
  evidence.push({
    cleanup: "delete annotation preview annotation",
    status: "passed",
    annotation_id: annotationId,
  });
}

async function deleteLabel(client, labelId, evidence) {
  if (!labelId) return;
  await client.delete(
    apiPath("/model-hub/annotations-labels/{id}/", { id: labelId }),
    {
      okStatuses: [200, 204, 404],
    },
  );
  evidence.push({
    cleanup: "delete annotation preview label",
    status: "passed",
    label_id: labelId,
  });
}

async function deleteDatasets(client, datasetIds, evidence) {
  if (!datasetIds.length) return;
  try {
    await client.delete(apiPath("/model-hub/develops/delete_dataset/"), {
      body: { dataset_ids: datasetIds },
      okStatuses: [200, 404],
    });
  } catch (error) {
    const detail = String(
      error?.body?.detail || error?.body?.message || error?.message || "",
    );
    if (error?.status !== 400 || !detail.includes("No datasets were found")) {
      throw error;
    }
  }
  evidence.push({
    cleanup: "delete annotation preview dataset",
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
