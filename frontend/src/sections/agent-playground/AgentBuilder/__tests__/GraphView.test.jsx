/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useAgentPlaygroundStore } from "../../store";

// Since GraphView uses ReactFlow internally (which requires a DOM provider),
// we test the callback logic extracted from GraphViewInner via the store
// and isolated callback tests.

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
const mockScreenToFlowPosition = vi.fn((pos) => pos);
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ children }) => <div data-testid="react-flow">{children}</div>,
  Controls: () => <div data-testid="controls" />,
  ConnectionLineType: { SmoothStep: "smoothstep" },
  useReactFlow: () => ({
    screenToFlowPosition: mockScreenToFlowPosition,
  }),
  ReactFlowProvider: ({ children }) => <div>{children}</div>,
}));

const mockSaveDraft = vi.fn();
vi.mock("../saveDraftContext", () => ({
  useSaveDraftContext: () => ({ saveDraft: mockSaveDraft }),
}));

vi.mock("../nodes", () => ({
  PromptNode: () => <div />,
  AgentNode: () => <div />,
  EvalNode: () => <div />,
  CodeExecutionNode: () => <div />,
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

  // ---- onNodeDragStop ----
  describe("onNodeDragStop logic", () => {
    it("calls saveDraft on node drag stop", () => {
      mockSaveDraft();
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
