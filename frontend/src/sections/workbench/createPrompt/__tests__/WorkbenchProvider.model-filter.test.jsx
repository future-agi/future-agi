import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderWithRouter } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { usePromptWorkbenchContext } from "../WorkbenchContext";
import WorkbenchProvider from "../WorkbenchProvider";
import ModelContainer from "../Playground/ModelContainer";

const dropdown = vi.hoisted(() => ({ current: null }));
vi.mock("src/hooks/useRestrictSimulateToEnvironments", () => ({
  useRestrictSimulateToEnvironments: () => false,
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/sections/workbench/createPrompt/hooks/usePromptStreamUrl", () => ({
  usePromptStreamUrl: () => null,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/sections/workbench-v2/store/usePromptStore", () => ({
  usePromptStoreShallow: () => ({
    setSelectTemplateDrawerOpen: vi.fn(),
    setSelectedPromptIndex: vi.fn(),
  }),
}));
vi.mock("src/api/develop/prompt", () => ({ useModelParams: () => ({}) }));
vi.mock("src/api/develop/develop-detail", () => ({
  useVoiceOptions: () => ({}),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/components/custom-model-dropdown/CustomModelDropdown", () => ({
  default: (props) => {
    dropdown.current = props;
    return null;
  },
}));
vi.mock("src/components/custom-model-tools", () => ({ default: () => null }));
vi.mock("../Playground/ModelParamsContainer", () => ({ default: () => null }));
vi.mock("../Playground/ResponseFormatSelector", () => ({
  default: () => null,
}));
vi.mock("../Playground/TemplateFormatSelector", () => ({
  default: () => null,
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {
    develop: {
      modelParams: "/model-params/",
      runPrompt: {
        responseSchema: "/schemas/",
        getPrompt: (id) => `/prompts/${id}/`,
        addDraftInPrompt: (id) => `/prompts/${id}/add-new-draft/`,
        runTemplatePrompt: (id) => `/prompts/${id}/run_template/`,
        compareVersions: (id) => `/prompts/${id}/compare/`,
      },
    },
  },
}));

const configuration = {
  model: "original",
  model_detail: { model_name: "original", providers: "local", type: "chat" },
  output_format: "string",
  responseFormat: "json_object",
  tools: [{ id: "synthetic-tool", name: "lookup" }],
  temperature: 0.4,
  model_type: "all",
};
let context;
function Editor() {
  context = usePromptWorkbenchContext();
  return (
    <ModelContainer
      modelConfig={context.modelConfig[0]}
      setModelConfig={(value, options) =>
        context.setModelConfigByIndex(0, value, options)
      }
      open
      setOpen={() => {}}
      promptIndex={0}
    />
  );
}

// Real provider state, TanStack mutations, draft creation and debounce; only
// HTTP and unrelated leaf widgets are substituted. The setter is never mocked.
describe("Integration: model filter browsing and persisted draft requests", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
  });
  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it.each([false, true])(
    "browses without saving, then saves a selection (draft=%s)",
    async (isDraft) => {
      axios.get.mockImplementation(async (url) => {
        if (url === "/model-params/")
          return { data: { result: { sliders: [], responseFormat: [] } } };
        if (url === "/schemas/") return { data: { results: [] } };
        return {
          data: {
            name: "Synthetic prompt",
            version: "v1",
            is_draft: isDraft,
            prompt_config: [
              {
                messages: [
                  { role: "user", content: [{ type: "text", text: "hello" }] },
                ],
                configuration,
              },
            ],
          },
        };
      });
      axios.post.mockImplementation(async (url) =>
        url.endsWith("add-new-draft/")
          ? { data: { result: [{ template_version: "v2", is_draft: true }] } }
          : { data: {} },
      );
      const client = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      });
      renderWithRouter(
        <QueryClientProvider client={client}>
          <Routes>
            <Route
              path="/dashboard/workbench/create/:id"
              element={
                <WorkbenchProvider>
                  <Editor />
                </WorkbenchProvider>
              }
            />
          </Routes>
        </QueryClientProvider>,
        { route: "/dashboard/workbench/create/prompt-1" },
      );
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(context.modelConfig[0].model).toBe("original");
      const loaded = { ...context.modelConfig[0] };
      expect(axios.post).not.toHaveBeenCalled();

      for (const filter of ["image", "llm", "all"]) {
        act(() => dropdown.current.onModelTypeChange(filter));
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1000);
        });
        expect(context.modelConfig[0]).toEqual({
          ...loaded,
          model_type: filter,
        });
        expect(context.selectedVersions[0]).toMatchObject({
          version: "v1",
          isDraft,
        });
        // Covers both immediate add-new-draft and debounced run_template writes.
        expect(axios.post).not.toHaveBeenCalled();
      }

      await act(async () => {
        await dropdown.current.onChange({
          target: {
            value: {
              model_name: "synthetic-image",
              providers: "local",
              type: "image_generation",
            },
          },
        });
      });
      // Flush React's selection render before advancing the autosave clock.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(context.modelConfig[0]).toMatchObject({
        model: "synthetic-image",
        output_format: "image",
        tools: configuration.tools,
      });
      expect(context.selectedVersions[0]).toMatchObject({
        version: isDraft ? "v1" : "v2",
        isDraft: true,
      });
      const draftRequests = axios.post.mock.calls.filter(([url]) =>
        url.endsWith("add-new-draft/"),
      );
      expect(draftRequests).toHaveLength(isDraft ? 0 : 1);
      const saves = axios.post.mock.calls.filter(([url]) =>
        url.endsWith("run_template/"),
      );
      expect(saves).toHaveLength(1);
      expect(saves[0][1]).toMatchObject({
        version: isDraft ? "v1" : "v2",
        is_run: false,
        prompt_config: [
          {
            configuration: {
              model: "synthetic-image",
              output_format: "image",
              tools: configuration.tools,
            },
          },
        ],
      });
    },
  );
});
