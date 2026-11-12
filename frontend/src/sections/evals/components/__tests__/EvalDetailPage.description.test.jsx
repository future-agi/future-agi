/* eslint-disable react/prop-types */
import React, { forwardRef, useEffect, useImperativeHandle } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import EvalDetailPage from "../EvalDetailPage";

// WLA-R2-02: the walk typed this Description on Create evaluation, saved, and
// found it empty on the saved evaluation (also after a reload).
const DESCRIPTION = "Checks that a support reply is polite.";
const PROMPT = "Is this reply polite? {{output}}";

const mocks = vi.hoisted(() => ({
  data: null,
  versions: { versions: [] },
  update: vi.fn(),
  create: vi.fn(),
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
// Rendered without a QueryClient, so the ground-truth list is mocked empty.
vi.mock("../../hooks/useGroundTruth", async (importOriginal) => ({
  ...(await importOriginal()),
  useGroundTruthList: () => ({ data: undefined }),
}));
vi.mock("src/utils/axios", () => ({ default: {}, endpoints: {} }));
vi.mock("src/components/custom-model-dropdown/KeysDrawer", () => ({
  default: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  ),
}));
vi.mock("../LLMPromptEditor", () => ({
  default: ({ messages, onMessagesChange }) => (
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
  ),
}));
vi.mock("../InstructionEditor", () => ({ default: () => null }));
vi.mock("../TestPlayground", () => ({
  default: forwardRef(function Playground({ onReadyChange }, ref) {
    useEffect(() => {
      onReadyChange(true);
    }, [onReadyChange]);
    useImperativeHandle(ref, () => ({ runTest: vi.fn() }));
    return null;
  }),
}));
vi.mock("../FewShotExamples", () => ({ default: () => null }));
vi.mock("../CodeEvalEditor", () => ({ default: () => null }));
vi.mock("../CompositeDetailPanel", () => ({ default: () => null }));
vi.mock("../EvalFeedbackTab", () => ({ default: () => null }));
vi.mock("../EvalGroundTruthTab", () => ({ default: () => null }));
vi.mock("../EvalUsageTab", () => ({ default: () => null }));
vi.mock("../VersionBadge", () => ({ default: () => null }));
vi.mock("../BulkDeleteDialog", () => ({ default: () => null }));

const MESSAGES = [{ role: "system", content: PROMPT }];

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/");
  // A custom LLM judge as Save Evaluation leaves it: the description and tags
  // live on the template; publishing created V1 from the template config.
  mocks.data = {
    id: "reply-is-polite",
    name: "reply_is_polite",
    owner: "user",
    eval_type: "llm",
    instructions: PROMPT,
    model: "gpt-4o",
    output_type: "pass_fail",
    pass_threshold: 0.5,
    description: DESCRIPTION,
    tags: ["CHATBOT_BEHAVIORS"],
    template_format: "mustache",
    config: {
      model: "gpt-4o",
      output: "Pass/Fail",
      required_keys: ["output"],
      rule_prompt: PROMPT,
      messages: MESSAGES,
      template_format: "mustache",
    },
  };
  mocks.versions = {
    versions: [
      {
        id: "v1",
        version_number: 1,
        is_default: true,
        model: "gpt-4o",
        criteria: PROMPT,
        config_snapshot: {
          model: "gpt-4o",
          output: "Pass/Fail",
          required_keys: ["output"],
          rule_prompt: PROMPT,
          messages: MESSAGES,
          template_format: "mustache",
        },
      },
    ],
  };
  mocks.update.mockResolvedValue({});
  mocks.create.mockResolvedValue({ id: "v2", version_number: 2 });
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Unexpected network request");
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

const descriptionBox = () =>
  screen.getByPlaceholderText("Enter a description here");

describe("Eval detail shows the description saved at create", () => {
  it("shows the description when the saved evaluation opens", async () => {
    render(<EvalDetailPage />);
    // The page opens the default version (V1) and says so in the URL.
    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).get("v")).toBe("1"),
    );
    expect(descriptionBox()).toHaveValue(DESCRIPTION);
  });

  it("shows the description after a reload on ?v=1", async () => {
    window.history.replaceState({}, "", "/dashboard/evaluations/x?v=1");
    render(<EvalDetailPage />);
    await waitFor(() => expect(descriptionBox()).toHaveValue(DESCRIPTION));
  });

  it("keeps the description and tags on Save Version", async () => {
    render(<EvalDetailPage />);
    // Initial population deliberately suppresses dirty events for 100 ms.
    await new Promise((resolve) => setTimeout(resolve, 120));
    await userEvent.type(screen.getByLabelText("Instructions"), " Be strict.");
    await userEvent.click(screen.getByRole("button", { name: "Save Version" }));
    await waitFor(() => expect(mocks.update).toHaveBeenCalled());
    expect(mocks.update).toHaveBeenCalledWith(
      expect.objectContaining({
        description: DESCRIPTION,
        tags: ["CHATBOT_BEHAVIORS"],
      }),
    );
  });
});
