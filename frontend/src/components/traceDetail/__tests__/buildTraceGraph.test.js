import { describe, expect, it } from "vitest";
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

describe("buildTraceGraph inferred execution", () => {
  it("builds a local fork and joins the next sibling from every branch", () => {
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
      "agent:guard->agent:answer",
      "agent:lookup->agent:answer",
      "agent:root->agent:guard",
      "agent:root->agent:lookup",
    ]);
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

  it("joins the next sibling from the prior subtree terminal", () => {
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
      "agent:answer->agent:evaluation",
      "agent:generation->agent:answer",
      "agent:root->agent:generation",
    ]);
    expect(edgePairs(graph)).not.toContain(
      "agent:generation->agent:evaluation",
    );
  });

  it("fails closed to hierarchy when a sibling timestamp is malformed", () => {
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
  });
});
