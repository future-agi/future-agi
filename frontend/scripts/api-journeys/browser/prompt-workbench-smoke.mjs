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
const FOLDER_SCREENSHOT_PATH = "/tmp/prompt-workbench-folder-smoke.png";
const DETAIL_SCREENSHOT_PATH = "/tmp/prompt-workbench-detail-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/prompt-workbench-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const suffix = shortRunId(auth.runId);
  const folderName = `ui_prompt_folder_${suffix}`;
  const promptName = `ui prompt workbench ${suffix}`;
  const promptText = `Hello {{customer}} from ${auth.runId}`;
  let folderId = null;
  let promptId = null;
  let browser = null;
  let caughtError = null;

  const apiFailures = [];
  const pageErrors = [];
  const promptRequests = [];
  const evidence = { folder_name: folderName, prompt_name: promptName };

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
      if (isPromptWorkbenchApiUrl(request.url())) {
        promptRequests.push(`${request.method()} ${request.url()}`);
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isPromptWorkbenchApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponsesDuring(
      page,
      "initial prompt workbench load",
      [
        (response) =>
          response.url().includes("/model-hub/prompt-folders/") &&
          response.status() < 400,
        (response) =>
          response.url().includes("/model-hub/prompt-executions/") &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/workbench/all`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/workbench/all");
    await waitForVisibleText(page, "All Prompts", { exact: true });
    await waitForVisibleText(page, "My templates", { exact: true });
    await waitForVisibleText(page, "New Folder", { exact: true });
    await waitForVisibleText(page, "Create prompt", { exact: true });

    const folderResponse = await waitForResponseDuring(
      page,
      "UI folder create",
      (response) =>
        response.url().includes("/model-hub/prompt-folders/") &&
        response.request().method() === "POST" &&
        response.status() < 400,
      async () => {
        await clickVisibleText(page, "New Folder", { exact: true });
        await waitForVisibleText(page, "Create new folder", { exact: true });
        await typeIntoVisibleInput(page, folderName);
        await clickVisibleText(page, "Create", { exact: true });
      },
    );
    const folderPayload = unwrapResult(await responseJson(folderResponse));
    folderId =
      folderPayload?.id ||
      folderPayload?.uuid ||
      (await parseWorkbenchFolderId(page));
    if (!isUuid(folderId)) {
      await waitForFunction(page, () =>
        /^\/dashboard\/workbench\/[0-9a-f-]{36}$/i.test(
          window.location.pathname,
        ),
      );
      folderId = await parseWorkbenchFolderId(page);
    }
    assert(
      isUuid(folderId),
      "Workbench UI folder create did not expose a UUID id.",
    );
    evidence.folder_id = folderId;
    await waitForPath(page, `/dashboard/workbench/${folderId}`);
    await waitForVisibleText(page, folderName, { exact: true });

    promptId = await createWorkbenchPrompt(auth.client, {
      folderId,
      name: promptName,
      runId: auth.runId,
      promptText,
    });
    evidence.prompt_id = promptId;

    await waitForResponseDuring(
      page,
      "folder prompt list",
      (response) => {
        if (
          !response.url().includes("/model-hub/prompt-executions/") ||
          response.status() >= 400
        ) {
          return false;
        }
        const url = new URL(response.url());
        return url.searchParams.get("prompt_folder") === folderId;
      },
      () =>
        page.goto(`${APP_BASE}/dashboard/workbench/${folderId}`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForVisibleText(page, folderName, { exact: true });
    await waitForVisibleText(page, promptName, { exact: true });
    await waitForVisibleText(page, "No.of prompts: 1", { exact: true });
    await page.screenshot({ path: FOLDER_SCREENSHOT_PATH, fullPage: true });
    evidence.folder_screenshot = FOLDER_SCREENSHOT_PATH;

    await waitForResponseDuring(
      page,
      "global prompt search",
      (response) => {
        if (
          !response.url().includes("/model-hub/prompt-executions/") ||
          response.status() >= 400
        ) {
          return false;
        }
        const url = new URL(response.url());
        return (
          url.searchParams.get("name") === promptName &&
          url.searchParams.get("send_all") === "true"
        );
      },
      () => typeSearch(page, promptName),
    );
    await waitForVisibleText(page, promptName, { exact: true });
    await waitForVisibleText(page, folderName, { exact: true });

    await waitForResponseDuring(
      page,
      "prompt detail load",
      (response) =>
        response.url().includes(`/model-hub/prompt-templates/${promptId}/`) &&
        response.status() < 400,
      () => clickPromptItem(page, promptId, promptName),
    );
    await waitForPath(page, `/dashboard/workbench/create/${promptId}`);
    await waitForVisibleText(page, promptName);
    await waitForVisibleText(page, "Playground", { exact: true });
    await waitForVisibleText(page, "Evaluation", { exact: true });
    await waitForVisibleText(page, "Metrics", { exact: true });
    await waitForEditorText(page, ["Hello", "customer", auth.runId]);
    await waitForNoVisibleText(page, "Invalid Date");
    await page.screenshot({ path: DETAIL_SCREENSHOT_PATH, fullPage: true });
    evidence.detail_screenshot = DETAIL_SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence,
          prompt_request_count: promptRequests.length,
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
          prompt_requests: promptRequests,
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
    await cleanupPromptTemplate(auth.client, promptId).catch((error) => {
      caughtError = appendCleanupError(caughtError, error);
    });
    await cleanupPromptFolder(auth.client, folderId).catch((error) => {
      caughtError = appendCleanupError(caughtError, error);
    });
  }

  if (caughtError) throw caughtError;
}

async function createWorkbenchPrompt(
  client,
  { folderId, name, runId, promptText },
) {
  const created = await client.post(
    apiPath("/model-hub/prompt-templates/create-draft/"),
    {
      name,
      description: "Prompt Workbench browser smoke candidate.",
      prompt_folder: folderId,
      variable_names: { customer: ["Ada"] },
      metadata: { source: "api-journey-browser", run_id: runId },
      prompt_config: [
        {
          messages: [
            {
              role: "system",
              content: [{ type: "text", text: "You are a concise assistant." }],
            },
            {
              role: "user",
              content: [{ type: "text", text: promptText }],
            },
          ],
          configuration: {
            model: "gpt-4o-mini",
            model_detail: { type: "chat" },
            template_format: "mustache",
          },
          placeholders: [],
        },
      ],
    },
  );
  const promptId =
    created?.id || created?.root_template || created?.rootTemplate;
  assert(isUuid(promptId), "Workbench prompt create did not return a UUID id.");
  return promptId;
}

async function cleanupPromptTemplate(client, promptId) {
  if (!promptId) return;
  try {
    await client.post(
      apiPath("/model-hub/prompt-templates/bulk-delete/"),
      { ids: [promptId] },
      { okStatuses: [200, 404] },
    );
    console.error(`cleanup prompt workbench prompt: ${promptId}`);
  } catch (error) {
    if (String(error?.message || "").includes("No valid ids provided")) return;
    throw error;
  }
}

async function cleanupPromptFolder(client, folderId) {
  if (!folderId) return;
  await client.delete(
    apiPath("/model-hub/prompt-folders/{id}/", { id: folderId }),
    {
      okStatuses: [200, 204, 404],
    },
  );
  console.error(`cleanup prompt workbench folder: ${folderId}`);
}

function appendCleanupError(caughtError, cleanupError) {
  if (!caughtError) return cleanupError;
  caughtError.message = `${caughtError.message}; cleanup failed: ${cleanupError.message}`;
  return caughtError;
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

async function waitForFunction(page, fn, timeout = 30000) {
  await page.waitForFunction(fn, { timeout });
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

async function waitForEditorText(page, fragments, timeout = 30000) {
  await page.waitForFunction(
    (expectedFragments) => {
      const editors = window.visibleElements(".ql-editor");
      return editors.some((editor) => {
        const textContent = window.normalizeText(editor.textContent);
        return expectedFragments.every((fragment) =>
          textContent.includes(fragment),
        );
      });
    },
    { timeout },
    fragments,
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

async function typeIntoVisibleInput(page, value) {
  await page.waitForFunction(
    () => window.visibleElements("input").some((input) => !input.disabled),
    { timeout: 30000 },
  );
  const inputs = await page.$$("input");
  for (const input of inputs) {
    const visible = await input.evaluate((element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        !element.disabled &&
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    });
    if (!visible) continue;
    await input.click({ clickCount: 3 });
    await page.keyboard.press("Backspace");
    await page.type("input:focus", value);
    return;
  }
  throw new Error("No visible input found.");
}

async function typeSearch(page, value) {
  const selector = 'input[placeholder="Search in prompts"]';
  await page.waitForSelector(selector, { timeout: 30000 });
  await page.click(selector);
  await page.keyboard.down(modifierKey());
  await page.keyboard.press("A");
  await page.keyboard.up(modifierKey());
  await page.keyboard.press("Backspace");
  await page.type(selector, value);
}

async function clickPromptItem(page, promptId, promptName) {
  await page.waitForFunction(
    ({ id, name }) =>
      window
        .visibleElements(`a[href$="/dashboard/workbench/create/${id}"]`)
        .some((anchor) =>
          window.normalizeText(anchor.textContent).includes(name),
        ),
    { timeout: 30000 },
    { id: promptId, name: promptName },
  );
  const clicked = await page.evaluate(
    ({ id, name }) => {
      const anchor = window
        .visibleElements(`a[href$="/dashboard/workbench/create/${id}"]`)
        .find((candidate) =>
          window.normalizeText(candidate.textContent).includes(name),
        );
      if (!anchor) return false;
      anchor.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
      );
      anchor.dispatchEvent(
        new MouseEvent("mouseup", { bubbles: true, cancelable: true }),
      );
      anchor.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
      return true;
    },
    { id: promptId, name: promptName },
  );
  assert(clicked, `Could not click prompt item: ${promptName}.`);
}

async function responseJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function unwrapResult(body) {
  if (body && Object.prototype.hasOwnProperty.call(body, "result")) {
    return body.result;
  }
  if (body && Object.prototype.hasOwnProperty.call(body, "data")) {
    return unwrapResult(body.data);
  }
  return body;
}

async function parseWorkbenchFolderId(page) {
  const pathname = await page.evaluate(() => window.location.pathname);
  const match = pathname.match(/\/dashboard\/workbench\/([0-9a-f-]{36})$/i);
  return match?.[1] || null;
}

function isPromptWorkbenchApiUrl(url) {
  return url.includes("/model-hub/prompt-");
}

function shortRunId(runId) {
  return String(runId || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .slice(-8);
}

function modifierKey() {
  return process.platform === "darwin" ? "Meta" : "Control";
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
