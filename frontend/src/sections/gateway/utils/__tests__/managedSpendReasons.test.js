import { describe, expect, it } from "vitest";

import { classify, readManagedRefusal } from "../managedRefusals";

const RUNTIME_DETAIL =
  "run:fixture-run has $12.345678 left; required $98.765432";
const envelope = (status, code, metadata = {}) => ({
  status_code: status,
  body: {
    error: {
      code,
      type: "rate_limit_error",
      message: "Server detail",
      metadata,
    },
  },
});

// Pinned spendguard/errors.go:25-52. These reasons must win over a header or
// misleading message; waiting cannot make an oversized request fit.
const NON_WAITABLE = [
  [
    "request_exceeds_minute_cap",
    "Request exceeds the spending limit",
    "Lower max_tokens or reduce the request",
  ],
  [
    "request_exceeds_hour_cap",
    "Request exceeds the spending limit",
    "Lower max_tokens or reduce the request",
  ],
  [
    "new_org_daily_cap",
    "New organization spending limit reached",
    "Contact support",
  ],
];

describe("managed spend reason remedies", () => {
  it.each(NON_WAITABLE)(
    "uses the nested reason for %s",
    (reason, title, action) => {
      const result = envelope(429, "managed_spend_velocity", { reason });
      result.metadata = { reason: "unknown_outer_reason" };
      result.body.error.message = `Wait and retry. ${RUNTIME_DETAIL}`;
      result.guardrail_headers = { "Retry-After": "9" };
      const mapped = readManagedRefusal(result);
      expect(mapped).toMatchObject({
        category: "rate_limit",
        title,
        action: { label: action },
      });
      expect(mapped.action).not.toHaveProperty("href");
      expect(mapped.body).toContain(
        reason === "new_org_daily_cap"
          ? "Contact support"
          : "Lower max_tokens or reduce the request",
      );
      expect(mapped.body).not.toMatch(
        /wait|Retry-After|\$|fixture-run|12\.345678|98\.765432/i,
      );
    },
  );

  // The actual window producer has no reason field, not a synthetic reason
  // such as minute_window_full. It uses PaymentMeta with remedy_hint=retry.
  it.each(["org", "key:fixture-key"])(
    "waits for a transient %s window without echoing values",
    (scope) => {
      const result = envelope(429, "managed_spend_velocity", {
        limit_source: "agentcc_spend_guard",
        remedy_hint: "retry",
        scope,
        resets_at: "2026-11-06T10:00:00Z",
      });
      result.body.error.message = `${scope} managed spend is at its $12.345678 cap. ${RUNTIME_DETAIL}`;
      for (const retryAfter of [undefined, "9"]) {
        result.guardrail_headers =
          retryAfter === undefined ? {} : { "Retry-After": retryAfter };
        const mapped = readManagedRefusal(result);
        expect(mapped.category).toBe("rate_limit");
        expect(mapped.action).toEqual({
          label: retryAfter
            ? "Try again after waiting"
            : "Review the spending limit with an administrator",
        });
        if (retryAfter)
          expect(mapped.body).toContain("Wait before trying again.");
        else expect(mapped.body).not.toMatch(/wait|try again/i);
        expect(mapped.body).toContain("not retry automatically");
        expect(mapped.body).not.toMatch(
          /\$|fixture-run|fixture-key|12\.345678|98\.765432|2026-11-06/,
        );
        if (retryAfter) expect(mapped.body).toContain("Retry-After: 9");
        else expect(mapped.body).not.toContain("Retry-After");
      }
    },
  );

  it("keeps unknown spend reasons terminal without promising a transient window", () => {
    const result = envelope(429, "managed_spend_velocity", {
      reason: "future_reason",
    });
    result.body.error.message = RUNTIME_DETAIL;
    result.guardrail_headers = { "Retry-After": "9" };
    const mapped = readManagedRefusal(result);
    expect(mapped.category).toBe("rate_limit");
    expect(mapped.action).toEqual({
      label: "Review the spending limit with an administrator",
    });
    expect(mapped.body).not.toMatch(/wait|try again|Retry-After/i);
    expect(mapped.body).toContain("not retry automatically");
    expect(mapped.body).not.toContain(RUNTIME_DETAIL);
    expect(
      classify(429, "managed_spend_velocity", { reason: "future_reason" }).body,
    ).not.toMatch(/undefined|null/);
  });
});
