import { describe, expect, it } from "vitest";

import { AGENT_GRAPH_NODE_SIZE, layoutGraph } from "../agentGraphLayout";

const makeNode = (id, type = "agent") => ({
  id,
  data: { type },
  position: { x: 0, y: 0 },
});

const findNode = (nodes, id) => nodes.find((node) => node.id === id);

describe("layoutGraph", () => {
  it("centers differently sized sentinel nodes on a vertical graph axis", () => {
    const nodes = [
      makeNode("start", "start"),
      makeNode("agent"),
      makeNode("end", "end"),
    ];
    const edges = [
      { source: "start", target: "agent" },
      { source: "agent", target: "end" },
    ];

    const layout = layoutGraph(nodes, edges, "TB");
    const start = findNode(layout, "start");
    const agent = findNode(layout, "agent");
    const end = findNode(layout, "end");
    const startCenter =
      start.position.x + AGENT_GRAPH_NODE_SIZE.sentinel.width / 2;
    const agentCenter =
      agent.position.x + AGENT_GRAPH_NODE_SIZE.default.width / 2;
    const endCenter = end.position.x + AGENT_GRAPH_NODE_SIZE.sentinel.width / 2;

    expect(startCenter).toBe(agentCenter);
    expect(endCenter).toBe(agentCenter);
  });

  it("leaves at least 60 pixels between sibling node rectangles", () => {
    const nodes = [
      makeNode("start", "start"),
      makeNode("left"),
      makeNode("right"),
      makeNode("end", "end"),
    ];
    const edges = [
      { source: "start", target: "left" },
      { source: "start", target: "right" },
      { source: "left", target: "end" },
      { source: "right", target: "end" },
    ];

    const layout = layoutGraph(nodes, edges, "TB");
    const left = findNode(layout, "left");
    const right = findNode(layout, "right");
    const horizontalGap =
      Math.abs(right.position.x - left.position.x) -
      AGENT_GRAPH_NODE_SIZE.default.width;

    expect(horizontalGap).toBeGreaterThanOrEqual(60);
  });

  it("returns finite positions for a minimal graph", () => {
    const layout = layoutGraph(
      [makeNode("start", "start"), makeNode("end", "end")],
      [{ source: "start", target: "end" }],
      "TB",
    );

    for (const node of layout) {
      expect(Number.isFinite(node.position.x)).toBe(true);
      expect(Number.isFinite(node.position.y)).toBe(true);
    }
  });
});
