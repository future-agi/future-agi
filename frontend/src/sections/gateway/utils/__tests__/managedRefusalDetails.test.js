import { describe, expect, it } from "vitest";

import {
  classify,
  readManagedFailure,
  readManagedRefusal,
} from "../managedRefusals";

const UNTRUSTED = "fixture-model; policy:fixture-policy; $12.345678";
const wire = (code, param, metadata = {}) => ({
  status_code: 400,
  body: { error: { code, param, metadata, message: UNTRUSTED } },
});
const ROUTING_CODES = [
  "invalid_provider_preference",
  "invalid_routing_field",
  "unsupported_routing_field",
  "routing_field_not_yet_supported",
  "extra_body_conflict",
  "invalid_extra_body",
];

// Locally named field identifiers, never parameter values or server prose.
const FIELDS = [
  "provider",
  "provider.order",
  "provider.only",
  "provider.ignore",
  "provider.allow_fallbacks",
  "provider.require_parameters",
  "provider.data_collection",
  "provider.zdr",
  "provider.quantizations",
  "provider.sort",
  "provider.sort.by",
  "provider.sort.partition",
  "provider.max_price",
  "provider.max_price.prompt",
  "provider.max_price.completion",
  "provider.max_price.request",
  "provider.max_price.image",
  "provider.preferred_max_latency",
  "provider.preferred_max_latency.p50",
  "provider.preferred_max_latency.p75",
  "provider.preferred_max_latency.p90",
  "provider.preferred_max_latency.p99",
  "provider.preferred_min_throughput",
  "provider.preferred_min_throughput.p50",
  "provider.preferred_min_throughput.p75",
  "provider.preferred_min_throughput.p90",
  "provider.preferred_min_throughput.p99",
  "providerOptions",
  "providerOptions.gateway",
  "providerOptions.gateway.order",
  "providerOptions.gateway.only",
  "providerOptions.gateway.sort",
  "providerOptions.gateway.models",
  "providerOptions.gateway.tags",
  "providerOptions.gateway.user",
  "providerOptions.gateway.sessionId",
  "providerOptions.gateway.zeroDataRetention",
  "models",
  "fallbacks",
  "route",
  "plugins",
  "transforms",
  "preset",
  "session_id",
  "caching",
  "extra_body",
  "extra_body.extra_body",
  "extra_body.model",
  "extra_body.messages",
  "extra_body.stream",
  "extra_body.stream_options",
  "extra_body.user",
  "extra_body.metadata",
  "extra_body.session_id",
  "extra_body.provider",
  "extra_body.providerOptions",
  "extra_body.models",
  "extra_body.fallbacks",
  "extra_body.route",
  "extra_body.plugins",
  "extra_body.transforms",
  "extra_body.preset",
  "extra_body.caching",
  "extra_body.max_tokens",
  "extra_body.temperature",
  "extra_body.top_p",
];

describe("idempotency manual wait details", () => {
  it.each(["1", " 9 ", 0, 12])(
    "shows the supplied Retry-After without changing same-key semantics: %s",
    (retryAfter) => {
      const result = {
        status_code: 409,
        body: { error: { code: "idempotency_in_progress" } },
        guardrail_headers: { "rEtRy-AfTeR": retryAfter },
      };
      const mapped = readManagedRefusal(result);
      expect(mapped.body).toContain(
        `Retry-After: ${String(retryAfter).trim()}.`,
      );
      expect(mapped.body).toContain("Wait before trying again.");
      expect(mapped.body).toContain("request body unchanged");
      expect(mapped.body).toContain("same idempotency key");
      expect(mapped.body).toContain("not retry automatically");
      expect(mapped.action).toEqual({
        label: "Wait before resending the same request and idempotency key",
      });
      expect(
        readManagedFailure({
          response: {
            status: 409,
            data: result.body,
            headers: result.guardrail_headers,
          },
        }),
      ).toEqual(mapped);
      expect(
        readManagedFailure({
          statusCode: 409,
          ...result.body,
          responseHeaders: result.guardrail_headers,
        }),
      ).toEqual(mapped);
    },
  );

  it.each(
    [undefined, null, "", " ", NaN, Infinity, {}, []].map((value) => [value]),
  )("does not invent a wait duration for %j", (retryAfter) => {
    const mapped = classify(409, "idempotency_in_progress", { retryAfter });
    expect(mapped.body).not.toContain("Retry-After");
    expect(mapped.body).toContain("Wait before trying again.");
    expect(mapped.body).toContain("request body unchanged");
    expect(mapped.body).toContain("same idempotency key");
    expect(mapped.body).toContain("not retry automatically");
  });

  it("does not turn idempotency-key reuse into manual waiting", () => {
    const mapped = classify(422, "idempotency_key_reused", { retryAfter: "1" });
    expect(mapped.action.label).toBe(
      "Use a new idempotency key for a different request",
    );
    expect(mapped.body).not.toContain("Retry-After");
  });
});

describe("allowlisted routing field details", () => {
  it.each(FIELDS)("uses a local field label for %s", (param) => {
    const mapped = readManagedRefusal(wire("invalid_routing_field", param));
    expect(mapped.body).toContain(`Field: ${param}.`);
    expect(mapped.body).not.toContain(UNTRUSTED);
  });

  it.each(ROUTING_CODES)("carries error.param separately for %s", (code) => {
    const result = wire(code, "provider.order", {
      param: "models",
      model: UNTRUSTED,
    });
    const mapped = readManagedRefusal(result);
    expect(mapped.body).toContain("Field: provider.order.");
    expect(mapped.body).not.toContain("Field: models.");
    expect(mapped.body).not.toContain(UNTRUSTED);
    expect(
      readManagedRefusal({ ...result, body: JSON.stringify(result.body) }),
    ).toEqual(mapped);
    expect(readManagedFailure({ statusCode: 400, ...result.body })).toEqual(
      mapped,
    );
    expect(
      readManagedFailure({ response: { status: 400, data: result.body } }),
    ).toEqual(mapped);
    expect(mapped.action).toEqual(classify(400, code).action);
  });

  it.each(
    [
      undefined,
      null,
      "",
      " provider.order ",
      "provider.order[0]",
      "provider.fixture-model",
      "provider.order=fixture-model",
      "extra_body.fixture-policy",
      "toString",
      "constructor",
      "__proto__",
      UNTRUSTED,
      "<script>fixture</script>",
      1,
      ["models"],
      { param: "models" },
    ].map((value) => [value]),
  )("does not echo an unknown or malformed field: %j", (param) => {
    for (const code of ROUTING_CODES) {
      const baseline = readManagedRefusal(wire(code));
      expect(
        readManagedRefusal(wire(code, param, { param: "models" })),
      ).toEqual(baseline);
      expect(baseline.body).not.toContain("Field:");
    }
  });

  it("ignores metadata-only field labels and non-validation codes", () => {
    const metadataOnly = readManagedRefusal(
      wire("invalid_routing_field", undefined, { param: "models" }),
    );
    expect(metadataOnly.body).not.toContain("Field:");
    for (const [status, code] of [
      [403, "key_scope_denied"],
      [409, "idempotency_in_progress"],
      [400, "future_code"],
      [418, "invalid_routing_field"],
    ]) {
      expect(classify(status, code, { param: "models" }).body).not.toContain(
        "Field:",
      );
    }
  });
});
