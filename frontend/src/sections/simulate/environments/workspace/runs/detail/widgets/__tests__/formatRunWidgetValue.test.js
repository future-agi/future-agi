import { describe, it, expect } from "vitest";
import { formatRunWidgetValue } from "../formatRunWidgetValue";

describe("formatRunWidgetValue", () => {
  it.each([
    [850, "850 ms"],
    [22000, "22s"],
    [56700, "56.7s"],
    [68004.5, "1m 8s"],
    [140000, "2m 20s"],
    [3725000, "1h 2m"],
  ])("reads %s ms as %s", (value, text) => {
    expect(formatRunWidgetValue(value, {}, "ms")).toBe(text);
  });

  it("never reads a duration in thousands of milliseconds", () => {
    expect(formatRunWidgetValue(140000, {}, "ms")).not.toMatch(/K/);
  });

  it("shows a dash for a missing value", () => {
    expect(formatRunWidgetValue(null, {}, "ms")).toBe("-");
  });

  it("keeps the axis unit a person picked", () => {
    expect(
      formatRunWidgetValue(1500, { unit: "#", prefixSuffix: "prefix" }, "ms"),
    ).toBe("#1.50K");
  });

  it("leaves other units to the dashboards formatter", () => {
    expect(formatRunWidgetValue(1200, {}, "tokens")).toBe("1.20K tokens");
  });
});
