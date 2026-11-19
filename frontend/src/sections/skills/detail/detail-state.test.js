import { describe, expect, it } from "vitest";
import { resolveViewedVersion, formatFetchedAt } from "./detail-state";
import { buildUsageChart } from "./usageChart";

const skill = {
  latestVersion: 12,
  labels: [{ name: "production", version: 3 }],
};

describe("published version links", () => {
  it("defaults the file viewer to the latest published version", () => {
    expect(resolveViewedVersion(skill, null)).toEqual({
      value: 12,
      number: 12,
    });
  });
  it("resolves a label and the virtual latest label without changing the selector", () => {
    expect(resolveViewedVersion(skill, "label:production")).toMatchObject({
      value: "label:production",
      number: 3,
    });
    expect(resolveViewedVersion(skill, "label:latest")).toMatchObject({
      number: 12,
    });
  });
  it("does not silently fall back when a label is missing", () => {
    expect(resolveViewedVersion(skill, "label:staging")).toMatchObject({
      number: null,
      error: expect.stringContaining("no staging label"),
    });
  });
  it.each(["draft", "-1", "0", "1.5", "1e2", "02", "9007199254740993"])(
    "rejects invalid version %s",
    (ref) => {
      expect(resolveViewedVersion(skill, ref)).toMatchObject({
        value: "",
        number: null,
        error: expect.any(String),
      });
    },
  );
  it("preserves a pinned version instead of replacing it with latest", () => {
    expect(resolveViewedVersion(skill, "2")).toEqual({ value: 2, number: 2 });
  });
  it("handles an unpublished skill and a never-fetched skill", () => {
    expect(
      resolveViewedVersion({ ...skill, latestVersion: null }, null).number,
    ).toBeNull();
    expect(formatFetchedAt(null)).toBe("Never");
  });
});

describe("daily version chart", () => {
  const now = new Date("2026-11-06T03:00:00Z");
  it("fills missing UTC dates with zero and sums repeated version/day rows", () => {
    const chart = buildUsageChart(
      [
        { day: "2026-11-05", version: 2, count: 3 },
        { day: "2026-11-05", version: 2, count: 4 },
        { day: "2026-11-06", version: 1, count: 2 },
      ],
      2,
      7,
      now,
    );
    expect(chart.dates).toEqual([
      "2026-10-31",
      "2026-11-01",
      "2026-11-02",
      "2026-11-03",
      "2026-11-04",
      "2026-11-05",
      "2026-11-06",
    ]);
    expect(chart.series.find((item) => item.name === "v2").data).toEqual([
      0, 0, 0, 0, 0, 7, 0,
    ]);
    expect(chart.series.find((item) => item.name === "v1").data.at(-1)).toBe(2);
  });
  it("hides unfetched versions without repainting the remaining series", () => {
    const rows = [{ day: "2026-11-06", version: 3, count: 2 }];
    const week = buildUsageChart(rows, 4, 7, now);
    const month = buildUsageChart(
      [...rows, { day: "2026-10-20", version: 2, count: 1 }],
      4,
      30,
      now,
    );
    expect(week.series.map((item) => item.name)).toEqual(["v3"]);
    expect(week.series[0].colorIndex).toBe(
      month.series.find((item) => item.name === "v3").colorIndex,
    );
  });
  it("groups old versions explicitly within the eight-color ceiling", () => {
    const chart = buildUsageChart(
      Array.from({ length: 200 }, (_, index) => ({
        day: "2026-11-06",
        version: index + 1,
        count: 1,
      })),
      200,
      90,
      now,
    );
    expect(chart.series).toHaveLength(8);
    expect(chart.series.at(-1)).toMatchObject({
      name: "Earlier versions (v1–v193)",
      colorIndex: 7,
    });
    expect(chart.series.at(-1).data.at(-1)).toBe(193);
    expect(chart.grouped).toBe(true);
  });
  it("does not show grouping or empty series without fetches", () => {
    expect(buildUsageChart([], 20, 7, now)).toMatchObject({
      series: [],
      grouped: false,
    });
    expect(buildUsageChart([], 0, 7, now)).toMatchObject({
      series: [],
      grouped: false,
    });
  });
});
