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
vi.mock("../../../../store", () => ({
  useAgentPlaygroundStore: {
    getState: vi.fn(() => ({
      nodes: [],
      edges: [],
      currentAgent: { id: "g1", versionId: "v1" },
    })),
  },
  useAgentPlaygroundStoreShallow: () => mockSetGraphData,
}));

vi.mock("src/api/agent-playground/agent-playground", () => ({
  deleteNodeApi: vi.fn(),
}));

vi.mock("../../../../utils/versionPayloadUtils", () => ({
  buildDraftCreationPayload: vi.fn(),
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function makeProps(overrides = {}) {
  return {
    id: "n1",
    preview: false,
    isWorkflowRunning: false,
    setSelectedNode: vi.fn(),
    deleteNode: vi.fn(),
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("useBaseNodeActions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockAddNode.mockResolvedValue(true);
    mockEnsureDraft.mockResolvedValue("existing");
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
    });
  });
});

// ---------------------------------------------------------------------------
// Edge-case tests
// ---------------------------------------------------------------------------
describe("useBaseNodeActions — edge cases", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockAddNode.mockResolvedValue(true);
    mockEnsureDraft.mockResolvedValue("existing");
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
