import { describe, expect, it } from "vitest";
import { shouldPollEvalOnboardingReviewRun } from "./evalUsageOnboarding";

describe("shouldPollEvalOnboardingReviewRun", () => {
  it("polls only while an onboarding review run has not opened", () => {
    expect(
      shouldPollEvalOnboardingReviewRun({
        isOnboarding: true,
        runId: "run-1",
        step: "review",
      }),
    ).toBe(true);

    expect(
      shouldPollEvalOnboardingReviewRun({
        autoOpenedRunId: "run-1",
        isOnboarding: true,
        runId: "run-1",
        step: "review",
      }),
    ).toBe(false);
  });

  it("does not poll outside review onboarding", () => {
    expect(
      shouldPollEvalOnboardingReviewRun({
        isOnboarding: false,
        runId: "run-1",
        step: "review",
      }),
    ).toBe(false);
    expect(
      shouldPollEvalOnboardingReviewRun({
        isOnboarding: true,
        runId: "run-1",
        step: "run",
      }),
    ).toBe(false);
    expect(
      shouldPollEvalOnboardingReviewRun({
        isOnboarding: true,
        step: "review",
      }),
    ).toBe(false);
  });
});
