/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import NodeDrawer from "../NodeDrawer";
import { useAgentPlaygroundStore, useWorkflowRunStore } from "../../../store";

// ---------------------------------------------------------------------------
// Mocks. The real CustomTooltip stays: its "Delete node" title lands on the
// wrapping <span>, which does not name the icon-only button inside it.
// ---------------------------------------------------------------------------
vi.mock("../NodeConfigurationForm", () => ({
  default: () => <div data-testid="node-configuration-form" />,
}));

vi.mock("../NodeDrawerSkeleton", () => ({
  default: () => <div data-testid="node-drawer-skeleton" />,
}));

vi.mock("../../../components/NodeCard", () => ({
  default: () => <div data-testid="node-card" />,
}));

vi.mock("../forms/nodeFormSchemas", () => ({
  getNodeFormSchema: () => null,
}));

vi.mock("../nodeFormUtils", () => ({
  getDefaultValues: () => ({}),
  mapNodeDetailToNodeData: (_detail, node) => node,
}));

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetNodeDetail: () => ({ data: undefined, isFetching: false }),
  deleteNodeApi: vi.fn(),
}));

let mockIsReadOnly = false;
vi.mock("../../../hooks/useCanEditAgent", () => ({
  default: () => ({
    canEditAgent: !mockIsReadOnly,
    isReadOnly: mockIsReadOnly,
  }),
}));

vi.mock("src/components/svg-color", () => ({
  default: () => <span data-testid="svg-icon" />,
}));

vi.mock("src/components/custom-dialog", () => ({
  ConfirmDialog: ({ open, title, action }) =>
    open ? (
      <div data-testid="confirm-dialog">
        {title}
        {action}
      </div>
    ) : null,
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const theme = createTheme();
const node = { id: "node-1", type: "llm_prompt", data: { label: "Prompt 1" } };

function renderDrawer(props = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const allProps = {
    open: true,
    onClose: vi.fn(),
    node,
    width: 500,
    isResizing: false,
    onResizeStart: vi.fn(),
    ...props,
  };
  render(
    <ThemeProvider theme={theme}>
      <QueryClientProvider client={queryClient}>
        <NodeDrawer {...allProps} />
      </QueryClientProvider>
    </ThemeProvider>,
  );
  return allProps;
}

// ---------------------------------------------------------------------------
// Tests — AUD-AGENTS-03: icon-only buttons must have a name.
// ---------------------------------------------------------------------------
describe("NodeDrawer — icon button names (AUD-AGENTS-03)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockIsReadOnly = false;
    useAgentPlaygroundStore.getState().reset();
    useWorkflowRunStore.getState().reset();
    useAgentPlaygroundStore.setState({
      currentAgent: { id: "graph-1", version_id: "version-1" },
      nodes: [node],
    });
  });

  it("names the Delete node button", () => {
    renderDrawer();

    expect(
      screen.getByRole("button", { name: "Delete node" }),
    ).toBeInTheDocument();
  });

  it("names the Close button, and it closes a clean drawer", () => {
    const { onClose } = renderDrawer();

    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Delete node asks for confirmation before deleting", () => {
    renderDrawer();

    fireEvent.click(screen.getByRole("button", { name: "Delete node" }));

    expect(screen.getByTestId("confirm-dialog")).toHaveTextContent(
      "Delete Node",
    );
  });

  it("read-only users get a named Close button and no Delete button", () => {
    mockIsReadOnly = true;
    renderDrawer();

    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Delete node" }),
    ).not.toBeInTheDocument();
  });
});
