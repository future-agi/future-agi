/* eslint-disable no-console */
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  isUuid,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const EVAL_PREFIX = "ui_eval_test_evaluation_";
const SCREENSHOT_PATH = "/tmp/eval-test-evaluation-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/eval-test-evaluation-smoke-failure.png";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

let expectedApiOrigin = new URL(process.env.API_BASE || "http://localhost:8003")
  .origin;

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  expectedApiOrigin = new URL(auth.apiBase).origin;

  const suffix = shortRunId(auth.runId);
  const evalName = `${EVAL_PREFIX}${suffix}`;
  const testData = { output: "same", expected: "same" };
  const evidence = {
    eval_name: evalName,
    browser_mutations: [],
    cleanup: [],
  };

  const apiFailures = [];
  const apiResponses = [];
  const pageErrors = [];
  const unexpectedMutations = [];
  let evalId = null;
  let logId = null;
  let browser = null;
  let caughtError = null;

  await cleanupEvalTemplatesByPrefix(
    auth.client,
    EVAL_PREFIX,
    evidence.cleanup,
  );

  try {
    const created = await createCodeEval(auth.client, evalName);
    evalId = created.id;
    evidence.eval_id = evalId;

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
      const rawUrl = request.url();
      if (!isModelHubUrl(rawUrl) || !MUTATION_METHODS.has(request.method())) {
        return;
      }
      const url = new URL(rawUrl);
      const mutation = {
        method: request.method(),
        pathname: maskPathname(url.pathname, evalId),
        body: parseJsonMaybe(request.postData()),
      };
      if (isExpectedTestRunMutation(request.method(), url.pathname, evalId)) {
        evidence.browser_mutations.push(mutation);
      }
      if (!isAllowedModelHubMutation(request.method(), url.pathname, evalId)) {
        unexpectedMutations.push(`${request.method()} ${url.pathname}`);
      }
    });
    page.on("response", (response) => {
      const rawUrl = response.url();
      if (!isModelHubUrl(rawUrl)) return;
      const url = new URL(rawUrl);
      apiResponses.push({
        method: response.request().method(),
        status: response.status(),
        pathname: maskPathname(url.pathname, evalId),
      });
      if (isExpectedApiOriginUrl(rawUrl) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url.pathname}`);
      }
    });
    page.on("pageerror", (error) => {
      pageErrors.push(error.stack || error.message);
    });

    await waitForResponseDuring(
      page,
      "eval detail load",
      (response) =>
        response
          .url()
          .includes(`/model-hub/eval-templates/${evalId}/detail/`) &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/evaluations/${evalId}`, {
          waitUntil: "domcontentloaded",
        }),
    );

    await waitForVisibleText(page, evalName, { timeout: 30000 });
    await waitForVisibleText(page, "Test Data", { exact: true });
    await waitForVisibleText(page, "Test Evaluation", { exact: true });
    await setMonacoJsonValue(page, "Test data JSON", testData);

    const runResponse = await waitForEvalRunDuring(page, () =>
      clickAriaButton(page, "Run test evaluation"),
    );

    const runPayload = unwrapResult(await responseJson(runResponse));
    assertEvalPlaygroundPayload(runPayload);
    logId = runPayload.log_id;
    evidence.log_id = logId;
    evidence.output = runPayload.output;

    const runRequest = parseJsonMaybe(runResponse.request().postData());
    assertEvalPlaygroundRequest(runRequest, {
      evalId,
      mapping: testData,
    });

    await waitForVisibleText(page, "Test completed", { exact: true });
    await waitForVisibleText(page, "Pass", { exact: true });
    await waitForVisibleText(page, "Explanation", { exact: true });
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assertExpectedBrowserMutations(evidence.browser_mutations, evalId);
    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Unexpected model-hub browser mutations: ${unexpectedMutations.join("; ")}`,
    );

    await cleanupEvalLog(auth.client, logId, evidence.cleanup);
    logId = null;
    await cleanupEvalTemplate(auth.client, evalId, evidence.cleanup);
    evalId = null;
    const residue = await listEvalTemplates(auth.client, evalName);
    const residueRows = residue.items.filter((item) => item.name === evalName);
    assert(
      residueRows.length === 0,
      `Eval test-evaluation residue remained: ${JSON.stringify(residueRows)}`,
    );
    evidence.residue_count = residueRows.length;

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
          api_responses: apiResponses.slice(-25),
          page_errors: pageErrors,
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
      console.error(`failure_screenshot=${FAILURE_SCREENSHOT_PATH}`);
    }
  } finally {
    if (browser) await browser.close();
    await cleanupEvalLog(auth.client, logId, evidence.cleanup).catch(
      (error) => {
        caughtError = appendCleanupError(caughtError, error);
      },
    );
    await cleanupEvalTemplate(auth.client, evalId, evidence.cleanup).catch(
      (error) => {
        caughtError = appendCleanupError(caughtError, error);
      },
    );
  }

  if (caughtError) throw caughtError;
}

async function createCodeEval(client, name) {
  const created = await client.post(
    apiPath("/model-hub/eval-templates/create-v2/"),
    {
      name,
      eval_type: "code",
      code: [
        "def evaluate(output=None, expected=None, **kwargs):",
        "    return str(output).strip().lower() == str(expected).strip().lower()",
      ].join("\n"),
      code_language: "python",
      output_type: "pass_fail",
      pass_threshold: 0.5,
      description: "Eval Detail test button browser smoke.",
      tags: ["api-journey", "eval-test-evaluation-ui"],
    },
  );
  assert(isUuid(created?.id), "Code eval create did not return a UUID id.");
  return created;
}

async function cleanupEvalTemplate(client, templateId, evidence) {
  if (!templateId) return;
  await client.post(
    apiPath("/model-hub/eval-templates/bulk-delete/"),
    { template_ids: [templateId] },
    { okStatuses: [200, 404] },
  );
  evidence.push({
    cleanup: "delete eval test-evaluation template",
    status: "passed",
    template_id: templateId,
  });
  console.error(`cleanup eval test-evaluation template: ${templateId}`);
}

async function cleanupEvalLog(client, maybeLogId, evidence) {
  if (!isUuid(maybeLogId)) return;
  await client.delete(apiPath("/model-hub/get-eval-logs"), {
    body: { log_ids: [maybeLogId] },
    okStatuses: [200, 404],
  });
  evidence.push({
    cleanup: "delete eval test-evaluation log",
    status: "passed",
    log_id: maybeLogId,
  });
  console.error(`cleanup eval test-evaluation log: ${maybeLogId}`);
}

async function cleanupEvalTemplatesByPrefix(client, prefix, evidence) {
  const result = await listEvalTemplates(client, prefix);
  const ids = result.items
    .filter((item) => String(item?.name || "").startsWith(prefix))
    .map((item) => item.id)
    .filter(isUuid);
  if (!ids.length) return;
  await client.post(
    apiPath("/model-hub/eval-templates/bulk-delete/"),
    { template_ids: ids },
    { okStatuses: [200, 404] },
  );
  evidence.push({
    cleanup: "delete stale eval test-evaluation templates",
    status: "passed",
    template_ids: ids,
  });
}

async function listEvalTemplates(client, search) {
  const result = await client.post(apiPath("/model-hub/eval-templates/list/"), {
    page: 0,
    page_size: 25,
    owner_filter: "all",
    search,
    sort_by: "updated_at",
    sort_order: "desc",
  });
  return {
    ...result,
    items: asArray(result?.items),
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
        return Array.from(document.querySelectorAll(selector)).filter(
          isVisible,
        );
      };
    },
    {
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );
}

async function waitForResponseDuring(
  page,
  label,
  predicate,
  action,
  { timeout = 60000 } = {},
) {
  const responsePromise = page.waitForResponse(predicate, { timeout });
  await action();
  const response = await responsePromise;
  assert(response, `${label} did not receive expected response.`);
  return response;
}

async function waitForEvalRunDuring(page, action) {
  const responsePromise = page
    .waitForResponse((response) => isEvalRunResponse(response), {
      timeout: 120000,
    })
    .then((response) => ({ type: "response", response }))
    .catch((error) => ({ type: "timeout", error }));
  const errorPromise = waitForVisibleTestError(page)
    .then((message) => ({ type: "ui-error", message }))
    .catch(() => null);

  await action();
  const outcome = await Promise.race([responsePromise, errorPromise]);
  if (outcome?.type === "response") return outcome.response;
  if (outcome?.type === "ui-error") {
    throw new Error(`Eval detail test surfaced UI error: ${outcome.message}`);
  }
  if (outcome?.type === "timeout") {
    throw new Error(`Eval detail test run timed out: ${outcome.error.message}`);
  }
  throw new Error(
    "Eval detail test run did not produce a response or UI error.",
  );
}

async function waitForVisibleTestError(page) {
  const handle = await page.waitForFunction(
    () => {
      const error = window
        .visibleElements('[data-testid="eval-test-error"]')
        .find((element) => window.normalizeText(element.textContent));
      return error ? window.normalizeText(error.textContent) : false;
    },
    { timeout: 120000 },
  );
  return handle.jsonValue();
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

async function clickAriaButton(page, ariaLabel, timeout = 30000) {
  await page.waitForFunction(
    (label) =>
      window
        .visibleElements("button")
        .some(
          (button) =>
            button.getAttribute("aria-label") === label && !button.disabled,
        ),
    { timeout },
    ariaLabel,
  );
  const clicked = await page.evaluate((label) => {
    const button = window
      .visibleElements("button")
      .find(
        (candidate) =>
          candidate.getAttribute("aria-label") === label && !candidate.disabled,
      );
    if (!button) return false;
    window.dispatchClick(button);
    return true;
  }, ariaLabel);
  assert(clicked, `Could not click aria button: ${ariaLabel}`);
}

async function setMonacoJsonValue(page, ariaLabel, value) {
  const serialized = JSON.stringify(value, null, 2);
  await page.waitForSelector(
    `[role="group"][aria-label="${cssString(ariaLabel)}"]`,
    { timeout: 30000 },
  );
  await page.waitForFunction(
    () =>
      window.monaco?.editor
        ?.getModels?.()
        ?.some((model) => model.getLanguageId() === "json"),
    { timeout: 30000 },
  );
  const applied = await page.evaluate(
    ({ label, text }) => {
      const editor = document.querySelector(
        `[role="group"][aria-label="${CSS.escape(label)}"]`,
      );
      if (!editor) {
        return { ok: false, reason: "editor wrapper missing" };
      }
      editor.click();
      const models = window.monaco.editor
        .getModels()
        .filter((model) => model.getLanguageId() === "json");
      const model = models[models.length - 1];
      if (!model) {
        return { ok: false, reason: "json model missing" };
      }
      model.setValue(text);
      return { ok: model.getValue() === text, value: model.getValue() };
    },
    { label: ariaLabel, text: serialized },
  );
  assert(applied?.ok, `Failed to set Monaco JSON: ${JSON.stringify(applied)}`);
  await page.waitForFunction(
    (text) =>
      window.monaco?.editor
        ?.getModels?.()
        ?.some((model) => model.getValue() === text),
    { timeout: 30000 },
    serialized,
  );
  await delay(250);
}

function assertEvalPlaygroundRequest(payload, { evalId, mapping }) {
  assert(
    payload?.template_id === evalId,
    "Playground request template mismatch.",
  );
  assert(
    payload?.error_localizer === false,
    "Playground request should keep error localization disabled.",
  );
  assert(
    payload?.config?.mapping?.output === mapping.output,
    "Playground request output mapping mismatch.",
  );
  assert(
    payload?.config?.mapping?.expected === mapping.expected,
    "Playground request expected mapping mismatch.",
  );
  assert(
    payload?.config?.params &&
      typeof payload.config.params === "object" &&
      !Array.isArray(payload.config.params),
    "Playground request did not include code params object.",
  );
}

function assertEvalPlaygroundPayload(payload) {
  assert(
    payload && typeof payload === "object",
    "Playground returned no payload.",
  );
  assert(
    isUuid(payload.log_id),
    "Playground response did not include a usage log id.",
  );
  assert(
    normalizeEvalOutput(payload.output) === "passed",
    `Playground returned output ${payload.output}, expected Passed.`,
  );
  assert(
    payload.output_type === "Pass/Fail" || payload.output_type === "pass_fail",
    "Playground response did not preserve Pass/Fail output type.",
  );
  assert(
    Object.prototype.hasOwnProperty.call(payload, "reason"),
    "Playground response did not include reason.",
  );
}

function assertExpectedBrowserMutations(mutations, evalId) {
  const run = mutations.filter(
    (mutation) =>
      mutation.method === "POST" &&
      (mutation.pathname === "/model-hub/eval-playground/" ||
        mutation.pathname === "/model-hub/eval-playground"),
  );
  const saves = mutations.filter(
    (mutation) =>
      mutation.method === "PUT" &&
      mutation.pathname ===
        maskPathname(`/model-hub/eval-templates/${evalId}/update/`, evalId),
  );
  assert(
    run.length === 1,
    `Expected one eval playground run, got ${run.length}.`,
  );
  assert(
    saves.length === 1,
    `Expected one eval detail auto-save, got ${saves.length}.`,
  );
}

function unwrapResult(payload) {
  return payload && Object.prototype.hasOwnProperty.call(payload, "result")
    ? payload.result
    : payload;
}

async function responseJson(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function parseJsonMaybe(value) {
  if (!value) return null;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function normalizeEvalOutput(value) {
  if (value === true) return "passed";
  if (value === false) return "failed";
  return String(value || "")
    .trim()
    .toLowerCase();
}

function isExpectedApiOriginUrl(rawUrl) {
  try {
    return new URL(rawUrl).origin === expectedApiOrigin;
  } catch {
    return false;
  }
}

function isModelHubUrl(rawUrl) {
  try {
    return new URL(rawUrl).pathname.startsWith("/model-hub/");
  } catch {
    return false;
  }
}

function isEvalRunResponse(response) {
  if (response.request().method() !== "POST" || response.status() >= 400) {
    return false;
  }
  const pathname = new URL(response.url()).pathname;
  return (
    pathname === "/model-hub/eval-playground/" ||
    pathname === "/model-hub/eval-playground" ||
    pathname === "/model-hub/test-evaluation/" ||
    pathname === "/model-hub/test-evaluation"
  );
}

function isExpectedTestRunMutation(method, pathname, evalId) {
  if (
    method === "POST" &&
    (pathname === "/model-hub/eval-playground/" ||
      pathname === "/model-hub/eval-playground")
  ) {
    return true;
  }
  return (
    method === "PUT" &&
    pathname === `/model-hub/eval-templates/${evalId}/update/`
  );
}

function isAllowedModelHubMutation(method, pathname, evalId) {
  if (!pathname.startsWith("/model-hub/")) return true;
  if (isExpectedTestRunMutation(method, pathname, evalId)) return true;
  if (method !== "POST") return false;
  return new Set([
    "/model-hub/get-eval-config",
    "/model-hub/get-eval-template-names",
    "/model-hub/eval-templates/list/",
    "/model-hub/eval-templates/list",
    "/model-hub/eval-templates/list-charts/",
    "/model-hub/eval-templates/list-charts",
  ]).has(pathname);
}

function maskPathname(pathname, evalId) {
  if (!evalId) return pathname;
  return String(pathname).replace(evalId, "<eval-id>");
}

function cssString(value) {
  return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function appendCleanupError(caughtError, cleanupError) {
  if (!caughtError) return cleanupError;
  caughtError.message = `${caughtError.message}; cleanup failed: ${cleanupError.message}`;
  return caughtError;
}

function shortRunId(runId) {
  return String(runId || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .slice(-8);
}

function delay(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
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
