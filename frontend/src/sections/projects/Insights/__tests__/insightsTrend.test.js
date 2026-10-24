import { describe, expect, it } from "vitest";
import {
  CATEGORY_PALETTE,
  OTHER_COLOR,
  OTHER_ID,
  buildAreaSeries,
} from "../insightsDistribution";
import { nearestIndex, tickIndexes, trendGeometry } from "../insightsTrend";

const run = (id, createdAt, distribution) => ({
  id,
  status: "completed",
  created_at: createdAt,
  finished_at: createdAt,
  distribution,
});

const runs = [
  run("r2", "2026-10-15T09:00:00Z", [
    { category_id: "a", name: "Alpha", count: 6 },
    { category_id: "b", name: "Beta", count: 2 },
    { category_id: "o", name: "Other", count: 2, status: "unmatched" },
  ]),
  run("r1", "2026-10-14T09:00:00Z", [
    { category_id: "a", name: "Alpha", count: 3 },
    { category_id: "b", name: "Beta (old name)", count: 1 },
  ]),
  { id: "r3", status: "failed", created_at: "2026-10-16T09:00:00Z" },
];

describe("buildAreaSeries", () => {
  const { series, rows } = buildAreaSeries(runs, { order: ["a", "b"] });

  it("orders completed runs oldest first with shares that sum to 1", () => {
    expect(rows.map((r) => r.runId)).toEqual(["r1", "r2"]);
    expect(rows[0].shares).toEqual({ a: 0.75, b: 0.25 });
    expect(rows[1].shares).toEqual({ a: 0.6, b: 0.2, [OTHER_ID]: 0.2 });
    expect(rows[1].counts).toEqual({ a: 6, b: 2, [OTHER_ID]: 2 });
  });

  it("keeps each category's slot colour and puts a grey Other last", () => {
    expect(series).toEqual([
      { id: "a", name: "Alpha", color: CATEGORY_PALETTE.light[0] },
      { id: "b", name: "Beta", color: CATEGORY_PALETTE.light[1] },
      { id: OTHER_ID, name: "Other", color: OTHER_COLOR.light, muted: true },
    ]);
  });

  it("folds categories past the palette into Other", () => {
    const many = Array.from({ length: 10 }, (_, i) => ({
      category_id: `c${i}`,
      name: `C${i}`,
      count: 1,
    }));
    const order = many.map((d) => d.category_id);
    const result = buildAreaSeries([run("x", "2026-10-15T00:00:00Z", many)], {
      order,
    });
    expect(result.series).toHaveLength(9);
    expect(result.rows[0].counts[OTHER_ID]).toBe(2);
  });
});

describe("trendGeometry", () => {
  const { rows, series } = buildAreaSeries(runs, { order: ["a", "b"] });
  const geometry = trendGeometry({
    rows,
    series,
    width: 440,
    height: 128,
    margin: { top: 8, right: 0, bottom: 20, left: 40 },
  });

  it("spans the plot area by run time", () => {
    expect(geometry.inner).toEqual({
      left: 40,
      right: 440,
      top: 8,
      bottom: 108,
    });
    expect(geometry.xs).toEqual([40, 440]);
    expect(geometry.yOf(0)).toBe(108);
    expect(geometry.yOf(1)).toBe(8);
  });

  it("stacks one closed band per series from the bottom up", () => {
    expect(geometry.bands.map((b) => b.id)).toEqual(["a", "b", OTHER_ID]);
    // Alpha: 75% then 60% of the height, from the baseline.
    expect(geometry.bands[0].d).toBe("M40,33L440,48L440,108L40,108Z");
    // Other only exists in the second run: zero-height at the first.
    expect(geometry.bands[2].d).toBe("M40,8L440,8L440,28L40,8Z");
  });

  it("spreads runs evenly when their times are equal", () => {
    const same = trendGeometry({
      rows: [
        { time: 5, shares: {} },
        { time: 5, shares: {} },
        { time: 5, shares: {} },
      ],
      series: [],
      width: 240,
      height: 100,
      margin: { top: 0, right: 0, bottom: 0, left: 40 },
    });
    expect(same.xs).toEqual([40, 140, 240]);
  });
});

describe("nearestIndex and tickIndexes", () => {
  it("picks the closest run", () => {
    expect(nearestIndex([40, 140, 240], 100)).toBe(1);
    expect(nearestIndex([40, 140, 240], 0)).toBe(0);
    expect(nearestIndex([], 10)).toBe(-1);
  });

  it("labels every run when few, else an even spread with both ends", () => {
    expect(tickIndexes(3)).toEqual([0, 1, 2]);
    expect(tickIndexes(20, 5)).toEqual([0, 5, 10, 14, 19]);
  });
});
