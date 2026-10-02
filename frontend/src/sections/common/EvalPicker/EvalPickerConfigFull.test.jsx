import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "src/utils/test-utils";
import { screen, waitFor } from "@testing-library/react";

import EvalPickerProvider from "./context/EvalPickerProvider";
import EvalPickerConfigFull from "./EvalPickerConfigFull";

const { capturedProps } = vi.hoisted(() => ({
  capturedProps: { tracing: null, instruction: null },
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

vi.mock("src/sections/evals/components/LLMPromptEditor", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/evals/components/CodeEvalEditor", () => ({
  default: () => <div />,
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
    <>
      {children}
      {show && title ? <div data-testid="tooltip-text">{title}</div> : null}
    </>
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

const renderConfigFull = ({ sourceTimeWindow, evalData, requireInputs } = {}) =>
  render(
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
        evalData={
          evalData || { id: "tpl-1", templateId: "tpl-1", name: "toxicity" }
        }
        onBack={() => {}}
        onSave={() => {}}
        isSaving={false}
      />
    </EvalPickerProvider>,
  );

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
