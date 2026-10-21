import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

vi.mock("@xyflow/react", () => ({
  // eslint-disable-next-line react/prop-types
  ReactFlow: ({ nodes, onNodeClick }) => (
    <div>
      {/* eslint-disable-next-line react/prop-types */}
      {nodes.map((node) => (
        <button
          key={node.id}
          type="button"
          data-testid={`node-${node.id}`}
          data-selectable={node.selectable}
          onClick={(event) => onNodeClick(event, node)}
        >
          {node.data?.label || node.id}
        </button>
      ))}
    </div>
  ),
  Background: () => null,
  BackgroundVariant: { Dots: "dots" },
  Controls: () => null,
  ReactFlowProvider: ({ children }) => children,
}));

import AgentGraph from "../AgentGraph";

const executionData = {
  nodes: [
    {
      id: "skipped",
      name: "Skipped Node",
      type: "atomic",
      node_execution: { status: "skipped" },
    },
    {
      id: "success",
      name: "Success Node",
      type: "atomic",
      node_execution: { status: "success" },
    },
  ],
  node_connections: [],
};

describe("AgentGraph", () => {
  it("does not select skipped nodes but keeps other execution nodes interactive", () => {
    const onNodeClick = vi.fn();
    render(
      <AgentGraph
        executionData={executionData}
        onNodeClick={onNodeClick}
        title={null}
      />,
    );

    const skippedNode = screen.getByTestId("node-skipped");
    const successNode = screen.getByTestId("node-success");

    expect(skippedNode).toHaveAttribute("data-selectable", "false");
    expect(successNode).toHaveAttribute("data-selectable", "true");

    fireEvent.click(skippedNode);
    expect(onNodeClick).not.toHaveBeenCalled();

    fireEvent.click(successNode);
    expect(onNodeClick).toHaveBeenCalledTimes(1);
  });
});
