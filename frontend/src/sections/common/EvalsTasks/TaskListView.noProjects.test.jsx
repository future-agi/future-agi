/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TaskListView from "./TaskListView";

const axiosGetMock = vi.hoisted(() => vi.fn());
const auth = vi.hoisted(() => ({ role: "admin" }));

vi.mock("src/utils/axios", () => ({
  default: { get: axiosGetMock, post: vi.fn() },
  endpoints: {
    project: {
      getEvalTaskList: () => "/project-tasks",
      getEvalTasksWithProjectName: () => "/workspace-tasks",
      listProjects: () => "/projects",
      markEvalsDeleted: () => "/delete-tasks",
      pauseEvalTask: (taskId) => `/tasks/${taskId}/pause`,
      resumeEvalTask: (taskId) => `/tasks/${taskId}/resume`,
    },
  },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: auth.role }),
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
  DataTable: () => <div data-testid="task-table" />,
  DataTablePagination: () => null,
}));

vi.mock("./DeleteConfirmation", () => ({
  default: () => null,
}));

// The top bar keeps its own Create task button in the DOM, hidden with a
// style rule on first run. Asserting inside the empty state keeps that hidden
// copy out of every query below.
vi.mock("src/components/empty-state", () => ({
  default: ({
    title,
    description,
    action,
    secondaryAction,
    actionDisabledReason,
  }) => (
    <section data-testid="tasks-empty-state">
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
      {secondaryAction}
      {actionDisabledReason && <p>{actionDisabledReason}</p>}
    </section>
  ),
}));

const NO_PROJECT_REASON =
  "A task runs on an Observe project, and this workspace has none.";
const VIEWER_REASON =
  "Viewers can't create tasks. Ask a workspace admin for Member access.";

const emptyTaskPage = () => ({
  data: {
    status: true,
    result: { table: [], metadata: { total_rows: 0 } },
  },
});

const projectList = (projects) => ({ data: { result: { projects } } });

const serveRequests = ({ projects = [], projectsError = null } = {}) =>
  axiosGetMock.mockImplementation((url) => {
    if (url === "/projects") {
      return projectsError
        ? Promise.reject(projectsError)
        : Promise.resolve(projectList(projects));
    }
    return Promise.resolve(emptyTaskPage());
  });

const renderTaskList = (props = {}) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onCreateTask = vi.fn();
  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <TaskListView
          onCreateTask={onCreateTask}
          onRowClick={vi.fn()}
          {...props}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...rendered, queryClient, onCreateTask };
};

const projectListSettled = (queryClient) =>
  waitFor(() =>
    expect(queryClient.getQueryState(["project-list"])?.status).not.toBe(
      "pending",
    ),
  );

describe("TaskListView first run with no Observe projects", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
    auth.role = "admin";
  });

  it("offers Observe setup and shows Create task beside it, disabled with the reason, when the workspace has no Observe projects", async () => {
    serveRequests({ projects: [] });

    const { onCreateTask } = renderTaskList();

    const emptyState = await screen.findByTestId("tasks-empty-state");
    const setup = await within(emptyState).findByRole("link", {
      name: "Set up Observe",
    });
    expect(setup).toHaveAttribute("href", "/dashboard/observe");
    expect(emptyState).toHaveTextContent("No tasks yet");
    expect(emptyState).toHaveTextContent("send your first trace to create one");
    const create = within(emptyState).getByRole("button", {
      name: "Create task",
    });
    expect(create).toBeDisabled();
    expect(within(emptyState).getByText(NO_PROJECT_REASON)).toBeVisible();
    fireEvent.click(create);
    expect(onCreateTask).not.toHaveBeenCalled();
    expect(axiosGetMock).toHaveBeenCalledWith("/projects", {
      params: { project_type: "observe" },
    });
  });

  it("tells a viewer with no Observe projects both reasons Create task is disabled", async () => {
    auth.role = "Viewer";
    serveRequests({ projects: [] });

    renderTaskList();

    const emptyState = await screen.findByTestId("tasks-empty-state");
    await within(emptyState).findByRole("link", { name: "Set up Observe" });
    expect(
      within(emptyState).getByRole("button", { name: "Create task" }),
    ).toBeDisabled();
    expect(within(emptyState).getByText(NO_PROJECT_REASON)).toBeVisible();
    expect(within(emptyState).getByText(VIEWER_REASON)).toBeVisible();
  });

  it("keeps Create task when the workspace has Observe projects", async () => {
    serveRequests({ projects: [{ id: "project-a", name: "Checkout agent" }] });

    const { queryClient, onCreateTask } = renderTaskList();

    await screen.findByTestId("tasks-empty-state");
    await projectListSettled(queryClient);
    const emptyState = screen.getByTestId("tasks-empty-state");
    expect(
      within(emptyState).queryByRole("link", { name: "Set up Observe" }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      within(emptyState).getByRole("button", { name: "Create task" }),
    );
    expect(onCreateTask).toHaveBeenCalledTimes(1);
  });

  it("keeps Create task when the project list cannot be read", async () => {
    serveRequests({ projectsError: new Error("Network Error") });

    const { queryClient } = renderTaskList();

    await screen.findByTestId("tasks-empty-state");
    await projectListSettled(queryClient);
    const emptyState = screen.getByTestId("tasks-empty-state");
    expect(
      within(emptyState).getByRole("button", { name: "Create task" }),
    ).toBeInTheDocument();
    expect(
      within(emptyState).queryByRole("link", { name: "Set up Observe" }),
    ).not.toBeInTheDocument();
  });

  it("does not look up projects inside a project, where one already exists", async () => {
    serveRequests({ projects: [] });

    renderTaskList({ observeId: "project-a" });

    const emptyState = await screen.findByTestId("tasks-empty-state");
    expect(
      within(emptyState).getByRole("button", { name: "Create task" }),
    ).toBeInTheDocument();
    expect(
      within(emptyState).queryByRole("link", { name: "Set up Observe" }),
    ).not.toBeInTheDocument();
    expect(axiosGetMock.mock.calls.map(([url]) => url)).toEqual([
      "/project-tasks",
    ]);
  });
});
