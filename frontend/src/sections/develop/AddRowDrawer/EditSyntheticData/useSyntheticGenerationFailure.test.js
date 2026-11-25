import { describe, expect, it } from "vitest";
import { getSyntheticGenerationFailureReason } from "./useSyntheticGenerationFailure";

// The data tab shows only the fixed message the backend recorded, and never
// renders anything that is not a string.
describe("getSyntheticGenerationFailureReason", () => {
  const withFailure = (failure) => ({
    dataset_config: { synthetic_generation_error: failure },
  });

  it("returns the recorded message", () => {
    expect(
      getSyntheticGenerationFailureReason(
        withFailure({
          code: "generation_failed",
          message: "Generation failed; try again or contact support",
        }),
      ),
    ).toBe("Generation failed; try again or contact support");
  });

  it.each([
    ["no result", undefined],
    ["no failure", { dataset_config: {} }],
    ["a bare string", withFailure("raw exception text")],
    ["a non-string message", withFailure({ message: { detail: "x" } })],
    ["an empty message", withFailure({ message: "" })],
  ])("returns nothing for %s", (_label, result) => {
    expect(getSyntheticGenerationFailureReason(result)).toBeUndefined();
  });
});
