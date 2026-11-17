/* eslint-disable react/prop-types */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "src/utils/test-utils";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { setCollabOfferForTest } from "src/sections/common/Collab/collabOffer";
import EvalCreatePage from "./EvalCreatePage";

// Remote scorers are listed in the launch registry but a deployment does
// not offer them: their create route is absent. Create evaluation keeps the
// Remote scorer tab, turned off and tied to the shared line; pressing it
// says that line, the tab does not open, its editor never mounts and
// nothing is sent to the create route. Decision tree is unaffected.

const REMOTE_ROUTE = "/model-hub/eval-templates/create-remote/";

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
// A typed editor's Save hands its create route and definition to the page,
// as RemoteScorerEditor and DagEvalEditor do.
vi.mock("../types/TypedEvalEditor", () => ({
  default: ({ evalType, onSave }) => (
    <button
      type="button"
      onClick={() =>
        onSave(evalType === "remote" ? REMOTE_ROUTE : "/unit/dag-create", {
          url: "https://scorer.example.com/score",
        }).catch(() => {})
      }
    >
      {`Save ${evalType}`}
    </button>
  ),
}));
vi.mock("../types/typedViews", () => ({
  typedTabsOf: () => [
    { value: "dag", label: "Decision tree" },
    { value: "remote", label: "Remote scorer" },
  ],
}));
vi.mock("src/sections/projects/MonitorsView/UnsavedChangesDialog", () => ({
  default: () => null,
}));

const remotePosts = () =>
  state.post.mock.calls.filter(([url]) =>
    String(url).includes("create-remote"),
  );
const mount = async () => {
  await act(async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <EvalCreatePage />
      </QueryClientProvider>,
    );
  });
  fireEvent.change(screen.getByPlaceholderText(/hallucination/i), {
    target: { value: "remote_check" },
  });
};
const pressTab = async (name) => {
  await act(async () => {
    fireEvent.click(screen.getByRole("tab", { name }));
  });
};
// Where an editor still mounted, a person saves it.
const saveIfShown = async (evalType) => {
  const save = screen.queryByRole("button", { name: `Save ${evalType}` });
  if (save)
    await act(async () => {
      fireEvent.click(save);
    });
};

beforeEach(() => {
  vi.clearAllMocks();
  // Pin the unavailable-deployment fixture, independent of live claims.
  setCollabOfferForTest({ all: false, madeReal: [] });
  // The page posts its draft on mount; a typed Save posts to its route.
  state.post.mockResolvedValue({ data: { result: { id: "new-draft" } } });
  state.save.mockResolvedValue({});
});
afterEach(() => setCollabOfferForTest(null));

describe("Create evaluation where remote scorers are not offered", () => {
  it("keeps the Remote scorer tab, disabled and described by the shared line", async () => {
    await mount();
    const tab = screen.getByRole("tab", { name: "Remote scorer" });
    expect(tab).toHaveAttribute("aria-disabled", "true");
    expect(tab).toHaveAccessibleDescription(NOT_IN_RELEASE);
    expect(
      screen.getByRole("tab", { name: "Decision tree" }),
    ).not.toHaveAttribute("aria-disabled");
  });

  it("pressing it says the shared line; the tab does not open and nothing is sent", async () => {
    await mount();
    await pressTab("Remote scorer");
    await saveIfShown("remote");
    expect(state.snackbar).toHaveBeenCalledWith(NOT_IN_RELEASE, {
      variant: "info",
    });
    expect(screen.getByRole("tab", { name: "Remote scorer" })).toHaveAttribute(
      "aria-selected",
      "false",
    );
    expect(screen.queryByRole("button", { name: "Save remote" })).toBeNull();
    expect(remotePosts()).toEqual([]);
  });

  it("Decision tree still opens its editor", async () => {
    await mount();
    await pressTab("Decision tree");
    expect(screen.getByRole("tab", { name: "Decision tree" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      screen.getByRole("button", { name: "Save dag" }),
    ).toBeInTheDocument();
  });
});

describe("Create evaluation where the registry makes remote scorers real", () => {
  it("the Remote scorer tab opens its editor, with no sentence", async () => {
    setCollabOfferForTest({ all: false, madeReal: ["evals.remote"] });
    await mount();
    const tab = screen.getByRole("tab", { name: "Remote scorer" });
    expect(tab).not.toHaveAttribute("aria-disabled");
    await pressTab("Remote scorer");
    expect(state.snackbar).not.toHaveBeenCalledWith(NOT_IN_RELEASE, {
      variant: "info",
    });
    expect(
      screen.getByRole("button", { name: "Save remote" }),
    ).toBeInTheDocument();
  });
});
