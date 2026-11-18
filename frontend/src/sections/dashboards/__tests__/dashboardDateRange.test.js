import { describe, it, expect } from "vitest";
import {
  getDateRange,
  resolveGlobalDateRange,
  buildTimeRangePayload,
  resolveInitialTimeRange,
  toTimeRangePayload,
  DEFAULT_DATE_PRESET,
} from "../dashboardDateRange";

const NOW = new Date("2026-06-15T10:30:00.000Z");
const DAY = 24 * 60 * 60 * 1000;

describe("getDateRange", () => {
  it("returns null for falsy / unknown / custom presets", () => {
    expect(getDateRange(null, NOW)).toBeNull();
    expect(getDateRange(undefined, NOW)).toBeNull();
    expect(getDateRange("", NOW)).toBeNull();
    expect(getDateRange("custom", NOW)).toBeNull();
    expect(getDateRange("nonsense", NOW)).toBeNull();
  });

  it("today → from local midnight to now", () => {
    const expStart = new Date(NOW);
    expStart.setHours(0, 0, 0, 0);
    expect(getDateRange("today", NOW)).toEqual({
      start: expStart.toISOString(),
      end: NOW.toISOString(),
    });
  });

  it("yesterday → full previous local day (00:00 → 23:59:59.999)", () => {
    const expStart = new Date(NOW);
    expStart.setDate(expStart.getDate() - 1);
    expStart.setHours(0, 0, 0, 0);
    const expEnd = new Date(NOW);
    expEnd.setDate(expEnd.getDate() - 1);
    expEnd.setHours(23, 59, 59, 999);
    expect(getDateRange("yesterday", NOW)).toEqual({
      start: expStart.toISOString(),
      end: expEnd.toISOString(),
    });
  });

  it("30m → exactly 30 minutes before now, ending now", () => {
    const r = getDateRange("30m", NOW);
    expect(r.end).toBe(NOW.toISOString());
    expect(NOW.getTime() - new Date(r.start).getTime()).toBe(30 * 60 * 1000);
  });

  it("6h → exactly 6 hours before now, ending now", () => {
    const r = getDateRange("6h", NOW);
    expect(r.end).toBe(NOW.toISOString());
    expect(NOW.getTime() - new Date(r.start).getTime()).toBe(
      6 * 60 * 60 * 1000,
    );
  });

  it("7D → exactly 7 days before now, ending now", () => {
    const r = getDateRange("7D", NOW);
    expect(r.end).toBe(NOW.toISOString());
    expect(NOW.getTime() - new Date(r.start).getTime()).toBe(7 * DAY);
  });

  it("30D → exactly 30 days before now, ending now", () => {
    const r = getDateRange("30D", NOW);
    expect(r.end).toBe(NOW.toISOString());
    expect(NOW.getTime() - new Date(r.start).getTime()).toBe(30 * DAY);
  });

  it.each([
    ["3M", 3],
    ["6M", 6],
    ["12M", 12],
  ])("%s → N months before now, ending now", (preset, months) => {
    const expStart = new Date(NOW);
    expStart.setMonth(expStart.getMonth() - months);
    expect(getDateRange(preset, NOW)).toEqual({
      start: expStart.toISOString(),
      end: NOW.toISOString(),
    });
  });

  it("does not mutate the reference date", () => {
    const ref = new Date(NOW);
    getDateRange("yesterday", ref);
    expect(ref.toISOString()).toBe(NOW.toISOString());
  });

  it("every preset returns start strictly before end", () => {
    for (const p of [
      "30m",
      "6h",
      "today",
      "yesterday",
      "7D",
      "30D",
      "3M",
      "6M",
      "12M",
    ]) {
      const r = getDateRange(p, NOW);
      expect(new Date(r.start).getTime()).toBeLessThan(
        new Date(r.end).getTime(),
      );
    }
  });
});

describe("resolveGlobalDateRange", () => {
  it("null preset (Default) → null", () => {
    expect(resolveGlobalDateRange(null, null, NOW)).toBeNull();
  });

  it("custom with both ends → ISO start/end from the picked dates", () => {
    const start = new Date("2026-03-26T00:00:00.000Z");
    const end = new Date("2026-04-29T00:00:00.000Z");
    expect(resolveGlobalDateRange("custom", [start, end], NOW)).toEqual({
      start: start.toISOString(),
      end: end.toISOString(),
    });
  });

  it("custom with a missing / incomplete range → null", () => {
    const d = new Date("2026-03-26T00:00:00.000Z");
    expect(resolveGlobalDateRange("custom", null, NOW)).toBeNull();
    expect(resolveGlobalDateRange("custom", [], NOW)).toBeNull();
    expect(resolveGlobalDateRange("custom", [d, null], NOW)).toBeNull();
    expect(resolveGlobalDateRange("custom", [null, d], NOW)).toBeNull();
  });

  it("a preset delegates to getDateRange", () => {
    expect(resolveGlobalDateRange("30D", null, NOW)).toEqual(
      getDateRange("30D", NOW),
    );
  });

  it("preset takes precedence; customDateRange is ignored unless preset is 'custom'", () => {
    const d = new Date("2020-01-01T00:00:00.000Z");
    expect(resolveGlobalDateRange("7D", [d, d], NOW)).toEqual(
      getDateRange("7D", NOW),
    );
  });

  it("custom with a malformed date → null (no Invalid Date crash)", () => {
    const good = new Date("2026-03-26T00:00:00.000Z");
    expect(
      resolveGlobalDateRange("custom", [good, new Date("nope")], NOW),
    ).toBeNull();
  });
});

describe("buildTimeRangePayload", () => {
  it("custom range with valid dates → { custom_start, custom_end }", () => {
    const start = new Date("2026-03-26T00:00:00.000Z");
    const end = new Date("2026-04-29T00:00:00.000Z");
    expect(buildTimeRangePayload("custom", [start, end])).toEqual({
      custom_start: start.toISOString(),
      custom_end: end.toISOString(),
    });
  });

  it("a contract preset → { preset }", () => {
    expect(buildTimeRangePayload("7D")).toEqual({ preset: "7D" });
  });

  it("custom without a range → falls back to the default preset", () => {
    expect(buildTimeRangePayload("custom", null)).toEqual({
      preset: DEFAULT_DATE_PRESET,
    });
  });

  it("custom with a malformed date → falls back to the default preset", () => {
    const good = new Date("2026-03-26T00:00:00.000Z");
    expect(buildTimeRangePayload("custom", [good, new Date("nope")])).toEqual({
      preset: DEFAULT_DATE_PRESET,
    });
  });

  it("an off-contract preset → normalized to the default (no off-enum value ships)", () => {
    expect(buildTimeRangePayload("not-a-preset")).toEqual({
      preset: DEFAULT_DATE_PRESET,
    });
  });
});

describe("resolveInitialTimeRange", () => {
  it("saved custom range (no url preset) → custom UI state with Date objects", () => {
    const saved = {
      custom_start: "2026-03-26T00:00:00.000Z",
      custom_end: "2026-04-29T00:00:00.000Z",
    };
    const r = resolveInitialTimeRange(saved, null);
    expect(r.timePreset).toBe("custom");
    expect(r.customDateRange[0].toISOString()).toBe(saved.custom_start);
    expect(r.customDateRange[1].toISOString()).toBe(saved.custom_end);
  });

  it("url preset overrides a saved custom range", () => {
    const saved = {
      custom_start: "2026-03-26T00:00:00.000Z",
      custom_end: "2026-04-29T00:00:00.000Z",
    };
    expect(resolveInitialTimeRange(saved, "7D")).toEqual({
      timePreset: "7D",
      customDateRange: null,
    });
  });

  it("saved preset (no custom) → preset UI state", () => {
    expect(resolveInitialTimeRange({ preset: "3M" }, null)).toEqual({
      timePreset: "3M",
      customDateRange: null,
    });
  });

  it("nothing saved → default preset", () => {
    expect(resolveInitialTimeRange(null, null)).toEqual({
      timePreset: DEFAULT_DATE_PRESET,
      customDateRange: null,
    });
  });

  it("malformed saved custom range → default preset (no Invalid Date crash)", () => {
    const saved = { custom_start: "garbage", custom_end: "also-garbage" };
    expect(resolveInitialTimeRange(saved, null)).toEqual({
      timePreset: DEFAULT_DATE_PRESET,
      customDateRange: null,
    });
  });
});

describe("toTimeRangePayload", () => {
  it("maps { start, end } → { custom_start, custom_end }", () => {
    expect(
      toTimeRangePayload({
        start: "2026-01-01T00:00:00.000Z",
        end: "2026-02-01T00:00:00.000Z",
      }),
    ).toEqual({
      custom_start: "2026-01-01T00:00:00.000Z",
      custom_end: "2026-02-01T00:00:00.000Z",
    });
  });

  it("null / incomplete range → null", () => {
    expect(toTimeRangePayload(null)).toBeNull();
    expect(toTimeRangePayload({ start: "x" })).toBeNull();
    expect(toTimeRangePayload({ end: "x" })).toBeNull();
  });
});
