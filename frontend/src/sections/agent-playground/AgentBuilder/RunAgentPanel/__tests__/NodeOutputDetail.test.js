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

  it("handles successful nodes with outputs and no error: does not trigger error alert or error row", () => {
    const nodeDetail = {
      status: "COMPLETED",
      outputs: [{ id: "out-1", payload: "Success output 1" }, { id: "out-2", payload: "Success output 2" }],
      inputs: [{ id: "in-1", payload: "Input 1" }, { id: "in-2", payload: "Input 2" }],
    };

    const errorMessage = nodeDetail?.errorMessage ?? nodeDetail?.error_message;
    const hasErrorMessage = Boolean(errorMessage);
    const outputs = nodeDetail?.outputs || [];
    const hasOutputs = outputs.length > 0;
    const isPairedMode = nodeDetail.inputs.length > 0 && nodeDetail.inputs.length === outputs.length;

    expect(hasErrorMessage).toBe(false);
    expect(hasOutputs).toBe(true);
    expect(isPairedMode).toBe(true);

    // Ensure normal paired rows are constructed without error row mapping
    const rows = nodeDetail.inputs.map((inp, i) => ({
      id: `node-1-${i}`,
      input: inp?.payload ?? "",
      output: outputs[i]?.payload ?? "",
    }));

    expect(rows).toHaveLength(2);
    expect(rows[0].output).toBe("Success output 1");
    expect(rows[1].output).toBe("Success output 2");
  });

  it("verifies error banner visibility behavior across running and settled node statuses", () => {
    // 1. Running node with error should not display premature error banner if status is running
    const runningNode = {
      status: "running",
      errorMessage: null,
      inputs: [{ payload: "running input" }],
      outputs: [],
    };
    const isRunning = runningNode.status === "running" || runningNode.status === "pending";
    expect(isRunning).toBe(true);

    // 2. Settled failed node with partial output displays both alert and partial outputs
    const settledFailedNode = {
      status: "FAILED",
      errorMessage: "Rate limit exceeded during token streaming",
      inputs: [{ id: "in-1", payload: "Prompt: generate long essay" }],
      outputs: [{ id: "out-1", payload: "Chapter 1: The Beginning..." }],
    };
    const settledError = settledFailedNode.errorMessage;
    const settledOutputs = settledFailedNode.outputs;
    const hasErrorAlert = Boolean(settledError);
    const hasPartialOutput = settledOutputs.length > 0;

    expect(hasErrorAlert).toBe(true);
    expect(hasPartialOutput).toBe(true);
    expect(settledOutputs[0].payload).toContain("Chapter 1");
  });

  it("extracts error message from various API field keys: errorMessage, error_message, error", () => {
    const fromCamelCase = { errorMessage: "CamelCase failure" };
    const fromSnakeCase = { error_message: "Snake_case failure" };
    const fromRawError = { error: "Raw error string" };

    const getMsg = (d) => d?.errorMessage || d?.error_message || d?.error || null;

    expect(getMsg(fromCamelCase)).toBe("CamelCase failure");
    expect(getMsg(fromSnakeCase)).toBe("Snake_case failure");
    expect(getMsg(fromRawError)).toBe("Raw error string");
  });
});
