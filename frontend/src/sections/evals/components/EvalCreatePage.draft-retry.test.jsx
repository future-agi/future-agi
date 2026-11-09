/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import EvalCreatePage from "./EvalCreatePage";

const state = vi.hoisted(() => ({
  params: {},
  post: vi.fn(),
  get: vi.fn(),
  save: vi.fn(),
  autoSave: vi.fn(),
  runTest: vi.fn(),
  navigate: vi.fn(),
  snackbar: vi.fn(),
}));

// No real transport is imported: every draft request stays in this test.
vi.mock("src/utils/axios", () => ({
  default: { post: state.post, get: state.get },
  endpoints: {
    develop: {
      eval: {
        createEvalTemplateV2: "/unit/eval-drafts",
        getEvalDetail: (id) => `/unit/eval-drafts/${id}`,
      },
    },
  },
}));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => state.params,
  useNavigate: () => state.navigate,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/hooks/useCapabilities", () => ({
  CAPABILITY: { TURING_MODELS: "turing", AGENTIC_EVAL: "agent" },
  useFeatureLocked: () => ({ locked: false, isLoading: false }),
}));
vi.mock("src/hooks/useErrorLocalization", () => ({
  useErrorLocalizationAvailable: () => false,
}));
vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: state.snackbar }),
}));
vi.mock("../hooks/useEvalDetail", () => ({
  useUpdateEval: (id) => ({
    mutate: (payload) => state.autoSave(id, payload),
    mutateAsync: (payload) => state.save(id, payload),
    isPending: false,
  }),
}));
vi.mock("../hooks/useCreateEval", () => ({
  useCreateEval: () => ({ isPending: false }),
}));
vi.mock("../hooks/useCompositeEval", () => ({
  useCreateCompositeEval: () => ({ isPending: false }),
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
}));
vi.mock("./TestPlayground", () => {
  const Mock = React.forwardRef((_, ref) => {
    React.useImperativeHandle(ref, () => ({ runTest: state.runTest }));
    return null;
  });
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

const failures = [
  ["5xx", { response: { status: 503 } }],
  ["403", { response: { status: 403 } }],
  ["network", new Error("Network Error")],
];
const createdDraft = { data: { result: { id: "new-draft" } } };
const loadedDraft = {
  data: {
    result: {
      id: "existing-draft",
      eval_type: "agent",
      instructions: "Saved instructions for {{output}}",
      config: { model: "turing_large" },
    },
  },
};
const click = async (name) => {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
  });
};
const fillName = () => {
  fireEvent.change(screen.getByPlaceholderText("Eg: Hallucination detector"), {
    target: { value: "recovered_eval" },
  });
};
const fillForm = () => {
  fillName();
  fireEvent.change(screen.getByLabelText("Instructions"), {
    target: { value: "Judge {{output}} carefully" },
  });
};
const expectConfigurationUnavailable = () => {
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(screen.queryByRole("slider")).not.toBeInTheDocument();
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Save Evaluation" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Test Evaluation" }),
  ).not.toBeInTheDocument();
};
const mountPage = async () => {
  await act(async () => {
    render(<EvalCreatePage />);
  });
};

beforeEach(() => {
  vi.useFakeTimers();
  // Keep the shared jsdom matchMedia/observer implementations intact.
  [
    state.post,
    state.get,
    state.save,
    state.autoSave,
    state.runTest,
    state.navigate,
    state.snackbar,
  ].forEach((mock) => mock.mockReset());
  state.params = {};
  state.save.mockResolvedValue({});
  state.post.mockResolvedValue(createdDraft);
  state.get.mockResolvedValue(loadedDraft);
});
afterEach(() => {
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe.each(["Save Evaluation", "Test Evaluation"])(
  "draft recovery before %s",
  (action) => {
    it.each(failures)(
      "retries a mount POST after %s without losing input",
      async (_, error) => {
        state.post.mockRejectedValueOnce(error);
        await mountPage();
        expect(state.post).toHaveBeenCalledTimes(1);
        expect(screen.getByRole("alert")).toHaveTextContent(
          "Draft not ready yet, please try again",
        );
        fillForm();
        await click("Retry");
        expect(state.post).toHaveBeenCalledTimes(2);
        expect(state.post).toHaveBeenLastCalledWith(
          "/unit/eval-drafts",
          expect.objectContaining({ is_draft: true }),
        );
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        expect(screen.getByLabelText("Instructions")).toHaveValue(
          "Judge {{output}} carefully",
        );
        await click(action);
        expect(state.save).toHaveBeenCalledWith(
          "new-draft",
          expect.objectContaining({
            instructions: "Judge {{output}} carefully",
          }),
        );
        if (action === "Save Evaluation") {
          expect(state.save).toHaveBeenLastCalledWith(
            "new-draft",
            expect.objectContaining({ name: "recovered_eval", publish: true }),
          );
          expect(state.navigate).toHaveBeenLastCalledWith(
            "/dashboard/evaluations/new-draft",
          );
        } else {
          expect(state.runTest).toHaveBeenCalledWith("new-draft");
        }
      },
    );

    it.each(failures)(
      "retries /create/:draftId after %s without replacing the draft",
      async (_, error) => {
        state.params = { draftId: "existing-draft" };
        state.get.mockRejectedValueOnce(error);
        await mountPage();
        expect(screen.getByRole("alert")).toHaveTextContent(
          "Failed to load evaluation",
        );
        // Unread config must not accept local work that a successful retry overwrites.
        expectConfigurationUnavailable();
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1000);
        });
        expect(state.autoSave).not.toHaveBeenCalled();
        await click("Retry");
        expect(state.get).toHaveBeenCalledTimes(2);
        expect(state.get).toHaveBeenLastCalledWith(
          "/unit/eval-drafts/existing-draft",
        );
        expect(state.post).not.toHaveBeenCalled();
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        expect(screen.getByLabelText("Instructions")).toHaveValue(
          "Saved instructions for {{output}}",
        );
        fillName();
        await click(action);
        expect(state.save).toHaveBeenCalledWith(
          "existing-draft",
          expect.objectContaining({
            instructions: "Saved instructions for {{output}}",
          }),
        );
        if (action === "Save Evaluation") {
          expect(state.navigate).toHaveBeenLastCalledWith(
            "/dashboard/evaluations/existing-draft",
          );
        } else {
          expect(state.runTest).toHaveBeenCalledWith("existing-draft");
        }
      },
    );
  },
);

it("waits for existing-draft hydration before mounting editable configuration", async () => {
  state.params = { draftId: "existing-draft" };
  let resolveGet;
  let rejectGet;
  state.get.mockImplementation(
    () =>
      new Promise((resolve, reject) => {
        resolveGet = resolve;
        rejectGet = reject;
      }),
  );
  await mountPage();
  expect(screen.getByText("Create evaluation")).toBeInTheDocument();
  expect(screen.getByText("Evals List")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent(
    "Draft not ready yet, please wait",
  );
  expectConfigurationUnavailable();

  await act(async () => {
    rejectGet(failures[0][1]);
  });
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Failed to load evaluation",
  );
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expectConfigurationUnavailable();

  await click("Retry");
  expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent(
    "Draft not ready yet, please wait",
  );
  expectConfigurationUnavailable();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(state.get).toHaveBeenCalledTimes(2);
  await act(async () => {
    rejectGet(failures[1][1]);
  });
  expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  expectConfigurationUnavailable();

  await click("Retry");
  expectConfigurationUnavailable();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(state.autoSave).not.toHaveBeenCalled();
  expect(state.save).not.toHaveBeenCalled();
  expect(state.runTest).not.toHaveBeenCalled();
  expect(state.post).not.toHaveBeenCalled();
  await act(async () => {
    resolveGet(loadedDraft);
  });
  expect(state.get).toHaveBeenCalledTimes(3);
  expect(state.get).toHaveBeenLastCalledWith(
    "/unit/eval-drafts/existing-draft",
  );
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Instructions")).toHaveValue(
    "Saved instructions for {{output}}",
  );
  fillForm();
  await click("Save Evaluation");
  expect(state.save).toHaveBeenLastCalledWith(
    "existing-draft",
    expect.objectContaining({
      name: "recovered_eval",
      instructions: "Judge {{output}} carefully",
      publish: true,
    }),
  );
});

it("keeps Retry available after repeated failures and prevents concurrent POSTs", async () => {
  state.post.mockRejectedValueOnce(failures[0][1]);
  state.post.mockRejectedValueOnce(failures[1][1]);
  await mountPage();
  await click("Retry");
  expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  let resolvePost;
  state.post.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolvePost = resolve;
      }),
  );
  await click("Retry");
  const retry = screen.getByRole("button", { name: "Retry" });
  expect(retry).toBeDisabled();
  fireEvent.click(retry);
  expect(state.post).toHaveBeenCalledTimes(3);
  // Unlike an unread existing draft, a new draft remains editable during POST.
  fillForm();
  await act(async () => {
    resolvePost(createdDraft);
  });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Instructions")).toHaveValue(
    "Judge {{output}} carefully",
  );
  await click("Save Evaluation");
  expect(state.save).toHaveBeenLastCalledWith(
    "new-draft",
    expect.objectContaining({
      instructions: "Judge {{output}} carefully",
      publish: true,
    }),
  );
});

it.each([false, true])(
  "offers Retry for a malformed successful draft response (existing=%s)",
  async (existing) => {
    const request = existing ? state.get : state.post;
    if (existing) state.params = { draftId: "existing-draft" };
    request.mockResolvedValueOnce({ data: { result: null } });
    await mountPage();
    expect(screen.getByRole("alert")).toBeInTheDocument();
    await click("Retry");
    expect(request).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  },
);
