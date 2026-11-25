import { describe, expect, it } from "vitest";

import {
  TracerDerivedMetricsActivateResponse,
  TracerDerivedMetricsListResponse,
  TracerDerivedMetricsReadResponse,
  TracerDerivedMetricsUpdateResponse,
} from "src/generated/api-contracts/api.zod";

const contracts = [
  [
    "list",
    TracerDerivedMetricsListResponse.shape.results.element.shape.calculation_paused,
  ],
  ["detail", TracerDerivedMetricsReadResponse.shape.calculation_paused],
  ["update", TracerDerivedMetricsUpdateResponse.shape.calculation_paused],
  ["activate", TracerDerivedMetricsActivateResponse.shape.calculation_paused],
];
const busy = {
  reason: "too_busy",
  since: "2026-11-17T00:00:00Z",
  rows: null,
  limit: 5000,
  work: { owners: 3200, captures: 100, head_generation: 4 },
};

for (const [name, contract] of contracts) {
  describe(`${name} metric pause contract`, () => {
    it("accepts an unpaused project without inventing a pause", () => {
      expect(contract.parse(null)).toBeNull();
    });

    it("preserves a busy pause with no measured row count", () => {
      expect(contract.parse(busy)).toEqual(busy);
    });

    it("preserves measured counts for size pauses", () => {
      const large = { ...busy, reason: "too_large", rows: 6000 };
      delete large.work;
      expect(contract.parse(large)).toEqual(large);
    });

    it("rejects invalid counts and unknown pause reasons", () => {
      for (const invalid of [
        { ...busy, rows: -1 },
        { ...busy, work: { captures: -1 } },
        { ...busy, reason: "unknown" },
      ]) {
        expect(contract.safeParse(invalid).success).toBe(false);
      }
    });
  });
}
