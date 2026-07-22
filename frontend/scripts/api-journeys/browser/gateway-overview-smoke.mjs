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
const SCREENSHOT_PATH = "/tmp/gateway-overview-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/gateway-overview-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  const auth = await createAuthenticatedContext();
  const evidence = await preflightGateway(auth.client);
  const apiFailures = [];
  const pageErrors = [];
  const gatewayRequests = [];
  const unexpectedMutations = [];
  let browser = null;
  let caughtError = null;

  try {
    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });
    const page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installBrowserState(page, auth);

    page.on("request", (request) => {
      const url = request.url();
      if (!isGatewayApiUrl(url)) return;
      gatewayRequests.push(`${request.method()} ${url}`);
      if (MUTATION_METHODS.has(request.method())) {
        unexpectedMutations.push(`${request.method()} ${url}`);
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
      "initial Gateway overview load",
      [
        (response) =>
          response.url().includes("/agentcc/gateways/") &&
          !response.url().includes("/providers/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response
            .url()
            .includes(`/agentcc/gateways/${evidence.gateway_id}/providers/`) &&
          response.status() < 400,
        (response) =>
          response.url().includes("/agentcc/api-keys/") &&
          response.status() < 400,
        (response) =>
          response.url().includes("/agentcc/analytics/overview/") &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway");

    await waitForVisibleText(page, evidence.gateway_name, { exact: true });
    await waitForVisibleText(page, evidence.gateway_status, { exact: true });
    await waitForVisibleText(page, "Gateway Endpoint", { exact: true });
    await waitForVisibleText(page, evidence.gateway_base_url);
    if (evidence.expects_getting_started_card) {
      await waitForVisibleText(
        page,
        "Get Started with Agent Command Center Gateway",
        { exact: true },
      );
      await waitForVisibleText(page, "Gateway Connected", { exact: true });
    }
    for (const label of [
      "Requests (24h)",
      "Cost (24h)",
      "Avg Latency",
      "Error Rate",
      "Provider Status",
      "Quick Links",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    for (const label of [
      "API Keys",
      "Providers",
      "Analytics",
      "Logs",
      "Guardrails",
      "Budgets",
      "Monitoring",
      "Settings",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    await waitForNoVisibleText(page, "Gateway is starting up");
    await waitForNoVisibleText(page, "Failed to load gateway");
    await waitForNoVisibleText(page, "Invalid Date");

    await waitForResponseDuring(
      page,
      "Providers quick link",
      (response) =>
        response
          .url()
          .includes(`/agentcc/gateways/${evidence.gateway_id}/config/`) &&
        response.status() < 400,
      () => clickVisibleText(page, "Providers", { exact: true }),
    );
    await waitForPath(page, "/dashboard/gateway/providers");
    await waitForVisibleText(page, "Providers", { exact: true });
    await waitForVisibleText(page, "Provider Health", { exact: true });
    evidence.quick_link_path = "/dashboard/gateway/providers";

    await page.goto(`${APP_BASE}/dashboard/gateway`, {
      waitUntil: "domcontentloaded",
    });
    await waitForPath(page, "/dashboard/gateway");
    await waitForVisibleText(page, evidence.gateway_name, { exact: true });
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Read-only Gateway overview smoke fired mutations: ${unexpectedMutations.join(
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
          evidence,
          gateway_request_count: gatewayRequests.length,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    caughtError = error;
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
          unexpected_mutations: unexpectedMutations,
        },
        null,
        2,
      ),
    );
    if (browser) {
      const pages = await browser.pages();
      const page = pages[pages.length - 1];
      await page
        ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .catch(() => null);
    }
  } finally {
    if (browser) await browser.close();
  }

  if (caughtError) throw caughtError;
}

async function preflightGateway(client) {
  const gateways = asArray(await client.get(apiPath("/agentcc/gateways/")));
  assert(gateways.length > 0, "Gateway list returned no configured gateway.");
  const listedGateway = gateways[0];
  const gatewayId = listedGateway.id || "default";
  const detail = await client.get(
    apiPath("/agentcc/gateways/{id}/", { id: gatewayId }),
  );
  assert(detail?.id === gatewayId, "Gateway detail did not match list id.");

  const config = await client.get(
    apiPath("/agentcc/gateways/{id}/config/", { id: gatewayId }),
  );
  assert(
    config && typeof config === "object",
    "Gateway config is not an object.",
  );
  assert(
    config.cost_tracking || config.providers,
    "Gateway config did not expose cost_tracking or providers.",
  );

  const providerHealth = await client.get(
    apiPath("/agentcc/gateways/{id}/providers/", { id: gatewayId }),
  );
  assert(
    providerHealth && typeof providerHealth === "object",
    "Gateway provider health is not an object.",
  );
  const providerList = normalizeProviderList(providerHealth.providers);

  const protectTemplates = await client.get(
    apiPath("/agentcc/gateways/protect-templates/"),
  );
  assert(
    protectTemplates && typeof protectTemplates === "object",
    "Gateway protect templates response is not an object.",
  );

  const apiKeys = asArray(
    await client.get(apiPath("/agentcc/api-keys/"), {
      query: { gateway_id: gatewayId },
    }),
  );

  const now = new Date();
  const dayAgo = new Date(now);
  dayAgo.setDate(dayAgo.getDate() - 1);
  const overview = await client.get(apiPath("/agentcc/analytics/overview/"), {
    query: {
      gateway_id: gatewayId,
      start: dayAgo.toISOString(),
      end: now.toISOString(),
    },
  });
  assert(
    overview && typeof overview === "object" && "total_requests" in overview,
    "Gateway analytics overview did not include total_requests.",
  );

  const providerCount = Number(
    listedGateway.providerCount ??
      listedGateway.provider_count ??
      detail.providerCount ??
      detail.provider_count ??
      providerList.length,
  );
  const modelCount = Number(
    listedGateway.modelCount ??
      listedGateway.model_count ??
      detail.modelCount ??
      detail.model_count ??
      0,
  );
  const totalRequests = Number(apiMetricValue(overview.total_requests) ?? 0);
  const gatewayBaseUrl =
    detail.baseUrl ||
    detail.base_url ||
    listedGateway.baseUrl ||
    listedGateway.base_url ||
    "";
  assert(gatewayBaseUrl, "Gateway detail did not expose a base URL.");

  return {
    gateway_id: gatewayId,
    gateway_name:
      detail.name || listedGateway.name || "Agent Command Center Gateway",
    gateway_status: detail.status || listedGateway.status || "healthy",
    gateway_base_url: gatewayBaseUrl,
    provider_count: providerCount,
    provider_health_count: providerList.length,
    model_count: modelCount,
    api_key_count: apiKeys.length,
    analytics_total_requests: totalRequests,
    protect_templates_shape: Array.isArray(protectTemplates)
      ? "array"
      : "object",
    expects_getting_started_card: !(
      providerCount > 0 &&
      apiKeys.length > 0 &&
      totalRequests > 0
    ),
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
        element?.closest("button,a,[role='button'],[role='menuitem']") ||
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

function apiMetricValue(metric) {
  if (metric && typeof metric === "object" && "value" in metric) {
    return metric.value;
  }
  return metric;
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
