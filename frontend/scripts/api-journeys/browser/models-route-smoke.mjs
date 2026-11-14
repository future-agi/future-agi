/* eslint-disable no-console */
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/models-route-smoke.png";
const DETAIL_SCREENSHOT_PATH = "/tmp/models-route-detail-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/models-route-smoke-failure.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const modelsPayload = await auth.client.get(
    apiPath("/model-hub/custom-models/"),
    { unwrap: false },
  );
  const models = asArray(modelsPayload?.results || modelsPayload);
  const firstModel = models.find((model) => isUuid(model?.id)) || null;
  const firstModelName = modelDisplayName(firstModel);
  const apiFailures = [];
  const pageErrors = [];
  const evidence = {
    model_count: models.length,
    first_model_id: firstModel?.id || null,
    first_model_name: firstModelName || null,
  };

  const browser = await puppeteer.launch({
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
      if (organizationId) {
        sessionStorage.setItem("organizationId", organizationId);
      }
      if (workspaceId) {
        sessionStorage.setItem("workspaceId", workspaceId);
      }
      if (user?.id) {
        sessionStorage.setItem("futureagi-current-user-id", user.id);
      }
    },
    {
      tokens: auth.tokens,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      user: auth.user,
    },
  );

  page.on("response", (response) => {
    const path = safePathname(response.url());
    if (
      path?.startsWith("/model-hub/custom-models/") &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${response.url()}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await waitForResponseDuring(
      page,
      "models list",
      (response) =>
        response.url().includes("/model-hub/custom-models/") &&
        !response.url().includes("/model-hub/custom-models/list/") &&
        response.request().method() === "GET" &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/models`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await page.waitForFunction(
      () => window.location.pathname === "/dashboard/models",
      { timeout: 30000 },
    );
    await waitForVisibleText(page, "Models", { exact: true });
    await waitForVisibleText(page, "Add Model", { exact: true });

    if (firstModel) {
      await waitForVisibleText(page, firstModelName);
      await waitForResponseDuring(
        page,
        "model detail",
        (response) =>
          response
            .url()
            .includes(`/model-hub/custom-models/${firstModel.id}/`) &&
          response.request().method() === "GET" &&
          response.status() < 400,
        () => clickVisibleText(page, firstModelName),
      );
      await page.waitForFunction(
        (modelId) =>
          window.location.pathname ===
          `/dashboard/models/${modelId}/performance`,
        { timeout: 30000 },
        firstModel.id,
      );
      await waitForVisibleText(page, "Performance", { exact: true });
      await waitForVisibleText(page, "Custom Metrics", { exact: true });
      await waitForVisibleText(page, "Datasets", { exact: true });
      await page.screenshot({ path: DETAIL_SCREENSHOT_PATH, fullPage: true });
      evidence.detail_screenshot = DETAIL_SCREENSHOT_PATH;
    } else {
      await waitForVisibleText(page, "You need to create a Model.", {
        exact: true,
      });
      evidence.detail_skipped = "No model rows returned by the real list API.";
    }

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

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
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await page.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true });
    console.error(
      JSON.stringify(
        {
          status: "failed",
          error: error.message,
          debug: await collectDebugState(page),
          error_screenshot: FAILURE_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    await browser.close();
  }
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

async function waitForResponseDuring(page, label, predicate, action) {
  const responsePromise = page.waitForResponse(predicate, { timeout: 60000 });
  await action();
  const response = await responsePromise;
  assert(
    response.status() >= 200 && response.status() < 400,
    `${label} response failed with HTTP ${response.status()}.`,
  );
  return response;
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      const normalized = (value) => String(value || "").trim();
      const isElementVisible = (element) => {
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
        if (!isElementVisible(element)) return false;
        const textContent = normalized(element.textContent);
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

async function clickVisibleText(page, text) {
  const handle = await page.waitForFunction(
    (expectedText) => {
      const isElementVisible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      };
      return (
        Array.from(document.querySelectorAll("body *")).find((element) => {
          if (!isElementVisible(element)) return false;
          return String(element.textContent || "").trim() === expectedText;
        }) || null
      );
    },
    { timeout: 30000 },
    text,
  );
  const element = handle.asElement();
  assert(element, `Could not resolve visible text "${text}".`);
  const box = await element.boundingBox();
  assert(box, `Could not resolve visible text box "${text}".`);
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

async function collectDebugState(page) {
  return page.evaluate(() => ({
    path: window.location.pathname,
    visibleText: String(document.body?.innerText || "").slice(0, 3000),
  }));
}

function modelDisplayName(model) {
  return String(model?.user_model_id || model?.name || model?.id || "").trim();
}

function safePathname(url) {
  try {
    return new URL(url).pathname;
  } catch {
    return "";
  }
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
  process.exitCode = 1;
});
