import { describe, expect, it } from "vitest";
import { experimentDescription } from "../experimentDescription";

describe("experiment description under the title (L7c g6)", () => {
  it("prefers the record, falls back to a run's copy, else empty", () => {
    expect(experimentDescription({ description: " Same V4 prompt " })).toBe(
      "Same V4 prompt",
    );
    expect(
      experimentDescription({
        runs: [{}, { experiment_description: "From the run" }],
      }),
    ).toBe("From the run");
    expect(experimentDescription({ runs: [] })).toBe("");
    expect(experimentDescription(null)).toBe("");
  });
});
