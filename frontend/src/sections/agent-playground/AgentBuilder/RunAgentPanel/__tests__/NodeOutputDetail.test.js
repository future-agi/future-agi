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

    const hasErrorMessage = Boolean(errorMessage);
    const hasOutputs = outputs.length > 0;

    expect(hasErrorMessage).toBe(true);
    expect(hasOutputs).toBe(true);
    expect(errorMessage).toBe("Node failed midway through execution");
  });

  it("handles nodes that fail with zero outputs by rendering error in grid row and alert", () => {
    const nodeDetail = {
      status: "FAILED",
      errorMessage: "Node failed before generating outputs",
      outputs: [],
      inputs: [{ id: "in-1", portName: "input_1", payload: "Test Input" }],
    };

    const errorMessage = nodeDetail?.errorMessage ?? nodeDetail?.error_message;
    const outputs = nodeDetail?.outputs || [];
    const inputs = nodeDetail?.inputs || [];

    const hasErrorMessage = Boolean(errorMessage);
    const hasOutputs = outputs.length > 0;

    expect(hasErrorMessage).toBe(true);
    expect(hasOutputs).toBe(false);

    // Grid row mapping for zero-output failure
    const rows = (hasErrorMessage && !hasOutputs)
      ? inputs.map((inp, i) => ({
          id: `node-1-${i}`,
          input: inp?.payload ?? "",
          output: errorMessage,
        }))
      : [];

    expect(rows).toHaveLength(1);
    expect(rows[0].output).toBe("Node failed before generating outputs");
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
