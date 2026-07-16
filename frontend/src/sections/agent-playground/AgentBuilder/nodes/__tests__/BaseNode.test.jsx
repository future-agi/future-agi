/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import BaseNode from "../BaseNode";
import { NODE_TYPES, PORT_DIRECTION } from "../../../utils/constants";
import useBaseNodeState from "../hooks/useBaseNodeState";
import useBaseNodeActions from "../hooks/useBaseNodeActions";

// ---- Mocks ----
vi.mock("@xyflow/react", () => ({
  Handle: ({ id, type }) => <div data-testid={`handle-${type}-${id}`} />,
  Position: { Left: "left", Right: "right" },
}));

vi.mock("../hooks/useBaseNodeState", () => ({
  default: vi.fn(),
}));

vi.mock("../hooks/useBaseNodeActions", () => ({
  default: vi.fn(),
}));

vi.mock("../../../components/NodeSelectionPopper", () => ({
  default: () => null,
}));

vi.mock("../StartIndicator", () => ({
  default: () => <div data-testid="start-indicator" />,
}));

const theme = createTheme({
  palette: {
    red: { 50: "#fff1f1", 500: "#ef4444", 700: "#dc2626", 800: "#991b1b" },
    green: { 50: "#ecfdf5", 500: "#22c55e" },
    blue: { 100: "#dbeafe", 500: "#3b82f6", 600: "#2563eb" },
    black: { 200: "#e5e7eb", 400: "#9ca3af", 800: "#1f2937" },
  },
});

const mockHandleDeleteClick = vi.fn();
const mockHandleNodeClick = vi.fn();

const defaultState = {
  nodeHeight: 40,
  selected: false,
  hasValidationError: false,
  hasIncomingEdge: true,
  hasOutgoingEdge: true,
  isRunning: false,
  isCompleted: false,
  isError: false,
  isWorkflowRunning: false,
  preview: false,
};

function renderBaseNode(props = {}) {
  return render(
    <ThemeProvider theme={theme}>
      <BaseNode
        id="node-1"
        type={NODE_TYPES.AGENT}
        isConnectable
        selected={false}
        data={{
          label: "Agent node",
          preview: false,
          ports: [{ id: "input", direction: PORT_DIRECTION.INPUT }],
        }}
        {...props}
      />
    </ThemeProvider>,
  );
}

describe("Unit: BaseNode", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockHandleDeleteClick.mockImplementation((event) =>
      event.stopPropagation(),
    );
    useBaseNodeState.mockReturnValue(defaultState);
    useBaseNodeActions.mockReturnValue({
      handleNodeClick: mockHandleNodeClick,
      handleAddClick: vi.fn(),
      handlePopperClose: vi.fn(),
      handleNodeSelect: vi.fn(),
      handleDeleteClick: mockHandleDeleteClick,
      popperOpen: false,
      addButtonRef: { current: null },
    });
  });

  it("shows the real delete tooltip on hover and preserves delete clicks", async () => {
    renderBaseNode();

    const deleteButton = screen.getByRole("button", {
      name: "Delete canvas node: Agent node",
    });
    expect(deleteButton).toHaveClass("node-delete-btn");

    // The button starts with pointer-events disabled until the node hover/focus
    // selector reveals it. Hover the tooltip wrapper so the test exercises the
    // same always-hit-testable target used by the browser.
    fireEvent.mouseOver(deleteButton.parentElement);

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("Delete node");

    fireEvent.click(deleteButton);
    expect(mockHandleDeleteClick).toHaveBeenCalledTimes(1);
    expect(mockHandleNodeClick).not.toHaveBeenCalled();
  });

  it("does not render the delete tooltip control in preview mode", () => {
    renderBaseNode({
      data: {
        label: "Agent node",
        preview: true,
        ports: [{ id: "input", direction: PORT_DIRECTION.INPUT }],
      },
    });

    expect(
      screen.queryByRole("button", { name: /delete canvas node/i }),
    ).not.toBeInTheDocument();
  });

  it("does not render the delete tooltip control while the workflow is running", () => {
    useBaseNodeState.mockReturnValue({
      ...defaultState,
      isWorkflowRunning: true,
    });

    renderBaseNode();

    expect(
      screen.queryByRole("button", { name: /delete canvas node/i }),
    ).not.toBeInTheDocument();
  });
});
