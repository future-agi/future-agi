/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TaskListView from "./TaskListView";

const axiosGetMock = vi.hoisted(() => vi.fn());
const auth = vi.hoisted(() => ({ role: "Viewer" }));

vi.mock("src/utils/axios", () => ({
  default: {
    get: axiosGetMock,
    post: vi.fn(),
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
  useAuthContext: () => ({ role: auth.role }),
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

const emptyTaskPage = {
  data: {
    status: true,
    result: {
      table: [],
      metadata: { total_rows: 0 },
    },
  },
};

const REASON =
  "Viewers can't create tasks. Ask a workspace admin for Member access.";

const renderTaskList = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TaskListView onCreateTask={vi.fn()} onRowClick={vi.fn()} />
    </QueryClientProvider>,
  );
};

const createTaskButtons = () =>
  screen.getAllByRole("button", { name: "Create task", hidden: true });

describe("TaskListView first run — view-only teammates (W10-DESIGN-SYSTEM-U03)", () => {
  beforeEach(() => {
    auth.role = "Viewer";
    axiosGetMock.mockReset();
    axiosGetMock.mockResolvedValue(emptyTaskPage);
  });

  it.each(["Viewer", "workspace_viewer"])(
    "tells a %s why Create task is disabled",
    async (role) => {
      auth.role = role;
      renderTaskList();

      expect(
        await screen.findByRole("heading", { name: "No tasks yet" }),
      ).toBeInTheDocument();
      expect(screen.getByText(REASON)).toBeInTheDocument();
      createTaskButtons().forEach((button) => expect(button).toBeDisabled());
    },
  );

  it("shows no reason when the teammate can create tasks", async () => {
    auth.role = "Member";
    renderTaskList();

    expect(
      await screen.findByRole("heading", { name: "No tasks yet" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/can't create tasks/)).not.toBeInTheDocument();
    createTaskButtons().forEach((button) => expect(button).toBeEnabled());
  });
});
