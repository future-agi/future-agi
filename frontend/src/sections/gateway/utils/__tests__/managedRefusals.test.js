import { describe, expect, it } from "vitest";

import { paths } from "src/routes/paths";
import { CREDITS_ANCHOR } from "src/sections/settings/BillingV2/gateway-credits/links";

import {
  classify,
  readManagedFailure,
  readManagedRefusal,
} from "../managedRefusals";

// The nine wire pairs in the gateway contract, plus credits-off from the
// parity request. UNTYPED-UNPRICED is an audit label, not a wire error.code.
const ROWS = [
  [
    402,
    "managed_credits_exhausted",
    "out_of_credits",
    "Future AGI credits are used up",
    "Top up credits",
  ],
  [
    402,
    "managed_credits_insufficient",
    "out_of_credits",
    "Not enough Future AGI credits",
    "Top up credits",
  ],
  [
    402,
    "agent_budget_exhausted",
    "agent_budget",
    "Agent budget exhausted",
    "Review the agent run budget",
  ],
  [
    429,
    "budget_exceeded",
    "budget_limit",
    "Gateway budget exceeded",
    "Open gateway budgets",
  ],
  [
    429,
    "org_budget_exceeded",
    "budget_limit",
    "Organization budget exceeded",
    "Open gateway budgets",
  ],
  [
    429,
    "managed_spend_velocity",
    "rate_limit",
    "Managed spending is limited",
    "Review the spending limit with an administrator",
  ],
  [
    429,
    "rate_limit_exceeded",
    "rate_limit",
    "Rate limit reached",
    "Try again after waiting",
  ],
  [
    503,
    "managed_billing_unavailable",
    "billing_unavailable",
    "Billing is temporarily unavailable",
    "Try again later",
  ],
  [
    403,
    "future_agi_credits_off",
    "permission",
    "Future AGI credits are off",
    "Ask an Owner or Admin to contact your Future AGI account team",
  ],
  [
    403,
    "permission_denied",
    "permission",
    "Model pricing is unavailable",
    "Choose a priced model or use your own provider key",
  ],
  [418, "unknown_refusal", "other", "Request failed", null],
];

const UNPRICED = "model pricing is not available for Future AGI credits";
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

describe("managed refusal contract", () => {
  it.each(ROWS)("maps %i / %s", (status, code, category, title, action) => {
    const message = code === "permission_denied" ? UNPRICED : "Server detail";
    const result = classify(status, code, { message });
    expect(Object.keys(result).sort()).toEqual([
      "action",
      "body",
      "category",
      "title",
    ]);
    expect(result).toMatchObject({ category, title });
    expect(result.action?.label ?? null).toBe(action);
    expect(result.body).not.toContain(message);
  });

  it.each(ROWS.filter(([, , category]) => category !== "other"))(
    "uses value-free copy for %i / %s",
    (status, code) => {
      const message =
        code === "permission_denied"
          ? `${UNPRICED}: ${RUNTIME_DETAIL}`
          : RUNTIME_DETAIL;
      const result = readManagedRefusal({
        ...envelope(status, code),
        body: {
          error: {
            code,
            message,
            metadata: {
              scope: "run:fixture-run",
              available_usd: 12.345678,
              required_usd: 98.765432,
            },
          },
        },
      });
      expect(result.category).not.toBe("other");
      expect(result.body).not.toMatch(
        /\$|12\.345678|98\.765432|fixture-run|run:/,
      );
      expect(result.body).not.toContain(message);
    },
  );

  it("uses central Billing credits and gateway budget routes", () => {
    expect(classify(402, "managed_credits_exhausted").action.href).toBe(
      `${paths.dashboard.settings.billing}#${CREDITS_ANCHOR}`,
    );
    expect(classify(429, "budget_exceeded").action.href).toBe(
      paths.dashboard.gateway.budgets,
    );
    expect(classify(429, "budget_exceeded").body).not.toMatch(/cap/i);
  });

  it("explains an in-flight hold without top-up, runtime values or automatic retry", () => {
    const result = classify(402, "managed_credits_insufficient", {
      limit_source: "agentcc_in_flight",
      remedy_hint: "retry",
      message: RUNTIME_DETAIL,
    });
    expect(result.category).toBe("out_of_credits");
    expect(result.body).toMatch(/Other requests are using your balance/);
    expect(result.action).toEqual({
      label: "Try again after those requests finish",
    });
    expect(result.body).toMatch(/not retry automatically/);
    expect(result.body).not.toMatch(/\$|fixture-run|12\.345678|98\.765432/);
  });

  it("does not offer a dead enable link on this head", () => {
    const result = classify(403, "future_agi_credits_off");
    expect(result.action).toEqual({
      label: "Ask an Owner or Admin to contact your Future AGI account team",
    });
    expect(result.body).toMatch(/Owner or Admin/);
  });

  it.each([
    "Permission denied",
    "Model access is denied",
    "Pricing permission denied",
    "",
  ])(
    "does not mistake other permission failures for missing managed pricing: %s",
    (message) => {
      expect(classify(403, "permission_denied", { message }).category).toBe(
        "permission",
      );
    },
  );

  it("requires the status/code pair and keeps unknown prose value-free", () => {
    expect(
      classify(500, "managed_credits_exhausted", {
        message: "Different failure",
      }),
    ).toEqual({
      category: "other",
      title: "Request failed",
      body: "The gateway refused the request. The console will not retry automatically.",
      action: null,
    });
    expect(
      classify(429, undefined, { type: "rate_limit_error" }).category,
    ).toBe("other");
    expect(classify(400, "bad_input", null).body).toBe(
      "The gateway refused the request. The console will not retry automatically.",
    );
  });

  it.each(["12", 0])(
    "shows Retry-After %s without scheduling a retry",
    (retryAfter) => {
      const result = classify(429, "rate_limit_exceeded", { retryAfter });
      expect(result.body).toContain(`Retry-After: ${retryAfter}`);
      expect(result.action).toEqual({ label: "Try again after waiting" });
      expect(result.body).toMatch(/not retry automatically/);
    },
  );

  it("reads code and metadata from error, not from ambiguous type or outer metadata", () => {
    const result = envelope(402, "managed_credits_insufficient", {
      limit_source: "agentcc_in_flight",
      remedy_hint: "retry",
    });
    result.metadata = { limit_source: "something_else" };
    expect(readManagedRefusal(result).body).toMatch(
      /Other requests are using your balance/,
    );
    expect(readManagedRefusal(envelope(429, "budget_exceeded")).category).toBe(
      "budget_limit",
    );
  });

  it("reads mixed-case response headers and JSON-string error bodies", () => {
    const result = envelope(429, "rate_limit_exceeded");
    result.guardrail_headers = { "rEtRy-AfTeR": "7" };
    result.body = JSON.stringify(result.body);
    expect(readManagedRefusal(result).body).toContain("Retry-After: 7");
  });

  it("does not classify successes or guardrail blocks as managed refusals", () => {
    expect(
      readManagedRefusal(envelope(200, "managed_credits_exhausted")),
    ).toBeNull();
    expect(
      readManagedRefusal({
        ...envelope(403, "content_blocked"),
        blocked: true,
      }),
    ).toBeNull();
    expect(
      readManagedRefusal({
        status_code: 403,
        body: { error: { type: "guardrail_error" } },
      }),
    ).toMatchObject({ category: "other", action: null });
    expect(readManagedRefusal({ status_code: 403 })).toMatchObject({
      category: "other",
      action: null,
    });
  });

  it("reads direct HTTP errors in the shared Axios and raw Axios shapes", () => {
    const result = envelope(429, "managed_spend_velocity", {
      remedy_hint: "retry",
    });
    expect(
      readManagedFailure({
        ...result.body,
        statusCode: 429,
        responseHeaders: { "Retry-After": "3" },
      }).body,
    ).toContain("Retry-After: 3");
    expect(
      readManagedFailure({
        response: {
          status: 429,
          data: result.body,
          headers: { "retry-after": "4" },
        },
      }).body,
    ).toContain("Retry-After: 4");
    expect(readManagedFailure(new Error("Network unavailable"))).toBeNull();
  });
});
