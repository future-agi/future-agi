// options-truth L5 G2: a saved version's commit message says what changed.
import { describe, expect, it } from "vitest";
import { commitMessageOf } from "../iterateModel";

const agent = {
  kind: "agent",
  agent: {
    limits: { max_iterations: 12 },
    nodes: [{ id: "respond", type: "llm", model: "gpt-4o" }],
  },
  tools: [{ name: "lookup_order", mode: "live" }],
};

describe("commitMessageOf", () => {
  it("names each change with its before and after", () => {
    expect(
      commitMessageOf(
        [{ axis: "agent", field: "max_iterations", value: 5 }],
        agent,
      ),
    ).toBe("max iterations 12 → 5");
    expect(
      commitMessageOf(
        [
          { axis: "agent", node: "respond", field: "prompt", value: "x" },
          { axis: "model", field: "id", value: "claude-sonnet-4-5" },
          { axis: "params", field: "temperature", value: 0.9 },
          { axis: "tools", node: "lookup_order", field: "mode", value: "mock" },
        ],
        agent,
      ),
    ).toBe(
      "respond prompt edited · model gpt-4o → claude-sonnet-4-5 · temperature default → 0.9 · lookup_order live → mock",
    );
    expect(commitMessageOf([], agent)).toBe("");
  });
});
