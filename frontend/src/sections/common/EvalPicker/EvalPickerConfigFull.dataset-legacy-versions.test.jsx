/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "src/utils/test-utils";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  hiddenSurfaceIds,
  isSurfaceShown,
  launchState,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";

import EvalPickerProvider from "./context/EvalPickerProvider";
import EvalPickerConfigFull from "./EvalPickerConfigFull";

// EvaluationDrawer opens this editor with source="dataset". The picker lock
// removes version selection even with every launch switch OFF, while retaining
// Save version and hydration of stored pins (including a held latest pin).
// The QueryClientProvider only satisfies hooks that read react-query; every
// query here stays disabled (no claims), so nothing is fetched.

const { preview } = vi.hoisted(() => ({ preview: { onReadyChange: null } }));
vi.mock("src/sections/evals/components/DatasetTestMode", () => {
  const M = React.forwardRef((props, _ref) => {
    const { onReadyChange } = props;
    preview.onReadyChange = onReadyChange;
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
  default: () => <div data-testid="llm-prompt-editor" />,
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

const renderDatasetBinding = (evalData, onSave) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
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
      </EvalPickerProvider>
    </QueryClientProvider>,
  );

const update = async (onSave) => {
  const button = await screen.findByRole("button", {
    name: "Update Evaluation",
  });
  await waitFor(() => expect(button).toBeDisabled());
  fireEvent.click(button);
  expect(onSave).not.toHaveBeenCalled();
  await act(async () =>
    preview.onReadyChange(true, { output: "other-column" }),
  );
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  const saved = onSave.mock.calls[0][0];
  expect(saved.bindingPayload.config.mapping).toEqual({
    output: "other-column",
  });
  return saved;
};

const expectLockedVersionControls = () => {
  expect(
    screen.queryByRole("combobox", { name: "Saved eval version" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Saved eval version")).toBeNull();
  expect(screen.queryByRole("option", { name: /Latest/ })).toBeNull();
  expect(screen.getByRole("button", { name: "Save version" })).toBeEnabled();
};

describe("dataset eval drawer keeps Save version and stored pins without a selector with every launch switch OFF", () => {
  beforeEach(() => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: [],
    });
  });
  afterEach(() => {
    setLaunchOverrideForTest(null);
  });

  it("runs in a launch build with no claims and experimentsBasic OFF", () => {
    const state = launchState();
    expect(state.launch).toBe(true);
    expect(state.experimentsBasic).toBe(false);
    expect(state.madeReal.size).toBe(0);
    expect(isSurfaceShown("dataset.evalVersions")).toBe(false);
    expect(hiddenSurfaceIds()).toContain("dataset.evalVersions");
  });

  it("keeps Save version enabled and saves the default without offering version selection", async () => {
    const onSave = vi.fn();
    renderDatasetBinding(binding(), onSave);
    await screen.findByText("Version label: V2");
    expectLockedVersionControls();
    const saved = await update(onSave);
    expect(saved.userEvalId).toBe("binding-1");
    expect(saved.versionId).toBeNull();
    expect(saved).not.toHaveProperty("versionPin");
    expect(saved.config).not.toHaveProperty("version_pin");
    expectLockedVersionControls();
  });

  it("reopens and saves a numbered pin unchanged without a selector", async () => {
    const onSave = vi.fn();
    const evalData = binding({ pinned_version_id: "ver-1" });
    renderDatasetBinding(evalData, onSave);
    await screen.findByText("Version label: V1");
    expectLockedVersionControls();
    const saved = await update(onSave);
    expect(saved.userEvalId).toBe("binding-1");
    expect(saved.versionId).toBe("ver-1");
    expect(saved.versionPin).toBe("v1");
    expect(saved.config.version_pin).toBe("v1");
    expect(saved.instructions).toBe("Historic {{output}}");
    expect(evalData.pinned_version_id).toBe("ver-1");
    expectLockedVersionControls();
  });

  it("saves a held latest pin unchanged without offering new version selection", async () => {
    const onSave = vi.fn();
    const evalData = binding({
      run_config: {
        model: "gpt-4o",
        pass_threshold: 0.7,
        version_pin: "latest",
      },
    });
    renderDatasetBinding(evalData, onSave);
    await screen.findByText("Version label: V2");
    expectLockedVersionControls();
    const saved = await update(onSave);
    expect(saved.userEvalId).toBe("binding-1");
    expect(saved.versionId).toBe("latest");
    expect(saved.versionPin).toBe("latest");
    expect(saved.config.version_pin).toBe("latest");
    expect(saved.instructions).toBe("Newest {{output}}");
    expect(evalData.run_config.version_pin).toBe("latest");
    expectLockedVersionControls();
  });
});
