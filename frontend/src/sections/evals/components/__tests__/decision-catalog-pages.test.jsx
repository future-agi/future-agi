/* eslint-disable react/prop-types -- Test-only harness. */
// The real model menu on Create New, the eval create page and the eval
// detail page offers the whole decision catalog where the launch switch
// shows decision models: the catalog's OpenRouter decision model is listed
// beside the native Jev one. The pages' data hooks and their heavy editors
// are stubbed; the instruction editor and its model selector are real.
import React, { forwardRef } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import { isSurfaceShown } from "src/launch/launchSwitch";
import EvalPickerProvider from "src/sections/common/EvalPicker/context/EvalPickerProvider";
import EvalPickerCreateNew from "src/sections/common/EvalPicker/EvalPickerCreateNew";
import EvalCreatePage from "../EvalCreatePage";
import EvalDetailPage from "../EvalDetailPage";

// Hook answers stay referentially stable across renders, as the real hooks'
// do, so no page effect reruns on a fresh object.
const mocks = vi.hoisted(() => ({
  draft: null,
  detail: null,
  auth: { role: "Owner" },
  navigate: vi.fn(),
  update: { mutate: vi.fn(), mutateAsync: vi.fn() },
  create: { mutateAsync: vi.fn(async () => ({ id: "draft-1" })) },
  composite: { mutateAsync: vi.fn() },
  versions: { data: { versions: [] } },
  unionKeys: [],
}));

vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: vi.fn(), post: vi.fn() },
}));
vi.mock("react-router", async (original) => ({
  ...(await original()),
  useParams: () => ({ draftId: "draft-1" }),
  useNavigate: () => mocks.navigate,
}));
vi.mock("notistack", async (original) => ({
  ...(await original()),
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => mocks.auth,
}));
vi.mock("src/hooks/useCapabilities", async (original) => ({
  ...(await original()),
  useFeatureLocked: () => ({ locked: false, isLoading: false }),
  useFeatureDenied: () => false,
  useFeatureAllowed: () => ({ allowed: false, isLoading: false }),
}));
vi.mock("src/hooks/useErrorLocalization", () => ({
  useErrorLocalizationAvailable: () => false,
}));
vi.mock("src/sections/evals/hooks/useEvalDetail", () => ({
  useEvalDetail: () => ({ data: mocks.detail, isLoading: false }),
  useUpdateEval: () => mocks.update,
  useDuplicateEval: () => ({}),
  evalDetailQuery: (templateId) => ({
    queryKey: ["evals", "detail", templateId],
    queryFn: () => new Promise(() => {}),
  }),
}));
vi.mock("src/sections/evals/hooks/useEvalVersions", () => ({
  useEvalVersions: () => mocks.versions,
  useCreateEvalVersion: () => mocks.composite,
  useSetDefaultVersion: () => mocks.composite,
}));
vi.mock("src/sections/evals/hooks/useCompositeEval", () => ({
  useCompositeDetail: () => ({}),
  useUpdateCompositeEval: () => mocks.composite,
  useCreateCompositeEval: () => mocks.composite,
  useExecuteCompositeEval: () => mocks.composite,
  useExecuteCompositeEvalAdhoc: () => mocks.composite,
}));
vi.mock("src/sections/evals/hooks/useCreateEval", () => ({
  useCreateEval: () => mocks.create,
}));
vi.mock("src/sections/evals/hooks/useCompositeChildrenKeys", () => ({
  useCompositeChildrenUnionKeys: () => mocks.unionKeys,
}));
vi.mock("src/sections/evals/hooks/useGroundTruth", async (original) => ({
  ...(await original()),
  useGroundTruthList: () => ({ data: undefined }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/custom-model-dropdown/KeysDrawer", () => ({
  default: () => null,
}));
vi.mock("src/components/PromptCards/PromptEditor", () => ({
  default: forwardRef(function PromptEditor() {
    return null;
  }),
}));
vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  ),
}));
vi.mock("src/sections/evals/components/TestPlayground", () => ({
  default: forwardRef(function TestPlaygroundStub() {
    return null;
  }),
}));
vi.mock("src/sections/evals/components/TracingTestMode", () => ({
  default: forwardRef(function TracingTestModeStub() {
    return null;
  }),
}));
vi.mock("src/sections/evals/components/SimulationTestMode", () => ({
  default: forwardRef(function SimulationTestModeStub() {
    return null;
  }),
}));
vi.mock("src/sections/evals/components/DatasetTestMode", () => ({
  default: forwardRef(function DatasetTestModeStub() {
    return null;
  }),
  JsonValueTree: () => null,
}));
vi.mock("src/sections/evals/components/LLMPromptEditor", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/CodeEvalEditor", () => ({
  default: () => null,
  PYTHON_CODE_TEMPLATE: "",
}));
vi.mock("src/sections/evals/components/CodeEditor", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/FewShotExamples", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/OutputTypeConfig", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/CompositeDetailPanel", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/EvalFeedbackTab", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/EvalGroundTruthTab", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/EvalUsageTab", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/VersionBadge", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/BulkDeleteDialog", () => ({
  default: () => null,
}));
vi.mock("src/sections/tasks/components/TaskFilterBar", () => ({
  default: () => null,
}));

const OPENROUTER_DECISION_MODEL =
  "decision/openrouter/openai/gpt-6-luna-decisions";

const connection = (model_name, provider, label) => ({
  model_name,
  provider,
  label,
  is_available: provider !== "openrouter",
});
const group = (model_name, label, author, connections) => ({
  model_name,
  label,
  author,
  connections,
  question_types: ["noul", "choice", "score"],
  type: "decision",
  version: "versioned",
  min_choices: 1,
  max_choices: 255,
  max_state_bytes: 120000,
  unavailable_reason: null,
  notice: "Text and JSON inputs only.",
});
const CATALOG = [
  group("typesafe/jev-1.14", "Jev 1.14", "TypeSafe", [
    connection("jev-1.14.0", "typesafe", "TypeSafe"),
  ]),
  group("openai/gpt-6-luna-decisions", "GPT-6 Luna Decisions", "OpenAI", [
    connection(OPENROUTER_DECISION_MODEL, "openrouter", "OpenRouter"),
  ]),
];

beforeEach(() => {
  vi.clearAllMocks();
  mocks.draft = {
    id: "draft-1",
    eval_type: "agent",
    instructions: "Check {{input}}",
    output_type_normalized: "pass_fail",
    config: { model: "turing_large", agent_mode: "agent" },
  };
  mocks.detail = {
    id: "eval-1",
    name: "decision_eval",
    owner: "user",
    eval_type: "agent",
    instructions: "Check {{input}}",
    output_type: "pass_fail",
    pass_threshold: 0.5,
    config: { model: "turing_large", agent_mode: "agent" },
  };
  axios.get.mockImplementation(async (url, { params } = {}) => {
    if (url === endpoints.develop.apiKey.status)
      return {
        data: {
          result: {
            providers: [
              {
                provider: "typesafe",
                display_name: "TypeSafe",
                type: "text",
                has_key: true,
              },
              {
                provider: "openrouter",
                display_name: "OpenRouter",
                type: "text",
                has_key: false,
              },
            ],
          },
        },
      };
    if (url === endpoints.develop.modelList)
      return {
        data:
          params?.model_type === "decision"
            ? {
                count: CATALOG.length,
                results: CATALOG,
                next: null,
                current_page: 1,
              }
            : {
                count: 1,
                results: [
                  { model_name: "ordinary-model", providers: "custom" },
                ],
                next: null,
                current_page: 1,
              },
      };
    if (url === endpoints.develop.eval.getEvalDetail("draft-1"))
      return { data: { result: mocks.draft } };
    return { data: { results: [], result: { table_data: [], total_rows: 0 } } };
  });
  axios.post.mockResolvedValue({ data: { status: true, result: {} } });
});

const withQueryClient = (ui) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {ui}
    </QueryClientProvider>,
  );

async function expectOpenRouterDecisionModelOffered() {
  expect(isSurfaceShown("models.decision")).toBe(true);
  await userEvent.click(
    await screen.findByRole("button", { name: /^Evaluation model:/ }),
  );
  // Jev 1.14 comes only from the catalog, so the catalog has loaded.
  await screen.findByRole("menuitem", { name: /Jev 1\.14/ });
  const row = screen.getByRole("menuitem", { name: /GPT-6 Luna Decisions/ });
  expect(row).not.toHaveAttribute("aria-disabled", "true");
  expect(row).toHaveTextContent("Add your OpenRouter API key");
}

describe("the decision catalog in each page's own model menu", () => {
  it("Create New offers the catalog's OpenRouter decision model", async () => {
    withQueryClient(
      <EvalPickerProvider
        source="simulation"
        sourceId="sim-1"
        sourceColumns={[]}
        existingEvals={[]}
        onEvalAdded={() => {}}
        onClose={() => {}}
      >
        <EvalPickerCreateNew onBack={() => {}} onSave={() => {}} />
      </EvalPickerProvider>,
    );
    await expectOpenRouterDecisionModelOffered();
  });

  it("the eval create page offers the catalog's OpenRouter decision model", async () => {
    withQueryClient(<EvalCreatePage />);
    await expectOpenRouterDecisionModelOffered();
  });

  it("the eval detail page offers the catalog's OpenRouter decision model", async () => {
    withQueryClient(<EvalDetailPage />);
    await expectOpenRouterDecisionModelOffered();
  });
});
