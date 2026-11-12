/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "src/utils/test-utils";
import EvalCreatePage from "./EvalCreatePage";

// FA-EVALS-02: the Eval Name box keeps what the user typed and says which
// characters a name can use, instead of lowercasing and dropping the rest.

const state = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  save: vi.fn(),
  autoSave: vi.fn(),
  navigate: vi.fn(),
  snackbar: vi.fn(),
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
vi.mock("../types/TypedEvalEditor", () => ({ default: () => null }));
vi.mock("../types/typedViews", () => ({ typedTabsOf: () => [] }));
vi.mock("src/sections/projects/MonitorsView/UnsavedChangesDialog", () => ({
  default: () => null,
}));

// The backend naming rule (model_hub eval update: ^[a-z0-9_-]+$).
const NAME_RULE = /^[a-z0-9_-]+$/;

// Matches the field whether its example is written "Eg:" or "e.g.".
const nameField = () => screen.getByPlaceholderText(/hallucination/i);
const saveButton = () =>
  screen.getByRole("button", { name: /^(Save Evaluation|Saving\.\.\.)$/ });
const type = (element, value) =>
  fireEvent.change(element, { target: { value } });

const mountPage = async () => {
  await act(async () => {
    render(<EvalCreatePage />);
  });
};

beforeEach(() => {
  vi.clearAllMocks();
  state.post.mockResolvedValue({ data: { result: { id: "new-draft" } } });
  state.save.mockResolvedValue({});
});

describe("EvalCreatePage Eval Name", () => {
  it("keeps what was typed and says which characters a name can use", async () => {
    await mountPage();
    type(nameField(), "Hallucination detector");
    // Nothing is lowercased or dropped behind the user's back.
    expect(nameField()).toHaveValue("Hallucination detector");
    expect(nameField()).toHaveAttribute("aria-invalid", "true");
    expect(nameField()).toHaveAccessibleDescription(
      /lowercase letters, numbers, hyphens \(-\) and underscores \(_\)/i,
    );
  });

  it("keeps Save off for a name the server refuses, then saves a valid one as typed", async () => {
    await mountPage();
    type(screen.getByLabelText("Instructions"), "Judge {{output}} carefully");
    type(nameField(), "Hallucination detector");
    expect(saveButton()).toBeDisabled();

    type(nameField(), "hallucination_detector");
    expect(nameField()).toHaveAttribute("aria-invalid", "false");
    expect(saveButton()).toBeEnabled();
    await act(async () => {
      fireEvent.click(saveButton());
    });
    expect(state.save).toHaveBeenLastCalledWith(
      expect.objectContaining({
        name: "hallucination_detector",
        publish: true,
      }),
    );
  });

  it("offers an example name the naming rule accepts", async () => {
    await mountPage();
    const example = nameField()
      .getAttribute("placeholder")
      .replace(/^e\.?g\.?:?\s*/i, "");
    expect(example).toMatch(NAME_RULE);
  });
});
