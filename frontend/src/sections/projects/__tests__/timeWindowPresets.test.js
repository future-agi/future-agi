import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { format } from "date-fns";
import {
  TIME_PERIOD_OPTIONS,
  presetToRange,
  presetToToken,
  tokenToPreset,
  formatTimeWindow,
} from "../timeWindowPresets";

const freezeClock = () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-08-21T06:00:00Z"));
  });
  afterEach(() => vi.useRealTimers());
};

describe("tokens", () => {
  it("round-trips every chip title", () => {
    for (const { title } of TIME_PERIOD_OPTIONS) {
      expect(tokenToPreset(presetToToken(title))).toBe(title);
    }
    expect(tokenToPreset(presetToToken("Custom"))).toBe("Custom");
  });

  it("uses the agreed wire tokens", () => {
    expect(presetToToken("12M")).toBe("12m");
    expect(presetToToken("30 mins")).toBe("30m");
    expect(presetToToken("Today")).toBe("today");
    expect(presetToToken("Custom")).toBe("custom");
  });

  it("returns null for an unknown token", () => {
    expect(tokenToPreset("nonsense")).toBeNull();
    expect(tokenToPreset(undefined)).toBeNull();
  });
});

describe("presetToRange", () => {
  freezeClock();

  it("returns null for Custom and unknown keys", () => {
    expect(presetToRange("Custom")).toBeNull();
    expect(presetToRange("nonsense")).toBeNull();
  });

  it("produces an ordered range for every preset", () => {
    for (const { title } of TIME_PERIOD_OPTIONS) {
      const [from, to] = presetToRange(title);
      expect(from.getTime()).toBeLessThan(to.getTime());
    }
  });

  // The whole point of the parameter is to compute a window without touching
  // the global clock. Honouring it for the start but not the end produced a
  // range spanning from the given instant to the real today.
  describe("with an explicit now", () => {
    const NOW = new Date("2020-01-15T10:00:00");

    it("anchors Today and Yesterday to the given day", () => {
      const [tFrom, tTo] = presetToRange("Today", NOW);
      expect(format(tFrom, "yyyy-MM-dd HH:mm")).toBe("2020-01-15 00:00");
      expect(format(tTo, "yyyy-MM-dd HH:mm")).toBe("2020-01-16 00:00");

      const [yFrom, yTo] = presetToRange("Yesterday", NOW);
      expect(format(yFrom, "yyyy-MM-dd HH:mm")).toBe("2020-01-14 00:00");
      expect(format(yTo, "yyyy-MM-dd HH:mm")).toBe("2020-01-15 00:00");
    });

    it("ends the duration presets on the day after the given day", () => {
      for (const { title } of TIME_PERIOD_OPTIONS) {
        const [, to] = presetToRange(title, NOW);
        expect(to.getFullYear()).toBe(2020);
      }
    });

    it("keeps every preset's span the length it claims", () => {
      const days = (title) => {
        const [from, to] = presetToRange(title, NOW);
        return (to.getTime() - from.getTime()) / 86400000;
      };
      expect(days("7D")).toBeGreaterThan(7);
      expect(days("7D")).toBeLessThan(9);
      expect(days("12M")).toBeLessThan(370);
    });
  });
});

describe("formatTimeWindow", () => {
  freezeClock();

  it("collapses a single-day window to one date", () => {
    expect(formatTimeWindow(...presetToRange("Today"))).toBe("21 Aug 2026");
    expect(formatTimeWindow(...presetToRange("Yesterday"))).toBe("20 Aug 2026");
  });

  it("includes times for a sub-day window", () => {
    expect(formatTimeWindow(...presetToRange("6 hrs"))).toMatch(
      /^21 Aug 2026, .+ – .+$/,
    );
  });

  it("shows both dates when a sub-day window crosses midnight", () => {
    expect(
      formatTimeWindow("2026-08-20 23:50:00", "2026-08-21 00:20:00"),
    ).toMatch(/^20 Aug 2026 .+ – 21 Aug 2026 .+$/);
  });

  it("shows a plain range when the window spans days", () => {
    expect(formatTimeWindow(...presetToRange("7D"))).toBe(
      "14 Aug 2026 – 21 Aug 2026",
    );
  });

  it("keeps a Custom end verbatim", () => {
    expect(
      formatTimeWindow("2026-06-01 00:00:00", "2026-07-01 00:00:00", {
        isCustom: true,
      }),
    ).toBe("01 Jun 2026 – 01 Jul 2026");
  });

  it("returns an empty string for missing or reversed bounds", () => {
    expect(formatTimeWindow(null, "2026-07-01 00:00:00")).toBe("");
    expect(formatTimeWindow("2026-07-01 00:00:00", undefined)).toBe("");
    expect(formatTimeWindow("2026-07-01 00:00:00", "2026-06-01 00:00:00")).toBe(
      "",
    );
  });
});
