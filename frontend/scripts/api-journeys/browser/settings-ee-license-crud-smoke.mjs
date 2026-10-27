import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  assert,
  createAuthenticatedContext,
  currentUserEmail,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_CREATE_PATH = "/tmp/settings-ee-license-create-smoke.png";
const SCREENSHOT_REVOKE_PATH = "/tmp/settings-ee-license-revoke-smoke.png";

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const userInfo = await auth.client.get(apiPath("/accounts/user-info/"));
  const userEmail = currentUserEmail(userInfo) || currentUserEmail(auth.user);
  assert(userEmail.includes("@"), "Authenticated user-info omitted email.");

  const marker = auth.runId.replace(/[^a-z0-9]/gi, "").slice(0, 16);
  const customerName = `UI License ${marker}`;
  const guardCustomerName = `UI License invalid ${marker}`;
  const customerNames = [customerName, guardCustomerName];
  let createdGrantId = null;
  let cleanupAudit = null;

  await hardDeleteLicenseFixtures({ email: userEmail, customerNames });

  try {
    const initialResidue = await loadLicenseDbAudit({
      email: userEmail,
      customerNames,
    });
    assert(
      initialResidue.total_count === 0,
      "Pre-run EE license fixture cleanup left rows behind.",
    );

    const invalidAnnualStatus = await assertAnnualIntervalRejected({
      auth,
      customerName: guardCustomerName,
    });
    const postInvalidResidue = await loadLicenseDbAudit({
      email: userEmail,
      customerNames,
    });
    assert(
      postInvalidResidue.total_count === 0,
      "Invalid annual billing_interval request created an EE license row.",
    );

    const apiFailures = [];
    const pageErrors = [];
    const mutationRequests = [];
    const mutationPayloads = [];
    const evidence = {
      organization_id: auth.organizationId,
      workspace_id: auth.workspaceId,
      user_email: userEmail,
      customer_name: customerName,
      invalid_annual_status: invalidAnnualStatus,
      initial_residue: initialResidue,
    };

    const browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
      args: ["--no-sandbox"],
    });

    const page = await browser.newPage();
    await installRuntimeConfig(page, auth, mutationRequests, mutationPayloads);
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
      if (isLicenseApiPath(pathname) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${pathname}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    try {
      await openLicensesPage(page);
      await waitForVisibleText(page, "EE Licenses", { exact: true });
      await waitForVisibleText(page, "Generate License", { exact: true });

      await clickVisibleText(page, "Generate License", { exact: true });
      await waitForVisibleText(page, "Generate EE License", { exact: true });
      await fillInputByLabel(page, "Customer / Company Name", customerName);
      await selectMuiOption(page, "License Band", "Business");
      await selectMuiOption(page, "Billing Interval", "Annual");

      const createResponse = waitForLicenseMutationResponse(page, "POST");
      await clickVisibleText(page, "Generate License Key", { exact: true });
      const createdLicense = responseResult(await createResponse);
      createdGrantId = String(createdLicense?.grant_id || "");
      assert(
        isUuid(createdGrantId),
        "EE license create did not return a UUID grant id.",
      );
      assert(
        createdLicense.band === "business" &&
          Array.isArray(createdLicense.features) &&
          createdLicense.features.length > 0,
        "EE license create response did not include expected business features.",
      );
      assert(
        String(createdLicense.jwt_key || "").split(".").length === 3,
        "EE license create response did not return a JWT-shaped key.",
      );
      const expectedKeyHash = createHash("sha256")
        .update(createdLicense.jwt_key)
        .digest("hex");
      assert(
        createdLicense.key_hash === expectedKeyHash,
        "EE license create key_hash did not match the returned JWT.",
      );

      await waitForVisibleText(page, "License key generated!", {
        exact: true,
      });
      await waitForVisibleText(page, customerName, { exact: true });
      await waitForVisibleText(page, "active", { exact: true });
      const fullJwtVisible = await pageContainsText(
        page,
        createdLicense.jwt_key,
      );
      const jwtPrefixVisible = await pageContainsText(
        page,
        createdLicense.jwt_key.slice(0, 24),
      );
      assert(!fullJwtVisible, "EE license page rendered the full JWT key.");
      assert(
        jwtPrefixVisible,
        "EE license page did not render the one-time JWT preview.",
      );
      await page.screenshot({ path: SCREENSHOT_CREATE_PATH, fullPage: true });

      const createdReadback = await findLicenseByCustomerName(
        auth,
        customerName,
      );
      assertNoEELicenseSecretLeak(
        await auth.client.get(apiPath("/usage/ee/licenses/")),
        "EE license list after create",
      );
      assert(
        createdReadback?.id === createdGrantId &&
          createdReadback.band === "business" &&
          createdReadback.billing_interval === "yearly" &&
          createdReadback.status === "active",
        "EE license create list readback did not match expected state.",
      );
      const createAudit = await loadLicenseDbAudit({
        email: userEmail,
        customerNames,
      });
      assert(
        createAudit.active_count === 1 &&
          createAudit.active_id === createdGrantId &&
          createAudit.active_customer_name === customerName &&
          createAudit.active_band === "business" &&
          createAudit.active_billing_interval === "yearly" &&
          createAudit.active_status === "active" &&
          createAudit.active_key_hash_length === 64,
        `EE license create DB audit did not match expected state: ${JSON.stringify(
          createAudit,
        )}`,
      );
      evidence.created_license = {
        grant_id: createdGrantId,
        band: createdReadback.band,
        billing_interval: createdReadback.billing_interval,
        status: createdReadback.status,
        feature_count: createdReadback.features.length,
        jwt_key_length: createdLicense.jwt_key.length,
        jwt_key_matches_hash: true,
        full_jwt_visible: fullJwtVisible,
        jwt_prefix_visible: jwtPrefixVisible,
        db_audit: createAudit,
        screenshot: SCREENSHOT_CREATE_PATH,
      };

      const revokeResponse = waitForLicenseMutationResponse(page, "POST", {
        grantId: createdGrantId,
      });
      await clickLicenseAction(page, customerName, "Revoke");
      const revokedLicense = responseResult(await revokeResponse);
      assert(
        revokedLicense?.revoked === true &&
          revokedLicense.grant_id === createdGrantId,
        "EE license revoke response did not confirm revocation.",
      );
      await waitForVisibleText(page, "revoked", { exact: true });
      await page.screenshot({ path: SCREENSHOT_REVOKE_PATH, fullPage: true });

      const revokedList = await auth.client.get(apiPath("/usage/ee/licenses/"));
      assertNoEELicenseSecretLeak(revokedList, "EE license list after revoke");
      const revokedReadback = findLicenseInPayload(revokedList, customerName);
      assert(
        revokedReadback?.id === createdGrantId &&
          revokedReadback.status === "revoked",
        "EE license revoke list readback did not show revoked status.",
      );
      const revokeAudit = await loadLicenseDbAudit({
        email: userEmail,
        customerNames,
      });
      assert(
        revokeAudit.revoked_count === 1 &&
          revokeAudit.revoked_id === createdGrantId &&
          revokeAudit.revoked_at_set === true &&
          revokeAudit.revocation_reason === "Revoked by customer",
        `EE license revoke DB audit did not match expected state: ${JSON.stringify(
          revokeAudit,
        )}`,
      );
      evidence.revoked_license = {
        grant_id: createdGrantId,
        status: revokedReadback.status,
        db_audit: revokeAudit,
        screenshot: SCREENSHOT_REVOKE_PATH,
      };

      assert(
        apiFailures.length === 0,
        `EE license API failures: ${apiFailures.join("; ")}`,
      );
      assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
      assertExpectedLicenseMutations(mutationRequests, createdGrantId);
      assertExpectedLicensePayloads(mutationPayloads, createdGrantId);
      evidence.mutation_requests = mutationRequests;
      evidence.mutation_payloads = mutationPayloads;
    } finally {
      await browser.close();
    }

    cleanupAudit = await hardDeleteLicenseFixtures({
      email: userEmail,
      customerNames,
    });
    const finalResidue = await loadLicenseDbAudit({
      email: userEmail,
      customerNames,
    });
    assert(
      finalResidue.total_count === 0,
      "EE license hard cleanup left disposable rows behind.",
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
      cleanupAudit = await hardDeleteLicenseFixtures({
        email: userEmail,
        customerNames,
      });
      console.error(JSON.stringify({ cleanup_after_error: cleanupAudit }));
    } catch (cleanupError) {
      console.error(
        `EE license cleanup failed after error: ${cleanupError?.stack || cleanupError}`,
      );
    }
    throw error;
  }
}

function requireMutations() {
  if (process.env.API_JOURNEY_MUTATIONS !== "1") {
    throw new Error(
      "Set API_JOURNEY_MUTATIONS=1 to run the EE license CRUD smoke.",
    );
  }
}

async function assertAnnualIntervalRejected({ auth, customerName }) {
  let status = null;
  try {
    await auth.client.post(apiPath("/usage/ee/licenses/"), {
      band: "team",
      customer_name: customerName,
      billing_interval: "annual",
    });
  } catch (error) {
    status = error.status;
  }
  assert(status === 400, "EE license API accepted stale annual interval.");
  return status;
}

async function installRuntimeConfig(
  page,
  auth,
  mutationRequests,
  mutationPayloads,
) {
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (isTrackedMutation(request.method(), url.pathname)) {
      mutationRequests.push(`${request.method()} ${url.pathname}`);
      mutationPayloads.push(sanitizeMutationPayload(request, url.pathname));
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

function sanitizeMutationPayload(request, pathname) {
  let body = {};
  try {
    body = JSON.parse(request.postData() || "{}");
  } catch {
    body = { unparsable: true };
  }
  if (pathname === "/usage/ee/licenses/") {
    return {
      path: pathname,
      band: body.band,
      billing_interval: body.billing_interval,
      has_customer_name: Boolean(body.customer_name),
      secret_fields: Object.keys(body).filter((key) =>
        /jwt|key|hash|secret/i.test(key),
      ),
    };
  }
  if (/^\/usage\/ee\/licenses\/[^/]+\/revoke\/$/.test(pathname)) {
    return {
      path: pathname,
      keys: Object.keys(body),
    };
  }
  return { path: pathname, keys: Object.keys(body) };
}

async function openLicensesPage(page) {
  const listResponse = waitForLicenseListResponse(page);
  await page.goto(`${APP_BASE}/dashboard/settings/ee-licenses`, {
    waitUntil: "domcontentloaded",
  });
  await listResponse;
}

function waitForLicenseListResponse(page) {
  return page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      new URL(response.url()).pathname === "/usage/ee/licenses/" &&
      response.status() < 400,
    { timeout: 60000 },
  );
}

async function waitForLicenseMutationResponse(page, method, { grantId } = {}) {
  const response = await page.waitForResponse(
    (candidate) => {
      if (
        candidate.request().method() !== method ||
        candidate.status() >= 400
      ) {
        return false;
      }
      const pathname = new URL(candidate.url()).pathname;
      if (!grantId) return pathname === "/usage/ee/licenses/";
      return pathname === `/usage/ee/licenses/${grantId}/revoke/`;
    },
    { timeout: 60000 },
  );
  return response.json();
}

function responseResult(payload) {
  return payload?.result || payload;
}

async function findLicenseByCustomerName(auth, customerName) {
  const payload = await auth.client.get(apiPath("/usage/ee/licenses/"));
  assertNoEELicenseSecretLeak(payload, "EE license list readback");
  return findLicenseInPayload(payload, customerName);
}

function findLicenseInPayload(payload, customerName) {
  const rows = Array.isArray(payload?.licenses) ? payload.licenses : [];
  return rows.find((row) => row.customer_name === customerName) || null;
}

function assertNoEELicenseSecretLeak(payload, label) {
  const text = JSON.stringify(payload || {});
  assert(
    !/"jwt_key"|"key_hash"|"license_key_hash"/.test(text),
    `${label} exposed license secret fields.`,
  );
  assert(
    !/eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/.test(text),
    `${label} appears to contain a JWT license key.`,
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

async function clickLicenseAction(page, customerName, actionText) {
  const clicked = await page.evaluate(
    ({ customerName: targetCustomerName, actionText: targetActionText }) => {
      const normalized = (value) => String(value || "").trim();
      const exactTextNodes = Array.from(
        document.querySelectorAll("body *"),
      ).filter(
        (element) => normalized(element.textContent) === targetCustomerName,
      );
      for (const element of exactTextNodes) {
        let current = element;
        while (current && current !== document.body) {
          const button = Array.from(current.querySelectorAll("button")).find(
            (item) => normalized(item.textContent) === targetActionText,
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
    { customerName, actionText },
  );
  assert(clicked, `Could not click ${actionText} for license ${customerName}`);
}

async function pageContainsText(page, text) {
  return page.evaluate((expectedText) => {
    return document.body.innerText.includes(expectedText);
  }, text);
}

function isLicenseApiPath(pathname) {
  return (
    pathname === "/usage/ee/licenses/" ||
    /^\/usage\/ee\/licenses\/[^/]+\/revoke\/$/.test(pathname)
  );
}

function isTrackedMutation(method, pathname) {
  if (method === "GET" || method === "HEAD" || method === "OPTIONS") {
    return false;
  }
  return (
    isLicenseApiPath(pathname) ||
    pathname.includes("/payment-methods") ||
    pathname.includes("/checkout") ||
    pathname.includes("/upgrade-to-payg") ||
    pathname.includes("/downgrade-to-free") ||
    pathname.includes("/add-addon") ||
    pathname.includes("/remove-addon") ||
    pathname.includes("/reinstate-addon") ||
    pathname.includes("/usage/v2/budgets")
  );
}

function assertExpectedLicenseMutations(mutationRequests, grantId) {
  const expected = [
    "POST /usage/ee/licenses/",
    `POST /usage/ee/licenses/${grantId}/revoke/`,
  ];
  assert(
    JSON.stringify(mutationRequests) === JSON.stringify(expected),
    `Unexpected billing mutations. Expected ${JSON.stringify(
      expected,
    )}, saw ${JSON.stringify(mutationRequests)}`,
  );
}

function assertExpectedLicensePayloads(mutationPayloads, grantId) {
  const expectedRevokePath = `/usage/ee/licenses/${grantId}/revoke/`;
  const createPayload = mutationPayloads.find(
    (item) => item.path === "/usage/ee/licenses/",
  );
  assert(
    createPayload?.band === "business" &&
      createPayload.billing_interval === "yearly" &&
      createPayload.has_customer_name === true &&
      createPayload.secret_fields.length === 0,
    `Unexpected EE license create payload: ${JSON.stringify(createPayload)}`,
  );
  const revokePayload = mutationPayloads.find(
    (item) => item.path === expectedRevokePath,
  );
  assert(
    revokePayload && JSON.stringify(revokePayload.keys) === JSON.stringify([]),
    `Unexpected EE license revoke payload: ${JSON.stringify(revokePayload)}`,
  );
}

async function loadLicenseDbAudit({ email, customerNames }) {
  const sql = `
WITH requested AS (
  SELECT
    lower(${sqlString(email)}) AS email,
    ARRAY[${customerNames.map(sqlString).join(", ")}]::text[] AS customer_names
),
license_rows AS (
  SELECT license.*
  FROM usage_eelicensegrant license
  JOIN requested r ON lower(license.customer_email) = r.email
  WHERE license.customer_name = ANY(r.customer_names)
)
SELECT json_build_object(
  'total_count', (SELECT count(*) FROM license_rows),
  'active_count', (SELECT count(*) FROM license_rows WHERE deleted = false AND status = 'active'),
  'revoked_count', (SELECT count(*) FROM license_rows WHERE deleted = false AND status = 'revoked'),
  'deleted_count', (SELECT count(*) FROM license_rows WHERE deleted = true),
  'active_id', (SELECT id::text FROM license_rows WHERE deleted = false AND status = 'active' ORDER BY issued_at DESC LIMIT 1),
  'revoked_id', (SELECT id::text FROM license_rows WHERE deleted = false AND status = 'revoked' ORDER BY issued_at DESC LIMIT 1),
  'active_customer_name', (SELECT customer_name FROM license_rows WHERE deleted = false AND status = 'active' ORDER BY issued_at DESC LIMIT 1),
  'active_band', (SELECT band FROM license_rows WHERE deleted = false AND status = 'active' ORDER BY issued_at DESC LIMIT 1),
  'active_billing_interval', (SELECT billing_interval FROM license_rows WHERE deleted = false AND status = 'active' ORDER BY issued_at DESC LIMIT 1),
  'active_status', (SELECT status FROM license_rows WHERE deleted = false AND status = 'active' ORDER BY issued_at DESC LIMIT 1),
  'active_key_hash_length', (SELECT length(license_key_hash) FROM license_rows WHERE deleted = false AND status = 'active' ORDER BY issued_at DESC LIMIT 1),
  'active_feature_count', (SELECT jsonb_array_length(features::jsonb) FROM license_rows WHERE deleted = false AND status = 'active' ORDER BY issued_at DESC LIMIT 1),
  'revoked_at_set', COALESCE((SELECT revoked_at IS NOT NULL FROM license_rows WHERE deleted = false AND status = 'revoked' ORDER BY issued_at DESC LIMIT 1), false),
  'revocation_reason', (SELECT revocation_reason FROM license_rows WHERE deleted = false AND status = 'revoked' ORDER BY issued_at DESC LIMIT 1),
  'all_customer_names', COALESCE((SELECT json_agg(customer_name ORDER BY issued_at) FROM license_rows), '[]'::json)
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteLicenseFixtures({ email, customerNames }) {
  const deleteSql = `
WITH requested AS (
  SELECT
    lower(${sqlString(email)}) AS email,
    ARRAY[${customerNames.map(sqlString).join(", ")}]::text[] AS customer_names
),
deleted_rows AS (
  DELETE FROM usage_eelicensegrant AS license
  USING requested r
  WHERE lower(license.customer_email) = r.email
    AND license.customer_name = ANY(r.customer_names)
  RETURNING license.id
)
SELECT json_build_object(
  'deleted_license_count', (SELECT count(*) FROM deleted_rows)
);
`;
  const deleted = await runPostgresJson(deleteSql);
  const residue = await loadLicenseDbAudit({ email, customerNames });
  return {
    ...deleted,
    remaining_license_count: residue.total_count,
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
