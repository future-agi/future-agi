import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import AgentGraph, { buildFlowData } from "../AgentGraph";
import { AGGREGATION_POLLING_PAUSED_MESSAGE } from "src/utils/queryReadState";

const canonicalNode = (id, type = "agent", metrics = {}) => ({
  id,
  name: id.split(":").at(-1),
  type,
  span_count: 1,
  avg_latency_ms: 10,
  total_tokens: 2,
  total_cost: 0.01,
  error_count: 0,
  trace_count: 1,
  ...metrics,
});

const canonicalEdge = (source, target, metrics = {}) => ({
  source,
  target,
  transition_count: 1,
  avg_latency_ms: 10,
  total_tokens: 2,
  total_cost: 0.01,
  error_count: 0,
  trace_count: 1,
  is_self_loop: source === target,
  ...metrics,
});

const dataById = (result, id) => result.nodes.find((n) => n.id === id)?.data;

describe("buildFlowData canonical graph contract", () => {
  it("preserves canonical snake_case metrics without aliases", () => {
    const node = canonicalNode("llm:openai_chat", "llm", {
      span_count: 5,
      avg_latency_ms: 800,
      total_tokens: 2250,
      total_cost: 0.06,
    });

    const data = dataById(buildFlowData({ nodes: [node], edges: [] }), node.id);

    expect(data).toEqual(expect.objectContaining(node));
  });

  it.each([
    ["missing graph", null],
    ["missing arrays", {}],
    [
      "missing node metrics",
      { nodes: [{ id: "tool:noop", name: "noop", type: "tool" }], edges: [] },
    ],
    [
      "legacy camelCase node metrics",
      {
        nodes: [
          {
            id: "tool:noop",
            name: "noop",
            type: "tool",
            spanCount: 1,
            avgLatencyMs: 10,
          },
        ],
        edges: [],
      },
    ],
    [
      "missing edge metrics",
      {
        nodes: [canonicalNode("agent:a"), canonicalNode("tool:b", "tool")],
        edges: [{ source: "agent:a", target: "tool:b" }],
      },
    ],
    [
      "unknown edge endpoint",
      {
        nodes: [canonicalNode("agent:a")],
        edges: [canonicalEdge("agent:a", "tool:missing")],
      },
    ],
  ])("rejects %s instead of defaulting values", (_, graph) => {
    expect(() => buildFlowData(graph)).toThrow();
  });

  it("accepts a canonical empty graph", () => {
    expect(buildFlowData({ nodes: [], edges: [] })).toEqual({
      nodes: [],
      edges: [],
    });
  });

  it("accepts disclosed inexact trace counts on folded nodes and edges", () => {
    const graph = buildFlowData({
      nodes: [
        canonicalNode("aggregate:other", "aggregate", {
          trace_count: null,
          trace_count_exact: false,
        }),
      ],
      edges: [
        canonicalEdge("aggregate:other", "aggregate:other", {
          trace_count: null,
          trace_count_exact: false,
        }),
      ],
    });

    expect(graph.nodes[0].data).toEqual(
      expect.objectContaining({ trace_count: null, trace_count_exact: false }),
    );
    expect(graph.edges).toHaveLength(1);
  });

  it.each([undefined, true])(
    "rejects a null trace count unless exactness is explicitly false (%s)",
    (traceCountExact) => {
      expect(() =>
        buildFlowData({
          nodes: [
            canonicalNode("aggregate:other", "aggregate", {
              trace_count: null,
              trace_count_exact: traceCountExact,
            }),
          ],
          edges: [],
        }),
      ).toThrow("inexact trace_count without disclosure");
    },
  );

  it("uses graph edges rather than substituting path edges", () => {
    const graph = buildFlowData({
      nodes: [
        canonicalNode("chain:root", "chain"),
        canonicalNode("chain:query", "chain"),
        canonicalNode("retriever:lookup", "retriever"),
      ],
      edges: [
        canonicalEdge("chain:root", "chain:query"),
        canonicalEdge("chain:root", "retriever:lookup"),
      ],
      path_edges: [
        canonicalEdge("chain:root", "chain:query"),
        canonicalEdge("chain:query", "retriever:lookup", {
          transition_count: 3,
        }),
      ],
    });

    expect(
      graph.edges.map(({ source, target }) => `${source}->${target}`),
    ).toEqual(["chain:root->chain:query", "chain:root->retriever:lookup"]);
  });

  it("does not replace an explicitly empty graph with path edges", () => {
    const graph = buildFlowData({
      nodes: [
        canonicalNode("chain:root", "chain"),
        canonicalNode("tool:child", "tool"),
      ],
      edges: [],
      path_edges: [canonicalEdge("chain:root", "tool:child")],
    });

    expect(graph.edges).toEqual([]);
  });

  it("retains forks, joins, back edges, and self-loops", () => {
    const graph = buildFlowData({
      nodes: [
        canonicalNode("agent:root"),
        canonicalNode("tool:left", "tool"),
        canonicalNode("tool:right", "tool"),
        canonicalNode("llm:join", "llm"),
      ],
      edges: [
        canonicalEdge("agent:root", "tool:left"),
        canonicalEdge("agent:root", "tool:right"),
        canonicalEdge("tool:left", "llm:join"),
        canonicalEdge("tool:right", "llm:join"),
        canonicalEdge("llm:join", "tool:left"),
        canonicalEdge("tool:left", "tool:left"),
      ],
    });

    expect(graph.edges).toHaveLength(6);
    expect(
      graph.edges.find(
        (edge) => edge.source === "tool:left" && edge.target === "tool:left",
      ),
    ).toEqual(expect.objectContaining({ animated: true }));
    graph.nodes.forEach((node) => {
      expect(Number.isFinite(node.position.x)).toBe(true);
      expect(Number.isFinite(node.position.y)).toBe(true);
    });
  });
});

describe("AgentGraph request states", () => {
  it("renders loading before validating absent pending data", () => {
    render(<AgentGraph data={undefined} isLoading isError={false} />);

    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    expect(screen.getByText("Loading graph data…")).toBeInTheDocument();
  });

  it("renders a sanitized error before validating absent failed data", () => {
    render(<AgentGraph data={undefined} isLoading={false} isError />);

    expect(
      screen.getByText(
        "We couldn't load the agent graph. Please retry in a moment.",
      ),
    ).toBeInTheDocument();
  });

  it("renders a neutral paused state when the exact job outlives polling", () => {
    render(
      <AgentGraph
        data={undefined}
        isLoading={false}
        isError={false}
        pollingPaused
      />,
    );

    expect(screen.getByText(AGGREGATION_POLLING_PAUSED_MESSAGE)).toBeVisible();
    expect(
      screen.queryByText(
        "We couldn't load the agent graph. Please retry in a moment.",
      ),
    ).not.toBeInTheDocument();
  });

  it("keeps a cached graph visible while reporting paused polling", () => {
    const { container } = render(
      <AgentGraph
        data={{
          nodes: [canonicalNode("agent:a")],
          edges: [],
          path_edges: [],
        }}
        isLoading={false}
        isError={false}
        pollingPaused
      />,
    );

    expect(screen.getByText(AGGREGATION_POLLING_PAUSED_MESSAGE)).toBeVisible();
    expect(container.querySelector(".react-flow")).toBeInTheDocument();
  });
});
