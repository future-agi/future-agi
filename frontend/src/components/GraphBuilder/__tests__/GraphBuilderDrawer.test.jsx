import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import GraphBuilderDrawer from "../GraphBuilderDrawer";
import { useGraphStore } from "../store/graphStore";

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// The real builder needs a React Flow canvas. One node header is enough to
// type in a rename input inside the real Flow Builder dialog.
vi.mock("../GraphBuilder", async () => {
  const { default: NodeHeader } = await import("../nodes/NodeHeader");
  return {
    default: function GraphBuilderStub() {
      return (
        <NodeHeader
          type="conversation"
          nodeId="Conversation_a"
          title="Conversation_a"
          editable
        />
      );
    },
  };
});

describe("GraphBuilderDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useGraphStore.setState({
      nodes: [
        {
          id: "Conversation_a",
          type: "conversation",
          position: { x: 0, y: 0 },
          data: { name: "Conversation_a", prompt: "Hi", editMode: true },
        },
      ],
      edges: [],
    });
  });

  it("stays open when Escape cancels a node rename", () => {
    const onClose = vi.fn();
    render(<GraphBuilderDrawer open onClose={onClose} />);

    fireEvent.click(screen.getByText("Conversation_a"));
    const input = screen.getByRole("textbox", { name: "Node name" });
    fireEvent.change(input, { target: { value: "Greeting" } });
    fireEvent.keyDown(input, { key: "Escape" });

    expect(
      screen.queryByRole("textbox", { name: "Node name" }),
    ).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(useGraphStore.getState().nodes[0].id).toBe("Conversation_a");
  });
});
