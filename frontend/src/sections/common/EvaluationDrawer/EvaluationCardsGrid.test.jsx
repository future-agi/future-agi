import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import EvaluationCardsGrid from "./EvaluationCardsGrid";
import { EvaluationContext } from "./context/EvaluationContext";
import { useEvalStore, resetEvalStore } from "../../evals/store/useEvalStore";

const mocks = vi.hoisted(() => ({
  mockPost: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { post: (...args) => mocks.mockPost(...args) },
  endpoints: {
    develop: { eval: { editGroupEvalList: "/edit-group-eval-list/" } },
  },
}));

vi.mock("src/auth/hooks", () => ({
  // RolePermission keys off ROLES.ADMIN === "Admin" (rolePermissionMapping.js).
  useAuthContext: () => ({ role: "Admin" }),
}));

vi.mock("./EvaluationCard", () => ({
  default: (props) => (
    <div data-testid={`eval-card-${props.eval.id}`}>
      <span>{props.eval.name}</span>
      <button
        onClick={() =>
          props.setSelectedEvals((prev) => [...(prev || []), props.eval])
        }
      >
        select-{props.eval.id}
      </button>
    </div>
  ),
}));

vi.mock("src/sections/evals/EvalPlayground/EvalPlayground", () => ({
  default: (props) => (
    <div data-testid="eval-playground" data-open={String(!!props.open)} />
  ),
}));

vi.mock("./EditCustomEvals", () => ({
  default: (props) => (
    <div data-testid="edit-custom-evals" data-open={String(!!props.open)} />
  ),
}));

vi.mock("./CreateEvaluationGroupDrawer", () => ({
  default: (props) => (
    <div data-testid="create-group-drawer" data-open={String(!!props.open)} />
  ),
}));

const DEFAULT_CONTEXT_VALUE = {
  playgroundEvaluation: null,
  setPlaygroundEvaluation: vi.fn(),
  setVisibleSection: vi.fn(),
  setCurrentTab: vi.fn(),
  setSelectedGroup: vi.fn(),
};

function renderComponent(props = {}, contextOverrides = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const contextValue = { ...DEFAULT_CONTEXT_VALUE, ...contextOverrides };
  const defaultProps = {
    evals: [],
    recommendations: [],
  };
  return render(
    <QueryClientProvider client={queryClient}>
      <EvaluationContext.Provider value={contextValue}>
        <EvaluationCardsGrid {...defaultProps} {...props} />
      </EvaluationContext.Provider>
    </QueryClientProvider>,
  );
}

describe("EvaluationCardsGrid", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetEvalStore();
  });

  it("renders the special card plus one card per eval, sorted recommended-first", () => {
    renderComponent({
      evals: [
        { id: "a1", name: "Alpha" },
        { id: "b1", name: "Beta" },
      ],
      recommendations: ["Beta"],
    });

    expect(screen.getByText("Create your own evals")).toBeInTheDocument();
    const cardTestIds = screen
      .getAllByTestId(/^eval-card-/)
      .map((el) => el.dataset.testid);
    expect(cardTestIds).toEqual(["eval-card-b1", "eval-card-a1"]);
  });

  it("preserves the original order when showRecommendations is false", () => {
    renderComponent({
      evals: [
        { id: "a1", name: "Alpha" },
        { id: "b1", name: "Beta" },
      ],
      recommendations: ["Beta"],
      showRecommendations: false,
    });

    const cardTestIds = screen
      .getAllByTestId(/^eval-card-/)
      .map((el) => el.dataset.testid);
    expect(cardTestIds).toEqual(["eval-card-a1", "eval-card-b1"]);
  });

  it("routes the special card click to setVisibleSection('custom') when the role can create evals", async () => {
    const setVisibleSection = vi.fn();
    const user = userEvent.setup();
    renderComponent({}, { setVisibleSection });

    await user.click(screen.getByText("Create your own evals"));

    expect(setVisibleSection).toHaveBeenCalledWith("custom");
  });

  it("shows the playground/edit drawers open based on playgroundEvaluation from context", () => {
    renderComponent(
      {},
      {
        playgroundEvaluation: { evalsActionType: "playground" },
      },
    );
    expect(screen.getByTestId("eval-playground")).toHaveAttribute(
      "data-open",
      "true",
    );
    expect(screen.getByTestId("edit-custom-evals")).toHaveAttribute(
      "data-open",
      "false",
    );
  });

  it("shows a sticky selection bar while createGroupMode is active, and Cancel resets the store", async () => {
    useEvalStore.setState({
      createGroupMode: true,
      selectedEvals: [{ id: "a1" }],
    });
    const user = userEvent.setup();
    renderComponent({
      evals: [{ id: "a1", name: "Alpha" }],
      recommendations: [],
    });

    expect(screen.getByText("Evals Selected (1)")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(useEvalStore.getState().createGroupMode).toBe(false);
    expect(useEvalStore.getState().selectedEvals).toEqual([]);
  });

  it("posts only the diff (added ids) to editGroupEvalList when updating an existing group", async () => {
    window.history.pushState({}, "", "/evaluations?group-id=group-1");
    mocks.mockPost.mockResolvedValueOnce({ data: { status: true } });
    useEvalStore.setState({
      createGroupMode: true,
      EditGroupMode: true,
      selectedEvals: [{ id: "a1", name: "Alpha" }],
    });

    const user = userEvent.setup();
    renderComponent({
      evals: [
        { id: "a1", name: "Alpha" },
        { id: "b2", name: "Beta" },
      ],
      recommendations: [],
    });

    // Select the new eval that wasn't part of the group initially.
    await user.click(screen.getByRole("button", { name: "select-b2" }));
    await user.click(screen.getByRole("button", { name: "Add Evaluations" }));

    await vi.waitFor(() => expect(mocks.mockPost).toHaveBeenCalled());
    expect(mocks.mockPost).toHaveBeenCalledWith("/edit-group-eval-list/", {
      eval_group_id: "group-1",
      added_template_ids: ["b2"],
    });

    // Reset the URL for subsequent tests in this file.
    window.history.pushState({}, "", "/");
  });
});
