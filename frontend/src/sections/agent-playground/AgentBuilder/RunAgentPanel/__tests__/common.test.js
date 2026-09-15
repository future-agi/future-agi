import { describe, it, expect } from "vitest";
import {
  getNodeConfig,
  getNodeDurationMs,
  mapExecutionNodesToTree,
} from "../common";

describe("getNodeConfig", () => {
  it("returns prompt config for 'llm_prompt' type", () => {
    const config = getNodeConfig("llm_prompt");
    expect(config.iconSrc).toContain("ic_chat_single");
    expect(config.color).toBe("orange.500");
  });

  it("returns agent config for 'agent' type", () => {
    const config = getNodeConfig("agent");
    expect(config.iconSrc).toContain("ic_agents");
    expect(config.color).toBe("purple.500");
  });

  it("returns eval config for 'eval' type", () => {
    const config = getNodeConfig("eval");
    expect(config.iconSrc).toContain("ic_rounded_square");
    expect(config.color).toBe("green.600");
  });

  it("returns default config for unknown type", () => {
    const config = getNodeConfig("unknown_type");
    expect(config.color).toBe("text.secondary");
  });

  it("returns default config for undefined type", () => {
    const config = getNodeConfig(undefined);
    expect(config.color).toBe("text.secondary");
  });

  it("maps executed nodes into the tree shape with nested ids", () => {
    const nodes = mapExecutionNodesToTree([
      {
        id: "parent",
        name: "Parent",
        type: "subgraph",
        node_execution: { id: "parent-exec", duration_seconds: 1.5 },
        sub_graph: {
          nodes: [
            {
              id: "child",
              name: "Child",
              type: "atomic",
              node_execution: { id: "child-exec" },
            },
            { id: "pending-child", name: "Pending child" },
          ],
        },
      },
      { id: "pending", name: "Pending" },
    ]);

    expect(nodes).toEqual([
      {
        id: "parent",
        type: "agent",
        name: "Parent",
        duration: 1500,
        children: [
          {
            id: "parent__child",
            type: "llm_prompt",
            name: "Child",
            duration: null,
          },
        ],
      },
    ]);
  });

  it("derives duration from timestamps when seconds are unavailable", () => {
    expect(
      getNodeDurationMs({
        node_execution: {
          started_at: "2026-09-11T00:00:00.000Z",
          completed_at: "2026-09-11T00:00:01.250Z",
        },
      }),
    ).toBe(1250);
  });

  it("does not create token or cost values when they are absent", () => {
    const [node] = mapExecutionNodesToTree([
      {
        id: "node-1",
        name: "Node 1",
        type: "atomic",
        nodeExecution: { id: "exec-1" },
      },
    ]);

    expect(node).not.toHaveProperty("tokens");
    expect(node).not.toHaveProperty("cost");
  });
});
