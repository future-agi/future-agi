import { describe, it, expect } from "vitest";
import { shouldMintExperimentVersion } from "./shouldMintExperimentVersion";

describe("shouldMintExperimentVersion", () => {
  it("mints only for dirty user evals on experiment source", () => {
    expect(
      shouldMintExperimentVersion({
        source: "experiment",
        isDirty: true,
        isSystemEval: false,
        templateType: "single",
      }),
    ).toBe(true);
  });

  it("does not mint for a clean version pick", () => {
    expect(
      shouldMintExperimentVersion({
        source: "experiment",
        isDirty: false,
        isSystemEval: false,
        templateType: "single",
      }),
    ).toBe(false);
  });

  it("does not mint for dataset / other hosts", () => {
    expect(
      shouldMintExperimentVersion({
        source: "dataset",
        isDirty: true,
        isSystemEval: false,
        templateType: "single",
      }),
    ).toBe(false);
  });

  it("does not mint for system or composite templates", () => {
    expect(
      shouldMintExperimentVersion({
        source: "experiment",
        isDirty: true,
        isSystemEval: true,
        templateType: "single",
      }),
    ).toBe(false);
    expect(
      shouldMintExperimentVersion({
        source: "experiment",
        isDirty: true,
        isSystemEval: false,
        templateType: "composite",
      }),
    ).toBe(false);
  });
});
