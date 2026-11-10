import React, { forwardRef } from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import EvalCreatePage from "../EvalCreatePage";

const PROMPT = "Is {{output}} polite?";

const mocks = vi.hoisted(() => ({
  draft: null,
  mutate: vi.fn(),
  mutateAsync: vi.fn(),
  snack: vi.fn(),
  navigate: vi.fn(),
}));
vi.mock("notistack", async (original) => ({
  ...(await original()),
  useSnackbar: () => ({ enqueueSnackbar: mocks.snack }),
}));
vi.mock("react-router", async (original) => ({
  ...(await original()),
  useParams: () => ({ draftId: "draft-1" }),
  useNavigate: () => mocks.navigate,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/hooks/useCapabilities", async (original) => ({
  ...(await original()),
  useFeatureLocked: () => ({ locked: false, isLoading: false }),
  useFeatureDenied: () => false,
}));
vi.mock("src/hooks/useErrorLocalization", () => ({
  useErrorLocalizationAvailable: () => false,
}));
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(async (url) => {
      if (url === "/provider-status") {
        return {
          data: {
            result: { providers: [{ provider: "typesafe", has_key: true }] },
          },
        };
      }
      if (url === "/models") {
        return { data: { results: [], next: null } };
      }
      return { data: { result: mocks.draft } };
    }),
    post: vi.fn(),
  },
  endpoints: {
    develop: {
      apiKey: { status: "/provider-status" },
      modelList: "/models",
      eval: {
        getEvalDetail: (id) => `/detail/${id}`,
        createEvalTemplateV2: "/create-v2",
        evalPlayground: "/playground",
      },
    },
    knowledge: { list: "/knowledge" },
  },
}));
vi.mock("../../hooks/useEvalDetail", () => ({
  useUpdateEval: () => ({
    mutate: mocks.mutate,
    mutateAsync: mocks.mutateAsync,
  }),
}));
vi.mock("../../hooks/useCreateEval", () => ({
  useCreateEval: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("../../hooks/useCompositeEval", () => ({
  useCreateCompositeEval: () => ({ mutateAsync: vi.fn() }),
  useExecuteCompositeEval: () => ({ mutateAsync: vi.fn() }),
  useExecuteCompositeEvalAdhoc: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("../../hooks/useEvalVersions", () => ({
  useEvalVersions: () => ({}),
  useSetDefaultVersion: () => ({}),
}));
vi.mock("../CodeEditor", () => ({ default: () => null }));
vi.mock("../EvalResultDisplay", () => ({ default: () => null }));
vi.mock("../../hooks/useCompositeChildrenKeys", () => ({
  useCompositeChildrenUnionKeys: () => [],
}));
vi.mock("src/components/custom-model-dropdown/KeysDrawer", () => ({
  default: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/resizablePanels/ResizablePanels", () => {
  const ResizablePanels = ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  );
  ResizablePanels.propTypes = {
    leftPanel: PropTypes.node,
    rightPanel: PropTypes.node,
  };
  return { default: ResizablePanels };
});
vi.mock("../LLMPromptEditor", () => {
  const LLMPromptEditor = ({ messages }) => (
    <textarea
      aria-label="System prompt"
      readOnly
      value={messages?.find((m) => m.role === "system")?.content ?? ""}
    />
  );
  LLMPromptEditor.propTypes = { messages: PropTypes.arrayOf(PropTypes.object) };
  return { default: LLMPromptEditor };
});
vi.mock("src/components/PromptCards/PromptEditor", () => ({
  default: forwardRef(function PromptEditor() {
    return null;
  }),
}));
vi.mock("../InstructionEditor", () => ({ default: () => null }));
vi.mock("../TestPlayground", () => ({
  default: forwardRef(function TestPlayground() {
    return null;
  }),
}));
vi.mock("../FewShotExamples", () => ({ default: () => null }));
vi.mock("../CodeEvalEditor", () => ({
  default: () => null,
  PYTHON_CODE_TEMPLATE: "",
}));
vi.mock("../CompositeDetailPanel", () => ({ default: () => null }));

const renderPage = async () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <EvalCreatePage />
    </QueryClientProvider>,
  );
  // The draft has loaded once its prompt is on screen.
  await screen.findByDisplayValue(PROMPT);
};

const customTagField = () => screen.getByLabelText("Custom tag");
// Only custom tags are removable; the predefined chips toggle instead.
const removableChips = () => screen.queryAllByTestId("CancelIcon");

beforeEach(() => {
  vi.clearAllMocks();
  mocks.mutateAsync.mockResolvedValue({});
  mocks.draft = {
    id: "draft-1",
    eval_type: "llm",
    output_type_normalized: "pass_fail",
    instructions: PROMPT,
    tags: [],
    config: {
      model: "turing_large",
      messages: [{ role: "system", content: PROMPT }],
    },
  };
});

describe("EvalCreatePage custom tags", () => {
  it("adds a typed tag on Enter as a removable chip and clears the field", async () => {
    await renderPage();
    await userEvent.type(customTagField(), "  client-demo  {Enter}");

    const chip = screen.getByRole("button", { name: "client-demo" });
    expect(within(chip).getByTestId("CancelIcon")).toBeInTheDocument();
    expect(customTagField()).toHaveValue("");

    await userEvent.click(within(chip).getByTestId("CancelIcon"));
    expect(screen.queryByRole("button", { name: "client-demo" })).toBeNull();
  });

  it("ignores blank input and a tag that is already selected", async () => {
    await renderPage();
    await userEvent.type(customTagField(), "   {Enter}");
    expect(removableChips()).toHaveLength(0);

    await userEvent.type(customTagField(), "client-demo{Enter}");
    await userEvent.type(customTagField(), "client-demo{Enter}");
    expect(
      screen.getAllByRole("button", { name: "client-demo" }),
    ).toHaveLength(1);
    expect(removableChips()).toHaveLength(1);
    expect(customTagField()).toHaveValue("");
  });

  it("shows the custom tags an evaluation already has as selected", async () => {
    mocks.draft = { ...mocks.draft, tags: ["my-project", "red_teaming"] };
    await renderPage();

    const custom = await screen.findByRole("button", { name: "my-project" });
    expect(within(custom).getByTestId("CancelIcon")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Red Teaming" })).toHaveClass(
      "MuiChip-filled",
    );
    expect(screen.getByRole("button", { name: "NLP Metrics" })).toHaveClass(
      "MuiChip-outlined",
    );
  });

  it("saves predefined, custom and still-typed tags in the tags payload", async () => {
    await renderPage();
    await userEvent.type(
      screen.getByPlaceholderText("Eg: Hallucination detector"),
      "tone-check",
    );
    await userEvent.click(screen.getByRole("button", { name: "Red Teaming" }));
    await userEvent.type(customTagField(), "client-demo{Enter}");
    // Typed but not confirmed with Enter.
    await userEvent.type(customTagField(), " my-project ");

    await userEvent.click(
      screen.getByRole("button", { name: "Save Evaluation" }),
    );

    expect(mocks.mutateAsync).toHaveBeenCalledTimes(1);
    expect(mocks.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "tone-check",
        tags: ["red_teaming", "client-demo", "my-project"],
        publish: true,
      }),
    );
  });
});
