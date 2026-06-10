/* eslint-disable no-console */
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
import process from "node:process";
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

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const KB_PREFIX = "ui_kb_lifecycle_";
const SCREENSHOT_PREFIX =
  process.env.KNOWLEDGE_BASE_LIFECYCLE_SCREENSHOT_PREFIX ||
  "/tmp/knowledge-base-lifecycle";
const DETAIL_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-detail-smoke.png`;
const LIST_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-list-smoke.png`;
const FAILURE_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-failure-smoke.png`;
const KB_FILES_PATH = "/model-hub/knowledge-base/files/";
const KB_PATH = "/model-hub/knowledge-base/";
const KB_GET_PATH = "/model-hub/knowledge-base/get/";
const KB_LIST_PATH = "/model-hub/knowledge-base/list/";

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  const kbName = `${KB_PREFIX}${suffix}`;
  const fileName = `${kbName}.txt`;
  const tmpDir = await mkdtemp(join(tmpdir(), "knowledge-base-lifecycle-"));
  const filePath = join(tmpDir, fileName);
  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const knowledgeBaseRequests = [];
  const browserWrites = [];
  const unexpectedWrites = [];
  let browser = null;
  let page = null;
  let kbId = "";
  let fileId = "";
  let deletedViaUi = false;

  await writeFile(
    filePath,
    `Knowledge base browser lifecycle fixture ${auth.runId}\n`,
    "utf8",
  );

  try {
    await deleteKnowledgeBaseFixturesByPrefix(
      auth.client,
      KB_PREFIX,
      cleanupEvidence,
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
      if (!isKnowledgeBaseApiUrl(request.url())) return;
      const requestKey = `${request.method()} ${request.url()}`;
      knowledgeBaseRequests.push(requestKey);
      if (
        isStateChangingKnowledgeBaseRequest(request.method(), request.url())
      ) {
        browserWrites.push(requestKey);
        if (!isAllowedBrowserWrite(request.method(), request.url())) {
          unexpectedWrites.push(requestKey);
        }
      }
    });
    page.on("response", (response) => {
      if (isKnowledgeBaseApiUrl(response.url()) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${response.url()}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponsesDuring(
      page,
      "Knowledge Base list load",
      [
        (response) =>
          response.url().includes(KB_GET_PATH) &&
          response.request().method() === "GET" &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/knowledge`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPathIncludes(page, "/dashboard/knowledge");
    await waitForVisibleText(page, "Create Knowledge Base", { exact: true });
    await clickEnabledButton(page, "Create Knowledge Base");
    await waitForVisibleText(page, "Create knowledge base", { exact: true });
    await setInputByPlaceholder(page, "Name", kbName);

    const input = await page.waitForSelector('input[type="file"]', {
      timeout: 30000,
    });
    await input.uploadFile(filePath);
    await waitForVisibleText(page, fileName, { exact: true });
    await waitForVisibleText(page, "Files uploaded: 1/1", { exact: true });

    const createResponse = await waitForResponseDuring(
      page,
      "Knowledge Base browser create",
      (response) =>
        response.url().includes(KB_PATH) &&
        response.request().method() === "POST",
      () => clickEnabledButton(page, "Create"),
    );
    const createPayload = await responseJson(createResponse);
    if (isEntitlementOrPlanLimit(createResponse, createPayload)) {
      console.log(
        JSON.stringify(
          {
            status: "skipped",
            reason: "knowledge-base browser create is entitlement-blocked",
            app_base: APP_BASE,
            api_base: auth.apiBase,
            organization_id: auth.organizationId,
            workspace_id: auth.workspaceId,
            response_status: createResponse.status(),
            response_message: apiMessage(createPayload),
            browser_writes: browserWrites.map(maskRequest),
            cleanup: cleanupEvidence,
          },
          null,
          2,
        ),
      );
      return;
    }
    assert(
      createResponse.status() >= 200 && createResponse.status() < 300,
      `Knowledge Base browser create returned HTTP ${createResponse.status()}: ${JSON.stringify(
        createPayload,
      )}`,
    );

    const createResult = createPayload?.result || createPayload || {};
    kbId = createResult.kb_id || createResult.kbId || "";
    const fileIds = asArray(createResult.file_ids || createResult.fileIds);
    fileId = fileIds[0] || "";
    assert(isUuid(kbId), "Knowledge Base browser create omitted a UUID kb_id.");
    assert(
      isUuid(fileId),
      "Knowledge Base browser create omitted file_ids[0].",
    );

    await waitForPathIncludes(page, `/dashboard/knowledge/${kbId}`);
    await waitForVisibleText(page, fileName, { exact: true });
    await waitForVisibleText(page, "Add docs", { exact: true });
    await page.screenshot({ path: DETAIL_SCREENSHOT_PATH, fullPage: true });

    const apiReadback = await assertApiReadback({
      client: auth.client,
      kbId,
      kbName,
      fileId,
      fileName,
    });

    await waitForResponsesDuring(
      page,
      "Knowledge Base list reload",
      [
        (response) =>
          response.url().includes(KB_GET_PATH) &&
          response.request().method() === "GET" &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/knowledge`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPathIncludes(page, "/dashboard/knowledge");
    await waitForResponseDuring(
      page,
      "Knowledge Base list search",
      (response) => {
        if (
          !response.url().includes(KB_GET_PATH) ||
          response.request().method() !== "GET" ||
          response.status() >= 400
        ) {
          return false;
        }
        return new URL(response.url()).searchParams.get("search") === kbName;
      },
      () => setInputByPlaceholder(page, "Search", kbName),
    );
    await waitForVisibleText(page, kbName, { exact: true });
    await selectKnowledgeBaseRow(page, kbName);
    await waitForVisibleText(page, "1 Selected", { exact: true });
    await page.screenshot({ path: LIST_SCREENSHOT_PATH, fullPage: true });

    await clickVisibleText(page, "Delete", { exact: true });
    await waitForVisibleText(page, "Delete Knowledge Base", { exact: true });
    const deleteResponse = await waitForResponseDuring(
      page,
      "Knowledge Base browser delete",
      (response) =>
        response.url().includes(KB_PATH) &&
        response.request().method() === "DELETE",
      () =>
        clickDialogButton(page, {
          buttonText: "Delete",
          dialogText: "Delete Knowledge Base",
        }),
    );
    const deletePayload = await responseJson(deleteResponse);
    assert(
      deleteResponse.status() >= 200 && deleteResponse.status() < 300,
      `Knowledge Base browser delete returned HTTP ${deleteResponse.status()}: ${JSON.stringify(
        deletePayload,
      )}`,
    );
    deletedViaUi = true;
    await waitForKnowledgeBaseGone(auth.client, { kbId, kbName });
    await expectApiErrorStatus(
      () =>
        auth.client.post(apiPath(KB_FILES_PATH), {
          kb_id: kbId,
          page_number: 0,
          page_size: 10,
        }),
      400,
      "Knowledge Base files endpoint accepted a deleted KB.",
    );

    assert(
      unexpectedWrites.length === 0,
      `Unexpected Knowledge Base browser writes: ${unexpectedWrites
        .map(maskRequest)
        .join(", ")}`,
    );
    assert(
      apiFailures.length === 0,
      `Knowledge Base browser API failures: ${apiFailures.join(", ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          kb_id: kbId,
          kb_name: kbName,
          file_id: fileId,
          file_name: fileName,
          api_readback: apiReadback,
          browser_writes: browserWrites.map(maskRequest),
          knowledge_base_request_count: knowledgeBaseRequests.length,
          knowledge_base_requests: knowledgeBaseRequests.map(maskRequest),
          deleted_via_ui: deletedViaUi,
          deleted_kb_files_guard: "verified",
          screenshots: {
            detail: DETAIL_SCREENSHOT_PATH,
            list: LIST_SCREENSHOT_PATH,
          },
          cleanup: cleanupEvidence,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    if (page) {
      await page
        .screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .catch(() => null);
      console.error(`failure_screenshot=${FAILURE_SCREENSHOT_PATH}`);
    }
    throw error;
  } finally {
    if (kbId && !deletedViaUi) {
      await auth.client
        .delete(apiPath(KB_PATH), { body: { kb_ids: [kbId] } })
        .then(() =>
          cleanupEvidence.push({
            cleanup: "delete browser-created knowledge base",
            status: "passed",
            kb_id: kbId,
          }),
        )
        .catch((error) =>
          cleanupEvidence.push({
            cleanup: "delete browser-created knowledge base",
            status: "failed",
            kb_id: kbId,
            error: error.message,
          }),
        );
    }
    if (browser) await browser.close();
    await rm(tmpDir, { recursive: true, force: true });
  }
}

async function assertApiReadback({ client, kbId, kbName, fileId, fileName }) {
  const tablePayload = await client.get(apiPath(KB_GET_PATH), {
    query: {
      search: kbName,
      page_number: 0,
      page_size: 10,
      sort: JSON.stringify([{ column_id: "name", type: "ascending" }]),
    },
  });
  const tableRows = payloadArray(tablePayload, "table_data");
  const tableRow = tableRows.find((row) => row?.id === kbId);
  assert(tableRow?.name === kbName, "KB table readback missed created row.");

  const optionsPayload = await client.get(apiPath(KB_LIST_PATH), {
    query: { search: kbName },
  });
  const optionsRows = payloadArray(optionsPayload, "table_data");
  assert(
    optionsRows.some((row) => row?.id === kbId && row?.name === kbName),
    "KB list readback missed created option.",
  );

  const filesPayload = await client.post(apiPath(KB_FILES_PATH), {
    kb_id: kbId,
    search: fileName,
    page_number: 0,
    page_size: 10,
    sort: [{ column_id: "name", type: "ascending" }],
  });
  const fileRows = payloadArray(filesPayload, "table_data");
  const fileRow = fileRows.find((row) => row?.id === fileId);
  assert(fileRow?.name === fileName, "KB files readback missed uploaded file.");

  return {
    table_rows_seen: tableRows.length,
    options_seen: optionsRows.length,
    files_seen: fileRows.length,
    files_uploaded: tableRow.files_uploaded ?? tableRow.filesUploaded ?? null,
    status: tableRow.status || null,
  };
}

async function deleteKnowledgeBaseFixturesByPrefix(client, prefix, evidence) {
  const payload = await client.get(apiPath(KB_GET_PATH), {
    query: {
      search: prefix,
      page_number: 0,
      page_size: 100,
    },
  });
  const rows = payloadArray(payload, "table_data").filter((row) =>
    String(row?.name || "").startsWith(prefix),
  );
  const kbIds = rows.map((row) => row.id).filter(Boolean);
  if (!kbIds.length) {
    evidence.push({
      cleanup: "pre-clean stale Knowledge Base lifecycle fixtures",
      status: "passed",
      deleted_count: 0,
    });
    return;
  }
  await client.delete(apiPath(KB_PATH), { body: { kb_ids: kbIds } });
  evidence.push({
    cleanup: "pre-clean stale Knowledge Base lifecycle fixtures",
    status: "passed",
    deleted_count: kbIds.length,
  });
}

async function waitForKnowledgeBaseGone(client, { kbId, kbName }) {
  await waitForCondition(async () => {
    const payload = await client.get(apiPath(KB_GET_PATH), {
      query: {
        search: kbName,
        page_number: 0,
        page_size: 10,
      },
    });
    return !payloadArray(payload, "table_data").some((row) => row?.id === kbId);
  }, "Knowledge Base row was still visible after UI delete.");
}

async function expectApiErrorStatus(fn, expectedStatus, label) {
  try {
    await fn();
  } catch (error) {
    if (error.status === expectedStatus) return;
    throw new Error(
      `${label} Expected HTTP ${expectedStatus}, got ${error.status || "unknown"}: ${
        error.message
      }`,
    );
  }
  throw new Error(`${label} Expected HTTP ${expectedStatus}, got success.`);
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

async function setInputByPlaceholder(page, placeholder, value) {
  await page.waitForSelector(`input[placeholder="${placeholder}"]`, {
    timeout: 30000,
  });
  const updated = await page.evaluate(
    ({ expectedPlaceholder, nextValue }) => {
      const input = Array.from(document.querySelectorAll("input")).find(
        (candidate) =>
          candidate.getAttribute("placeholder") === expectedPlaceholder,
      );
      if (!input) return false;
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      setter?.call(input, nextValue);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    },
    { expectedPlaceholder: placeholder, nextValue: value },
  );
  assert(updated, `Could not set input with placeholder ${placeholder}.`);
}

async function selectKnowledgeBaseRow(page, kbName) {
  await waitForVisibleText(page, kbName, { exact: true });
  const result = await page.evaluate((expectedName) => {
    const rows = window
      .visibleElements('[role="row"]')
      .filter((row) =>
        window.normalizeText(row.textContent).includes(expectedName),
      );
    const row = rows.find((candidate) =>
      candidate.querySelector('input[type="checkbox"]'),
    );
    const checkbox = row?.querySelector('input[type="checkbox"]');
    if (!checkbox) {
      return {
        selected: false,
        row_count: rows.length,
        text: rows.map((candidate) =>
          window.normalizeText(candidate.textContent),
        ),
      };
    }
    window.dispatchClick(checkbox);
    return { selected: true, row_count: rows.length };
  }, kbName);
  assert(
    result.selected,
    `Could not select Knowledge Base row ${kbName}: ${JSON.stringify(result)}`,
  );
}

async function clickDialogButton(page, { buttonText, dialogText }) {
  await waitForVisibleText(page, dialogText, { exact: true });
  const clicked = await page.evaluate(
    ({ expectedButtonText, expectedDialogText }) => {
      const dialog = window
        .visibleElements(".MuiDialog-root,[role='dialog']")
        .find((candidate) =>
          window
            .normalizeText(candidate.textContent)
            .includes(expectedDialogText),
        );
      const button = Array.from(dialog?.querySelectorAll("button") || []).find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedButtonText &&
          !candidate.disabled,
      );
      if (!button) return false;
      window.dispatchClick(button);
      return true;
    },
    {
      expectedButtonText: buttonText,
      expectedDialogText: dialogText,
    },
  );
  assert(clicked, `Could not click ${buttonText} in ${dialogText} dialog.`);
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

async function waitForCondition(fn, timeoutMessage, timeoutMs = 30000) {
  const startedAt = Date.now();
  let lastError = null;
  while (Date.now() - startedAt < timeoutMs) {
    try {
      if (await fn()) return;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(
    lastError
      ? `${timeoutMessage} Last error: ${lastError.message}`
      : timeoutMessage,
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

function payloadArray(payload, key) {
  if (Array.isArray(payload?.[key])) return payload[key];
  const camelKey = key.replace(/_([a-z])/g, (_, letter) =>
    letter.toUpperCase(),
  );
  if (Array.isArray(payload?.[camelKey])) return payload[camelKey];
  return asArray(payload);
}

function isKnowledgeBaseApiUrl(rawUrl) {
  const url = new URL(rawUrl);
  return (
    url.origin ===
      new URL(process.env.API_BASE || "http://localhost:8003").origin &&
    (url.pathname.startsWith(KB_PATH) ||
      url.pathname.startsWith("/model-hub/kb/"))
  );
}

function isStateChangingKnowledgeBaseRequest(method, rawUrl) {
  const path = new URL(rawUrl).pathname;
  if (method === "POST" && path === KB_PATH) return true;
  if (["PATCH", "PUT", "DELETE"].includes(method) && path.startsWith(KB_PATH)) {
    return true;
  }
  return false;
}

function isAllowedBrowserWrite(method, rawUrl) {
  const path = new URL(rawUrl).pathname;
  return (
    (method === "POST" && path === KB_PATH) ||
    (method === "DELETE" && path === KB_PATH)
  );
}

function isEntitlementOrPlanLimit(response, payload) {
  const status = response.status();
  const statusCode = payload?.statusCode || payload?.result?.statusCode;
  return [402, 429].includes(status) || [402, 429].includes(statusCode);
}

function apiMessage(payload) {
  if (typeof payload === "string") return payload;
  return (
    payload?.message ||
    payload?.detail ||
    payload?.result?.message ||
    payload?.result?.detail ||
    null
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
