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
const SCREENSHOT_PATH = "/tmp/gateway-analytics-smoke.png";
const FAILURE_SCREENSHOT_PATH = "/tmp/gateway-analytics-smoke-failure.png";
const ANALYTICS_PREFIX = "ui_gateway_analytics_";
const MUTATION_METHODS = new Set(["POST", "PATCH", "PUT", "DELETE"]);

async function main() {
  requireMutations();
  const auth = await createAuthenticatedContext();
  const suffix = auth.runId.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const shortSuffix = suffix.slice(-8);
  const marker = `${ANALYTICS_PREFIX}${suffix}`;
  const apiKeyId = `${marker}_key`;
  const modelAlpha = `qa-an-${shortSuffix}-gpt`;
  const modelBeta = `qa-an-${shortSuffix}-claude`;
  const providerAlpha = `qa-an-${shortSuffix}-openai`;
  const providerBeta = `qa-an-${shortSuffix}-anthropic`;
  const sessionId = `${ANALYTICS_PREFIX}session_${shortSuffix}`;
  const logIds = [randomUUID(), randomUUID(), randomUUID()];
  const cleanupEvidence = [];
  const apiFailures = [];
  const pageErrors = [];
  const gatewayRequests = [];
  const unexpectedMutations = [];
  const evidence = {
    model_alpha: modelAlpha,
    model_beta: modelBeta,
    provider_alpha: providerAlpha,
    provider_beta: providerBeta,
    api_key_id: apiKeyId,
  };
  let browser = null;
  let page = null;
  let caughtError = null;

  await cleanupStaleGatewayAnalyticsDb({
    organizationId: auth.organizationId,
    evidence: cleanupEvidence,
  });

  try {
    evidence.seeded_request_logs = await seedAgentccAnalyticsLogsDb({
      organizationId: auth.organizationId,
      workspaceId: auth.workspaceId,
      logIds,
      marker,
      apiKeyId,
      sessionId,
      modelAlpha,
      modelBeta,
      providerAlpha,
      providerBeta,
    });

    const range = {
      start: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
      end: new Date(Date.now() + 60 * 1000).toISOString(),
    };
    evidence.api_preflight = await preflightAnalyticsApis(auth.client, {
      range,
      apiKeyId,
      modelAlpha,
      modelBeta,
      providerAlpha,
      providerBeta,
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

    await waitForResponsesDuring(
      page,
      "initial Gateway analytics load",
      [
        analyticsResponse("/agentcc/analytics/overview/"),
        analyticsResponse("/agentcc/analytics/usage-timeseries/"),
      ],
      () =>
        page.goto(`${APP_BASE}/dashboard/gateway/analytics`, {
          waitUntil: "domcontentloaded",
        }),
    );
    await waitForPath(page, "/dashboard/gateway/analytics");

    for (const label of [
      "Analytics",
      "Total Requests",
      "Total Cost",
      "Avg Latency",
      "Error Rate",
      "Cache Hit Rate",
      "Usage",
      "Cost",
      "Latency",
      "Errors",
      "Models",
      "Requests Over Time",
      "Tokens Over Time",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    await waitForResponseDuring(
      page,
      "switch Analytics time range to 1H",
      analyticsResponse("/agentcc/analytics/overview/"),
      () => clickToggleButton(page, "1H"),
    );
    await waitForNoVisibleText(page, "Invalid Date");
    await waitForNoVisibleText(page, "undefined");

    await waitForResponseDuring(
      page,
      "group usage by model",
      analyticsResponse("/agentcc/analytics/usage-timeseries/", {
        group_by: "model",
      }),
      () => clickToggleButton(page, "Model"),
    );
    await waitForVisibleText(page, modelAlpha);
    await waitForVisibleText(page, modelBeta);

    await waitForResponseDuring(
      page,
      "group usage by provider",
      analyticsResponse("/agentcc/analytics/usage-timeseries/", {
        group_by: "provider",
      }),
      () => clickToggleButton(page, "Provider"),
    );
    await waitForVisibleText(page, providerAlpha);
    await waitForVisibleText(page, providerBeta);

    await waitForResponseDuring(
      page,
      "open cost analytics tab",
      analyticsResponse("/agentcc/analytics/cost-breakdown/"),
      () => clickTab(page, "Cost"),
    );
    await waitForPath(page, "/dashboard/gateway/analytics/cost");
    await waitForVisibleText(page, "Cost by Model", { exact: true });
    await waitForVisibleText(page, "Cost Distribution", { exact: true });
    await waitForVisibleText(page, modelAlpha);
    await waitForVisibleText(page, modelBeta);

    await waitForResponseDuring(
      page,
      "group cost by provider",
      analyticsResponse("/agentcc/analytics/cost-breakdown/", {
        group_by: "provider",
      }),
      () => clickToggleButton(page, "Provider"),
    );
    await waitForVisibleText(page, providerAlpha);
    await waitForVisibleText(page, providerBeta);

    await waitForResponseDuring(
      page,
      "open latency analytics tab",
      analyticsResponse("/agentcc/analytics/latency-stats/"),
      () => clickTab(page, "Latency"),
    );
    await waitForPath(page, "/dashboard/gateway/analytics/latency");
    for (const label of [
      "P50",
      "P95",
      "P99",
      "Average",
      "Latency Percentiles Over Time",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    await assertVisibleTextAbsentFromNearestCard(page, "P50", "--");
    await assertVisibleTextAbsentFromNearestCard(page, "P95", "--");

    await waitForResponseDuring(
      page,
      "open error analytics tab",
      analyticsResponse("/agentcc/analytics/error-breakdown/"),
      () => clickTab(page, "Errors"),
    );
    await waitForPath(page, "/dashboard/gateway/analytics/errors");
    for (const label of [
      "Total Errors:",
      "Error Rate:",
      "Error Rate Over Time",
      "Error Breakdown",
      "Top Errors",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }
    await assertVisibleTextAbsentFromNearestStack(page, "Total Errors:", "--");

    await waitForResponseDuring(
      page,
      "group errors by provider",
      analyticsResponse("/agentcc/analytics/error-breakdown/", {
        group_by: "provider",
      }),
      () => clickToggleButton(page, "Provider"),
    );
    await waitForVisibleText(page, providerAlpha);
    await waitForVisibleText(page, providerBeta);

    await waitForResponseDuring(
      page,
      "open model comparison tab",
      analyticsResponse("/agentcc/analytics/model-comparison/"),
      () => clickTab(page, "Models"),
    );
    await waitForPath(page, "/dashboard/gateway/analytics/models");
    await waitForInputPlaceholder(page, "Filter models (comma-separated)...");
    await waitForVisibleText(page, modelAlpha);
    await waitForVisibleText(page, modelBeta);
    for (const label of [
      "Model",
      "Provider",
      "Requests",
      "Avg Latency",
      "P95 Latency",
      "Error Rate",
      "Cost",
      "Cache Hit Rate",
    ]) {
      await waitForVisibleText(page, label, { exact: true });
    }

    await waitForResponseDuring(
      page,
      "filter model comparison",
      analyticsResponse("/agentcc/analytics/model-comparison/", {
        models: modelAlpha,
      }),
      () =>
        setVisibleInputByPlaceholder(
          page,
          "Filter models (comma-separated)...",
          modelAlpha,
        ),
    );
    await waitForVisibleText(page, "1 model", { exact: true });
    await waitForVisibleText(page, modelAlpha);

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
    evidence.screenshot = SCREENSHOT_PATH;

    assert(apiFailures.length === 0, `API failures: ${apiFailures.join("; ")}`);
    assert(pageErrors.length === 0, `Page errors: ${pageErrors.join("; ")}`);
    assert(
      unexpectedMutations.length === 0,
      `Read-only Gateway analytics smoke fired mutations: ${unexpectedMutations.join(
        "; ",
      )}`,
    );
  } catch (error) {
    caughtError = error;
    await page
      ?.screenshot({ path: FAILURE_SCREENSHOT_PATH, fullPage: true })
      .catch(() => null);
  } finally {
    await deleteGatewayAnalyticsFixtureDb({
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
      `Gateway analytics cleanup failed: ${cleanupFailures
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

async function preflightAnalyticsApis(
  client,
  { range, apiKeyId, modelAlpha, modelBeta, providerAlpha, providerBeta },
) {
  const query = { ...range, api_key_id: apiKeyId };
  const overview = await client.get(
    withQuery(apiPath("/agentcc/analytics/overview/"), query),
  );
  assert(
    Number(overview?.total_requests?.value) === 3,
    "Analytics overview did not return seeded request count.",
  );
  assert(
    Number(overview?.total_tokens?.value) === 800,
    "Analytics overview did not return seeded token total.",
  );
  assert(
    Number(overview?.total_cost?.value).toFixed(2) === "191.34",
    "Analytics overview did not return seeded cost total.",
  );
  assert(
    Number(overview?.avg_latency_ms?.value).toFixed(2) === "500.00",
    "Analytics overview did not return seeded average latency.",
  );
  assert(
    Number(overview?.error_rate?.value).toFixed(2) === "66.67",
    "Analytics overview did not return seeded error rate.",
  );
  assert(
    Number(overview?.cache_hit_rate?.value).toFixed(2) === "33.33",
    "Analytics overview did not return seeded cache hit rate.",
  );

  const usageByModel = await client.get(
    withQuery(apiPath("/agentcc/analytics/usage-timeseries/"), {
      ...query,
      granularity: "hour",
      group_by: "model",
    }),
  );
  assert(usageByModel.group_by === "model", "Usage group_by was not model.");
  assert(
    sumRequestCount(usageByModel.groups?.[modelAlpha]) === 2 &&
      sumRequestCount(usageByModel.groups?.[modelBeta]) === 1,
    "Usage timeseries model grouping did not match seeded rows.",
  );

  const usageByProvider = await client.get(
    withQuery(apiPath("/agentcc/analytics/usage-timeseries/"), {
      ...query,
      granularity: "hour",
      group_by: "provider",
    }),
  );
  assert(
    sumRequestCount(usageByProvider.groups?.[providerAlpha]) === 2 &&
      sumRequestCount(usageByProvider.groups?.[providerBeta]) === 1,
    "Usage timeseries provider grouping did not match seeded rows.",
  );

  const cost = await client.get(
    withQuery(apiPath("/agentcc/analytics/cost-breakdown/"), {
      ...query,
      group_by: "provider",
      top_n: 10,
    }),
  );
  assert(
    Number(cost?.total_cost).toFixed(2) === "191.34",
    "Cost breakdown total did not match seeded rows.",
  );
  assert(
    cost.breakdown?.some((row) => row.name === providerAlpha) &&
      cost.breakdown?.some((row) => row.name === providerBeta),
    "Cost breakdown did not include seeded providers.",
  );

  const latency = await client.get(
    withQuery(apiPath("/agentcc/analytics/latency-stats/"), {
      ...query,
      granularity: "hour",
    }),
  );
  assert(
    Number(latency?.summary?.total_requests) === 3,
    "Latency summary did not return seeded request count.",
  );
  assert(
    Number(latency?.summary?.avg_ms).toFixed(2) === "500.00",
    "Latency summary did not return seeded average.",
  );
  assert(
    Number(latency?.summary?.p50_ms).toFixed(2) === "480.00" &&
      Number(latency?.summary?.p95_ms).toFixed(2) === "900.00",
    "Latency percentiles did not match seeded rows.",
  );

  const errors = await client.get(
    withQuery(apiPath("/agentcc/analytics/error-breakdown/"), {
      ...query,
      granularity: "hour",
      group_by: "status_code",
    }),
  );
  assert(
    Number(errors?.total_errors) === 2 &&
      Number(errors?.overall_error_rate).toFixed(2) === "66.67",
    "Error breakdown did not return seeded error rate.",
  );
  assert(
    errors.breakdown?.some((row) => row.name === "503") &&
      errors.breakdown?.some((row) => row.name === "429"),
    "Error breakdown did not include seeded status codes.",
  );

  const models = await client.get(
    withQuery(apiPath("/agentcc/analytics/model-comparison/"), query),
  );
  assert(
    models.models?.some((row) => row.model === modelAlpha) &&
      models.models?.some((row) => row.model === modelBeta),
    "Model comparison did not include seeded models.",
  );

  const filteredModel = await client.get(
    withQuery(apiPath("/agentcc/analytics/model-comparison/"), {
      ...query,
      models: modelAlpha,
    }),
  );
  assert(
    filteredModel.models?.length === 1 &&
      filteredModel.models[0].model === modelAlpha &&
      Number(filteredModel.models[0].request_count) === 2,
    "Model comparison filter did not isolate the seeded model.",
  );

  return {
    overview: {
      total_requests: overview.total_requests.value,
      total_cost: overview.total_cost.value,
      avg_latency_ms: overview.avg_latency_ms.value,
      error_rate: overview.error_rate.value,
      cache_hit_rate: overview.cache_hit_rate.value,
    },
    usage_by_model: {
      [modelAlpha]: sumRequestCount(usageByModel.groups?.[modelAlpha]),
      [modelBeta]: sumRequestCount(usageByModel.groups?.[modelBeta]),
    },
    cost_total: cost.total_cost,
    latency_summary: latency.summary,
    total_errors: errors.total_errors,
    model_count: models.models.length,
  };
}

async function seedAgentccAnalyticsLogsDb({
  organizationId,
  workspaceId,
  logIds,
  marker,
  apiKeyId,
  sessionId,
  modelAlpha,
  modelBeta,
  providerAlpha,
  providerBeta,
}) {
  const rows = [
    {
      id: logIds[0],
      requestId: `${marker}_success`,
      model: modelAlpha,
      provider: providerAlpha,
      resolvedModel: `${providerAlpha}/${modelAlpha}`,
      latencyMs: 120,
      startedOffset: "15 minutes",
      inputTokens: 120,
      outputTokens: 180,
      totalTokens: 300,
      cost: "12.340000",
      statusCode: 200,
      isStream: false,
      isError: false,
      errorMessage: "",
      cacheHit: true,
      fallbackUsed: false,
      guardrailTriggered: false,
      userId: `${marker}_user_alpha`,
      sessionId,
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
      requestId: `${marker}_server_error`,
      model: modelAlpha,
      provider: providerAlpha,
      resolvedModel: `${providerAlpha}/${modelAlpha}`,
      latencyMs: 480,
      startedOffset: "12 minutes",
      inputTokens: 80,
      outputTokens: 120,
      totalTokens: 200,
      cost: "56.780000",
      statusCode: 503,
      isStream: false,
      isError: true,
      errorMessage: `${marker} provider unavailable`,
      cacheHit: false,
      fallbackUsed: false,
      guardrailTriggered: true,
      userId: `${marker}_user_beta`,
      sessionId,
      routingStrategy: "primary",
      metadata: { marker, lane: "server-error" },
      requestBody: { messages: [{ role: "user", content: `${marker} error` }] },
      responseBody: { error: { code: "provider_unavailable" } },
      requestHeaders: { "x-api-journey": marker },
      responseHeaders: { "x-request-outcome": "error" },
      guardrailResults: [
        { name: "toxicity", action: "monitor", triggered: true },
      ],
    },
    {
      id: logIds[2],
      requestId: `${marker}_rate_limited`,
      model: modelBeta,
      provider: providerBeta,
      resolvedModel: `${providerBeta}/${modelBeta}`,
      latencyMs: 900,
      startedOffset: "9 minutes",
      inputTokens: 140,
      outputTokens: 160,
      totalTokens: 300,
      cost: "122.220000",
      statusCode: 429,
      isStream: true,
      isError: true,
      errorMessage: `${marker} rate limited`,
      cacheHit: false,
      fallbackUsed: true,
      guardrailTriggered: false,
      userId: `${marker}_user_gamma`,
      sessionId,
      routingStrategy: "fallback",
      metadata: { marker, lane: "rate-limited", fallback: true },
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
    `Failed to seed Gateway analytics request-log rows: ${JSON.stringify(
      result,
    )}`,
  );
  return result;
}

async function cleanupStaleGatewayAnalyticsDb({ organizationId, evidence }) {
  const sql = `
WITH stale_logs AS (
  SELECT id
  FROM agentcc_request_log
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND request_id LIKE ${sqlString(`${ANALYTICS_PREFIX}%`)}
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
      cleanup: "stale UI Gateway analytics logs",
      status: "passed",
      result,
    });
  }
  return result;
}

async function deleteGatewayAnalyticsFixtureDb({
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
        cleanup: "hard delete Gateway analytics fixture",
        status: Number(result.remaining_log_count) === 0 ? "passed" : "failed",
        result,
      });
    })
    .catch((error) =>
      evidence.push({
        cleanup: "hard delete Gateway analytics fixture",
        status: "failed",
        error: error.message,
      }),
    );
}

function sumRequestCount(points) {
  return asArray(points).reduce(
    (sum, point) => sum + Number(point.request_count || 0),
    0,
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

function analyticsResponse(pathname, query = {}) {
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
  await waitForInputPlaceholder(page, placeholder, timeout);
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

async function waitForInputPlaceholder(page, placeholder, timeout = 30000) {
  await page.waitForFunction(
    (expectedPlaceholder) =>
      window
        .visibleElements("input,textarea")
        .some((element) => element.placeholder === expectedPlaceholder),
    { timeout },
    placeholder,
  );
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

async function assertVisibleTextAbsentFromNearestCard(page, label, absentText) {
  await waitForVisibleText(page, label, { exact: true });
  const ok = await page.evaluate(
    ({ expectedLabel, blockedText }) => {
      const element = window
        .visibleElements()
        .find(
          (candidate) =>
            window.normalizeText(candidate.textContent) === expectedLabel,
        );
      const card = element?.closest(".MuiCard-root");
      return (
        Boolean(card) &&
        !window.normalizeText(card.textContent).includes(blockedText)
      );
    },
    { expectedLabel: label, blockedText: absentText },
  );
  assert(ok, `${label} card still contained ${absentText}.`);
}

async function assertVisibleTextAbsentFromNearestStack(
  page,
  label,
  absentText,
) {
  await waitForVisibleText(page, label, { exact: true });
  const ok = await page.evaluate(
    ({ expectedLabel, blockedText }) => {
      const element = window
        .visibleElements()
        .find(
          (candidate) =>
            window.normalizeText(candidate.textContent) === expectedLabel,
        );
      const stack = element?.closest(".MuiStack-root");
      return (
        Boolean(stack) &&
        !window.normalizeText(stack.textContent).includes(blockedText)
      );
    },
    { expectedLabel: label, blockedText: absentText },
  );
  assert(ok, `${label} stack still contained ${absentText}.`);
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
