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
const SAVE_SCREENSHOT_PATH = "/tmp/gateway-budgets-mutation-save-smoke.png";
const REMOVE_SCREENSHOT_PATH = "/tmp/gateway-budgets-mutation-remove-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/gateway-budgets-mutation-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

const FIXED_BUDGET_LEVELS = [
  "per_model",
  "per_user",
  "per_key",
  "hard_limit",
  "org_limit",
];

const BUDGET_LEVEL_LABELS = {
  org_limit: "Organization",
  orgLimit: "Organization",
  hard_limit: "Hard Limit",
  hardLimit: "Hard Limit",
  per_key: "Per API Key",
  perKey: "Per API Key",
  per_user: "Per User",
  perUser: "Per User",
  per_model: "Per Model",
  perModel: "Per Model",
};

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
  let cleanupEvidence = null;
  let evidence = {};

  try {
    const preflight = await preflightBudgets(auth.client);
    evidence = preflight.evidence;
    cleanup = preflight.cleanup;

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
        if (!isAllowedBudgetMutation(request.method(), url)) {
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
      "initial Gateway budgets load",
      [
        gatewayListResponse(),
        gatewayConfigResponse(evidence.gateway_id),
        analyticsOverviewResponse(),
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway/budgets`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway/budgets");

    for (const label of [
      "Budgets",
      "Set spending limits and track cost utilization",
      "Set Budget",
      "Budget Dashboard",
      "Budget Config",
      "Budget Utilization",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    await clickVisibleText(page, "Set Budget", { exact: true });
    await waitForVisibleText(page, "Budget Level");
    await selectDialogOption(page, "Budget Level", evidence.budget_label);
    await setDialogInputByLabel(
      page,
      "Monthly Limit ($)",
      String(evidence.budget_limit),
    );
    await setDialogInputByLabel(
      page,
      "Alert Threshold (%)",
      String(evidence.alert_threshold),
    );
    await selectDialogOption(page, "When Limit Exceeded", "Block");

    const [setBudgetResponse] = await waitForResponsesDuring(
      page,
      "save budget through browser",
      [setBudgetMutationResponse(evidence.gateway_id), gatewayConfigResponse()],
      () => clickDialogButton(page, "Save"),
    );
    evidence.set_budget_response = await responseResult(setBudgetResponse);
    await waitForNoVisibleText(page, "Monthly Limit ($)");
    await waitForVisibleText(page, evidence.budget_label, { exact: true });
    await waitForVisibleText(page, evidence.formatted_budget_limit);
    await page.screenshot({ path: SAVE_SCREENSHOT_PATH, fullPage: true });
    evidence.save_screenshot = SAVE_SCREENSHOT_PATH;

    const savedConfig = await auth.client.get(
      apiPath("/agentcc/gateways/{id}/config/", { id: evidence.gateway_id }),
    );
    const savedBudget = savedConfig?.budgets?.[evidence.budget_level];
    assert(
      savedBudget?.limit === evidence.budget_limit &&
        savedBudget?.alert_threshold === evidence.alert_threshold &&
        savedBudget?.on_exceed === "block",
      `Saved budget config did not match the browser form: ${JSON.stringify(
        savedBudget,
      )}`,
    );
    evidence.saved_budget = {
      limit: savedBudget.limit,
      alert_threshold: savedBudget.alert_threshold,
      on_exceed: savedBudget.on_exceed,
    };

    await clickVisibleText(page, "Budget Config", { exact: true });
    await waitForPath(page, "/dashboard/gateway/budgets/config");
    for (const label of [
      evidence.budget_label,
      "block",
      "Limit",
      evidence.formatted_budget_limit,
      "Alert Threshold",
      `${evidence.alert_threshold}%`,
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    await clickVisibleText(page, "Budget Dashboard", { exact: true });
    await waitForPath(page, "/dashboard/gateway/budgets");
    await waitForVisibleText(page, evidence.budget_label, { exact: true });
    const [removeBudgetResponse] = await waitForResponsesDuring(
      page,
      "remove budget through browser",
      [
        removeBudgetMutationResponse(evidence.gateway_id),
        gatewayConfigResponse(),
      ],
      () => clickBudgetRemoveButton(page, evidence.budget_label),
    );
    evidence.remove_budget_response =
      await responseResult(removeBudgetResponse);
    await waitForNoVisibleText(page, evidence.formatted_budget_limit);
    await waitForNoVisibleText(page, evidence.budget_label, { exact: true });
    await page.screenshot({ path: REMOVE_SCREENSHOT_PATH, fullPage: true });
    evidence.remove_screenshot = REMOVE_SCREENSHOT_PATH;

    const removedConfig = await auth.client.get(
      apiPath("/agentcc/gateways/{id}/config/", { id: evidence.gateway_id }),
    );
    assert(
      !Object.prototype.hasOwnProperty.call(
        removedConfig?.budgets || {},
        evidence.budget_level,
      ),
      "Removed budget level was still present in gateway config.",
    );
    evidence.removed_budget_absent = true;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Unexpected Gateway budget browser mutations: ${unexpectedMutations.join(
        "; ",
      )}`,
    );
    assert(
      browserMutations.length === 2,
      `Expected two budget browser mutations, saw ${browserMutations.length}: ${browserMutations.join(
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
        cleanupEvidence = await cleanup();
        evidence.cleanup = cleanupEvidence;
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

async function preflightBudgets(client) {
  const gateways = asArray(await client.get(apiPath("/agentcc/gateways/")));
  assert(gateways.length > 0, "Gateway list returned no configured gateway.");
  const gatewayId =
    gateways.find((gateway) => gateway.id === "default")?.id ||
    gateways[0].id ||
    "default";
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
  const budgetLevel = chooseFixedBudgetLevel(originalActiveConfig.budgets);
  const budgetLabel = getBudgetLevelLabel(budgetLevel);
  const cleanup = createOrgConfigRestorer({
    client,
    beforeConfigIds,
    originalActiveConfigId: originalActiveConfig.id,
  });

  return {
    evidence: {
      gateway_id: gatewayId,
      original_org_config_id: originalActiveConfig.id,
      original_org_config_version: originalActiveConfig.version,
      budget_level: budgetLevel,
      budget_label: budgetLabel,
      budget_limit: 427,
      formatted_budget_limit: "$427.00",
      alert_threshold: 67,
      on_exceed: "block",
      original_budget_level_existed: Object.prototype.hasOwnProperty.call(
        originalActiveConfig.budgets || {},
        budgetLevel,
      ),
    },
    cleanup,
  };
}

function chooseFixedBudgetLevel(existingBudgets = {}) {
  const budgetKeys = existingBudgets || {};
  return (
    FIXED_BUDGET_LEVELS.find(
      (level) => !Object.prototype.hasOwnProperty.call(budgetKeys, level),
    ) || "per_model"
  );
}

function getBudgetLevelLabel(level) {
  return BUDGET_LEVEL_LABELS[level] || level;
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

async function waitForNoVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) =>
      !window.visibleElements().some((element) => {
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

async function setDialogInputByLabel(page, label, value, timeout = 30000) {
  await waitForVisibleText(page, label, { timeout });
  const updated = await page.evaluate(
    ({ label: expectedLabel, value: nextValue }) => {
      const dialog = window.visibleElements("[role='dialog']").at(-1);
      if (!dialog) return false;
      const labels = Array.from(dialog.querySelectorAll("label"));
      const labelElement = labels.find((candidate) =>
        window.normalizeText(candidate.textContent).includes(expectedLabel),
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
  assert(updated, `Could not set dialog input: ${label}`);
}

async function selectDialogOption(page, label, option, timeout = 30000) {
  await waitForVisibleText(page, label, { timeout });
  const opened = await page.evaluate((expectedLabel) => {
    const dialog = window.visibleElements("[role='dialog']").at(-1);
    if (!dialog) return false;
    const labels = Array.from(dialog.querySelectorAll("label"));
    const labelElement = labels.find((candidate) =>
      window.normalizeText(candidate.textContent).includes(expectedLabel),
    );
    const formControl =
      labelElement?.closest(".MuiFormControl-root") ||
      labelElement?.parentElement;
    const combo = formControl?.querySelector("[role='combobox']");
    if (!combo) return false;
    window.dispatchClick(combo);
    return true;
  }, label);
  assert(opened, `Could not open dialog select: ${label}`);
  await clickMenuOption(page, option, timeout);
}

async function clickMenuOption(page, label, timeout = 30000) {
  await page.waitForFunction(
    (expectedLabel) =>
      window
        .visibleElements("[role='option'], .MuiMenuItem-root")
        .some((element) =>
          window.normalizeText(element.textContent).startsWith(expectedLabel),
        ),
    { timeout },
    label,
  );
  const clicked = await page.evaluate((expectedLabel) => {
    const option = window
      .visibleElements("[role='option'], .MuiMenuItem-root")
      .find((element) =>
        window.normalizeText(element.textContent).startsWith(expectedLabel),
      );
    if (!option) return false;
    window.dispatchClick(option);
    return true;
  }, label);
  assert(clicked, `Could not click menu option: ${label}`);
}

async function clickDialogButton(page, label, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
  const clicked = await page.evaluate((expectedLabel) => {
    const dialog = window.visibleElements("[role='dialog']").at(-1);
    if (!dialog) return false;
    const button = Array.from(dialog.querySelectorAll("button")).find(
      (candidate) =>
        window.normalizeText(candidate.textContent) === expectedLabel &&
        !candidate.disabled,
    );
    if (!button) return false;
    window.dispatchClick(button);
    return true;
  }, label);
  assert(clicked, `Could not click dialog button: ${label}`);
}

async function clickBudgetRemoveButton(page, budgetLabel, timeout = 30000) {
  await waitForVisibleText(page, budgetLabel, { exact: true, timeout });
  const clicked = await page.evaluate((expectedLabel) => {
    const labelElement = window
      .visibleElements()
      .find(
        (element) =>
          window.normalizeText(element.textContent) === expectedLabel,
      );
    let container = labelElement;
    for (let depth = 0; container && depth < 8; depth += 1) {
      const removeButton = container.querySelector?.(
        'button[title="Remove budget"]',
      );
      if (removeButton && !removeButton.disabled) {
        window.dispatchClick(removeButton);
        return true;
      }
      container = container.parentElement;
    }
    return false;
  }, budgetLabel);
  assert(clicked, `Could not click remove button for budget: ${budgetLabel}`);
}

async function responseResult(response) {
  const data = await response.json();
  return data?.result ?? data;
}

function gatewayListResponse() {
  return (response) =>
    response.url().includes("/agentcc/gateways/") &&
    !response.url().includes("/config/") &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function gatewayConfigResponse(gatewayId = "") {
  return (response) =>
    response.url().includes("/agentcc/gateways/") &&
    response.url().includes("/config/") &&
    (!gatewayId ||
      response.url().includes(`/agentcc/gateways/${gatewayId}/`)) &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function analyticsOverviewResponse() {
  return (response) =>
    response.url().includes("/agentcc/analytics/overview/") &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function setBudgetMutationResponse(gatewayId) {
  return (response) =>
    response.url().includes(`/agentcc/gateways/${gatewayId}/set-budget/`) &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function removeBudgetMutationResponse(gatewayId) {
  return (response) =>
    response.url().includes(`/agentcc/gateways/${gatewayId}/remove-budget/`) &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function isGatewayApiUrl(url) {
  return url.includes("/agentcc/");
}

function isAllowedBudgetMutation(method, url) {
  return (
    method === "POST" &&
    (url.includes("/set-budget/") || url.includes("/remove-budget/"))
  );
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
