import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import EvalDetailPage from "../EvalDetailPage";

const mocks = vi.hoisted(() => ({
  detail: null,
  versions: null,
  update: vi.fn(),
  createVersion: vi.fn(),
  search: new URLSearchParams(),
  setSearch: vi.fn(),
}));
vi.mock("react-router", async (original) => ({
  ...(await original()),
  useParams: () => ({ evalId: "eval-1" }),
  useNavigate: () => vi.fn(),
}));
vi.mock("react-router-dom", async (original) => ({
  ...(await original()),
  useSearchParams: () => [mocks.search, mocks.setSearch],
}));
vi.mock("notistack", async (original) => ({
  ...(await original()),
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/hooks/useCapabilities", () => ({
  CAPABILITY: { TURING_MODELS: "turing_models", AGENTIC_EVAL: "agentic_eval" },
  useFeatureLocked: () => ({ locked: false, isLoading: false }),
}));
vi.mock("src/hooks/useErrorLocalization", () => ({
  useErrorLocalizationAvailable: () => false,
}));
vi.mock("../../hooks/useEvalDetail", () => ({
  useEvalDetail: () => ({ data: mocks.detail, isLoading: false }),
  useUpdateEval: () => ({ mutateAsync: mocks.update }),
  useDuplicateEval: () => ({}),
}));
vi.mock("../../hooks/useEvalVersions", () => ({
  useEvalVersions: () => ({ data: mocks.versions }),
  useCreateEvalVersion: () => ({ mutateAsync: mocks.createVersion }),
}));
vi.mock("../../hooks/useCompositeEval", () => ({
  useCompositeDetail: () => ({}),
  useUpdateCompositeEval: () => ({}),
}));
vi.mock("../InstructionEditor", () => ({ default: () => null }));
vi.mock("../LLMPromptEditor", () => ({ default: () => null }));
vi.mock("../OutputTypeConfig", () => ({ default: () => null }));
vi.mock("../FewShotExamples", () => ({ default: () => null }));
vi.mock("../CodeEvalEditor", () => ({ default: () => null }));
vi.mock("../CompositeDetailPanel", () => ({ default: () => null }));
vi.mock("../EvalFeedbackTab", () => ({ default: () => null }));
vi.mock("../EvalGroundTruthTab", () => ({ default: () => null }));
vi.mock("../EvalUsageTab", () => ({ default: () => null }));
vi.mock("../BulkDeleteDialog", () => ({ default: () => null }));
vi.mock("../TestPlayground", async () => {
  const { forwardRef } = await import("react");
  return {
    default: forwardRef(function Playground() {
      return null;
    }),
  };
});
vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  ),
}));

// Detail uses `tags`; version snapshots are separate and must not replace them.
const detail = () => ({
  id: "eval-1",
  name: "tagroundtrip",
  owner: "user",
  template_type: "single",
  eval_type: "code",
  tags: ["output_validation", "client-demo"],
  config: { code: "def evaluate():\n    return 1", language: "python" },
  output_type: "pass_fail",
  pass_threshold: 0.5,
  required_keys: [],
});

describe("Unit: EvalDetailPage tags", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.detail = detail();
    mocks.versions = {
      versions: [
        {
          id: "version-1",
          version_number: 1,
          is_default: true,
          config_snapshot: mocks.detail.config,
        },
      ],
    };
    mocks.search = new URLSearchParams();
    mocks.update.mockResolvedValue({});
    mocks.createVersion.mockResolvedValue({
      id: "version-2",
      version_number: 2,
    });
  });

  it.each(["", "v=1"])(
    "hydrates template tags when opening a saved version (%s)",
    async (query) => {
      mocks.search = new URLSearchParams(query);
      render(<EvalDetailPage />);
      expect(
        await screen.findByRole("button", { name: "Client-Demo" }),
      ).toBeInTheDocument();
      const user = userEvent.setup();
      await user.type(
        screen.getByRole("textbox", { name: "Add custom tag" }),
        "next-tag",
      );
      await user.click(screen.getByRole("button", { name: "Save Version" }));
      await waitFor(() =>
        expect(mocks.update).toHaveBeenCalledWith(
          expect.objectContaining({
            tags: ["output_validation", "client-demo", "next-tag"],
          }),
        ),
      );
    },
  );

  it("clears saved pending input so removing its chip and saving again does not resurrect it", async () => {
    const user = userEvent.setup();
    render(<EvalDetailPage />);
    await screen.findByText("Client-Demo");
    const input = screen.getByRole("textbox", { name: "Add custom tag" });
    await user.type(input, "  pending-tag  ");
    await user.click(screen.getByRole("button", { name: "Save Version" }));
    await waitFor(() => expect(input).toHaveValue(""));
    await waitFor(() => expect(mocks.createVersion).toHaveBeenCalledTimes(1));
    const chip = screen.getByRole("button", { name: "Pending-Tag" });
    chip.focus();
    await user.keyboard("{Backspace}");
    await user.click(screen.getByRole("button", { name: "Save Version" }));
    await waitFor(() => expect(mocks.update).toHaveBeenCalledTimes(2));
    expect(mocks.update).toHaveBeenLastCalledWith(
      expect.objectContaining({ tags: ["output_validation", "client-demo"] }),
    );
    expect(screen.queryByText("Pending-Tag")).not.toBeInTheDocument();
  });

  it("keeps pending text on update failure", async () => {
    const user = userEvent.setup();
    mocks.update.mockRejectedValueOnce(new Error("Save failed"));
    render(<EvalDetailPage />);
    await screen.findByText("Client-Demo");
    const input = screen.getByRole("textbox", { name: "Add custom tag" });
    await user.type(input, "retry-tag");
    await user.click(screen.getByRole("button", { name: "Save Version" }));
    await waitFor(() => expect(mocks.update).toHaveBeenCalledTimes(1));
    expect(input).toHaveValue("retry-tag");
    expect(mocks.createVersion).not.toHaveBeenCalled();
  });

  it("does not expose custom entry on a system evaluation", async () => {
    mocks.detail = { ...detail(), owner: "system" };
    render(<EvalDetailPage />);
    await screen.findByText("Client-Demo");
    expect(
      screen.queryByRole("textbox", { name: "Add custom tag" }),
    ).not.toBeInTheDocument();
  });
});
