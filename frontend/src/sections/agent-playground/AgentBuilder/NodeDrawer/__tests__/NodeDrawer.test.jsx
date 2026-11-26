/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
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

const permissionMocks = vi.hoisted(() => ({ isReadOnly: false }));

vi.mock("../../../hooks/useCanEditAgent", () => ({
  default: () => permissionMocks,
}));

const notificationMocks = vi.hoisted(() => ({
  enqueueSnackbar: vi.fn(),
}));

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
  enqueueSnackbar: notificationMocks.enqueueSnackbar,
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
    permissionMocks.isReadOnly = false;
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

    const deleteButton = screen.getByRole("button", { name: "Delete node" });

    await user.hover(deleteButton);

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("Delete node");

    await user.click(deleteButton);
    expect(screen.getByRole("dialog", { name: "Delete Node" })).toBeVisible();
  });

  it("keeps close keyboard accessible without deleting a node", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderDrawer({ onClose });

    await user.tab();
    await user.tab();
    expect(
      screen.getByRole("button", { name: "Close node editor" }),
    ).toHaveFocus();
    expect(
      await screen.findByRole("tooltip", { name: "Close" }),
    ).toHaveTextContent("Close");
    await user.keyboard("{Enter}");
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
  });

  it("hides deletion but leaves close available in read-only mode", () => {
    permissionMocks.isReadOnly = true;
    renderDrawer();

    expect(
      screen.queryByRole("button", { name: "Delete node" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Close node editor" }),
    ).toBeEnabled();
    expect(storeMocks.deleteNode).not.toHaveBeenCalled();
  });

  it("disables deletion while a workflow runs", () => {
    workflowMocks.isRunning = true;
    renderDrawer();
    expect(screen.getByRole("button", { name: "Delete node" })).toBeDisabled();
  });

  it.each(["read-only", "running"])(
    "guards an already-open confirmation when the graph becomes %s",
    async (mode) => {
      const user = userEvent.setup();
      const view = renderDrawer();
      await user.click(screen.getByRole("button", { name: "Delete node" }));
      if (mode === "read-only") permissionMocks.isReadOnly = true;
      else workflowMocks.isRunning = true;
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

      await user.click(
        within(screen.getByRole("dialog", { name: "Delete Node" })).getByRole(
          "button",
          { name: "Delete", exact: true },
        ),
      );
      expect(storeMocks.deleteNode).not.toHaveBeenCalled();
      expect(draftMocks.ensureDraft).not.toHaveBeenCalled();
      expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
    },
  );

  it("deletes an existing draft using the store's version_id", async () => {
    const user = userEvent.setup();
    renderDrawer();
    await user.click(screen.getByRole("button", { name: "Delete node" }));
    await user.click(
      within(screen.getByRole("dialog", { name: "Delete Node" })).getByRole(
        "button",
        { name: "Delete", exact: true },
      ),
    );

    await waitFor(() =>
      expect(apiMocks.deleteNodeApi).toHaveBeenCalledWith({
        graphId: "graph-1",
        versionId: "version-1",
        nodeId: "node-1",
      }),
    );
    expect(storeMocks.deleteNode).toHaveBeenCalledWith("node-1");
  });

  it("rolls back when draft creation is denied", async () => {
    draftMocks.ensureDraft.mockResolvedValue(false);
    const user = userEvent.setup();
    renderDrawer();
    await user.click(screen.getByRole("button", { name: "Delete node" }));
    await user.click(
      within(screen.getByRole("dialog", { name: "Delete Node" })).getByRole(
        "button",
        { name: "Delete", exact: true },
      ),
    );

    await waitFor(() =>
      expect(storeMocks.setGraphData).toHaveBeenCalledWith([node], []),
    );
    expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
  });

  it("shows the close tooltip and preserves close clicks", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    renderDrawer({ onClose });

    const closeButton = screen.getByRole("button", {
      name: "Close node editor",
    });

    await user.hover(closeButton);

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("Close");

    await user.click(closeButton);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
