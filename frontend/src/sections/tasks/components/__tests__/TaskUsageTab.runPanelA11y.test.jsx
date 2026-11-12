/* eslint-disable react/prop-types */
import React from "react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "src/utils/test-utils";
import TaskUsageTab from "../TaskUsageTab";

const RUNS = vi.hoisted(() =>
  [
    {
      id: "run-0",
      eval_name: "eval-0",
      trace_id: "trace-A",
      span_id: "span-A",
    },
    {
      id: "run-1",
      eval_name: "eval-1",
      trace_id: "trace-B",
      span_id: "span-B",
    },
  ].map((run) => ({
    status: "success",
    result: "Passed",
    score: 1,
    ...run,
    detail: { trace_id: run.trace_id, span_id: run.span_id },
  })),
);

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

const renderTab = () =>
  render(<TaskUsageTab taskId="task-1" projectId="project-1" />);
const openRun = (evalName) => fireEvent.click(screen.getByText(evalName));

// W10-SETTINGS-TASKS-U09: the run panel is usable without a mouse or sight.
describe("TaskUsageTab run panel accessibility", () => {
  it("names the previous, next and close buttons", () => {
    renderTab();
    openRun("eval-0");

    expect(screen.getByRole("button", { name: "Previous run" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Next run" }));
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next run" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Previous run" }));
    expect(screen.getByText("1 / 2")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close run details" }));
    expect(screen.queryByText("1 / 2")).not.toBeInTheDocument();
  });

  it("switches between Formatted and JSON with the keyboard", async () => {
    const user = userEvent.setup();
    renderTab();
    openRun("eval-0");

    const view = within(screen.getByRole("group", { name: "Run detail view" }));
    const formatted = view.getByRole("button", { name: "Formatted" });
    const json = view.getByRole("button", { name: "JSON" });
    expect(formatted).toHaveAttribute("aria-pressed", "true");
    expect(json).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByTestId("run-json")).not.toBeInTheDocument();

    act(() => json.focus());
    expect(json).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(json).toHaveAttribute("aria-pressed", "true");
    expect(formatted).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("run-json")).toBeInTheDocument();

    act(() => formatted.focus());
    await user.keyboard(" ");
    expect(formatted).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByTestId("run-json")).not.toBeInTheDocument();
  });
});
