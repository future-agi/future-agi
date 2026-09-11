import { describe, expect, it } from "vitest";
import { buildGraphDiff, comparisonGraphForMode } from "../buildGraphDiff";

const node = (id) => ({
  id,
  name: id,
  type: "agent",
  error_count: 0,
  avg_latency_ms: 1,
});

describe("comparisonGraphForMode", () => {
  it("keeps Agent Path on canonical nodes instead of diff-only ghosts", () => {
    const failGraph = {
      nodes: [node("recorded")],
      edges: [],
      path_edges: [],
    };
    const passGraph = {
      nodes: [node("recorded"), node("missing")],
      edges: [
        {
          source: "recorded",
          target: "missing",
          transition_count: 1,
        },
      ],
      path_edges: [
        {
          source: "recorded",
          target: "missing",
          transition_count: 1,
        },
      ],
    };
    const { failAnnotated } = buildGraphDiff(failGraph, passGraph);

    expect(failAnnotated.nodes.map(({ id }) => id)).toContain("ghost-missing");
    expect(comparisonGraphForMode("path", failGraph, failAnnotated)).toBe(
      failGraph,
    );
    expect(
      comparisonGraphForMode("path", failGraph, failAnnotated).nodes.map(
        ({ id }) => id,
      ),
    ).toEqual(["recorded"]);
    expect(comparisonGraphForMode("graph", failGraph, failAnnotated)).toBe(
      failAnnotated,
    );
  });
});
