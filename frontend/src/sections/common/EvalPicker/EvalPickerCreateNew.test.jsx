import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "src/utils/test-utils";
import { screen, fireEvent, act } from "@testing-library/react";

import EvalPickerProvider from "./context/EvalPickerProvider";
import EvalPickerCreateNew from "./EvalPickerCreateNew";

const { capturedProps, enqueueSnackbarSpy } = vi.hoisted(() => ({
  capturedProps: { simulation: null, tracing: null, dataset: null },
  enqueueSnackbarSpy: vi.fn(),
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
  useDeploymentMode: () => ({ isOSS: false, isCloud: true }),
}));

vi.mock("notistack", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useSnackbar: () => ({ enqueueSnackbar: enqueueSnackbarSpy }),
  };
});

const renderWithSource = (
  source,
  { onSave = () => {}, ...providerProps } = {},
) =>
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
      <EvalPickerCreateNew onBack={() => {}} onSave={onSave} />
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

  it("enables exact/freeSolo attribute mapping on the create-task route", () => {
    renderWithSource("task", {
      sourceId: "00000000-0000-4000-8000-000000000901",
      sourceRowType: "spans",
    });

    expect(capturedProps.tracing).not.toBeNull();
    expect(capturedProps.tracing.initialProjectId).toBe(
      "00000000-0000-4000-8000-000000000901",
    );
    expect(capturedProps.tracing.initialRowType).toBe("spans");
    expect(capturedProps.tracing.allowCustomFieldPath).toBe(true);
  });
});

// The mapping step normally reports at least one mapped input, but an eval
// stored with an empty mapping reads back as a result column and is never
// graded, so with requireInputs the save must refuse one outright.
describe("EvalPickerCreateNew — requireInputs", () => {
  beforeEach(() => {
    capturedProps.simulation = null;
    enqueueSnackbarSpy.mockClear();
  });

  const NO_INPUTS_MESSAGE =
    "This evaluation has no inputs to map, so it can't run in an environment.";

  it("blocks saving a new single eval with no inputs when requireInputs is set", async () => {
    const onSave = vi.fn();
    renderWithSource("simulation", { onSave, requireInputs: true });

    // Switch to the Code tab: its default template code is non-empty, so
    // `validate()` needs nothing else to pass for this eval type.
    fireEvent.click(screen.getByRole("tab", { name: "Code" }));
    fireEvent.change(
      screen.getByPlaceholderText("e.g. hallucination_detector"),
      { target: { value: "my_code_eval" } },
    );

    // Simulate the (mocked) SimulationTestMode reporting "ready" with an
    // empty mapping — a state the mapping step should never report; the
    // save must still refuse it.
    await act(async () => {
      capturedProps.simulation.onReadyChange(true, {});
    });

    const saveBtn = screen.getByRole("button", {
      name: "Save & Add Evaluation",
    });
    await act(async () => {
      fireEvent.click(saveBtn);
    });

    expect(onSave).not.toHaveBeenCalled();
    expect(enqueueSnackbarSpy).toHaveBeenCalledWith(
      NO_INPUTS_MESSAGE,
      expect.objectContaining({ variant: "error" }),
    );
  });

  it("saves normally when requireInputs is absent, even with an empty mapping", async () => {
    const onSave = vi.fn();
    renderWithSource("simulation", { onSave });

    fireEvent.click(screen.getByRole("tab", { name: "Code" }));
    fireEvent.change(
      screen.getByPlaceholderText("e.g. hallucination_detector"),
      { target: { value: "my_code_eval" } },
    );

    await act(async () => {
      capturedProps.simulation.onReadyChange(true, {});
    });

    const saveBtn = screen.getByRole("button", {
      name: "Save & Add Evaluation",
    });
    await act(async () => {
      fireEvent.click(saveBtn);
    });

    expect(enqueueSnackbarSpy).not.toHaveBeenCalledWith(
      NO_INPUTS_MESSAGE,
      expect.anything(),
    );
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ mapping: {} }),
    );
  });
});
