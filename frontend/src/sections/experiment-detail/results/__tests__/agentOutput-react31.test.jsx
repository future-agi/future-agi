// Agent (graph) experiment runs store each row's output as
// {"response": "..."}. The results grid handed that object to React as a
// child (minified React error #31) and the whole experiment page showed
// "Something went wrong" the moment an agent run completed. Every output
// render path now shows the response as text.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";
import OutputCell, { diffSource } from "../cells/OutputCell";
import { errorText, outputText, outputTextOrNull } from "../outputText";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));
vi.mock("src/sections/common/Trace/TraceChip", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/Review/CorrectedOutputChip", () => ({
  default: () => null,
}));

const RESPONSE = "echo: What is the capital of France?";
const agentResult = (extra = {}) => ({
  status: "completed",
  output: { response: RESPONSE },
  ...extra,
});
const runsById = {
  "run-agent": { run_id: "run-agent", label: "#1A", target_type: "agent" },
  "run-prompt": { run_id: "run-prompt", label: "#2A", target_type: "prompt" },
};

describe("outputText", () => {
  it("shows a one-port agent output as its text, strings as they are, and JSON for other values", () => {
    expect(outputText({ response: RESPONSE })).toBe(RESPONSE);
    // The port is named by the graph: any one text port shows as its text.
    expect(outputText({ answer: "Paris" })).toBe("Paris");
    expect(outputText("plain text")).toBe("plain text");
    expect(outputText(null)).toBe("");
    expect(outputText(undefined)).toBe("");
    expect(outputText(42)).toBe("42");
    expect(outputText([1, 2])).toBe("[1,2]");
    // Several ports, or a port that is not text, show every field as JSON.
    expect(outputText({ response: "Paris", tool_calls: 1 })).toBe(
      '{"response":"Paris","tool_calls":1}',
    );
    expect(outputText({ answer: "Paris", note: null })).toBe(
      '{"answer":"Paris","note":null}',
    );
    expect(outputText({ answer: { city: "Paris" } })).toBe(
      '{"answer":{"city":"Paris"}}',
    );
    expect(outputTextOrNull(undefined)).toBeNull();
    expect(outputTextOrNull({ response: RESPONSE })).toBe(RESPONSE);
  });

  it("shows a stored error's message", () => {
    expect(errorText({ type: "timeout", message: "The agent timed out" })).toBe(
      "The agent timed out",
    );
    expect(errorText("Run failed")).toBe("Run failed");
    expect(errorText(null)).toBe("");
  });
});

describe("OutputCell with an agent run's output", () => {
  it("renders the response text instead of throwing", () => {
    const { container } = render(
      <OutputCell
        data={{ example_id: "ex-1", results: { "run-agent": agentResult() } }}
        runId="run-agent"
        context={{ runsById }}
      />,
    );
    expect(container.textContent).toContain(RESPONSE);
    expect(container.textContent).not.toContain("[object Object]");
  });

  it("renders a graph's one output port, whatever its name", () => {
    const { container } = render(
      <OutputCell
        data={{
          example_id: "ex-1",
          results: {
            "run-agent": agentResult({ output: { answer: "echo: Paris" } }),
          },
        }}
        runId="run-agent"
        context={{ runsById }}
      />,
    );
    expect(container.textContent).toContain("echo: Paris");
    expect(container.textContent).not.toContain('{"answer"');
  });

  it("shows an error object's message, not [object Object]", () => {
    const { container } = render(
      <OutputCell
        data={{
          example_id: "ex-1",
          results: {
            "run-agent": {
              status: "error",
              error: { type: "timeout", message: "The agent timed out" },
            },
          },
        }}
        runId="run-agent"
        context={{ runsById }}
      />,
    );
    expect(container.textContent).toContain("Error · The agent timed out");
    expect(container.textContent).not.toContain("[object Object]");
  });

  it("renders each trial's response when the run has several trials", () => {
    const { container } = render(
      <OutputCell
        data={{
          example_id: "ex-1",
          results: {
            "run-agent": agentResult({
              trial_outputs: [{ response: "first try" }, { response: "second try" }],
            }),
          },
        }}
        runId="run-agent"
        context={{ runsById }}
      />,
    );
    expect(container.textContent).toContain("first try");
  });

  it("Raw JSON values still shows the output as stored", () => {
    const { container } = render(
      <OutputCell
        data={{ example_id: "ex-1", results: { "run-agent": agentResult() } }}
        runId="run-agent"
        context={{ runsById, valueFormat: "raw" }}
      />,
    );
    expect(container.textContent).toContain(
      JSON.stringify({ response: RESPONSE }),
    );
  });

  it("an agent baseline diffs as its response text", () => {
    const row = {
      example_id: "ex-1",
      results: {
        "run-agent": agentResult(),
        "run-prompt": { status: "completed", output: "Paris" },
      },
    };
    expect(
      diffSource(row, "run-prompt", { diff: "baseline", baselineId: "run-agent" }),
    ).toBe(RESPONSE);
  });
});
