import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { enqueueSnackbar } from "notistack";
import { fireEvent, render, screen, userEvent } from "src/utils/test-utils";
import NodeHeader from "../NodeHeader";
import { useGraphStore } from "../../store/graphStore";

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

const seedGraph = () => {
  useGraphStore.setState({
    nodes: [
      {
        id: "Conversation_a",
        type: "conversation",
        position: { x: 0, y: 0 },
        data: { name: "Conversation_a", prompt: "Hi", editMode: true },
      },
      {
        id: "End_call_b",
        type: "end",
        position: { x: 0, y: 200 },
        data: { name: "End_call_b", prompt: "Bye", editMode: true },
      },
    ],
    edges: [
      {
        id: "edge-1",
        type: "condition",
        source: "Conversation_a",
        target: "End_call_b",
        data: { prompt: "Default condition" },
      },
    ],
    activeNodeId: "Conversation_a",
    activeEdgeId: null,
  });
};

const renderHeader = (props = {}) =>
  render(
    <NodeHeader
      type="conversation"
      nodeId="Conversation_a"
      title="Conversation_a"
      editable
      {...props}
    />,
  );

const startEditing = () => {
  fireEvent.click(screen.getByText("Conversation_a"));
  return screen.getByRole("textbox", { name: "Node name" });
};

const getNode = (id) =>
  useGraphStore.getState().nodes.find((node) => node.id === id);

describe("NodeHeader click-to-edit name", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedGraph();
  });

  it("switches the name to a focused input when clicked", () => {
    renderHeader();
    const input = startEditing();
    expect(input).toHaveValue("Conversation_a");
    expect(input).toHaveFocus();
  });

  it("saves on Enter and moves its edges and selection", () => {
    renderHeader();
    const input = startEditing();
    fireEvent.change(input, { target: { value: "  Greeting  " } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    const state = useGraphStore.getState();
    expect(getNode("Conversation_a")).toBeUndefined();
    expect(getNode("Greeting")?.data?.name).toBe("Greeting");
    expect(getNode("Greeting")?.data?.prompt).toBe("Hi");
    expect(state.edges[0].source).toBe("Greeting");
    expect(state.edges[0].target).toBe("End_call_b");
    expect(state.activeNodeId).toBe("Greeting");
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("saves when the input loses focus", () => {
    renderHeader();
    const input = startEditing();
    fireEvent.change(input, { target: { value: "Greeting" } });
    fireEvent.blur(input);

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(getNode("Greeting")?.data?.name).toBe("Greeting");
  });

  it("cancels on Escape and keeps the original name", () => {
    renderHeader();
    const input = startEditing();
    fireEvent.change(input, { target: { value: "Greeting" } });
    fireEvent.keyDown(input, { key: "Escape" });

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByText("Conversation_a")).toBeInTheDocument();
    expect(getNode("Conversation_a")?.data?.name).toBe("Conversation_a");
    expect(getNode("Greeting")).toBeUndefined();
  });

  it("rejects an empty or whitespace-only name", () => {
    renderHeader();
    const input = startEditing();
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(getNode("Conversation_a")?.data?.name).toBe("Conversation_a");
  });

  it("rejects a name another node already uses", () => {
    renderHeader();
    const input = startEditing();
    fireEvent.change(input, { target: { value: "End_call_b" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(getNode("Conversation_a")?.data?.name).toBe("Conversation_a");
    expect(useGraphStore.getState().edges[0].source).toBe("Conversation_a");
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      expect.stringContaining("End_call_b"),
      { variant: "error" },
    );
  });

  it("keeps the name read-only when the graph is not being edited", () => {
    renderHeader({ editable: false });
    fireEvent.click(screen.getByText("Conversation_a"));
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("stops Escape at the input so the builder dialog stays open", () => {
    const onAncestorKeyDown = vi.fn();
    render(
      <div onKeyDown={onAncestorKeyDown}>
        <NodeHeader
          type="conversation"
          nodeId="Conversation_a"
          title="Conversation_a"
          editable
        />
      </div>,
    );
    const input = startEditing();
    fireEvent.keyDown(input, { key: "Escape" });

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(onAncestorKeyDown).not.toHaveBeenCalled();
  });
});

describe("NodeHeader rename from the keyboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedGraph();
  });

  it.each([
    ["Enter", "{Enter}"],
    ["Space", " "],
  ])("starts editing with %s after tabbing to the name", async (_key, keys) => {
    const user = userEvent.setup();
    renderHeader();

    await user.tab();
    expect(
      screen.getByRole("button", { name: "Rename Conversation_a" }),
    ).toHaveFocus();

    await user.keyboard(keys);
    expect(screen.getByRole("textbox", { name: "Node name" })).toHaveFocus();
  });

  it("offers no rename control when the graph is read-only", async () => {
    const user = userEvent.setup();
    renderHeader({ editable: false });

    await user.tab();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(document.body).toHaveFocus();
  });
});
