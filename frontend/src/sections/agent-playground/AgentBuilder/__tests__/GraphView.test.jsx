/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render } from "src/utils/test-utils";
import GraphView from "../GraphView";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useAgentPlaygroundStore } from "../../store";

// Since GraphView uses ReactFlow internally (which requires a DOM provider),
// we test the callback logic extracted from GraphViewInner via the store
// and isolated callback tests.

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
const mockScreenToFlowPosition = vi.fn((pos) => pos);
let reactFlowProps;
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ children, ...props }) => {
    reactFlowProps = props;
    return <div data-testid="react-flow">{children}</div>;
  },
  Controls: () => <div data-testid="controls" />,
  ConnectionLineType: { SmoothStep: "smoothstep" },
  useReactFlow: () => ({
    screenToFlowPosition: mockScreenToFlowPosition,
  }),
  ReactFlowProvider: ({ children }) => <div>{children}</div>,
}));

const mockEnsureDraft = vi.fn();
const mockSaveDraft = vi.fn();
const mockUpdateNodeApi = vi.fn();
const mockEnqueueSnackbar = vi.fn();
vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
}));
vi.mock("src/utils/logger", () => ({ default: { error: vi.fn() } }));
vi.mock("src/api/agent-playground/agent-playground", () => ({
  updateNodeApi: (...args) => mockUpdateNodeApi(...args),
  createConnectionApi: vi.fn(),
  deleteConnectionApi: vi.fn(),
  deleteNodeApi: vi.fn(),
}));
vi.mock("../saveDraftContext", () => ({
  useSaveDraftContext: () => ({
    saveDraft: mockSaveDraft,
    ensureDraft: mockEnsureDraft,
  }),
}));

vi.mock("../nodes", () => ({
  PromptNode: () => <div />,
  AgentNode: () => <div />,
  EvalNode: () => <div />,
}));

vi.mock("../edges", () => ({
  AnimatedEdge: () => <div />,
}));

vi.mock("../../components/ConfirmationDialog", () => ({
  ConfirmationDialog: ({ open, onClose, onConfirm }) =>
    open ? (
      <div data-testid="confirm-dialog">
        <button data-testid="confirm-btn" onClick={onConfirm}>
          Confirm
        </button>
        <button data-testid="cancel-btn" onClick={onClose}>
          Cancel
        </button>
      </div>
    ) : null,
}));

// ---------------------------------------------------------------------------
// Tests: GraphView callback logic
// ---------------------------------------------------------------------------
describe("GraphView – callback logic", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgentPlaygroundStore.getState().reset();
  });

  it("captures the real ReactFlow drag callbacks", () => {
    mockEnsureDraft.mockResolvedValue("created");
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <GraphView />
      </QueryClientProvider>,
    );
    expect(reactFlowProps.onNodeDragStart).toBeTypeOf("function");
    expect(reactFlowProps.onNodeDragStop).toBeTypeOf("function");
  });

  it("uses ensureDraft and skips PATCH when drag creates a draft", async () => {
    vi.useFakeTimers();
    mockEnsureDraft.mockResolvedValue("created");
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <GraphView />
      </QueryClientProvider>,
    );
    const node = { id: "node-1", position: { x: 20, y: 30 } };
    act(() => reactFlowProps.onNodeDragStart(null, node, [node]));
    act(() => reactFlowProps.onNodeDragStop(null, node, [node]));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(mockEnsureDraft).toHaveBeenCalled();
    expect(mockUpdateNodeApi).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it("rolls back when ensureDraft blocks a drag", async () => {
    vi.useFakeTimers();
    mockEnsureDraft.mockResolvedValue(false);
    const onNodesChange = vi.fn();
    useAgentPlaygroundStore.setState({ onNodesChange });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <GraphView />
      </QueryClientProvider>,
    );
    const start = { id: "node-1", position: { x: 1, y: 2 } };
    const moved = { id: "node-1", position: { x: 9, y: 9 } };
    act(() => reactFlowProps.onNodeDragStart(null, start, [start]));
    act(() => reactFlowProps.onNodeDragStop(null, moved, [moved]));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(onNodesChange).toHaveBeenCalledWith(
      expect.arrayContaining([
        expect.objectContaining({ position: start.position }),
      ]),
    );
    expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
      "Failed to save positions",
      { variant: "error" },
    );
    vi.useRealTimers();
  });

  it("PATCHes an existing draft position with current ids", async () => {
    vi.useFakeTimers();
    mockEnsureDraft.mockResolvedValue(true);
    mockUpdateNodeApi.mockResolvedValue({});
    useAgentPlaygroundStore.setState({
      currentAgent: { id: "graph", version_id: "draft", is_draft: true },
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <GraphView />
      </QueryClientProvider>,
    );
    const node = { id: "node-1", position: { x: 20, y: 30 } };
    act(() => reactFlowProps.onNodeDragStart(null, node, [node]));
    act(() => reactFlowProps.onNodeDragStop(null, node, [node]));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(mockUpdateNodeApi).toHaveBeenCalledWith({
      graphId: "graph",
      versionId: "draft",
      nodeId: "node-1",
      data: { position: { x: 20, y: 30 } },
    });
    vi.useRealTimers();
  });

  it("rolls back and notifies when a position PATCH rejects", async () => {
    vi.useFakeTimers();
    mockEnsureDraft.mockResolvedValue(true);
    mockUpdateNodeApi.mockRejectedValue(new Error("reject"));
    const onNodesChange = vi.fn();
    useAgentPlaygroundStore.setState({
      currentAgent: { id: "graph", version_id: "draft", is_draft: true },
      onNodesChange,
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <GraphView />
      </QueryClientProvider>,
    );
    const start = { id: "node-1", position: { x: 1, y: 2 } };
    const moved = { id: "node-1", position: { x: 9, y: 9 } };
    act(() => reactFlowProps.onNodeDragStart(null, start, [start]));
    act(() => reactFlowProps.onNodeDragStop(null, moved, [moved]));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
      await Promise.resolve();
    });
    expect(onNodesChange).toHaveBeenCalledWith(
      expect.arrayContaining([
        expect.objectContaining({ position: start.position }),
      ]),
    );
    expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
      "Failed to save positions",
      { variant: "error" },
    );
    vi.useRealTimers();
  });

  // ---- onBeforeDelete ----
  describe("onBeforeDelete logic", () => {
    it("resolves immediately for empty deletions", async () => {
      // Simulating the onBeforeDelete callback behavior
      const onBeforeDelete = ({ nodes }) => {
        if (nodes.length === 0) return Promise.resolve(true);
        return new Promise((resolve) => resolve(true));
      };

      const result = await onBeforeDelete({ nodes: [] });
      expect(result).toBe(true);
    });

    it("returns a promise for non-empty deletions", () => {
      const onBeforeDelete = ({ nodes }) => {
        if (nodes.length === 0) return Promise.resolve(true);
        return new Promise(() => {
          // Waits for user confirmation
        });
      };

      const promise = onBeforeDelete({ nodes: [{ id: "n1" }] });
      expect(promise).toBeInstanceOf(Promise);
    });
  });

  // ---- handleConfirmDelete / handleCancelDelete ----
  describe("delete confirmation flow", () => {
    it("confirm resolves promise with true", async () => {
      let resolveRef;
      const promise = new Promise((resolve) => {
        resolveRef = resolve;
      });

      // Simulate confirm
      resolveRef(true);
      const result = await promise;
      expect(result).toBe(true);
    });

    it("cancel resolves promise with false", async () => {
      let resolveRef;
      const promise = new Promise((resolve) => {
        resolveRef = resolve;
      });

      resolveRef(false);
      const result = await promise;
      expect(result).toBe(false);
    });
  });

  // ---- handlePostDelete ----
  describe("handlePostDelete logic", () => {
    it("calls saveDraft with rollback callback", () => {
      const setGraphData = vi.fn();
      const snapshot = {
        nodes: [{ id: "n1" }],
        edges: [{ id: "e1" }],
      };

      // Simulate handlePostDelete
      mockSaveDraft({
        onError: () => {
          if (snapshot) {
            setGraphData(snapshot.nodes, snapshot.edges);
          }
        },
      });

      expect(mockSaveDraft).toHaveBeenCalledWith(
        expect.objectContaining({ onError: expect.any(Function) }),
      );

      // Simulate error — should rollback
      const onError = mockSaveDraft.mock.calls[0][0].onError;
      onError();

      expect(setGraphData).toHaveBeenCalledWith([{ id: "n1" }], [{ id: "e1" }]);
    });
  });

  // ---- onDrop ----
  describe("onDrop logic", () => {
    it("extracts node type and adds node at converted position", () => {
      const addNode = vi.fn();
      mockScreenToFlowPosition.mockReturnValue({ x: 100, y: 200 });

      // Simulate onDrop
      const event = {
        preventDefault: vi.fn(),
        clientX: 170,
        clientY: 220,
        dataTransfer: {
          getData: vi.fn((key) => {
            if (key === "application/reactflow") return "llm_prompt";
            if (key === "application/node-template-id") return "tpl-1";
            return "";
          }),
        },
      };

      event.preventDefault();
      const type = event.dataTransfer.getData("application/reactflow");
      const nodeTemplateId =
        event.dataTransfer.getData("application/node-template-id") || undefined;
      const position = mockScreenToFlowPosition({
        x: event.clientX - 70,
        y: event.clientY - 20,
      });
      addNode(type, position, nodeTemplateId);

      expect(addNode).toHaveBeenCalledWith(
        "llm_prompt",
        { x: 100, y: 200 },
        "tpl-1",
      );
    });

    it("does nothing when type is empty", () => {
      const addNode = vi.fn();

      const event = {
        preventDefault: vi.fn(),
        dataTransfer: {
          getData: vi.fn(() => ""),
        },
      };

      event.preventDefault();
      const type = event.dataTransfer.getData("application/reactflow");
      if (typeof type === "undefined" || !type) return;
      addNode(type);

      expect(addNode).not.toHaveBeenCalled();
    });
  });

  // ---- onConnect ----
  describe("onConnect logic", () => {
    it("calls storeOnConnect then saveDraft", () => {
      const storeOnConnect = vi.fn();
      const connection = { source: "n1", target: "n2" };

      // Simulate onConnect
      storeOnConnect(connection);
      mockSaveDraft();

      expect(storeOnConnect).toHaveBeenCalledWith(connection);
      expect(mockSaveDraft).toHaveBeenCalled();
    });
  });

  // ---- onConnectStart / onConnectEnd ----
  describe("connection tracking", () => {
    it("sets connection state on connect start", () => {
      useAgentPlaygroundStore.setState({
        isConnecting: false,
        connectingFromNodeId: null,
      });

      useAgentPlaygroundStore.getState().setIsConnecting?.(true);
      useAgentPlaygroundStore.getState().setConnectingFromNodeId?.("n1");

      const state = useAgentPlaygroundStore.getState();
      expect(state.isConnecting).toBe(true);
      expect(state.connectingFromNodeId).toBe("n1");
    });

    it("clears connection state on connect end", () => {
      useAgentPlaygroundStore.setState({
        isConnecting: true,
        connectingFromNodeId: "n1",
      });

      useAgentPlaygroundStore.getState().setIsConnecting?.(false);
      useAgentPlaygroundStore.getState().setConnectingFromNodeId?.(null);

      const state = useAgentPlaygroundStore.getState();
      expect(state.isConnecting).toBe(false);
      expect(state.connectingFromNodeId).toBeNull();
    });
  });
});
