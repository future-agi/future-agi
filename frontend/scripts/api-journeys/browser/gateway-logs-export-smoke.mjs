/* eslint-disable no-console */
import { Buffer } from "node:buffer";
import { execFile as execFileCallback } from "node:child_process";
import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
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
const SCREENSHOT_PATH = "/tmp/gateway-logs-export-smoke.png";
const DETAIL_SCREENSHOT_PATH = "/tmp/gateway-log-detail-export-smoke.png";
const SESSIONS_SCREENSHOT_PATH = "/tmp/gateway-log-sessions-export-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/gateway-logs-export-smoke-failure.png";
const LOG_PREFIX = "ui_gateway_log_";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const shortSuffix = suffix.slice(-8);
  const marker = `${LOG_PREFIX}${suffix}`;
  const apiKeyId = `${marker}_key`;
  const sharedSessionId = `${LOG_PREFIX}session_${shortSuffix}`;
  const soloSessionId = `${LOG_PREFIX}solo_${shortSuffix}`;
  const targetRequestId = `${marker}_target_error`;
  const otherRequestId = `${marker}_success`;
  const modelAlpha = `qa-log-${shortSuffix}-gpt`;
  const modelBeta = `qa-log-${shortSuffix}-claude`;
  const providerAlpha = `qa-log-${shortSuffix}-openai`;
  const providerBeta = `qa-log-${shortSuffix}-anthropic`;
  const downloadDir = path.join(
    "/tmp",
    `gateway-logs-downloads-${shortSuffix}`,
  );
  const logIds = [randomUUID(), randomUUID(), randomUUID()];
  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const gatewayRequests = [];
  const unexpectedMutations = [];
  const evidence = {
    api_key_id: apiKeyId,
    target_request_id: targetRequestId,
    shared_session_id: sharedSessionId,
    download_dir: downloadDir,
  };
  let browser = null;
  let page = null;
  let caughtError = null;

  await fs.rm(downloadDir, { recursive: true, force: true });
  await fs.mkdir(downloadDir, { recursive: true });
  await cleanupStaleGatewayLogsDb({
    organizationId: auth.organizationId,
    evidence: cleanupEvidence,
  });

  try {
    evidence.seeded_request_logs = await seedGatewayRequestLogsDb({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      logIds,
      marker,
      apiKeyId,
      sharedSessionId,
      soloSessionId,
      targetRequestId,
      otherRequestId,
      modelAlpha,
      modelBeta,
      providerAlpha,
      providerBeta,
    });

    evidence.api_preflight = await preflightRequestLogApis(auth, {
      apiKeyId,
      targetRequestId,
      otherRequestId,
      sharedSessionId,
    });

    browser = await puppeteer.launch({
      executablePath: browserExecutablePath(),
      headless: process.env.HEADLESS !== "0",
      defaultViewport: { width: 1440, height: 980 },
      args: ["--no-sandbox"],
    });
    page = await browser.newPage();
    await page.setBypassServiceWorker(true);
    await allowDownloads(page, downloadDir);
    await installRuntimeConfig(page, auth);
    await installBrowserState(page, auth);

    page.on("request", (request) => {
      const url = request.url();
      if (!isGatewayApiUrl(url)) return;
      gatewayRequests.push(`${request.method()} ${url}`);
      if (MUTATION_METHODS.has(request.method())) {
        unexpectedMutations.push(`${request.method()} ${url}`);
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
      "initial Gateway logs load",
      requestLogsResponse({ api_key_id: apiKeyId }),
      () =>
        page.goto(
          `${APP_BASE}/dashboard/gateway/logs?api_key_id=${encodeURIComponent(
            apiKeyId,
          )}`,
          { waitUntil: "domcontentloaded" },
        ),
    );
    await waitForPath(page, "/dashboard/gateway/logs");

    for (const label of [
      "Request Logs",
      "Export",
      "Filters",
      "All",
      "Errors",
      "Slow (>1s latency)",
      "Cache Hits",
      "Guardrails",
      "Requests",
      "Sessions",
      "Timestamp",
      "Model",
      "Provider",
      "Status",
      "Latency",
      "Cost",
      "Tokens",
      "Session ID",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    await waitForVisibleText(page, modelAlpha);
    await waitForVisibleText(page, modelBeta);
    await waitForVisibleText(page, "503", { exact: true });
    await waitForVisibleText(page, "429", { exact: true });

    await waitForResponseDuring(
      page,
      "open Gateway request-log detail",
      requestLogDetailResponse(logIds[1]),
      () => clickVisibleText(page, modelAlpha),
    );
    await waitForVisibleText(page, targetRequestId.slice(0, 20));
    await waitForVisibleText(page, "480ms", { exact: true });
    await waitForVisibleText(page, "80 in / 120 out", { exact: true });
    await waitForVisibleText(page, "Guardrails", { exact: true });
    await clickTab(page, "Request");
    await waitForVisibleText(page, `${marker} target`);
    await waitForVisibleText(page, "Request Headers", { exact: true });
    await clickTab(page, "Guardrails");
    await waitForVisibleText(page, "toxicity", { exact: true });
    await waitForVisibleText(page, "Latency: 14ms", { exact: true });
    await page.screenshot({ path: DETAIL_SCREENSHOT_PATH, fullPage: true });
    await page.keyboard.press("Escape");
    await waitForNoVisibleText(page, "Request Headers", { exact: true });

    await waitForResponseDuring(
      page,
      "open request-log Sessions tab",
      requestLogSessionsResponse({ api_key_id: apiKeyId }),
      () => clickTab(page, "Sessions"),
    );
    await waitForVisibleText(page, "Sort by:", { exact: true });
    await waitForVisibleText(page, "2 requests", { exact: true });
    await waitForVisibleText(page, "1 errors", { exact: true });
    await waitForResponseDuring(
      page,
      "sort request-log Sessions by request count",
      requestLogSessionsResponse({
        api_key_id: apiKeyId,
        ordering: "-request_count",
      }),
      () => clickToggleButton(page, "Most Requests"),
    );
    await waitForResponseDuring(
      page,
      "expand request-log session",
      sessionDetailResponse(sharedSessionId),
      () => clickVisibleText(page, `${sharedSessionId.slice(0, 16)}...`),
    );
    await waitForVisibleText(page, targetRequestId.slice(0, 12));
    await waitForVisibleText(page, otherRequestId.slice(0, 12));
    await page.screenshot({ path: SESSIONS_SCREENSHOT_PATH, fullPage: true });

    await clickTab(page, "Requests");
    await waitForVisibleText(page, "Timestamp", { exact: true });
    await waitForVisibleText(page, modelAlpha);
    await waitForResponseDuring(
      page,
      "search request-log table",
      requestLogSearchResponse({ api_key_id: apiKeyId, q: targetRequestId }),
      () =>
        setVisibleInputByPlaceholder(
          page,
          "Search model, provider, request ID...",
          targetRequestId,
        ),
    );
    await waitForVisibleText(page, modelAlpha);
    await waitForNoVisibleText(page, modelBeta);

    const csvResponse = await waitForResponseDuring(
      page,
      "export searched request logs as CSV",
      exportResponse({
        api_key_id: apiKeyId,
        search: targetRequestId,
        export_format: "csv",
      }),
      async () => {
        await openExportMenu(page);
        await clickMenuItem(page, "Export CSV");
      },
    );
    assert(
      csvResponse.status() === 200,
      "CSV browser export did not return 200.",
    );
    const csvPath = await waitForDownloadedFile(downloadDir, ".csv");
    const csvText = await fs.readFile(csvPath, "utf8");
    assertDownloadedCsv(csvText, { targetRequestId, otherRequestId });

    const jsonResponse = await waitForResponseDuring(
      page,
      "export searched request logs as JSON",
      exportResponse({
        api_key_id: apiKeyId,
        search: targetRequestId,
        export_format: "json",
      }),
      async () => {
        await openExportMenu(page);
        await clickMenuItem(page, "Export JSON");
      },
    );
    assert(
      jsonResponse.status() === 200,
      "JSON browser export did not return 200.",
    );
    const jsonPath = await waitForDownloadedFile(downloadDir, ".json", {
      previousPath: csvPath,
    });
    const ndjsonText = await fs.readFile(jsonPath, "utf8");
    assertDownloadedNdjson(ndjsonText, { targetRequestId, otherRequestId });

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshots = [
      DETAIL_SCREENSHOT_PATH,
      SESSIONS_SCREENSHOT_PATH,
      SCREENSHOT_PATH,
    ];
    evidence.downloads = {
      csv: csvPath,
      json: jsonPath,
      csv_bytes: Buffer.byteLength(csvText),
      json_lines: ndjsonText.trim().split(/\n/).filter(Boolean).length,
    };

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Read-only Gateway logs smoke fired mutations: ${unexpectedMutations.join(
        "; ",
      )}`,
    );
  } catch (error) {
    caughtError = error;
    await page
      ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
  } finally {
    await deleteGatewayLogsFixtureDb({
      organizationId: auth.organizationId,
      logIds,
      apiKeyId,
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
          workspace_id: auth.workspaceId,
          evidence,
          cleanup: cleanupEvidence,
          api_failures: apiFailures,
          page_errors: pageErrors,
          gateway_requests: gatewayRequests,
          unexpected_mutations: unexpectedMutations,
          failure_screenshot: FAILURE_SCREENSHOT_PATH,
        },
        null,
        2,
      ),
    );
    if (caughtError) throw caughtError;
    throw new Error(
      `Gateway logs cleanup failed: ${cleanupFailures
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
      },
      null,
      2,
    ),
  );
}

async function preflightRequestLogApis(
  auth,
  { apiKeyId, targetRequestId, otherRequestId, sharedSessionId },
) {
  const list = await auth.client.get(
    withQuery(apiPath("/agentcc/request-logs/"), {
      api_key_id: apiKeyId,
      limit: 25,
    }),
    { unwrap: false },
  );
  const listRows = responseRows(list);
  assert(listRows.length === 3, "Request-log list did not return seed rows.");

  const search = await auth.client.get(
    withQuery(apiPath("/agentcc/request-logs/search/"), {
      api_key_id: apiKeyId,
      q: targetRequestId,
    }),
    { unwrap: false },
  );
  const searchRows = responseRows(search);
  assert(
    searchRows.length === 1 && searchRows[0].request_id === targetRequestId,
    "Request-log search did not isolate the target row.",
  );

  const sessions = await auth.client.get(
    withQuery(apiPath("/agentcc/request-logs/sessions/"), {
      api_key_id: apiKeyId,
      ordering: "-request_count",
    }),
    { unwrap: false },
  );
  const sessionRows = responseRows(sessions);
  assert(
    sessionRows[0]?.session_id === sharedSessionId &&
      Number(sessionRows[0]?.request_count) === 2,
    "Request-log sessions did not sort seeded shared session first.",
  );

  const csvText = await fetchExportText(auth, {
    api_key_id: apiKeyId,
    search: targetRequestId,
    export_format: "csv",
  });
  assertDownloadedCsv(csvText, { targetRequestId, otherRequestId });

  const ndjsonText = await fetchExportText(auth, {
    api_key_id: apiKeyId,
    search: targetRequestId,
    export_format: "json",
  });
  assertDownloadedNdjson(ndjsonText, { targetRequestId, otherRequestId });

  return {
    list_count: listRows.length,
    search_count: searchRows.length,
    first_session: sessionRows[0]?.session_id,
    csv_bytes: Buffer.byteLength(csvText),
    ndjson_rows: ndjsonText.trim().split(/\n/).filter(Boolean).length,
  };
}

function responseRows(data) {
  return asArray(data?.result ?? data);
}

async function fetchExportText(auth, query) {
  const url = new URL(
    withQuery(apiPath("/agentcc/request-logs/export/"), query),
    auth.apiBase,
  );
  const response = await fetch(url, {
    headers: {
      Authorization: `Bearer ${auth.tokens.access}`,
      "X-Organization-Id": auth.organizationId,
      "X-Workspace-Id": auth.workspaceId,
    },
  });
  const text = await response.text();
  assert(
    response.status === 200,
    `Export preflight failed with HTTP ${response.status}: ${text}`,
  );
  return text;
}

async function seedGatewayRequestLogsDb({
  organizationId,
  workspaceId,
  logIds,
  marker,
  apiKeyId,
  sharedSessionId,
  soloSessionId,
  targetRequestId,
  otherRequestId,
  modelAlpha,
  modelBeta,
  providerAlpha,
  providerBeta,
}) {
  const rows = [
    {
      id: logIds[0],
      requestId: otherRequestId,
      model: modelAlpha,
      provider: providerAlpha,
      resolvedModel: `${providerAlpha}/${modelAlpha}`,
      latencyMs: 120,
      startedOffset: "18 minutes",
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
      metadata: { marker, lane: "success" },
      requestBody: {
        messages: [{ role: "user", content: `${marker} success` }],
      },
      responseBody: { choices: [{ message: { content: "ok" } }] },
      requestHeaders: { "x-api-journey": marker },
      responseHeaders: { "x-request-outcome": "success" },
      guardrailResults: [],
    },
    {
      id: logIds[1],
      requestId: targetRequestId,
      model: modelAlpha,
      provider: providerAlpha,
      resolvedModel: `${providerAlpha}/${modelAlpha}`,
      latencyMs: 480,
      startedOffset: "12 minutes",
      inputTokens: 80,
      outputTokens: 120,
      totalTokens: 200,
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
      metadata: { marker, lane: "target-error" },
      requestBody: {
        messages: [{ role: "user", content: `${marker} target` }],
      },
      responseBody: { error: { code: "provider_unavailable" } },
      requestHeaders: { "x-api-journey": marker },
      responseHeaders: { "x-request-outcome": "error" },
      guardrailResults: [
        {
          name: "toxicity",
          action: "warn",
          score: 0.71,
          threshold: 0.65,
          latency_ms: 14,
        },
      ],
    },
    {
      id: logIds[2],
      requestId: `${marker}_other_error`,
      model: modelBeta,
      provider: providerBeta,
      resolvedModel: `${providerBeta}/${modelBeta}`,
      latencyMs: 900,
      startedOffset: "8 minutes",
      inputTokens: 140,
      outputTokens: 160,
      totalTokens: 300,
      cost: "0.003000",
      statusCode: 429,
      isStream: true,
      isError: true,
      errorMessage: `${marker} rate limited`,
      cacheHit: false,
      fallbackUsed: true,
      guardrailTriggered: false,
      userId: `${marker}_user_gamma`,
      sessionId: soloSessionId,
      routingStrategy: "fallback",
      metadata: { marker, lane: "other-error", fallback: true },
      requestBody: { messages: [{ role: "user", content: `${marker} limit` }] },
      responseBody: { error: { code: "rate_limited" } },
      requestHeaders: { "x-api-journey": marker },
      responseHeaders: { "x-request-outcome": "rate-limited" },
      guardrailResults: [],
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

async function cleanupStaleGatewayLogsDb({ organizationId, evidence }) {
  const sql = `
WITH stale_logs AS (
  SELECT id
  FROM agentcc_request_log
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND request_id LIKE ${sqlString(`${LOG_PREFIX}%`)}
),
deleted_logs AS (
  DELETE FROM agentcc_request_log
  USING stale_logs
  WHERE agentcc_request_log.id = stale_logs.id
  RETURNING agentcc_request_log.id
)
SELECT json_build_object(
  'deleted_log_count', (SELECT count(*) FROM deleted_logs)
);
`;
  const result = await runPostgresJson(sql);
  if (Number(result.deleted_log_count) > 0) {
    evidence.push({
      cleanup: "stale UI Gateway request logs",
      status: "passed",
      result,
    });
  }
  return result;
}

async function deleteGatewayLogsFixtureDb({
  organizationId,
  logIds,
  apiKeyId,
  marker,
  evidence,
}) {
  const sql = `
WITH target_logs AS (
  SELECT id
  FROM agentcc_request_log
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND (
      id = ANY(${sqlUuidArray(logIds)})
      OR api_key_id = ${sqlString(apiKeyId)}
      OR request_id LIKE ${sqlString(`${marker}%`)}
    )
),
deleted_logs AS (
  DELETE FROM agentcc_request_log
  USING target_logs
  WHERE agentcc_request_log.id = target_logs.id
  RETURNING agentcc_request_log.id
)
SELECT json_build_object(
  'deleted_log_count', (SELECT count(*) FROM deleted_logs),
  'remaining_log_count',
    (SELECT count(*) FROM target_logs) - (SELECT count(*) FROM deleted_logs)
);
`;
  await runPostgresJson(sql)
    .then((result) => {
      evidence.push({
        cleanup: "hard delete Gateway logs fixture",
        status: Number(result.remaining_log_count) === 0 ? "passed" : "failed",
        result,
      });
    })
    .catch((error) =>
      evidence.push({
        cleanup: "hard delete Gateway logs fixture",
        status: "failed",
        error: error.message,
      }),
    );
}

function assertDownloadedCsv(text, { targetRequestId, otherRequestId }) {
  const lines = text.trim().split(/\r?\n/).filter(Boolean);
  assert(
    lines.length === 2,
    `Expected header + one CSV row, saw ${lines.length}.`,
  );
  assert(lines[0].startsWith("started_at,request_id,"), "CSV header mismatch.");
  assert(lines[1].includes(targetRequestId), "CSV export missing target row.");
  assert(
    !text.includes(otherRequestId),
    "CSV export included non-searched row.",
  );
}

function assertDownloadedNdjson(text, { targetRequestId, otherRequestId }) {
  const lines = text.trim().split(/\n/).filter(Boolean);
  assert(lines.length === 1, `Expected one NDJSON row, saw ${lines.length}.`);
  const row = JSON.parse(lines[0]);
  assert(row.request_id === targetRequestId, "NDJSON target request mismatch.");
  assert(!text.includes(otherRequestId), "NDJSON included non-searched row.");
}

async function allowDownloads(page, downloadDir) {
  const client = await page.target().createCDPSession();
  await client
    .send("Page.setDownloadBehavior", {
      behavior: "allow",
      downloadPath: downloadDir,
    })
    .catch(() =>
      client.send("Browser.setDownloadBehavior", {
        behavior: "allow",
        downloadPath: downloadDir,
      }),
    );
}

async function waitForDownloadedFile(
  downloadDir,
  extension,
  { previousPath = null, timeout = 30000 } = {},
) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const entries = await fs.readdir(downloadDir).catch(() => []);
    const matches = entries
      .filter(
        (entry) =>
          entry.endsWith(extension) &&
          !entry.endsWith(".crdownload") &&
          path.join(downloadDir, entry) !== previousPath,
      )
      .sort();
    if (matches.length > 0) {
      const fullPath = path.join(downloadDir, matches[matches.length - 1]);
      const stats = await fs.stat(fullPath);
      if (stats.size > 0) return fullPath;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(
    `Timed out waiting for ${extension} download in ${downloadDir}`,
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

function requestLogsResponse(query = {}) {
  return gatewayGetResponse("/agentcc/request-logs/", query);
}

function requestLogSearchResponse(query = {}) {
  return gatewayGetResponse("/agentcc/request-logs/search/", query);
}

function requestLogSessionsResponse(query = {}) {
  return gatewayGetResponse("/agentcc/request-logs/sessions/", query);
}

function exportResponse(query = {}) {
  return gatewayGetResponse("/agentcc/request-logs/export/", query);
}

function requestLogDetailResponse(logId) {
  return (response) => {
    const url = new URL(response.url());
    return (
      url.pathname === `/agentcc/request-logs/${logId}/` &&
      response.request().method() === "GET" &&
      response.status() < 400
    );
  };
}

function sessionDetailResponse(sessionId) {
  return (response) => {
    const url = new URL(response.url());
    return (
      url.pathname === `/agentcc/request-logs/sessions/${sessionId}/` &&
      response.request().method() === "GET" &&
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

async function clickVisibleText(
  page,
  text,
  { exact = false, timeout = 30000 } = {},
) {
  await waitForVisibleText(page, text, { exact, timeout });
  const clicked = await page.evaluate(
    ({ expectedText, exactMatch }) => {
      const matches = window
        .visibleElements()
        .filter((candidate) => {
          const textContent = window.normalizeText(candidate.textContent);
          return exactMatch
            ? textContent === expectedText
            : textContent.includes(expectedText);
        })
        .sort(
          (a, b) =>
            window.normalizeText(a.textContent).length -
            window.normalizeText(b.textContent).length,
        );
      for (const element of matches) {
        const target =
          element.closest(
            'button,[role="button"],[role="tab"],[role="menuitem"],li,tr,.MuiAccordionSummary-root',
          ) || element;
        if (
          target.disabled ||
          target.getAttribute("aria-disabled") === "true"
        ) {
          continue;
        }
        window.dispatchClick(target);
        return true;
      }
      return false;
    },
    { expectedText: text, exactMatch: exact },
  );
  assert(clicked, `Could not click visible text: ${text}`);
}

async function clickTab(page, label, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
  const clicked = await page.evaluate((expectedLabel) => {
    const tab = window
      .visibleElements('button[role="tab"]')
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedLabel &&
          !candidate.disabled,
      );
    if (!tab) return false;
    window.dispatchClick(tab);
    return true;
  }, label);
  assert(clicked, `Could not click tab: ${label}`);
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

async function openExportMenu(page, timeout = 30000) {
  await waitForVisibleText(page, "Export", { exact: true, timeout });
  const clicked = await page.evaluate(() => {
    const button = window
      .visibleElements("button")
      .find(
        (candidate) => window.normalizeText(candidate.textContent) === "Export",
      );
    if (!button || button.disabled) return false;
    button.click();
    return true;
  });
  assert(clicked, "Could not click Export button.");
  await waitForVisibleText(page, "Export CSV", { exact: true, timeout });
}

async function clickMenuItem(page, label, timeout = 30000) {
  await waitForVisibleText(page, label, { exact: true, timeout });
  const clicked = await page.evaluate((expectedLabel) => {
    const item = window
      .visibleElements('[role="menuitem"],li')
      .find(
        (candidate) =>
          window.normalizeText(candidate.textContent) === expectedLabel &&
          candidate.getAttribute("aria-disabled") !== "true",
      );
    if (!item) return false;
    item.click();
    return true;
  }, label);
  assert(clicked, `Could not click menu item: ${label}`);
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
