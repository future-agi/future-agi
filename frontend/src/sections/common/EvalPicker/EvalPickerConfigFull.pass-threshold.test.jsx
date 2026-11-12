/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render as renderWithTheme } from "src/utils/test-utils";
import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import EvalPickerProvider from "./context/EvalPickerProvider";
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

// The dataset binding editor's pass threshold is a setting of the binding
// (config.run_config.pass_threshold, which a run applies before the version's
// bar). Reopening shows the saved bar, and changing only the bar updates the
// binding without saving a new version of the evaluation.

const { captured } = vi.hoisted(() => ({ captured: { llmPrompt: null } }));

vi.mock("src/sections/evals/components/DatasetTestMode", () => {
  const M = React.forwardRef((props, _ref) => {
    const { onReadyChange } = props;
    React.useEffect(() => {
      onReadyChange?.(true, { output: "col-output" });
    }, [onReadyChange]);
    return <div />;
  });
  M.displayName = "DatasetTestModeMock";
  return { default: M, JsonValueTree: () => <div /> };
});

vi.mock("src/sections/evals/components/TracingTestMode", () => {
  const M = React.forwardRef(() => <div />);
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
  default: () => <div />,
}));

vi.mock("src/sections/common/DecisionJudges/DecisionQuestionEditor", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/evals/components/LLMPromptEditor", () => ({
  default: (props) => {
    captured.llmPrompt = props;
    return <div data-testid="llm-prompt-editor" />;
  },
}));

vi.mock("src/sections/evals/components/CodeEvalEditor", () => ({
  default: () => <div />,
}));

// The bar the editor holds, and a control that moves it.
vi.mock("src/sections/evals/components/OutputTypeConfig", () => ({
  default: ({ passThreshold, onPassThresholdChange }) => (
    <div>
      <span data-testid="pass-bar">{String(passThreshold)}</span>
      <button type="button" onClick={() => onPassThresholdChange(0.8)}>
        bar 0.8
      </button>
    </div>
  ),
}));

vi.mock("src/sections/evals/components/FewShotExamples", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/tasks/components/TaskFilterBar", () => ({
  default: () => <div />,
}));

vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ children }) => <>{children}</>,
}));

vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({
  default: () => <div />,
}));

// Hook mocks return referentially stable values.
const { detail, versions, updateEval, createVersion, composite, unionKeys } =
  vi.hoisted(() => ({
    detail: {
      data: {
        id: "tpl-1",
        name: "release_scorer",
        owner: "user",
        eval_type: "llm",
        output_type: "percentage",
        instructions: "Newest {{output}}",
        current_version: "V2",
        config: {
          model: "gpt-4o",
          rule_prompt: "Newest {{output}}",
          output: "score",
          required_keys: ["output"],
        },
      },
      isLoading: false,
      isError: false,
    },
    versions: {
      data: {
        versions: [
          {
            id: "ver-2",
            version_number: 2,
            is_default: true,
            config_snapshot: {
              rule_prompt: "Newest {{output}}",
              output: "score",
              pass_threshold: 0.5,
              model: "gpt-4o",
            },
          },
          {
            id: "ver-1",
            version_number: 1,
            is_default: false,
            config_snapshot: {
              rule_prompt: "Historic {{output}}",
              output: "score",
              pass_threshold: 0.5,
              model: "gpt-4o",
            },
          },
        ],
      },
    },
    updateEval: { mutate: () => {}, mutateAsync: async () => ({}) },
    createVersion: { mutateAsync: async () => ({}) },
    composite: { data: null },
    unionKeys: [],
  }));

vi.mock("src/sections/evals/hooks/useEvalDetail", () => ({
  useEvalDetail: () => detail,
  useUpdateEval: () => updateEval,
}));

vi.mock("src/sections/evals/hooks/useEvalVersions", () => ({
  useEvalVersions: () => versions,
  useCreateEvalVersion: () => createVersion,
}));

vi.mock("src/sections/evals/hooks/useCompositeEval", () => ({
  useCompositeDetail: () => composite,
}));

vi.mock("src/sections/evals/hooks/useCompositeChildrenKeys", () => ({
  useCompositeChildrenUnionKeys: () => unionKeys,
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
    mode: "oss",
    isCloud: false,
    isOSS: true,
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

// A saved dataset binding, as the dataset's saved eval list opens it.
const binding = (overrides = {}) => ({
  id: "tpl-1",
  templateId: "tpl-1",
  userEvalId: "binding-1",
  name: "release_scorer",
  mapping: { output: "col-output" },
  run_config: { model: "gpt-4o", pass_threshold: 0.7 },
  ...overrides,
});

const renderBinding = (evalData, onSave) =>
  render(
    <EvalPickerProvider
      source="dataset"
      sourceId="dataset-1"
      sourceColumns={[{ field: "col-output", headerName: "output" }]}
      existingEvals={[]}
      onEvalAdded={() => {}}
      onClose={() => {}}
      initialEval={evalData}
    >
      <EvalPickerConfigFull
        evalData={evalData}
        onBack={() => {}}
        onSave={onSave}
        isSaving={false}
      />
    </EvalPickerProvider>,
  );

const update = async () => {
  const button = await screen.findByRole("button", {
    name: "Update Evaluation",
  });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
};

describe("EvalPickerConfigFull — a dataset binding's pass threshold", () => {
  beforeEach(() => {
    captured.llmPrompt = null;
  });

  it("reopens a binding pinned to a saved version with the bar the binding saved", async () => {
    renderBinding(binding({ pinned_version_id: "ver-1" }), vi.fn());
    await waitFor(() =>
      expect(captured.llmPrompt?.messages?.[0]?.content).toBe(
        "Historic {{output}}",
      ),
    );
    expect(screen.getByTestId("pass-bar")).toHaveTextContent("0.7");
  });

  it("reopens a binding on the default version with the bar the binding saved", async () => {
    renderBinding(binding(), vi.fn());
    await waitFor(() =>
      expect(screen.getByTestId("pass-bar")).toHaveTextContent("0.7"),
    );
  });

  it("keeps the pinned version when only the bar changes", async () => {
    const onSave = vi.fn();
    renderBinding(binding({ pinned_version_id: "ver-1" }), onSave);
    await waitFor(() =>
      expect(captured.llmPrompt?.messages?.[0]?.content).toBe(
        "Historic {{output}}",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "bar 0.8" }));
    expect(screen.getByTestId("pass-bar")).toHaveTextContent("0.8");
    await update();
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0][0];
    expect(saved.versionId).toBe("ver-1");
    expect(saved.pass_threshold).toBe(0.8);
    expect(saved.instructions).toBe("Historic {{output}}");
  });

  it("keeps following the default version when only the bar changes", async () => {
    const onSave = vi.fn();
    renderBinding(binding(), onSave);
    await waitFor(() =>
      expect(screen.getByTestId("pass-bar")).toHaveTextContent("0.7"),
    );
    fireEvent.click(screen.getByRole("button", { name: "bar 0.8" }));
    await update();
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0][0];
    expect(saved.versionId).toBeNull();
    expect(saved.pass_threshold).toBe(0.8);
  });

  it("still saves an edited prompt as a new version (the bar moved too)", async () => {
    const onSave = vi.fn();
    renderBinding(binding({ pinned_version_id: "ver-1" }), onSave);
    await waitFor(() =>
      expect(captured.llmPrompt?.messages?.[0]?.content).toBe(
        "Historic {{output}}",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "bar 0.8" }));
    act(() => {
      captured.llmPrompt.onMessagesChange([
        { role: "system", content: "Edited {{output}}" },
      ]);
    });
    await update();
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0][0];
    expect(saved.versionId).toBeUndefined();
    expect(saved.instructions).toBe("Edited {{output}}");
    expect(saved.pass_threshold).toBe(0.8);
  });
});
