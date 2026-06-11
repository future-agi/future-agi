/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
  isUuid,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const FIXTURE_PREFIX = "ui_eval_summary_template_";
const SCREENSHOT_PATH = "/tmp/eval-summary-template-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/eval-summary-template-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

let expectedApiOrigin = new URL(process.env.API_BASE || "http://localhost:8003")
  .origin;

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  expectedApiOrigin = new URL(auth.apiBase).origin;
  const suffix = shortRunId(auth.runId);
  const evalName = `${FIXTURE_PREFIX}${suffix}_eval`;
  const templateName = `${FIXTURE_PREFIX}${suffix}_summary`;
  const criteria = `Group ${suffix} failures by root cause and severity.`;
  const evidence = { cleanup: [], browser_mutations: [] };
  const apiFailures = [];
  const pageErrors = [];
  const unexpectedMutations = [];
  let browser = null;
  let page = null;
  let evalId = null;
  let summaryTemplateId = null;
  let cleanupComplete = false;
  let caughtError = null;

  await hardDeleteFixturesByPrefix(FIXTURE_PREFIX, evidence.cleanup);

  try {
    const createdEval = await auth.client.post(
      apiPath("/model-hub/eval-templates/create-v2/"),
      {
        name: evalName,
        eval_type: "agent",
        instructions: "Evaluate {{output}} and return a pass/fail decision.",
        output_type: "pass_fail",
        model: "gpt-4o-mini",
        pass_threshold: 0.5,
        description: "Eval summary template browser smoke.",
        tags: ["api-journey", "eval-summary-template"],
        mode: "quick",
        data_injection: { variables_only: true },
        summary: { type: "concise" },
      },
    );
    evalId = createdEval?.id;
    assert(isUuid(evalId), "Agent eval create did not return a UUID id.");
    evidence.eval_id = evalId;

    const createdTemplate = await auth.client.post(
      apiPath("/model-hub/eval-summary-templates/"),
      {
        name: templateName,
        description: "Browser smoke reusable summary template.",
        criteria,
      },
    );
    summaryTemplateId = createdTemplate?.id;
    assert(
      isUuid(summaryTemplateId),
      "Summary template create did not return a UUID id.",
    );
    evidence.summary_template_id = summaryTemplateId;

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 1050 },
      args: ["--no-sandbox"],
    });
    page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installBrowserState(page, auth);

    page.on("request", (request) => {
      const url = request.url();
      if (
        isExpectedApiOriginUrl(url) &&
        MUTATION_METHODS.has(request.method())
      ) {
        const mutation = {
          method: request.method(),
          url: maskUrl(url),
          body: parseJsonBody(request.postData()),
        };
        if (isEvalSummarySmokeMutation(request.method(), url)) {
          evidence.browser_mutations.push(mutation);
        }
        if (!isAllowedMutation(request.method(), url, evalId)) {
          unexpectedMutations.push(`${request.method()} ${maskUrl(url)}`);
        }
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isRelevantApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${maskUrl(url)}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponseDuring(
      page,
      "eval detail load",
      (response) =>
        response
          .url()
          .includes(`/model-hub/eval-templates/${evalId}/detail/`) &&
        response.request().method() === "GET" &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/evaluations/${evalId}`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForVisibleText(page, evalName, { exact: true });
    await waitForVisibleText(page, "Save Version", { exact: true });

    await clickByAriaLabel(page, "Open evaluation runtime options");
    await clickVisibleText(page, "Summary", { exact: true });
    await waitForVisibleText(page, "Saved Templates", { exact: true });
    await waitForVisibleText(page, templateName, { exact: true });
    await waitForVisibleText(page, criteria, { exact: true });
    await clickVisibleText(page, templateName, { exact: true });
    await waitForVisibleText(page, templateName, { exact: true });

    const [, updateResponse, versionResponse] = await waitForResponsesDuring(
      page,
      "save eval summary template selection",
      [
        (response) =>
          isEvalUpdateResponse(response, evalId) &&
          response.request().method() === "PUT" &&
          response.status() < 400,
        (response) =>
          isEvalVersionCreateResponse(response, evalId) &&
          response.request().method() === "POST" &&
          response.status() < 400,
      ],
      () => clickEnabledButtonByText(page, "Save Version"),
    );
    const versionPayload = await responseJson(versionResponse);
    evidence.version_number =
      versionPayload?.result?.version_number ||
      versionPayload?.result?.versionNumber ||
      null;
    await page.waitForFunction(
      () => new URL(window.location.href).searchParams.has("v"),
      { timeout: 10000 },
    );
    const updateRequest = requestBody(updateResponse);
    assertSavedTemplateSummary(updateRequest?.summary, {
      summaryTemplateId,
      criteria,
      label: "browser update request",
    });

    const detail = await auth.client.get(
      apiPath("/model-hub/eval-templates/{template_id}/detail/", {
        template_id: evalId,
      }),
    );
    assertSavedTemplateSummary(detail?.config?.summary, {
      summaryTemplateId,
      criteria,
      label: "detail readback",
    });
    evidence.detail_summary = detail.config.summary;

    await waitForResponseDuring(
      page,
      "eval detail reload",
      (response) =>
        response
          .url()
          .includes(`/model-hub/eval-templates/${evalId}/detail/`) &&
        response.status() < 400,
      () => page.reload({ waitUntil: "domcontentloaded" }),
    );
    await waitForVisibleText(page, templateName, { exact: true });
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Unexpected browser mutations: ${unexpectedMutations.join("; ")}`,
    );

    await publicDeleteFixtures(auth.client, {
      evalId,
      summaryTemplateId,
      evidence: evidence.cleanup,
    });
    const cleanupAudit = await hardDeleteFixturesByPrefix(
      FIXTURE_PREFIX,
      evidence.cleanup,
    );
    assert(
      Number(cleanupAudit.remaining_eval_count) === 0 &&
        Number(cleanupAudit.remaining_summary_template_count) === 0,
      `Fixture cleanup left residue: ${JSON.stringify(cleanupAudit)}`,
    );
    cleanupComplete = true;

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
          unexpected_mutations: unexpectedMutations,
        },
        null,
        2,
      ),
    );
    if (page) {
      await page
        .screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .catch(() => null);
    }
  } finally {
    if (browser) await browser.close();
    if (!cleanupComplete) {
      await publicDeleteFixtures(auth.client, {
        evalId,
        summaryTemplateId,
        evidence: evidence.cleanup,
      }).catch((error) => {
        caughtError = appendCleanupError(caughtError, error);
      });
      await hardDeleteFixturesByPrefix(FIXTURE_PREFIX, evidence.cleanup).catch(
        (error) => {
          caughtError = appendCleanupError(caughtError, error);
        },
      );
    }
  }

  if (caughtError) throw caughtError;
}

function assertSavedTemplateSummary(
  summary,
  { summaryTemplateId, criteria, label },
) {
  assert(summary && typeof summary === "object", `${label} summary missing.`);
  assert(summary.type === "custom", `${label} summary type mismatch.`);
  assert(
    summary.template_id === summaryTemplateId,
    `${label} summary template_id mismatch: ${JSON.stringify(summary)}`,
  );
  assert(
    summary.custom === criteria,
    `${label} summary custom criteria mismatch: ${JSON.stringify(summary)}`,
  );
}

async function publicDeleteFixtures(
  client,
  { evalId, summaryTemplateId, evidence },
) {
  if (evalId) {
    await client.post(
      apiPath("/model-hub/eval-templates/bulk-delete/"),
      { template_ids: [evalId] },
      { okStatuses: [200, 404] },
    );
    evidence.push({
      cleanup: "public delete eval summary smoke eval",
      status: "passed",
      eval_id: evalId,
    });
  }
  if (summaryTemplateId) {
    await client.delete(
      apiPath("/model-hub/eval-summary-templates/{template_id}/", {
        template_id: summaryTemplateId,
      }),
      { okStatuses: [200, 404] },
    );
    evidence.push({
      cleanup: "public delete eval summary smoke template",
      status: "passed",
      summary_template_id: summaryTemplateId,
    });
  }
}

async function hardDeleteFixturesByPrefix(prefix, evidence) {
  const deleteSql = `
WITH fixture_templates AS (
  SELECT id
  FROM model_hub_evaltemplate
  WHERE name LIKE ${sqlTextLiteral(`${prefix}%`)}
),
deleted_eval_settings AS (
  DELETE FROM eval_settings
  WHERE eval_id IN (SELECT id FROM fixture_templates)
  RETURNING id
),
deleted_versions AS (
  DELETE FROM model_hub_eval_template_version
  WHERE eval_template_id IN (SELECT id FROM fixture_templates)
  RETURNING id
),
deleted_evaluators AS (
  DELETE FROM model_hub_evaluator
  WHERE eval_template_id IN (SELECT id FROM fixture_templates)
  RETURNING id
),
deleted_templates AS (
  DELETE FROM model_hub_evaltemplate
  WHERE id IN (SELECT id FROM fixture_templates)
  RETURNING id
),
deleted_summary_templates AS (
  DELETE FROM model_hub_evalsummarytemplate
  WHERE name LIKE ${sqlTextLiteral(`${prefix}%`)}
  RETURNING id
)
SELECT json_build_object(
  'deleted_eval_setting_count', (SELECT count(*) FROM deleted_eval_settings),
  'deleted_version_count', (SELECT count(*) FROM deleted_versions),
  'deleted_evaluator_count', (SELECT count(*) FROM deleted_evaluators),
  'deleted_eval_count', (SELECT count(*) FROM deleted_templates),
  'deleted_summary_template_count', (
    SELECT count(*) FROM deleted_summary_templates
  )
);
`;
  const countSql = `
SELECT json_build_object(
  'remaining_eval_count', (
    SELECT count(*)
    FROM model_hub_evaltemplate
    WHERE name LIKE ${sqlTextLiteral(`${prefix}%`)}
  ),
  'remaining_summary_template_count', (
    SELECT count(*)
    FROM model_hub_evalsummarytemplate
    WHERE name LIKE ${sqlTextLiteral(`${prefix}%`)}
  )
);
`;
  const result = {
    ...(await runPostgresJson(deleteSql)),
    ...(await runPostgresJson(countSql)),
  };
  if (
    Number(result.deleted_eval_count) > 0 ||
    Number(result.deleted_summary_template_count) > 0 ||
    Number(result.remaining_eval_count) > 0 ||
    Number(result.remaining_summary_template_count) > 0
  ) {
    evidence.push({
      cleanup: "hard delete eval summary smoke fixtures by prefix",
      status:
        Number(result.remaining_eval_count) === 0 &&
        Number(result.remaining_summary_template_count) === 0
          ? "passed"
          : "failed",
      audit: result,
    });
  }
  return result;
}

async function runPostgresJson(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
  const user = process.env.API_JOURNEY_DB_USER || "user";
  const database = process.env.API_JOURNEY_DB_NAME || "tfc";
  const { stdout } = await execFileAsync(
    "docker",
    ["exec", container, "psql", "-U", user, "-d", database, "-At", "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const text = stdout.trim();
  assert(text, "Postgres DB cleanup returned no JSON output.");
  return JSON.parse(text);
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
    window.normalizeText = (value) =>
      String(value || "")
        .replace(/\s+/g, " ")
        .trim();
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
      localStorage.setItem("TanstackQueryDevtools.open", "false");
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
    const responsePromise = page.waitForResponse(predicate, { timeout: 60000 });
    const actionResult = await action();
    const response = await responsePromise;
    return [actionResult, response];
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`, { cause: error });
  }
}

async function waitForResponsesDuring(page, label, predicates, action) {
  try {
    const responsePromises = predicates.map((predicate) =>
      page.waitForResponse(predicate, { timeout: 60000 }),
    );
    const actionResult = await action();
    const responses = await Promise.all(responsePromises);
    return [actionResult, ...responses];
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`, { cause: error });
  }
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

async function clickByAriaLabel(page, label, timeout = 30000) {
  await page.waitForFunction(
    (expectedLabel) =>
      window
        .visibleElements("[aria-label]")
        .some(
          (element) =>
            element.getAttribute("aria-label") === expectedLabel &&
            !element.disabled,
        ),
    { timeout },
    label,
  );
  const clicked = await page.evaluate((expectedLabel) => {
    const element = window
      .visibleElements("[aria-label]")
      .find(
        (candidate) =>
          candidate.getAttribute("aria-label") === expectedLabel &&
          !candidate.disabled,
      );
    if (!element) return false;
    element.scrollIntoView({ block: "center", inline: "center" });
    window.dispatchClick(element);
    return true;
  }, label);
  assert(clicked, `Could not click aria-label: ${label}`);
}

async function clickVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await waitForVisibleText(page, text, { exact, timeout });
  const clicked = await page.evaluate(
    ({ text: expectedText, exact: exactMatch }) => {
      const matches = window.visibleElements().filter((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
      const element =
        matches.find((candidate) =>
          ["menuitem", "button"].includes(candidate.getAttribute("role")),
        ) || matches[0];
      if (!element) return false;
      element.scrollIntoView({ block: "center", inline: "center" });
      window.dispatchClick(element);
      return true;
    },
    { text, exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

async function clickEnabledButtonByText(page, text, timeout = 30000) {
  await page.waitForFunction(
    (expectedText) =>
      window
        .visibleElements('button, [role="button"]')
        .some(
          (button) =>
            window.normalizeText(button.textContent) === expectedText &&
            !button.disabled &&
            button.getAttribute("aria-disabled") !== "true",
        ),
    { timeout },
    text,
  );
  const clicked = await page.evaluate((expectedText) => {
    const button = window
      .visibleElements('button, [role="button"]')
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedText &&
          !candidate.disabled &&
          candidate.getAttribute("aria-disabled") !== "true",
      );
    if (!button) return false;
    button.scrollIntoView({ block: "center", inline: "center" });
    window.dispatchClick(button);
    return true;
  }, text);
  assert(clicked, `Could not click enabled button: ${text}`);
}

async function responseJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function requestBody(response) {
  return parseJsonBody(response.request().postData());
}

function parseJsonBody(raw) {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

function isRelevantApiUrl(url) {
  return (
    isEvalUpdateUrl(url) || url.includes("/model-hub/eval-summary-templates/")
  );
}

function isEvalUpdateUrl(url) {
  return url.includes("/model-hub/eval-templates/") && url.includes("/update/");
}

function isEvalUpdateResponse(response, evalId) {
  return response.url().includes(`/model-hub/eval-templates/${evalId}/update/`);
}

function isEvalVersionCreateResponse(response, evalId) {
  return response
    .url()
    .includes(`/model-hub/eval-templates/${evalId}/versions/create/`);
}

function isEvalSummarySmokeMutation(method, url) {
  return (
    (method === "PUT" && isEvalUpdateUrl(url)) ||
    (method === "POST" &&
      url.includes("/model-hub/eval-templates/") &&
      url.includes("/versions/create/"))
  );
}

function isAllowedMutation(method, url, evalId) {
  if (method === "PUT" && isEvalUpdateResponse({ url: () => url }, evalId)) {
    return true;
  }
  if (
    method === "POST" &&
    isEvalVersionCreateResponse({ url: () => url }, evalId)
  ) {
    return true;
  }
  return false;
}

function isExpectedApiOriginUrl(url) {
  try {
    return new URL(url).origin === expectedApiOrigin;
  } catch {
    return false;
  }
}

function maskUrl(url) {
  return String(url).replace(/([?&](?:token|access|refresh)=)[^&]+/gi, "$1***");
}

function sqlTextLiteral(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

function shortRunId(runId) {
  return String(runId || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .slice(-8);
}

function appendCleanupError(caughtError, cleanupError) {
  if (!caughtError) return cleanupError;
  caughtError.message = `${caughtError.message}; cleanup failed: ${cleanupError.message}`;
  return caughtError;
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
