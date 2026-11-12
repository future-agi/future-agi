import { describe, expect, it } from "vitest";
import { jsonChipSummary } from "../jsonChips";

describe("jsonChipSummary (IT-R1 G5 P5)", () => {
  it("leaves conversations to the turn-chip summary", () => {
    expect(
      jsonChipSummary({
        turns: [{ role: "customer", content: "hi" }],
        resolution: "Fail",
        resolution_reason: "no callback",
      }),
    ).toBeNull();
    expect(
      jsonChipSummary({ messages: [{ role: "user", content: "x" }] }),
    ).toBeNull();
  });

  it("still summarises other nested objects", () => {
    expect(
      jsonChipSummary({ order: { id: 1 }, customer: { id: 2 } }).text,
    ).toBe("{ 1 order, 1 customer }");
  });
});
