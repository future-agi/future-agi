/* eslint-disable no-console */
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  unwrapApiData,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/gateway-keys-after-revoke-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/gateway-keys-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const keyName = `ui_gateway_key_${suffix}`;
  const updatedKeyName = `${keyName}_edited`;
  const keyOwner = `browser-smoke-${suffix}`;
  const updatedOwner = `${keyOwner}-edited`;
  const cleanupEvidence = [];
  const evidence = await preflightGateway(auth.client);
  const apiFailures = [];
  const pageErrors = [];
  const gatewayRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let caughtError = null;
  let createdKeyId = null;
  let rawKey = "";

  await cleanupDisposableKeys(auth.client, cleanupEvidence);

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
        if (!isAllowedApiKeyMutation(request.method(), url)) {
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
      "initial Gateway keys load",
      [
        (response) =>
          response.url().includes("/agentcc/gateways/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/agentcc/api-keys/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway/keys`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway/keys");

    for (const label of [
      "API Keys",
      "Create and manage API keys for gateway access",
      "Sync",
      "Create Key",
      "All",
      "Active",
      "Revoked",
      "Expired",
      "Name",
      "Key",
      "Status",
      "Owner",
      "Models",
      "Created",
      "Last Used",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    await clickVisibleText(page, "Create Key", { exact: true });
    await waitForVisibleText(page, "Create API Key", { exact: true });
    await setDialogInputByLabel(page, "Name", keyName);
    await setDialogInputByLabel(page, "Owner", keyOwner);

    const createResponse = await waitForResponseDuring(
      page,
      "create API key",
      (response) =>
        response.url().includes("/agentcc/api-keys/") &&
        response.request().method() === "POST" &&
        response.status() < 400,
      () => clickDialogButton(page, "Create"),
    );
    const createdKey = await responseResult(createResponse);
    createdKeyId = createdKey.id;
    rawKey = createdKey.key || "";
    assert(createdKeyId, "Create key response did not include an id.");
    assert(
      typeof rawKey === "string" && rawKey.length > 8,
      "Create key response did not include a one-time raw key.",
    );
    assert(
      createdKey.key_prefix || createdKey.keyPrefix,
      "Create key response did not include a key prefix.",
    );
    evidence.created_key = safeKeyEvidence(createdKey);

    await waitForVisibleText(page, "API Key Created", { exact: true });
    await waitForVisibleText(
      page,
      "Copy this key now. It will not be shown again.",
      { exact: true },
    );
    await waitForVisibleInputValue(page, rawKey);
    await clickDialogButton(page, "Done");
    await waitForNoVisibleText(page, rawKey);

    await setVisibleInputByPlaceholder(
      page,
      "Search by name or owner...",
      keyName,
    );
    await waitForVisibleText(page, keyName, { exact: true });
    if (createdKey.key_prefix || createdKey.keyPrefix) {
      await waitForVisibleText(
        page,
        `${createdKey.key_prefix || createdKey.keyPrefix}****`,
        { exact: true },
      );
    }

    await waitForResponsesDuring(
      page,
      "open API key detail",
      [
        (response) =>
          response.url().includes(`/agentcc/api-keys/${createdKeyId}/`) &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/agentcc/analytics/overview/") &&
          response
            .url()
            .includes(
              `api_key_id=${encodeURIComponent(
                createdKey.gateway_key_id || createdKey.gatewayKeyId,
              )}`,
            ) &&
          response.status() < 400,
      ],
      () => clickVisibleText(page, keyName, { exact: true }),
    );
    await waitForVisibleText(page, "Allowed Models", { exact: true });
    await waitForVisibleText(page, "All models", { exact: true });
    await waitForVisibleText(page, "Allowed Providers", { exact: true });
    await waitForVisibleText(page, "All providers", { exact: true });
    await waitForVisibleText(page, "Usage (Last 7 days)", { exact: true });
    await waitForVisibleText(page, "View Logs", { exact: true });
    await waitForVisibleText(page, "Revoke Key", { exact: true });

    await page.click('button[title="Edit"]');
    await waitForVisibleText(page, "Edit API Key", { exact: true });
    await setDialogInputByLabel(page, "Name", updatedKeyName);
    await setDialogInputByLabel(page, "Owner", updatedOwner);
    const updateResponse = await waitForResponseDuring(
      page,
      "update API key",
      (response) =>
        response.url().includes(`/agentcc/api-keys/${createdKeyId}/`) &&
        response.request().method() === "PATCH" &&
        response.status() < 400,
      () => clickDialogButton(page, "Save"),
    );
    const updatedKey = await responseResult(updateResponse);
    assert(
      updatedKey.name === updatedKeyName,
      "Updated key name did not save.",
    );
    assert(
      updatedKey.owner === updatedOwner,
      "Updated key owner did not save.",
    );
    evidence.updated_key = safeKeyEvidence(updatedKey);
    await waitForNoVisibleText(page, "Edit API Key", { exact: true });
    await waitForVisibleText(page, updatedKeyName, { exact: true });
    await waitForNoVisibleText(page, rawKey);

    const apiUpdatedKey = await auth.client.get(
      apiPath("/agentcc/api-keys/{id}/", { id: createdKeyId }),
    );
    assert(
      apiUpdatedKey.name === updatedKeyName,
      "API detail did not persist updated key name.",
    );
    assert(
      apiUpdatedKey.owner === updatedOwner,
      "API detail did not persist updated key owner.",
    );

    await clickVisibleText(page, "Revoke Key", { exact: true });
    await waitForVisibleText(page, "Revoke API Key?", { exact: true });
    await waitForVisibleText(page, updatedKeyName, { exact: false });
    const revokeResponse = await waitForResponseDuring(
      page,
      "revoke API key",
      (response) =>
        response.url().includes(`/agentcc/api-keys/${createdKeyId}/revoke/`) &&
        response.request().method() === "POST" &&
        response.status() < 400,
      () => clickDialogButton(page, "Revoke"),
    );
    const revokedKey = await responseResult(revokeResponse);
    assert(revokedKey.status === "revoked", "Revoked key status did not save.");
    evidence.revoked_key = safeKeyEvidence(revokedKey);
    await waitForNoVisibleText(page, "Revoke API Key?", { exact: true });
    await waitForNoVisibleText(page, "Usage (Last 7 days)", { exact: true });

    await clickVisibleText(page, "Revoked", { exact: true });
    await setVisibleInputByPlaceholder(
      page,
      "Search by name or owner...",
      updatedKeyName,
    );
    await waitForVisibleText(page, updatedKeyName, { exact: true });
    await waitForVisibleText(page, "revoked", { exact: true });
    await waitForNoVisibleText(page, rawKey);
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Unexpected Gateway key mutations: ${unexpectedMutations.join("; ")}`,
    );
  } catch (error) {
    caughtError = error;
    await page
      ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
  } finally {
    if (createdKeyId) {
      await auth.client
        .delete(apiPath("/agentcc/api-keys/{id}/", { id: createdKeyId }), {
          okStatuses: [200, 204, 404],
        })
        .then(() =>
          cleanupEvidence.push({
            cleanup: "created API key",
            id: createdKeyId,
            status: "passed",
          }),
        )
        .catch((error) =>
          cleanupEvidence.push({
            cleanup: "created API key",
            id: createdKeyId,
            status: "failed",
            error: error.message,
          }),
        );
    }
    if (browser) await browser.close();
  }

  const cleanupFailures = cleanupEvidence.filter(
    (item) => item.status === "failed",
  );

  if (caughtError || cleanupFailures.length > 0) {
    console.error(
      JSON.stringify(
        {
          status: "failed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence,
          cleanup: cleanupEvidence,
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
    if (caughtError) throw caughtError;
    throw new Error(
      `Gateway key cleanup failed: ${cleanupFailures
        .map((item) => item.error)
        .join("; ")}`,
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
        cleanup: cleanupEvidence,
        gateway_request_count: gatewayRequests.length,
        browser_mutations: browserMutations,
      },
      null,
      2,
    ),
  );
}

async function preflightGateway(client) {
  const gateways = asArray(await client.get(apiPath("/agentcc/gateways/")));
  assert(gateways.length > 0, "Gateway list returned no configured gateway.");
  const listedGateway = gateways[0];
  const gatewayId = listedGateway.id || "default";
  const apiKeys = asArray(
    await client.get(apiPath("/agentcc/api-keys/"), {
      query: { gateway_id: gatewayId },
    }),
  );

  return {
    gateway_id: gatewayId,
    gateway_name: listedGateway.name || "Agent Command Center Gateway",
    starting_api_key_count: apiKeys.length,
  };
}

async function cleanupDisposableKeys(client, evidence) {
  const apiKeys = asArray(await client.get(apiPath("/agentcc/api-keys/")));
  for (const key of apiKeys) {
    const name = String(key?.name || "");
    if (!name.startsWith("ui_gateway_key_")) continue;
    await client
      .delete(apiPath("/agentcc/api-keys/{id}/", { id: key.id }), {
        okStatuses: [200, 204, 404],
      })
      .then(() =>
        evidence.push({
          cleanup: "stale UI gateway key",
          id: key.id,
          name,
          status: "passed",
        }),
      )
      .catch((error) =>
        evidence.push({
          cleanup: "stale UI gateway key",
          id: key.id,
          name,
          status: "failed",
          error: error.message,
        }),
      );
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
    window.setNativeInputValue = (input, value) => {
      const prototype =
        input.tagName === "TEXTAREA"
          ? window.HTMLTextAreaElement.prototype
          : window.HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
      descriptor.set.call(input, value);
      input.dispatchEvent(
        new InputEvent("input", {
          bubbles: true,
          cancelable: true,
          inputType: "insertText",
          data: value,
        }),
      );
      input.dispatchEvent(new Event("change", { bubbles: true }));
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
      localStorage.removeItem("agentcc_getting_started_dismissed");
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

async function waitForVisibleInputValue(page, value, timeout = 30000) {
  await page.waitForFunction(
    (expectedValue) =>
      window
        .visibleElements("input,textarea")
        .some((element) => element.value === expectedValue),
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
      clickable.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
      );
      clickable.dispatchEvent(
        new MouseEvent("mouseup", { bubbles: true, cancelable: true }),
      );
      clickable.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
      return true;
    },
    { text, exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
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
    button.dispatchEvent(
      new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
    );
    button.dispatchEvent(
      new MouseEvent("mouseup", { bubbles: true, cancelable: true }),
    );
    button.dispatchEvent(
      new MouseEvent("click", { bubbles: true, cancelable: true }),
    );
    return true;
  }, label);
  assert(clicked, `Could not click dialog button: ${label}`);
}

async function setDialogInputByLabel(page, label, value, timeout = 30000) {
  await page.waitForFunction(
    (expectedLabel) =>
      window
        .visibleElements("[role='dialog'] label")
        .some((element) =>
          window.normalizeText(element.textContent).includes(expectedLabel),
        ),
    { timeout },
    label,
  );
  const changed = await page.evaluate(
    ({ expectedLabel, nextValue }) => {
      const dialog = window.visibleElements("[role='dialog']").at(-1);
      if (!dialog) return false;
      const labelElement = Array.from(dialog.querySelectorAll("label")).find(
        (element) =>
          window.normalizeText(element.textContent).includes(expectedLabel),
      );
      if (!labelElement) return false;
      const inputId = labelElement.getAttribute("for");
      const input = inputId
        ? dialog.querySelector(`#${CSS.escape(inputId)}`)
        : labelElement.parentElement?.querySelector("input,textarea");
      if (!input) return false;
      window.setNativeInputValue(input, nextValue);
      return true;
    },
    { expectedLabel: label, nextValue: value },
  );
  assert(changed, `Could not set dialog input: ${label}`);
}

async function setVisibleInputByPlaceholder(
  page,
  placeholder,
  value,
  timeout = 30000,
) {
  await page.waitForFunction(
    (expectedPlaceholder) =>
      window
        .visibleElements("input,textarea")
        .some((element) => element.placeholder === expectedPlaceholder),
    { timeout },
    placeholder,
  );
  const changed = await page.evaluate(
    ({ expectedPlaceholder, nextValue }) => {
      const input = window
        .visibleElements("input,textarea")
        .find((element) => element.placeholder === expectedPlaceholder);
      if (!input) return false;
      window.setNativeInputValue(input, nextValue);
      return true;
    },
    { expectedPlaceholder: placeholder, nextValue: value },
  );
  assert(changed, `Could not set input placeholder: ${placeholder}`);
}

async function responseResult(response) {
  const body = await response.json();
  return unwrapApiData(body);
}

function safeKeyEvidence(key) {
  return {
    id: key.id,
    gateway_key_id: key.gateway_key_id || key.gatewayKeyId,
    key_prefix: key.key_prefix || key.keyPrefix,
    name: key.name,
    owner: key.owner,
    status: key.status,
    allowed_models: key.allowed_models || key.allowedModels || [],
    allowed_providers: key.allowed_providers || key.allowedProviders || [],
    raw_key_length: key.key ? key.key.length : undefined,
  };
}

function isAllowedApiKeyMutation(method, rawUrl) {
  const url = new URL(rawUrl);
  if (!url.pathname.includes("/agentcc/api-keys/")) return false;
  if (method === "POST" && /\/agentcc\/api-keys\/?$/.test(url.pathname)) {
    return true;
  }
  if (
    method === "PATCH" &&
    /\/agentcc\/api-keys\/[^/]+\/?$/.test(url.pathname)
  ) {
    return true;
  }
  if (
    method === "POST" &&
    /\/agentcc\/api-keys\/[^/]+\/revoke\/?$/.test(url.pathname)
  ) {
    return true;
  }
  return false;
}

function isGatewayApiUrl(url) {
  return url.includes("/agentcc/");
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
