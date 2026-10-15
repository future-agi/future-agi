import { describe, expect, it } from "vitest";
import { buildExecutedNodeTree } from "../executionList";

describe("buildExecutedNodeTree", () => {
  it("omits nodes with no execution and maps seconds to milliseconds", () => {
    expect(buildExecutedNodeTree([
      { id: "idle", name: "Idle" },
      { id: "llm", type: "llm_prompt", nodeExecution: { node_name: "Prompt", duration_seconds: 1.25 } },
    ])).toEqual([{ id: "llm", name: "Prompt", type: "llm_prompt", duration: 1250 }]);
  });

  it("preserves nested graph ids and omits unreported metrics", () => {
    const nodes = buildExecutedNodeTree([{
      id: "agent", nodeExecution: { node_name: "Agent" },
      subGraph: { nodes: [{ id: "step", node_execution: { node_name: "Step", duration_seconds: 0 } }] },
    }]);
    expect(nodes[0].children[0]).toMatchObject({ id: "agent__step", duration: 0 });
    expect(nodes[0]).not.toHaveProperty("tokens");
    expect(nodes[0]).not.toHaveProperty("cost");
  });

  it("returns no rows for missing execution data", () => {
    expect(buildExecutedNodeTree()).toEqual([]);
  });
});
