import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { startOfToday, startOfTomorrow } from "date-fns";
import { formatDate } from "src/utils/report-utils";
import { getDefaultTaskValues } from "../common";
import { inferPreset } from "src/sections/projects/legacyPresetInference";

const task = (dateRange, extra = {}, runType = "historical") => ({
  run_type: runType,
  filters_applied: { date_range: dateRange, ...extra },
  evals_applied: [],
  spans_limit: 100000,
  sampling_rate: 50,
});

// Hydration is a pure restore. Re-anchoring here slid the window forward on
// every poll of the task-details query and misreported what a completed task
// actually ran on — it happens at save time instead.
describe("getDefaultTaskValues time window", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-08-21T06:00:00Z"));
  });
  afterEach(() => vi.useRealTimers());

  it("uses the stored preset when present", () => {
    const v = getDefaultTaskValues(
      task(["2026-08-18 00:00:00", "2026-08-19 00:00:00"], {
        date_preset: "today",
      }),
      null,
    );
    expect(v.datePreset).toBe("Today");
  });

  it("infers the preset for a legacy task", () => {
    expect(
      getDefaultTaskValues(
        task(["2025-07-23 18:13:32", "2026-07-23 23:59:59"]),
        null,
      ).datePreset,
    ).toBe("12M");
  });

  it("keeps a legacy Custom window as Custom", () => {
    expect(
      getDefaultTaskValues(
        task(["2026-06-01 00:00:00", "2026-07-01 00:00:00"]),
        null,
      ).datePreset,
    ).toBe("Custom");
  });

  // An inferred Today stops being identifiable once its day passes, so trusting
  // one would re-anchor the task onto the wrong day.
  it("downgrades an inferred Today to Custom", () => {
    const v = getDefaultTaskValues(
      task([formatDate(startOfToday()), formatDate(startOfTomorrow())]),
      null,
    );
    expect(v.datePreset).toBe("Custom");
  });

  it("returns the stored dates verbatim", () => {
    const v = getDefaultTaskValues(
      task(["2025-07-23 18:13:32", "2026-07-23 23:59:59"]),
      null,
    );
    expect(v.startDate).toBe("2025-07-23 18:13:32");
    expect(v.endDate).toBe("2026-07-23 23:59:59");
  });

  // Continuous tasks skip the date block; without a preset here, switching to
  // historical later would send "custom" with the untouched 6-month default.
  it("sets a preset on continuous tasks too", () => {
    const v = getDefaultTaskValues(task(["a", "b"], {}, "continuous"), null);
    expect(v.datePreset).toBeDefined();
  });

  // date_preset lives inside filters, but it is time-window metadata, not a
  // filter row — hydrating it as one renders a bogus "date_preset is one of
  // 12m" chip in the Filters panel.
  it("does not hydrate date_preset as a filter row", () => {
    const v = getDefaultTaskValues(
      task(["2025-08-21 14:33:22", "2026-08-22 00:00:00"], {
        date_preset: "12m",
      }),
      null,
    );
    expect(v.filters.map((f) => f.property)).not.toContain("date_preset");
    expect(v.filters).toHaveLength(0);
  });

  // Placeholder shape only — every caller resets with real data before render.
  // It still has to describe its own range, or it seeds a frozen window if a
  // caller ever loses its load guard.
  it("labels the no-data placeholder to match its six-month range", () => {
    const v = getDefaultTaskValues(null, null);
    expect(v.datePreset).toBe("6M");
    expect(inferPreset(v.startDate, v.endDate)).toBe("6M");
  });
});
