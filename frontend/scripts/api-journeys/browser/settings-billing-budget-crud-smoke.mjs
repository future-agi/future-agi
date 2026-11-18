import { execFile } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_CREATE_PATH = "/tmp/settings-billing-budget-create-smoke.png";
const SCREENSHOT_EDIT_PATH = "/tmp/settings-billing-budget-edit-smoke.png";
const SCREENSHOT_DELETE_PATH = "/tmp/settings-billing-budget-delete-smoke.png";

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const marker = auth.runId.replace(/[^a-z0-9]/gi, "").slice(0, 16);
  const budgetName = `ui_budget_${marker}`;
  const updatedBudgetName = `ui_budget_${marker}_edited`;
  const budgetNames = [budgetName, updatedBudgetName];
  let createdBudgetId = null;
  let cleanupAudit = null;

  await hardDeleteBudgetFixtures({
    organizationId: auth.organizationId,
    budgetNames,
  });

  try {
    const initialResidue = await loadBudgetDbAudit({
      organizationId: auth.organizationId,
      budgetNames,
    });
    assert(
      initialResidue.total_count === 0,
      "Pre-run billing budget fixture cleanup left rows behind.",
    );

    const apiFailures = [];
    const pageErrors = [];
    const mutationRequests = [];
    const evidence = {
      organization_id: auth.organizationId,
      workspace_id: auth.workspaceId,
      budget_name: budgetName,
      updated_budget_name: updatedBudgetName,
      initial_residue: initialResidue,
    };

    const browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });

    const page = await browser.newPage();
    await installRuntimeConfig(page, auth, mutationRequests);
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
      const pathname = new URL(response.url()).pathname;
      if (isBudgetApiPath(pathname) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${pathname}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    try {
      await openBillingPage(page);
      await waitForVisibleText(page, "Billing", { exact: true });
      await waitForVisibleText(page, "Usage Budgets", { exact: true });
      await waitForVisibleText(page, "Add Budget", { exact: true });

      await clickVisibleText(page, "Add Budget", { exact: true });
      await waitForVisibleText(page, "Add Usage Budget", { exact: true });
      await fillInputByLabel(page, "Budget Name", budgetName);
      await fillInputByLabel(page, "Threshold", "1e5");
      await waitForVisibleText(page, "Enter a positive number", {
        exact: true,
      });
      assert(
        await buttonIsDisabled(page, "Create Budget"),
        "Invalid budget threshold did not disable Create Budget.",
      );
      await fillInputByLabel(page, "Threshold", "125.50");

      const createResponse = waitForBudgetMutationResponse(page, "POST");
      await clickVisibleText(page, "Create Budget", { exact: true });
      const createdBudget = responseResult(await createResponse);
      createdBudgetId = Number(createdBudget?.id);
      assert(
        Number.isInteger(createdBudgetId),
        "Budget create did not return an integer budget id.",
      );
      assert(
        createdBudget.name === budgetName &&
          createdBudget.scope === "ai_credits" &&
          createdBudget.action === "notify",
        "Budget create response did not match the submitted budget.",
      );
      await waitForVisibleText(page, budgetName, { exact: true });
      await waitForVisibleText(page, "AI Credits:");
      await waitForVisibleText(page, "Notify", { exact: true });
      await page.screenshot({ path: SCREENSHOT_CREATE_PATH, fullPage: true });

      const createdReadback = await findBudgetByName(auth, budgetName);
      assert(
        createdReadback?.id === createdBudgetId &&
          createdReadback.scope === "ai_credits" &&
          Number(createdReadback.threshold_value) === 125.5 &&
          createdReadback.action === "notify" &&
          createdReadback.is_active === true,
        "Budget create API readback did not match expected state.",
      );
      const createAudit = await loadBudgetDbAudit({
        organizationId: auth.organizationId,
        budgetNames,
      });
      assert(
        createAudit.active_count === 1 &&
          createAudit.active_id === createdBudgetId &&
          createAudit.active_name === budgetName &&
          createAudit.active_scope === "ai_credits" &&
          Number(createAudit.active_threshold_value) === 125.5 &&
          createAudit.active_action === "notify",
        `Budget create DB audit did not match expected state: ${JSON.stringify(
          createAudit,
        )}`,
      );
      evidence.created_budget = {
        id: createdBudgetId,
        api: createdReadback,
        db_audit: createAudit,
        screenshot: SCREENSHOT_CREATE_PATH,
      };

      await clickBudgetAction(page, budgetName, "Edit budget");
      await waitForVisibleText(page, "Edit Budget", { exact: true });
      await fillInputByLabel(page, "Budget Name", updatedBudgetName);
      await fillInputByLabel(page, "Threshold", "200.75");
      await selectMuiOption(page, "Action", "Warn");

      const updateResponse = waitForBudgetMutationResponse(page, "PUT", {
        budgetId: createdBudgetId,
      });
      await clickVisibleText(page, "Save Changes", { exact: true });
      const updatedBudget = responseResult(await updateResponse);
      assert(
        updatedBudget?.id === createdBudgetId &&
          updatedBudget.name === updatedBudgetName &&
          Number(updatedBudget.threshold_value) === 200.75 &&
          updatedBudget.action === "warn",
        "Budget update response did not match expected state.",
      );
      await waitForVisibleText(page, updatedBudgetName, { exact: true });
      await waitForVisibleText(page, "Warn", { exact: true });
      await page.screenshot({ path: SCREENSHOT_EDIT_PATH, fullPage: true });

      const updatedReadback = await findBudgetByName(auth, updatedBudgetName);
      assert(
        updatedReadback?.id === createdBudgetId &&
          Number(updatedReadback.threshold_value) === 200.75 &&
          updatedReadback.action === "warn",
        "Budget update API readback did not match expected state.",
      );
      const updateAudit = await loadBudgetDbAudit({
        organizationId: auth.organizationId,
        budgetNames,
      });
      assert(
        updateAudit.active_count === 1 &&
          updateAudit.active_id === createdBudgetId &&
          updateAudit.active_name === updatedBudgetName &&
          Number(updateAudit.active_threshold_value) === 200.75 &&
          updateAudit.active_action === "warn",
        `Budget update DB audit did not match expected state: ${JSON.stringify(
          updateAudit,
        )}`,
      );
      evidence.updated_budget = {
        api: updatedReadback,
        db_audit: updateAudit,
        screenshot: SCREENSHOT_EDIT_PATH,
      };

      await clickBudgetAction(page, updatedBudgetName, "Delete budget");
      await waitForVisibleText(page, "Delete Budget?", { exact: true });
      await waitForVisibleText(page, updatedBudgetName, { exact: false });
      const deleteResponse = waitForBudgetMutationResponse(page, "DELETE", {
        budgetId: createdBudgetId,
      });
      await clickVisibleText(page, "Delete", { exact: true });
      const deletedBudget = responseResult(await deleteResponse);
      assert(
        deletedBudget?.deleted === true,
        "Budget delete response did not confirm deletion.",
      );
      await waitForNoVisibleText(page, updatedBudgetName, { exact: true });
      await page.screenshot({ path: SCREENSHOT_DELETE_PATH, fullPage: true });

      const deletedReadback = await findBudgetByName(auth, updatedBudgetName);
      assert(!deletedReadback, "Deleted budget remained visible in API list.");
      const deleteAudit = await loadBudgetDbAudit({
        organizationId: auth.organizationId,
        budgetNames,
      });
      assert(
        deleteAudit.total_count === 1 &&
          deleteAudit.active_count === 0 &&
          deleteAudit.deleted_count === 1 &&
          deleteAudit.deleted_id === createdBudgetId,
        `Budget delete DB audit did not find the expected soft-delete row: ${JSON.stringify(
          deleteAudit,
        )}`,
      );
      evidence.deleted_budget = {
        api_visible_after_delete: !!deletedReadback,
        db_audit: deleteAudit,
        screenshot: SCREENSHOT_DELETE_PATH,
      };

      assert(
        apiFailures.length === 0,
        `Budget API failures: ${apiFailures.join("; ")}`,
      );
      assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
      assertExpectedBudgetMutations(mutationRequests, createdBudgetId);
      evidence.mutation_requests = mutationRequests;
    } finally {
      await browser.close();
    }

    cleanupAudit = await hardDeleteBudgetFixtures({
      organizationId: auth.organizationId,
      budgetNames,
    });
    const finalResidue = await loadBudgetDbAudit({
      organizationId: auth.organizationId,
      budgetNames,
    });
    assert(
      finalResidue.total_count === 0,
      "Billing budget hard cleanup left disposable rows behind.",
    );
    evidence.cleanup = cleanupAudit;
    evidence.final_residue = finalResidue;

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
    try {
      cleanupAudit = await hardDeleteBudgetFixtures({
        organizationId: auth.organizationId,
        budgetNames,
      });
      console.error(JSON.stringify({ cleanup_after_error: cleanupAudit }));
    } catch (cleanupError) {
      console.error(
        `Budget cleanup failed after error: ${cleanupError?.stack || cleanupError}`,
      );
    }
    throw error;
  }
}

function requireMutations() {
  if (process.env.API_JOURNEY_MUTATIONS !== "1") {
    throw new Error(
      "Set API_JOURNEY_MUTATIONS=1 to run the billing budget CRUD smoke.",
    );
  }
}

async function installRuntimeConfig(page, auth, mutationRequests) {
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (isTrackedMutation(request.method(), url.pathname)) {
      mutationRequests.push(`${request.method()} ${url.pathname}`);
    }
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

async function openBillingPage(page) {
  const budgetResponse = waitForReadResponse(page, "/usage/v2/budgets/");
  await page.goto(`${APP_BASE}/dashboard/settings/billing`, {
    waitUntil: "domcontentloaded",
  });
  await budgetResponse;
}

function waitForReadResponse(page, pathname) {
  return page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === pathname && response.status() < 400,
    { timeout: 60000 },
  );
}

async function waitForBudgetMutationResponse(page, method, { budgetId } = {}) {
  const response = await page.waitForResponse(
    (candidate) => {
      if (
        candidate.request().method() !== method ||
        candidate.status() >= 400
      ) {
        return false;
      }
      const pathname = new URL(candidate.url()).pathname;
      if (method === "POST") return pathname === "/usage/v2/budgets/";
      return pathname === `/usage/v2/budgets/${budgetId}/`;
    },
    { timeout: 60000 },
  );
  return response.json();
}

function responseResult(payload) {
  return payload?.result || payload;
}

async function findBudgetByName(auth, name) {
  const payload = await auth.client.get(apiPath("/usage/v2/budgets/"));
  const rows = Array.isArray(payload?.budgets) ? payload.budgets : [];
  return rows.find((row) => row.name === name) || null;
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

async function clickVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await waitForVisibleText(page, text, { exact, timeout });
  const clicked = await page.evaluate(
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
      const candidates = Array.from(
        document.querySelectorAll("button, [role='button'], li, a, span, p"),
      );
      const target = candidates.find((element) => {
        if (!isVisible(element)) return false;
        const textContent = normalized(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      });
      if (!target) return false;
      target.click();
      return true;
    },
    { text, exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

async function fillInputByLabel(page, labelText, value) {
  const filled = await page.evaluate(
    ({ labelText: targetLabel, value: nextValue }) => {
      const labels = Array.from(document.querySelectorAll("label"));
      const label = labels.find(
        (item) => item.textContent.trim() === targetLabel,
      );
      if (!label) return false;
      const input = label.htmlFor
        ? document.getElementById(label.htmlFor)
        : label.closest(".MuiFormControl-root")?.querySelector("input");
      if (!input) return false;
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      setter.call(input, "");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      setter.call(input, nextValue);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      input.dispatchEvent(new Event("blur", { bubbles: true }));
      return true;
    },
    { labelText, value },
  );
  assert(filled, `Could not fill input with label: ${labelText}`);
}

async function buttonIsDisabled(page, text) {
  return page.evaluate((expectedText) => {
    const button = Array.from(document.querySelectorAll("button")).find(
      (item) => item.textContent.trim() === expectedText,
    );
    return !!button?.disabled;
  }, text);
}

async function selectMuiOption(page, labelText, optionText) {
  const opened = await page.evaluate((targetLabel) => {
    const labels = Array.from(document.querySelectorAll("label"));
    const label = labels.find(
      (item) => item.textContent.trim() === targetLabel,
    );
    const formControl = label?.closest(".MuiFormControl-root");
    const combo = formControl?.querySelector("[role='combobox']");
    if (!combo) return false;
    combo.dispatchEvent(
      new MouseEvent("mousedown", {
        bubbles: true,
        cancelable: true,
        view: window,
      }),
    );
    combo.click();
    return true;
  }, labelText);
  assert(opened, `Could not open select with label: ${labelText}`);
  await waitForVisibleText(page, optionText);
  const selected = await page.evaluate((targetOption) => {
    const option = Array.from(
      document.querySelectorAll("[role='option'], li"),
    ).find((item) => item.textContent.includes(targetOption));
    if (!option) return false;
    option.click();
    return true;
  }, optionText);
  assert(selected, `Could not select option: ${optionText}`);
}

async function clickBudgetAction(page, budgetName, title) {
  const clicked = await page.evaluate(
    ({ budgetName: targetBudgetName, title: targetTitle }) => {
      const exactTextNodes = Array.from(
        document.querySelectorAll("body *"),
      ).filter((element) => element.textContent.trim() === targetBudgetName);
      for (const element of exactTextNodes) {
        let current = element;
        while (current && current !== document.body) {
          const button = current.querySelector(
            `button[title="${targetTitle}"]`,
          );
          if (button) {
            button.click();
            return true;
          }
          current = current.parentElement;
        }
      }
      return false;
    },
    { budgetName, title },
  );
  assert(clicked, `Could not click ${title} for budget ${budgetName}`);
}

function isBudgetApiPath(pathname) {
  return (
    pathname === "/usage/v2/budgets/" ||
    /^\/usage\/v2\/budgets\/\d+\/$/.test(pathname)
  );
}

function isTrackedMutation(method, pathname) {
  if (method === "GET" || method === "HEAD" || method === "OPTIONS") {
    return false;
  }
  return (
    isBudgetApiPath(pathname) ||
    pathname.includes("/payment-methods") ||
    pathname.includes("/upgrade-to-payg") ||
    pathname.includes("/downgrade-to-free") ||
    pathname.includes("/add-addon") ||
    pathname.includes("/remove-addon") ||
    pathname.includes("/reinstate-addon") ||
    pathname.includes("/usage/ee/licenses")
  );
}

function assertExpectedBudgetMutations(mutationRequests, budgetId) {
  const expected = [
    "POST /usage/v2/budgets/",
    `PUT /usage/v2/budgets/${budgetId}/`,
    `DELETE /usage/v2/budgets/${budgetId}/`,
  ];
  assert(
    JSON.stringify(mutationRequests) === JSON.stringify(expected),
    `Unexpected billing mutations. Expected ${JSON.stringify(
      expected,
    )}, saw ${JSON.stringify(mutationRequests)}`,
  );
}

async function loadBudgetDbAudit({ organizationId, budgetNames }) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ARRAY[${budgetNames.map(sqlString).join(", ")}]::text[] AS budget_names
),
budget_rows AS (
  SELECT budget.*
  FROM usage_usagebudget budget
  JOIN requested r ON budget.organization_id = r.organization_id
  WHERE budget.name = ANY(r.budget_names)
)
SELECT json_build_object(
  'total_count', (SELECT count(*) FROM budget_rows),
  'active_count', (SELECT count(*) FROM budget_rows WHERE deleted = false),
  'deleted_count', (SELECT count(*) FROM budget_rows WHERE deleted = true),
  'active_id', (SELECT id FROM budget_rows WHERE deleted = false ORDER BY id DESC LIMIT 1),
  'deleted_id', (SELECT id FROM budget_rows WHERE deleted = true ORDER BY id DESC LIMIT 1),
  'active_name', (SELECT name FROM budget_rows WHERE deleted = false ORDER BY id DESC LIMIT 1),
  'active_scope', (SELECT scope FROM budget_rows WHERE deleted = false ORDER BY id DESC LIMIT 1),
  'active_threshold_value', (SELECT threshold_value::text FROM budget_rows WHERE deleted = false ORDER BY id DESC LIMIT 1),
  'active_action', (SELECT action FROM budget_rows WHERE deleted = false ORDER BY id DESC LIMIT 1),
  'active_is_active', (SELECT is_active FROM budget_rows WHERE deleted = false ORDER BY id DESC LIMIT 1),
  'all_names', COALESCE((SELECT json_agg(name ORDER BY id) FROM budget_rows), '[]'::json)
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteBudgetFixtures({ organizationId, budgetNames }) {
  const deleteSql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ARRAY[${budgetNames.map(sqlString).join(", ")}]::text[] AS budget_names
),
deleted_rows AS (
  DELETE FROM usage_usagebudget budget
  USING requested r
  WHERE budget.organization_id = r.organization_id
    AND budget.name = ANY(r.budget_names)
  RETURNING budget.id
)
SELECT json_build_object(
  'deleted_budget_count', (SELECT count(*) FROM deleted_rows)
);
`;
  const deleted = await runPostgresJson(deleteSql);
  const residue = await loadBudgetDbAudit({ organizationId, budgetNames });
  return {
    ...deleted,
    remaining_budget_count: residue.total_count,
  };
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
