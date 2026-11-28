import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import NodeDrawer from "../NodeDrawer";

// ---- Mocks ----
const mockNode = {
  id: "node-1",
  type: "llm_prompt",
  data: { label: "Summarize" },
};

const mockStoreState = {
  setSelectedNode: vi.fn(),
  updateNodeData: vi.fn(),
  currentAgent: { id: "graph-1", version_id: "version-1" },
  setNodeFormDirty: vi.fn(),
  deleteNode: vi.fn(),
  setGraphData: vi.fn(),
};

vi.mock("../../../store", () => ({
  useAgentPlaygroundStore: {
    getState: () => ({ nodes: [mockNode], edges: [] }),
  },
  useAgentPlaygroundStoreShallow: (selector) => selector(mockStoreState),
  useWorkflowRunStoreShallow: (selector) => selector({ isRunning: false }),
}));

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetNodeDetail: () => ({ data: undefined, isFetching: false }),
  deleteNodeApi: vi.fn(),
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

vi.mock("../../saveDraftContext", () => ({
  useSaveDraftContext: () => ({ ensureDraft: vi.fn() }),
}));

vi.mock("../../../hooks/useCanEditAgent", () => ({
  default: () => ({ isReadOnly: false }),
}));

vi.mock("../nodeFormUtils", () => ({
  getDefaultValues: () => ({}),
  mapNodeDetailToNodeData: (_detail, node) => node,
}));

vi.mock("../forms/nodeFormSchemas", () => ({
  getNodeFormSchema: () => null,
}));

vi.mock("../NodeConfigurationForm", () => ({
  default: () => null,
}));

vi.mock("../NodeDrawerSkeleton", () => ({
  default: () => null,
}));

vi.mock("../../../components/NodeCard", () => ({
  default: () => null,
}));

vi.mock("src/components/custom-dialog", () => ({
  ConfirmDialog: () => null,
}));

vi.mock("src/components/svg-color", () => ({
  default: () => <span />,
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// ---- Helpers ----
const theme = createTheme();

function renderDrawer(props = {}) {
  const defaultProps = {
    open: true,
    onClose: vi.fn(),
    node: mockNode,
    width: 500,
    isResizing: false,
    onResizeStart: vi.fn(),
    ...props,
  };
  return render(
    <ThemeProvider theme={theme}>
      <NodeDrawer {...defaultProps} />
    </ThemeProvider>,
  );
}

// ---- Tests ----
describe("NodeDrawer header buttons (#1074)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows a "Close" tooltip on hover and names the close button', async () => {
    renderDrawer();

    const closeButton = screen.getByRole("button", { name: "Close" });
    fireEvent.mouseOver(closeButton);

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("Close");
  });

  it("still closes the drawer when the close button is clicked", () => {
    const onClose = vi.fn();
    renderDrawer({ onClose });

    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('keeps the existing "Delete node" tooltip', async () => {
    renderDrawer();

    fireEvent.mouseOver(screen.getByLabelText("Delete node"));

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("Delete node");
  });
});
