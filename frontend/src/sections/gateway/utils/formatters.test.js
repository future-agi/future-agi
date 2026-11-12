import { describe, expect, it } from "vitest";
import { formatCost, formatCostTick } from "./formatters";

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
