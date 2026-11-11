import { describe, expect, it } from "vitest";

import { parseAgentGraphResponse } from "../agent-graph-contract";

const node = {
  id: "agent:a",
  name: "a",
  type: "agent",
  span_count: 7,
  avg_latency_ms: 90,
  total_tokens: 12,
  total_cost: 0.02,
  error_count: 2,
  trace_count: 4,
};

const edge = {
  source: "agent:a",
  target: "agent:a",
  transition_count: 3,
  avg_latency_ms: 90,
  total_tokens: 12,
  total_cost: 0.02,
  error_count: 2,
  trace_count: 4,
  is_self_loop: true,
};

describe("parseAgentGraphResponse", () => {
  it("returns a generated-contract-validated canonical result", () => {
    const result = parseAgentGraphResponse({
      status: true,
      result: { nodes: [node], edges: [edge], path_edges: [] },
    });

    expect(result.nodes).toEqual([node]);
    expect(result.edges).toEqual([edge]);
    expect(result.path_edges).toEqual([]);
  });

  it.each([
    ["missing envelope", { result: { nodes: [], edges: [], path_edges: [] } }],
    [
      "unsuccessful envelope",
      { status: false, result: { nodes: [], edges: [], path_edges: [] } },
    ],
    [
      "missing path projection",
      { status: true, result: { nodes: [], edges: [] } },
    ],
    [
      "missing node metrics",
      {
        status: true,
        result: {
          nodes: [{ id: "agent:a", name: "a", type: "agent" }],
          edges: [],
          path_edges: [],
        },
      },
    ],
    [
      "legacy camelCase aliases",
      {
        status: true,
        result: {
          nodes: [{ ...node, span_count: undefined, spanCount: 7 }],
          edges: [],
          path_edges: [],
        },
      },
    ],
  ])("rejects %s instead of manufacturing graph data", (_, payload) => {
    expect(() => parseAgentGraphResponse(payload)).toThrow();
  });
});
