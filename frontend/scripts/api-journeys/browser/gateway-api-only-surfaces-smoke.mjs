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
const SCREENSHOT_PATH = "/tmp/gateway-api-only-surfaces-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/gateway-api-only-surfaces-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);
const STALE_SURFACES = [
  {
    key: "blocklists",
    label: "Blocklists",
    path: "/dashboard/gateway/blocklists",
    apiPath: "/agentcc/blocklists/",
    forbiddenTexts: ["Create blocklist", "Add words", "Remove words"],
  },
  {
    key: "routing_policies",
    label: "Routing Policies",
    path: "/dashboard/gateway/routing-policies",
    apiPath: "/agentcc/routing-policies/",
    forbiddenTexts: ["Create policy", "Activate policy", "Sync policies"],
  },
];

async function main() {
  const auth = await createAuthenticatedContext();
  const evidence = await preflightApiOnlySurfaces(auth.client);
  const apiFailures = [];
  const pageErrors = [];
  const gatewayRequests = [];
  const browserMutations = [];
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
        browserMutations.push(`${request.method()} ${url}`);
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
          response.request().method() === "GET" &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway");
    await waitForVisibleText(page, "Quick Links", { exact: true });

    const overviewLabels = await visibleTextSnapshot(page);
    evidence.overview_has_blocklists_label = overviewLabels.some((text) =>
      text.includes("Blocklists"),
    );
    evidence.overview_has_routing_policies_label = overviewLabels.some((text) =>
      text.includes("Routing Policies"),
    );
    assert(
      !evidence.overview_has_blocklists_label,
      "Gateway overview rendered a Blocklists nav/quick-link label, but no route is documented.",
    );
    assert(
      !evidence.overview_has_routing_policies_label,
      "Gateway overview rendered a Routing Policies nav/quick-link label, but no route is documented.",
    );

    evidence.direct_route_probes = {};
    for (const surface of STALE_SURFACES) {
      evidence.direct_route_probes[surface.key] = await probeStaleRoute(
        page,
        surface,
      );
    }

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    const staleBrowserApiRequests = gatewayRequests.filter((request) =>
      STALE_SURFACES.some((surface) => request.includes(surface.apiPath)),
    );
    assert(
      staleBrowserApiRequests.length === 0,
      `Stale API-only Gateway browser routes unexpectedly called APIs: ${staleBrowserApiRequests.join(
        "; ",
      )}`,
    );
    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      browserMutations.length === 0,
      `Read-only Gateway API-only surfaces smoke fired mutations: ${browserMutations.join(
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
          browser_mutations: browserMutations,
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
          browser_mutations: browserMutations,
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

async function preflightApiOnlySurfaces(client) {
  const gateways = asArray(await client.get(apiPath("/agentcc/gateways/")));
  assert(gateways.length > 0, "Gateway list returned no configured gateway.");

  const blocklistsPayload = await client.get(apiPath("/agentcc/blocklists/"), {
    unwrap: false,
  });
  const routingPoliciesPayload = await client.get(
    apiPath("/agentcc/routing-policies/"),
    { unwrap: false },
  );

  const blocklists = asArray(blocklistsPayload);
  const routingPolicies = asArray(routingPoliciesPayload);
  assert(
    blocklistsPayload && typeof blocklistsPayload === "object",
    "Blocklists API did not return an object/array payload.",
  );
  assert(
    routingPoliciesPayload && typeof routingPoliciesPayload === "object",
    "Routing policies API did not return an object/array payload.",
  );

  return {
    gateway_id: gateways[0].id || "default",
    gateway_count: gateways.length,
    blocklist_count: blocklists.length,
    routing_policy_count: routingPolicies.length,
    blocklists_payload_shape: payloadShape(blocklistsPayload),
    routing_policies_payload_shape: payloadShape(routingPoliciesPayload),
  };
}

async function probeStaleRoute(page, surface) {
  const beforeRequestCount = await page.evaluate(() => performance.now());
  await page.goto(`${APP_BASE}${surface.path}`, {
    waitUntil: "domcontentloaded",
  });
  await page.waitForFunction(
    () =>
      document.readyState === "interactive" ||
      document.readyState === "complete",
    { timeout: 30000 },
  );
  await sleep(750);

  const textSnapshot = await visibleTextSnapshot(page);
  const foundForbiddenTexts = surface.forbiddenTexts.filter((text) =>
    textSnapshot.some((visibleText) => visibleText.includes(text)),
  );
  assert(
    foundForbiddenTexts.length === 0,
    `${surface.label} stale route rendered management controls: ${foundForbiddenTexts.join(
      ", ",
    )}`,
  );

  const titleVisible = textSnapshot.some((text) => text === surface.label);
  assert(
    !titleVisible,
    `${surface.label} stale route rendered a dedicated page title.`,
  );

  return {
    probed_at_ms: beforeRequestCount,
    requested_path: surface.path,
    final_path: await page.evaluate(() => window.location.pathname),
    title_visible: titleVisible,
    forbidden_controls_visible: foundForbiddenTexts,
    visible_text_sample: textSnapshot.slice(0, 25),
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

async function visibleTextSnapshot(page) {
  return page.evaluate(() => {
    const seen = new Set();
    return window
      .visibleElements()
      .map((element) => window.normalizeText(element.textContent))
      .filter(Boolean)
      .filter((text) => {
        if (seen.has(text)) return false;
        seen.add(text);
        return true;
      });
  });
}

function payloadShape(payload) {
  if (Array.isArray(payload)) return "array";
  if (Array.isArray(payload?.results)) return "paginated_results";
  if (Array.isArray(payload?.result)) return "result_array";
  if (payload && typeof payload === "object") return "object";
  return typeof payload;
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
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
