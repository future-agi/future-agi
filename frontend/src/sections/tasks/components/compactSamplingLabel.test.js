import { describe, expect, it } from "vitest";
import { compactSamplingLabel } from "./simpleSamplingPolicy";

const closed = {
  version: 1,
  sampling_unit: "session",
  mode: "closed",
  window: { start: "2026-11-01T00:00:00Z", end: "2026-11-08T00:00:00Z" },
  default: { target_n: 10 },
  strata: [],
};

describe("compactSamplingLabel", () => {
  it("shows a count-based cohort as its count, not the legacy 100%", () => {
    expect(compactSamplingLabel(closed, 100)).toBe("10 conversations");
  });

  it("shows a live cohort rate with its unit", () => {
    const live = {
      ...closed,
      mode: "live",
      window: { start: "2026-11-01T00:00:00Z", end: null },
      default: { rate: 25 },
    };
    expect(compactSamplingLabel(live, 100)).toBe("25% of conversations");
  });

  it("totals per-group counts", () => {
    const grouped = {
      ...closed,
      default: { target_n: 5 },
      strata: [
        {
          name: "eu",
          target_n: 20,
          filters: [
            { source: "metadata", key: "region", op: "in", values: ["eu"] },
          ],
        },
      ],
    };
    expect(compactSamplingLabel(grouped, 100)).toBe("Up to 25 conversations");
  });

  it("labels policies the task screen cannot summarise as advanced", () => {
    expect(
      compactSamplingLabel({ ...closed, coordination: "project" }, 100),
    ).toBe("Advanced");
  });

  it("falls back to the legacy rate only when no policy is saved", () => {
    expect(compactSamplingLabel(null, 40)).toBe("40%");
    expect(compactSamplingLabel(undefined, null)).toBe("—");
  });
});
