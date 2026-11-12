import { describe, expect, it } from "vitest";

import {
  TracerObservabilityProviderCreateBody,
  TracerObservabilityProviderReadResponse,
} from "src/generated/api-contracts/api.zod";

const health = {
  status: "misconfigured",
  reason_code: "missing_key",
  first_failed_at: "2026-01-01T00:00:00Z",
  last_failed_at: "2026-01-01T00:00:00Z",
  failure_count: 1,
  next_retry_at: "2026-01-01T00:10:00Z",
};

describe("generated provider configuration health", () => {
  it.each([null, health])(
    "accepts the API health state %j",
    (configurationHealth) => {
      const payload = {
        provider: "retell",
        configuration_health: configurationHealth,
      };
      expect(TracerObservabilityProviderReadResponse.parse(payload)).toEqual(
        payload,
      );
    },
  );

  it("does not send read-only health in create requests", () => {
    expect(
      TracerObservabilityProviderCreateBody.parse({
        provider: "retell",
        configuration_health: health,
      }),
    ).toEqual({ provider: "retell" });
  });

  it("rejects unclassified failure details", () => {
    expect(
      TracerObservabilityProviderReadResponse.safeParse({
        provider: "retell",
        configuration_health: { ...health, reason_code: "raw_provider_error" },
      }).success,
    ).toBe(false);
  });
});
