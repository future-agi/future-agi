import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import fs from "node:fs/promises";
import process from "node:process";
import vm from "node:vm";
import { apiPath } from "../lib/api-client.mjs";
import { runJourneys } from "../lib/runner.mjs";
import { publicApiJourneys } from "./public-api.mjs";

// Keep the normal api-journey Vitest discovery working while allowing a
// dependency-free, offline `node --test` regression run.
const { test, afterEach, vi } = process.env.VITEST
  ? await import("vitest")
  : await import("node:test");
if (vi) afterEach(() => vi.restoreAllMocks());

const marker = "arbitrary-server-value-A09";
const authDetail = "Authentication credentials were not provided.";

function mockMethod(t, target, key, implementation) {
  if (vi) {
    const spy = vi.spyOn(target, key).mockImplementation(implementation);
    return () => spy.mockRestore();
  }
  const mocked = t.mock.method(target, key, implementation);
  return () => mocked.mock.restore();
}

function stubResponses(t, respond) {
  const requests = [];
  const restore = mockMethod(t, globalThis, "fetch", async (url, options) => {
    const path = new URL(url).pathname;
    requests.push({ path, method: options.method });
    const { status, body, text, headers } = respond(path, options);
    return new Response(text === undefined ? JSON.stringify(body) : text, {
      status,
      headers: { "Content-Type": "application/json", ...headers },
    });
  });
  return { requests, restore };
}

async function runJourney(t, id, respond, journeys = publicApiJourneys) {
  const evidence = [];
  const { requests, restore } = stubResponses(t, respond);
  const journey = journeys.find((item) => item.id === id);
  assert.ok(journey, `Missing journey ${id}`);
  let error;
  try {
    await journey.run({ apiBase: "https://api.invalid", evidence });
  } catch (caught) {
    error = caught;
  } finally {
    restore();
  }
  return { evidence, requests, error };
}

async function runFailedJourneyThroughRunner(t, id, respond) {
  const { requests, restore } = stubResponses(t, respond);
  const stdout = [];
  let summaryJson;
  const jsonPath = new URL("./runner-summary.json", import.meta.url).pathname;
  const restoreWrite = mockMethod(t, fs, "writeFile", async (file, text) => {
    assert.equal(file, jsonPath);
    summaryJson = text;
  });
  const restoreStdout = mockMethod(t, process.stdout, "write", (chunk) => {
    stdout.push(String(chunk));
    return true;
  });
  const restoreLog = mockMethod(t, console, "log", (text) => {
    stdout.push(`${text}\n`);
  });
  const originalExitCode = process.exitCode;
  try {
    const summary = await runJourneys(publicApiJourneys, [
      "--only",
      id,
      "--json",
      jsonPath,
    ]);
    assert.equal(process.exitCode, 1);
    // Pin the actual runner's file/stdout serialization, not a reduced result
    // that might silently omit error.message, stack, or partially added receipts.
    assert.equal(summaryJson, `${JSON.stringify(summary, null, 2)}\n`);
    assert.ok(stdout.includes(summaryJson));
    assert.equal(summary.failed, 1);
    assert.equal(summary.results[0].status, "failed");
    assert.ok(requests.length > 0, "must execute a real response assertion");
    return { summary, summaryJson, stdout: stdout.join(""), requests };
  } finally {
    process.exitCode = originalExitCode;
    restoreLog();
    restoreStdout();
    restoreWrite();
    restore();
  }
}

const markedHeaders = {
  "Content-Type": `application/json; server-value=${marker}`,
  Location: `https://example.invalid/${marker}`,
};
const markedBody = { code: marker, detail: marker, [marker]: marker };

function mcpFailureFixture(override) {
  const fixture = mcpResponseFixture(42);
  return (path, options) => {
    const response = fixture(path, options);
    return { ...response, ...override(path, response), headers: markedHeaders };
  };
}

for (const { name, id, respond, failure } of [
  {
    name: "auth guard unexpected HTTP 500",
    id: "PUBLIC-AUTH-001",
    respond: () => ({ status: 500, body: markedBody, headers: markedHeaders }),
    failure: /expected anonymous auth guard/,
  },
  {
    name: "auth guard non-JSON body",
    id: "PUBLIC-AUTH-001",
    respond: () => ({ status: 403, text: marker, headers: markedHeaders }),
    failure: /returned non-JSON body/,
  },
  {
    name: "auth guard missing expected error text",
    id: "PUBLIC-AUTH-001",
    respond: () => ({ status: 403, body: markedBody, headers: markedHeaders }),
    failure: /missing any expected error text/,
  },
  {
    name: "auth guard token-like data",
    id: "PUBLIC-AUTH-001",
    respond: () => ({
      status: 403,
      body: { detail: authDetail, access_token: marker },
      headers: markedHeaders,
    }),
    failure: /exposed token-like data/,
  },
  {
    name: "JSON client error unexpected HTTP 500",
    id: "PUBLIC-AUTH-014",
    respond: () => ({ status: 500, body: markedBody, headers: markedHeaders }),
    failure: /expected JSON client error/,
  },
  {
    name: "JSON client error missing expected text",
    id: "PUBLIC-AUTH-014",
    respond: () => ({ status: 400, body: markedBody, headers: markedHeaders }),
    failure: /missing expected error text/,
  },
  {
    name: "JSON client error after a receipt with marked headers",
    id: "PUBLIC-AUTH-014",
    respond: (path) => ({
      status: 400,
      body:
        path === "/saml2_auth/idp-login/" ? { detail: "email" } : markedBody,
      headers: markedHeaders,
    }),
    failure: /missing expected error text/,
  },
  {
    name: "SAML redirect token-like Location header",
    id: "PUBLIC-AUTH-014",
    respond: (path) => {
      if (path.includes("idp-uploads"))
        return {
          ...boundaryResponse(path, "not_authenticated"),
          headers: markedHeaders,
        };
      if (
        path === "/saml2_auth/idp-login/" ||
        path.startsWith("/saml2_auth/login")
      )
        return {
          status: 400,
          body: { detail: "email provider" },
          headers: markedHeaders,
        };
      return {
        status: 302,
        text: "",
        headers: {
          ...markedHeaders,
          Location: `https://example.invalid/?token=Bearer ${marker}`,
        },
      };
    },
    failure: /exposed token-like data/,
  },
  {
    name: "MCP health payload mismatch",
    id: "MCP-OAUTH-001",
    respond: () => ({ status: 200, body: markedBody, headers: markedHeaders }),
    failure: /MCP health payload mismatch/,
  },
  {
    name: "MCP unexpected OAuth error code",
    id: "MCP-OAUTH-001",
    respond: mcpFailureFixture((path) =>
      path === "/mcp/oauth/token/"
        ? { body: { error: marker, error_description: "grant_type" } }
        : {},
    ),
    failure: /expected OAuth error invalid_request/,
  },
  {
    name: "MCP missing grant_type description",
    id: "MCP-OAUTH-001",
    respond: mcpFailureFixture((path) =>
      path === "/mcp/oauth/token/"
        ? { body: { error: "invalid_request", error_description: marker } }
        : {},
    ),
    failure: /did not mention grant_type/,
  },
  {
    name: "MCP unknown client after marked-header receipt",
    id: "MCP-OAUTH-001",
    respond: mcpFailureFixture((path, response) =>
      path === "/mcp/oauth/authorize/" &&
      response.body.detail === "Unknown client_id"
        ? { status: 500, body: markedBody }
        : {},
    ),
    failure: /expected 400 or registry 503/,
  },
  {
    name: "LiveKit unexpected signed webhook code",
    id: "PUBLIC-AUTH-021",
    respond: (path) => {
      if (path.endsWith("/webhook/")) {
        return {
          status: 401,
          body: { code: marker, detail: "Missing Authorization header" },
          headers: markedHeaders,
        };
      }
      if (
        path.includes("/listener-token/") ||
        path.includes("/validate-credentials/")
      ) {
        return {
          ...boundaryResponse(path, "not_authenticated"),
          headers: markedHeaders,
        };
      }
      return {
        status: 401,
        body: { detail: "Missing Bearer token" },
        headers: markedHeaders,
      };
    },
    failure: /expected not_authenticated webhook code/,
  },
  {
    name: "HTML client error unexpected content type",
    id: "PUBLIC-AUTH-011",
    respond: (path) =>
      path.endsWith("/oauth/callback/")
        ? {
            status: 400,
            text: `Missing code or state parameter ${marker}`,
            headers: markedHeaders,
          }
        : {
            ...boundaryResponse(path, "not_authenticated"),
            headers: markedHeaders,
          },
    failure: /expected text\/html response/,
  },
  {
    name: "pricing invalid value under a marked JSON key",
    id: "PUBLIC-AUTH-025",
    respond: (path) =>
      path === "/usage/get_latest_prices/"
        ? {
            status: 200,
            body: { status: true, result: { [marker]: -1 } },
            headers: markedHeaders,
          }
        : {
            ...boundaryResponse(path, "not_authenticated"),
            headers: markedHeaders,
          },
    failure: /returned non-numeric pricing/,
  },
]) {
  test(`failure output excludes response values: ${name} (${id})`, async (t) => {
    const { summary, summaryJson, stdout } =
      await runFailedJourneyThroughRunner(t, id, respond);
    const result = summary.results[0];
    assert.match(result.error, failure);
    for (const [surface, output] of Object.entries({
      message: result.error,
      stack: result.stack,
      summary_json: summaryJson,
      stdout,
    })) {
      assert.equal(
        output.includes(marker),
        false,
        `${surface} must not contain response values`,
      );
    }
  });
}

for (const [contentType, expectedClass] of [
  [`application/json; value=${marker}`, "JSON"],
  [`application/problem+json; value=${marker}`, "JSON"],
  [`text/html; value=${marker}`, "HTML"],
  [`text/${marker}`, "OTHER"],
  ["", "MISSING"],
]) {
  test(`failure metadata reports shape/UTF-8 bytes and ${expectedClass} content-type class`, async (t) => {
    const text = `  { "code": "${marker}", "${marker}": "é" }\n`;
    const { summary, summaryJson, stdout } =
      await runFailedJourneyThroughRunner(t, "PUBLIC-AUTH-001", () => ({
        status: 500,
        text,
        headers: { "Content-Type": contentType },
      }));
    const message = summary.results[0].error;
    assert.deepEqual(JSON.parse(message.slice(message.indexOf("{"))), {
      status: 500,
      content_type_class: expectedClass,
      body_kind: "object",
      json_key_count: 2,
      body_bytes: Buffer.byteLength(text, "utf8"),
      code_class: "UNEXPECTED",
      code_present: true,
    });
    assert.equal(summaryJson.includes(marker), false);
    assert.equal(stdout.includes(marker), false);
  });
}

function boundaryResponse(path, code) {
  return {
    status: path.startsWith("/usage/ee/licenses/") ? 404 : 403,
    body: { code, detail: authDetail },
  };
}

function assertCodeEvidence(row, codeClass, codePresent) {
  for (const key of ["code", "codeClass", "codePresent"]) {
    assert.equal(Object.hasOwn(row, key), false, `${key} field must not land`);
  }
  assert.equal(row.code_class, codeClass);
  assert.equal(row.code_present, codePresent);
}

test("anonymous boundary evidence never lands arbitrary response codes", async (t) => {
  const { evidence, error } = await runJourney(t, "PUBLIC-AUTH-004", (path) =>
    boundaryResponse(path, marker),
  );
  assert.ifError(error);
  assert.ok(evidence.some((row) => row.kind === "absence"));
  assert.ok(evidence.some((row) => row.kind === "authentication"));
  assert.equal(JSON.stringify(evidence).includes(marker), false);
  for (const row of evidence) assertCodeEvidence(row, "UNEXPECTED", true);
});

test("failed probes still record every boundary without copying body values", async (t) => {
  const { evidence, requests, error } = await runJourney(
    t,
    "PUBLIC-AUTH-004",
    () => ({
      status: 500,
      body: { code: marker, message: marker, id: marker, access_token: marker },
    }),
  );
  assert.ok(error);
  assert.match(
    error.message,
    /authentication\/JSON\/redaction contract failed/,
  );
  assert.equal(evidence.length, requests.length);
  assert.ok(evidence.some((row) => row.kind === "absence"));
  assert.equal(JSON.stringify(evidence).includes(marker), false);
  assert.equal(error.message.includes(marker), false);
  for (const row of evidence) assertCodeEvidence(row, "UNEXPECTED", true);
});

for (const { name, code, authClass, absenceClass, present = true } of [
  {
    name: "missing",
    code: undefined,
    authClass: "UNEXPECTED",
    absenceClass: "UNEXPECTED",
    present: false,
  },
  {
    name: "null",
    code: null,
    authClass: "UNEXPECTED",
    absenceClass: "UNEXPECTED",
    present: false,
  },
  {
    name: "not authenticated",
    code: "not_authenticated",
    authClass: "not_authenticated",
    absenceClass: "UNEXPECTED",
  },
  {
    name: "authentication failed",
    code: "authentication_failed",
    authClass: "authentication_failed",
    absenceClass: "UNEXPECTED",
  },
  {
    name: "permission denied",
    code: "permission_denied",
    authClass: "permission_denied",
    absenceClass: "UNEXPECTED",
  },
  {
    name: "not found",
    code: "not_found",
    authClass: "UNEXPECTED",
    absenceClass: "not_found",
  },
  {
    name: "wrong probe family",
    code: "invalid",
    authClass: "UNEXPECTED",
    absenceClass: "UNEXPECTED",
  },
  {
    name: "prefix is not membership",
    code: `not_found-${marker}`,
    authClass: "UNEXPECTED",
    absenceClass: "UNEXPECTED",
  },
  {
    name: "empty string",
    code: "",
    authClass: "UNEXPECTED",
    absenceClass: "UNEXPECTED",
  },
  {
    name: "false",
    code: false,
    authClass: "UNEXPECTED",
    absenceClass: "UNEXPECTED",
  },
  {
    name: "zero",
    code: 0,
    authClass: "UNEXPECTED",
    absenceClass: "UNEXPECTED",
  },
  {
    name: "object",
    code: { value: marker },
    authClass: "UNEXPECTED",
    absenceClass: "UNEXPECTED",
  },
  {
    name: "array",
    code: ["not_authenticated", marker],
    authClass: "UNEXPECTED",
    absenceClass: "UNEXPECTED",
  },
  {
    name: "prototype key",
    code: "__proto__",
    authClass: "UNEXPECTED",
    absenceClass: "UNEXPECTED",
  },
]) {
  test(`boundary code classification is closed and kind-specific: ${name}`, async (t) => {
    const { evidence, error } = await runJourney(t, "PUBLIC-AUTH-004", (path) =>
      boundaryResponse(path, code),
    );
    assert.ifError(error);
    assert.ok(evidence.length > 0);
    for (const row of evidence) {
      assertCodeEvidence(
        row,
        row.kind === "absence" ? absenceClass : authClass,
        present,
      );
    }
    assert.equal(JSON.stringify(evidence).includes(marker), false);
  });
}

// These journeys consist entirely of auth guards (or retired-route boundaries).
// Exercise their actual evidence.push calls, not a parallel test-only sanitizer.
const mixedJourneyIds = new Set([
  "MCP-OAUTH-001",
  "PUBLIC-AUTH-007",
  "PUBLIC-AUTH-008",
  "PUBLIC-AUTH-009",
  "PUBLIC-AUTH-010",
  "PUBLIC-AUTH-011",
  "PUBLIC-AUTH-013",
  "PUBLIC-AUTH-014",
  "PUBLIC-AUTH-021",
  "PUBLIC-AUTH-023",
  "PUBLIC-AUTH-025",
  "PUBLIC-SYSTEM-046",
]);
for (const journey of publicApiJourneys.filter(
  (item) => !mixedJourneyIds.has(item.id),
)) {
  test(`${journey.id} records value-free auth codes`, async (t) => {
    const { evidence, error } = await runJourney(t, journey.id, (path) =>
      boundaryResponse(path, marker),
    );
    assert.ifError(error);
    assert.ok(evidence.length > 0);
    assert.equal(JSON.stringify(evidence).includes(marker), false);
    for (const row of evidence) assertCodeEvidence(row, "UNEXPECTED", true);
  });
}

async function sharedJourneyWithRouteFixture() {
  // This base's generated registry omits /tracer/otlp/v1/traces, which stops
  // PUBLIC-SYSTEM-046 before its shared-link probe. Isolate that unrelated gap
  // with a test-only apiPath fixture; run the real receipt and assertion source.
  const source = await fs.readFile(
    new URL("./public-api.mjs", import.meta.url),
    "utf8",
  );
  return vm.runInNewContext(
    `${source.replace(/^import[\s\S]*?;\n/gm, "").replace("export const publicApiJourneys", "const publicApiJourneys")}\npublicApiJourneys;`,
    {
      assert,
      Buffer,
      fetch: (...args) => globalThis.fetch(...args),
      apiPath: (template, params) =>
        template === "/tracer/otlp/v1/traces"
          ? template
          : apiPath(template, params),
    },
  );
}

for (const status of [404, 503]) {
  test(`shared-link ${status} detail is a classification, never server text`, async (t) => {
    const code = status === 404 ? "not_found" : "service_unavailable";
    const journeys = await sharedJourneyWithRouteFixture();
    const { evidence, error } = await runJourney(
      t,
      "PUBLIC-SYSTEM-046",
      (path) => {
        if (path === "/tracer/otlp/v1/traces") {
          return {
            status: 403,
            body: { code: "not_authenticated", detail: authDetail },
          };
        }
        if (path === "/tracer/v1/health") {
          return {
            status: 200,
            body: { status: "healthy", service: "otlp-trace-receiver" },
          };
        }
        assert.ok(path.startsWith("/tracer/shared/"));
        return {
          status,
          body: {
            code,
            type: code,
            detail: `${status === 404 ? "Shared link not found" : "Shared link resolver is temporarily unavailable"}: ${marker}`,
          },
        };
      },
      journeys,
    );
    assert.ifError(error);
    assert.equal(evidence.length, 3);
    assert.equal(JSON.stringify(evidence).includes(marker), false);
    const shared = evidence.at(-1);
    assert.equal(Object.hasOwn(shared, "detail"), false);
    assert.equal(Object.hasOwn(shared, "detailClass"), false);
    assertCodeEvidence(shared, code, true);
    assert.equal(
      shared.detail_class,
      status === 404 ? "NOT_FOUND" : "SERVICE_UNAVAILABLE",
    );
  });
}

for (const status of [200, 400]) {
  test(`pricing ${status} receipt never lands response detail or arbitrary code`, async (t) => {
    const { evidence, error } = await runJourney(
      t,
      "PUBLIC-AUTH-025",
      (path) => {
        if (path === "/usage/get_latest_prices/") {
          return {
            status,
            body:
              status === 400
                ? {
                    status: false,
                    code: "invalid",
                    detail: `Unable to fetch pricing data: ${marker}`,
                  }
                : { status: true, code: marker, result: { [marker]: 1 } },
          };
        }
        if (path === "/usage/webhook/") {
          return {
            status: 400,
            body: { code: marker, detail: "Missing Stripe-Signature" },
          };
        }
        return boundaryResponse(path, "not_authenticated");
      },
    );
    assert.ifError(error);
    assert.equal(JSON.stringify(evidence).includes(marker), false);
    const pricing = evidence.find(
      (row) => row.auth_boundary === "public_pricing_read",
    );
    assert.ok(pricing);
    assert.equal(Object.hasOwn(pricing, "error_detail"), false);
    assert.equal(Object.hasOwn(pricing, "errorDetailClass"), false);
    assert.equal(
      pricing.error_detail_class,
      status === 400 ? "PRICING_SOURCE_UNAVAILABLE" : null,
    );
    assertCodeEvidence(
      pricing,
      status === 400 ? "invalid" : "UNEXPECTED",
      true,
    );
    assert.equal(pricing.result_key_count, status === 200 ? 1 : 0);
  });
}

function mcpResponseFixture(toolCount) {
  let approveInfoCalls = 0;
  let authorizeCalls = 0;
  return (path, options) => {
    const body = options.body ? JSON.parse(options.body) : {};
    if (path === "/mcp/health/") {
      return {
        status: 200,
        body: {
          status: true,
          result: { healthy: true, tool_count: toolCount },
        },
      };
    }
    // The two approve-info and three authorize calls are distinguished by call order.
    if (path === "/mcp/oauth/approve-info/") {
      approveInfoCalls += 1;
      return approveInfoCalls === 1
        ? { status: 400, body: { detail: "Missing request_id" } }
        : {
            status: 404,
            body: { detail: "Approval request not found or expired" },
          };
    }
    if (path === "/mcp/oauth/authorize/") {
      authorizeCalls += 1;
      return {
        status: 400,
        body: {
          detail: [
            "Missing client_id or redirect_uri",
            "Unsupported response_type",
            "Unknown client_id",
          ][authorizeCalls - 1],
        },
      };
    }
    if (path === "/mcp/oauth/token/") {
      if (!body.grant_type)
        return {
          status: 400,
          body: { error: "invalid_request", error_description: "grant_type" },
        };
      if (body.grant_type === "client_credentials")
        return { status: 400, body: { error: "unsupported_grant_type" } };
      if (body.grant_type === "refresh_token" && !body.refresh_token)
        return { status: 400, body: { error: "invalid_request" } };
      return { status: 401, body: { error: "invalid_client" } };
    }
    return boundaryResponse(path, "not_authenticated");
  };
}

for (const { name, raw, expected } of [
  { name: "numeric string", raw: "0000000000000000042", expected: null },
  { name: "null", raw: null, expected: null },
  { name: "false", raw: false, expected: null },
  { name: "empty array", raw: [], expected: null },
  { name: "true", raw: true, expected: null },
  { name: "empty string", raw: "", expected: null },
  { name: "zero", raw: 0, expected: 0 },
  { name: "number", raw: 42, expected: 42 },
]) {
  test(`MCP health counts record only raw finite numbers: ${name}`, async (t) => {
    const { evidence, error } = await runJourney(
      t,
      "MCP-OAUTH-001",
      mcpResponseFixture(raw),
    );
    assert.ifError(error);
    assert.equal(evidence[0].tool_count, expected);
    assert.equal(evidence.at(-1).health_tool_count, expected);
  });
}

for (const raw of [undefined, marker, {}]) {
  test(`MCP health count assertion still rejects ${JSON.stringify(raw)}`, async (t) => {
    const { evidence, error } = await runJourney(
      t,
      "MCP-OAUTH-001",
      mcpResponseFixture(raw),
    );
    assert.match(error?.message || "", /MCP health payload mismatch/);
    assert.equal(evidence.length, 0);
  });
}

test("every public API receipt excludes direct raw code and detail projections", async () => {
  const source = await fs.readFile(
    new URL("./public-api.mjs", import.meta.url),
    "utf8",
  );
  const receipts = [
    ...source.matchAll(/evidence\.push\(\{[\s\S]*?^\s*\}\);/gm),
  ];
  assert.ok(receipts.length > 50, "scan must cover all receipt call sites");
  for (const [receipt] of receipts) {
    assert.doesNotMatch(receipt, /\b(?:code|detail|error_detail):/);
    assert.doesNotMatch(
      receipt,
      /\.body\??\.(?:code|detail|message|error|id|token)\b/,
    );
  }
});
