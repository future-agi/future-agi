/* eslint-disable no-console */
import { Buffer } from "node:buffer";
import { createRequire } from "node:module";
import process from "node:process";
import { assert } from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const API_BASE = process.env.API_BASE || "http://127.0.0.1:8005";
const SCREENSHOT_PATH =
  process.env.NOTIFICATION_SETTINGS_SCREENSHOT ||
  "/tmp/notification-settings-controlled-smoke.png";

async function main() {
  const auth = createStubbedAuthenticatedContext();
  await markLocalOnboardingComplete();
  const apiFailures = [];
  const notificationRequests = [];
  const pageErrors = [];
  const requestFailures = [];

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 1000 },
    args: ["--no-sandbox"],
  });

  const page = await browser.newPage();
  await page.setCacheEnabled(false);
  await page.setBypassServiceWorker(true);
  await installRuntimeConfig(page, auth, notificationRequests);
  await page.evaluateOnNewDocument(
    ({ tokens, organizationId, workspaceId, user }) => {
      localStorage.setItem("accessToken", tokens.access);
      localStorage.setItem("refreshToken", tokens.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.setItem("initial-render", "done");
      sessionStorage.setItem("organizationId", organizationId);
      sessionStorage.setItem("organizationName", "Onboarding smoke org");
      sessionStorage.setItem("organizationDisplayName", "Onboarding smoke org");
      sessionStorage.setItem("organizationRole", "Owner");
      sessionStorage.setItem("workspaceId", workspaceId);
      sessionStorage.setItem("workspaceName", "Onboarding smoke workspace");
      sessionStorage.setItem(
        "workspaceDisplayName",
        "Onboarding smoke workspace",
      );
      sessionStorage.setItem("workspaceRole", "Owner");
      sessionStorage.setItem("currentUserId", user.id);
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
    if (url.includes("/accounts/notification-") && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("requestfailed", (request) => {
    requestFailures.push(
      `${request.failure()?.errorText || "failed"} ${request.url()}`,
    );
  });

  try {
    const settingsResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/accounts/notification-preferences/") &&
        response.request().method() === "GET" &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(`${APP_BASE}/dashboard/settings/notifications`, {
      waitUntil: "domcontentloaded",
    });
    await settingsResponse;

    await waitForVisibleText(page, "Notifications", { exact: true });
    await waitForVisibleText(
      page,
      "Manage onboarding, quality, usage, and workspace alert routing.",
    );
    await waitForVisibleText(page, "Product onboarding");
    await waitForVisibleText(page, "Daily quality digest");
    await waitForVisibleText(page, "Usage and budget alerts");
    await waitForVisibleText(page, "Onboarding Slack");
    await waitForVisibleText(page, "Slack webhook configured");
    await waitForVisibleText(page, "Delivery log");
    await waitForVisibleText(page, "budget_threshold_80 - 80");
    await waitForVisibleText(page, "Suppressed");
    await waitForVisibleText(page, "Channel Disabled");

    assert(
      await checkboxChecked(page, "Usage and budget alerts Email"),
      "Usage-budget email should start enabled.",
    );
    const patchCountBeforeUsage = notificationPatchCount(notificationRequests);
    await clickCheckbox(page, "Usage and budget alerts Email");
    const usagePatch = await waitForRequest(notificationRequests, (request) => {
      const body = parseJson(request.postData);
      return (
        request.method === "PATCH" &&
        notificationPatchCount(notificationRequests) > patchCountBeforeUsage &&
        body?.preferences?.some(
          (preference) =>
            preference.scope === "workspace" &&
            preference.family === "usage_budget" &&
            preference.channel === "email" &&
            preference.enabled === false,
        )
      );
    });
    assert(
      usagePatch,
      "Usage-budget email switch did not send the expected workspace preference.",
    );

    await waitForCheckboxEnabled(page, "Product onboarding Slack");
    assert(
      !(await checkboxChecked(page, "Product onboarding Slack")),
      "Product onboarding Slack should start as opt-in.",
    );
    const patchCountBeforeSlack = notificationPatchCount(notificationRequests);
    await clickCheckbox(page, "Product onboarding Slack");
    const slackPatch = await waitForRequest(notificationRequests, (request) => {
      const body = parseJson(request.postData);
      return (
        request.method === "PATCH" &&
        notificationPatchCount(notificationRequests) > patchCountBeforeSlack &&
        body?.preferences?.some(
          (preference) =>
            preference.scope === "user_workspace" &&
            preference.family === "product_onboarding" &&
            preference.channel === "slack" &&
            preference.enabled === true,
        )
      );
    });
    assert(
      slackPatch,
      "Product onboarding Slack switch did not send the expected user-workspace preference.",
    );

    const channelTestCountBefore = notificationRequests.filter(
      (request) =>
        request.method === "POST" &&
        request.path ===
          "/accounts/notification-channels/stub-slack-channel/test/",
    ).length;
    await clickChannelTestButton(page);
    const channelTest = await waitForRequest(
      notificationRequests,
      (request) =>
        request.method === "POST" &&
        request.path ===
          "/accounts/notification-channels/stub-slack-channel/test/" &&
        notificationRequests.filter(
          (candidate) =>
            candidate.method === "POST" &&
            candidate.path ===
              "/accounts/notification-channels/stub-slack-channel/test/",
        ).length > channelTestCountBefore,
    );
    assert(channelTest, "Slack channel test request was not sent.");
    const settingsAfterTest = await readNotificationSettings();
    assert(
      settingsAfterTest.delivery_logs?.some(
        (log) =>
          log.notification_key === "notification_channel_test" &&
          log.status === "eligible",
      ),
      "Slack channel test was not recorded in the notification delivery log.",
    );

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      requestFailures.length === 0,
      `Request failures: ${requestFailures.join("; ")}`,
    );

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: API_BASE,
          screenshot: SCREENSHOT_PATH,
          requests: {
            notification_gets: notificationRequests.filter(
              (request) =>
                request.method === "GET" &&
                request.path === "/accounts/notification-preferences/",
            ).length,
            notification_patches: notificationRequests.filter(
              (request) =>
                request.method === "PATCH" &&
                request.path === "/accounts/notification-preferences/",
            ).length,
            channel_tests: notificationRequests.filter(
              (request) =>
                request.method === "POST" &&
                request.path ===
                  "/accounts/notification-channels/stub-slack-channel/test/",
            ).length,
          },
          channel_test_delivery_logged: true,
        },
        null,
        2,
      ),
    );
  } finally {
    await browser.close();
  }
}

async function installRuntimeConfig(page, auth, notificationRequests) {
  await page.setRequestInterception(true);
  page.on("request", async (request) => {
    const url = new URL(request.url());
    const path = slashPath(url.pathname);
    if (path === "/config.js/") {
      await request.respond({
        status: 200,
        contentType: "application/javascript",
        body: `window.__FUTURE_AGI_CONFIG__ = ${JSON.stringify({
          VITE_HOST_API: auth.apiBase,
          VITE_ASSETS_API: APP_BASE,
        })};`,
      });
      return;
    }

    if (path.startsWith("/accounts/notification-")) {
      notificationRequests.push({
        method: request.method(),
        path,
        postData: request.postData() || "",
      });
    }

    await request.continue();
  });
}

async function readNotificationSettings() {
  const response = await fetch(
    `${API_BASE}/accounts/notification-preferences/`,
    {
      headers: { "Content-Type": "application/json" },
    },
  );
  assert(
    response.ok,
    `Notification preferences read failed with HTTP ${response.status}.`,
  );
  const body = await response.json();
  return body.result || {};
}

async function markLocalOnboardingComplete() {
  const response = await fetch(`${API_BASE}/accounts/onboarding/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      goals: ["monitor_production_ai_app"],
      role: "Subject Matter Expert",
    }),
  });
  assert(
    response.ok,
    `Local onboarding completion failed with HTTP ${response.status}.`,
  );
}

function createStubbedAuthenticatedContext() {
  const userId = "00000000-0000-4000-8000-000000000111";
  const organizationId = "00000000-0000-4000-8000-000000000211";
  const workspaceId = "00000000-0000-4000-8000-000000000311";
  return {
    apiBase: API_BASE,
    organizationId,
    workspaceId,
    user: {
      id: userId,
      email: "notification-settings-smoke@example.com",
      name: "Notification Settings Smoke",
      organization_id: organizationId,
      organization_role: "Owner",
      org_level: 100,
      default_workspace_id: workspaceId,
      default_workspace_name: "Onboarding smoke workspace",
      default_workspace_display_name: "Onboarding smoke workspace",
      default_workspace_role: "Owner",
      ws_level: 100,
      remember_me: true,
      requires_org_setup: false,
    },
    tokens: {
      access: fakeJwt(userId),
      refresh: "",
    },
  };
}

function fakeJwt(userId) {
  const header = base64Url({ alg: "none", typ: "JWT" });
  const payload = base64Url({
    exp: Math.floor(Date.now() / 1000) + 3600,
    sub: userId,
    user_id: userId,
  });
  return `${header}.${payload}.signature`;
}

function base64Url(value) {
  return Buffer.from(JSON.stringify(value))
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function slashPath(path) {
  return path.endsWith("/") ? path : `${path}/`;
}

function parseJson(value) {
  if (!value) return {};
  try {
    return JSON.parse(value);
  } catch {
    return {};
  }
}

function notificationPatchCount(requests) {
  return requests.filter(
    (request) =>
      request.method === "PATCH" &&
      request.path === "/accounts/notification-preferences/",
  ).length;
}

async function waitForRequest(requests, predicate, timeout = 30000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeout) {
    const found = requests.find(predicate);
    if (found) return found;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return null;
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

async function waitForCheckboxEnabled(page, label, timeout = 30000) {
  await page.waitForFunction(
    (expectedLabel) => {
      const input = Array.from(
        document.querySelectorAll('input[type="checkbox"]'),
      ).find((element) => element.getAttribute("aria-label") === expectedLabel);
      return input && !input.disabled;
    },
    { timeout },
    label,
  );
}

async function checkboxChecked(page, label) {
  return page.evaluate((expectedLabel) => {
    const input = Array.from(
      document.querySelectorAll('input[type="checkbox"]'),
    ).find((element) => element.getAttribute("aria-label") === expectedLabel);
    return Boolean(input?.checked);
  }, label);
}

async function clickCheckbox(page, label) {
  await waitForCheckboxEnabled(page, label);
  await page.evaluate((expectedLabel) => {
    const input = Array.from(
      document.querySelectorAll('input[type="checkbox"]'),
    ).find((element) => element.getAttribute("aria-label") === expectedLabel);
    const labelElement = input?.closest("label");
    if (labelElement) {
      labelElement.click();
      return;
    }
    input?.click();
  }, label);
}

async function clickChannelTestButton(page) {
  const startedAt = Date.now();
  let result = { clicked: false, buttons: [] };
  while (Date.now() - startedAt < 30000) {
    result = await page.evaluate(() => {
      const buttons = Array.from(document.querySelectorAll("button")).map(
        (button) => ({
          ariaLabel: button.getAttribute("aria-label") || "",
          disabled: button.disabled,
          text: button.textContent || "",
          title: button.getAttribute("title") || "",
        }),
      );
      const button = Array.from(document.querySelectorAll("button")).find(
        (candidate) => {
          const label = candidate.getAttribute("aria-label") || "";
          const title = candidate.getAttribute("title") || "";
          return (
            !candidate.disabled &&
            (label === "Test Onboarding Slack" || title === "Test channel")
          );
        },
      );
      if (!button) return { clicked: false, buttons };
      button.click();
      return { clicked: true, buttons };
    });
    if (result.clicked) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }

  assert(
    result.clicked,
    `Could not find a usable channel test button. Buttons: ${JSON.stringify(
      result.buttons.slice(0, 40),
    )}`,
  );
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  }
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  if (process.platform === "darwin") {
    return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  }
  if (process.platform === "linux") return "/usr/bin/google-chrome";
  return undefined;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
