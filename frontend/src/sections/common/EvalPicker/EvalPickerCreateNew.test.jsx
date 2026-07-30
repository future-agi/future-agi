import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "src/utils/test-utils";

import EvalPickerProvider from "./context/EvalPickerProvider";
import EvalPickerCreateNew from "./EvalPickerCreateNew";

const { capturedProps } = vi.hoisted(() => ({
  capturedProps: { simulation: null, tracing: null, dataset: null },
}));

vi.mock("src/sections/evals/components/SimulationTestMode", () => {
  const M = React.forwardRef((props, _ref) => {
    capturedProps.simulation = props;
    return <div data-testid="simulation-test-mode" />;
  });
  M.displayName = "SimulationTestModeMock";
  return { default: M };
});

vi.mock("src/sections/evals/components/TracingTestMode", () => {
  const M = React.forwardRef((props, _ref) => {
    capturedProps.tracing = props;
    return <div data-testid="tracing-test-mode" />;
  });
  M.displayName = "TracingTestModeMock";
  return { default: M };
});

vi.mock("src/sections/evals/components/DatasetTestMode", () => {
  const M = React.forwardRef((props, _ref) => {
    capturedProps.dataset = props;
    return <div data-testid="dataset-test-mode" />;
  });
  M.displayName = "DatasetTestModeMock";
  return { default: M, JsonValueTree: () => <div /> };
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

vi.mock("src/sections/evals/components/CompositeDetailPanel", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/tasks/components/TaskFilterBar", () => ({
  default: () => <div />,
}));

// Real buildApiFilterArray so the task time-window test exercises the
// actual created_at filter construction.
vi.mock(
  "src/sections/tasks/components/TaskLivePreview",
  async (importOriginal) => {
    const actual = await importOriginal();
    return { buildApiFilterArray: actual.buildApiFilterArray };
  },
);

// Transitive import of the real TaskLivePreview; its module-scope
// localStorage read breaks under the test environment.
vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({
  default: () => <div />,
}));

vi.mock("src/sections/evals/hooks/useCreateEval", () => ({
  useCreateEval: () => ({
    mutateAsync: vi.fn(async () => ({ id: "draft-1" })),
  }),
}));

vi.mock("src/sections/evals/hooks/useEvalDetail", () => ({
  useUpdateEval: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn(async () => ({})),
  }),
}));

vi.mock("src/sections/evals/hooks/useCompositeEval", () => ({
  useCreateCompositeEval: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock("src/sections/evals/hooks/useCompositeChildrenKeys", () => ({
  useCompositeChildrenUnionKeys: () => [],
}));

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false }),
}));

vi.mock("notistack", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
  };
});

const renderWithSource = (source, providerProps = {}) =>
  render(
    <EvalPickerProvider
      source={source}
      sourceId="sim-1"
      sourceColumns={[]}
      existingEvals={[]}
      onEvalAdded={() => {}}
      onClose={() => {}}
      {...providerProps}
    >
      <EvalPickerCreateNew onBack={() => {}} onSave={() => {}} />
    </EvalPickerProvider>,
  );

describe("EvalPickerCreateNew — onReadyChange wiring (TH-5013 regression)", () => {
  beforeEach(() => {
    capturedProps.simulation = null;
    capturedProps.tracing = null;
    capturedProps.dataset = null;
  });

  it("passes onReadyChange to SimulationTestMode so canSave can flip true after mapping", () => {
    renderWithSource("simulation");
    expect(capturedProps.simulation).not.toBeNull();
    expect(typeof capturedProps.simulation.onReadyChange).toBe("function");
  });

  it("passes onReadyChange to TracingTestMode for source='tracing'", () => {
    renderWithSource("tracing");
    expect(capturedProps.tracing).not.toBeNull();
    expect(typeof capturedProps.tracing.onReadyChange).toBe("function");
  });

  it("passes onReadyChange to DatasetTestMode (regression guard for sibling sources)", () => {
    renderWithSource("dataset");
    expect(capturedProps.dataset).not.toBeNull();
    expect(typeof capturedProps.dataset.onReadyChange).toBe("function");
  });
});

describe("EvalPickerCreateNew — task preview time window", () => {
  beforeEach(() => {
    capturedProps.tracing = null;
  });

  it("passes the task's time window to TracingTestMode as a created_at filter", () => {
    const timeWindow = {
      startDate: "2025-05-18T13:37:41.000Z",
      endDate: "2026-05-18T18:29:59.000Z",
    };
    renderWithSource("task", { sourceTimeWindow: timeWindow });

    expect(capturedProps.tracing).not.toBeNull();
    const createdAt = (capturedProps.tracing.localFilters || []).find(
      (f) => f.column_id === "created_at",
    );
    // Without this filter the backend defaults to a 30-day lookback and the
    // drawer previews empty for tasks whose data is older than that.
    expect(createdAt?.filter_config?.filter_value).toEqual([
      timeWindow.startDate,
      timeWindow.endDate,
    ]);
  });
});
