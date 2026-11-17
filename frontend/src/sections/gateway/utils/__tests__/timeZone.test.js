import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import {
  bucketTime,
  formatChartTime,
  localTimeAxis,
  localTimeTooltip,
  localZoneName,
  timeZoneParam,
  zoneNote,
} from "../timeZone";

// Logs said "Times are in America/Los_Angeles" while
// Analytics plotted UTC hours, so the same 8 requests at 23:39 PDT sat at
// 23:00 on Logs and near 06:30 on Analytics. One helper, one zone.
const INSTANT = Date.parse("2026-10-22T06:39:00Z"); // 23:39 PDT on Oct 21

describe("gateway chart time", () => {
  // A viewer in Los Angeles, on any machine: Node re-reads TZ when it changes.
  beforeAll(() => vi.stubEnv("TZ", "America/Los_Angeles"));
  afterAll(() => vi.unstubAllEnvs());

  it("names the viewer's zone", () => {
    expect(localZoneName()).toBe("America/Los_Angeles");
  });

  it("formats a fixed instant in the viewer's zone, not UTC", () => {
    expect(formatChartTime(INSTANT)).toBe("Oct 21, 23:39");
    expect(formatChartTime(INSTANT, { day: true })).toBe("Oct 21");
    expect(formatChartTime(null)).toBe("—");
  });

  it("builds a datetime axis that labels local time", () => {
    const axis = localTimeAxis({ color: "#666" });
    expect(axis.type).toBe("datetime");
    expect(axis.labels.datetimeUTC).toBe(false);
    expect(axis.labels.style.colors).toBe("#666");
  });

  it("gives the tooltip the same local time as the axis", () => {
    expect(localTimeTooltip().formatter(INSTANT)).toBe("Oct 21, 23:39");
    expect(localTimeTooltip({ day: true }).formatter(INSTANT)).toBe("Oct 21");
  });
});

// A day bucket cut at UTC midnight read as the day before
// for a viewer west of UTC. A series names the zone it cut its days in; a
// UTC day reads in UTC, the viewer's day in the viewer's zone.
describe("gateway day buckets", () => {
  beforeAll(() => vi.stubEnv("TZ", "America/Los_Angeles"));
  afterAll(() => vi.unstubAllEnvs());

  const UTC_DAY = Date.parse("2026-10-21T00:00:00Z");
  const LOCAL_DAY = Date.parse("2026-10-21T07:00:00Z"); // midnight in LA

  it("asks the API for the viewer's zone", () => {
    expect(timeZoneParam()).toEqual({ tz: "America/Los_Angeles" });
  });

  it("reads a series' days as UTC days when the API names no zone or UTC", () => {
    expect(bucketTime("day", { series: [] })).toEqual({ day: true, utc: true });
    expect(bucketTime("day", { time_zone: "UTC" })).toEqual({
      day: true,
      utc: true,
    });
    expect(bucketTime("day", { time_zone: "America/Los_Angeles" })).toEqual({
      day: true,
      utc: false,
    });
    // Minute and hour buckets read by instant, in the viewer's zone.
    expect(bucketTime("hour", {})).toEqual({ day: false, utc: false });
  });

  it("labels a UTC day bucket as its own date", () => {
    expect(formatChartTime(UTC_DAY, { day: true, utc: true })).toBe("Oct 21");
    expect(localTimeTooltip({ day: true, utc: true }).formatter(UTC_DAY)).toBe(
      "Oct 21",
    );
    expect(localTimeAxis({ utc: true }).labels.datetimeUTC).toBe(true);
  });

  it("labels the viewer's day bucket as its own date", () => {
    expect(formatChartTime(LOCAL_DAY, { day: true })).toBe("Oct 21");
    expect(localTimeAxis().labels.datetimeUTC).toBe(false);
  });

  it("says when daily bars are UTC days", () => {
    expect(zoneNote()).toBe("Times are in America/Los_Angeles.");
    expect(zoneNote({ utcDays: true })).toBe(
      "Times are in America/Los_Angeles. Daily bars are UTC days.",
    );
  });
});
