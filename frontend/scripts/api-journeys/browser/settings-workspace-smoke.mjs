import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  currentUserEmail,
  currentUserId,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/settings-workspace-smoke.png";
const UPDATE_SCREENSHOT_PATH = "/tmp/settings-workspace-name-update-smoke.png";
const REVERT_SCREENSHOT_PATH = "/tmp/settings-workspace-name-revert-smoke.png";
const ERROR_SCREENSHOT_PATH = "/tmp/settings-workspace-error-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const email = currentUserEmail(auth.user);
  const userId = currentUserId(auth.user);
  assert(email, "Authenticated user did not include an email.");
  assert(userId, "Authenticated user did not include an id.");

  const workspaceRows = asArray(
    await auth.client.get(apiPath("/accounts/workspace/list/")),
  );
  const workspace = workspaceRows.find((row) => row?.id === auth.workspaceId);
  assert(workspace, "Workspace list did not include active workspace.");
  const workspaceName = workspace.display_name || workspace.name;
  const shouldMutate = process.env.API_JOURNEY_MUTATIONS === "1";
  const updatedWorkspaceName = `${workspaceName} UI ${auth.runId.replace(/[^a-z0-9-]/gi, "").slice(0, 12)}`;
  let workspaceNameRestored = !shouldMutate;

  const memberRows = asArray(
    await auth.client.get(
      apiPath("/accounts/workspace/{workspace_id}/members/", {
        workspace_id: auth.workspaceId,
      }),
      {
        query: { search: email, page: 1, limit: 10, filter_status: ["Active"] },
      },
    ),
  );
  const currentMember = memberRows.find(
    (row) =>
      row?.id === userId ||
      String(row?.email || "").toLowerCase() === email.toLowerCase(),
  );
  assert(currentMember, "Workspace members API did not return current user.");

  const apiFailures = [];
  const pageErrors = [];
  const evidence = {
    email,
    user_id: userId,
    workspace_id: auth.workspaceId,
    workspace_name: workspaceName,
    workspace_name_mutation_exercised: shouldMutate,
    current_user_ws_level: currentMember.ws_level,
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
      (url.includes("/accounts/workspace/list/") ||
        url.includes(`/accounts/workspace/${auth.workspaceId}/members/`) ||
        url.includes(`/accounts/workspaces/${auth.workspaceId}/`)) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    const workspaceListResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/accounts/workspace/list/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(
      `${APP_BASE}/dashboard/settings/workspace/${auth.workspaceId}/general`,
      { waitUntil: "domcontentloaded" },
    );
    await workspaceListResponse;
    await waitForVisibleText(page, "General", { exact: true });
    await waitForVisibleText(page, "Manage workspace settings", {
      exact: true,
    });
    await waitForVisibleText(page, "Workspace Name", { exact: true });
    await page.waitForSelector("input", { visible: true, timeout: 30000 });
    const inputValue = await page.$eval("input", (element) => element.value);
    assert(
      inputValue === workspaceName,
      `Workspace name input showed ${inputValue}, expected ${workspaceName}.`,
    );
    const saveDisabled = await page.$$eval("button", (buttons) => {
      const saveButton = buttons.find((button) =>
        button.textContent?.includes("Save Changes"),
      );
      return saveButton ? saveButton.disabled : null;
    });
    assert(
      saveDisabled === true,
      "Save Changes button was not disabled before edits.",
    );

    if (shouldMutate) {
      await updateWorkspaceNameThroughBrowser(page, updatedWorkspaceName);
      workspaceNameRestored = false;
      await waitForInputValue(page, "Workspace Name", updatedWorkspaceName);
      let apiWorkspace = await loadActiveWorkspace(auth);
      assert(
        (apiWorkspace.display_name || apiWorkspace.name) ===
          updatedWorkspaceName,
        "Workspace API did not reflect the browser-updated name.",
      );
      await page.screenshot({ path: UPDATE_SCREENSHOT_PATH, fullPage: true });

      await reloadWorkspaceGeneralPage(page, auth.workspaceId);
      await updateWorkspaceNameThroughBrowser(page, workspaceName);
      await waitForInputValue(page, "Workspace Name", workspaceName);
      apiWorkspace = await loadActiveWorkspace(auth);
      assert(
        (apiWorkspace.display_name || apiWorkspace.name) === workspaceName,
        "Workspace API did not reflect the browser name revert.",
      );
      workspaceNameRestored = true;
      evidence.updated_workspace_name = updatedWorkspaceName;
      evidence.reverted_workspace_name = workspaceName;
      evidence.update_screenshot = UPDATE_SCREENSHOT_PATH;
      evidence.revert_screenshot = REVERT_SCREENSHOT_PATH;
      await page.screenshot({ path: REVERT_SCREENSHOT_PATH, fullPage: true });
    }

    const membersResponse = page.waitForResponse(
      (response) =>
        response
          .url()
          .includes(`/accounts/workspace/${auth.workspaceId}/members/`) &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(
      `${APP_BASE}/dashboard/settings/workspace/${auth.workspaceId}/members`,
      { waitUntil: "domcontentloaded" },
    );
    await membersResponse;
    await waitForVisibleText(page, "Members", { exact: true });
    await waitForVisibleText(page, "Manage workspace members and their roles", {
      exact: true,
    });
    await waitForVisibleText(page, "Workspace Role", { exact: true });
    await waitForVisibleText(page, "Status", { exact: true });
    await waitForVisibleText(page, email);

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
    if (shouldMutate && !workspaceNameRestored) {
      await auth.client.put(
        apiPath("/accounts/workspaces/{workspace_id}/", {
          workspace_id: auth.workspaceId,
        }),
        { display_name: workspaceName },
      );
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

async function updateWorkspaceNameThroughBrowser(page, name) {
  await fillInputByLabel(page, "Workspace Name", name);
  const updateResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/accounts/workspaces/") &&
      response.url().includes("/") &&
      response.request().method() === "PUT",
    { timeout: 60000 },
  );
  await clickVisibleButton(page, "Save Changes");
  const updateResponse = await updateResponsePromise;
  assert(
    updateResponse.status() >= 200 && updateResponse.status() < 300,
    `Workspace name update failed with HTTP ${updateResponse.status()}.`,
  );
  const submittedPayload = JSON.parse(
    updateResponse.request().postData() || "{}",
  );
  assert(
    submittedPayload.display_name === name,
    "Workspace name update submitted an unexpected payload.",
  );
  await waitForVisibleText(page, "Workspace updated", { exact: true });
  await waitForNoVisibleText(page, "Saving...", { exact: true });
}

async function reloadWorkspaceGeneralPage(page, workspaceId) {
  const workspaceListResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/accounts/workspace/list/") &&
      response.status() < 400,
    { timeout: 60000 },
  );
  await page.goto(
    `${APP_BASE}/dashboard/settings/workspace/${workspaceId}/general`,
    {
      waitUntil: "domcontentloaded",
    },
  );
  await workspaceListResponse;
  await waitForVisibleText(page, "General", { exact: true });
}

async function loadActiveWorkspace(auth) {
  const rows = asArray(
    await auth.client.get(apiPath("/accounts/workspace/list/")),
  );
  const workspace = rows.find((row) => row?.id === auth.workspaceId);
  assert(workspace, "Workspace list did not include active workspace.");
  return workspace;
}

async function fillInputByLabel(page, label, value) {
  const input = await visibleInputForLabel(page, label);
  await input.click({ clickCount: 3 });
  await page.keyboard.press("Backspace");
  await input.type(value);
}

async function waitForInputValue(page, label, value) {
  await page.waitForFunction(
    ({ label, value }) => {
      const input = window.__apiJourneyFindVisibleInput?.(label);
      return input?.value === value;
    },
    { timeout: 30000 },
    { label, value },
  );
}

async function visibleInputForLabel(page, label) {
  const handle = await page.waitForFunction(
    (expectedLabel) => {
      window.__apiJourneyFindVisibleInput = (label) => {
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
        const labels = Array.from(document.querySelectorAll("label"));
        const labelElement = labels.find(
          (candidate) => String(candidate.textContent || "").trim() === label,
        );
        if (labelElement) {
          const id =
            labelElement.htmlFor || labelElement.id.replace(/-label$/, "");
          const target = document.getElementById(id);
          if (target?.tagName.toLowerCase() === "input" && visible(target)) {
            return target;
          }
        }
        const visibleInputs = Array.from(document.querySelectorAll("input"))
          .filter(visible)
          .filter((input) => input.type !== "hidden");
        return visibleInputs.length ? visibleInputs[0] : null;
      };
      return window.__apiJourneyFindVisibleInput(expectedLabel);
    },
    { timeout: 30000 },
    label,
  );
  const element = handle.asElement();
  assert(element, `Could not resolve input for label "${label}".`);
  return element;
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
          label: input.id,
          value: input.value,
          disabled: input.disabled,
        })),
      buttons: Array.from(document.querySelectorAll("button"))
        .filter(visible)
        .map((button) => ({
          text: String(button.textContent || "").trim(),
          disabled: button.disabled,
        })),
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
