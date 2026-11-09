/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  screen,
  within,
} from "@testing-library/react";
import { render } from "src/utils/test-utils";
import EvalCreatePage from "./EvalCreatePage";
import EvalDetailPage from "./EvalDetailPage";

const state = vi.hoisted(() => ({
  prototype: false,
  detail: null,
  versions: { versions: [] },
  update: {
    mutate: vi.fn(),
    mutateAsync: vi.fn(async () => ({})),
    isPending: false,
  },
  create: {
    mutateAsync: vi.fn(async () => ({ id: "draft-1" })),
    isPending: false,
  },
  compositeCreate: {
    mutateAsync: vi.fn(async () => ({ id: "composite-1" })),
    isPending: false,
  },
  createVersion: { mutateAsync: vi.fn(async () => ({})), isPending: false },
  composite: { data: null },
  compositeUpdate: { mutateAsync: vi.fn(), isPending: false },
  snackbar: vi.fn(),
}));
// Create uses axios directly for its initial draft, not useCreateEval.
// Keep this test seam local so even the red control cannot make HTTP calls.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = /** @type {Record<string, unknown>} */ (
    await importOriginal()
  );
  return {
    ...actual,
    default: {
      post: vi.fn(async () => ({ data: { result: { id: "draft-1" } } })),
      get: vi.fn(async () => {
        throw new Error("Unexpected GET in eval page unit test");
      }),
    },
  };
});

vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => state.prototype,
}));
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
  useUpdateEval: () => state.update,
  useDuplicateEval: () => state.create,
}));
vi.mock("../hooks/useCreateEval", () => ({
  useCreateEval: () => state.create,
}));
vi.mock("../hooks/useEvalVersions", () => ({
  useEvalVersions: () => ({ data: state.versions }),
  useCreateEvalVersion: () => state.createVersion,
}));
vi.mock("../hooks/useCompositeEval", () => ({
  useCompositeDetail: () => state.composite,
  useUpdateCompositeEval: () => state.compositeUpdate,
  useCreateCompositeEval: () => state.compositeCreate,
}));
vi.mock("../hooks/useCompositeChildrenKeys", () => ({
  useCompositeChildrenUnionKeys: () => [],
}));
vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  ),
}));
vi.mock("./InstructionEditor", () => ({
  default: ({ value, onChange }) => (
    <textarea
      aria-label="Instructions"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));
vi.mock("./LLMPromptEditor", () => ({
  default: ({ messages, onMessagesChange }) => (
    <textarea
      aria-label="Instructions"
      value={messages[0]?.content || ""}
      onChange={(e) =>
        onMessagesChange([{ role: "system", content: e.target.value }])
      }
    />
  ),
}));
vi.mock("./ModelSelector", () => ({
  default: () => null,
  FAGI_MODEL_VALUES: new Set(["turing_large"]),
}));
vi.mock("./TestPlayground", () => {
  const Mock = React.forwardRef(() => null);
  Mock.displayName = "TestPlaygroundMock";
  return { default: Mock };
});
vi.mock("./FewShotExamples", () => ({ default: () => null }));
vi.mock("./CodeEvalEditor", () => ({
  default: () => null,
  PYTHON_CODE_TEMPLATE: "",
}));
vi.mock("./CompositeDetailPanel", () => ({
  default: ({ name, onNameChange, onChildrenChange, disabled }) => (
    <>
      <input
        aria-label="Composite name"
        value={name}
        onChange={(e) => onNameChange(e.target.value)}
        disabled={disabled}
      />
      <button
        type="button"
        disabled={disabled}
        onClick={() =>
          onChildrenChange([
            { child_id: "child-1", child_name: "Child", weight: 1 },
          ])
        }
      >
        Add child evaluation
      </button>
    </>
  ),
}));
vi.mock("./EvalFeedbackTab", () => ({ default: () => null }));
vi.mock("./EvalGroundTruthTab", () => ({ default: () => null }));
vi.mock("./EvalUsageTab", () => ({ default: () => null }));
vi.mock("./BulkDeleteDialog", () => ({ default: () => null }));
vi.mock("./VersionBadge", () => ({ default: () => null }));
vi.mock("../types/TypedEvalDetail", () => ({ default: () => null }));
vi.mock("../types/TypedEvalEditor", () => ({ default: () => null }));
vi.mock("../alignment/AgreementTab", () => ({ default: () => null }));
vi.mock("src/sections/projects/MonitorsView/UnsavedChangesDialog", () => ({
  default: () => null,
}));

beforeEach(() => {
  vi.useFakeTimers();
  state.prototype = false;
  state.detail = {
    id: "template-1",
    name: "polite_eval",
    owner: "user",
    eval_type: "agent",
    output_type: "percentage",
    instructions: "Judge {{output}}",
    config: { allow_na: true, choice_scores: { Good: 1, Bad: 0 } },
    choice_scores: { Good: 1, Bad: 0 },
  };
  state.update.isPending = false;
  state.create.isPending = false;
  state.compositeCreate.isPending = false;
  state.update.mutate.mockClear();
  state.create.mutateAsync.mockClear();
  state.compositeCreate.mutateAsync.mockClear();
  state.createVersion.isPending = false;
  state.compositeUpdate.isPending = false;
  state.composite.data = null;
  state.update.mutateAsync.mockClear();
  state.createVersion.mutateAsync.mockClear();
  state.compositeUpdate.mutateAsync.mockClear();
});
afterEach(() => {
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
});

// Flush the editor's initial populate guard, not a test/network wait.
const finishPopulation = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(150);
  });

describe("eval pages restore dev Choices", () => {
  it("offers Choices and score mappings on Create", async () => {
    render(<EvalCreatePage />);
    await finishPopulation();
    expect(screen.getByRole("radio", { name: "Choices" })).toBeEnabled();
    fireEvent.click(screen.getByRole("radio", { name: "Scoring" }));
    expect(screen.getByPlaceholderText("Choice name")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Choices" }));
    expect(screen.getByRole("radio", { name: "Choices" })).toBeChecked();
  });

  it("keeps the detail category locked but restores its radio and editable score mappings", async () => {
    render(<EvalDetailPage />);
    await finishPopulation();
    expect(screen.getByRole("radio", { name: "Choices" })).toBeDisabled();
    expect(screen.getByDisplayValue("Good")).toBeEnabled();
    expect(screen.getByDisplayValue("Bad")).toBeEnabled();
  });
});

describe.each([
  { action: "composite create", composite: true },
  { action: "publish", composite: false },
])("create pending state ($action)", ({ composite }) => {
  it("disables Save Evaluation and shows Saving... while the mutation is pending", async () => {
    const { rerender } = render(<EvalCreatePage />);
    await finishPopulation();
    // CustomTooltip remounts the button when validation or pending state changes.
    const getSaveButton = () =>
      screen.getByRole("button", { name: /^(Save Evaluation|Saving\.\.\.)$/ });
    expect(getSaveButton()).toBeDisabled();
    if (composite) {
      fireEvent.click(screen.getByRole("tab", { name: "Composite" }));
      fireEvent.change(screen.getByLabelText("Composite name"), {
        target: { value: "pending_composite" },
      });
      expect(getSaveButton()).toBeDisabled();
      fireEvent.click(
        screen.getByRole("button", { name: "Add child evaluation" }),
      );
    } else {
      fireEvent.change(
        screen.getByPlaceholderText("Eg: Hallucination detector"),
        {
          target: { value: "pending_publish" },
        },
      );
      expect(getSaveButton()).toBeDisabled();
      fireEvent.change(screen.getByLabelText("Instructions"), {
        target: { value: "Judge {{output}}" },
      });
    }
    // Valid input is enabled first, so validation cannot mask the pending bug.
    expect(getSaveButton()).toBeEnabled();
    const mutation = composite ? state.compositeCreate : state.update;
    expect(mutation).not.toHaveProperty("isLoading");
    mutation.isPending = true;
    rerender(<EvalCreatePage />);
    expect(getSaveButton()).toBeDisabled();
    expect(getSaveButton()).toHaveTextContent(/^Saving\.\.\.$/);
    fireEvent.click(getSaveButton());
    expect(state.compositeCreate.mutateAsync).not.toHaveBeenCalled();
    expect(state.update.mutateAsync).not.toHaveBeenCalled();
    expect(state.create.mutateAsync).not.toHaveBeenCalled();

    mutation.isPending = false;
    rerender(<EvalCreatePage />);
    expect(getSaveButton()).toBeEnabled();
    expect(getSaveButton()).toHaveTextContent(/^Save Evaluation$/);
    await act(async () => {
      fireEvent.click(getSaveButton());
    });
    expect(mutation.mutateAsync).toHaveBeenCalledTimes(1);
    expect(mutation.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining(
        composite
          ? { name: "pending_composite", child_template_ids: ["child-1"] }
          : { name: "pending_publish", publish: true },
      ),
    );
    expect(state.create.mutateAsync).not.toHaveBeenCalled();
  });
});

describe.each([
  { label: "Save Version", composite: false },
  { label: "Save Changes", composite: true },
])("detail pending state ($label)", ({ label, composite }) => {
  it.each(["updateEval", "createVersion", "updateComposite"])(
    "disables saving and shows progress while %s is pending",
    async (pendingMutation) => {
      if (composite) {
        state.detail = { ...state.detail, template_type: "composite" };
        state.composite.data = {
          name: "composite_eval",
          aggregation_enabled: true,
          aggregation_function: "weighted_avg",
          children: [{ child_id: "child-1", child_name: "Child", weight: 1 }],
        };
      }
      const { rerender } = render(<EvalDetailPage />);
      await finishPopulation();
      fireEvent.change(
        screen.getByLabelText(composite ? "Composite name" : "Instructions"),
        {
          target: {
            value: composite
              ? "Edited composite"
              : "Judge {{output}} carefully",
          },
        },
      );
      const save = screen.getByRole("button", { name: label });
      // Prove pending is the disabling cause, not pristine/invalid input.
      expect(save).toBeEnabled();
      const mutation =
        pendingMutation === "updateEval"
          ? state.update
          : pendingMutation === "createVersion"
            ? state.createVersion
            : state.compositeUpdate;
      // Match v5: mutations expose isPending, never an isLoading alias.
      expect(mutation).not.toHaveProperty("isLoading");
      mutation.isPending = true;
      rerender(<EvalDetailPage />);
      expect(save).toBeDisabled();
      expect(save).toHaveTextContent(/^Saving\.\.\.$/);
      expect(within(save).getByRole("progressbar")).toBeInTheDocument();
      if (composite) {
        expect(screen.getByLabelText("Composite name")).toBeDisabled();
      }
      fireEvent.click(save);
      expect(state.update.mutateAsync).not.toHaveBeenCalled();
      expect(state.createVersion.mutateAsync).not.toHaveBeenCalled();
      expect(state.compositeUpdate.mutateAsync).not.toHaveBeenCalled();

      mutation.isPending = false;
      rerender(<EvalDetailPage />);
      expect(save).toBeEnabled();
      expect(save).toHaveTextContent(label);
      expect(within(save).queryByRole("progressbar")).not.toBeInTheDocument();
      if (composite) {
        expect(screen.getByLabelText("Composite name")).toBeEnabled();
      }
    },
  );
});

describe.each([false, true])(
  "detail save dev parity (prototype=%s)",
  (prototype) => {
    it.each(["llm", "agent"])(
      "saves %s without unsupported launch fields, preserving prototype N/A",
      async (evalType) => {
        state.prototype = prototype;
        state.detail.eval_type = evalType;
        render(<EvalDetailPage />);
        await finishPopulation();
        expect(Boolean(screen.queryByTestId("allow-na-switch"))).toBe(
          prototype,
        );
        fireEvent.change(screen.getByLabelText("Instructions"), {
          target: { value: "Judge {{output}} carefully" },
        });
        const save = screen.getByRole("button", { name: "Save Version" });
        expect(save).toBeEnabled();
        await act(async () => {
          fireEvent.click(save);
        });
        expect(state.update.mutateAsync).toHaveBeenCalledTimes(1);
        expect(state.createVersion.mutateAsync).toHaveBeenCalledTimes(1);
        const update = JSON.parse(
          JSON.stringify(state.update.mutateAsync.mock.calls[0][0]),
        );
        const snapshot = JSON.parse(
          JSON.stringify(
            state.createVersion.mutateAsync.mock.calls[0][0].config_snapshot,
          ),
        );
        expect(update.instructions).toBe("Judge {{output}} carefully");
        expect(snapshot.choice_scores).toEqual({ Good: 1, Bad: 0 });
        for (const payload of [update, snapshot]) {
          if (prototype) expect(payload.allow_na).toBe(true);
          else expect(payload).not.toHaveProperty("allow_na");
        }
        expect(state.detail.config.allow_na).toBe(true);
      },
    );
  },
);
