import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import EvalFeedbackTab from "../EvalFeedbackTab";

// W10-EVALS-U05: Feedback search runs on the loaded page only. It must say
// so, match the labels the table shows (Correct / Incorrect), and never
// claim an eval has no feedback when the search simply missed this page.

const h = vi.hoisted(() => ({ feedback: {} }));

vi.mock("../../hooks/useEvalFeedback", () => ({
  useEvalFeedbackList: () => h.feedback,
}));
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));
vi.mock("src/components/data-table", () => {
  // Mirrors DataTable's empty overlay: `emptyContent` wins over
  // `emptyMessage` when the page has no rows to show.
  const MockDataTable = ({ data = [], emptyMessage, emptyContent }) => (
    <div data-testid="feedback-table">
      {data.length === 0
        ? emptyContent ?? emptyMessage
        : data.map((row) => (
            <div key={row.id} data-testid="feedback-row">
              {row.explanation}
            </div>
          ))}
    </div>
  );
  MockDataTable.propTypes = {
    data: PropTypes.array,
    emptyMessage: PropTypes.string,
    emptyContent: PropTypes.node,
  };
  const MockDataTablePagination = ({ total }) => (
    <div data-testid="feedback-pagination">{`1-25 of ${total}`}</div>
  );
  MockDataTablePagination.propTypes = { total: PropTypes.number };
  return {
    DataTable: MockDataTable,
    DataTablePagination: MockDataTablePagination,
  };
});
vi.mock("src/sections/develop-detail/DataTab/common", () => ({
  normalizeEvalCellValue: (value) => value,
}));
vi.mock(
  "src/sections/evals/EvalDetails/EvalsFeedback/AddEvalsFeedbackDrawer",
  () => ({ default: () => null }),
);
vi.mock("@monaco-editor/react", () => ({ default: () => null }));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));

const PAGE_ONE = [
  {
    id: "fb-1",
    value: "passed",
    explanation: "Matches the reference answer",
    user_name: "Ana Ruiz",
    source: "dataset",
    created_at: "2026-08-03T10:00:00Z",
  },
  {
    id: "fb-2",
    value: "failed",
    explanation: "Misses the refund policy",
    user_name: "Ben Ode",
    source: "eval_playground",
    created_at: "2026-08-03T11:00:00Z",
  },
];

const search = (value) =>
  fireEvent.change(screen.getByRole("textbox", { name: "Search this page" }), {
    target: { value },
  });

describe("EvalFeedbackTab search scope (W10-EVALS-U05)", () => {
  beforeEach(() => {
    h.feedback = {
      data: { items: PAGE_ONE, total: 140 },
      isLoading: false,
      isFetching: false,
    };
  });

  it("labels the search field as covering this page", () => {
    render(<EvalFeedbackTab templateId="eval-1" />);

    expect(
      screen.getByRole("textbox", { name: "Search this page" }),
    ).toHaveAttribute("placeholder", "Search this page");
  });

  it("matches the Incorrect label the table shows", () => {
    render(<EvalFeedbackTab templateId="eval-1" />);

    search("Incorrect");

    expect(screen.getByText("Misses the refund policy")).toBeInTheDocument();
    expect(
      screen.queryByText("Matches the reference answer"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("No feedback submitted yet"),
    ).not.toBeInTheDocument();
  });

  it("says the search missed this page instead of claiming no feedback exists", () => {
    render(<EvalFeedbackTab templateId="eval-1" />);

    search("Priya");

    expect(
      screen.getByText('No feedback on this page matches "Priya"'),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("No feedback submitted yet"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("feedback-pagination")).toHaveTextContent(
      "1-25 of 140",
    );

    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));

    expect(
      screen.getByRole("textbox", { name: "Search this page" }),
    ).toHaveValue("");
    expect(screen.getAllByTestId("feedback-row")).toHaveLength(2);
    expect(
      screen.queryByText('No feedback on this page matches "Priya"'),
    ).not.toBeInTheDocument();
  });

  it("keeps the no-feedback message for an eval without feedback", () => {
    h.feedback = {
      data: { items: [], total: 0 },
      isLoading: false,
      isFetching: false,
    };
    render(<EvalFeedbackTab templateId="eval-1" />);

    expect(screen.getByText("No feedback submitted yet")).toBeInTheDocument();

    search("Priya");

    expect(screen.getByText("No feedback submitted yet")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Clear search" }),
    ).not.toBeInTheDocument();
  });
});
