import { describe, it, expect } from "vitest";
import { getYAxisRangeWarning } from "../widgetUtils";

const series = (values) => [
  { name: "s1", data: values.map((y, i) => ({ x: i, y })) },
];

describe("getYAxisRangeWarning", () => {
  it("returns null when no min/max is configured", () => {
    expect(getYAxisRangeWarning(series([2, 7]), {})).toBeNull();
    expect(
      getYAxisRangeWarning(series([2, 7]), { min: "", max: "" }),
    ).toBeNull();
  });

  it("warns when every data point falls below the configured min", () => {
    const msg = getYAxisRangeWarning(series([2, 7]), { min: "34", max: "545" });
    expect(msg).toBe(
      "Data is outside your configured Y-axis range (34–545). Adjust bounds to see your data.",
    );
  });

  it("warns when every data point falls above the configured max", () => {
    const msg = getYAxisRangeWarning(series([900]), { min: "34", max: "545" });
    expect(msg).toBe(
      "Data is outside your configured Y-axis range (34–545). Adjust bounds to see your data.",
    );
  });

  it("returns null when at least one data point is within bounds", () => {
    expect(
      getYAxisRangeWarning(series([2, 400]), { min: "34", max: "545" }),
    ).toBeNull();
  });

  it("returns null when there are no numeric data points", () => {
    expect(
      getYAxisRangeWarning(series([null, null]), { min: "34", max: "545" }),
    ).toBeNull();
  });

  it("supports a min-only or max-only bound", () => {
    expect(getYAxisRangeWarning(series([2, 7]), { min: "34" })).toBe(
      "Data is outside your configured Y-axis minimum (34). Adjust bounds to see your data.",
    );
    expect(getYAxisRangeWarning(series([900]), { max: "545" })).toBe(
      "Data is outside your configured Y-axis maximum (545). Adjust bounds to see your data.",
    );
  });
});
