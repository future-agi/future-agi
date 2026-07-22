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
const CONFIG_SCREENSHOT_PATH =
  "/tmp/gateway-guardrails-mutation-config-smoke.png";
const EDIT_SCREENSHOT_PATH = "/tmp/gateway-guardrails-mutation-edit-smoke.png";
const TEST_SCREENSHOT_PATH = "/tmp/gateway-guardrails-mutation-test-smoke.png";
const TOGGLE_SCREENSHOT_PATH =
  "/tmp/gateway-guardrails-mutation-toggle-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/gateway-guardrails-mutation-smoke-failure.png";
const GUARDRAIL_NAME = "keyword-blocklist";
const GUARDRAIL_LABEL = "Keyword Blocklist";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();

  const auth = await createAuthenticatedContext();
  const apiFailures = [];
  const expectedApiFailures = [];
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
    const preflight = await preflightGuardrailMutations(
      auth.client,
      auth.runId,
    );
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
        if (!isAllowedGuardrailMutation(request.method(), url)) {
          unexpectedMutations.push(mutation);
        }
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (!isGatewayApiUrl(url) || response.status() < 400) return;
      const failure = `${response.status()} ${url}`;
      if (isExpectedPlaygroundFailure(response)) {
        expectedApiFailures.push(failure);
        return;
      }
      apiFailures.push(failure);
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponsesDuring(
      page,
      "initial Gateway guardrails configuration load",
      [gatewayListResponse(), activeOrgConfigResponse()],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway/guardrails/configuration`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway/guardrails/configuration");
    await waitForVisibleText(page, "Rule-Based Checks", { exact: true });
    await waitForVisibleText(page, GUARDRAIL_LABEL, { exact: true });

    await clickButtonWithinText(page, GUARDRAIL_LABEL);
    await waitForVisibleText(page, "Configure: Keyword Blocklist", {
      exact: false,
    });
    await setDialogInputByLabel(page, "Blocked Keywords", evidence.keyword);
    await clickDialogButton(page, "Save");
    await waitForNoVisibleText(page, "Configure: Keyword Blocklist", {
      exact: false,
    });

    const [createConfigResponse] = await waitForResponsesDuring(
      page,
      "save keyword guardrail config through browser",
      [orgConfigCreateResponse()],
      () => clickVisibleButton(page, "Save & Activate"),
    );
    evidence.create_config_response =
      await responseResult(createConfigResponse);

    const configAfterCreate = await waitForGuardrailRule(auth.client, {
      name: GUARDRAIL_NAME,
      predicate: (rule) =>
        rule?.enabled === true &&
        rule?.action === "block" &&
        asArray(rule?.config?.words).includes(evidence.keyword),
    });
    evidence.created_rule = publicRuleEvidence(configAfterCreate);
    await waitForVisibleText(page, `Action: block`, { exact: true });
    await page.screenshot({ path: CONFIG_SCREENSHOT_PATH, fullPage: true });
    evidence.config_screenshot = CONFIG_SCREENSHOT_PATH;

    await clickVisibleText(page, "Overview", { exact: true });
    await waitForPath(page, "/dashboard/gateway/guardrails");
    await waitForVisibleText(page, GUARDRAIL_NAME, { exact: true });

    await clickTitleWithinText(page, GUARDRAIL_NAME, "Edit");
    await waitForVisibleText(page, `Edit Guardrail: ${GUARDRAIL_NAME}`, {
      exact: true,
    });
    await selectDialogOptionByLabel(page, "Action", "log");
    await selectDialogOptionByLabel(page, "Stage", "post");
    await setDialogInputByLabel(page, "Threshold", "0.65");

    const editResponse = await waitForResponseDuring(
      page,
      "edit keyword guardrail through browser",
      updateGuardrailResponse(evidence.gateway_id),
      () => clickDialogButton(page, "Save"),
    );
    evidence.edit_response = await responseResult(editResponse);

    const configAfterEdit = await waitForGuardrailRule(auth.client, {
      name: GUARDRAIL_NAME,
      predicate: (rule) =>
        rule?.action === "log" &&
        rule?.stage === "post" &&
        Number(rule?.threshold) === 0.65 &&
        asArray(rule?.config?.words).includes(evidence.keyword),
    });
    evidence.edited_rule = publicRuleEvidence(configAfterEdit);
    await waitForVisibleText(page, "log", { exact: true });
    await waitForVisibleText(page, "After LLM", { exact: true });
    await page.screenshot({ path: EDIT_SCREENSHOT_PATH, fullPage: true });
    evidence.edit_screenshot = EDIT_SCREENSHOT_PATH;

    await clickVisibleText(page, "Test", { exact: true });
    await waitForPath(page, "/dashboard/gateway/guardrails/playground");
    await clickVisibleText(page, "Safe prompt", { exact: true });
    assert(
      await isRunTestButtonEnabled(page),
      "Safe prompt chip did not enable the Run Test button.",
    );

    const playgroundResponse = await waitForResponseDuring(
      page,
      "run guardrail playground test through browser",
      testPlaygroundResponse(evidence.gateway_id),
      () => clickVisibleButton(page, "Run Test"),
    );
    evidence.playground_response_status = playgroundResponse.status();
    evidence.playground_response = await safeResponseJson(playgroundResponse);
    if (playgroundResponse.status() >= 400) {
      await waitForAnyVisibleText(page, [
        "Could not sync",
        "Gateway error",
        "No model specified",
        "Failed to test",
      ]);
      evidence.playground_mode = "expected_local_failure";
    } else {
      await waitForVisibleText(page, "Result", { exact: true });
      evidence.playground_mode = "success";
    }
    await page.screenshot({ path: TEST_SCREENSHOT_PATH, fullPage: true });
    evidence.test_screenshot = TEST_SCREENSHOT_PATH;

    await clickVisibleText(page, "Overview", { exact: true });
    await waitForPath(page, "/dashboard/gateway/guardrails");
    await waitForVisibleText(page, GUARDRAIL_NAME, { exact: true });
    const toggleResponse = await waitForResponseDuring(
      page,
      "toggle keyword guardrail through browser",
      toggleGuardrailResponse(evidence.gateway_id),
      () => clickSwitchWithinText(page, GUARDRAIL_NAME),
    );
    evidence.toggle_response = await responseResult(toggleResponse);

    const configAfterToggle = await waitForGuardrailRule(auth.client, {
      name: GUARDRAIL_NAME,
      predicate: (rule) =>
        rule?.enabled === false &&
        rule?.action === "log" &&
        asArray(rule?.config?.words).includes(evidence.keyword),
    });
    evidence.toggled_rule = publicRuleEvidence(configAfterToggle);
    await page.screenshot({ path: TOGGLE_SCREENSHOT_PATH, fullPage: true });
    evidence.toggle_screenshot = TOGGLE_SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Unexpected Gateway guardrail browser mutations: ${unexpectedMutations.join(
        "; ",
      )}`,
    );
    assert(
      browserMutations.length === 4,
      `Expected four guardrail browser mutations, saw ${browserMutations.length}: ${browserMutations.join(
        "; ",
      )}`,
    );
    evidence.browser_mutations = browserMutations;
    evidence.expected_api_failures = expectedApiFailures;
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
          expected_api_failures: expectedApiFailures,
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

async function preflightGuardrailMutations(client, runId) {
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
  const suffix = String(runId || Date.now())
    .replace(/[^a-z0-9]/gi, "_")
    .toLowerCase();

  return {
    evidence: {
      gateway_id: gatewayId,
      original_org_config_id: originalActiveConfig.id,
      original_org_config_version: originalActiveConfig.version,
      original_guardrail_count:
        extractGuardrailRules(originalActiveConfig).length,
      keyword: `ui_guardrail_keyword_${suffix}`,
    },
    cleanup: createOrgConfigRestorer({
      client,
      beforeConfigIds,
      originalActiveConfigId: originalActiveConfig.id,
    }),
  };
}

async function waitForGuardrailRule(client, { name, predicate }) {
  const started = Date.now();
  let lastRule = null;
  while (Date.now() - started < 30000) {
    const config = await client.get(apiPath("/agentcc/org-configs/active/"));
    lastRule = extractGuardrailRules(config).find((rule) => rule.name === name);
    if (lastRule && predicate(lastRule)) return lastRule;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(
    `Guardrail rule ${name} did not reach expected state: ${JSON.stringify(
      lastRule,
    )}`,
  );
}

function extractGuardrailRules(config) {
  const guardrails = config?.guardrails || {};
  const rules = guardrails.rules || [];
  if (Array.isArray(rules)) return rules;
  if (rules && typeof rules === "object") {
    return Object.entries(rules).map(([name, cfg]) => ({
      name,
      ...(cfg && typeof cfg === "object" ? cfg : {}),
    }));
  }
  return [];
}

function publicRuleEvidence(rule) {
  return {
    name: rule.name,
    enabled: rule.enabled,
    action: rule.action,
    stage: rule.stage,
    mode: rule.mode,
    threshold: rule.threshold,
    config: rule.config,
  };
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

async function waitForAnyVisibleText(page, texts, timeout = 30000) {
  await page.waitForFunction(
    (expectedTexts) =>
      window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return expectedTexts.some((text) => textContent.includes(text));
      }),
    { timeout },
    texts,
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

async function clickVisibleButton(page, label, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
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
  assert(clicked, `Could not click visible button: ${label}`);
}

async function clickButtonWithinText(page, text, timeout = 30000) {
  await waitForVisibleText(page, text, { exact: true, timeout });
  const clicked = await page.evaluate((expectedText) => {
    const textElements = window
      .visibleElements()
      .filter(
        (element) => window.normalizeText(element.textContent) === expectedText,
      );
    for (const element of textElements) {
      const container = element.closest(".MuiCard-root,tr") || element;
      const button = Array.from(container.querySelectorAll("button")).find(
        (candidate) => !candidate.disabled,
      );
      if (button) {
        window.dispatchClick(button);
        return true;
      }
    }
    return false;
  }, text);
  assert(clicked, `Could not click button within ${text}`);
}

async function clickSwitchWithinText(page, text, timeout = 30000) {
  await waitForVisibleText(page, text, { exact: true, timeout });
  const clicked = await page.evaluate((expectedText) => {
    const textElements = window
      .visibleElements()
      .filter(
        (element) => window.normalizeText(element.textContent) === expectedText,
      );
    for (const element of textElements) {
      const container = element.closest(".MuiCard-root,tr") || element;
      const switchInput = container?.querySelector(
        "input[type='checkbox']:not(:disabled)",
      );
      if (switchInput) {
        switchInput.click();
        return true;
      }
    }
    return false;
  }, text);
  assert(clicked, `Could not click switch within ${text}`);
}

async function clickTitleWithinText(page, text, title, timeout = 30000) {
  await waitForVisibleText(page, text, { exact: true, timeout });
  const clicked = await page.evaluate(
    ({ expectedText, expectedTitle }) => {
      const textElements = window
        .visibleElements()
        .filter(
          (element) =>
            window.normalizeText(element.textContent) === expectedText,
        );
      for (const element of textElements) {
        const container =
          element.closest(".MuiCard-root,tr,[role='dialog']") ||
          element.parentElement ||
          element;
        const button = Array.from(
          container.querySelectorAll("button[title]"),
        ).find(
          (candidate) => candidate.getAttribute("title") === expectedTitle,
        );
        if (button && !button.disabled) {
          window.dispatchClick(button);
          return true;
        }
      }
      return false;
    },
    { expectedText: text, expectedTitle: title },
  );
  assert(clicked, `Could not click ${title} within ${text}`);
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

async function selectDialogOptionByLabel(page, label, option, timeout = 30000) {
  await waitForVisibleText(page, label, { timeout });
  const opened = await page.evaluate((expectedLabel) => {
    const dialog = window.visibleElements("[role='dialog']").at(-1);
    if (!dialog) return false;
    const labelElement = Array.from(dialog.querySelectorAll("label")).find(
      (candidate) =>
        window
          .normalizeText(candidate.textContent)
          .replace(/\s*\*$/, "")
          .includes(expectedLabel),
    );
    const formControl =
      labelElement?.closest(".MuiFormControl-root") ||
      labelElement?.parentElement;
    const combobox = formControl?.querySelector('[role="combobox"]');
    if (!combobox) return false;
    window.dispatchClick(combobox);
    return true;
  }, label);
  assert(opened, `Could not open select for dialog label: ${label}`);

  await page.waitForFunction(
    (expectedOption) =>
      window
        .visibleElements('[role="option"], li')
        .some(
          (candidate) =>
            window.normalizeText(candidate.textContent).toLowerCase() ===
            expectedOption.toLowerCase(),
        ),
    { timeout },
    option,
  );
  const selected = await page.evaluate((expectedOption) => {
    const optionElement = window
      .visibleElements('[role="option"], li')
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent).toLowerCase() ===
          expectedOption.toLowerCase(),
      );
    if (!optionElement) return false;
    window.dispatchClick(optionElement);
    return true;
  }, option);
  assert(selected, `Could not select dialog option: ${option}`);
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

async function isRunTestButtonEnabled(page) {
  return page.evaluate(() => {
    const button = window
      .visibleElements("button")
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === "Run Test",
      );
    return Boolean(button && !button.disabled);
  });
}

async function responseResult(response) {
  const data = await response.json();
  return data?.result ?? data;
}

async function safeResponseJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function gatewayListResponse() {
  return (response) =>
    response.url().includes("/agentcc/gateways/") &&
    !response.url().includes("/config/") &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function activeOrgConfigResponse() {
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

function updateGuardrailResponse(gatewayId) {
  return (response) =>
    response
      .url()
      .includes(`/agentcc/gateways/${gatewayId}/update-guardrail/`) &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function toggleGuardrailResponse(gatewayId) {
  return (response) =>
    response
      .url()
      .includes(`/agentcc/gateways/${gatewayId}/toggle-guardrail/`) &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function testPlaygroundResponse(gatewayId) {
  return (response) =>
    response
      .url()
      .includes(`/agentcc/gateways/${gatewayId}/test-playground/`) &&
    response.request().method() === "POST";
}

function isGatewayApiUrl(url) {
  return url.includes("/agentcc/");
}

function isExpectedPlaygroundFailure(response) {
  return (
    response.status() === 400 &&
    response.request().method() === "POST" &&
    response.url().includes("/agentcc/gateways/") &&
    response.url().includes("/test-playground/")
  );
}

function isAllowedGuardrailMutation(method, rawUrl) {
  const path = new URL(rawUrl).pathname;
  return (
    (method === "POST" && /\/agentcc\/org-configs\/?$/.test(path)) ||
    (method === "POST" &&
      /\/agentcc\/gateways\/[^/]+\/update-guardrail\/?$/.test(path)) ||
    (method === "POST" &&
      /\/agentcc\/gateways\/[^/]+\/toggle-guardrail\/?$/.test(path)) ||
    (method === "POST" &&
      /\/agentcc\/gateways\/[^/]+\/test-playground\/?$/.test(path))
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
