import { describe, expect, it } from "vitest";
import { isRawFormat, rawValueText } from "../rawValue";

describe("Raw JSON values (L7c g6)", () => {
  it("prints the stored value as JSON", () => {
    expect(rawValueText("Hi\nthere")).toBe('"Hi\\nthere"');
    expect(rawValueText({ customer_message: "Where is A-7781?" })).toBe(
      '{"customer_message":"Where is A-7781?"}',
    );
    expect(rawValueText([{ name: "lookup_order" }])).toBe(
      '[{"name":"lookup_order"}]',
    );
    expect(rawValueText(null)).toBe("null");
    expect(rawValueText(undefined)).toBe("");
  });
  it("reads the view option from the grid context", () => {
    expect(isRawFormat({ valueFormat: "raw" })).toBe(true);
    expect(isRawFormat({ valueFormat: "formatted" })).toBe(false);
    expect(isRawFormat(undefined)).toBe(false);
  });
});

describe("trial dots words (L7c g6 cell:trials)", () => {
  it("name the run's own try count", async () => {
    const { trialDotsTip } = await import("../rawValue");
    expect(trialDotsTip({ pass: 3, n: 4 }, "Correctness")).toBe(
      "3 of 4 trials passed on Correctness. A trial is one try of one task; this run tries each task 4 times.",
    );
    expect(trialDotsTip({ pass: 1, n: 2 }, "Tone")).toMatch(
      /each task 2 times\.$/,
    );
    expect(trialDotsTip({ pass: 1, n: 2 }, "Tone")).not.toMatch(/3 times/);
  });
});
