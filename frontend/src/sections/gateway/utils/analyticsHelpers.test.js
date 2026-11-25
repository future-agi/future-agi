import { describe, expect, it } from "vitest";
import { computeGranularity } from "./analyticsHelpers";

// The bucket sizes the analytics API accepts (TRUNC_MAP in
// futureagi/agentcc/services/analytics.py). It serves anything else as "hour".
const API_GRANULARITIES = ["minute", "hour", "day", "week", "month"];

const END = "2026-01-31T12:00:00.000Z";
const startBefore = (hours) =>
  new Date(new Date(END).getTime() - hours * 60 * 60 * 1000).toISOString();

describe("computeGranularity", () => {
  it.each([
    ["1h", 1, "minute"],
    ["6h", 6, "minute"],
    ["24h", 24, "hour"],
    ["7d", 7 * 24, "hour"],
    ["30d", 30 * 24, "day"],
    ["90d", 90 * 24, "day"],
  ])(
    "asks for a bucket size the API accepts over %s",
    (_preset, hours, expected) => {
      const granularity = computeGranularity(startBefore(hours), END);

      expect(API_GRANULARITIES).toContain(granularity);
      expect(granularity).toBe(expected);
    },
  );

  it("uses the API's own default when the range is missing", () => {
    expect(computeGranularity(undefined, END)).toBe("hour");
    expect(computeGranularity(startBefore(6), undefined)).toBe("hour");
  });
});
