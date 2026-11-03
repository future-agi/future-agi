/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { PROJECT_SOURCE } from "src/utils/constants";
import TaskUsageTab from "../TaskUsageTab";

// Two evals score trace A, one run is session-level, one scores trace B.
const RUNS = [
  { id: "run-0", eval_name: "eval-0", trace_id: "trace-A", span_id: "span-A0" },
  { id: "run-1", eval_name: "eval-1", trace_id: "trace-A", span_id: "span-A1" },
  {
    id: "run-2",
    eval_name: "eval-2",
    trace_id: null,
    detail: { target_type: "session", session_id: "session-1" },
  },
  { id: "run-3", eval_name: "eval-3", trace_id: "trace-B", span_id: "span-B3" },
].map((run) => ({
  status: "success",
  result: "Passed",
  score: 1,
  ...run,
  detail: { trace_id: run.trace_id, span_id: run.span_id, ...run.detail },
}));

const copyToClipboard = vi.hoisted(() => vi.fn(() => Promise.resolve(true)));
const enqueueSnackbar = vi.hoisted(() => vi.fn());
const projectSource = vi.hoisted(() => ({ current: "prototype" }));
const traceDrawerMounts = vi.hoisted(() => ({ count: 0 }));
// What the runs query currently returns; tests swap it to simulate refetches.
const logs = vi.hoisted(() => ({ current: [] }));

vi.mock("../../hooks/useTaskUsage", () => ({
  useTaskUsageChart: () => ({
    data: { stats: {}, chart: [], evals: [] },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useTaskUsageLogs: () => ({
    data: { results: logs.current, count: logs.current.length },
    isLoading: false,
    isFetching: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));
vi.mock("src/components/data-table", () => ({
  DataTable: ({ data, onRowClick, activeRowId }) =>
    data.map((row) => (
      <button
        key={row.id}
        type="button"
        aria-current={row.id === activeRowId || undefined}
        onClick={() => onRowClick(row)}
      >
        {row.eval_name}
      </button>
    )),
  DataTablePagination: ({ onPageChange, onPageSizeChange }) => (
    <>
      <button type="button" onClick={() => onPageChange(1)}>
        Next page
      </button>
      <button type="button" onClick={() => onPageSizeChange(25)}>
        Page size
      </button>
    </>
  ),
}));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({
    data: { source: projectSource.current },
    isLoading: false,
  }),
}));
vi.mock("src/sections/agents/helper", () => ({
  useVoiceCallDetail: () => ({ data: undefined, isFetching: false }),
}));
// Stub the trace body but keep the real DrawerHeader: it owns the drawer's
// j/k/Esc handling, which is what the keyboard hand-off tests exercise.
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", async () => {
  const { useEffect } = await import("react");
  const { default: DrawerHeader } = await import(
    "src/components/traceDetail/DrawerHeader"
  );
  const TraceDetailDrawerStub = ({ open, traceId, initialSpanId, ...rest }) => {
    useEffect(() => {
      traceDrawerMounts.count += 1;
    }, []);
    return open ? (
      <div
        data-testid="trace-drawer"
        data-trace-id={traceId}
        data-span-id={initialSpanId}
        data-project-id={rest.projectId}
      >
        <DrawerHeader open traceId={traceId} {...rest} />
      </div>
    ) : null;
  };
  return { default: TraceDetailDrawerStub };
});
// Like the real voice call drawer, the stub has no Esc handling of its own.
vi.mock("src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2", () => ({
  default: ({ data }) => (
    <div data-testid="voice-drawer" data-trace-id={data.trace_id} />
  ),
}));
vi.mock("src/utils/utils", async (importOriginal) => ({
  ...(await importOriginal()),
  copyToClipboard,
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar }));
vi.mock("src/sections/evals/components/UsageChart", () => ({
  default: () => null,
}));
vi.mock("src/sections/projects/DateTimeRangePicker", () => ({
  default: ({ setDateOption, setParentDateFilter }) => (
    <>
      <button type="button" onClick={() => setDateOption("7D")}>
        Date preset
      </button>
      <button
        type="button"
        onClick={() => setParentDateFilter(["2026-10-01", "2026-10-02"])}
      >
        Date range
      </button>
    </>
  ),
}));
vi.mock("@monaco-editor/react", () => ({ default: () => null }));

const tab = (props = {}) => (
  <TaskUsageTab taskId="task-1" projectId="project-1" {...props} />
);
const renderTab = (props) => render(tab(props));
const openRun = (evalName) => fireEvent.click(screen.getByText(evalName));
const openTrace = () =>
  fireEvent.click(screen.getByRole("button", { name: /open trace/i }));
const press = (key) => fireEvent.keyDown(document.body, { key });
const traceDrawer = () => screen.queryByTestId("trace-drawer");
const activeRow = () =>
  screen.queryAllByRole("button", { current: true }).map((b) => b.textContent);
const runPosition = (position) =>
  screen.getByText(`${position} / ${RUNS.length}`);

describe("TaskUsageTab trace drawer", () => {
  beforeEach(() => {
    copyToClipboard.mockClear();
    enqueueSnackbar.mockClear();
    projectSource.current = "prototype";
    traceDrawerMounts.count = 0;
    logs.current = RUNS;
  });

  it("opens the run's trace on its evaluated span without leaving Usage", () => {
    renderTab();
    openRun("eval-1");
    openTrace();

    expect(traceDrawer()).toHaveAttribute("data-trace-id", "trace-A");
    expect(traceDrawer()).toHaveAttribute("data-span-id", "span-A1");
    expect(traceDrawer()).toHaveAttribute("data-project-id", "project-1");
    // The run panel and table stay mounted behind the drawer.
    expect(runPosition(2)).toBeInTheDocument();
    expect(screen.getByText("eval-0")).toBeInTheDocument();
  });

  it("offers no Open trace for session-level runs or without a project", () => {
    const { unmount } = renderTab();
    openRun("eval-2");
    expect(
      screen.queryByRole("button", { name: /open trace/i }),
    ).not.toBeInTheDocument();
    unmount();

    renderTab({ projectId: undefined });
    openRun("eval-0");
    expect(
      screen.queryByRole("button", { name: /open trace/i }),
    ).not.toBeInTheDocument();
  });

  it("j/k in the drawer move across the task's traces, and the run panel follows", () => {
    renderTab();
    openRun("eval-0");
    openTrace();

    // run-1 scores the same trace and run-2 has none, so j lands on trace B.
    press("j");
    expect(traceDrawer()).toHaveAttribute("data-trace-id", "trace-B");
    expect(traceDrawer()).toHaveAttribute("data-span-id", "span-B3");
    expect(runPosition(4)).toBeInTheDocument();

    press("k");
    expect(traceDrawer()).toHaveAttribute("data-trace-id", "trace-A");
    expect(runPosition(2)).toBeInTheDocument();
  });

  it("highlights the table row of the run being shown as j/k move", () => {
    renderTab();
    openRun("eval-0");
    expect(activeRow()).toEqual(["eval-0"]);
    openTrace();

    press("j");
    expect(activeRow()).toEqual(["eval-3"]);
    press("Escape");
    expect(activeRow()).toEqual(["eval-3"]);
    press("Escape");
    expect(activeRow()).toEqual([]);
  });

  it("j/k with no further trace leave both the drawer and the run panel in place", () => {
    renderTab();
    // run-0 scores the same trace, so the drawer has no previous trace even
    // though the run panel alone would step back to it.
    openRun("eval-1");
    openTrace();

    press("k");
    expect(traceDrawer()).toHaveAttribute("data-trace-id", "trace-A");
    expect(traceDrawer()).toHaveAttribute("data-span-id", "span-A1");
    expect(runPosition(2)).toBeInTheDocument();
  });

  it("Esc closes only the trace drawer, then the run panel", () => {
    renderTab();
    openRun("eval-3");
    openTrace();

    press("Escape");
    expect(traceDrawer()).not.toBeInTheDocument();
    expect(runPosition(4)).toBeInTheDocument();

    press("Escape");
    expect(screen.queryByText(`4 / ${RUNS.length}`)).not.toBeInTheDocument();
  });

  it("closing the drawer returns to the run of the trace it was showing", () => {
    renderTab();
    openRun("eval-0");
    openTrace();
    press("j");

    fireEvent.click(screen.getByRole("button", { name: "Close (Esc)" }));
    expect(traceDrawer()).not.toBeInTheDocument();
    expect(runPosition(4)).toBeInTheDocument();
  });

  it("does not reopen a stale drawer after the run panel closes", () => {
    renderTab();
    openRun("eval-0");
    openTrace();
    press("Escape");
    press("Escape");

    openRun("eval-3");
    expect(traceDrawer()).not.toBeInTheDocument();
  });

  it("clicking another row while the drawer is open shows that run's trace", () => {
    renderTab();
    openRun("eval-3");
    openTrace();

    openRun("eval-0");
    expect(traceDrawer()).toHaveAttribute("data-trace-id", "trace-A");
    expect(runPosition(1)).toBeInTheDocument();
  });

  it("keeps j/k/Esc on the run panel when the trace drawer is closed", () => {
    renderTab();
    openRun("eval-0");

    press("j");
    expect(runPosition(2)).toBeInTheDocument();
    press("k");
    expect(runPosition(1)).toBeInTheDocument();
    press("Escape");
    expect(screen.queryByText(`1 / ${RUNS.length}`)).not.toBeInTheDocument();
  });

  it("landing on a run without a trace ends the trace view", () => {
    renderTab();
    openRun("eval-3");
    openTrace();

    openRun("eval-2");
    expect(traceDrawer()).not.toBeInTheDocument();
    // Back on a run with a trace, the drawer stays shut until asked.
    press("j");
    expect(runPosition(4)).toBeInTheDocument();
    expect(traceDrawer()).not.toBeInTheDocument();
  });

  it.each(["Date preset", "Date range", "Next page", "Page size"])(
    "%s change closes the drawer and the run panel",
    (control) => {
      renderTab();
      openRun("eval-3");
      openTrace();

      fireEvent.click(screen.getByRole("button", { name: control }));
      expect(traceDrawer()).not.toBeInTheDocument();
      expect(screen.queryByText(`4 / ${RUNS.length}`)).not.toBeInTheDocument();
    },
  );

  it("new runs arriving keep the panel and drawer on the same run", () => {
    const { rerender } = renderTab();
    openRun("eval-3");
    openTrace();

    // A continuous task's refetch brings newer runs in at the top.
    logs.current = [
      {
        ...RUNS[3],
        id: "run-new",
        eval_name: "eval-new",
        trace_id: "trace-C",
        span_id: "span-C",
      },
      ...RUNS,
    ];
    rerender(tab());

    expect(traceDrawer()).toHaveAttribute("data-trace-id", "trace-B");
    expect(traceDrawer()).toHaveAttribute("data-span-id", "span-B3");
    expect(screen.getByText("5 / 5")).toBeInTheDocument();

    // Navigation follows the new order: k steps back through trace A.
    press("k");
    expect(traceDrawer()).toHaveAttribute("data-trace-id", "trace-A");
    expect(screen.getByText("3 / 5")).toBeInTheDocument();
  });

  it("closes the panel and drawer when the open run drops off the page", () => {
    const { rerender } = renderTab();
    openRun("eval-3");
    openTrace();

    logs.current = RUNS.slice(0, 3);
    rerender(tab());
    expect(traceDrawer()).not.toBeInTheDocument();
    expect(screen.queryByText(/\d+ \/ 3/)).not.toBeInTheDocument();

    // It stays closed if the run comes back.
    logs.current = RUNS;
    rerender(tab());
    expect(screen.queryByText(`4 / ${RUNS.length}`)).not.toBeInTheDocument();
  });

  it("each Open trace starts a fresh drawer on the evaluated span", () => {
    renderTab();
    openRun("eval-0");
    openTrace();
    press("Escape");
    openTrace();

    expect(traceDrawerMounts.count).toBe(2);
    expect(traceDrawer()).toHaveAttribute("data-span-id", "span-A0");
  });

  it("Esc closes the voice call drawer, then the run panel", () => {
    projectSource.current = PROJECT_SOURCE.SIMULATOR;
    renderTab();
    openRun("eval-0");
    openTrace();
    expect(screen.getByTestId("voice-drawer")).toHaveAttribute(
      "data-trace-id",
      "trace-A",
    );

    press("Escape");
    expect(screen.queryByTestId("voice-drawer")).not.toBeInTheDocument();
    expect(runPosition(1)).toBeInTheDocument();

    press("Escape");
    expect(screen.queryByText(`1 / ${RUNS.length}`)).not.toBeInTheDocument();
  });

  it("copies the raw trace ID and confirms with a toast", async () => {
    renderTab();
    openRun("eval-0");
    fireEvent.click(screen.getByRole("button", { name: "Copy Trace ID" }));

    expect(copyToClipboard).toHaveBeenCalledWith("trace-A");
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Trace ID copied",
        expect.objectContaining({ variant: "info" }),
      ),
    );
  });
});
