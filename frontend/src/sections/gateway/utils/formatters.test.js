import { describe, expect, it } from "vitest";
import { formatCost, formatCostTick, isUnpriced } from "./formatters";

describe("isUnpriced", () => {
  it.each([null, undefined, "", 0, "0", "0.000000"])(
    "marks an explicitly tagged empty or zero cost (%s) as unpriced",
    (cost) => {
      expect(isUnpriced({ cost, metadata: { cost_source: "unpriced" } })).toBe(
        true,
      );
    },
  );

  it.each(["0.002000", "-0.5", "not-a-number", Infinity, NaN])(
    "keeps a tagged nonzero or invalid cost (%s) on the normal display path",
    (cost) => {
      expect(isUnpriced({ cost, metadata: { cost_source: "unpriced" } })).toBe(
        false,
      );
    },
  );

  it.each([
    null,
    {},
    { cost_source: "org_custom" },
    { cost_source: "litellm" },
  ])(
    "does not infer an unpriced request from zero cost alone (%j)",
    (metadata) => {
      expect(isUnpriced({ cost: "0", metadata })).toBe(false);
    },
  );

  it("handles a missing log", () => {
    expect(isUnpriced(null)).toBe(false);
    expect(isUnpriced(undefined)).toBe(false);
  });
});

describe("formatCost", () => {
  it("distinguishes unknown spend from an explicitly reported zero", () => {
    expect(formatCost(null)).toBe("—");
    expect(formatCost(undefined)).toBe("—");
    expect(formatCost(NaN)).toBe("—");
    expect(formatCost(0)).toBe("$0.00");
    expect(formatCost(1.25)).toBe("$1.25");
    expect(formatCostTick(null)).toBe("—");
  });
});
