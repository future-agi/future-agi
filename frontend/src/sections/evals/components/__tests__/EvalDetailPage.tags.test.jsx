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

// RED_TEAMING is in the curated EVAL_TAGS list; BILLING_CHECKS is not, so it
// only renders (as "Billing Checks") when it is in the page's tag state.
const EVAL_TAGS_ON_TEMPLATE = ["RED_TEAMING", "BILLING_CHECKS"];

const version = (id, number, isDefault = false) => ({
  id,
  version_number: number,
  is_default: isDefault,
  model: "turing_large",
  criteria: "Check {{input}}",
  config_snapshot: { model: "turing_large", output: "Pass/Fail" },
});

const chip = (label) => screen.getByText(label).closest(".MuiChip-root");

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/");
  mocks.versions = {
    versions: [version("v2", 2, true), version("v1", 1)],
  };
  mocks.data = {
    id: "eval",
    name: "Tagged eval",
    owner: "user",
    eval_type: "llm",
    instructions: "Check {{input}}",
    output_type: "pass_fail",
    pass_threshold: 0.5,
    tags: EVAL_TAGS_ON_TEMPLATE,
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

describe("Eval detail tags", () => {
  it("shows the eval's tags when the page opens on its saved default version, and keeps them on save", async () => {
    render(<EvalDetailPage />);
    expect(screen.getByText("Billing Checks")).toBeInTheDocument();
    expect(chip("Red Teaming")).toHaveClass("MuiChip-filled");

    // Initial population deliberately suppresses dirty events for 100 ms.
    await new Promise((resolve) => setTimeout(resolve, 120));
    await userEvent.type(screen.getByLabelText("Instructions"), " updated");
    await userEvent.click(screen.getByRole("button", { name: "Save Version" }));
    await waitFor(() => expect(mocks.update).toHaveBeenCalled());
    expect(mocks.update).toHaveBeenCalledWith(
      expect.objectContaining({ tags: EVAL_TAGS_ON_TEMPLATE }),
    );
  });

  it("keeps the eval's tags when switching to another saved version", async () => {
    render(<EvalDetailPage />);
    await userEvent.click(
      screen.getByRole("button", { name: "Load old version" }),
    );
    expect(screen.getByText("Billing Checks")).toBeInTheDocument();
    expect(chip("Red Teaming")).toHaveClass("MuiChip-filled");
  });

  it("shows only unselected tags for an eval without tags", () => {
    delete mocks.data.tags;
    render(<EvalDetailPage />);
    expect(screen.queryByText("Billing Checks")).not.toBeInTheDocument();
    expect(chip("Red Teaming")).toHaveClass("MuiChip-outlined");
  });
});
