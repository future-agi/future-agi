import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "src/utils/test-utils";

import EvalPickerProvider from "./context/EvalPickerProvider";
import EvalPickerConfigFull from "./EvalPickerConfigFull";

const { capturedProps } = vi.hoisted(() => ({
  capturedProps: { tracing: null },
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

const renderConfigFull = ({ sourceTimeWindow } = {}) =>
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
        evalData={{ id: "tpl-1", templateId: "tpl-1", name: "toxicity" }}
        onBack={() => {}}
        onSave={() => {}}
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
