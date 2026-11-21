import React, { forwardRef } from "react";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import EvalDetailPage from "../EvalDetailPage";

const mocks = vi.hoisted(() => ({
  data: null,
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
vi.mock("../../hooks/useEvalVersions", () => {
  const versions = { versions: [] };
  return {
    useCreateEvalVersion: () => ({ mutateAsync: mocks.create }),
    useEvalVersions: () => ({ data: versions }),
  };
});
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
  const ResizablePanels = ({ leftPanel, rightPanel }) => (
    <>
      {leftPanel}
      {rightPanel}
    </>
  );
  ResizablePanels.propTypes = {
    leftPanel: PropTypes.node,
    rightPanel: PropTypes.node,
  };
  return { default: ResizablePanels };
});
vi.mock("../LLMPromptEditor", () => ({ default: () => null }));
vi.mock("../InstructionEditor", () => ({ default: () => null }));
vi.mock("../TestPlayground", () => ({
  default: forwardRef(function TestPlayground() {
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

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/");
  mocks.data = {
    id: "eval",
    name: "Tone check",
    owner: "user",
    eval_type: "llm",
    instructions: "Check {{input}}",
    output_type: "pass_fail",
    pass_threshold: 0.5,
    tags: ["RED_TEAMING"],
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

const renderPage = async () => {
  render(<EvalDetailPage />);
  // Initial population deliberately suppresses dirty events for 100 ms.
  await new Promise((resolve) => setTimeout(resolve, 120));
};
const customTagField = () => screen.getByLabelText("Custom tag");
const tagsSection = () => screen.getByText("Tags").parentElement;
// Only custom tags are removable; the curated chips toggle instead.
const removableChips = () =>
  within(tagsSection()).queryAllByTestId("CancelIcon");
const save = () => screen.getByRole("button", { name: "Save Version" });

describe("EvalDetailPage custom tags", () => {
  it("adds a typed tag on Enter as a removable chip and saves it", async () => {
    await renderPage();
    await userEvent.type(customTagField(), "  client-demo  {Enter}");

    // This page shows tags outside the curated list in title case.
    const chip = screen.getByRole("button", { name: "Client-Demo" });
    expect(within(chip).getByTestId("CancelIcon")).toBeInTheDocument();
    expect(customTagField()).toHaveValue("");

    await userEvent.click(save());
    await waitFor(() => expect(mocks.update).toHaveBeenCalledTimes(1));
    expect(mocks.update).toHaveBeenCalledWith(
      expect.objectContaining({ tags: ["RED_TEAMING", "client-demo"] }),
    );
  });

  it("ignores blank input and a tag that is already selected", async () => {
    await renderPage();
    await userEvent.type(customTagField(), "   {Enter}");
    expect(removableChips()).toHaveLength(0);

    await userEvent.type(customTagField(), "client-demo{Enter}");
    await userEvent.type(customTagField(), "client-demo{Enter}");
    expect(
      screen.getAllByRole("button", { name: "Client-Demo" }),
    ).toHaveLength(1);
    expect(removableChips()).toHaveLength(1);
    expect(customTagField()).toHaveValue("");
  });

  it("saves a tag still in the field when it is the only change", async () => {
    await renderPage();
    expect(save()).toBeDisabled();
    // Typed but not confirmed with Enter.
    await userEvent.type(customTagField(), " my-project ");
    expect(save()).toBeEnabled();

    await userEvent.click(save());
    expect(mocks.update).toHaveBeenCalledTimes(1);
    expect(mocks.update).toHaveBeenCalledWith(
      expect.objectContaining({ tags: ["RED_TEAMING", "my-project"] }),
    );
    // The saved tag becomes a chip and the field is ready for the next one.
    await waitFor(() => expect(customTagField()).toHaveValue(""));
    expect(
      screen.getByRole("button", { name: "My-Project" }),
    ).toBeInTheDocument();
  });

  it("offers no custom tag field on a system evaluation", () => {
    mocks.data = { ...mocks.data, owner: "system", tags: ["my-project"] };
    render(<EvalDetailPage />);

    expect(screen.getByText("My-Project")).toBeInTheDocument();
    expect(screen.queryByLabelText("Custom tag")).toBeNull();
    expect(removableChips()).toHaveLength(0);
  });
});
