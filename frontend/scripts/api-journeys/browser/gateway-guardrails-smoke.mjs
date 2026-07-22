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
const SCREENSHOT_PATH = "/tmp/gateway-guardrails-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/gateway-guardrails-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  const auth = await createAuthenticatedContext();
  const evidence = await preflightGuardrails(auth.client);
  const apiFailures = [];
  const pageErrors = [];
  const gatewayRequests = [];
  const browserMutations = [];
  let browser = null;
  let page = null;
  let caughtError = null;

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
        browserMutations.push(`${request.method()} ${url}`);
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (!isGatewayApiUrl(url) || response.status() < 400) return;
      apiFailures.push(`${response.status()} ${url}`);
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponsesDuring(
      page,
      "initial Gateway guardrails load",
      [
        (response) =>
          response.url().includes("/agentcc/gateways/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/agentcc/org-configs/active/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway/guardrails`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway/guardrails");

    for (const label of [
      "Guardrails",
      "Configure safety rules and content policies",
      "Overview",
      "Rules",
      "Analytics",
      "Feedback",
      "Test",
      "Logs",
      "Total Guardrails",
      "Active",
      "Disabled",
      "Types",
      "Guardrail",
      "Type",
      "Enabled",
      "Action",
      "Stage",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    if (evidence.sample_guardrail?.name) {
      await waitForVisibleText(page, evidence.sample_guardrail.name, {
        exact: true,
      });
      await waitForVisibleText(page, evidence.sample_guardrail.action, {
        exact: true,
      });
    }

    await clickVisibleText(page, "Rules", { exact: true });
    await waitForPath(page, "/dashboard/gateway/guardrails/configuration");
    for (const label of [
      "Pipeline Settings",
      "Mode",
      "Fail Open",
      "Timeout (ms)",
      "AI-Powered Checks",
      "Future AGI Eval",
      "Llama Guard",
      "Rule-Based Checks",
      "PII Detection",
      "Content Moderation",
      "Keyword Blocklist",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    await clickButtonWithinText(page, "Keyword Blocklist");
    await waitForVisibleText(page, "Configure: Keyword Blocklist");
    await waitForVisibleText(page, "Blocked Keywords");
    await waitForVisibleText(page, "Confidence Threshold: 0.80");
    await clickDialogButton(page, "Cancel");
    await waitForNoVisibleText(page, "Configure: Keyword Blocklist");

    await waitForResponsesDuring(
      page,
      "guardrail analytics load",
      [
        (response) =>
          response.url().includes("/agentcc/analytics/guardrail-overview/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/agentcc/analytics/guardrail-rules/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/agentcc/analytics/guardrail-trends/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
      ],
      () => clickVisibleText(page, "Analytics", { exact: true }),
    );
    await waitForPath(page, "/dashboard/gateway/guardrails/analytics");
    for (const label of [
      "Trigger Rate",
      "Blocked",
      "Warned",
      "Avg Latency",
      "Guardrail Triggers Over Time",
      "Top Triggered Rules",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    await waitForResponseDuring(
      page,
      "guardrail feedback summary load",
      (response) =>
        response.url().includes("/agentcc/guardrail-feedback/summary/") &&
        response.request().method() === "GET" &&
        response.status() < 400,
      () => clickVisibleText(page, "Feedback", { exact: true }),
    );
    await waitForPath(page, "/dashboard/gateway/guardrails/feedback");
    await waitForVisibleText(page, "Feedback Summary");

    await clickVisibleText(page, "Test", { exact: true });
    await waitForPath(page, "/dashboard/gateway/guardrails/playground");
    for (const label of [
      "Test Guardrails",
      "Safe prompt",
      "PII test",
      "Injection test",
      "Secrets test",
      "Toxic content",
      "Model (optional)",
      "Run Test",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    await clickVisibleText(page, "Safe prompt", { exact: true });
    assert(
      await isRunTestButtonEnabled(page),
      "Safe prompt chip did not enable the Run Test button.",
    );

    await waitForResponseDuring(
      page,
      "guardrail logs load",
      (response) =>
        response.url().includes("/agentcc/request-logs/") &&
        response.url().includes("guardrail_triggered=true") &&
        response.request().method() === "GET" &&
        response.status() < 400,
      () => clickVisibleText(page, "Logs", { exact: true }),
    );
    await waitForPath(page, "/dashboard/gateway/guardrails/logs");
    for (const label of [
      "Time",
      "Model",
      "Status",
      "Guardrail Details",
      "Request ID",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      browserMutations.length === 0,
      `Unexpected Gateway guardrail mutations: ${browserMutations.join("; ")}`,
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
          page_errors: pageErrors,
          gateway_requests: gatewayRequests,
          browser_mutations: browserMutations,
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
      },
      null,
      2,
    ),
  );
}

async function preflightGuardrails(client) {
  const gateways = asArray(await client.get(apiPath("/agentcc/gateways/")));
  assert(gateways.length > 0, "Gateway list returned no configured gateway.");
  const gatewayId = gateways[0].id || "default";
  const [
    orgConfig,
    policies,
    topics,
    piiEntities,
    feedbackSummary,
    analyticsOverview,
    analyticsRules,
    analyticsTrends,
    guardrailLogs,
    validCel,
    invalidCel,
  ] = await Promise.all([
    client.get(apiPath("/agentcc/org-configs/active/")),
    client.get(apiPath("/agentcc/guardrail-policies/")),
    client.get(apiPath("/agentcc/guardrail-configs/topics/")),
    client.get(apiPath("/agentcc/guardrail-configs/pii-entities/")),
    client.get(apiPath("/agentcc/guardrail-feedback/summary/")),
    client.get(apiPath("/agentcc/analytics/guardrail-overview/")),
    client.get(apiPath("/agentcc/analytics/guardrail-rules/")),
    client.get(apiPath("/agentcc/analytics/guardrail-trends/")),
    client.get(apiPath("/agentcc/request-logs/"), {
      query: { guardrail_triggered: true, limit: 5 },
    }),
    client.post(apiPath("/agentcc/guardrail-configs/validate-cel/"), {
      expression: 'request.model == "gpt-4o"',
    }),
    client.post(apiPath("/agentcc/guardrail-configs/validate-cel/"), {
      expression: 'request.model == ("gpt-4o"',
    }),
  ]);

  const guardrails = extractGuardrails(orgConfig);
  assert(
    asArray(topics).length > 0,
    "Guardrail topic catalog returned no categories.",
  );
  assert(
    asArray(piiEntities).length > 0,
    "Guardrail PII catalog returned no entity types.",
  );
  assert(validCel?.valid === true, "Valid CEL preflight did not pass.");
  assert(invalidCel?.valid === false, "Invalid CEL preflight did not fail.");

  return {
    gateway_id: gatewayId,
    org_config_id: orgConfig?.id,
    guardrail_count: guardrails.length,
    sample_guardrail: guardrails[0]
      ? {
          name: guardrails[0].name,
          action: guardrails[0].action || "log",
          enabled: guardrails[0].enabled !== false,
        }
      : null,
    policy_count: asArray(policies).length,
    topic_count: asArray(topics).length,
    pii_entity_count: asArray(piiEntities).length,
    feedback_summary_count: asArray(feedbackSummary).length,
    analytics_trigger_rate:
      analyticsOverview?.trigger_rate ?? analyticsOverview?.triggerRate ?? 0,
    analytics_rule_count: Array.isArray(analyticsRules)
      ? analyticsRules.length
      : asArray(analyticsRules?.rules).length,
    analytics_trend_points: asArray(analyticsTrends?.series).length,
    guardrail_log_count: asArray(guardrailLogs).length,
    cel_validation_checked: true,
  };
}

function extractGuardrails(orgConfig) {
  if (!orgConfig) return [];
  const guardrailSection = orgConfig.guardrails || {};
  const rules = guardrailSection.rules;
  if (Array.isArray(rules) && rules.length > 0) return rules;

  const checks = guardrailSection.checks || {};
  if (typeof checks === "object" && !Array.isArray(checks)) {
    return Object.entries(checks).map(([name, cfg]) => ({
      name,
      ...(typeof cfg === "object" ? cfg : { enabled: cfg }),
    }));
  }
  if (Array.isArray(checks)) return checks;
  return [];
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
