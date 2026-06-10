import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
  envFlag,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const ROUTE_MODE =
  process.env.AI_PROVIDERS_ROUTE_MODE === "global" ? "global" : "workspace";
const IS_GLOBAL_ROUTE = ROUTE_MODE === "global";
const SCREENSHOT_PREFIX = `/tmp/settings-${ROUTE_MODE}-ai-providers`;
const SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-smoke.png`;
const MUTATION_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-mutation-smoke.png`;
const DELETE_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-delete-smoke.png`;
const RUN_PROVIDER_KEY_MUTATION = envFlag("API_JOURNEY_MUTATIONS");
const SAFE_PROVIDER_KEY_CANDIDATES = [
  "palm",
  "perplexity",
  "ai21",
  "rime",
  "groq",
  "lmnt",
  "cartesia",
  "hume",
  "neuphonic",
  "inworld",
  "deepgram",
  "elevenlabs",
  "sagemaker",
];

async function main() {
  if (RUN_PROVIDER_KEY_MUTATION) requireMutations();
  const auth = await createAuthenticatedContext();
  const providerStatus = await auth.client.get(
    apiPath("/model-hub/develops/provider-status/"),
  );
  const providers = Array.isArray(providerStatus?.providers)
    ? providerStatus.providers
    : [];
  assert(providers.length > 0, "Provider status returned no providers.");
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
  const mutationProvider = RUN_PROVIDER_KEY_MUTATION
    ? chooseSafeUnconfiguredProvider(providers)
    : null;

  const customModelsRaw = await auth.client.get(
    apiPath("/model-hub/custom-models/"),
    {
      query: { page_number: 0, page_size: 20 },
      unwrap: false,
    },
  );

  const apiFailures = [];
  const pageErrors = [];
  const expectedMutations = [];
  let disposableProviderCreated = false;
  const evidence = {
    workspace_id: auth.workspaceId,
    provider_count: providers.length,
    configured_provider_count: providers.filter((provider) => provider?.has_key)
      .length,
    checked_provider: configuredTextProvider.provider,
    checked_provider_display_name: configuredTextProvider.display_name,
    checked_provider_masked_key: configuredTextProvider.masked_key,
    custom_model_count:
      customModelsRaw?.count ??
      customModelsRaw?.result?.count ??
      customModelsRaw?.results?.length ??
      0,
    provider_key_mutation_enabled: RUN_PROVIDER_KEY_MUTATION,
  };
  if (mutationProvider) {
    evidence.mutation_provider = mutationProvider.provider;
    evidence.mutation_provider_display_name = mutationProvider.display_name;
  }

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
  page.on("response", (response) => {
    const url = response.url();
    if (
      RUN_PROVIDER_KEY_MUTATION &&
      url.includes("/model-hub/api-keys/") &&
      ["POST", "DELETE"].includes(response.request().method()) &&
      response.status() < 400
    ) {
      expectedMutations.push(`${response.request().method()} ${url}`);
    }
    if (
      (url.includes("/model-hub/develops/provider-status/") ||
        url.includes("/model-hub/custom-models/") ||
        url.includes("/model-hub/api-keys/")) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const expectedPath = IS_GLOBAL_ROUTE
      ? "/dashboard/settings/ai-providers"
      : `/dashboard/settings/workspace/${auth.workspaceId}/ai-providers`;
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
    await page.goto(`${APP_BASE}${expectedPath}`, {
      waitUntil: "domcontentloaded",
    });
    await Promise.all([providerStatusResponse, customModelsResponse]);

    await assertCurrentPath(page, expectedPath);
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

    if (mutationProvider) {
      const rawKey = `api-journey-provider-key-${auth.runId}`;
      const updatedRawKey = `api-journey-provider-key-updated-${auth.runId}`;

      await filterProvider(page, mutationProvider.display_name);
      await waitForVisibleText(page, mutationProvider.display_name, {
        exact: true,
      });
      await fillVisibleProviderKeyInput(page, rawKey);
      const createResponse = page.waitForResponse(
        (response) =>
          response.url().includes("/model-hub/api-keys/") &&
          response.request().method() === "POST" &&
          response.status() < 400,
        { timeout: 60000 },
      );
      await clickVisibleButton(page, "Add");
      await createResponse;
      disposableProviderCreated = true;

      const createdKey = await waitForProviderKey(auth.client, {
        provider: mutationProvider.provider,
        absent: false,
      });
      assert(createdKey?.id, "Provider key create did not appear in API list.");
      assertProviderKeyPayloadMasked(createdKey, [rawKey, updatedRawKey]);
      evidence.created_provider_key_id = createdKey.id;

      await waitForProviderCardMaskedValue(page, rawKey);
      await clickProviderActionButton(page, mutationProvider.display_name, 0);
      await fillVisibleProviderKeyInput(page, updatedRawKey);
      const updateResponse = page.waitForResponse(
        (response) =>
          response.url().includes("/model-hub/api-keys/") &&
          response.request().method() === "POST" &&
          response.status() < 400,
        { timeout: 60000 },
      );
      await clickVisibleButton(page, "Save");
      await updateResponse;

      const updatedKey = await waitForProviderKey(auth.client, {
        provider: mutationProvider.provider,
        absent: false,
      });
      assert(
        updatedKey?.id === createdKey.id,
        "Provider key update changed the active provider-key id.",
      );
      assertProviderKeyPayloadMasked(updatedKey, [rawKey, updatedRawKey]);
      await page.screenshot({ path: MUTATION_SCREENSHOT_PATH, fullPage: true });
      evidence.mutation_screenshot = MUTATION_SCREENSHOT_PATH;

      await clickProviderActionButton(page, mutationProvider.display_name, 1);
      await waitForVisibleText(page, "Delete API key", { exact: true });
      const deleteResponse = page.waitForResponse(
        (response) =>
          response.url().includes(`/model-hub/api-keys/${createdKey.id}/`) &&
          response.request().method() === "DELETE" &&
          response.status() < 400,
        { timeout: 60000 },
      );
      await clickVisibleButton(page, "Delete");
      await deleteResponse;
      await waitForProviderKey(auth.client, {
        provider: mutationProvider.provider,
        absent: true,
      });
      await waitForVisibleButton(page, "Add");
      await page.screenshot({ path: DELETE_SCREENSHOT_PATH, fullPage: true });
      evidence.delete_screenshot = DELETE_SCREENSHOT_PATH;
      evidence.provider_key_create_visible = true;
      evidence.provider_key_update_visible = true;
      evidence.provider_key_delete_visible = true;
      evidence.expected_mutation_count = expectedMutations.length;
      disposableProviderCreated = false;
      await assertNoRawSecretVisible(page);
    }

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          route_mode: ROUTE_MODE,
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
    if (mutationProvider && disposableProviderCreated) {
      await deleteProviderKeyForProvider(
        auth.client,
        mutationProvider.provider,
      );
    }
  }
}

function chooseSafeUnconfiguredProvider(providers) {
  const provider = SAFE_PROVIDER_KEY_CANDIDATES.map((candidate) =>
    providers.find(
      (row) =>
        row?.provider === candidate &&
        row?.type === "text" &&
        row?.has_key === false,
    ),
  ).find(Boolean);
  assert(
    provider,
    "No safe unconfigured text provider is available for browser provider-key mutation coverage.",
  );
  return provider;
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
      return !Array.from(document.querySelectorAll("body *")).some(
        (element) => {
          if (!isVisible(element)) return false;
          const textContent = normalized(element.textContent);
          if (exactMatch) return textContent === expectedText;
          return textContent.includes(expectedText);
        },
      );
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

async function filterProvider(page, providerName) {
  const searchInput = await page.waitForSelector(
    'input[placeholder="Search AI Provider"]',
    { timeout: 30000 },
  );
  await searchInput.click({ clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.keyboard.type(providerName);
  await page.waitForFunction(
    (name) => document.body.innerText.includes(name),
    { timeout: 30000 },
    providerName,
  );
}

async function fillVisibleProviderKeyInput(page, value) {
  const inputHandle = await page.waitForFunction(
    () => {
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
      return Array.from(document.querySelectorAll("input, textarea")).find(
        (element) =>
          isVisible(element) &&
          !element.disabled &&
          element.placeholder !== "Search AI Provider",
      );
    },
    { timeout: 30000 },
  );
  const input = inputHandle.asElement();
  assert(input, "Provider key input was not found.");
  await input.click({ clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.keyboard.type(value);
}

async function clickVisibleButton(page, label) {
  const buttonHandle = await page.waitForFunction(
    (buttonLabel) => {
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
      return Array.from(document.querySelectorAll("button")).find(
        (button) =>
          isVisible(button) &&
          !button.disabled &&
          button.textContent.trim() === buttonLabel,
      );
    },
    { timeout: 30000 },
    label,
  );
  const button = buttonHandle.asElement();
  assert(button, `Visible button ${label} was not found.`);
  await button.click();
}

async function waitForVisibleButton(page, label) {
  await page.waitForFunction(
    (buttonLabel) => {
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
      return Array.from(document.querySelectorAll("button")).some(
        (button) =>
          isVisible(button) && button.textContent.trim() === buttonLabel,
      );
    },
    { timeout: 30000 },
    label,
  );
}

async function assertCurrentPath(page, expectedPath, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (path) => window.location.pathname === path,
    { timeout },
    expectedPath,
  );
}

async function clickProviderActionButton(page, providerName, actionIndex) {
  const buttonHandle = await page.waitForFunction(
    ({ name, index }) => {
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
      const label = Array.from(
        document.querySelectorAll("p, span, div, h1, h2, h3, h4, h5, h6"),
      ).find(
        (element) => isVisible(element) && element.textContent.trim() === name,
      );
      if (!label) return null;
      let current = label;
      for (let depth = 0; depth < 8 && current; depth += 1) {
        const buttons = Array.from(current.querySelectorAll("button")).filter(
          (button) => isVisible(button) && !button.disabled,
        );
        if (buttons.length > index) return buttons[index];
        current = current.parentElement;
      }
      return null;
    },
    { timeout: 30000 },
    { name: providerName, index: actionIndex },
  );
  const button = buttonHandle.asElement();
  assert(button, `Provider action button ${actionIndex} was not found.`);
  await button.click();
}

async function waitForProviderCardMaskedValue(page, forbiddenRawValue) {
  await page.waitForFunction(
    (rawValue) => {
      const inputs = Array.from(document.querySelectorAll("input, textarea"));
      return inputs.some(
        (input) =>
          input.disabled &&
          input.value &&
          input.value.includes("*") &&
          input.value !== rawValue,
      );
    },
    { timeout: 30000 },
    forbiddenRawValue,
  );
}

async function waitForProviderKey(
  client,
  { provider, absent = false, timeout = 30000 },
) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeout) {
    const keys = await listProviderKeys(client);
    const match = keys.find((key) => key.provider === provider);
    if (absent && !match) return null;
    if (!absent && match) return match;
    await new Promise((resolve) => setTimeout(resolve, 750));
  }
  throw new Error(
    absent
      ? `Provider key ${provider} was still present.`
      : `Provider key ${provider} did not appear.`,
  );
}

async function listProviderKeys(client) {
  const payload = await client.get(apiPath("/model-hub/api-keys/"), {
    query: { page: 1, page_size: 100 },
    unwrap: false,
  });
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.results)) return payload.results;
  if (Array.isArray(payload?.result?.results)) return payload.result.results;
  if (Array.isArray(payload?.result)) return payload.result;
  return [];
}

async function deleteProviderKeyForProvider(client, provider) {
  const keys = await listProviderKeys(client);
  const key = keys.find((candidate) => candidate.provider === provider);
  if (!key?.id) return;
  await client.delete(apiPath("/model-hub/api-keys/{id}/", { id: key.id }));
}

function assertProviderKeyPayloadMasked(payload, forbiddenValues) {
  const serialized = JSON.stringify(payload || {});
  for (const value of forbiddenValues) {
    assert(
      !serialized.includes(value),
      "Provider key API payload exposed raw browser-submitted key material.",
    );
  }
  assert(
    !Object.prototype.hasOwnProperty.call(payload || {}, "key"),
    "Provider key API payload exposed key field.",
  );
  assert(
    !Object.prototype.hasOwnProperty.call(payload || {}, "config_json"),
    "Provider key API payload exposed config_json field.",
  );
}

async function findInputState(page, expectedValue) {
  return page.evaluate((value) => {
    const element = Array.from(
      document.querySelectorAll("input, textarea"),
    ).find((candidate) => candidate.value === value);
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
  const rawSecretPatterns = [
    /sk-[A-Za-z0-9_-]{12,}/,
    /AIza[A-Za-z0-9_-]{20,}/,
    /AKIA[A-Z0-9]{16}/,
    /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
  ];
  for (const pattern of rawSecretPatterns) {
    assert(
      !pattern.test(visibleText),
      "Visible page text contains a raw provider secret pattern.",
    );
  }
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
