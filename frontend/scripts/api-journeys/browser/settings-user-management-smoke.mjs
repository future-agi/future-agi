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
  envFlag,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFileCallback);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/settings-user-management-smoke.png";
const INVITE_SCREENSHOT_PATH = "/tmp/settings-user-management-invite-smoke.png";
const CANCEL_SCREENSHOT_PATH = "/tmp/settings-user-management-cancel-smoke.png";
const ROLE_SCREENSHOT_PATH = "/tmp/settings-user-management-role-smoke.png";
const REMOVE_SCREENSHOT_PATH = "/tmp/settings-user-management-remove-smoke.png";
const REACTIVATE_SCREENSHOT_PATH =
  "/tmp/settings-user-management-reactivate-smoke.png";
const ERROR_SCREENSHOT_PATH = "/tmp/settings-user-management-error-smoke.png";
const ORG_ADMIN_LEVEL = 8;

async function main() {
  const auth = await createAuthenticatedContext();
  const email = currentUserEmail(auth.user);
  const userId = currentUserId(auth.user);
  assert(email, "Authenticated user did not include an email.");
  assert(userId, "Authenticated user did not include an id.");

  const memberPayload = await auth.client.get(
    apiPath("/accounts/organization/members/"),
    { query: { search: email, page: 1, limit: 10 } },
  );
  const memberRows = asArray(memberPayload);
  const memberRow = memberRows.find(
    (row) =>
      row?.id === userId || row?.email?.toLowerCase() === email.toLowerCase(),
  );
  assert(
    memberRow,
    "RBAC member list did not return the current user by email.",
  );
  const workspaceRows = asArray(
    await auth.client.get(apiPath("/accounts/workspace/list/")),
  );
  const activeWorkspace = workspaceRows.find(
    (row) => row?.id === auth.workspaceId,
  );
  const workspaceName =
    activeWorkspace?.display_name ||
    activeWorkspace?.name ||
    "Default Workspace";
  const shouldMutate = envFlag("API_JOURNEY_MUTATIONS");
  const marker = auth.runId.replace(/[^a-z0-9-]/gi, "").slice(0, 20);
  const inviteEmail = `ui.journey.rbac.${marker}@futureagi.local`.toLowerCase();
  const activeMemberEmail =
    `ui.journey.rbac.member.${marker}@futureagi.local`.toLowerCase();
  const activeMemberPassword = `ApiJourney${marker.slice(0, 8)}123!`;
  let inviteCancelled = !shouldMutate;
  let activeMemberCleaned = !shouldMutate;
  let activeMemberUserId = "";

  const apiFailures = [];
  const pageErrors = [];
  let stage = "starting";
  const evidence = {
    email,
    user_id: userId,
    member_status: memberRow.status,
    member_org_level: memberRow.org_level,
    member_type: memberRow.type,
    workspace_name: workspaceName,
    invite_mutation_exercised: shouldMutate,
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
    window.__userManagementInputByLabel = (expectedLabel) => {
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
      const labelElement = Array.from(document.querySelectorAll("label")).find(
        (candidate) =>
          visible(candidate) &&
          normalize(candidate.textContent) === expectedLabel &&
          (!document.querySelector('[role="dialog"]') ||
            document.querySelector('[role="dialog"]').contains(candidate)),
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
      (url.includes("/accounts/organization/members/") ||
        url.includes("/accounts/organization/invite/") ||
        url.includes("/accounts/workspace/list/") ||
        url.includes("/accounts/user-info/")) &&
      response.status() >= 400
    ) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    stage = "load user management page";
    const initialMembersResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/accounts/organization/members/") &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(`${APP_BASE}/dashboard/settings/user-management`, {
      waitUntil: "domcontentloaded",
    });
    await initialMembersResponse;

    await waitForVisibleText(page, "Members", { exact: true });
    await page.waitForSelector('input[placeholder="Search by name or email"]', {
      visible: true,
      timeout: 30000,
    });
    await waitForVisibleText(page, "Invite User", { exact: true });

    await waitForVisibleText(page, email);
    await waitForVisibleText(page, "Organisation Role", { exact: true });
    await waitForVisibleText(page, "Workspaces", { exact: true });
    await waitForVisibleText(page, "Status", { exact: true });

    const searchedMembersResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/accounts/organization/members/") &&
        response.url().includes(`search=${encodeURIComponent(email)}`) &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.click('input[placeholder="Search by name or email"]', {
      clickCount: 3,
    });
    await page.type('input[placeholder="Search by name or email"]', email);
    await searchedMembersResponse;
    await waitForVisibleText(page, email);

    await clickVisibleText(page, "Invite User", { exact: true });
    await waitForVisibleText(page, "Invite new users", { exact: true });
    await waitForVisibleText(page, "Emails", { exact: true });

    if (shouldMutate) {
      stage = "assert current user can invite";
      assert(
        Number(memberRow.org_level || 0) >= ORG_ADMIN_LEVEL,
        "Current user is not an org admin; browser invite mutation is unsafe.",
      );

      stage = "submit browser invite";
      const inviteResponsePromise = page.waitForResponse(
        (response) =>
          response.url().includes("/accounts/organization/invite/") &&
          response.request().method() === "POST",
        { timeout: 60000 },
      );
      await fillChipsInput(page, "Emails, comma separated", inviteEmail);
      await selectSearchFieldOption(page, "Organization Role", "Viewer");
      await clickEnabledButton(page, "Send Invite");
      const inviteResponse = await inviteResponsePromise;
      const inviteBody = await inviteResponse.json().catch(() => null);
      assert(
        inviteResponse.status() < 400,
        `Browser invite create failed with HTTP ${inviteResponse.status()}: ${JSON.stringify(inviteBody)}`,
      );
      const inviteResult = inviteBody?.result || inviteBody;
      assert(
        asArray(inviteResult?.invited).includes(inviteEmail),
        "Browser invite create response did not include the disposable email.",
      );
      await waitForNoVisibleText(page, "Invite new users", { exact: true });

      stage = "verify browser invite row";
      await searchMembers(page, inviteEmail);
      await waitForVisibleText(page, inviteEmail);
      await waitForVisibleText(page, "Pending", { exact: true });
      let inviteRows = asArray(
        await auth.client.get(apiPath("/accounts/organization/members/"), {
          query: { search: inviteEmail, page: 1, limit: 10 },
        }),
      );
      const pendingInvite = inviteRows.find(
        (row) =>
          row?.type === "invite" &&
          String(row?.email || "").toLowerCase() === inviteEmail,
      );
      assert(
        pendingInvite?.status === "Pending",
        "Member list API did not expose the browser-created pending invite.",
      );
      evidence.invite_email = inviteEmail;
      evidence.invite_id = pendingInvite.id;
      evidence.invite_screenshot = INVITE_SCREENSHOT_PATH;
      await page.screenshot({ path: INVITE_SCREENSHOT_PATH, fullPage: true });

      stage = "open pending invite action menu";
      await clickMemberRowActionMenu(page, inviteEmail);
      await waitForVisibleText(page, "Cancel invite", { exact: true });
      stage = "open cancel invite confirmation";
      await clickVisibleTextElement(page, "Cancel invite", { exact: true });
      await waitForVisibleText(
        page,
        "Are you sure you want to cancel this invite?",
        {
          exact: true,
        },
      );
      stage = "submit browser invite cancel";
      const cancelResponsePromise = page.waitForResponse(
        (response) =>
          response.url().includes("/accounts/organization/invite/cancel/") &&
          response.request().method() === "DELETE",
        { timeout: 60000 },
      );
      await clickDialogButton(page, "Remove");
      const cancelResponse = await cancelResponsePromise;
      const cancelBody = await cancelResponse.json().catch(() => null);
      assert(
        cancelResponse.status() < 400,
        `Browser invite cancel failed with HTTP ${cancelResponse.status()}: ${JSON.stringify(cancelBody)}`,
      );
      assert(
        String(cancelBody?.result?.message || cancelBody?.message || "")
          .toLowerCase()
          .includes("cancel"),
        "Browser invite cancel response did not report cancellation.",
      );

      stage = "verify browser invite cancel";
      await waitForNoVisibleText(page, inviteEmail);
      inviteRows = asArray(
        await auth.client.get(apiPath("/accounts/organization/members/"), {
          query: { search: inviteEmail, page: 1, limit: 10 },
        }),
      );
      assert(
        !inviteRows.some(
          (row) => String(row?.email || "").toLowerCase() === inviteEmail,
        ),
        "Cancelled invite was still visible in the organization members API.",
      );
      inviteCancelled = true;
      evidence.cancel_screenshot = CANCEL_SCREENSHOT_PATH;
      await page.screenshot({ path: CANCEL_SCREENSHOT_PATH, fullPage: true });

      stage = "setup accepted disposable member";
      const activeMember = await createAcceptedRbacMember({
        auth,
        email: activeMemberEmail,
        password: activeMemberPassword,
        orgLevel: 3,
        workspaceLevel: 3,
      });
      activeMemberUserId = activeMember.user_id;
      activeMemberCleaned = false;
      evidence.active_member_email = activeMemberEmail;
      evidence.active_member_user_id = activeMemberUserId;

      stage = "verify accepted member row";
      await searchMembers(page, activeMemberEmail);
      await waitForVisibleText(page, activeMemberEmail);
      await waitForMemberRowText(page, activeMemberEmail, "Active");
      await waitForMemberRowText(page, activeMemberEmail, "Member");

      stage = "update accepted member role";
      await clickMemberRowActionMenu(page, activeMemberEmail, "Edit user info");
      await clickVisibleTextElement(page, "Edit user info", { exact: true });
      await waitForVisibleText(page, "Edit user info", { exact: true });
      await selectSearchFieldOption(page, "Organization Role", "Admin");
      const roleResponsePromise = page.waitForResponse(
        (response) =>
          response.url().includes("/accounts/organization/members/role/") &&
          response.request().method() === "POST",
        { timeout: 60000 },
      );
      await clickDialogButton(page, "Update");
      const roleResponse = await roleResponsePromise;
      const roleBody = await roleResponse.json().catch(() => null);
      assert(
        roleResponse.status() < 400,
        `Browser member role update failed with HTTP ${roleResponse.status()}: ${JSON.stringify(roleBody)}`,
      );
      await waitForNoVisibleText(page, "Edit user info", { exact: true });
      let activeRows = asArray(
        await auth.client.get(apiPath("/accounts/organization/members/"), {
          query: { search: activeMemberEmail, page: 1, limit: 10 },
        }),
      );
      let activeRow = findMemberRow(activeRows, activeMemberEmail);
      assert(
        activeRow?.org_level === 8,
        "Browser role update did not persist Admin org level.",
      );
      await waitForMemberRowText(page, activeMemberEmail, "Admin");
      evidence.role_screenshot = ROLE_SCREENSHOT_PATH;
      await page.screenshot({ path: ROLE_SCREENSHOT_PATH, fullPage: true });

      stage = "remove accepted member";
      await clickMemberRowActionMenu(
        page,
        activeMemberEmail,
        "Remove from organization",
      );
      await clickVisibleTextElement(page, "Remove from organization", {
        exact: true,
      });
      await waitForVisibleText(
        page,
        "Are you sure you want to remove this member?",
        { exact: true },
      );
      const removeResponsePromise = page.waitForResponse(
        (response) =>
          response.url().includes("/accounts/organization/members/remove/") &&
          response.request().method() === "DELETE",
        { timeout: 60000 },
      );
      await clickDialogButton(page, "Remove");
      const removeResponse = await removeResponsePromise;
      const removeBody = await removeResponse.json().catch(() => null);
      assert(
        removeResponse.status() < 400,
        `Browser member remove failed with HTTP ${removeResponse.status()}: ${JSON.stringify(removeBody)}`,
      );
      await waitForMemberRowText(page, activeMemberEmail, "Deactivated");
      activeRows = asArray(
        await auth.client.get(apiPath("/accounts/organization/members/"), {
          query: { search: activeMemberEmail, page: 1, limit: 10 },
        }),
      );
      activeRow = findMemberRow(activeRows, activeMemberEmail);
      assert(
        activeRow?.status === "Deactivated",
        "Browser member remove did not reload the row as Deactivated.",
      );
      evidence.remove_screenshot = REMOVE_SCREENSHOT_PATH;
      await page.screenshot({ path: REMOVE_SCREENSHOT_PATH, fullPage: true });

      stage = "reactivate accepted member";
      await clickMemberRowActionMenu(
        page,
        activeMemberEmail,
        "Reactivate member",
      );
      await clickVisibleTextElement(page, "Reactivate member", { exact: true });
      await waitForVisibleText(page, "Reactivate this member?", {
        exact: true,
      });
      const reactivateResponsePromise = page.waitForResponse(
        (response) =>
          response
            .url()
            .includes("/accounts/organization/members/reactivate/") &&
          response.request().method() === "POST",
        { timeout: 60000 },
      );
      await clickDialogButton(page, "Reactivate");
      const reactivateResponse = await reactivateResponsePromise;
      const reactivateBody = await reactivateResponse.json().catch(() => null);
      assert(
        reactivateResponse.status() < 400,
        `Browser member reactivate failed with HTTP ${reactivateResponse.status()}: ${JSON.stringify(reactivateBody)}`,
      );
      await waitForMemberRowText(page, activeMemberEmail, "Active");
      activeRows = asArray(
        await auth.client.get(apiPath("/accounts/organization/members/"), {
          query: { search: activeMemberEmail, page: 1, limit: 10 },
        }),
      );
      activeRow = findMemberRow(activeRows, activeMemberEmail);
      assert(
        activeRow?.status === "Active",
        "Browser member reactivate did not reload the row as Active.",
      );
      evidence.reactivate_screenshot = REACTIVATE_SCREENSHOT_PATH;
      await page.screenshot({
        path: REACTIVATE_SCREENSHOT_PATH,
        fullPage: true,
      });
    } else {
      stage = "close read-only invite drawer";
      await page.keyboard.press("Escape");
      await waitForNoVisibleText(page, "Invite new users", { exact: true });
    }

    stage = "final assertions";
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
          stage,
          api_failures: apiFailures,
          debug: await collectDebugState(page),
          error_screenshot: ERROR_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    if (shouldMutate && !inviteCancelled) {
      await cancelInvitesByEmail(auth, inviteEmail);
    }
    if (shouldMutate && !activeMemberCleaned) {
      await cleanupAcceptedMember(auth, {
        email: activeMemberEmail,
        userId: activeMemberUserId,
      });
      activeMemberCleaned = true;
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

async function fillChipsInput(page, placeholder, value) {
  const selector = `input[placeholder="${cssString(placeholder)}"]`;
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector);
  await page.keyboard.type(value);
  await page.keyboard.press("Enter");
  await waitForVisibleText(page, value, { exact: true });
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
      Boolean(window.__userManagementInputByLabel(expectedLabel)),
    { timeout: 30000 },
    label,
  );
  const box = await page.evaluate(
    ({ expectedLabel, strategy }) => {
      const input = window.__userManagementInputByLabel(expectedLabel);
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

async function waitForLabeledInputValue(page, label, value) {
  await page.waitForFunction(
    ({ label: expectedLabel, value: expectedValue }) => {
      const input = window.__userManagementInputByLabel(expectedLabel);
      return input?.value === expectedValue;
    },
    { timeout: 30000 },
    { label, value },
  );
}

async function searchMembers(page, search) {
  const searchedMembersResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/accounts/organization/members/") &&
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

async function clickEnabledButton(page, text) {
  await clickableTextBox(page, text, {
    exact: true,
    selector: "button",
    enabledOnly: true,
  });
  const clicked = await page.evaluate((expectedText) => {
    const button = Array.from(document.querySelectorAll("button")).find(
      (candidate) =>
        !candidate.disabled &&
        String(candidate.textContent || "").trim() === expectedText,
    );
    if (!button) return false;
    button.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    button.click();
    return true;
  }, text);
  assert(clicked, `Could not click enabled button ${text}.`);
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
  const box = await page.evaluate((expectedText) => {
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
    if (!button) return null;
    button.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    button.click();
    const rect = button.getBoundingClientRect();
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  }, text);
  assert(box, `Could not click dialog button ${text}.`);
}

async function clickMemberRowActionMenu(
  page,
  email,
  expectedMenuText = "Cancel invite",
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
    const opened = await page
      .waitForFunction(
        (text) => document.body.innerText.includes(text),
        { timeout: 1000 },
        expectedMenuText,
      )
      .then(() => true)
      .catch(() => false);
    if (opened) return;
  }
  throw new Error(`Could not open action menu for ${email}.`);
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

async function clickVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await waitForVisibleText(page, text, { exact, timeout });
  await page.evaluate(
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
      const element = Array.from(
        document.querySelectorAll("button, [role='button']"),
      ).find((candidate) => {
        if (!isVisible(candidate)) return false;
        const textContent = normalized(candidate.textContent);
        if (exactMatch) return textContent === expectedText;
        return textContent.includes(expectedText);
      });
      if (!element)
        throw new Error(`No visible clickable text: ${expectedText}`);
      element.click();
    },
    { text, exact },
  );
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

async function cancelInvitesByEmail(auth, email) {
  const rows = asArray(
    await auth.client.get(apiPath("/accounts/organization/members/"), {
      query: { search: email, page: 1, limit: 10 },
    }),
  );
  for (const row of rows) {
    if (
      row?.type === "invite" &&
      String(row?.email || "").toLowerCase() === email
    ) {
      await auth.client.delete(
        apiPath("/accounts/organization/invite/cancel/"),
        {
          body: { invite_id: row.id },
        },
      );
    }
  }
}

async function createAcceptedRbacMember({
  auth,
  email,
  password,
  orgLevel,
  workspaceLevel,
}) {
  const invited = await auth.client.post(
    apiPath("/accounts/organization/invite/"),
    {
      emails: [email],
      org_level: orgLevel,
      workspace_access: [
        { workspace_id: auth.workspaceId, level: workspaceLevel },
      ],
    },
  );
  assert(
    asArray(invited?.invited).includes(email),
    "Accepted-member setup invite response did not include disposable email.",
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
    "Accepted-member setup did not create a Pending invite row.",
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
    "Accepted-member setup invite preview did not validate.",
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
    "Accepted-member setup did not return access and refresh tokens.",
  );

  rows = asArray(
    await auth.client.get(apiPath("/accounts/organization/members/"), {
      query: { search: email, page: 1, limit: 10 },
    }),
  );
  const activeMember = findMemberRow(rows, email);
  assert(
    activeMember?.type === "member" && activeMember?.status === "Active",
    "Accepted-member setup did not reload as an Active member.",
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
      rows: Array.from(document.querySelectorAll('[role="row"]'))
        .filter(visible)
        .slice(0, 10)
        .map((row) => String(row.textContent || "").trim()),
    };
  });
}

function cssString(value) {
  return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
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
