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
const SCREENSHOT_PATH = "/tmp/gateway-providers-config-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/gateway-providers-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  const auth = await createAuthenticatedContext();
  const evidence = await preflightGateway(auth.client);
  const apiFailures = [];
  const expectedApiFailures = [];
  const providerFetchResponses = [];
  const pageErrors = [];
  const gatewayRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let caughtError = null;
  const sampleProvider = evidence.sample_provider;

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
      "initial Gateway providers load",
      [
        (response) =>
          response.url().includes("/agentcc/gateways/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/agentcc/gateways/default/config/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/agentcc/gateways/default/providers/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway/providers`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway/providers");

    for (const label of [
      "Providers",
      "Manage LLM providers, routing rules, and cache settings",
      "Provider Health",
      "Provider Config",
      "Routing",
      "Cache",
      "Provider",
      "Status",
      "Models",
      "Latency (P50)",
      "Error Rate",
      "Circuit Breaker",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    await waitForVisibleText(page, sampleProvider.health_display_name, {
      exact: true,
    });
    if (sampleProvider.models.length > 0) {
      await waitForVisibleText(page, sampleProvider.models[0], {
        exact: true,
      });
    }

    await clickVisibleText(page, "Provider Config", { exact: true });
    await waitForPath(page, "/dashboard/gateway/providers/config");
    await waitForVisibleText(page, sampleProvider.name, { exact: true });
    for (const label of [
      "Base URL",
      "API Format",
      "API Key",
      "Timeout",
      "Max Concurrent",
      "Connection Pool",
      "Models",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    for (const value of [
      sampleProvider.base_url,
      sampleProvider.api_format,
      sampleProvider.default_timeout,
      sampleProvider.max_concurrent,
      sampleProvider.conn_pool_size,
      sampleProvider.models[0],
    ]) {
      if (value !== undefined && value !== null && value !== "") {
        await waitForVisibleText(page, String(value), { exact: true });
      }
    }
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    const fetchResponse = await waitForResponseDuring(
      page,
      "fetch provider models in edit dialog",
      (response) =>
        response.url().includes("/agentcc/provider-credentials/fetch_models/") &&
        response.request().method() === "POST",
      () => clickTitleWithinText(page, sampleProvider.name, "Edit provider"),
    );
    providerFetchResponses.push(
      `${fetchResponse.status()} ${fetchResponse.url()}`,
    );
    await waitForVisibleText(page, "Edit Provider", { exact: true });
    await waitForVisibleText(page, "Provider Name");
    await waitForVisibleText(page, "API Format", { exact: true });
    await waitForVisibleText(page, "Models", { exact: true });
    await clickDialogButton(page, "Cancel");
    await waitForNoVisibleText(page, "Edit Provider", { exact: true });

    await clickVisibleText(page, "Routing", { exact: true });
    await waitForPath(page, "/dashboard/gateway/providers/routing");
    for (const label of [
      "Routing Strategy",
      "Default Strategy",
      "Failover Configuration",
      "Max Attempts",
      "Retry on Status Codes",
      "Rate Limiting",
      "Advanced Routing",
      "Configure Advanced Routing",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    await waitForResponseDuring(
      page,
      "cache analytics load",
      (response) =>
        response.url().includes("/agentcc/analytics/overview/") &&
        response.request().method() === "GET" &&
        response.status() < 400,
      () => clickVisibleText(page, "Cache", { exact: true }),
    );
    await waitForPath(page, "/dashboard/gateway/providers/cache");
    for (const label of [
      "Cache Configuration",
      "Configure Cache",
      "Enabled",
      "L1 Backend",
      "Default TTL",
      "Max Entries",
      "Semantic Cache",
      "Edge Cache",
      "Cache Performance (Last 7 days)",
      "Cache Hit Rate",
      "Total Requests",
      "Cache Hits",
      "Est. Savings",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Unexpected Gateway provider mutations: ${unexpectedMutations.join("; ")}`,
    );
    assert(
      providerFetchResponses.length > 0,
      "Provider edit dialog did not exercise fetch-model path.",
    );
  } catch (error) {
    caughtError = error;
    await page
      ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
  } finally {
    if (browser) await browser.close();
  }

  if (caughtError) {
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
          provider_fetch_responses: providerFetchResponses,
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
    throw caughtError;
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
        expected_api_failures: expectedApiFailures,
        provider_fetch_responses: providerFetchResponses,
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
  const [config, providerHealth] = await Promise.all([
    client.get(apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId })),
    client.get(apiPath("/agentcc/gateways/{id}/providers/", { id: gatewayId })),
  ]);
  const providerEntries = Object.entries(config?.providers || {}).map(
    ([name, providerConfig]) => ({
      name,
      health_display_name:
        providerConfig.display_name ||
        providerConfig.displayName ||
        name.charAt(0).toUpperCase() + name.slice(1),
      base_url: providerConfig.base_url ?? providerConfig.baseUrl ?? "",
      api_format: providerConfig.api_format ?? providerConfig.apiFormat ?? "",
      models: Array.isArray(providerConfig.models)
        ? providerConfig.models
        : [],
      default_timeout:
        providerConfig.default_timeout ?? providerConfig.defaultTimeout,
      max_concurrent:
        providerConfig.max_concurrent ?? providerConfig.maxConcurrent,
      conn_pool_size:
        providerConfig.conn_pool_size ?? providerConfig.connPoolSize,
    }),
  );
  assert(
    providerEntries.length > 0,
    "Gateway config returned no providers to browser-verify.",
  );
  const sampleProvider =
    providerEntries.find((provider) => provider.models.length > 0) ||
    providerEntries[0];

  return {
    gateway_id: gatewayId,
    gateway_name: listedGateway.name || "Agent Command Center Gateway",
    configured_provider_count: Object.keys(config?.providers || {}).length,
    provider_health_count: normalizeProviderList(providerHealth?.providers)
      .length,
    sample_provider: sampleProvider,
  };
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

async function clickTitleWithinText(page, text, title, timeout = 30000) {
  await waitForVisibleText(page, text, { exact: true, timeout });
  const clicked = await page.evaluate(
    ({ expectedText, expectedTitle }) => {
      const textElements = window
        .visibleElements()
        .filter((element) =>
          window.normalizeText(element.textContent) === expectedText,
        );
      for (const element of textElements) {
        const container =
          element.closest(".MuiCard-root,tr,[role='dialog']") ||
          element.parentElement ||
          element;
        const button = Array.from(
          container.querySelectorAll("button[title]"),
        ).find((candidate) => candidate.getAttribute("title") === expectedTitle);
        if (button && !button.disabled) {
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
        }
      }
      return false;
    },
    { expectedText: text, expectedTitle: title },
  );
  assert(clicked, `Could not click ${title} within ${text}`);
}

function normalizeProviderList(providers) {
  if (Array.isArray(providers)) return providers;
  if (providers && typeof providers === "object") {
    return Object.entries(providers).map(([name, value]) => ({
      name,
      ...(value && typeof value === "object" ? value : {}),
    }));
  }
  return [];
}

function isAllowedProviderMutation(method, rawUrl) {
  const url = new URL(rawUrl);
  if (
    method === "POST" &&
    /\/agentcc\/provider-credentials\/fetch_models\/?$/.test(url.pathname)
  ) {
    return true;
  }
  return false;
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
