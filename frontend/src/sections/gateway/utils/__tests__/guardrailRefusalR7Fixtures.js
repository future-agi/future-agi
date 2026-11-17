// Synthetic values only. The body must not choose the rendering path.
export const PRIVATE_PROSE =
  "Not charged; private-value-r7; try again in 123456 seconds";
export const REQUEST_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV";
export const REMEDIES = [
  ["managed_async_unsupported", "Send the request without x-agentcc-async"],
  ["managed_output_ceiling_required", "Set a positive max_tokens value"],
  ["managed_unsupported_parameter", "omit n/best_of or use 1"],
  [
    "managed_openrouter_streaming_not_yet",
    "explicitly choose your own provider key",
  ],
];
export const leakedCopy = /private-value-r7|123456|not charged|retry-after:/i;

export const refusalWire = (code, overrides = {}) => ({
  status_code: 400,
  blocked: false,
  guardrail_headers: {},
  body: {
    error: {
      code,
      type: "guardrail_error",
      message: PRIVATE_PROSE,
      param: "max_tokens=private-value-r7",
      metadata: {
        dispatched: false,
        retry_after: 123456,
        request_id: "private-value-r7",
      },
    },
  },
  ...overrides,
});

// C72: blocked is a relay-owned boolean, not a field in the upstream body.
// N11: block responses need not have warn/log outcome headers.
export const blockedWire = () => ({
  status_code: 403,
  blocked: true,
  guardrail_headers: {},
  body: {
    error: {
      code: "content_blocked",
      type: "guardrail_error",
      message:
        "Request blocked by guardrail: keyword-blocklist — Blocked keywords found: internal only",
    },
  },
});

export function directFailure(result, normalized = false) {
  if (normalized) {
    return {
      statusCode: result.status_code,
      ...result.body,
      responseHeaders: result.guardrail_headers,
      message: PRIVATE_PROSE,
      requestId: "private-value-r7",
      retryAfterSeconds: 123456,
    };
  }
  return {
    message: PRIVATE_PROSE,
    response: {
      status: result.status_code,
      data: result.body,
      headers: result.guardrail_headers,
    },
  };
}
