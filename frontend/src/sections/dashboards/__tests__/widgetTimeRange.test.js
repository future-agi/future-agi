import { describe, it, expect } from "vitest";
import {
  buildTimeRangePayload,
  resolveInitialTimeRange,
  DEFAULT_TIME_PRESET,
} from "../widgetTimeRange";

const start = new Date("2026-05-31T18:30:00.000Z");
const end = new Date("2026-07-30T18:30:00.000Z");

describe("buildTimeRangePayload", () => {
  it("sends custom_start/custom_end for a picked custom range", () => {
    expect(buildTimeRangePayload("custom", [start, end])).toEqual({
      custom_start: start.toISOString(),
      custom_end: end.toISOString(),
    });
  });

  it("never emits preset:'custom' when custom has no range yet", () => {
    const payload = buildTimeRangePayload("custom", null);
    expect(payload).toEqual({ preset: DEFAULT_TIME_PRESET });
    expect(payload.preset).not.toBe("custom");
  });

  it("passes through a normal preset", () => {
    expect(buildTimeRangePayload("7D", null)).toEqual({ preset: "7D" });
    expect(buildTimeRangePayload("30D", null)).toEqual({ preset: "30D" });
  });
});

describe("resolveInitialTimeRange", () => {
  it("restores a saved custom range as custom + dates (not a preset)", () => {
    const result = resolveInitialTimeRange(
      { custom_start: start.toISOString(), custom_end: end.toISOString() },
      null,
    );
    expect(result.timePreset).toBe("custom");
    expect(result.customDateRange[0]).toEqual(start);
    expect(result.customDateRange[1]).toEqual(end);
  });

  it("restores a saved preset", () => {
    expect(resolveInitialTimeRange({ preset: "7D" }, null)).toEqual({
      timePreset: "7D",
      customDateRange: null,
    });
  });

  it("lets a URL preset override a saved custom range", () => {
    const result = resolveInitialTimeRange(
      { custom_start: start.toISOString(), custom_end: end.toISOString() },
      "7D",
    );
    expect(result.timePreset).toBe("7D");
    expect(result.customDateRange).toBeNull();
  });

  it("defaults to 30D when nothing is saved", () => {
    expect(resolveInitialTimeRange(undefined, null)).toEqual({
      timePreset: DEFAULT_TIME_PRESET,
      customDateRange: null,
    });
  });
});
