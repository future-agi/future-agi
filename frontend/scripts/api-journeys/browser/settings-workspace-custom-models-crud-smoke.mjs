import http from "node:http";
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const ROUTE_MODE =
  process.env.AI_PROVIDERS_ROUTE_MODE === "global" ? "global" : "workspace";
const IS_GLOBAL_ROUTE = ROUTE_MODE === "global";
const SCREENSHOT_PREFIX = `/tmp/settings-${ROUTE_MODE}-custom-model`;
const CALLBACK_HOST =
  process.env.API_JOURNEY_CALLBACK_HOST || "host.docker.internal";
const CREATE_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-create-smoke.png`;
const UPDATE_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-update-smoke.png`;
const DELETE_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-delete-smoke.png`;
const ERROR_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-error-smoke.png`;

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const fakeServer = await createFakeCompletionServer();
  const modelName = `ui_custom_model_${auth.runId}`;
  const rawHeaderValue = `custom-model-header-${auth.runId}`;
  const updatedRawHeaderValue = `custom-model-header-updated-${auth.runId}`;
  const expectedMutations = [];
  const apiFailures = [];
  const pageErrors = [];
  let createdModelId = null;
  let deletedViaUi = false;

  await cleanupModelsByName(auth.client, modelName);

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });

  const page = await browser.newPage();
  await page.setBypassServiceWorker(true);
  await installDomHelpers(page);
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

  page.on("response", (response) => {
    const url = response.url();
    if (
      isCustomModelMutation(url) &&
      ["POST", "PATCH", "DELETE"].includes(response.request().method()) &&
      response.status() < 400
    ) {
      expectedMutations.push(`${response.request().method()} ${url}`);
    }
    if (isCustomModelSettingsUrl(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const expectedPath = IS_GLOBAL_ROUTE
      ? "/dashboard/settings/ai-providers"
      : `/dashboard/settings/workspace/${auth.workspaceId}/ai-providers`;
    const customModelsResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/model-hub/custom-models/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(`${APP_BASE}${expectedPath}`, {
      waitUntil: "domcontentloaded",
    });
    await customModelsResponse;
    await assertCurrentPath(page, expectedPath);
    await waitForVisibleText(page, "Create custom model", { exact: true });

    await clickVisibleButton(page, "Create custom model");
    await waitForVisibleText(page, "Add Model", { exact: true });
    await selectRadioByValue(page, "configure-custom-model");
    await waitForInputByPlaceholder(page, "Enter API base URL");
    await fillInputByLabel(page, "Model Name", modelName, {
      placeholder: "Enter model name",
    });
    await fillInputByLabel(
      page,
      "Input Token Cost Per Million Tokens",
      "0.01",
      { placeholder: "Enter input token cost per million tokens" },
    );
    await fillInputByLabel(
      page,
      "Output Token Cost Per Million Tokens",
      "0.02",
      { placeholder: "Enter output token cost per million tokens" },
    );
    await fillInputByLabel(page, "API Base URL", fakeServer.callbackUrl, {
      placeholder: "Enter API base URL",
    });
    await fillCustomConfigValueIfPresent(page, "api_base", "api-base-header");
    await ensureCustomConfigKey(page, "x_api_key");
    await fillCustomConfigValue(page, "x_api_key", rawHeaderValue);

    const createResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/model-hub/custom_models/create/") &&
        response.request().method() === "POST",
      { timeout: 60000 },
    );
    await clickVisibleButton(page, "Add Custom model");
    await assertHttpResponseOk(await createResponse, "custom model create");

    const createdModel = await waitForCustomModel(auth.client, {
      modelName,
      absent: false,
    });
    createdModelId = createdModel.id;
    assert(createdModelId, "Custom model create did not return a listable id.");
    assertPayloadDoesNotContain(createdModel, [
      rawHeaderValue,
      updatedRawHeaderValue,
    ]);
    assert(
      fakeServer.hitCount() >= 1,
      "Custom model create did not call the fake completion endpoint.",
    );

    await filterCustomModel(page, modelName);
    await waitForVisibleText(page, modelName, { exact: true });
    await waitForNoVisibleText(page, rawHeaderValue);
    await page.screenshot({ path: CREATE_SCREENSHOT_PATH, fullPage: true });

    await clickCardActionButton(page, modelName, 1);
    await waitForVisibleText(page, `Configure ${modelName}`, { exact: true });
    const updatedConfig = {
      api_base: fakeServer.callbackUrl,
      headers: {
        x_api_key: updatedRawHeaderValue,
      },
      custom_provider: true,
    };
    await fillMonacoJson(page, JSON.stringify(updatedConfig, null, 2));

    const updateResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/model-hub/custom_models/edit/") &&
        response.request().method() === "PATCH",
      { timeout: 60000 },
    );
    await clickVisibleButton(page, "Save");
    await assertHttpResponseOk(await updateResponse, "custom model update");

    const updatedModel = await waitForCustomModel(auth.client, {
      modelName,
      absent: false,
    });
    assert(
      updatedModel.id === createdModelId,
      "Custom model update changed the active model id.",
    );
    assertPayloadDoesNotContain(updatedModel, [
      rawHeaderValue,
      updatedRawHeaderValue,
    ]);
    assert(
      fakeServer.hitCount() >= 2,
      "Custom model update did not call the fake completion endpoint.",
    );
    await waitForNoVisibleText(page, updatedRawHeaderValue);
    await page.screenshot({ path: UPDATE_SCREENSHOT_PATH, fullPage: true });

    await clickCardActionButton(page, modelName, 2);
    await waitForVisibleText(page, "Delete API key", { exact: true });
    const deleteResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/model-hub/custom_models/delete/") &&
        response.request().method() === "DELETE",
      { timeout: 60000 },
    );
    await clickVisibleButton(page, "Delete");
    await assertHttpResponseOk(await deleteResponse, "custom model delete");
    await waitForCustomModel(auth.client, { modelName, absent: true });
    deletedViaUi = true;
    await waitForNoVisibleText(page, modelName, { exact: true });
    await page.screenshot({ path: DELETE_SCREENSHOT_PATH, fullPage: true });

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      expectedMutations.length === 3,
      `Expected 3 custom-model mutations, saw ${expectedMutations.length}.`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          route_mode: ROUTE_MODE,
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence: {
            workspace_id: auth.workspaceId,
            model_name: modelName,
            created_model_id: createdModelId,
            fake_server_hit_count: fakeServer.hitCount(),
            create_screenshot: CREATE_SCREENSHOT_PATH,
            update_screenshot: UPDATE_SCREENSHOT_PATH,
            delete_screenshot: DELETE_SCREENSHOT_PATH,
            expected_mutation_count: expectedMutations.length,
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    const debug = await collectDebugState(page);
    await page.screenshot({ path: ERROR_SCREENSHOT_PATH, fullPage: true });
    console.error(
      JSON.stringify(
        {
          status: "failed",
          error: error.message,
          debug,
          error_screenshot: ERROR_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    await browser.close();
    await fakeServer.close();
    if (createdModelId && !deletedViaUi) {
      await deleteCustomModelIds(auth.client, [createdModelId]);
    }
    await cleanupModelsByName(auth.client, modelName);
  }
}

async function createFakeCompletionServer() {
  let hitCount = 0;
  const server = http.createServer((request, response) => {
    hitCount += 1;
    request.resume();
    response.writeHead(200, {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Headers": "*",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
    });
    response.end(
      JSON.stringify({
        choices: [{ message: { content: "custom ok" } }],
      }),
    );
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "0.0.0.0", resolve);
  });

  const { port } = server.address();
  return {
    callbackUrl: `http://${CALLBACK_HOST}:${port}/chat`,
    hitCount: () => hitCount,
    close: () =>
      new Promise((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      }),
  };
}

async function assertHttpResponseOk(response, label) {
  if (response.status() < 400) return;
  const body = await response.text();
  throw new Error(
    `${label} failed with HTTP ${response.status()}: ${body.slice(0, 1000)}`,
  );
}

async function collectDebugState(page) {
  return page.evaluate(() => ({
    path: window.location.pathname,
    selected_radios: Array.from(
      document.querySelectorAll('input[type="radio"]'),
    )
      .filter((input) => input.checked)
      .map((input) => input.value),
    buttons: Array.from(document.querySelectorAll("button"))
      .filter((button) => window.isVisible(button))
      .map((button) => ({
        text: button.textContent.trim(),
        disabled: button.disabled,
      })),
    inputs: Array.from(document.querySelectorAll("input, textarea"))
      .filter((input) => window.isVisible(input))
      .map((input) => ({
        placeholder: input.getAttribute("placeholder") || "",
        disabled: input.disabled,
        readOnly: input.readOnly,
        type: input.getAttribute("type") || input.tagName.toLowerCase(),
        hasValue: Boolean(input.value),
      })),
  }));
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

async function installDomHelpers(page) {
  await page.evaluateOnNewDocument(() => {
    window.isVisible = (element) => {
      if (!element) return false;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    };
  });
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
      return Array.from(document.querySelectorAll("body *")).some((element) => {
        if (!isVisible(element)) return false;
        const textContent = normalized(element.textContent);
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
      const normalized = (value) => String(value || "").trim();
      return !Array.from(document.querySelectorAll("body *")).some(
        (element) => {
          if (!isVisible(element)) return false;
          const textContent = normalized(element.textContent);
          return exactMatch
            ? textContent === expectedText
            : textContent.includes(expectedText);
        },
      );
    },
    { timeout },
    { text, exact },
  );
}

async function assertCurrentPath(page, expectedPath, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (path) => window.location.pathname === path,
    { timeout },
    expectedPath,
  );
}

async function clickVisibleButton(page, label) {
  const buttonHandle = await page.waitForFunction(
    (buttonLabel) =>
      Array.from(document.querySelectorAll("button")).find(
        (button) =>
          isVisible(button) &&
          !button.disabled &&
          button.textContent.trim() === buttonLabel,
      ),
    { timeout: 30000 },
    label,
  );
  const button = buttonHandle.asElement();
  assert(button, `Visible button ${label} was not found.`);
  await button.click();
}

async function selectRadioByValue(page, value) {
  await page.waitForFunction(
    (radioValue) =>
      Array.from(document.querySelectorAll('input[type="radio"]')).find(
        (input) => !input.disabled && input.value === radioValue,
      ),
    { timeout: 30000 },
    value,
  );
  await page.evaluate((radioValue) => {
    const input = Array.from(
      document.querySelectorAll('input[type="radio"]'),
    ).find((candidate) => candidate.value === radioValue);
    input.click();
  }, value);
}

async function waitForInputByPlaceholder(page, placeholder) {
  await page.waitForSelector(`input[placeholder="${placeholder}"]`, {
    visible: true,
    timeout: 30000,
  });
}

async function fillInputByLabel(page, label, value, { placeholder = "" } = {}) {
  const inputHandle = await page.waitForFunction(
    ({ labelText, inputPlaceholder }) => {
      if (inputPlaceholder) {
        const input = Array.from(
          document.querySelectorAll("input, textarea"),
        ).find(
          (candidate) =>
            isVisible(candidate) &&
            !candidate.disabled &&
            candidate.getAttribute("placeholder") === inputPlaceholder,
        );
        if (input) return input;
      }

      const labels = Array.from(document.querySelectorAll("label")).filter(
        (element) =>
          isVisible(element) && element.textContent.trim() === labelText,
      );
      for (const labelElement of labels) {
        const controlId = labelElement.getAttribute("for");
        const directInput = controlId
          ? document.getElementById(controlId)
          : null;
        if (directInput && isVisible(directInput) && !directInput.disabled) {
          return directInput;
        }
        let current = labelElement.parentElement;
        for (let depth = 0; depth < 5 && current; depth += 1) {
          const input = Array.from(
            current.querySelectorAll("input, textarea"),
          ).find((candidate) => isVisible(candidate) && !candidate.disabled);
          if (input) return input;
          current = current.parentElement;
        }
      }
      return null;
    },
    { timeout: 30000 },
    { labelText: label, inputPlaceholder: placeholder },
  );
  const input = inputHandle.asElement();
  assert(input, `Input with label ${label} was not found.`);
  await input.click({ clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.keyboard.type(value);
}

async function fillCustomConfigValue(page, keyName, value) {
  const inputHandle = await page.waitForFunction(
    (expectedKey) => {
      const keyInput = Array.from(document.querySelectorAll("input")).find(
        (input) => isVisible(input) && input.value === expectedKey,
      );
      if (!keyInput) return null;
      let current = keyInput.parentElement;
      for (let depth = 0; depth < 8 && current; depth += 1) {
        const inputs = Array.from(current.querySelectorAll("input")).filter(
          (input) => isVisible(input) && !input.disabled,
        );
        const valueInput = inputs.find((input) => input !== keyInput);
        if (valueInput) return valueInput;
        current = current.parentElement;
      }
      return null;
    },
    { timeout: 30000 },
    keyName,
  );
  const input = inputHandle.asElement();
  assert(
    input,
    `Custom configuration value input for ${keyName} was not found.`,
  );
  await input.click({ clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.keyboard.type(value);
}

async function fillCustomConfigValueIfPresent(page, keyName, value) {
  const hasKey = await page.evaluate(
    (expectedKey) =>
      Array.from(document.querySelectorAll("input")).some(
        (input) => window.isVisible(input) && input.value === expectedKey,
      ),
    keyName,
  );
  if (!hasKey) return false;
  await fillCustomConfigValue(page, keyName, value);
  return true;
}

async function ensureCustomConfigKey(page, keyName) {
  const inputHandle = await page.waitForFunction(
    (expectedKey) => {
      const exactInput = Array.from(document.querySelectorAll("input")).find(
        (input) => isVisible(input) && input.value === expectedKey,
      );
      if (exactInput) return exactInput;

      return Array.from(document.querySelectorAll("input")).find(
        (input) =>
          isVisible(input) &&
          !input.disabled &&
          input.getAttribute("placeholder") === "Enter custom key" &&
          input.value.trim() === "",
      );
    },
    { timeout: 30000 },
    keyName,
  );
  const input = inputHandle.asElement();
  assert(input, `Custom configuration key input for ${keyName} was not found.`);
  const currentValue = await input.evaluate((element) => element.value);
  if (currentValue === keyName) return;
  await input.click({ clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.keyboard.type(keyName);
}

async function fillMonacoJson(page, value) {
  await page.waitForSelector(".monaco-editor textarea", { timeout: 30000 });
  await page.waitForFunction(
    () => window.monaco?.editor?.getModels?.().length > 0,
    { timeout: 30000 },
  );
  await page.evaluate((jsonValue) => {
    const models = window.monaco.editor.getModels();
    models[models.length - 1].setValue(jsonValue);
  }, value);
}

async function filterCustomModel(page, modelName) {
  const searchInput = await page.waitForSelector(
    'input[placeholder="Search AI Provider"]',
    { timeout: 30000 },
  );
  await searchInput.click({ clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.keyboard.type(modelName);
  await waitForVisibleText(page, modelName, { exact: true });
}

async function clickCardActionButton(page, cardTitle, actionIndex) {
  const buttonHandle = await page.waitForFunction(
    ({ title, index }) => {
      const titleElement = Array.from(
        document.querySelectorAll("p, span, div, h1, h2, h3, h4, h5, h6"),
      ).find(
        (element) => isVisible(element) && element.textContent.trim() === title,
      );
      if (!titleElement) return null;
      let current = titleElement.parentElement;
      for (let depth = 0; depth < 8 && current; depth += 1) {
        const buttons = Array.from(current.querySelectorAll("button")).filter(
          (button) => isVisible(button) && !button.disabled,
        );
        if (buttons.length > index) return buttons[index];
        current = current.parentElement;
      }
      return null;
    },
    { timeout: 30000 },
    { title: cardTitle, index: actionIndex },
  );
  const button = buttonHandle.asElement();
  assert(button, `Card action button ${actionIndex} was not found.`);
  await button.click();
}

async function waitForCustomModel(
  client,
  { modelName, absent = false, timeout = 30000 },
) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeout) {
    const models = await listCustomModels(client, modelName);
    const match = models.find((model) => model.user_model_id === modelName);
    if (absent && !match) return null;
    if (!absent && match) return match;
    await new Promise((resolve) => setTimeout(resolve, 750));
  }
  throw new Error(
    absent
      ? `Custom model ${modelName} was still present.`
      : `Custom model ${modelName} did not appear.`,
  );
}

async function listCustomModels(client, modelName) {
  const payload = await client.get(apiPath("/model-hub/custom-models/"), {
    query: {
      page_number: 0,
      page_size: 100,
      search_query: modelName,
    },
    unwrap: false,
  });
  if (Array.isArray(payload?.results)) return payload.results;
  if (Array.isArray(payload?.result?.results)) return payload.result.results;
  if (Array.isArray(payload?.result)) return payload.result;
  return [];
}

async function cleanupModelsByName(client, modelName) {
  const models = await listCustomModels(client, modelName);
  const ids = models
    .filter((model) => model.user_model_id === modelName)
    .map((model) => model.id)
    .filter(Boolean);
  if (ids.length > 0) await deleteCustomModelIds(client, ids);
}

async function deleteCustomModelIds(client, ids) {
  await client.delete(apiPath("/model-hub/custom_models/delete/"), {
    body: { ids },
  });
}

function assertPayloadDoesNotContain(payload, forbiddenValues) {
  const serialized = JSON.stringify(payload || {});
  for (const value of forbiddenValues) {
    assert(
      !serialized.includes(value),
      "Custom model API payload exposed raw browser-submitted key material.",
    );
  }
}

function isCustomModelSettingsUrl(url) {
  return (
    url.includes("/model-hub/custom-models/") ||
    url.includes("/model-hub/custom_models/")
  );
}

function isCustomModelMutation(url) {
  return (
    url.includes("/model-hub/custom_models/create/") ||
    url.includes("/model-hub/custom_models/edit/") ||
    url.includes("/model-hub/custom_models/delete/")
  );
}

function isVisible(element) {
  if (!element) return false;
  const style = window.getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  return (
    style.visibility !== "hidden" &&
    style.display !== "none" &&
    rect.width > 0 &&
    rect.height > 0
  );
}

function browserExecutablePath() {
  return (
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  );
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
