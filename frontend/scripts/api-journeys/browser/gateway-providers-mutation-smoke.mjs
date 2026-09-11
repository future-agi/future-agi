/* eslint-disable no-console */
import { spawnSync } from "node:child_process";
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
const ADD_SCREENSHOT_PATH = "/tmp/gateway-providers-mutation-add-smoke.png";
const EDIT_SCREENSHOT_PATH = "/tmp/gateway-providers-mutation-edit-smoke.png";
const DELETE_SCREENSHOT_PATH =
  "/tmp/gateway-providers-mutation-delete-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/gateway-providers-mutation-smoke-failure.png";
const PROVIDER_NAME = "bedrock";
const PROVIDER_LABEL = "AWS Bedrock";
const MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);
let dbConnection = null;

async function main() {
  requireMutations();

  const auth = await createAuthenticatedContext();
  const apiFailures = [];
  const expectedApiFailures = [];
  const pageErrors = [];
  const gatewayRequests = [];
  const browserMutations = [];
  const providerMutations = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let caughtError = null;
  let cleanupError = null;
  let createdProviderId = null;
  const evidence = await preflightProviders(auth.client);

  try {
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
        if (isProviderConfigMutation(request.method(), url)) {
          providerMutations.push(mutation);
        }
        if (!isAllowedProviderMutation(request.method(), url)) {
          unexpectedMutations.push(mutation);
        }
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (!isGatewayApiUrl(url) || response.status() < 400) return;
      const failure = `${response.status()} ${url}`;
      if (isExpectedProviderFetchFailure(response)) {
        expectedApiFailures.push(failure);
        return;
      }
      apiFailures.push(failure);
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponsesDuring(
      page,
      "initial Gateway providers mutation load",
      [gatewayConfigResponse(), providerHealthResponse()],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway/providers/config`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway/providers/config");
    await waitForVisibleText(page, "Providers", { exact: true });
    await waitForVisibleText(page, "Provider Config", { exact: true });

    await clickVisibleButton(page, "Add Provider");
    await waitForVisibleText(page, "Add Provider", { exact: true });
    await selectMuiOptionByLabel(page, "Provider", PROVIDER_LABEL);
    await setInputByLabel(page, "AWS Access Key ID", "AKIAFAKEBROWSERTEST");
    await setInputByLabel(
      page,
      "AWS Secret Access Key",
      "fake-secret-for-browser-test",
    );
    await addFreeSoloModel(page, MODEL_ID);
    await setInputByLabel(page, "Timeout", "45s");
    await setInputByLabel(page, "Max Concurrent", "3");

    const addResponse = await waitForResponseDuring(
      page,
      "add disposable Gateway provider through browser",
      updateProviderResponse(),
      () => clickDialogButton(page, "Add Provider"),
    );
    evidence.add_response = await responseResult(addResponse);

    const configAfterAdd = await waitForProviderConfig(auth.client, {
      gatewayId: evidence.gateway_id,
      providerName: PROVIDER_NAME,
      shouldExist: true,
    });
    const providerAfterAdd = configAfterAdd.providers[PROVIDER_NAME];
    createdProviderId = providerAfterAdd.id;
    assert(createdProviderId, "Added provider config did not expose an id.");
    assert(
      providerAfterAdd.default_timeout === 45 &&
        providerAfterAdd.max_concurrent === 3 &&
        asArray(providerAfterAdd.models).includes(MODEL_ID),
      `Added provider readback mismatch: ${JSON.stringify(providerAfterAdd)}`,
    );
    evidence.added_provider = publicProviderEvidence(providerAfterAdd);
    await waitForVisibleText(page, PROVIDER_NAME, { exact: true });
    await waitForVisibleText(page, MODEL_ID, { exact: true });
    await waitForVisibleText(page, "45", { exact: true });
    await page.screenshot({ path: ADD_SCREENSHOT_PATH, fullPage: true });
    evidence.add_screenshot = ADD_SCREENSHOT_PATH;

    await clickTitleWithinText(page, PROVIDER_NAME, "Edit provider");
    await waitForVisibleText(page, "Edit Provider", { exact: true });
    await setInputByLabel(page, "Timeout", "55s");
    await setInputByLabel(page, "Max Concurrent", "4");
    const editResponse = await waitForResponseDuring(
      page,
      "edit disposable Gateway provider through browser",
      updateProviderResponse(),
      () => clickDialogButton(page, "Save Changes"),
    );
    evidence.edit_response = await responseResult(editResponse);

    const configAfterEdit = await waitForProviderConfig(auth.client, {
      gatewayId: evidence.gateway_id,
      providerName: PROVIDER_NAME,
      shouldExist: true,
    });
    const providerAfterEdit = configAfterEdit.providers[PROVIDER_NAME];
    assert(
      providerAfterEdit.id === createdProviderId &&
        providerAfterEdit.default_timeout === 55 &&
        providerAfterEdit.max_concurrent === 4,
      `Edited provider readback mismatch: ${JSON.stringify(providerAfterEdit)}`,
    );
    evidence.edited_provider = publicProviderEvidence(providerAfterEdit);
    await waitForVisibleText(page, "55", { exact: true });
    await waitForVisibleText(page, "4", { exact: true });
    await page.screenshot({ path: EDIT_SCREENSHOT_PATH, fullPage: true });
    evidence.edit_screenshot = EDIT_SCREENSHOT_PATH;

    await clickTitleWithinText(page, PROVIDER_NAME, "Remove provider");
    await waitForVisibleText(page, "Remove Provider", { exact: true });
    await setInputByLabel(
      page,
      `Type "${PROVIDER_NAME}" to confirm`,
      PROVIDER_NAME,
    );
    const removeResponse = await waitForResponseDuring(
      page,
      "remove disposable Gateway provider through browser",
      removeProviderResponse(),
      () => clickDialogButton(page, "Remove"),
    );
    evidence.remove_response = await responseResult(removeResponse);

    await waitForProviderConfig(auth.client, {
      gatewayId: evidence.gateway_id,
      providerName: PROVIDER_NAME,
      shouldExist: false,
    });
    await waitForNoVisibleText(page, PROVIDER_NAME, { exact: true });
    evidence.deleted_provider_absent = true;
    evidence.deleted_provider_db_audit =
      await loadProviderCredentialDeletedAudit(createdProviderId);
    await page.screenshot({ path: DELETE_SCREENSHOT_PATH, fullPage: true });
    evidence.delete_screenshot = DELETE_SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Unexpected Gateway provider browser mutations: ${unexpectedMutations.join(
        "; ",
      )}`,
    );
    assert(
      providerMutations.length === 3,
      `Expected three Gateway provider add/edit/delete mutations, saw ${providerMutations.length}: ${providerMutations.join(
        "; ",
      )}`,
    );
    evidence.browser_mutations = browserMutations;
    evidence.provider_mutations = providerMutations;
    evidence.expected_api_failures = expectedApiFailures;
  } catch (error) {
    caughtError = error;
    await page
      ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
  } finally {
    if (browser) await browser.close();
    try {
      evidence.cleanup = await cleanupDisposableProvider({
        client: auth.client,
        gatewayId: evidence.gateway_id,
        providerName: PROVIDER_NAME,
        providerId: createdProviderId,
      });
    } catch (error) {
      cleanupError = error;
      evidence.cleanup = { status: "failed", error: error.message };
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
          provider_mutations: providerMutations,
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
        provider_mutations: providerMutations,
      },
      null,
      2,
    ),
  );
}

async function preflightProviders(client) {
  const gateways = asArray(await client.get(apiPath("/agentcc/gateways/")));
  assert(gateways.length > 0, "Gateway list returned no configured gateway.");
  const gatewayId =
    gateways.find((gateway) => gateway.id === "default")?.id ||
    gateways[0].id ||
    "default";
  const config = await client.get(
    apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
  );
  assert(
    !config?.providers?.[PROVIDER_NAME],
    `${PROVIDER_NAME} is already configured; refusing to mutate an existing provider.`,
  );

  return {
    gateway_id: gatewayId,
    provider_count_before: Object.keys(config?.providers || {}).length,
  };
}

async function waitForProviderConfig(
  client,
  { gatewayId, providerName, shouldExist },
) {
  const started = Date.now();
  let lastConfig = null;
  while (Date.now() - started < 30000) {
    lastConfig = await client.get(
      apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
    );
    const exists = Boolean(lastConfig?.providers?.[providerName]);
    if (exists === shouldExist) return lastConfig;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(
    `Provider ${providerName} existence did not become ${shouldExist}: ${JSON.stringify(
      lastConfig?.providers?.[providerName],
    )}`,
  );
}

function publicProviderEvidence(providerConfig) {
  return {
    id: providerConfig.id,
    name: providerConfig.name,
    base_url: providerConfig.base_url,
    api_format: providerConfig.api_format,
    models: providerConfig.models,
    default_timeout: providerConfig.default_timeout,
    max_concurrent: providerConfig.max_concurrent,
    conn_pool_size: providerConfig.conn_pool_size,
  };
}

async function cleanupDisposableProvider({
  client,
  gatewayId,
  providerName,
  providerId,
}) {
  const cleanup = {
    status: "passed",
    api_removed: false,
    hard_deleted_provider_id: null,
  };

  const config = await client.get(
    apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
  );
  if (config?.providers?.[providerName]) {
    await client.post(
      apiPath("/agentcc/gateways/{id}/remove-provider/", { id: gatewayId }),
      { name: providerName },
    );
    cleanup.api_removed = true;
  }

  if (providerId) {
    cleanup.hard_deleted_provider_id =
      await hardDeleteProviderCredential(providerId);
  }

  const finalConfig = await client.get(
    apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
  );
  assert(
    !finalConfig?.providers?.[providerName],
    `Cleanup left provider active in Gateway config: ${JSON.stringify(
      finalConfig?.providers?.[providerName],
    )}`,
  );

  return cleanup;
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

async function clickDialogButton(page, label, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
  const clicked = await page.evaluate((expectedLabel) => {
    const dialog = window.visibleElements("[role='dialog']").slice(-1)[0];
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

async function selectMuiOptionByLabel(page, label, option, timeout = 30000) {
  await waitForVisibleText(page, label, { timeout });
  const opened = await page.evaluate((expectedLabel) => {
    const labelMatches = (element) =>
      window.normalizeText(element.textContent).replace(/\s*\*$/, "") ===
      expectedLabel;
    const labelElement = window
      .visibleElements("label")
      .find((candidate) => labelMatches(candidate));
    const formControl =
      labelElement?.closest(".MuiFormControl-root") ||
      labelElement?.parentElement;
    const combobox = formControl?.querySelector('[role="combobox"]');
    if (!combobox) return false;
    window.dispatchClick(combobox);
    return true;
  }, label);
  assert(opened, `Could not open select for label: ${label}`);

  await page.waitForFunction(
    (expectedOption) =>
      window
        .visibleElements('[role="option"], li')
        .some(
          (candidate) =>
            window.normalizeText(candidate.textContent) === expectedOption,
        ),
    { timeout },
    option,
  );
  const selected = await page.evaluate((expectedOption) => {
    const optionElement = window
      .visibleElements('[role="option"], li')
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedOption,
      );
    if (!optionElement) return false;
    window.dispatchClick(optionElement);
    return true;
  }, option);
  assert(selected, `Could not select option: ${option}`);
}

async function setInputByLabel(page, label, value, timeout = 30000) {
  await waitForVisibleText(page, label, { timeout });
  const updated = await page.evaluate(
    ({ label: expectedLabel, value: nextValue }) => {
      const labelMatches = (element) =>
        window.normalizeText(element.textContent).replace(/\s*\*$/, "") ===
        expectedLabel;
      const labelElement = window
        .visibleElements("label")
        .find((candidate) => labelMatches(candidate));
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

async function addFreeSoloModel(page, model, timeout = 30000) {
  await page.waitForFunction(
    () =>
      window
        .visibleElements("input")
        .some((input) =>
          String(input.getAttribute("placeholder") || "").includes(
            "Type or paste",
          ),
        ),
    { timeout },
  );
  const focused = await page.evaluate(() => {
    const input = window
      .visibleElements("input")
      .find((candidate) =>
        String(candidate.getAttribute("placeholder") || "").includes(
          "Type or paste",
        ),
      );
    if (!input) return false;
    input.focus();
    return true;
  });
  assert(focused, "Could not focus provider model autocomplete input.");
  await page.keyboard.type(model);
  await page.keyboard.press("Enter");
  await waitForVisibleText(page, model, { exact: true, timeout });
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

async function responseResult(response) {
  const data = await response.json();
  return data?.result ?? data;
}

function gatewayConfigResponse() {
  return (response) =>
    response.url().includes("/agentcc/gateways/default/config/") &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function providerHealthResponse() {
  return (response) =>
    response.url().includes("/agentcc/gateways/default/providers/") &&
    response.request().method() === "GET" &&
    response.status() < 400;
}

function updateProviderResponse() {
  return (response) =>
    response.url().endsWith("/agentcc/gateways/default/update-provider/") &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function removeProviderResponse() {
  return (response) =>
    response.url().endsWith("/agentcc/gateways/default/remove-provider/") &&
    response.request().method() === "POST" &&
    response.status() < 400;
}

function isProviderConfigMutation(method, rawUrl) {
  const path = new URL(rawUrl).pathname;
  return (
    method === "POST" &&
    (/\/agentcc\/gateways\/default\/update-provider\/?$/.test(path) ||
      /\/agentcc\/gateways\/default\/remove-provider\/?$/.test(path))
  );
}

function isAllowedProviderMutation(method, rawUrl) {
  const path = new URL(rawUrl).pathname;
  return (
    (method === "POST" &&
      /\/agentcc\/gateways\/default\/update-provider\/?$/.test(path)) ||
    (method === "POST" &&
      /\/agentcc\/gateways\/default\/remove-provider\/?$/.test(path)) ||
    (method === "POST" &&
      /\/agentcc\/provider-credentials\/fetch_models\/?$/.test(path))
  );
}

function isExpectedProviderFetchFailure(response) {
  return (
    response.request().method() === "POST" &&
    response.url().includes("/agentcc/provider-credentials/fetch_models/")
  );
}

function isGatewayApiUrl(url) {
  return url.includes("/agentcc/");
}

async function loadProviderCredentialDeletedAudit(providerId) {
  const rows = await runDb(`
    SELECT deleted::text || '|' || (deleted_at IS NOT NULL)::text
    FROM agentcc_provider_credential
    WHERE id = ${sqlString(providerId)}
  `);
  const [deleted, deletedAtSet] = String(rows[0] || "").split("|");
  return {
    provider_id: providerId,
    row_found: Boolean(rows[0]),
    deleted: deleted === "true",
    deleted_at_set: deletedAtSet === "true",
  };
}

async function hardDeleteProviderCredential(providerId) {
  const rows = await runDb(`
    DELETE FROM agentcc_provider_credential
    WHERE id = ${sqlString(providerId)}
    RETURNING id::text
  `);
  return rows[0] || null;
}

async function runDb(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
  const candidates = dbConnection ? [dbConnection] : databaseCandidates();
  const failures = [];

  for (const candidate of candidates) {
    const result = spawnSync(
      "docker",
      [
        "exec",
        container,
        "psql",
        "-U",
        candidate.user,
        "-d",
        candidate.database,
        "-At",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        sql,
      ],
      { encoding: "utf8" },
    );
    if (result.status === 0) {
      dbConnection = candidate;
      return result.stdout
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
    }
    failures.push(
      `${candidate.user}/${candidate.database}: ${result.stderr || result.stdout}`,
    );
  }

  throw new Error(`Database command failed: ${failures.join(" | ")}`);
}

function databaseCandidates() {
  if (process.env.API_JOURNEY_DB_USER || process.env.API_JOURNEY_DB_NAME) {
    return [
      {
        user: process.env.API_JOURNEY_DB_USER || "postgres",
        database: process.env.API_JOURNEY_DB_NAME || "postgres",
      },
    ];
  }

  const candidates = [
    { user: process.env.PG_USER, database: process.env.PG_DB },
    { user: "user", database: "tfc" },
    { user: "futureagi", database: "futureagi" },
    { user: "postgres", database: "postgres" },
  ];
  const seen = new Set();
  return candidates.filter((candidate) => {
    if (!candidate.user || !candidate.database) return false;
    const key = `${candidate.user}/${candidate.database}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function sqlString(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
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
