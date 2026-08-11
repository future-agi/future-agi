import { describe, it, expect } from "vitest";
import { buildFlowData } from "../AgentGraph";

const dataById = (result, id) => result.nodes.find((n) => n.id === id)?.data;

// buildFlowData feeds two different producers into the same AgentNode tooltip,
// which reads snake_case keys (span_count, avg_latency_ms, ...):
//   - the agent_graph API (snake_case; the axios interceptor also adds camelCase aliases)
//   - buildTraceGraph (client-side, camelCase only — never hits axios)
// The mapping has to land real snake_case values for both.
describe("buildFlowData metric mapping", () => {
  it("preserves API metrics that arrive with both snake_case and camelCase (no regression on project/compare graph)", () => {
    const apiNode = {
      id: "LLM:openai_chat",
      name: "openai_chat",
      type: "llm",
      span_count: 5,
      spanCount: 5,
      avg_latency_ms: 800,
      avgLatencyMs: 800,
      total_tokens: 2250,
      totalTokens: 2250,
      total_cost: 0.06,
      totalCost: 0.06,
      error_count: 0,
      errorCount: 0,
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
});
