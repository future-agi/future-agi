import { execFile as execFileCallback } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  CleanupStack,
  apiPath,
  assert,
  createAuthenticatedContext,
  envFlag,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFileCallback);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/get-started-smoke.png";
const RUN_PROVIDER_FIXTURE = envFlag("API_JOURNEY_MUTATIONS");
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
  const auth = await createAuthenticatedContext();
  const cleanup = new CleanupStack();
  const cleanupEvidence = [];
  const rawSecrets = [];
  let cleanupRan = false;
  let browser = null;
  let caughtError = null;
  let deferredCleanupError = null;
  let fixture = null;

  try {
    const initialChecks = await auth.client.get(
      apiPath("/accounts/first-checks/"),
    );
    let checks = initialChecks;
    assertGetStartedChecks(checks);

    let providerStatus = await auth.client.get(
      apiPath("/model-hub/develops/provider-status/"),
    );
    let providers = Array.isArray(providerStatus?.providers)
      ? providerStatus.providers
      : [];
    assert(providers.length > 0, "Provider status returned no providers.");
    let configuredTextProvider = findConfiguredTextProvider(providers);
    if (!configuredTextProvider) {
      assert(
        RUN_PROVIDER_FIXTURE,
        "No configured text provider with a masked key is available for Get Started smoke. Set API_JOURNEY_MUTATIONS=1 to create a disposable provider key fixture.",
      );
      fixture = await createDisposableProviderKeyFixture({
        auth,
        providers,
        cleanup,
      });
      rawSecrets.push(fixture.rawSetupKey);

      checks = await auth.client.get(apiPath("/accounts/first-checks/"));
      assertGetStartedChecks(checks);
      providerStatus = await auth.client.get(
        apiPath("/model-hub/develops/provider-status/"),
      );
      providers = Array.isArray(providerStatus?.providers)
        ? providerStatus.providers
        : [];
      configuredTextProvider = findConfiguredTextProvider(providers);
    }
    assert(
      configuredTextProvider,
      "No configured text provider with a masked key is available for Get Started smoke.",
    );
    assertNoRawSecretString(
      JSON.stringify(providerStatus || {}),
      "provider status",
      rawSecrets,
    );

    const customModelsRaw = await auth.client.get(
      apiPath("/model-hub/custom-models/"),
      {
        query: { page_number: 0, page_size: 20 },
        unwrap: false,
      },
    );
    assertNoRawSecretString(
      JSON.stringify(customModelsRaw || {}),
      "custom models",
      rawSecrets,
    );

    const apiFailures = [];
    const pageErrors = [];
    const unexpectedMutations = [];
    const evidence = {
      first_checks: checks,
      first_checks_before_fixture: fixture ? initialChecks : undefined,
      provider_count: providers.length,
      configured_provider_count: providers.filter(
        (provider) => provider?.has_key,
      ).length,
      checked_provider: configuredTextProvider.provider,
      checked_provider_display_name: configuredTextProvider.display_name,
      checked_provider_masked_key: configuredTextProvider.masked_key,
      provider_fixture_enabled: RUN_PROVIDER_FIXTURE,
      provider_fixture_created: Boolean(fixture),
      provider_fixture_provider: fixture?.provider,
      provider_fixture_key_id: fixture?.keyId,
      provider_fixture_masked_key: fixture?.maskedKey,
      custom_model_count:
        customModelsRaw?.count ??
        customModelsRaw?.result?.count ??
        customModelsRaw?.results?.length ??
        0,
    };

    browser = await puppeteer.launch({
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
        isGetStartedApiUrl(url) &&
        ["POST", "PATCH", "PUT", "DELETE"].includes(request.method())
      ) {
        unexpectedMutations.push(`${request.method()} ${url}`);
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isGetStartedApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto(`${APP_BASE}/dashboard/get-started`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(
      () => window.location.pathname === "/dashboard/get-started",
      { timeout: 30000 },
    );

    await waitForGetStartedPage(page);
    await openAddKeysTab(page);
    evidence.add_keys_tab = await getGetStartedTab(page);
    await waitForVisibleText(page, configuredTextProvider.display_name, {
      exact: true,
    });
    await waitForInputValue(page, configuredTextProvider.masked_key);
    await assertNoRawSecretVisible(page, rawSecrets);
    await waitForNoVisibleText(page, "Invalid Date");
    evidence.provider_setup_next_tab = await advanceFromProviderSetupStep(page);

    evidence.dataset_navigation_path = await navigateFromGetStarted({
      page,
      buttonText: "Create dataset",
      expectedPath: "/dashboard/develop",
    });
    evidence.experiment_navigation_path = await navigateFromGetStarted({
      page,
      buttonText: "Start experiment",
      expectedPath: "/dashboard/prototype",
    });
    evidence.evaluate_navigation_path = await navigateFromGetStarted({
      page,
      buttonText: "Try evaluations",
      expectedPath: "/dashboard/evaluations",
    });
    evidence.observe_navigation_path = await navigateFromGetStarted({
      page,
      buttonText: "Go to observe",
      expectedPath: "/dashboard/observe",
      beforeClick: async () => {
        await clickVisibleText(page, "Setup observability in application");
        await waitForVisibleText(page, "Set observability in application", {
          exact: true,
        });
      },
    });
    await page.goto(`${APP_BASE}/dashboard/get-started`, {
      waitUntil: "domcontentloaded",
    });
    await waitForGetStartedPage(page);
    await assertNoRawSecretVisible(page, rawSecrets);
    await waitForNoVisibleText(page, "Invalid Date");

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Get Started smoke fired mutations: ${unexpectedMutations.join("; ")}`,
    );

    const cleanupFailures = await cleanup.run(cleanupEvidence);
    cleanupRan = true;
    evidence.cleanup = cleanupEvidence;
    evidence.provider_fixture_cleanup = fixture?.cleanupState;
    if (fixture) {
      const postCleanupChecks = await auth.client.get(
        apiPath("/accounts/first-checks/"),
      );
      assertGetStartedChecks(postCleanupChecks);
      const postCleanupStatus = await auth.client.get(
        apiPath("/model-hub/develops/provider-status/"),
      );
      assertNoRawSecretString(
        JSON.stringify(postCleanupStatus || {}),
        "provider status after disposable provider key cleanup",
        rawSecrets,
      );
      const postCleanupProviders = Array.isArray(postCleanupStatus?.providers)
        ? postCleanupStatus.providers
        : [];
      const postCleanupProvider = postCleanupProviders.find(
        (provider) => provider?.provider === fixture.provider,
      );
      evidence.first_checks_after_fixture_cleanup = postCleanupChecks;
      evidence.provider_fixture_has_key_after_cleanup = Boolean(
        postCleanupProvider?.has_key,
      );
      assert(
        !postCleanupProvider?.has_key,
        `Disposable provider ${fixture.provider} still reports has_key after cleanup.`,
      );
    }

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
    assert(
      cleanupFailures.length === 0,
      `Cleanup failures: ${cleanupFailures
        .map((failure) => `${failure.label}: ${failure.error}`)
        .join("; ")}`,
    );
  } catch (error) {
    caughtError = error;
    throw error;
  } finally {
    if (browser) await closeBrowser(browser);
    if (!cleanupRan) {
      const cleanupFailures = await cleanup.run(cleanupEvidence);
      if (cleanupFailures.length && !caughtError) {
        deferredCleanupError = new Error(
          `Cleanup failures: ${cleanupFailures
            .map((failure) => `${failure.label}: ${failure.error}`)
            .join("; ")}`,
        );
      }
    }
  }
  if (deferredCleanupError) throw deferredCleanupError;
}

function findConfiguredTextProvider(providers) {
  return providers.find(
    (provider) =>
      provider?.has_key &&
      provider?.type === "text" &&
      typeof provider?.masked_key === "string" &&
      provider.masked_key.includes("*"),
  );
}

async function createDisposableProviderKeyFixture({
  auth,
  providers,
  cleanup,
}) {
  const existingKeys = await listProviderKeys(auth.client);
  const existingProviders = new Set(
    existingKeys.map((key) => key.provider).filter(Boolean),
  );
  const provider = chooseSafeUnconfiguredProvider(providers, existingProviders);
  const rawSetupKey = `get-started-fixture-${auth.runId}`;
  const created = await auth.client.post(apiPath("/model-hub/api-keys/"), {
    provider: provider.provider,
    key: rawSetupKey,
  });
  assert(created?.id, "Get Started provider fixture create lacked an id.");

  const cleanupState = {};
  cleanup.defer("hard-delete Get Started provider key fixture", async () => {
    cleanupState.hard_delete = await hardDeleteProviderApiKeyFixtureDb({
      organizationId: auth.organizationId,
      keyId: created.id,
    });
  });
  cleanup.defer("delete Get Started provider key fixture", () =>
    ignoreNotFound(() =>
      auth.client.delete(
        apiPath("/model-hub/api-keys/{id}/", { id: created.id }),
      ),
    ),
  );
  assertProviderKeyResponseIsMaskedOnly(
    created,
    "Get Started provider fixture create",
    [rawSetupKey],
  );

  return {
    provider: provider.provider,
    displayName: provider.display_name,
    keyId: created.id,
    maskedKey: created.masked_actual_key,
    rawSetupKey,
    cleanupState,
  };
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

function chooseSafeUnconfiguredProvider(providers, existingProviders) {
  const provider =
    SAFE_PROVIDER_KEY_CANDIDATES.map((candidate) =>
      providers.find(
        (row) =>
          row?.provider === candidate &&
          row?.type === "text" &&
          row?.has_key === false &&
          !existingProviders.has(row.provider),
      ),
    ).find(Boolean) ||
    providers.find(
      (row) =>
        row?.provider &&
        row?.type === "text" &&
        row?.has_key === false &&
        !existingProviders.has(row.provider),
    );
  assert(
    provider,
    "No safe unconfigured text provider is available for Get Started provider fixture coverage.",
  );
  return provider;
}

function assertProviderKeyResponseIsMaskedOnly(
  payload,
  label,
  rawSecrets = [],
) {
  assertNoRawSecretString(JSON.stringify(payload ?? {}), label, rawSecrets);
  assert(
    typeof payload?.masked_actual_key === "string" &&
      payload.masked_actual_key.includes("*"),
    `${label} did not return a masked key.`,
  );
  assert(
    !Object.prototype.hasOwnProperty.call(payload ?? {}, "key") ||
      isEmptySecretBearingField(payload.key),
    `${label} exposed non-empty secret-bearing key field.`,
  );
  assert(
    !Object.prototype.hasOwnProperty.call(payload ?? {}, "config_json") ||
      isEmptySecretBearingField(payload.config_json),
    `${label} exposed non-empty secret-bearing config_json field.`,
  );
}

function isEmptySecretBearingField(value) {
  if (value == null || value === "") return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value).length === 0;
  return false;
}

async function hardDeleteProviderApiKeyFixtureDb({ organizationId, keyId }) {
  assert(
    isUuid(organizationId),
    "Provider fixture cleanup needs organization id.",
  );
  assert(isUuid(keyId), "Provider fixture cleanup needs key id.");
  const sql = `
WITH target_keys AS (
  SELECT id
  FROM model_hub_apikey
  WHERE id = ${sqlUuid(keyId)}
    AND organization_id = ${sqlUuid(organizationId)}
),
deleted_keys AS (
  DELETE FROM model_hub_apikey key
  USING target_keys target
  WHERE key.id = target.id
  RETURNING key.id
)
SELECT json_build_object(
  'deleted_key_count', (SELECT count(*) FROM deleted_keys),
  'remaining_key_count',
    (SELECT count(*) FROM target_keys) - (SELECT count(*) FROM deleted_keys)
);
`;
  const result = await runPostgresJson(sql);
  assert(
    Number(result.remaining_key_count) === 0,
    `Provider fixture cleanup left ${result.remaining_key_count} key rows.`,
  );
  return result;
}

async function runPostgresJson(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
  const user = process.env.API_JOURNEY_DB_USER || "user";
  const database = process.env.API_JOURNEY_DB_NAME || "tfc";
  const { stdout } = await execFileAsync(
    "docker",
    [
      "exec",
      "-i",
      container,
      "psql",
      "-U",
      user,
      "-d",
      database,
      "-AtX",
      "-c",
      sql,
    ],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const text = stdout.trim();
  assert(text, "Postgres provider fixture cleanup returned no JSON output.");
  return JSON.parse(text.split("\n").at(-1));
}

function sqlUuid(value) {
  assert(isUuid(value), `Expected UUID, got ${value}`);
  return `'${String(value).replaceAll("'", "''")}'::uuid`;
}

async function ignoreNotFound(fn) {
  try {
    return await fn();
  } catch (error) {
    const message = String(error?.message || "").toLowerCase();
    if (
      error?.status === 404 ||
      message.includes("not found") ||
      message.includes("does not exist")
    ) {
      return null;
    }
    throw error;
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

async function waitForGetStartedPage(page) {
  await page.waitForFunction(
    () => window.location.pathname === "/dashboard/get-started",
    { timeout: 30000 },
  );
  await waitForVisibleText(page, "Get Started with FutureAGI", { exact: true });
  await waitForVisibleText(page, "Initial Setup", { exact: true });
  await waitForVisibleText(page, "Add keys", { exact: true });
  await waitForVisibleText(page, "Create first dataset", { exact: true });
  await waitForVisibleText(page, "Create your first evaluation", {
    exact: true,
  });
  await waitForVisibleText(page, "Run your first experiment", { exact: true });
  await waitForVisibleText(page, "Setup observability in application", {
    exact: true,
  });
  await waitForVisibleText(page, "Try out our features", { exact: true });
  await waitForVisibleText(page, "Add dataset", { exact: true });
  await waitForVisibleText(page, "Experiment", { exact: true });
  await waitForVisibleText(page, "Evaluate", { exact: true });
}

async function openAddKeysTab(page) {
  await clickVisibleText(page, "Add keys");
  await waitForVisibleText(page, "Add Keys", { exact: true });
}

async function advanceFromProviderSetupStep(page) {
  await clickVisibleText(page, "Next");
  await waitForVisibleText(page, "Create your first dataset", { exact: true });
  const tab = await getGetStartedTab(page);
  assert(
    tab === "createFirstDataset",
    `Expected provider setup Next to land on createFirstDataset, got ${tab}.`,
  );
  return tab;
}

async function getGetStartedTab(page) {
  return page.evaluate(() =>
    new URLSearchParams(window.location.search).get("tab"),
  );
}

async function navigateFromGetStarted({
  page,
  buttonText,
  expectedPath,
  beforeClick,
}) {
  await page.goto(`${APP_BASE}/dashboard/get-started`, {
    waitUntil: "domcontentloaded",
  });
  await waitForGetStartedPage(page);
  if (beforeClick) await beforeClick();

  const navigation = page.waitForFunction(
    (path) => window.location.pathname === path,
    { timeout: 30000 },
    expectedPath,
  );
  await clickVisibleText(page, buttonText);
  await navigation;
  return page.evaluate(() => window.location.pathname);
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

async function clickVisibleText(page, text) {
  await page.waitForFunction(
    (expectedText) => {
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
      return Array.from(document.querySelectorAll("body *")).some(
        (element) =>
          isVisible(element) &&
          normalized(element.textContent) === expectedText,
      );
    },
    { timeout: 30000 },
    text,
  );
  const clicked = await page.evaluate((expectedText) => {
    const normalized = (value) => String(value || "").trim();
    const actionableSelector = "button,[role='button'],a,[role='tab']";
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
    const matches = Array.from(document.querySelectorAll("body *")).filter(
      (candidate) =>
        isVisible(candidate) &&
        normalized(candidate.textContent) === expectedText,
    );
    const element =
      matches.find((candidate) => candidate.matches(actionableSelector)) ||
      matches
        .map((candidate) => candidate.closest(actionableSelector))
        .find((candidate) => candidate && isVisible(candidate));
    const clickable = element?.closest(actionableSelector) || element;
    if (!clickable) return false;
    clickable.scrollIntoView({ block: "center", inline: "center" });
    clickable.click();
    return true;
  }, text);
  assert(clicked, `Could not click visible text ${text}.`);
}

async function assertNoRawSecretVisible(page, forbiddenRawValues = []) {
  const visibleText = await page.evaluate(() => {
    const inputValues = Array.from(document.querySelectorAll("input, textarea"))
      .map((element) => element.value || "")
      .join("\n");
    return `${document.body.innerText || ""}\n${inputValues}`;
  });
  assertNoRawSecretString(visibleText, "visible page text", forbiddenRawValues);
}

function assertNoRawSecretString(value, label, forbiddenRawValues = []) {
  const text = String(value ?? "");
  const rawSecretPatterns = [
    /sk-[A-Za-z0-9_-]{12,}/,
    /AIza[A-Za-z0-9_-]{20,}/,
    /AKIA[A-Z0-9]{16}/,
    /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
  ];
  for (const pattern of rawSecretPatterns) {
    assert(
      !pattern.test(text),
      `${label} contains a raw provider secret pattern.`,
    );
  }
  for (const rawValue of forbiddenRawValues.filter(Boolean)) {
    assert(
      !text.includes(rawValue),
      `${label} exposed raw provider key material.`,
    );
  }
  assert(
    !/"actual_key"|"actual_json"/.test(text),
    `${label} exposed decrypted key field names.`,
  );
}

function assertGetStartedChecks(checks) {
  for (const key of [
    "keys",
    "dataset",
    "evaluation",
    "experiment",
    "observe",
    "invite",
  ]) {
    assert(
      typeof checks?.[key] === "boolean",
      `first-checks omitted boolean ${key}.`,
    );
  }
}

function isGetStartedApiUrl(url) {
  return (
    url.includes("/accounts/first-checks/") ||
    url.includes("/model-hub/develops/provider-status/") ||
    url.includes("/model-hub/custom-models/")
  );
}

function browserExecutablePath() {
  return (
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  );
}

async function closeBrowser(browser) {
  let closed = false;
  try {
    await Promise.race([
      browser.close().then(() => {
        closed = true;
      }),
      delay(5000),
    ]);
  } finally {
    if (!closed) {
      const child = browser.process?.();
      if (child && !child.killed) child.kill("SIGKILL");
      try {
        browser.disconnect();
      } catch {
        // The browser may already be disconnected after killing the process.
      }
    }
  }
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
