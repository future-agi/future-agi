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
  nodeExecutionStates: {},
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

const notificationMocks = vi.hoisted(() => ({
  enqueueSnackbar: vi.fn(),
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
      nodeExecutionStates: storeMocks.nodeExecutionStates,
    }),
  useWorkflowRunStore: {
    getState: () => ({ isRunning: workflowMocks.isRunning }),
  },
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
  enqueueSnackbar: notificationMocks.enqueueSnackbar,
}));

const theme = createTheme();

const node = {
  id: "node-1",
  type: NODE_TYPES.AGENT,
  data: { label: "Agent node" },
};

let currentStoreState;

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
    storeMocks.nodeExecutionStates = {};
    draftMocks.ensureDraft.mockResolvedValue(true);
    apiMocks.deleteNodeApi.mockResolvedValue({});
    currentStoreState = {
      nodes: [node],
      edges: [],
      currentAgent: { id: "graph-1", version_id: "version-1" },
      nodeExecutionStates: storeMocks.nodeExecutionStates,
    };
    storeMocks.getState.mockImplementation(() => currentStoreState);
    storeMocks.deleteNode.mockImplementation((nodeId) => {
      if (workflowMocks.isRunning) return;
      currentStoreState = {
        ...currentStoreState,
        nodes: currentStoreState.nodes.filter((n) => n.id !== nodeId),
        edges: currentStoreState.edges.filter(
          (edge) => edge.source !== nodeId && edge.target !== nodeId,
        ),
      };
    });
    storeMocks.setGraphData.mockImplementation((nodes, edges) => {
      currentStoreState = {
        ...currentStoreState,
        nodes,
        edges,
      };
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
    expect(tooltip).toHaveTextContent(
      "Stop the workflow before deleting this node",
    );

    await user.click(deleteButton);
    expect(
      screen.queryByRole("dialog", { name: "Delete Node" }),
    ).not.toBeInTheDocument();
    expect(storeMocks.deleteNode).not.toHaveBeenCalled();
    expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
  });

  it("keeps the drawer delete action unavailable while the selected node is running", async () => {
    const user = userEvent.setup();
    storeMocks.nodeExecutionStates = { "node-1": "running" };
    currentStoreState = {
      ...currentStoreState,
      nodeExecutionStates: storeMocks.nodeExecutionStates,
    };

    renderDrawer();

    const deleteButton = screen.getByRole("button", {
      name: "Delete node from editor: Agent node",
    });
    expect(deleteButton).not.toBeDisabled();
    expect(deleteButton).toHaveAttribute("aria-disabled", "true");

    await user.hover(deleteButton);

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent(
      "Wait for this node to finish running before deleting it",
    );

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

  it("restores the drawer context when an existing draft delete fails", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    apiMocks.deleteNodeApi.mockRejectedValue(new Error("delete failed"));

    renderDrawer({ onClose });

    await user.click(
      screen.getByRole("button", {
        name: "Delete node from editor: Agent node",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(apiMocks.deleteNodeApi).toHaveBeenCalledWith({
        graphId: "graph-1",
        versionId: "version-1",
        nodeId: "node-1",
      }),
    );
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(storeMocks.setGraphData).toHaveBeenCalledWith([node], []);
    expect(storeMocks.setSelectedNode).toHaveBeenCalledWith(node);
    expect(notificationMocks.enqueueSnackbar).toHaveBeenCalledWith(
      "Couldn't delete node. Your changes were restored. Try again.",
      { variant: "error" },
    );
  });

  it("restores the drawer context if the workflow starts before delete persists", async () => {
    const user = userEvent.setup();
    draftMocks.ensureDraft.mockImplementation(async () => {
      workflowMocks.isRunning = true;
      return true;
    });

    renderDrawer();

    await user.click(
      screen.getByRole("button", {
        name: "Delete node from editor: Agent node",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(draftMocks.ensureDraft).toHaveBeenCalled());
    expect(storeMocks.deleteNode).toHaveBeenCalledWith("node-1");
    expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
    expect(storeMocks.setGraphData).toHaveBeenCalledWith([node], []);
    expect(storeMocks.setSelectedNode).toHaveBeenCalledWith(node);
  });

  it("restores the drawer context if the node starts running before delete persists", async () => {
    const user = userEvent.setup();
    draftMocks.ensureDraft.mockImplementation(async () => {
      storeMocks.nodeExecutionStates = { "node-1": "running" };
      currentStoreState = {
        ...currentStoreState,
        nodeExecutionStates: storeMocks.nodeExecutionStates,
      };
      return true;
    });

    renderDrawer();

    await user.click(
      screen.getByRole("button", {
        name: "Delete node from editor: Agent node",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(draftMocks.ensureDraft).toHaveBeenCalled());
    expect(storeMocks.deleteNode).toHaveBeenCalledWith("node-1");
    expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
    expect(storeMocks.setGraphData).toHaveBeenCalledWith([node], []);
    expect(storeMocks.setSelectedNode).toHaveBeenCalledWith(node);
  });

  it("does not draft or call the API when the store rejects the drawer delete", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    storeMocks.deleteNode.mockImplementation(() => {});

    renderDrawer({ onClose });

    await user.click(
      screen.getByRole("button", {
        name: "Delete node from editor: Agent node",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Delete" }));

    expect(storeMocks.deleteNode).toHaveBeenCalledWith("node-1");
    expect(onClose).not.toHaveBeenCalled();
    expect(draftMocks.ensureDraft).not.toHaveBeenCalled();
    expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
  });

  it("does not queue duplicate drawer deletes while one is pending", async () => {
    const user = userEvent.setup();
    apiMocks.deleteNodeApi.mockReturnValue(new Promise(() => {}));

    renderDrawer();

    const deleteButton = screen.getByRole("button", {
      name: "Delete node from editor: Agent node",
    });
    await user.click(deleteButton);
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(apiMocks.deleteNodeApi).toHaveBeenCalledTimes(1),
    );

    await user.click(deleteButton);

    expect(
      screen.queryByRole("dialog", { name: "Delete Node" }),
    ).not.toBeInTheDocument();
    expect(apiMocks.deleteNodeApi).toHaveBeenCalledTimes(1);
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
