import { describe, expect, it } from "vitest";

import { computeGranularity } from "./analyticsHelpers";

// Must match TRUNC_MAP in futureagi/agentcc/services/analytics.py; the backend coerces any other token to "hour".
const BACKEND_GRANULARITIES = ["minute", "hour", "day", "week", "month"];

const range = (hours) => ({
  start: new Date("2026-01-01T00:00:00Z").toISOString(),
  end: new Date(
    new Date("2026-01-01T00:00:00Z").getTime() + hours * 60 * 60 * 1000,
  ).toISOString(),
});

describe("computeGranularity", () => {
  it("returns 'hour' when the range is missing", () => {
    expect(computeGranularity(null, null)).toBe("hour");
    expect(computeGranularity("2026-01-01T00:00:00Z", null)).toBe("hour");
  });

  it("maps range length to the expected bucket size", () => {
    const { start, end } = range(6);
    expect(computeGranularity(start, end)).toBe("minute");
    expect(computeGranularity(...Object.values(range(24)))).toBe("hour");
    expect(computeGranularity(...Object.values(range(168)))).toBe("hour");
    expect(computeGranularity(...Object.values(range(720)))).toBe("day");
    expect(computeGranularity(...Object.values(range(2160)))).toBe("week");
  });

  it("only emits tokens the analytics backend accepts", () => {
    [1, 6, 12, 24, 72, 168, 400, 720, 2160, 9000].forEach((hours) => {
      const { start, end } = range(hours);
      expect(BACKEND_GRANULARITIES).toContain(computeGranularity(start, end));
    });
  });
});
