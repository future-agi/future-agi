import React from "react";
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";

import AgentPath from "../AgentPath";
import AgentGraph from "../AgentGraph";

const reactFlowControls = {
  zoomIn: vi.fn(),
  zoomOut: vi.fn(),
  fitView: vi.fn(),
};

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="iconify" data-icon={icon} />,
}));

vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));

vi.mock("@xyflow/react", () => ({
  // eslint-disable-next-line react/prop-types
  ReactFlowProvider: ({ children }) => <div>{children}</div>,
  // eslint-disable-next-line react/prop-types
  ReactFlow: ({ children, onNodeClick }) => (
    <div
      data-testid="react-flow"
      onClick={() =>
        onNodeClick?.(null, { data: { type: "agent", name: "Agent" } })
      }
    >
      {children}
    </div>
  ),
  useNodesState: (nodes) => [nodes, vi.fn(), vi.fn()],
  useEdgesState: (edges) => [edges, vi.fn(), vi.fn()],
  useReactFlow: () => reactFlowControls,
  Handle: () => null,
  Position: {
    Left: "left",
    Right: "right",
    Top: "top",
    Bottom: "bottom",
  },
  MarkerType: {
    ArrowClosed: "arrowclosed",
  },
}));

const graphData = {
  nodes: [
    { id: "start", type: "start", name: "Start", span_count: 1 },
    { id: "agent", type: "agent", name: "Agent", span_count: 2 },
    { id: "tool", type: "tool", name: "Tool", span_count: 1 },
    { id: "end", type: "end", name: "End", span_count: 1 },
  ],
  edges: [
    { source: "agent", target: "tool", transitionCount: 1 },
    { source: "agent", target: "tool", transition_count: 1 },
  ],
};

describe("tracing graph fullscreen controls", () => {
  it("toggles AgentPath fullscreen and exits on Escape", () => {
    render(<AgentPath data={graphData} isLoading={false} />);

    fireEvent.click(screen.getByTitle("Fullscreen"));

    expect(screen.getByTitle("Exit fullscreen")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.getByTitle("Fullscreen")).toBeInTheDocument();
  });

  it("toggles AgentGraph fullscreen without replacing the existing fit control", () => {
    render(<AgentGraph data={graphData} isLoading={false} isError={false} />);

    fireEvent.mouseEnter(screen.getByTestId("react-flow").parentElement);
    fireEvent.click(screen.getByTitle("Fit"));

    expect(reactFlowControls.fitView).toHaveBeenCalledWith({
      duration: 300,
      padding: 0.3,
    });

    fireEvent.click(screen.getByTitle("Fullscreen"));

    expect(screen.getByTitle("Exit fullscreen")).toBeInTheDocument();
  });
});
