/* eslint-disable no-console */
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createRequire } from "node:module";
import process from "node:process";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH =
  process.env.SIMULATE_PERSONAS_SCREENSHOT ||
  "/tmp/simulate-personas-lifecycle-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const runId = auth.runId.replace(/[^a-z0-9-]/gi, "-");
  const namePrefix = `browser persona ${runId}`;
  const createdName = `${namePrefix} created`;
  const duplicatedName = `${namePrefix} duplicate`;
  const editedDuplicateName = `${namePrefix} duplicate edited`;
  const editedDescription =
    "Updated by the browser persona lifecycle smoke after duplication.";

  await hardDeletePersonaFixtures({ namePrefix, organizationId: auth.organizationId });

  const pageErrors = [];
  const apiFailures = [];
  const mutationResponses = [];
  const browser = await puppeteer.launch({
    executablePath: browserExecutablePath(),
    headless: process.env.HEADLESS !== "0",
    defaultViewport: { width: 1440, height: 950 },
    args: ["--no-sandbox"],
  });

  let createdId = null;
  let duplicatedId = null;

  try {
    const page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installAuthState(page, auth);

    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("response", (response) => {
      const url = response.url();
      if (!url.includes("/simulate/api/personas/")) return;
      const status = response.status();
      if (status >= 400) apiFailures.push(`${status} ${url}`);
      if (["POST", "PATCH", "DELETE"].includes(response.request().method())) {
        mutationResponses.push(`${response.request().method()} ${status} ${url}`);
      }
    });

    await page.goto(`${APP_BASE}/dashboard/simulate/personas`, {
      waitUntil: "domcontentloaded",
    });
    await expectVisibleText(page, "Create persona");

    const createdResponse = await waitForResponseDuring(
      page,
      "persona UI create",
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/simulate/api/personas/") &&
        response.status() < 400,
      async () => {
        await clickButtonByText(page, "Create persona");
        await clickVisibleText(page, "Chat Type");
        await setInputValue(page, 'input[aria-label="Persona name"]', createdName);
        await setInputValue(
          page,
          'textarea[aria-label="Description"]',
          "Temporary chat persona created by the browser lifecycle smoke.",
        );
        await selectDropdownOption(
          page,
          'input[placeholder="Select language"]',
          "English",
        );
        await clickButtonByText(page, "Save");
      },
    );
    const createdBody = await createdResponse.json();
    createdId = createdBody?.result?.id || createdBody?.id;
    assert(isUuid(createdId), "Persona create response did not include a UUID id.");

    await searchPersonas(page, createdName);
    await expectVisibleText(page, createdName);
    await expectNoVisibleText(page, "Invalid Date");
    await expectNoVisibleText(page, "undefined");

    const duplicateResponse = await waitForResponseDuring(
      page,
      "persona UI duplicate",
      (response) =>
        response.request().method() === "POST" &&
        response.url().includes("/simulate/api/personas/duplicate/") &&
        response.status() < 400,
      async () => {
        await clickAriaButton(page, `Duplicate persona ${createdName}`);
        await setInputValue(
          page,
          'input[aria-label="Duplicate persona name"]',
          duplicatedName,
        );
        await clickButtonByText(page, "Duplicate");
      },
    );
    const duplicateBody = await duplicateResponse.json();
    duplicatedId = duplicateBody?.result?.id || duplicateBody?.id;
    assert(
      isUuid(duplicatedId),
      "Persona duplicate response did not include a UUID id.",
    );

    await searchPersonas(page, duplicatedName);
    await expectVisibleText(page, duplicatedName);

    await waitForResponseDuring(
      page,
      "persona UI update",
      (response) =>
        response.request().method() === "PATCH" &&
        response.url().includes(`/simulate/api/personas/${duplicatedId}/`) &&
        response.status() < 400,
      async () => {
        await clickAriaButton(page, `Edit persona ${duplicatedName}`);
        await setInputValue(
          page,
          'input[aria-label="Persona name"]',
          editedDuplicateName,
        );
        await setInputValue(
          page,
          'textarea[aria-label="Description"]',
          editedDescription,
        );
        await clickButtonByText(page, "Update");
      },
    );

    await searchPersonas(page, namePrefix);
    await expectVisibleText(page, createdName);
    await expectVisibleText(page, editedDuplicateName);
    await selectRowsByText(page, [createdName, editedDuplicateName]);

    await waitForPersonaDeletes(page, 2, async () => {
      await clickButtonByText(page, "Delete");
      await expectVisibleText(page, "This action cannot be undone.");
      await clickDialogButtonByText(page, "Delete");
    });

    const remaining = await findPersonasBySearch(auth.client, namePrefix);
    assert(
      remaining.length === 0,
      `Deleted personas still visible in API list: ${remaining
        .map((persona) => persona.name)
        .join(", ")}`,
    );

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(
      apiFailures.length === 0,
      `Persona API failures during browser smoke: ${apiFailures.join("; ")}`,
    );
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);

    const dbCleanup = await hardDeletePersonaFixtures({
      namePrefix,
      organizationId: auth.organizationId,
    });

    console.log(
      JSON.stringify(
        {
          status: "passed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          workspace_id: auth.workspaceId,
          evidence: {
            created_persona_id: createdId,
            duplicated_persona_id: duplicatedId,
            mutation_responses: mutationResponses,
            db_cleanup: dbCleanup,
            screenshot: SCREENSHOT_PATH,
          },
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await hardDeletePersonaFixtures({
      namePrefix,
      organizationId: auth.organizationId,
    }).catch(() => null);
    throw error;
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

async function installAuthState(page, auth) {
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

async function waitForPersonaDeletes(page, expectedCount, action) {
  const responses = [];
  let resolveDeletes;
  const deletePromise = new Promise((resolve) => {
    resolveDeletes = resolve;
  });
  const listener = (response) => {
    if (
      response.request().method() === "DELETE" &&
      response.url().includes("/simulate/api/personas/")
    ) {
      responses.push(response);
      if (responses.length >= expectedCount) resolveDeletes();
    }
  };
  page.on("response", listener);
  try {
    await Promise.all([deletePromiseWithTimeout(deletePromise, 60000), action()]);
  } finally {
    page.off("response", listener);
  }
  assert(
    responses.length >= expectedCount,
    `Expected ${expectedCount} persona DELETE responses, saw ${responses.length}.`,
  );
  for (const response of responses) {
    assert(
      response.status() < 400,
      `Persona delete returned HTTP ${response.status()} for ${response.url()}`,
    );
  }
}

async function searchPersonas(page, text) {
  await Promise.all([
    page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        response.url().includes("/simulate/api/personas/") &&
        response.url().includes("search="),
      { timeout: 60000 },
    ),
    setInputValue(page, 'input[placeholder="Search personas"]', text),
  ]);
}

async function setInputValue(page, selector, value) {
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.$eval(
    selector,
    (element, nextValue) => {
      const proto =
        element instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      setter.call(element, nextValue);
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
    },
    value,
  );
}

async function selectDropdownOption(page, inputSelector, optionText) {
  await page.waitForSelector(inputSelector, { visible: true, timeout: 30000 });
  await page.click(inputSelector);
  await clickVisibleText(page, optionText, {
    selector: '[role="menu"] li, [role="presentation"] li, li',
  });
}

async function clickButtonByText(page, text) {
  await clickVisibleText(page, text, { selector: "button" });
}

async function clickDialogButtonByText(page, text) {
  await clickVisibleText(page, text, {
    selector: '[role="dialog"] button',
  });
}

async function clickAriaButton(page, label) {
  const selector = `button[aria-label="${cssEscape(label)}"]`;
  await page.waitForSelector(selector, { visible: true, timeout: 30000 });
  await page.click(selector);
}

async function clickVisibleText(page, text, { selector = "body *" } = {}) {
  await page.waitForFunction(
    (expected, targetSelector) =>
      Array.from(document.querySelectorAll(targetSelector)).some((node) => {
        const rect = node.getBoundingClientRect();
        const style = window.getComputedStyle(node);
        return (
          rect.width > 0 &&
          rect.height > 0 &&
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          String(node.textContent || "")
            .replace(/\s+/g, " ")
            .trim()
            .includes(expected)
        );
      }),
    { timeout: 30000 },
    text,
    selector,
  );
  await page.evaluate(
    (expected, targetSelector) => {
      const candidates = Array.from(document.querySelectorAll(targetSelector))
        .filter((candidate) => {
          const rect = candidate.getBoundingClientRect();
          const style = window.getComputedStyle(candidate);
          return (
            rect.width > 0 &&
            rect.height > 0 &&
            style.visibility !== "hidden" &&
            style.display !== "none" &&
            String(candidate.textContent || "")
              .replace(/\s+/g, " ")
              .trim()
              .includes(expected)
          );
        })
        .sort(
          (a, b) =>
            String(a.textContent || "").length -
            String(b.textContent || "").length,
        );
      const node = candidates[0];
      if (!node) throw new Error(`No visible node found for ${expected}`);
      node.click();
    },
    text,
    selector,
  );
}

function deletePromiseWithTimeout(promise, timeoutMs) {
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      setTimeout(
        () => reject(new Error("Timed out waiting for persona DELETE responses.")),
        timeoutMs,
      );
    }),
  ]);
}

async function selectRowsByText(page, labels) {
  const selected = await page.evaluate((expectedLabels) => {
    let count = 0;
    const rows = Array.from(document.querySelectorAll('[role="row"][data-id]'));
    for (const label of expectedLabels) {
      const row = rows.find((candidate) =>
        candidate.innerText?.includes(label),
      );
      if (!row) continue;
      const checkbox = row.querySelector('input[type="checkbox"]');
      if (!checkbox) continue;
      if (!checkbox.checked) checkbox.click();
      count += 1;
    }
    return count;
  }, labels);
  assert(
    selected === labels.length,
    `Selected ${selected} persona rows, expected ${labels.length}.`,
  );
}

async function expectVisibleText(page, text) {
  await page.waitForFunction(
    (expected) => document.body?.innerText?.includes(expected),
    { timeout: 30000 },
    text,
  );
}

async function expectNoVisibleText(page, text) {
  const found = await page.evaluate((expected) =>
    Boolean(document.body?.innerText?.includes(expected)),
  );
  assert(!found, `Unexpected visible text found: ${text}`);
}

async function findPersonasBySearch(client, search) {
  const result = await client.get(apiPath("/simulate/api/personas/"), {
    query: { page: 1, limit: 100, search },
  });
  return asArray(result).filter((persona) => persona?.name?.startsWith(search));
}

async function hardDeletePersonaFixtures({ namePrefix, organizationId }) {
  const sql = `
WITH target_personas AS (
  SELECT id
  FROM simulate_personas
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${namePrefix}%`)}
),
deleted_personas AS (
  DELETE FROM simulate_personas p
  USING target_personas target
  WHERE p.id = target.id
  RETURNING p.id
)
SELECT json_build_object(
  'deleted_persona_count', (SELECT count(*) FROM deleted_personas),
  'remaining_persona_count', (
    SELECT count(*)
    FROM target_personas
    WHERE id NOT IN (SELECT id FROM deleted_personas)
  )
);
`;
  return runPostgresJson(sql);
}

async function runPostgresJson(sql) {
  const container =
    process.env.API_JOURNEY_DB_CONTAINER || "futureagi-ws2-postgres-1";
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

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlString(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function cssEscape(value) {
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
  return "/usr/bin/google-chrome";
}

main()
  .then(() => {
    process.exit(0);
  })
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
