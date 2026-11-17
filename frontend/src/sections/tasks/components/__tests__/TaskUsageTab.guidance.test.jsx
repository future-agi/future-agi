/* eslint-disable react/prop-types */
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import TaskUsageTab from "../TaskUsageTab";

// The usage list stops paging at the server's page cap and trims large run
// details. Both states must tell the user what is missing and what to do,
// in plain words rather than paging internals.
const fixtures = vi.hoisted(() => ({
  // Simulates a server whose page cap is the second page (1-indexed page 2).
  capPageIndex: 1,
  runs: [
    {
      id: "run-trace",
      eval_name: "eval-trace",
      trace_id: "trace-A",
      span_id: "span-A",
    },
    {
      id: "run-session",
      eval_name: "eval-session",
      trace_id: null,
      detail: { target_type: "session", session_id: "session-1" },
    },
  ].map((run) => ({
    status: "success",
    result: "Passed",
    score: 1,
    ...run,
    detail: {
      trace_id: run.trace_id,
      span_id: run.span_id,
      detail_complete: false,
      omitted_fields: ["reason_tail"],
      ...run.detail,
    },
  })),
}));

vi.mock("../../hooks/useTaskUsage", () => ({
  useTaskUsageChart: () => ({
    data: { stats: {}, chart: [], evals: [] },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useTaskUsageLogs: (_taskId, { page }) => ({
    data: {
      results: fixtures.runs,
      count: 10_000,
      count_is_lower_bound: true,
      has_more: page !== fixtures.capPageIndex,
      page_limit_reached: page === fixtures.capPageIndex,
    },
    isLoading: false,
    isFetching: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));
vi.mock("src/components/data-table", () => ({
  DataTable: ({ data, onRowClick }) =>
    data.map((row) => (
      <button key={row.id} type="button" onClick={() => onRowClick(row)}>
        {row.eval_name}
      </button>
    )),
  DataTablePagination: ({ onPageChange }) => (
    <button type="button" onClick={() => onPageChange(fixtures.capPageIndex)}>
      Next page
    </button>
  ),
}));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({
    data: { source: "prototype" },
    isLoading: false,
  }),
}));
vi.mock("src/sections/agents/helper", () => ({
  useVoiceCallDetail: () => ({ data: undefined, isFetching: false }),
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/sections/evals/components/UsageChart", () => ({
  default: () => null,
}));
vi.mock("src/sections/projects/DateTimeRangePicker", () => ({
  default: () => null,
}));
vi.mock("@monaco-editor/react", () => ({ default: () => null }));

const renderTab = () =>
  render(<TaskUsageTab taskId="task-1" projectId="project-1" />);

describe("TaskUsageTab guidance copy", () => {
  it("explains the page cap with the real run count and what to change", () => {
    renderTab();
    expect(
      screen.queryByText(/showing the most recent/i),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));

    // Two pages of the default 50 rows were browsed when the cap was hit.
    expect(
      screen.getByText(
        "Showing the most recent 100 runs. Narrow the date range or pick one evaluation to see older runs.",
      ),
    ).toBeVisible();
    expect(screen.queryByText(/bounded/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/browsing window/i)).not.toBeInTheDocument();
  });

  it("points a trimmed trace run to the full trace", () => {
    renderTab();
    fireEvent.click(screen.getByText("eval-trace"));

    expect(
      screen.getByText(
        "Some large fields are not shown here. Open the trace to see the full span.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /open trace/i }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/bounded/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/list response/i)).not.toBeInTheDocument();
  });

  it("does not point a trimmed session run to a trace it cannot open", () => {
    renderTab();
    fireEvent.click(screen.getByText("eval-session"));

    expect(
      screen.getByText("Some large fields are not shown here."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /open trace/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/open the trace/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/bounded/i)).not.toBeInTheDocument();
  });
});
