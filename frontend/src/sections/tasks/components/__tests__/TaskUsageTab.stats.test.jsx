import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import TaskUsageTab from "../TaskUsageTab";

vi.mock("../../hooks/useTaskUsage", () => ({
  useTaskUsageChart: () => ({
    data: {
      stats: {
        total_runs: 28,
        runs_period: 28,
        success_count: 28,
        error_count: 0,
        pass_rate: 100,
      },
      chart: [{ timestamp: "2026-09-25T00:00:00Z", calls: 28 }],
      evals: [],
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useTaskUsageLogs: () => ({
    data: { results: [], count: 28 },
    isLoading: false,
    isFetching: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));
vi.mock("src/sections/evals/components/UsageChart", () => ({
  default: () => <div data-testid="usage-chart" />,
}));
vi.mock("src/sections/projects/DateTimeRangePicker", () => ({
  default: () => null,
}));
vi.mock("@monaco-editor/react", () => ({ default: () => null }));

describe("TaskUsageTab stats", () => {
  it("summarizes successful runs only, without an error count or completion rate", () => {
    render(<TaskUsageTab taskId="task-1" />);

    expect(screen.getByText("Runs:")).toBeInTheDocument();
    expect(screen.getByText("Success:")).toBeInTheDocument();
    expect(screen.queryByText("Errors:")).not.toBeInTheDocument();
    expect(screen.queryByText(/completion rate/i)).not.toBeInTheDocument();
  });
});
