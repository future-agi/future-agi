/* eslint-disable react/prop-types */
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import TaskUsageTab from "../TaskUsageTab";

// Usage is metered on successful runs only (get_usage filters to COMPLETED
// rows without an error). A task whose every eval failed therefore gets an
// empty Usage payload even though it ran: the failures live in the Logs tab.
vi.mock("../../hooks/useTaskUsage", () => ({
  useTaskUsageChart: () => ({
    data: {
      stats: {
        total_runs: 0,
        runs_period: 0,
        success_count: 0,
        error_count: 0,
        pass_rate: 0,
      },
      chart: [],
      evals: [],
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useTaskUsageLogs: () => ({
    data: { results: [], count: 0 },
    isLoading: false,
    isFetching: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));
// Mirrors DataTable's empty overlay: a custom `emptyContent` node wins over
// the plain `emptyMessage` title.
vi.mock("src/components/data-table", () => ({
  DataTable: ({ data, emptyContent, emptyMessage }) =>
    data.length === 0 ? emptyContent ?? <div>{emptyMessage}</div> : null,
  DataTablePagination: () => null,
}));
vi.mock("src/sections/evals/components/UsageChart", () => ({
  default: () => <div data-testid="usage-chart" />,
}));
vi.mock("src/sections/projects/DateTimeRangePicker", () => ({
  default: () => null,
}));
vi.mock("@monaco-editor/react", () => ({ default: () => null }));

describe("TaskUsageTab with no successful runs in the period", () => {
  it("names the successful-runs scope instead of claiming the task never ran", () => {
    render(<TaskUsageTab taskId="task-1" onViewLogs={vi.fn()} />);

    expect(screen.getByText("Successful runs:")).toBeInTheDocument();
    expect(screen.queryByText("Runs:")).not.toBeInTheDocument();
    expect(screen.getByText("Successful evaluation runs")).toBeInTheDocument();
    expect(screen.queryByText("Evaluation Runs")).not.toBeInTheDocument();

    // Both the chart and the table empty states say what is missing.
    expect(
      screen.getAllByText("No successful evaluation runs in this period"),
    ).toHaveLength(2);
    expect(
      screen.queryByText("No data for this period"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("No evaluation runs for this period"),
    ).not.toBeInTheDocument();
  });

  it("points to the Logs tab, where failed runs and their errors are", () => {
    const onViewLogs = vi.fn();
    render(<TaskUsageTab taskId="task-1" onViewLogs={onViewLogs} />);

    expect(
      screen.getByText("Failed runs and their errors are in the Logs tab."),
    ).toBeInTheDocument();
    const viewLogs = screen.getByRole("button", { name: "View logs" });
    expect(viewLogs).toBeEnabled();

    fireEvent.click(viewLogs);
    expect(onViewLogs).toHaveBeenCalledTimes(1);
  });
});
