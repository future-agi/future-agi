/* eslint-disable no-console */
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/eval-sdk-copy-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/eval-sdk-copy-smoke-failure.png";
const EVAL_PREFIX = "ui_eval_sdk_copy_";
const SDK_LANGUAGES = ["python", "javascript", "curl"];
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  const auth = await createAuthenticatedContext();
  const evalName = `${EVAL_PREFIX}${shortRunId(auth.runId)}`;
  const browserMutations = [];
  const apiFailures = [];
  const pageErrors = [];
  const consoleMessages = [];
  const failedRequests = [];
  let evalId = null;
  let browser = null;
  let caughtError = null;

  try {
    const created = await createCodeEval(auth.client, evalName);
    evalId = created.id;

    const sdkQuery = {
      template_id: evalId,
      model: "turing_large",
      mapping: JSON.stringify({}),
      error_localizer: false,
    };
    const directSdkPayload = await auth.client.get(
      apiPath("/model-hub/eval-sdk-code/"),
      { query: sdkQuery },
    );
    assertEvalSdkPayload({
      payload: directSdkPayload,
      evalId,
      evalName,
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

    const page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installBrowserState(page, auth);

    page.on("request", (request) => {
      const url = request.url();
      if (!isEvalApiUrl(url)) return;
      if (MUTATION_METHODS.has(request.method())) {
        browserMutations.push(`${request.method()} ${url}`);
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isEvalApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("console", (message) => {
      if (["error", "warning"].includes(message.type())) {
        consoleMessages.push(`${message.type()}: ${message.text()}`);
      }
    });
    page.on("requestfailed", (request) => {
      failedRequests.push(`${request.method()} ${request.url()}`);
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    const browserSdkResponse = await waitForEvalSdkTab(page, evalId);
    const browserSdkEnvelope = await responseJson(browserSdkResponse);
    const browserSdkPayload = browserSdkEnvelope?.result || browserSdkEnvelope;
    assertEvalSdkPayload({
      payload: browserSdkPayload,
      evalId,
      evalName,
      label: "browser API",
    });

    for (const language of SDK_LANGUAGES) {
      assert(
        browserSdkPayload[language] === directSdkPayload[language],
        `${language} browser SDK snippet differs from direct API snippet.`,
      );
    }

    await waitForVisibleText(page, "SDK Code", { exact: true });
    await waitForVisibleText(page, "placeholder credentials");

    const copiedSnippets = {};
    for (const language of SDK_LANGUAGES) {
      const label = languageLabel(language);
      await clickAriaButton(page, `Show ${label} Eval SDK Code`);
      copiedSnippets[language] = await clickAndReadClipboard(
        page,
        `Copy ${label} Eval SDK Code`,
      );
      assert(
        copiedSnippets[language] === directSdkPayload[language],
        `${label} clipboard did not match direct API snippet.`,
      );
      assertSnippetSafe({
        language,
        snippet: copiedSnippets[language],
        evalId,
        evalName,
      });
    }

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      browserMutations.length === 0,
      `Read-only SDK tab fired mutations: ${browserMutations
        .map(maskRequest)
        .join("; ")}`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          eval_id: evalId,
          eval_name: evalName,
          direct_sdk_keys: Object.keys(directSdkPayload).sort(),
          browser_sdk_keys: Object.keys(browserSdkPayload).sort(),
          copied: Object.fromEntries(
            Object.entries(copiedSnippets).map(([language, snippet]) => [
              language,
              summarizeSnippetCopy(snippet, { evalId, evalName }),
            ]),
          ),
          browser_mutations: browserMutations.map(maskRequest),
          screenshot: SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    caughtError = error;
    console.error(
      JSON.stringify(
        {
          status: "failed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          eval_id: evalId,
          eval_name: evalName,
          api_failures: apiFailures,
          page_errors: pageErrors,
          console_messages: consoleMessages.slice(-20),
          failed_requests: failedRequests,
          browser_mutations: browserMutations.map(maskRequest),
        },
        null,
        2,
      ),
    );
    if (browser) {
      const pages = await browser.pages();
      const page = pages[pages.length - 1];
      await page
        ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .catch(() => null);
      console.error(`failure_screenshot=${FAILURE_SCREENSHOT_PATH}`);
    }
  } finally {
    if (browser) await browser.close();
    await cleanupEvalTemplate(auth.client, evalId).catch((error) => {
      caughtError = appendCleanupError(caughtError, error);
    });
  }

  if (caughtError) throw caughtError;
}

async function createCodeEval(client, name) {
  const created = await client.post(
    apiPath("/model-hub/eval-templates/create-v2/"),
    {
      name,
      eval_type: "code",
      code: [
        "def evaluate(output=None, expected=None, **kwargs):",
        "    return True",
      ].join("\n"),
      code_language: "python",
      output_type: "pass_fail",
      pass_threshold: 0.5,
      description: "Eval SDK copy browser smoke.",
      tags: ["api-journey", "eval-sdk-copy-ui"],
    },
  );
  assert(isUuid(created?.id), "Code eval create did not return a UUID id.");
  return created;
}

async function cleanupEvalTemplate(client, templateId) {
  if (!templateId) return;
  await client.post(
    apiPath("/model-hub/eval-templates/bulk-delete/"),
    { template_ids: [templateId] },
    { okStatuses: [200, 404] },
  );
  console.error(`cleanup eval sdk copy template: ${templateId}`);
}

async function waitForEvalSdkTab(page, evalId) {
  const detailResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes(`/model-hub/eval-templates/${evalId}/detail/`) &&
      response.status() < 400,
    { timeout: 60000 },
  );
  const sdkResponsePromise = page.waitForResponse(
    (response) => {
      if (
        !response.url().includes("/model-hub/eval-sdk-code/") ||
        response.request().method() !== "GET" ||
        response.status() >= 400
      ) {
        return false;
      }
      const url = new URL(response.url());
      return url.searchParams.get("template_id") === evalId;
    },
    { timeout: 60000 },
  );
  await page.goto(`${APP_BASE}/dashboard/evaluations/${evalId}?tab=sdk_code`, {
    waitUntil: "domcontentloaded",
  });
  await detailResponsePromise;
  return sdkResponsePromise;
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

      const nativeClipboard = navigator.clipboard;
      window.__evalSdkClipboardText = "";
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: async (text) => {
            window.__evalSdkClipboardText = String(text ?? "");
            if (nativeClipboard?.writeText) {
              try {
                await nativeClipboard.writeText(text);
              } catch {
                // The recorder remains authoritative when local dev
                // permissions block the native clipboard.
              }
            }
          },
          readText: async () => {
            if (window.__evalSdkClipboardText) {
              return window.__evalSdkClipboardText;
            }
            if (nativeClipboard?.readText) {
              try {
                return await nativeClipboard.readText();
              } catch {
                return "";
              }
            }
            return "";
          },
        },
      });

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
        return Array.from(document.querySelectorAll(selector)).filter(
          isVisible,
        );
      };
    },
    {
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      return window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

async function clickAriaButton(page, ariaLabel, timeout = 30000) {
  await page.waitForFunction(
    (label) =>
      window
        .visibleElements("button")
        .some(
          (button) =>
            button.getAttribute("aria-label") === label && !button.disabled,
        ),
    { timeout },
    ariaLabel,
  );
  const clicked = await page.evaluate((label) => {
    const button = window
      .visibleElements("button")
      .find(
        (candidate) =>
          candidate.getAttribute("aria-label") === label && !candidate.disabled,
      );
    if (!button) return false;
    window.dispatchClick(button);
    return true;
  }, ariaLabel);
  assert(clicked, `Could not click aria button: ${ariaLabel}`);
}

async function clickAndReadClipboard(page, ariaLabel) {
  const previousText = await page
    .evaluate(() => navigator.clipboard.readText())
    .catch(() => "");
  await clickAriaButton(page, ariaLabel);
  await page.waitForFunction(
    async (previous) => {
      try {
        const text = await navigator.clipboard.readText();
        return Boolean(text) && text !== previous;
      } catch {
        return false;
      }
    },
    {},
    previousText,
  );
  return page.evaluate(() => navigator.clipboard.readText());
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

function assertEvalSdkPayload({ payload, evalId, evalName, label }) {
  for (const language of SDK_LANGUAGES) {
    assert(
      typeof payload?.[language] === "string" && payload[language].length > 0,
      `${label} SDK payload missing non-empty ${language}.`,
    );
    assertSnippetSafe({
      language,
      snippet: payload[language],
      evalId,
      evalName,
    });
  }
}

function assertSnippetSafe({ language, snippet, evalId, evalName }) {
  assert(snippet.length > 0, `${language} copied an empty SDK snippet.`);
  assert(
    snippet.includes("YOUR_API_KEY") && snippet.includes("YOUR_SECRET_KEY"),
    `${language} SDK snippet did not include placeholder credentials: ${snippetSummary(
      snippet,
      { evalId, evalName },
    )}`,
  );
  if (language === "python") {
    assert(
      snippet.includes(evalName),
      `${language} SDK snippet did not include the evaluation name.`,
    );
  } else {
    assert(
      snippet.includes(evalId),
      `${language} SDK snippet did not include the evaluation id.`,
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
    !rawCredentialPatterns.some((pattern) => pattern.test(snippet)),
    `${language} SDK snippet included a raw credential-looking value.`,
  );
}

function summarizeSnippetCopy(snippet, { evalId, evalName }) {
  const value = String(snippet || "");
  return {
    length: value.length,
    has_api_placeholder: value.includes("YOUR_API_KEY"),
    has_secret_placeholder: value.includes("YOUR_SECRET_KEY"),
    has_eval_id: value.includes(evalId),
    has_eval_name: value.includes(evalName),
  };
}

function snippetSummary(snippet, { evalId, evalName }) {
  const value = String(snippet || "");
  return JSON.stringify({
    length: value.length,
    has_api_placeholder: value.includes("YOUR_API_KEY"),
    has_secret_placeholder: value.includes("YOUR_SECRET_KEY"),
    has_eval_id: value.includes(evalId),
    has_eval_name: value.includes(evalName),
    preview: value
      .replace(/[0-9a-f]{32}/giu, "<redacted-hex>")
      .replace(new RegExp(evalId, "gu"), "<eval-id>")
      .replace(new RegExp(evalName, "gu"), "<eval-name>")
      .slice(0, 160),
  });
}

function languageLabel(language) {
  if (language === "javascript") return "JavaScript";
  if (language === "curl") return "cURL";
  return "Python";
}

function isEvalApiUrl(rawUrl) {
  const url = new URL(rawUrl);
  return (
    url.origin ===
      new URL(process.env.API_BASE || "http://localhost:8003").origin &&
    (url.pathname.startsWith("/model-hub/eval-templates/") ||
      url.pathname === "/model-hub/eval-sdk-code/")
  );
}

function maskRequest(rawRequest) {
  const [method, rawUrl] = rawRequest.split(" ");
  const url = new URL(rawUrl);
  return `${method} ${url.pathname}`;
}

function appendCleanupError(caughtError, cleanupError) {
  if (!caughtError) return cleanupError;
  caughtError.message = `${caughtError.message}; cleanup failed: ${cleanupError.message}`;
  return caughtError;
}

function shortRunId(runId) {
  return String(runId || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .slice(-8);
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
