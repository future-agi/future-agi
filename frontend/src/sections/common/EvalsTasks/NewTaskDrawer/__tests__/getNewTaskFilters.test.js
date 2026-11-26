import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { getNewTaskFilters } from "../validation";

const NOW = new Date("2026-08-21T06:00:00Z");
const historical = (extra) => ({ runType: "historical", ...extra });

describe("getNewTaskFilters time window", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it("sends the wire token alongside a re-anchored range", () => {
    const { filters } = getNewTaskFilters(
      historical({
        datePreset: "12M",
        startDate: "2025-07-23 18:13:32",
        endDate: "2026-07-23 23:59:59",
      }),
      "proj",
    );
    expect(filters.date_preset).toBe("12m");
    expect(new Date(filters.date_range[1]).getTime()).toBeGreaterThan(
      NOW.getTime(),
    );
  });

  // The case inference could never solve: the stored dates look Custom.
  it("re-anchors Today from the stored key", () => {
    const { filters } = getNewTaskFilters(
      historical({
        datePreset: "Today",
        startDate: "2026-08-18 00:00:00",
        endDate: "2026-08-19 00:00:00",
      }),
      "proj",
    );
    expect(filters.date_preset).toBe("today");
    expect(new Date(filters.date_range[1]).getTime()).toBeGreaterThan(
      NOW.getTime(),
    );
  });

  it("sends a Custom window verbatim", () => {
    const { filters } = getNewTaskFilters(
      historical({
        datePreset: "Custom",
        startDate: "2026-06-01 00:00:00",
        endDate: "2026-07-01 00:00:00",
      }),
      "proj",
    );
    expect(filters.date_preset).toBe("custom");
    expect(filters.date_range).toEqual([
      new Date("2026-06-01 00:00:00").toISOString(),
      new Date("2026-07-01 00:00:00").toISOString(),
    ]);
  });

  it("treats a missing preset as Custom", () => {
    const { filters } = getNewTaskFilters(
      historical({
        startDate: "2026-06-01 00:00:00",
        endDate: "2026-07-01 00:00:00",
      }),
      "proj",
    );
    expect(filters.date_preset).toBe("custom");
  });

  it("omits both keys for continuous tasks and when ignoreDate is set", () => {
    expect(
      getNewTaskFilters({ runType: "continuous", datePreset: "12M" }, "proj")
        .filters.date_preset,
    ).toBeUndefined();
    expect(
      getNewTaskFilters(
        historical({
          datePreset: "12M",
          startDate: "2026-06-01 00:00:00",
          endDate: "2026-07-01 00:00:00",
        }),
        "proj",
        true,
      ).filters.date_preset,
    ).toBeUndefined();
  });
});
