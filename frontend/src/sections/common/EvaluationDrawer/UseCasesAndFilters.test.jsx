import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import UseCasesAndFilters from "./UseCasesAndFilters";
import { EvaluationContext } from "./context/EvaluationContext";

vi.mock("./sections/Evaluations", () => ({
  default: () => <div data-testid="evaluations-filters" />,
}));

const DEFAULT_CONTEXT_VALUE = {
  setSelectedGroup: vi.fn(),
  module: "dataset",
};

function renderComponent(props = {}, contextOverrides = {}) {
  const contextValue = { ...DEFAULT_CONTEXT_VALUE, ...contextOverrides };
  const defaultProps = {
    control: {},
    setValue: vi.fn(),
    isEvalsView: false,
    currentTab: "evals",
    setCurrentTab: vi.fn(),
  };
  return render(
    <EvaluationContext.Provider value={contextValue}>
      <UseCasesAndFilters {...defaultProps} {...props} />
    </EvaluationContext.Provider>,
  );
}

describe("UseCasesAndFilters", () => {
  it("shows both the Evals and Groups tabs for a module that supports eval groups", () => {
    renderComponent({}, { module: "dataset" });
    expect(screen.getByRole("tab", { name: "Evals" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Groups" })).toBeInTheDocument();
  });

  it("hides the Groups tab for a module that doesn't support eval groups", () => {
    renderComponent({}, { module: "some-unlisted-module" });
    expect(screen.getByRole("tab", { name: "Evals" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Groups" })).not.toBeInTheDocument();
  });

  it("hides the tab strip entirely in the standalone evals view", () => {
    renderComponent({ isEvalsView: true }, { module: "some-unlisted-module" });
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("calls setCurrentTab and clears the selected group when a tab is clicked", async () => {
    const setCurrentTab = vi.fn();
    const setSelectedGroup = vi.fn();
    const user = userEvent.setup();
    renderComponent({ setCurrentTab }, { module: "dataset", setSelectedGroup });

    await user.click(screen.getByRole("tab", { name: "Groups" }));

    expect(setCurrentTab).toHaveBeenCalledWith("groups");
    expect(setSelectedGroup).toHaveBeenCalledWith(null);
  });

  it("renders the Evaluations filters when the evals tab is active", () => {
    renderComponent({ currentTab: "evals" }, { module: "dataset" });
    expect(screen.getByTestId("evaluations-filters")).toBeInTheDocument();
  });

  it("hides the Evaluations filters when a different tab is active", () => {
    renderComponent({ currentTab: "groups" }, { module: "dataset" });
    expect(screen.queryByTestId("evaluations-filters")).not.toBeInTheDocument();
  });
});
