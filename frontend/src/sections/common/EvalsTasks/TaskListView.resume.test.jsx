/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TaskListView from "./TaskListView";

const axiosGetMock = vi.hoisted(() => vi.fn());
const axiosPostMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return {
    default: {
      get: axiosGetMock,
      post: axiosPostMock,
    },
    endpoints: {
      project: {
        getEvalTaskList: () => "/project-tasks",
        getEvalTasksWithProjectName: () => "/workspace-tasks",
        markEvalsDeleted: () => "/delete-tasks",
        pauseEvalTask: (taskId) => `/tasks/${taskId}/pause`,
        // The real mapping, so each click below is checked against the URL
        // the backend serves rather than a stand-in.
        resumeEvalTask: actual.endpoints.project.resumeEvalTask,
      },
    },
  };
});

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "admin" }),
}));

vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { ADD_TASKS_ALERTS: "add-tasks-alerts" },
  RolePermission: {
    OBSERVABILITY: { "add-tasks-alerts": { admin: true } },
  },
}));

vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="icon">{icon}</span>,
}));

vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: ({ searchQuery, onChange }) => (
    <input aria-label="Search tasks" value={searchQuery} onChange={onChange} />
  ),
}));

// Renders each row's real Status cell so the row actions are exercised.
vi.mock("src/components/data-table", () => ({
  DataTable: ({ columns, data }) => {
    const statusColumn = columns.find((column) => column.id === "status");
    return (
      <div>
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

vi.mock("./DeleteConfirmation", () => ({
  default: () => null,
}));

const RESUME_ICON = "solar:play-circle-linear";

const task = (id, status) => ({
  id,
  name: `Task ${id}`,
  status,
  sampling_rate: 100,
  evals_applied: [],
  filters_applied: {},
});

const taskPage = (table) => ({
  data: {
    status: true,
    result: { table, metadata: { total_rows: table.length } },
  },
});

const renderTaskList = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TaskListView onCreateTask={vi.fn()} onRowClick={vi.fn()} />
    </QueryClientProvider>,
  );
};

const resumeButtonIn = (row) =>
  Array.from(row.querySelectorAll("button")).find((button) =>
    button.textContent.includes(RESUME_ICON),
  );

describe("TaskListView resume action", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
    axiosPostMock.mockReset();
    axiosPostMock.mockResolvedValue({ data: { status: true } });
  });

  it("offers Resume on a failed task and resumes it by id", async () => {
    axiosGetMock.mockResolvedValue(taskPage([task("task-failed", "failed")]));

    renderTaskList();

    const row = await screen.findByTestId("row-task-failed");
    const resume = resumeButtonIn(row);
    expect(resume).toBeDefined();

    fireEvent.click(resume);

    await waitFor(() =>
      expect(axiosPostMock).toHaveBeenCalledWith(
        "/tracer/eval-task/unpause_eval_task/?eval_task_id=task-failed",
        {},
      ),
    );
  });

  it("still offers Resume on a paused task", async () => {
    axiosGetMock.mockResolvedValue(taskPage([task("task-paused", "paused")]));

    renderTaskList();

    const row = await screen.findByTestId("row-task-paused");
    fireEvent.click(resumeButtonIn(row));

    await waitFor(() =>
      expect(axiosPostMock).toHaveBeenCalledWith(
        "/tracer/eval-task/unpause_eval_task/?eval_task_id=task-paused",
        {},
      ),
    );
  });

  it("offers no Resume on completed, running or pending tasks", async () => {
    axiosGetMock.mockResolvedValue(
      taskPage([
        task("task-completed", "completed"),
        task("task-running", "running"),
        task("task-pending", "pending"),
      ]),
    );

    renderTaskList();

    for (const id of ["task-completed", "task-running", "task-pending"]) {
      const row = await screen.findByTestId(`row-${id}`);
      expect(resumeButtonIn(row)).toBeUndefined();
    }
    expect(axiosPostMock).not.toHaveBeenCalled();
  });
});
