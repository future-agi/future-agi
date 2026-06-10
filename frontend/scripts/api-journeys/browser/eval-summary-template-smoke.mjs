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
const SCREENSHOT_PATH = "/tmp/eval-summary-template-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/eval-summary-template-smoke-failure.png";
const TEMPLATE_PREFIX = "ui_summary_template_";
const EVAL_PREFIX = "ui_eval_summary_template_";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  const auth = await createAuthenticatedContext();
  const suffix = shortRunId(auth.runId);
  const evalName = `${EVAL_PREFIX}${suffix}`;
  const templateName = `${TEMPLATE_PREFIX}${suffix}`;
  const templateDescription = `Browser summary template ${suffix}`;
  const templateCriteria = `Summarize output quality for ${suffix}.`;
  const updatedName = `${templateName}_updated`;
  const updatedDescription = `${templateDescription} updated`;
  const updatedCriteria = `${templateCriteria} Include severity buckets.`;

  const apiFailures = [];
  const pageErrors = [];
  const unexpectedMutations = [];
  const summaryMutations = [];
  const consoleMessages = [];
  let evalId = null;
  let summaryTemplateId = null;
  let browser = null;
  let caughtError = null;

  try {
    await deleteSummaryTemplatesByPrefix(auth.client, TEMPLATE_PREFIX);
    const createdEval = await createAgentEval(auth.client, evalName);
    evalId = createdEval.id;

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
      if (!isModelHubApiUrl(url) || !MUTATION_METHODS.has(request.method())) {
        return;
      }
      const parsed = new URL(url);
      if (parsed.pathname.startsWith("/model-hub/eval-summary-templates/")) {
        summaryMutations.push({
          method: request.method(),
          pathname: parsed.pathname,
          body: parseJsonMaybe(request.postData()),
        });
        return;
      }
      unexpectedMutations.push(`${request.method()} ${parsed.pathname}`);
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isRelevantApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${new URL(url).pathname}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("console", (message) => {
      if (["error", "warning"].includes(message.type())) {
        consoleMessages.push(`${message.type()}: ${message.text()}`);
      }
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
    await openSummaryMenu(page);
    await waitForVisibleText(page, "Create custom template", { exact: true });

    await clickMenuItem(page, "Create custom template");
    await fillAriaField(page, "Summary template name", templateName);
    await fillAriaField(
      page,
      "Summary template description",
      templateDescription,
    );
    await fillAriaField(page, "Summary template criteria", templateCriteria);
    const createResponse = await waitForSummaryMutationDuring(
      page,
      "POST",
      () => clickAriaButton(page, "Save summary template"),
    );
    const createPayload = await responseJson(createResponse);
    const createdTemplate = createPayload?.result || createPayload;
    summaryTemplateId = createdTemplate?.id;
    assert(
      isUuid(summaryTemplateId),
      "Browser summary template create did not return a UUID id.",
    );
    assertSummaryTemplatePayload(createdTemplate, {
      id: summaryTemplateId,
      name: templateName,
      description: templateDescription,
      criteria: templateCriteria,
      label: "create response",
    });

    await waitForVisibleText(page, templateName, { exact: true });
    await waitForVisibleText(page, templateCriteria, { exact: true });
    await clickMenuItem(page, templateName);
    await waitForVisibleText(page, templateName, { exact: true });

    await openSummaryChipMenu(page, templateName);
    await clickAriaButton(page, `Edit summary template ${templateName}`);
    await fillAriaField(page, "Summary template name", updatedName);
    await fillAriaField(
      page,
      "Summary template description",
      updatedDescription,
    );
    await fillAriaField(page, "Summary template criteria", updatedCriteria);
    const updateResponse = await waitForSummaryMutationDuring(page, "PUT", () =>
      clickAriaButton(page, "Save summary template"),
    );
    const updatePayload = await responseJson(updateResponse);
    const updatedTemplate = updatePayload?.result || updatePayload;
    assertSummaryTemplatePayload(updatedTemplate, {
      id: summaryTemplateId,
      name: updatedName,
      description: updatedDescription,
      criteria: updatedCriteria,
      label: "update response",
    });

    await waitForVisibleText(page, updatedName, { exact: true });
    await waitForVisibleText(page, updatedCriteria, { exact: true });

    if (
      !(await hasAriaButton(page, `Delete summary template ${updatedName}`))
    ) {
      await openSummaryChipMenu(page, updatedName);
    }
    const deleteResponse = await waitForSummaryMutationDuring(
      page,
      "DELETE",
      () => clickAriaButton(page, `Delete summary template ${updatedName}`),
    );
    const deletePayload = await responseJson(deleteResponse);
    const deleteResult = deletePayload?.result || deletePayload;
    assert(
      deleteResult?.deleted === true,
      "Browser summary template delete did not return deleted=true.",
    );
    await waitForNoVisibleText(page, updatedName, { exact: true });
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    const residue = await listSummaryTemplates(auth.client);
    const residueRows = residue.filter((template) =>
      String(template?.name || "").startsWith(TEMPLATE_PREFIX),
    );
    assert(
      residueRows.length === 0,
      `Summary template residue remained: ${JSON.stringify(residueRows)}`,
    );
    summaryTemplateId = null;

    assertExpectedBrowserMutations(summaryMutations, {
      templateId: createdTemplate.id,
      create: {
        name: templateName,
        description: templateDescription,
        criteria: templateCriteria,
      },
      update: {
        name: updatedName,
        description: updatedDescription,
        criteria: updatedCriteria,
      },
    });
    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Unexpected model-hub browser mutations: ${unexpectedMutations.join("; ")}`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          eval_id: evalId,
          eval_name: evalName,
          summary_template_id: createdTemplate.id,
          created_name: templateName,
          updated_name: updatedName,
          browser_mutations: summaryMutations.map((mutation) => ({
            method: mutation.method,
            pathname: maskTemplatePath(mutation.pathname, createdTemplate.id),
            body: mutation.body,
          })),
          residue_count: residueRows.length,
          screenshot: SCREENSHOT_PATH,
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
          eval_id: evalId,
          summary_template_id: summaryTemplateId,
          api_failures: apiFailures,
          page_errors: pageErrors,
          unexpected_mutations: unexpectedMutations,
          summary_mutations: summaryMutations,
          console_messages: consoleMessages.slice(-20),
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
    if (summaryTemplateId) {
      await deleteSummaryTemplate(auth.client, summaryTemplateId).catch(
        (error) => {
          caughtError = appendCleanupError(caughtError, error);
        },
      );
    }
    await deleteSummaryTemplatesByPrefix(auth.client, TEMPLATE_PREFIX).catch(
      (error) => {
        caughtError = appendCleanupError(caughtError, error);
      },
    );
    await cleanupEvalTemplate(auth.client, evalId).catch((error) => {
      caughtError = appendCleanupError(caughtError, error);
    });
  }

  if (caughtError) throw caughtError;
}

async function createAgentEval(client, name) {
  const instructions =
    "Summarize {{output}} and return pass when the output is acceptable.";
  const created = await client.post(
    apiPath("/model-hub/eval-templates/create-v2/"),
    {
      name,
      eval_type: "agent",
      instructions,
      model: "turing_large",
      output_type: "pass_fail",
      pass_threshold: 0.5,
      description: "Eval summary template browser smoke.",
      tags: ["api-journey", "eval-summary-template-ui"],
      check_internet: false,
      mode: "agent",
      summary: { type: "concise" },
      template_format: "mustache",
    },
  );
  assert(isUuid(created?.id), "Agent eval create did not return a UUID id.");
  return created;
}

async function cleanupEvalTemplate(client, templateId) {
  if (!templateId) return;
  await client.post(
    apiPath("/model-hub/eval-templates/bulk-delete/"),
    { template_ids: [templateId] },
    { okStatuses: [200, 404] },
  );
  console.error(`cleanup eval summary template smoke eval: ${templateId}`);
}

async function listSummaryTemplates(client) {
  const payload = await client.get(
    apiPath("/model-hub/eval-summary-templates/"),
  );
  return Array.isArray(payload?.templates) ? payload.templates : [];
}

async function deleteSummaryTemplatesByPrefix(client, prefix) {
  const templates = await listSummaryTemplates(client);
  const matches = templates.filter((template) =>
    String(template?.name || "").startsWith(prefix),
  );
  await Promise.all(
    matches.map((template) => deleteSummaryTemplate(client, template.id)),
  );
  return matches.length;
}

async function deleteSummaryTemplate(client, templateId) {
  if (!templateId) return;
  await client.delete(
    apiPath("/model-hub/eval-summary-templates/{template_id}/", {
      template_id: templateId,
    }),
    { okStatuses: [200, 404] },
  );
  console.error(`cleanup eval summary template: ${templateId}`);
}

async function openSummaryMenu(page) {
  if (await hasVisibleText(page, "Create custom template", { exact: true })) {
    return;
  }
  if (!(await hasVisibleText(page, "Use Internet"))) {
    await clickAriaButton(page, "Open eval runtime settings");
    await waitForVisibleText(page, "Use Internet");
  }
  const listResponsePromise = page
    .waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        response.url().includes("/model-hub/eval-summary-templates/") &&
        response.status() < 400,
      { timeout: 30000 },
    )
    .catch(() => null);
  await clickMenuItem(page, "Summary");
  await listResponsePromise;
  await waitForVisibleText(page, "Create custom template", { exact: true });
}

async function openSummaryChipMenu(page, label) {
  if (await hasVisibleText(page, "Create custom template", { exact: true })) {
    return;
  }
  await clickVisibleText(page, label, { exact: true });
  await waitForVisibleText(page, "Create custom template", { exact: true });
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

async function waitForResponseDuring(page, label, predicate, action) {
  const responsePromise = page.waitForResponse(predicate, { timeout: 60000 });
  await action();
  const response = await responsePromise;
  assert(response, `${label} did not receive expected response.`);
  return response;
}

async function waitForSummaryMutation(page, method) {
  return page.waitForResponse(
    (response) =>
      response.request().method() === method &&
      response.url().includes("/model-hub/eval-summary-templates/") &&
      response.status() < 400,
    { timeout: 30000 },
  );
}

async function waitForSummaryMutationDuring(page, method, action) {
  const responsePromise = waitForSummaryMutation(page, method);
  await action();
  return responsePromise;
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      return window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
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
      return !window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

async function hasVisibleText(page, text, { exact = false } = {}) {
  return page.evaluate(
    ({ text: expectedText, exact: exactMatch }) => {
      return window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
    },
    { text, exact },
  );
}

async function hasAriaButton(page, ariaLabel) {
  return page.evaluate((label) => {
    return window
      .visibleElements("button")
      .some(
        (button) =>
          button.getAttribute("aria-label") === label && !button.disabled,
      );
  }, ariaLabel);
}

async function clickVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await waitForVisibleText(page, text, { exact, timeout });
  const clicked = await page.evaluate(
    ({ text: expectedText, exact: exactMatch }) => {
      const match = window.visibleElements().find((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
      if (!match) return false;
      window.dispatchClick(match);
      return true;
    },
    { text, exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

async function clickMenuItem(page, text, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (expectedText) =>
      window
        .visibleElements('[role="menuitem"]')
        .some((element) =>
          window.normalizeText(element.textContent).includes(expectedText),
        ),
    { timeout },
    text,
  );
  const clicked = await page.evaluate((expectedText) => {
    const menuItem = window
      .visibleElements('[role="menuitem"]')
      .find((element) =>
        window.normalizeText(element.textContent).includes(expectedText),
      );
    if (!menuItem) return false;
    window.dispatchClick(menuItem);
    return true;
  }, text);
  assert(clicked, `Could not click menu item: ${text}`);
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

async function fillAriaField(page, ariaLabel, value) {
  await page.waitForFunction(
    (label) =>
      window
        .visibleElements("input, textarea")
        .some((field) => field.getAttribute("aria-label") === label),
    { timeout: 30000 },
    ariaLabel,
  );
  const filledValue = await page.evaluate(
    ({ label, nextValue }) => {
      const field = window
        .visibleElements("input, textarea")
        .find((candidate) => candidate.getAttribute("aria-label") === label);
      if (!field) return null;
      field.focus();
      const prototype =
        field.tagName === "TEXTAREA"
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype;
      const valueSetter = Object.getOwnPropertyDescriptor(
        prototype,
        "value",
      )?.set;
      valueSetter.call(field, nextValue);
      field.dispatchEvent(
        new InputEvent("input", {
          bubbles: true,
          inputType: "insertText",
          data: nextValue,
        }),
      );
      field.dispatchEvent(new Event("change", { bubbles: true }));
      return field.value;
    },
    { label: ariaLabel, nextValue: value },
  );
  assert(
    filledValue === value,
    `Failed to fill ${ariaLabel}; got ${JSON.stringify(filledValue)}.`,
  );
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

function assertSummaryTemplatePayload(
  payload,
  { id, name, description, criteria, label },
) {
  assert(payload?.id === id, `${label} id mismatch.`);
  assert(payload?.name === name, `${label} name mismatch.`);
  assert(
    payload?.description === description,
    `${label} description mismatch.`,
  );
  assert(payload?.criteria === criteria, `${label} criteria mismatch.`);
}

function assertExpectedBrowserMutations(
  mutations,
  { templateId, create, update },
) {
  const methods = mutations.map((mutation) => mutation.method);
  assert(
    JSON.stringify(methods) === JSON.stringify(["POST", "PUT", "DELETE"]),
    `Expected POST, PUT, DELETE summary mutations, got ${methods.join(", ")}.`,
  );
  assertPayloadMatches(mutations[0].body, create, "create request");
  assert(
    mutations[1].pathname.endsWith(`/${templateId}/`),
    "Update request path did not target the created template.",
  );
  assertPayloadMatches(mutations[1].body, update, "update request");
  assert(
    mutations[2].pathname.endsWith(`/${templateId}/`),
    "Delete request path did not target the updated template.",
  );
}

function assertPayloadMatches(actual, expected, label) {
  for (const [key, value] of Object.entries(expected)) {
    assert(actual?.[key] === value, `${label} ${key} mismatch.`);
  }
}

function isModelHubApiUrl(rawUrl) {
  const url = new URL(rawUrl);
  return (
    url.origin ===
      new URL(process.env.API_BASE || "http://localhost:8003").origin &&
    url.pathname.startsWith("/model-hub/")
  );
}

function isRelevantApiUrl(rawUrl) {
  const url = new URL(rawUrl);
  return (
    url.origin ===
      new URL(process.env.API_BASE || "http://localhost:8003").origin &&
    (url.pathname.startsWith("/model-hub/eval-summary-templates/") ||
      url.pathname.startsWith("/model-hub/eval-templates/"))
  );
}

function parseJsonMaybe(value) {
  if (!value) return null;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function maskTemplatePath(pathname, templateId) {
  return String(pathname).replace(templateId, "<summary-template-id>");
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
