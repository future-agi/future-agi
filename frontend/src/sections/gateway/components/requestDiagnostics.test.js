import { describe, expect, it } from "vitest";

import { requestFailureFacts } from "./requestDiagnostics";

const REQUEST_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV";

describe("request failure evidence", () => {
  it("reports exactly the sanitized relay evidence, without inventing upstream diagnostics", () => {
    expect(requestFailureFacts({
      status_code: 429,
      body: { error: { message: "Gateway request failed.", code: "upstream_4xx" } },
      guardrail_headers: {},
    })).toEqual({ outcome: "refused", status: 429, message: "Gateway request failed.", code: "upstream_4xx" });
  });

  it.each(["relay", "axios", "console", "wrapped"])("reads optional evidence actually supplied by a %s envelope", (shape) => {
    const body = { error: { message: "Lower max_tokens.", code: "invalid_request", type: "invalid_request_error", param: "max_tokens" } };
    const headers = { "X-Agentcc-Request-Id": REQUEST_ID, "Retry-After": "12" };
    const relay = { status_code: 400, body, guardrail_headers: headers };
    const input = shape === "relay" ? relay
      : shape === "axios" ? { response: { status: 400, data: body, headers } }
        : shape === "console" ? { statusCode: 400, ...body, responseHeaders: headers }
          : { response: { status: 200, data: { result: relay }, headers: { "Retry-After": "99" } } };
    expect(requestFailureFacts(input)).toEqual({
      outcome: "refused", status: 400, message: "Lower max_tokens.", code: "invalid_request",
      type: "invalid_request_error", param: "max_tokens", requestId: REQUEST_ID, retryAfter: "12",
    });
  });

  it("never derives optional fields from prose, metadata, request config or body aliases", () => {
    const message = `Wait 30 seconds; request ${REQUEST_ID}; param temperature`;
    expect(requestFailureFacts({
      statusCode: 400,
      error: { message, metadata: { request_id: REQUEST_ID, retry_after: 30 }, param: { secret: "fixture-only" } },
      requestId: REQUEST_ID, retryAfter: "30",
      config: { headers: { Authorization: "Bearer fixture-credential" } },
      request: { headers: { "x-request-id": REQUEST_ID } },
    })).toEqual({ outcome: "refused", status: 400, message });
  });

  it("drops unsafe optional values and redacts recognizable credentials in messages", () => {
    const facts = requestFailureFacts({
      statusCode: 400,
      error: { message: "Rejected Authorization: Bearer fixture-credential", code: "bad\ncode", type: { secret: "fixture-only" }, param: "Bearer fixture-credential" },
      responseHeaders: { "x-request-id": "bad\nid", "retry-after": "https://example.test/secret" },
    });
    expect(facts).toEqual({ outcome: "refused", status: 400, message: "Rejected Authorization: Bearer [redacted]" });
  });

  it.each([
    { message: "Connection interrupted", transportCode: "ECONNABORTED" },
    { message: "Network Error", code: "ERR_NETWORK" },
    { statusCode: 408, message: "Request timed out" },
    { status_code: 504, body: { error: { message: "Gateway timed out" } } },
    { status_code: 200, message: "Stream ended early" },
  ])("does not assert completion or refusal after a transport/timeout failure: %j", (input) => {
    expect(requestFailureFacts(input).outcome).toBe("unknown");
  });

  it.each(["axios", "console"])("preserves the safe authentication remedy in %s diagnostics", (shape) => {
    const body = { code: "gateway_request_rejected", message: "fixture-private-upstream-details" };
    const input = shape === "axios" ? { response: { status: 401, data: body } } : { statusCode: 401, ...body };
    expect(requestFailureFacts(input)).toEqual({
      outcome: "refused", status: 401, code: "gateway_request_rejected",
      message: "Gateway authentication failed. Ask your administrator to check the gateway credentials.",
    });
  });

  // test_playground catches GatewayClientError after a delegated deadline;
  // GeneralMethods.bad_request/build_error_envelope supply these default fields.
  // The shared Axios client adds statusCode, not a gateway status_code.
  const consoleTimeoutBody = {
    status: false,
    type: "validation_error",
    code: "invalid",
    detail: "Gateway request failed. (gateway_error)",
    message: "Gateway request failed. (gateway_error)",
    error: "Gateway request failed. (gateway_error)",
    result: "Gateway request failed. (gateway_error)",
  };

  it.each(["axios", "console"])("keeps the real relay timeout's HTTP 400 but not a definite refusal via %s", (shape) => {
    const input = shape === "axios"
      ? { response: { status: 400, data: consoleTimeoutBody } }
      : { ...consoleTimeoutBody, statusCode: 400, transportCode: "ERR_BAD_REQUEST", responseHeaders: {} };
    expect(requestFailureFacts(input)).toEqual({
      outcome: "unknown", status: 400, message: consoleTimeoutBody.message, code: "invalid",
    });
  });

  it.each(["Choose a chat model.", "Gateway request failed. (internal_error)"])("does not infer refusal from ambiguous console prose: %s", (message) => {
    expect(requestFailureFacts({
      ...consoleTimeoutBody, statusCode: 400, message, detail: message, error: message, result: message,
    }).outcome).toBe("unknown");
  });

  it.each([
    { statusCode: 400, status: false, code: "required", detail: "Prompt is required." },
    { statusCode: 400, status: false, error: { code: "invalid_request", message: "Lower max_tokens." } },
    { status_code: 400, body: consoleTimeoutBody },
  ])("keeps explicit refusal evidence distinct from a generic console failure: %j", (input) => {
    expect(requestFailureFacts(input).outcome).toBe("refused");
  });

  it("keeps legacy console string errors and response-derived IDs without guessing completion", () => {
    expect(requestFailureFacts({ statusCode: 400, status: false, result: "Choose a chat model.", responseHeaders: { "x-request-id": "console-request-04" } }))
      .toEqual({ outcome: "unknown", status: 400, message: "Choose a chat model.", requestId: "console-request-04" });
  });
});
