import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";
import NodeOutputListView from "../NodeOutputListView";

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

describe("NodeOutputListView", () => {
  const mockNodes = [
    {
      id: "node-1",
      name: "Search Node",
      type: "llm_prompt",
      duration: 1200,
      cost: 0.001,
      tokens: 150,
    },
    {
      id: "node-2",
      name: "Classifier Node",
      type: "agent",
      duration: 3500,
      cost: 0.005,
      tokens: 420,
    },
    {
      id: "node-3",
      name: "Subflow Node",
      type: "subgraph",
      duration: 4000,
      children: [
        {
          id: "node-3__child-1",
          name: "Inner Analyzer",
          type: "llm_prompt",
          duration: 1000,
        },
      ],
    },
  ];

  it("renders the agent title and logs label", () => {
    render(
      <NodeOutputListView
        currentAgent={{ name: "OrderAssistant" }}
        nodes={mockNodes}
        onNodeSelect={vi.fn()}
      />,
    );

    expect(screen.getByText("OrderAssistant Logs")).toBeInTheDocument();
  });

  it("falls back to 'Agent Logs' when currentAgent is omitted", () => {
    render(
      <NodeOutputListView
        nodes={mockNodes}
        onNodeSelect={vi.fn()}
      />,
    );

    expect(screen.getByText("Agent Logs")).toBeInTheDocument();
  });

  it("displays empty state when nodes list is empty", () => {
    render(
      <NodeOutputListView
        nodes={[]}
        onNodeSelect={vi.fn()}
      />,
    );

    expect(screen.getByText("No nodes to display")).toBeInTheDocument();
  });

  it("renders node names in the tree", () => {
    render(
      <NodeOutputListView
        nodes={mockNodes}
        onNodeSelect={vi.fn()}
      />,
    );

    expect(screen.getByText("Search Node")).toBeInTheDocument();
    expect(screen.getByText("Classifier Node")).toBeInTheDocument();
    expect(screen.getByText("Subflow Node")).toBeInTheDocument();
  });

  it("filters nodes when typing into the search box", () => {
    render(
      <NodeOutputListView
        nodes={mockNodes}
        onNodeSelect={vi.fn()}
      />,
    );

    const searchInput = screen.getByPlaceholderText("Search");
    fireEvent.change(searchInput, { target: { value: "Class" } });

    expect(screen.getByText("Classifier Node")).toBeInTheDocument();
    expect(screen.queryByText("Search Node")).not.toBeInTheDocument();
  });

  it("shows 'No matching nodes' when search has zero matches", () => {
    render(
      <NodeOutputListView
        nodes={mockNodes}
        onNodeSelect={vi.fn()}
      />,
    );

    const searchInput = screen.getByPlaceholderText("Search");
    fireEvent.change(searchInput, { target: { value: "nonexistent_node_xyz" } });

    expect(screen.getByText("No matching nodes")).toBeInTheDocument();
  });

  it("calls onNodeSelect when a node row is clicked", () => {
    const handleNodeSelect = vi.fn();
    render(
      <NodeOutputListView
        nodes={mockNodes}
        onNodeSelect={handleNodeSelect}
      />,
    );

    const nodeElement = screen.getByText("Search Node");
    fireEvent.click(nodeElement);

    expect(handleNodeSelect).toHaveBeenCalledWith("node-1");
  });

  it("retains parent node when a child matches the search query", () => {
    render(
      <NodeOutputListView
        nodes={mockNodes}
        onNodeSelect={vi.fn()}
      />,
    );

    const searchInput = screen.getByPlaceholderText("Search");
    fireEvent.change(searchInput, { target: { value: "Inner Analyzer" } });

    expect(screen.getByText("Subflow Node")).toBeInTheDocument();
    expect(screen.getByText("Inner Analyzer")).toBeInTheDocument();
    expect(screen.queryByText("Search Node")).not.toBeInTheDocument();
  });
});
