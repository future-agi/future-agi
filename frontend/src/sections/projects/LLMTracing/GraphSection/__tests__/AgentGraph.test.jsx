import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import AgentGraph, { buildFlowData } from "../AgentGraph";
import { AGGREGATION_POLLING_PAUSED_MESSAGE } from "src/utils/queryReadState";
import { buildTraceGraph } from "src/components/traceDetail/buildTraceGraph";

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

// ---------------------------------------------------------------------------
// TH-4321: same-level (parallel) nodes must render on one level.
// ---------------------------------------------------------------------------

const traceSpan = (id, name, type, startS, endS, children = []) => ({
  observation_span: {
    id,
    name,
    observation_type: type,
    start_time: new Date(
      Date.UTC(2026, 3, 20, 8) + startS * 1000,
    ).toISOString(),
    end_time: new Date(Date.UTC(2026, 3, 20, 8) + endS * 1000).toISOString(),
    latency_ms: (endS - startS) * 1000,
    span_attributes: {},
  },
  children,
});

// The screenshot on TH-4321: an agent fans out to independent steps, one of
// which (response_generation) has its own nested work.
const screenshotTrace = () => [
  traceSpan("agent", "Agent", "agent", 0, 10, [
    traceSpan("ip", "input_processing", "chain", 1, 3),
    traceSpan("ic", "intent_classification", "llm", 1.2, 3.5),
    traceSpan("qc", "quality_check", "unknown", 1.4, 4),
    traceSpan("rg", "response_generation", "chain", 1.5, 9, [
      traceSpan("pb", "prompt_building", "chain", 2, 3, [
        traceSpan("gen", "generate", "llm", 2.1, 2.9),
      ]),
    ]),
  ]),
];

const SAME_LEVEL = [
  "chain:input_processing",
  "llm:intent_classification",
  "unknown:quality_check",
  "chain:response_generation",
];

// Coordinate along the layout's rank axis: y for TB, x for LR.
const rankCoordinates = (flow, direction) =>
  Object.fromEntries(
    flow.nodes.map((node) => [
      node.id,
      direction === "TB" ? node.position.y : node.position.x,
    ]),
  );

// Deterministic pseudo-random span trees (no Math.random in tests).
const makeRng = (seed) => {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 2 ** 32;
  };
};

const randomTrace = (seed) => {
  const rng = makeRng(seed);
  const depthByNodeId = {};
  let counter = 0;
  const build = (depth) => {
    const index = counter;
    counter += 1;
    const name = `step_${index}`;
    depthByNodeId[`chain:${name}`] = depth;
    const childCount =
      depth >= 4 || counter > 24 ? 0 : Math.floor(rng() * (depth ? 4 : 5));
    const children = [];
    for (let i = 0; i < childCount; i += 1) children.push(build(depth + 1));
    return traceSpan(`s${index}`, name, "chain", depth, depth + 1, children);
  };
  const roots = [build(0)];
  if (rng() < 0.3) roots.push(build(0));
  return { trace: roots, depthByNodeId };
};

// Span trees whose leaves often reuse one LLM or tool name, as real agents
// do (every call of ChatOpenAI groups into one node). Internal spans keep
// unique names, so the grouped graph stays acyclic.
const SHARED_LEAVES = [
  ["llm", "ChatOpenAI"],
  ["tool", "web_search"],
];

const randomSharedLeafTrace = (seed) => {
  const rng = makeRng(seed);
  let counter = 0;
  const build = (depth) => {
    const index = counter;
    counter += 1;
    const childCount =
      depth >= 4 || counter > 24 ? 0 : Math.floor(rng() * (depth ? 4 : 5));
    if (childCount === 0 && depth > 0 && rng() < 0.6) {
      const [type, name] = SHARED_LEAVES[Math.floor(rng() * 2)];
      return traceSpan(`s${index}`, name, type, depth, depth + 1);
    }
    const children = [];
    for (let i = 0; i < childCount; i += 1) children.push(build(depth + 1));
    return traceSpan(
      `s${index}`,
      `step_${index}`,
      "chain",
      depth,
      depth + 1,
      children,
    );
  };
  return [build(0)];
};

// Level each recorded node should sit on: one past its deepest recorded
// parent (roots at 0). Sentinels are ignored.
const expectedLevels = (graph) => {
  const isSentinel = (id) => id === "__start__" || id === "__end__";
  const ids = graph.nodes
    .map((node) => node.id)
    .filter((id) => !isSentinel(id));
  const edges = graph.edges.filter(
    (edge) =>
      !isSentinel(edge.source) &&
      !isSentinel(edge.target) &&
      edge.source !== edge.target,
  );
  const indegree = Object.fromEntries(ids.map((id) => [id, 0]));
  edges.forEach((edge) => {
    indegree[edge.target] += 1;
  });
  const level = Object.fromEntries(ids.map((id) => [id, 0]));
  const queue = ids.filter((id) => indegree[id] === 0);
  for (let i = 0; i < queue.length; i += 1) {
    const source = queue[i];
    edges
      .filter((edge) => edge.source === source)
      .forEach((edge) => {
        level[edge.target] = Math.max(level[edge.target], level[source] + 1);
        indegree[edge.target] -= 1;
        if (indegree[edge.target] === 0) queue.push(edge.target);
      });
  }
  expect(queue).toHaveLength(ids.length);
  return level;
};

const expectRowsFollowLevels = (flow, levels) => {
  const at = rankCoordinates(flow, "TB");
  const rowByLevel = new Map();
  Object.entries(levels).forEach(([id, level]) => {
    if (!rowByLevel.has(level)) rowByLevel.set(level, at[id]);
    expect({ id, row: at[id] }).toEqual({ id, row: rowByLevel.get(level) });
  });
  const sorted = [...rowByLevel.keys()].sort((a, b) => a - b);
  sorted.slice(1).forEach((level, index) => {
    expect(rowByLevel.get(level)).toBeGreaterThan(
      rowByLevel.get(sorted[index]),
    );
  });
};

describe("buildFlowData keeps same-level nodes on one level (TH-4321)", () => {
  it.each(["TB", "LR"])(
    "draws the screenshot's parallel steps on one level directly after their parent (%s)",
    (direction) => {
      const flow = buildFlowData(buildTraceGraph(screenshotTrace()), direction);
      const at = rankCoordinates(flow, direction);

      const levels = new Set(SAME_LEVEL.map((id) => at[id]));
      expect(levels.size).toBe(1);
      const [siblingLevel] = levels;

      expect(at["agent:Agent"]).toBeLessThan(siblingLevel);
      expect(at["chain:prompt_building"]).toBeGreaterThan(siblingLevel);
      expect(at["llm:generate"]).toBeGreaterThan(at["chain:prompt_building"]);
      // Stop still closes the graph below its deepest node.
      expect(at.__end__).toBeGreaterThan(at["llm:generate"]);
    },
  );

  it("places a leaf sibling on its parent's next level, not on the Stop row", () => {
    const sentinel = (id, type) =>
      canonicalNode(id, type, {
        span_count: 0,
        avg_latency_ms: 0,
        total_tokens: 0,
        total_cost: 0,
      });
    const flow = buildFlowData(
      {
        nodes: [
          sentinel("__start__", "start"),
          canonicalNode("agent:root"),
          canonicalNode("tool:lookup", "tool"),
          canonicalNode("chain:plan", "chain"),
          canonicalNode("llm:draft", "llm"),
          canonicalNode("tool:render", "tool"),
          sentinel("__end__", "end"),
        ],
        edges: [
          canonicalEdge("__start__", "agent:root"),
          canonicalEdge("agent:root", "tool:lookup"),
          canonicalEdge("agent:root", "chain:plan"),
          canonicalEdge("chain:plan", "llm:draft"),
          canonicalEdge("llm:draft", "tool:render"),
          canonicalEdge("tool:lookup", "__end__"),
          canonicalEdge("tool:render", "__end__"),
        ],
      },
      "TB",
    );
    const at = rankCoordinates(flow, "TB");

    expect(at["tool:lookup"]).toBe(at["chain:plan"]);
    expect(at["tool:lookup"]).toBeLessThan(at["llm:draft"]);
    expect(at.__end__).toBeGreaterThan(at["tool:render"]);
  });

  it.each(Array.from({ length: 40 }, (_, seed) => seed + 1))(
    "ranks every node of random span tree #%i by its depth",
    (seed) => {
      const { trace, depthByNodeId } = randomTrace(seed);
      const flow = buildFlowData(buildTraceGraph(trace), "TB");
      const at = rankCoordinates(flow, "TB");

      const levelByDepth = new Map();
      Object.entries(depthByNodeId).forEach(([id, depth]) => {
        if (!levelByDepth.has(depth)) levelByDepth.set(depth, at[id]);
        expect({ id, level: at[id] }).toEqual({
          id,
          level: levelByDepth.get(depth),
        });
      });
      const depths = [...levelByDepth.keys()].sort((a, b) => a - b);
      depths.slice(1).forEach((depth, index) => {
        expect(levelByDepth.get(depth)).toBeGreaterThan(
          levelByDepth.get(depths[index]),
        );
      });
    },
  );

  it("keeps siblings on one level when one shares a grouped LLM node with a deeper branch", () => {
    // Verify-r1 M1: every ChatOpenAI call groups into one node. It has a
    // parent on level 1 (intent_classification) and one on level 2
    // (prompt_building), so it must sit on level 3. intent_classification
    // still belongs on level 1 with its siblings, not next to
    // prompt_building.
    const graph = buildTraceGraph([
      traceSpan("agent", "Agent", "agent", 0, 10, [
        traceSpan("ip", "input_processing", "chain", 1, 2),
        traceSpan("ic", "intent_classification", "chain", 1, 3, [
          traceSpan("llm1", "ChatOpenAI", "llm", 1.5, 2.5),
        ]),
        traceSpan("rg", "response_generation", "chain", 1, 9, [
          traceSpan("pb", "prompt_building", "chain", 2, 8, [
            traceSpan("llm2", "ChatOpenAI", "llm", 3, 7),
          ]),
        ]),
      ]),
    ]);
    const flow = buildFlowData(graph, "TB");
    const at = rankCoordinates(flow, "TB");

    expect(at["chain:intent_classification"]).toBe(
      at["chain:input_processing"],
    );
    expect(at["chain:response_generation"]).toBe(at["chain:input_processing"]);
    expect(at["chain:prompt_building"]).toBeGreaterThan(
      at["chain:intent_classification"],
    );
    expect(at["llm:ChatOpenAI"]).toBeGreaterThan(at["chain:prompt_building"]);
    expectRowsFollowLevels(flow, expectedLevels(graph));
  });

  it.each(Array.from({ length: 40 }, (_, seed) => seed + 101))(
    "puts every node of shared-leaf span tree #%i one level past its deepest parent",
    (seed) => {
      const graph = buildTraceGraph(randomSharedLeafTrace(seed));
      expectRowsFollowLevels(buildFlowData(graph, "TB"), expectedLevels(graph));
    },
  );

  it.each(["TB", "LR"])(
    "lays out recorded nodes exactly as if Stop were absent (%s)",
    (direction) => {
      const graph = buildTraceGraph(randomTrace(7).trace);
      const withoutStop = {
        ...graph,
        nodes: graph.nodes.filter((node) => node.type !== "end"),
        edges: graph.edges.filter((edge) => edge.target !== "__end__"),
      };
      const positions = (flow) =>
        Object.fromEntries(
          flow.nodes
            .filter((node) => node.id !== "__end__")
            .map((node) => [node.id, node.position]),
        );

      const flow = buildFlowData(graph, direction);
      expect(positions(flow)).toEqual(
        positions(buildFlowData(withoutStop, direction)),
      );

      const at = rankCoordinates(flow, direction);
      const deepest = Math.max(
        ...flow.nodes
          .filter((node) => node.id !== "__end__")
          .map((node) => at[node.id]),
      );
      expect(at.__end__).toBeGreaterThan(deepest);
      flow.nodes.forEach((node) => {
        expect(Number.isFinite(node.position.x)).toBe(true);
        expect(Number.isFinite(node.position.y)).toBe(true);
      });
    },
  );
});

describe("buildFlowData places Stop on dagre's rank axis", () => {
  // dagre reads rankdir case-insensitively and reverses the rank axis for
  // BT/RL. Stop must follow the same reading or it lands beside the graph.
  const extent = (flow, axis) => {
    const values = flow.nodes
      .filter((node) => node.id !== "__end__")
      .map((node) => node.position[axis]);
    return { min: Math.min(...values), max: Math.max(...values) };
  };

  it.each([
    ["TB", "y", "after"],
    ["tb", "y", "after"],
    ["BT", "y", "before"],
    ["LR", "x", "after"],
    ["lr", "x", "after"],
    ["RL", "x", "before"],
  ])("%s puts Stop past the last level along %s", (direction, axis, side) => {
    const flow = buildFlowData(buildTraceGraph(screenshotTrace()), direction);
    const stop = flow.nodes.find((node) => node.id === "__end__").position;
    const { min, max } = extent(flow, axis);

    if (side === "after") expect(stop[axis]).toBeGreaterThan(max);
    else expect(stop[axis]).toBeLessThan(min);

    const cross = axis === "y" ? "x" : "y";
    const span = extent(flow, cross);
    expect(stop[cross]).toBeGreaterThanOrEqual(span.min);
    expect(stop[cross]).toBeLessThanOrEqual(span.max);
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
