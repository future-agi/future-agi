import { describe, it, expect } from "vitest";
import { buildNodeOutputTree, getNodeConfig } from "../common";

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
});

describe("buildNodeOutputTree", () => {
  it("maps executed node fields and converts seconds to milliseconds", () => {
    const result = buildNodeOutputTree([
      {
        id: "prompt",
        name: "Fallback name",
        node_execution: {
          node_name: "Generate answer",
          node_type: "llm_prompt",
          duration_seconds: 1.25,
        },
      },
    ]);

    expect(result).toEqual([
      {
        id: "prompt",
        name: "Generate answer",
        type: "llm_prompt",
        duration: 1250,
        children: [],
      },
    ]);
  });

  it("omits nodes that did not execute and prefixes subgraph child ids", () => {
    const result = buildNodeOutputTree([
      { id: "skipped", name: "Skipped" },
      {
        id: "agent",
        name: "Nested agent",
        subGraph: {
          nodes: [
            {
              id: "inner",
              nodeExecution: {
                node_name: "Inner prompt",
                node_type: "llm_prompt",
                duration_seconds: 0.5,
              },
            },
          ],
        },
      },
    ]);

    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("agent");
    expect(result[0].children[0]).toMatchObject({
      id: "agent__inner",
      name: "Inner prompt",
      duration: 500,
    });
  });

  it("includes cost and token metrics only when the API supplies them", () => {
    const [node] = buildNodeOutputTree([
      {
        id: "prompt",
        nodeExecution: { cost: 0, tokens: 0 },
      },
    ]);

    expect(node).toMatchObject({ cost: 0, tokens: 0 });
  });
});
