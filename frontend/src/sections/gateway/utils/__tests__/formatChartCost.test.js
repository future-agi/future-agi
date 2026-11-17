import { describe, expect, it } from "vitest";

import { EMPTY, formatChartCost, formatCostCell } from "../formatters";

// One format for every cost on a chart (Analytics > Cost: the value axis,
// the bars' and slices' values, the donut's total, the tooltips): the grids'
// grouped thousands and exact digits. Where a narrow chart's axis has no room
// (compact), $1,000 and up read short; the exact amount stays on the bar.
describe("formatChartCost", () => {
  it.each([
    [1000, "$1,000.00"],
    [25000, "$25,000.00"],
    ["1234567.891", "$1,234,567.891"],
    [0.3, "$0.30"],
    [0, "$0.00"],
    [0.000123456789, "$0.000123456789"],
    ["7307.07156", "$7,307.07156"],
    [-1500, "-$1,500.00"],
  ])("reads %s as the grids do", (value, expected) => {
    expect(formatChartCost(value)).toBe(expected);
    expect(formatChartCost(value)).toBe(formatCostCell(value));
  });

  it.each([
    [25000, "$25K"],
    [12500, "$12.5K"],
    [1000, "$1K"],
    [2500000, "$2.5M"],
    [-1500, "-$1.5K"],
  ])("shortens %s on a compact axis", (value, expected) => {
    expect(formatChartCost(value, { compact: true })).toBe(expected);
  });

  it("keeps an amount under $1,000 whole on a compact axis", () => {
    expect(formatChartCost(999.5, { compact: true })).toBe("$999.50");
    expect(formatChartCost(0.0004, { compact: true })).toBe("$0.0004");
  });

  it("reads a missing amount as the one empty mark", () => {
    for (const value of [null, undefined, "", "not money"]) {
      expect(formatChartCost(value)).toBe(EMPTY);
      expect(formatChartCost(value, { compact: true })).toBe(EMPTY);
    }
  });
});
