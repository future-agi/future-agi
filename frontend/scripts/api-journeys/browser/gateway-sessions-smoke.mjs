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
  unwrapApiData,
} from "../lib/api-client.mjs";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer-core");
const execFile = promisify(execFileCallback);

const APP_BASE = process.env.APP_BASE || "http://127.0.0.1:3032";
const SCREENSHOT_PATH = "/tmp/gateway-sessions-current-smoke.png";
const DETAIL_SCREENSHOT_PATH = "/tmp/gateway-session-detail-current-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/gateway-sessions-smoke-failure.png";
const SESSION_PREFIX = "ui_gateway_session_";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const sessionId = `${SESSION_PREFIX}${suffix}`;
  const sessionName = `Browser smoke gateway session ${suffix}`;
  const marker = `${SESSION_PREFIX}log_${suffix}`;
  const logIds = [randomUUID(), randomUUID(), randomUUID()];
  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const gatewayRequests = [];
  const browserMutations = [];
  const unexpectedMutations = [];
  const evidence = {};
  let browser = null;
  let page = null;
  let caughtError = null;
  let createdSessionUuid = null;
  let closedViaUi = false;

  await cleanupStaleGatewaySessionsDb({
    organizationId: auth.organizationId,
    evidence: cleanupEvidence,
  });

  try {
    const createdSession = await auth.client.post(
      apiPath("/agentcc/sessions/"),
      {
        session_id: sessionId,
        name: sessionName,
        status: "active",
        metadata: { source: "browser-smoke", runId: auth.runId },
      },
    );
    createdSessionUuid = createdSession.id;
    assert(createdSessionUuid, "Gateway session create did not return an id.");

    const seededLogs = await seedAgentccRequestLogsDb({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      logIds,
      marker,
      apiKeyId: `ui_gateway_session_key_${suffix}`,
      sharedSessionId: sessionId,
      soloSessionId: `${sessionId}_other`,
    });
    evidence.seeded_request_logs = seededLogs;

    const apiDetail = await auth.client.get(
      apiPath("/agentcc/sessions/{id}/", { id: createdSessionUuid }),
    );
    assertSessionStats(apiDetail, {
      request_count: 2,
      total_tokens: 180,
      total_cost: "0.003700",
      avg_latency_ms: "185.00",
    });

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 950 },
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
        if (
          !isAllowedSessionMutation(request.method(), url, createdSessionUuid)
        ) {
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

    await waitForResponsesDuring(
      page,
      "initial Gateway sessions load",
      [
        (response) =>
          response.url().includes("/agentcc/gateways/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          response.url().includes("/agentcc/sessions/") &&
          response.request().method() === "GET" &&
          response.status() < 400,
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway/sessions`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway/sessions");

    for (const label of [
      "Sessions",
      "Browse and manage active conversation sessions",
      "All",
      "Active",
      "Closed",
      "Session ID",
      "Requests",
      "Actions",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    await setVisibleInputByPlaceholder(
      page,
      "Search by session ID or name...",
      sessionId,
    );
    await waitForVisibleText(page, sessionName, { exact: true });
    await waitForVisibleText(page, "active", { exact: true });
    await waitForVisibleText(page, "2", { exact: true });

    const [detailResponse, requestsResponse] = await waitForResponsesDuring(
      page,
      "open Gateway session detail",
      [
        (response) =>
          isSessionDetailUrl(response.url(), createdSessionUuid) &&
          response.request().method() === "GET" &&
          response.status() < 400,
        (response) =>
          isSessionRequestsUrl(response.url(), createdSessionUuid) &&
          response.request().method() === "GET" &&
          response.status() < 400,
      ],
      () => clickTableRowByText(page, sessionName),
    );
    const detailPayload = await responseResult(detailResponse);
    const requestPayload = asArray(await responseResult(requestsResponse));
    assertSessionStats(detailPayload, {
      request_count: 2,
      total_tokens: 180,
      total_cost: "0.003700",
      avg_latency_ms: "185.00",
    });
    assert(
      requestPayload.length === 2 &&
        requestPayload.every((request) => request.session_id === sessionId),
      "Gateway session requests response did not return the seeded request chain.",
    );

    await waitForVisibleText(page, "Session Detail", { exact: true });
    await waitForVisibleText(page, sessionName, { exact: true });
    await waitForVisibleText(page, `ID: ${sessionId}`, { exact: true });
    await waitForVisibleText(page, "Requests (2)", { exact: true });
    await waitForVisibleText(page, "gpt-4o-mini", { exact: true });
    await waitForVisibleText(page, "gpt-4o", { exact: true });
    await waitForVisibleText(page, "503", { exact: true });
    await waitForVisibleText(page, "$0.0037", { exact: true });
    await waitForVisibleText(page, "180", { exact: true });
    await waitForVisibleText(page, "185ms", { exact: true });
    await page.screenshot({ path: DETAIL_SCREENSHOT_PATH, fullPage: true });

    await page.keyboard.press("Escape");
    await waitForNoVisibleText(page, "Session Detail", { exact: true });

    const closeResponse = await waitForResponseDuring(
      page,
      "close Gateway session",
      (response) =>
        response
          .url()
          .includes(`/agentcc/sessions/${createdSessionUuid}/close/`) &&
        response.request().method() === "POST" &&
        response.status() < 400,
      () => clickRowButtonByTitle(page, sessionName, "Close session"),
    );
    const closedSession = await responseResult(closeResponse);
    assert(closedSession.status === "closed", "Close response was not closed.");
    closedViaUi = true;
    await waitForVisibleText(page, "Session closed", { exact: true });
    await waitForSessionStatus(auth.client, createdSessionUuid, "closed");

    await clickToggleButton(page, "Closed");
    await setVisibleInputByPlaceholder(
      page,
      "Search by session ID or name...",
      sessionId,
    );
    await waitForVisibleText(page, sessionName, { exact: true });
    await waitForVisibleText(page, "closed", { exact: true });
    await assertRowButtonAbsent(page, sessionName, "Close session");
    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Unexpected Gateway session mutations: ${unexpectedMutations.join("; ")}`,
    );
    assert(
      browserMutations.length === 1,
      `Expected 1 Gateway session browser mutation, saw ${browserMutations.length}.`,
    );

    evidence.created_session = {
      id: createdSessionUuid,
      session_id: sessionId,
      name: sessionName,
      browser_closed: closedViaUi,
      request_rows: requestPayload.length,
    };

    evidence.detail_screenshot = DETAIL_SCREENSHOT_PATH;
    evidence.closed_list_screenshot = SCREENSHOT_PATH;
    evidence.expected_mutation_count = browserMutations.length;
  } catch (error) {
    caughtError = error;
    await page
      ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
  } finally {
    if (createdSessionUuid) {
      await auth.client
        .delete(
          apiPath("/agentcc/sessions/{id}/", { id: createdSessionUuid }),
          {
            okStatuses: [200, 204, 404],
          },
        )
        .then(() =>
          cleanupEvidence.push({
            cleanup: "public delete created Gateway session",
            id: createdSessionUuid,
            status: "passed",
          }),
        )
        .catch((error) =>
          cleanupEvidence.push({
            cleanup: "public delete created Gateway session",
            id: createdSessionUuid,
            status: "failed",
            error: error.message,
          }),
        );
    }
    if (createdSessionUuid || logIds.length) {
      await deleteGatewaySessionFixtureDb({
        sessionUuid: createdSessionUuid,
        organizationId: auth.organizationId,
        sessionId,
        logIds,
        evidence: cleanupEvidence,
      });
    }
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
          workspace_id: auth.workspaceId,
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
      `Gateway session cleanup failed: ${cleanupFailures
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
        workspace_id: auth.workspaceId,
        evidence,
        cleanup: cleanupEvidence,
        gateway_request_count: gatewayRequests.length,
        browser_mutations: browserMutations,
      },
      null,
      2,
    ),
  );
}

async function seedAgentccRequestLogsDb({
  organizationId,
  workspaceId,
  logIds,
  apiKeyId,
  marker,
  sharedSessionId,
  soloSessionId,
}) {
  const rows = [
    {
      id: logIds[0],
      requestId: `${marker}_success`,
      model: "gpt-4o-mini",
      provider: "openai",
      resolvedModel: "openai/gpt-4o-mini",
      latencyMs: 120,
      startedOffset: "30 minutes",
      inputTokens: 50,
      outputTokens: 70,
      totalTokens: 120,
      cost: "0.001200",
      statusCode: 200,
      isStream: false,
      isError: false,
      errorMessage: "",
      cacheHit: true,
      fallbackUsed: false,
      guardrailTriggered: false,
      userId: `${marker}_user_alpha`,
      sessionId: sharedSessionId,
      routingStrategy: "primary",
      metadata: { marker, lane: "success", tier: "gold" },
      requestBody: {
        messages: [{ role: "user", content: `${marker} success` }],
      },
      responseBody: { choices: [{ message: { content: "ok" } }] },
      requestHeaders: { "x-api-journey": marker },
      responseHeaders: { "x-request-outcome": "success" },
      guardrailResults: { checks: [] },
    },
    {
      id: logIds[1],
      requestId: `${marker}_error`,
      model: "gpt-4o",
      provider: "openai",
      resolvedModel: "openai/gpt-4o",
      latencyMs: 250,
      startedOffset: "20 minutes",
      inputTokens: 40,
      outputTokens: 20,
      totalTokens: 60,
      cost: "0.002500",
      statusCode: 503,
      isStream: false,
      isError: true,
      errorMessage: `${marker} provider unavailable`,
      cacheHit: false,
      fallbackUsed: false,
      guardrailTriggered: true,
      userId: `${marker}_user_beta`,
      sessionId: sharedSessionId,
      routingStrategy: "primary",
      metadata: { marker, lane: "error", retryable: true },
      requestBody: { messages: [{ role: "user", content: `${marker} error` }] },
      responseBody: { error: { code: "provider_unavailable" } },
      requestHeaders: { "x-api-journey": marker },
      responseHeaders: { "x-request-outcome": "error" },
      guardrailResults: {
        checks: [{ name: "toxicity", action: "monitor", triggered: true }],
      },
    },
    {
      id: logIds[2],
      requestId: `${marker}_other`,
      model: "claude-3-haiku",
      provider: "anthropic",
      resolvedModel: "anthropic/claude-3-haiku",
      latencyMs: 420,
      startedOffset: "10 minutes",
      inputTokens: 300,
      outputTokens: 400,
      totalTokens: 700,
      cost: "0.005500",
      statusCode: 201,
      isStream: true,
      isError: false,
      errorMessage: "",
      cacheHit: false,
      fallbackUsed: true,
      guardrailTriggered: false,
      userId: `${marker}_user_gamma`,
      sessionId: soloSessionId,
      routingStrategy: "fallback",
      metadata: { marker, lane: "other", fallback: true },
      requestBody: {
        messages: [{ role: "user", content: `${marker} other` }],
      },
      responseBody: { stream: true, chunks: 3 },
      requestHeaders: { "x-api-journey": marker },
      responseHeaders: { "x-request-outcome": "other" },
      guardrailResults: { checks: [] },
    },
  ];

  const values = rows
    .map(
      (row) => `(
        ${sqlUuid(row.id)},
        ${sqlUuid(organizationId)},
        ${sqlUuid(workspaceId)},
        ${sqlString(row.requestId)},
        ${sqlString(row.model)},
        ${sqlString(row.provider)},
        ${sqlString(row.resolvedModel)},
        ${row.latencyMs},
        now() - ${sqlString(row.startedOffset)}::interval,
        ${row.inputTokens},
        ${row.outputTokens},
        ${row.totalTokens},
        ${sqlString(row.cost)}::numeric,
        ${row.statusCode},
        ${row.isStream},
        ${row.isError},
        ${sqlString(row.errorMessage)},
        ${row.cacheHit},
        ${row.fallbackUsed},
        ${row.guardrailTriggered},
        ${sqlString(apiKeyId)},
        ${sqlString(row.userId)},
        ${sqlString(row.sessionId)},
        ${sqlString(row.routingStrategy)},
        ${sqlJson(row.metadata)},
        ${sqlJson(row.requestBody)},
        ${sqlJson(row.responseBody)},
        ${sqlJson(row.requestHeaders)},
        ${sqlJson(row.responseHeaders)},
        ${sqlJson(row.guardrailResults)},
        now(),
        now(),
        false,
        NULL
      )`,
    )
    .join(",\n");

  const sql = `
WITH stale AS (
  DELETE FROM agentcc_request_log
  WHERE request_id LIKE ${sqlString(`${marker}%`)}
    AND organization_id = ${sqlUuid(organizationId)}
  RETURNING id
),
inserted AS (
  INSERT INTO agentcc_request_log (
    id,
    organization_id,
    workspace_id,
    request_id,
    model,
    provider,
    resolved_model,
    latency_ms,
    started_at,
    input_tokens,
    output_tokens,
    total_tokens,
    cost,
    status_code,
    is_stream,
    is_error,
    error_message,
    cache_hit,
    fallback_used,
    guardrail_triggered,
    api_key_id,
    user_id,
    session_id,
    routing_strategy,
    metadata,
    request_body,
    response_body,
    request_headers,
    response_headers,
    guardrail_results,
    created_at,
    updated_at,
    deleted,
    deleted_at
  )
  VALUES ${values}
  RETURNING id
)
SELECT json_build_object(
  'inserted_count', (SELECT count(*) FROM inserted),
  'stale_deleted_count', (SELECT count(*) FROM stale)
);
`;
  const result = await runPostgresJson(sql);
  assert(
    Number(result.inserted_count) === rows.length,
    `Failed to seed Gateway request-log rows: ${JSON.stringify(result)}`,
  );
  return result;
}

async function cleanupStaleGatewaySessionsDb({ organizationId, evidence }) {
  const sql = `
WITH stale_sessions AS (
  SELECT id, session_id
  FROM agentcc_session
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND session_id LIKE ${sqlString(`${SESSION_PREFIX}%`)}
),
stale_logs AS (
  SELECT id
  FROM agentcc_request_log
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND session_id LIKE ${sqlString(`${SESSION_PREFIX}%`)}
),
deleted_logs AS (
  DELETE FROM agentcc_request_log
  USING stale_logs
  WHERE agentcc_request_log.id = stale_logs.id
  RETURNING agentcc_request_log.id
),
deleted_sessions AS (
  DELETE FROM agentcc_session
  USING stale_sessions
  WHERE agentcc_session.id = stale_sessions.id
  RETURNING agentcc_session.id
)
SELECT json_build_object(
  'deleted_log_count', (SELECT count(*) FROM deleted_logs),
  'deleted_session_count', (SELECT count(*) FROM deleted_sessions)
);
`;
  const result = await runPostgresJson(sql);
  if (
    Number(result.deleted_log_count) > 0 ||
    Number(result.deleted_session_count) > 0
  ) {
    evidence.push({
      cleanup: "stale UI Gateway sessions",
      status: "passed",
      result,
    });
  }
  return result;
}

async function deleteGatewaySessionFixtureDb({
  sessionUuid,
  organizationId,
  sessionId,
  logIds,
  evidence,
}) {
  const sessionFilter = sessionUuid
    ? `id = ${sqlUuid(sessionUuid)}`
    : `session_id = ${sqlString(sessionId)}`;
  const sql = `
WITH target_logs AS (
  SELECT id
  FROM agentcc_request_log
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND (
      id = ANY(${sqlUuidArray(logIds)})
      OR session_id IN (${sqlString(sessionId)}, ${sqlString(`${sessionId}_other`)})
    )
),
target_session AS (
  SELECT id
  FROM agentcc_session
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND ${sessionFilter}
),
deleted_logs AS (
  DELETE FROM agentcc_request_log
  USING target_logs
  WHERE agentcc_request_log.id = target_logs.id
  RETURNING agentcc_request_log.id
),
deleted_session AS (
  DELETE FROM agentcc_session
  USING target_session
  WHERE agentcc_session.id = target_session.id
  RETURNING agentcc_session.id
)
SELECT json_build_object(
  'deleted_log_count', (SELECT count(*) FROM deleted_logs),
  'deleted_session_count', (SELECT count(*) FROM deleted_session),
  'remaining_log_count',
    (SELECT count(*) FROM target_logs) - (SELECT count(*) FROM deleted_logs),
  'remaining_session_count',
    (SELECT count(*) FROM target_session) - (SELECT count(*) FROM deleted_session)
);
`;
  await runPostgresJson(sql)
    .then((result) => {
      evidence.push({
        cleanup: "hard delete Gateway session fixture",
        status:
          Number(result.remaining_log_count) === 0 &&
          Number(result.remaining_session_count) === 0
            ? "passed"
            : "failed",
        result,
      });
    })
    .catch((error) =>
      evidence.push({
        cleanup: "hard delete Gateway session fixture",
        status: "failed",
        error: error.message,
      }),
    );
}

function assertSessionStats(session, expected) {
  assert(
    Number(session?.stats?.request_count) === expected.request_count,
    "Gateway session request_count did not match seeded logs.",
  );
  assert(
    Number(session?.stats?.total_tokens) === expected.total_tokens,
    "Gateway session total_tokens did not match seeded logs.",
  );
  assert(
    Number(session?.stats?.total_cost).toFixed(6) === expected.total_cost,
    "Gateway session total_cost did not match seeded logs.",
  );
  assert(
    Number(session?.stats?.avg_latency_ms).toFixed(2) ===
      expected.avg_latency_ms,
    "Gateway session avg_latency_ms did not match seeded logs.",
  );
}

async function waitForSessionStatus(client, sessionUuid, expectedStatus) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    const detail = await client.get(
      apiPath("/agentcc/sessions/{id}/", { id: sessionUuid }),
    );
    if (detail?.status === expectedStatus) return detail;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Gateway session did not become ${expectedStatus}.`);
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
      localStorage.removeItem("agentcc_getting_started_dismissed");
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

async function waitForResponsesDuring(page, label, predicates, action) {
  try {
    return await Promise.all([
      ...predicates.map((predicate) =>
        page.waitForResponse(predicate, { timeout: 60000 }),
      ),
      action(),
    ]);
  } catch (error) {
    throw new Error(`${label} failed: ${error.message}`);
  }
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

async function clickTableRowByText(page, rowText, timeout = 30000) {
  await waitForVisibleText(page, rowText, { exact: true, timeout });
  const clicked = await page.evaluate((expectedText) => {
    const row = window
      .visibleElements("tr")
      .find((candidate) =>
        window.normalizeText(candidate.textContent).includes(expectedText),
      );
    if (!row) return false;
    window.dispatchClick(row);
    return true;
  }, rowText);
  assert(clicked, `Could not click table row: ${rowText}`);
}

async function clickRowButtonByTitle(page, rowText, title, timeout = 30000) {
  await waitForVisibleText(page, rowText, { exact: true, timeout });
  const clicked = await page.evaluate(
    ({ expectedRowText, expectedTitle }) => {
      const row = window
        .visibleElements("tr")
        .find((candidate) =>
          window.normalizeText(candidate.textContent).includes(expectedRowText),
        );
      if (!row) return false;
      const button = Array.from(row.querySelectorAll("button")).find(
        (candidate) =>
          candidate.getAttribute("title") === expectedTitle &&
          !candidate.disabled,
      );
      if (!button) return false;
      window.dispatchClick(button);
      return true;
    },
    { expectedRowText: rowText, expectedTitle: title },
  );
  assert(clicked, `Could not click row ${title} action for ${rowText}`);
}

async function assertRowButtonAbsent(page, rowText, title, timeout = 30000) {
  await waitForVisibleText(page, rowText, { exact: true, timeout });
  const absent = await page.evaluate(
    ({ expectedRowText, expectedTitle }) => {
      const row = window
        .visibleElements("tr")
        .find((candidate) =>
          window.normalizeText(candidate.textContent).includes(expectedRowText),
        );
      if (!row) return false;
      return !Array.from(row.querySelectorAll("button")).some(
        (candidate) => candidate.getAttribute("title") === expectedTitle,
      );
    },
    { expectedRowText: rowText, expectedTitle: title },
  );
  assert(absent, `Unexpected row ${title} action still existed for ${rowText}`);
}

async function clickToggleButton(page, label, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
  const clicked = await page.evaluate((expectedLabel) => {
    const button = window
      .visibleElements("button")
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedLabel &&
          !candidate.disabled,
      );
    if (!button) return false;
    window.dispatchClick(button);
    return true;
  }, label);
  assert(clicked, `Could not click toggle button: ${label}`);
}

async function responseResult(response) {
  const body = await response.json();
  return unwrapApiData(body);
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

function isAllowedSessionMutation(method, rawUrl, sessionUuid) {
  const url = new URL(rawUrl);
  if (!url.pathname.includes("/agentcc/sessions/")) return false;
  return (
    method === "POST" &&
    url.pathname.includes(`/agentcc/sessions/${sessionUuid}/close/`)
  );
}

function isSessionDetailUrl(rawUrl, sessionUuid) {
  const url = new URL(rawUrl);
  return url.pathname === `/agentcc/sessions/${sessionUuid}/`;
}

function isSessionRequestsUrl(rawUrl, sessionUuid) {
  const url = new URL(rawUrl);
  return url.pathname === `/agentcc/sessions/${sessionUuid}/requests/`;
}

function isGatewayApiUrl(url) {
  return url.includes("/agentcc/");
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
