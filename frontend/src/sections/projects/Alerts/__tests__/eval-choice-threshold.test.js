import { describe, it, expect } from "vitest";
import { evalUsesChoiceThreshold } from "../common";

describe("evalUsesChoiceThreshold", () => {
  it("is true for a Pass/Fail eval", () => {
    expect(
      evalUsesChoiceThreshold({
        output_type: "Pass/Fail",
        choices: ["Passed", "Failed"],
      }),
    ).toBe(true);
  });

  it("is true for a choices eval", () => {
    expect(
      evalUsesChoiceThreshold({
        output_type: "choices",
        choices: ["never", "always"],
      }),
    ).toBe(true);
  });

  it("is false for a scoring eval that has labels", () => {
    expect(
      evalUsesChoiceThreshold({
        output_type: "score",
        choices: ["Complete", "Partial", "Incomplete"],
      }),
    ).toBe(false);
  });

  it("is false for a scoring eval with no labels", () => {
    expect(
      evalUsesChoiceThreshold({ output_type: "score", choices: null }),
    ).toBe(false);
  });

  it("is false for an unknown or missing output type", () => {
    expect(evalUsesChoiceThreshold({ choices: ["a"] })).toBe(false);
    expect(evalUsesChoiceThreshold(undefined)).toBe(false);
  });
});
