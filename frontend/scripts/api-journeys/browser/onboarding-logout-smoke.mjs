import { execFile as execFileCallback } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
  createApiClient,
  createAuthenticatedContext,
  currentUserId,
  envFlag,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFileCallback);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const ONBOARDING_SCREENSHOT_PATH = "/tmp/onboarding-form-smoke.png";
const LOGOUT_SCREENSHOT_PATH = "/tmp/onboarding-logout-smoke.png";
const ERROR_SCREENSHOT_PATH = "/tmp/onboarding-logout-error-smoke.png";

async function main() {
  assert(
    envFlag("API_JOURNEY_MUTATIONS"),
    "Set API_JOURNEY_MUTATIONS=1 to run the onboarding/logout browser smoke.",
  );

  const ownerAuth = await createAuthenticatedContext();
  assert(
    isOrgOwner(ownerAuth.user),
    "Current user is not an org owner; disposable invite setup is unsafe.",
  );
  assert(
    ownerAuth.organizationId,
    "Authenticated context did not resolve org id.",
  );
  assert(
    ownerAuth.workspaceId,
    "Authenticated context did not resolve workspace id.",
  );

  const marker = ownerAuth.runId.replace(/[^a-z0-9-]/gi, "").slice(0, 20);
  const email =
    `ui.journey.onboarding.logout.${marker}@futureagi.local`.toLowerCase();
  const password = `ApiJourney${marker.slice(0, 8)}123!`;
  const onboardingRole = "Data Scientist / ML Engineer";
  const onboardingGoals = ["Run Evaluations", "Optimize AI Agents"];
  let disposableCleaned = false;
  let logoutSucceeded = false;
  const pageErrors = [];
  const evidence = {
    email,
    role: onboardingRole,
    goals: onboardingGoals,
  };

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });

  const page = await browser.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await deleteDisposableRbacUserArtifacts(email).catch(() => null);
    const accepted = await createAcceptedDisposableUser({
      auth: ownerAuth,
      email,
      password,
    });
    evidence.user_id = accepted.user_id;

    const disposableClient = createApiClient({
      apiBase: ownerAuth.apiBase,
      accessToken: accepted.access,
      organizationId: ownerAuth.organizationId,
      workspaceId: ownerAuth.workspaceId,
    });
    const initialOnboarding = await disposableClient.get(
      apiPath("/accounts/onboarding/"),
    );
    assert(
      initialOnboarding?.completed === false &&
        !initialOnboarding?.role &&
        asArray(initialOnboarding?.goals).length === 0,
      "Disposable accepted user did not start on incomplete onboarding state.",
    );

    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, ownerAuth);
    await installBrowserState(page, {
      tokens: { access: accepted.access, refresh: accepted.refresh },
      organizationId: ownerAuth.organizationId,
      workspaceId: ownerAuth.workspaceId,
      user: { id: accepted.user_id, email },
    });

    const onboardingGet = page.waitForResponse(
      (response) =>
        response.url().includes("/accounts/onboarding/") &&
        response.request().method() === "GET" &&
        response.status() < 400,
      { timeout: 60000 },
    );
    await page.goto(`${APP_BASE}/auth/jwt/setup-org`, {
      waitUntil: "domcontentloaded",
    });
    await onboardingGet;
    await waitForVisibleText(page, "What's your role", { exact: true });
    await waitForVisibleText(
      page,
      "Select the job title you most identify with",
    );
    await assertVisibleButtonDisabled(page, "Continue");

    await clickVisibleText(page, onboardingRole, { exact: true });
    await clickEnabledButton(page, "Continue");
    await waitForVisibleText(page, "Let's get started", { exact: true });
    await waitForVisibleText(page, "Tell us about your goals", { exact: true });
    for (const goal of onboardingGoals) {
      await clickVisibleText(page, goal, { exact: true });
    }
    await page.screenshot({ path: ONBOARDING_SCREENSHOT_PATH, fullPage: true });
    evidence.onboarding_screenshot = ONBOARDING_SCREENSHOT_PATH;

    const onboardingPost = page.waitForResponse(
      (response) =>
        response.url().includes("/accounts/onboarding/") &&
        response.request().method() === "POST",
      { timeout: 60000 },
    );
    await clickEnabledButton(page, "Continue");
    const onboardingResponse = await onboardingPost;
    const onboardingBody = await onboardingResponse.json().catch(() => null);
    assert(
      onboardingResponse.status() < 400,
      `Browser onboarding submit failed with HTTP ${onboardingResponse.status()}: ${JSON.stringify(onboardingBody)}`,
    );
    const savedOnboarding = await disposableClient.get(
      apiPath("/accounts/onboarding/"),
    );
    assert(
      savedOnboarding?.role === onboardingRole &&
        savedOnboarding?.completed === true,
      "Browser onboarding submit did not persist completed role state.",
    );
    assertGroupSetsEqual(
      savedOnboarding?.goals,
      onboardingGoals,
      "Browser onboarding submit did not persist selected goals.",
    );

    let audit = await loadOnboardingLogoutDbAudit(email);
    assert(
      audit?.role === onboardingRole && audit?.onboarding_completed_at,
      "Browser onboarding DB audit did not find saved role/completion timestamp.",
    );
    assertGroupSetsEqual(
      audit.goals,
      onboardingGoals,
      "Browser onboarding DB audit did not match selected goals.",
    );
    const preLogoutInactiveAccessCount = Number(
      audit.inactive_access_token_count || 0,
    );

    await waitForPathIncludes(page, "/dashboard", { timeout: 60000 });
    await openAccountPopover(page, {
      email,
      workspaceLabel:
        ownerAuth.user?.default_workspace_display_name || "Default Workspace",
    });
    await page.screenshot({ path: LOGOUT_SCREENSHOT_PATH, fullPage: true });
    evidence.logout_screenshot = LOGOUT_SCREENSHOT_PATH;

    await clickVisibleText(page, "Log out", { exact: true });
    await waitForVisibleText(page, "Are you sure you want to logout?");
    const logoutPost = page.waitForResponse(
      (response) =>
        response.url().includes("/accounts/logout/") &&
        response.request().method() === "POST",
      { timeout: 60000 },
    );
    await clickEnabledButton(page, "Logout");
    const logoutResponse = await logoutPost;
    const logoutBody = await logoutResponse.json().catch(() => null);
    assert(
      logoutResponse.status() < 400,
      `Browser logout failed with HTTP ${logoutResponse.status()}: ${JSON.stringify(logoutBody)}`,
    );
    logoutSucceeded = true;

    await page.waitForFunction(
      () =>
        !localStorage.getItem("accessToken") &&
        !localStorage.getItem("refreshToken"),
      { timeout: 30000 },
    );

    const oldAccessStatus = await fetchStatus(ownerAuth.apiBase, {
      pathName: apiPath("/accounts/user-info/"),
      accessToken: accepted.access,
    });
    assert(
      [401, 403].includes(oldAccessStatus),
      `Browser logout left old access token usable, status=${oldAccessStatus}.`,
    );

    audit = await loadOnboardingLogoutDbAudit(email);
    assert(
      Number(audit.inactive_access_token_count || 0) >
        preLogoutInactiveAccessCount,
      "Browser logout DB audit did not mark the access token inactive.",
    );

    const refreshed = await createApiClient({
      apiBase: ownerAuth.apiBase,
    }).post(apiPath("/accounts/token/refresh/"), { refresh: accepted.refresh });
    assert(
      typeof refreshed?.access === "string" && refreshed.access.length > 20,
      "Refresh token did not mint a new access token after browser logout.",
    );
    const refreshedClient = createApiClient({
      apiBase: ownerAuth.apiBase,
      accessToken: refreshed.access,
      organizationId: ownerAuth.organizationId,
      workspaceId: ownerAuth.workspaceId,
    });
    const refreshedUser = await refreshedClient.get(
      apiPath("/accounts/user-info/"),
    );
    assert(
      currentUserId(refreshedUser) === accepted.user_id,
      "Refresh after browser logout authenticated as the wrong user.",
    );

    await deleteDisposableRbacUserArtifacts(email);
    disposableCleaned = true;
    const cleanupAudit = await loadOnboardingLogoutDbAudit(email);
    assert(
      cleanupAudit.user_count === 0,
      "Disposable onboarding/logout browser user remained after cleanup.",
    );
    evidence.old_access_status = oldAccessStatus;
    evidence.inactive_access_token_count = audit.inactive_access_token_count;
    evidence.active_refresh_token_count = audit.active_refresh_token_count;
    evidence.cleanup_user_count = cleanupAudit.user_count;

    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: ownerAuth.apiBase,
          organization_id: ownerAuth.organizationId,
          workspace_id: ownerAuth.workspaceId,
          evidence,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await page
      .screenshot({ path: ERROR_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
    console.error(
      JSON.stringify(
        {
          status: "failed",
          error: error.message,
          debug: await collectDebugState(page).catch((debugError) => ({
            error: debugError.message,
          })),
          error_screenshot: ERROR_SCREENSHOT_PATH,
          logout_succeeded: logoutSucceeded,
        },
        null,
        2,
      ),
    );
    throw error;
  } finally {
    if (!disposableCleaned) {
      await deleteDisposableRbacUserArtifacts(email).catch(() => null);
    }
    await browser.close();
  }
}

async function createAcceptedDisposableUser({ auth, email, password }) {
  const invited = await auth.client.post(
    apiPath("/accounts/organization/invite/"),
    {
      emails: [email],
      org_level: 1,
      workspace_access: [{ workspace_id: auth.workspaceId, level: 1 }],
    },
  );
  assert(
    asArray(invited?.invited).includes(email),
    "Disposable onboarding browser invite did not include the email.",
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
    "Disposable onboarding browser invite preview did not validate.",
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
    "Disposable onboarding browser invite accept did not return tokens.",
  );
  return {
    user_id: tokenInfo.user_id,
    access: accepted.access,
    refresh: accepted.refresh,
  };
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
          VITE_DISABLE_MSW: "true",
        })};`,
      });
      return;
    }
    request.continue();
  });
}

async function installBrowserState(
  page,
  { tokens, organizationId, workspaceId, user },
) {
  await page.evaluateOnNewDocument(
    ({
      tokens: tokenPayload,
      organizationId: orgId,
      workspaceId: wsId,
      user: userPayload,
    }) => {
      localStorage.setItem("accessToken", tokenPayload.access);
      localStorage.setItem("refreshToken", tokenPayload.refresh || "");
      localStorage.setItem("rememberMe", "true");
      localStorage.removeItem("initial-render");
      if (orgId) sessionStorage.setItem("organizationId", orgId);
      if (wsId) sessionStorage.setItem("workspaceId", wsId);
      if (userPayload?.id) {
        sessionStorage.setItem("futureagi-current-user-id", userPayload.id);
        sessionStorage.setItem("currentUserId", userPayload.id);
      }
    },
    { tokens, organizationId, workspaceId, user },
  );
}

async function openAccountPopover(page, { email, workspaceLabel }) {
  await waitForVisibleText(page, workspaceLabel, { timeout: 60000 });
  await page.evaluate((label) => {
    const normalized = (value) =>
      String(value || "")
        .replace(/\s+/g, " ")
        .trim();
    const candidates = Array.from(document.querySelectorAll("body *")).filter(
      (element) =>
        visible(element) && normalized(element.textContent).includes(label),
    );
    const accountButton = candidates.find((element) => {
      let current = element;
      while (current && current !== document.body) {
        if (window.getComputedStyle(current).cursor === "pointer") {
          return true;
        }
        current = current.parentElement;
      }
      return false;
    });
    if (!accountButton) {
      throw new Error(`No visible workspace switcher found for ${label}.`);
    }
    accountButton.click();

    function visible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    }
  }, workspaceLabel);
  await waitForVisibleText(page, email, { exact: true });
  await waitForVisibleText(page, "Log out", { exact: true });
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ expectedText, exactMatch }) => {
      const normalized = (value) =>
        String(value || "")
          .replace(/\s+/g, " ")
          .trim();
      const expected = normalized(expectedText);
      return Array.from(document.querySelectorAll("body *")).some((element) => {
        if (!visible(element)) return false;
        const candidate = normalized(element.textContent);
        return exactMatch
          ? candidate === expected
          : candidate.includes(expected);
      });

      function visible(element) {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      }
    },
    { timeout },
    { expectedText: text, exactMatch: exact },
  );
}

async function clickVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await waitForVisibleText(page, text, { exact, timeout });
  const clicked = await page.evaluate(
    ({ expectedText, exactMatch }) => {
      const normalized = (value) =>
        String(value || "")
          .replace(/\s+/g, " ")
          .trim();
      const expected = normalized(expectedText);
      const candidates = Array.from(document.querySelectorAll("body *")).filter(
        (element) => {
          if (!visible(element)) return false;
          const candidate = normalized(element.textContent);
          return exactMatch
            ? candidate === expected
            : candidate.includes(expected);
        },
      );
      const target = candidates.find((element) => {
        const text = normalized(element.textContent);
        return text === expected || element.tagName === "LABEL";
      });
      const clickable =
        target?.closest(
          "button, label, [role='button'], .MuiFormControlLabel-root",
        ) || target;
      if (!clickable) return false;
      clickable.click();
      return true;

      function visible(element) {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      }
    },
    { expectedText: text, exactMatch: exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

async function clickEnabledButton(page, text, { timeout = 30000 } = {}) {
  await waitForVisibleText(page, text, { exact: true, timeout });
  const clicked = await page.evaluate((expectedText) => {
    const expected = normalized(expectedText);
    const button = Array.from(document.querySelectorAll("button")).find(
      (candidate) =>
        visible(candidate) &&
        !candidate.disabled &&
        normalized(candidate.textContent) === expected,
    );
    if (!button) return false;
    button.click();
    return true;

    function normalized(value) {
      return String(value || "")
        .replace(/\s+/g, " ")
        .trim();
    }
    function visible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    }
  }, text);
  assert(clicked, `Could not click enabled button: ${text}`);
}

async function assertVisibleButtonDisabled(page, text) {
  const disabled = await page.evaluate((expectedText) => {
    const expected = normalized(expectedText);
    const button = Array.from(document.querySelectorAll("button")).find(
      (candidate) =>
        visible(candidate) && normalized(candidate.textContent) === expected,
    );
    if (!button) return null;
    return button.disabled || button.getAttribute("aria-disabled") === "true";

    function normalized(value) {
      return String(value || "")
        .replace(/\s+/g, " ")
        .trim();
    }
    function visible(element) {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        rect.width > 0 &&
        rect.height > 0
      );
    }
  }, text);
  assert(disabled === true, `Expected visible button to be disabled: ${text}`);
}

async function waitForPathIncludes(
  page,
  expectedPath,
  { timeout = 30000 } = {},
) {
  await page.waitForFunction(
    (path) => window.location.pathname.includes(path),
    { timeout },
    expectedPath,
  );
}

async function fetchStatus(apiBase, { pathName, accessToken }) {
  const response = await fetch(new URL(pathName, apiBase), {
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
  });
  await response.text().catch(() => "");
  return response.status;
}

function assertGroupSetsEqual(actual, expected, message) {
  const actualValues = asArray(actual)
    .map((value) => String(value))
    .sort();
  const expectedValues = asArray(expected)
    .map((value) => String(value))
    .sort();
  assert(
    JSON.stringify(actualValues) === JSON.stringify(expectedValues),
    `${message} actual=${JSON.stringify(actualValues)} expected=${JSON.stringify(expectedValues)}`,
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

async function loadOnboardingLogoutDbAudit(email) {
  const sql = `
WITH requested AS (
  SELECT lower(${sqlTextLiteral(email)}) AS email
),
user_rows AS (
  SELECT u.id, u.email, u.is_active, u.role, u.goals, u.config
  FROM accounts_user u
  JOIN requested r ON lower(u.email) = r.email
),
auth_tokens AS (
  SELECT token.id, token.auth_type, token.is_active
  FROM accounts_auth_token token
  JOIN user_rows u ON token.user_id = u.id
)
SELECT json_build_object(
  'email', (SELECT email FROM requested),
  'user_count', (SELECT count(*) FROM user_rows),
  'user_id', (SELECT id::text FROM user_rows LIMIT 1),
  'user_active', (SELECT is_active FROM user_rows LIMIT 1),
  'role', (SELECT role FROM user_rows LIMIT 1),
  'goals', COALESCE((SELECT goals FROM user_rows LIMIT 1), '[]'::jsonb),
  'onboarding_completed_at', (SELECT config->>'onboarding_completed_at' FROM user_rows LIMIT 1),
  'active_access_token_count', (
    SELECT count(*) FROM auth_tokens WHERE auth_type = 'access' AND is_active = true
  ),
  'inactive_access_token_count', (
    SELECT count(*) FROM auth_tokens WHERE auth_type = 'access' AND is_active = false
  ),
  'active_refresh_token_count', (
    SELECT count(*) FROM auth_tokens WHERE auth_type = 'refresh' AND is_active = true
  ),
  'inactive_refresh_token_count', (
    SELECT count(*) FROM auth_tokens WHERE auth_type = 'refresh' AND is_active = false
  )
);
`;
  return runPostgresJson(sql);
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
  assert(text, "Postgres DB command returned no JSON output.");
  return JSON.parse(text);
}

function isOrgOwner(user) {
  const role = String(user?.organization_role || user?.role || "");
  const level = Number(user?.org_level || user?.organization_level || 0);
  return role === "Owner" || level >= 15;
}

function sqlTextLiteral(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`;
}

function shellQuote(value) {
  return `'${String(value).replaceAll("'", "'\"'\"'")}'`;
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
      localStorageKeys: {
        hasAccessToken: Boolean(localStorage.getItem("accessToken")),
        hasRefreshToken: Boolean(localStorage.getItem("refreshToken")),
        initialRender: localStorage.getItem("initial-render"),
      },
      buttons: Array.from(document.querySelectorAll("button"))
        .filter(visible)
        .map((button) => ({
          text: String(button.textContent || "").trim(),
          disabled: button.disabled,
        })),
    };
  });
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
