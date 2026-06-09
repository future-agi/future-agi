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

vi.mock("src/sections/tasks/components/TaskLivePreview", () => ({
  buildApiFilterArray: () => [],
}));

vi.mock("src/sections/evals/hooks/useCreateEval", () => ({
  useCreateEval: () => ({ mutateAsync: vi.fn(async () => ({ id: "draft-1" })) }),
}));

vi.mock("src/sections/evals/hooks/useEvalDetail", () => ({
  useUpdateEval: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(async () => ({})) }),
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

vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

const renderWithSource = (source) =>
  render(
    <EvalPickerProvider
      source={source}
      sourceId="sim-1"
      sourceColumns={[]}
      existingEvals={[]}
      onEvalAdded={() => {}}
      onClose={() => {}}
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
