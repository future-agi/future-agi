import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/settings-global-ai-providers-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const providerStatus = await auth.client.get(
    apiPath("/model-hub/develops/provider-status/"),
  );
  const providers = Array.isArray(providerStatus?.providers)
    ? providerStatus.providers
    : [];
  assert(providers.length > 0, "Provider status returned no providers.");
  assertNoRawSecretInPayload(providerStatus, "provider status");

  const configuredTextProvider = providers.find(
    (provider) =>
      provider?.has_key &&
      provider?.type === "text" &&
      typeof provider?.masked_key === "string" &&
      provider.masked_key.includes("*"),
  );
  assert(
    configuredTextProvider,
    "No configured text provider with a masked key is available for browser smoke.",
  );

  const customModelsRaw = await auth.client.get(
    apiPath("/model-hub/custom-models/"),
    {
      query: { page_number: 0, page_size: 20 },
      unwrap: false,
    },
  );
  assertNoRawSecretInPayload(customModelsRaw, "custom model list");

  const apiKeysRaw = await auth.client.get(apiPath("/model-hub/api-keys/"), {
    query: { page: 1, page_size: 20 },
    unwrap: false,
  });
  assertNoRawSecretInPayload(apiKeysRaw, "api key list");

  const apiFailures = [];
  const pageErrors = [];
  const unexpectedMutations = [];
  const evidence = {
    workspace_id: auth.workspaceId,
    provider_count: providers.length,
    configured_provider_count: providers.filter((provider) => provider?.has_key)
      .length,
    checked_provider: configuredTextProvider.provider,
    checked_provider_display_name: configuredTextProvider.display_name,
    checked_provider_masked_key: configuredTextProvider.masked_key,
    custom_model_count: getPayloadTotal(customModelsRaw),
    api_key_list_rows: getPayloadTotal(apiKeysRaw),
  };

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });

  const page = await browser.newPage();
  await installRuntimeConfig(page, auth);
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      if (organizationId) sessionStorage.setItem("organizationId", organizationId);
      if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
      if (user?.id) sessionStorage.setItem("futureagi-current-user-id", user.id);
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
      isProviderSettingsUrl(url) &&
      ["POST", "PATCH", "PUT", "DELETE"].includes(request.method())
    ) {
      unexpectedMutations.push(`${request.method()} ${url}`);
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (isProviderSettingsUrl(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const providerStatusResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/model-hub/develops/provider-status/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    const customModelsResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/model-hub/custom-models/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(`${APP_BASE}/dashboard/settings/ai-providers`, {
      waitUntil: "domcontentloaded",
    });
    await Promise.all([providerStatusResponse, customModelsResponse]);

    await page.waitForFunction(
      () => window.location.pathname.endsWith("/dashboard/settings/ai-providers"),
      { timeout: 30000 },
    );
    await waitForVisibleText(page, "AI Providers", { exact: true });
    await waitForVisibleText(page, "Manage your AI providers");
    await waitForVisibleText(page, "Create custom model", { exact: true });
    await waitForVisibleText(page, "Default model provider");
    await waitForVisibleText(page, "Default cloud providers");
    await waitForVisibleText(page, "Custom model");
    await waitForVisibleText(page, configuredTextProvider.display_name, {
      exact: true,
    });
    await waitForInputValue(page, configuredTextProvider.masked_key);

    const inputState = await findInputState(
      page,
      configuredTextProvider.masked_key,
    );
    assert(inputState.found, "Masked provider key input was not found.");
    assert(
      inputState.disabled || inputState.readOnly,
      "Configured provider masked key input was editable before entering edit mode.",
    );

    await assertNoRawSecretVisible(page);
    await waitForNoVisibleText(page, "No Models Added", { exact: true });
    await waitForNoVisibleText(page, "Invalid Date");
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Read-only global AI providers smoke fired mutations: ${unexpectedMutations.join("; ")}`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence,
        },
        null,
        2,
      ),
    );
  } finally {
    await browser.close();
  }
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

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
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
      return Array.from(document.querySelectorAll("body *")).some((element) => {
        if (!isVisible(element)) return false;
        const textContent = normalized(element.textContent);
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

async function waitForNoVisibleText(
  page,
  text,
  { exact = false, timeout = 10000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
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
      return !Array.from(document.querySelectorAll("body *")).some((element) => {
        if (!isVisible(element)) return false;
        const textContent = normalized(element.textContent);
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

async function waitForInputValue(page, expectedValue, timeout = 30000) {
  await page.waitForFunction(
    (value) =>
      Array.from(document.querySelectorAll("input, textarea")).some(
        (element) => element.value === value,
      ),
    { timeout },
    expectedValue,
  );
}

async function findInputState(page, expectedValue) {
  return page.evaluate((value) => {
    const element = Array.from(document.querySelectorAll("input, textarea")).find(
      (candidate) => candidate.value === value,
    );
    if (!element) return { found: false };
    return {
      found: true,
      disabled: Boolean(element.disabled),
      readOnly: Boolean(element.readOnly),
    };
  }, expectedValue);
}

async function assertNoRawSecretVisible(page) {
  const visibleText = await page.evaluate(() => {
    const inputValues = Array.from(document.querySelectorAll("input, textarea"))
      .map((element) => element.value || "")
      .join("\n");
    return `${document.body.innerText || ""}\n${inputValues}`;
  });
  assertNoRawSecretString(visibleText, "visible page text");
}

function assertNoRawSecretInPayload(payload, label) {
  assertNoRawSecretString(JSON.stringify(payload || {}), label);
}

function assertNoRawSecretString(value, label) {
  const rawSecretPatterns = [
    /sk-[A-Za-z0-9_-]{12,}/,
    /AIza[A-Za-z0-9_-]{20,}/,
    /AKIA[A-Z0-9]{16}/,
    /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
  ];
  for (const pattern of rawSecretPatterns) {
    assert(!pattern.test(value), `${label} contains a raw provider secret pattern.`);
  }
}

function getPayloadTotal(payload) {
  if (Number.isFinite(payload?.count)) return payload.count;
  if (Number.isFinite(payload?.result?.count)) return payload.result.count;
  if (Array.isArray(payload?.results)) return payload.results.length;
  if (Array.isArray(payload?.result?.results)) return payload.result.results.length;
  if (Array.isArray(payload)) return payload.length;
  return 0;
}

function isProviderSettingsUrl(url) {
  return (
    url.includes("/model-hub/develops/provider-status/") ||
    url.includes("/model-hub/custom-models/") ||
    url.includes("/model-hub/api-keys/")
  );
}

function browserExecutablePath() {
  return (
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  );
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
