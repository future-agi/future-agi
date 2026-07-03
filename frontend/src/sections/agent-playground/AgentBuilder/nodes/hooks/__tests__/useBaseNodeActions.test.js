import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import useBaseNodeActions from "../useBaseNodeActions";
import { NODE_X_OFFSET } from "../../../../utils/constants";

// ---------------------------------------------------------------------------
// Mocks
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
}));

const mockAddNode = vi.fn();
vi.mock("../../../hooks/useAddNodeOptimistic", () => ({
  default: () => ({ addNode: mockAddNode }),
}));

const mockEnsureDraft = vi.fn();
vi.mock("../../../saveDraftContext", () => ({
  useSaveDraftContext: () => ({ ensureDraft: mockEnsureDraft }),
}));

const mockSetGraphData = vi.fn();
const apiMocks = vi.hoisted(() => ({
  deleteNodeApi: vi.fn(),
}));
const storeMocks = vi.hoisted(() => ({
  getState: vi.fn(),
}));
const workflowMocks = vi.hoisted(() => ({
  getState: vi.fn(),
}));
vi.mock("../../../../store", () => ({
  useAgentPlaygroundStore: {
    getState: storeMocks.getState,
  },
  useAgentPlaygroundStoreShallow: () => mockSetGraphData,
  useWorkflowRunStore: {
    getState: workflowMocks.getState,
  },
}));

vi.mock("src/api/agent-playground/agent-playground", () => ({
  deleteNodeApi: apiMocks.deleteNodeApi,
}));

vi.mock("../../../../utils/versionPayloadUtils", () => ({
  buildDraftCreationPayload: vi.fn(),
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

let baseStoreState;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function makeProps(overrides = {}) {
  return {
    id: "n1",
    preview: false,
    isWorkflowRunning: false,
    isRunning: false,
    setSelectedNode: vi.fn(),
    deleteNode: vi.fn((nodeId) => {
      baseStoreState = {
        ...baseStoreState,
        nodes: baseStoreState.nodes.filter((n) => n.id !== nodeId),
        edges: baseStoreState.edges.filter(
          (edge) => edge.source !== nodeId && edge.target !== nodeId,
        ),
      };
    }),
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("Unit: useBaseNodeActions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockAddNode.mockResolvedValue(true);
    mockEnsureDraft.mockResolvedValue("existing");
    apiMocks.deleteNodeApi.mockResolvedValue({});
    baseStoreState = {
      nodes: [{ id: "n1" }],
      edges: [],
      currentAgent: { id: "g1", version_id: "v1" },
      nodeExecutionStates: {},
    };
    storeMocks.getState.mockImplementation(() => baseStoreState);
    workflowMocks.getState.mockReturnValue({ isRunning: false });
  });

  // ---- handleNodeClick ----
  describe("handleNodeClick", () => {
    it("selects node when clicked", () => {
      const props = makeProps();
      const nodeData = { id: "n1", position: { x: 0, y: 0 } };
      mockGetNode.mockReturnValue(nodeData);

      const { result } = renderHook(() => useBaseNodeActions(props));

      act(() => result.current.handleNodeClick());

      expect(props.setSelectedNode).toHaveBeenCalledWith(nodeData);
    });

    it("does nothing in preview mode", () => {
      const props = makeProps({ preview: true });
      const { result } = renderHook(() => useBaseNodeActions(props));

      act(() => result.current.handleNodeClick());

      expect(props.setSelectedNode).not.toHaveBeenCalled();
    });

    it("does nothing when workflow is running", () => {
      const props = makeProps({ isWorkflowRunning: true });
      const { result } = renderHook(() => useBaseNodeActions(props));

      act(() => result.current.handleNodeClick());

      expect(props.setSelectedNode).not.toHaveBeenCalled();
    });

    it("does nothing when getNode returns null", () => {
      const props = makeProps();
      mockGetNode.mockReturnValue(null);

      const { result } = renderHook(() => useBaseNodeActions(props));

      act(() => result.current.handleNodeClick());

      expect(props.setSelectedNode).not.toHaveBeenCalled();
    });
  });

  // ---- handleAddClick ----
  describe("handleAddClick", () => {
    it("opens popper on add click", () => {
      const props = makeProps();
      const { result } = renderHook(() => useBaseNodeActions(props));

      expect(result.current.popperOpen).toBe(false);

      act(() => {
        result.current.handleAddClick({ stopPropagation: vi.fn() });
      });

      expect(result.current.popperOpen).toBe(true);
    });

    it("stops event propagation", () => {
      const props = makeProps();
      const stopPropagation = vi.fn();
      const { result } = renderHook(() => useBaseNodeActions(props));

      act(() => {
        result.current.handleAddClick({ stopPropagation });
      });

      expect(stopPropagation).toHaveBeenCalled();
    });

    it("does nothing in preview mode", () => {
      const props = makeProps({ preview: true });
      const { result } = renderHook(() => useBaseNodeActions(props));

      act(() => {
        result.current.handleAddClick({ stopPropagation: vi.fn() });
      });

      expect(result.current.popperOpen).toBe(false);
    });
  });

  // ---- handlePopperClose ----
  describe("handlePopperClose", () => {
    it("closes the popper", () => {
      const props = makeProps();
      const { result } = renderHook(() => useBaseNodeActions(props));

      act(() => {
        result.current.handleAddClick({ stopPropagation: vi.fn() });
      });
      expect(result.current.popperOpen).toBe(true);

      act(() => result.current.handlePopperClose());
      expect(result.current.popperOpen).toBe(false);
    });
  });

  // ---- handleNodeSelect ----
  describe("handleNodeSelect", () => {
    it("adds node at NODE_X_OFFSET right offset and centers view", async () => {
      const props = makeProps();
      const currentNode = { position: { x: 100, y: 200 } };
      mockGetNode.mockReturnValue(currentNode);

      const { result } = renderHook(() => useBaseNodeActions(props));

      await act(async () => {
        result.current.handleNodeSelect("llm_prompt", "tpl-1");
      });

      expect(mockAddNode).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "llm_prompt",
          position: { x: 100 + NODE_X_OFFSET, y: 200 },
          sourceNodeId: "n1",
          node_template_id: "tpl-1",
        }),
      );
      expect(mockSetCenter).toHaveBeenCalledWith(
        100 + NODE_X_OFFSET + 300,
        200,
        { duration: 800, zoom: 1 },
      );
    });

    it("adds node without position when getNode returns null", async () => {
      const props = makeProps();
      mockGetNode.mockReturnValue(null);

      const { result } = renderHook(() => useBaseNodeActions(props));

      await act(async () => {
        result.current.handleNodeSelect("agent", null);
      });

      expect(mockAddNode).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "agent",
          position: undefined,
          sourceNodeId: "n1",
          node_template_id: null,
        }),
      );
      expect(mockSetCenter).not.toHaveBeenCalled();
    });

    it("closes popper after selection", async () => {
      const props = makeProps();
      mockGetNode.mockReturnValue({ position: { x: 0, y: 0 } });

      const { result } = renderHook(() => useBaseNodeActions(props));

      act(() => {
        result.current.handleAddClick({ stopPropagation: vi.fn() });
      });
      expect(result.current.popperOpen).toBe(true);

      await act(async () => {
        result.current.handleNodeSelect("llm_prompt");
      });
      expect(result.current.popperOpen).toBe(false);
    });

    it("does nothing in preview mode", async () => {
      const props = makeProps({ preview: true });
      const { result } = renderHook(() => useBaseNodeActions(props));

      await act(async () => {
        result.current.handleNodeSelect("llm_prompt");
      });

      expect(mockAddNode).not.toHaveBeenCalled();
    });

    it("does nothing when workflow is running", async () => {
      const props = makeProps({ isWorkflowRunning: true });
      const { result } = renderHook(() => useBaseNodeActions(props));

      await act(async () => {
        result.current.handleNodeSelect("llm_prompt");
      });

      expect(mockAddNode).not.toHaveBeenCalled();
    });
  });

  // ---- handleDeleteClick ----
  describe("handleDeleteClick", () => {
    it("deletes node when ensureDraft returns existing draft", async () => {
      const props = makeProps();
      mockEnsureDraft.mockResolvedValue("existing");

      const { result } = renderHook(() => useBaseNodeActions(props));

      await act(async () => {
        result.current.handleDeleteClick({ stopPropagation: vi.fn() });
      });

      expect(mockEnsureDraft).toHaveBeenCalled();
      expect(props.deleteNode).toHaveBeenCalledWith("n1");
      expect(apiMocks.deleteNodeApi).toHaveBeenCalledWith({
        graphId: "g1",
        versionId: "v1",
        nodeId: "n1",
      });
    });

    it("stops event propagation", async () => {
      const props = makeProps();
      const stopPropagation = vi.fn();
      const { result } = renderHook(() => useBaseNodeActions(props));

      await act(async () => {
        result.current.handleDeleteClick({ stopPropagation });
      });

      expect(stopPropagation).toHaveBeenCalled();
    });

    it("does nothing in preview mode", async () => {
      const props = makeProps({ preview: true });
      const { result } = renderHook(() => useBaseNodeActions(props));

      await act(async () => {
        result.current.handleDeleteClick({ stopPropagation: vi.fn() });
      });

      expect(props.deleteNode).not.toHaveBeenCalled();
    });

    it("does nothing when workflow is running", async () => {
      const props = makeProps({ isWorkflowRunning: true });
      const { result } = renderHook(() => useBaseNodeActions(props));

      await act(async () => {
        result.current.handleDeleteClick({ stopPropagation: vi.fn() });
      });

      expect(props.deleteNode).not.toHaveBeenCalled();
      expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
    });

    it("does nothing while the node is running", async () => {
      const props = makeProps({ isRunning: true });
      const stopPropagation = vi.fn();
      const { result } = renderHook(() => useBaseNodeActions(props));

      await act(async () => {
        result.current.handleDeleteClick({ stopPropagation });
      });

      expect(stopPropagation).toHaveBeenCalled();
      expect(props.deleteNode).not.toHaveBeenCalled();
      expect(mockEnsureDraft).not.toHaveBeenCalled();
      expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
    });

    it("does nothing when the live node state is running even if props are stale", async () => {
      storeMocks.getState.mockReturnValue({
        nodes: [{ id: "n1" }],
        edges: [],
        currentAgent: { id: "g1", version_id: "v1" },
        nodeExecutionStates: { n1: "running" },
      });
      const props = makeProps({ isRunning: false });
      const stopPropagation = vi.fn();
      const { result } = renderHook(() => useBaseNodeActions(props));

      await act(async () => {
        result.current.handleDeleteClick({ stopPropagation });
      });

      expect(stopPropagation).toHaveBeenCalled();
      expect(props.deleteNode).not.toHaveBeenCalled();
      expect(mockEnsureDraft).not.toHaveBeenCalled();
      expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
    });

    it("does not draft or call the API when the store rejects the optimistic delete", async () => {
      storeMocks.getState.mockReturnValue({
        nodes: [{ id: "n1" }],
        edges: [],
        currentAgent: { id: "g1", version_id: "v1" },
        nodeExecutionStates: {},
      });
      const props = makeProps({
        deleteNode: vi.fn(),
      });
      const { result } = renderHook(() => useBaseNodeActions(props));

      await act(async () => {
        result.current.handleDeleteClick({ stopPropagation: vi.fn() });
      });

      expect(props.deleteNode).toHaveBeenCalledWith("n1");
      expect(mockEnsureDraft).not.toHaveBeenCalled();
      expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
      expect(mockSetGraphData).not.toHaveBeenCalled();
    });

    it("rolls back and skips the API when draft creation declines the delete", async () => {
      let storeState = {
        nodes: [{ id: "n1" }],
        edges: [{ id: "e1", source: "n1", target: "n2" }],
        currentAgent: { id: "g1", version_id: "v1" },
        nodeExecutionStates: {},
      };
      storeMocks.getState.mockImplementation(() => storeState);
      const props = makeProps({
        deleteNode: vi.fn(() => {
          storeState = {
            ...storeState,
            nodes: [],
            edges: [],
          };
        }),
      });
      mockEnsureDraft.mockResolvedValue(false);

      const { result } = renderHook(() => useBaseNodeActions(props));

      await act(async () => {
        result.current.handleDeleteClick({ stopPropagation: vi.fn() });
      });

      expect(props.deleteNode).toHaveBeenCalledWith("n1");
      expect(mockEnsureDraft).toHaveBeenCalledWith({ skipDirtyCheck: true });
      expect(mockSetGraphData).toHaveBeenCalledWith(
        [{ id: "n1" }],
        [{ id: "e1", source: "n1", target: "n2" }],
      );
      expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
    });

    it("restores selected node context when a canvas delete rollback targets the selected node", async () => {
      const selectedNode = { id: "n1", data: { label: "Selected node" } };
      let storeState = {
        nodes: [selectedNode],
        edges: [{ id: "e1", source: "n1", target: "n2" }],
        selectedNode,
        currentAgent: { id: "g1", version_id: "v1" },
        nodeExecutionStates: {},
      };
      storeMocks.getState.mockImplementation(() => storeState);
      const props = makeProps({
        setSelectedNode: vi.fn(),
        deleteNode: vi.fn(() => {
          storeState = {
            ...storeState,
            nodes: [],
            edges: [],
            selectedNode: null,
          };
        }),
      });
      mockEnsureDraft.mockResolvedValue(false);

      const { result } = renderHook(() => useBaseNodeActions(props));

      await act(async () => {
        result.current.handleDeleteClick({ stopPropagation: vi.fn() });
      });

      expect(mockSetGraphData).toHaveBeenCalledWith(
        [selectedNode],
        [{ id: "e1", source: "n1", target: "n2" }],
      );
      expect(props.setSelectedNode).toHaveBeenCalledWith(selectedNode);
      expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
    });

    it("rolls back and skips the API if workflow running starts before delete persists", async () => {
      let storeState = {
        nodes: [{ id: "n1" }],
        edges: [{ id: "e1", source: "n1", target: "n2" }],
        currentAgent: { id: "g1", version_id: "v1" },
        nodeExecutionStates: {},
      };
      storeMocks.getState.mockImplementation(() => storeState);
      const props = makeProps({
        deleteNode: vi.fn(() => {
          storeState = {
            ...storeState,
            nodes: [],
            edges: [],
          };
        }),
      });
      mockEnsureDraft.mockImplementation(async () => {
        workflowMocks.getState.mockReturnValue({ isRunning: true });
        return "existing";
      });

      const { result } = renderHook(() => useBaseNodeActions(props));

      await act(async () => {
        result.current.handleDeleteClick({ stopPropagation: vi.fn() });
      });

      expect(props.deleteNode).toHaveBeenCalledWith("n1");
      expect(mockSetGraphData).toHaveBeenCalledWith(
        [{ id: "n1" }],
        [{ id: "e1", source: "n1", target: "n2" }],
      );
      expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
    });

    it("rolls back and skips the API if the node starts running before delete persists", async () => {
      let storeState = {
        nodes: [{ id: "n1" }],
        edges: [{ id: "e1", source: "n1", target: "n2" }],
        currentAgent: { id: "g1", version_id: "v1" },
        nodeExecutionStates: {},
      };
      storeMocks.getState.mockImplementation(() => storeState);
      const props = makeProps({
        deleteNode: vi.fn(() => {
          storeState = {
            ...storeState,
            nodes: [],
            edges: [],
          };
        }),
      });
      mockEnsureDraft.mockImplementation(async () => {
        storeState = {
          ...storeState,
          nodeExecutionStates: { n1: "running" },
        };
        return "existing";
      });

      const { result } = renderHook(() => useBaseNodeActions(props));

      await act(async () => {
        result.current.handleDeleteClick({ stopPropagation: vi.fn() });
      });

      expect(props.deleteNode).toHaveBeenCalledWith("n1");
      expect(mockSetGraphData).toHaveBeenCalledWith(
        [{ id: "n1" }],
        [{ id: "e1", source: "n1", target: "n2" }],
      );
      expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
    });
  });
});

// ---------------------------------------------------------------------------
// Edge-case tests
// ---------------------------------------------------------------------------
describe("Unit: useBaseNodeActions — edge cases", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockAddNode.mockResolvedValue(true);
    mockEnsureDraft.mockResolvedValue("existing");
    apiMocks.deleteNodeApi.mockResolvedValue({});
    baseStoreState = {
      nodes: [{ id: "n1" }],
      edges: [],
      currentAgent: { id: "g1", version_id: "v1" },
      nodeExecutionStates: {},
    };
    storeMocks.getState.mockImplementation(() => baseStoreState);
    workflowMocks.getState.mockReturnValue({ isRunning: false });
  });

  it("handleNodeSelect rejects when getNode returns node without .position property", async () => {
    const props = makeProps();
    // getNode returns a node object that has no `position` property
    mockGetNode.mockReturnValue({ id: "n1" });

    const { result } = renderHook(() => useBaseNodeActions(props));

    // The source accesses currentNode.position.x without optional chaining,
    // so a missing position property causes a TypeError (async rejection)
    await expect(
      act(async () => {
        await result.current.handleNodeSelect("llm_prompt", "tpl-1");
      }),
    ).rejects.toThrow(TypeError);
  });

  it("handleNodeSelect passes zoom value 0 (falsy but valid) to setCenter", async () => {
    const props = makeProps();
    const currentNode = { position: { x: 50, y: 100 } };
    mockGetNode.mockReturnValue(currentNode);
    mockGetZoom.mockReturnValue(0);

    const { result } = renderHook(() => useBaseNodeActions(props));

    await act(async () => {
      result.current.handleNodeSelect("agent", null);
    });

    expect(mockAddNode).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "agent",
        position: { x: 50 + NODE_X_OFFSET, y: 100 },
        sourceNodeId: "n1",
        node_template_id: null,
      }),
    );
    // zoom: 0 should be passed as-is, not substituted with a default
    expect(mockSetCenter).toHaveBeenCalledWith(50 + NODE_X_OFFSET + 300, 100, {
      duration: 800,
      zoom: 0,
    });
  });
});
