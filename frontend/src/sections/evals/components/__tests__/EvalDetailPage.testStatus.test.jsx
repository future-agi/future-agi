/* eslint-disable react/prop-types */
import React, { forwardRef, useEffect, useImperativeHandle } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import EvalDetailPage from "../EvalDetailPage";

// sandbox.py EXECUTOR_UNAVAILABLE_MESSAGE: what the walk's run failed with.
const EXECUTOR_UNAVAILABLE =
  "Code executor unavailable: the code-executor service could not be reached";

const mocks = vi.hoisted(() => ({
  data: null,
  // Stable references: the page re-populates its form when these change.
  versions: { versions: [] },
  outcome: null,
  notify: vi.fn(),
  request: vi.fn(async () => ({ data: {} })),
  idle: {
    mutate: vi.fn(),
    mutateAsync: vi.fn(async () => ({})),
    isPending: false,
  },
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
  useUpdateEval: () => mocks.idle,
  useDuplicateEval: () => mocks.idle,
}));
vi.mock("../../hooks/useEvalVersions", async (importOriginal) => ({
  ...(await importOriginal()),
  useEvalVersions: () => ({ data: mocks.versions }),
  useCreateEvalVersion: () => mocks.idle,
}));
vi.mock("../../hooks/useCompositeEval", async (importOriginal) => ({
  ...(await importOriginal()),
  useCompositeDetail: () => ({ data: undefined }),
  useUpdateCompositeEval: () => mocks.idle,
}));
// Rendered without a QueryClient, so the ground-truth list is mocked empty.
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
// The playground reports ready; Test Evaluation makes it report the outcome
// the test sets, as TracingTestMode does after its request.
vi.mock("../TestPlayground", () => ({
  default: forwardRef(function TestPlaygroundDouble(
    { onReadyChange, onTestResult },
    ref,
  ) {
    useEffect(() => {
      onReadyChange(true);
    }, [onReadyChange]);
    useImperativeHandle(ref, () => ({
      runTest: () => onTestResult(...mocks.outcome),
    }));
    return null;
  }),
}));
vi.mock("../CodeEvalEditor", () => ({ default: () => null }));
vi.mock("../InstructionEditor", () => ({ default: () => null }));
vi.mock("../LLMPromptEditor", () => ({ default: () => null }));
vi.mock("../FewShotExamples", () => ({ default: () => null }));
vi.mock("../CompositeDetailPanel", () => ({ default: () => null }));
vi.mock("../EvalFeedbackTab", () => ({ default: () => null }));
vi.mock("../EvalGroundTruthTab", () => ({ default: () => null }));
vi.mock("../EvalUsageTab", () => ({ default: () => null }));
vi.mock("../VersionBadge", () => ({ default: () => null }));
vi.mock("../BulkDeleteDialog", () => ({ default: () => null }));

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/");
  // The walk's eval: a system code eval, so testing skips the auto-save.
  mocks.data = {
    id: "is-json",
    name: "is_json",
    owner: "system",
    eval_type: "code",
    description: "Validates if content is proper JSON format",
    config: {
      required_keys: ["text"],
      code: "import json\n",
      language: "python",
    },
  };
  mocks.outcome = null;
});
afterEach(() => {
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
});

const advance = (ms) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

const runTest = async () => {
  render(<EvalDetailPage />);
  await advance(150);
  const button = screen.getByRole("button", { name: "Test Evaluation" });
  expect(button).toBeEnabled();
  await act(async () => {
    fireEvent.click(button);
  });
};

describe("Eval detail test status", () => {
  it("never says Test completed after a run that could not execute", async () => {
    mocks.outcome = [false, EXECUTOR_UNAVAILABLE];
    await runTest();
    expect(screen.getByText(EXECUTOR_UNAVAILABLE)).toBeInTheDocument();
    expect(screen.queryByText("Test completed")).not.toBeInTheDocument();

    // The error caption clears itself after 6 seconds; the failed run must
    // not turn into a completed one when it does.
    await advance(6000);
    expect(screen.queryByText(EXECUTOR_UNAVAILABLE)).not.toBeInTheDocument();
    expect(screen.queryByText("Test completed")).not.toBeInTheDocument();
  });

  it("says Test completed after a run that returned a result", async () => {
    mocks.outcome = [true, { output: 1, reason: "Text is valid JSON" }];
    await runTest();
    expect(screen.getByText("Test completed")).toBeInTheDocument();

    await advance(6000);
    expect(screen.getByText("Test completed")).toBeInTheDocument();
  });
});
