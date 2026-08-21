import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { inferPreset } from "../legacyPresetInference";
import { TIME_PERIOD_OPTIONS, presetToRange } from "../timeWindowPresets";

describe("inferPreset", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-08-21T06:00:00Z"));
  });
  afterEach(() => vi.useRealTimers());

  it("recognises every preset on the day it was generated", () => {
    for (const { title } of TIME_PERIOD_OPTIONS) {
      expect(inferPreset(...presetToRange(title))).toBe(title);
    }
  });

  // Presets derive their start from an instant and carry a wall-clock time; the
  // date-only Custom calendar always starts at midnight. Duration alone cannot
  // separate a custom 30-day pick from the 30D preset — this can.
  it("classifies midnight-bounded ranges as Custom whatever their length", () => {
    expect(inferPreset("2026-06-01 00:00:00", "2026-07-01 00:00:00")).toBe(
      "Custom",
    );
    expect(inferPreset("2026-06-01 00:00:00", "2026-06-08 00:00:00")).toBe(
      "Custom",
    );
    expect(inferPreset("2025-06-01 00:00:00", "2026-06-01 00:00:00")).toBe(
      "Custom",
    );
  });

  it("still recognises a machine-generated 30-day range", () => {
    expect(inferPreset("2026-07-22 10:31:18", "2026-08-22 00:00:00")).toBe(
      "30D",
    );
  });

  it("recognises the escalation task's stored window", () => {
    expect(inferPreset("2025-07-23 18:13:32", "2026-07-23 23:59:59")).toBe(
      "12M",
    );
  });

  it("returns Custom on bad input rather than guessing", () => {
    expect(inferPreset(null, null)).toBe("Custom");
    expect(inferPreset("garbage", "garbage")).toBe("Custom");
    expect(inferPreset(undefined, "2026-07-01 00:00:00")).toBe("Custom");
  });
});
