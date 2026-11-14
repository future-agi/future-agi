import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "src/utils/test-utils";
import AgentPath from "../AgentPath";

const widePathNodes = Array.from({ length: 6 }, (_, index) => ({
  id: `agent:${index}`,
  name: `Agent ${index}`,
  type: "agent",
  span_count: 1,
}));
const widePath = {
  nodes: widePathNodes,
  edges: widePathNodes.slice(1).map((node, index) => ({
    source: widePathNodes[index].id,
    target: node.id,
    transition_count: 1,
  })),
};

const forkChildren = Array.from({ length: 8 }, (_, index) => ({
  id: `tool:${index}`,
  name: `Tool ${index}`,
  type: "tool",
  span_count: 1,
}));
const tallFork = {
  nodes: [
    { id: "agent:root", name: "Root", type: "agent", span_count: 1 },
    ...forkChildren,
  ],
  edges: forkChildren.map((node) => ({
    source: "agent:root",
    target: node.id,
    transition_count: 1,
  })),
};

const viewport = { width: 600, height: 500 };
const cases = [
  {
    name: "inline width-limited path",
    data: widePath,
    fullscreen: false,
    viewBox: "0 0 1072 84",
    fittedWidth: "584",
    fittedHeight: "46",
  },
  {
    name: "inline height-limited fork",
    data: tallFork,
    fullscreen: false,
    viewBox: "0 0 384 644",
    fittedWidth: "145",
    fittedHeight: "244",
  },
  {
    name: "fullscreen width-limited path",
    data: widePath,
    fullscreen: true,
    viewBox: "0 0 1072 84",
    fittedWidth: "584",
    fittedHeight: "46",
  },
  {
    name: "fullscreen height-limited fork",
    data: tallFork,
    fullscreen: true,
    viewBox: "0 0 384 644",
    fittedWidth: "289",
    fittedHeight: "484",
  },
];

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback) {
        this.callback = callback;
      }

      observe(target) {
        this.callback([{ target, contentRect: viewport }]);
      }

      disconnect = vi.fn();
    },
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AgentPath viewport unit regression", () => {
  it.each(cases)(
    "fits the whole $name after manual zoom",
    async ({ data, fullscreen, viewBox, fittedWidth, fittedHeight }) => {
      const { container } = render(<AgentPath data={data} />);
      const inlineChart = container
        .querySelector(".agent-path-node")
        .closest("svg");
      // The inline toolbar renders only while the graph is hovered. A pointer
      // move in jsdom reads as leaving the graph and hides it before a click
      // lands, so hover with mouseover and click without moving the pointer.
      fireEvent.mouseOver(inlineChart);

      let graphRoot = container;
      if (fullscreen) {
        fireEvent.click(screen.getByTitle("Fullscreen"));
        graphRoot = await screen.findByRole("dialog");
      }

      const chart = graphRoot.querySelector(".agent-path-node").closest("svg");
      const controls = within(graphRoot);
      fireEvent.click(controls.getByTitle("Zoom in"));
      expect(Number(chart.getAttribute("width"))).toBeGreaterThan(
        Number(fittedWidth),
      );

      fireEvent.click(controls.getByTitle("Fit to view"));

      expect(chart).toHaveAttribute("viewBox", viewBox);
      expect(chart).toHaveAttribute("width", fittedWidth);
      expect(chart).toHaveAttribute("height", fittedHeight);
      expect(chart.querySelectorAll(".agent-path-node")).toHaveLength(
        data.nodes.length,
      );
      for (const node of data.nodes) {
        expect(within(chart).getByText(node.name)).toBeInTheDocument();
      }
    },
  );
});
