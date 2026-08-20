import { describe, it, expect } from "vitest";
import { buildConnectors, getCenterValue } from "../widgetPieUtils";

const group = (aggregation, values) => ({
  aggregation,
  slices: values.map((value, i) => ({ name: `s${i}`, value })),
});

describe("getCenterValue", () => {
  it("shows a single slice's own value whatever the aggregation", () => {
    // One slice means no summing happens, so the number is exact.
    expect(getCenterValue(group("avg", [74.95]))).toBe(74.95);
    expect(getCenterValue(group("max", [220]))).toBe(220);
    expect(getCenterValue(group("median", [12]))).toBe(12);
  });

  it("totals the slices when the aggregation is additive", () => {
    expect(getCenterValue(group("sum", [10, 20, 30]))).toBe(60);
    expect(getCenterValue(group("count", [1, 2]))).toBe(3);
  });

  it("shows nothing when adding several slices would invent a quantity", () => {
    // The sum of three per-project averages is not an average of anything.
    expect(getCenterValue(group("avg", [10, 20, 30]))).toBeNull();
    expect(getCenterValue(group("max", [10, 20]))).toBeNull();
    expect(getCenterValue(group("p95", [10, 20]))).toBeNull();
  });

  it("shows nothing when there are no slices", () => {
    expect(getCenterValue(group("sum", []))).toBeNull();
  });
});

// Review comment 6 on PR #2074: callouts used to prefix each slice with a
// letter counted within its own donut, while the editor's summary strip
// letters a metric. Two unrelated meanings of "A" on the same screen, and the
// callout letter cross-referenced nothing — the name is already beside it.
describe("buildConnectors labels", () => {
  const geometry = { cx: 200, cy: 200, radius: 80, width: 600, height: 400 };

  it("labels a callout with the slice name alone", () => {
    const items = buildConnectors({
      geometry,
      slices: [
        { name: "alpha", value: 60 },
        { name: "beta", value: 40 },
      ],
      formatSlice: (v) => String(v),
    });
    expect(items).toHaveLength(2);
    expect(items.map((c) => c.line1)).toEqual(["alpha", "beta"]);
    items.forEach((c) => expect(c.line1).not.toMatch(/^[A-Z]\.\s/));
  });

  it("keeps the value on its own line", () => {
    const [first] = buildConnectors({
      geometry,
      slices: [
        { name: "alpha", value: 60 },
        { name: "beta", value: 40 },
      ],
      formatSlice: (v) => `${v} tok`,
    });
    expect(first.line2).toBe("60 tok");
  });
});
