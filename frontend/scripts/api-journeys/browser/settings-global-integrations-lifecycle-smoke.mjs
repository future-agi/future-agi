import { execFile } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
  currentUserId,
  isUuid,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const ROUTE_MODE =
  process.env.INTEGRATIONS_LIFECYCLE_ROUTE === "workspace"
    ? "workspace"
    : "global";
const IS_WORKSPACE_ROUTE = ROUTE_MODE === "workspace";
const DISPLAY_NAME_PREFIX = `UI Journey ${
  IS_WORKSPACE_ROUTE ? "Workspace" : "Global"
} Integration`;
const SCREENSHOT_PREFIX = `/tmp/settings-${ROUTE_MODE}-integrations-lifecycle`;
const EDIT_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-edit-smoke.png`;
const SYNC_GUARD_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-sync-guard-smoke.png`;
const PAUSE_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-pause-smoke.png`;
const RESUME_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-resume-smoke.png`;
const DELETE_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-delete-smoke.png`;
const ERROR_SCREENSHOT_PATH = `${SCREENSHOT_PREFIX}-error-smoke.png`;

async function main() {
  requireMutations();

  const auth = await createAuthenticatedContext();
  const userInfo = await auth.client.get(apiPath("/accounts/user-info/"));
  const userId = currentUserId(userInfo) || currentUserId(auth.user);
  assert(
    isUuid(userId),
    "Authenticated user-info did not include a valid user id.",
  );

  const marker = auth.runId.replace(/[^a-z0-9-]/gi, "").slice(0, 20);
  await deleteUiJourneyIntegrationData({
    displayNamePrefix: DISPLAY_NAME_PREFIX,
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
  });
  const seeded = await seedIntegrationConnectionData({
    displayNamePrefix: DISPLAY_NAME_PREFIX,
    marker,
    organizationId: auth.organizationId,
    workspaceId: auth.workspaceId,
    userId,
  });
  const seededDetail = await auth.client.get(
    apiPath("/integrations/connections/{id}/", {
      id: seeded.connection_id,
    }),
  );
  assertPayloadDoesNotContain(seededDetail, [
    seeded.plain_public_key,
    seeded.plain_secret_key,
  ]);
  const expectedPublicKeyDisplay = seededDetail.public_key_display || "****";
  const expectedSecretKeyDisplay = seededDetail.secret_key_display || "****";
  const updatedDisplayName = `${seeded.display_name} edited`;
  let hardCleaned = false;
  const observedRequests = [];
  const observedMutations = [];
  const unexpectedApiFailures = [];
  const pageErrors = [];

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
    if (isIntegrationMutationResponse(response)) {
      observedMutations.push({
        method: response.request().method(),
        url,
        status: response.status(),
      });
    }
    if (isIntegrationSettingsUrl(url) && response.status() >= 400) {
      const expectedDeletedDetail404 =
        url.includes(`/integrations/connections/${seeded.connection_id}/`) &&
        response.request().method() === "GET" &&
        response.status() === 404;
      const expectedSyncGuard =
        url.includes(
          `/integrations/connections/${seeded.connection_id}/sync_now/`,
        ) &&
        response.request().method() === "POST" &&
        response.status() === 400;
      if (!expectedDeletedDetail404 && !expectedSyncGuard) {
        unexpectedApiFailures.push(`${response.status()} ${url}`);
      }
    }
  });
  page.on("request", (request) => {
    const url = request.url();
    if (
      isIntegrationSettingsUrl(url) &&
      ["POST", "PATCH", "PUT", "DELETE"].includes(request.method())
    ) {
      observedRequests.push({
        method: request.method(),
        url,
        postData: request.postData() || "",
      });
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const expectedListPath = IS_WORKSPACE_ROUTE
      ? `/dashboard/settings/workspace/${auth.workspaceId}/integrations`
      : "/dashboard/settings/integrations";
    const expectedDetailPath = IS_WORKSPACE_ROUTE
      ? `/dashboard/settings/workspace/${auth.workspaceId}/integrations/${seeded.connection_id}`
      : `/dashboard/settings/integrations/${seeded.connection_id}`;
    const listResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/integrations/connections/") &&
        response.request().method() === "GET" &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(`${APP_BASE}${expectedListPath}`, {
      waitUntil: "domcontentloaded",
    });
    await listResponse;

    await waitForVisibleText(page, "Integrations", { exact: true });
    if (IS_WORKSPACE_ROUTE) {
      await waitForVisibleText(page, "Manage workspace integrations", {
        exact: true,
      });
    }
    await assertCurrentPath(page, expectedListPath);
    await waitForVisibleText(page, seeded.display_name, { exact: true });
    await waitForVisibleText(page, seeded.host_url, { exact: true });
    await assertPageDoesNotContain(page, [
      seeded.plain_public_key,
      seeded.plain_secret_key,
    ]);

    const detailResponse = page.waitForResponse(
      (response) =>
        response
          .url()
          .includes(`/integrations/connections/${seeded.connection_id}/`) &&
        response.request().method() === "GET" &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await clickVisibleText(page, seeded.display_name);
    await detailResponse;
    await assertCurrentPath(page, expectedDetailPath);

    await waitForVisibleText(page, "Back to Integrations", { exact: true });
    await waitForVisibleText(page, seeded.display_name, { exact: true });
    await waitForVisibleText(page, expectedPublicKeyDisplay, {
      exact: true,
    });
    await waitForVisibleText(page, expectedSecretKeyDisplay, {
      exact: true,
    });
    await assertPageDoesNotContain(page, [
      seeded.plain_public_key,
      seeded.plain_secret_key,
    ]);

    const syncGuardResponsePromise = page.waitForResponse(
      (response) =>
        response
          .url()
          .includes(
            `/integrations/connections/${seeded.connection_id}/sync_now/`,
          ) && response.request().method() === "POST",
      { timeout: 60000 },
    );
    await clickVisibleButton(page, "Sync Now");
    const syncGuardResponse = await syncGuardResponsePromise;
    await assertHttpResponseStatus(
      syncGuardResponse,
      400,
      "integration sync-now cooldown guard",
    );
    const syncGuardBody = await safeJson(syncGuardResponse);
    assert(
      JSON.stringify(syncGuardBody).toLowerCase().includes("wait"),
      "Sync Now guard response did not mention cooldown waiting.",
    );
    await page.screenshot({
      path: SYNC_GUARD_SCREENSHOT_PATH,
      fullPage: true,
    });

    const pauseResponsePromise = page.waitForResponse(
      (response) =>
        response
          .url()
          .includes(
            `/integrations/connections/${seeded.connection_id}/pause/`,
          ) && response.request().method() === "POST",
      { timeout: 60000 },
    );
    await clickVisibleButton(page, "Pause");
    const pauseResponse = await pauseResponsePromise;
    await assertHttpResponseOk(pauseResponse, "integration pause");
    const pauseBody = await safeJson(pauseResponse);
    assert(
      responseResult(pauseBody)?.status === "paused",
      "Integration pause response did not return paused status.",
    );
    await waitForVisibleText(page, "Paused", { exact: true });
    await waitForVisibleText(page, "Resume", { exact: true });
    await page.screenshot({ path: PAUSE_SCREENSHOT_PATH, fullPage: true });

    const pausedSyncResponsePromise = page.waitForResponse(
      (response) =>
        response
          .url()
          .includes(
            `/integrations/connections/${seeded.connection_id}/sync_now/`,
          ) && response.request().method() === "POST",
      { timeout: 60000 },
    );
    await clickVisibleButton(page, "Sync Now");
    const pausedSyncResponse = await pausedSyncResponsePromise;
    await assertHttpResponseStatus(
      pausedSyncResponse,
      400,
      "paused integration sync-now guard",
    );
    const pausedSyncBody = await safeJson(pausedSyncResponse);
    assert(
      JSON.stringify(pausedSyncBody).toLowerCase().includes("paused"),
      "Paused Sync Now guard response did not mention paused state.",
    );

    const resumeResponsePromise = page.waitForResponse(
      (response) =>
        response
          .url()
          .includes(
            `/integrations/connections/${seeded.connection_id}/resume/`,
          ) && response.request().method() === "POST",
      { timeout: 60000 },
    );
    await clickVisibleButton(page, "Resume");
    const resumeResponse = await resumeResponsePromise;
    await assertHttpResponseOk(resumeResponse, "integration resume");
    const resumeBody = await safeJson(resumeResponse);
    assert(
      responseResult(resumeBody)?.status === "active",
      "Integration resume response did not return active status.",
    );
    await waitForVisibleText(page, "Active", { exact: true });
    await waitForVisibleText(page, "Pause", { exact: true });
    await page.screenshot({ path: RESUME_SCREENSHOT_PATH, fullPage: true });

    await clickButtonByAriaLabel(page, "Integration options menu");
    await clickVisibleMenuItem(page, "Edit");
    await waitForVisibleText(page, "Edit Integration", { exact: true });
    await fillInputByLabel(page, "Display Name", updatedDisplayName);
    await selectByLabel(page, "Sync Interval", "600");

    const updateResponsePromise = page.waitForResponse(
      (response) =>
        response
          .url()
          .includes(`/integrations/connections/${seeded.connection_id}/`) &&
        response.request().method() === "PATCH",
      { timeout: 60000 },
    );
    await clickVisibleButton(page, "Save Changes");
    const updateResponse = await updateResponsePromise;
    await assertHttpResponseOk(updateResponse, "integration update");
    const updatePayload = JSON.parse(
      updateResponse.request().postData() || "{}",
    );
    assert(
      updatePayload.display_name === updatedDisplayName,
      "Integration edit did not submit the updated display_name.",
    );
    assert(
      updatePayload.sync_interval_seconds === 600,
      "Integration edit did not submit the updated sync interval.",
    );
    assert(
      !Object.prototype.hasOwnProperty.call(updatePayload, "public_key") &&
        !Object.prototype.hasOwnProperty.call(updatePayload, "secret_key"),
      "Integration edit submitted credential fields even though keys were unchanged.",
    );

    await waitForVisibleText(page, updatedDisplayName, { exact: true });
    await waitForVisibleText(page, "10 min", { exact: true });
    await assertPageDoesNotContain(page, [
      seeded.plain_public_key,
      seeded.plain_secret_key,
    ]);
    await page.screenshot({ path: EDIT_SCREENSHOT_PATH, fullPage: true });
    await waitForNoVisibleText(page, "Integration updated successfully", {
      timeout: 15000,
    });
    await waitForNoVisibleSelector(page, '[role="dialog"], .MuiBackdrop-root');

    await clickButtonByAriaLabel(page, "Integration options menu");
    await clickVisibleMenuItem(page, "Delete");
    await waitForVisibleText(page, "Delete Integration", { exact: true });
    await fillInputByPlaceholder(page, 'Type "DELETE" to confirm', "DELETE");
    const deleteResponsePromise = page.waitForResponse(
      (response) =>
        response
          .url()
          .includes(`/integrations/connections/${seeded.connection_id}/`) &&
        response.request().method() === "DELETE",
      { timeout: 60000 },
    );
    await clickDialogActionButton(page, "Delete");
    await assertHttpResponseOk(
      await deleteResponsePromise,
      "integration delete",
    );

    await assertCurrentPath(page, expectedListPath);
    await waitForVisibleText(page, "Integrations", { exact: true });
    if (IS_WORKSPACE_ROUTE) {
      await waitForVisibleText(page, "Manage workspace integrations", {
        exact: true,
      });
    }
    await waitForNoVisibleText(page, updatedDisplayName, { exact: true });
    await page.screenshot({ path: DELETE_SCREENSHOT_PATH, fullPage: true });

    const deletedDetail = await expectApiStatus(
      () =>
        auth.client.get(
          apiPath("/integrations/connections/{id}/", {
            id: seeded.connection_id,
          }),
        ),
      404,
      "deleted integration detail",
    );
    const deletedAudit = await loadIntegrationConnectionDbAudit({
      connectionId: seeded.connection_id,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assert(
      deletedAudit.deleted === true && deletedAudit.deleted_at_set === true,
      "Integration delete did not soft-delete the disposable row.",
    );

    await deleteIntegrationConnectionData({
      connectionId: seeded.connection_id,
    });
    hardCleaned = true;
    const cleanupAudit = await loadIntegrationConnectionDbAudit({
      connectionId: seeded.connection_id,
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
    });
    assert(
      cleanupAudit.connection_count === 0 && cleanupAudit.sync_log_count === 0,
      "Disposable integration data remained after cleanup.",
    );

    assert(
      unexpectedApiFailures.length === 0,
      `Unexpected API failures: ${unexpectedApiFailures.join("; ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      observedMutations.length === 6,
      `Expected 6 integration mutations, saw ${observedMutations.length}.`,
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
            connection_id: seeded.connection_id,
            entry_path: expectedListPath,
            detail_path: expectedDetailPath,
            display_name: updatedDisplayName,
            sync_guard_status: syncGuardResponse.status(),
            pause_status: responseResult(pauseBody)?.status,
            paused_sync_guard_status: pausedSyncResponse.status(),
            resume_status: responseResult(resumeBody)?.status,
            update_payload_fields: Object.keys(updatePayload).sort(),
            deleted_detail_status: deletedDetail.status,
            db_deleted_at_set: deletedAudit.deleted_at_set,
            cleanup_connection_count: cleanupAudit.connection_count,
            cleanup_sync_log_count: cleanupAudit.sync_log_count,
            expected_mutation_count: observedMutations.length,
            sync_guard_screenshot: SYNC_GUARD_SCREENSHOT_PATH,
            pause_screenshot: PAUSE_SCREENSHOT_PATH,
            resume_screenshot: RESUME_SCREENSHOT_PATH,
            edit_screenshot: EDIT_SCREENSHOT_PATH,
            delete_screenshot: DELETE_SCREENSHOT_PATH,
          },
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
          observed_requests: observedRequests.map((request) => ({
            method: request.method,
            url: request.url,
            postDataLength: request.postData.length,
          })),
          observed_mutations: observedMutations,
          error_screenshot: ERROR_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    await browser.close();
    if (!hardCleaned) {
      await deleteIntegrationConnectionData({
        connectionId: seeded.connection_id,
      });
    }
  }
}

async function installDomHelpers(page) {
  await page.evaluateOnNewDocument(() => {
    window.isElementVisible = (element) => {
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

async function clickVisibleText(page, text) {
  await page.waitForFunction(
    (expectedText) => {
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
        (candidate) =>
          isVisible(candidate) &&
          String(candidate.textContent || "").trim() === expectedText &&
          Boolean(
            candidate.closest(
              ".MuiCardActionArea-root,button,a,[role='button']",
            ),
          ),
      );
    },
    { timeout: 30000 },
    text,
  );
  await page.evaluate((expectedText) => {
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
    const element = Array.from(document.querySelectorAll("body *")).find(
      (candidate) =>
        isVisible(candidate) &&
        String(candidate.textContent || "").trim() === expectedText &&
        Boolean(
          candidate.closest(".MuiCardActionArea-root,button,a,[role='button']"),
        ),
    );
    element.closest(".MuiCardActionArea-root,button,a,[role='button']").click();
  }, text);
}

async function clickVisibleButton(page, text) {
  await clickElementByText(page, text, "button");
}

async function clickDialogActionButton(page, text) {
  const handle = await page.waitForFunction(
    (expectedText) => {
      const dialog = document.querySelector('[role="dialog"]');
      if (!dialog) return null;
      return (
        Array.from(dialog.querySelectorAll("button")).find(
          (button) =>
            window.isElementVisible(button) &&
            !button.disabled &&
            String(button.textContent || "").trim() === expectedText,
        ) || null
      );
    },
    { timeout: 30000 },
    text,
  );
  const element = handle.asElement();
  assert(element, `Could not resolve dialog button "${text}".`);
  await element.click();
}

async function clickVisibleMenuItem(page, text) {
  await clickElementByText(page, text, '[role="menuitem"]');
}

async function clickElementByText(page, text, selector) {
  const handle = await page.waitForFunction(
    ({ expectedText, selector }) =>
      Array.from(document.querySelectorAll(selector)).find(
        (element) =>
          window.isElementVisible(element) &&
          !element.disabled &&
          String(element.textContent || "").trim() === expectedText,
      ) || null,
    { timeout: 30000 },
    { expectedText: text, selector },
  );
  const element = handle.asElement();
  assert(element, `Could not resolve visible ${selector} text "${text}".`);
  await element.click();
}

async function clickButtonByAriaLabel(page, label) {
  await page.waitForSelector(`button[aria-label="${cssString(label)}"]`, {
    visible: true,
    timeout: 30000,
  });
  await page.click(`button[aria-label="${cssString(label)}"]`);
}

async function fillInputByLabel(page, label, value) {
  const selector = await inputSelectorForLabel(page, label);
  await page.click(selector, { clickCount: 3 });
  await page.type(selector, value);
}

async function fillInputByPlaceholder(page, placeholder, value) {
  const selector = `input[placeholder="${cssString(placeholder)}"]`;
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector, { clickCount: 3 });
  await page.type(selector, value);
}

async function selectByLabel(page, label, value) {
  const selector = await inputSelectorForLabel(page, label, "select");
  await page.select(selector, value);
}

async function inputSelectorForLabel(page, label, tagName = "input") {
  const id = await page.evaluate(
    ({ label, tagName }) => {
      const labels = Array.from(document.querySelectorAll("label"));
      const labelElement = labels.find(
        (candidate) => String(candidate.textContent || "").trim() === label,
      );
      if (!labelElement?.id) return null;
      const id = labelElement.id.replace(/-label$/, "");
      const target = document.getElementById(id);
      if (!target || target.tagName.toLowerCase() !== tagName) return null;
      return id;
    },
    { label, tagName },
  );
  assert(id, `Could not resolve ${tagName} for label "${label}".`);
  const selector = `${tagName}#${cssIdentifier(id)}`;
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  return selector;
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) =>
      Array.from(document.querySelectorAll("body *")).some((element) => {
        if (!window.isElementVisible(element)) return false;
        const textContent = String(element.textContent || "").trim();
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
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
      !Array.from(document.querySelectorAll("body *")).some((element) => {
        if (!window.isElementVisible(element)) return false;
        const textContent = String(element.textContent || "").trim();
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      }),
    { timeout },
    { text, exact },
  );
}

async function waitForNoVisibleSelector(
  page,
  selector,
  { timeout = 30000 } = {},
) {
  await page.waitForFunction(
    (selector) =>
      !Array.from(document.querySelectorAll(selector)).some((element) =>
        window.isElementVisible(element),
      ),
    { timeout },
    selector,
  );
}

async function assertCurrentPath(page, expectedPath, { timeout = 30000 } = {}) {
  await page.waitForFunction(
    (path) => window.location.pathname === path,
    { timeout },
    expectedPath,
  );
}

async function assertPageDoesNotContain(page, values) {
  const content = await page.content();
  for (const value of values) {
    assert(
      !content.includes(value),
      "Integration page leaked raw credential text.",
    );
  }
}

function assertPayloadDoesNotContain(payload, values) {
  const serialized = JSON.stringify(payload);
  for (const value of values) {
    assert(
      !serialized.includes(value),
      "Integration API payload leaked raw credential text.",
    );
  }
}

async function assertHttpResponseOk(response, label) {
  if (response.status() >= 200 && response.status() < 300) return;
  throw new Error(
    `${label} failed with HTTP ${response.status()}: ${JSON.stringify(
      await safeJson(response),
    ).slice(0, 1000)}`,
  );
}

async function assertHttpResponseStatus(response, expectedStatus, label) {
  if (response.status() === expectedStatus) return;
  throw new Error(
    `${label} expected HTTP ${expectedStatus}, got ${response.status()}: ${JSON.stringify(
      await safeJson(response),
    ).slice(0, 1000)}`,
  );
}

function responseResult(body) {
  return body?.result || body;
}

async function safeJson(response) {
  try {
    return await response.json();
  } catch {
    try {
      return await response.text();
    } catch {
      return null;
    }
  }
}

async function collectDebugState(page) {
  return page.evaluate(() => {
    const inspectButtonReactProps = (text) => {
      const button = Array.from(document.querySelectorAll("button")).find(
        (candidate) =>
          window.isElementVisible(candidate) &&
          String(candidate.textContent || "").trim() === text,
      );
      if (!button) return null;
      const propKey = Object.keys(button).find((key) =>
        key.startsWith("__reactProps$"),
      );
      const fiberKey = Object.keys(button).find((key) =>
        key.startsWith("__reactFiber$"),
      );
      const props = propKey ? button[propKey] : null;
      return {
        hasReactProps: Boolean(propKey),
        hasReactFiber: Boolean(fiberKey),
        propKeys: props ? Object.keys(props).sort() : [],
        hasOnClick: typeof props?.onClick === "function",
        disabled: button.disabled,
        className: button.className,
      };
    };
    return {
      path: window.location.pathname,
      visibleText: String(document.body?.innerText || "").slice(0, 4000),
      pauseButtonReactProps: inspectButtonReactProps("Pause"),
      buttons: Array.from(document.querySelectorAll("button"))
        .filter((button) => window.isElementVisible(button))
        .map((button) => ({
          text: String(button.textContent || "").trim(),
          disabled: button.disabled,
          ariaLabel: button.getAttribute("aria-label") || "",
        }))
        .slice(0, 100),
    };
  });
}

function isIntegrationSettingsUrl(url) {
  return (
    url.includes("/integrations/connections/") ||
    url.includes("/integrations/sync-logs/")
  );
}

function isIntegrationMutationResponse(response) {
  const url = response.url();
  return (
    url.includes("/integrations/connections/") &&
    ["POST", "PATCH", "PUT", "DELETE"].includes(response.request().method())
  );
}

async function seedIntegrationConnectionData({
  displayNamePrefix,
  marker,
  organizationId,
  workspaceId,
  userId,
}) {
  assert(
    isUuid(organizationId),
    "Integration seed organization id must be a UUID.",
  );
  assert(isUuid(workspaceId), "Integration seed workspace id must be a UUID.");
  assert(isUuid(userId), "Integration seed user id must be a UUID.");

  const displayName = `${displayNamePrefix} ${marker}`;
  const externalProjectName = `ui-journey-${marker}`;
  const publicKey = `pk-lf-${marker.slice(0, 12)}pub1234`;
  const secretKey = `sk-lf-${marker.slice(0, 12)}sec5678`;
  const script = `
import json
from datetime import datetime, timedelta, timezone
from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from integrations.models import ConnectionStatus, IntegrationConnection, IntegrationPlatform, SyncLog, SyncStatus
from integrations.services.credentials import CredentialManager
from tracer.models.project import Project

organization = Organization.objects.get(id=${JSON.stringify(organizationId)})
workspace = Workspace.objects.get(id=${JSON.stringify(workspaceId)}, organization=organization)
user = User.objects.get(id=${JSON.stringify(userId)})
project = Project.objects.filter(
    organization=organization,
    workspace=workspace,
    deleted=False,
).first()
if project is None:
    raise RuntimeError("No active project exists in the current workspace for integration seeding")
credentials = {
    "public_key": ${JSON.stringify(publicKey)},
    "secret_key": ${JSON.stringify(secretKey)},
}
now = datetime.now(timezone.utc)
connection = IntegrationConnection.no_workspace_objects.create(
    organization=organization,
    workspace=workspace,
    created_by=user,
    platform=IntegrationPlatform.LANGFUSE,
    display_name=${JSON.stringify(displayName)},
    host_url="https://langfuse.example.com",
    encrypted_credentials=CredentialManager.encrypt(credentials),
    project=project,
    external_project_name=${JSON.stringify(externalProjectName)},
    status=ConnectionStatus.ACTIVE,
    status_message="",
    last_synced_at=now - timedelta(seconds=20),
    sync_interval_seconds=300,
    backfill_completed=True,
    total_traces_synced=7,
    total_spans_synced=11,
    total_scores_synced=3,
)
sync_log = SyncLog.objects.create(
    connection=connection,
    status=SyncStatus.FAILED,
    started_at=now - timedelta(minutes=8),
    completed_at=now - timedelta(minutes=7),
    traces_fetched=7,
    traces_created=5,
    traces_updated=2,
    spans_synced=11,
    scores_synced=3,
    error_message="UI journey seeded sync failure without secret values",
    error_details={"type": "UiJourneySeed"},
    sync_from=now - timedelta(hours=1),
    sync_to=now,
)
print(json.dumps({
    "connection_id": str(connection.id),
    "sync_log_id": str(sync_log.id),
    "project_id": str(project.id),
    "display_name": connection.display_name,
    "host_url": connection.host_url,
    "external_project_name": connection.external_project_name,
    "plain_public_key": ${JSON.stringify(publicKey)},
    "plain_secret_key": ${JSON.stringify(secretKey)},
    "expected_public_key_display": "pk-lf-****1234",
    "expected_secret_key_display": "sk-lf-****5678",
}))
`;
  return runBackendShellJson(script);
}

async function loadIntegrationConnectionDbAudit({
  connectionId,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(connectionId)} AS connection_id,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id
),
connection_rows AS (
  SELECT
    connection.id,
    connection.organization_id,
    connection.workspace_id,
    connection.display_name,
    connection.status,
    connection.deleted,
    connection.deleted_at IS NOT NULL AS deleted_at_set
  FROM integrations_connection connection
  JOIN requested r ON connection.id = r.connection_id
),
sync_log_rows AS (
  SELECT log.id, log.connection_id
  FROM integrations_sync_log log
  JOIN requested r ON log.connection_id = r.connection_id
)
SELECT json_build_object(
  'connection_count', (SELECT count(*) FROM connection_rows),
  'sync_log_count', (SELECT count(*) FROM sync_log_rows),
  'organization_id', (SELECT organization_id::text FROM connection_rows LIMIT 1),
  'workspace_id', (SELECT workspace_id::text FROM connection_rows LIMIT 1),
  'display_name', (SELECT display_name FROM connection_rows LIMIT 1),
  'status', (SELECT status FROM connection_rows LIMIT 1),
  'deleted', (SELECT deleted FROM connection_rows LIMIT 1),
  'deleted_at_set', (SELECT deleted_at_set FROM connection_rows LIMIT 1)
);
`;
  return runPostgresJson(sql);
}

async function deleteIntegrationConnectionData({ connectionId }) {
  const sql = `
WITH requested AS (
  SELECT ${sqlUuid(connectionId)} AS connection_id
),
deleted_logs AS (
  DELETE FROM integrations_sync_log log
  USING requested r
  WHERE log.connection_id = r.connection_id
  RETURNING log.id
),
deleted_connections AS (
  DELETE FROM integrations_connection connection
  USING requested r
  WHERE connection.id = r.connection_id
  RETURNING connection.id
)
SELECT json_build_object(
  'deleted_logs', (SELECT count(*) FROM deleted_logs),
  'deleted_connections', (SELECT count(*) FROM deleted_connections)
);
`;
  return runPostgresJson(sql);
}

async function deleteUiJourneyIntegrationData({
  displayNamePrefix,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id
),
target_connections AS (
  SELECT connection.id
  FROM integrations_connection connection
  JOIN requested r
    ON connection.organization_id = r.organization_id
   AND connection.workspace_id = r.workspace_id
  WHERE connection.display_name LIKE ${sqlString(`${displayNamePrefix} %`)}
),
deleted_logs AS (
  DELETE FROM integrations_sync_log log
  USING target_connections target
  WHERE log.connection_id = target.id
  RETURNING log.id
),
deleted_connections AS (
  DELETE FROM integrations_connection connection
  USING target_connections target
  WHERE connection.id = target.id
  RETURNING connection.id
)
SELECT json_build_object(
  'deleted_logs', (SELECT count(*) FROM deleted_logs),
  'deleted_connections', (SELECT count(*) FROM deleted_connections)
);
`;
  return runPostgresJson(sql);
}

async function runBackendShellJson(script) {
  let stdout;
  const container = process.env.API_JOURNEY_BACKEND_CONTAINER;
  if (container) {
    const command = [
      "cd /app/backend",
      `python manage.py shell -c ${shellQuote(script)}`,
    ].join(" && ");
    ({ stdout } = await execFileAsync(
      "docker",
      ["exec", container, "sh", "-lc", command],
      { env: childProcessEnv(), maxBuffer: 20 * 1024 * 1024 },
    ));
  } else {
    const backendDir = process.env.API_JOURNEY_BACKEND_DIR || "futureagi";
    ({ stdout } = await execFileAsync(
      "uv",
      ["run", "python", "manage.py", "shell", "-c", script],
      {
        cwd: backendDir,
        env: {
          ...childProcessEnv(),
          EE_LICENSE_KEY: process.env.EE_LICENSE_KEY || "test-license-key",
          PGBOUNCER_HOST: process.env.PGBOUNCER_HOST || "127.0.0.1",
          PGBOUNCER_PORT: process.env.PGBOUNCER_PORT || "5436",
          REDIS_URL: process.env.REDIS_URL || "redis://127.0.0.1:6382/0",
          REDIS_CACHE_URL:
            process.env.REDIS_CACHE_URL || "redis://127.0.0.1:6382/0",
          UV_PROJECT_ENVIRONMENT:
            process.env.UV_PROJECT_ENVIRONMENT || ".venv-th5064-py311",
        },
        maxBuffer: 20 * 1024 * 1024,
      },
    ));
  }
  const jsonLine = stdout
    .trim()
    .split(/\r?\n/)
    .reverse()
    .find((line) => line.trim().startsWith("{"));
  assert(jsonLine, "Backend shell command did not emit a JSON object.");
  return JSON.parse(jsonLine);
}

async function runPostgresJson(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
  const user = process.env.API_JOURNEY_DB_USER || "user";
  const database = process.env.API_JOURNEY_DB_NAME || "tfc";
  const { stdout } = await execFileAsync(
    "docker",
    ["exec", container, "psql", "-U", user, "-d", database, "-At", "-c", sql],
    { env: childProcessEnv(), maxBuffer: 10 * 1024 * 1024 },
  );
  const text = stdout.trim();
  assert(text, "Postgres DB audit returned no JSON output.");
  return JSON.parse(text);
}

async function expectApiStatus(fn, expectedStatus, label) {
  try {
    await fn();
  } catch (error) {
    if (error?.status === expectedStatus) return error;
    throw error;
  }
  throw new Error(`${label} unexpectedly succeeded.`);
}

function childProcessEnv() {
  if (process.env.DOCKER_HOST || !process.env.HOME) return process.env;
  return {
    ...process.env,
    DOCKER_HOST: `unix://${process.env.HOME}/.colima/default/docker.sock`,
  };
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlString(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function shellQuote(value) {
  return `'${String(value).replaceAll("'", "'\"'\"'")}'`;
}

function cssIdentifier(value) {
  if (globalThis.CSS?.escape) return globalThis.CSS.escape(value);
  return String(value).replaceAll(/([^a-zA-Z0-9_-])/g, "\\$1");
}

function cssString(value) {
  return String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"');
}

function browserExecutablePath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH)
    return process.env.PUPPETEER_EXECUTABLE_PATH;
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
