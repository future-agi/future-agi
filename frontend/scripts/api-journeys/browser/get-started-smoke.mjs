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
const SCREENSHOT_PATH = "/tmp/get-started-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const checks = await auth.client.get(apiPath("/accounts/first-checks/"));
  assertGetStartedChecks(checks);

  const providerStatus = await auth.client.get(
    apiPath("/model-hub/develops/provider-status/"),
  );
  const providers = Array.isArray(providerStatus?.providers)
    ? providerStatus.providers
    : [];
  assert(providers.length > 0, "Provider status returned no providers.");
  const configuredTextProvider = providers.find(
    (provider) =>
      provider?.has_key &&
      provider?.type === "text" &&
      typeof provider?.masked_key === "string" &&
      provider.masked_key.includes("*"),
  );
  assert(
    configuredTextProvider,
    "No configured text provider with a masked key is available for Get Started smoke.",
  );
  assertNoRawSecretString(
    JSON.stringify(providerStatus || {}),
    "provider status",
  );

  const customModelsRaw = await auth.client.get(
    apiPath("/model-hub/custom-models/"),
    {
      query: { page_number: 0, page_size: 20 },
      unwrap: false,
    },
  );
  assertNoRawSecretString(
    JSON.stringify(customModelsRaw || {}),
    "custom models",
  );

  const apiFailures = [];
  const pageErrors = [];
  const unexpectedMutations = [];
  const evidence = {
    first_checks: checks,
    provider_count: providers.length,
    configured_provider_count: providers.filter((provider) => provider?.has_key)
      .length,
    checked_provider: configuredTextProvider.provider,
    checked_provider_display_name: configuredTextProvider.display_name,
    checked_provider_masked_key: configuredTextProvider.masked_key,
    custom_model_count:
      customModelsRaw?.count ??
      customModelsRaw?.result?.count ??
      customModelsRaw?.results?.length ??
      0,
  };

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
      isGetStartedApiUrl(url) &&
      ["POST", "PATCH", "PUT", "DELETE"].includes(request.method())
    ) {
      unexpectedMutations.push(`${request.method()} ${url}`);
    }
  });
  page.on("response", (response) => {
    const url = response.url();
    if (isGetStartedApiUrl(url) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await page.goto(`${APP_BASE}/dashboard/get-started`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(
      () => window.location.pathname === "/dashboard/get-started",
      { timeout: 30000 },
    );

    await waitForGetStartedPage(page);
    await waitForVisibleText(page, configuredTextProvider.display_name, {
      exact: true,
    });
    await waitForInputValue(page, configuredTextProvider.masked_key);
    await assertNoRawSecretVisible(page);
    await waitForNoVisibleText(page, "Invalid Date");

    evidence.dataset_navigation_path = await navigateFromGetStarted({
      page,
      buttonText: "Create dataset",
      expectedPath: "/dashboard/develop",
    });
    evidence.experiment_navigation_path = await navigateFromGetStarted({
      page,
      buttonText: "Start experiment",
      expectedPath: "/dashboard/prototype",
    });
    evidence.evaluate_navigation_path = await navigateFromGetStarted({
      page,
      buttonText: "Try evaluations",
      expectedPath: "/dashboard/evaluations",
    });
    evidence.observe_navigation_path = await navigateFromGetStarted({
      page,
      buttonText: "Go to observe",
      expectedPath: "/dashboard/observe",
      beforeClick: async () => {
        await clickVisibleText(page, "Setup observability in application");
        await waitForVisibleText(page, "Set observability in application", {
          exact: true,
        });
      },
    });
    await page.goto(`${APP_BASE}/dashboard/get-started`, {
      waitUntil: "domcontentloaded",
    });
    await waitForGetStartedPage(page);
    await assertNoRawSecretVisible(page);
    await waitForNoVisibleText(page, "Invalid Date");

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Get Started smoke fired mutations: ${unexpectedMutations.join("; ")}`,
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

async function waitForGetStartedPage(page) {
  await page.waitForFunction(
    () => window.location.pathname === "/dashboard/get-started",
    { timeout: 30000 },
  );
  await waitForVisibleText(page, "Get Started with FutureAGI", { exact: true });
  await waitForVisibleText(page, "Initial Setup", { exact: true });
  await waitForVisibleText(page, "Add keys", { exact: true });
  await waitForVisibleText(page, "Create first dataset", { exact: true });
  await waitForVisibleText(page, "Create your first evaluation", {
    exact: true,
  });
  await waitForVisibleText(page, "Run your first experiment", { exact: true });
  await waitForVisibleText(page, "Setup observability in application", {
    exact: true,
  });
  await waitForVisibleText(page, "Try out our features", { exact: true });
  await waitForVisibleText(page, "Add dataset", { exact: true });
  await waitForVisibleText(page, "Experiment", { exact: true });
  await waitForVisibleText(page, "Evaluate", { exact: true });
}

async function navigateFromGetStarted({
  page,
  buttonText,
  expectedPath,
  beforeClick,
}) {
  await page.goto(`${APP_BASE}/dashboard/get-started`, {
    waitUntil: "domcontentloaded",
  });
  await waitForGetStartedPage(page);
  if (beforeClick) await beforeClick();

  const navigation = page.waitForFunction(
    (path) => window.location.pathname === path,
    { timeout: 30000 },
    expectedPath,
  );
  await clickVisibleText(page, buttonText);
  await navigation;
  return page.evaluate(() => window.location.pathname);
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
  { exact = false, timeout = 10000 } = {},
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

async function waitForInputValue(page, expectedValue, timeout = 30000) {
  await page.waitForFunction(
    (value) =>
      Array.from(document.querySelectorAll("input, textarea")).some(
        (element) => element.value === value,
      ),
    { timeout },
    expectedValue,
  );
}

async function clickVisibleText(page, text) {
  await page.waitForFunction(
    (expectedText) => {
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
      return Array.from(document.querySelectorAll("body *")).some(
        (element) =>
          isVisible(element) &&
          normalized(element.textContent) === expectedText,
      );
    },
    { timeout: 30000 },
    text,
  );
  const clicked = await page.evaluate((expectedText) => {
    const normalized = (value) => String(value || "").trim();
    const actionableSelector = "button,[role='button'],a,[role='tab']";
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
    const matches = Array.from(document.querySelectorAll("body *")).filter(
      (candidate) =>
        isVisible(candidate) &&
        normalized(candidate.textContent) === expectedText,
    );
    const element =
      matches.find((candidate) => candidate.matches(actionableSelector)) ||
      matches
        .map((candidate) => candidate.closest(actionableSelector))
        .find((candidate) => candidate && isVisible(candidate));
    const clickable = element?.closest(actionableSelector) || element;
    if (!clickable) return false;
    clickable.scrollIntoView({ block: "center", inline: "center" });
    clickable.click();
    return true;
  }, text);
  assert(clicked, `Could not click visible text ${text}.`);
}

async function assertNoRawSecretVisible(page) {
  const visibleText = await page.evaluate(() => {
    const inputValues = Array.from(document.querySelectorAll("input, textarea"))
      .map((element) => element.value || "")
      .join("\n");
    return `${document.body.innerText || ""}\n${inputValues}`;
  });
  assertNoRawSecretString(visibleText, "visible page text");
}

function assertNoRawSecretString(value, label) {
  const rawSecretPatterns = [
    /sk-[A-Za-z0-9_-]{12,}/,
    /AIza[A-Za-z0-9_-]{20,}/,
    /AKIA[A-Z0-9]{16}/,
    /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
  ];
  for (const pattern of rawSecretPatterns) {
    assert(
      !pattern.test(value),
      `${label} contains a raw provider secret pattern.`,
    );
  }
}

function assertGetStartedChecks(checks) {
  for (const key of [
    "keys",
    "dataset",
    "evaluation",
    "experiment",
    "observe",
    "invite",
  ]) {
    assert(
      typeof checks?.[key] === "boolean",
      `first-checks omitted boolean ${key}.`,
    );
  }
}

function isGetStartedApiUrl(url) {
  return (
    url.includes("/accounts/first-checks/") ||
    url.includes("/model-hub/develops/provider-status/") ||
    url.includes("/model-hub/custom-models/")
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
