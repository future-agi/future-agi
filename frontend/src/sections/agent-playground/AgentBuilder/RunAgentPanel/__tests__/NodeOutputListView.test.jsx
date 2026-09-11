import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import NodeOutputListView from "../NodeOutputListView";

vi.mock("src/components/svg-color", () => ({
  default: () => <span aria-hidden="true" />,
}));

vi.mock("../CustomTreeNode", () => ({
  CustomTreeNode: ({ node, onSelect }) => (
    <button type="button" onClick={onSelect}>
      {node.name}
    </button>
  ),
}));

const nodes = [
  {
    id: "agent",
    type: "agent",
    name: "Research agent",
    duration: 100,
    children: [
      {
        id: "agent__search",
        type: "llm_prompt",
        name: "Search the web",
        duration: 200,
      },
    ],
  },
  {
    id: "summarize",
    type: "llm_prompt",
    name: "Summarize results",
    duration: 300,
  },
];

describe("NodeOutputListView", () => {
  it("selects a node and keeps an ancestor when searching for a child", () => {
    const onNodeSelect = vi.fn();
    render(
      <NodeOutputListView
        nodes={nodes}
        selectedNodeId="agent__search"
        onNodeSelect={onNodeSelect}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Search the web" }));
    expect(onNodeSelect).toHaveBeenCalledWith("agent__search");

    fireEvent.change(screen.getByPlaceholderText("Search"), {
      target: { value: "search" },
    });

    expect(screen.getByRole("button", { name: "Research agent" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search the web" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Summarize results" }),
    ).not.toBeInTheDocument();
  });

  it("shows an empty state for an execution without nodes", () => {
    render(
      <NodeOutputListView
        nodes={[]}
        onNodeSelect={vi.fn()}
      />,
    );

    expect(screen.getByText("No nodes to display")).toBeInTheDocument();
  });

  it("shows a distinct empty state when search has no matches", () => {
    render(
      <NodeOutputListView
        nodes={nodes}
        onNodeSelect={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Search"), {
      target: { value: "missing" },
    });

    expect(screen.getByText("No matching nodes")).toBeInTheDocument();
  });
});
