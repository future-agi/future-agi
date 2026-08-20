import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TaskDetailPage from "../TaskDetailPage";
import { useGetTaskData } from "src/sections/common/EvalsTasks/common";
import { enqueueSnackbar } from "src/components/snackbar";

const axiosPatchMock = vi.hoisted(() => vi.fn());
const axiosPostMock = vi.hoisted(() => vi.fn());
const confirmDialogMock = vi.hoisted(() => vi.fn());

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
  default: (props) => {
    confirmDialogMock(props);
    return props.open ? <div>{props.title}</div> : null;
  },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
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
    confirmDialogMock.mockReset();
    enqueueSnackbar.mockReset();
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

  it("labels the confirm dialog as an update when it comes from Save", async () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({ evals_applied: [{ id: "eval-1" }] }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(await screen.findByText("Update Task")).toBeInTheDocument();
    expect(confirmDialogMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ confirmText: "Run task" }),
    );
  });

  it("opens the confirm dialog as a re-run when it comes from the header", async () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({ evals_applied: [{ id: "eval-1" }] }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");
    fireEvent.click(screen.getByRole("button", { name: /re-run/i }));

    expect(await screen.findByText("Re-run Task")).toBeInTheDocument();
    expect(confirmDialogMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ confirmText: "Re-run" }),
    );
  });

  it("submits the same mutation Save uses when Re-run is confirmed", async () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({ evals_applied: [{ id: "eval-1" }] }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");
    fireEvent.click(screen.getByRole("button", { name: /re-run/i }));

    await screen.findByText("Re-run Task");

    const { onConfirm } = confirmDialogMock.mock.calls.at(-1)[0];
    await act(async () => {
      onConfirm("fresh_run");
    });

    await waitFor(() => {
      expect(axiosPatchMock).toHaveBeenCalledWith(
        "/tracer/eval-task/update_eval_task/",
        expect.objectContaining({
          edit_type: "fresh_run",
          evals: ["eval-1"],
          eval_task_id: "task-1",
        }),
      );
    });
  });

  it("blocks re-running a task that is still going, which would race the live run", () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({
        status: "running",
        evals_applied: [{ id: "eval-1" }],
      }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");

    expect(screen.getByRole("button", { name: /re-run/i })).toBeDisabled();
  });

  it("sends the user back to Details when a re-run is attempted on an invalid form", async () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({ evals_applied: [] }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");
    fireEvent.click(screen.getByRole("tab", { name: /logs/i }));
    expect(screen.getByText("logs")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /re-run/i }));

    await waitFor(() => {
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Fix the highlighted fields before running this task.",
        { variant: "error" },
      );
    });
    expect(screen.getByText("panels")).toBeInTheDocument();
  });

  it("reports a re-run, not an update, when the Re-run confirm resolves", async () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({ evals_applied: [{ id: "eval-1" }] }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");
    fireEvent.click(screen.getByRole("button", { name: /re-run/i }));

    await screen.findByText("Re-run Task");

    const { onConfirm } = confirmDialogMock.mock.calls.at(-1)[0];
    await act(async () => {
      onConfirm("fresh_run");
    });

    await waitFor(() => {
      expect(enqueueSnackbar).toHaveBeenCalledWith("Re-run started", {
        variant: "success",
      });
    });
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(
      "Task updated successfully",
      expect.anything(),
    );
  });

  it("still reports an update, not a re-run, when the Save confirm resolves", async () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({ evals_applied: [{ id: "eval-1" }] }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await screen.findByText("Update Task");

    const { onConfirm } = confirmDialogMock.mock.calls.at(-1)[0];
    await act(async () => {
      onConfirm("edit");
    });

    await waitFor(() => {
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Task updated successfully",
        { variant: "success" },
      );
    });
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(
      "Re-run started",
      expect.anything(),
    );
  });
});
