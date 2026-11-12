import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";
import { reviewScoringLine } from "../reviewScoringLine";

const POLICY_WORDS = /count as 0|left out|excluded|Errored|Errors/;
const reviewStep = readFileSync(
  join(
    dirname(fileURLToPath(import.meta.url)),
    "..",
    "StepperComponentExperiment",
    "ReviewStep.jsx",
  ),
  "utf8",
);

afterEach(() => setLaunchOverrideForTest(null));

describe("review scoring line", () => {
  it("names only how repeated tries are reduced", () => {
    for (const errorPolicy of ["zero", "exclude", undefined]) {
      const line = reviewScoringLine({
        errorPolicy,
        trialReducer: "median",
        maxTries: 3,
      });
      expect(line).toBe("Each row scored by Median of its 3 tries");
      expect(line).not.toMatch(POLICY_WORDS);
    }
  });

  it("has nothing to say when every run plays one try", () => {
    expect(reviewScoringLine({ trialReducer: "mean", maxTries: 1 })).toBeNull();
    expect(reviewScoringLine({})).toBeNull();
  });

  it("is the only scoring text a run-settings-only claim can show", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      madeReal: ["exp.new.runSettings"],
    });
    expect(isSurfaceShown("exp.new.runSettings")).toBe(true);
    expect(isSurfaceShown("exp.new.scope")).toBe(false);
    // Review renders the policy summary only behind the scope gate, and its
    // run-settings scoring line comes from reviewScoringLine alone.
    expect(reviewStep).not.toMatch(/scoringSummary/);
    expect(reviewStep).toMatch(
      /\{scopeShown && <ScopePolicySummary values=\{values\} \/>\}/,
    );
    expect(reviewStep).toMatch(
      /\{runSettingsShown && scoringLine \? \(\s*<Line>\s*<Typography\s+typography="s2"\s+data-testid="review-scoring-summary"\s*>\s*\{scoringLine\}/,
    );
    expect(reviewStep).toMatch(
      /const scoringLine = reviewScoringLine\(\{\s*trialReducer: values\.trialReducer,\s*maxTries: Math\.max\(1, \.\.\.triesEach\.map\(Number\)\),\s*\}\);/,
    );
  });
});
