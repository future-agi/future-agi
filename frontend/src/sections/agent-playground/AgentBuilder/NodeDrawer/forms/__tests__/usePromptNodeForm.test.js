import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePromptNodeForm } from "../usePromptNodeForm";
import {
  useAgentPlaygroundStore,
  useGlobalVariablesDrawerStore,
} from "../../../../store";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
const mockSetValue = vi.fn();
const mockWatch = vi.fn();
const mockControl = {};
let mockResponseFormatValue = "text";

vi.mock("react-hook-form", () => ({
  useFormContext: () => ({
    control: mockControl,
    watch: mockWatch,
    setValue: mockSetValue,
  }),
  useController: () => ({
    field: { value: mockResponseFormatValue, onChange: vi.fn() },
  }),
}));

const mockModelParams = null;
const mockResponseFormatMenuItems = [
  { value: "text", label: "Text" },
  { value: "json_object", label: "JSON" },
];
const mockResponseSchema = null;
vi.mock("../usePromptNodeQueries", () => ({
  usePromptNodeQueries: () => ({
    modelParams: mockModelParams,
    responseFormatMenuItems: mockResponseFormatMenuItems,
    responseSchema: mockResponseSchema,
  }),
}));

vi.mock("../useModelParameters", () => ({
  useModelParameters: () => ({
    modelParameters: {
      sliders: [],
      booleans: [],
      dropdowns: [],
      reasoning: null,
    },
    updateSliderParameter: vi.fn(),
    updateBooleanParameter: vi.fn(),
    updateDropdownParameter: vi.fn(),
    updateReasoningSliderParameter: vi.fn(),
    updateReasoningDropdownParameter: vi.fn(),
    updateShowReasoningProcess: vi.fn(),
  }),
}));

const mockSavePromptNode = vi.fn(() => ({ payload: "test" }));
vi.mock("../promptNodeFormUtils", () => ({
  savePromptNode: (...args) => mockSavePromptNode(...args),
}));

vi.mock("src/sections/agent-playground/utils/constants", async () => {
  const actual = await vi.importActual(
    "src/sections/agent-playground/utils/constants",
  );
  return {
    ...actual,
    MODEL_CONFIG_DEFAULTS: { model: null, maxTokens: 500 },
  };
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function setupWatch(modelConfig = null, messages = null, outputFormat) {
  mockWatch.mockImplementation((key) => {
    if (key === "modelConfig") return modelConfig;
    if (key === "messages") return messages;
    if (key === "outputFormat") return outputFormat;
    return undefined;
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("usePromptNodeForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgentPlaygroundStore.getState().reset();
    useGlobalVariablesDrawerStore.getState().reset();
    setupWatch();
    mockResponseFormatValue = "text";
  });

  it("returns form control and state", () => {
    const { result } = renderHook(() => usePromptNodeForm());

    expect(result.current.control).toBe(mockControl);
    expect(result.current.modelConfig).toBeDefined();
    expect(result.current.messages).toEqual([]);
    expect(typeof result.current.handleModelChange).toBe("function");
    expect(typeof result.current.handleToolsApply).toBe("function");
    expect(typeof result.current.buildPayload).toBe("function");
  });

  it("uses default model config when watch returns null", () => {
    setupWatch(null);
    const { result } = renderHook(() => usePromptNodeForm());

    expect(result.current.modelConfig).toEqual({ model: null, maxTokens: 500 });
  });

  it("uses watched model config when available", () => {
    const config = { model: "gpt-4", maxTokens: 2000 };
    setupWatch(config);
    const { result } = renderHook(() => usePromptNodeForm());

    expect(result.current.modelConfig).toEqual(config);
  });

  it("derives isModelSelected from modelConfig.model", () => {
    setupWatch({ model: "gpt-4", maxTokens: 1000 });
    const { result } = renderHook(() => usePromptNodeForm());
    expect(result.current.isModelSelected).toBe(true);
  });

  it("isModelSelected is false when no model", () => {
    setupWatch({ model: null });
    const { result } = renderHook(() => usePromptNodeForm());
    expect(result.current.isModelSelected).toBe(false);
  });

  it("allows JSON output format in the prompt drawer", () => {
    setupWatch({ model: "gpt-4", maxTokens: 1000 }, [], "json");
    const { result } = renderHook(() => usePromptNodeForm());

    expect(result.current.isUnsupportedOutputFormat).toBe(false);
  });

  it("marks non-textual output formats as unsupported", () => {
    setupWatch({ model: "gpt-4", maxTokens: 1000 }, [], "image");
    const { result } = renderHook(() => usePromptNodeForm());

    expect(result.current.isUnsupportedOutputFormat).toBe(true);
  });

  it("adds a JSON Schema response-format option for schema-backed imports without schema ids", () => {
    mockResponseFormatValue = "json_schema";
    setupWatch({
      model: "gpt-4",
      maxTokens: 1000,
      responseFormat: "json_schema",
      responseSchema: { type: "object" },
    });

    const { result } = renderHook(() => usePromptNodeForm());

    expect(result.current.responseFormatMenuItems).toEqual(
      expect.arrayContaining([{ value: "json_schema", label: "JSON Schema" }]),
    );
  });

  // ---- handleModelChange ----
  it("handleModelChange sets model, modelDetail, and maxTokens=1000", () => {
    const config = { model: null, maxTokens: 500, tools: [] };
    setupWatch(config);
    const { result } = renderHook(() => usePromptNodeForm());

    act(() => {
      result.current.handleModelChange({
        target: {
          value: { modelName: "claude-3", providers: "anthropic", extra: 1 },
        },
      });
    });

    expect(mockSetValue).toHaveBeenCalledWith("modelConfig", {
      ...config,
      model: "claude-3",
      modelDetail: { modelName: "claude-3", providers: "anthropic", extra: 1 },
      maxTokens: 1000,
    });
  });

  // ---- handleToolsApply ----
  it("handleToolsApply merges tools into modelConfig", () => {
    const config = { model: "gpt-4", maxTokens: 1000 };
    setupWatch(config);
    const { result } = renderHook(() => usePromptNodeForm());
    const tools = [{ name: "search" }];

    act(() => {
      result.current.handleToolsApply(tools);
    });

    expect(mockSetValue).toHaveBeenCalledWith("modelConfig", {
      ...config,
      tools,
    });
  });

  // ---- buildPayload ----
  it("buildPayload calls savePromptNode with correct args", () => {
    useGlobalVariablesDrawerStore.setState({});
    useAgentPlaygroundStore.setState({
      selectedNode: {
        id: "n1",
        data: {
          config: {
            payload: {
              ports: [{ key: "p1", direction: "output" }],
            },
          },
        },
      },
    });

    const { result } = renderHook(() => usePromptNodeForm());
    const formData = { model: "gpt-4", messages: [] };

    act(() => {
      result.current.buildPayload(formData);
    });

    expect(mockSavePromptNode).toHaveBeenCalledWith(
      formData,
      expect.any(Object), // modelParameters
      mockResponseSchema, // responseSchema
    );
  });

  // ---- Popover state ----
  it("manages popover state for parameters", () => {
    const { result } = renderHook(() => usePromptNodeForm());

    expect(result.current.isParamsPopoverOpen).toBe(false);

    act(() => {
      result.current.handleParamsClick({
        currentTarget: document.createElement("button"),
      });
    });
    expect(result.current.isParamsPopoverOpen).toBe(true);

    act(() => {
      result.current.handleParamsClose();
    });
    expect(result.current.isParamsPopoverOpen).toBe(false);
  });

  // ---- showCreateSchema state ----
  it("manages showCreateSchema state", () => {
    const { result } = renderHook(() => usePromptNodeForm());

    expect(result.current.showCreateSchema).toBe(false);

    act(() => {
      result.current.setShowCreateSchema(true);
    });
    expect(result.current.showCreateSchema).toBe(true);
  });
});
