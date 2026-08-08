import { describe, it, expect } from "vitest";
import { buildFlowData } from "../AgentGraph";

const dataById = (result, id) => result.nodes.find((n) => n.id === id)?.data;

// buildFlowData feeds two different producers into the same AgentNode tooltip,
// which reads snake_case keys (span_count, avg_latency_ms, ...):
//   - the agent_graph API (canonical snake_case)
//   - buildTraceGraph (client-side, camelCase only — never hits axios)
// The mapping has to land real snake_case values for both.
describe("buildFlowData metric mapping", () => {
  it("preserves canonical snake_case API metrics without relying on aliases", () => {
    const apiNode = {
      id: "LLM:openai_chat",
      name: "openai_chat",
      type: "llm",
      span_count: 5,
      avg_latency_ms: 800,
      total_tokens: 2250,
      total_cost: 0.06,
      error_count: 0,
    };

    const data = dataById(
      buildFlowData({ nodes: [apiNode], edges: [] }),
      "LLM:openai_chat",
    );

    expect(data.span_count).toBe(5);
    expect(data.avg_latency_ms).toBe(800);
    expect(data.total_tokens).toBe(2250);
    expect(data.total_cost).toBe(0.06);
    expect(data.error_count).toBe(0);
  });

  it("populates snake_case metrics from a camelCase-only node (fixes the blank trace-detail tooltip)", () => {
    const traceNode = {
      id: "LLM:openai_chat",
      name: "openai_chat",
      type: "llm",
      spanCount: 2,
      avgLatencyMs: 700,
      totalTokens: 750,
      totalCost: 0.02,
      errorCount: 1,
    };

    const data = dataById(
      buildFlowData({ nodes: [traceNode], edges: [] }),
      "LLM:openai_chat",
    );

    expect(data.span_count).toBe(2);
    expect(data.avg_latency_ms).toBe(700);
    expect(data.total_tokens).toBe(750);
    expect(data.total_cost).toBe(0.02);
    expect(data.error_count).toBe(1);
  });

  it("defaults missing metrics to 0", () => {
    const bareNode = { id: "TOOL:noop", name: "noop", type: "tool" };

    const data = dataById(
      buildFlowData({ nodes: [bareNode], edges: [] }),
      "TOOL:noop",
    );

    expect(data.span_count).toBe(0);
    expect(data.avg_latency_ms).toBe(0);
    expect(data.total_tokens).toBe(0);
    expect(data.total_cost).toBe(0);
    expect(data.error_count).toBe(0);
  });

  it("returns empty nodes/edges for empty or missing graph data", () => {
    expect(buildFlowData({ nodes: [], edges: [] })).toEqual({
      nodes: [],
      edges: [],
    });
    expect(buildFlowData(null)).toEqual({ nodes: [], edges: [] });
  });

  it("normalizes camelCase trace-detail edge metrics", () => {
    const graph = buildFlowData({
      nodes: [
        { id: "agent:a", name: "a", type: "agent" },
        { id: "tool:b", name: "b", type: "tool" },
      ],
      edges: [
        {
          source: "agent:a",
          target: "tool:b",
          transitionCount: 7,
          isSelfLoop: true,
        },
      ],
    });

    expect(graph.edges[0]).toEqual(
      expect.objectContaining({ label: "×7", animated: true }),
    );
  });

  it("uses recorded parent-span topology instead of chronological path edges", () => {
    const graph = buildFlowData({
      nodes: [
        { id: "chain:root", name: "root", type: "chain" },
        { id: "chain:query", name: "query", type: "chain" },
        { id: "retriever:lookup", name: "lookup", type: "retriever" },
      ],
      // These are authoritative parent_span_id relationships.
      edges: [
        { source: "chain:root", target: "chain:query" },
        { source: "chain:root", target: "retriever:lookup" },
      ],
      path_edges: [
        { source: "chain:root", target: "chain:query" },
        {
          source: "chain:query",
          target: "retriever:lookup",
          transition_count: 3,
        },
      ],
    });

    expect(
      graph.edges.map(({ source, target }) => `${source}->${target}`),
    ).toEqual(["chain:root->chain:query", "chain:root->retriever:lookup"]);
  });

  it("does not replace an explicitly empty hierarchy with chronological edges", () => {
    const graph = buildFlowData({
      nodes: [
        { id: "chain:root", name: "root", type: "chain" },
        { id: "tool:child", name: "child", type: "tool" },
      ],
      edges: [],
      path_edges: [{ source: "chain:root", target: "tool:child" }],
    });

    expect(graph.edges).toEqual([]);
  });

  it("retains forks, joins, back edges, and self-loops with finite layout positions", () => {
    const graph = buildFlowData({
      nodes: [
        { id: "agent:root", name: "root", type: "agent" },
        { id: "tool:left", name: "left", type: "tool" },
        { id: "tool:right", name: "right", type: "tool" },
        { id: "llm:join", name: "join", type: "llm" },
      ],
      edges: [
        { source: "agent:root", target: "tool:left" },
        { source: "agent:root", target: "tool:right" },
        { source: "tool:left", target: "llm:join" },
        { source: "tool:right", target: "llm:join" },
        { source: "llm:join", target: "tool:left" },
        {
          source: "tool:left",
          target: "tool:left",
          is_self_loop: true,
        },
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
