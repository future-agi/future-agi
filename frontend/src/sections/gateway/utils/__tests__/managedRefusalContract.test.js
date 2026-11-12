import { describe, expect, it } from "vitest";

import {
  classify,
  readManagedFailure,
  readManagedRefusal,
} from "../managedRefusals";
import { CONTRACT_CASES } from "./managedRefusalContractCases";

const UNTRUSTED = "fixture-model; run:fixture-run has $12.345678 left";
const envelope = ({ status, code }, metadata) => ({
  status_code: status,
  body: {
    error: { code, message: UNTRUSTED, type: "untrusted_type", metadata },
  },
});

describe("authoritative r3 refusal table", () => {
  it("pins all 49 current rows and two explicitly forward-compatible rows", () => {
    expect(CONTRACT_CASES).toHaveLength(51);
    expect(
      new Set(CONTRACT_CASES.map(({ status, code }) => `${status}:${code}`))
        .size,
    ).toBe(51);
  });

  it.each(CONTRACT_CASES)(
    "maps $status / $code without requiring metadata",
    (row) => {
      const { category, title, action } = row;
      const expected = { category, title, action: { label: action } };
      const baseline = readManagedRefusal(envelope(row));
      expect(baseline).toMatchObject(expected);
      expect(Object.keys(baseline).sort()).toEqual([
        "action",
        "body",
        "category",
        "evidence",
        "title",
      ]);
      expect(baseline.body).not.toContain(UNTRUSTED);
      expect(baseline.body).not.toMatch(/fixture-model|fixture-run|12\.345678/);
      // Body dispatched metadata is ignored; only response headers are evidence.
      for (const dispatched of [false, true, null, "false"]) {
        const result = envelope(row, {
          dispatched,
          model: "fixture-model",
          reason: "future_reason",
        });
        expect(readManagedRefusal(result)).toEqual(baseline);
        expect(
          readManagedFailure({ statusCode: row.status, ...result.body }),
        ).toEqual(baseline);
        expect(
          readManagedFailure({
            response: { status: row.status, data: result.body },
          }),
        ).toEqual(baseline);
        expect(
          readManagedRefusal({ ...result, body: JSON.stringify(result.body) }),
        ).toEqual(baseline);
      }
    },
  );

  it.each(CONTRACT_CASES)(
    "fails closed on a wrong status for $code",
    ({ code }) => {
      expect(classify(418, code, { message: UNTRUSTED })).toEqual({
        category: "other",
        title: "Request failed",
        body: "The gateway refused the request. The console will not retry automatically.",
        action: null,
      });
    },
  );
});

describe("conditional credit guidance", () => {
  for (const limit_source of [
    undefined,
    null,
    "agentcc_credits",
    "agentcc_in_flight",
  ]) {
    for (const remedy_hint of [
      undefined,
      null,
      "add_credits",
      "retry",
      "Retry",
    ]) {
      it(`requires both exact gates: ${limit_source} / ${remedy_hint}`, () => {
        const row = { status: 402, code: "managed_credits_insufficient" };
        const wire = envelope(row, { limit_source, remedy_hint });
        wire.metadata = {
          limit_source: "agentcc_in_flight",
          remedy_hint: "retry",
        };
        const result = readManagedRefusal(wire);
        expect(result.category).toBe("out_of_credits");
        if (limit_source === "agentcc_in_flight" && remedy_hint === "retry") {
          expect(result.action).toEqual({
            label: "Try again after those requests finish",
          });
          expect(result.body).toContain("not retry automatically");
        } else {
          expect(result.action.label).toBe("Top up credits");
          expect(result.body).not.toMatch(/try again|retry|wait/i);
        }
      });
    }
  }
});

describe("authentication and permission vocabulary", () => {
  it.each([
    undefined,
    null,
    "missing_api_key",
    "unknown_api_key",
    "future_agi_api_key",
    "not_a_gateway_key",
    "future_reason",
  ])("maps invalid keys without exposing key existence: %s", (reason) => {
    const result = readManagedRefusal(
      envelope({ status: 401, code: "invalid_api_key" }, { reason }),
    );
    expect(result.category).toBe("invalid_key");
    expect(result.action).toEqual({
      label: "Open gateway API keys",
      href: "/dashboard/gateway/keys",
    });
    expect(result.body).toContain("gateway API key");
    expect(result.body).not.toMatch(/revoked|expired|deleted|exists|fixture/i);
  });

  it.each([
    {},
    { message: "model pricing is not available for Future AGI credits" },
    { unpricedModel: true },
    { source: "typed DSX managed-route refusal", reason: "unpriced" },
    { message: "Model access is denied" },
  ])(
    "keeps every legacy permission_denied variant in permission: %j",
    (metadata) => {
      const result = classify(403, "permission_denied", metadata);
      expect(result.category).toBe("permission");
      expect(result.action).not.toBeNull();
    },
  );

  it("keeps legacy unpriced guidance but reserves unpriced_model for the typed code", () => {
    const legacy = classify(403, "permission_denied", {
      message: "model pricing is not available for Future AGI credits",
    });
    const typed = classify(403, "managed_model_unpriced", {
      model: "fixture-model",
      reason: "missing_pricing",
      message: UNTRUSTED,
    });
    expect(legacy.category).toBe("permission");
    expect(typed.category).toBe("unpriced_model");
    expect(legacy.action).toEqual(typed.action);
    expect(typed.body).not.toContain(UNTRUSTED);
  });
});

describe("default and non-waitable spend guidance", () => {
  it.each([
    undefined,
    null,
    "",
    "future_reason",
    "minute_window_full",
    "toString",
  ])(
    "does not promise waiting without the retry hint for reason %s",
    (reason) => {
      const mapped = classify(429, "managed_spend_velocity", {
        reason,
        remedy_hint: "contact_admin",
        retryAfter: "9",
      });
      expect(mapped.action).toEqual({
        label: "Review the spending limit with an administrator",
      });
      expect(mapped.body).not.toMatch(/wait|try again|Retry-After/i);
      expect(mapped.body).toContain("not retry automatically");
    },
  );

  it.each([
    "request_exceeds_minute_cap",
    "request_exceeds_hour_cap",
    "new_org_daily_cap",
  ])("does not turn explicit non-waitable reason %s into retry", (reason) => {
    const mapped = classify(429, "managed_spend_velocity", {
      reason,
      remedy_hint: "retry",
      retryAfter: "9",
      dispatched: false,
    });
    expect(mapped.action.label).toBe(
      reason === "new_org_daily_cap"
        ? "Contact support"
        : "Lower max_tokens or reduce the request",
    );
    expect(mapped.body).not.toMatch(/try again|wait|Retry-After/i);
  });

  it.each([400, 401, 402, 403, 404, 409, 410, 422, 429, 503, 504])(
    "keeps unknown HTTP %i codes generic without a replay action",
    (status) => {
      expect(
        classify(status, "unknown_future_code", {
          message: UNTRUSTED,
          dispatched: false,
          remedy_hint: "retry",
        }),
      ).toEqual({
        category: "other",
        title: "Request failed",
        body: "The gateway refused the request. The console will not retry automatically.",
        action: null,
      });
    },
  );
});
