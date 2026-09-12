import { describe, it, expect } from "vitest";
import { getNodeConfig, getNodeDurationMs, mapExecutionNodesToTree } from "../common";

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

  it("returns prompt config for API type 'atomic'", () => {
    const config = getNodeConfig("atomic");
    expect(config.iconSrc).toContain("ic_chat_single");
    expect(config.color).toBe("orange.500");
  });

  it("returns agent config for API type 'subgraph'", () => {
    const config = getNodeConfig("subgraph");
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
});

describe("getNodeDurationMs", () => {
  it("extracts duration from duration_seconds in node_execution", () => {
    const node = {
      node_execution: { duration_seconds: 3.5 },
    };
    expect(getNodeDurationMs(node)).toBe(3500);
  });

  it("extracts duration from duration_seconds in camelCase nodeExecution", () => {
    const node = {
      nodeExecution: { duration_seconds: 1.2 },
    };
    expect(getNodeDurationMs(node)).toBe(1200);
  });

  it("extracts duration from duration in milliseconds if provided", () => {
    const node = {
      node_execution: { duration: 450 },
    };
    expect(getNodeDurationMs(node)).toBe(450);
  });

  it("computes duration from completed_at - started_at timestamps", () => {
    const node = {
      node_execution: {
        started_at: "2026-09-03T10:00:00.000Z",
        completed_at: "2026-09-03T10:00:02.500Z",
      },
    };
    expect(getNodeDurationMs(node)).toBe(2500);
  });

  it("returns 0 if no execution information is available", () => {
    expect(getNodeDurationMs({})).toBe(0);
    expect(getNodeDurationMs(null)).toBe(0);
  });
});

describe("mapExecutionNodesToTree", () => {
  it("returns empty array for missing or empty executionData", () => {
    expect(mapExecutionNodesToTree(null)).toEqual([]);
    expect(mapExecutionNodesToTree({})).toEqual([]);
    expect(mapExecutionNodesToTree({ nodes: [] })).toEqual([]);
  });

  it("filters out __start__ and __end__ sentinel nodes", () => {
    const executionData = {
      nodes: [
        { id: "__start__", name: "Start" },
        { id: "node-1", name: "Agent 1", type: "agent" },
        { id: "__end__", name: "End" },
      ],
    };
    const result = mapExecutionNodesToTree(executionData);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("node-1");
  });

  it("maps flat nodes with duration and metadata", () => {
    const executionData = {
      nodes: [
        {
          id: "prompt-1",
          name: "Summarize Prompt",
          type: "llm_prompt",
          node_execution: { duration_seconds: 2.1 },
        },
      ],
    };
    const result = mapExecutionNodesToTree(executionData);
    expect(result).toHaveLength(1);
    expect(result[0]).toEqual({
      id: "prompt-1",
      name: "Summarize Prompt",
      type: "llm_prompt",
      duration: 2100,
      cost: 0,
      tokens: 0,
      children: undefined,
    });
  });

  it("maps subgraph nodes and prefixes child node ids with parentId__", () => {
    const executionData = {
      nodes: [
        {
          id: "sub-1",
          name: "Subgraph Parent",
          type: "subgraph",
          node_execution: { duration_seconds: 5.0 },
          sub_graph: {
            nodes: [
              {
                id: "child-1",
                name: "Child Prompt",
                type: "atomic",
                node_execution: { duration_seconds: 1.5 },
              },
            ],
          },
        },
      ],
    };
    const result = mapExecutionNodesToTree(executionData);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("sub-1");
    expect(result[0].duration).toBe(5000);
    expect(result[0].children).toHaveLength(1);
    expect(result[0].children[0]).toEqual({
      id: "sub-1__child-1",
      name: "Child Prompt",
      type: "atomic",
      duration: 1500,
      cost: 0,
      tokens: 0,
    });
  });
});

