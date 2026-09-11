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
const useAuthContextMock = vi.hoisted(() => vi.fn(() => ({ role: "Admin" })));

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
      createEvalTask: () => "/tracer/eval-task/",
    },
  },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: useAuthContextMock,
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
  default: ({ leftPanel, rightPanel }) => (
    <div>
      <div>panels</div>
      {leftPanel}
      {rightPanel}
    </div>
  ),
}));

vi.mock("src/sections/common/EvalsTasks/TaskLogsView", () => ({
  default: () => <div>logs</div>,
}));

vi.mock("../components/TaskHeader", () => ({
  default: ({ name, status, actions, onNameChange }) => (
    <div>
      <div>task header</div>
      <div>{name}</div>
      <div>{status}</div>
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
  default: ({ control }) => (
    <div>
      <div>task config</div>
      {control?.register && (
        <input
          data-testid="test-start-date"
          {...control.register("startDate")}
        />
      )}
    </div>
  ),
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
    useAuthContextMock.mockReset();
    useAuthContextMock.mockReturnValue({ role: "Admin" });
    confirmDialogMock.mockReset();
    enqueueSnackbar.mockReset();
  });

  it("shows a retryable failure instead of an endless spinner when the task API fails", () => {
    const refetch = vi.fn();
    useGetTaskData.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      isFetching: false,
      refetch,
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
    fireEvent.click(screen.getByRole("button", { name: /^retry$/i }));
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("keeps prior task truth visible when a refresh fails and offers retry", () => {
    const refetch = vi.fn();
    useGetTaskData.mockReturnValue({
      data: loadedTask(),
      isLoading: false,
      isFetching: false,
      isError: true,
      error: new Error("refresh failed"),
      refetch,
    });

    renderTaskDetail("task-1");

    expect(screen.getByText("task header")).toBeInTheDocument();
    expect(screen.getByText("panels")).toBeInTheDocument();
    expect(
      screen.getByText(/Existing task details are still shown/i),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^retry$/i }));
    expect(refetch).toHaveBeenCalledOnce();
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

  it("renders the detail page with task header and tabs", () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask(),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");

    expect(screen.getByText("Original Task")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /details/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /logs/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /usage/i })).toBeInTheDocument();
  });

  it("pauses a running task from the header", async () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({ status: "running" }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");
    fireEvent.click(screen.getByRole("button", { name: /pause/i }));

    await waitFor(() => {
      expect(axiosPostMock).toHaveBeenCalledWith(
        "/tracer/eval-task/pause_eval_task/?eval_task_id=task-1",
        {},
      );
    });
  });

  it("resumes a paused task from the header", async () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({ status: "paused" }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");
    fireEvent.click(screen.getByRole("button", { name: /resume/i }));

    await waitFor(() => {
      expect(axiosPostMock).toHaveBeenCalledWith(
        "/tracer/eval-task/unpause_eval_task/?eval_task_id=task-1",
        {},
      );
    });
  });

  it("renders the open source button when a trace/span source link is present", () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({
        filters_applied: {
          trace_id: "trace-123",
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

  it("duplicates a task with converted wire-format filters, date_range, and evals", async () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({
        name: "Filter Task",
        run_type: "historical",
        row_type: "spans",
        spans_limit: 100,
        sampling_rate: 100,
        evals_applied: [{ id: "eval-1", mapping: { input: "prompt" } }],
        filters_applied: {
          project_id: "project-1",
          start_date: "2026-01-01T00:00:00.000Z",
          end_date: "2026-01-02T00:00:00.000Z",
          filters: [
            {
              column_id: "llm.model_name",
              filter_config: {
                filter_type: "text",
                filter_op: "equals",
                filter_value: "gpt-4",
                col_type: "SPAN_ATTRIBUTE",
              },
            },
          ],
        },
      }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");

    fireEvent.click(screen.getByRole("button", { name: /actions/i }));
    const duplicateItem = await screen.findByRole("menuitem", {
      name: /duplicate/i,
    });
    fireEvent.click(duplicateItem);

    const createButton = await screen.findByRole("button", { name: /^create$/i });
    fireEvent.click(createButton);

    await waitFor(() => {
      expect(axiosPostMock).toHaveBeenCalledWith("/tracer/eval-task/", {
        name: "Filter Task-duplicate",
        project: "project-1",
        run_type: "historical",
        row_type: "spans",
        spans_limit: 100,
        sampling_rate: 100,
        evals: ["eval-1"],
        filters: {
          project_id: "project-1",
          date_preset: "custom",
          date_range: [
            "2026-01-01T00:00:00.000Z",
            "2026-01-02T00:00:00.000Z",
          ],
          filters: [
            {
              column_id: "llm.model_name",
              filter_config: {
                filter_type: "text",
                filter_op: "equals",
                filter_value: "gpt-4",
                col_type: "SPAN_ATTRIBUTE",
              },
            },
          ],
        },
      });
    });
  });

  it("omits date_range when source task has no saved date range", async () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({
        name: "Unbounded Task",
        run_type: "historical",
        row_type: "spans",
        spans_limit: 50,
        sampling_rate: 100,
        evals_applied: [{ id: "eval-2" }],
        filters_applied: {
          project_id: "project-1",
        },
      }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");

    fireEvent.click(screen.getByRole("button", { name: /actions/i }));
    const duplicateItem = await screen.findByRole("menuitem", {
      name: /duplicate/i,
    });
    fireEvent.click(duplicateItem);

    const createButton = await screen.findByRole("button", { name: /^create$/i });
    fireEvent.click(createButton);

    await waitFor(() => {
      expect(axiosPostMock).toHaveBeenCalledWith("/tracer/eval-task/", {
        name: "Unbounded Task-duplicate",
        project: "project-1",
        run_type: "historical",
        row_type: "spans",
        spans_limit: 50,
        sampling_rate: 100,
        evals: ["eval-2"],
        filters: {
          project_id: "project-1",
        },
      });
    });
  });

  it("disables Duplicate action when user lacks ADD_TASKS_ALERTS permission", async () => {
    useAuthContextMock.mockReturnValue({ role: "Viewer" });
    useGetTaskData.mockReturnValue({
      data: loadedTask({ evals_applied: [{ id: "eval-1" }] }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");

    fireEvent.click(screen.getByRole("button", { name: /actions/i }));
    const duplicateItem = await screen.findByRole("menuitem", {
      name: /duplicate/i,
    });
    expect(duplicateItem).toHaveAttribute("aria-disabled", "true");
  });

  it("includes date_range when unbounded source task has live date edits in the form", async () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({
        name: "Unbounded Task",
        run_type: "historical",
        row_type: "spans",
        spans_limit: 50,
        sampling_rate: 100,
        evals_applied: [{ id: "eval-3" }],
        filters_applied: {
          project_id: "project-1",
        },
      }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");

    // Live edit the startDate in the form
    const dateInput = screen.getByTestId("test-start-date");
    fireEvent.change(dateInput, { target: { value: "2026-03-01T00:00:00.000Z" } });

    fireEvent.click(screen.getByRole("button", { name: /actions/i }));
    const duplicateItem = await screen.findByRole("menuitem", {
      name: /duplicate/i,
    });
    fireEvent.click(duplicateItem);

    const createButton = await screen.findByRole("button", { name: /^create$/i });
    fireEvent.click(createButton);

    await waitFor(() => {
      expect(axiosPostMock).toHaveBeenCalledWith(
        "/tracer/eval-task/",
        expect.objectContaining({
          name: "Unbounded Task-duplicate",
          project: "project-1",
          run_type: "historical",
          row_type: "spans",
          spans_limit: 50,
          sampling_rate: 100,
          evals: ["eval-3"],
          filters: expect.objectContaining({
            project_id: "project-1",
            date_range: expect.any(Array),
          }),
        }),
      );
    });
  });

  it("allows editing task name in dialog before creating duplicate", async () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({
        name: "Source Task",
        evals_applied: [{ id: "eval-1" }],
      }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");

    fireEvent.click(screen.getByRole("button", { name: /actions/i }));
    const duplicateItem = await screen.findByRole("menuitem", {
      name: /duplicate/i,
    });
    fireEvent.click(duplicateItem);

    const input = await screen.findByLabelText(/task name/i);
    expect(input).toHaveValue("Source Task-duplicate");

    fireEvent.change(input, { target: { value: "Custom Duplicated Task" } });
    expect(input).toHaveValue("Custom Duplicated Task");

    const createButton = screen.getByRole("button", { name: /^create$/i });
    fireEvent.click(createButton);

    await waitFor(() => {
      expect(axiosPostMock).toHaveBeenCalledWith(
        "/tracer/eval-task/",
        expect.objectContaining({
          name: "Custom Duplicated Task",
        }),
      );
    });
  });

  it("validates required task name and shows inline error when empty", async () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({
        name: "Source Task",
        evals_applied: [{ id: "eval-1" }],
      }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");

    fireEvent.click(screen.getByRole("button", { name: /actions/i }));
    const duplicateItem = await screen.findByRole("menuitem", {
      name: /duplicate/i,
    });
    fireEvent.click(duplicateItem);

    const input = await screen.findByLabelText(/task name/i);
    fireEvent.change(input, { target: { value: "   " } });

    await waitFor(() => {
      expect(screen.getByText("Task name is required")).toBeInTheDocument();
    });

    const createButton = screen.getByRole("button", { name: /^create$/i });
    expect(createButton).toBeDisabled();
  });

  it("validates 255 character limit and shows inline error when name is too long", async () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({
        name: "Source Task",
        evals_applied: [{ id: "eval-1" }],
      }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");

    fireEvent.click(screen.getByRole("button", { name: /actions/i }));
    const duplicateItem = await screen.findByRole("menuitem", {
      name: /duplicate/i,
    });
    fireEvent.click(duplicateItem);

    const input = await screen.findByLabelText(/task name/i);
    const longName = "a".repeat(256);
    fireEvent.change(input, { target: { value: longName } });

    await waitFor(() => {
      expect(
        screen.getByText("Task name must not exceed 255 characters"),
      ).toBeInTheDocument();
    });

    const createButton = screen.getByRole("button", { name: /^create$/i });
    expect(createButton).toBeDisabled();
  });

  it("immediately marks form invalid when initial default name exceeds 255 characters", async () => {
    const longOriginalName = "a".repeat(250); // with '-duplicate' (10 chars), total is 260 chars > 255
    useGetTaskData.mockReturnValue({
      data: loadedTask({
        name: longOriginalName,
        evals_applied: [{ id: "eval-1" }],
      }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");

    fireEvent.click(screen.getByRole("button", { name: /actions/i }));
    const duplicateItem = await screen.findByRole("menuitem", {
      name: /duplicate/i,
    });
    fireEvent.click(duplicateItem);

    await waitFor(() => {
      expect(
        screen.getByText("Task name must not exceed 255 characters"),
      ).toBeInTheDocument();
    });

    const createButton = screen.getByRole("button", { name: /^create$/i });
    expect(createButton).toBeDisabled();
  });

  it("closes duplicate dialog without duplicating when Cancel button is clicked", async () => {
    useGetTaskData.mockReturnValue({
      data: loadedTask({
        name: "Source Task",
        evals_applied: [{ id: "eval-1" }],
      }),
      isLoading: false,
      isError: false,
    });

    renderTaskDetail("task-1");

    fireEvent.click(screen.getByRole("button", { name: /actions/i }));
    const duplicateItem = await screen.findByRole("menuitem", {
      name: /duplicate/i,
    });
    fireEvent.click(duplicateItem);

    const cancelButton = await screen.findByRole("button", { name: /cancel/i });
    fireEvent.click(cancelButton);

    await waitFor(() => {
      expect(screen.queryByText("Duplicate Task")).not.toBeInTheDocument();
    });

    expect(axiosPostMock).not.toHaveBeenCalled();
  });
});
