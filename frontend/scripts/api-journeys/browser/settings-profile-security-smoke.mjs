import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
  currentUserEmail,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/settings-profile-security-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const profile = await auth.client.get(
    apiPath("/accounts/get-user-profile-details/"),
  );
  const twoFactorStatus = await auth.client.get(apiPath("/accounts/2fa/status/"), {
    unwrap: false,
  });
  const email = currentUserEmail(auth.user);
  assert(profile?.email === email, "Profile details email did not match auth context.");

  const apiFailures = [];
  const pageErrors = [];
  const evidence = {
    email,
    name: profile.name,
    two_factor_enabled: twoFactorStatus.two_factor_enabled,
    totp_enabled: twoFactorStatus.methods?.totp?.enabled,
    passkey_enabled: twoFactorStatus.methods?.passkey?.enabled,
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
    if (
      (url.includes("/accounts/get-user-profile-details/") ||
        url.includes("/accounts/2fa/status/") ||
        url.includes("/accounts/passkeys/")) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const profileResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/accounts/get-user-profile-details/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    const twoFactorResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/accounts/2fa/status/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    const passkeysResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/accounts/passkeys/") &&
        response.status() < 400,
      { timeout: 60000 },
    );

    await page.goto(`${APP_BASE}/dashboard/settings/profile-settings`, {
      waitUntil: "domcontentloaded",
    });
    await profileResponse;
    await twoFactorResponse;
    await passkeysResponse.catch(() => null);

    await waitForVisibleText(page, "Profile Details", { exact: true });
    await waitForVisibleText(page, profile.name, { exact: true });
    await waitForVisibleText(page, profile.email, { exact: true });
    await waitForVisibleText(page, "Security", { exact: true });
    await waitForVisibleText(page, "Authenticator App", { exact: true });
    await waitForVisibleText(page, "Passkeys", { exact: true });
    await waitForVisibleText(page, "Reset Password", { exact: true });
    await clickVisibleIconByTitle(page, "Edit Name");
    await waitForVisibleText(page, "Update Name", { exact: true });
    await waitForVisibleText(page, "Full Name", { exact: true });
    await page.keyboard.press("Escape");

    await waitForNoVisibleText(page, "Invalid Date");
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

async function clickVisibleIconByTitle(page, title) {
  await page.waitForSelector(`[aria-label="${title}"]`, {
    visible: true,
    timeout: 30000,
  });
  await page.click(`[aria-label="${title}"]`);
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
