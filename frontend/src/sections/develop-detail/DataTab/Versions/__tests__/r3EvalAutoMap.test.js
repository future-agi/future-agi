import { describe, expect, it } from "vitest";
import {
  autoMapEvalKeys,
  evalDisplayName,
} from "src/sections/develop-detail/Experiment/variableMapping";

// IT-R3 G5 P3: `Add` on an existing eval maps its keys without a form.
const COLS = [
  { id: "c-msg", name: "customer_message", role: "input" },
  { id: "c-exp", name: "expected_reply", role: "expected" },
  { id: "c-conv", name: "conversation", role: "input" },
];

describe("autoMapEvalKeys", () => {
  it("maps output to the run output, expected to the expected column", () => {
    expect(
      autoMapEvalKeys(["output", "expected", "input"], COLS, {
        referenceColumnId: "c-exp",
      }),
    ).toEqual({ output: "output", expected: "c-exp", input: "c-msg" });
  });
  it("defaults to output when the eval lists no keys", () => {
    expect(autoMapEvalKeys([], COLS)).toEqual({ output: "output" });
  });
  it("maps same-name keys and leaves unknown ones for the pencil", () => {
    expect(autoMapEvalKeys(["conversation", "policy"], COLS)).toEqual({
      conversation: "c-conv",
    });
  });
});

describe("evalDisplayName", () => {
  it("names picker rows like the wizard cards", () => {
    expect(evalDisplayName("correctness_vs_reference")).toBe("Correctness");
    expect(evalDisplayName("task_completion")).toBe("Task completion");
    expect(evalDisplayName("resolution")).toBe("Resolution");
    expect(evalDisplayName("Tone")).toBe("Tone");
  });
});
