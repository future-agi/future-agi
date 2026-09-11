import { describe, expect, it } from "vitest";
import {
  buildFlowBandPath,
  computeFlowBands,
  computeNaturalSize,
  computeSankeyLayout,
} from "../agentPathUtils";

const nodes = [
  { id: "agent:root", name: "root", type: "agent", span_count: 3 },
  { id: "tool:lookup", name: "lookup", type: "tool", span_count: 2 },
  { id: "llm:answer", name: "answer", type: "llm", span_count: 2 },
];

const pathEdge = (source, target, transitionCount = 1, patch = {}) => ({
  source,
  target,
  transition_count: transitionCount,
  ...patch,
});

describe("computeSankeyLayout Agent Path contract", () => {
  const rankOf = (layout, nodeId) =>
    layout.columns.find((column) =>
      column.nodes.some((node) => node.id === nodeId),
    )?.rank;

  it("uses the dedicated exact path topology projection", () => {
    const layout = computeSankeyLayout({
      nodes,
      edges: [
        { source: "agent:root", target: "tool:lookup", transition_count: 99 },
      ],
      path_edges: [
        { source: "tool:lookup", target: "llm:answer", transition_count: 7 },
      ],
    });

    expect(layout.flows).toEqual([
      expect.objectContaining({
        source: "tool:lookup",
        target: "llm:answer",
        count: 7,
      }),
    ]);
  });

  it("renders exact hierarchy when the optional path projection is empty", () => {
    const layout = computeSankeyLayout({
      nodes,
      edges: [
        { source: "agent:root", target: "tool:lookup", transition_count: 99 },
      ],
      path_edges: [],
    });

    expect(layout.flows).toEqual([
      expect.objectContaining({
        source: "agent:root",
        target: "tool:lookup",
        count: 99,
      }),
    ]);
    expect(layout.columns.flatMap((column) => column.nodes)).toHaveLength(3);
  });

  it("rejects a payload with neither path nor hierarchy edges", () => {
    expect(() =>
      computeSankeyLayout({
        nodes,
        pathEdges: [
          { source: "agent:root", target: "llm:answer", transitionCount: 2 },
        ],
      }),
    ).toThrow("missing exact topology edges");
  });

  it("rejects a path edge that references an unknown node", () => {
    expect(() =>
      computeSankeyLayout({
        nodes,
        path_edges: [pathEdge("agent:root", "tool:missing")],
      }),
    ).toThrow("references an unknown node");
  });

  it("uses longest DAG rank when paths converge", () => {
    const layout = computeSankeyLayout({
      nodes,
      path_edges: [
        pathEdge("agent:root", "llm:answer"),
        pathEdge("agent:root", "tool:lookup"),
        pathEdge("tool:lookup", "llm:answer"),
      ],
    });

    expect(rankOf(layout, "agent:root")).toBe(0);
    expect(rankOf(layout, "tool:lookup")).toBe(1);
    expect(rankOf(layout, "llm:answer")).toBe(2);
  });

  it("keeps a cycle in one component and ranks its downstream node later", () => {
    const layout = computeSankeyLayout({
      nodes,
      path_edges: [
        pathEdge("agent:root", "tool:lookup"),
        pathEdge("tool:lookup", "agent:root"),
        pathEdge("tool:lookup", "llm:answer"),
      ],
    });

    expect(rankOf(layout, "agent:root")).toBe(rankOf(layout, "tool:lookup"));
    expect(rankOf(layout, "llm:answer")).toBe(
      rankOf(layout, "tool:lookup") + 1,
    );
    expect(layout.cycleGutter).toBeGreaterThan(0);
    expect(layout.flows.filter((flow) => flow.isCycle)).toHaveLength(2);
    expect(computeNaturalSize(layout).width).toBeGreaterThan(
      layout.columns.length * 172,
    );
  });

  it("keeps an exact self-loop visible instead of dropping the cycle", () => {
    const layout = computeSankeyLayout({
      nodes,
      path_edges: [
        {
          source: "agent:root",
          target: "agent:root",
          transition_count: 4,
          is_self_loop: true,
        },
        pathEdge("agent:root", "llm:answer"),
      ],
    });

    expect(layout.flows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          source: "agent:root",
          target: "agent:root",
          count: 4,
          isCycle: true,
        }),
      ]),
    );
    expect(layout.cycleGutter).toBeGreaterThan(0);
  });

  it("lays out a deep path iteratively without overflowing the call stack", () => {
    const depth = 2000;
    const deepNodes = Array.from({ length: depth }, (_, index) => ({
      id: `agent:${index}`,
      name: String(index),
      type: "agent",
      span_count: 1,
    }));
    const deepEdges = Array.from({ length: depth - 1 }, (_, index) => ({
      source: `agent:${index}`,
      target: `agent:${index + 1}`,
      transition_count: 1,
    }));

    const layout = computeSankeyLayout({
      nodes: deepNodes,
      path_edges: deepEdges,
    });

    expect(rankOf(layout, `agent:${depth - 1}`)).toBe(depth - 1);
  });
});

describe("computeFlowBands", () => {
  it("allocates source and target offsets in deterministic linear passes", () => {
    const positions = new Map([
      ["a", { h: 40 }],
      ["b", { h: 20 }],
      ["c", { h: 20 }],
    ]);
    const bands = computeFlowBands(
      [
        { source: "a", target: "b", count: 1 },
        { source: "a", target: "c", count: 3 },
        { source: "b", target: "c", count: 1 },
      ],
      positions,
    );

    expect(bands[0]).toEqual(
      expect.objectContaining({
        srcBandH: 10,
        srcYOffset: 0,
        tgtBandH: 20,
        tgtYOffset: 0,
      }),
    );
    expect(bands[1]).toEqual(
      expect.objectContaining({
        srcBandH: 30,
        srcYOffset: 10,
        tgtBandH: 15,
        tgtYOffset: 0,
      }),
    );
    expect(bands[2]).toEqual(
      expect.objectContaining({
        srcBandH: 20,
        srcYOffset: 0,
        tgtBandH: 5,
        tgtYOffset: 15,
      }),
    );
  });

  it("routes same-column SCC bands through the reserved left gutter", () => {
    const d = buildFlowBandPath({
      flow: { isCycle: true, cycleLane: 2 },
      index: 0,
      src: { x: 100, y: 20 },
      tgt: { x: 100, y: 90 },
      srcBandH: 8,
      tgtBandH: 6,
      srcYOffset: 3,
      tgtYOffset: 4,
    });

    expect(d).toContain("M 100 23");
    expect(d).toContain("C 62 23, 62 94, 100 94");
    expect(d).toContain("L 100 100");
    expect(d).not.toMatch(/NaN|Infinity/);
  });

  it("keeps a wide fork and join inside each node's available height", () => {
    const positions = new Map([
      ["root", { h: 60 }],
      ["join", { h: 60 }],
      ...Array.from({ length: 40 }, (_, index) => [
        `child-${index}`,
        { h: 24 },
      ]),
    ]);
    const flows = Array.from({ length: 40 }, (_, index) => ({
      source: "root",
      target: `child-${index}`,
      count: index === 39 ? 10_000 : 1,
    })).concat(
      Array.from({ length: 40 }, (_, index) => ({
        source: `child-${index}`,
        target: "join",
        count: 1,
      })),
    );

    const bands = computeFlowBands(flows, positions);
    const rootBands = bands.filter((band) => band.flow.source === "root");
    const joinBands = bands.filter((band) => band.flow.target === "join");

    expect(
      rootBands.reduce((total, band) => total + band.srcBandH, 0),
    ).toBeCloseTo(60, 8);
    expect(
      joinBands.reduce((total, band) => total + band.tgtBandH, 0),
    ).toBeCloseTo(60, 8);
    expect(
      Math.max(...rootBands.map((band) => band.srcYOffset + band.srcBandH)),
    ).toBeCloseTo(60, 8);
    expect(
      Math.max(...joinBands.map((band) => band.tgtYOffset + band.tgtBandH)),
    ).toBeCloseTo(60, 8);
  });
});
