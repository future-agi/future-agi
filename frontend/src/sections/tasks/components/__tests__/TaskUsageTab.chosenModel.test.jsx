/* eslint-disable react/prop-types -- test doubles read only the props they render */
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import TaskUsageTab from "../TaskUsageTab";

const RUNS = vi.hoisted(() => [
  {
    id: "run-0",
    eval_name: "eval-other-model",
    status: "success",
    result: "Passed",
    score: 1,
    detail: { model: "gpt-4o", ran_on_label: "Falcon AI" },
  },
  {
    id: "run-1",
    eval_name: "eval-same-model",
    status: "success",
    result: "Passed",
    score: 1,
    detail: { model: "gpt-4o" },
  },
]);

vi.mock("../../hooks/useTaskUsage", () => ({
  useTaskUsageChart: () => ({
    data: { stats: {}, chart: [], evals: [] },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useTaskUsageLogs: () => ({
    data: { results: RUNS, count: RUNS.length },
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
  DataTablePagination: () => null,
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
vi.mock("@monaco-editor/react", () => ({
  default: () => <div data-testid="run-json" />,
}));

const openRun = (evalName) => {
  render(<TaskUsageTab taskId="task-1" projectId="project-1" />);
  fireEvent.click(screen.getByText(evalName));
};

// A run that ran on another model than the chosen one names both.
describe("TaskUsageTab run panel model", () => {
  it("shows the chosen model and the model the run ran on", () => {
    openRun("eval-other-model");

    expect(screen.getByText("Chosen: gpt-4o")).toBeInTheDocument();
    expect(screen.getByText("Ran on Falcon AI")).toBeInTheDocument();
  });

  it("shows only the model when the run names no other", () => {
    openRun("eval-same-model");

    expect(screen.getByText("gpt-4o")).toBeInTheDocument();
    expect(screen.queryByText(/Chosen/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Ran on/)).not.toBeInTheDocument();
  });
});
