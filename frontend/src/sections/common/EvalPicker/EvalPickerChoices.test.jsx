import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render as renderWithTheme } from "src/utils/test-utils";
import {
  act,
  cleanup,
  screen,
  waitFor,
  fireEvent,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import EvalPickerProvider from "./context/EvalPickerProvider";
import EvalPickerCreateNew from "./EvalPickerCreateNew";
import EvalPickerConfigFull from "./EvalPickerConfigFull";

// Even a disabled jury query reads the client; isolate its cache per test.
let queryClient;
beforeEach(() => {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
});
afterEach(() => {
  cleanup();
  queryClient.clear();
});

const render = (ui, options) =>
  renderWithTheme(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
    options,
  );

// Release-lead 10-31: the eval picker offers the Choices output type, as the
// Create page does, and saves it with its score mapping. OutputTypeConfig is
// the real component here; everything around it is mocked as in
// EvalPickerCreateNew.test.jsx and EvalPickerConfigFull.test.jsx.

const {
  capturedProps,
  createDraft,
  updateDraft,
  stableUpdateEval,
  stableEvalDetail,
  stableVersions,
  stableCreateVersion,
  stableCompositeDetail,
  stableCreateComposite,
  stableUnionKeys,
} = vi.hoisted(() => {
  const updateDraftFn = vi.fn(async () => ({}));
  return {
    capturedProps: { dataset: null, tracing: null },
    createDraft: vi.fn(async () => ({ id: "draft-1" })),
    updateDraft: updateDraftFn,
    stableUpdateEval: { mutate: vi.fn(), mutateAsync: updateDraftFn },
    stableEvalDetail: { data: null, isLoading: false, isError: false },
    stableVersions: { data: { versions: [] } },
    stableCreateVersion: { mutateAsync: async () => ({}) },
    stableCompositeDetail: { data: null },
    stableCreateComposite: { mutateAsync: vi.fn() },
    stableUnionKeys: [],
  };
});

vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => false,
}));

vi.mock("src/sections/evals/components/DatasetTestMode", () => {
  const M = React.forwardRef((props, _ref) => {
    capturedProps.dataset = props;
    return <div data-testid="dataset-test-mode" />;
  });
  M.displayName = "DatasetTestModeMock";
  return { default: M, JsonValueTree: () => <div /> };
});

vi.mock("src/sections/evals/components/TracingTestMode", () => {
  const M = React.forwardRef((props, _ref) => {
    capturedProps.tracing = props;
    return <div data-testid="tracing-test-mode" />;
  });
  M.displayName = "TracingTestModeMock";
  return { default: M };
});

vi.mock("src/sections/evals/components/SimulationTestMode", () => {
  const M = React.forwardRef(() => <div />);
  M.displayName = "SimulationTestModeMock";
  return { default: M };
});

vi.mock("src/sections/evals/components/CreateSimulationPreviewMode", () => {
  const M = React.forwardRef(() => <div />);
  M.displayName = "CreateSimulationPreviewModeMock";
  return { default: M };
});

vi.mock("src/sections/evals/components/TestPlayground", () => {
  const M = React.forwardRef(() => <div />);
  M.displayName = "TestPlaygroundMock";
  return { default: M };
});

vi.mock("src/sections/evals/components/ModelSelector", () => ({
  default: () => <div />,
  FAGI_MODEL_VALUES: new Set(["turing_large", "turing_small", "turing_flash"]),
}));

vi.mock("src/sections/evals/components/InstructionEditor", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ value, onChange }) => (
    <textarea
      aria-label="Instructions"
      value={value ?? ""}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}));

vi.mock("src/sections/common/DecisionJudges/DecisionQuestionEditor", () => ({
  default: () => <div data-testid="decision-question-editor" />,
}));

vi.mock("src/sections/evals/components/LLMPromptEditor", () => ({
  default: () => <div data-testid="llm-prompt-editor" />,
}));

vi.mock("src/sections/evals/components/CodeEvalEditor", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/evals/components/FewShotExamples", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/evals/components/CompositeDetailPanel", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/tasks/components/TaskFilterBar", () => ({
  default: () => <div />,
}));

vi.mock(
  "src/sections/tasks/components/TaskLivePreview",
  async (importOriginal) => {
    const actual = await importOriginal();
    return { buildApiFilterArray: actual.buildApiFilterArray };
  },
);

vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({
  default: () => <div />,
}));

vi.mock("src/components/tooltip/CustomTooltip", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ children }) => <>{children}</>,
}));

vi.mock("src/sections/evals/hooks/useCreateEval", () => ({
  useCreateEval: () => ({ mutateAsync: createDraft }),
}));

vi.mock("src/sections/evals/hooks/useEvalDetail", () => ({
  useEvalDetail: () => stableEvalDetail,
  useUpdateEval: () => stableUpdateEval,
}));

vi.mock("src/sections/evals/hooks/useEvalVersions", () => ({
  useEvalVersions: () => stableVersions,
  useCreateEvalVersion: () => stableCreateVersion,
}));

vi.mock("src/sections/evals/hooks/useCompositeEval", () => ({
  useCompositeDetail: () => stableCompositeDetail,
  useCreateCompositeEval: () => stableCreateComposite,
}));

vi.mock("src/sections/evals/hooks/useCompositeChildrenKeys", () => ({
  useCompositeChildrenUnionKeys: () => stableUnionKeys,
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

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    mode: "cloud",
    isCloud: true,
    isOSS: false,
    isEE: false,
  }),
}));

vi.mock("notistack", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    enqueueSnackbar: vi.fn(),
    useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
  };
});

describe("picker Create new offers Choices", () => {
  beforeEach(() => {
    capturedProps.dataset = null;
    createDraft.mockClear();
    updateDraft.mockClear();
  });

  it("offers Pass/fail, Scoring and Choices and saves a Choices eval with its score mapping", async () => {
    const onSave = vi.fn();
    render(
      <EvalPickerProvider
        source="dataset"
        sourceId="dataset-1"
        sourceColumns={[]}
        existingEvals={[]}
        onEvalAdded={() => {}}
        onClose={() => {}}
      >
        <EvalPickerCreateNew onBack={() => {}} onSave={onSave} />
      </EvalPickerProvider>,
    );

    expect(screen.getByRole("radio", { name: "Pass/fail" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Scoring" })).toBeEnabled();
    const choices = screen.getByRole("radio", { name: "Choices" });
    expect(choices).toBeEnabled();

    fireEvent.change(screen.getByLabelText("Instructions"), {
      target: { value: "Judge {{output}} as Good or Bad." },
    });
    fireEvent.change(
      screen.getByPlaceholderText("e.g. hallucination_detector"),
      { target: { value: "tone_choices" } },
    );
    await act(async () => {
      capturedProps.dataset.onReadyChange(true, { output: "output-column" });
    });

    fireEvent.click(choices);
    expect(screen.getByRole("radio", { name: "Choices" })).toBeChecked();
    // Choosing Choices seeds one row of its score mapping.
    expect(screen.getByDisplayValue("Choice 1")).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Save & Add Evaluation" }),
      );
    });

    expect(createDraft).toHaveBeenCalledTimes(1);
    const publish = updateDraft.mock.calls.at(-1)[0];
    expect(publish).toMatchObject({
      name: "tone_choices",
      output_type: "deterministic",
      choice_scores: { "Choice 1": 0.5 },
      publish: true,
    });
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0]).toMatchObject({
      templateId: "draft-1",
      outputType: "deterministic",
      mapping: { output: "output-column" },
    });
  });
});

describe("picker configuration of a saved eval offers Choices", () => {
  const renderConfigFull = (onSave) =>
    render(
      <EvalPickerProvider
        source="task"
        sourceId="project-1"
        sourceRowType="traces"
        sourceColumns={[]}
        existingEvals={[]}
        onEvalAdded={() => {}}
        onClose={() => {}}
        initialEval={null}
      >
        <EvalPickerConfigFull
          evalData={{ id: "tpl-1", templateId: "tpl-1", name: "tone" }}
          onBack={() => {}}
          onSave={onSave}
          isSaving={false}
        />
      </EvalPickerProvider>,
    );

  const save = async (onSave) => {
    await waitFor(() => expect(capturedProps.tracing).toBeTruthy());
    await act(async () => {
      capturedProps.tracing.onReadyChange(true, { output: "output-column" });
    });
    fireEvent.click(
      screen.getByRole("button", { name: /Update Evaluation|Add Evaluation/ }),
    );
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    return onSave.mock.calls[0][0];
  };

  beforeEach(() => {
    capturedProps.tracing = null;
  });
  afterEach(() => {
    stableEvalDetail.data = null;
  });

  it("shows Choices beside a Scoring eval's categories and saves its score mapping", async () => {
    stableEvalDetail.data = {
      id: "tpl-1",
      name: "tone",
      owner: "user",
      eval_type: "llm",
      output_type: "percentage",
      instructions: "Judge {{output}}",
      choice_scores: { High: 1, Low: 0 },
      config: { model: "turing_large" },
    };
    const onSave = vi.fn();
    renderConfigFull(onSave);

    expect(
      await screen.findByRole("radio", { name: "Choices" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Scoring" })).toBeChecked();
    expect(screen.getByDisplayValue("High")).toBeEnabled();
    expect(screen.getByDisplayValue("Low")).toBeEnabled();

    const saved = await save(onSave);
    expect(saved).toMatchObject({
      outputType: "percentage",
      choice_scores: { High: 1, Low: 0 },
    });
  });

  it("keeps a Choices eval's score mapping editable and saves it on the binding", async () => {
    stableEvalDetail.data = {
      id: "tpl-1",
      name: "tone",
      owner: "user",
      eval_type: "llm",
      output_type: "deterministic",
      instructions: "Judge {{output}}",
      choice_scores: { Good: 1, Bad: 0 },
      config: { model: "turing_large" },
    };
    const onSave = vi.fn();
    renderConfigFull(onSave);

    expect(await screen.findByRole("radio", { name: "Choices" })).toBeChecked();
    expect(screen.getByDisplayValue("Good")).toBeEnabled();
    expect(screen.getByDisplayValue("Bad")).toBeEnabled();

    const saved = await save(onSave);
    expect(saved).toMatchObject({
      outputType: "deterministic",
      choice_scores: { Good: 1, Bad: 0 },
      config: { choice_scores: { Good: 1, Bad: 0 } },
    });
  });
});
