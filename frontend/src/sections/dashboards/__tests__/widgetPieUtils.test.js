import { describe, it, expect } from "vitest";
import { getCenterValue } from "../widgetPieUtils";

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
