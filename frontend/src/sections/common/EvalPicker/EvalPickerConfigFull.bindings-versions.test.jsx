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
import { serializeEvalConfig } from "./serializeEvalConfig";
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
  const M = React.forwardRef((props, _ref) => {
    capturedProps.simulation = props;
    return <div />;
  });
  M.displayName = "SimulationTestModeMock";
  return { default: M };
});

vi.mock("src/sections/evals/components/CreateSimulationPreviewMode", () => {
  const M = React.forwardRef((props, _ref) => {
    capturedProps.createSimulate = props;
    return <div />;
  });
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

// W1: a binding's run config carries the template's config (and
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

  it("brings a decision judge back with its saved question, as the same Jev model, and Test sends that question", async () => {
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
        model: "jev-1.13.0",
      }),
    );
    expect(screen.getByLabelText("Describe a pass (true)")).toHaveValue(
      "Refund confirmed",
    );
    await waitFor(() =>
      expect(capturedProps.tracing?.runConfig?.jev_mapping).toBeTruthy(),
    );
    const sent = capturedProps.tracing.runConfig;
    expect(sent.model).toBe("jev-1.13.0");
    expect(sent.jev_mapping.pass.criteria_true).toBe("Refund confirmed");
    expect(sent.jev_mapping).not.toHaveProperty("instructions");
  });
});

describe("EvalPickerConfigFull — a decision judge's question type", () => {
  it("names the question a decision model is asked, from the output type", async () => {
    const previous = stableEvalDetail.data;
    stableEvalDetail.data = {
      ...DEFAULT_DETAIL,
      owner: "user",
      output_type: "percentage",
      instructions: "Rate {{input}}",
      required_keys: ["input"],
      config: { model: "jev-latest", output: "score" },
    };
    try {
      renderConfigFull();
      expect(
        await screen.findByTestId("decision-question-type"),
      ).toHaveTextContent("Question type: A score on ordered rubric levels");
    } finally {
      stableEvalDetail.data = previous;
    }
  });
});

describe("EvalPickerConfigFull — a decision judge's instructions", () => {
  it("holds an agent evaluation with an empty prompt, and says why", async () => {
    const previous = stableEvalDetail.data;
    stableEvalDetail.data = {
      ...DEFAULT_DETAIL,
      owner: "user",
      eval_type: "agent",
      instructions: "",
      required_keys: ["input"],
      config: { model: "jev-latest", agent_mode: "quick" },
    };
    try {
      renderConfigFull();
      expect(
        await screen.findByText(
          "Jev instructions must contain text: write the eval's prompt (it is the question's instructions).",
        ),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Save version" }),
      ).toBeDisabled();
    } finally {
      stableEvalDetail.data = previous;
    }
  });
});

describe("EvalPickerConfigFull — a decision judge's mapped columns", () => {
  it("holds a decision judge mapped to a media column, and says why", async () => {
    const previous = stableEvalDetail.data;
    stableEvalDetail.data = {
      ...DEFAULT_DETAIL,
      owner: "user",
      instructions: "Judge {{input}}",
      required_keys: ["input"],
      config: { model: "jev-latest" },
    };
    const media =
      "Media inputs is not supported with Jev models. Nothing was changed or removed from your template.";
    capturedProps.dataset = null;
    try {
      renderConfigFull({ source: "dataset" });
      await waitFor(() => expect(capturedProps.dataset).toBeTruthy());
      await act(async () => {
        capturedProps.dataset.onColumnsLoaded(
          [
            { id: "col-question", name: "question", data_type: "text" },
            { id: "col-photo", name: "photo", data_type: "image" },
          ],
          {},
        );
        capturedProps.dataset.onReadyChange(true, { input: "col-question" });
      });
      // A text column: the template takes it.
      expect(screen.queryAllByText(media)).toHaveLength(0);
      expect(
        screen.getByRole("button", { name: "Save version" }),
      ).toBeEnabled();

      await act(async () => {
        capturedProps.dataset.onReadyChange(true, { input: "col-photo" });
      });
      // The image column: refused, and Save version, Add and Test say why.
      expect((await screen.findAllByText(media)).length).toBeGreaterThan(0);
      expect(
        screen.getByRole("button", { name: "Save version" }),
      ).toBeDisabled();
      expect(
        screen.getAllByTestId("tooltip-text").map((tip) => tip.textContent),
      ).toContain(media);
    } finally {
      stableEvalDetail.data = previous;
    }
  });
});

// The model a binding saved in its own run config is the one it runs; the
// eval list's model can name another (the template's, or a stripped form).
describe("EvalPickerConfigFull — the reopened binding's model", () => {
  it("shows the model saved in the binding's own run config over the eval list's model", async () => {
    capturedProps.instruction = null;
    renderConfigFull({
      evalData: {
        id: "tpl-1",
        templateId: "tpl-1",
        userEvalId: "binding-4",
        name: "toxicity_experiment",
        model: "gpt-4o-mini",
        runConfig: { model: "gpt-4.1", mapping: {} },
      },
    });
    await waitFor(() =>
      expect(capturedProps.instruction).toMatchObject({ model: "gpt-4.1" }),
    );
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
    // A stored label is printed where a pick is used. Where the default
    // version runs, the header names that version (the header cases below).
    renderConfigFull({
      source: "dataset",
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
    // On this host the header names the default version, which runs.
    expect(
      await screen.findByText(bpRendered.header("V2")),
    ).toBeInTheDocument();
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
      // On this host the header names the version that runs (no version is
      // marked default here, so the current one), not the held latest pin.
      expect(await screen.findByText(bpRendered.header(2))).toBeInTheDocument();
      expect(screen.queryByText(bpRendered.header("V2"))).toBeNull();
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
      showUnreleased: false,
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
  // A pinned binding runs its version's saved prompt, so an edit to the
  // prompt asks first; saving it as a new version then updates the binding
  // to that version. Returns that version's snapshot and the Update payload.
  const saveEditAsNewVersion = async (onSave) => {
    stableCreateVersion.mutateAsync = vi.fn(async ({ config_snapshot }) => {
      const created = { id: "version-2", version_number: 2, config_snapshot };
      stableVersions.data = {
        versions: [created, ...stableVersions.data.versions],
      };
      return created;
    });
    fireEvent.click(screen.getByRole("button", { name: "Update Evaluation" }));
    expect(
      await screen.findByText("Your prompt edits aren't in V1"),
    ).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "Save as new version and update" }),
    );
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(stableCreateVersion.mutateAsync).toHaveBeenCalledTimes(1);
    return {
      created: stableCreateVersion.mutateAsync.mock.calls[0][0].config_snapshot,
      payload: onSave.mock.calls[0][0].bindingPayload,
    };
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

  // The binding is pinned to version-1, whose saved messages run; its stored
  // runtime override of the turns does not, so the edit is saved as a version.
  it("saves an edited User turn of a pinned binding as a new version", async () => {
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
    const { created, payload } = await saveEditAsNewVersion(onSave);
    expect(created.messages).toEqual(edited);
    expect(payload.pinned_version_id).toBe("version-2");
  });

  it("clears the last inherited few-shot dataset of a pinned binding in a new version", async () => {
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
    const { created, payload } = await saveEditAsNewVersion(onSave);
    expect(created.few_shot_examples).toEqual([]);
    expect(payload.pinned_version_id).toBe("version-2");
    expect(storedBinding.config.config).toEqual({});
  });

  it.each(snapshotCases.cases)(
    "saves the shared snapshot edit of a pinned binding as a new version: $name",
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
      // The shared request without a pin (the server's create-version path)
      // is no longer sent for a pinned binding: the edit is saved as a
      // version first, and the binding is updated to it.
      const { created, payload } = await saveEditAsNewVersion(onSave);
      expect(created[field]).toEqual(value);
      expect(created[field]).toEqual(requestConfig.config[field]);
      expect(payload.pinned_version_id).toBe("version-2");
      expect(payload.template_id).toBe(storedBinding.template_id);
      expect(payload.config.mapping).toEqual(requestConfig.mapping);
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

// Hiding an unsupported choice must not clear saved
// pins. These exercise the real picker state and onSave payload, not a mock
// serializer. The no-selector cases are RED before the UI-only fix; payload
// cases guard the existing default and stored-pin behavior.
// The version choice on each host. Where the host saves the picked version
// and runs it (dataset, experiment, the optimization drawer), it is dev's
// choice: a new binding saves with no pin, a stored pin reopens and saves back
// unchanged, and a pin changes only when the person picks another version.
// Where the host runs its current settings whatever is picked (task,
// simulation, the workbench), the choice names no version, cannot be
// changed, says why, and nothing sends a version.
const DEFAULT_VERSION_LINE =
  "A version can't be chosen here. This evaluation runs with its current settings.";
// The pinned-edit dialog's save line on those hosts: a version saved there
// also becomes the evaluation's current settings, which they run.
const DEFAULT_SAVE_LINE =
  "Saves your changes as a new version. The evaluation then runs with these settings wherever no version is chosen and no prompt of its own was saved.";

describe.each([
  ["dataset", "pick"],
  ["experiment", "pick"],
  ["run-optimization", "pick"],
  ["task", "default only"],
  ["simulation", "default only"],
  ["create-simulate", "default only"],
  ["workbench", "default only"],
])("the picker's version choice (%s)", (source, choice) => {
  const picks = choice === "pick";
  const originalDetail = stableEvalDetail.data;
  const originalVersions = stableVersions.data;
  beforeEach(() => {
    setLaunchOverrideForTest({ launch: true, experimentsBasic: false });
    capturedProps.llmPrompt = null;
    capturedProps.dataset = null;
    capturedProps.tracing = null;
    capturedProps.simulation = null;
    capturedProps.createSimulate = null;
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
        {
          task: capturedProps.tracing,
          simulation: capturedProps.simulation,
          "create-simulate": capturedProps.createSimulate,
        }[source] ?? capturedProps.dataset;
      preview.onReadyChange(true, mapping);
    });
    const button = await screen.findByRole("button", { name: buttonName });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
  };

  const pick = async (name) => {
    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(await screen.findByRole("option", { name }));
  };

  const prompt = () => capturedProps.llmPrompt?.messages?.[0]?.content;

  const editPrompt = (content) =>
    act(() => {
      capturedProps.llmPrompt.onMessagesChange([{ role: "system", content }]);
    });

  // The default version (V2) gets a prompt of its own, so the editor shows
  // which version it loaded.
  const withDefaultPrompt = () => {
    stableVersions.data = {
      versions: stableVersions.data.versions.map((version) =>
        version.id === "version-2"
          ? {
              ...version,
              config_snapshot: {
                ...version.config_snapshot,
                rule_prompt: "Default {{output}}",
              },
            }
          : version,
      ),
    };
  };

  // A version saved by the create page keeps its model on the version, not
  // in its saved settings.
  const withVersionModelOnly = () => {
    stableVersions.data = {
      versions: stableVersions.data.versions.map((version) =>
        version.id === "version-1"
          ? {
              ...version,
              model: "gpt-4o",
              config_snapshot: { rule_prompt: "Historic {{output}}" },
            }
          : version,
      ),
    };
  };

  // The evaluation's current model differs from V1's.
  const withOtherCurrentModel = () => {
    stableEvalDetail.data = { ...stableEvalDetail.data, model: "turing_small" };
  };

  const existingBinding = {
    ...DEFAULT_EVAL_DATA,
    userEvalId: "binding-1",
    pinned_version_id: "version-1",
  };

  describe.skipIf(!picks)("where a pick is used", () => {
    it.each([false, true])(
      "offers dev's version choice (existing binding=%s)",
      async (existing) => {
        renderConfigFull({
          source,
          ...(existing ? { evalData: existingBinding } : {}),
        });
        await waitFor(() =>
          expect(prompt()).toBe(
            existing ? "Historic {{output}}" : "Current {{output}}",
          ),
        );
        const versionChoice = screen.getByRole("combobox");
        expect(versionChoice).toHaveTextContent(
          existing ? "V1" : "Default version",
        );
        // The header names the version being configured beside the choice.
        expect(
          screen.getByText(bpRendered.header(existing ? "V1" : 2)),
        ).toBeInTheDocument();
        expect(versionChoice).not.toHaveAttribute("aria-disabled", "true");
        expect(screen.queryByText(DEFAULT_VERSION_LINE)).toBeNull();
        // Save version sits beside the choice. It is checked before the menu
        // opens: an open menu hides the rest of the page from assistive
        // technology until it closes.
        expect(
          screen.getByRole("button", { name: "Save version" }),
        ).toBeEnabled();
        fireEvent.mouseDown(versionChoice);
        expect(
          await screen.findByRole("option", { name: "Default version" }),
        ).toBeInTheDocument();
        expect(
          screen.getByRole("option", { name: "V2 (default)" }),
        ).toBeInTheDocument();
        expect(screen.getByRole("option", { name: "V1" })).toBeInTheDocument();
        expect(screen.queryByRole("option", { name: /latest/i })).toBeNull();
      },
    );

    it("loads the stored version and names it when its prompt is edited", async () => {
      const onSave = vi.fn();
      renderConfigFull({ source, evalData: existingBinding, onSave });
      await waitFor(() => expect(prompt()).toBe("Historic {{output}}"));
      editPrompt("Edited {{output}}");
      await saveBinding("Update Evaluation");
      expect(
        await screen.findByText("Your prompt edits aren't in V1"),
      ).toBeInTheDocument();
      expect(
        screen.getByText("V1 runs the prompt and model it was saved with."),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Update with V1 as saved" }),
      ).toBeInTheDocument();
      expect(onSave).not.toHaveBeenCalled();
    });

    it("saves exactly the version picked for a new binding", async () => {
      const onSave = vi.fn();
      renderConfigFull({ source, onSave });
      await waitFor(() => expect(prompt()).toBe("Current {{output}}"));
      await pick("V1");
      await waitFor(() => expect(prompt()).toBe("Historic {{output}}"));
      expect(screen.getByRole("combobox")).toHaveTextContent("V1");
      await saveBinding("Add Evaluation");
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0];
      expect(saved.versionId).toBe("version-1");
      expect(saved.versionPin).toBe("v1");
      expect(saved.config.version_pin).toBe("v1");
    });

    const unpinnedBinding = {
      ...DEFAULT_EVAL_DATA,
      userEvalId: "binding-1",
      mapping: { output: "output-column" },
    };

    it("picks a version whose saved settings name no model and saves it with that version's model, asking nothing", async () => {
      withVersionModelOnly();
      const onSave = vi.fn();
      renderConfigFull({ source, evalData: unpinnedBinding, onSave });
      await waitFor(() => expect(prompt()).toBe("Current {{output}}"));
      await pick("V1");
      await waitFor(() => expect(prompt()).toBe("Historic {{output}}"));
      expect(capturedProps.llmPrompt.model).toBe("gpt-4o");
      await saveBinding("Update Evaluation");
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(screen.queryByText(/isn't in V1|aren't in V1/)).toBeNull();
      const saved = onSave.mock.calls[0][0];
      expect(saved.model).toBe("gpt-4o");
      expect(saved.versionId).toBe("version-1");
      expect(saved.versionPin).toBe("v1");
    });

    it("still asks first when the model is changed after a version is picked", async () => {
      withVersionModelOnly();
      const onSave = vi.fn();
      renderConfigFull({ source, evalData: unpinnedBinding, onSave });
      await waitFor(() => expect(prompt()).toBe("Current {{output}}"));
      await pick("V1");
      await waitFor(() => expect(prompt()).toBe("Historic {{output}}"));
      act(() => {
        capturedProps.llmPrompt.onModelChange("turing_small");
      });
      await saveBinding("Update Evaluation");
      expect(
        await screen.findByText("Your model change isn't in V1"),
      ).toBeInTheDocument();
      expect(onSave).not.toHaveBeenCalled();
    });

    it("picks a version for a new binding whose saved settings name no model and adds it with that version's model, asking nothing", async () => {
      withVersionModelOnly();
      withOtherCurrentModel();
      const onSave = vi.fn();
      renderConfigFull({ source, onSave });
      await waitFor(() => expect(prompt()).toBe("Current {{output}}"));
      await pick("V1");
      await waitFor(() => expect(prompt()).toBe("Historic {{output}}"));
      expect(capturedProps.llmPrompt.model).toBe("gpt-4o");
      await saveBinding("Add Evaluation");
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(screen.queryByText(/isn't in V1|aren't in V1/)).toBeNull();
      const saved = onSave.mock.calls[0][0];
      expect(saved.model).toBe("gpt-4o");
      expect(saved.versionId).toBe("version-1");
      expect(saved.versionPin).toBe("v1");
    });

    it("still asks first when the model of a new binding is changed after a version is picked", async () => {
      withVersionModelOnly();
      withOtherCurrentModel();
      const onSave = vi.fn();
      renderConfigFull({ source, onSave });
      await waitFor(() => expect(prompt()).toBe("Current {{output}}"));
      await pick("V1");
      await waitFor(() => expect(prompt()).toBe("Historic {{output}}"));
      act(() => {
        capturedProps.llmPrompt.onModelChange("turing_flash");
      });
      await saveBinding("Add Evaluation");
      expect(
        await screen.findByText("Your model change isn't in V1"),
      ).toBeInTheDocument();
      expect(onSave).not.toHaveBeenCalled();
    });

    it("changes a stored pin to the version picked", async () => {
      const onSave = vi.fn();
      const evalData = {
        ...existingBinding,
        mapping: { output: "output-column" },
      };
      renderConfigFull({ source, evalData, onSave });
      await waitFor(() => expect(prompt()).toBe("Historic {{output}}"));
      await pick("V2 (default)");
      await waitFor(() => expect(prompt()).toBe("Current {{output}}"));
      await saveBinding("Update Evaluation");
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0];
      expect(saved.versionId).toBe("version-2");
      expect(saved.versionPin).toBe("v2");
      expect(saved.config.version_pin).toBe("v2");
      expect(evalData.pinned_version_id).toBe("version-1");
    });

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
          expect(prompt()).toBe(
            versionPin === "latest"
              ? "Current {{output}}"
              : "Historic {{output}}",
          ),
        );
        expect(screen.getByRole("combobox")).toHaveTextContent(
          versionPin === "latest" ? "Latest" : "V1",
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

  describe.skipIf(picks)("where the default version runs", () => {
    it.each([false, true])(
      "names no version, cannot be changed, and says why (existing binding=%s)",
      async (existing) => {
        renderConfigFull({
          source,
          ...(existing ? { evalData: existingBinding } : {}),
        });
        await waitFor(() => expect(prompt()).toBeTruthy());
        const versionChoice = screen.getByRole("combobox");
        expect(versionChoice).toHaveTextContent("Current settings");
        expect(versionChoice).toHaveAttribute("aria-disabled", "true");
        fireEvent.mouseDown(versionChoice);
        expect(screen.queryByRole("listbox")).toBeNull();
        expect(screen.queryByRole("option")).toBeNull();
        expect(screen.getByText(DEFAULT_VERSION_LINE)).toBeInTheDocument();
      },
    );

    it("shows Current settings when the template has no saved versions", async () => {
      stableVersions.data = { versions: [] };
      renderConfigFull({ source });
      await waitFor(() => expect(prompt()).toBeTruthy());
      const versionChoice = screen.getByRole("combobox");
      expect(versionChoice).toHaveTextContent("Current settings");
      expect(versionChoice).toHaveAttribute("aria-disabled", "true");
      expect(screen.getByText(DEFAULT_VERSION_LINE)).toBeInTheDocument();
    });

    it("names no version once a newer version is saved without being made default", async () => {
      stableVersions.data = {
        versions: [
          {
            id: "version-3",
            version_number: 3,
            is_default: false,
            config_snapshot: {
              model: "turing_large",
              rule_prompt: "Newest {{output}}",
            },
          },
          ...stableVersions.data.versions,
        ],
      };
      renderConfigFull({ source, evalData: existingBinding });
      await waitFor(() => expect(prompt()).toBeTruthy());
      const versionChoice = screen.getByRole("combobox");
      expect(versionChoice).toHaveTextContent("Current settings");
      expect(versionChoice).not.toHaveTextContent(/V\d/);
      expect(versionChoice).toHaveAttribute("aria-disabled", "true");
      expect(screen.getByText(DEFAULT_VERSION_LINE)).toBeInTheDocument();
    });

    it("sends no version for a new binding", async () => {
      const onSave = vi.fn();
      renderConfigFull({ source, onSave });
      await waitFor(() => expect(prompt()).toBe("Current {{output}}"));
      expect(screen.getByRole("combobox")).toHaveAttribute(
        "aria-disabled",
        "true",
      );
      await saveBinding("Add Evaluation");
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0];
      expect(saved.versionId ?? null).toBeNull();
      expect(saved).not.toHaveProperty("versionPin");
      expect(saved.config).not.toHaveProperty("version_pin");
      // The body the task and simulation hosts send (serializeEvalConfig)
      // carries no version; the workbench's own body has no version field.
      const body = JSON.stringify(serializeEvalConfig(saved));
      expect(body).not.toMatch(/version_pin|pinned_version_id|versionId/);
    });

    it("names the version that runs in the header, and keeps a stored pin in the save", async () => {
      withDefaultPrompt();
      const onSave = vi.fn();
      renderConfigFull({ source, evalData: existingBinding, onSave });
      await waitFor(() => expect(prompt()).toBe("Default {{output}}"));
      expect(screen.getByRole("combobox")).toHaveTextContent(
        "Current settings",
      );
      expect(screen.getByText(bpRendered.header("V2"))).toBeInTheDocument();
      expect(screen.queryByText(bpRendered.header("V1"))).toBeNull();
      // The stored pin is not printed here, and the save keeps it unchanged.
      await saveBinding("Update Evaluation");
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0];
      expect(saved.versionId).toBe("version-1");
      expect(saved.versionPin).toBe("v1");
      expect(saved.config.version_pin).toBe("v1");
      expect(existingBinding.pinned_version_id).toBe("version-1");
    });

    it("asks first about a prompt edit, naming the version that runs, and puts that version back as saved", async () => {
      withDefaultPrompt();
      const onSave = vi.fn();
      renderConfigFull({ source, evalData: existingBinding, onSave });
      await waitFor(() => expect(prompt()).toBe("Default {{output}}"));
      expect(capturedProps.llmPrompt.model).toBe("turing_large");
      editPrompt("Edited {{output}}");
      act(() => {
        capturedProps.llmPrompt.onModelChange("turing_small");
      });
      await saveBinding("Update Evaluation");
      expect(
        await screen.findByText("Your prompt edits aren't in V2"),
      ).toBeInTheDocument();
      expect(
        screen.getByText("V2 keeps the prompt it was saved with."),
      ).toBeInTheDocument();
      expect(screen.getByText(DEFAULT_SAVE_LINE)).toBeInTheDocument();
      expect(
        screen.getByText(
          /^Uses V2's own prompt and its model, .+\. Your changes are discarded\.$/,
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText(/aren't in V1/)).toBeNull();
      expect(screen.queryByText(/^V1 runs the prompt/)).toBeNull();
      expect(onSave).not.toHaveBeenCalled();
      // As saved puts back the default version's prompt and its model; the
      // stored pin is left as it was.
      fireEvent.click(
        screen.getByRole("button", { name: "Update with V2 as saved" }),
      );
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0];
      expect(saved.instructions).toBe("Default {{output}}");
      expect(saved.model).toBe("turing_large");
      expect(saved.versionId).toBe("version-1");
      expect(saved.versionPin).toBe("v1");
      expect(saved.config.version_pin).toBe("v1");
    });

    it("saves a model change with no question, sending the changed model and the stored pin", async () => {
      withDefaultPrompt();
      const onSave = vi.fn();
      renderConfigFull({ source, evalData: existingBinding, onSave });
      await waitFor(() => expect(prompt()).toBe("Default {{output}}"));
      act(() => {
        capturedProps.llmPrompt.onModelChange("turing_small");
      });
      await saveBinding("Update Evaluation");
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(screen.queryByText(/isn't in V|aren't in V/)).toBeNull();
      const saved = onSave.mock.calls[0][0];
      // The model the run uses on these hosts is the one the save sends.
      expect(saved.model).toBe("turing_small");
      expect(serializeEvalConfig(saved).model).toBe("turing_small");
      expect(saved.versionId).toBe("version-1");
      expect(saved.versionPin).toBe("v1");
      expect(saved.config.version_pin).toBe("v1");
    });

    it("keeps today's confirm when the template has no default version", async () => {
      stableVersions.data = {
        versions: stableVersions.data.versions.map((version) => ({
          ...version,
          is_default: false,
        })),
      };
      const onSave = vi.fn();
      renderConfigFull({ source, evalData: existingBinding, onSave });
      await waitFor(() => expect(prompt()).toBe("Historic {{output}}"));
      editPrompt("Edited {{output}}");
      await saveBinding("Update Evaluation");
      expect(
        await screen.findByText("Your prompt edits aren't in V1"),
      ).toBeInTheDocument();
      expect(
        screen.getByText("V1 runs the prompt and model it was saved with."),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          "Saves your changes as a new version and updates the evaluation to that version.",
        ),
      ).toBeInTheDocument();
      expect(onSave).not.toHaveBeenCalled();
    });

    it("asks nothing and sends no version when a binding with no stored pin is edited", async () => {
      withDefaultPrompt();
      const onSave = vi.fn();
      renderConfigFull({
        source,
        evalData: { ...DEFAULT_EVAL_DATA, userEvalId: "binding-1" },
        onSave,
      });
      await waitFor(() => expect(prompt()).toBe("Current {{output}}"));
      editPrompt("Edited {{output}}");
      await saveBinding("Update Evaluation");
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0];
      expect(saved.versionId ?? null).toBeNull();
      expect(saved).not.toHaveProperty("versionPin");
      expect(saved.config).not.toHaveProperty("version_pin");
      const body = JSON.stringify(serializeEvalConfig(saved));
      expect(body).not.toMatch(/version_pin|pinned_version_id|versionId/);
    });

    it("adds a new binding with no version and the evaluation's own model, whatever a version's saved settings name", async () => {
      withVersionModelOnly();
      withOtherCurrentModel();
      const onSave = vi.fn();
      renderConfigFull({ source, onSave });
      await waitFor(() => expect(prompt()).toBe("Current {{output}}"));
      expect(capturedProps.llmPrompt.model).toBe("turing_large");
      await saveBinding("Add Evaluation");
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(screen.queryByText(/isn't in V|aren't in V/)).toBeNull();
      const saved = onSave.mock.calls[0][0];
      expect(saved.model).toBe("turing_large");
      expect(saved.versionId ?? null).toBeNull();
      expect(saved).not.toHaveProperty("versionPin");
      expect(saved.config).not.toHaveProperty("version_pin");
      const body = JSON.stringify(serializeEvalConfig(saved));
      expect(body).not.toMatch(/version_pin|pinned_version_id|versionId/);
    });

    it("puts back the default version's own model as saved, and names it", async () => {
      // The default version (V2) names a model of its own, different from
      // the stored version's, the evaluation's and the binding's.
      stableVersions.data = {
        versions: stableVersions.data.versions.map((version) =>
          version.id === "version-2"
            ? {
                ...version,
                config_snapshot: {
                  ...version.config_snapshot,
                  model: "turing_flash",
                  rule_prompt: "Default {{output}}",
                },
              }
            : version,
        ),
      };
      const onSave = vi.fn();
      renderConfigFull({
        source,
        evalData: { ...existingBinding, run_config: { model: "turing_small" } },
        onSave,
      });
      await waitFor(() => expect(prompt()).toBe("Default {{output}}"));
      expect(capturedProps.llmPrompt.model).toBe("turing_small");
      editPrompt("Edited {{output}}");
      await saveBinding("Update Evaluation");
      expect(
        await screen.findByText(
          "Uses V2's own prompt and its model, Turing Flash. Your changes are discarded.",
        ),
      ).toBeInTheDocument();
      fireEvent.click(
        screen.getByRole("button", { name: "Update with V2 as saved" }),
      );
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0];
      expect(saved.model).toBe("turing_flash");
      expect(saved.instructions).toBe("Default {{output}}");
      expect(saved.versionId).toBe("version-1");
      expect(saved.versionPin).toBe("v1");
    });

    it("saves an edit as a new version and moves the stored pin to it", async () => {
      withDefaultPrompt();
      const listed = stableVersions.data;
      const create = stableCreateVersion.mutateAsync;
      stableCreateVersion.mutateAsync = vi.fn(async ({ config_snapshot }) => {
        stableVersions.data = {
          versions: [
            { id: "version-3", version_number: 3, config_snapshot },
            ...listed.versions,
          ],
        };
        return { id: "version-3", version_number: 3 };
      });
      try {
        const onSave = vi.fn();
        renderConfigFull({ source, evalData: existingBinding, onSave });
        await waitFor(() => expect(prompt()).toBe("Default {{output}}"));
        editPrompt("Edited {{output}}");
        await saveBinding("Update Evaluation");
        fireEvent.click(
          await screen.findByRole("button", {
            name: "Save as new version and update",
          }),
        );
        await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
        expect(stableCreateVersion.mutateAsync).toHaveBeenCalledTimes(1);
        const saved = onSave.mock.calls[0][0];
        expect(saved.instructions).toBe("Edited {{output}}");
        expect(saved.versionId).toBe("version-3");
        expect(saved.versionPin).toBe("v3");
        expect(saved.config.version_pin).toBe("v3");
        expect(existingBinding.pinned_version_id).toBe("version-1");
      } finally {
        stableVersions.data = listed;
        stableCreateVersion.mutateAsync = create;
      }
    });

    it("saves an edit as a new version that also becomes the settings that run, and says so", async () => {
      withDefaultPrompt();
      const listed = stableVersions.data;
      const create = stableCreateVersion.mutateAsync;
      stableCreateVersion.mutateAsync = vi.fn(async ({ config_snapshot }) => {
        stableVersions.data = {
          versions: [
            { id: "version-3", version_number: 3, config_snapshot },
            ...listed.versions,
          ],
        };
        return { id: "version-3", version_number: 3 };
      });
      const update = vi.spyOn(stableUpdateEval, "mutateAsync");
      try {
        const onSave = vi.fn();
        renderConfigFull({ source, evalData: existingBinding, onSave });
        await waitFor(() => expect(prompt()).toBe("Default {{output}}"));
        editPrompt("Edited {{output}}");
        await saveBinding("Update Evaluation");
        expect(await screen.findByText(DEFAULT_SAVE_LINE)).toBeInTheDocument();
        fireEvent.click(
          screen.getByRole("button", {
            name: "Save as new version and update",
          }),
        );
        await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
        // The save writes the evaluation's current settings, which these
        // hosts run: the edited text, whichever version is the default.
        expect(update).toHaveBeenCalledTimes(1);
        expect(update.mock.calls[0][0].instructions).toBe("Edited {{output}}");
        expect(stableCreateVersion.mutateAsync).toHaveBeenCalledTimes(1);
      } finally {
        update.mockRestore();
        stableVersions.data = listed;
        stableCreateVersion.mutateAsync = create;
      }
    });

    it("reopens a stored pin whose settings name no model on the default version's own model, asking nothing", async () => {
      // Only the versions themselves name a model; the binding and the
      // versions' saved settings name none.
      stableVersions.data = {
        versions: stableVersions.data.versions.map((version) => ({
          ...version,
          model: version.id === "version-2" ? "gpt-4o" : "turing_flash",
          config_snapshot: {
            rule_prompt:
              version.id === "version-2"
                ? "Default {{output}}"
                : "Historic {{output}}",
          },
        })),
      };
      withOtherCurrentModel();
      const onSave = vi.fn();
      renderConfigFull({ source, evalData: existingBinding, onSave });
      await waitFor(() => expect(prompt()).toBe("Default {{output}}"));
      expect(capturedProps.llmPrompt.model).toBe("gpt-4o");
      await saveBinding("Update Evaluation");
      expect(screen.queryByText(/isn't in V|aren't in V/)).toBeNull();
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0];
      expect(saved.model).toBe("gpt-4o");
      expect(saved.versionId).toBe("version-1");
      expect(saved.versionPin).toBe("v1");
    });

    it("still asks first when an ordinary binding has its prompt edited", async () => {
      withDefaultPrompt();
      const onSave = vi.fn();
      renderConfigFull({ source, evalData: existingBinding, onSave });
      await waitFor(() => expect(prompt()).toBe("Default {{output}}"));
      editPrompt("Edited {{output}}");
      await saveBinding("Update Evaluation");
      expect(
        await screen.findByText("Your prompt edits aren't in V2"),
      ).toBeInTheDocument();
      expect(
        screen.getByText("V2 keeps the prompt it was saved with."),
      ).toBeInTheDocument();
      expect(onSave).not.toHaveBeenCalled();
    });

    // A binding saved with a decision model keeps its own prompt turns in its
    // run settings, and those are handed to the decision judge over the
    // default version's. The editor shows them, and an edit to them is saved
    // there and runs.
    const route = "decision/openrouter/morph/reflex";
    const rubric = (label) => ({
      score: { levels: [`${label} bad`, `${label} good`] },
    });
    const decisionSnapshot = (label, rule_prompt) => ({
      model: route,
      output: "score",
      rule_prompt,
      jev_mapping: rubric(label),
    });
    const storedTurns = [{ role: "system", content: "Stored {{output}}" }];
    // A decision model takes no few-shot examples (its check refuses them
    // and holds the save), so a saved binding carries an empty list.
    const storedShots = [];
    const withDecisionBinding = () => {
      stableEvalDetail.data = {
        ...DEFAULT_DETAIL,
        owner: "user",
        output_type: "percentage",
        current_version: 2,
        instructions: "Default {{output}}",
        required_keys: ["output"],
        config: decisionSnapshot("Current", "Default {{output}}"),
      };
      stableVersions.data = {
        versions: [
          {
            id: "version-2",
            version_number: 2,
            is_default: true,
            config_snapshot: decisionSnapshot(
              "Version two",
              "Default {{output}}",
            ),
          },
          {
            id: "version-1",
            version_number: 1,
            is_default: false,
            config_snapshot: decisionSnapshot(
              "Version one",
              "Historic {{output}}",
            ),
          },
        ],
      };
      return {
        ...DEFAULT_EVAL_DATA,
        name: "saved_quality",
        userEvalId: "binding-1",
        pinned_version_id: "version-1",
        bindingConfig: { jev_mapping: rubric("Authored") },
        run_config: {
          model: route,
          agent_mode: "quick",
          messages: storedTurns,
          few_shot_examples: storedShots,
        },
      };
    };

    it("reopens a decision model's binding on its stored turns, and an unchanged Update asks nothing and keeps them", async () => {
      const evalData = withDecisionBinding();
      const storedRunConfig = JSON.stringify(evalData.run_config);
      const onSave = vi.fn();
      renderConfigFull({ source, evalData, onSave });
      await waitFor(() => expect(prompt()).toBe("Stored {{output}}"));
      expect(capturedProps.llmPrompt.messages).toEqual(storedTurns);
      await saveBinding("Update Evaluation");
      expect(screen.queryByText("Your prompt edits aren't in V2")).toBeNull();
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0];
      expect(saved.versionId).toBe("version-1");
      // Opening and an unchanged Update neither delete nor rewrite the
      // binding's stored turns: the save carries them as they were.
      const runConfig = serializeEvalConfig(saved).config.run_config;
      expect(JSON.stringify(runConfig.messages)).toBe(
        JSON.stringify(storedTurns),
      );
      expect(JSON.stringify(runConfig.few_shot_examples)).toBe(
        JSON.stringify(storedShots),
      );
      expect(JSON.stringify(evalData.run_config)).toBe(storedRunConfig);
    });

    it("saves a prompt edit on a decision model's binding with no question, carrying the edited turns", async () => {
      const evalData = withDecisionBinding();
      const onSave = vi.fn();
      renderConfigFull({ source, evalData, onSave });
      await waitFor(() => expect(prompt()).toBe("Stored {{output}}"));
      editPrompt("Edited {{output}}");
      await saveBinding("Update Evaluation");
      expect(screen.queryByText("Your prompt edits aren't in V2")).toBeNull();
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0];
      expect(saved.versionId).toBe("version-1");
      const runConfig = serializeEvalConfig(saved).config.run_config;
      expect(runConfig.messages).toEqual([
        { role: "system", content: "Edited {{output}}" },
      ]);
      expect(JSON.stringify(runConfig.few_shot_examples)).toBe(
        JSON.stringify(storedShots),
      );
    });
  });
});

// A composite evaluation on the workbench keeps a stored jury version in its
// saved settings. The workbench runs the default version, so the header names
// that version, and the save still carries the stored jury version.
describe("the evaluation header for a composite with a stored jury version on the workbench", () => {
  const originalDetail = stableEvalDetail.data;
  const originalVersions = stableVersions.data;
  beforeEach(() => {
    setLaunchOverrideForTest({ launch: true, madeReal: [] });
    capturedProps.dataset = null;
    stableEvalDetail.data = {
      ...DEFAULT_DETAIL,
      owner: "user",
      template_type: "composite",
      eval_type: "composite",
      current_version: 2,
    };
    stableVersions.data = {
      versions: [
        { id: "version-2", version_number: 2, is_default: true },
        { id: "version-1", version_number: 1, is_default: false },
      ],
    };
    stableCompositeDetail.data = {
      children: [
        {
          child_id: "child-a",
          child_name: "relevance",
          required_keys: ["question"],
        },
      ],
    };
  });
  afterEach(() => {
    setLaunchOverrideForTest(null);
    stableEvalDetail.data = originalDetail;
    stableVersions.data = originalVersions;
    stableCompositeDetail.data = null;
  });

  it("names the default version, and keeps the stored jury version in the save", async () => {
    const onSave = vi.fn();
    renderConfigFull({
      source: "workbench",
      onSave,
      evalData: {
        id: "composite-1",
        templateId: "composite-1",
        userEvalId: "binding-1",
        name: "grounded_answers",
        mapping: { question: "output-column" },
        bindingConfig: { jury_version_id: "version-1" },
      },
    });
    await waitFor(() => expect(capturedProps.dataset).not.toBeNull());
    expect(screen.getByRole("combobox")).toHaveTextContent("Current settings");
    expect(screen.getByText(bpRendered.header("V2"))).toBeInTheDocument();
    expect(screen.queryByText(bpRendered.header("V1"))).toBeNull();
    await act(async () => {
      capturedProps.dataset.onReadyChange(true, { question: "output-column" });
    });
    const update = screen.getByRole("button", { name: "Update Evaluation" });
    await waitFor(() => expect(update).toBeEnabled());
    fireEvent.click(update);
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave.mock.calls[0][0].versionId).toBe("version-1");
  });
});

// Beside the team's version reset case: a task host runs its template's
// default version, so after an attempted choice and after the version list is
// fetched again the choice still shows the default version, cannot be changed
// and says why.
describe("EvalPickerConfigFull — the version choice on a task host after a reset", () => {
  const originalDetail = stableEvalDetail.data;
  const originalVersions = stableVersions.data;
  beforeEach(() => {
    capturedProps.llmPrompt = null;
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
    stableEvalDetail.data = originalDetail;
    stableVersions.data = originalVersions;
  });

  it("still shows the default version, disabled, with the line", async () => {
    const queryClient = newQueryClient();
    const evalData = {
      ...DEFAULT_EVAL_DATA,
      userEvalId: "binding-1",
      pinned_version_id: "version-1",
    };
    const tree = () => (
      <EvalPickerProvider
        source="task"
        sourceId="project-1"
        sourceRowType="traces"
        sourceColumns={[]}
        existingEvals={[]}
        onEvalAdded={() => {}}
        onClose={() => {}}
        initialEval={evalData}
      >
        <EvalPickerConfigFull
          evalData={evalData}
          onBack={() => {}}
          onSave={() => {}}
          isSaving={false}
        />
      </EvalPickerProvider>
    );
    const expectDefaultOnly = () => {
      const versionChoice = screen.getByRole("combobox");
      expect(versionChoice).toHaveTextContent("Current settings");
      expect(versionChoice).toHaveAttribute("aria-disabled", "true");
      expect(
        screen.getByText(
          "A version can't be chosen here. This evaluation runs with its current settings.",
        ),
      ).toBeInTheDocument();
    };

    const view = render(tree(), { queryClient });
    await waitFor(() =>
      expect(capturedProps.llmPrompt?.messages?.[0]?.content).toBeTruthy(),
    );
    expectDefaultOnly();

    fireEvent.mouseDown(screen.getByRole("combobox"));
    expect(screen.queryByRole("option")).toBeNull();
    expectDefaultOnly();

    stableVersions.data = {
      versions: stableVersions.data.versions.map((version) => ({
        ...version,
        usage_count: 3,
      })),
    };
    view.rerender(
      <QueryClientProvider client={queryClient}>{tree()}</QueryClientProvider>,
    );
    await waitFor(() =>
      expect(screen.getByRole("combobox")).toHaveTextContent(
        "Current settings",
      ),
    );
    expectDefaultOnly();
  });
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
  ["saved-v1", "Historic {{output}}"],
  [null, "Current {{output}}"],
])(
  "keeps the explicit pin/default %s; the version choice names no version",
  async (pinnedVersionId, prompt) => {
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
      // The header names the version that runs on this host (no version is
      // marked default here, so the current one), never the stored pin.
      expect(screen.getByText(bpRendered.header(2))).toBeInTheDocument();
      expect(screen.queryByText(bpRendered.header("V1"))).toBeNull();
      const versionChoice = screen.getByRole("combobox");
      expect(versionChoice).toHaveTextContent("Current settings");
      expect(versionChoice).toHaveAttribute("aria-disabled", "true");
      expect(
        screen.queryByRole("option", { name: "Current settings" }),
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
