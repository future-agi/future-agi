import { describe, it, expect } from "vitest";
import { isUnpriced, formatCost, UNPRICED_TOOLTIP } from "../formatters";

describe("isUnpriced helper", () => {
  it("returns true when cost_source is 'unpriced' and cost is 0 or empty", () => {
    expect(isUnpriced({ metadata: { cost_source: "unpriced" }, cost: "0" })).toBe(true);
    expect(isUnpriced({ metadata: { cost_source: "unpriced" }, cost: 0 })).toBe(true);
    expect(isUnpriced({ metadata: { cost_source: "unpriced" }, cost: null })).toBe(true);
  });

  it("returns false when cost_source is 'unpriced' but cost is positive", () => {
    expect(isUnpriced({ metadata: { cost_source: "unpriced" }, cost: "0.005" })).toBe(false);
    expect(isUnpriced({ metadata: { cost_source: "unpriced" }, cost: 1.25 })).toBe(false);
  });

  it("returns false when cost_source is not 'unpriced' (e.g. org_custom or undefined)", () => {
    expect(isUnpriced({ metadata: { cost_source: "org_custom" }, cost: "0" })).toBe(false);
    expect(isUnpriced({ metadata: {}, cost: "0" })).toBe(false);
    expect(isUnpriced({ cost: "0" })).toBe(false);
    expect(isUnpriced(null)).toBe(false);
  });

  it("exports correct explanatory tooltip text", () => {
    expect(UNPRICED_TOOLTIP).toContain("No price is set for this model");
  });
});
