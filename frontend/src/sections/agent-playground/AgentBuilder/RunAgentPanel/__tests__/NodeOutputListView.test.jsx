import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import NodeOutputListView from "../NodeOutputListView";

const nodes = [
  {
    id: "agent",
    name: "Nested agent",
    type: "agent",
    children: [
      {
        id: "agent__prompt",
        name: "Generate answer",
        type: "llm_prompt",
        duration: 500,
        children: [],
      },
    ],
  },
];

describe("NodeOutputListView", () => {
  it("selects rows and filters nested executions by name", async () => {
    const onNodeSelect = vi.fn();
    const user = userEvent.setup();
    render(
      <NodeOutputListView
        nodes={nodes}
        selectedNodeId={null}
        onNodeSelect={onNodeSelect}
      />,
    );

    await user.click(screen.getByText("Generate answer"));
    expect(onNodeSelect).toHaveBeenCalledWith("agent__prompt");

    await user.type(screen.getByPlaceholderText("Search"), "generate");
    expect(screen.getByText("Generate answer")).toBeInTheDocument();
    expect(screen.getByText("Nested agent")).toBeInTheDocument();

    await user.clear(screen.getByPlaceholderText("Search"));
    await user.type(screen.getByPlaceholderText("Search"), "missing");
    expect(screen.getByText("No matching nodes")).toBeInTheDocument();
  });

  it("uses the existing empty state when no nodes executed", () => {
    render(<NodeOutputListView nodes={[]} onNodeSelect={vi.fn()} />);

    expect(screen.getByText("No nodes to display")).toBeInTheDocument();
  });
});
