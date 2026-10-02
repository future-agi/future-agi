/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TaskListView from "./TaskListView";

const axiosGetMock = vi.hoisted(() => vi.fn());
const axiosPostMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", () => ({
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

vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/components/iconify", () => ({
  default: () => null,
}));

vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: ({ searchQuery, onChange }) => (
    <input aria-label="Search tasks" value={searchQuery} onChange={onChange} />
  ),
}));

vi.mock("src/components/data-table", () => ({
  DataTable: ({
    data,
    rowCount,
    isLoading,
    onSortingChange,
    onRowSelectionChange,
  }) => (
    <div>
      <div data-testid="task-rows">
        {data.map((row) => (
          <span key={row.id}>{row.name}</span>
        ))}
      </div>
      <div data-testid="task-total">{rowCount}</div>
      <div data-testid="task-loading">{String(isLoading)}</div>
      <button
        type="button"
        onClick={() => onSortingChange([{ id: "name", desc: false }])}
      >
        Sort by name
      </button>
      {data.length > 0 && (
        <button type="button" onClick={() => onRowSelectionChange({ 0: true })}>
          Select first row
        </button>
      )}
    </div>
  ),
  DataTablePagination: ({ page, total, onPageChange }) => (
    <div>
      <div data-testid="task-page">{page}</div>
      <div data-testid="pagination-total">{total}</div>
      <button type="button" onClick={() => onPageChange(page + 1)}>
        Next page
      </button>
    </div>
  ),
}));

vi.mock("./DeleteConfirmation", () => ({
  default: ({ open, onConfirm }) =>
    open ? (
      <button type="button" onClick={onConfirm}>
        Confirm task deletion
      </button>
    ) : null,
}));

const task = (id, name) => ({
  id,
  name,
  status: "completed",
  sampling_rate: 100,
  evals_applied: [],
  filters_applied: {},
});

const taskPage = (table, totalRows) => ({
  data: {
    status: true,
    result: {
      table,
      metadata: { total_rows: totalRows },
    },
  },
});

const deferred = () => {
  let resolve;
  const promise = new Promise((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
};

const renderTaskList = (initialProps = {}) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  const baseProps = {
    onCreateTask: vi.fn(),
    onRowClick: vi.fn(),
  };
  const view = (props) => (
    <QueryClientProvider client={queryClient}>
      <TaskListView {...baseProps} {...props} />
    </QueryClientProvider>
  );
  const rendered = render(view(initialProps));
  return {
    ...rendered,
    queryClient,
    rerenderView: (props) => rendered.rerender(view(props)),
  };
};

const requestParams = (call) => axiosGetMock.mock.calls[call][1].params;

describe("TaskListView server pagination", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
    axiosPostMock.mockReset();
    axiosPostMock.mockResolvedValue({ data: { status: true } });
  });

  it("keeps the previous rows and total visible while the next page loads", async () => {
    const nextPage = deferred();
    axiosGetMock.mockImplementation((_url, { params }) => {
      if (params.page_number === 0) {
        return Promise.resolve(taskPage([task("task-0", "Task zero")], 30));
      }
      return nextPage.promise;
    });

    renderTaskList();

    expect(await screen.findByText("Task zero")).toBeInTheDocument();
    expect(requestParams(0).page_number).toBe(0);

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));

    await waitFor(() => expect(axiosGetMock).toHaveBeenCalledTimes(2));
    expect(requestParams(1).page_number).toBe(1);
    expect(screen.getByTestId("task-page")).toHaveTextContent("1");
    expect(screen.getByText("Task zero")).toBeInTheDocument();
    expect(screen.getByTestId("task-total")).toHaveTextContent("30");
    expect(screen.getByTestId("pagination-total")).toHaveTextContent("30");

    await act(async () => {
      nextPage.resolve(taskPage([task("task-25", "Task twenty-five")], 30));
      await nextPage.promise;
    });

    expect(await screen.findByText("Task twenty-five")).toBeInTheDocument();
    expect(screen.queryByText("Task zero")).not.toBeInTheDocument();
  });

  it("resets the zero-based backend page when sorting changes", async () => {
    axiosGetMock.mockImplementation((_url, { params }) =>
      Promise.resolve(
        taskPage(
          [
            task(
              `task-${params.page_number}`,
              `Task page ${params.page_number}`,
            ),
          ],
          30,
        ),
      ),
    );

    renderTaskList();
    expect(await screen.findByText("Task page 0")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("Task page 1")).toBeInTheDocument();
    expect(screen.getByTestId("task-page")).toHaveTextContent("1");

    fireEvent.click(screen.getByRole("button", { name: "Sort by name" }));

    await waitFor(() =>
      expect(screen.getByTestId("task-page")).toHaveTextContent("0"),
    );
    await waitFor(() => {
      const params = requestParams(axiosGetMock.mock.calls.length - 1);
      expect(params.page_number).toBe(0);
      expect(JSON.parse(params.sort_params)).toEqual([
        { column_id: "name", direction: "asc" },
      ]);
    });
  });

  it("resets to page zero when the project or workspace scope changes", async () => {
    axiosGetMock.mockImplementation((_url, { params }) =>
      Promise.resolve(
        taskPage(
          [
            task(
              `${params.project_id || "workspace"}-${params.page_number}`,
              "Task",
            ),
          ],
          30,
        ),
      ),
    );

    const { rerenderView } = renderTaskList({ observeId: "project-a" });
    await waitFor(() => expect(axiosGetMock).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() =>
      expect(screen.getByTestId("task-page")).toHaveTextContent("1"),
    );

    rerenderView({ observeId: "project-b" });

    await waitFor(() => {
      const projectBCalls = axiosGetMock.mock.calls.filter(
        ([url, config]) =>
          url === "/project-tasks" && config.params.project_id === "project-b",
      );
      expect(projectBCalls).toHaveLength(1);
      expect(projectBCalls[0][1].params.page_number).toBe(0);
    });
    expect(screen.getByTestId("task-page")).toHaveTextContent("0");

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() =>
      expect(screen.getByTestId("task-page")).toHaveTextContent("1"),
    );
    rerenderView({ observeId: null });

    await waitFor(() => {
      const workspaceCalls = axiosGetMock.mock.calls.filter(
        ([url]) => url === "/workspace-tasks",
      );
      expect(workspaceCalls).toHaveLength(1);
      expect(workspaceCalls[0][1].params.page_number).toBe(0);
    });
    expect(screen.getByTestId("task-page")).toHaveTextContent("0");
  });

  it("clears rows and totals while a new project scope is loading", async () => {
    const projectBPage = deferred();
    axiosGetMock.mockImplementation((_url, { params }) => {
      if (params.project_id === "project-a") {
        return Promise.resolve(
          taskPage([task("task-a", "Project A task")], 30),
        );
      }
      return projectBPage.promise;
    });

    const { rerenderView } = renderTaskList({ observeId: "project-a" });
    expect(await screen.findByText("Project A task")).toBeInTheDocument();
    expect(screen.getByTestId("task-total")).toHaveTextContent("30");

    rerenderView({ observeId: "project-b" });

    await waitFor(() =>
      expect(
        axiosGetMock.mock.calls.some(
          ([, config]) => config.params.project_id === "project-b",
        ),
      ).toBe(true),
    );
    expect(screen.queryByText("Project A task")).not.toBeInTheDocument();
    expect(screen.getByTestId("task-total")).toHaveTextContent("0");
    expect(screen.getByTestId("task-loading")).toHaveTextContent("true");

    await act(async () => {
      projectBPage.resolve(taskPage([task("task-b", "Project B task")], 1));
      await projectBPage.promise;
    });

    expect(await screen.findByText("Project B task")).toBeInTheDocument();
    expect(screen.getByTestId("task-total")).toHaveTextContent("1");
  });

  it("clamps to the prior page when deleting the final row on the last page", async () => {
    let deleted = false;
    axiosPostMock.mockImplementation(() => {
      deleted = true;
      return Promise.resolve({ data: { status: true } });
    });
    axiosGetMock.mockImplementation((_url, { params }) => {
      if (params.page_number === 1) {
        return Promise.resolve(
          deleted
            ? taskPage([], 25)
            : taskPage([task("task-final", "Final task")], 26),
        );
      }
      return Promise.resolve(
        taskPage([task("task-0", "Task zero")], deleted ? 25 : 26),
      );
    });

    renderTaskList();
    expect(await screen.findByText("Task zero")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("Final task")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Select first row" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm task deletion" }),
    );

    await waitFor(() =>
      expect(screen.getByTestId("task-page")).toHaveTextContent("0"),
    );
    expect(await screen.findByText("Task zero")).toBeInTheDocument();
    expect(screen.getByTestId("task-total")).toHaveTextContent("25");
    expect(axiosPostMock).toHaveBeenCalledWith("/delete-tasks", {
      eval_task_ids: ["task-final"],
    });
  });
});
