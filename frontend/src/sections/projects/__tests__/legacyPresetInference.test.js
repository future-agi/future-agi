import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { add, startOfToday, sub } from "date-fns";
import { inferPreset, inferPresetForLegacy } from "../legacyPresetInference";
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

// The Add Evals entrypoint hands us a window with no stored preset, so the
// preset has to be measured back out. Today/Yesterday are day-granular
// matches — any same-day window infers Today, and any window straddling one
// midnight infers Yesterday — so re-anchoring those would rewrite the user's
// range. Only relative presets are safe to carry over.
describe("inferPresetForLegacy", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-08-21T06:00:00Z"));
  });
  afterEach(() => vi.useRealTimers());

  it("keeps a partial same-day window instead of widening it to a full day", () => {
    const start = add(startOfToday(), { hours: 8 });
    const end = add(startOfToday(), { hours: 15 });
    expect(inferPreset(start, end)).toBe("Today");
    expect(inferPresetForLegacy(start, end)).toBe("Custom");
  });

  it("keeps a window that straddles midnight instead of snapping it to yesterday", () => {
    const start = sub(startOfToday(), { hours: 1 });
    const end = add(startOfToday(), { hours: 23 });
    expect(inferPreset(start, end)).toBe("Yesterday");
    expect(inferPresetForLegacy(start, end)).toBe("Custom");
  });

  it("downgrades the exact Today and Yesterday windows to Custom", () => {
    expect(inferPresetForLegacy(...presetToRange("Today"))).toBe("Custom");
    expect(inferPresetForLegacy(...presetToRange("Yesterday"))).toBe("Custom");
  });

  it("passes every other preset through untouched", () => {
    for (const { title } of TIME_PERIOD_OPTIONS) {
      if (title === "Today" || title === "Yesterday") continue;
      expect(inferPresetForLegacy(...presetToRange(title))).toBe(title);
    }
  });
});
