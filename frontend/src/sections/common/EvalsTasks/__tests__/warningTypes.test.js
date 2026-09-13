import { describe, expect, it } from "vitest";

import {
  GROUND_TRUTH_NOT_APPLIED_WARNING_TYPE,
  PARTIAL_INPUT_WARNING_TYPE,
  warningMessage,
  warningTypeLabel,
} from "../warningTypes";

// These two strings are the wire contract with tracer/utils/eval.py and
// tracer/views/eval_task.py. A rename on either side has to break here, not
// silently degrade every warning chip to "Warning".
describe("warning type values match what the backend emits", () => {
  it("ground truth", () => {
    expect(GROUND_TRUTH_NOT_APPLIED_WARNING_TYPE).toBe(
      "ground_truth_not_applied",
    );
  });

  it("partial input", () => {
    expect(PARTIAL_INPUT_WARNING_TYPE).toBe("partial_input");
  });
});

describe("warningTypeLabel", () => {
  it("labels both known types", () => {
    expect(warningTypeLabel(GROUND_TRUTH_NOT_APPLIED_WARNING_TYPE)).toBe(
      "Ground Truth not applied",
    );
    expect(warningTypeLabel(PARTIAL_INPUT_WARNING_TYPE)).toBe("Partial inputs");
  });

  it("falls back for a type the frontend does not know yet", () => {
    expect(warningTypeLabel("something_new")).toBe("Warning");
    expect(warningTypeLabel(undefined)).toBe("Warning");
  });
});

describe("warningMessage", () => {
  it("prefers the message the server sent", () => {
    expect(
      warningMessage({
        type: GROUND_TRUTH_NOT_APPLIED_WARNING_TYPE,
        message: "Ground Truth is enabled but was not applied to this run.",
      }),
    ).toBe("Ground Truth is enabled but was not applied to this run.");
  });

  it("falls back to the local copy when the server sent none", () => {
    expect(warningMessage({ type: PARTIAL_INPUT_WARNING_TYPE })).toContain(
      "Eval ran with some inputs empty",
    );
  });

  // The task-logs endpoint fills the ground truth message from the backend
  // table, so the copy must live there and not be duplicated here.
  it("holds no local copy of the ground truth message", () => {
    expect(warningMessage({ type: GROUND_TRUTH_NOT_APPLIED_WARNING_TYPE })).toBe(
      "",
    );
    expect(
      warningMessage({
        type: GROUND_TRUTH_NOT_APPLIED_WARNING_TYPE,
        message: "from the server",
      }),
    ).toBe("from the server");
  });

  it("is empty for an unknown type with no message", () => {
    expect(warningMessage({ type: "something_new" })).toBe("");
    expect(warningMessage(undefined)).toBe("");
  });
});
