import { execFile as execFileCallback } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
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
const execFileAsync = promisify(execFileCallback);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/settings-workspace-smoke.png";
const UPDATE_SCREENSHOT_PATH = "/tmp/settings-workspace-name-update-smoke.png";
const REVERT_SCREENSHOT_PATH = "/tmp/settings-workspace-name-revert-smoke.png";
const MEMBER_ROLE_SCREENSHOT_PATH =
  "/tmp/settings-workspace-member-role-smoke.png";
const MEMBER_REMOVE_SCREENSHOT_PATH =
  "/tmp/settings-workspace-member-remove-smoke.png";
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
  const alternateWorkspace = workspaceRows.find(
    (row) => row?.id && row.id !== auth.workspaceId,
  );
  const workspaceName = workspace.display_name || workspace.name;
  const shouldMutate = process.env.API_JOURNEY_MUTATIONS === "1";
  const marker = auth.runId.replace(/[^a-z0-9-]/gi, "").slice(0, 20);
  const updatedWorkspaceName = `${workspaceName} UI ${auth.runId.replace(/[^a-z0-9-]/gi, "").slice(0, 12)}`;
  const workspaceMemberEmail =
    `ui.journey.workspace.member.${marker}@futureagi.local`.toLowerCase();
  const workspaceMemberPassword = `ApiJourney${marker.slice(0, 8)}123!`;
  let workspaceNameRestored = !shouldMutate;
  let workspaceMemberCleaned = !shouldMutate;
  let workspaceMemberUserId = "";

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
    workspace_member_mutation_exercised: shouldMutate,
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
  await page.evaluateOnNewDocument(() => {
    window.__workspaceJourneyInputByLabel = (expectedLabel) => {
      const normalize = (value) =>
        String(value || "")
          .replace(/\s*\*$/, "")
          .trim();
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
      const dialog = document.querySelector('[role="dialog"]');
      const labelElement = Array.from(document.querySelectorAll("label")).find(
        (candidate) =>
          visible(candidate) &&
          normalize(candidate.textContent) === expectedLabel &&
          (!dialog || dialog.contains(candidate)),
      );
      const root =
        labelElement?.closest(".MuiFormControl-root") ||
        labelElement?.parentElement?.parentElement;
      return root?.querySelector("input") || null;
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
  page.on("response", (response) => {
    const url = response.url();
    if (
      (url.includes("/accounts/workspace/list/") ||
        url.includes(`/accounts/workspace/${auth.workspaceId}/members/`) ||
        url.includes(`/accounts/workspace/${auth.workspaceId}/members/role/`) ||
        url.includes(
          `/accounts/workspace/${auth.workspaceId}/members/remove/`,
        ) ||
        url.includes("/accounts/organization/invite/") ||
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
      assert(
        alternateWorkspace,
        "Workspace member remove mutation needs a second workspace so cleanup does not hit the last-workspace guard.",
      );
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

      workspaceMemberCleaned = false;
      const workspaceMember = await createAcceptedWorkspaceMember({
        auth,
        email: workspaceMemberEmail,
        password: workspaceMemberPassword,
        workspaceAccess: [
          { workspace_id: auth.workspaceId, level: 3 },
          { workspace_id: alternateWorkspace.id, level: 3 },
        ],
      });
      workspaceMemberUserId = workspaceMember.user_id;
      evidence.workspace_member_email = workspaceMemberEmail;
      evidence.workspace_member_user_id = workspaceMemberUserId;
      evidence.alternate_workspace_id = alternateWorkspace.id;
      evidence.alternate_workspace_name =
        alternateWorkspace.display_name || alternateWorkspace.name;
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

    if (shouldMutate) {
      await searchWorkspaceMembers(
        page,
        auth.workspaceId,
        workspaceMemberEmail,
      );
      await waitForMemberRowText(page, workspaceMemberEmail, "Active");
      await waitForMemberRowText(
        page,
        workspaceMemberEmail,
        "Workspace Member",
      );

      await clickMemberRowActionMenu(
        page,
        workspaceMemberEmail,
        "Edit user info",
      );
      await clickVisibleTextElement(page, "Edit user info", { exact: true });
      await waitForVisibleText(page, "Edit workspace role", { exact: true });
      await selectSearchFieldOption(page, "Workspace Role", "Workspace Viewer");
      const roleResponsePromise = page.waitForResponse(
        (response) =>
          response
            .url()
            .includes(
              `/accounts/workspace/${auth.workspaceId}/members/role/`,
            ) && response.request().method() === "POST",
        { timeout: 60000 },
      );
      await clickDialogButton(page, "Update");
      const roleResponse = await roleResponsePromise;
      const roleBody = await roleResponse.json().catch(() => null);
      assert(
        roleResponse.status() < 400,
        `Browser workspace role update failed with HTTP ${roleResponse.status()}: ${JSON.stringify(roleBody)}`,
      );
      const rolePayload = JSON.parse(roleResponse.request().postData() || "{}");
      assert(
        rolePayload.user_id === workspaceMemberUserId &&
          rolePayload.ws_level === 1,
        "Workspace role update submitted an unexpected payload.",
      );
      await waitForNoVisibleText(page, "Edit workspace role", { exact: true });
      let rows = asArray(
        await auth.client.get(
          apiPath("/accounts/workspace/{workspace_id}/members/", {
            workspace_id: auth.workspaceId,
          }),
          {
            query: {
              search: workspaceMemberEmail,
              page: 1,
              limit: 10,
              filter_status: ["Active"],
            },
          },
        ),
      );
      let row = findMemberRow(rows, workspaceMemberEmail);
      assert(
        row?.ws_level === 1,
        "Workspace role update did not persist Workspace Viewer level.",
      );
      await waitForMemberRowText(
        page,
        workspaceMemberEmail,
        "Workspace Viewer",
      );
      evidence.member_role_screenshot = MEMBER_ROLE_SCREENSHOT_PATH;
      await page.screenshot({
        path: MEMBER_ROLE_SCREENSHOT_PATH,
        fullPage: true,
      });

      await clickMemberRowActionMenu(
        page,
        workspaceMemberEmail,
        "Remove from workspace",
      );
      await clickVisibleTextElement(page, "Remove from workspace", {
        exact: true,
      });
      await waitForVisibleText(
        page,
        "Are you sure you want to remove this member from the workspace?",
        { exact: true },
      );
      const removeResponsePromise = page.waitForResponse(
        (response) =>
          response
            .url()
            .includes(
              `/accounts/workspace/${auth.workspaceId}/members/remove/`,
            ) && response.request().method() === "DELETE",
        { timeout: 60000 },
      );
      await clickDialogButton(page, "Remove");
      const removeResponse = await removeResponsePromise;
      const removeBody = await removeResponse.json().catch(() => null);
      assert(
        removeResponse.status() < 400,
        `Browser workspace member remove failed with HTTP ${removeResponse.status()}: ${JSON.stringify(removeBody)}`,
      );
      rows = asArray(
        await auth.client.get(
          apiPath("/accounts/workspace/{workspace_id}/members/", {
            workspace_id: auth.workspaceId,
          }),
          {
            query: {
              search: workspaceMemberEmail,
              page: 1,
              limit: 10,
              filter_status: ["Active"],
            },
          },
        ),
      );
      row = findMemberRow(rows, workspaceMemberEmail);
      assert(
        !row,
        "Workspace member remove did not remove the row from active workspace member search.",
      );
      await waitForNoMemberRow(page, workspaceMemberEmail);
      evidence.member_remove_screenshot = MEMBER_REMOVE_SCREENSHOT_PATH;
      await page.screenshot({
        path: MEMBER_REMOVE_SCREENSHOT_PATH,
        fullPage: true,
      });
    }

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
    if (shouldMutate && !workspaceMemberCleaned) {
      await cleanupAcceptedMember(auth, {
        email: workspaceMemberEmail,
        userId: workspaceMemberUserId,
      });
      workspaceMemberCleaned = true;
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

async function selectSearchFieldOption(page, label, option) {
  await openSearchSelectField(page, label, option);
  await clickVisibleTextElement(page, option, {
    exact: true,
    selector: '[role="menuitem"], .MuiMenuItem-root, li',
  });
  await waitForLabeledInputValue(page, label, option);
}

async function openSearchSelectField(page, label, option) {
  const strategies = ["input", "root", "icon", "dispatch"];
  for (const strategy of strategies) {
    const box = await labeledInputBox(page, label, strategy);
    if (box.dispatched) {
      await page.waitForTimeout(100);
    } else {
      await page.mouse.click(box.x, box.y);
    }
    const opened = await visibleTextExists(page, option, {
      exact: true,
      selector: '[role="menuitem"], .MuiMenuItem-root, li',
      timeout: 1000,
    });
    if (opened) return;
  }
  throw new Error(`Could not open search select ${label}.`);
}

async function labeledInputBox(page, label, strategy = "input") {
  await page.waitForFunction(
    (expectedLabel) =>
      Boolean(window.__workspaceJourneyInputByLabel(expectedLabel)),
    { timeout: 30000 },
    label,
  );
  const box = await page.evaluate(
    ({ expectedLabel, strategy }) => {
      const input = window.__workspaceJourneyInputByLabel(expectedLabel);
      if (!input) return null;
      const root =
        input.closest(".MuiFormControl-root") ||
        input.closest(".MuiTextField-root");
      if (strategy === "dispatch") {
        input.focus();
        input.dispatchEvent(new FocusEvent("focus", { bubbles: true }));
        input.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
        input.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
        input.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        root?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        return { dispatched: true };
      }
      let target = input;
      if (strategy === "root") {
        target = root || input;
      }
      if (strategy === "icon") {
        target =
          root?.querySelector(".MuiInputAdornment-root svg") ||
          root?.querySelector(".MuiInputAdornment-root [class*='Iconify']") ||
          input;
      }
      const rect = target.getBoundingClientRect();
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    },
    { expectedLabel: label, strategy },
  );
  assert(box, `Could not locate input for ${label}.`);
  return box;
}

async function waitForLabeledInputValue(page, label, value) {
  await page.waitForFunction(
    ({ label: expectedLabel, value: expectedValue }) => {
      const input = window.__workspaceJourneyInputByLabel(expectedLabel);
      return input?.value === expectedValue;
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

async function searchWorkspaceMembers(page, workspaceId, search) {
  const searchedMembersResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/accounts/workspace/${workspaceId}/members/`) &&
      new URL(response.url()).searchParams.get("search") === search &&
      response.status() < 400,
    { timeout: 60000 },
  );
  const selector = 'input[placeholder="Search by name or email"]';
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector, { clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.type(selector, search);
  await searchedMembersResponse;
}

async function waitForMemberRowText(page, email, text) {
  await page.waitForFunction(
    ({ expectedEmail, expectedText }) => {
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
      return Array.from(document.querySelectorAll('[role="row"]')).some(
        (row) =>
          visible(row) &&
          String(row.textContent || "")
            .toLowerCase()
            .includes(expectedEmail) &&
          String(row.textContent || "").includes(expectedText),
      );
    },
    { timeout: 30000 },
    { expectedEmail: email, expectedText: text },
  );
}

async function waitForNoMemberRow(page, email) {
  await page.waitForFunction(
    (expectedEmail) => {
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
      return !Array.from(document.querySelectorAll('[role="row"]')).some(
        (row) =>
          visible(row) &&
          String(row.textContent || "")
            .toLowerCase()
            .includes(expectedEmail),
      );
    },
    { timeout: 30000 },
    email,
  );
}

async function clickMemberRowActionMenu(
  page,
  email,
  expectedMenuText = "Edit user info",
) {
  await page.waitForFunction(
    (expectedEmail) =>
      Array.from(document.querySelectorAll('[role="row"]')).some((row) =>
        String(row.textContent || "")
          .toLowerCase()
          .includes(expectedEmail),
      ),
    { timeout: 30000 },
    email,
  );
  const strategies = [
    "action-cell-center",
    "action-icon-center",
    "row-right-center",
    "dispatch-stack-click",
  ];
  for (const strategy of strategies) {
    const box = await page.evaluate(
      ({ expectedEmail, strategy: clickStrategy }) => {
        const row = Array.from(document.querySelectorAll('[role="row"]')).find(
          (candidate) =>
            String(candidate.textContent || "")
              .toLowerCase()
              .includes(expectedEmail),
        );
        const actionCell = row?.querySelector('[col-id="action"]');
        if (!row || !actionCell) return null;
        if (clickStrategy === "dispatch-stack-click") {
          const target =
            actionCell.querySelector(".MuiStack-root") ||
            actionCell.querySelector("svg") ||
            actionCell;
          target.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
          target.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
          target.dispatchEvent(new MouseEvent("click", { bubbles: true }));
          target.click?.();
          return { dispatched: true };
        }
        let target = actionCell;
        if (clickStrategy === "action-icon-center") {
          target =
            actionCell.querySelector(
              "svg, [class*='Iconify'], [data-testid]",
            ) || actionCell;
        }
        const rect =
          clickStrategy === "row-right-center"
            ? row.getBoundingClientRect()
            : target.getBoundingClientRect();
        if (clickStrategy === "row-right-center") {
          return { x: rect.right - 24, y: rect.top + rect.height / 2 };
        }
        return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
      },
      { expectedEmail: email, strategy },
    );
    assert(box, `Could not find action cell for ${email}.`);
    if (!box.dispatched) {
      await page.mouse.click(box.x, box.y);
    }
    const opened = await visibleTextExists(page, expectedMenuText, {
      exact: true,
      timeout: 1000,
    });
    if (opened) return;
  }
  throw new Error(`Could not open action menu for ${email}.`);
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

async function clickDialogButton(page, text) {
  await page.waitForFunction(
    (expectedText) => {
      const dialog = document.querySelector('[role="dialog"]');
      if (!dialog) return false;
      return Array.from(dialog.querySelectorAll("button")).some((button) => {
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
      });
    },
    { timeout: 30000 },
    text,
  );
  const clicked = await page.evaluate((expectedText) => {
    const dialog = document.querySelector('[role="dialog"]');
    const button = Array.from(dialog.querySelectorAll("button")).find(
      (candidate) => {
        const style = window.getComputedStyle(candidate);
        const rect = candidate.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0 &&
          !candidate.disabled &&
          String(candidate.textContent || "").trim() === expectedText
        );
      },
    );
    if (!button) return false;
    button.click();
    return true;
  }, text);
  assert(clicked, `Could not click dialog button ${text}.`);
}

async function clickVisibleTextElement(
  page,
  text,
  { exact = false, selector = "body *" } = {},
) {
  const box = await clickableTextBox(page, text, { exact, selector });
  await page.mouse.click(box.x, box.y);
}

async function clickableTextBox(
  page,
  text,
  { exact = false, selector = "body *", enabledOnly = false } = {},
) {
  await page.waitForFunction(
    ({
      text: expectedText,
      exact: exactMatch,
      selector: targetSelector,
      enabledOnly: enabled,
    }) => {
      const normalize = (value) => String(value || "").trim();
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
      return Array.from(document.querySelectorAll(targetSelector)).some(
        (candidate) => {
          if (!visible(candidate)) return false;
          if (enabled && candidate.disabled) return false;
          const textContent = normalize(candidate.textContent);
          return exactMatch
            ? textContent === expectedText
            : textContent.includes(expectedText);
        },
      );
    },
    { timeout: 30000 },
    { text, exact, selector, enabledOnly },
  );
  const box = await page.evaluate(
    ({
      text: expectedText,
      exact: exactMatch,
      selector: targetSelector,
      enabledOnly: enabled,
    }) => {
      const normalize = (value) => String(value || "").trim();
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
      const elements = Array.from(document.querySelectorAll(targetSelector))
        .filter((candidate) => {
          if (!visible(candidate)) return false;
          if (enabled && candidate.disabled) return false;
          const textContent = normalize(candidate.textContent);
          return exactMatch
            ? textContent === expectedText
            : textContent.includes(expectedText);
        })
        .sort((a, b) => {
          const aRect = a.getBoundingClientRect();
          const bRect = b.getBoundingClientRect();
          return aRect.width * aRect.height - bRect.width * bRect.height;
        });
      const element = elements[0];
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    },
    { text, exact, selector, enabledOnly },
  );
  assert(box, `Could not find visible text ${text}.`);
  return box;
}

async function visibleTextExists(
  page,
  text,
  { exact = false, selector = "body *", timeout = 30000 } = {},
) {
  return page
    .waitForFunction(
      ({ text: expectedText, exact: exactMatch, selector: targetSelector }) => {
        const normalize = (value) => String(value || "").trim();
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
        return Array.from(document.querySelectorAll(targetSelector)).some(
          (candidate) => {
            if (!visible(candidate)) return false;
            const textContent = normalize(candidate.textContent);
            return exactMatch
              ? textContent === expectedText
              : textContent.includes(expectedText);
          },
        );
      },
      { timeout },
      { text, exact, selector },
    )
    .then(() => true)
    .catch(() => false);
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

async function createAcceptedWorkspaceMember({
  auth,
  email,
  password,
  workspaceAccess,
}) {
  const invited = await auth.client.post(
    apiPath("/accounts/organization/invite/"),
    {
      emails: [email],
      org_level: 3,
      workspace_access: workspaceAccess,
    },
  );
  assert(
    asArray(invited?.invited).includes(email),
    "Workspace-member setup invite response did not include disposable email.",
  );

  let rows = asArray(
    await auth.client.get(apiPath("/accounts/organization/members/"), {
      query: { search: email, page: 1, limit: 10 },
    }),
  );
  const pendingInvite = rows.find(
    (row) =>
      row?.type === "invite" &&
      String(row?.email || "").toLowerCase() === email,
  );
  assert(
    pendingInvite?.status === "Pending",
    "Workspace-member setup did not create a Pending invite row.",
  );

  const tokenInfo = await resolveInviteAcceptanceToken(email);
  const acceptPath = apiPath("/accounts/accept-invitation/{uidb64}/{token}/", {
    uidb64: tokenInfo.uidb64,
    token: tokenInfo.token,
  });
  const preview = await unauthenticatedApiRequest(
    auth.apiBase,
    "GET",
    acceptPath,
  );
  assert(
    preview?.valid === true && preview?.email === email,
    "Workspace-member setup invite preview did not validate.",
  );
  const accepted = await unauthenticatedApiRequest(
    auth.apiBase,
    "POST",
    acceptPath,
    {
      new_password: password,
      repeat_password: password,
    },
  );
  assert(
    typeof accepted?.access === "string" &&
      typeof accepted?.refresh === "string",
    "Workspace-member setup did not return access and refresh tokens.",
  );

  rows = asArray(
    await auth.client.get(
      apiPath("/accounts/workspace/{workspace_id}/members/", {
        workspace_id: auth.workspaceId,
      }),
      {
        query: { search: email, page: 1, limit: 10, filter_status: ["Active"] },
      },
    ),
  );
  const activeMember = findMemberRow(rows, email);
  assert(
    activeMember?.type === "member" && activeMember?.status === "Active",
    "Workspace-member setup did not reload as an Active workspace member.",
  );
  assert(
    activeMember.ws_level === 3,
    "Workspace-member setup did not persist Workspace Member level.",
  );
  return {
    user_id: tokenInfo.user_id,
    member: activeMember,
  };
}

async function cleanupAcceptedMember(auth, { email, userId }) {
  if (userId) {
    await ignoreNotFound(() =>
      auth.client.delete(
        apiPath("/accounts/team/users/{member_id}/", {
          member_id: userId,
        }),
      ),
    );
  }
  await deleteDisposableRbacUserArtifacts(email);
}

function findMemberRow(rows, email) {
  return asArray(rows).find(
    (row) => String(row?.email || "").toLowerCase() === email,
  );
}

async function resolveInviteAcceptanceToken(email) {
  const script = `
import json
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from accounts.models import User
user = User.objects.get(email=${JSON.stringify(email)})
print(json.dumps({
    "user_id": str(user.id),
    "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
    "token": default_token_generator.make_token(user),
}))
`;
  return runBackendShellJson(script);
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
      { maxBuffer: 20 * 1024 * 1024 },
    ));
  } else {
    const backendDir = process.env.API_JOURNEY_BACKEND_DIR || "futureagi";
    ({ stdout } = await execFileAsync(
      "uv",
      ["run", "python", "manage.py", "shell", "-c", script],
      {
        cwd: backendDir,
        env: {
          ...process.env,
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

async function unauthenticatedApiRequest(apiBase, method, pathName, body) {
  const response = await fetch(new URL(pathName, apiBase), {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (!response.ok) {
    throw new Error(
      `${method} ${pathName} failed with HTTP ${response.status}: ${text.slice(0, 1000)}`,
    );
  }
  if (payload && typeof payload === "object" && payload.status === false) {
    throw new Error(
      `${method} ${pathName} returned status:false: ${JSON.stringify(payload).slice(0, 1000)}`,
    );
  }
  return payload?.result ?? payload;
}

async function deleteDisposableRbacUserArtifacts(email) {
  const sql = `
WITH requested AS (
  SELECT lower(${sqlTextLiteral(email)}) AS email
),
user_rows AS (
  SELECT u.id
  FROM accounts_user u
  JOIN requested r ON lower(u.email) = r.email
),
deleted_auth_tokens AS (
  DELETE FROM accounts_auth_token token
  USING user_rows u
  WHERE token.user_id = u.id
  RETURNING token.id
),
deleted_recovery_codes AS (
  DELETE FROM accounts_recovery_code code
  USING user_rows u
  WHERE code.user_id = u.id
  RETURNING code.id
),
deleted_totp_devices AS (
  DELETE FROM accounts_user_totp_device device
  USING user_rows u
  WHERE device.user_id = u.id
  RETURNING device.id
),
deleted_webauthn_credentials AS (
  DELETE FROM accounts_webauthn_credential credential
  USING user_rows u
  WHERE credential.user_id = u.id
  RETURNING credential.id
),
deleted_user_groups AS (
  DELETE FROM accounts_user_groups user_group
  USING user_rows u
  WHERE user_group.user_id = u.id
  RETURNING user_group.id
),
deleted_user_permissions AS (
  DELETE FROM accounts_user_user_permissions user_permission
  USING user_rows u
  WHERE user_permission.user_id = u.id
  RETURNING user_permission.id
),
deleted_workspace_memberships AS (
  DELETE FROM accounts_workspacemembership membership
  USING user_rows u
  WHERE membership.user_id = u.id
  RETURNING membership.id
),
deleted_org_memberships AS (
  DELETE FROM accounts_organization_membership membership
  USING user_rows u
  WHERE membership.user_id = u.id
  RETURNING membership.id
),
deleted_invites AS (
  DELETE FROM accounts_organization_invite oi
  USING requested r
  WHERE lower(oi.target_email) = r.email
  RETURNING oi.id
),
deleted_users AS (
  DELETE FROM accounts_user u
  USING requested r
  WHERE lower(u.email) = r.email
  RETURNING u.id
)
SELECT json_build_object(
  'deleted_invites', (SELECT count(*) FROM deleted_invites),
  'deleted_auth_tokens', (SELECT count(*) FROM deleted_auth_tokens),
  'deleted_recovery_codes', (SELECT count(*) FROM deleted_recovery_codes),
  'deleted_totp_devices', (SELECT count(*) FROM deleted_totp_devices),
  'deleted_webauthn_credentials', (SELECT count(*) FROM deleted_webauthn_credentials),
  'deleted_user_groups', (SELECT count(*) FROM deleted_user_groups),
  'deleted_user_permissions', (SELECT count(*) FROM deleted_user_permissions),
  'deleted_workspace_memberships', (SELECT count(*) FROM deleted_workspace_memberships),
  'deleted_org_memberships', (SELECT count(*) FROM deleted_org_memberships),
  'deleted_users', (SELECT count(*) FROM deleted_users),
  'remaining_invites', (
    SELECT count(*) FROM accounts_organization_invite oi, requested r
    WHERE lower(oi.target_email) = r.email
  ),
  'remaining_users', (
    SELECT count(*) FROM accounts_user u, requested r
    WHERE lower(u.email) = r.email
  )
);
`;
  return runPostgresJson(sql);
}

async function runPostgresJson(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
  const user = process.env.API_JOURNEY_DB_USER || "user";
  const database = process.env.API_JOURNEY_DB_NAME || "tfc";
  const { stdout } = await execFileAsync(
    "docker",
    ["exec", container, "psql", "-U", user, "-d", database, "-At", "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const text = stdout.trim();
  assert(text, "Postgres DB cleanup returned no JSON output.");
  return JSON.parse(text);
}

function sqlTextLiteral(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`;
}

function shellQuote(value) {
  return `'${String(value).replaceAll("'", "'\"'\"'")}'`;
}

async function ignoreNotFound(fn) {
  try {
    return await fn();
  } catch (error) {
    const message = String(error?.message || "").toLowerCase();
    if (
      error?.status === 404 ||
      message.includes("not found") ||
      message.includes("does not exist") ||
      message.includes("no keys are associated") ||
      (message.includes("no ") && message.includes(" matches "))
    ) {
      return null;
    }
    throw error;
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
