/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TaskListView from "./TaskListView";

const axiosGetMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", () => ({
  default: { get: axiosGetMock, post: vi.fn() },
  endpoints: {
    project: {
      getEvalTaskList: () => "/project-tasks",
      getEvalTasksWithProjectName: () => "/workspace-tasks",
      markEvalsDeleted: () => "/delete-tasks",
      pauseEvalTask: (taskId) => `/tasks/${taskId}/pause`,
      resumeEvalTask: (taskId) => `/tasks/${taskId}/resume`,
    },
  },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "admin" }),
}));

vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { ADD_TASKS_ALERTS: "add-tasks-alerts" },
  RolePermission: {
    OBSERVABILITY: { "add-tasks-alerts": { admin: true } },
  },
}));

vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="icon">{icon}</span>,
}));

vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: ({ searchQuery, onChange }) => (
    <input aria-label="Search tasks" value={searchQuery} onChange={onChange} />
  ),
}));

// Renders each row's real Status cell and exposes the column's width.
vi.mock("src/components/data-table", () => ({
  DataTable: ({ columns, data }) => {
    const statusColumn = columns.find((column) => column.id === "status");
    return (
      <div data-testid="status-width" data-width={statusColumn.size}>
        {data.map((row) => (
          <div key={row.id} data-testid={`row-${row.id}`}>
            {statusColumn.cell({
              getValue: () => row.status,
              row: { original: row },
            })}
          </div>
        ))}
      </div>
    );
  },
  DataTablePagination: () => null,
}));

vi.mock("./DeleteConfirmation", () => ({ default: () => null }));

const task = (id, status, progress) => ({
  id,
  name: `Task ${id}`,
  status,
  run_type: "historical",
  sampling_rate: 100,
  evals_applied: [],
  filters_applied: {},
  ...(progress !== undefined && { progress }),
});

const taskPage = (table) => ({
  data: {
    status: true,
    result: { table, metadata: { total_rows: table.length } },
  },
});

const renderTaskList = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <TaskListView onCreateTask={vi.fn()} onRowClick={vi.fn()} />
    </QueryClientProvider>,
  );

describe("TaskListView completed-run hint", () => {
  beforeEach(() => axiosGetMock.mockReset());

  it("says next to Completed when a run evaluated nothing or skipped runs", async () => {
    axiosGetMock.mockResolvedValue(
      taskPage([
        task("empty", "completed", {
          dispatched: 0,
          completed: 0,
          skipped: 0,
          missing: 0,
          percent: null,
        }),
        task("skipped", "completed", {
          dispatched: 160,
          completed: 0,
          skipped: 160,
          missing: 0,
          percent: 0,
        }),
        task("clean", "completed", {
          dispatched: 10,
          completed: 10,
          skipped: 0,
          missing: 0,
          percent: 100,
        }),
      ]),
    );
    renderTaskList();

    const empty = await screen.findByTestId("row-empty");
    expect(within(empty).getByText("Completed")).toBeInTheDocument();
    expect(within(empty).getByText("Nothing evaluated")).toBeInTheDocument();
    const skipped = screen.getByTestId("row-skipped");
    expect(within(skipped).getByText("All 160 skipped")).toBeInTheDocument();
    expect(
      within(skipped).getByTitle(
        "Nothing was evaluated: all 160 evaluation runs were skipped.",
      ),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("row-clean")).queryByText(/skipped|evaluated/),
    ).toBeNull();
    expect(screen.getByTestId("status-width")).toHaveAttribute(
      "data-width",
      "260",
    );
  });

  it("keeps today's Status column when rows carry no progress", async () => {
    axiosGetMock.mockResolvedValue(
      taskPage([task("legacy", "completed"), task("live", "running", null)]),
    );
    renderTaskList();

    const legacy = await screen.findByTestId("row-legacy");
    expect(within(legacy).getByText("Completed")).toBeInTheDocument();
    expect(within(legacy).queryByText(/skipped|evaluated/)).toBeNull();
    expect(screen.getByTestId("status-width")).toHaveAttribute(
      "data-width",
      "140",
    );
  });
});
