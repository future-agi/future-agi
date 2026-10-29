import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import NodeHeader from "../NodeHeader";
import { NODE_TYPES, useGraphStore } from "../../store/graphStore";

vi.mock("src/components/svg-color", () => ({
  default: () => <span data-testid="node-icon" />,
}));

const theme = createTheme();

function renderNodeHeader(props = {}) {
  return render(
    <ThemeProvider theme={theme}>
      <NodeHeader
        id="node-1"
        type={NODE_TYPES.CONVERSATION}
        title="Conversation_1"
        {...props}
      />
    </ThemeProvider>,
  );
}

describe("NodeHeader", () => {
  beforeEach(() => {
    useGraphStore.setState({
      nodes: [
        {
          id: "node-1",
          type: NODE_TYPES.CONVERSATION,
          data: { name: "Conversation_1" },
        },
      ],
      edges: [
        { id: "edge-1", source: "node-1", target: "node-2" },
        { id: "edge-2", source: "node-3", target: "node-1" },
      ],
      activeNodeId: "node-1",
      activeEdgeId: null,
    });
  });

  it("switches to edit mode when the node name is clicked", () => {
    renderNodeHeader();

    fireEvent.click(screen.getByText("Conversation_1"));

    expect(screen.getByRole("textbox")).toHaveValue("Conversation_1");
  });

  it("saves the updated node name on Enter", () => {
    renderNodeHeader();

    fireEvent.click(screen.getByText("Conversation_1"));
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Greeting" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(useGraphStore.getState().nodes[0]).toMatchObject({
      id: "Greeting",
      data: { name: "Greeting" },
    });
    expect(useGraphStore.getState().edges[0].source).toBe("Greeting");
    expect(useGraphStore.getState().edges[1].target).toBe("Greeting");
    expect(useGraphStore.getState().activeNodeId).toBe("Greeting");
  });

  it("saves the updated node name on blur", () => {
    renderNodeHeader();

    fireEvent.click(screen.getByText("Conversation_1"));
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Blurred name" } });
    fireEvent.blur(input);

    expect(useGraphStore.getState().nodes[0].data.name).toBe("Blurred name");
  });

  it("cancels editing on Escape", () => {
    renderNodeHeader();

    fireEvent.click(screen.getByText("Conversation_1"));
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Draft name" } });
    fireEvent.keyDown(input, { key: "Escape" });

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(useGraphStore.getState().nodes[0].data.name).toBe("Conversation_1");
  });
});
