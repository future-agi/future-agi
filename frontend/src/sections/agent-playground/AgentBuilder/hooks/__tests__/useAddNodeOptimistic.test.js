import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import useAddNodeOptimistic from "../useAddNodeOptimistic";
import { useAgentPlaygroundStore } from "../../../store";

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
const { addNodeApi } = await import(
  "src/api/agent-playground/agent-playground"
);
const logger = (await import("src/utils/logger")).default;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const mockAddOptimisticNode = vi.fn();
const mockRemoveOptimisticNode = vi.fn();
const mockComputeNewNodeData = vi.fn();
const mockSetSelectedNode = vi.fn();
const mockGetNodeById = vi.fn();
const mockUpdateEdgeId = vi.fn();

function setStoreState(overrides = {}) {
  useAgentPlaygroundStore.setState({
    addOptimisticNode: mockAddOptimisticNode,
    removeOptimisticNode: mockRemoveOptimisticNode,
    computeNewNodeData: mockComputeNewNodeData,
    setSelectedNode: mockSetSelectedNode,
    getNodeById: mockGetNodeById,
    updateEdgeId: mockUpdateEdgeId,
    currentAgent: {
      id: "graph-1",
      version_id: "v-1",
      is_draft: true,
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
    mockAddOptimisticNode.mockReturnValue(defaultOptimisticResult);
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
    expect(returnValue).toEqual({
      nodeId: "node-123",
      position: { x: 100, y: 200 },
    });
  });

  it("draft path: persists full seeded LLM prompt config in addNodeApi payload", async () => {
    mockEnsureDraft.mockResolvedValue("existing-draft");
    mockAddOptimisticNode.mockReturnValue(defaultOptimisticResult);
    mockGetNodeById.mockReturnValue({ id: "node-123", type: "llm_prompt" });

    const libraryPromptConfig = {
      prompt_template_id: null,
      prompt_version_id: null,
      outputFormat: "json",
      templateFormat: "jinja",
      modelConfig: {
        model: "gpt-4o-mini",
        modelDetail: { model_name: "gpt-4o-mini", providers: "openai" },
        responseFormat: "text",
        toolChoice: "auto",
        tools: [{ name: "search" }],
      },
      messages: [
        {
          id: "msg-user",
          role: "user",
          content: [
            { type: "text", text: "Describe this image" },
            {
              type: "image_url",
              image_url: { url: "https://example.com/image.png" },
            },
          ],
        },
      ],
      payload: {
        promptConfig: [
          {
            configuration: {
              temperature: 0.2,
              max_tokens: 512,
              top_p: 0.9,
              frequency_penalty: 0.1,
              presence_penalty: 0.3,
              output_format: "json",
              template_format: "jinja",
              tools: [{ name: "search" }],
              tool_choice: "auto",
            },
          },
        ],
      },
    };

    const { result } = renderHook(() => useAddNodeOptimistic());

    await act(async () => {
      await result.current.addNode({
        ...defaultPayload,
        sourceNodeId: undefined,
        config: libraryPromptConfig,
      });
    });

    expect(addNodeApi).toHaveBeenCalledWith(
      expect.objectContaining({
        data: expect.objectContaining({
          prompt_template: expect.objectContaining({
            prompt_template_id: null,
            prompt_version_id: null,
            model: "gpt-4o-mini",
            model_detail: {
              model_name: "gpt-4o-mini",
              providers: "openai",
            },
            response_format: "text",
            output_format: "json",
            temperature: 0.2,
            max_tokens: 512,
            top_p: 0.9,
            frequency_penalty: 0.1,
            presence_penalty: 0.3,
            tools: [{ name: "search" }],
            tool_choice: "auto",
            template_format: "jinja",
            save_prompt_version: false,
            messages: [
              {
                id: "msg-user",
                role: "user",
                content: [
                  { type: "text", text: "Describe this image" },
                  {
                    type: "image_url",
                    image_url: { url: "https://example.com/image.png" },
                  },
                ],
              },
            ],
          }),
        }),
      }),
    );
  });

  it("draft path: derives JSON output port schema from prompt config", async () => {
    const responseSchema = {
      type: "object",
      properties: { answer: { type: "string" } },
    };

    mockEnsureDraft.mockResolvedValue("existing-draft");
    mockAddOptimisticNode.mockReturnValue({
      ...defaultOptimisticResult,
      ports: [
        {
          id: "port-response",
          key: "response",
          display_name: "response",
          direction: "output",
          data_schema: { type: "string" },
          required: true,
        },
      ],
    });
    mockGetNodeById.mockReturnValue({ id: "node-123", type: "llm_prompt" });

    const { result } = renderHook(() => useAddNodeOptimistic());

    await act(async () => {
      await result.current.addNode({
        ...defaultPayload,
        config: {
          outputFormat: "json",
          modelConfig: { responseSchema },
        },
      });
    });

    expect(addNodeApi).toHaveBeenCalledWith(
      expect.objectContaining({
        data: expect.objectContaining({
          ports: [
            expect.objectContaining({
              key: "response",
              data_schema: responseSchema,
            }),
          ],
        }),
      }),
    );
  });

  it("draft path: syncs optimistic edge ID from snake_case node_connection response", async () => {
    mockEnsureDraft.mockResolvedValue("existing-draft");
    mockAddOptimisticNode.mockReturnValue(defaultOptimisticResult);
    mockGetNodeById.mockReturnValue({ id: "node-123", type: "llm_prompt" });
    addNodeApi.mockResolvedValue({
      node_connection: { id: "backend-edge-123" },
    });

    const { result } = renderHook(() => useAddNodeOptimistic());

    await act(async () => {
      await result.current.addNode(defaultPayload);
    });

    await vi.waitFor(() => {
      expect(mockUpdateEdgeId).toHaveBeenCalledWith(
        "edge-456",
        "backend-edge-123",
      );
    });
  });

  it("draft path: keeps camelCase nodeConnection response compatibility", async () => {
    mockEnsureDraft.mockResolvedValue("existing-draft");
    mockAddOptimisticNode.mockReturnValue(defaultOptimisticResult);
    mockGetNodeById.mockReturnValue({ id: "node-123", type: "llm_prompt" });
    addNodeApi.mockResolvedValue({
      nodeConnection: { id: "backend-edge-456" },
    });

    const { result } = renderHook(() => useAddNodeOptimistic());

    await act(async () => {
      await result.current.addNode(defaultPayload);
    });

    await vi.waitFor(() => {
      expect(mockUpdateEdgeId).toHaveBeenCalledWith(
        "edge-456",
        "backend-edge-456",
      );
    });
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
