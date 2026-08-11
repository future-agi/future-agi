import { execFile as execFileCallback } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
  isUuid,
  requireMutations,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFile = promisify(execFileCallback);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/settings-api-keys-smoke.png";
const ACTION_MENU_SCREENSHOT_PATH =
  "/tmp/settings-api-keys-smoke-action-menu.png";
const DISABLED_SCREENSHOT_PATH = "/tmp/settings-api-keys-smoke-disabled.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/settings-api-keys-smoke-failure.png";

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const keyName = `browser key ${auth.runId}`;
  const apiFailures = [];
  const pageErrors = [];
  let createdKey = null;
  let uiDeleted = false;
  let hardCleanup = null;
  let caughtError = null;
  let cleanupError = null;

  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });

  const page = await browser.newPage();
  await preparePage(page, auth);

  page.on("response", (response) => {
    const url = response.url();
    if (url.includes("/accounts/key/") && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${url}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await waitForResponseDuring(
      page,
      "API keys initial list",
      (response) =>
        response.url().includes("/accounts/key/get_secret_keys/") &&
        response.status() < 400,
      () =>
        page.goto(`${APP_BASE}/dashboard/settings/api_keys`, {
          waitUntil: "domcontentloaded",
        }),
    );

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
    assert(
      createdKey?.key_id,
      "Create API key response did not include key_id.",
    );
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

    await filterKeys(page, keyName);
    await waitForVisibleText(page, keyName);

    await clickRowActionMenu(page, keyName);
    await waitForVisibleText(page, "Disable Key", { exact: true });
    await page.screenshot({
      path: ACTION_MENU_SCREENSHOT_PATH,
      fullPage: true,
    });
    await clickVisibleMenuItem(page, "Disable Key");
    await waitForVisibleText(page, "Disable Key?", { exact: true });
    await waitForResponseDuring(
      page,
      "API key disable",
      (response) =>
        response.url().includes("/accounts/key/disable_key/") &&
        response.request().method() === "POST" &&
        response.status() < 400,
      () => clickDialogButton(page, "Disable Key"),
    );
    await waitForKeyRowState(page, keyName, { disabled: true });
    await page.screenshot({ path: DISABLED_SCREENSHOT_PATH, fullPage: true });
    await assertListedKeyState(auth.client, {
      keyId: createdKey.key_id,
      keyName,
      enabled: false,
      rawApiKey: createdKey.api_key,
      rawSecretKey: createdKey.secret_key,
    });
    await assertRawKeysNotRendered(page, createdKey);

    await clickRowActionMenu(page, keyName);
    await waitForVisibleText(page, "Re-enable key", { exact: true });
    await clickVisibleMenuItem(page, "Re-enable key");
    await waitForVisibleText(page, "Re-Enable API Key", { exact: true });
    await waitForResponseDuring(
      page,
      "API key re-enable",
      (response) =>
        response.url().includes("/accounts/key/enable_key/") &&
        response.request().method() === "POST" &&
        response.status() < 400,
      () => clickDialogButton(page, "Re-enable"),
    );
    await waitForKeyRowState(page, keyName, { disabled: false });
    await assertListedKeyState(auth.client, {
      keyId: createdKey.key_id,
      keyName,
      enabled: true,
      rawApiKey: createdKey.api_key,
      rawSecretKey: createdKey.secret_key,
    });
    await assertRawKeysNotRendered(page, createdKey);

    await clickRowActionMenu(page, keyName);
    await waitForVisibleText(page, "Delete Key", { exact: true });
    await clickVisibleMenuItem(page, "Delete Key");
    await waitForVisibleText(page, "Delete Key?", { exact: true });
    await waitForResponseDuring(
      page,
      "API key delete",
      (response) =>
        response.url().includes("/accounts/key/delete_secret_key/") &&
        response.request().method() === "DELETE" &&
        response.status() < 400,
      () => clickDialogButton(page, "Delete Key"),
    );
    uiDeleted = true;
    await waitForNoKeyRow(page, keyName);
    await assertKeyAbsentFromList(auth.client, {
      keyId: createdKey.key_id,
      keyName,
    });
    hardCleanup = await hardDeleteDeveloperSecretKeyDb(
      createdKey.key_id,
      auth.organizationId,
    );
    assert(
      Number(hardCleanup.remaining_key_count) === 0,
      `Developer key cleanup left DB rows behind: ${JSON.stringify(hardCleanup)}`,
    );
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
  } catch (error) {
    caughtError = error;
    await page
      .screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
  } finally {
    await browser.close();
    if (createdKey?.key_id && !hardCleanup) {
      try {
        if (!uiDeleted) {
          await auth.client.delete(
            apiPath("/accounts/key/delete_secret_key/"),
            {
              body: { key_id: createdKey.key_id },
              okStatuses: [200, 204, 404],
            },
          );
        }
        hardCleanup = await hardDeleteDeveloperSecretKeyDb(
          createdKey.key_id,
          auth.organizationId,
        );
      } catch (error) {
        cleanupError = error;
      }
    }
  }

  if (caughtError || cleanupError) {
    if (caughtError && cleanupError) {
      caughtError.message = `${caughtError.message}; cleanup failed: ${cleanupError.message}`;
      throw caughtError;
    }
    throw caughtError || cleanupError;
  }

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
          ui_disable_chip_visible: true,
          ui_reenable_removed_disabled_chip: true,
          ui_delete_removed_row: true,
          hard_cleanup_remaining_key_count: Number(
            hardCleanup?.remaining_key_count,
          ),
          action_menu_screenshot: ACTION_MENU_SCREENSHOT_PATH,
          disabled_screenshot: DISABLED_SCREENSHOT_PATH,
          screenshot: SCREENSHOT_PATH,
        },
      },
      null,
      2,
    ),
  );
}

async function preparePage(page, auth) {
  await page.setBypassServiceWorker(true);
  await installRuntimeConfig(page, auth);
  await page.evaluateOnNewDocument(() => {
    window.__apiJourneyNormalizeText = (value) => String(value || "").trim();
    window.__apiJourneyElementText = (element) => {
      const values = [element.textContent];
      if ("value" in element) values.push(element.value);
      values.push(element.getAttribute?.("aria-label"));
      return values
        .map((value) => window.__apiJourneyNormalizeText(value))
        .filter(Boolean)
        .join(" ");
    };
    window.__apiJourneyVisibleElements = () => {
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
      return Array.from(document.querySelectorAll("body *")).filter(isVisible);
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

async function waitForResponseDuring(page, label, predicate, action) {
  try {
    const [response] = await Promise.all([
      page.waitForResponse(predicate, { timeout: 60000 }),
      action(),
    ]);
    return response;
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
}

async function clickVisibleText(page, text, { exact = false } = {}) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      return Array.from(
        document.querySelectorAll("button,a,[role='button']"),
      ).some((candidate) => {
        if (!window.__apiJourneyVisibleElements().includes(candidate))
          return false;
        const textContent = window.__apiJourneyNormalizeText(
          candidate.textContent,
        );
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
    },
    { timeout: 30000 },
    { text, exact },
  );
  await page.evaluate(
    ({ text: expectedText, exact: exactMatch }) => {
      const element = Array.from(
        document.querySelectorAll("button,a,[role='button']"),
      ).find((candidate) => {
        if (!window.__apiJourneyVisibleElements().includes(candidate))
          return false;
        const textContent = window.__apiJourneyNormalizeText(
          candidate.textContent,
        );
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
      element?.click();
    },
    { text, exact },
  );
}

async function clickVisibleMenuItem(page, text) {
  await page.waitForFunction(
    (expectedText) =>
      window.__apiJourneyVisibleElements().some((element) => {
        const menuItem = element.closest('[role="menuitem"]');
        return (
          menuItem &&
          window.__apiJourneyNormalizeText(menuItem.textContent) ===
            expectedText
        );
      }),
    { timeout: 30000 },
    text,
  );
  const clicked = await page.evaluate((expectedText) => {
    const element = window.__apiJourneyVisibleElements().find((candidate) => {
      const menuItem = candidate.closest('[role="menuitem"]');
      return (
        menuItem &&
        window.__apiJourneyNormalizeText(menuItem.textContent) === expectedText
      );
    });
    const menuItem = element?.closest('[role="menuitem"]');
    if (!menuItem) return false;
    menuItem.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    menuItem.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    menuItem.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return true;
  }, text);
  assert(clicked, `Could not click menu item ${text}.`);
}

async function clickDialogButton(page, text) {
  await page.waitForFunction(
    (expectedText) => {
      const dialog = document.querySelector('[role="dialog"]');
      if (!dialog) return false;
      return window.__apiJourneyVisibleElements().some((element) => {
        if (!dialog.contains(element)) return false;
        const button = element.closest("button,[role='button']");
        return (
          button &&
          dialog.contains(button) &&
          window.__apiJourneyNormalizeText(button.textContent) === expectedText
        );
      });
    },
    { timeout: 30000 },
    text,
  );
  const clicked = await page.evaluate((expectedText) => {
    const dialog = document.querySelector('[role="dialog"]');
    const element = window.__apiJourneyVisibleElements().find((candidate) => {
      if (!dialog?.contains(candidate)) return false;
      const button = candidate.closest("button,[role='button']");
      return (
        button &&
        dialog.contains(button) &&
        window.__apiJourneyNormalizeText(button.textContent) === expectedText
      );
    });
    const button = element?.closest("button,[role='button']");
    if (!button) return false;
    button.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return true;
  }, text);
  assert(clicked, `Could not click dialog button ${text}.`);
}

async function typeIntoInput(page, placeholder, value) {
  const selector = `input[placeholder="${placeholder}"]`;
  await page.waitForSelector(selector, { timeout: 30000 });
  await page.click(selector);
  await page.keyboard.type(value);
}

async function filterKeys(page, search) {
  await waitForResponseDuring(
    page,
    "API key search",
    (response) => {
      if (
        !response.url().includes("/accounts/key/get_secret_keys/") ||
        response.status() >= 400
      ) {
        return false;
      }
      const url = new URL(response.url());
      return url.searchParams.get("search") === search;
    },
    async () => {
      const selector = 'input[placeholder="Search"]';
      await page.waitForSelector(selector, { timeout: 30000 });
      await page.click(selector, { clickCount: 3 });
      await page.keyboard.press("Backspace");
      await page.type(selector, search, { delay: 1 });
    },
  );
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) => {
      return window.__apiJourneyVisibleElements().some((element) => {
        const textContent = window.__apiJourneyElementText(element);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
    },
    { timeout },
    { text, exact },
  );
}

async function clickRowActionMenu(page, keyName) {
  await page.waitForFunction(
    (expectedName) =>
      Array.from(document.querySelectorAll('[role="row"]')).some((row) => {
        const text = window.__apiJourneyNormalizeText(row.textContent);
        return (
          text.includes(expectedName) &&
          row.querySelector("button,[role='button']")
        );
      }),
    { timeout: 30000 },
    keyName,
  );
  const clicked = await page.evaluate((expectedName) => {
    const row = Array.from(document.querySelectorAll('[role="row"]')).find(
      (candidate) => {
        const text = window.__apiJourneyNormalizeText(candidate.textContent);
        return (
          text.includes(expectedName) &&
          candidate.querySelector("button,[role='button']")
        );
      },
    );
    const actionCell = row?.querySelector('[data-field="actions"]');
    const target =
      actionCell?.querySelector("button,[role='button']") ||
      Array.from(row?.querySelectorAll("button,[role='button']") || []).at(-1);
    if (!target) return false;
    target.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    target.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    target.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return true;
  }, keyName);
  assert(clicked, `Could not open action menu for API key ${keyName}.`);
}

async function waitForKeyRowState(page, keyName, { disabled }) {
  await page.waitForFunction(
    ({ keyName: expectedName, disabled: expectedDisabled }) => {
      const row = Array.from(document.querySelectorAll('[role="row"]')).find(
        (candidate) =>
          window
            .__apiJourneyNormalizeText(candidate.textContent)
            .includes(expectedName),
      );
      if (!row) return false;
      const text = window.__apiJourneyNormalizeText(row.textContent);
      return expectedDisabled
        ? text.includes("Disabled")
        : !text.includes("Disabled");
    },
    { timeout: 60000 },
    { keyName, disabled },
  );
}

async function waitForNoKeyRow(page, keyName) {
  await page.waitForFunction(
    (expectedName) =>
      !Array.from(document.querySelectorAll('[role="row"]')).some((row) =>
        window
          .__apiJourneyNormalizeText(row.textContent)
          .includes(expectedName),
      ),
    { timeout: 60000 },
    keyName,
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
  assert(
    !rendered.includes(key.secret_key),
    "Page rendered the raw secret key.",
  );
}

async function assertListedKeyState(
  client,
  { keyId, keyName, enabled, rawApiKey, rawSecretKey },
) {
  const list = await client.get(apiPath("/accounts/key/get_secret_keys/"), {
    query: { search: keyName, page_size: 10 },
  });
  const row = list?.table?.find((item) => item.id === keyId);
  assert(row, `Could not find listed API key ${keyId}.`);
  assert(row.key_name === keyName, "Listed API key name mismatch.");
  assert(row.enabled === enabled, "Listed API key enabled state mismatch.");
  assert(row.api_key !== rawApiKey, "List response exposed raw api_key.");
  assert(
    row.secret_key !== rawSecretKey,
    "List response exposed raw secret_key.",
  );
  assertMaskedKey(row.api_key, "listed api_key");
  assertMaskedKey(row.secret_key, "listed secret_key");
}

async function assertKeyAbsentFromList(client, { keyId, keyName }) {
  const list = await client.get(apiPath("/accounts/key/get_secret_keys/"), {
    query: { search: keyName, page_size: 10 },
  });
  assert(
    !list?.table?.some((item) => item.id === keyId),
    "Deleted API key was still visible through list/search.",
  );
}

async function hardDeleteDeveloperSecretKeyDb(keyId, organizationId) {
  const sql = `
WITH target_keys AS (
  SELECT id
  FROM accounts_orgapikey
  WHERE id = ${sqlUuid(keyId)}
    AND organization_id = ${sqlUuid(organizationId)}
),
deleted_keys AS (
  DELETE FROM accounts_orgapikey key
  USING target_keys target
  WHERE key.id = target.id
  RETURNING key.id
)
SELECT json_build_object(
  'deleted_key_count', (SELECT count(*) FROM deleted_keys),
  'remaining_key_count',
    (SELECT count(*) FROM target_keys) - (SELECT count(*) FROM deleted_keys)
);
`;
  return runPostgresJson(sql);
}

async function runPostgresJson(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
  const user = process.env.API_JOURNEY_DB_USER || "user";
  const database = process.env.API_JOURNEY_DB_NAME || "tfc";
  const { stdout } = await execFile(
    "docker",
    ["exec", container, "psql", "-U", user, "-d", database, "-At", "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const text = stdout.trim();
  assert(text, "Postgres DB cleanup returned no JSON output.");
  return JSON.parse(text);
}

function sqlUuid(value) {
  assert(isUuid(value), `Expected UUID, got ${value}`);
  return `'${String(value).replaceAll("'", "''")}'::uuid`;
}

function assertRawKey(value, label) {
  assert(
    /^[0-9a-f]{32}$/i.test(String(value || "")),
    `${label} was not raw key material.`,
  );
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
