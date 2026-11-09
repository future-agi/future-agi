/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import EvalDetailPage from "./EvalDetailPage";

const state = vi.hoisted(() => ({
  detail: null,
  composite: null,
  versions: { versions: [] },
  selectedVersion: null,
  updateComposite: { mutateAsync: vi.fn(), isPending: false },
  idleMutation: { mutateAsync: vi.fn(), isPending: false },
  snackbar: vi.fn(),
}));

// Every data hook is local; reject unexpected requests even in the red control.
vi.mock("src/utils/axios", () => {
  const unexpectedRequest = () => {
    throw new Error("Unexpected request in composite version regression test");
  };
  return {
    default: {
      get: unexpectedRequest,
      post: unexpectedRequest,
      patch: unexpectedRequest,
      delete: unexpectedRequest,
    },
    endpoints: {},
  };
});
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureLocked: () => ({ locked: false, isLoading: false }),
}));
vi.mock("src/hooks/useErrorLocalization", () => ({
  useErrorLocalizationAvailable: () => false,
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: state.snackbar }),
}));
vi.mock("../hooks/useEvalDetail", () => ({
  useEvalDetail: () => ({ data: state.detail, isLoading: false }),
  useUpdateEval: () => state.idleMutation,
  useDuplicateEval: () => state.idleMutation,
}));
vi.mock("../hooks/useEvalVersions", () => ({
  useEvalVersions: () => ({ data: state.versions }),
  useCreateEvalVersion: () => state.idleMutation,
}));
vi.mock("../hooks/useCompositeEval", () => ({
  useCompositeDetail: () => ({ data: state.composite }),
  useUpdateCompositeEval: () => state.updateComposite,
}));
vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  ),
}));
vi.mock("./CompositeDetailPanel", () => ({
  default: ({ name, onNameChange, disabled }) => (
    <input
      aria-label="Composite name"
      value={name}
      onChange={(event) => onNameChange(event.target.value)}
      disabled={disabled}
    />
  ),
}));
vi.mock("./TestPlayground", () => {
  const Playground = React.forwardRef(({ onVersionSelect }, ref) => (
    <button
      ref={ref}
      type="button"
      onClick={() => onVersionSelect(state.selectedVersion)}
    >
      Load selected version
    </button>
  ));
  Playground.displayName = "VersionSelectionMock";
  return { default: Playground };
});
vi.mock("./InstructionEditor", () => ({ default: () => null }));
vi.mock("./LLMPromptEditor", () => ({ default: () => null }));
vi.mock("./OutputTypeConfig", () => ({ default: () => null }));
vi.mock("./FewShotExamples", () => ({ default: () => null }));
vi.mock("./CodeEvalEditor", () => ({ default: () => null }));
vi.mock("./EvalFeedbackTab", () => ({ default: () => null }));
vi.mock("./EvalGroundTruthTab", () => ({ default: () => null }));
vi.mock("./EvalUsageTab", () => ({ default: () => null }));
vi.mock("./BulkDeleteDialog", () => ({ default: () => null }));
vi.mock("./VersionBadge", () => ({ default: () => null }));
vi.mock("../types/TypedEvalDetail", () => ({ default: () => null }));
vi.mock("../alignment/AgreementTab", () => ({ default: () => null }));
vi.mock("./ModelSelector", () => ({
  FAGI_MODEL_VALUES: new Set(["turing_large"]),
}));

const olderConfigs = {
  "code-child": { params: { min_words: 0, max_words: 9 } },
  "judge-child": {
    run_config: {
      model: "older-judge",
      pass_threshold: 0,
      check_internet: false,
      tools: [],
      data_injection: { variables_only: false, full_row: true },
    },
    custom_options: { labels: ["helpful", "clear"] },
  },
};
const currentConfigs = {
  "code-child": { params: { min_words: 20, max_words: 50 } },
  "judge-child": {
    run_config: { model: "current-judge", pass_threshold: 0.9 },
  },
};
const olderChildren = [
  {
    child_id: "code-child",
    child_name: "Code child",
    order: 0,
    weight: 2,
    config: olderConfigs["code-child"],
  },
  {
    child_id: "judge-child",
    child_name: "Judge child",
    order: 1,
    weight: 3,
    config: olderConfigs["judge-child"],
  },
];

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/dashboard/evaluations/composite-1");
  state.detail = {
    id: "composite-1",
    name: "Composite evaluation",
    owner: "user",
    eval_type: "llm",
    template_type: "composite",
    config: {},
  };
  state.composite = {
    name: "Composite evaluation",
    description: "Current description",
    aggregation_enabled: true,
    aggregation_function: "weighted_avg",
    composite_child_axis: "quality",
    children: olderChildren.map((child) => ({
      ...child,
      config: currentConfigs[child.child_id],
    })),
  };
  const currentVersion = {
    id: "version-2",
    version_number: 2,
    is_default: true,
    criteria: state.composite.description,
    config_snapshot: state.composite,
  };
  state.versions = { versions: [currentVersion] };
  state.selectedVersion = null;
  state.updateComposite.mutateAsync.mockResolvedValue({ version_number: 3 });
});
afterEach(() => {
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
  window.history.replaceState({}, "", "/");
});

const finishPopulation = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(150);
  });

const saveNameChange = async () => {
  fireEvent.change(screen.getByLabelText("Composite name"), {
    target: { value: "Edited composite" },
  });
  const save = screen.getByRole("button", { name: "Save Changes" });
  expect(save).toBeEnabled();
  await act(async () => {
    fireEvent.click(save);
  });
  expect(state.updateComposite.mutateAsync).toHaveBeenCalledTimes(1);
  expect(state.idleMutation.mutateAsync).not.toHaveBeenCalled();
  return state.updateComposite.mutateAsync.mock.calls[0][0];
};

describe("composite version save preserves child configs", () => {
  it.each(["config_snapshot", "configSnapshot"])(
    "saves the older version's configs, not the current configs (%s)",
    async (snapshotKey) => {
      const snapshot = {
        aggregation_enabled: true,
        aggregation_function: "weighted_avg",
        composite_child_axis: "accuracy",
        children: olderChildren,
      };
      const originalSnapshot = JSON.stringify(snapshot);
      state.selectedVersion = {
        id: "version-1",
        version_number: 1,
        is_default: false,
        criteria: "Older description",
        [snapshotKey]: snapshot,
      };
      state.versions.versions.push(state.selectedVersion);
      render(<EvalDetailPage />);
      await finishPopulation();
      fireEvent.click(
        screen.getByRole("button", { name: "Load selected version" }),
      );
      await finishPopulation();
      expect(new URLSearchParams(window.location.search).get("v")).toBe("1");

      expect(await saveNameChange()).toEqual({
        name: "Edited composite",
        description: "Older description",
        aggregation_enabled: true,
        aggregation_function: "weighted_avg",
        composite_child_axis: "accuracy",
        child_template_ids: ["code-child", "judge-child"],
        child_configs: olderConfigs,
        child_weights: { "code-child": 2, "judge-child": 3 },
      });
      expect(JSON.stringify(snapshot)).toBe(originalSnapshot);
    },
  );

  it("leaves the current-version save payload unchanged", async () => {
    render(<EvalDetailPage />);
    await finishPopulation();
    expect(await saveNameChange()).toEqual({
      name: "Edited composite",
      description: "Current description",
      aggregation_enabled: true,
      aggregation_function: "weighted_avg",
      composite_child_axis: "quality",
      child_template_ids: ["code-child", "judge-child"],
      child_configs: currentConfigs,
      child_weights: { "code-child": 2, "judge-child": 3 },
    });
  });

  it("retains config-less legacy children without inventing overrides", async () => {
    state.selectedVersion = {
      id: "version-1",
      version_number: 1,
      is_default: false,
      config_snapshot: {
        children: [
          { child_id: "missing", child_name: "Missing config" },
          { child_id: "empty", child_name: "Empty config", config: {} },
          { child_id: "null", child_name: "Null config", config: null },
        ],
      },
    };
    render(<EvalDetailPage />);
    await finishPopulation();
    fireEvent.click(
      screen.getByRole("button", { name: "Load selected version" }),
    );
    await finishPopulation();
    expect(await saveNameChange()).toMatchObject({
      child_template_ids: ["missing", "empty", "null"],
      child_configs: {},
      child_weights: { missing: 1, empty: 1, null: 1 },
    });
  });
});
