/* eslint-disable react/prop-types */
import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import EvalCreatePage from "../EvalCreatePage";
import axios from "src/utils/axios";
import { JEV_RUBRIC_ERROR } from "../../utils/jevMapping";

const mocks = vi.hoisted(() => ({
  draft: null,
  mutate: vi.fn(),
  snack: vi.fn(),
  navigate: vi.fn(),
  // When set, the capability hooks read Jev's entry from /api/capabilities/
  // for real: its answer, or "fail" for a fetch that fails.
  capabilities: null,
  realPlayground: false,
  realInstructionEditor: false,
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
vi.mock("src/hooks/useCapabilities", async (original) => {
  const actual = await original();
  const real = (id) => Boolean(mocks.capabilities) && id === "jev_models";
  return {
    ...actual,
    useFeatureLocked: (id) =>
      real(id)
        ? actual.useFeatureLocked(id)
        : { locked: false, isLoading: false },
    useFeatureDenied: (id) => (real(id) ? actual.useFeatureDenied(id) : false),
  };
});
vi.mock("src/hooks/useErrorLocalization", () => ({
  useErrorLocalizationAvailable: () => false,
}));
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(async (url) => {
      if (String(url).includes("/api/capabilities/")) {
        if (mocks.capabilities === "fail") throw new Error("unavailable");
        return { data: mocks.capabilities };
      }
      if (url === "/provider-status") {
        return {
          data: {
            result: { providers: [{ provider: "typesafe", has_key: true }] },
          },
        };
      }
      if (url === "/models") {
        return {
          data: {
            results: [{ model_name: "local-model", providers: "custom" }],
            next: null,
          },
        };
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
  useUpdateEval: () => ({ mutate: mocks.mutate, mutateAsync: vi.fn() }),
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
vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  ),
}));
vi.mock("../LLMPromptEditor", () => ({
  default: ({ model, onModelChange, messages, onMessagesChange }) => (
    <>
      <select
        aria-label="Evaluator model"
        value={model}
        onChange={(e) => onModelChange(e.target.value)}
      >
        <option value="jev-latest">Jev latest</option>
        <option value="turing_large">Turing Large</option>
      </select>
      <textarea
        aria-label="System prompt"
        value={messages?.find((m) => m.role === "system")?.content ?? ""}
        onChange={(e) =>
          onMessagesChange([{ role: "system", content: e.target.value }])
        }
      />
    </>
  ),
}));
vi.mock("src/components/PromptCards/PromptEditor", () => ({
  default: forwardRef(function PromptEditor() {
    return null;
  }),
}));
vi.mock("../InstructionEditor", async (original) => {
  const { default: Editor } = await original();
  return {
    default: (props) =>
      mocks.realInstructionEditor ? <Editor {...props} /> : null,
  };
});
vi.mock("../TestPlayground", async (original) => {
  const { default: Playground } = await original();
  return {
    default: forwardRef(function TestPlayground(props, ref) {
      return mocks.realPlayground ? <Playground {...props} ref={ref} /> : null;
    }),
  };
});
vi.mock("../FewShotExamples", () => ({ default: () => null }));
vi.mock("../CodeEvalEditor", () => ({
  default: () => null,
  PYTHON_CODE_TEMPLATE: "",
}));
vi.mock("../CompositeDetailPanel", () => ({ default: () => null }));

const lastPayload = () => mocks.mutate.mock.calls.at(-1)?.[0];

beforeEach(() => {
  vi.clearAllMocks();
  mocks.capabilities = null;
  mocks.realPlayground = false;
  mocks.realInstructionEditor = false;
  axios.post.mockResolvedValue({
    data: { status: true, result: { output: 1 } },
  });
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
  it.each(["auto", "agent"])(
    "preserves %s through Jev selection and evaluator tab changes",
    async (initialMode) => {
      mocks.realInstructionEditor = true;
      mocks.draft = {
        ...mocks.draft,
        eval_type: "agent",
        config: { model: "local-model", agent_mode: initialMode },
      };
      render(
        <QueryClientProvider client={new QueryClient()}>
          <EvalCreatePage />
        </QueryClientProvider>,
      );
      await userEvent.click(
        await screen.findByRole("button", {
          name: "Evaluation model: local-model",
        }),
      );
      await userEvent.click(
        await screen.findByRole("menuitem", { name: /Jev latest/ }),
      );
      expect(
        screen.getByRole("button", { name: "Evaluation mode: Quick" }),
      ).toBeInTheDocument();
      await userEvent.click(screen.getByRole("tab", { name: /LLM/ }));
      await screen.findByDisplayValue("Jev latest");
      await userEvent.click(screen.getByRole("tab", { name: /Agents/ }));
      await userEvent.click(
        screen.getByRole("button", { name: "Evaluation model: Jev latest" }),
      );
      await userEvent.click(
        await screen.findByRole("menuitem", { name: /local-model/ }),
      );
      expect(
        screen.getByRole("button", {
          name: `Evaluation mode: ${initialMode === "auto" ? "Auto" : "Agent"}`,
        }),
      ).toBeInTheDocument();
      await waitFor(() => expect(lastPayload()?.mode).toBe(initialMode), {
        timeout: 4000,
      });

      // Changing the model on the other tab must also restore the draft mode.
      await userEvent.click(
        screen.getByRole("button", { name: "Evaluation model: local-model" }),
      );
      await userEvent.click(
        await screen.findByRole("menuitem", { name: /Jev latest/ }),
      );
      await userEvent.click(screen.getByRole("tab", { name: /LLM/ }));
      await userEvent.selectOptions(
        await screen.findByLabelText("Evaluator model"),
        "turing_large",
      );
      await userEvent.click(screen.getByRole("tab", { name: /Agents/ }));
      expect(
        screen.getByRole("button", {
          name: `Evaluation mode: ${initialMode === "auto" ? "Auto" : "Agent"}`,
        }),
      ).toBeInTheDocument();
    },
  );

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

  it("doesn't flag or autosave a Score before its levels are written", async () => {
    // The server refuses a Score without levels; that refusal was a toast.
    mocks.mutate.mockImplementation((payload, options) => {
      if (
        payload.output_type === "percentage" &&
        !payload.jev_mapping.score.levels.length
      )
        options?.onError?.(
          new Error(
            "Jev score requires 2 to 10 unique non-empty rubric levels.",
          ),
        );
    });
    render(<EvalCreatePage />);
    await screen.findByDisplayValue("Jev latest");
    mocks.mutate.mockClear();
    await userEvent.click(screen.getByLabelText("Scoring"));
    await userEvent.click(
      await screen.findByRole("button", { name: "Add level" }),
    );
    await userEvent.type(screen.getByLabelText("Level 0"), "Unresolved");
    await new Promise((resolve) => setTimeout(resolve, 1000));
    expect(screen.queryByText(JEV_RUBRIC_ERROR)).toBeNull();
    expect(screen.getByText(/1 \/ 10 levels · minimum 2/)).toBeInTheDocument();
    expect(
      mocks.mutate.mock.calls.filter(
        ([payload]) => payload.output_type === "percentage",
      ),
    ).toEqual([]);
    expect(mocks.snack).not.toHaveBeenCalled();
    // A second level makes it runnable: now it saves.
    await userEvent.click(screen.getByRole("button", { name: "Add level" }));
    await userEvent.type(screen.getByLabelText("Level 1"), "Resolved");
    await waitFor(
      () =>
        expect(lastPayload()?.jev_mapping?.score).toEqual({
          levels: ["Unresolved", "Resolved"],
        }),
      { timeout: 4000 },
    );
    expect(mocks.snack).not.toHaveBeenCalled();
  });

  it("saves the prompt while it holds an unfinished Jev Score, and says so", async () => {
    render(<EvalCreatePage />);
    await screen.findByDisplayValue("Jev latest");
    await userEvent.click(screen.getByLabelText("Scoring"));
    expect(
      await screen.findByText(`Not saved yet: ${JEV_RUBRIC_ERROR}`),
    ).toBeInTheDocument();
    mocks.mutate.mockClear();
    await userEvent.type(
      screen.getByLabelText("System prompt"),
      "Was it resolved?",
    );
    // The prompt saves; the Jev part (model, output type, mapping) is held,
    // so the server keeps the last setup it could run.
    await waitFor(
      () =>
        expect(lastPayload()).toEqual(
          expect.objectContaining({
            instructions: "Was it resolved?",
            messages: [{ role: "system", content: "Was it resolved?" }],
          }),
        ),
      { timeout: 4000 },
    );
    for (const held of ["model", "output_type", "jev_mapping", "eval_type"])
      expect(lastPayload()).not.toHaveProperty(held);
    expect(mocks.snack).not.toHaveBeenCalled();
  });

  const leavePrevented = () => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  };

  it("asks before leaving while the Jev settings are held", async () => {
    render(<EvalCreatePage />);
    await screen.findByDisplayValue("Jev latest");
    expect(leavePrevented()).toBe(false);
    await userEvent.click(screen.getByLabelText("Scoring"));
    // The browser asks on a reload or a closed tab...
    expect(leavePrevented()).toBe(true);
    // ...and the page's own way out asks first.
    await userEvent.click(screen.getByText("Evals List"));
    expect(
      await screen.findByText("Leave without the Jev settings?"),
    ).toBeInTheDocument();
    expect(mocks.navigate).not.toHaveBeenCalledWith("/dashboard/evaluations");
    await userEvent.click(screen.getByRole("button", { name: "Leave" }));
    expect(mocks.navigate).toHaveBeenCalledWith("/dashboard/evaluations");
  });

  it("leaves without asking once the Jev setup can run", async () => {
    render(<EvalCreatePage />);
    await screen.findByDisplayValue("Jev latest");
    await userEvent.click(screen.getByLabelText("Scoring"));
    for (const [index, level] of ["Unresolved", "Resolved"].entries()) {
      await userEvent.click(
        await screen.findByRole("button", { name: "Add level" }),
      );
      await userEvent.type(screen.getByLabelText(`Level ${index}`), level);
    }
    expect(screen.queryByText(/Not saved yet/)).toBeNull();
    expect(leavePrevented()).toBe(false);
    await userEvent.click(screen.getByText("Evals List"));
    expect(screen.queryByText("Leave without the Jev settings?")).toBeNull();
    expect(mocks.navigate).toHaveBeenCalledWith("/dashboard/evaluations");
  });

  it("flags a Score whose levels repeat", async () => {
    render(<EvalCreatePage />);
    await screen.findByDisplayValue("Jev latest");
    await userEvent.click(screen.getByLabelText("Scoring"));
    for (const index of [0, 1]) {
      await userEvent.click(
        await screen.findByRole("button", { name: "Add level" }),
      );
      await userEvent.type(screen.getByLabelText(`Level ${index}`), "Same");
    }
    expect(await screen.findByText(JEV_RUBRIC_ERROR)).toBeInTheDocument();
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

  it("autosaves a Jev-to-Code switch, then tests it through the real playground without a Jev override", async () => {
    mocks.realPlayground = true;
    mocks.draft = {
      ...mocks.draft,
      code: "def evaluate(input, output, expected):\n    return 1",
    };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <EvalCreatePage />
      </QueryClientProvider>,
    );
    await screen.findByDisplayValue("Jev latest");
    await userEvent.click(screen.getByRole("tab", { name: /Code/ }));
    await waitFor(() => expect(lastPayload()?.eval_type).toBe("code"), {
      timeout: 4000,
    });
    expect(lastPayload().model).toBe("");
    expect(lastPayload()).not.toHaveProperty("jev_mapping");
    await userEvent.click(
      screen.getByRole("button", { name: /Test Evaluation/ }),
    );
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        "/playground",
        expect.objectContaining({ template_id: "draft-1" }),
      ),
    );
    const request = axios.post.mock.calls.find(
      ([url]) => url === "/playground",
    )[1];
    expect(request).not.toHaveProperty("model");
    expect(request.config).toHaveProperty("params", {});
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

  describe("with the deployment's capabilities read for real", () => {
    // A judge Jev can run: Pass/Fail over a variable.
    const runnable = () => ({
      ...mocks.draft,
      name: "Resolved?",
      instructions: "Did the agent resolve {{conversation}}?",
      config: {
        ...mocks.draft.config,
        messages: [
          {
            role: "system",
            content: "Did the agent resolve {{conversation}}?",
          },
        ],
      },
    });

    const renderPage = () =>
      render(
        <QueryClientProvider client={new QueryClient()}>
          <EvalCreatePage />
        </QueryClientProvider>,
      );

    it("leaves Jev testable when the capabilities can't be read", async () => {
      mocks.draft = runnable();
      mocks.capabilities = "fail";
      renderPage();
      await screen.findByDisplayValue("Jev latest");
      // Past the fetch's retry: failed, which is no denial.
      await new Promise((resolve) => setTimeout(resolve, 1500));
      expect(
        screen.getByRole("button", { name: /Test Evaluation/ }),
      ).toBeEnabled();
    });

    it("locks Jev on the plan's denial", async () => {
      mocks.draft = runnable();
      mocks.capabilities = { features: { jev_models: { allowed: false } } };
      renderPage();
      await screen.findByDisplayValue("Jev latest");
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: /Test Evaluation/ }),
        ).toBeDisabled(),
      );
    });
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
