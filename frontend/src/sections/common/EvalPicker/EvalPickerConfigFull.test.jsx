/* eslint-disable react/prop-types */
import React from "react";
import {
  bpText,
  bpRendered,
} from "../../evals/components/__tests__/pinCopyBp.fixture";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render as renderWithTheme } from "src/utils/test-utils";
import { act, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";

import EvalPickerProvider from "./context/EvalPickerProvider";
import EvalPickerConfigFull from "./EvalPickerConfigFull";
import snapshotCases from "./__tests__/bindingSnapshot.fixture.json";

const { capturedProps, mode } = vi.hoisted(() => ({
  capturedProps: {
    tracing: null,
    llmPrompt: null,
    instruction: null,
    dataset: null,
    datasetVariables: [],
  },
  mode: { prototype: false },
}));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => mode.prototype,
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
  // Mirrors DatasetTestMode's mapping state: seeded once from the saved
  // binding, then every mapped key missing from `variables` is pruned.
  const M = React.forwardRef((props, _ref) => {
    const { variables = [], initialMapping } = props;
    const [mapping, setMapping] = React.useState(() => ({
      ...(initialMapping || {}),
    }));
    React.useEffect(() => {
      if (!variables.length) return;
      const keep = new Set(variables);
      setMapping((previous) => {
        const kept = Object.entries(previous).filter(([key]) => keep.has(key));
        return kept.length === Object.keys(previous).length
          ? previous
          : Object.fromEntries(kept);
      });
    }, [variables]);
    capturedProps.datasetVariables.push(variables);
    capturedProps.dataset = { ...props, variables, mapping };
    return <div data-testid="dataset-test-mode" />;
  });
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

// The real Turing values: a saved binding's bare list form ("small") maps
// back to its canonical value through this set (savedBindingModelOf).
vi.mock("src/sections/evals/components/ModelSelector", () => ({
  default: () => <div />,
  FAGI_MODEL_VALUES: new Set(["turing_large", "turing_small", "turing_flash"]),
}));

vi.mock("src/sections/evals/components/InstructionEditor", () => ({
  default: (props) => {
    capturedProps.instruction = props;
    return <div />;
  },
}));

// The decision question editor reads the workspace's decision models
// (react-query); its props are what these tests check.
vi.mock("src/sections/common/DecisionJudges/DecisionQuestionEditor", () => ({
  default: (props) => {
    capturedProps.decisionEditor = props;
    return <div data-testid="decision-question-editor" />;
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
  default: (props) => {
    capturedProps.code = props;
    return <div />;
  },
}));

vi.mock("src/sections/evals/components/OutputTypeConfig", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/evals/components/FewShotExamples", () => ({
  default: (props) => {
    capturedProps.fewShot = props;
    return <div />;
  },
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
  childDetails,
  capabilityState,
} = vi.hoisted(() => ({
  stableEvalDetail: {
    /**
     * @type {{
     *   id: string, name: string, eval_type: string, output_type: string,
     *   owner?: string, instructions?: string, model?: string,
     *   template_type?: string, current_version?: number, required_keys?: string[],
     *   config?: {
     *     model?: string, tools?: Record<string, boolean>, allow_na?: boolean,
     *     retained?: string, code?: string, language?: string,
     *   },
     * }}
     */
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
    refetch: vi.fn(),
  },
  stableUpdateEval: { mutate: () => {}, mutateAsync: async () => ({}) },
  stableVersions: {
    data: { versions: [] },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  },
  capabilityState: { locked: false, isLoading: false },
  stableCreateVersion: {
    /** @type {(payload: { config_snapshot: Record<string, unknown> }) => Promise<object>} */
    mutateAsync: async () => ({}),
  },
  stableCompositeDetail: { data: null, isError: false, refetch: vi.fn() },
  // Answers each composite child's detail request; a test decides when.
  childDetails: { fetch: () => new Promise(() => {}) },
}));

vi.mock("src/sections/evals/hooks/useEvalDetail", () => ({
  useEvalDetail: () => stableEvalDetail,
  useUpdateEval: () => stableUpdateEval,
  // The real composite child union (useCompositeChildrenKeys) queries this.
  evalDetailQuery: (templateId) => ({
    queryKey: ["evals", "detail", templateId],
    queryFn: () => childDetails.fetch(templateId),
  }),
}));

vi.mock("src/sections/evals/hooks/useEvalVersions", () => ({
  useEvalVersions: () => stableVersions,
  useCreateEvalVersion: () => stableCreateVersion,
}));

vi.mock("src/sections/evals/hooks/useCompositeEval", () => ({
  useCompositeDetail: () => stableCompositeDetail,
}));

vi.mock("src/hooks/useCapabilities", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useFeatureAllowed: () => ({ allowed: true, isLoading: false }),
    useFeatureLocked: () => capabilityState,
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

// The picker reads react-query itself: the jury dataset inputs (switch OFF
// too) and, through the child union, each composite child's detail.
const newQueryClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });

const render = (ui, { queryClient = newQueryClient(), ...options } = {}) =>
  renderWithTheme(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
    options,
  );

/**
 * @param {{
 *   sourceTimeWindow?: typeof TIME_WINDOW,
 *   source?: string,
 *   evalData?: Record<string, unknown>,
 *   requireInputs?: boolean,
 *   onSave?: (payload: Record<string, unknown>) => void,
 * }} [options]
 */
const renderConfigFull = ({
  sourceTimeWindow,
  source = "task",
  evalData,
  requireInputs,
  onSave = () => {},
} = {}) =>
  render(
    <EvalPickerProvider
      source={source}
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

// W1 review M1: a binding's run config carries the template's config (and
// its config.model); reopening the editor must keep the binding's own model
// and, for a decision judge, its saved question.
describe("EvalPickerConfigFull — reopening a saved binding (W1 M1)", () => {
  it("keeps the binding's own model over the template config's model", async () => {
    renderConfigFull({
      evalData: {
        id: "tpl-1",
        templateId: "tpl-1",
        userEvalId: "binding-2",
        name: "toxicity_experiment",
        model: "gpt-4o-mini",
        runConfig: { model: "template-model", mapping: {} },
      },
    });
    await waitFor(() =>
      expect(capturedProps.instruction).toMatchObject({
        model: "gpt-4o-mini",
      }),
    );
  });

  it("brings a decision judge back with its question, and Test sends it with the eval's prompt", async () => {
    const decision = {
      revision: "jev-map-v1",
      model: "typesafe/jev-1.13.0",
      format: "systemone",
      route: "/v1/decisions",
      question_type: "noul",
      pass: { criteria_true: "Refund confirmed", criteria_false: "" },
      choice: { labels: [], descriptions: {}, scores: {} },
      score: { levels: [] },
      output_kind: "pass_fail",
      // An earlier draft's own copy of the prompt is dropped on reopen.
      instructions: "stale copy",
    };
    renderConfigFull({
      evalData: {
        id: "tpl-1",
        templateId: "tpl-1",
        userEvalId: "binding-3",
        name: "toxicity_experiment",
        model: "typesafe/jev-1.13.0",
        runConfig: { model: "template-model", mapping: {}, decision },
      },
    });
    await waitFor(() =>
      expect(capturedProps.instruction).toMatchObject({
        model: "typesafe/jev-1.13.0",
      }),
    );
    await waitFor(() =>
      expect(capturedProps.tracing?.decisionTest?.decision).toBeTruthy(),
    );
    const sent = capturedProps.tracing.decisionTest;
    expect(sent.decision.model).toBe("typesafe/jev-1.13.0");
    expect(sent.decision.pass.criteria_true).toBe("Refund confirmed");
    expect(sent.decision).not.toHaveProperty("instructions");
    expect(sent.rule_prompt).toBe("Template instructions");
    // The editor shows the restored question, and the eval's prompt as
    // its instructions.
    expect(capturedProps.decisionEditor.value.pass.criteria_true).toBe(
      "Refund confirmed",
    );
    expect(capturedProps.decisionEditor.instructions).toBe(
      "Template instructions",
    );
  });
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

describe.each([false, true])(
  "picker edit dev parity (prototype=%s)",
  (prototype) => {
    const originalDetail = stableEvalDetail.data;
    beforeEach(() => {
      mode.prototype = prototype;
      stableEvalDetail.data = {
        ...DEFAULT_DETAIL,
        owner: "user",
        instructions: "Judge {{output}}",
        config: { allow_na: true, model: "turing_large", retained: "template" },
      };
    });
    afterEach(() => {
      mode.prototype = false;
      stableEvalDetail.data = originalDetail;
    });

    it("removes N/A from the switch, Test definition and reopened binding update in launch mode", async () => {
      const onSave = vi.fn();
      renderConfigFull({
        onSave,
        evalData: {
          ...DEFAULT_EVAL_DATA,
          userEvalId: "binding-1",
          bindingConfig: { allow_na: true, retained: "binding" },
        },
      });
      await waitFor(() =>
        expect(capturedProps.tracing?.decisionTest?.rule_prompt).toBe(
          "Judge {{output}}",
        ),
      );
      expect(Boolean(screen.queryByTestId("allow-na-switch"))).toBe(prototype);
      if (prototype)
        expect(capturedProps.tracing.decisionTest.allow_na).toBe(true);
      else
        expect(capturedProps.tracing.decisionTest).not.toHaveProperty(
          "allow_na",
        );
      await act(async () => {
        capturedProps.tracing.onReadyChange(true, { output: "output-column" });
      });
      const save = screen.getByRole("button", {
        name: /Update Evaluation|Add Evaluation/,
      });
      expect(save).toBeEnabled();
      fireEvent.click(save);
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const config = onSave.mock.calls[0][0].config;
      expect(config.retained).toBe("binding");
      if (prototype) expect(config.allow_na).toBe(true);
      else expect(config).not.toHaveProperty("allow_na");
      expect(stableEvalDetail.data.config.allow_na).toBe(true);
    });
  },
);

describe.each([false, true])(
  "picker snapshot dev parity (prototype=%s)",
  (prototype) => {
    const originalDetail = stableEvalDetail.data;
    beforeEach(() => {
      mode.prototype = prototype;
      stableEvalDetail.data = {
        ...DEFAULT_DETAIL,
        owner: "user",
        instructions: "Judge {{output}}",
        config: { allow_na: true, retained: "template", model: "turing_large" },
      };
    });
    afterEach(() => {
      mode.prototype = false;
      stableEvalDetail.data = originalDetail;
      vi.restoreAllMocks();
    });

    it.each(["template", "fallback"])(
      "cleans the Save Version %s config without mutating it",
      async (source) => {
        const stored = Object.freeze({ allow_na: true, retained: source });
        if (source === "template") stableEvalDetail.data.config = stored;
        else delete stableEvalDetail.data.config;
        const update = vi.spyOn(stableUpdateEval, "mutateAsync");
        const create = vi.spyOn(stableCreateVersion, "mutateAsync");
        renderConfigFull({
          evalData: { ...DEFAULT_EVAL_DATA, config: stored },
        });
        await waitFor(() =>
          expect(capturedProps.tracing?.decisionTest?.rule_prompt).toBe(
            "Judge {{output}}",
          ),
        );
        if (prototype) {
          expect(
            screen.getByRole("checkbox", { name: "Let the judge answer N/A" }),
          ).toBeChecked();
        }
        await act(async () => {
          fireEvent.click(screen.getByRole("button", { name: "Save version" }));
        });
        expect(update).toHaveBeenCalledTimes(1);
        expect(create).toHaveBeenCalledTimes(1);
        const snapshot = create.mock.calls[0][0].config_snapshot;
        expect(snapshot.retained).toBe(source);
        expect(snapshot.rule_prompt).toBe("Judge {{output}}");
        if (prototype) expect(snapshot.allow_na).toBe(true);
        else expect(snapshot).not.toHaveProperty("allow_na");
        expect(stored.allow_na).toBe(true);
      },
    );

    it("cleans a composite binding's stored config too", async () => {
      stableEvalDetail.data.template_type = "composite";
      const onSave = vi.fn();
      renderConfigFull({ onSave });
      await act(async () => {
        capturedProps.tracing.onReadyChange(true, {});
      });
      fireEvent.click(screen.getByRole("button", { name: "Add Evaluation" }));
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const config = onSave.mock.calls[0][0].config;
      expect(config.retained).toBe("template");
      if (prototype) expect(config.allow_na).toBe(true);
      else expect(config).not.toHaveProperty("allow_na");
      expect(stableEvalDetail.data.config.allow_na).toBe(true);
    });
  },
);

describe("BP copy survives the picker version lock", () => {
  const originalDetail = stableEvalDetail.data;
  const originalVersions = stableVersions.data;
  beforeEach(() => {
    setLaunchOverrideForTest({ launch: true, madeReal: [] });
    stableEvalDetail.data = {
      ...DEFAULT_DETAIL,
      owner: "user",
      current_version: 2,
      config: { model: "turing_large" },
    };
    stableVersions.data = {
      versions: [
        {
          id: "version-2",
          version_number: 2,
          config_snapshot: { model: "turing_large" },
        },
        {
          id: "version-1",
          version_number: 1,
          config_snapshot: { model: "turing_large" },
        },
      ],
    };
  });
  afterEach(() => {
    setLaunchOverrideForTest(null);
    stableEvalDetail.data = originalDetail;
    stableVersions.data = originalVersions;
  });
  it("A1-05/06/08 and A2-04 are absent while A1-10 and A2-05 remain", async () => {
    renderConfigFull();
    const badge = await screen.findByText(bpRendered.header(2));
    expect(badge.closest("[title]")).toHaveAttribute("title", bpText["A2-05"]);
    expect(screen.getByRole("button", { name: "Save version" })).toBeEnabled();
    expect(
      screen.queryByRole("combobox", {
        name: /Saved eval version|Recorded version/,
      }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText(bpText["A1-05"])).toBeNull();
    expect(
      screen.queryByRole("option", { name: bpText["A1-06"], exact: true }),
    ).toBeNull();
    expect(
      screen.queryByRole("option", { name: "Default version" }),
    ).toBeNull();
    for (const version_number of [1, 2]) {
      expect(
        screen.queryByRole("option", {
          name: bpRendered.numbered({ version_number }),
          exact: true,
        }),
      ).toBeNull();
    }
    expect(screen.queryByRole("option", { name: /latest/i })).toBeNull();
  });
  it("A1-10 still labels a stored numbered version without a selector", async () => {
    renderConfigFull({
      evalData: {
        ...DEFAULT_EVAL_DATA,
        userEvalId: "binding-1",
        pinned_version_id: "version-1",
      },
    });
    expect(
      await screen.findByText(bpRendered.header("V1")),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("combobox", { name: bpText["A1-05"] }),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "Save version" })).toBeEnabled();
  });
  it("A1-09 has no default option because the version selector is absent", async () => {
    stableVersions.data = {
      versions: stableVersions.data.versions.map((v) => ({
        ...v,
        is_default: v.version_number === 2,
      })),
    };
    renderConfigFull();
    expect(await screen.findByText(bpRendered.header(2))).toBeInTheDocument();
    expect(
      screen.queryByRole("combobox", { name: bpText["A1-05"] }),
    ).toBeNull();
    expect(
      screen.queryByRole("option", {
        name: bpRendered.numbered({ version_number: 2 }) + bpText["A1-09"],
        exact: true,
      }),
    ).toBeNull();
    expect(
      screen.queryByRole("option", { name: /recorded default/i }),
    ).toBeNull();
  });
  it.each([false, true])(
    "A1-07 has no Latest or Saved latest option (former gate shown=%s)",
    async (shown) => {
      setLaunchOverrideForTest(
        shown
          ? { launch: false, madeReal: [] }
          : { launch: true, madeReal: [] },
      );
      const evalData = {
        ...DEFAULT_EVAL_DATA,
        run_config: { version_pin: "latest" },
      };
      renderConfigFull({ evalData });
      expect(
        await screen.findByText(bpRendered.header("V2")),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("combobox", { name: bpText["A1-05"] }),
      ).toBeNull();
      expect(
        screen.queryByRole("option", {
          name: bpRendered.latest(shown, stableVersions.data.versions),
          exact: true,
        }),
      ).toBeNull();
      expect(
        screen.queryByRole("option", { name: bpText["A1-06"], exact: true }),
      ).toBeNull();
      expect(
        screen.queryByRole("option", {
          name: bpRendered.numbered({ version_number: 1 }),
          exact: true,
        }),
      ).toBeNull();
      expect(evalData.run_config.version_pin).toBe("latest");
      expect(
        screen.getByRole("button", { name: "Save version" }),
      ).toBeEnabled();
    },
  );
});

describe("classic experiment Save version at launch defaults", () => {
  const originalDetail = stableEvalDetail.data;
  const originalVersions = stableVersions.data;
  const originalCreate = stableCreateVersion.mutateAsync;
  const originalUpdate = stableUpdateEval.mutateAsync;
  beforeEach(() => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: madeRealSurfaceIds(),
    });
    capturedProps.instruction = null;
    capturedProps.dataset = null;
    stableEvalDetail.data = {
      ...DEFAULT_DETAIL,
      owner: "user",
      eval_type: "agent",
      instructions: "Judge {{output}}",
      config: { model: "turing_large", rule_prompt: "Judge {{output}}" },
    };
    stableVersions.data = { versions: [] };
    stableUpdateEval.mutateAsync = vi.fn(async () => ({}));
    stableCreateVersion.mutateAsync = vi.fn(async ({ config_snapshot }) => ({
      id: "version-2",
      version_number: 2,
      config_snapshot,
    }));
  });
  afterEach(() => {
    setLaunchOverrideForTest(null);
    stableEvalDetail.data = originalDetail;
    stableVersions.data = originalVersions;
    stableCreateVersion.mutateAsync = originalCreate;
    stableUpdateEval.mutateAsync = originalUpdate;
  });

  it("saves and applies the legacy definition without adding a wizard execution pin", async () => {
    const onSave = vi.fn();
    renderConfigFull({ source: "experiment", onSave });
    await waitFor(() =>
      expect(capturedProps.instruction?.value).toBe("Judge {{output}}"),
    );
    const saveVersion = screen.getByRole("button", { name: "Save version" });
    expect(saveVersion).toBeEnabled();
    await act(async () => {
      capturedProps.instruction.onChange("Saved {{output}}");
      capturedProps.dataset.onReadyChange(true, { output: "output-column" });
    });
    fireEvent.click(saveVersion);
    await waitFor(() =>
      expect(screen.getByText("Version label: V2")).toBeInTheDocument(),
    );
    expect(stableUpdateEval.mutateAsync).toHaveBeenCalledTimes(1);
    expect(stableCreateVersion.mutateAsync).toHaveBeenCalledTimes(1);
    expect(stableCreateVersion.mutateAsync.mock.calls[0][0]).toMatchObject({
      criteria: "Saved {{output}}",
      config_snapshot: { rule_prompt: "Saved {{output}}" },
    });
    // Item 238's existing picker lock remains; restoring Save version must
    // not resurrect the separate numbered/Latest execution-pin selector.
    expect(
      screen.queryByRole("combobox", {
        name: /Saved eval version|Recorded version/,
      }),
    ).toBeNull();
    expect(screen.queryByRole("option", { name: /latest/i })).toBeNull();
    await act(async () => {
      capturedProps.dataset.onReadyChange(true, { output: "output-column" });
    });
    const add = screen.getByRole("button", { name: "Add Evaluation" });
    expect(add).toBeEnabled();
    fireEvent.click(add);
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0][0];
    expect(saved.config.rule_prompt).toBe("Saved {{output}}");
    expect(saved.mapping).toEqual({ output: "output-column" });
    expect(saved).not.toHaveProperty("bindingPayload");
    expect(saved).not.toHaveProperty("pinned_version_id");
    expect(saved).not.toHaveProperty("versionId");
    expect(saved).not.toHaveProperty("versionPin");
    expect(saved.config).not.toHaveProperty("version_pin");
  });
});

// Exception 7: the Update boundary is the persisted binding, not isDirty.
// These tests intentionally precede the fix. Execute RED/GREEN only in the
// approved cloud row; no provider or evaluator is called by this harness.
describe.each(["dataset", "experiment", "run-optimization"])(
  "picker binding payload equivalence (%s)",
  (source) => {
    const originalDetail = stableEvalDetail.data;
    const originalVersions = stableVersions.data;
    const originalCreate = stableCreateVersion.mutateAsync;
    const snapshot = {
      model: "turing_large",
      rule_prompt: "Judge {{output}}",
      output: "score",
      pass_threshold: 0.5,
      template_format: "mustache",
      multi_choice: false,
      data_injection: { variables_only: true },
    };
    const overrides = {
      model: "turing_large",
      pass_threshold: 0.7,
      agent_mode: "agent",
      check_internet: false,
      summary: { type: "custom", custom: "Keep this explanation" },
      tools: { disabled_connector: false },
      knowledge_bases: [],
      data_injection: { full_row: true, trace_context: false },
      error_localizer_enabled: false,
    };
    beforeEach(() => {
      setLaunchOverrideForTest({ launch: true, experimentsBasic: false });
      capturedProps.instruction = null;
      capturedProps.dataset = null;
      stableEvalDetail.data = {
        ...DEFAULT_DETAIL,
        owner: "user",
        eval_type: "agent",
        output_type: "percentage",
        instructions: "New {{output}}",
        config: { ...snapshot, rule_prompt: "New {{output}}" },
      };
      stableVersions.data = {
        versions: [
          { id: "version-1", version_number: 1, config_snapshot: snapshot },
        ],
      };
    });
    afterEach(() => {
      setLaunchOverrideForTest(null);
      stableEvalDetail.data = originalDetail;
      stableVersions.data = originalVersions;
      stableCreateVersion.mutateAsync = originalCreate;
    });
    const open = async (runConfig = overrides) => {
      const storedBinding = {
        template_id: "tpl-1",
        name: "saved-judge",
        model: "turing_large",
        pinned_version_id: "version-1",
        error_localizer: false,
        config: {
          mapping: { output: "output-column" },
          config: snapshot,
          run_config: runConfig,
          retained_binding_option: { enabled: true },
        },
      };
      const onSave = vi.fn();
      renderConfigFull({
        source,
        onSave,
        evalData: {
          ...DEFAULT_EVAL_DATA,
          name: storedBinding.name,
          userEvalId: "binding-1",
          model: storedBinding.model,
          pinned_version_id: "version-1",
          mapping: storedBinding.config.mapping,
          bindingConfig: snapshot,
          run_config: runConfig,
          storedBinding,
        },
      });
      await waitFor(() =>
        expect(capturedProps.instruction?.value).toBe(snapshot.rule_prompt),
      );
      await act(async () => {
        capturedProps.dataset.onReadyChange(true, storedBinding.config.mapping);
      });
      return {
        onSave,
        storedBinding,
        button: screen.getByRole("button", { name: "Update Evaluation" }),
      };
    };

    it.each([{}, overrides])(
      "disables a no-op without sending a save or changing runtime (%j)",
      async (runtime) => {
        const { button, onSave, storedBinding } = await open(runtime);
        const before = JSON.stringify(storedBinding);
        expect(button).toBeDisabled();
        fireEvent.click(button);
        expect(onSave).not.toHaveBeenCalled();
        expect(JSON.stringify(storedBinding)).toBe(before);
      },
    );

    it("saves a mapping-only edit and preserves every stored runtime override", async () => {
      const { button, onSave, storedBinding } = await open();
      await act(async () =>
        capturedProps.dataset.onReadyChange(true, { output: "other-column" }),
      );
      expect(button).toBeEnabled();
      fireEvent.click(button);
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(onSave.mock.calls[0][0].bindingPayload).toEqual({
        ...storedBinding,
        config: {
          ...storedBinding.config,
          mapping: { output: "other-column" },
        },
      });
      expect(onSave.mock.calls[0][0].versionId).toBe(
        storedBinding.pinned_version_id,
      );
      expect(storedBinding.pinned_version_id).toBe("version-1");
      expect(storedBinding.config.mapping).toEqual({ output: "output-column" });
    });

    it("hydrates binding overrides instead of the pinned snapshot's runtime", async () => {
      await open();
      expect(capturedProps.instruction.activeContextOptions).toEqual([
        "dataset_row",
      ]);
      expect(capturedProps.instruction.activeConnectorIds).toEqual([]);
      expect(capturedProps.instruction.activeSummary).toBe("custom");
    });

    it("saves a real runtime change without erasing unrelated settings", async () => {
      const { button, onSave, storedBinding } = await open();
      await act(async () =>
        capturedProps.instruction.onUseInternetChange(true),
      );
      expect(button).toBeEnabled();
      fireEvent.click(button);
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0].bindingPayload;
      expect(saved.config).toEqual({
        ...storedBinding.config,
        config: {
          ...snapshot,
          ...overrides,
          check_internet: true,
        },
        run_config: { ...overrides, check_internet: true },
      });
      expect(saved).not.toHaveProperty("pinned_version_id");
    });

    it("disables Update after reverting a real edit even though isDirty stays true", async () => {
      const { button, onSave } = await open();
      await act(async () =>
        capturedProps.instruction.onUseInternetChange(true),
      );
      expect(button).toBeEnabled();
      await act(async () =>
        capturedProps.instruction.onUseInternetChange(false),
      );
      expect(button).toBeDisabled();
      fireEvent.click(button);
      expect(onSave).not.toHaveBeenCalled();
    });

    it.each(["agent", "llm", "code"])(
      "edits the %s definition again after Save version, including reverting to the original",
      async (evalType) => {
        stableEvalDetail.data = {
          ...stableEvalDetail.data,
          eval_type: evalType,
        };
        stableCreateVersion.mutateAsync = vi.fn(
          async ({ config_snapshot }) => ({
            id: "version-2",
            version_number: 2,
            config_snapshot,
          }),
        );
        const original =
          evalType === "code"
            ? "def evaluate(output):\n    return 1"
            : snapshot.rule_prompt;
        if (evalType === "code") {
          stableVersions.data = {
            versions: [
              {
                ...stableVersions.data.versions[0],
                config_snapshot: {
                  ...snapshot,
                  code: original,
                  language: "python",
                },
              },
            ],
          };
        }
        // open() waits on the agent editor, so mount each editor directly.
        const onSave = vi.fn();
        renderConfigFull({
          source,
          onSave,
          evalData: {
            ...DEFAULT_EVAL_DATA,
            userEvalId: "binding-1",
            pinned_version_id: "version-1",
            mapping: { output: "output-column" },
          },
        });
        await waitFor(() =>
          expect(
            screen.getByRole("button", { name: "Save version" }),
          ).toBeEnabled(),
        );
        const edit = (value) => {
          if (evalType === "code") capturedProps.code.setCode(value);
          else if (evalType === "llm")
            capturedProps.llmPrompt.onMessagesChange([
              { role: "system", content: value },
            ]);
          else capturedProps.instruction.onChange(value);
        };
        await act(async () => {
          capturedProps.dataset.onReadyChange(true, {
            output: "output-column",
          });
          edit(
            evalType === "code"
              ? "def evaluate(output):\n    return 0"
              : "Saved {{output}}",
          );
        });
        fireEvent.click(screen.getByRole("button", { name: "Save version" }));
        await waitFor(() =>
          expect(screen.getByText("Version label: V2")).toBeInTheDocument(),
        );
        await act(async () => edit(original));
        const button = screen.getByRole("button", {
          name: "Update Evaluation",
        });
        expect(button).toBeEnabled();
        fireEvent.click(button);
        await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
        const payload = onSave.mock.calls[0][0].bindingPayload;
        expect(payload).not.toHaveProperty("pinned_version_id");
        expect(
          payload.config.config[evalType === "code" ? "code" : "rule_prompt"],
        ).toBe(original);
      },
    );

    it("disables a normalized binding revert after Save version without sending an Update", async () => {
      const runtime = {
        ...overrides,
        pass_threshold: 0.5,
        multi_choice: false,
        summary: { type: "custom", custom: "" },
        tools: {},
        data_injection: { variables_only: true },
      };
      const originalMessages = [
        { role: "system", content: snapshot.rule_prompt },
        { role: "user", content: "Review {{output}}" },
      ];
      const normalizedSnapshot = {
        ...snapshot,
        ...runtime,
        messages: originalMessages,
        few_shot_examples: [],
        required_keys: ["output"],
      };
      const storedBinding = {
        template_id: "tpl-1",
        name: "saved-judge",
        model: "turing_large",
        pinned_version_id: "version-1",
        error_localizer: false,
        config: {
          mapping: { output: "output-column" },
          config: normalizedSnapshot,
          run_config: runtime,
          reason_column: true,
          retained_binding_option: { enabled: true },
        },
      };
      const originalBinding = JSON.stringify(storedBinding);
      stableEvalDetail.data = {
        ...stableEvalDetail.data,
        eval_type: "llm",
        instructions: snapshot.rule_prompt,
        config: normalizedSnapshot,
      };
      stableVersions.data = {
        versions: [
          {
            id: "version-1",
            version_number: 1,
            config_snapshot: normalizedSnapshot,
          },
        ],
      };
      stableCreateVersion.mutateAsync = vi.fn(async ({ config_snapshot }) => ({
        id: "version-2",
        version_number: 2,
        config_snapshot,
      }));
      const onSave = vi.fn();
      renderConfigFull({
        source,
        onSave,
        evalData: {
          ...DEFAULT_EVAL_DATA,
          name: storedBinding.name,
          userEvalId: "binding-1",
          model: storedBinding.model,
          pinned_version_id: "version-1",
          mapping: storedBinding.config.mapping,
          bindingConfig: normalizedSnapshot,
          run_config: runtime,
          storedBinding,
        },
      });
      await waitFor(() =>
        expect(capturedProps.llmPrompt?.messages).toEqual(originalMessages),
      );
      await act(async () =>
        capturedProps.dataset.onReadyChange(true, storedBinding.config.mapping),
      );
      const button = screen.getByRole("button", { name: "Update Evaluation" });
      expect(button).toBeDisabled();
      const savedMessages = [
        { role: "system", content: "Saved {{output}}" },
        { role: "user", content: "Changed {{output}}" },
      ];
      await act(async () =>
        capturedProps.llmPrompt.onMessagesChange(savedMessages),
      );
      expect(button).toBeEnabled();
      fireEvent.click(screen.getByRole("button", { name: "Save version" }));
      await waitFor(() =>
        expect(screen.getByText("Version label: V2")).toBeInTheDocument(),
      );
      expect(stableCreateVersion.mutateAsync).toHaveBeenCalledTimes(1);
      expect(
        JSON.parse(
          JSON.stringify(
            stableCreateVersion.mutateAsync.mock.calls[0][0].config_snapshot,
          ),
        ),
      ).toEqual({
        ...normalizedSnapshot,
        rule_prompt: "Saved {{output}}",
        messages: savedMessages,
      });
      // Saving V2 has not updated the binding pinned to V1.
      expect(button).toBeEnabled();
      expect(onSave).not.toHaveBeenCalled();
      await act(async () =>
        capturedProps.llmPrompt.onMessagesChange(originalMessages),
      );
      expect(button).toBeDisabled();
      fireEvent.click(button);
      expect(onSave).not.toHaveBeenCalled();
      expect(JSON.stringify(storedBinding)).toBe(originalBinding);

      // The no-op guard must not swallow a subsequent real definition edit.
      const nextMessages = [
        { role: "system", content: "Another {{output}}" },
        originalMessages[1],
      ];
      await act(async () =>
        capturedProps.llmPrompt.onMessagesChange(nextMessages),
      );
      expect(button).toBeEnabled();
      fireEvent.click(button);
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(onSave.mock.calls[0][0].bindingPayload).toEqual({
        template_id: "tpl-1",
        name: "saved-judge",
        model: "turing_large",
        error_localizer: false,
        config: {
          ...storedBinding.config,
          config: {
            ...normalizedSnapshot,
            rule_prompt: "Another {{output}}",
            messages: nextMessages,
          },
        },
      });
    });

    it("recaptures the binding baseline only after a successful save", async () => {
      const { button, onSave } = await open();
      onSave.mockResolvedValueOnce(false).mockResolvedValueOnce(true);
      await act(async () =>
        capturedProps.dataset.onReadyChange(true, { output: "other-column" }),
      );
      await act(async () => fireEvent.click(button));
      expect(button).toBeEnabled();
      await act(async () => fireEvent.click(button));
      expect(onSave).toHaveBeenCalledTimes(2);
      expect(button).toBeDisabled();
      fireEvent.click(button);
      expect(onSave).toHaveBeenCalledTimes(2);
    });

    it("keeps runtime settings after Save version clears editor dirtiness", async () => {
      stableCreateVersion.mutateAsync = vi.fn(async ({ config_snapshot }) => {
        const created = { id: "version-2", version_number: 2, config_snapshot };
        stableVersions.data = {
          versions: [created, ...stableVersions.data.versions],
        };
        return created;
      });
      const { button, onSave } = await open();
      await act(async () =>
        capturedProps.instruction.onUseInternetChange(true),
      );
      fireEvent.click(screen.getByRole("button", { name: "Save version" }));
      await waitFor(() =>
        expect(stableCreateVersion.mutateAsync).toHaveBeenCalledTimes(1),
      );
      await waitFor(() =>
        expect(capturedProps.instruction.useInternet).toBe(true),
      );
      expect(button).toBeEnabled();
      fireEvent.click(button);
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0].bindingPayload;
      expect(saved.pinned_version_id).toBe("version-2");
      expect(saved.config.run_config).toEqual({
        ...overrides,
        check_internet: true,
      });
      expect(
        stableCreateVersion.mutateAsync.mock.calls[0][0].config_snapshot,
      ).toMatchObject({
        ...overrides,
        check_internet: true,
      });
    });
  },
);

// Fix-round RED cases: sparse historical bindings and independently arriving
// detail/version/capability queries. Execution belongs to the isolated cloud row.
describe("binding initialization and sparse historical snapshots", () => {
  const originalDetail = stableEvalDetail.data;
  const originalVersions = stableVersions.data;
  const originalCreate = stableCreateVersion.mutateAsync;
  const snapshot = {
    model: "turing_large",
    rule_prompt: "Historic {{output}}",
    output: "score",
    pass_threshold: 0.6,
    template_format: "mustache",
    data_injection: { full_row: true },
    tools: { saved_connector: true },
    summary: { type: "custom", custom: "Historic explanation" },
    check_internet: false,
    error_localizer_enabled: false,
  };
  const versionData = {
    versions: [
      { id: "version-1", version_number: 1, config_snapshot: snapshot },
    ],
  };
  const detail = {
    ...DEFAULT_DETAIL,
    owner: "user",
    eval_type: "agent",
    output_type: "percentage",
    instructions: "Live {{output}}",
    config: {
      ...snapshot,
      rule_prompt: "Live {{output}}",
      data_injection: { variables_only: true },
      live_only: true,
    },
  };
  beforeEach(() => {
    setLaunchOverrideForTest({ launch: true, experimentsBasic: false });
    stableEvalDetail.data = detail;
    stableEvalDetail.isLoading = false;
    stableEvalDetail.isError = false;
    stableEvalDetail.refetch.mockReset();
    Object.assign(stableVersions, {
      data: versionData,
      isLoading: false,
      isError: false,
    });
    stableVersions.refetch.mockReset();
    Object.assign(capabilityState, { locked: false, isLoading: false });
    capturedProps.dataset = null;
    capturedProps.instruction = null;
  });
  afterEach(() => {
    setLaunchOverrideForTest(null);
    stableEvalDetail.data = originalDetail;
    stableEvalDetail.isLoading = false;
    stableEvalDetail.isError = false;
    Object.assign(stableVersions, {
      data: originalVersions,
      isLoading: false,
      isError: false,
    });
    Object.assign(capabilityState, { locked: false, isLoading: false });
    stableCreateVersion.mutateAsync = originalCreate;
  });
  const mount = (overrides = {}) => {
    const storedBinding = {
      template_id: "tpl-1",
      name: "sparse-binding",
      model: "turing_large",
      pinned_version_id: "version-1",
      error_localizer: false,
      config: { mapping: { output: "column-a" }, config: {}, run_config: {} },
      ...overrides,
    };
    const evalData = {
      ...DEFAULT_EVAL_DATA,
      ...storedBinding,
      userEvalId: "binding-1",
      bindingConfig: storedBinding.config.config,
      run_config: storedBinding.config.run_config,
      mapping: storedBinding.config.mapping,
      storedBinding,
    };
    const onSave = vi.fn();
    const client = newQueryClient();
    const ui = () => (
      <QueryClientProvider client={client}>
        <EvalPickerProvider
          source="dataset"
          sourceId="dataset-1"
          existingEvals={[]}
          onEvalAdded={() => {}}
          onClose={() => {}}
          initialEval={evalData}
        >
          <EvalPickerConfigFull
            evalData={evalData}
            onBack={() => {}}
            onSave={onSave}
          />
        </EvalPickerProvider>
      </QueryClientProvider>
    );
    const view = renderWithTheme(ui());
    return { onSave, storedBinding, refresh: () => view.rerender(ui()) };
  };
  const ready = async (mapping = { output: "column-a" }) => {
    await waitFor(() => expect(capturedProps.dataset).not.toBeNull());
    await act(async () => capturedProps.dataset.onReadyChange(true, mapping));
    return screen.getByRole("button", { name: "Update Evaluation" });
  };
  const save = async (onSave) => {
    fireEvent.click(screen.getByRole("button", { name: "Update Evaluation" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    return onSave.mock.calls[0][0].bindingPayload;
  };

  it("keeps sparse stored JSON for a mapping edit, but sends the selected definition for a runtime edit", async () => {
    const { onSave, storedBinding } = mount();
    const button = await ready();
    expect(button).toBeDisabled();
    await act(async () => capturedProps.instruction.onUseInternetChange(true));
    const payload = await save(onSave);
    expect(payload).not.toHaveProperty("pinned_version_id");
    expect(payload.config.config).toMatchObject({
      ...snapshot,
      check_internet: true,
    });
    expect(payload.config.config).not.toHaveProperty("live_only");
    expect(storedBinding.config.config).toEqual({});
  });

  it("keeps inherited context and connectors when introducing override objects", async () => {
    const { onSave } = mount();
    await ready();
    await act(async () => {
      capturedProps.instruction.onActiveContextOptionsChange([
        "dataset_row",
        "trace_context",
      ]);
      capturedProps.instruction.onActiveConnectorIdsChange([
        "saved_connector",
        "new_connector",
      ]);
    });
    const payload = await save(onSave);
    expect(payload.config.run_config.data_injection).toEqual({
      full_row: true,
      trace_context: true,
    });
    expect(payload.config.run_config.tools).toEqual({
      saved_connector: true,
      new_connector: true,
    });
  });

  it("Save version then Update preserves inherited settings and shows the pending binding save", async () => {
    stableCreateVersion.mutateAsync = vi.fn(async ({ config_snapshot }) => ({
      id: "version-2",
      version_number: 2,
      config_snapshot,
    }));
    const { onSave } = mount();
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Save version" }));
    await waitFor(() =>
      expect(stableCreateVersion.mutateAsync).toHaveBeenCalledTimes(1),
    );
    await waitFor(() =>
      expect(screen.getByText("Unsaved changes")).toBeInTheDocument(),
    );
    const created =
      stableCreateVersion.mutateAsync.mock.calls[0][0].config_snapshot;
    expect(created).toMatchObject(snapshot);
    expect(created).not.toHaveProperty("live_only");
    const payload = await save(onSave);
    expect(payload.pinned_version_id).toBe("version-2");
    expect(payload.config.run_config).toEqual({});
    expect(capturedProps.instruction.activeContextOptions).toEqual([
      "dataset_row",
    ]);
  });

  it("synchronizes an edited User turn with its stored runtime override", async () => {
    stableEvalDetail.data = { ...detail, eval_type: "llm" };
    const messages = [
      { role: "system", content: snapshot.rule_prompt },
      { role: "user", content: "Old {{output}}" },
    ];
    const runtime = { messages, opaque: { retained: true } };
    const { onSave } = mount({
      config: {
        mapping: { output: "column-a" },
        config: {},
        run_config: runtime,
      },
    });
    const button = await ready();
    expect(button).toBeDisabled();
    expect(capturedProps.llmPrompt.messages).toEqual(messages);
    const edited = [
      messages[0],
      { role: "user", content: "Edited {{output}}" },
    ];
    await act(async () => capturedProps.llmPrompt.onMessagesChange(edited));
    const payload = await save(onSave);
    expect(payload).not.toHaveProperty("pinned_version_id");
    expect(payload.config.config.messages).toEqual(edited);
    expect(payload.config.run_config).toEqual({ ...runtime, messages: edited });
  });

  it("clears the last inherited few-shot dataset explicitly", async () => {
    stableEvalDetail.data = { ...detail, eval_type: "llm" };
    const examples = [
      {
        dataset_id: "historical-examples",
        mapping: { output: "example-output" },
      },
    ];
    stableVersions.data = {
      versions: [
        {
          ...versionData.versions[0],
          config_snapshot: { ...snapshot, few_shot_examples: examples },
        },
      ],
    };
    const { onSave, storedBinding } = mount();
    const button = await ready();
    expect(button).toBeDisabled();
    expect(capturedProps.fewShot.selectedDatasets).toEqual(examples);
    await act(async () => capturedProps.fewShot.onChange([]));
    expect(button).toBeEnabled();
    const payload = await save(onSave);
    expect(payload).not.toHaveProperty("pinned_version_id");
    expect(payload.config.config.few_shot_examples).toEqual([]);
    expect(storedBinding.config.config).toEqual({});
  });

  it.each(snapshotCases.cases)(
    "submits the shared persisted snapshot contract through handleAdd: $name",
    async ({ field, value, storedRunConfig, requestConfig }) => {
      stableEvalDetail.data = { ...detail, eval_type: "llm" };
      stableVersions.data = {
        versions: [
          {
            id: "version-1",
            version_number: 1,
            config_snapshot: snapshotCases.base,
          },
        ],
      };
      const { onSave, storedBinding } = mount({
        model: "fixture-model",
        config: {
          mapping: requestConfig.mapping,
          config: {},
          run_config: storedRunConfig,
        },
      });
      const button = await ready(requestConfig.mapping);
      expect(button).toBeDisabled();
      expect(capturedProps.llmPrompt.messages).toEqual(
        snapshotCases.base.messages,
      );
      expect(capturedProps.fewShot.selectedDatasets).toEqual(
        snapshotCases.base.few_shot_examples,
      );
      await act(async () => {
        if (field === "messages")
          capturedProps.llmPrompt.onMessagesChange(value);
        else capturedProps.fewShot.onChange(value);
      });
      expect(button).toBeEnabled();
      expect(await save(onSave)).toEqual({
        template_id: storedBinding.template_id,
        name: storedBinding.name,
        model: storedBinding.model,
        error_localizer: false,
        config: requestConfig,
      });
    },
  );

  it("recovers a failed cold template detail load without saving or replacing stored values", async () => {
    Object.assign(stableEvalDetail, {
      data: undefined,
      isLoading: false,
      isError: true,
    });
    const { onSave, refresh, storedBinding } = mount();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to load evaluation details.",
    );
    expect(screen.queryByTestId("dataset-test-mode")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save version" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back to list" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(stableEvalDetail.refetch).toHaveBeenCalledTimes(1);
    expect(stableVersions.refetch).not.toHaveBeenCalled();
    expect(onSave).not.toHaveBeenCalled();

    Object.assign(stableEvalDetail, { isLoading: true, isError: false });
    refresh();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    expect(screen.queryByTestId("dataset-test-mode")).not.toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();

    Object.assign(stableEvalDetail, { data: detail, isLoading: false });
    refresh();
    const button = await ready();
    expect(button).toBeDisabled();
    expect(capturedProps.instruction.value).toBe(snapshot.rule_prompt);
    fireEvent.click(button);
    expect(onSave).not.toHaveBeenCalled();
    await act(async () =>
      capturedProps.dataset.onReadyChange(true, { output: "column-b" }),
    );
    expect(button).toBeEnabled();
    expect(await save(onSave)).toEqual({
      ...storedBinding,
      config: { ...storedBinding.config, mapping: { output: "column-b" } },
    });
  });

  it("recovers a failed cold composite detail load without saving or replacing stored values", async () => {
    const originalComposite = stableCompositeDetail.data;
    stableEvalDetail.data = { ...detail, template_type: "composite" };
    Object.assign(stableCompositeDetail, { data: null, isError: true });
    stableCompositeDetail.refetch.mockReset();
    try {
      const { onSave, refresh, storedBinding } = mount({
        composite_weight_overrides: { child: 0.7 },
      });
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Failed to load evaluation details.",
      );
      expect(screen.queryByTestId("dataset-test-mode")).not.toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Back to list" }),
      ).toBeEnabled();
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(stableCompositeDetail.refetch).toHaveBeenCalledTimes(1);
      expect(onSave).not.toHaveBeenCalled();
      Object.assign(stableCompositeDetail, {
        data: { children: [] },
        isError: false,
      });
      refresh();
      const button = await ready();
      expect(button).toBeDisabled();
      await act(async () =>
        capturedProps.dataset.onReadyChange(true, { output: "column-b" }),
      );
      expect(await save(onSave)).toEqual({
        ...storedBinding,
        config: { ...storedBinding.config, mapping: { output: "column-b" } },
      });
    } finally {
      Object.assign(stableCompositeDetail, {
        data: originalComposite,
        isError: false,
      });
    }
  });

  it("hydrates the legacy top-level localization flag and can save OFF", async () => {
    const { onSave } = mount({
      pinned_version_id: null,
      error_localizer: true,
      config: {
        mapping: { output: "column-a" },
        config: {},
        run_config: { check_internet: false },
      },
    });
    const button = await ready();
    const checkbox = screen.getByRole("checkbox", {
      // FormControlLabel includes both the title and the explanatory caption.
      name: "Error Localization Pinpoints which parts of the input caused evaluation failures",
    });
    expect(checkbox.closest("label")).toBeVisible();
    expect(checkbox).toBeEnabled();
    expect(checkbox).toBeChecked();
    expect(button).toBeDisabled();
    fireEvent.click(checkbox);
    expect(button).toBeEnabled();
    const payload = await save(onSave);
    expect(payload.error_localizer).toBe(false);
    expect(payload.config.run_config.error_localizer_enabled).toBe(false);
  });

  it("versions before detail cannot establish a stale baseline", async () => {
    stableEvalDetail.data = undefined;
    stableEvalDetail.isLoading = true;
    const { onSave, refresh } = mount();
    expect(capturedProps.dataset).toBeNull();
    stableEvalDetail.data = detail;
    stableEvalDetail.isLoading = false;
    refresh();
    const button = await ready();
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onSave).not.toHaveBeenCalled();
  });

  it("does not expose editable mapping before the stored version is hydrated", async () => {
    stableVersions.data = undefined;
    stableVersions.isLoading = true;
    const { onSave, refresh, storedBinding } = mount();
    expect(screen.queryByTestId("dataset-test-mode")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save version" }),
    ).not.toBeInTheDocument();
    stableVersions.data = versionData;
    stableVersions.isLoading = false;
    refresh();
    const button = await ready();
    expect(button).toBeDisabled();
    await act(async () =>
      capturedProps.dataset.onReadyChange(true, { output: "column-b" }),
    );
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    expect(await save(onSave)).toEqual({
      ...storedBinding,
      config: { ...storedBinding.config, mapping: { output: "column-b" } },
    });
  });

  it.each(["loading", "failure"])(
    "waits for all version data even without a stored pin (%s)",
    async (state) => {
      Object.assign(stableVersions, {
        data: undefined,
        isLoading: state === "loading",
        isError: state === "failure",
      });
      const { onSave, refresh } = mount({ pinned_version_id: null });
      expect(screen.queryByTestId("dataset-test-mode")).not.toBeInTheDocument();
      if (state === "failure") {
        expect(await screen.findByRole("alert")).toHaveTextContent(
          "Failed to load evaluation versions.",
        );
        fireEvent.click(screen.getByRole("button", { name: "Retry" }));
        expect(stableVersions.refetch).toHaveBeenCalledTimes(1);
      }
      expect(onSave).not.toHaveBeenCalled();
      Object.assign(stableVersions, {
        data: versionData,
        isLoading: false,
        isError: false,
      });
      refresh();
      expect(await ready()).toBeDisabled();
    },
  );

  it("waits for capabilities before capturing localization and permits a real OFF edit", async () => {
    Object.assign(capabilityState, { locked: true, isLoading: true });
    const { onSave, refresh } = mount({ error_localizer: true });
    expect(screen.queryByTestId("dataset-test-mode")).not.toBeInTheDocument();
    Object.assign(capabilityState, { locked: false, isLoading: false });
    refresh();
    const button = await ready();
    expect(button).toBeDisabled();
    const checkbox = screen.getByRole("checkbox", {
      // FormControlLabel includes both the title and the explanatory caption.
      name: "Error Localization Pinpoints which parts of the input caused evaluation failures",
    });
    expect(checkbox.closest("label")).toBeVisible();
    expect(checkbox).toBeEnabled();
    expect(checkbox).toBeChecked();
    fireEvent.click(checkbox);
    expect(button).toBeEnabled();
    expect((await save(onSave)).error_localizer).toBe(false);
  });

  it("reverted edits clear both pending indicators", async () => {
    mount();
    const button = await ready();
    await act(async () => capturedProps.instruction.onUseInternetChange(true));
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    await act(async () => capturedProps.instruction.onUseInternetChange(false));
    expect(button).toBeDisabled();
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
    expect(screen.queryByText("Edited")).not.toBeInTheDocument();
  });

  it.each(["failure", "missing"])(
    "offers retry for a %s version without replacing the pin",
    async (state) => {
      stableVersions.data = state === "missing" ? { versions: [] } : undefined;
      stableVersions.isError = state === "failure";
      const { onSave, refresh } = mount();
      expect(await screen.findByRole("alert")).toHaveTextContent(
        state === "failure"
          ? "Failed to load evaluation versions."
          : "Saved evaluation version is unavailable.",
      );
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(stableVersions.refetch).toHaveBeenCalledTimes(1);
      expect(onSave).not.toHaveBeenCalled();
      stableVersions.data = versionData;
      stableVersions.isError = false;
      refresh();
      const button = await ready();
      expect(button).toBeDisabled();
      await act(async () =>
        capturedProps.dataset.onReadyChange(true, { output: "column-b" }),
      );
      expect((await save(onSave)).pinned_version_id).toBe("version-1");
    },
  );
});

// Release-lead item 237: hiding an unsupported choice must not clear saved
// pins. These exercise the real picker state and onSave payload, not a mock
// serializer. The no-selector cases are RED before the UI-only fix; payload
// cases guard the existing default and stored-pin behavior.
describe.each(["dataset", "task"])("picker version lock (%s)", (source) => {
  const originalDetail = stableEvalDetail.data;
  const originalVersions = stableVersions.data;
  beforeEach(() => {
    setLaunchOverrideForTest({ launch: true, experimentsBasic: false });
    capturedProps.llmPrompt = null;
    capturedProps.dataset = null;
    capturedProps.tracing = null;
    stableEvalDetail.data = {
      ...DEFAULT_DETAIL,
      owner: "user",
      current_version: 2,
      instructions: "Current {{output}}",
      config: { model: "turing_large", rule_prompt: "Current {{output}}" },
    };
    stableVersions.data = {
      versions: [
        {
          id: "version-2",
          version_number: 2,
          is_default: true,
          config_snapshot: {
            model: "turing_large",
            rule_prompt: "Current {{output}}",
          },
        },
        {
          id: "version-1",
          version_number: 1,
          is_default: false,
          config_snapshot: {
            model: "turing_large",
            rule_prompt: "Historic {{output}}",
          },
        },
      ],
    };
  });
  afterEach(() => {
    setLaunchOverrideForTest(null);
    stableEvalDetail.data = originalDetail;
    stableVersions.data = originalVersions;
  });

  const saveBinding = async (
    buttonName,
    mapping = { output: "output-column" },
  ) => {
    await act(async () => {
      const preview =
        source === "dataset" ? capturedProps.dataset : capturedProps.tracing;
      preview.onReadyChange(true, mapping);
    });
    const button = await screen.findByRole("button", { name: buttonName });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
  };

  it.each([false, true])(
    "renders no Saved eval version control (existing binding=%s)",
    async (existing) => {
      renderConfigFull({
        source,
        ...(existing
          ? {
              evalData: {
                ...DEFAULT_EVAL_DATA,
                userEvalId: "binding-1",
                pinned_version_id: "version-1",
              },
            }
          : {}),
      });
      await waitFor(() =>
        expect(capturedProps.llmPrompt?.messages?.[0]?.content).toBe(
          existing ? "Historic {{output}}" : "Current {{output}}",
        ),
      );
      expect(
        screen.queryByRole("combobox", { name: "Saved eval version" }),
      ).not.toBeInTheDocument();
      expect(screen.queryByLabelText("Saved eval version")).toBeNull();
      expect(
        screen.getByRole("button", { name: "Save version" }),
      ).toBeEnabled();
      expect(
        screen.getByText(bpRendered.header(existing ? "V1" : 2)),
      ).toBeInTheDocument();
    },
  );

  it("saves a new binding on the default without an explicit version pin", async () => {
    const onSave = vi.fn();
    renderConfigFull({ source, onSave });
    await saveBinding("Add Evaluation");
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0][0];
    expect(saved.userEvalId).toBeUndefined();
    expect(saved.versionId).toBeNull();
    expect(saved).not.toHaveProperty("versionPin");
    expect(saved.config).not.toHaveProperty("version_pin");
  });

  it.each([
    [{ pinned_version_id: "version-1" }, "version-1", "v1"],
    [{ pinnedVersionId: "version-1" }, "version-1", "v1"],
    [{ run_config: { version_pin: "v1" } }, "version-1", "v1"],
    [{ run_config: { version_pin: "latest" } }, "latest", "latest"],
  ])(
    "saves a mapping edit with the existing binding's stored pin unchanged (%j)",
    async (stored, versionId, versionPin) => {
      const onSave = vi.fn();
      const evalData = {
        ...DEFAULT_EVAL_DATA,
        userEvalId: "binding-1",
        mapping: { output: "output-column" },
        ...stored,
      };
      renderConfigFull({ source, evalData, onSave });
      await waitFor(() =>
        expect(capturedProps.llmPrompt?.messages?.[0]?.content).toBe(
          versionPin === "latest"
            ? "Current {{output}}"
            : "Historic {{output}}",
        ),
      );
      await saveBinding("Update Evaluation", { output: "other-column" });
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0];
      expect(saved.userEvalId).toBe("binding-1");
      expect(saved.versionId).toBe(versionId);
      expect(saved.versionPin).toBe(versionPin);
      expect(saved.config.version_pin).toBe(versionPin);
      expect(evalData).toMatchObject(stored);
    },
  );
});

describe("BY2 template-wide default notice", () => {
  const notice =
    "Saving settings can create a new template-wide default version.";
  const originalDetail = stableEvalDetail.data;
  const originalVersions = stableVersions.data;
  beforeEach(() => {
    setLaunchOverrideForTest({ launch: true, experimentsBasic: false });
    stableEvalDetail.data = {
      ...DEFAULT_DETAIL,
      owner: "user",
      current_version: 2,
      instructions: "Current {{output}}",
      config: { model: "turing_large", rule_prompt: "Current {{output}}" },
    };
    stableVersions.data = { versions: [] };
  });
  afterEach(() => {
    setLaunchOverrideForTest(null);
    stableEvalDetail.data = originalDetail;
    stableVersions.data = originalVersions;
  });

  it.each(["dataset", "experiment", "run-optimization"])(
    "renders for an existing User Built binding in %s",
    async (source) => {
      renderConfigFull({
        source,
        evalData: { ...DEFAULT_EVAL_DATA, userEvalId: "binding-1" },
      });
      expect(
        await screen.findByText(notice, { exact: true }),
      ).toBeInTheDocument();
      expect(screen.getAllByText(notice, { exact: true })).toHaveLength(1);
    },
  );

  it.each([
    ["dataset", "user", false],
    ["experiment", "user", false],
    ["run-optimization", "user", false],
    ["dataset", "system", true],
    ["experiment", "system", true],
    ["run-optimization", "system", true],
    ["task", "user", true],
    ["workbench", "user", true],
    ["simulation", "user", true],
    ["composite", "user", true],
  ])(
    "is absent for source=%s owner=%s existing=%s",
    async (source, owner, existing) => {
      stableEvalDetail.data = { ...stableEvalDetail.data, owner };
      renderConfigFull({
        source,
        ...(existing
          ? { evalData: { ...DEFAULT_EVAL_DATA, userEvalId: "binding-1" } }
          : {}),
      });
      // System evaluations omit the version badge; wait for the shared header.
      expect(
        await screen.findByText(DEFAULT_DETAIL.name, { exact: true }),
      ).toBeInTheDocument();
      if (owner === "system") {
        expect(
          screen.queryByText(bpRendered.header(2)),
        ).not.toBeInTheDocument();
      } else {
        expect(screen.getByText(bpRendered.header(2))).toBeInTheDocument();
      }
      expect(
        screen.queryByText(notice, { exact: true }),
      ).not.toBeInTheDocument();
    },
  );
});

describe("composite dataset mapping while child definitions load (jury switch OFF)", () => {
  const originalDetail = stableEvalDetail.data;
  const originalFetch = childDetails.fetch;
  beforeEach(() => {
    setLaunchOverrideForTest({ launch: true, madeReal: [] });
    stableEvalDetail.data = {
      ...DEFAULT_DETAIL,
      owner: "user",
      template_type: "composite",
      eval_type: "composite",
    };
    stableCompositeDetail.data = {
      children: [
        {
          child_id: "child-a",
          child_name: "relevance",
          required_keys: ["question"],
        },
        {
          child_id: "child-b",
          child_name: "grounding",
          required_keys: ["context"],
        },
      ],
    };
    capturedProps.dataset = null;
    capturedProps.datasetVariables = [];
  });
  afterEach(() => {
    setLaunchOverrideForTest(null);
    stableEvalDetail.data = originalDetail;
    stableCompositeDetail.data = null;
    childDetails.fetch = originalFetch;
  });

  it("keeps a saved mapping to another column while one child's definition is pending", async () => {
    const queryClient = newQueryClient();
    // Child A answered earlier (cached); child B answers later.
    queryClient.setQueryData(["evals", "detail", "child-a"], {
      required_keys: ["question"],
    });
    let answerChildB;
    childDetails.fetch = () =>
      new Promise((resolve) => {
        answerChildB = resolve;
      });
    // Neither saved column is named after its variable.
    const saved = { question: "Prompt text", context: "Retrieved docs" };
    render(
      <EvalPickerProvider
        source="dataset"
        sourceId="dataset-1"
        existingEvals={[]}
        onEvalAdded={() => {}}
        onClose={() => {}}
      >
        <EvalPickerConfigFull
          evalData={{
            id: "composite-1",
            templateId: "composite-1",
            userEvalId: "binding-1",
            name: "grounded_answers",
            mapping: saved,
          }}
          onBack={() => {}}
          onSave={() => {}}
          isSaving={false}
        />
      </EvalPickerProvider>,
      { queryClient },
    );

    await waitFor(() => expect(answerChildB).toBeTypeOf("function"));
    await waitFor(() => expect(capturedProps.dataset).not.toBeNull());
    expect(capturedProps.dataset.mapping).toEqual(saved);
    capturedProps.datasetVariables.forEach((variables) =>
      expect(variables).toEqual(["question", "context"]),
    );

    await act(async () => {
      answerChildB({ required_keys: ["context"] });
    });
    await waitFor(() =>
      expect(
        queryClient.getQueryState(["evals", "detail", "child-b"])?.status,
      ).toBe("success"),
    );
    expect(capturedProps.dataset.variables).toEqual(["question", "context"]);
    expect(capturedProps.dataset.mapping).toEqual(saved);
    queryClient.clear();
  });
});

it.each([
  ["saved-v1", "V1", "Historic {{output}}"],
  [null, 2, "Current {{output}}"],
])(
  "keeps the explicit pin/default %s without a version selector",
  async (pinnedVersionId, shownVersion, prompt) => {
    const previousVersions = stableVersions.data;
    const previousDetail = stableEvalDetail.data;
    stableEvalDetail.data = {
      ...DEFAULT_DETAIL,
      owner: "user",
      current_version: 2,
      instructions: "Current {{output}}",
      config: { model: "turing_large", rule_prompt: "Current {{output}}" },
    };
    stableVersions.data = {
      versions: [
        {
          id: "saved-v1",
          version_number: 1,
          config_snapshot: {
            model: "turing_large",
            rule_prompt: "Historic {{output}}",
          },
        },
      ],
    };
    const onSave = vi.fn();
    try {
      renderConfigFull({
        onSave,
        evalData: {
          ...DEFAULT_EVAL_DATA,
          userEvalId: "binding-1",
          pinned_version_id: pinnedVersionId,
          run_config: { version_pin: "v1" },
        },
      });
      await waitFor(() =>
        expect(capturedProps.llmPrompt?.messages?.[0]?.content).toBe(prompt),
      );
      expect(
        screen.getByText(bpRendered.header(shownVersion)),
      ).toBeInTheDocument();
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("option", { name: "Default version" }),
      ).toBeNull();
      await act(async () => {
        capturedProps.tracing.onReadyChange(true, { output: "output-column" });
      });
      fireEvent.click(
        screen.getByRole("button", { name: "Update Evaluation" }),
      );
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0];
      expect(saved.versionId).toBe(pinnedVersionId);
      if (pinnedVersionId) {
        expect(saved.config.version_pin).toBe("v1");
      } else {
        expect(saved).not.toHaveProperty("versionPin");
        expect(saved.config).not.toHaveProperty("version_pin");
      }
    } finally {
      stableVersions.data = previousVersions;
      stableEvalDetail.data = previousDetail;
    }
  },
);
