// W1-2: the results eval header says what the cells were scored with.
import { describe, expect, it } from "vitest";
import { scoredWithText } from "../scoredWithText";

describe("scoredWithText", () => {
  it("one line when every run shares the definition", () => {
    expect(
      scoredWithText(
        "Groundedness",
        { a: "v4", b: "v4" },
        {
          a: { threshold: 0.95, judge_model: "gpt-4o" },
          b: { threshold: 0.95, judge_model: "gpt-4o" },
        },
      ),
    ).toBe("Groundedness v4 · Pass ≥ 0.95 · judge gpt-4o");
  });
  it("per run when they differ; a pass/fail eval names no bar", () => {
    expect(
      scoredWithText(
        "Correctness",
        { a: "v2", b: "v3" },
        {
          a: { threshold: null, judge_model: "turing_large" },
          b: { threshold: null, judge_model: "gpt-4o" },
        },
        { a: "#2A", b: "#2A·2" },
      ),
    ).toBe(
      "#2A: Correctness v2 · judge turing_large · #2A·2: Correctness v3 · judge gpt-4o",
    );
    expect(scoredWithText("Tone", {}, {})).toBe("Tone");
  });
});
