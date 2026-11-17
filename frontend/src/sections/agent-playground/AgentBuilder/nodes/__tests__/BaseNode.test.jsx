import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import BaseNode from "../BaseNode";

// ---- Mocks ----
// The real CustomTooltip stays: the test hovers the delete button and reads
// the tooltip it opens.
const { mockActions } = vi.hoisted(() => ({
  mockActions: {
    handleNodeClick: vi.fn(),
    handleAddClick: vi.fn(),
    handlePopperClose: vi.fn(),
    handleNodeSelect: vi.fn(),
    handleDeleteClick: vi.fn(),
    popperOpen: false,
    addButtonRef: { current: null },
  },
}));

// Handle needs a React Flow provider; the canvas wiring is not under test.
vi.mock("@xyflow/react", () => ({
  Handle: () => null,
  Position: { Left: "left", Right: "right" },
}));

vi.mock("src/components/svg-color", () => ({
  default: () => <span data-testid="svg-icon" />,
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

// An editable node, idle, with an outgoing edge (so no add button).
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

// The real styles show the delete button only while the node is hovered;
// that hover CSS is covered by hooks/__tests__/useBaseNodeStyles.test.jsx.
vi.mock("../hooks/useBaseNodeStyles", () => ({
  default: () => ({ boxSx: {}, borderColor: "grey", theme: {} }),
}));

vi.mock("../hooks/useBaseNodeActions", () => ({
  default: () => mockActions,
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
// Hover with fireEvent only: a user-event press makes CustomTooltip wait for
// pointer movement jsdom never sends.
describe("BaseNode canvas delete button", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('is named "Delete node" and shows that tooltip on hover', async () => {
    renderNode();

    const deleteButton = screen.getByRole("button", { name: "Delete node" });
    fireEvent.mouseOver(deleteButton);

    expect(await screen.findByRole("tooltip")).toHaveTextContent("Delete node");
  });

  it("still deletes the node when clicked", () => {
    renderNode();

    fireEvent.click(screen.getByRole("button", { name: "Delete node" }));

    expect(mockActions.handleDeleteClick).toHaveBeenCalledTimes(1);
  });
});
