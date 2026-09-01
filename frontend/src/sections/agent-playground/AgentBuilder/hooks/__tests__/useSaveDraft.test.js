import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import useSaveDraft from "../useSaveDraft";
import { useAgentPlaygroundStore } from "../../../store";
import logger from "src/utils/logger";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
const mockSaveDraftMutation = vi.fn();
const mockCreateVersionMutation = vi.fn();

vi.mock("../../../hooks/useCanEditAgent", () => ({
  default: () => ({ canEditAgent: true, isReadOnly: false }),
}));

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useSaveDraftVersion: () => ({ mutate: mockSaveDraftMutation }),
  useCreateVersion: () => ({ mutate: mockCreateVersionMutation }),
}));

vi.mock("../../../utils/versionPayloadUtils", () => ({
  buildVersionPayload: vi.fn((_nodes, _edges) => ({
    nodes: _nodes,
    edges: _edges,
  })),
}));

vi.mock("src/utils/logger", () => ({
  default: { error: vi.fn(), warn: vi.fn() },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function setStoreState(overrides = {}) {
  useAgentPlaygroundStore.setState({
    currentAgent: {
      id: "graph-1",
      version_id: "v-1",
      is_draft: true,
      ...overrides.currentAgent,
    },
    nodes: overrides.nodes ?? [{ id: "n1" }],
    edges: overrides.edges ?? [],
    ...overrides,
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("useSaveDraft", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgentPlaygroundStore.getState().reset();
  });

  it("returns a saveDraft function", () => {
    setStoreState();
    const { result } = renderHook(() => useSaveDraft());
    expect(typeof result.current.saveDraft).toBe("function");
  });

  // ---- Draft path (no-op) ----
  it("is a no-op when version is a draft (content persisted via individual CRUD calls)", () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: true },
    });
    const { result } = renderHook(() => useSaveDraft());

    act(() => result.current.saveDraft());

    expect(mockSaveDraftMutation).not.toHaveBeenCalled();
    expect(mockCreateVersionMutation).not.toHaveBeenCalled();
  });

  it("does not call any mutation when draft with onError", () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: true },
    });
    const onError = vi.fn();
    const { result } = renderHook(() => useSaveDraft());

    act(() => result.current.saveDraft({ onError }));

    expect(mockSaveDraftMutation).not.toHaveBeenCalled();
    expect(mockCreateVersionMutation).not.toHaveBeenCalled();
  });

  // ---- Active path (POST then PUT) ----
  it("calls createVersionMutation (POST) when version is active", () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: false },
    });
    const { result } = renderHook(() => useSaveDraft());

    act(() => result.current.saveDraft());

    expect(mockCreateVersionMutation).toHaveBeenCalledOnce();
    expect(mockSaveDraftMutation).not.toHaveBeenCalled();
  });

  it("calls onCreateDraft callback on successful POST", () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: false },
    });
    const onCreateDraft = vi.fn();
    const { result } = renderHook(() => useSaveDraft({ onCreateDraft }));

    act(() => result.current.saveDraft());

    // Simulate onSuccess callback
    const mutationCall = mockCreateVersionMutation.mock.calls[0];
    const onSuccess = mutationCall[1].onSuccess;
    const fakeRes = { data: { result: { version_id: "v-new" } } };

    act(() => onSuccess(fakeRes));

    expect(onCreateDraft).toHaveBeenCalledWith({ version_id: "v-new" });
  });

  // ---- Concurrent save queuing ----
  it("queues a save when POST is already in-flight", () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: false },
    });
    const { result } = renderHook(() => useSaveDraft());

    // First save → starts POST
    act(() => result.current.saveDraft());
    expect(mockCreateVersionMutation).toHaveBeenCalledTimes(1);

    // Second save while POST in-flight → should be queued, not a second POST
    act(() => result.current.saveDraft());
    expect(mockCreateVersionMutation).toHaveBeenCalledTimes(1);
  });

  it("clears pending flag after POST completes (no PUT flush needed)", () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: false },
    });
    const { result } = renderHook(() => useSaveDraft());

    // First save
    act(() => result.current.saveDraft());
    // Queue a second save
    act(() => result.current.saveDraft());

    // Simulate POST success — store should now have new versionId
    useAgentPlaygroundStore.setState({
      currentAgent: { id: "g1", version_id: "v-new", is_draft: true },
    });

    const onSuccess = mockCreateVersionMutation.mock.calls[0][1].onSuccess;
    act(() => onSuccess({ data: { result: { version_id: "v-new" } } }));

    // Draft content is persisted via individual CRUD calls, so no PUT is needed
    expect(mockSaveDraftMutation).not.toHaveBeenCalled();
  });

  // ---- Error handling ----
  it("clears flags and calls callerOnError on POST error", () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: false },
    });
    const onError = vi.fn();
    const { result } = renderHook(() => useSaveDraft());

    act(() => result.current.saveDraft({ onError }));

    const onErrorCb = mockCreateVersionMutation.mock.calls[0][1].onError;
    act(() => onErrorCb());

    expect(onError).toHaveBeenCalledOnce();

    // After error, a new save should start a fresh POST (not be queued)
    act(() => result.current.saveDraft());
    expect(mockCreateVersionMutation).toHaveBeenCalledTimes(2);
  });

  // ---- No-op guards ----
  it("does nothing when graphId is missing", () => {
    useAgentPlaygroundStore.setState({
      currentAgent: { id: null, version_id: "v1", is_draft: true },
      nodes: [],
      edges: [],
    });
    const { result } = renderHook(() => useSaveDraft());

    act(() => result.current.saveDraft());

    expect(mockSaveDraftMutation).not.toHaveBeenCalled();
    expect(mockCreateVersionMutation).not.toHaveBeenCalled();
  });

  it("does nothing when versionId is missing", () => {
    useAgentPlaygroundStore.setState({
      currentAgent: { id: "g1", version_id: null, is_draft: true },
      nodes: [],
      edges: [],
    });
    const { result } = renderHook(() => useSaveDraft());

    act(() => result.current.saveDraft());

    expect(mockSaveDraftMutation).not.toHaveBeenCalled();
    expect(mockCreateVersionMutation).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Edge-case tests
// ---------------------------------------------------------------------------
describe("useSaveDraft – edge cases", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgentPlaygroundStore.getState().reset();
  });

  // ---- onCreateDraft must NOT fire when POST fails ----
  it("does not call onCreateDraft when POST errors", () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: false },
    });
    const onCreateDraft = vi.fn();
    const onError = vi.fn();
    const { result } = renderHook(() => useSaveDraft({ onCreateDraft }));

    act(() => result.current.saveDraft({ onError }));

    // Simulate POST failure
    const onErrorCb = mockCreateVersionMutation.mock.calls[0][1].onError;
    act(() => onErrorCb());

    expect(onCreateDraft).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledOnce();
  });

  // ---- Draft path is a no-op, so no PUT error path ----
  it("does not call saveDraftMutation for isDraft=true (no PUT error possible)", () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: true },
    });
    const onError = vi.fn();
    const { result } = renderHook(() => useSaveDraft());

    act(() => result.current.saveDraft({ onError }));

    // Draft saves are no-ops — no mutation is called
    expect(mockSaveDraftMutation).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  // ---- Triple-call: queued saves are cleared, no PUT flush ----
  it("clears pending flag without PUT when saveDraft is called 3 times during in-flight POST", () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: false },
      nodes: [{ id: "n-first" }],
      edges: [],
    });
    const onCreateDraft = vi.fn();
    const { result } = renderHook(() => useSaveDraft({ onCreateDraft }));

    // 1st call → starts POST
    act(() => result.current.saveDraft());
    expect(mockCreateVersionMutation).toHaveBeenCalledTimes(1);

    // 2nd call → queued (pendingSaveRef = true)
    act(() => result.current.saveDraft());
    expect(mockCreateVersionMutation).toHaveBeenCalledTimes(1);

    // 3rd call → still queued (pendingSaveRef already true, no extra POST)
    act(() => result.current.saveDraft());
    expect(mockCreateVersionMutation).toHaveBeenCalledTimes(1);

    // Simulate store being updated to the latest state BEFORE POST resolves
    useAgentPlaygroundStore.setState({
      currentAgent: { id: "g1", version_id: "v-new", is_draft: true },
      nodes: [{ id: "n-latest" }],
      edges: [{ id: "e-latest" }],
    });

    // Resolve the POST
    const onSuccess = mockCreateVersionMutation.mock.calls[0][1].onSuccess;
    act(() => onSuccess({ data: { result: { version_id: "v-new" } } }));

    expect(onCreateDraft).toHaveBeenCalledWith({ version_id: "v-new" });

    // Draft content is persisted via individual CRUD calls — no PUT flush needed
    expect(mockSaveDraftMutation).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// ensureDraft (debounced)
// ---------------------------------------------------------------------------
describe("useSaveDraft – ensureDraft", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    useAgentPlaygroundStore.getState().reset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("resolves 'created' when onCreateDraft succeeds", async () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: false },
    });
    const mockOnCreateDraft = vi.fn();

    const { result } = renderHook(() =>
      useSaveDraft({ onCreateDraft: mockOnCreateDraft }),
    );

    let draftPromise;
    await act(async () => {
      draftPromise = result.current.ensureDraft();
    });

    // Advance past debounce timer
    await act(async () => {
      vi.advanceTimersByTime(200);
    });

    let draftResult;
    await act(async () => {
      const mutationCall = mockCreateVersionMutation.mock.calls[0];
      mutationCall[1].onSuccess({ data: { result: { version_id: "v-new" } } });
      draftResult = await draftPromise;
    });

    expect(draftResult).toBe("created");
    expect(mockOnCreateDraft).toHaveBeenCalledWith({ version_id: "v-new" });
  });

  it("resolves false when onCreateDraft throws", async () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: false },
    });
    const mockOnCreateDraft = vi.fn(() => {
      throw new Error("onCreateDraft boom");
    });

    const { result } = renderHook(() =>
      useSaveDraft({ onCreateDraft: mockOnCreateDraft }),
    );

    let draftPromise;
    await act(async () => {
      draftPromise = result.current.ensureDraft();
    });

    await act(async () => {
      vi.advanceTimersByTime(200);
    });

    let draftResult;
    await act(async () => {
      const mutationCall = mockCreateVersionMutation.mock.calls[0];
      mutationCall[1].onSuccess({ data: { result: { version_id: "v-new" } } });
      draftResult = await draftPromise;
    });

    expect(draftResult).toBe(false);
  });

  it("logs error via logger when onCreateDraft throws", async () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: false },
    });
    const onCreateDraftError = new Error("onCreateDraft boom");
    const mockOnCreateDraft = vi.fn(() => {
      throw onCreateDraftError;
    });

    const { result } = renderHook(() =>
      useSaveDraft({ onCreateDraft: mockOnCreateDraft }),
    );

    let draftPromise;
    await act(async () => {
      draftPromise = result.current.ensureDraft();
    });

    await act(async () => {
      vi.advanceTimersByTime(200);
    });

    await act(async () => {
      const mutationCall = mockCreateVersionMutation.mock.calls[0];
      mutationCall[1].onSuccess({ data: { result: { version_id: "v-new" } } });
      await draftPromise;
    });

    expect(logger.error).toHaveBeenCalledWith(
      "[useSaveDraft] onCreateDraft failed after draft creation",
      onCreateDraftError,
    );
  });

  it("resolves true immediately when already a draft", async () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: true },
    });
    const { result } = renderHook(() => useSaveDraft());

    let draftResult;
    await act(async () => {
      draftResult = await result.current.ensureDraft();
    });

    expect(draftResult).toBe(true);
    expect(mockCreateVersionMutation).not.toHaveBeenCalled();
  });

  it("resolves false when graphId is missing on active version", async () => {
    useAgentPlaygroundStore.setState({
      currentAgent: { id: null, version_id: "v1", is_draft: false },
      nodes: [],
      edges: [],
    });
    const { result } = renderHook(() => useSaveDraft());

    let draftResult;
    await act(async () => {
      draftResult = await result.current.ensureDraft();
    });

    expect(draftResult).toBe(false);
    expect(mockCreateVersionMutation).not.toHaveBeenCalled();
  });

  it("resolves false when createVersionMutation errors", async () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: false },
    });
    const { result } = renderHook(() => useSaveDraft());

    let draftPromise;
    await act(async () => {
      draftPromise = result.current.ensureDraft();
    });

    await act(async () => {
      vi.advanceTimersByTime(200);
    });

    let draftResult;
    await act(async () => {
      const mutationCall = mockCreateVersionMutation.mock.calls[0];
      mutationCall[1].onError();
      draftResult = await draftPromise;
    });

    expect(draftResult).toBe(false);
  });

  it("sets isDraft optimistically before debounce fires", async () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: false },
    });
    const { result } = renderHook(() => useSaveDraft());

    await act(async () => {
      result.current.ensureDraft();
    });

    // isDraft should be true before debounce timer fires
    const { currentAgent } = useAgentPlaygroundStore.getState();
    expect(currentAgent.is_draft).toBe(true);
    expect(currentAgent.version_status).toBe("draft");
    expect(mockCreateVersionMutation).not.toHaveBeenCalled();
  });

  it("reverts isDraft on POST error", async () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: false },
    });
    const { result } = renderHook(() => useSaveDraft());

    let draftPromise;
    await act(async () => {
      draftPromise = result.current.ensureDraft();
    });

    expect(useAgentPlaygroundStore.getState().currentAgent.is_draft).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(200);
    });

    await act(async () => {
      const mutationCall = mockCreateVersionMutation.mock.calls[0];
      mutationCall[1].onError();
      await draftPromise;
    });

    const { currentAgent } = useAgentPlaygroundStore.getState();
    expect(currentAgent.is_draft).toBe(false);
    expect(currentAgent.version_status).toBe("active");
  });

  it("debounce batches rapid ensureDraft calls into single POST", async () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: false },
    });
    const { result } = renderHook(() => useSaveDraft());

    let firstPromise;
    let secondPromise;
    await act(async () => {
      firstPromise = result.current.ensureDraft();
      secondPromise = result.current.ensureDraft();
    });

    // No POST yet — debounce timer not fired
    expect(mockCreateVersionMutation).not.toHaveBeenCalled();

    // Advance past debounce
    await act(async () => {
      vi.advanceTimersByTime(200);
    });

    // Single POST fired
    expect(mockCreateVersionMutation).toHaveBeenCalledTimes(1);

    let firstResult;
    let secondResult;
    await act(async () => {
      const mutationCall = mockCreateVersionMutation.mock.calls[0];
      mutationCall[1].onSuccess({ data: { result: { version_id: "v-new" } } });
      firstResult = await firstPromise;
      secondResult = await secondPromise;
    });

    // Both callers get "created" — both edits were in the POST snapshot
    expect(firstResult).toBe("created");
    expect(secondResult).toBe("created");
  });

  it("syncs backend-assigned edge IDs from POST response", async () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: false },
      nodes: [{ id: "n1" }, { id: "n2" }],
      edges: [{ id: "frontend-edge-1", source: "n1", target: "n2" }],
    });
    const { result } = renderHook(() => useSaveDraft());

    let draftPromise;
    await act(async () => {
      draftPromise = result.current.ensureDraft();
    });

    await act(async () => {
      vi.advanceTimersByTime(200);
    });

    // Grab the remapped source/target that were sent in the POST
    const storeEdges = useAgentPlaygroundStore.getState().edges;
    const remappedSource = storeEdges[0].source;
    const remappedTarget = storeEdges[0].target;

    await act(async () => {
      const mutationCall = mockCreateVersionMutation.mock.calls[0];
      mutationCall[1].onSuccess({
        data: {
          result: {
            version_id: "v-new",
            nodeConnections: [
              {
                id: "backend-edge-99",
                sourceNodeId: remappedSource,
                targetNodeId: remappedTarget,
              },
            ],
          },
        },
      });
      await draftPromise;
    });

    // Edge ID should now match the backend-assigned ID
    const { edges } = useAgentPlaygroundStore.getState();
    expect(edges[0].id).toBe("backend-edge-99");
    expect(edges[0].source).toBe(remappedSource);
    expect(edges[0].target).toBe(remappedTarget);
  });

  it("syncs backend-assigned edge IDs from snake_case POST response", async () => {
    setStoreState({
      currentAgent: { id: "g1", version_id: "v1", is_draft: false },
      nodes: [{ id: "n1" }, { id: "n2" }],
      edges: [{ id: "frontend-edge-1", source: "n1", target: "n2" }],
    });
    const { result } = renderHook(() => useSaveDraft());

    let draftPromise;
    await act(async () => {
      draftPromise = result.current.ensureDraft();
    });

    await act(async () => {
      vi.advanceTimersByTime(200);
    });

    const storeEdges = useAgentPlaygroundStore.getState().edges;
    const remappedSource = storeEdges[0].source;
    const remappedTarget = storeEdges[0].target;

    await act(async () => {
      const mutationCall = mockCreateVersionMutation.mock.calls[0];
      mutationCall[1].onSuccess({
        data: {
          result: {
            version_id: "v-new",
            node_connections: [
              {
                id: "backend-edge-snake",
                source_node_id: remappedSource,
                target_node_id: remappedTarget,
              },
            ],
          },
        },
      });
      await draftPromise;
    });

    const { edges } = useAgentPlaygroundStore.getState();
    expect(edges[0].id).toBe("backend-edge-snake");
    expect(edges[0].source).toBe(remappedSource);
    expect(edges[0].target).toBe(remappedTarget);
  });
});
