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
const ADD_SCREENSHOT_PATH = "/tmp/gateway-fallbacks-mutation-add-smoke.png";
const SAVE_SCREENSHOT_PATH = "/tmp/gateway-fallbacks-mutation-save-smoke.png";
const REMOVE_SCREENSHOT_PATH =
  "/tmp/gateway-fallbacks-mutation-remove-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/gateway-fallbacks-mutation-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();

  const auth = await createAuthenticatedContext();
  const apiFailures = [];
  const pageErrors = [];
  const gatewayRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let caughtError = null;
  let cleanupError = null;
  let cleanup = null;
  let evidence = {};

  try {
    const baseline = await prepareOrgConfigRestorer(auth.client);
    cleanup = baseline.cleanup;
    evidence = await preflightFallbacks(auth.client, baseline);

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
      if (!isGatewayApiUrl(url)) return;
      gatewayRequests.push(`${request.method()} ${url}`);
      if (MUTATION_METHODS.has(request.method())) {
        const mutation = `${request.method()} ${url}`;
        browserMutations.push(mutation);
        if (!isAllowedFallbackMutation(request.method(), url)) {
          unexpectedMutations.push(mutation);
        }
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isGatewayApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponsesDuring(
      page,
      "initial Gateway fallbacks mutation load",
      [
        gatewayListResponse(),
        orgConfigActiveResponse(),
        providerHealthResponse(evidence.gateway_id),
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway/fallbacks`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway/fallbacks");

    for (const label of [
      "Fallbacks & Reliability",
      "Model Fallback Chains",
      "Default Model",
      evidence.primary_model,
      "Add Fallback Chain",
      "Provider Failover",
      "Retry",
      "Circuit Breaker",
      "Model Timeouts",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    await waitForVisibleText(page, "No fallback chains configured.");

    await clickVisibleText(page, "Add Fallback Chain", { exact: true });
    await waitForVisibleText(page, "Primary", { exact: true });
    await waitForVisibleText(page, evidence.primary_model, { exact: true });
    await clickVisibleText(page, "Add Fallback", { exact: true });
    await waitForVisibleText(page, "#1", { exact: true });
    await selectFallbackChainModel(page, 1, evidence.fallback_model);
    await waitForVisibleText(page, evidence.fallback_model, { exact: true });
    await waitForVisibleText(page, "You have unsaved changes.");

    const [addResponse] = await waitForResponsesDuring(
      page,
      "save added fallback chain through browser",
      [orgConfigCreateResponse(), orgConfigActiveResponse()],
      () => clickSaveAndApply(page),
    );
    evidence.add_response = await responseResult(addResponse);

    const activeAfterAdd = await auth.client.get(
      apiPath("/agentcc/org-configs/active/"),
    );
    const gatewayAfterAdd = await auth.client.get(
      apiPath("/agentcc/gateways/{id}/config/", { id: evidence.gateway_id }),
    );
    assert(
      activeAfterAdd?.routing?.model_fallbacks?.[
        evidence.primary_model
      ]?.includes(evidence.fallback_model),
      `Active OrgConfig did not persist added fallback chain: ${JSON.stringify(
        activeAfterAdd?.routing?.model_fallbacks,
      )}`,
    );
    assert(
      gatewayAfterAdd?.routing?.model_fallbacks?.[
        evidence.primary_model
      ]?.includes(evidence.fallback_model),
      `Gateway config did not expose added fallback chain: ${JSON.stringify(
        gatewayAfterAdd?.routing?.model_fallbacks,
      )}`,
    );
    evidence.added_routing = {
      version: activeAfterAdd.version,
      fallback_chain:
        activeAfterAdd.routing.model_fallbacks[evidence.primary_model],
    };

    await page.screenshot({ path: ADD_SCREENSHOT_PATH, fullPage: true });
    evidence.add_screenshot = ADD_SCREENSHOT_PATH;

    await clickVisibleText(page, "Provider Failover", { exact: true });
    await setInputByLabel(page, "Max Attempts", String(evidence.max_attempts));
    await setInputByLabel(page, "Per-Attempt Timeout", evidence.timeout);
    await waitForVisibleText(page, "You have unsaved changes.");

    const [saveResponse] = await waitForResponsesDuring(
      page,
      "save edited fallback reliability config through browser",
      [orgConfigCreateResponse(), orgConfigActiveResponse()],
      () => clickSaveAndApply(page),
    );
    evidence.save_response = await responseResult(saveResponse);

    const activeAfterSave = await auth.client.get(
      apiPath("/agentcc/org-configs/active/"),
    );
    const gatewayAfterSave = await auth.client.get(
      apiPath("/agentcc/gateways/{id}/config/", { id: evidence.gateway_id }),
    );
    assert(
      activeAfterSave?.routing?.failover?.max_attempts ===
        evidence.max_attempts &&
        activeAfterSave?.routing?.failover?.per_attempt_timeout ===
          evidence.timeout,
      `Active OrgConfig did not persist fallback edits: ${JSON.stringify(
        activeAfterSave?.routing?.failover,
      )}`,
    );
    assert(
      gatewayAfterSave?.routing?.failover?.max_attempts ===
        evidence.max_attempts &&
        gatewayAfterSave?.routing?.model_fallbacks?.[
          evidence.primary_model
        ]?.includes(evidence.fallback_model),
      `Gateway config did not expose saved fallback edits: ${JSON.stringify(
        gatewayAfterSave?.routing,
      )}`,
    );
    evidence.saved_routing = {
      version: activeAfterSave.version,
      max_attempts: activeAfterSave.routing.failover.max_attempts,
      per_attempt_timeout: activeAfterSave.routing.failover.per_attempt_timeout,
      fallback_chain:
        activeAfterSave.routing.model_fallbacks[evidence.primary_model],
    };

    await waitForVisibleValue(page, String(evidence.max_attempts));
    await waitForVisibleValue(page, evidence.timeout);
    await page.screenshot({ path: SAVE_SCREENSHOT_PATH, fullPage: true });
    evidence.save_screenshot = SAVE_SCREENSHOT_PATH;

    await clickRemoveFallbackChain(page);
    await waitForVisibleText(page, "No fallback chains configured.");

    const [removeResponse] = await waitForResponsesDuring(
      page,
      "save removed fallback chain through browser",
      [orgConfigCreateResponse(), orgConfigActiveResponse()],
      () => clickSaveAndApply(page),
    );
    evidence.remove_response = await responseResult(removeResponse);

    const activeAfterRemove = await auth.client.get(
      apiPath("/agentcc/org-configs/active/"),
    );
    const gatewayAfterRemove = await auth.client.get(
      apiPath("/agentcc/gateways/{id}/config/", { id: evidence.gateway_id }),
    );
    assert(
      !activeAfterRemove?.routing?.model_fallbacks?.[evidence.primary_model],
      `Active OrgConfig still exposes removed fallback chain: ${JSON.stringify(
        activeAfterRemove?.routing?.model_fallbacks,
      )}`,
    );
    assert(
      !gatewayAfterRemove?.routing?.model_fallbacks?.[evidence.primary_model],
      `Gateway config still exposes removed fallback chain: ${JSON.stringify(
        gatewayAfterRemove?.routing?.model_fallbacks,
      )}`,
    );
    evidence.removed_routing = {
      version: activeAfterRemove.version,
      removed_primary_absent: true,
      remaining_chain_count: Object.keys(
        activeAfterRemove.routing?.model_fallbacks || {},
      ).length,
    };

    await page.screenshot({ path: REMOVE_SCREENSHOT_PATH, fullPage: true });
    evidence.remove_screenshot = REMOVE_SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Unexpected Gateway fallbacks browser mutations: ${unexpectedMutations.join(
        "; ",
      )}`,
    );
    assert(
      browserMutations.length === 3,
      `Expected three fallback browser mutations, saw ${browserMutations.length}: ${browserMutations.join(
        "; ",
      )}`,
    );
    evidence.browser_mutations = browserMutations;
  } catch (error) {
    caughtError = error;
    await page
      ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
  } finally {
    if (browser) await browser.close();
    if (cleanup) {
      try {
        evidence.cleanup = await cleanup();
      } catch (error) {
        cleanupError = error;
        evidence.cleanup = { status: "failed", error: error.message };
      }
    }
  }

  if (caughtError || cleanupError) {
    console.error(
      JSON.stringify(
        {
          status: "failed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence,
          api_failures: apiFailures,
          page_errors: pageErrors,
          gateway_requests: gatewayRequests,
          browser_mutations: browserMutations,
          unexpected_mutations: unexpectedMutations,
          failure_screenshot: FAILURE_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
    throw caughtError || cleanupError;
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
        gateway_request_count: gatewayRequests.length,
        browser_mutations: browserMutations,
      },
      null,
      2,
    ),
  );
}

async function preflightFallbacks(client, baseline) {
  const gateways = asArray(await client.get(apiPath("/agentcc/gateways/")));
  assert(gateways.length > 0, "Gateway list returned no configured gateway.");
  const gatewayId =
    gateways.find((gateway) => gateway.id === "default")?.id ||
    gateways[0].id ||
    "default";

  const providerHealth = await client.get(
    apiPath("/agentcc/gateways/{id}/providers/", { id: gatewayId }),
  );
  const models = providerModels(providerHealth?.providers);
  assert(
    models.length >= 2,
    "Gateway fallbacks mutation smoke requires at least two configured provider models.",
  );
  const [primaryModel, fallbackModel] = models;

  const updateResult = await client.post(
    apiPath("/agentcc/gateways/{id}/update-config/", { id: gatewayId }),
    {
      routing: {
        strategy: "fallback",
        fallback_enabled: true,
        default_model: primaryModel,
        model_fallbacks: {},
        failover: {
          enabled: true,
          max_attempts: 3,
          on_status_codes: [429, 500, 502, 503, 504],
          on_timeout: true,
          per_attempt_timeout: "30s",
        },
        retry: {
          enabled: true,
          max_retries: 2,
          initial_delay: "500ms",
          max_delay: "10s",
          multiplier: 2,
          on_status_codes: [429, 500, 502, 503],
          on_timeout: true,
        },
        circuit_breaker: {
          enabled: true,
          failure_threshold: 7,
          success_threshold: 2,
          cooldown: "45s",
          on_status_codes: [500, 502, 503, 504],
        },
        model_timeouts: {
          [primaryModel]: "90s",
          [fallbackModel]: "120s",
        },
      },
    },
  );

  const gatewayConfig = await client.get(
    apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
  );
  assert(
    gatewayConfig.routing?.fallback_enabled === true &&
      Object.keys(gatewayConfig.routing?.model_fallbacks || {}).length === 0,
    `Gateway config did not expose an empty disposable fallback chain state: ${JSON.stringify(
      gatewayConfig.routing?.model_fallbacks,
    )}`,
  );

  return {
    gateway_id: gatewayId,
    original_org_config_id: baseline.originalActiveConfig.id,
    original_org_config_version: baseline.originalActiveConfig.version,
    seed_update_config_version: updateResult.version,
    seed_update_config_gateway_synced: updateResult.gateway_synced,
    provider_count: normalizeProviderList(providerHealth?.providers).length,
    model_count: models.length,
    primary_model: primaryModel,
    fallback_model: fallbackModel,
    max_attempts: 4,
    timeout: "35s",
  };
}

async function prepareOrgConfigRestorer(client) {
  const originalActiveConfig = await client.get(
    apiPath("/agentcc/org-configs/active/"),
  );
  assert(
    originalActiveConfig?.id && originalActiveConfig?.is_active === true,
    "AgentCC active org config endpoint did not return an active baseline.",
  );
  const beforeConfigIds = new Set(
    collectionRows(await client.get(apiPath("/agentcc/org-configs/")))
      .map((config) => config?.id)
      .filter(Boolean),
  );

  return {
    originalActiveConfig,
    beforeConfigIds,
    cleanup: createOrgConfigRestorer({
      client,
      beforeConfigIds,
      originalActiveConfigId: originalActiveConfig.id,
    }),
  };
}

function providerModels(providers) {
  const models = new Set();
  for (const provider of normalizeProviderList(providers)) {
    for (const model of asArray(provider.models)) {
      if (model) models.add(String(model));
    }
  }
  return Array.from(models).sort();
}

function normalizeProviderList(providers) {
  if (Array.isArray(providers)) {
    return providers.map((provider) => ({
      ...provider,
      name: provider.name || provider.provider_name || provider.id,
    }));
  }
  if (providers && typeof providers === "object") {
    return Object.entries(providers).map(([name, info]) => ({
      name,
      ...(info && typeof info === "object" ? info : {}),
    }));
  }
  return [];
}

function collectionRows(value) {
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.results)) return value.results;
  if (Array.isArray(value?.data)) return value.data;
  return asArray(value);
}

function createOrgConfigRestorer({
  client,
  beforeConfigIds,
  originalActiveConfigId,
}) {
  let completed = false;

  return async () => {
    if (completed) return { status: "already-cleaned" };
    const restoreEvidence = {
      status: "passed",
      original_config_id: originalActiveConfigId,
      activated_original: false,
      deleted_config_ids: [],
      deleted_config_versions: [],
    };

    const activeConfig = await client.get(
      apiPath("/agentcc/org-configs/active/"),
    );
    if (activeConfig?.id !== originalActiveConfigId) {
      await client.post(
        apiPath("/agentcc/org-configs/{id}/activate/", {
          id: originalActiveConfigId,
        }),
        {},
      );
      restoreEvidence.activated_original = true;
    }

    const configs = collectionRows(
      await client.get(apiPath("/agentcc/org-configs/")),
    );
    const disposableConfigs = configs.filter(
      (config) =>
        config?.id &&
        config.id !== originalActiveConfigId &&
        !beforeConfigIds.has(config.id),
    );

    for (const config of disposableConfigs) {
      await ignoreNotFound(() =>
        client.delete(apiPath("/agentcc/org-configs/{id}/", { id: config.id })),
      );
      restoreEvidence.deleted_config_ids.push(config.id);
      restoreEvidence.deleted_config_versions.push(config.version);
    }

    const restoredActive = await client.get(
      apiPath("/agentcc/org-configs/active/"),
    );
    assert(
      restoredActive?.id === originalActiveConfigId,
      "AgentCC org config cleanup did not restore the original active config.",
    );

    completed = true;
    return restoreEvidence;
  };
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

async function installBrowserState(page, auth) {
  await page.evaluateOnNewDocument(() => {
    window.normalizeText = (value) => String(value || "").trim();
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

async function waitForPath(page, pathname, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname === expectedPath,
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

async function waitForVisibleValue(page, value, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (expectedValue) =>
      window.visibleElements().some((element) => {
        const textContent = window
          .normalizeText(element.textContent)
          .replace(/\s+/g, " ");
        if (
          textContent === expectedValue ||
          textContent === `${expectedValue} ×` ||
          textContent === `${expectedValue} x`
        ) {
          return true;
        }

        if (
          element instanceof HTMLInputElement &&
          element.value === expectedValue
        ) {
          return true;
        }

        const input = element.querySelector?.("input");
        return input?.value === expectedValue;
      }),
    { timeout },
    value,
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
        element?.closest("button,a,[role='button'],[role='menuitem'],tr") ||
        element;
      if (!clickable || clickable.disabled) return false;
      window.dispatchClick(clickable);
      return true;
    },
    { text, exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

async function setInputByLabel(page, label, value, timeout = 30000) {
  await waitForVisibleText(page, label, { timeout });
  const updated = await page.evaluate(
    ({ label: expectedLabel, value: nextValue }) => {
      const labels = window.visibleElements("label");
      const labelElement = labels.find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedLabel,
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

async function clickSaveAndApply(page, timeout = 30000) {
  await waitForVisibleText(page, "Save & Apply", { exact: true, timeout });
  const clicked = await page.evaluate(() => {
    const button = window
      .visibleElements("button")
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === "Save & Apply" &&
          !candidate.disabled,
      );
    if (!button) return false;
    window.dispatchClick(button);
    return true;
  });
  assert(clicked, "Could not click Save & Apply.");
}

async function selectFallbackChainModel(page, comboboxIndex, model) {
  await page.waitForFunction(
    (index) => {
      const candidates = window
        .visibleElements(".MuiCard-root")
        .filter(
          (card) =>
            card.textContent.includes("Primary") &&
            card.textContent.includes("Add Fallback"),
        );
      const chain = candidates.sort((a, b) => {
        const aRect = a.getBoundingClientRect();
        const bRect = b.getBoundingClientRect();
        return aRect.width * aRect.height - bRect.width * bRect.height;
      })[0];
      return (
        chain &&
        window
          .visibleElements('[role="combobox"]')
          .filter((combobox) => chain.contains(combobox)).length > index
      );
    },
    { timeout: 30000 },
    comboboxIndex,
  );

  const opened = await page.evaluate((index) => {
    const candidates = window
      .visibleElements(".MuiCard-root")
      .filter(
        (card) =>
          card.textContent.includes("Primary") &&
          card.textContent.includes("Add Fallback"),
      );
    const chain = candidates.sort((a, b) => {
      const aRect = a.getBoundingClientRect();
      const bRect = b.getBoundingClientRect();
      return aRect.width * aRect.height - bRect.width * bRect.height;
    })[0];
    if (!chain) return false;
    const comboboxes = window
      .visibleElements('[role="combobox"]')
      .filter((combobox) => chain.contains(combobox));
    const combobox = comboboxes[index];
    if (!combobox) return false;
    window.dispatchClick(combobox);
    return true;
  }, comboboxIndex);
  assert(
    opened,
    `Could not open fallback chain model select ${comboboxIndex}.`,
  );

  await page.waitForFunction(
    (expectedModel) =>
      window
        .visibleElements('[role="option"], li')
        .some(
          (option) =>
            window.normalizeText(option.textContent) === expectedModel &&
            option.getAttribute("aria-disabled") !== "true",
        ),
    { timeout: 30000 },
    model,
  );
  const selected = await page.evaluate((expectedModel) => {
    const option = window
      .visibleElements('[role="option"], li')
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedModel &&
          candidate.getAttribute("aria-disabled") !== "true",
      );
    if (!option) return false;
    window.dispatchClick(option);
    return true;
  }, model);
  assert(selected, `Could not select fallback chain model: ${model}`);
}

async function clickRemoveFallbackChain(page, timeout = 30000) {
  await page.waitForFunction(
    () =>
      window
        .visibleElements("button")
        .some(
          (button) =>
            button.getAttribute("aria-label") === "Remove this chain" &&
            !button.disabled,
        ),
    { timeout },
  );
  const clicked = await page.evaluate(() => {
    const button = window
      .visibleElements("button")
      .find(
        (candidate) =>
          candidate.getAttribute("aria-label") === "Remove this chain" &&
          !candidate.disabled,
      );
    if (!button) return false;
    window.dispatchClick(button);
    return true;
  });
  assert(clicked, "Could not click Remove this chain.");
}

async function responseResult(response) {
  const data = await response.json();
  return data?.result ?? data;
}

function gatewayListResponse() {
  return (response) =>
    response.url().includes("/agentcc/gateways/") &&
    !response.url().includes("/providers/") &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function orgConfigActiveResponse() {
  return (response) =>
    response.url().includes("/agentcc/org-configs/active/") &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function orgConfigCreateResponse() {
  return (response) =>
    response.url().endsWith("/agentcc/org-configs/") &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function providerHealthResponse(gatewayId) {
  return (response) =>
    response.url().includes(`/agentcc/gateways/${gatewayId}/providers/`) &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function isGatewayApiUrl(url) {
  return url.includes("/agentcc/");
}

function isAllowedFallbackMutation(method, url) {
  return method === "POST" && url.endsWith("/agentcc/org-configs/");
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
