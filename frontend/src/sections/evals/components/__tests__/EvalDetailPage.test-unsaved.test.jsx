/* eslint-disable react/prop-types */
import React, { forwardRef, useEffect, useImperativeHandle } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import EvalDetailPage from "../EvalDetailPage";

// Test on the evaluation page runs what is on screen and saves nothing:
// experiments and their New experiment card keep the saved judge.

const SAVED_PROMPT = "Is {{output}} polite?";
const EDITED_PROMPT = "Is {{output}} kind?";

const mocks = vi.hoisted(() => ({
  data: null,
  versions: { versions: [] },
  composite: undefined,
  update: { mutateAsync: vi.fn(), isPending: false },
  create: { mutateAsync: vi.fn(), isPending: false },
  updateComposite: { mutateAsync: vi.fn(), isPending: false },
  idle: { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false },
  runTest: vi.fn(),
  notify: vi.fn(),
  request: vi.fn(async () => ({ data: {} })),
}));

// Catch-all: any request the page or a child makes answers with empty data.
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: {
    get: mocks.request,
    post: mocks.request,
    put: mocks.request,
    patch: mocks.request,
    delete: mocks.request,
  },
}));
vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: mocks.notify }),
}));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureLocked: () => ({ locked: false, isLoading: false }),
  useFeatureDenied: () => false,
}));
vi.mock("src/hooks/useErrorLocalization", async (importOriginal) => ({
  ...(await importOriginal()),
  useErrorLocalizationAvailable: () => false,
}));
vi.mock("../../hooks/useEvalDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useEvalDetail: () => ({ data: mocks.data, isLoading: false }),
  useUpdateEval: () => mocks.update,
  useDuplicateEval: () => mocks.idle,
}));
vi.mock("../../hooks/useEvalVersions", async (importOriginal) => ({
  ...(await importOriginal()),
  useEvalVersions: () => ({ data: mocks.versions }),
  useCreateEvalVersion: () => mocks.create,
}));
vi.mock("../../hooks/useCompositeEval", async (importOriginal) => ({
  ...(await importOriginal()),
  useCompositeDetail: () => ({ data: mocks.composite }),
  useUpdateCompositeEval: () => mocks.updateComposite,
}));
vi.mock("../../hooks/useGroundTruth", async (importOriginal) => ({
  ...(await importOriginal()),
  useGroundTruthList: () => ({ data: undefined }),
}));
vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("src/components/custom-model-dropdown/KeysDrawer", () => ({
  default: () => null,
}));
vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  ),
}));
// The playground reports ready, records what each Test hands it and reports
// the run finished, as the real one does after its request.
vi.mock("../TestPlayground", () => ({
  default: forwardRef(function TestPlaygroundDouble(props, ref) {
    const { onReadyChange, onTestResult } = props;
    useEffect(() => {
      onReadyChange(true);
    }, [onReadyChange]);
    useImperativeHandle(ref, () => ({
      runTest: (templateId) => {
        mocks.runTest(templateId, props);
        onTestResult(true, { output: "Passed" });
      },
    }));
    return null;
  }),
}));
vi.mock("../LLMPromptEditor", () => ({
  default: ({ messages, onMessagesChange, model, onModelChange }) => (
    <>
      <textarea
        aria-label="Instructions"
        value={messages[0]?.content || ""}
        onChange={(e) =>
          onMessagesChange([
            { ...messages[0], content: e.target.value },
            ...messages.slice(1),
          ])
        }
      />
      <select
        aria-label="Judge model"
        value={model}
        onChange={(e) => onModelChange(e.target.value)}
      >
        <option value="gpt-4o-mini">GPT-4o mini</option>
        <option value="claude-sonnet-4-5">Claude Sonnet 4.5</option>
      </select>
    </>
  ),
}));
vi.mock("../CodeEvalEditor", () => ({
  default: ({ code, setCode }) => (
    <textarea
      aria-label="Code"
      value={code}
      onChange={(e) => setCode(e.target.value)}
    />
  ),
}));
vi.mock("../CompositeDetailPanel", () => ({
  default: ({ children, childWeights, onChildWeightsChange }) => (
    <button
      type="button"
      onClick={() =>
        onChildWeightsChange({ ...childWeights, [children[0].child_id]: 5 })
      }
    >
      Raise first weight
    </button>
  ),
}));
vi.mock("../InstructionEditor", () => ({ default: () => null }));
vi.mock("../OutputTypeConfig", () => ({ default: () => null }));
vi.mock("../FewShotExamples", () => ({ default: () => null }));
vi.mock("../EvalFeedbackTab", () => ({ default: () => null }));
vi.mock("../EvalGroundTruthTab", () => ({ default: () => null }));
vi.mock("../EvalUsageTab", () => ({ default: () => null }));
vi.mock("../VersionBadge", () => ({ default: () => null }));
vi.mock("../BulkDeleteDialog", () => ({ default: () => null }));

const llmEval = () => ({
  id: "polite-judge",
  name: "polite_judge",
  owner: "user",
  eval_type: "llm",
  instructions: SAVED_PROMPT,
  output_type: "pass_fail",
  pass_threshold: 0.5,
  config: {
    model: "gpt-4o-mini",
    rule_prompt: SAVED_PROMPT,
    messages: [{ role: "system", content: SAVED_PROMPT }],
    required_keys: ["output"],
    template_format: "mustache",
  },
});

const compositeEval = () => ({
  id: "composite-1",
  name: "composite_judge",
  owner: "user",
  eval_type: "llm",
  template_type: "composite",
  pass_threshold: 0.7,
  config: {},
});

const compositeDetail = (pinned = null) => ({
  name: "composite_judge",
  description: "",
  aggregation_enabled: true,
  aggregation_function: "weighted_avg",
  composite_child_axis: "",
  children: [
    {
      child_id: "child-a",
      child_name: "Child A",
      order: 0,
      weight: 1,
      config: {},
      pinned_version_id: pinned,
    },
    {
      child_id: "child-b",
      child_name: "Child B",
      order: 1,
      weight: 2,
      config: {},
    },
  ],
});

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/");
  mocks.data = llmEval();
  mocks.versions = { versions: [] };
  mocks.composite = undefined;
  mocks.update.mutateAsync.mockResolvedValue({});
  mocks.create.mutateAsync.mockResolvedValue({ version_number: 2 });
  mocks.updateComposite.mutateAsync.mockResolvedValue({ version_number: 3 });
});
afterEach(() => {
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
});

// The page ignores editor events for 100 ms while it fills the form.
const finishPopulation = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(150);
  });

// Clicks, then lets the handler's awaited mutations settle.
const click = async (name) => {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10);
  });
};

const lastTest = () => mocks.runTest.mock.calls.at(-1)[1];

describe("Test on the evaluation page runs unsaved changes without saving them", () => {
  it("runs a newly picked judge model and saves nothing", async () => {
    render(<EvalDetailPage />);
    await finishPopulation();
    fireEvent.change(screen.getByLabelText("Judge model"), {
      target: { value: "claude-sonnet-4-5" },
    });

    await click("Test Evaluation");

    expect(mocks.runTest).toHaveBeenCalledTimes(1);
    expect(lastTest().model).toBe("claude-sonnet-4-5");
    expect(lastTest().unsavedDefinition).toEqual(
      expect.objectContaining({
        model: "claude-sonnet-4-5",
        instructions: SAVED_PROMPT,
      }),
    );
    expect(mocks.update.mutateAsync).not.toHaveBeenCalled();
    expect(mocks.create.mutateAsync).not.toHaveBeenCalled();
    expect(mocks.request).not.toHaveBeenCalledWith(
      expect.stringContaining("/update/"),
      expect.anything(),
    );
  });

  it("runs edited instructions as they are on screen and saves nothing", async () => {
    render(<EvalDetailPage />);
    await finishPopulation();
    fireEvent.change(screen.getByLabelText("Instructions"), {
      target: { value: EDITED_PROMPT },
    });

    await click("Test Evaluation");

    expect(lastTest().unsavedDefinition).toEqual(
      expect.objectContaining({
        model: "gpt-4o-mini",
        instructions: EDITED_PROMPT,
        messages: [{ role: "system", content: EDITED_PROMPT }],
        output_type: "pass_fail",
        pass_threshold: 0.5,
        template_format: "mustache",
      }),
    );
    expect(mocks.update.mutateAsync).not.toHaveBeenCalled();
  });

  it("says Testing unsaved changes only while there are unsaved changes", async () => {
    render(<EvalDetailPage />);
    await finishPopulation();
    expect(
      screen.queryByText("Testing unsaved changes"),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Judge model"), {
      target: { value: "claude-sonnet-4-5" },
    });
    expect(screen.getByText("Testing unsaved changes")).toBeInTheDocument();

    await click("Save Version");
    expect(
      screen.queryByText("Testing unsaved changes"),
    ).not.toBeInTheDocument();
  });

  // MUI puts a Tooltip's text title on its child as the aria-label.
  it("says on hover that Test runs the unsaved edits without saving them", async () => {
    render(<EvalDetailPage />);
    await finishPopulation();
    fireEvent.change(screen.getByLabelText("Judge model"), {
      target: { value: "claude-sonnet-4-5" },
    });

    expect(screen.getByText("Testing unsaved changes")).toHaveAttribute(
      "aria-label",
      "Test runs your unsaved edits without saving them. Save Version keeps your edits for experiments and runs.",
    );
  });

  it("says the same on hover for code", async () => {
    mocks.data = {
      id: "length-check",
      name: "length_check",
      owner: "user",
      eval_type: "code",
      output_type: "pass_fail",
      pass_threshold: 0.5,
      config: {
        code: "def evaluate(output, **kwargs):\n    return True\n",
        language: "python",
        required_keys: ["output"],
      },
    };
    render(<EvalDetailPage />);
    await finishPopulation();
    fireEvent.change(screen.getByLabelText("Code"), {
      target: {
        value: "def evaluate(output, **kwargs):\n    return False\n",
      },
    });

    expect(screen.getByText("Testing unsaved changes")).toHaveAttribute(
      "aria-label",
      "Test runs your unsaved edits without saving them. Save Version keeps your edits for experiments and runs.",
    );
  });

  it("keeps the composite's words on hover", async () => {
    mocks.data = compositeEval();
    mocks.composite = compositeDetail();
    render(<EvalDetailPage />);
    await finishPopulation();

    await click("Raise first weight");

    expect(screen.getByText("Testing unsaved changes")).toHaveAttribute(
      "aria-label",
      "Test runs what is on screen without saving it. Save Changes keeps it for experiments and runs.",
    );
  });

  it("still saves the picked judge on Save Version", async () => {
    render(<EvalDetailPage />);
    await finishPopulation();
    fireEvent.change(screen.getByLabelText("Judge model"), {
      target: { value: "claude-sonnet-4-5" },
    });

    await click("Save Version");

    expect(mocks.update.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ model: "claude-sonnet-4-5" }),
    );
    expect(mocks.create.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ model: "claude-sonnet-4-5" }),
    );
    expect(mocks.runTest).not.toHaveBeenCalled();
  });

  it("runs edited code and saves nothing", async () => {
    mocks.data = {
      id: "length-check",
      name: "length_check",
      owner: "user",
      eval_type: "code",
      output_type: "pass_fail",
      pass_threshold: 0.5,
      config: {
        code: "def evaluate(output, **kwargs):\n    return True\n",
        language: "python",
        required_keys: ["output"],
      },
    };
    const edited =
      "def evaluate(output, **kwargs):\n    return len(output) > 3\n";
    render(<EvalDetailPage />);
    await finishPopulation();
    fireEvent.change(screen.getByLabelText("Code"), {
      target: { value: edited },
    });

    await click("Test Evaluation");

    const { unsavedDefinition } = lastTest();
    expect(unsavedDefinition).toEqual(
      expect.objectContaining({ code: edited, code_language: "python" }),
    );
    expect(unsavedDefinition).not.toHaveProperty("model");
    expect(unsavedDefinition).not.toHaveProperty("instructions");
    expect(mocks.update.mutateAsync).not.toHaveBeenCalled();
  });

  it("runs a composite's unsaved weights through the unsaved request and saves nothing", async () => {
    mocks.data = compositeEval();
    mocks.composite = compositeDetail();
    render(<EvalDetailPage />);
    await finishPopulation();

    await click("Run Composite");
    // Nothing edited yet: the saved composite runs.
    expect(lastTest().compositeAdhocConfig).toBeNull();

    await click("Raise first weight");
    expect(screen.getByText("Testing unsaved changes")).toBeInTheDocument();
    await click("Run Composite");

    expect(lastTest().compositeAdhocConfig).toEqual(
      expect.objectContaining({
        child_template_ids: ["child-a", "child-b"],
        child_weights: { "child-a": 5, "child-b": 2 },
        aggregation_enabled: true,
        aggregation_function: "weighted_avg",
        pass_threshold: 0.7,
      }),
    );
    expect(lastTest().unsavedDefinition).toBeNull();
    expect(mocks.updateComposite.mutateAsync).not.toHaveBeenCalled();
  });

  it("runs a composite with a pinned child as saved and says so", async () => {
    mocks.data = compositeEval();
    mocks.composite = compositeDetail("version-7");
    render(<EvalDetailPage />);
    await finishPopulation();

    await click("Raise first weight");
    expect(
      screen.queryByText("Testing unsaved changes"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    await click("Run Composite");

    expect(lastTest().compositeAdhocConfig).toBeNull();
    expect(mocks.updateComposite.mutateAsync).not.toHaveBeenCalled();
  });
});
