import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import React from "react";
import RunAgentPanel from "../RunAgentPanel";

vi.mock("src/components/AgentGraph", () => ({
  AgentGraph: ({ executionData, onNodeClick, selectedNodeId }) => (
    <div data-testid="agent-graph-mock">
      <span data-testid="selected-node-id">{selectedNodeId}</span>
      {executionData?.nodes?.map((node) => (
        <button
          key={node.id}
          data-testid={`graph-node-${node.id}`}
          onClick={(e) => onNodeClick?.(e, node)}
        >
          {`Canvas Node: ${node.name}`}
        </button>
      ))}
    </div>
  ),
}));

vi.mock("../NodeOutputDetail", () => ({
  default: ({ executionId, nodeExecutionId }) => (
    <div data-testid="node-output-detail-mock">
      <span data-testid="detail-exec-id">{executionId}</span>
      <span data-testid="detail-node-exec-id">{nodeExecutionId}</span>
    </div>
  ),
}));

vi.mock("src/components/svg-color", () => ({
  default: ({ src, ...props }) => (
    <span data-testid="svg-color" data-src={src} {...props} />
  ),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

describe("RunAgentPanel - Steps List and Bidirectional Selection", () => {
  const mockExecutionData = {
    id: "exec-101",
    nodes: [
      {
        id: "node-prompt-1",
        name: "Prompt Analyzer",
        type: "atomic",
        node_execution: {
          id: "ne-prompt-1",
          status: "success",
          duration_seconds: 2.5,
        },
      },
      {
        id: "node-agent-2",
        name: "Agent Executor",
        type: "subgraph",
        node_execution: {
          id: "ne-agent-2",
          status: "success",
          duration_seconds: 6.0,
        },
      },
    ],
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("mounts NodeOutputListView alongside AgentGraph and NodeOutputDetail", () => {
    render(
      <RunAgentPanel
        panelHeight={350}
        onResize={vi.fn()}
        executionId="exec-101"
        executionData={mockExecutionData}
      />,
    );

    // List view is present with nodes
    expect(screen.getByText("Prompt Analyzer")).toBeInTheDocument();
    expect(screen.getByText("Agent Executor")).toBeInTheDocument();

    // Canvas mock is present
    expect(screen.getByTestId("agent-graph-mock")).toBeInTheDocument();

    // Detail inspector is present
    expect(screen.getByTestId("node-output-detail-mock")).toBeInTheDocument();
  });

  it("selects the corresponding node on the canvas when clicked in the step list", () => {
    render(
      <RunAgentPanel
        panelHeight={350}
        onResize={vi.fn()}
        executionId="exec-101"
        executionData={mockExecutionData}
      />,
    );

    // Click 'Prompt Analyzer' in the step list tree
    const promptListItem = screen.getByText("Prompt Analyzer");
    fireEvent.click(promptListItem);

    // AgentGraph receives selectedNodeId
    expect(screen.getByTestId("selected-node-id").textContent).toBe("node-prompt-1");
  });

  it("updates selection when a node is clicked on the canvas", () => {
    render(
      <RunAgentPanel
        panelHeight={350}
        onResize={vi.fn()}
        executionId="exec-101"
        executionData={mockExecutionData}
      />,
    );

    // First click prompt list item to change selection to prompt-1
    fireEvent.click(screen.getByText("Prompt Analyzer"));
    expect(screen.getByTestId("selected-node-id").textContent).toBe("node-prompt-1");

    // Click canvas node button for agent-2
    const canvasNode = screen.getByTestId("graph-node-node-agent-2");
    fireEvent.click(canvasNode);

    // Selected node updates
    expect(screen.getByTestId("selected-node-id").textContent).toBe("node-agent-2");
  });

  it("allows searching and narrowing the step list inside RunAgentPanel", () => {
    const { container } = render(
      <RunAgentPanel
        panelHeight={350}
        onResize={vi.fn()}
        executionId="exec-101"
        executionData={mockExecutionData}
      />,
    );

    const searchInput = screen.getByPlaceholderText("Search");
    fireEvent.change(searchInput, { target: { value: "Prompt" } });

    // Step list narrowing
    const stepList = container.querySelector(".tree-view");
    expect(within(stepList).getByText("Prompt Analyzer")).toBeInTheDocument();
    expect(within(stepList).queryByText("Agent Executor")).not.toBeInTheDocument();
  });
});
