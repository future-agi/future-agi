import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import useAddNodeOptimistic from "../useAddNodeOptimistic";
import { useAgentPlaygroundStore } from "../../../store";
import { NODE_TYPES } from "../../../utils/constants";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
vi.mock("src/api/agent-playground/agent-playground", () => ({
  addNodeApi: vi.fn(),
}));

vi.mock("src/utils/logger", () => ({
  default: { error: vi.fn(), warn: vi.fn() },
}));

const mockEnqueueSnackbar = vi.fn();
vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
}));

const mockEnsureDraft = vi.fn();
vi.mock("../../saveDraftContext", () => ({
  useSaveDraftContext: () => ({ ensureDraft: mockEnsureDraft }),
}));

vi.mock("../../../utils/versionPayloadUtils", () => ({
  buildDraftCreationPayload: vi.fn(),
}));

// Re-import after mocks are set up
const { addNodeApi } =
  await import("src/api/agent-playground/agent-playground");
const logger = (await import("src/utils/logger")).default;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const mockAddOptimisticNode = vi.fn();
const mockRemoveOptimisticNode = vi.fn();
const mockComputeNewNodeData = vi.fn();
const mockSetSelectedNode = vi.fn();
const mockGetNodeById = vi.fn();

function setStoreState(overrides = {}) {
  useAgentPlaygroundStore.setState({
    addOptimisticNode: mockAddOptimisticNode,
    removeOptimisticNode: mockRemoveOptimisticNode,
    computeNewNodeData: mockComputeNewNodeData,
    setSelectedNode: mockSetSelectedNode,
    getNodeById: mockGetNodeById,
    currentAgent: {
      id: "graph-1",
      versionId: "v-1",
      isDraft: true,
      ...overrides.currentAgent,
    },
    ...overrides,
  });
}

const defaultPayload = {
  type: "llm_prompt",
  position: { x: 100, y: 200 },
  sourceNodeId: "source-1",
  node_template_id: "tmpl-1",
  name: "My Node",
  config: {},
};

const defaultOptimisticResult = {
  nodeId: "node-123",
  edgeId: "edge-456",
  position: { x: 100, y: 200 },
  ports: { input: [], output: [] },
  label: "My Node",
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("useAddNodeOptimistic", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgentPlaygroundStore.getState().reset();
    setStoreState();
    addNodeApi.mockResolvedValue({});
  });

  it("returns null and calls removeOptimisticNode when ensureDraft returns false (POST failed)", async () => {
    mockAddOptimisticNode.mockReturnValue(defaultOptimisticResult);
    mockGetNodeById.mockReturnValue({ id: "node-123", type: "llm_prompt" });
    mockEnsureDraft.mockResolvedValue(false);

    const { result } = renderHook(() => useAddNodeOptimistic());

    let returnValue;
    await act(async () => {
      returnValue = await result.current.addNode(defaultPayload);
    });

    expect(returnValue).toBeNull();
    // Optimistic edit was applied first, then rolled back on failure
    expect(mockAddOptimisticNode).toHaveBeenCalledWith(
      defaultPayload.type,
      defaultPayload.position,
      defaultPayload.sourceNodeId,
      defaultPayload.node_template_id,
      defaultPayload.name,
      defaultPayload.config,
    );
    expect(mockRemoveOptimisticNode).toHaveBeenCalledWith("node-123");
    expect(addNodeApi).not.toHaveBeenCalled();
  });

  it('returns { nodeId, position } when ensureDraft returns "created"', async () => {
    mockAddOptimisticNode.mockReturnValue(defaultOptimisticResult);
    mockGetNodeById.mockReturnValue({ id: "node-123", type: "llm_prompt" });
    mockEnsureDraft.mockResolvedValue("created");

    const { result } = renderHook(() => useAddNodeOptimistic());

    let returnValue;
    await act(async () => {
      returnValue = await result.current.addNode(defaultPayload);
    });

    // Optimistic edit applied, IDs remapped in store by ensureDraft — return optimistic nodeId/position
    expect(returnValue).toEqual({
      nodeId: "node-123",
      position: { x: 100, y: 200 },
    });
    expect(mockAddOptimisticNode).toHaveBeenCalled();
    // No individual API call — node was included in the draft creation POST
    expect(addNodeApi).not.toHaveBeenCalled();
  });

  it("returns null when addOptimisticNode returns null (before calling ensureDraft)", async () => {
    // addOptimisticNode returns null — bail out early
    mockAddOptimisticNode.mockReturnValue(null);
    mockEnsureDraft.mockResolvedValue("created");

    const { result } = renderHook(() => useAddNodeOptimistic());

    let returnValue;
    await act(async () => {
      returnValue = await result.current.addNode(defaultPayload);
    });

    expect(returnValue).toBeNull();
    expect(mockEnsureDraft).not.toHaveBeenCalled();
  });

  it("draft path: calls addOptimisticNode, fires addNodeApi, returns { nodeId, position }", async () => {
    mockEnsureDraft.mockResolvedValue("existing-draft");
    mockAddOptimisticNode.mockReturnValue({
      ...defaultOptimisticResult,
      config: { prompt_template_id: null, prompt_version_id: null },
    });
    mockGetNodeById.mockReturnValue({ id: "node-123", type: "llm_prompt" });

    const { result } = renderHook(() => useAddNodeOptimistic());

    let returnValue;
    await act(async () => {
      returnValue = await result.current.addNode(defaultPayload);
    });

    expect(mockAddOptimisticNode).toHaveBeenCalledWith(
      defaultPayload.type,
      defaultPayload.position,
      defaultPayload.sourceNodeId,
      defaultPayload.node_template_id,
      defaultPayload.name,
      defaultPayload.config,
    );
    expect(addNodeApi).toHaveBeenCalled();
    expect(addNodeApi).toHaveBeenCalledWith(
      expect.objectContaining({
        data: expect.objectContaining({
          prompt_template: expect.objectContaining({
            messages: [
              {
                id: expect.any(String),
                role: "system",
                content: [{ type: "text", text: "" }],
              },
              {
                id: expect.any(String),
                role: "user",
                content: [{ type: "text", text: "" }],
              },
            ],
          }),
        }),
      }),
    );
    expect(returnValue).toEqual({
      nodeId: "node-123",
      position: { x: 100, y: 200 },
    });
  });

  it("draft path: creates code nodes with store-computed defaults", async () => {
    const codeConfig = {
      language: "python",
      code: 'result = {"ok": True}',
      timeout_ms: 5000,
      memory_mb: 128,
    };
    mockEnsureDraft.mockResolvedValue("existing-draft");
    mockAddOptimisticNode.mockReturnValue({
      ...defaultOptimisticResult,
      ports: [
        { key: "inputs", direction: "input" },
        { key: "result", direction: "output" },
      ],
      config: codeConfig,
    });
    mockGetNodeById.mockReturnValue({
      id: "node-123",
      type: NODE_TYPES.CODE_EXECUTION,
    });

    const { result } = renderHook(() => useAddNodeOptimistic());

    await act(async () => {
      await result.current.addNode({
        ...defaultPayload,
        type: NODE_TYPES.CODE_EXECUTION,
        config: {},
      });
    });

    expect(addNodeApi).toHaveBeenCalledWith(
      expect.objectContaining({
        data: expect.objectContaining({
          config: codeConfig,
          ports: [
            { key: "inputs", direction: "input" },
            { key: "result", direction: "output" },
          ],
        }),
      }),
    );
  });

  it("draft path: when addOptimisticNode returns null, returns null without calling addNodeApi", async () => {
    mockEnsureDraft.mockResolvedValue("existing-draft");
    mockAddOptimisticNode.mockReturnValue(null);

    const { result } = renderHook(() => useAddNodeOptimistic());

    let returnValue;
    await act(async () => {
      returnValue = await result.current.addNode(defaultPayload);
    });

    expect(returnValue).toBeNull();
    expect(addNodeApi).not.toHaveBeenCalled();
  });

  it('draft path: when addNodeApi rejects, calls logger.error with "[useAddNodeOptimistic]" context', async () => {
    const apiError = new Error("Network failure");
    mockEnsureDraft.mockResolvedValue("existing-draft");
    mockAddOptimisticNode.mockReturnValue(defaultOptimisticResult);
    mockGetNodeById.mockReturnValue({ id: "node-123" });
    addNodeApi.mockRejectedValue(apiError);

    const { result } = renderHook(() => useAddNodeOptimistic());

    await act(async () => {
      await result.current.addNode(defaultPayload);
    });

    // Wait for the catch handler to fire
    await vi.waitFor(() => {
      expect(logger.error).toHaveBeenCalledWith(
        "[useAddNodeOptimistic] addNodeApi failed",
        apiError,
      );
    });
  });

  it("draft path: when addNodeApi rejects, calls removeOptimisticNode", async () => {
    const apiError = new Error("Network failure");
    mockEnsureDraft.mockResolvedValue("existing-draft");
    mockAddOptimisticNode.mockReturnValue(defaultOptimisticResult);
    mockGetNodeById.mockReturnValue({ id: "node-123" });
    addNodeApi.mockRejectedValue(apiError);

    const { result } = renderHook(() => useAddNodeOptimistic());

    await act(async () => {
      await result.current.addNode(defaultPayload);
    });

    await vi.waitFor(() => {
      expect(mockRemoveOptimisticNode).toHaveBeenCalledWith("node-123");
    });
  });

  it("draft path: when addNodeApi rejects, shows snackbar error", async () => {
    const apiError = new Error("Network failure");
    mockEnsureDraft.mockResolvedValue("existing-draft");
    mockAddOptimisticNode.mockReturnValue(defaultOptimisticResult);
    mockGetNodeById.mockReturnValue({ id: "node-123" });
    addNodeApi.mockRejectedValue(apiError);

    const { result } = renderHook(() => useAddNodeOptimistic());

    await act(async () => {
      await result.current.addNode(defaultPayload);
    });

    await vi.waitFor(() => {
      expect(mockEnqueueSnackbar).toHaveBeenCalledWith("Failed to add node", {
        variant: "error",
      });
    });
  });
});
