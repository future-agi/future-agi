import React, { forwardRef, useEffect, useImperativeHandle } from "react";
import PropTypes from "prop-types";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import EvalDetailPage from "../EvalDetailPage";

const mocks = vi.hoisted(() => ({
  data: null,
  versions: { versions: [] },
  update: vi.fn(),
  create: vi.fn(),
  run: vi.fn(),
  notify: vi.fn(),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("notistack", async (original) => ({
  ...(await original()),
  useSnackbar: () => ({ enqueueSnackbar: mocks.notify }),
}));
vi.mock("src/hooks/useCapabilities", async (original) => ({
  ...(await original()),
  useFeatureLocked: () => ({ locked: false, isLoading: false }),
  useFeatureDenied: () => false,
}));
vi.mock("src/hooks/useErrorLocalization", () => ({
  useErrorLocalizationAvailable: () => false,
}));
vi.mock("../../hooks/useEvalDetail", () => ({
  useEvalDetail: () => ({ data: mocks.data }),
  useUpdateEval: () => ({ mutateAsync: mocks.update }),
  useDuplicateEval: () => ({}),
}));
vi.mock("../../hooks/useEvalVersions", () => ({
  useCreateEvalVersion: () => ({ mutateAsync: mocks.create }),
  useEvalVersions: () => ({ data: mocks.versions }),
}));
vi.mock("../../hooks/useCompositeEval", () => ({
  useCompositeDetail: () => ({}),
  useUpdateCompositeEval: () => ({}),
}));
vi.mock("src/utils/axios", () => ({ default: {}, endpoints: {} }));
vi.mock("src/api/evaluations/decision-models", () => ({
  default: () => ({ data: [] }),
  decisionModelsQueryKey: ["eval-decision-models"],
}));
vi.mock("src/components/custom-model-dropdown/KeysDrawer", () => ({
  default: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/resizablePanels/ResizablePanels", () => {
  const ResizablePanelsStub = ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  );
  ResizablePanelsStub.propTypes = {
    leftPanel: PropTypes.element,
    rightPanel: PropTypes.element,
  };
  return { default: ResizablePanelsStub };
});
vi.mock("../LLMPromptEditor", () => {
  const LLMPromptEditorStub = ({ messages, onMessagesChange }) => (
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
  );
  LLMPromptEditorStub.propTypes = {
    messages: PropTypes.array.isRequired,
    onMessagesChange: PropTypes.func.isRequired,
  };
  return { default: LLMPromptEditorStub };
});
vi.mock("../InstructionEditor", () => {
  const InstructionEditorStub = ({ value, onChange }) => (
    <textarea
      aria-label="Instructions"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
  InstructionEditorStub.propTypes = {
    value: PropTypes.string,
    onChange: PropTypes.func.isRequired,
  };
  return { default: InstructionEditorStub };
});
vi.mock("../TestPlayground", () => {
  const TestPlaygroundStub = forwardRef(function TestPlaygroundStub(
    { onReadyChange, onVersionSelect },
    ref,
  ) {
    useEffect(() => {
      onReadyChange(true);
    }, [onReadyChange]);
    useImperativeHandle(ref, () => ({ runTest: mocks.run }));
    return (
      <button onClick={() => onVersionSelect(mocks.versions.versions[1])}>
        Load old version
      </button>
    );
  });
  TestPlaygroundStub.propTypes = {
    onReadyChange: PropTypes.func,
    onVersionSelect: PropTypes.func,
  };
  return { default: TestPlaygroundStub };
});
vi.mock("../FewShotExamples", () => ({ default: () => null }));
vi.mock("../CodeEvalEditor", () => ({ default: () => null }));
vi.mock("../CompositeDetailPanel", () => ({ default: () => null }));
vi.mock("../EvalFeedbackTab", () => ({ default: () => null }));
vi.mock("../EvalGroundTruthTab", () => ({ default: () => null }));
vi.mock("../EvalUsageTab", () => ({ default: () => null }));
vi.mock("../VersionBadge", () => ({ default: () => null }));
vi.mock("../BulkDeleteDialog", () => ({ default: () => null }));

const DESCRIPTION = "Flags replies that leak internal ticket IDs.";

const version = (id, number, isDefault = false) => ({
  id,
  version_number: number,
  is_default: isDefault,
  model: "turing_large",
  criteria: `Version ${number}: check {{input}}`,
  config_snapshot: { model: "turing_large", output: "Pass/Fail" },
});

const descriptionBox = () =>
  screen.getByPlaceholderText("Enter a description here");

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/");
  mocks.versions = {
    versions: [version("v2", 2, true), version("v1", 1)],
  };
  mocks.data = {
    id: "eval",
    name: "Described eval",
    owner: "user",
    eval_type: "llm",
    instructions: "Check {{input}}",
    output_type: "pass_fail",
    pass_threshold: 0.5,
    description: DESCRIPTION,
    tags: [],
    config: { model: "turing_large" },
  };
  mocks.update.mockResolvedValue({});
  mocks.create.mockResolvedValue({});
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Unexpected network request");
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

describe("Eval detail description", () => {
  it.each(["pass_fail", "percentage", "deterministic"])(
    "shows the saved description when a %s eval opens on its default version",
    (outputType) => {
      mocks.data.output_type = outputType;
      render(<EvalDetailPage />);
      // The page opened on the saved default version, not the template.
      expect(screen.getByLabelText("Instructions")).toHaveValue(
        "Version 2: check {{input}}",
      );
      expect(descriptionBox()).toHaveValue(DESCRIPTION);
    },
  );

  it("shows the saved description when the URL names a version", () => {
    window.history.replaceState({}, "", "/?v=1");
    render(<EvalDetailPage />);
    expect(screen.getByLabelText("Instructions")).toHaveValue(
      "Version 1: check {{input}}",
    );
    expect(descriptionBox()).toHaveValue(DESCRIPTION);
  });

  it("keeps the description when switching to another saved version", async () => {
    render(<EvalDetailPage />);
    await userEvent.click(
      screen.getByRole("button", { name: "Load old version" }),
    );
    expect(screen.getByLabelText("Instructions")).toHaveValue(
      "Version 1: check {{input}}",
    );
    expect(descriptionBox()).toHaveValue(DESCRIPTION);
  });

  it("does not clear the saved description when another field is saved", async () => {
    render(<EvalDetailPage />);
    // Initial population deliberately suppresses dirty events for 100 ms.
    await new Promise((resolve) => setTimeout(resolve, 120));
    await userEvent.type(screen.getByLabelText("Instructions"), " updated");
    await userEvent.click(screen.getByRole("button", { name: "Save Version" }));
    await waitFor(() => expect(mocks.update).toHaveBeenCalled());
    expect(mocks.update).toHaveBeenCalledWith(
      expect.objectContaining({ description: DESCRIPTION }),
    );
  });

  it("shows an empty box for an eval without a description", () => {
    mocks.data.description = null;
    render(<EvalDetailPage />);
    expect(descriptionBox()).toHaveValue("");
  });
});
