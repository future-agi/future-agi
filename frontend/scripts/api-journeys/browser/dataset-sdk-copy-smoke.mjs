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
const DATASET_PREFIX = "ui_dataset_sdk_copy_";
const SCREENSHOT_PATH = "/tmp/dataset-sdk-copy-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/dataset-sdk-copy-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);
const SDK_MODAL_HEADING = "Add Rows via SDK or API";

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const datasetName = `${DATASET_PREFIX}${suffix}`;
  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const modelHubRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let datasetId = null;

  await cleanupDatasetsByPrefix(auth.client, DATASET_PREFIX, cleanupEvidence);

  try {
    const source = await createSourceDataset(auth.client, datasetName);
    if (source.skipped) {
      console.log(JSON.stringify(source.output, null, 2));
      return;
    }
    datasetId = source.datasetId;

    const directSdkPayload = await auth.client.post(
      apiPath("/model-hub/develops/add_rows_sdk/"),
      { dataset_id: datasetId },
    );
    assertDatasetSdkPayload({
      payload: directSdkPayload,
      datasetId,
      datasetName,
      label: "direct API",
    });

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });
    await browser
      .defaultBrowserContext()
      .overridePermissions(APP_BASE, ["clipboard-read", "clipboard-write"])
      .catch(() => null);

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
        browserMutations.push(requestKey);
        if (!isAllowedBrowserMutation(request.method(), url)) {
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

    await clickEnabledButton(page, "Add Row");
    await waitForVisibleText(page, "Add Rows", { exact: true });

    const browserSdkResponse = await waitForResponseDuring(
      page,
      "browser add rows SDK payload",
      (response) =>
        response.url().includes("/model-hub/develops/add_rows_sdk/") &&
        response.request().method() === "POST" &&
        response.status() < 400,
      () => clickVisibleText(page, "Add data using SDK", { exact: true }),
    );
    const browserSdkEnvelope = await responseJson(browserSdkResponse);
    const browserSdkPayload =
      browserSdkEnvelope?.result || browserSdkEnvelope || {};
    assertDatasetSdkPayload({
      payload: browserSdkPayload,
      datasetId,
      datasetName,
      label: "browser API",
    });

    await waitForVisibleText(page, "Add Rows via SDK or API", { exact: true });
    await waitForVisibleText(page, datasetName, { exact: true });
    await waitForVisibleText(page, datasetId, { exact: true });
    await waitForVisibleText(page, "YOUR_API_KEY", { exact: true });
    await waitForVisibleText(page, "YOUR_SECRET_KEY", { exact: true });

    const clipboard = {};
    clipboard.dataset_name = await clickAndReadClipboard(page, {
      ariaLabel: "Copy Dataset Name",
      scopeText: SDK_MODAL_HEADING,
    });
    clipboard.dataset_id = await clickAndReadClipboard(page, {
      ariaLabel: "Copy Dataset ID",
      scopeText: SDK_MODAL_HEADING,
    });
    clipboard.api_key = await clickAndReadClipboard(page, {
      ariaLabel: "Copy API Key",
      scopeText: SDK_MODAL_HEADING,
    });
    clipboard.secret_key = await clickAndReadClipboard(page, {
      ariaLabel: "Copy Secret Key",
      scopeText: SDK_MODAL_HEADING,
    });
    assert(
      clipboard.dataset_name === datasetName,
      `Dataset Name copy wrote ${clipboard.dataset_name}`,
    );
    assert(
      clipboard.dataset_id === datasetId,
      `Dataset ID copy wrote ${clipboard.dataset_id}`,
    );
    assert(
      clipboard.api_key === "YOUR_API_KEY",
      `API Key copy wrote ${clipboard.api_key}`,
    );
    assert(
      clipboard.secret_key === "YOUR_SECRET_KEY",
      `Secret Key copy wrote ${clipboard.secret_key}`,
    );

    const snippetCopies = {};
    snippetCopies.python = await clickAndReadClipboard(page, {
      ariaLabel: "Copy code",
      scopeText: SDK_MODAL_HEADING,
    });
    await clickVisibleText(page, "Typescript", { exact: true });
    snippetCopies.typescript = await clickAndReadClipboard(page, {
      ariaLabel: "Copy code",
      scopeText: SDK_MODAL_HEADING,
    });
    await clickVisibleText(page, "Curl", { exact: true });
    snippetCopies.curl = await clickAndReadClipboard(page, {
      ariaLabel: "Copy code",
      scopeText: SDK_MODAL_HEADING,
    });
    for (const [language, snippet] of Object.entries(snippetCopies)) {
      assertSnippetSafe({ language, snippet, datasetId, datasetName });
    }

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
          direct_code_keys: Object.keys(directSdkPayload?.code || {}).sort(),
          browser_code_keys: Object.keys(browserSdkPayload?.code || {}).sort(),
          copied: {
            dataset_name: clipboard.dataset_name === datasetName,
            dataset_id: clipboard.dataset_id === finalDatasetId,
            api_key: clipboard.api_key,
            secret_key: clipboard.secret_key,
            snippets: Object.fromEntries(
              Object.entries(snippetCopies).map(([language, snippet]) => [
                language,
                {
                  length: snippet.length,
                  has_api_placeholder: snippet.includes("YOUR_API_KEY"),
                  has_secret_placeholder: snippet.includes("YOUR_SECRET_KEY"),
                  has_dataset_id: snippet.includes(finalDatasetId),
                  has_dataset_name: snippet.includes(datasetName),
                },
              ]),
            ),
          },
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
            cleanup: "delete dataset SDK copy dataset after failure",
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

async function createSourceDataset(client, datasetName) {
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

  const table = await getDatasetTable(client, datasetId);
  const columns = asArray(table.column_config);
  const rows = asArray(table.table).filter((row) => row?.row_id);
  assert(rows.length === 1, "Dataset SDK copy fixture did not have one row.");
  assert(
    columns.some((column) => column?.name === "Column 1"),
    "Dataset SDK copy fixture was missing Column 1.",
  );

  return { datasetId };
}

async function getDatasetTable(client, datasetId) {
  return client.get(
    apiPath("/model-hub/develops/{dataset_id}/get-dataset-table/", {
      dataset_id: datasetId,
    }),
    { query: { current_page_index: 0, page_size: 50 } },
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
    cleanup: "delete dataset SDK copy dataset",
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
    window.isScopedAriaButton = (button, label, scopeText) => {
      if (
        button.getAttribute("aria-label") !== label ||
        button.disabled ||
        !window.visibleElements("button").includes(button)
      ) {
        return false;
      }
      if (!scopeText) return true;
      const modalRoot = button.closest(".MuiModal-root,[role='presentation']");
      return Boolean(
        modalRoot &&
          window.normalizeText(modalRoot.textContent).includes(scopeText),
      );
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

      window.__datasetSdkClipboardWrites = [];
      const clipboard = {
        writeText: async (text) => {
          window.__datasetSdkClipboardWrites.push(String(text));
        },
        readText: async () =>
          window.__datasetSdkClipboardWrites[
            window.__datasetSdkClipboardWrites.length - 1
          ] || "",
      };
      Object.defineProperty(Navigator.prototype, "clipboard", {
        configurable: true,
        get: () => clipboard,
      });
    },
    {
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );
}

async function clickAndReadClipboard(page, { ariaLabel, scopeText }) {
  const previousWriteCount = await page.evaluate(
    () => window.__datasetSdkClipboardWrites?.length || 0,
  );
  await clickButtonByAriaLabel(page, { ariaLabel, scopeText });
  await page.waitForFunction(
    (writeCount) =>
      (window.__datasetSdkClipboardWrites?.length || 0) > writeCount,
    { timeout: 10000 },
    previousWriteCount,
  );
  return page.evaluate(() => navigator.clipboard.readText());
}

async function clickButtonByAriaLabel(
  page,
  { ariaLabel, scopeText },
  timeout = 30000,
) {
  await page.waitForFunction(
    ({ label, scope }) =>
      Array.from(document.querySelectorAll("button")).some((button) =>
        window.isScopedAriaButton(button, label, scope),
      ),
    { timeout },
    { label: ariaLabel, scope: scopeText },
  );
  const clicked = await page.evaluate(
    ({ label, scope }) => {
      const button = Array.from(document.querySelectorAll("button")).find(
        (candidate) => window.isScopedAriaButton(candidate, label, scope),
      );
      if (!button) return false;
      window.dispatchClick(button);
      return true;
    },
    { label: ariaLabel, scope: scopeText },
  );
  assert(clicked, `Could not click button with aria-label ${ariaLabel}.`);
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
          "button,a,[role='button'],[role='menuitem'],li.MuiMenuItem-root",
        ) || element;
      if (!clickable || clickable.disabled) return false;
      window.dispatchClick(clickable);
      return true;
    },
    { text, exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

async function clickEnabledButton(page, label, timeout = 30000) {
  await page.waitForFunction(
    (expectedLabel) =>
      window
        .visibleElements("button")
        .some(
          (candidate) =>
            window.normalizeText(candidate.textContent) === expectedLabel &&
            !candidate.disabled,
        ),
    { timeout },
    label,
  );
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
  assert(clicked, `Could not click enabled button: ${label}`);
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

function assertDatasetSdkPayload({ payload, datasetId, datasetName, label }) {
  assert(
    payload?.dataset?.id === datasetId,
    `${label} SDK payload returned dataset ${payload?.dataset?.id}`,
  );
  assert(
    payload?.dataset?.name === datasetName,
    `${label} SDK payload returned dataset name ${payload?.dataset?.name}`,
  );
  const apiKeys = payload?.api_keys || payload?.apiKeys || {};
  assert(
    (apiKeys?.api_key || apiKeys?.apiKey) === "YOUR_API_KEY",
    `${label} SDK payload exposed a non-placeholder API key.`,
  );
  assert(
    (apiKeys?.secret_key || apiKeys?.secretKey) === "YOUR_SECRET_KEY",
    `${label} SDK payload exposed a non-placeholder secret key.`,
  );

  const expectedCodeKeys = [
    "curl_add_col",
    "curl_add_row",
    "python_add_col",
    "python_add_row",
    "typescript_add_col",
    "typescript_add_row",
  ];
  const code = payload?.code || {};
  for (const key of expectedCodeKeys) {
    assert(
      typeof code[key] === "string" && code[key].length > 0,
      `${label} SDK payload missing non-empty code.${key}.`,
    );
  }
  for (const [key, snippet] of Object.entries(code)) {
    assertSnippetSafe({ language: key, snippet, datasetId, datasetName });
  }
}

function assertSnippetSafe({ language, snippet, datasetId, datasetName }) {
  assert(snippet.length > 0, `${language} copied an empty SDK snippet.`);
  const hasApiPlaceholder = snippet.includes("YOUR_API_KEY");
  const hasSecretPlaceholder = snippet.includes("YOUR_SECRET_KEY");
  assert(
    hasApiPlaceholder && hasSecretPlaceholder,
    `${language} SDK snippet did not include placeholder credentials: ${snippetSummary(
      snippet,
      { datasetId, datasetName },
    )}`,
  );
  if (String(language).toLowerCase().includes("curl")) {
    assert(
      snippet.includes(datasetId),
      `${language} SDK snippet did not include the selected dataset id.`,
    );
  }
  assert(
    snippet.includes(datasetId) || snippet.includes(datasetName),
    `${language} SDK snippet did not reference the selected dataset: ${snippetSummary(
      snippet,
      { datasetId, datasetName },
    )}`,
  );
  const rawCredentialPatterns = [
    /FI_API_KEY["'\]]*\s*[:=]\s*["'][0-9a-f]{32}["']/iu,
    /FI_SECRET_KEY["'\]]*\s*[:=]\s*["'][0-9a-f]{32}["']/iu,
    /X-Api-Key:\s*[0-9a-f]{32}/iu,
    /X-Secret-Key:\s*[0-9a-f]{32}/iu,
  ];
  assert(
    !rawCredentialPatterns.some((pattern) => pattern.test(snippet)),
    `${language} SDK snippet included a raw credential-looking value.`,
  );
}

function snippetSummary(snippet, { datasetId, datasetName }) {
  const value = String(snippet || "");
  return JSON.stringify({
    length: value.length,
    has_api_placeholder: value.includes("YOUR_API_KEY"),
    has_secret_placeholder: value.includes("YOUR_SECRET_KEY"),
    has_dataset_id: value.includes(datasetId),
    has_dataset_name: value.includes(datasetName),
    preview: value
      .replace(/[0-9a-f]{32}/giu, "<redacted-hex>")
      .replace(new RegExp(datasetId, "gu"), "<dataset-id>")
      .replace(new RegExp(datasetName, "gu"), "<dataset-name>")
      .slice(0, 160),
  });
}

function isModelHubApiUrl(rawUrl) {
  const url = new URL(rawUrl);
  return (
    url.origin ===
      new URL(process.env.API_BASE || "http://localhost:8003").origin &&
    (url.pathname.startsWith("/model-hub/develops/") ||
      url.pathname.startsWith("/model-hub/datasets/"))
  );
}

function isAllowedBrowserMutation(method, rawUrl) {
  const path = new URL(rawUrl).pathname;
  return method === "POST" && path === "/model-hub/develops/add_rows_sdk/";
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
