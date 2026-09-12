import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "src/utils/test-utils";
import { modelConfigDefault } from "../WorkbenchContext";

const { mockFetchQuery, dropdownProps } = vi.hoisted(() => ({
  mockFetchQuery: vi.fn(),
  dropdownProps: { current: null },
}));

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useParams: () => ({ id: "prompt-1" }),
  };
});

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));

vi.mock("../WorkbenchContext", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    usePromptWorkbenchContext: () => ({
      selectedVersions: [{ version: "v1" }],
      templateFormat: "mustache",
      setTemplateFormat: vi.fn(),
    }),
  };
});

vi.mock("src/sections/workbench-v2/store/usePromptStore", () => ({
  usePromptStoreShallow: () => ({
    setSelectTemplateDrawerOpen: vi.fn(),
    setSelectedPromptIndex: vi.fn(),
  }),
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ fetchQuery: mockFetchQuery }),
  useQuery: () => ({ data: undefined }),
}));

vi.mock("src/api/develop/prompt", () => ({
  useModelParams: () => ({ data: undefined }),
}));

vi.mock("src/api/develop/develop-detail", () => ({
  useVoiceOptions: () => ({ data: undefined }),
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: {
    promptSelectModelClicked: "promptSelectModelClicked",
    promptUseTemplateClicked: "promptUseTemplateClicked",
    promptSelectToolsClicked: "promptSelectToolsClicked",
  },
  PropertyName: {
    click: "click",
    promptId: "promptId",
    type: "type",
    version: "version",
  },
  trackEvent: vi.fn(),
}));

vi.mock("src/components/custom-model-dropdown/CustomModelDropdown", () => ({
  default: (props) => {
    dropdownProps.current = props;
    return <div data-testid="model-dropdown" />;
  },
}));

vi.mock("src/components/custom-model-tools", () => ({
  default: () => <div data-testid="model-tools" />,
}));

vi.mock("./ModelParamsContainer", () => ({
  default: () => <div data-testid="model-params" />,
}));

vi.mock("./ResponseFormatSelector", () => ({
  default: () => <div data-testid="response-format" />,
}));

vi.mock("./TemplateFormatSelector", () => ({
  default: () => <div data-testid="template-format" />,
}));

vi.mock("src/components/svg-color", () => ({
  default: () => <span />,
}));

vi.mock("src/components/iconify", () => ({
  default: () => <span />,
}));

vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));

import ModelContainer from "./ModelContainer";

const EXISTING_CONFIG = {
  model: "gpt-4o",
  model_detail: {
    model_name: "gpt-4o",
    providers: "openai",
    type: "chat",
    logoUrl: "https://example.com/gpt.png",
    isAvailable: true,
  },
  output_format: "json_object",
  responseFormat: "json_object",
  tools: [{ id: "tool-1", name: "search" }],
  tool_choice: "auto",
  temperature: 0.4,
  model_type: "all",
};

const applyLatestUpdater = (setModelConfig, previous = EXISTING_CONFIG) => {
  const updater = setModelConfig.mock.calls.at(-1)[0];
  return typeof updater === "function" ? updater(previous) : updater;
};

const renderContainer = (overrides = {}) => {
  const setModelConfig = vi.fn();
  render(
    <ModelContainer
      modelConfig={{ ...EXISTING_CONFIG, ...overrides }}
      setModelConfig={setModelConfig}
      open
      setOpen={vi.fn()}
      promptIndex={0}
    />,
  );
  return setModelConfig;
};

describe("ModelContainer filter tabs (#2436)", () => {
  beforeEach(() => {
    mockFetchQuery.mockReset();
    dropdownProps.current = null;
  });

  it("clicking a non-all filter tab keeps model, output format, and companion config and skips draft save", () => {
    const setModelConfig = renderContainer();
    setModelConfig.mockClear();

    dropdownProps.current.onModelTypeChange("image");

    expect(setModelConfig).toHaveBeenCalledTimes(1);
    expect(setModelConfig.mock.calls[0][1]).toEqual({ skipSave: true });

    const next = applyLatestUpdater(setModelConfig);
    expect(next.model).toBe("gpt-4o");
    expect(next.output_format).toBe("json_object");
    expect(next.responseFormat).toBe("json_object");
    expect(next.tools).toEqual([{ id: "tool-1", name: "search" }]);
    expect(next.tool_choice).toBe("auto");
    expect(next.temperature).toBe(0.4);
    expect(next.model_detail.model_name).toBe("gpt-4o");
    expect(next.model_type).toBe("image");
    expect(next).not.toEqual(
      expect.objectContaining({
        model: modelConfigDefault.model,
        output_format: modelConfigDefault.output_format,
        tools: modelConfigDefault.tools,
      }),
    );
  });

  it("clicking the all tab also preserves configuration and skips draft save", () => {
    const setModelConfig = renderContainer({ model_type: "llm" });
    setModelConfig.mockClear();

    dropdownProps.current.onModelTypeChange("all");

    expect(setModelConfig.mock.calls[0][1]).toEqual({ skipSave: true });
    const next = applyLatestUpdater(setModelConfig, {
      ...EXISTING_CONFIG,
      model_type: "llm",
    });
    expect(next.model).toBe("gpt-4o");
    expect(next.output_format).toBe("json_object");
    expect(next.tools).toHaveLength(1);
    expect(next.model_type).toBe("all");
  });

  it("selecting a different model still updates config and does not skip save", async () => {
    mockFetchQuery.mockResolvedValue({
      data: {
        result: {
          sliders: [],
          responseFormat: [{ value: "text" }, { value: "json_object" }],
        },
      },
    });

    const setModelConfig = renderContainer();
    setModelConfig.mockClear();

    dropdownProps.current.onChange({
      target: {
        value: {
          modelName: "dall-e-3",
          model_name: "dall-e-3",
          providers: "openai",
          type: "image_generation",
          logoUrl: "https://example.com/dalle.png",
          isAvailable: true,
        },
      },
    });

    await waitFor(() => {
      expect(setModelConfig).toHaveBeenCalled();
    });

    const saveCall = setModelConfig.mock.calls.find(
      ([, options]) => !options?.skipSave,
    );
    expect(saveCall).toBeTruthy();
    expect(saveCall[1]).toBeUndefined();

    const next =
      typeof saveCall[0] === "function"
        ? saveCall[0](EXISTING_CONFIG)
        : saveCall[0];
    expect(next.model).toBe("dall-e-3");
    expect(next.model_detail.modelName ?? next.model_detail.model_name).toBe(
      "dall-e-3",
    );
    expect(next.output_format).toBe("image");
  });
});
