import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "src/utils/test-utils";

import EvalPickerProvider from "./context/EvalPickerProvider";
import EvalPickerConfigFull from "./EvalPickerConfigFull";

const { capturedProps } = vi.hoisted(() => ({
  capturedProps: { tracing: null, llm: null },
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
  default: () => <div />,
}));

vi.mock("src/sections/evals/components/LLMPromptEditor", () => ({
  default: (props) => {
    capturedProps.llm = props;
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
  default: () => <div />,
}));

vi.mock("src/sections/tasks/components/TaskFilterBar", () => ({
  default: () => <div />,
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
      eval_type: "llm",
      output_type: "pass_fail",
      config: {},
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

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false }),
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

const renderConfigFull = ({ sourceTimeWindow, evalData, onSave } = {}) =>
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
    >
      <EvalPickerConfigFull
        evalData={
          evalData ?? { id: "tpl-1", templateId: "tpl-1", name: "toxicity" }
        }
        onBack={() => {}}
        onSave={onSave ?? (() => {})}
        isSaving={false}
      />
    </EvalPickerProvider>,
  );

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

describe("EvalPickerConfigFull — judge model params (#1764)", () => {
  beforeEach(() => {
    capturedProps.tracing = null;
    capturedProps.llm = null;
    // Give the llm eval real prompt content + a variable so the
    // Add Evaluation button's content gates pass.
    stableEvalDetail.data.config = {
      messages: [{ role: "system", content: "Judge {{input}} for toxicity" }],
    };
  });

  afterEach(() => {
    stableEvalDetail.data.config = {};
  });

  const clickAdd = () => {
    // Source readiness is reported by the (mocked) test-mode panel.
    act(() => {
      capturedProps.tracing.onReadyChange(true, { input: "col-1" });
    });
    fireEvent.click(screen.getByRole("button", { name: /add evaluation/i }));
  };

  it("omits model_params from the save payload when never touched", () => {
    const onSave = vi.fn();
    renderConfigFull({ onSave });

    clickAdd();

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0]).not.toHaveProperty("model_params");
  });

  it("carries applied model params into the save payload, preserving zero", () => {
    const onSave = vi.fn();
    renderConfigFull({ onSave });

    expect(capturedProps.llm).not.toBeNull();
    act(() => {
      capturedProps.llm.onModelParamsChange({
        temperature: 0,
        max_tokens: 256,
      });
    });

    clickAdd();

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0].model_params).toEqual({
      temperature: 0,
      max_tokens: 256,
    });
  });

  it("clears model_params from the payload when the override is removed", () => {
    const onSave = vi.fn();
    renderConfigFull({ onSave });

    act(() => {
      capturedProps.llm.onModelParamsChange({ temperature: 0.2 });
    });
    act(() => {
      capturedProps.llm.onModelParamsChange(null);
    });

    clickAdd();

    expect(onSave.mock.calls[0][0]).not.toHaveProperty("model_params");
  });

  it("hydrates saved run_config.model_params in edit mode and re-emits on save", () => {
    const onSave = vi.fn();
    renderConfigFull({
      onSave,
      evalData: {
        id: "tpl-1",
        templateId: "tpl-1",
        name: "toxicity",
        run_config: { model_params: { temperature: 0 } },
      },
    });

    // The editor receives the saved values (falsy zero included) …
    expect(capturedProps.llm.modelParams).toEqual({ temperature: 0 });

    // … and an untouched save round-trips them.
    clickAdd();
    expect(onSave.mock.calls[0][0].model_params).toEqual({ temperature: 0 });
  });
});
