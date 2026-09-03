/* eslint-disable no-console */
import { execFile as execFileCallback } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createRequire } from "node:module";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
  createAuthenticatedContext,
  isUuid,
  requireMutations,
  withQuery,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFile = promisify(execFileCallback);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/gateway-webhooks-delivery-smoke.png";
const RETRY_SCREENSHOT_PATH = "/tmp/gateway-webhooks-delivery-retry-smoke.png";
const FAILURE_SCREENSHOT_PATH =
  "/tmp/gateway-webhooks-delivery-smoke-failure.png";
const WEBHOOK_PREFIX = "ui_gateway_webhook_";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const shortSuffix = suffix.slice(-8);
  const marker = `${WEBHOOK_PREFIX}${suffix}`;
  const targetWebhookId = randomUUID();
  const otherWebhookId = randomUUID();
  const failedEventId = randomUUID();
  const deliveredEventId = randomUUID();
  const otherEventId = randomUUID();
  const targetName = `${marker}_target`;
  const otherName = `${marker}_other`;
  const targetUrl = `https://example.com/futureagi-ui-webhook-${shortSuffix}`;
  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const gatewayRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  const evidence = {
    webhook_id: targetWebhookId,
    failed_event_id: failedEventId,
    delivered_event_id: deliveredEventId,
    target_name: targetName,
  };
  let browser = null;
  let page = null;
  let caughtError = null;

  await cleanupStaleWebhookFixturesDb({
    organizationId: auth.organizationId,
    evidence: cleanupEvidence,
  });

  try {
    evidence.seeded = await seedWebhookDeliveryFixturesDb({
      organizationId: auth.organizationId,
      targetWebhookId,
      otherWebhookId,
      failedEventId,
      deliveredEventId,
      otherEventId,
      marker,
      targetName,
      otherName,
      targetUrl,
    });

    evidence.api_preflight = await preflightWebhookDeliveryApis(auth, {
      targetWebhookId,
      failedEventId,
      deliveredEventId,
      otherEventId,
      targetName,
    });

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 980 },
      args: ["--no-sandbox"],
    });
    page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await installRuntimeConfig(page, auth);
    await installBrowserState(page, auth);

    page.on("request", (request) => {
      const url = request.url();
      if (!isGatewayApiUrl(url)) return;
      gatewayRequests.push(`${request.method()} ${url}`);
      if (MUTATION_METHODS.has(request.method())) {
        const mutation = `${request.method()} ${url}`;
        browserMutations.push(mutation);
        if (!isAllowedWebhookMutation(request.method(), url, failedEventId)) {
          unexpectedMutations.push(mutation);
        }
      }
    });
    page.on("response", (response) => {
      const url = response.url();
      if (isGatewayApiUrl(url) && response.status() >= 400) {
        apiFailures.push(`${response.status()} ${url}`);
      }
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await waitForResponseDuring(
      page,
      "initial Gateway webhooks load",
      gatewayGetResponse("/agentcc/webhooks/"),
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway/webhooks`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway/webhooks");

    for (const label of [
      "Webhooks",
      "Configure webhook endpoints for event notifications",
      "Create Webhook",
      "Delivery Log",
      "Name",
      "URL",
      "Events",
      "Status",
      "Created",
      "Actions",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    await setVisibleInputByPlaceholder(
      page,
      "Search by name or URL...",
      targetName,
    );
    await waitForVisibleText(page, targetName, { exact: true });
    await waitForVisibleText(page, targetUrl);
    await waitForVisibleText(page, "request.completed", { exact: true });
    await waitForVisibleText(page, "error.occurred", { exact: true });
    await waitForNoVisibleText(page, otherName);

    await waitForResponseDuring(
      page,
      "open filtered webhook Delivery Log",
      webhookEventsResponse({ webhook_id: targetWebhookId }),
      () => clickWebhookRowAction(page, targetName, "view-events"),
    );
    await waitForPath(page, "/dashboard/gateway/webhooks/delivery");

    for (const label of [
      "Event Type",
      "Status",
      "Attempts",
      "Response Code",
      "Last Attempt",
      "Error",
      "Actions",
      "error.occurred",
      "request.completed",
      "failed",
      "delivered",
      "2/5",
      "1/5",
      "503",
      "204",
      `${marker} temporary delivery failure`,
      "Showing 2 events",
      "Retry",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    await waitForNoVisibleText(page, "budget.exceeded", { exact: true });
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    await waitForResponsesDuring(
      page,
      "retry failed webhook event",
      [
        retryResponse(failedEventId),
        webhookEventsResponse({ webhook_id: targetWebhookId }),
      ],
      () => clickRetryForEventRow(page, "error.occurred"),
    );

    await waitForVisibleText(page, "pending", { exact: true });
    await waitForVisibleText(page, "0/5", { exact: true });
    await waitForNoVisibleText(page, `${marker} temporary delivery failure`, {
      exact: true,
    });
    await waitForNoVisibleText(page, "Retry", { exact: true });
    await page.screenshot({ path: RETRY_SCREENSHOT_PATH, fullPage: true });

    evidence.post_retry_audit = await auditWebhookDeliveryFixturesDb({
      organizationId: auth.organizationId,
      targetWebhookId,
      failedEventId,
      deliveredEventId,
      otherEventId,
    });
    assert(
      evidence.post_retry_audit.failed_event_status === "pending" &&
        Number(evidence.post_retry_audit.failed_event_attempts) === 0 &&
        evidence.post_retry_audit.failed_event_next_retry_at === null &&
        evidence.post_retry_audit.failed_event_last_error === "",
      `Retry did not reset failed event state: ${JSON.stringify(
        evidence.post_retry_audit,
      )}`,
    );
    assert(
      evidence.post_retry_audit.delivered_event_status === "delivered" &&
        Number(evidence.post_retry_audit.other_event_count) === 1,
      `Unexpected webhook event side effect: ${JSON.stringify(
        evidence.post_retry_audit,
      )}`,
    );

    evidence.screenshots = [SCREENSHOT_PATH, RETRY_SCREENSHOT_PATH];
    evidence.browser_mutations = browserMutations;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      browserMutations.length === 1,
      `Expected one browser retry mutation, saw ${browserMutations.length}: ${browserMutations.join(
        "; ",
      )}`,
    );
    assert(
      unexpectedMutations.length === 0,
      `Unexpected Gateway webhook browser mutations: ${unexpectedMutations.join(
        "; ",
      )}`,
    );
  } catch (error) {
    caughtError = error;
    await page
      ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
  } finally {
    await deleteWebhookDeliveryFixturesDb({
      organizationId: auth.organizationId,
      webhookIds: [targetWebhookId, otherWebhookId],
      eventIds: [failedEventId, deliveredEventId, otherEventId],
      marker,
      evidence: cleanupEvidence,
    });
    if (browser) await browser.close();
  }

  const cleanupFailures = cleanupEvidence.filter(
    (item) => item.status === "failed",
  );
  if (caughtError || cleanupFailures.length > 0) {
    console.error(
      JSON.stringify(
        {
          status: "failed",
          app_base: APP_BASE,
          api_base: auth.apiBase,
          organization_id: auth.organizationId,
          evidence,
          cleanup: cleanupEvidence,
          api_failures: apiFailures,
          page_errors: pageErrors,
          gateway_requests: gatewayRequests,
          browser_mutations: browserMutations,
          unexpected_mutations: unexpectedMutations,
          failure_screenshot: FAILURE_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
    if (caughtError) throw caughtError;
    throw new Error(
      `Gateway webhook cleanup failed: ${cleanupFailures
        .map((item) => item.error)
        .join("; ")}`,
    );
  }

  console.log(
    JSON.stringify(
      {
        status: "passed",
        app_base: APP_BASE,
        api_base: auth.apiBase,
        organization_id: auth.organizationId,
        evidence,
        cleanup: cleanupEvidence,
        gateway_request_count: gatewayRequests.length,
      },
      null,
      2,
    ),
  );
}

async function preflightWebhookDeliveryApis(
  auth,
  {
    targetWebhookId,
    failedEventId,
    deliveredEventId,
    otherEventId,
    targetName,
  },
) {
  const webhooks = responseRows(
    await auth.client.get(apiPath("/agentcc/webhooks/"), { unwrap: false }),
  );
  const target = webhooks.find((webhook) => webhook.id === targetWebhookId);
  assert(target?.name === targetName, "Seeded target webhook was not listed.");
  assert(
    !Object.prototype.hasOwnProperty.call(target, "secret"),
    "Webhook list leaked write-only secret.",
  );

  const detail = await auth.client.get(
    apiPath("/agentcc/webhooks/{id}/", { id: targetWebhookId }),
  );
  assert(detail.id === targetWebhookId, "Webhook detail returned wrong id.");
  assert(
    !Object.prototype.hasOwnProperty.call(detail, "secret"),
    "Webhook detail leaked write-only secret.",
  );

  const targetEvents = responseRows(
    await auth.client.get(
      withQuery(apiPath("/agentcc/webhook-events/"), {
        webhook_id: targetWebhookId,
      }),
      { unwrap: false },
    ),
  );
  assert(
    targetEvents.length === 2 &&
      targetEvents.every((event) => event.webhook === targetWebhookId) &&
      targetEvents.every((event) => event.id !== otherEventId),
    "Webhook event list did not isolate target webhook events.",
  );

  const failedEvents = responseRows(
    await auth.client.get(
      withQuery(apiPath("/agentcc/webhook-events/"), {
        webhook_id: targetWebhookId,
        status: "failed",
        event_type: "error.occurred",
      }),
      { unwrap: false },
    ),
  );
  assert(
    failedEvents.length === 1 && failedEvents[0].id === failedEventId,
    "Webhook status/event_type filters did not isolate the failed event.",
  );

  const failedDetail = await auth.client.get(
    apiPath("/agentcc/webhook-events/{id}/", { id: failedEventId }),
  );
  assert(
    failedDetail.id === failedEventId &&
      failedDetail.status === "failed" &&
      failedDetail.last_response_code === 503,
    "Webhook failed event detail did not return seeded state.",
  );

  const deliveredRetry = await fetchRaw(auth, {
    method: "POST",
    pathName: apiPath("/agentcc/webhook-events/{id}/retry/", {
      id: deliveredEventId,
    }),
  });
  assert(
    deliveredRetry.status === 400 &&
      deliveredRetry.text.toLowerCase().includes("delivered"),
    `Delivered webhook retry guard returned unexpected response: ${deliveredRetry.status} ${deliveredRetry.text}`,
  );

  return {
    listed_webhook_count: webhooks.length,
    target_event_count: targetEvents.length,
    failed_event_count: failedEvents.length,
    delivered_retry_status: deliveredRetry.status,
  };
}

async function fetchRaw(auth, { method, pathName }) {
  const response = await fetch(new URL(pathName, auth.apiBase), {
    method,
    headers: {
      Authorization: `Bearer ${auth.tokens.access}`,
      "X-Organization-Id": auth.organizationId,
      "X-Workspace-Id": auth.workspaceId,
      "Content-Type": "application/json",
    },
    body: method === "GET" ? undefined : "{}",
  });
  const text = await response.text();
  return { status: response.status, text };
}

function responseRows(data) {
  return asArray(data?.result ?? data);
}

async function seedWebhookDeliveryFixturesDb({
  organizationId,
  targetWebhookId,
  otherWebhookId,
  failedEventId,
  deliveredEventId,
  otherEventId,
  marker,
  targetName,
  otherName,
  targetUrl,
}) {
  const sql = `
WITH stale_webhooks AS (
  SELECT id
  FROM agentcc_webhook
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${WEBHOOK_PREFIX}%`)}
),
deleted_stale_events AS (
  DELETE FROM agentcc_webhook_event
  USING stale_webhooks
  WHERE agentcc_webhook_event.webhook_id = stale_webhooks.id
  RETURNING agentcc_webhook_event.id
),
deleted_stale_webhooks AS (
  DELETE FROM agentcc_webhook
  USING stale_webhooks
  WHERE agentcc_webhook.id = stale_webhooks.id
  RETURNING agentcc_webhook.id
),
inserted_webhooks AS (
  INSERT INTO agentcc_webhook (
    id,
    organization_id,
    name,
    url,
    secret,
    events,
    is_active,
    headers,
    description,
    created_at,
    updated_at,
    deleted,
    deleted_at
  )
  VALUES
    (
      ${sqlUuid(targetWebhookId)},
      ${sqlUuid(organizationId)},
      ${sqlString(targetName)},
      ${sqlString(targetUrl)},
      ${sqlString(`${marker}_secret`)},
      ${sqlJson(["request.completed", "error.occurred"])},
      true,
      ${sqlJson({ "X-API-Journey": marker })},
      ${sqlString("UI smoke target webhook")},
      now(),
      now(),
      false,
      NULL
    ),
    (
      ${sqlUuid(otherWebhookId)},
      ${sqlUuid(organizationId)},
      ${sqlString(otherName)},
      ${sqlString(`https://example.com/futureagi-ui-webhook-other-${marker}`)},
      ${sqlString(`${marker}_other_secret`)},
      ${sqlJson(["budget.exceeded"])},
      true,
      ${sqlJson({ "X-API-Journey": marker, other: true })},
      ${sqlString("UI smoke unrelated webhook")},
      now(),
      now(),
      false,
      NULL
    )
  RETURNING id
),
inserted_events AS (
  INSERT INTO agentcc_webhook_event (
    id,
    organization_id,
    webhook_id,
    event_type,
    payload,
    status,
    attempts,
    max_attempts,
    last_attempt_at,
    last_response_code,
    last_error,
    next_retry_at,
    created_at,
    updated_at,
    deleted,
    deleted_at
  )
  VALUES
    (
      ${sqlUuid(failedEventId)},
      ${sqlUuid(organizationId)},
      ${sqlUuid(targetWebhookId)},
      'error.occurred',
      ${sqlJson({
        marker,
        event: "error.occurred",
        request_id: `${marker}_failed_request`,
      })},
      'failed',
      2,
      5,
      now() - interval '7 minutes',
      503,
      ${sqlString(`${marker} temporary delivery failure`)},
      now() + interval '10 minutes',
      now() - interval '7 minutes',
      now() - interval '7 minutes',
      false,
      NULL
    ),
    (
      ${sqlUuid(deliveredEventId)},
      ${sqlUuid(organizationId)},
      ${sqlUuid(targetWebhookId)},
      'request.completed',
      ${sqlJson({
        marker,
        event: "request.completed",
        request_id: `${marker}_delivered_request`,
      })},
      'delivered',
      1,
      5,
      now() - interval '3 minutes',
      204,
      '',
      NULL,
      now() - interval '3 minutes',
      now() - interval '3 minutes',
      false,
      NULL
    ),
    (
      ${sqlUuid(otherEventId)},
      ${sqlUuid(organizationId)},
      ${sqlUuid(otherWebhookId)},
      'budget.exceeded',
      ${sqlJson({
        marker,
        event: "budget.exceeded",
        request_id: `${marker}_other_request`,
      })},
      'failed',
      4,
      5,
      now() - interval '2 minutes',
      500,
      ${sqlString(`${marker} unrelated failure`)},
      now() + interval '1 minute',
      now() - interval '2 minutes',
      now() - interval '2 minutes',
      false,
      NULL
    )
  RETURNING id
)
SELECT json_build_object(
  'inserted_webhook_count', (SELECT count(*) FROM inserted_webhooks),
  'inserted_event_count', (SELECT count(*) FROM inserted_events),
  'deleted_stale_webhook_count', (SELECT count(*) FROM deleted_stale_webhooks),
  'deleted_stale_event_count', (SELECT count(*) FROM deleted_stale_events)
);
`;
  const result = await runPostgresJson(sql);
  assert(
    Number(result.inserted_webhook_count) === 2 &&
      Number(result.inserted_event_count) === 3,
    `Failed to seed Gateway webhook delivery fixtures: ${JSON.stringify(result)}`,
  );
  return result;
}

async function auditWebhookDeliveryFixturesDb({
  organizationId,
  targetWebhookId,
  failedEventId,
  deliveredEventId,
  otherEventId,
}) {
  const sql = `
SELECT json_build_object(
  'failed_event_status',
    (SELECT status FROM agentcc_webhook_event WHERE id = ${sqlUuid(failedEventId)} AND organization_id = ${sqlUuid(organizationId)}),
  'failed_event_attempts',
    (SELECT attempts FROM agentcc_webhook_event WHERE id = ${sqlUuid(failedEventId)} AND organization_id = ${sqlUuid(organizationId)}),
  'failed_event_next_retry_at',
    (SELECT next_retry_at FROM agentcc_webhook_event WHERE id = ${sqlUuid(failedEventId)} AND organization_id = ${sqlUuid(organizationId)}),
  'failed_event_last_error',
    (SELECT last_error FROM agentcc_webhook_event WHERE id = ${sqlUuid(failedEventId)} AND organization_id = ${sqlUuid(organizationId)}),
  'delivered_event_status',
    (SELECT status FROM agentcc_webhook_event WHERE id = ${sqlUuid(deliveredEventId)} AND webhook_id = ${sqlUuid(targetWebhookId)}),
  'other_event_count',
    (SELECT count(*) FROM agentcc_webhook_event WHERE id = ${sqlUuid(otherEventId)} AND organization_id = ${sqlUuid(organizationId)})
);
`;
  return runPostgresJson(sql);
}

async function cleanupStaleWebhookFixturesDb({ organizationId, evidence }) {
  const sql = `
WITH stale_webhooks AS (
  SELECT id
  FROM agentcc_webhook
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND name LIKE ${sqlString(`${WEBHOOK_PREFIX}%`)}
),
deleted_events AS (
  DELETE FROM agentcc_webhook_event
  USING stale_webhooks
  WHERE agentcc_webhook_event.webhook_id = stale_webhooks.id
  RETURNING agentcc_webhook_event.id
),
deleted_webhooks AS (
  DELETE FROM agentcc_webhook
  USING stale_webhooks
  WHERE agentcc_webhook.id = stale_webhooks.id
  RETURNING agentcc_webhook.id
)
SELECT json_build_object(
  'deleted_event_count', (SELECT count(*) FROM deleted_events),
  'deleted_webhook_count', (SELECT count(*) FROM deleted_webhooks)
);
`;
  const result = await runPostgresJson(sql);
  if (
    Number(result.deleted_event_count) > 0 ||
    Number(result.deleted_webhook_count) > 0
  ) {
    evidence.push({
      cleanup: "stale UI Gateway webhook fixtures",
      status: "passed",
      result,
    });
  }
  return result;
}

async function deleteWebhookDeliveryFixturesDb({
  organizationId,
  webhookIds,
  eventIds,
  marker,
  evidence,
}) {
  const sql = `
WITH target_webhooks AS (
  SELECT id
  FROM agentcc_webhook
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND (
      id = ANY(${sqlUuidArray(webhookIds)})
      OR name LIKE ${sqlString(`${marker}%`)}
    )
),
target_events AS (
  SELECT id
  FROM agentcc_webhook_event
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND (
      id = ANY(${sqlUuidArray(eventIds)})
      OR webhook_id IN (SELECT id FROM target_webhooks)
      OR payload->>'marker' = ${sqlString(marker)}
    )
),
deleted_events AS (
  DELETE FROM agentcc_webhook_event
  USING target_events
  WHERE agentcc_webhook_event.id = target_events.id
  RETURNING agentcc_webhook_event.id
),
deleted_webhooks AS (
  DELETE FROM agentcc_webhook
  USING target_webhooks
  WHERE agentcc_webhook.id = target_webhooks.id
  RETURNING agentcc_webhook.id
)
SELECT json_build_object(
  'deleted_event_count', (SELECT count(*) FROM deleted_events),
  'deleted_webhook_count', (SELECT count(*) FROM deleted_webhooks),
  'remaining_event_count',
    (SELECT count(*) FROM target_events) - (SELECT count(*) FROM deleted_events),
  'remaining_webhook_count',
    (SELECT count(*) FROM target_webhooks) - (SELECT count(*) FROM deleted_webhooks)
);
`;
  await runPostgresJson(sql)
    .then((result) => {
      evidence.push({
        cleanup: "hard delete Gateway webhook delivery fixture",
        status:
          Number(result.remaining_event_count) === 0 &&
          Number(result.remaining_webhook_count) === 0
            ? "passed"
            : "failed",
        result,
      });
    })
    .catch((error) =>
      evidence.push({
        cleanup: "hard delete Gateway webhook delivery fixture",
        status: "failed",
        error: error.message,
      }),
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

async function installBrowserState(page, auth) {
  await page.evaluateOnNewDocument(() => {
    window.normalizeText = (value) => String(value || "").trim();
    window.setNativeInputValue = (input, value) => {
      const prototype =
        input.tagName === "TEXTAREA"
          ? window.HTMLTextAreaElement.prototype
          : window.HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
      descriptor.set.call(input, value);
      input.dispatchEvent(
        new InputEvent("input", {
          bubbles: true,
          cancelable: true,
          inputType: "insertText",
          data: value,
        }),
      );
      input.dispatchEvent(new Event("change", { bubbles: true }));
    };
    window.dispatchClick = (element) => {
      element.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
      );
      element.dispatchEvent(
        new MouseEvent("mouseup", { bubbles: true, cancelable: true }),
      );
      element.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
    };
    window.visibleElements = (selector = "body *") => {
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
      return Array.from(document.querySelectorAll(selector)).filter(isVisible);
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

function webhookEventsResponse(query = {}) {
  return gatewayGetResponse("/agentcc/webhook-events/", query);
}

function retryResponse(eventId) {
  return (response) => {
    const url = new URL(response.url());
    return (
      url.pathname === `/agentcc/webhook-events/${eventId}/retry/` &&
      response.request().method() === "POST" &&
      response.status() < 400
    );
  };
}

function gatewayGetResponse(pathname, query = {}) {
  return (response) => {
    const url = new URL(response.url());
    if (url.pathname !== pathname) return false;
    if (response.request().method() !== "GET") return false;
    if (response.status() >= 400) return false;
    for (const [key, value] of Object.entries(query)) {
      if (url.searchParams.get(key) !== String(value)) return false;
    }
    return true;
  };
}

async function waitForResponsesDuring(page, label, predicates, action) {
  try {
    const waits = predicates.map((predicate) =>
      page.waitForResponse(predicate, { timeout: 60000 }),
    );
    const responses = await Promise.all([...waits, action()]);
    return responses.slice(0, predicates.length);
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
}

async function waitForResponseDuring(page, label, predicate, action) {
  const [response] = await waitForResponsesDuring(
    page,
    label,
    [predicate],
    action,
  );
  return response;
}

async function waitForPath(page, pathname, timeout = 30000) {
  await page.waitForFunction(
    (expectedPath) => window.location.pathname === expectedPath,
    { timeout },
    pathname,
  );
}

async function waitForVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await page.waitForFunction(
    ({ text: expectedText, exact: exactMatch }) =>
      window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
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
      !window.visibleElements().some((element) => {
        const textContent = window.normalizeText(element.textContent);
        return exactMatch
          ? textContent === expectedText
          : textContent.includes(expectedText);
      }),
    { timeout },
    { text, exact },
  );
}

async function setVisibleInputByPlaceholder(
  page,
  placeholder,
  value,
  timeout = 30000,
) {
  await page.waitForFunction(
    (expectedPlaceholder) =>
      window
        .visibleElements("input,textarea")
        .some((element) => element.placeholder === expectedPlaceholder),
    { timeout },
    placeholder,
  );
  const changed = await page.evaluate(
    ({ expectedPlaceholder, nextValue }) => {
      const input = window
        .visibleElements("input,textarea")
        .find((element) => element.placeholder === expectedPlaceholder);
      if (!input) return false;
      window.setNativeInputValue(input, nextValue);
      return true;
    },
    { expectedPlaceholder: placeholder, nextValue: value },
  );
  assert(changed, `Could not set input placeholder: ${placeholder}`);
}

async function clickWebhookRowAction(page, webhookName, action) {
  const actionIndex = {
    test: 0,
    "view-events": 1,
    edit: 2,
    delete: 3,
  }[action];
  assert(actionIndex != null, `Unknown webhook row action: ${action}`);
  await waitForVisibleText(page, webhookName, { exact: true });
  const clicked = await page.evaluate(
    ({ expectedName, index }) => {
      const row = window
        .visibleElements("tr")
        .find((candidate) =>
          window.normalizeText(candidate.textContent).includes(expectedName),
        );
      if (!row) return false;
      const buttons = Array.from(row.querySelectorAll("button")).filter(
        (button) =>
          window.getComputedStyle(button).display !== "none" &&
          !button.disabled,
      );
      const target = buttons[index];
      if (!target) return false;
      window.dispatchClick(target);
      return true;
    },
    { expectedName: webhookName, index: actionIndex },
  );
  assert(clicked, `Could not click ${action} for webhook row ${webhookName}`);
}

async function clickRetryForEventRow(page, eventType) {
  await waitForVisibleText(page, eventType, { exact: true });
  const clicked = await page.evaluate((expectedEventType) => {
    const row = window
      .visibleElements("tr")
      .find((candidate) =>
        window.normalizeText(candidate.textContent).includes(expectedEventType),
      );
    if (!row) return false;
    const button = Array.from(row.querySelectorAll("button")).find(
      (candidate) => window.normalizeText(candidate.textContent) === "Retry",
    );
    if (!button || button.disabled) return false;
    window.dispatchClick(button);
    return true;
  }, eventType);
  assert(clicked, `Could not click retry for webhook event row ${eventType}`);
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
  assert(text, "Postgres DB audit returned no JSON output.");
  return JSON.parse(text);
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlUuidArray(values) {
  const uuids = asArray(values);
  for (const value of uuids) {
    assert(isUuid(value), "SQL UUID array values must be UUIDs.");
  }
  if (uuids.length === 0) {
    return "ARRAY[]::uuid[]";
  }
  return `ARRAY[${uuids.map((value) => sqlUuid(value)).join(", ")}]::uuid[]`;
}

function sqlString(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function sqlJson(value) {
  return `${sqlString(JSON.stringify(value ?? null))}::jsonb`;
}

function isGatewayApiUrl(url) {
  return url.includes("/agentcc/");
}

function isAllowedWebhookMutation(method, url, failedEventId) {
  const parsed = new URL(url);
  return (
    method === "POST" &&
    parsed.pathname === `/agentcc/webhook-events/${failedEventId}/retry/`
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
  process.exit(1);
});
