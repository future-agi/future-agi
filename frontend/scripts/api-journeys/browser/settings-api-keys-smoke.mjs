import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/settings-api-keys-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const keyName = `browser key ${Date.now()}`;
  const apiFailures = [];
  const pageErrors = [];
  let createdKey = null;

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });

  const page = await browser.newPage();
  await installRuntimeConfig(page, auth);
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      if (organizationId) sessionStorage.setItem("organizationId", organizationId);
      if (workspaceId) sessionStorage.setItem("workspaceId", workspaceId);
      if (user?.id) sessionStorage.setItem("futureagi-current-user-id", user.id);
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
    if (url.includes("/accounts/key/") && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const listResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/accounts/key/get_secret_keys/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(`${APP_BASE}/dashboard/settings/api_keys`, {
      waitUntil: "domcontentloaded",
    });
    await listResponse;

    await waitForVisibleText(page, "Your secret API keys are listed below");
    await clickVisibleText(page, "Add API Key");
    await waitForVisibleText(page, "Key Name", { exact: true });
    await typeIntoInput(page, "Enter your key name", keyName);

    const createResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/accounts/key/generate_secret_key/") &&
        response.request().method() === "POST" &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await clickVisibleText(page, "Next", { exact: true });
    const createHttpResponse = await createResponse;
    const createBody = await createHttpResponse.json();
    createdKey = createBody?.result || null;
    assert(createdKey?.key_id, "Create API key response did not include key_id.");
    assertRawKey(createdKey.api_key, "created api_key");
    assertRawKey(createdKey.secret_key, "created secret_key");
    assertMaskedKey(createdKey.masked_api_key, "created masked_api_key");
    assertMaskedKey(createdKey.masked_secret_key, "created masked_secret_key");

    await waitForVisibleText(page, "Generated");
    await waitForInputValue(page, createdKey.masked_api_key);
    await waitForInputValue(page, createdKey.masked_secret_key);
    await assertRawKeysNotRendered(page, createdKey);

    await clickVisibleText(page, "Done", { exact: true });

    await waitForVisibleText(page, keyName);
    await waitForVisibleText(page, createdKey.masked_api_key);
    await waitForVisibleText(page, createdKey.masked_secret_key);
    await assertRawKeysNotRendered(page, createdKey);
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

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
          evidence: {
            key_name: keyName,
            key_id: createdKey.key_id,
            list_api_key_masked: true,
            list_secret_key_masked: true,
            screenshot: SCREENSHOT_PATH,
          },
        },
        null,
        2,
      ),
    );
  } finally {
    if (createdKey?.key_id) {
      await auth.client.delete(apiPath("/accounts/key/delete_secret_key/"), {
        body: { key_id: createdKey.key_id },
      });
    }
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

async function clickVisibleText(page, text, { exact = false } = {}) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
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
      return Array.from(document.querySelectorAll("button,a,[role='button']")).some(
        (candidate) => {
          if (!isVisible(candidate)) return false;
          const textContent = String(candidate.textContent || "").trim();
          return exactMatch
            ? textContent === expectedText
            : textContent.includes(expectedText);
        },
      );
    },
    { timeout: 30000 },
    { text, exact },
  );
  await page.evaluate(
    ({ text: expectedText, exact: exactMatch }) => {
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
      const element = Array.from(
        document.querySelectorAll("button,a,[role='button']"),
      ).find((candidate) => {
        if (!isVisible(candidate)) return false;
        const textContent = String(candidate.textContent || "").trim();
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
      element?.click();
    },
    { text, exact },
  );
}

async function typeIntoInput(page, placeholder, value) {
  const selector = `input[placeholder="${placeholder}"]`;
  await page.waitForSelector(selector, { timeout: 30000 });
  await page.click(selector);
  await page.keyboard.type(value);
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
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

async function waitForInputValue(page, value) {
  await page.waitForFunction(
    (expectedValue) =>
      Array.from(document.querySelectorAll("input, textarea")).some(
        (element) => element.value === expectedValue,
      ),
    { timeout: 30000 },
    value,
  );
}

async function assertRawKeysNotRendered(page, key) {
  const rendered = await page.evaluate(() => {
    const values = Array.from(document.querySelectorAll("input, textarea"))
      .map((element) => element.value)
      .join("\n");
    return `${document.body.innerText}\n${values}`;
  });
  assert(!rendered.includes(key.api_key), "Page rendered the raw API key.");
  assert(!rendered.includes(key.secret_key), "Page rendered the raw secret key.");
}

function assertRawKey(value, label) {
  assert(/^[0-9a-f]{32}$/i.test(String(value || "")), `${label} was not raw key material.`);
}

function assertMaskedKey(value, label) {
  const text = String(value || "");
  assert(text.includes("*"), `${label} was not masked.`);
  assert(!/^[0-9a-f]{32}$/i.test(text), `${label} exposed raw key material.`);
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
