import { describe, expect, it } from "vitest";

import { REVIEW_COPY } from "../designReview.constants";
import { CONSOLE_COPY } from "../../../buildEnvironment/build.constants";

describe("design review copy", () => {
  it.each([
    [3, 3, "3 scenarios are written after you approve, one per use case."],
    [
      3,
      1,
      "3 scenarios are written after you approve, all for this one use case.",
    ],
    [
      5,
      2,
      "5 scenarios are written after you approve, spread across these use cases.",
    ],
  ])("words %i scenarios over %i use cases", (scenarios, useCases, text) => {
    expect(REVIEW_COPY.writtenAfterApproval(scenarios, useCases)).toBe(text);
  });

  it("says what happened to a removed item in a sentence", () => {
    expect(REVIEW_COPY.removedFromPlan(REVIEW_COPY.theUseCase)).toBe(
      "The use case was removed from the plan.",
    );
  });

  it("does not invite typing while the console waits on its questions", () => {
    expect(CONSOLE_COPY.empty).not.toMatch(/typing/);
  });
});
