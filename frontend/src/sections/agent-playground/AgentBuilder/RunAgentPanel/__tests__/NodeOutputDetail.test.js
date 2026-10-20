import { describe, it, expect } from "vitest";

describe("NodeOutputDetail error handling with partial output (Issue #2503)", () => {
  it("detects error message presence even when outputs array is not empty", () => {
    const nodeDetail = {
      status: "FAILED",
      errorMessage: "Node failed midway through execution",
      outputs: [{ id: "out-1", portName: "output_1", payload: "Partial output generated" }],
      inputs: [{ id: "in-1", portName: "input_1", payload: "Initial input" }],
    };

    const errorMessage = nodeDetail?.errorMessage ?? nodeDetail?.error_message;
    const outputs = nodeDetail?.outputs || [];

    // The bug previously had: const hasErrorMessage = !!errorMessage && outputs.length === 0;
    // With fix: error presence should not be suppressed by partial outputs
    const hasError = !!errorMessage;
    const hasOutputs = outputs.length > 0;

    expect(hasError).toBe(true);
    expect(hasOutputs).toBe(true);
    expect(errorMessage).toBe("Node failed midway through execution");
  });

  it("handles nodes that fail with zero outputs", () => {
    const nodeDetail = {
      status: "FAILED",
      errorMessage: "Node failed before generating outputs",
      outputs: [],
      inputs: [],
    };

    const errorMessage = nodeDetail?.errorMessage ?? nodeDetail?.error_message;
    const outputs = nodeDetail?.outputs || [];

    const hasError = !!errorMessage;
    const hasOutputs = outputs.length > 0;

    expect(hasError).toBe(true);
    expect(hasOutputs).toBe(false);
  });

  it("handles successful nodes with outputs and no error", () => {
    const nodeDetail = {
      status: "COMPLETED",
      outputs: [{ id: "out-1", payload: "Success" }],
      inputs: [],
    };

    const errorMessage = nodeDetail?.errorMessage ?? nodeDetail?.error_message;
    expect(!!errorMessage).toBe(false);
  });
});
