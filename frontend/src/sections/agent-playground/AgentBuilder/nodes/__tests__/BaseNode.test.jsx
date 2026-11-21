import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import BaseNode from "../BaseNode";

// ---- Mocks ----
// Handle needs a React Flow provider; the canvas wiring is not under test here.
vi.mock("@xyflow/react", () => ({
  Handle: () => null,
  Position: { Left: "left", Right: "right" },
}));

vi.mock("src/components/svg-color", () => ({
  default: () => <span />,
}));

vi.mock("../../../components/NodeSelectionPopper", () => ({
  default: () => null,
}));

vi.mock("../EditableLabel", () => ({
  default: () => null,
}));

vi.mock("../StartIndicator", () => ({
  default: () => null,
}));

vi.mock("../hooks/useBaseNodeState", () => ({
  default: () => ({
    hasIncomingEdge: true,
    hasOutgoingEdge: true,
    isRunning: false,
    isCompleted: false,
    isError: false,
    isWorkflowRunning: false,
    isReadOnly: false,
  }),
}));

// The real styles hide the delete button until the node is hovered; that
// hover CSS is covered by useBaseNodeStyles.test.jsx.
vi.mock("../hooks/useBaseNodeStyles", () => ({
  default: () => ({ boxSx: {}, borderColor: "grey", theme: {} }),
}));

const mockHandleDeleteClick = vi.fn();
vi.mock("../hooks/useBaseNodeActions", () => ({
  default: () => ({
    handleNodeClick: vi.fn(),
    handleAddClick: vi.fn(),
    handlePopperClose: vi.fn(),
    handleNodeSelect: vi.fn(),
    handleDeleteClick: mockHandleDeleteClick,
    popperOpen: false,
    addButtonRef: { current: null },
  }),
}));

// ---- Helpers ----
const theme = createTheme();

function renderNode() {
  return render(
    <ThemeProvider theme={theme}>
      <BaseNode
        id="node-1"
        type="llm_prompt"
        data={{ label: "Summarize", preview: false, ports: [] }}
        isConnectable
      />
    </ThemeProvider>,
  );
}

// ---- Tests ----
describe("BaseNode delete button (#1074)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows a "Delete node" tooltip on hover and names the button', async () => {
    renderNode();

    const deleteButton = screen.getByRole("button", { name: "Delete node" });
    fireEvent.mouseOver(deleteButton);

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("Delete node");
  });

  it("still deletes the node when the button is clicked", () => {
    renderNode();

    fireEvent.click(screen.getByRole("button", { name: "Delete node" }));

    expect(mockHandleDeleteClick).toHaveBeenCalledTimes(1);
  });
});
