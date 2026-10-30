import { describe, expect, it } from "vitest";
import {
  ERROR_ANSWER,
  SKIPPED_ANSWER,
  answerText,
  isFailure,
  quoted,
  summarizeCounts,
} from "../instantEvalAnswers";

const ITEM = {
  configId: "c1",
  question: "Did the customer get what they came for?",
  answerType: "yes_no",
  labels: ["Yes", "No"],
};

describe("answer words", () => {
  it("names failures and skips in plain words", () => {
    expect(answerText(ERROR_ANSWER)).toBe("Failed");
    expect(answerText(SKIPPED_ANSWER)).toBe("Skipped");
    expect(answerText("Yes")).toBe("Yes");
    expect(isFailure(ERROR_ANSWER)).toBe(true);
    expect(isFailure("No")).toBe(false);
    expect(quoted("No")).toBe("“No”");
  });
});

describe("summarizeCounts", () => {
  it("reads a past run's split from its counts, failures apart", () => {
    const summary = summarizeCounts(
      { No: 2, Yes: 6, __error__: 1, Maybe: 1, __skipped__: 0 },
      ITEM,
    );
    expect(summary.total).toBe(10);
    expect(summary.labels).toEqual([
      { label: "Yes", count: 6, share: 0.6 },
      { label: "No", count: 2, share: 0.2 },
      // A label the question no longer lists still counts, after its own.
      { label: "Maybe", count: 1, share: 0.1 },
    ]);
    expect(summary.errors).toEqual({ count: 1, share: 0.1 });
    expect(summary.skipped.count).toBe(0);
    expect(summarizeCounts(undefined, ITEM).total).toBe(0);
  });
});
