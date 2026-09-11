import { describe, expect, it } from "vitest";
import { TracerTraceAgentGraphResponse } from "src/generated/api-contracts/api.zod";
import { buildTraceGraph } from "../buildTraceGraph";

const entry = (id, name, start, end, children = [], attrs = {}) => ({
  observation_span: {
    id,
    name,
    observation_type: "agent",
    start_time: start,
    end_time: end,
    span_attributes: attrs,
  },
  children,
});

const edgePairs = (graph) =>
  graph.edges
    .filter(
      (edge) => !edge.source.startsWith("__") && !edge.target.startsWith("__"),
    )
    .map((edge) => `${edge.source}->${edge.target}`)
    .sort();

const pathPairs = (graph) =>
  graph.path_edges.map((edge) => `${edge.source}->${edge.target}`).sort();

describe("buildTraceGraph recorded hierarchy", () => {
  it("keeps every sibling on its authoritative parent edge", () => {
    const graph = buildTraceGraph([
      entry(
        "root",
        "root",
        "2026-08-06T10:00:00.000Z",
        "2026-08-06T10:00:10.000Z",
        [
          entry(
            "a",
            "lookup",
            "2026-08-06T10:00:01.000Z",
            "2026-08-06T10:00:03.000Z",
          ),
          entry(
            "b",
            "guard",
            "2026-08-06T10:00:01.500Z",
            "2026-08-06T10:00:04.000Z",
          ),
          entry(
            "c",
            "answer",
            "2026-08-06T10:00:05.000Z",
            "2026-08-06T10:00:06.000Z",
          ),
        ],
      ),
    ]);

    expect(edgePairs(graph)).toEqual([
      "agent:root->agent:answer",
      "agent:root->agent:guard",
      "agent:root->agent:lookup",
    ]);
    expect(pathPairs(graph)).toEqual([]);
  });

  it("does not connect unrelated branches that happen at adjacent times", () => {
    const graph = buildTraceGraph([
      entry(
        "root-a",
        "root-a",
        "2026-08-06T10:00:00.000Z",
        "2026-08-06T10:00:10.000Z",
        [
          entry(
            "a",
            "branch-a",
            "2026-08-06T10:00:01.000Z",
            "2026-08-06T10:00:02.000Z",
          ),
        ],
      ),
      entry(
        "root-b",
        "root-b",
        "2026-08-06T10:00:00.000Z",
        "2026-08-06T10:00:10.000Z",
        [
          entry(
            "b",
            "branch-b",
            "2026-08-06T10:00:03.000Z",
            "2026-08-06T10:00:04.000Z",
          ),
        ],
      ),
    ]);

    expect(edgePairs(graph)).not.toContain("agent:branch-a->agent:branch-b");
    expect(edgePairs(graph)).not.toContain("agent:branch-b->agent:branch-a");
  });

  it("does not infer a sibling path from non-overlapping timestamps", () => {
    const graph = buildTraceGraph([
      entry(
        "root",
        "root",
        "2026-08-06T10:00:00.000Z",
        "2026-08-06T10:00:10.000Z",
        [
          entry(
            "generation",
            "generation",
            "2026-08-06T10:00:01.000Z",
            "2026-08-06T10:00:06.000Z",
            [
              entry(
                "llm",
                "answer",
                "2026-08-06T10:00:02.000Z",
                "2026-08-06T10:00:05.000Z",
              ),
            ],
          ),
          entry(
            "evaluation",
            "evaluation",
            "2026-08-06T10:00:07.000Z",
            "2026-08-06T10:00:08.000Z",
          ),
        ],
      ),
    ]);

    expect(edgePairs(graph)).toEqual([
      "agent:generation->agent:answer",
      "agent:root->agent:evaluation",
      "agent:root->agent:generation",
    ]);
    expect(pathPairs(graph)).toEqual([]);
  });

  it("uses hierarchy regardless of malformed sibling timestamps", () => {
    const graph = buildTraceGraph([
      entry("root", "root", "bad", "bad", [
        entry("a", "a", "bad", "bad"),
        entry("b", "b", "2026-08-06T10:00:03.000Z", "2026-08-06T10:00:04.000Z"),
      ]),
    ]);

    expect(edgePairs(graph)).toEqual([
      "agent:root->agent:a",
      "agent:root->agent:b",
    ]);
  });

  it("keeps explicit graph metadata authoritative", () => {
    const graph = buildTraceGraph([
      entry("a", "ignored-a", "bad", "bad", [], { "graph.node.id": "alpha" }),
      entry("b", "ignored-b", "bad", "bad", [], {
        "graph.node.id": "beta",
        "graph.node.parent_id": "alpha",
      }),
    ]);

    expect(edgePairs(graph)).toContain("alpha->beta");
    expect(graph.path_edges).toEqual([]);
  });

  it("emits the canonical graph fields without inventing camelCase aliases", () => {
    const graph = buildTraceGraph([
      entry(
        "root",
        "root",
        "2026-08-06T10:00:00.000Z",
        "2026-08-06T10:00:01.000Z",
      ),
    ]);
    const root = graph.nodes.find((node) => node.id === "agent:root");

    expect(root).toEqual(
      expect.objectContaining({
        span_count: 1,
        avg_latency_ms: 0,
        total_tokens: 0,
        total_cost: 0,
        error_count: 0,
        trace_count: 1,
      }),
    );
    expect(root).not.toHaveProperty("spanCount");
    expect(graph.edges[0]).toEqual(
      expect.objectContaining({
        transition_count: 1,
        avg_latency_ms: 0,
        total_tokens: 0,
        total_cost: 0,
        error_count: 0,
        trace_count: 1,
        is_self_loop: false,
      }),
    );
    expect(() =>
      TracerTraceAgentGraphResponse.parse({ status: true, result: graph }),
    ).not.toThrow();
  });

  it("rejects a malformed span-tree payload instead of rendering empty data", () => {
    expect(() => buildTraceGraph(null)).toThrow(
      "Trace graph requires a span-tree array",
    );
  });
});
