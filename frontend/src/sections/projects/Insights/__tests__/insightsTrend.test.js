import { describe, expect, it } from "vitest";
import {
  CATEGORY_PALETTE,
  OTHER_COLOR,
  OTHER_ID,
  buildAreaSeries,
} from "../insightsDistribution";
import { nearestIndex, tickLabels, trendGeometry } from "../insightsTrend";

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

  it("marks a run that found its categories afresh", () => {
    const fresh = buildAreaSeries(
      [
        { ...runs[0], taxonomy_mode: "discover" },
        { ...runs[1], taxonomy_mode: "discover" },
      ],
      { order: ["a", "b"] },
    );
    expect(fresh.rows.map((r) => r.fresh)).toEqual([true, true]);
    expect(rows.map((r) => r.fresh)).toEqual([false, false]);
  });

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

describe("a rediscovered run", () => {
  // Two runs on the saved categories, then one that found its own.
  const series = [
    { id: "a", color: "#a" },
    { id: "b", color: "#b" },
    { id: "c", color: "#c" },
  ];
  const rows = [
    { time: 0, fresh: true, shares: { a: 0.5, b: 0.5 } },
    { time: 50, shares: { a: 0.75, b: 0.25 } },
    { time: 100, fresh: true, shares: { c: 1 } },
  ];
  const geometry = trendGeometry({
    rows,
    series,
    width: 240,
    height: 100,
    margin: { top: 0, right: 0, bottom: 0, left: 40 },
  });

  it("is never joined to the runs before it", () => {
    expect(geometry.xs).toEqual([40, 140, 240]);
    // The earlier categories end at the last run that had them...
    expect(geometry.bands[0].d).toBe("M40,50L140,25L140,100L40,100Z");
    expect(geometry.bands[1].d).toBe("M40,0L140,0L140,25L40,50Z");
    // ...and the run alone after the break is a column of its own, kept
    // inside the plot.
    expect(geometry.bands[2].d).toBe("M224,0L240,0L240,100L224,100Z");
  });

  it("marks the break between them", () => {
    expect(geometry.breaks).toEqual([{ index: 2, x: 182 }]);
  });

  it("keeps a column clear of a run close beside it", () => {
    const close = trendGeometry({
      rows: [rows[0], rows[1], { ...rows[2], time: 52 }],
      series,
      width: 240,
      height: 100,
      margin: { top: 0, right: 0, bottom: 0, left: 40 },
    });
    const [left] = close.bands[2].d.slice(1).split(",").map(Number);
    expect(left).toBeGreaterThanOrEqual(close.xs[1]);
    expect(close.breaks[0].x).toBeGreaterThanOrEqual(close.xs[1]);
  });
});

describe("nearestIndex and tickLabels", () => {
  it("picks the closest run", () => {
    expect(nearestIndex([40, 140, 240], 100)).toBe(1);
    expect(nearestIndex([40, 140, 240], 0)).toBe(0);
    expect(nearestIndex([], 10)).toBe(-1);
  });

  const extent = ({ index, anchor }, xs, texts) => {
    const w = texts[index].length * 6.5;
    const x = xs[index];
    return anchor === "start"
      ? [x, x + w]
      : anchor === "end"
        ? [x - w, x]
        : [x - w / 2, x + w / 2];
  };

  it("labels every run that fits, anchored inside the chart", () => {
    const xs = [40, 340, 640];
    const texts = ["Oct 20", "Oct 24", "Oct 28"];
    expect(tickLabels({ xs, texts, left: 0, right: 652 })).toEqual([
      { index: 0, anchor: "middle" },
      { index: 1, anchor: "middle" },
      { index: 2, anchor: "end" },
    ]);
  });

  it("never overlaps runs close in time, keeping the run on screen", () => {
    // 1:50 AM, then two runs ten minutes apart at the right edge.
    const xs = [40, 630, 640];
    const texts = ["1:50 AM", "6:09 AM", "6:19 AM"];
    const shown = tickLabels({ xs, texts, current: 1, left: 0, right: 652 });
    expect(shown.map((t) => t.index)).toEqual([0, 1]);
    const many = Array.from({ length: 30 }, (_, i) => 40 + i * 20);
    const labels = many.map((_, i) => `${i + 1}:00 AM`);
    const spread = tickLabels({
      xs: many,
      texts: labels,
      current: 12,
      left: 0,
      right: 652,
    });
    expect(spread.map((t) => t.index)).toContain(12);
    expect(spread.map((t) => t.index)).toContain(29);
    const boxes = spread
      .map((t) => extent(t, many, labels))
      .sort((a, b) => a[0] - b[0]);
    boxes.slice(1).forEach((box, i) => {
      expect(box[0]).toBeGreaterThan(boxes[i][1]);
    });
    boxes.forEach(([from, to]) => {
      expect(from).toBeGreaterThanOrEqual(0);
      expect(to).toBeLessThanOrEqual(652);
    });
  });

  it("doesn't repeat a day two runs share", () => {
    const shown = tickLabels({
      xs: [40, 340, 640],
      texts: ["Oct 20", "Oct 28", "Oct 28"],
      left: 0,
      right: 652,
    });
    expect(shown.map((t) => t.index)).toEqual([0, 2]);
  });
});
