import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ModelSelector from "../ModelSelector";

const state = vi.hoisted(() => ({
  allowed: false,
  isLoading: false,
  isError: false,
}));
vi.mock("src/hooks/useCapabilities", () => ({
  CAPABILITY: { FALCON_AI: "falcon_ai", TURING_MODELS: "turing_models" },
  useFeatureAllowed: () => state,
  useFeatureLocked: () => ({ locked: true, isLoading: false }),
}));
vi.mock("src/components/iconify", () => ({ default: () => <span /> }));
vi.mock("src/components/custom-model-dropdown/KeysDrawer", () => ({
  default: () => null,
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { results: [] } }) },
  endpoints: {
    falconAI: { connectors: "/falcon-ai/connectors/" },
    knowledge: { list: "/knowledge/list/" },
    develop: {
      modelList: "/model-hub/api/models_list/",
      eval: { summaryTemplates: "/eval/summary-templates/" },
    },
  },
}));

describe("Evals connector new-entry capability gate", () => {
  beforeEach(() =>
    Object.assign(state, { allowed: false, isLoading: false, isError: false }),
  );
  it.each(["denied", "loading", "error", "allowed"])(
    "%s capability",
    async (mode) => {
      state.allowed = mode !== "denied";
      state.isLoading = mode === "loading";
      state.isError = mode === "error";
      const onConfigChange = vi.fn();
      render(
        <QueryClientProvider
          client={
            new QueryClient({ defaultOptions: { queries: { retry: false } } })
          }
        >
          <ModelSelector
            model=""
            onModelChange={vi.fn()}
            activeConnectorIds={["saved-connector"]}
            onActiveConnectorIdsChange={onConfigChange}
            showPlus
          />
        </QueryClientProvider>,
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Evaluation options" }),
      );
      if (mode === "allowed") {
        await userEvent.click(screen.getByText("Connectors"));
        expect(screen.getByText("Manage connectors")).toBeInTheDocument();
      } else {
        expect(screen.queryByText("Connectors")).not.toBeInTheDocument();
        expect(screen.queryByText("Manage connectors")).not.toBeInTheDocument();
      }
      // Merely hiding new-entry UI must not erase a saved evaluator policy.
      expect(onConfigChange).not.toHaveBeenCalled();
    },
  );
});
