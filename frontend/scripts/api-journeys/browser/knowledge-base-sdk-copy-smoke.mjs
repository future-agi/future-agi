/* eslint-disable no-console */
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const KB_NAME_PREFIX = "ui_kb_sdk_copy_";
const SCREENSHOT_PATH = "/tmp/knowledge-base-sdk-copy-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/knowledge-base-sdk-copy-smoke-failure.png";
const SDK_COPY_ARIA_LABEL = "Copy";
const MUTATION_METHODS = new Set(["PATCH", "PUT", "DELETE"]);

async function main() {
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const createKbName = `${KB_NAME_PREFIX}${suffix}`;
  const apiFailures = [];
  const pageErrors = [];
  const knowledgeBaseRequests = [];
  const unexpectedWriteRequests = [];
  let browser = null;
  let page = null;

  const directCreatePayload = await auth.client.get(
    apiPath("/model-hub/knowledge-base/"),
    { query: { type: "create", name: createKbName } },
  );
  assertKnowledgeBaseSdkSnippet({
    code: directCreatePayload?.code,
    mode: "create",
    expectedNames: [createKbName],
  });

  const knowledgeBaseList = await auth.client.get(
    apiPath("/model-hub/knowledge-base/get/"),
    { query: { page_number: 0, page_size: 10 } },
  );
  const existingKb = asArray(knowledgeBaseList?.table_data)
    .filter((kb) => kb?.id && kb?.name)
    .sort((a, b) => String(a.name).localeCompare(String(b.name)))[0];
  assert(
    existingKb,
    "Knowledge Base SDK update browser smoke needs one existing KB row.",
  );

  const directUpdatePayload = await auth.client.get(
    apiPath("/model-hub/knowledge-base/"),
    {
      query: {
        type: "update",
        name: existingKb.name,
        kb_id: existingKb.id,
      },
    },
  );
  assertKnowledgeBaseSdkSnippet({
    code: directUpdatePayload?.code,
    mode: "update",
    expectedNames: [existingKb.name, "UPDATED_KB_NAME"],
  });

  try {
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
      if (!isKnowledgeBaseApiUrl(url)) return;
      const requestKey = `${request.method()} ${url}`;
      knowledgeBaseRequests.push(requestKey);
      if (
        MUTATION_METHODS.has(request.method()) ||
        isStateChangingKnowledgeBasePost(request.method(), url)
      ) {
        unexpectedWriteRequests.push(requestKey);
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isKnowledgeBaseApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    const createClipboard = await exerciseCreateSdkCopy({
      page,
      kbName: createKbName,
    });
    assert(
      createClipboard === directCreatePayload.code,
      "Create-mode browser copy did not match the direct SDK API snippet.",
    );
    assertKnowledgeBaseSdkSnippet({
      code: createClipboard,
      mode: "create browser copy",
      expectedNames: [createKbName],
    });

    const updateClipboard = await exerciseUpdateSdkCopy({
      page,
      kbId: existingKb.id,
      kbName: existingKb.name,
    });
    assert(
      updateClipboard === directUpdatePayload.code,
      "Update-mode browser copy did not match the direct SDK API snippet.",
    );
    assertKnowledgeBaseSdkSnippet({
      code: updateClipboard,
      mode: "update browser copy",
      expectedNames: [existingKb.name, "UPDATED_KB_NAME"],
    });

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      unexpectedWriteRequests.length === 0,
      `Knowledge Base SDK copy smoke fired write requests: ${unexpectedWriteRequests
        .map(maskRequest)
        .join(", ")}`,
    );
    assert(
      apiFailures.length === 0,
      `Knowledge Base API failures: ${apiFailures.join(", ")}`,
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
          create_kb_name: createKbName,
          update_kb_id: existingKb.id,
          update_kb_name: existingKb.name,
          copied: {
            create: summarizeSnippetCopy(createClipboard, [createKbName]),
            update: summarizeSnippetCopy(updateClipboard, [
              existingKb.name,
              "UPDATED_KB_NAME",
            ]),
          },
          knowledge_base_request_count: knowledgeBaseRequests.length,
          knowledge_base_requests: knowledgeBaseRequests.map(maskRequest),
          screenshot: SCREENSHOT_PATH,
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
    if (browser) await browser.close();
  }
}

async function exerciseCreateSdkCopy({ page, kbName }) {
  await waitForResponsesDuring(
    page,
    "Knowledge Base list load",
    [
      (response) =>
        response.url().includes("/model-hub/knowledge-base/get/") &&
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

  const createSdkResponse = await waitForResponseDuring(
    page,
    "Knowledge Base create SDK browser response",
    (response) => {
      if (
        !response.url().includes("/model-hub/knowledge-base/") ||
        response.request().method() !== "GET" ||
        response.status() >= 400
      ) {
        return false;
      }
      const url = new URL(response.url());
      return (
        url.searchParams.get("type") === "create" &&
        url.searchParams.get("name") === kbName
      );
    },
    () => setInputByPlaceholder(page, "Name", kbName),
  );
  await responseJson(createSdkResponse);

  await clickVisibleText(page, "Import from SDK", { exact: true });
  await waitForVisibleText(page, "YOUR_API_KEY", { exact: false });
  await waitForVisibleText(page, "YOUR_SECRET_KEY", { exact: false });
  await waitForVisibleText(page, kbName, { exact: false });
  return clickAndReadClipboard(page, {
    ariaLabel: SDK_COPY_ARIA_LABEL,
    scopeText: "Create knowledge base",
  });
}

async function exerciseUpdateSdkCopy({ page, kbId, kbName }) {
  await page.goto(`${APP_BASE}/dashboard/knowledge/${kbId}`, {
    waitUntil: "domcontentloaded",
  });
  await waitForPathIncludes(page, `/dashboard/knowledge/${kbId}`);
  await waitForVisibleText(page, "Add docs", { exact: true });

  const updateSdkResponse = await waitForResponseDuring(
    page,
    "Knowledge Base update SDK browser response",
    (response) => {
      if (
        !response.url().includes("/model-hub/knowledge-base/") ||
        response.request().method() !== "GET" ||
        response.status() >= 400
      ) {
        return false;
      }
      const url = new URL(response.url());
      return (
        url.searchParams.get("type") === "update" &&
        url.searchParams.get("name") === kbName
      );
    },
    () => clickEnabledButton(page, "Add docs"),
  );
  await responseJson(updateSdkResponse);

  await waitForVisibleText(page, "Add files", { exact: true });
  await clickVisibleText(page, "Import from SDK", { exact: true });
  await waitForVisibleText(page, "YOUR_API_KEY", { exact: false });
  await waitForVisibleText(page, "YOUR_SECRET_KEY", { exact: false });
  await waitForVisibleText(page, kbName, { exact: false });
  return clickAndReadClipboard(page, {
    ariaLabel: SDK_COPY_ARIA_LABEL,
    scopeText: "Add files",
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

      window.__knowledgeBaseSdkClipboardWrites = [];
      const clipboard = {
        writeText: async (text) => {
          window.__knowledgeBaseSdkClipboardWrites.push(String(text));
        },
        readText: async () =>
          window.__knowledgeBaseSdkClipboardWrites[
            window.__knowledgeBaseSdkClipboardWrites.length - 1
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

async function clickAndReadClipboard(page, { ariaLabel, scopeText }) {
  const previousWriteCount = await page.evaluate(
    () => window.__knowledgeBaseSdkClipboardWrites?.length || 0,
  );
  await clickButtonByAriaLabel(page, { ariaLabel, scopeText });
  await page.waitForFunction(
    (writeCount) =>
      (window.__knowledgeBaseSdkClipboardWrites?.length || 0) > writeCount,
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

function assertKnowledgeBaseSdkSnippet({ code, mode, expectedNames }) {
  assert(
    typeof code === "string" && code.length > 0,
    `${mode} SDK code empty.`,
  );
  assert(
    code.includes("YOUR_API_KEY") && code.includes("YOUR_SECRET_KEY"),
    `${mode} SDK code did not include placeholder credentials.`,
  );
  for (const expectedName of expectedNames) {
    assert(
      code.includes(expectedName),
      `${mode} SDK code did not include ${expectedName}.`,
    );
  }
  const rawCredentialPatterns = [
    /FI_API_KEY["'\]]*\s*[:=]\s*["'][0-9a-f]{32}["']/iu,
    /FI_SECRET_KEY["'\]]*\s*[:=]\s*["'][0-9a-f]{32}["']/iu,
    /fi_api_key\s*=\s*["'][0-9a-f]{32}["']/iu,
    /fi_secret_key\s*=\s*["'][0-9a-f]{32}["']/iu,
    /X-Api-Key:\s*[0-9a-f]{32}/iu,
    /X-Secret-Key:\s*[0-9a-f]{32}/iu,
  ];
  assert(
    !rawCredentialPatterns.some((pattern) => pattern.test(code)),
    `${mode} SDK code included a raw credential-looking value.`,
  );
}

function summarizeSnippetCopy(snippet, expectedNames) {
  return {
    length: snippet.length,
    has_api_placeholder: snippet.includes("YOUR_API_KEY"),
    has_secret_placeholder: snippet.includes("YOUR_SECRET_KEY"),
    expected_names_present: expectedNames.every((name) =>
      snippet.includes(name),
    ),
  };
}

function isKnowledgeBaseApiUrl(rawUrl) {
  const url = new URL(rawUrl);
  return (
    url.origin ===
      new URL(process.env.API_BASE || "http://localhost:8003").origin &&
    (url.pathname.startsWith("/model-hub/knowledge-base/") ||
      url.pathname.startsWith("/model-hub/kb/"))
  );
}

function isStateChangingKnowledgeBasePost(method, rawUrl) {
  if (method !== "POST") return false;
  const path = new URL(rawUrl).pathname;
  return path === "/model-hub/knowledge-base/";
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
