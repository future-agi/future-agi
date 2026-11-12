/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "src/utils/test-utils";
import EvalCreatePage from "./EvalCreatePage";

// FA-EVALS-02 (evalcreate:name): the Name field keeps what was typed, so a
// typed eval's Save (Decision tree, Remote scorer) runs the same naming check
// as Save Evaluation before anything is sent, and a name the server refuses
// is said at the Name field.

const state = vi.hoisted(() => ({
  typedRoute: "/unit/typed-create",
  typedAnswer: vi.fn(),
  post: vi.fn(),
  get: vi.fn(),
  save: vi.fn(),
  autoSave: vi.fn(),
  navigate: vi.fn(),
  snackbar: vi.fn(),
  typedRefusal: vi.fn(),
}));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { post: state.post, get: state.get },
}));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({}),
  useNavigate: () => state.navigate,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureLocked: () => ({ locked: false, isLoading: false }),
  useFeatureDenied: () => false,
}));
vi.mock("src/hooks/useErrorLocalization", () => ({
  useErrorLocalizationAvailable: () => false,
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: state.snackbar }),
}));
vi.mock("../hooks/useEvalDetail", () => ({
  useUpdateEval: () => ({
    mutate: state.autoSave,
    mutateAsync: state.save,
    isPending: false,
  }),
}));
vi.mock("../hooks/useCreateEval", () => ({
  useCreateEval: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("../hooks/useCompositeEval", () => ({
  useCreateCompositeEval: () => ({ mutateAsync: vi.fn(), isPending: false }),
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
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("./InstructionEditor", () => ({
  default: ({ value, onChange }) => (
    <textarea
      aria-label="Instructions"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}));
vi.mock("./LLMPromptEditor", () => ({ default: () => null }));
vi.mock("./ModelSelector", () => ({
  default: () => null,
  FAGI_MODEL_VALUES: new Set(["turing_large"]),
  JEV_MODEL_VALUES: new Set(["jev-latest"]),
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
vi.mock("./CompositeDetailPanel", () => ({ default: () => null }));
// A typed editor's Save: it hands its route and definition to the page's
// typed save, as DagEvalEditor and RemoteScorerEditor do.
vi.mock("../types/TypedEvalEditor", () => ({
  default: ({ onSave }) => (
    <button
      type="button"
      onClick={() =>
        onSave(state.typedRoute, { dag: { root: "n1" } }).catch(
          state.typedRefusal,
        )
      }
    >
      Save decision tree
    </button>
  ),
}));
vi.mock("../types/typedViews", () => ({
  typedTabsOf: () => [{ value: "dag", label: "Decision tree" }],
}));
vi.mock("src/sections/projects/MonitorsView/UnsavedChangesDialog", () => ({
  default: () => null,
}));

const nameField = () => screen.getByPlaceholderText(/hallucination/i);
const type = (element, value) =>
  fireEvent.change(element, { target: { value } });
const typedPosts = () =>
  state.post.mock.calls.filter(([url]) => url === state.typedRoute);

const openDecisionTree = async (name) => {
  await act(async () => {
    render(<EvalCreatePage />);
  });
  type(nameField(), name);
  fireEvent.click(screen.getByRole("tab", { name: "Decision tree" }));
};
const saveTyped = async () => {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Save decision tree" }));
  });
};

beforeEach(() => {
  vi.clearAllMocks();
  // The page posts its draft on mount; a typed Save posts to its own route.
  state.post.mockImplementation(async (url) =>
    url === state.typedRoute
      ? state.typedAnswer()
      : { data: { result: { id: "new-draft" } } },
  );
  state.typedAnswer.mockResolvedValue({ data: { result: { id: "dag-1" } } });
  state.save.mockResolvedValue({});
});

describe("EvalCreatePage typed eval Save and the eval name", () => {
  it("does not send a typed eval whose name the server refuses", async () => {
    await openDecisionTree("Hallucination detector");
    await saveTyped();
    expect(typedPosts()).toHaveLength(0);
    expect(state.navigate).not.toHaveBeenCalledWith(
      "/dashboard/evaluations/dag-1",
    );
    expect(state.typedRefusal).toHaveBeenCalledWith(
      expect.objectContaining({
        message: expect.stringMatching(/characters an eval name can't use/i),
      }),
    );
    expect(nameField()).toHaveAttribute("aria-invalid", "true");
  });

  it("sends a valid name as typed", async () => {
    await openDecisionTree("hallucination_detector");
    await saveTyped();
    expect(typedPosts()).toHaveLength(1);
    expect(typedPosts()[0][1]).toEqual(
      expect.objectContaining({
        name: "hallucination_detector",
        dag: { root: "n1" },
      }),
    );
    expect(state.navigate).toHaveBeenCalledWith("/dashboard/evaluations/dag-1");
  });

  it("says a name the server refuses at the Name field", async () => {
    await openDecisionTree("hallucination_detector");
    // utils/axios rejects with the response body spread onto the error.
    state.typedAnswer.mockRejectedValueOnce({
      errors: ["An evaluation with this name already exists."],
      error: "An evaluation with this name already exists.",
      statusCode: 400,
    });
    await saveTyped();
    expect(typedPosts()).toHaveLength(1);
    expect(state.typedRefusal).toHaveBeenCalledTimes(1);
    expect(nameField()).toHaveAttribute("aria-invalid", "true");
    expect(nameField()).toHaveAccessibleDescription(/already exists/i);
  });
});
