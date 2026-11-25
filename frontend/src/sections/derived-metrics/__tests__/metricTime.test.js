import { describe, expect, it } from "vitest";
import {
  formatLocalMetricWindow,
  formatMetricTime,
  formatMetricTimeRange,
  formatMetricWindow,
  firstValuesInstant,
  forwardValuesStart,
  coveragePending,
  coverageWindow,
  formatRelativeMetricTime,
  localTime,
  metricBucketWindow,
  metricWindow,
} from "../metricTime";

const now = "2026-11-08T12:00:00.000Z";

describe("metric time and closed windows", () => {
  it.each([null, undefined, "", "invalid-date", false, NaN, Infinity])(
    "does not invent a timestamp for %s",
    (value) => {
      expect(formatMetricTime(value)).toBe("Unavailable");
      expect(formatRelativeMetricTime(value, { now })).toBe("Unavailable");
      expect(localTime(value)).toBe("");
    },
  );

  it("preserves the real epoch zero instant", () => {
    expect(formatMetricTime(0)).toBe("01 Jan 1970, 00:00:00 UTC");
  });

  it("labels absolute instants UTC and local windows as local", () => {
    expect(formatMetricTime("2026-11-08T17:30:00+05:30")).toBe(
      "08 Nov 2026, 12:00:00 UTC",
    );
    const range = metricWindow(7, now);
    expect(formatMetricWindow(range)).toBe(
      "01 Nov 2026, 12:00:00 UTC to 08 Nov 2026, 12:00:00 UTC",
    );
    expect(formatLocalMetricWindow(range)).toContain("(local time)");
    expect(formatMetricWindow(null)).toBe("Unavailable to Unavailable");
    expect(formatLocalMetricWindow(null)).toBe(
      "Unavailable to Unavailable (local time)",
    );
  });

  it("rounds both ends down to 15-minute UTC buckets, like the server", () => {
    const selection = {
      window_start: "2026-11-02T14:09:54.321Z",
      window_end: "2026-11-09T14:15:00.000Z",
    };
    expect(metricBucketWindow(selection)).toEqual({
      window_start: "2026-11-02T14:00:00.000Z",
      window_end: "2026-11-09T14:15:00.000Z",
    });
    expect(metricBucketWindow({ window_start: "invalid-date" })).toEqual({
      window_start: null,
      window_end: null,
    });
    expect(metricBucketWindow(null)).toEqual({
      window_start: null,
      window_end: null,
    });
  });

  it("creates a fixed elapsed UTC range across the daylight-saving boundary", () => {
    expect(metricWindow(7, "2026-11-02T12:00:00Z")).toEqual({
      window_start: "2026-10-26T12:00:00.000Z",
      window_end: "2026-11-02T12:00:00.000Z",
    });
  });

  it.each([0, -1, 91, 1.5, null, "7"])(
    "rejects invalid day count %s",
    (days) => {
      expect(() => metricWindow(days, now)).toThrow("between 1 and 90 days");
    },
  );

  it("rejects a missing reference instant instead of using epoch zero", () => {
    expect(() => metricWindow(7, null)).toThrow();
    expect(formatRelativeMetricTime(now, { now: null })).toBe("Unavailable");
  });

  it.each([
    [0, "now"],
    [-59, "59 seconds ago"],
    [-60, "1 minute ago"],
    [-3599, "59 minutes ago"],
    [-3600, "1 hour ago"],
    [-86399, "23 hours ago"],
    [-86400, "yesterday"],
    [60, "in 1 minute"],
    [86400, "tomorrow"],
  ])("handles the relative boundary %s seconds", (seconds, expected) => {
    const value = new Date(new Date(now).getTime() + seconds * 1000);
    expect(formatRelativeMetricTime(value, { now, locale: "en-US" })).toBe(
      expected,
    );
  });
});

describe("time ranges in a UTC column", () => {
  it("shows the date once when both ends fall on the same UTC day", () => {
    expect(
      formatMetricTimeRange("2026-11-11T09:49:47Z", "2026-11-11T17:49:47Z"),
    ).toBe("11 Nov 2026, 09:49:47 – 17:49:47");
    expect(
      formatMetricTimeRange("2026-11-10T23:30:00Z", "2026-11-11T00:10:00Z"),
    ).toBe("10 Nov 2026, 23:30:00 – 11 Nov 2026, 00:10:00");
    expect(formatMetricTimeRange(null, "2026-11-11T00:10:00Z")).toBe(
      "Unavailable – 11 Nov 2026, 00:10:00",
    );
  });
});

describe("forward-only metric windows", () => {
  const definition = {
    history_mode: "forward",
    coverage_start: "2026-11-08T10:07:30.000Z",
  };

  it("starts at the first complete 15-minute bucket, not the partial one", () => {
    expect(firstValuesInstant(definition.coverage_start).toISOString()).toBe(
      "2026-11-08T10:15:00.000Z",
    );
    expect(forwardValuesStart(definition).toISOString()).toBe(
      "2026-11-08T10:15:00.000Z",
    );
    const window = {
      window_start: "2026-11-01T10:00:00.000Z",
      window_end: "2026-11-08T10:22:00.000Z",
    };
    expect(coverageWindow(window, definition)).toEqual({
      ...window,
      window_start: "2026-11-08T10:15:00.000Z",
    });
  });

  it("does not read within the coverage bucket or before the first full bucket ends", () => {
    const window = {
      window_start: "2026-11-01T10:00:00.000Z",
      window_end: "2026-11-08T10:14:59.000Z",
    };
    expect(coveragePending(window, definition)).toBe(true);
    expect(
      coveragePending(
        { ...window, window_end: "2026-11-08T10:15:00.000Z" },
        definition,
      ),
    ).toBe(true);
    expect(
      coveragePending(
        { ...window, window_end: "2026-11-08T10:30:00.000Z" },
        definition,
      ),
    ).toBe(false);
  });

  it("keeps an activation after mount pending without inventing an empty window", () => {
    const mounted = {
      window_start: "2026-11-01T10:00:00.000Z",
      window_end: "2026-11-08T10:03:00.000Z",
    };
    const activated = {
      history_mode: "forward",
      coverage_start: "2026-11-08T10:03:05.000Z",
    };
    expect(coveragePending(mounted, activated)).toBe(true);
    // The mount-time end precedes coverage_start, so keep the user's closed
    // window as-is; the page skips the read until its preset can re-anchor.
    expect(coverageWindow(mounted, activated)).toBe(mounted);
  });
});
