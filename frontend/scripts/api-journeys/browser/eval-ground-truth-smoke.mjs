/* eslint-disable no-console */
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/eval-ground-truth-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/eval-ground-truth-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const suffix = shortRunId(auth.runId);
  const evalName = `ui_eval_ground_truth_${suffix}`;
  const groundTruthName = `ui_gt_dataset_${suffix}`;
  let evalId = null;
  let groundTruthId = null;
  let browser = null;
  let caughtError = null;

  const apiFailures = [];
  const pageErrors = [];
  const unexpectedMutations = [];
  const evidence = {
    eval_name: evalName,
    ground_truth_name: groundTruthName,
  };

  try {
    const created = await createLlmEval(auth.client, evalName);
    evalId = created.id;
    evidence.eval_id = evalId;

    const groundTruth = await uploadGroundTruth(
      auth.client,
      evalId,
      groundTruthName,
    );
    groundTruthId = groundTruth.id;
    evidence.ground_truth_id = groundTruthId;

    await enableGroundTruthConfig(auth.client, evalId, groundTruthId);

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });
    const page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
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
    page.on("request", (request) => {
      const url = request.url();
      if (
        isGroundTruthApiUrl(url) &&
        ["POST", "PATCH", "PUT", "DELETE"].includes(request.method())
      ) {
        unexpectedMutations.push(`${request.method()} ${url}`);
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isGroundTruthApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponsesDuring(
      page,
      "eval ground truth tab",
      [
        (response) => isEvalDetailResponseFor(response, evalId),
        (response) => isGroundTruthListResponseFor(response, evalId),
        (response) => isGroundTruthConfigResponseFor(response, evalId),
        (response) => isGroundTruthDataResponseFor(response, groundTruthId),
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/evaluations/${evalId}?tab=ground_truth`, {
          waitUntil: "domcontentloaded",
        }),
    );

    await waitForVisibleText(page, evalName, { timeout: 30000 });
    await waitForVisibleText(page, "Ground Truth", { exact: true });
    await waitForVisibleText(page, groundTruthName, { exact: true });
    await waitForVisibleText(page, "2 rows", { exact: true });
    await waitForVisibleText(page, "Pending", { exact: true });
    await waitForVisibleText(page, "Variable Mapping", { exact: true });
    await waitForVisibleText(page, "{{question}}", { exact: true });
    await waitForVisibleText(page, "{{answer}}", { exact: true });
    await waitForVisibleText(page, "{{expected_output}}", { exact: true });
    await waitForVisibleText(page, "Role Mapping");
    await waitForVisibleText(page, "Expected Output", { exact: true });
    await waitForVisibleText(page, "Injection Settings", { exact: true });
    await waitForVisibleText(page, "Enabled", { exact: true });
    await waitForVisibleText(page, "Few-shot examples", { exact: true });
    await waitForVisibleText(page, "Min similarity", { exact: true });
    await waitForVisibleText(page, "Data Preview", { exact: true });
    await waitForVisibleText(page, "What is 2+2?", { exact: true });
    await waitForVisibleText(page, "Capital of France?", { exact: true });
    await waitForVisibleText(page, "Paris", { exact: true });
    await waitForNoVisibleText(page, "Invalid Date", { exact: true });
    await waitForNoVisibleText(page, "undefined", { exact: true });
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Read-only eval ground-truth smoke fired mutations: ${unexpectedMutations.join("; ")}`,
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
    if (browser) {
      const pages = await browser.pages();
      const page = pages[pages.length - 1];
      await page
        ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
        .catch(() => null);
    }
  } finally {
    if (browser) await browser.close();
    await cleanupGroundTruth(auth.client, groundTruthId).catch((error) => {
      caughtError = appendCleanupError(caughtError, error);
    });
    await cleanupEvalTemplate(auth.client, evalId).catch((error) => {
      caughtError = appendCleanupError(caughtError, error);
    });
  }

  if (caughtError) throw caughtError;
}

async function createLlmEval(client, name) {
  const instructions =
    "Judge whether {{answer}} matches {{expected_output}} for {{question}}. Return pass or fail.";
  const created = await client.post(
    apiPath("/model-hub/eval-templates/create-v2/"),
    {
      name,
      eval_type: "llm",
      instructions,
      messages: [
        { role: "system", content: instructions },
        {
          role: "user",
          content:
            "Question: {{question}}\nAnswer: {{answer}}\nExpected: {{expected_output}}",
        },
      ],
      model: "turing_large",
      output_type: "pass_fail",
      pass_threshold: 0.5,
      description: "Eval ground truth browser smoke.",
      tags: ["api-journey", "eval-ground-truth-ui"],
      check_internet: false,
      template_format: "mustache",
    },
  );
  assert(isUuid(created?.id), "LLM eval create did not return a UUID id.");
  return created;
}

async function uploadGroundTruth(client, evalId, name) {
  const created = await client.post(
    apiPath("/model-hub/eval-templates/{template_id}/ground-truth/upload/", {
      template_id: evalId,
    }),
    {
      name,
      description: "Ground truth rows created by browser smoke.",
      file_name: "eval-ground-truth-smoke.json",
      columns: ["question", "answer", "expected_output"],
      data: [
        {
          question: "What is 2+2?",
          answer: "4",
          expected_output: "4",
        },
        {
          question: "Capital of France?",
          answer: "Paris",
          expected_output: "Paris",
        },
      ],
      variable_mapping: {
        question: "question",
        answer: "answer",
        expected_output: "expected_output",
      },
      role_mapping: {
        input: "question",
        expected_output: "expected_output",
      },
    },
  );
  assert(
    isUuid(created?.id),
    "Ground truth upload did not return a UUID id.",
  );
  return created;
}

async function enableGroundTruthConfig(client, evalId, groundTruthId) {
  const config = await client.put(
    apiPath("/model-hub/eval-templates/{template_id}/ground-truth-config/", {
      template_id: evalId,
    }),
    {
      enabled: true,
      ground_truth_id: groundTruthId,
      mode: "manual",
      max_examples: 2,
      similarity_threshold: 0.4,
      injection_format: "structured",
    },
  );
  assert(
    config?.ground_truth?.ground_truth_id === groundTruthId,
    "Ground truth config did not persist selected dataset.",
  );
  return config;
}

async function cleanupGroundTruth(client, groundTruthId) {
  if (!groundTruthId) return;
  await client.delete(
    apiPath("/model-hub/ground-truth/{ground_truth_id}/", {
      ground_truth_id: groundTruthId,
    }),
    { okStatuses: [200, 404] },
  );
  console.error(`cleanup eval ground truth dataset: ${groundTruthId}`);
}

async function cleanupEvalTemplate(client, templateId) {
  if (!templateId) return;
  await client.post(
    apiPath("/model-hub/eval-templates/bulk-delete/"),
    { template_ids: [templateId] },
    { okStatuses: [200, 404] },
  );
  console.error(`cleanup eval ground truth template: ${templateId}`);
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

async function waitForResponsesDuring(page, label, predicates, action) {
  try {
    await Promise.all([
      ...predicates.map((predicate) =>
        page.waitForResponse(predicate, { timeout: 60000 }),
      ),
      action(),
    ]);
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
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
      return Array.from(document.querySelectorAll("body *")).some((element) => {
        if (!isVisible(element)) return false;
        const textContent = normalized(element.textContent);
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      });
    },
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
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
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
      return !Array.from(document.querySelectorAll("body *")).some(
        (element) => {
          if (!isVisible(element)) return false;
          const textContent = normalized(element.textContent);
          if (exactMatch) return textContent === expectedText;
          return textContent.includes(expectedText);
        },
      );
    },
    { timeout },
    { text, exact },
  );
}

function isGroundTruthApiUrl(url) {
  return (
    url.includes("/model-hub/eval-templates/") ||
    url.includes("/model-hub/ground-truth/")
  );
}

function isEvalDetailResponseFor(response, templateId) {
  return (
    response.url().includes(`/model-hub/eval-templates/${templateId}/detail/`) &&
    response.status() < 400
  );
}

function isGroundTruthListResponseFor(response, templateId) {
  return (
    response.url().includes(`/model-hub/eval-templates/${templateId}/ground-truth/`) &&
    !response.url().includes("/upload/") &&
    response.status() < 400
  );
}

function isGroundTruthConfigResponseFor(response, templateId) {
  return (
    response
      .url()
      .includes(`/model-hub/eval-templates/${templateId}/ground-truth-config/`) &&
    response.status() < 400
  );
}

function isGroundTruthDataResponseFor(response, groundTruthId) {
  if (
    !response
      .url()
      .includes(`/model-hub/ground-truth/${groundTruthId}/data/`) ||
    response.status() >= 400
  ) {
    return false;
  }
  const url = new URL(response.url());
  return url.searchParams.get("page_size") === "500";
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
