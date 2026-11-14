import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, userEvent } from "src/utils/test-utils";
import { screen, waitFor, within } from "@testing-library/react";

import EvalPickerProvider from "./context/EvalPickerProvider";
import EvalPickerConfigFull from "./EvalPickerConfigFull";

const { capturedProps } = vi.hoisted(() => ({
  capturedProps: { tracing: null, llmPrompt: null, instruction: null },
}));

vi.mock("src/sections/evals/components/TracingTestMode", () => {
  const M = React.forwardRef((props, _ref) => {
    capturedProps.tracing = props;
    return <div data-testid="tracing-test-mode" />;
  });
  M.displayName = "TracingTestModeMock";
  return { default: M };
});

vi.mock("src/sections/evals/components/DatasetTestMode", () => {
  const M = React.forwardRef(() => <div />);
  M.displayName = "DatasetTestModeMock";
  return { default: M, JsonValueTree: () => <div /> };
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

vi.mock("src/api/evaluations/decision-models", () => ({
  default: () => ({ data: [] }),
  decisionModelsQueryKey: ["eval-decision-models"],
}));
vi.mock("src/sections/evals/components/ModelSelector", () => ({
  default: () => <div />,
  FAGI_MODEL_VALUES: new Set(),
}));

vi.mock("src/sections/evals/components/InstructionEditor", () => ({
  default: (props) => {
    capturedProps.instruction = props;
    return <div />;
  },
}));

// LLMPromptEditor renders the ModelSelector inline, so its `model` prop is
// the hydrated model the user actually sees in the chip.
vi.mock("src/sections/evals/components/LLMPromptEditor", () => ({
  default: (props) => {
    capturedProps.llmPrompt = props;
    return <div data-testid="llm-prompt-editor" />;
  },
}));

vi.mock("src/sections/evals/components/CodeEvalEditor", () => ({
  default: () => <div data-testid="custom-code-editor" />,
}));

vi.mock("src/sections/evals/components/OutputTypeConfig", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/evals/components/FewShotExamples", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/tasks/components/TaskFilterBar", () => ({
  default: () => <div />,
}));

// Renders the tooltip title as plain text whenever `show` is true, so tests
// can assert on the disabled-reason copy without simulating a real hover.
vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ show, title, children }) => (
    <span data-tooltip-host>
      {children}
      {show && title ? <span data-testid="tooltip-text">{title}</span> : null}
    </span>
  ),
}));

// Transitive import of the real TaskLivePreview (needed for the real
// buildApiFilterArray); its module-scope localStorage read breaks under
// the test environment.
vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({
  default: () => <div />,
}));

// Hook mocks must return referentially stable values — a fresh object per
// call re-triggers every downstream useMemo/useEffect and loops the render.
const {
  stableEvalDetail,
  stableUpdateEval,
  stableVersions,
  stableCreateVersion,
  stableCompositeDetail,
  stableUnionKeys,
} = vi.hoisted(() => ({
  stableEvalDetail: {
    data: {
      id: "tpl-1",
      name: "toxicity",
      owner: "system",
      eval_type: "agent",
      output_type: "pass_fail",
      instructions: "Template instructions",
      config: { model: "template-model", tools: { template: true } },
    },
    isLoading: false,
    isError: false,
  },
  stableUpdateEval: { mutate: () => {}, mutateAsync: async () => ({}) },
  stableVersions: { data: { versions: [] } },
  stableCreateVersion: { mutateAsync: async () => ({}) },
  stableCompositeDetail: { data: null },
  stableUnionKeys: [],
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
    useFeatureDenied: () => false,
    useCapabilities: () => ({ data: undefined, isLoading: false }),
  };
});
const { deploymentMode } = vi.hoisted(() => ({
  // Mutable on purpose: TH-7177 tests flip the mode per test. Reset in the
  // gating suite's beforeEach; default matches cloud so other suites keep
  // seeing the pre-existing UI.
  deploymentMode: { mode: "cloud", isCloud: true, isOSS: false, isEE: false },
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => deploymentMode,
}));

vi.mock("notistack", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    enqueueSnackbar: vi.fn(),
    useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
  };
});

const TIME_WINDOW = {
  startDate: "2025-05-18T13:37:41.000Z",
  endDate: "2026-05-18T18:29:59.000Z",
};

const DEFAULT_EVAL_DATA = {
  id: "tpl-1",
  templateId: "tpl-1",
  name: "toxicity",
};

const DEFAULT_DETAIL = {
  id: "tpl-1",
  name: "toxicity",
  eval_type: "llm",
  output_type: "pass_fail",
  config: {},
};

const configFullTree = ({
  sourceTimeWindow,
  evalData,
  requireInputs,
  onSave = () => {},
} = {}) => (
  <EvalPickerProvider
    source="task"
    sourceId="project-1"
    sourceRowType="traces"
    sourceColumns={[]}
    existingEvals={[]}
    onEvalAdded={() => {}}
    onClose={() => {}}
    sourceTimeWindow={sourceTimeWindow}
    initialEval={evalData || null}
    requireInputs={requireInputs}
  >
    <EvalPickerConfigFull
      evalData={evalData || DEFAULT_EVAL_DATA}
      onBack={() => {}}
      onSave={onSave}
      isSaving={false}
    />
  </EvalPickerProvider>
);

const renderConfigFull = (options = {}) => render(configFullTree(options));

it("attaches a registered function without requiring or editing sandbox code", async () => {
  const previous = stableEvalDetail.data;
  stableEvalDetail.data = {
    id: "tpl-1", templateId: "tpl-1", name: "tool_call_loop_repetition", owner: "system",
    eval_type: "code", output_type: "pass_fail", required_keys: [],
    config: { eval_type_id: "ToolCallLoopRepetition", required_keys: [],
      function_params_schema: { window_size: { type: "integer", default: 100 } } },
  };
  try {
    const onSave = vi.fn();
    renderConfigFull({ onSave });
    await act(async () => capturedProps.tracing.onReadyChange(true, {}));
    await waitFor(() => expect(screen.getByRole("button", { name: "Add Evaluation" })).toBeEnabled());
    expect(screen.queryByTestId("custom-code-editor")).not.toBeInTheDocument();
    expect(screen.getByText(/Built-in function evaluator/)).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("window_size"));
    await userEvent.type(screen.getByLabelText("window_size"), "8");
    await userEvent.click(screen.getByRole("button", { name: "Add Evaluation" }));
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
      templateId: "tpl-1", params: { window_size: 8 },
    }));
  } finally {
    stableEvalDetail.data = previous;
  }
});

it("authors a decision rubric in the attach drawer and forwards the exact route and mapping to preview and binding", async () => {
  const previous = stableEvalDetail.data;
  const route = "decision/openrouter/openai/gpt-6-luna-decisions";
  stableEvalDetail.data = {
    id: "tpl-1",
    name: "quality",
    owner: "user",
    eval_type: "llm",
    output_type: "percentage",
    instructions: "Rate {{input}}",
    required_keys: ["input"],
    config: { model: route, output: "score" },
  };
  try {
    const onSave = vi.fn();
    renderConfigFull({ onSave });
    await waitFor(() => expect(capturedProps.llmPrompt?.model).toBe(route));
    expect(capturedProps.llmPrompt.outputType).toBe("percentage");
    await act(async () =>
      capturedProps.tracing.onReadyChange(true, { input: "output" }),
    );
    expect(
      screen.getByRole("button", { name: "Add Evaluation" }),
    ).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Add level" }));
    await userEvent.type(screen.getByLabelText("Level 0"), "Incorrect");
    await userEvent.click(screen.getByRole("button", { name: "Add level" }));
    await userEvent.type(screen.getByLabelText("Level 1"), "Correct");
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Add Evaluation" }),
      ).toBeEnabled(),
    );
    expect(capturedProps.tracing.runConfig).toMatchObject({
      model: route,
      output_type: "score",
      jev_mapping: {
        question_type: "score",
        score: { levels: ["Incorrect", "Correct"] },
      },
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Add Evaluation" }),
    );
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        model: route,
        jev_mapping: expect.objectContaining({ question_type: "score" }),
        config: expect.objectContaining({
          run_config: expect.objectContaining({
            model: route,
            jev_mapping: expect.objectContaining({
              score: { levels: ["Incorrect", "Correct"] },
            }),
          }),
        }),
      }),
    );
  } finally {
    stableEvalDetail.data = previous;
  }
});

it("reopens a decision score binding without replacing it with the template's Pass/Fail output", async () => {
  const previous = stableEvalDetail.data;
  const route = "decision/openrouter/morph/reflex";
  stableEvalDetail.data = {
    ...DEFAULT_DETAIL,
    owner: "user",
    instructions: "Judge {{input}}",
    required_keys: ["input"],
    config: { model: "ordinary-model", output: "Pass/Fail" },
  };
  try {
    renderConfigFull({
      evalData: {
        ...DEFAULT_EVAL_DATA,
        userEvalId: "binding-decision-score",
        runConfig: {
          model: route,
          output_type: "score",
          jev_mapping: { score: { levels: ["Incorrect", "Correct"] } },
        },
      },
    });
    await waitFor(() => expect(capturedProps.llmPrompt?.model).toBe(route));
    expect(capturedProps.llmPrompt.outputType).toBe("percentage");
    expect(screen.getByLabelText("Level 0")).toHaveValue("Incorrect");
    expect(capturedProps.tracing.runConfig).toMatchObject({
      model: route,
      output_type: "score",
      jev_mapping: {
        question_type: "score",
        score: { levels: ["Incorrect", "Correct"] },
      },
    });
  } finally {
    stableEvalDetail.data = previous;
  }
});

describe("pinned decision binding hydration", () => {
  const route = "decision/openrouter/morph/reflex";
  const rubric = (label) => ({
    score: { levels: [`${label} bad`, `${label} good`] },
  });
  const snapshot = (label) => ({
    model: route,
    output: "score",
    rule_prompt: "Judge {{input}}",
    jev_mapping: rubric(label),
  });
  let previousDetail;
  let previousVersions;

  beforeEach(() => {
    previousDetail = stableEvalDetail.data;
    previousVersions = stableVersions.data;
    stableEvalDetail.data = {
      ...DEFAULT_DETAIL,
      owner: "user",
      output_type: "percentage",
      instructions: "Judge {{input}}",
      required_keys: ["input"],
      config: snapshot("Current"),
    };
    stableVersions.data = {
      versions: [
        {
          id: "v1",
          version_number: 1,
          config_snapshot: snapshot("Version one"),
        },
        {
          id: "v2",
          version_number: 2,
          config_snapshot: snapshot("Version two"),
        },
      ],
    };
  });

  afterEach(() => {
    stableEvalDetail.data = previousDetail;
    stableVersions.data = previousVersions;
  });

  const savedBinding = () => ({
    ...DEFAULT_EVAL_DATA,
    name: "saved_quality",
    userEvalId: "binding-1",
    pinned_version_id: "v1",
    bindingConfig: { jev_mapping: rubric("Authored") },
    run_config: { model: route, agent_mode: "quick" },
  });

  it("preserves authored levels across clean refetch, save, and reopen", async () => {
    const evalData = savedBinding();
    const onSave = vi.fn();
    const view = renderConfigFull({ evalData, onSave });
    await waitFor(() =>
      expect(screen.getByLabelText("Level 0")).toHaveValue("Authored bad"),
    );
    expect(capturedProps.tracing.runConfig.jev_mapping.score).toEqual(
      rubric("Authored").score,
    );

    stableEvalDetail.data = {
      ...stableEvalDetail.data,
      description: "Fresh detail",
    };
    stableVersions.data = {
      versions: stableVersions.data.versions.map((version) => ({
        ...version,
        usage_count: 2,
      })),
    };
    view.rerender(configFullTree({ evalData, onSave }));
    await waitFor(() =>
      expect(screen.getByLabelText("Level 0")).toHaveValue("Authored bad"),
    );
    await act(async () =>
      capturedProps.tracing.onReadyChange(true, { input: "output" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Update Evaluation" }),
    );
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        versionId: "v1",
        config: expect.objectContaining({
          jev_mapping: expect.objectContaining(rubric("Authored")),
        }),
      }),
    );
    const saved = onSave.mock.calls[0][0];
    view.unmount();
    renderConfigFull({
      evalData: {
        ...evalData,
        bindingConfig: saved.config,
        run_config: saved.config.run_config,
      },
    });
    await waitFor(() =>
      expect(screen.getByLabelText("Level 0")).toHaveValue("Authored bad"),
    );
    expect(screen.getByLabelText("Level 1")).toHaveValue("Authored good");
  });

  it("resets to an explicitly chosen version and does not reapply the saved binding on refetch", async () => {
    const evalData = savedBinding();
    const view = renderConfigFull({ evalData });
    await waitFor(() =>
      expect(screen.getByLabelText("Level 0")).toHaveValue("Authored bad"),
    );
    await userEvent.click(screen.getByRole("combobox"));
    await userEvent.click(screen.getByRole("option", { name: "V2" }));
    expect(screen.getByLabelText("Level 0")).toHaveValue("Version two bad");

    stableVersions.data = {
      versions: stableVersions.data.versions.map((version) => ({
        ...version,
        usage_count: 3,
      })),
    };
    view.rerender(configFullTree({ evalData }));
    await waitFor(() =>
      expect(screen.getByLabelText("Level 0")).toHaveValue("Version two bad"),
    );

    await userEvent.click(screen.getByRole("combobox"));
    await userEvent.click(
      screen.getByRole("option", { name: "Default version" }),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Level 0")).toHaveValue("Current bad"),
    );
    expect(screen.getByRole("combobox")).toHaveTextContent("Default version");

    await userEvent.click(screen.getByRole("combobox"));
    await userEvent.click(screen.getByRole("option", { name: "V1" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Level 0")).toHaveValue("Version one bad"),
    );
  });
});

it("names the sample-row blocker in the task footer and disabled action explanations", async () => {
  const original = stableEvalDetail.data;
  stableEvalDetail.data = {
    ...original,
    eval_type: "llm",
    instructions: "Grade {{input}}",
    config: { model: "template-model" },
  };
  try {
    renderConfigFull();
    const message =
      "Sample row could not be loaded. Retry loading the sample row.";
    await act(async () => {
      capturedProps.tracing.onReadyChange(false, { input: "locale" });
      capturedProps.tracing.onReadinessChange?.({ message });
    });
    // Three independent sites: a surviving tooltip cannot stand in for the
    // footer or for the other disabled action's explanation.
    expect(
      screen.getByText(message, { selector: ".MuiTypography-caption" }),
    ).toBeVisible();
    for (const name of ["Test Evaluation", "Add Evaluation"]) {
      const button = screen.getByRole("button", { name });
      expect(button).toBeDisabled();
      expect(
        within(button.closest("[data-tooltip-host]")).getByTestId(
          "tooltip-text",
        ),
      ).toHaveTextContent(message);
    }
    expect(
      screen.queryByText("Map all variables to enable testing & adding"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Map all variables before/),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Add Evaluation/ }),
    ).toBeDisabled();
  } finally {
    stableEvalDetail.data = original;
  }
});

it("restores saved system-eval binding configuration", async () => {
  renderConfigFull({
    evalData: {
      id: "tpl-1",
      templateId: "tpl-1",
      userEvalId: "binding-1",
      name: "toxicity_dataset",
      bindingConfig: {
        template_format: "jinja",
      },
      runConfig: {
        model: "saved-model",
        tools: { github: true },
      },
    },
  });

  await waitFor(() =>
    expect(capturedProps.instruction).toMatchObject({
      model: "saved-model",
      templateFormat: "jinja",
    }),
  );
  expect(capturedProps.instruction.activeConnectorIds).toEqual(["github"]);
});

describe("EvalPickerConfigFull — task preview time window", () => {
  beforeEach(() => {
    capturedProps.tracing = null;
  });

  it("passes the task's time window to TracingTestMode as a created_at filter", () => {
    renderConfigFull({ sourceTimeWindow: TIME_WINDOW });

    expect(capturedProps.tracing).not.toBeNull();
    const createdAt = (capturedProps.tracing.localFilters || []).find(
      (f) => f.column_id === "created_at",
    );
    // Without this filter the backend defaults to a 30-day lookback and the
    // drawer previews empty for tasks whose data is older than that.
    expect(createdAt).toEqual({
      column_id: "created_at",
      filter_config: {
        filter_type: "datetime",
        filter_op: "between",
        filter_value: [TIME_WINDOW.startDate, TIME_WINDOW.endDate],
      },
    });
  });

  it("enables the backend native-field contract only for task mapping", () => {
    renderConfigFull();
    expect(capturedProps.tracing.taskMapping).toBe(true);
  });

  it("omits the created_at filter when no time window is provided", () => {
    renderConfigFull();

    expect(capturedProps.tracing).not.toBeNull();
    expect(
      (capturedProps.tracing.localFilters || []).some(
        (f) => f.column_id === "created_at",
      ),
    ).toBe(false);
  });
});

describe("EvalPickerConfigFull — model hydration on edit", () => {
  const originalDetail = stableEvalDetail.data;
  afterEach(() => {
    stableEvalDetail.data = originalDetail;
  });

  beforeEach(() => {
    capturedProps.llmPrompt = null;
    stableEvalDetail.data = { ...DEFAULT_DETAIL };
  });

  const hydratedModel = async () => {
    await waitFor(() => expect(capturedProps.llmPrompt).not.toBeNull());
    return capturedProps.llmPrompt.model;
  };

  it("restores the model saved in run_config instead of the template default", async () => {
    // The user edited this eval down to turing_small. `build_run_config_view`
    // used to drop `model` from its whitelist, so run_config arrived without
    // it and the form fell back to the template default (turing_large).
    stableEvalDetail.data = { ...DEFAULT_DETAIL, model: "turing_large" };
    renderConfigFull({
      evalData: {
        ...DEFAULT_EVAL_DATA,
        run_config: { model: "turing_small", agent_mode: "agent" },
      },
    });

    expect(await hydratedModel()).toBe("turing_small");
  });

  it("prefers the canonical detail model over the list's stripped form", async () => {
    // The list endpoint returns "small" for built-in templates while detail
    // returns "turing_small". Mapping `model` into normalizedRunConfig let the
    // stripped value win and render "small" in the chip — guard against that
    // mapping coming back.
    stableEvalDetail.data = { ...DEFAULT_DETAIL, model: "turing_small" };
    renderConfigFull({
      evalData: { ...DEFAULT_EVAL_DATA, model: "small", run_config: {} },
    });

    expect(await hydratedModel()).toBe("turing_small");
  });

  it("keeps the saved model when the list also carries a stripped form", async () => {
    stableEvalDetail.data = { ...DEFAULT_DETAIL, model: "turing_large" };
    renderConfigFull({
      evalData: {
        ...DEFAULT_EVAL_DATA,
        model: "small",
        run_config: { model: "turing_small" },
      },
    });

    expect(await hydratedModel()).toBe("turing_small");
  });

  it("falls back to turing_large when no model is available anywhere", async () => {
    renderConfigFull({ evalData: { ...DEFAULT_EVAL_DATA, run_config: {} } });

    expect(await hydratedModel()).toBe("turing_large");
  });
});

describe("EvalPickerConfigFull — error localization gating (TH-7177)", () => {
  beforeEach(() => {
    Object.assign(deploymentMode, {
      mode: "cloud",
      isCloud: true,
      isOSS: false,
      isEE: false,
    });
  });

  it("shows the Error Localization checkbox on cloud", () => {
    renderConfigFull();
    expect(screen.getByText("Error Localization")).toBeTruthy();
  });

  it("hides the Error Localization checkbox on OSS", () => {
    Object.assign(deploymentMode, { mode: "oss", isCloud: false, isOSS: true });
    renderConfigFull();
    expect(screen.queryByText("Error Localization")).toBeNull();
  });

  it("shows the Error Localization checkbox on licensed self-hosted EE", () => {
    Object.assign(deploymentMode, { mode: "ee", isCloud: false, isEE: true });
    renderConfigFull();
    expect(screen.getByText("Error Localization")).toBeTruthy();
  });
});

describe("EvalPickerConfigFull — host-supplied queue decorations", () => {
  it("overrides the primary button label and renders the progress node", async () => {
    render(
      <EvalPickerProvider
        source="task"
        sourceId="project-1"
        sourceRowType="traces"
        sourceColumns={[]}
        existingEvals={[]}
        onEvalAdded={() => {}}
        onClose={() => {}}
      >
        <EvalPickerConfigFull
          evalData={{ id: "tpl-1", templateId: "tpl-1", name: "toxicity" }}
          onBack={() => {}}
          onSave={() => {}}
          isSaving={false}
          primaryLabel="Walk on"
          progress={<div data-testid="queue-progress">bar</div>}
        />
      </EvalPickerProvider>,
    );

    expect(
      await screen.findByRole("button", { name: "Walk on" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add Evaluation" })).toBeNull();
    expect(screen.getByTestId("queue-progress")).toBeInTheDocument();
  });
});

const NO_INPUTS_TOOLTIP =
  "This evaluation has no inputs to map, so it can't run in an environment.";

describe("EvalPickerConfigFull — requireInputs", () => {
  // stableEvalDetail (the mocked eval detail) has no required_keys and no
  // {{variables}} in its instructions, so `variables` resolves to [] —
  // exactly the "no inputs to map" case this opt-in rule targets.
  it("disables adding an eval with no inputs when requireInputs is set", async () => {
    renderConfigFull({ requireInputs: true });

    const addButton = await screen.findByRole("button", {
      name: "Add Evaluation",
    });
    expect(addButton).toBeDisabled();
    expect(screen.getByTestId("tooltip-text")).toHaveTextContent(
      NO_INPUTS_TOOLTIP,
    );
  });

  it("does not disable for missing inputs when requireInputs is absent", async () => {
    renderConfigFull({});

    await screen.findByRole("button", { name: "Add Evaluation" });
    expect(screen.queryByText(NO_INPUTS_TOOLTIP)).toBeNull();
  });
});

// Under the environment's own source (`simulation`), `hasDataInjection` is
// always false — it only ever applies to "task" and "tracing"
// (EvalPickerConfigFull.jsx ~L403-409). So a system eval with no variables
// hits the pre-existing "no variables" branch before requireInputs gets a
// chance to run, and would show the template-editing copy instead of the
// no-inputs-to-map one — misleading for a system eval, whose template a
// person can't edit.
const DEFAULT_STABLE_EVAL_DETAIL_DATA = {
  id: "tpl-1",
  name: "toxicity",
  owner: "system",
  eval_type: "agent",
  output_type: "pass_fail",
  instructions: "Template instructions",
  config: { model: "template-model", tools: { template: true } },
};

describe("EvalPickerConfigFull — requireInputs environment shape", () => {
  afterEach(() => {
    Object.keys(stableEvalDetail.data).forEach((k) => {
      delete stableEvalDetail.data[k];
    });
    Object.assign(stableEvalDetail.data, DEFAULT_STABLE_EVAL_DETAIL_DATA);
    stableCompositeDetail.data = null;
  });

  it("shows the no-inputs-to-map copy (not the template-variable copy) for a system eval with no variables under source=simulation", async () => {
    render(
      <EvalPickerProvider
        source="simulation"
        sourceId="run-test-1"
        existingEvals={[]}
        onEvalAdded={() => {}}
        onClose={() => {}}
        requireInputs
      >
        <EvalPickerConfigFull
          evalData={{ id: "tpl-1", templateId: "tpl-1", name: "toxicity" }}
          onBack={() => {}}
          onSave={() => {}}
          isSaving={false}
        />
      </EvalPickerProvider>,
    );

    const addButton = await screen.findByRole("button", {
      name: "Add Evaluation",
    });
    expect(addButton).toBeDisabled();
    // Not getByTestId: under source="simulation" the Test button's own
    // "no variables" tooltip also renders (it has no requireInputs override),
    // so two `tooltip-text` nodes exist — match on the no-inputs-to-map copy itself.
    expect(screen.getByText(NO_INPUTS_TOOLTIP)).toBeInTheDocument();
  });

  it("shows the no-inputs-to-map copy for a code eval with no required_keys and no saved mapping, under source=simulation", async () => {
    Object.assign(stableEvalDetail.data, {
      eval_type: "code",
      config: {
        code: "def evaluate(**kwargs):\n    return 1",
        language: "python",
      },
      required_keys: [],
    });

    render(
      <EvalPickerProvider
        source="simulation"
        sourceId="run-test-1"
        existingEvals={[]}
        onEvalAdded={() => {}}
        onClose={() => {}}
        requireInputs
      >
        <EvalPickerConfigFull
          evalData={{ id: "tpl-2", templateId: "tpl-2", name: "my_code_eval" }}
          onBack={() => {}}
          onSave={() => {}}
          isSaving={false}
        />
      </EvalPickerProvider>,
    );

    const addButton = await screen.findByRole("button", {
      name: "Add Evaluation",
    });
    expect(addButton).toBeDisabled();
    expect(screen.getByText(NO_INPUTS_TOOLTIP)).toBeInTheDocument();
  });

  // requireInputs must not be limited to non-composite evals — a composite
  // with no child required_keys has nothing to map either.
  it("shows the no-inputs-to-map copy for a composite eval with no child required_keys", async () => {
    Object.assign(stableEvalDetail.data, {
      template_type: "composite",
      eval_type: "composite",
    });
    stableCompositeDetail.data = { children: [] };

    render(
      <EvalPickerProvider
        source="simulation"
        sourceId="run-test-1"
        existingEvals={[]}
        onEvalAdded={() => {}}
        onClose={() => {}}
        requireInputs
      >
        <EvalPickerConfigFull
          evalData={{
            id: "tpl-3",
            templateId: "tpl-3",
            name: "my_composite_eval",
          }}
          onBack={() => {}}
          onSave={() => {}}
          isSaving={false}
        />
      </EvalPickerProvider>,
    );

    const addButton = await screen.findByRole("button", {
      name: "Add Evaluation",
    });
    expect(addButton).toBeDisabled();
    expect(screen.getByText(NO_INPUTS_TOOLTIP)).toBeInTheDocument();
    stableCompositeDetail.data = null;
  });
});

describe("EvalPickerConfigFull — no-variables copy outside the environment", () => {
  afterEach(() => {
    stableEvalDetail.data.owner = "system";
  });
  const renderSim = (props = {}) =>
    render(
      <EvalPickerProvider
        source="simulation"
        sourceId="run-test-1"
        existingEvals={[]}
        onEvalAdded={() => {}}
        onClose={() => {}}
        {...props}
      >
        <EvalPickerConfigFull
          evalData={{ id: "tpl-1", templateId: "tpl-1", name: "toxicity" }}
          onBack={() => {}}
          onSave={() => {}}
          isSaving={false}
        />
      </EvalPickerProvider>,
    );

  it("keeps the template-variable copy for a system eval when requireInputs is absent", async () => {
    renderSim();
    expect(
      await screen.findByRole("button", { name: "Add Evaluation" }),
    ).toBeDisabled();
    expect(screen.queryByText(NO_INPUTS_TOOLTIP)).toBeNull();
    expect(
      screen.getByText(
        /Your Mustache template has no variables\. .* before adding this evaluation\./,
      ),
    ).toBeInTheDocument();
  });

  it("keeps the template-variable copy for a user eval even with requireInputs, since its template is editable", async () => {
    stableEvalDetail.data.owner = "user";
    renderSim({ requireInputs: true });
    expect(
      await screen.findByRole("button", { name: "Add Evaluation" }),
    ).toBeDisabled();
    expect(screen.queryByText(NO_INPUTS_TOOLTIP)).toBeNull();
  });
});
