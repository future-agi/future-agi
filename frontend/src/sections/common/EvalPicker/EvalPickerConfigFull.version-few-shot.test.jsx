/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render as renderWithTheme } from "src/utils/test-utils";
import { act, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

import EvalPickerProvider from "./context/EvalPickerProvider";
import EvalPickerConfigFull from "./EvalPickerConfigFull";

// A picked version runs the prompt it was saved with: its rule prompt, its
// messages and its few-shot examples. Picking another version for a saved
// binding loads all three, so an untouched pick is no prompt edit, Update
// asks nothing, and the save carries that version's own few-shot examples.
// The mocks follow EvalPickerConfigFull.bindings-versions.test.jsx.

const { capturedProps } = vi.hoisted(() => ({
  capturedProps: {
    llmPrompt: null,
    dataset: null,
    fewShot: null,
  },
}));

vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => false,
}));

vi.mock("src/sections/evals/components/TracingTestMode", () => {
  const M = React.forwardRef(() => <div />);
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

vi.mock("src/api/evaluations/decision-models", () => ({
  default: () => ({ data: [] }),
  decisionModelsQueryKey: ["eval-decision-models"],
}));

vi.mock("src/sections/evals/components/ModelSelector", () => ({
  default: () => <div />,
  FAGI_MODEL_VALUES: new Set(["turing_large", "turing_small", "turing_flash"]),
}));

vi.mock("src/sections/evals/components/InstructionEditor", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/common/DecisionJudges/DecisionQuestionEditor", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/evals/components/LLMPromptEditor", () => ({
  default: (props) => {
    capturedProps.llmPrompt = props;
    return <div data-testid="llm-prompt-editor" />;
  },
}));

vi.mock("src/sections/evals/components/CodeEvalEditor", () => ({
  default: () => <div />,
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

vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ show, title, children }) => (
    <>
      {children}
      {show && title ? <div data-testid="tooltip-text">{title}</div> : null}
    </>
  ),
}));

vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({
  default: () => <div />,
}));

// Hook mocks must return referentially stable values: a fresh object per
// call re-triggers every downstream useMemo/useEffect and loops the render.
const {
  stableEvalDetail,
  stableUpdateEval,
  stableVersions,
  stableCreateVersion,
  stableCompositeDetail,
} = vi.hoisted(() => ({
  stableEvalDetail: {
    data: null,
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
  stableCreateVersion: { mutateAsync: async () => ({}) },
  stableCompositeDetail: { data: null, isError: false, refetch: vi.fn() },
}));

vi.mock("src/sections/evals/hooks/useEvalDetail", () => ({
  useEvalDetail: () => stableEvalDetail,
  useUpdateEval: () => stableUpdateEval,
  evalDetailQuery: (templateId) => ({
    queryKey: ["evals", "detail", templateId],
    queryFn: () => new Promise(() => {}),
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
    useFeatureLocked: () => ({ locked: false, isLoading: false }),
    useFeatureDenied: () => false,
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

// V1 and V2 each keep their own few-shot examples.
const SHOTS_V1 = [
  { dataset_id: "shots-v1", mapping: { output: "v1-example-output" } },
];
const SHOTS_V2 = [
  { dataset_id: "shots-v2", mapping: { output: "v2-example-output" } },
];

// A saved dataset binding pinned to V1.
const bindingOnV1 = {
  id: "tpl-1",
  templateId: "tpl-1",
  name: "toxicity",
  userEvalId: "binding-1",
  pinned_version_id: "version-1",
  mapping: { output: "output-column" },
};

const renderPicker = (onSave) =>
  renderWithTheme(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, staleTime: Infinity } },
        })
      }
    >
      <EvalPickerProvider
        source="dataset"
        sourceId="dataset-1"
        sourceRowType="traces"
        sourceColumns={[]}
        existingEvals={[]}
        onEvalAdded={() => {}}
        onClose={() => {}}
        initialEval={bindingOnV1}
      >
        <EvalPickerConfigFull
          evalData={bindingOnV1}
          onBack={() => {}}
          onSave={onSave}
          isSaving={false}
        />
      </EvalPickerProvider>
    </QueryClientProvider>,
  );

const prompt = () => capturedProps.llmPrompt?.messages?.[0]?.content;

const pickVersion = async (name) => {
  fireEvent.mouseDown(screen.getByRole("combobox"));
  fireEvent.click(await screen.findByRole("option", { name }));
};

const update = async () => {
  await act(async () => {
    capturedProps.dataset.onReadyChange(true, { output: "output-column" });
  });
  const button = await screen.findByRole("button", {
    name: "Update Evaluation",
  });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
};

describe("EvalPickerConfigFull: a picked version's few-shot examples", () => {
  const originalDetail = stableEvalDetail.data;
  const originalVersions = stableVersions.data;
  beforeEach(() => {
    setLaunchOverrideForTest({ launch: true, experimentsBasic: false });
    capturedProps.llmPrompt = null;
    capturedProps.dataset = null;
    capturedProps.fewShot = null;
    stableEvalDetail.data = {
      id: "tpl-1",
      name: "toxicity",
      owner: "user",
      eval_type: "llm",
      output_type: "pass_fail",
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
            few_shot_examples: SHOTS_V2,
          },
        },
        {
          id: "version-1",
          version_number: 1,
          is_default: false,
          config_snapshot: {
            model: "turing_large",
            rule_prompt: "Historic {{output}}",
            few_shot_examples: SHOTS_V1,
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

  it("loads the picked version's own few-shot examples; Update asks nothing and saves them with that version", async () => {
    const onSave = vi.fn();
    renderPicker(onSave);
    await waitFor(() => expect(prompt()).toBe("Historic {{output}}"));
    expect(capturedProps.fewShot.selectedDatasets).toEqual(SHOTS_V1);

    await pickVersion("V2 (default)");
    await waitFor(() => expect(prompt()).toBe("Current {{output}}"));
    // Nothing was typed: the editor shows V2's own examples, not V1's.
    await waitFor(() =>
      expect(capturedProps.fewShot.selectedDatasets).toEqual(SHOTS_V2),
    );

    await update();
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/aren't in V2|isn't in V2/)).toBeNull();
    const saved = onSave.mock.calls[0][0];
    expect(saved.versionId).toBe("version-2");
    expect(saved.versionPin).toBe("v2");
    expect(saved.config.few_shot_examples).toEqual(SHOTS_V2);
    expect(saved.bindingPayload.pinned_version_id).toBe("version-2");
    expect(saved.bindingPayload.config.config.few_shot_examples).toEqual(
      SHOTS_V2,
    );
  });

  it("still asks first when a few-shot example is changed after the pick", async () => {
    const onSave = vi.fn();
    renderPicker(onSave);
    await waitFor(() => expect(prompt()).toBe("Historic {{output}}"));

    await pickVersion("V2 (default)");
    await waitFor(() =>
      expect(capturedProps.fewShot.selectedDatasets).toEqual(SHOTS_V2),
    );
    await act(async () =>
      capturedProps.fewShot.onChange([
        ...SHOTS_V2,
        { dataset_id: "shots-new", mapping: { output: "new-output" } },
      ]),
    );

    await update();
    expect(
      await screen.findByText("Your prompt edits aren't in V2"),
    ).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("loads the evaluation's own few-shot examples when the default version is picked, and saves them", async () => {
    // The evaluation's current settings keep their own few-shot examples.
    stableEvalDetail.data = {
      ...stableEvalDetail.data,
      config: { ...stableEvalDetail.data.config, few_shot_examples: SHOTS_V2 },
    };
    const onSave = vi.fn();
    renderPicker(onSave);
    await waitFor(() => expect(prompt()).toBe("Historic {{output}}"));
    expect(capturedProps.fewShot.selectedDatasets).toEqual(SHOTS_V1);

    await pickVersion("Default version");
    await waitFor(() => expect(prompt()).toBe("Current {{output}}"));
    // Nothing was typed: the editor shows the evaluation's examples, not V1's.
    await waitFor(() =>
      expect(capturedProps.fewShot.selectedDatasets).toEqual(SHOTS_V2),
    );

    await update();
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave.mock.calls[0][0].config.few_shot_examples).toEqual(SHOTS_V2);
  });
});
