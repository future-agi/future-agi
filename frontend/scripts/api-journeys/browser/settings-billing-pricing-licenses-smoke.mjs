import { execFile } from "node:child_process";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  currentUserEmail,
  isUuid,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFileAsync = promisify(execFile);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const BILLING_SCREENSHOT_PATH = "/tmp/settings-billing-smoke.png";
const PRICING_SCREENSHOT_PATH = "/tmp/settings-pricing-smoke.png";
const LICENSES_SCREENSHOT_PATH = "/tmp/settings-ee-licenses-smoke.png";

async function main() {
  const auth = await createAuthenticatedContext();
  const userInfo = await auth.client.get(apiPath("/accounts/user-info/"));
  const userEmail = currentUserEmail(userInfo) || currentUserEmail(auth.user);
  assert(userEmail.includes("@"), "Authenticated user-info omitted email.");

  const plans = await auth.client.get(apiPath("/usage/v2/plans-and-addons/"));
  assert(Array.isArray(plans?.tiers), "Plans API did not return tiers.");
  assert(Array.isArray(plans?.addons), "Plans API did not return add-ons.");
  assertNoSensitiveBillingPayload(plans, "plans-and-addons");

  const billing = await auth.client.get(apiPath("/usage/v2/billing-overview/"));
  assert(
    billing?.org_id === auth.organizationId,
    "Billing overview org mismatch.",
  );
  assertNoSensitiveBillingPayload(billing, "billing-overview");

  const invoices = await auth.client.get(apiPath("/usage/v2/invoices/"));
  const invoiceRows = Array.isArray(invoices?.invoices)
    ? invoices.invoices
    : [];
  let invoiceDetail = null;
  let missingInvoiceStatus = null;
  if (invoiceRows[0]?.id) {
    invoiceDetail = await auth.client.get(
      apiPath("/usage/v2/invoices/{invoice_id}/", {
        invoice_id: invoiceRows[0].id,
      }),
    );
    assert(
      invoiceDetail?.invoice?.id === invoiceRows[0].id,
      "Invoice detail returned a different invoice id.",
    );
    assert(
      Array.isArray(invoiceDetail?.line_items),
      "Invoice detail omitted line_items.",
    );
  }
  try {
    await auth.client.get(
      apiPath("/usage/v2/invoices/{invoice_id}/", {
        invoice_id: "00000000-0000-0000-0000-000000000000",
      }),
    );
  } catch (error) {
    missingInvoiceStatus = error.status;
  }
  assert(
    missingInvoiceStatus === 404,
    "Missing invoice detail did not return HTTP 404.",
  );
  assertNoSensitiveBillingPayload(invoices, "invoices");
  assertNoSensitiveBillingPayload(invoiceDetail || {}, "invoice-detail");

  const notifications = await auth.client.get(
    apiPath("/usage/v2/notifications/"),
  );
  const budgets = await auth.client.get(apiPath("/usage/v2/budgets/"));
  const paymentMethods = asArray(
    await auth.client.get(apiPath("/usage/v2/payment-methods/")),
  );
  const licenses = await auth.client.get(apiPath("/usage/ee/licenses/"));
  const legacySubscriptionStatus = await auth.client.get(
    apiPath("/usage/subscription-status/"),
    { unwrap: false },
  );
  const legacyBillingDetails = await auth.client.get(
    apiPath("/usage/get-billing-details/"),
    { unwrap: false },
  );

  assert(
    legacySubscriptionStatus?.status === true,
    "Legacy subscription-status did not return status true.",
  );
  assert(
    legacySubscriptionStatus?.result?.subscription_status,
    "Legacy subscription-status omitted subscription_status.",
  );
  assert(
    legacyBillingDetails?.status === "success" &&
      legacyBillingDetails?.billing_info,
    "Legacy billing details did not return billing_info.",
  );
  assertNoSensitiveBillingPayload(notifications, "notifications");
  assertNoSensitiveBillingPayload(budgets, "budgets");
  assertNoSensitiveBillingPayload(paymentMethods, "payment-methods");
  assertNoSensitiveBillingPayload(licenses, "ee-licenses");

  for (const method of paymentMethods) {
    assert(
      String(method?.last4 || "").match(/^\d{4}$/),
      "Payment method omitted last4 masking.",
    );
    assert(
      !method?.card_number && !method?.number && !method?.cvc,
      "Payment method payload exposed raw card fields.",
    );
  }

  const licenseRows = Array.isArray(licenses?.licenses)
    ? licenses.licenses
    : [];
  const budgetRows = Array.isArray(budgets?.budgets) ? budgets.budgets : [];
  const audit = await loadBillingLicenseDbAudit({
    organizationId: auth.organizationId,
    email: userEmail,
  });
  if (audit.subscription_count > 0) {
    assert(
      audit.subscription_plan === plans.current_plan,
      "DB subscription plan does not match plans current_plan.",
    );
  }
  assert(
    audit.invoice_count >= invoiceRows.length,
    "DB invoice count is smaller than invoice API rows.",
  );
  assert(
    audit.budget_count === budgetRows.length,
    "DB budget count does not match budgets API rows.",
  );
  assert(
    audit.email_license_count === licenseRows.length,
    "DB email license count does not match license API rows.",
  );

  const apiFailures = [];
  const mutationRequests = [];
  const pageErrors = [];
  const evidence = {
    organization_id: auth.organizationId,
    workspace_id: auth.workspaceId,
    current_plan: plans.current_plan,
    billing_period: billing.period,
    billing_total: billing.total,
    invoice_count: invoiceRows.length,
    invoice_detail_line_count: invoiceDetail?.line_items?.length ?? null,
    missing_invoice_status: missingInvoiceStatus,
    notification_banner_count: notifications?.banners?.length ?? 0,
    budget_count: budgetRows.length,
    payment_method_count: paymentMethods.length,
    ee_license_count: licenseRows.length,
    legacy_subscription_status:
      legacySubscriptionStatus.result.subscription_status,
    legacy_billing_details_fields: Object.keys(
      legacyBillingDetails.billing_info || {},
    ).length,
    db_audit: audit,
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
    if (isBillingReadPath(pathname) && response.status() >= 400) {
      apiFailures.push(`${response.status()} ${pathname}`);
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  try {
    await openBillingPage(page);
    await waitForVisibleText(page, "Billing", { exact: true });
    await waitForVisibleText(
      page,
      "Manage your billing, view invoices, and update payment methods.",
    );
    await waitForVisibleText(page, "Current Period", { exact: true });
    await waitForVisibleText(page, "Usage Budgets", { exact: true });
    await waitForVisibleText(page, "Invoice History", { exact: true });
    await waitForVisibleText(page, "Payment Methods", { exact: true });
    if (paymentMethods.length > 0) {
      await waitForVisibleText(page, paymentMethods[0].brand);
      await waitForVisibleText(page, paymentMethods[0].last4);
    } else {
      await waitForVisibleText(page, "No payment methods on file");
    }
    await assertNoBillingMutationVisible(page);
    await waitForNoVisibleText(page, "Invalid Date");
    await page.screenshot({ path: BILLING_SCREENSHOT_PATH, fullPage: true });
    evidence.billing_screenshot = BILLING_SCREENSHOT_PATH;

    await openPricingPage(page);
    await waitForVisibleText(page, "Plans & Pricing", { exact: true });
    await waitForVisibleText(page, "Start free or pay as you go.");
    if (plans.isCustomPricing) {
      await waitForVisibleText(page, "Custom Pricing", { exact: true });
      await waitForNoVisibleText(page, "Choose your tier", { exact: true });
    } else {
      await waitForVisibleText(page, "Choose your tier", { exact: true });
      await waitForVisibleText(page, "Add-ons", { exact: true });
      await waitForVisibleText(page, "Usage-based pricing", { exact: true });
    }
    await assertNoBillingMutationVisible(page);
    await waitForNoVisibleText(page, "Invalid Date");
    await page.screenshot({ path: PRICING_SCREENSHOT_PATH, fullPage: true });
    evidence.pricing_screenshot = PRICING_SCREENSHOT_PATH;

    await openLicensesPage(page);
    await waitForVisibleText(page, "EE Licenses", { exact: true });
    await waitForVisibleText(
      page,
      "Manage enterprise license keys for self-hosted deployments.",
    );
    await waitForVisibleText(page, "Generate License", { exact: true });
    if ((licenses?.licenses?.length ?? 0) === 0) {
      await waitForVisibleText(page, "No licenses yet", { exact: true });
    }
    await waitForNoVisibleText(page, "License key generated!", { exact: true });
    await waitForNoVisibleText(page, "EE_LICENSE_KEY");
    await assertNoVisibleJwt(page);
    await page.screenshot({ path: LICENSES_SCREENSHOT_PATH, fullPage: true });
    evidence.licenses_screenshot = LICENSES_SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(
      mutationRequests.length === 0,
      `Unexpected billing mutation requests: ${mutationRequests.join("; ")}`,
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
  } finally {
    await browser.close();
  }
}

async function installRuntimeConfig(page, auth, mutationRequests) {
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (isBillingMutationRequest(request.method(), url.pathname)) {
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
  const responses = [
    waitForReadResponse(page, "/usage/v2/billing-overview/"),
    waitForReadResponse(page, "/usage/v2/notifications/"),
    waitForReadResponse(page, "/usage/v2/invoices/"),
    waitForReadResponse(page, "/usage/v2/budgets/"),
    waitForReadResponse(page, "/usage/v2/payment-methods/"),
  ];
  await page.goto(`${APP_BASE}/dashboard/settings/billing`, {
    waitUntil: "domcontentloaded",
  });
  await Promise.all(responses);
}

async function openPricingPage(page) {
  const plansResponse = waitForReadResponse(
    page,
    "/usage/v2/plans-and-addons/",
  );
  await page.goto(`${APP_BASE}/dashboard/settings/pricing`, {
    waitUntil: "domcontentloaded",
  });
  await plansResponse;
}

async function openLicensesPage(page) {
  const licensesResponse = waitForReadResponse(page, "/usage/ee/licenses/");
  await page.goto(`${APP_BASE}/dashboard/settings/ee-licenses`, {
    waitUntil: "domcontentloaded",
  });
  await licensesResponse;
}

function waitForReadResponse(page, pathname) {
  return page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === pathname && response.status() < 400,
    { timeout: 60000 },
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
  { exact = false, timeout = 10000 } = {},
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

async function assertNoBillingMutationVisible(page) {
  const href = await page.evaluate(() => window.location.href);
  assert(
    !/checkout|stripe/i.test(href),
    "Browser navigated to checkout/Stripe during read-only smoke.",
  );
}

async function assertNoVisibleJwt(page) {
  const visibleText = await page.evaluate(() => document.body.innerText || "");
  assert(
    !/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/.test(visibleText),
    "Visible page text contains a JWT-looking EE license key.",
  );
}

function isBillingReadPath(pathname) {
  return [
    "/usage/v2/plans-and-addons/",
    "/usage/v2/billing-overview/",
    "/usage/v2/invoices/",
    "/usage/v2/notifications/",
    "/usage/v2/budgets/",
    "/usage/v2/payment-methods/",
    "/usage/ee/licenses/",
  ].some(
    (path) => pathname === path || pathname.startsWith("/usage/v2/invoices/"),
  );
}

function isBillingMutationRequest(method, pathname) {
  if (method === "GET" || method === "HEAD" || method === "OPTIONS")
    return false;
  return [
    "/usage/v2/upgrade-to-payg/",
    "/usage/v2/downgrade-to-free/",
    "/usage/v2/add-addon/",
    "/usage/v2/remove-addon/",
    "/usage/v2/reinstate-addon/",
    "/usage/v2/payment-methods/",
    "/usage/v2/payment-methods/setup-intent/",
    "/usage/ee/licenses/",
  ].some(
    (path) => pathname === path || pathname.startsWith("/usage/ee/licenses/"),
  );
}

async function loadBillingLicenseDbAudit({ organizationId, email }) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    lower(${sqlString(email)}) AS email
),
subscription_rows AS (
  SELECT
    sub.id,
    sub.plan,
    sub.billing_interval,
    sub.status,
    sub.wallet_balance
  FROM usage_organizationsubscription sub
  JOIN requested r ON sub.organization_id = r.organization_id
  WHERE sub.deleted = false
),
invoice_rows AS (
  SELECT invoice.id, invoice.total
  FROM usage_invoice invoice
  JOIN requested r ON invoice.organization_id = r.organization_id
  WHERE invoice.deleted = false
),
budget_rows AS (
  SELECT budget.id, budget.is_active
  FROM usage_usagebudget budget
  JOIN requested r ON budget.organization_id = r.organization_id
  WHERE budget.deleted = false
),
license_rows AS (
  SELECT license.id, license.status
  FROM usage_eelicensegrant license
  JOIN requested r ON lower(license.customer_email) = r.email
  WHERE license.deleted = false
)
SELECT json_build_object(
  'subscription_count', (SELECT count(*) FROM subscription_rows),
  'subscription_plan', (SELECT plan FROM subscription_rows LIMIT 1),
  'subscription_billing_interval', (SELECT billing_interval FROM subscription_rows LIMIT 1),
  'subscription_status', (SELECT status FROM subscription_rows LIMIT 1),
  'invoice_count', (SELECT count(*) FROM invoice_rows),
  'invoice_total_sum', COALESCE((SELECT sum(total)::float FROM invoice_rows), 0),
  'budget_count', (SELECT count(*) FROM budget_rows),
  'active_budget_count', (SELECT count(*) FROM budget_rows WHERE is_active = true),
  'email_license_count', (SELECT count(*) FROM license_rows),
  'active_email_license_count', (SELECT count(*) FROM license_rows WHERE status = 'active'),
  'revoked_email_license_count', (SELECT count(*) FROM license_rows WHERE status = 'revoked')
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

function assertNoSensitiveBillingPayload(value, label) {
  const text = JSON.stringify(value || {});
  assert(
    !/"checkout_url"\s*:/.test(text) && !/"checkoutUrl"\s*:/.test(text),
    `${label} exposed a checkout URL in a read response.`,
  );
  assert(
    !/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/.test(text),
    `${label} exposed a JWT-looking token.`,
  );
  assert(
    !/\b(?:\d[ -]*?){13,19}\b/.test(text),
    `${label} exposed a full-card-looking number.`,
  );
  assert(
    !/(license_key|licenseKey|license_key_hash|licenseKeyHash|jwt_key|jwtKey)/.test(
      text,
    ),
    `${label} exposed license secret fields.`,
  );
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
