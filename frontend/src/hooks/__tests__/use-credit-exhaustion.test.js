import { describe, expect, it, vi } from "vitest";

vi.mock("src/utils/PostHog/posthog", () => ({
  trackPostHogEvent: vi.fn(),
}));

import { isCreditExhaustionError } from "src/hooks/use-credit-exhaustion";

/**
 * Errors rejected by the axios layer spread the response body onto the
 * custom error (`{ ...errData, statusCode }`), so the code arrives as the
 * envelope's top-level `code` — never as a top-level `errorCode`.
 * isCreditExhaustionError must match on the shape that actually arrives.
 */
describe("isCreditExhaustionError", () => {
  it("matches on HTTP 402 regardless of the body shape", () => {
    expect(
      isCreditExhaustionError({ statusCode: 402, detail: "upgrade" }),
    ).toBe(true);
    expect(
      isCreditExhaustionError({ statusCode: 402, code: "SOMETHING_ELSE" }),
    ).toBe(true);
  });

  it("matches billing codes from the envelope top-level code field", () => {
    for (const code of [
      "FREE_TIER_LIMIT",
      "BUDGET_PAUSED",
      "ENTITLEMENT_LIMIT",
      "ENTITLEMENT_DENIED",
      "PAYMENT_REQUIRED",
    ]) {
      expect(
        isCreditExhaustionError({ statusCode: 403, code, message: "denied" }),
      ).toBe(true);
    }
  });

  it("matches billing codes nested under result (snake_case and camelCase)", () => {
    expect(
      isCreditExhaustionError({
        statusCode: 403,
        result: { error_code: "FREE_TIER_LIMIT" },
      }),
    ).toBe(true);
    expect(
      isCreditExhaustionError({
        statusCode: 403,
        result: { errorCode: "ENTITLEMENT_LIMIT" },
      }),
    ).toBe(true);
  });

  it("does not match non-credit error codes or missing codes", () => {
    expect(
      isCreditExhaustionError({ statusCode: 403, code: "AUTH_FAILED" }),
    ).toBe(false);
    expect(
      isCreditExhaustionError({ statusCode: 500, code: "INTERNAL_ERROR" }),
    ).toBe(false);
    expect(isCreditExhaustionError({ statusCode: 400 })).toBe(false);
  });

  it("ignores the never-set top-level errorCode field", () => {
    // The old implementation keyed off error.errorCode, a field the axios
    // layer never sets — such a shape must not count as a credit error
    // unless the status or a real code says so.
    expect(isCreditExhaustionError({ errorCode: "FREE_TIER_LIMIT" })).toBe(
      false,
    );
    expect(
      isCreditExhaustionError({
        statusCode: 403,
        code: "AUTH_FAILED",
        errorCode: "FREE_TIER_LIMIT",
      }),
    ).toBe(false);
  });

  it("returns false for falsy input", () => {
    expect(isCreditExhaustionError(null)).toBe(false);
    expect(isCreditExhaustionError(undefined)).toBe(false);
  });
});
