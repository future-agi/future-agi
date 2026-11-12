// The Agent Graph's first view where the caller sets no zoom (the trace
// drawer). React Flow fits the whole graph into the pane and stops at its
// 0.3 floor. A long trace fits at 0.16 to 0.22, so it stopped at 0.3: 12 px
// node labels drew about 3.6 px tall, and the graph, centred, was cut at
// both edges of the pane at every width. Only a top-to-bottom graph whose
// plain fit stops at that floor now opens at a reading zoom with Start at
// the top of the pane, and pans. A graph that fits at or above the floor
// keeps exactly today's view, and callers with their own zoom,
// left-to-right graphs and "fit all" are unchanged.
//
// React Flow's measurements are mocked: each node's measured size is the
// box the layout reserves for it, and the pane size is the drawer's Agent
// Graph pane at the widths the app opens it in. Nothing reads jsdom layout.
import React from "react";
import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getViewportForBounds } from "@xyflow/react";
import { buildTraceGraph } from "src/components/traceDetail/buildTraceGraph";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";
import AgentGraph, { buildFlowData } from "../AgentGraph";

// The zoom controls' glyphs (the only icons this render draws).
for (const name of [
  "solar:add-linear",
  "solar:minus-linear",
  "solar:target-linear",
  "solar:full-screen-linear",
]) {
  registerOfflineIcon(name, bundled[name]);
}

const flow = vi.hoisted(() => {
  const state = {
    nodes: [],
    domNode: { clientWidth: 0, clientHeight: 0 },
    setViewport: () => {},
    props: null,
  };
  const boundsOf = (nodes) => {
    const x = Math.min(...nodes.map((n) => n.position.x));
    const y = Math.min(...nodes.map((n) => n.position.y));
    const x2 = Math.max(...nodes.map((n) => n.position.x + n.measured.width));
    const y2 = Math.max(...nodes.map((n) => n.position.y + n.measured.height));
    return { x, y, width: x2 - x, height: y2 - y };
  };
  const api = {
    getNodes: () => state.nodes,
    getNodesBounds: (nodes) => boundsOf(nodes),
    setViewport: (viewport) => state.setViewport(viewport),
    zoomIn: () => {},
    zoomOut: () => {},
    fitView: () => {},
  };
  return { state, boundsOf, api };
});

vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    // The canvas itself is not under test: record its props, keep children.
    // eslint-disable-next-line react/prop-types
    ReactFlow: ({ children, ...props }) => {
      flow.state.props = props;
      return <div data-testid="agent-graph-canvas">{children}</div>;
    },
    useNodesInitialized: () => true,
    useStore: (selector) =>
      selector({ nodes: flow.state.nodes, domNode: flow.state.domNode }),
    useReactFlow: () => flow.api,
  };
});

// 12 px labels × 0.92 ≈ 11 px, the reading zoom the graphs already use.
const READING_ZOOM = 0.92;
const LABEL_PX = 12;
// React Flow's zoom floor (the ReactFlow minZoom prop).
const FLOOR_ZOOM = 0.3;

// The drawer's Agent Graph pane at the widths the app opens it in.
const PANES = [
  ["1024", { width: 244, height: 372 }],
  ["1280", { width: 306, height: 372 }],
  ["1440", { width: 344, height: 372 }],
];

// Sizes the layout reserves: 140×44 for a step, 50×32 for Start and Stop.
const withMeasured = (nodes) =>
  nodes.map((node) => {
    const sentinel = node.data.type === "start" || node.data.type === "end";
    return {
      ...node,
      measured: sentinel
        ? { width: 50, height: 32 }
        : { width: 140, height: 44 },
    };
  });

const traceSpan = (id, name, type, startS, endS, children = []) => ({
  observation_span: {
    id,
    name,
    observation_type: type,
    start_time: new Date(
      Date.UTC(2026, 10, 11, 3) + startS * 1000,
    ).toISOString(),
    end_time: new Date(Date.UTC(2026, 10, 11, 3) + endS * 1000).toISOString(),
    latency_ms: (endS - startS) * 1000,
    span_attributes: {},
  },
  children,
});

// A long trace (27 spans): an agent with ChatOpenAI,
// lookup_order and 24 steps, every fourth step under the agent and the
// three after it each nested under the one before.
const longTrace = () => {
  const kinds = ["retriever", "tool", "llm", "chain", "embedding"];
  const step = (i, children = []) => {
    const kind = kinds[i % kinds.length];
    const tail =
      i % 3 === 0
        ? "fetch_customer_order_history_and_shipping_preferences_from_warehouse"
        : "call";
    const start = 0.3 + i * 0.15;
    return traceSpan(
      `s${i}`,
      `step_${i + 1}_${kind}_${tail}`,
      kind,
      start,
      start + 0.4,
      children,
    );
  };
  const chain = (i, left) => step(i, left > 1 ? [chain(i + 1, left - 1)] : []);
  return [
    traceSpan("root", "long-research-agent-run", "agent", 0, 9, [
      traceSpan("chat", "ChatOpenAI", "llm", 0, 9),
      traceSpan("lookup", "lookup_order", "tool", 0.2, 0.5),
      ...[0, 4, 8, 12, 16, 20].map((i) => chain(i, 4)),
    ]),
  ];
};

// A short order-agent trace (3 spans): an agent with ChatOpenAI
// and lookup_order side by side under it, about 305 px of layout wide.
const orderTrace = () => [
  traceSpan("root", "order-agent", "agent", 0, 4, [
    traceSpan("chat", "ChatOpenAI", "llm", 0, 4),
    traceSpan("lookup", "lookup_order", "tool", 0.2, 0.5),
  ]),
];

const metrics = {
  span_count: 1,
  avg_latency_ms: 10,
  total_tokens: 2,
  total_cost: 0.01,
  error_count: 0,
  trace_count: 1,
};
// One step and its tool, one 140 px column wide.
const shortGraph = () => ({
  nodes: [
    { id: "agent:support", name: "support", type: "agent", ...metrics },
    { id: "tool:lookup_order", name: "lookup_order", type: "tool", ...metrics },
  ],
  edges: [
    {
      source: "agent:support",
      target: "tool:lookup_order",
      transition_count: 1,
      avg_latency_ms: 10,
      total_tokens: 2,
      total_cost: 0.01,
      error_count: 0,
      trace_count: 1,
      is_self_loop: false,
    },
  ],
});

// Mounts the graph the way the drawer does, over a mocked pane.
const renderGraph = (data, pane, props = {}) => {
  const direction = props.direction ?? "TB";
  flow.state.nodes = withMeasured(buildFlowData(data, direction).nodes);
  flow.state.domNode = {
    clientWidth: pane.width,
    clientHeight: pane.height,
  };
  render(
    <AgentGraph
      data={data}
      isLoading={false}
      onNodeClick={() => {}}
      direction={direction}
      {...props}
    />,
  );
  return flow.boundsOf(flow.state.nodes);
};

// The zoom React Flow's plain first fit lands on in that pane, before its
// floor (same padding and cap as the graph's fitViewOptions).
const reactFlowFit = (bounds, pane) =>
  getViewportForBounds(bounds, pane.width, pane.height, 0, 1.3, 0.2).zoom;

const startNode = () => flow.state.nodes.find((n) => n.data.type === "start");

let setViewport;
beforeEach(() => {
  setViewport = vi.fn();
  flow.state.setViewport = setViewport;
  flow.state.props = null;
});

describe("Agent Graph with no caller zoom (the trace drawer)", () => {
  it.each(PANES)(
    "opens a long trace at 0.92 with Start at the top of the %s px pane",
    (_, pane) => {
      const bounds = renderGraph(buildTraceGraph(longTrace()), pane);

      // Fitted whole, the graph lands below React Flow's floor.
      expect(reactFlowFit(bounds, pane)).toBeLessThan(FLOOR_ZOOM);

      expect(setViewport).toHaveBeenCalled();
      const viewport = setViewport.mock.calls.at(-1)[0];
      expect(viewport.zoom).toBe(READING_ZOOM);
      expect(LABEL_PX * viewport.zoom).toBeGreaterThanOrEqual(11);

      // The top of the graph (Start) sits 16 px below the pane's top edge.
      expect(viewport.y).toBeCloseTo(16 - bounds.y * READING_ZOOM, 6);

      // Start is centred across the pane, whole, not cut at an edge.
      const start = startNode();
      const startCentre = start.position.x + start.measured.width / 2;
      expect(viewport.x).toBeCloseTo(
        pane.width / 2 - startCentre * READING_ZOOM,
        6,
      );
      const left = viewport.x + start.position.x * viewport.zoom;
      const right = left + start.measured.width * viewport.zoom;
      expect(left).toBeGreaterThanOrEqual(0);
      expect(right).toBeLessThanOrEqual(pane.width);
      expect(viewport.y + start.position.y * viewport.zoom).toBeCloseTo(16, 6);

      // React Flow's own first fit is today's; the rest of the graph pans
      // by drag, the wheel stays with the page, and Fit to view still fits
      // the whole graph down to the floor.
      expect(flow.state.props.fitViewOptions).not.toHaveProperty("minZoom");
      expect(flow.state.props.panOnScroll).toBe(false);
      expect(flow.state.props.minZoom).toBe(FLOOR_ZOOM);
    },
  );

  // React Flow fits this trace at about 0.67 (1024), 0.84 (1280) and 0.94
  // (1440): whole, so the view stays as fitted, below the reading zoom too.
  it.each(PANES)(
    "keeps today's fitted view for the 3-span trace in the %s px pane",
    (_, pane) => {
      const bounds = renderGraph(buildTraceGraph(orderTrace()), pane);

      expect(reactFlowFit(bounds, pane)).toBeGreaterThanOrEqual(FLOOR_ZOOM);
      expect(setViewport).not.toHaveBeenCalled();
      expect(flow.state.props.fitViewOptions).not.toHaveProperty("minZoom");
      expect(flow.state.props.panOnScroll).toBe(false);
      expect(flow.state.props.minZoom).toBe(FLOOR_ZOOM);
    },
  );
});

describe("Agent Graph callers outside the default", () => {
  const pane = { width: 344, height: 372 };

  it("keeps a caller's own zoom on a long trace, with scroll to pan", () => {
    const bounds = renderGraph(buildTraceGraph(longTrace()), pane, {
      readableMinZoom: READING_ZOOM,
    });

    expect(flow.state.props.fitViewOptions.minZoom).toBe(READING_ZOOM);
    expect(flow.state.props.panOnScroll).toBe(true);
    const viewport = setViewport.mock.calls.at(-1)[0];
    expect(viewport.zoom).toBe(READING_ZOOM);
    expect(viewport.y).toBeCloseTo(16 - bounds.y * READING_ZOOM, 6);
  });

  // React Flow fits this graph at 1.0 in a 168 px pane: (168 - 28) / 140.
  // The base estimated it as 168 / (140 × 1.4) ≈ 0.86, below the caller's
  // 0.92, and moved the fitted graph to 0.92.
  it("keeps React Flow's fit when a graph fits at the caller's zoom", () => {
    const narrow = { width: 168, height: 372 };
    const bounds = renderGraph(shortGraph(), narrow, {
      readableMinZoom: READING_ZOOM,
    });

    expect(reactFlowFit(bounds, narrow)).toBeGreaterThanOrEqual(READING_ZOOM);
    expect(setViewport).not.toHaveBeenCalled();
    expect(flow.state.props.fitViewOptions.minZoom).toBe(READING_ZOOM);
    expect(flow.state.props.panOnScroll).toBe(true);
  });

  it("leaves a left-to-right graph on today's fit", () => {
    renderGraph(buildTraceGraph(longTrace()), pane, { direction: "LR" });

    expect(setViewport).not.toHaveBeenCalled();
    expect(flow.state.props.fitViewOptions).not.toHaveProperty("minZoom");
    expect(flow.state.props.panOnScroll).toBe(false);
  });

  it("shows the whole graph when the caller asks to fit all", () => {
    renderGraph(buildTraceGraph(longTrace()), pane, { fitAll: true });

    expect(setViewport).not.toHaveBeenCalled();
    expect(flow.state.props.fitViewOptions).not.toHaveProperty("minZoom");
    expect(flow.state.props.panOnScroll).toBe(false);
  });
});
