import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import AnimatedEdge from "../AnimatedEdge";
import { NODE_X_OFFSET } from "../../../utils/constants";

// ---------------------------------------------------------------------------
// Mocks — isolate AnimatedEdge.handleNodeSelect (TH-4549 deferred Agent add)
// ---------------------------------------------------------------------------
const mockSetCenter = vi.fn();
const mockGetZoom = vi.fn(() => 1);
const mockGetNode = vi.fn();

vi.mock("@xyflow/react", () => ({
  useReactFlow: () => ({
    setCenter: mockSetCenter,
    getZoom: mockGetZoom,
    getNode: mockGetNode,
  }),
  getSmoothStepPath: () => ["M0,0 L100,0", 50, 0],
  // eslint-disable-next-line react/prop-types
  EdgeLabelRenderer: ({ children }) => <div>{children}</div>,
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

const mockAddNode = vi.fn();
vi.mock("../../hooks/useAddNodeOptimistic", () => ({
  default: () => ({ addNode: mockAddNode }),
}));

vi.mock("../../saveDraftContext", () => ({
  useSaveDraftContext: () => ({ ensureDraft: vi.fn() }),
}));

vi.mock("../../../hooks/useCanEditAgent", () => ({
  default: () => ({ isReadOnly: false }),
}));

vi.mock("../../../store", () => ({
  useAgentPlaygroundStore: {
    getState: vi.fn(() => ({ nodes: [], edges: [] })),
  },
  useAgentPlaygroundStoreShallow: (selector) =>
    selector({
      setGraphData: vi.fn(),
      onEdgesChange: vi.fn(),
      edgeExecutionStates: {},
    }),
  useWorkflowRunStoreShallow: (selector) => selector({ isRunning: false }),
}));

vi.mock("src/api/agent-playground/agent-playground", () => ({
  deleteConnectionApi: vi.fn(),
}));

vi.mock("src/components/svg-color", () => ({
  default: () => <span />,
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// Capture the popper's props so the test can drive onNodeSelect like a
// deferred "Add Agent node" would (after the hover chrome has unmounted).
let popperProps = null;
vi.mock("../../../components/NodeSelectionPopper", () => ({
  default: (props) => {
    popperProps = props;
    return <div data-testid="popper-stub" data-open={String(props.open)} />;
  },
}));

const theme = createTheme({
  palette: {
    green: { 500: "#2e7d32" },
    grey: { 400: "#bdbdbd", 600: "#757575" },
  },
});

const edgeProps = {
  id: "e1",
  source: "n1",
  sourceX: 0,
  sourceY: 0,
  targetX: 100,
  targetY: 0,
  sourcePosition: "right",
  targetPosition: "left",
  data: {},
};

const renderEdge = () =>
  render(
    <ThemeProvider theme={theme}>
      <svg>
        <AnimatedEdge {...edgeProps} />
      </svg>
    </ThemeProvider>,
  );

describe("AnimatedEdge.handleNodeSelect (TH-4549 deferred Agent add, PRD R-13)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    popperProps = null;
  });

  it("attaches to the source node when it still exists, even after hover chrome unmounted", async () => {
    mockGetNode.mockReturnValue({ id: "n1", position: { x: 10, y: 20 } });
    renderEdge();
    expect(popperProps).not.toBeNull();

    // Deferred add: the popper is closed (hover chrome gone) and the user
    // clicks "Add Agent node" in the setup dialog.
    popperProps.onClose();
    popperProps.onNodeSelect("agent", undefined);

    await waitFor(() =>
      expect(mockAddNode).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "agent",
          sourceNodeId: "n1",
          position: { x: 10 + NODE_X_OFFSET, y: 20 },
        }),
      ),
    );
    expect(mockSetCenter).toHaveBeenCalledWith(10 + NODE_X_OFFSET, 20, {
      duration: 800,
      zoom: 1,
    });
  });

  it("refuses to attach when the source node was deleted while the dialog was open", async () => {
    const { enqueueSnackbar } = await import("notistack");
    mockGetNode.mockReturnValue(undefined);
    renderEdge();

    popperProps.onClose();
    popperProps.onNodeSelect("agent", undefined);

    expect(mockAddNode).not.toHaveBeenCalled();
    expect(mockSetCenter).not.toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      expect.stringMatching(/no longer exists/),
      { variant: "warning" },
    );
  });

  it("keeps the popper mounted (dialog can outlive the hover chrome) while never showing it in preview", () => {
    mockGetNode.mockReturnValue({ id: "n1", position: { x: 0, y: 0 } });
    renderEdge();
    expect(screen.getByTestId("popper-stub")).toHaveAttribute(
      "data-open",
      "false",
    );
    expect(typeof popperProps.onNodeSelect).toBe("function");
  });
});
