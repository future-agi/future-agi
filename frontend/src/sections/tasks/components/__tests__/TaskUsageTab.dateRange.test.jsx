import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import TaskUsageTab from "../TaskUsageTab";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  chart: vi.fn(() => null),
  table: vi.fn(() => null),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    project: { getEvalTaskUsage: () => "/tracer/eval-task/get_usage/" },
  },
}));
vi.mock("src/sections/evals/components/UsageChart", () => ({
  default: mocks.chart,
}));
vi.mock("src/components/data-table", () => ({
  DataTable: mocks.table,
  DataTablePagination: () => null,
}));
vi.mock("../TaskRunTraceDrawer", () => ({ default: () => null }));
vi.mock("@monaco-editor/react", () => ({ default: () => null }));
vi.mock("src/sections/evals/components/DatasetTestMode", () => ({
  JsonValueTree: () => null,
}));
vi.mock("src/sections/develop-detail/DataTab/common", () => ({
  parsePythonReprIfNeeded: (value) => value,
}));

// Keep the actual date pickers and usage hooks: this regression is in the
// caller's interpretation of the picker's half-open range, not in either alone.
describe("TaskUsageTab custom date boundaries", () => {
  let queryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
  });

  afterEach(() => queryClient.clear());

  it.each([
    ["2026-11-01", 1],
    ["2026-11-02", 2],
  ])(
    "W10-SETTINGS-TASKS-L01: keeps %s to one local calendar day in both requests",
    async (selectedDate, day) => {
      // Local calendar midnights also cover the 25-hour November 1 day when
      // the test process runs in America/Los_Angeles (no fixed 24-hour math).
      const start = new Date(2026, 10, day);
      const end = new Date(2026, 10, day + 1);
      const runs = [
        { id: "before-start", created_at: new Date(+start - 1) },
        { id: "at-start", created_at: start },
        { id: "before-end", created_at: new Date(+end - 1) },
        { id: "at-end", created_at: end },
        { id: "after-end", created_at: new Date(+end + 1) },
      ].map((run) => ({ ...run, status: "success" }));

      mocks.get.mockImplementation(async (_url, { params }) => {
        // Model the endpoint's created_at >= start AND created_at < end filter.
        const selected = params.start_date
          ? runs.filter(
              (run) =>
                run.created_at >= new Date(params.start_date) &&
                run.created_at < new Date(params.end_date),
            )
          : runs;
        return {
          data: {
            result: {
              stats: { runs_period: selected.length },
              chart: [
                { timestamp: start.toISOString(), calls: selected.length },
              ],
              evals: [],
              logs: {
                results: selected,
                count: selected.length,
                has_more: false,
              },
            },
          },
        };
      });

      render(
        <QueryClientProvider client={queryClient}>
          <TaskUsageTab taskId="task-1" />
        </QueryClientProvider>,
      );
      await waitFor(() =>
        expect(
          screen.getByText("Successful runs:").parentElement,
        ).toHaveTextContent(/Successful runs:\s*5$/),
      );

      fireEvent.click(
        screen.getByRole("button", { name: "Custom", exact: true }),
      );
      // Clear the seeded end so input normalization cannot swap in today's date.
      fireEvent.change(screen.getByLabelText("End Date"), {
        target: { value: "" },
      });
      fireEvent.change(screen.getByLabelText("Start Date"), {
        target: { value: selectedDate },
      });
      fireEvent.change(screen.getByLabelText("End Date"), {
        target: { value: selectedDate },
      });
      fireEvent.click(screen.getByRole("button", { name: "Done" }));

      const customRequests = () =>
        mocks.get.mock.calls
          .map(([, options]) => options.params)
          .filter((params) => params.start_date);
      await waitFor(() => expect(customRequests()).toHaveLength(2));
      expect(customRequests()).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            eval_task_id: "task-1",
            start_date: start.toISOString(),
            end_date: end.toISOString(),
            page: 1,
            page_size: 1,
          }),
          expect.objectContaining({
            eval_task_id: "task-1",
            start_date: start.toISOString(),
            end_date: end.toISOString(),
            page: 1,
            page_size: 50,
            include_summary: false,
          }),
        ]),
      );
      await waitFor(() => {
        expect(
          screen.getByText("Successful runs:").parentElement,
        ).toHaveTextContent(/Successful runs:\s*2$/);
        expect(mocks.chart.mock.lastCall[0].data).toEqual([
          { timestamp: start.toISOString(), calls: 2 },
        ]);
        expect(mocks.table.mock.lastCall[0].data.map((run) => run.id)).toEqual([
          "at-start",
          "before-end",
        ]);
      });

      // Returning to a preset must drop the explicit range for both queries.
      mocks.get.mockClear();
      fireEvent.click(screen.getByRole("button", { name: "7D", exact: true }));
      await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
      for (const [, { params }] of mocks.get.mock.calls) {
        expect(params.period).toBe("7d");
        expect(params).not.toHaveProperty("start_date");
        expect(params).not.toHaveProperty("end_date");
      }
    },
  );
});
