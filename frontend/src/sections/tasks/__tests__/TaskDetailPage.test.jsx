import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TaskDetailPage from "../TaskDetailPage";
import { useGetTaskData } from "src/sections/common/EvalsTasks/common";

const axiosPatchMock = vi.hoisted(() => vi.fn());
const axiosPostMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", () => ({
  default: {
    patch: axiosPatchMock,
    post: axiosPostMock,
  },
  endpoints: {
    project: {
      updateEvalTask: (id) => `/tracer/eval-task/${id}/`,
      patchEvalTask: () => "/tracer/eval-task/update_eval_task/",
      pauseEvalTask: (id) =>
        `/tracer/eval-task/pause_eval_task/?eval_task_id=${id}`,
      resumeEvalTask: (id) =>
        `/tracer/eval-task/unpause_eval_task/?eval_task_id=${id}`,
    },
  },
}));

vi.mock("src/sections/common/EvalsTasks/common", async () => {
  const actual = await vi.importActual("src/sections/common/EvalsTasks/common");
  return {
    ...actual,
    useGetTaskData: vi.fn(),
  };
});

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="icon">{icon}</span>,
}));

vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: () => <div>panels</div>,
}));

vi.mock("src/sections/common/EvalsTasks/TaskLogsView", () => ({
  default: () => <div>logs</div>,
}));

vi.mock("../components/TaskHeader", () => ({
  default: ({ actions, onNameChange }) => (
    <div>
      <div>task header</div>
      <button
        type="button"
        onClick={() => onNameChange?.("Renamed Inline Task")}
      >
        mock rename
      </button>
      <div>{actions}</div>
    </div>
  ),
}));

vi.mock("../components/TaskConfigPanel", () => ({
  default: () => <div>task config</div>,
}));

vi.mock("../components/TaskLivePreview", () => {
  const MockTaskLivePreview = React.forwardRef(() => <div>task preview</div>);
  MockTaskLivePreview.displayName = "MockTaskLivePreview";
  return { default: MockTaskLivePreview };
});

vi.mock("../components/TaskUsageTab", () => ({
  default: () => <div>task usage</div>,
}));

vi.mock("src/sections/common/EvalsTasks/EditTaskDrawer/TaskConfirmBox", () => ({
  default: () => null,
}));

const renderTaskDetail = (taskId = "missing-task") => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/dashboard/tasks/${taskId}`]}>
        <Routes>
          <Route path="/dashboard/tasks/:taskId" element={<TaskDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const loadedTask = (overrides = {}) => ({
  id: "task-1",
  name: "Original Task",
  project_id: "project-1",
  project_name: "Project One",
  status: "completed",
  filters_applied: {},
  evals_applied: [],
  sampling_rate: 100,
  spans_limit: 100,
  run_type: "continuous",
  row_type: "spans",
  ...overrides,
});

describe("TaskDetailPage", () => {
  beforeEach(() => {
    axiosPatchMock.mockReset();
    axiosPatchMock.mockResolvedValue({ data: { result: {} } });
    axiosPostMock.mockReset();
    axiosPostMock.mockResolvedValue({ data: { result: {} } });
    useGetTaskData.mockReset();
  });

  it("shows a not-found state instead of an endless spinner when the task API fails", () => {
    useGetTaskData.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: {
        statusCode: 404,
        result: "Eval task not found",
      },
    });

    renderTaskDetail();

    expect(screen.getByText("Task not available")).toBeInTheDocument();
    expect(screen.getByText("Eval task not found")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Back to Tasks/i }),
    ).toBeEnabled();
  });

  it("uses the detail PATCH route for inline rename without requiring edit_type", async () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask(),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");
    fireEvent.click(screen.getByRole("button", { name: /mock rename/i }));

    await waitFor(() => {
      expect(axiosPatchMock).toHaveBeenCalledWith("/tracer/eval-task/task-1/", {
        name: "Renamed Inline Task",
      });
    });
  });

  it("does not offer Pause for pending tasks because the backend only pauses running tasks", () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({ status: "pending" }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");

    expect(
      screen.queryByRole("button", { name: /^pause$/i }),
    ).not.toBeInTheDocument();
  });

  it("offers a source backlink when task filters include a trace id", () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({
        project_id: "project-1",
        filters_applied: {
          project_id: "project-1",
          trace_id: ["trace-1"],
        },
      }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");

    expect(
      screen.getByRole("button", { name: /open source/i }),
    ).toBeInTheDocument();
  });
});
