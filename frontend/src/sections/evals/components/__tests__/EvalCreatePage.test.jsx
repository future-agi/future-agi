import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent, userEvent } from "src/utils/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import EvalCreatePage from "../EvalCreatePage";

// vi.hoisted since these are referenced inside the hoisted vi.mock factory
// below, but they're also used directly in test bodies further down.
const CREATE_URL = vi.hoisted(() => "/model-hub/eval-templates/create-v2/");
const getDetailUrl = vi.hoisted(
  () => (id) => `/model-hub/eval-templates/${id}/detail/`,
);
const getUpdateUrl = vi.hoisted(
  () => (id) => `/model-hub/eval-templates/${id}/update/`,
);
const COMPOSITE_CREATE_URL = vi.hoisted(
  () => "/model-hub/eval-templates/composite/create/",
);
const COMPOSITE_ADHOC_URL = vi.hoisted(
  () => "/model-hub/eval-templates/composite/execute-adhoc/",
);

const axiosGetMock = vi.hoisted(() => vi.fn());
const axiosPostMock = vi.hoisted(() => vi.fn());
const axiosPutMock = vi.hoisted(() => vi.fn());
const mockNavigate = vi.hoisted(() => vi.fn());
const enqueueSnackbarMock = vi.hoisted(() => vi.fn());
// Mutable so individual tests can simulate the `/create` vs
// `/create/:draftId` route without needing a real Router + route match.
const routeParams = vi.hoisted(() => ({ draftId: undefined }));

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => axiosGetMock(...args),
    post: (...args) => axiosPostMock(...args),
    put: (...args) => axiosPutMock(...args),
  },
  endpoints: {
    develop: {
      eval: {
        createEvalTemplateV2: CREATE_URL,
        getEvalDetail: getDetailUrl,
        updateEvalTemplate: getUpdateUrl,
        evalPlayground: "/model-hub/eval-playground/",
        getEvalVersions: (id) => `/model-hub/eval-templates/${id}/versions/`,
        getEvalLogs: "/model-hub/get-eval-logs",
        aiEvalWriter: "/model-hub/ai-eval-writer/",
        createCompositeEval: COMPOSITE_CREATE_URL,
        executeCompositeEval: (id) =>
          `/model-hub/eval-templates/${id}/composite/execute/`,
        executeCompositeEvalAdhoc: COMPOSITE_ADHOC_URL,
        setDefaultVersion: (id, versionId) =>
          `/model-hub/eval-templates/${id}/versions/${versionId}/set-default/`,
      },
    },
  },
}));

// EvalCreatePage imports useNavigate/useParams from "react-router" (not
// react-router-dom). Mocking both directly — rather than wiring up a real
// MemoryRouter — lets each test drive the `/create` vs `/create/:draftId`
// branch without a route match, mirroring CreateNewPrompt.test.jsx.
vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useParams: () => routeParams,
  };
});

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));

vi.mock("src/hooks/useCapabilities", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useFeatureAllowed: () => ({ allowed: true, isLoading: false }),
    useFeatureLocked: () => ({ locked: false, isLoading: false }),
    useCapabilities: () => ({ data: undefined, isLoading: false }),
  };
});

vi.mock("notistack", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useSnackbar: () => ({ enqueueSnackbar: enqueueSnackbarMock }),
  };
});

// TestPlayground's Custom-tab JSON editor is Monaco-based — swap for a plain
// textarea, same as CustomJsonInput.test.jsx / TestPlayground.test.jsx.
vi.mock("../CodeEditor", () => ({
  default: ({ value, onChange }) => (
    <textarea
      data-testid="json-editor"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));

const { capturedProps } = vi.hoisted(() => ({
  capturedProps: { llmPrompt: null, outputType: null, composite: null },
}));

vi.mock("../LLMPromptEditor", () => ({
  default: (props) => {
    capturedProps.llmPrompt = props;
    return <div data-testid="llm-prompt-editor" />;
  },
}));

vi.mock("../InstructionEditor", () => ({
  default: () => <div data-testid="instruction-editor" />,
}));

vi.mock("../OutputTypeConfig", () => ({
  default: (props) => {
    capturedProps.outputType = props;
    return <div data-testid="output-type-config" />;
  },
}));

vi.mock("../FewShotExamples", () => ({
  default: () => <div />,
}));

vi.mock("../CodeEvalEditor", () => ({
  default: () => <div />,
  PYTHON_CODE_TEMPLATE: "# python code template",
}));

// CompositeDetailPanel owns the child-picker UI (its own behavior is
// covered by CompositeDetailPanel.test.jsx). Here we only need to drive
// EvalCreatePage's composite state — name, children, weights — so a thin
// interactive stand-in captures the props and exposes a couple of controls.
vi.mock("../CompositeDetailPanel", () => ({
  default: (props) => {
    capturedProps.composite = props;
    return (
      <div data-testid="composite-detail-panel">
        <input
          placeholder="Composite name"
          value={props.name}
          onChange={(e) => props.onNameChange(e.target.value)}
        />
        <button
          type="button"
          onClick={() =>
            props.onChildrenChange([
              { child_id: "c1", child_name: "Child One", order: 0, weight: 1 },
            ])
          }
        >
          add child
        </button>
        <button
          type="button"
          onClick={() => props.onChildrenChange([])}
        >
          clear children
        </button>
      </div>
    );
  },
}));

function mockCreateDraft(id = "draft-1", { pending = false } = {}) {
  axiosPostMock.mockImplementation((url) => {
    if (String(url).includes("create-v2")) {
      if (pending) return new Promise(() => {}); // never resolves
      return Promise.resolve({ data: { result: { id } } });
    }
    return Promise.resolve({ data: { result: {} } });
  });
}

function mockDraftDetail(id, detail) {
  axiosGetMock.mockImplementation((url) => {
    if (url === getDetailUrl(id)) {
      return Promise.resolve({ data: { result: detail } });
    }
    return Promise.resolve({ data: { result: {} } });
  });
}

function renderCreatePage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <EvalCreatePage />
    </QueryClientProvider>,
  );
}

describe("EvalCreatePage", () => {
  beforeEach(() => {
    routeParams.draftId = undefined;
    axiosGetMock.mockReset();
    axiosGetMock.mockResolvedValue({ data: { result: {} } });
    axiosPostMock.mockReset();
    axiosPostMock.mockResolvedValue({ data: { result: {} } });
    axiosPutMock.mockReset();
    axiosPutMock.mockResolvedValue({ data: { result: {} } });
    mockNavigate.mockReset();
    enqueueSnackbarMock.mockReset();
    capturedProps.llmPrompt = null;
    capturedProps.outputType = null;
    capturedProps.composite = null;
  });

  it("auto-creates a draft template on mount with is_draft true", async () => {
    mockCreateDraft("draft-99");
    renderCreatePage();

    await waitFor(() =>
      expect(axiosPostMock).toHaveBeenCalledWith(CREATE_URL, {
        is_draft: true,
        eval_type: "agent",
        output_type: "pass_fail",
        model: "turing_large",
        pass_threshold: 0.5,
      }),
    );
  });

  it("moves the URL to the newly created draft id once the create call resolves", async () => {
    mockCreateDraft("draft-99");
    renderCreatePage();

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith(
        "/dashboard/evaluations/create/draft-99",
        { replace: true },
      ),
    );
  });

  it("does not block Save on the draft being ready, but the handler itself refuses to run without a draft id", async () => {
    // Simulate the draft POST never resolving — the create-page's only real
    // gate against saving/testing before the draft exists lives inside the
    // save/test handlers themselves (an early-return + warning snackbar),
    // not a disabled prop on the buttons.
    mockCreateDraft("draft-1", { pending: true });
    renderCreatePage();

    // Switch to the Code tab so `canSaveSingle` is satisfied by the default
    // PYTHON_CODE_TEMPLATE without needing to drive the mocked editors.
    fireEvent.click(screen.getByRole("tab", { name: "Code" }));
    await userEvent.type(
      screen.getByPlaceholderText("Eg: Hallucination detector"),
      "my_eval",
    );

    const saveButton = await screen.findByRole("button", {
      name: "Save Evaluation",
    });
    expect(saveButton).not.toBeDisabled();

    fireEvent.click(saveButton);

    await waitFor(() =>
      expect(enqueueSnackbarMock).toHaveBeenCalledWith(
        "Draft not ready yet, please try again",
        { variant: "warning" },
      ),
    );
    expect(mockNavigate).not.toHaveBeenCalledWith(
      expect.stringContaining("/dashboard/evaluations/draft-1"),
    );
  });

  it("disables Test Evaluation when a resumed draft's instructions have no template variable", async () => {
    routeParams.draftId = "draft-1";
    mockDraftDetail("draft-1", {
      eval_type: "llm",
      instructions: "Check the following text for toxicity",
      output_type_normalized: "pass_fail",
      config: {},
    });
    renderCreatePage();

    const button = await screen.findByRole("button", {
      name: "Test Evaluation",
    });
    await waitFor(() => expect(button).toBeDisabled());
  });

  it("enables Test Evaluation once a resumed draft's instructions contain a template variable", async () => {
    routeParams.draftId = "draft-1";
    mockDraftDetail("draft-1", {
      eval_type: "llm",
      instructions: "Check the following text for toxicity: {{input}}",
      output_type_normalized: "pass_fail",
      config: {},
    });
    renderCreatePage();

    // Wait for the resumed draft's GET to resolve and populate state before
    // reading the button's disabled attribute — otherwise this can race the
    // async load and observe the pre-resume (disabled) render.
    await screen.findByTestId("llm-prompt-editor");
    const button = await screen.findByRole("button", {
      name: "Test Evaluation",
    });
    await waitFor(() => expect(button).not.toBeDisabled());
  });

  it("populates the LLM prompt editor's model/messages from the resumed draft", async () => {
    routeParams.draftId = "draft-1";
    mockDraftDetail("draft-1", {
      eval_type: "llm",
      instructions: "Rate this: {{input}}",
      output_type_normalized: "percentage",
      config: { model: "gpt-4o", messages: [{ role: "system", content: "Rate this: {{input}}" }] },
    });
    renderCreatePage();

    await screen.findByTestId("llm-prompt-editor");

    await waitFor(() => expect(capturedProps.llmPrompt?.model).toBe("gpt-4o"));
    expect(
      capturedProps.llmPrompt?.messages?.some((m) =>
        m.content?.includes("Rate this"),
      ),
    ).toBe(true);
    await waitFor(() =>
      expect(capturedProps.outputType?.outputType).toBe("percentage"),
    );
  });

  it("runs Testing... then Test completed after a successful test on a resumed draft", async () => {
    routeParams.draftId = "draft-1";
    mockDraftDetail("draft-1", {
      eval_type: "llm",
      instructions: "Rate this: {{input}}",
      output_type_normalized: "pass_fail",
      config: {},
    });
    axiosPostMock.mockImplementation((url) => {
      if (String(url).includes("eval-playground")) {
        return Promise.resolve({
          data: {
            status: true,
            result: {
              output: "Passed",
              output_type: "Pass/Fail",
              reason: "Looks fine",
            },
          },
        });
      }
      return Promise.resolve({ data: { result: {} } });
    });
    // handleTestEvaluation awaits the draft PUT before triggering the
    // playground run; with every mock resolving as an instant microtask the
    // "Testing..." interim render can get coalesced away before any
    // assertion observes it. A one-tick macrotask delay on the PUT forces a
    // real intermediate commit so the transient state is observable.
    axiosPutMock.mockImplementation(
      () =>
        new Promise((resolve) =>
          setTimeout(() => resolve({ data: { result: {} } }), 10),
        ),
    );
    renderCreatePage();

    await screen.findByTestId("llm-prompt-editor");
    const button = await screen.findByRole("button", {
      name: "Test Evaluation",
    });
    await waitFor(() => expect(button).not.toBeDisabled());

    fireEvent.click(button);

    expect(
      await screen.findByRole("button", { name: "Testing..." }),
    ).toBeInTheDocument();

    // The draft is re-saved (PUT) before the test itself runs.
    await waitFor(() =>
      expect(axiosPutMock).toHaveBeenCalledWith(
        getUpdateUrl("draft-1"),
        expect.objectContaining({ eval_type: "llm" }),
      ),
    );

    expect(await screen.findByText("Test completed")).toBeInTheDocument();
  });

  it("publishes the draft on Save: PUTs publish:true and navigates to the eval detail page", async () => {
    routeParams.draftId = "draft-1";
    mockDraftDetail("draft-1", {
      eval_type: "llm",
      instructions: "Rate this: {{input}}",
      output_type_normalized: "pass_fail",
      config: {},
    });
    renderCreatePage();

    // Resumed drafts don't restore a name — the user must supply one before
    // saving/publishing.
    await screen.findByTestId("llm-prompt-editor");
    await userEvent.type(
      screen.getByPlaceholderText("Eg: Hallucination detector"),
      "my_eval",
    );

    const saveButton = await screen.findByRole("button", {
      name: "Save Evaluation",
    });
    await waitFor(() => expect(saveButton).not.toBeDisabled());

    fireEvent.click(saveButton);

    await waitFor(() =>
      expect(axiosPutMock).toHaveBeenCalledWith(
        getUpdateUrl("draft-1"),
        expect.objectContaining({ name: "my_eval", publish: true }),
      ),
    );

    expect(enqueueSnackbarMock).toHaveBeenCalledWith(
      "Evaluation saved successfully",
      { variant: "success" },
    );
    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith(
        "/dashboard/evaluations/draft-1",
      ),
    );
  });

  describe("composite mode", () => {
    async function switchToComposite() {
      renderCreatePage();
      fireEvent.click(screen.getByRole("tab", { name: "Composite" }));
      await screen.findByTestId("composite-detail-panel");
    }

    it("renders the composite children editor and marks it editable", async () => {
      await switchToComposite();

      expect(capturedProps.composite).toMatchObject({
        editable: true,
        name: "",
        children: [],
      });
    });

    it("disables Save until a name and at least one child are present", async () => {
      await switchToComposite();

      // CustomTooltip swaps its wrapped element type (Tooltip vs. a bare
      // fragment) depending on whether `show` is true, which remounts the
      // Button underneath as a new DOM node each time `saveDisabled`
      // flips — so the button must be re-queried fresh after every
      // interaction rather than reusing one captured reference.
      const getSaveButton = () =>
        screen.getByRole("button", { name: "Save Evaluation" });

      await waitFor(() => expect(getSaveButton()).toBeDisabled());

      fireEvent.change(screen.getByPlaceholderText("Composite name"), {
        target: { value: "my composite" },
      });
      expect(getSaveButton()).toBeDisabled();

      fireEvent.click(screen.getByRole("button", { name: "add child" }));
      await waitFor(() => expect(getSaveButton()).not.toBeDisabled());

      fireEvent.click(screen.getByRole("button", { name: "clear children" }));
      await waitFor(() => expect(getSaveButton()).toBeDisabled());
    });

    it("creates the composite via the composite/create endpoint and navigates to its detail page", async () => {
      axiosPostMock.mockImplementation((url) => {
        if (String(url).includes("create-v2")) {
          return Promise.resolve({ data: { result: { id: "draft-1" } } });
        }
        if (url === COMPOSITE_CREATE_URL) {
          return Promise.resolve({ data: { result: { id: "composite-9" } } });
        }
        return Promise.resolve({ data: { result: {} } });
      });
      await switchToComposite();

      fireEvent.change(screen.getByPlaceholderText("Composite name"), {
        target: { value: "  my composite  " },
      });
      fireEvent.click(screen.getByRole("button", { name: "add child" }));

      const saveButton = await screen.findByRole("button", {
        name: "Save Evaluation",
      });
      await waitFor(() => expect(saveButton).not.toBeDisabled());
      fireEvent.click(saveButton);

      await waitFor(() =>
        expect(axiosPostMock).toHaveBeenCalledWith(COMPOSITE_CREATE_URL, {
          name: "my composite",
          description: null,
          child_template_ids: ["c1"],
          child_configs: {},
          aggregation_enabled: true,
          aggregation_function: "weighted_avg",
          composite_child_axis: "pass_fail",
          child_weights: null,
          child_pinned_versions: null,
        }),
      );

      expect(enqueueSnackbarMock).toHaveBeenCalledWith(
        "Composite evaluation created successfully",
        { variant: "success" },
      );
      await waitFor(() =>
        expect(mockNavigate).toHaveBeenCalledWith(
          "/dashboard/evaluations/composite-9",
        ),
      );
    });

    it("surfaces the backend error message when composite creation fails", async () => {
      axiosPostMock.mockImplementation((url) => {
        if (String(url).includes("create-v2")) {
          return Promise.resolve({ data: { result: { id: "draft-1" } } });
        }
        if (url === COMPOSITE_CREATE_URL) {
          return Promise.reject({
            // 400, not a bare rejection: getSafeActionErrorMessage only
            // forwards a backend string on a validation status (400/404/
            // 409/422) and falls back to the generic copy otherwise.
            response: {
              status: 400,
              data: { result: "duplicate composite name" },
            },
          });
        }
        return Promise.resolve({ data: { result: {} } });
      });
      await switchToComposite();

      fireEvent.change(screen.getByPlaceholderText("Composite name"), {
        target: { value: "my composite" },
      });
      fireEvent.click(screen.getByRole("button", { name: "add child" }));

      const saveButton = await screen.findByRole("button", {
        name: "Save Evaluation",
      });
      await waitFor(() => expect(saveButton).not.toBeDisabled());
      fireEvent.click(saveButton);

      await waitFor(() =>
        expect(enqueueSnackbarMock).toHaveBeenCalledWith(
          "duplicate composite name",
          { variant: "error" },
        ),
      );
      expect(mockNavigate).not.toHaveBeenCalledWith(
        expect.stringContaining("/dashboard/evaluations/"),
      );
    });

    it("runs a composite test via the adhoc endpoint without touching the draft, and skips the single-eval draft-ready gate", async () => {
      axiosPostMock.mockImplementation((url) => {
        if (String(url).includes("create-v2")) {
          return Promise.resolve({ data: { result: { id: "draft-1" } } });
        }
        if (url === COMPOSITE_ADHOC_URL) {
          return Promise.resolve({
            data: {
              result: {
                aggregation_enabled: true,
                aggregate_score: 0.8,
                summary: "looks good",
              },
            },
          });
        }
        return Promise.resolve({ data: { result: {} } });
      });
      await switchToComposite();

      fireEvent.click(screen.getByRole("button", { name: "add child" }));

      const testButton = await screen.findByRole("button", {
        name: "Test Evaluation",
      });
      await waitFor(() => expect(testButton).not.toBeDisabled());
      fireEvent.click(testButton);

      await waitFor(() =>
        expect(axiosPostMock).toHaveBeenCalledWith(
          COMPOSITE_ADHOC_URL,
          expect.objectContaining({
            child_template_ids: ["c1"],
            aggregation_enabled: true,
            aggregation_function: "weighted_avg",
            composite_child_axis: "pass_fail",
            child_weights: null,
            pass_threshold: 0.5,
            mapping: {},
          }),
        ),
      );

      // Composite tests bypass the single-eval draft update entirely.
      expect(axiosPutMock).not.toHaveBeenCalled();
      expect(await screen.findByText("Test completed")).toBeInTheDocument();
    });

    it("warns before discarding a passed single-eval test when switching to Composite mode", async () => {
      routeParams.draftId = "draft-1";
      mockDraftDetail("draft-1", {
        eval_type: "llm",
        instructions: "Rate this: {{input}}",
        output_type_normalized: "pass_fail",
        config: {},
      });
      axiosPostMock.mockImplementation((url) => {
        if (String(url).includes("eval-playground")) {
          return Promise.resolve({
            data: {
              status: true,
              result: { output: "Passed", output_type: "Pass/Fail", reason: "ok" },
            },
          });
        }
        return Promise.resolve({ data: { result: {} } });
      });
      renderCreatePage();

      await screen.findByTestId("llm-prompt-editor");
      const testButton = await screen.findByRole("button", {
        name: "Test Evaluation",
      });
      await waitFor(() => expect(testButton).not.toBeDisabled());
      fireEvent.click(testButton);
      expect(await screen.findByText("Test completed")).toBeInTheDocument();

      fireEvent.click(screen.getByRole("tab", { name: "Composite" }));

      expect(
        await screen.findByText(
          "Switching to Composite will clear your current test results. Continue?",
        ),
      ).toBeInTheDocument();
      // Still on Single — the composite panel hasn't mounted yet.
      expect(
        screen.queryByTestId("composite-detail-panel"),
      ).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

      await screen.findByTestId("composite-detail-panel");
      expect(screen.queryByText("Test completed")).not.toBeInTheDocument();
    });
  });
});
