import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { createTheme } from "@mui/material/styles";
import EvaluationsSelectionGrid from "./EvaluationsSelectionGrid";
import EvaluationProvider from "./context/EvaluationProvider";
import { resetEvalStore } from "../../evals/store/useEvalStore";

const mocks = vi.hoisted(() => ({
  evalsListState: {
    data: { evals: [], eval_recommendations: [] },
    isLoading: false,
  },
}));

vi.mock("./getEvalsList", () => ({
  useEvalsList: () => mocks.evalsListState,
}));

vi.mock("./UseCasesAndFilters", () => ({
  default: (props) => (
    <div data-testid="use-cases-and-filters">
      <span data-testid="current-tab">{props.currentTab}</span>
      <button onClick={() => props.setCurrentTab("groups")}>
        go-to-groups
      </button>
      <button onClick={() => props.setCurrentTab("evals")}>
        go-to-evals
      </button>
    </div>
  ),
}));

vi.mock("./EvaluationCardsGrid", () => ({
  default: (props) => (
    <div data-testid="evaluation-cards-grid">
      <span data-testid="cards-grid-count">{props.evals?.length ?? 0}</span>
    </div>
  ),
}));

vi.mock("./SkeletonEvaluationCardsGrid", () => ({
  default: () => <div data-testid="cards-skeleton" />,
}));

vi.mock("../../evals/Groups/GroupsGrid", () => ({
  default: (props) => (
    <div data-testid="groups-grid">
      <button onClick={() => props.onGroupSelect("g1")}>select-group</button>
    </div>
  ),
}));

vi.mock("src/sections/evals/Groups/IndividualGroup", () => ({
  default: (props) => (
    <div data-testid="individual-group">{props.groupId}</div>
  ),
}));

function renderComponent(props = {}) {
  const theme = createTheme();
  const defaultProps = {
    onClose: vi.fn(),
    theme,
    datasetId: "dataset-1",
    isEvalsView: false,
  };
  return render(
    <EvaluationProvider>
      <EvaluationsSelectionGrid {...defaultProps} {...props} />
    </EvaluationProvider>,
  );
}

describe("EvaluationsSelectionGrid", () => {
  beforeEach(() => {
    resetEvalStore();
  });

  it("shows the loading state instead of the cards grid while evals load", () => {
    mocks.evalsListState = { data: undefined, isLoading: true };
    renderComponent();
    expect(screen.getByText("Loading Evaluations...")).toBeInTheDocument();
    expect(screen.getByTestId("cards-skeleton")).toBeInTheDocument();
    expect(screen.queryByTestId("evaluation-cards-grid")).not.toBeInTheDocument();
  });

  it("excludes built-in deterministic evals from the count and the cards grid", async () => {
    mocks.evalsListState = {
      data: {
        evals: [
          { id: "1", eval_template_name: "Custom Eval A" },
          { id: "2", eval_template_name: "Regex" }, // excluded
          { id: "3", eval_template_name: "Custom Eval B" },
        ],
        eval_recommendations: [],
      },
      isLoading: false,
    };
    renderComponent();
    expect(screen.getByText("All (2)")).toBeInTheDocument();
    // EvaluationCardsGrid is lazy-loaded (React.lazy + Suspense), so even a
    // mocked module resolves on a later tick.
    expect(await screen.findByTestId("cards-grid-count")).toHaveTextContent(
      "2",
    );
  });

  it("switches to the groups tab and hides the evals count/cards grid", async () => {
    mocks.evalsListState = {
      data: { evals: [], eval_recommendations: [] },
      isLoading: false,
    };
    const user = userEvent.setup();
    renderComponent();

    expect(screen.getByTestId("current-tab")).toHaveTextContent("evals");
    await user.click(screen.getByRole("button", { name: "go-to-groups" }));

    expect(screen.getByTestId("current-tab")).toHaveTextContent("groups");
    expect(screen.getByTestId("groups-grid")).toBeInTheDocument();
    expect(screen.queryByText(/^All \(/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("evaluation-cards-grid")).not.toBeInTheDocument();
  });

  it("shows the individual group view once a group is selected from the groups grid", async () => {
    const user = userEvent.setup();
    renderComponent();

    await user.click(screen.getByRole("button", { name: "go-to-groups" }));
    await user.click(screen.getByRole("button", { name: "select-group" }));

    expect(screen.getByTestId("individual-group")).toHaveTextContent("g1");
    expect(screen.queryByTestId("groups-grid")).not.toBeInTheDocument();
  });

  it("calls onClose when the close button is clicked", async () => {
    // Header renders [back IconButton, close IconButton, "View Docs" Button]
    // before any other interactive controls further down the tree.
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderComponent({ onClose });

    const buttons = screen.getAllByRole("button");
    await user.click(buttons[1]);

    expect(onClose).toHaveBeenCalled();
  });

  it("calls onConfigBack (in addition to resetting to the list) when the back button is clicked", async () => {
    const onConfigBack = vi.fn();
    const user = userEvent.setup();
    renderComponent({ onConfigBack });

    const buttons = screen.getAllByRole("button");
    await user.click(buttons[0]);

    expect(onConfigBack).toHaveBeenCalled();
  });
});
