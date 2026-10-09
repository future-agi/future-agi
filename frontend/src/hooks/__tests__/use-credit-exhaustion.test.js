import { describe, it, expect } from "vitest";
import {
  isCreditExhaustionError,
  isEnterpriseGateError,
} from "src/hooks/use-credit-exhaustion";

// The rejected axios error: the response body plus statusCode
// (src/utils/axios.js), as the backend's 402 envelope shapes it.
const editionGate = {
  status: false,
  type: "entitlement_error",
  code: "ENTERPRISE_FEATURE_REQUIRED",
  message:
    "Community includes up to 3 organization members. More members are an Enterprise feature.",
  upgrade_required: true,
  error: { code: "ENTERPRISE_FEATURE_REQUIRED" },
  enterprise_gate: {
    feature: "members",
    edition: "community",
    limit: 3,
    current: 3,
    requested: 1,
    license_state: "missing",
    contact: "sales@futureagi.com",
    activation_route: "/dashboard/settings/ee-licenses",
  },
  statusCode: 402,
};

describe("isEnterpriseGateError (TH-8084 AC-08)", () => {
  it("recognises the edition gate by code", () => {
    expect(isEnterpriseGateError(editionGate)).toBe(true);
    expect(
      isEnterpriseGateError({
        code: "ENTERPRISE_FEATURE_REQUIRED",
        statusCode: 402,
      }),
    ).toBe(true);
  });

  it("recognises a product gate by its enterprise_gate block", () => {
    expect(
      isEnterpriseGateError({
        code: "LICENSE_MISSING",
        statusCode: 402,
        enterprise_gate: { feature: "falcon_ai" },
      }),
    ).toBe(true);
  });

  it("is false for other errors", () => {
    expect(isEnterpriseGateError(null)).toBe(false);
    expect(
      isEnterpriseGateError({ statusCode: 402, error_code: "FREE_TIER_LIMIT" }),
    ).toBe(false);
    expect(isEnterpriseGateError({ statusCode: 429 })).toBe(false);
  });
});

describe("isCreditExhaustionError never claims the Enterprise gate (R10)", () => {
  it("returns false for the 402 Enterprise gate", () => {
    expect(isCreditExhaustionError(editionGate)).toBe(false);
  });

  it("still flags Cloud credit exhaustion", () => {
    expect(
      isCreditExhaustionError({
        statusCode: 402,
        error_code: "FREE_TIER_LIMIT",
      }),
    ).toBe(true);
    expect(isCreditExhaustionError({ statusCode: 402 })).toBe(true);
    expect(isCreditExhaustionError({ error_code: "BUDGET_PAUSED" })).toBe(true);
  });
});
