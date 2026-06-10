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
const UPDATE_SCREENSHOT_PATH =
  "/tmp/settings-profile-security-name-update-smoke.png";
const REVERT_SCREENSHOT_PATH =
  "/tmp/settings-profile-security-name-revert-smoke.png";
const ORG_SECURITY_SCREENSHOT_PATH =
  "/tmp/settings-org-security-policy-smoke.png";
const ERROR_SCREENSHOT_PATH = "/tmp/settings-profile-security-error-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const profile = await auth.client.get(
    apiPath("/accounts/get-user-profile-details/"),
  );
  const twoFactorStatus = await auth.client.get(
    apiPath("/accounts/2fa/status/"),
    {
      unwrap: false,
    },
  );
  const orgPolicy = await auth.client.get(
    apiPath("/accounts/organization/2fa-policy/"),
    {
      unwrap: false,
    },
  );
  const email = currentUserEmail(auth.user);
  assert(
    profile?.email === email,
    "Profile details email did not match auth context.",
  );
  const originalName = String(profile.name || "").trim();
  assert(originalName, "Profile details did not include a current name.");
  const shouldMutate = process.env.API_JOURNEY_MUTATIONS === "1";
  const updatedName = `${originalName} UI ${auth.runId.replace(/[^a-z0-9-]/gi, "").slice(0, 12)}`;
  let nameRestored = !shouldMutate;

  const apiFailures = [];
  const pageErrors = [];
  const orgPolicyMutations = [];
  const evidence = {
    email,
    original_name: originalName,
    profile_name_mutation_exercised: shouldMutate,
    two_factor_enabled: twoFactorStatus.two_factor_enabled,
    totp_enabled: twoFactorStatus.methods?.totp?.enabled,
    passkey_enabled: twoFactorStatus.methods?.passkey?.enabled,
    org_require_2fa: orgPolicy.require_2fa,
    org_2fa_grace_days: orgPolicy.require_2fa_grace_period_days,
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
      (url.includes("/accounts/get-user-profile-details/") ||
        url.includes("/accounts/2fa/status/") ||
        url.includes("/accounts/passkeys/") ||
        url.includes("/accounts/organization/2fa-policy/")) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("request", (request) => {
    const writeMethods = new Set(["POST", "PUT", "PATCH", "DELETE"]);
    if (
      request.url().includes("/accounts/organization/2fa-policy/") &&
      writeMethods.has(request.method())
    ) {
      orgPolicyMutations.push(`${request.method()} ${request.url()}`);
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

    if (shouldMutate) {
      await submitOpenNameDrawer(page, updatedName);
      nameRestored = false;
      await waitForVisibleText(page, updatedName, { exact: true });
      let apiProfile = await auth.client.get(
        apiPath("/accounts/get-user-profile-details/"),
      );
      assert(
        apiProfile?.name === updatedName,
        "Profile API did not reflect the browser-updated name.",
      );
      await page.screenshot({ path: UPDATE_SCREENSHOT_PATH, fullPage: true });

      await reloadProfilePage(page);
      await updateNameThroughBrowser(page, originalName);
      await waitForVisibleText(page, originalName, { exact: true });
      await waitForNoVisibleText(page, updatedName, { exact: true });
      apiProfile = await auth.client.get(
        apiPath("/accounts/get-user-profile-details/"),
      );
      assert(
        apiProfile?.name === originalName,
        "Profile API did not reflect the browser name revert.",
      );
      nameRestored = true;
      evidence.updated_name = updatedName;
      evidence.reverted_name = originalName;
      evidence.update_screenshot = UPDATE_SCREENSHOT_PATH;
      evidence.revert_screenshot = REVERT_SCREENSHOT_PATH;
      await page.screenshot({ path: REVERT_SCREENSHOT_PATH, fullPage: true });
    } else {
      await page.keyboard.press("Escape");
      await waitForNoVisibleText(page, "Update Name", { exact: true });
    }

    await waitForNoVisibleText(page, "Invalid Date");
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    await verifyOrganizationSecurityPolicy(page, orgPolicy, twoFactorStatus);
    evidence.org_policy_screenshot = ORG_SECURITY_SCREENSHOT_PATH;
    evidence.org_policy_mutation_count = orgPolicyMutations.length;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(
      orgPolicyMutations.length === 0,
      `Unexpected organization policy mutations: ${orgPolicyMutations.join("; ")}`,
    );
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
    await page.screenshot({ path: ERROR_SCREENSHOT_PATH, fullPage: true });
    console.error(
      JSON.stringify(
        {
          status: "failed",
          error: error.message,
          debug: await collectDebugState(page),
          error_screenshot: ERROR_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    if (shouldMutate && !nameRestored) {
      await auth.client.post(apiPath("/accounts/update-user-full-name/"), {
        name: originalName,
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

async function verifyOrganizationSecurityPolicy(
  page,
  orgPolicy,
  twoFactorStatus,
) {
  const orgPolicyResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/accounts/organization/2fa-policy/") &&
      response.request().method() === "GET" &&
      response.status() < 400,
    { timeout: 60000 },
  );
  const twoFactorResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/accounts/2fa/status/") &&
      response.request().method() === "GET" &&
      response.status() < 400,
    { timeout: 60000 },
  );

  await page.goto(`${APP_BASE}/dashboard/settings/org-settings`, {
    waitUntil: "domcontentloaded",
  });
  await orgPolicyResponse;
  await twoFactorResponse.catch(() => null);

  await waitForVisibleText(page, "Organization Settings", { exact: true });
  await waitForVisibleText(page, "Organization Name", { exact: true });
  await waitForVisibleText(page, "Organization Security", { exact: true });
  await waitForVisibleText(page, "Require 2FA for all members", {
    exact: true,
  });

  const require2faChecked = await visibleSwitchCheckedNearText(
    page,
    "Require 2FA for all members",
  );
  assert(
    require2faChecked === Boolean(orgPolicy.require_2fa),
    "Organization 2FA toggle did not reflect the API policy state.",
  );

  if (!twoFactorStatus.two_factor_enabled && !orgPolicy.require_2fa) {
    await waitForVisibleText(
      page,
      "You must enable two-factor authentication on your own account before requiring it for the organization.",
    );
    await waitForVisibleText(page, "Go to Profile Settings", { exact: true });
    const require2faDisabled = await visibleSwitchDisabledNearText(
      page,
      "Require 2FA for all members",
    );
    assert(
      require2faDisabled,
      "Organization 2FA toggle should be disabled until the actor has 2FA.",
    );
  }

  await waitForNoVisibleText(page, "Invalid Date");
  await page.screenshot({
    path: ORG_SECURITY_SCREENSHOT_PATH,
    fullPage: true,
  });
}

async function visibleSwitchCheckedNearText(page, text) {
  return page.evaluate((expectedText) => {
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
    const label = Array.from(document.querySelectorAll("body *")).find(
      (element) =>
        isVisible(element) &&
        String(element.textContent || "").trim() === expectedText,
    );
    let section = label;
    for (let index = 0; section && index < 8; index += 1) {
      if (section.querySelector('input[type="checkbox"]')) break;
      section = section.parentElement;
    }
    const input = section?.querySelector('input[type="checkbox"]');
    return Boolean(input?.checked);
  }, text);
}

async function visibleSwitchDisabledNearText(page, text) {
  return page.evaluate((expectedText) => {
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
    const label = Array.from(document.querySelectorAll("body *")).find(
      (element) =>
        isVisible(element) &&
        String(element.textContent || "").trim() === expectedText,
    );
    let section = label;
    for (let index = 0; section && index < 8; index += 1) {
      if (section.querySelector('input[type="checkbox"]')) break;
      section = section.parentElement;
    }
    const input = section?.querySelector('input[type="checkbox"]');
    return Boolean(input?.disabled);
  }, text);
}

async function clickVisibleIconByTitle(page, title) {
  const handle = await page.waitForFunction(
    (expectedTitle) =>
      Array.from(
        document.querySelectorAll(`[aria-label="${expectedTitle}"]`),
      ).find((element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      }) || null,
    { timeout: 30000 },
    title,
  );
  const element = handle.asElement();
  assert(element, `Could not resolve visible icon "${title}".`);
  await element.click();
}

async function updateNameThroughBrowser(page, name) {
  await clickVisibleIconByTitle(page, "Edit Name");
  await waitForVisibleText(page, "Update Name", { exact: true });
  await submitOpenNameDrawer(page, name);
}

async function submitOpenNameDrawer(page, name) {
  await fillInputByPlaceholder(page, "Enter your full name", name);
  let updateResponse = await waitForNameUpdateAfterAction(page, () =>
    clickVisibleButton(page, "Update Full Name"),
  );
  if (!updateResponse) {
    updateResponse = await waitForNameUpdateAfterAction(page, () =>
      page.keyboard.press("Enter"),
    );
  }
  if (!updateResponse) {
    updateResponse = await waitForNameUpdateAfterAction(page, () =>
      submitVisibleNameForm(page),
    );
  }
  assert(updateResponse, "Profile name form did not submit an update request.");
  assert(
    updateResponse.status() >= 200 && updateResponse.status() < 300,
    `Profile name update failed with HTTP ${updateResponse.status()}.`,
  );
  const submittedPayload = JSON.parse(
    updateResponse.request().postData() || "{}",
  );
  assert(
    submittedPayload.name === name,
    "Profile name update submitted an unexpected payload.",
  );
  await waitForNoVisibleText(page, "Update Name", { exact: true });
}

async function submitVisibleNameForm(page) {
  await page.evaluate(() => {
    const visible = (element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    };
    const button = Array.from(document.querySelectorAll("button")).find(
      (candidate) =>
        visible(candidate) &&
        String(candidate.textContent || "").trim() === "Update Full Name",
    );
    if (!button) throw new Error("Visible Update Full Name button not found.");
    const form = button.closest("form");
    if (!form) throw new Error("Visible Update Full Name form not found.");
    form.requestSubmit(button);
  });
}

async function waitForNameUpdateAfterAction(page, action) {
  const responsePromise = page
    .waitForResponse(
      (response) =>
        response.url().includes("/accounts/update-user-full-name/") &&
        response.request().method() === "POST",
      { timeout: 15000 },
    )
    .catch(() => null);
  await action();
  return responsePromise;
}

async function collectDebugState(page) {
  return page.evaluate(() => {
    const visible = (element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    };
    return {
      path: window.location.pathname,
      visibleText: String(document.body?.innerText || "").slice(0, 3000),
      inputs: Array.from(document.querySelectorAll("input"))
        .filter(visible)
        .map((input) => ({
          placeholder: input.getAttribute("placeholder") || "",
          value: input.value,
          disabled: input.disabled,
        })),
      buttons: Array.from(document.querySelectorAll("button"))
        .filter(visible)
        .map((button) => ({
          text: String(button.textContent || "").trim(),
          disabled: button.disabled,
        })),
      editIconCount: Array.from(
        document.querySelectorAll('[aria-label="Edit Name"]'),
      ).filter(visible).length,
    };
  });
}

async function reloadProfilePage(page) {
  const profileResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/accounts/get-user-profile-details/") &&
      response.status() < 400,
    { timeout: 60000 },
  );
  await page.goto(`${APP_BASE}/dashboard/settings/profile-settings`, {
    waitUntil: "domcontentloaded",
  });
  await profileResponse;
  await waitForVisibleText(page, "Profile Details", { exact: true });
}

async function clickVisibleButton(page, text) {
  const handle = await page.waitForFunction(
    (expectedText) =>
      Array.from(document.querySelectorAll("button")).find((button) => {
        const style = window.getComputedStyle(button);
        const rect = button.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0 &&
          !button.disabled &&
          String(button.textContent || "").trim() === expectedText
        );
      }) || null,
    { timeout: 30000 },
    text,
  );
  const element = handle.asElement();
  assert(element, `Could not resolve visible button "${text}".`);
  const box = await element.boundingBox();
  assert(box, `Could not resolve visible button box "${text}".`);
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

async function fillInputByPlaceholder(page, placeholder, value) {
  const selector = `input[placeholder="${cssString(placeholder)}"]`;
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector, { clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.type(selector, value);
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

function cssString(value) {
  return String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
