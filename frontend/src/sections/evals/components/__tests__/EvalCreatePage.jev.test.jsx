/* eslint-disable react/prop-types */
import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import EvalCreatePage from "../EvalCreatePage";

const mocks = vi.hoisted(() => ({
  draft: null,
  mutate: vi.fn(),
  snack: vi.fn(),
}));
vi.mock("notistack", async (original) => ({
  ...(await original()),
  useSnackbar: () => ({ enqueueSnackbar: mocks.snack }),
}));
vi.mock("react-router", async (original) => ({
  ...(await original()),
  useParams: () => ({ draftId: "draft-1" }),
  useNavigate: () => vi.fn(),
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
    get: vi.fn(async () => ({ data: { result: mocks.draft } })),
    post: vi.fn(),
  },
  endpoints: {
    develop: {
      eval: {
        getEvalDetail: (id) => `/detail/${id}`,
        createEvalTemplateV2: "/create-v2",
      },
    },
  },
}));
vi.mock("../../hooks/useEvalDetail", () => ({
  useUpdateEval: () => ({ mutate: mocks.mutate, mutateAsync: vi.fn() }),
}));
vi.mock("../../hooks/useCreateEval", () => ({
  useCreateEval: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("../../hooks/useCompositeEval", () => ({
  useCreateCompositeEval: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("../../hooks/useCompositeChildrenKeys", () => ({
  useCompositeChildrenUnionKeys: () => [],
}));
vi.mock("src/components/custom-model-dropdown/KeysDrawer", () => ({
  default: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  ),
}));
vi.mock("../LLMPromptEditor", () => ({
  default: ({ model, onModelChange }) => (
    <select
      aria-label="Evaluator model"
      value={model}
      onChange={(e) => onModelChange(e.target.value)}
    >
      <option value="jev-latest">Jev latest</option>
      <option value="turing_large">Turing Large</option>
    </select>
  ),
}));
vi.mock("../InstructionEditor", () => ({ default: () => null }));
vi.mock("../TestPlayground", () => ({
  default: forwardRef(function Playground() {
    return null;
  }),
}));
vi.mock("../FewShotExamples", () => ({ default: () => null }));
vi.mock("../CodeEvalEditor", () => ({
  default: () => null,
  PYTHON_CODE_TEMPLATE: "",
}));
vi.mock("../CompositeDetailPanel", () => ({ default: () => null }));

const lastPayload = () => mocks.mutate.mock.calls.at(-1)?.[0];

beforeEach(() => {
  vi.clearAllMocks();
  // An LLM judge drafted on the Agents tab: agent_mode stays "agent", and the
  // stored mapping is the Pass/Fail one from before the output change.
  mocks.draft = {
    id: "draft-1",
    eval_type: "llm",
    output_type_normalized: "pass_fail",
    config: {
      model: "jev-latest",
      agent_mode: "agent",
      jev_mapping: { revision: "jev-map-v1", question_type: "noul" },
    },
  };
});

describe("EvalCreatePage with a Jev model", () => {
  it("autosaves a mapping that follows the chosen output type", async () => {
    render(<EvalCreatePage />);
    await screen.findByDisplayValue("Jev latest");
    await userEvent.click(screen.getByLabelText("Scoring"));
    await userEvent.click(
      await screen.findByRole("button", { name: "Add level" }),
    );
    await userEvent.type(screen.getByLabelText("Level 0"), "Unresolved");
    await userEvent.click(screen.getByRole("button", { name: "Add level" }));
    await userEvent.type(screen.getByLabelText("Level 1"), "Resolved");
    await waitFor(
      () =>
        expect(lastPayload()).toEqual(
          expect.objectContaining({
            model: "jev-latest",
            output_type: "percentage",
            choice_scores: null,
            jev_mapping: expect.objectContaining({
              question_type: "score",
              score: { levels: ["Unresolved", "Resolved"] },
            }),
          }),
        ),
      { timeout: 4000 },
    );
  });

  it("autosaves a reopened draft's choices in the authored order", async () => {
    // choice_scores is jsonb, which returns keys shortest first; the mapping
    // labels and `choices` keep the order the author saved.
    const authored = ["Resolved", "Unresolved", "Escalated"];
    mocks.draft = {
      ...mocks.draft,
      output_type_normalized: "deterministic",
      choice_scores: { Resolved: 1, Escalated: 0.5, Unresolved: 0 },
      choices: authored,
      config: {
        model: "jev-latest",
        choices: authored,
        jev_mapping: {
          revision: "jev-map-v1",
          question_type: "choice",
          choice: { labels: authored, descriptions: {} },
        },
      },
    };
    render(<EvalCreatePage />);
    await userEvent.type(
      await screen.findByLabelText("Description for Unresolved"),
      "Still open",
    );
    await waitFor(
      () =>
        expect(lastPayload()?.jev_mapping?.choice?.descriptions).toEqual({
          Resolved: "",
          Unresolved: "Still open",
          Escalated: "",
        }),
      { timeout: 4000 },
    );
    expect(lastPayload().jev_mapping.choice.labels).toEqual(authored);
    expect(Object.keys(lastPayload().choice_scores)).toEqual(authored);
  });

  it("sends no jev_mapping once a Jev draft switches to Code", async () => {
    render(<EvalCreatePage />);
    await screen.findByDisplayValue("Jev latest");
    await userEvent.click(screen.getByRole("tab", { name: /Code/ }));
    await waitFor(() => expect(lastPayload()?.eval_type).toBe("code"), {
      timeout: 4000,
    });
    expect(lastPayload()).not.toHaveProperty("jev_mapping");
  });

  it("keeps Test off while Jev can't run the setup", async () => {
    // A judge with variables, scored on levels it hasn't named yet.
    mocks.draft = {
      ...mocks.draft,
      name: "Resolved?",
      instructions: "Did the agent resolve {{conversation}}?",
      output_type_normalized: "percentage",
      config: {
        model: "jev-latest",
        messages: [
          {
            role: "system",
            content: "Did the agent resolve {{conversation}}?",
          },
        ],
        jev_mapping: { revision: "jev-map-v1", question_type: "score" },
      },
    };
    render(<EvalCreatePage />);
    await screen.findByDisplayValue("Jev latest");
    expect(
      await screen.findByRole("button", { name: /Test Evaluation/ }),
    ).toBeDisabled();
    await userEvent.selectOptions(
      screen.getByLabelText("Evaluator model"),
      "turing_large",
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /Test Evaluation/ }),
      ).toBeEnabled(),
    );
  });

  it("says once when autosave is refused", async () => {
    mocks.mutate.mockImplementation((payload, options) =>
      options?.onError?.(new Error("refused")),
    );
    render(<EvalCreatePage />);
    const picker = await screen.findByLabelText("Evaluator model");
    await userEvent.selectOptions(picker, "turing_large");
    await waitFor(() => expect(mocks.snack).toHaveBeenCalledTimes(1), {
      timeout: 4000,
    });
    expect(mocks.snack).toHaveBeenCalledWith(
      "Your latest changes couldn't be saved.",
      { variant: "error" },
    );
    await userEvent.selectOptions(picker, "jev-latest");
    await waitFor(
      () => expect(mocks.mutate.mock.calls.length).toBeGreaterThan(1),
      {
        timeout: 4000,
      },
    );
    expect(mocks.snack).toHaveBeenCalledTimes(1);
  });

  it("sends no jev_mapping for non-Jev models", async () => {
    render(<EvalCreatePage />);
    await userEvent.selectOptions(
      await screen.findByLabelText("Evaluator model"),
      "turing_large",
    );
    await waitFor(() => expect(lastPayload()?.model).toBe("turing_large"), {
      timeout: 4000,
    });
    expect(lastPayload()).not.toHaveProperty("jev_mapping");
  });
});
