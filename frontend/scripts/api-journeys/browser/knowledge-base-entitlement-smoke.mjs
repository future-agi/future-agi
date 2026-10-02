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
const SCREENSHOT_PATH = "/tmp/knowledge-base-entitlement-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const legacyGate = await expectLegacyKnowledgeBaseGate(auth.client);
  const structuredList = await auth.client.get(apiPath("/model-hub/kb/"), {
    query: { page: 1, page_size: 5 },
  });
  const embeddingModels = asArray(
    await auth.client.get(apiPath("/model-hub/kb/supported-embedding-models")),
  );
  assert(
    embeddingModels.some((model) => model?.value),
    "Structured KB embedding model catalog returned no selectable models.",
  );

  const apiFailures = [];
  const pageErrors = [];
  const legacyResponses = [];
  const unexpectedMutations = [];

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  await page.setBypassServiceWorker(true);
  await installRuntimeConfig(page, auth);
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

  page.on("request", (request) => {
    const url = request.url();
    if (
      isLegacyKnowledgeBaseApiUrl(url) &&
      ["POST", "PATCH", "PUT", "DELETE"].includes(request.method())
    ) {
      unexpectedMutations.push(`${request.method()} ${url}`);
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (isLegacyKnowledgeBaseApiUrl(url)) {
      legacyResponses.push(`${response.status()} ${url}`);
      if (response.status() >= 500) {
        apiFailures.push(`${response.status()} ${url}`);
      }
      return;
    }
    if (isKnowledgeBaseApiUrl(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const listResponse = await waitForResponseDuring(
      page,
      "Knowledge Base legacy list entitlement response",
      (response) =>
        response.request().method() === "GET" &&
        response.url().includes("/model-hub/knowledge-base/get/") &&
        response.status() === 402,
      () =>
        page.goto(`${APP_BASE}/dashboard/knowledge`, {
          waitUntil: "domcontentloaded",
        }),
    );
    const listBody = await listResponse.json().catch(() => ({}));
    assert(
      listBody?.code === "ENTITLEMENT_DENIED" ||
        listBody?.type === "entitlement_error",
      `Knowledge Base list 402 body was not an entitlement error: ${JSON.stringify(
        listBody,
      )}`,
    );

    await expectVisibleText(page, "Create Knowledge Base");
    await expectVisibleText(page, "This feature requires an EE license key");
    await expectVisibleText(
      page,
      "Knowledge Base requires an EE license key",
    );
    await expectNoVisibleText(page, "Invalid Date");
    await expectNoVisibleText(page, "undefined");

    const createButtonDisabled = await page.evaluate(() => {
      const buttons = Array.from(document.querySelectorAll("button"));
      const button = buttons.find(
        (node) => node.textContent?.trim() === "Create Knowledge Base",
      );
      return Boolean(
        button?.disabled || button?.getAttribute("aria-disabled") === "true",
      );
    });
    assert(
      createButtonDisabled,
      "Create Knowledge Base button stayed enabled while the legacy KB API is entitlement-blocked.",
    );

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      apiFailures.length === 0,
      `Knowledge Base API failures: ${apiFailures.join("; ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Read-only Knowledge Base smoke fired mutations: ${unexpectedMutations.join(
        "; ",
      )}`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence: {
            mode: "legacy_entitlement_denied",
            legacy_preflight_status: legacyGate.status,
            legacy_response_count: legacyResponses.length,
            structured_kb_count: structuredList?.count ?? null,
            structured_embedding_model_count: embeddingModels.length,
            create_button_disabled: createButtonDisabled,
            screenshot: SCREENSHOT_PATH,
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await page
      .screenshot({
        path: SCREENSHOT_PATH.replace(/\.png$/, "-failure.png"),
        fullPage: true,
      })
      .catch(() => null);
    throw error;
  } finally {
    await browser.close();
  }
}

async function expectLegacyKnowledgeBaseGate(client) {
  try {
    await client.get(apiPath("/model-hub/knowledge-base/get/"), {
      query: { page_number: 0, page_size: 1 },
    });
  } catch (error) {
    assert(
      error?.status === 402,
      `Legacy Knowledge Base list returned unexpected status ${
        error?.status || error.message
      }.`,
    );
    const bodyText = JSON.stringify(error.body || {});
    assert(
      bodyText.includes("ENTITLEMENT_DENIED") ||
        bodyText.includes("knowledge_base"),
      `Legacy Knowledge Base 402 did not expose entitlement metadata: ${bodyText}`,
    );
    return { status: error.status, body: error.body };
  }
  throw new Error(
    "Legacy Knowledge Base list is entitlement-enabled; use the full KB browser lifecycle smoke instead of this OSS gate smoke.",
  );
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

async function expectVisibleText(page, text) {
  await page.waitForFunction(
    (expected) => document.body?.innerText?.includes(expected),
    { timeout: 30000 },
    text,
  );
}

async function expectNoVisibleText(page, text) {
  const found = await page.evaluate((expected) =>
    Boolean(document.body?.innerText?.includes(expected)),
  );
  assert(!found, `Unexpected visible text found: ${text}`);
}

function isKnowledgeBaseApiUrl(url) {
  return (
    url.includes("/model-hub/knowledge-base/") ||
    url.includes("/model-hub/kb/")
  );
}

function isLegacyKnowledgeBaseApiUrl(url) {
  return url.includes("/model-hub/knowledge-base/");
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  }
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  return "/usr/bin/google-chrome";
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
