/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import NodeDrawer from "../NodeDrawer";
import { NODE_TYPES } from "../../../utils/constants";

const storeMocks = vi.hoisted(() => ({
  setSelectedNode: vi.fn(),
  updateNodeData: vi.fn(),
  setNodeFormDirty: vi.fn(),
  deleteNode: vi.fn(),
  setGraphData: vi.fn(),
  getState: vi.fn(),
}));

const queryMocks = vi.hoisted(() => ({
  invalidateQueries: vi.fn(),
}));

const apiMocks = vi.hoisted(() => ({
  deleteNodeApi: vi.fn(),
}));

const draftMocks = vi.hoisted(() => ({
  ensureDraft: vi.fn(),
}));

const workflowMocks = vi.hoisted(() => ({
  isRunning: false,
}));

// ---- Mocks ----
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => queryMocks,
}));

vi.mock("../../../store", () => ({
  useAgentPlaygroundStore: {
    getState: storeMocks.getState,
  },
  useAgentPlaygroundStoreShallow: (selector) =>
    selector({
      setSelectedNode: storeMocks.setSelectedNode,
      updateNodeData: storeMocks.updateNodeData,
      currentAgent: { id: "graph-1", version_id: "version-1" },
      setNodeFormDirty: storeMocks.setNodeFormDirty,
      deleteNode: storeMocks.deleteNode,
      setGraphData: storeMocks.setGraphData,
    }),
  useWorkflowRunStoreShallow: (selector) =>
    selector({ isRunning: workflowMocks.isRunning }),
}));

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetNodeDetail: () => ({ data: null, isFetching: false }),
  deleteNodeApi: apiMocks.deleteNodeApi,
}));

vi.mock("../../saveDraftContext", () => ({
  useSaveDraftContext: () => ({ ensureDraft: draftMocks.ensureDraft }),
}));

vi.mock("../NodeConfigurationForm", () => ({
  default: () => <div data-testid="node-configuration-form" />,
}));

vi.mock("../NodeDrawerSkeleton", () => ({
  default: () => <div data-testid="node-drawer-skeleton" />,
}));

vi.mock("../../components/NodeCard", () => ({
  default: () => <div data-testid="node-card" />,
}));

vi.mock("../forms/nodeFormSchemas", () => ({
  getNodeFormSchema: () => null,
}));

vi.mock("../nodeFormUtils", () => ({
  getDefaultValues: () => ({}),
  mapNodeDetailToNodeData: (_detail, node) => node,
}));

vi.mock("src/components/custom-dialog", () => ({
  ConfirmDialog: ({ open, title, content, action }) =>
    open ? (
      <div role="dialog" aria-label={title}>
        <p>{content}</p>
        {action}
      </div>
    ) : null,
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

const theme = createTheme();

const node = {
  id: "node-1",
  type: NODE_TYPES.AGENT,
  data: { label: "Agent node" },
};

function renderDrawer(props = {}) {
  return render(
    <ThemeProvider theme={theme}>
      <NodeDrawer
        open
        onClose={vi.fn()}
        node={node}
        width={520}
        isResizing={false}
        onResizeStart={vi.fn()}
        {...props}
      />
    </ThemeProvider>,
  );
}

describe("Unit: NodeDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    workflowMocks.isRunning = false;
    draftMocks.ensureDraft.mockResolvedValue(true);
    apiMocks.deleteNodeApi.mockResolvedValue({});
    storeMocks.getState.mockReturnValue({
      nodes: [node],
      edges: [],
      currentAgent: { id: "graph-1", version_id: "version-1" },
    });
  });

  it("keeps the drawer delete button labelled with its existing tooltip", async () => {
    const user = userEvent.setup();

    renderDrawer();

    const deleteButton = screen.getByRole("button", {
      name: "Delete node from editor: Agent node",
    });
    expect(deleteButton).toBeInTheDocument();

    await user.hover(deleteButton);

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("Delete node");
  });

  it("keeps the drawer delete tooltip keyboard-discoverable when the workflow disables the action", async () => {
    const user = userEvent.setup();
    workflowMocks.isRunning = true;

    renderDrawer();

    const deleteButton = screen.getByRole("button", {
      name: "Delete node from editor: Agent node",
    });
    expect(deleteButton).not.toBeDisabled();
    expect(deleteButton).toHaveAttribute("aria-disabled", "true");

    await user.tab();
    expect(deleteButton).toHaveFocus();

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("Delete node");

    await user.click(deleteButton);
    expect(
      screen.queryByRole("dialog", { name: "Delete Node" }),
    ).not.toBeInTheDocument();
    expect(storeMocks.deleteNode).not.toHaveBeenCalled();
    expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
  });

  it("deletes from an existing draft with the current agent version id", async () => {
    const user = userEvent.setup();

    renderDrawer();

    await user.click(
      screen.getByRole("button", {
        name: "Delete node from editor: Agent node",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Delete" }));

    expect(storeMocks.deleteNode).toHaveBeenCalledWith("node-1");
    expect(draftMocks.ensureDraft).toHaveBeenCalledWith({
      skipDirtyCheck: true,
    });
    await waitFor(() =>
      expect(apiMocks.deleteNodeApi).toHaveBeenCalledWith({
        graphId: "graph-1",
        versionId: "version-1",
        nodeId: "node-1",
      }),
    );
    expect(storeMocks.setGraphData).not.toHaveBeenCalled();
  });

  it("closes an open delete confirmation when the workflow starts", async () => {
    const user = userEvent.setup();
    const view = renderDrawer();

    await user.click(
      screen.getByRole("button", {
        name: "Delete node from editor: Agent node",
      }),
    );
    expect(screen.getByRole("dialog", { name: "Delete Node" })).toBeVisible();

    workflowMocks.isRunning = true;
    view.rerender(
      <ThemeProvider theme={theme}>
        <NodeDrawer
          open
          onClose={vi.fn()}
          node={node}
          width={520}
          isResizing={false}
          onResizeStart={vi.fn()}
        />
      </ThemeProvider>,
    );

    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Delete Node" }),
      ).not.toBeInTheDocument(),
    );
    expect(storeMocks.deleteNode).not.toHaveBeenCalled();
    expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
  });

  it("shows the close tooltip and preserves close clicks", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    renderDrawer({ onClose });

    const closeButton = screen.getByRole("button", {
      name: "Close node editor",
    });
    expect(closeButton).toBeInTheDocument();

    await user.hover(closeButton);

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("Close");

    await user.click(closeButton);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
