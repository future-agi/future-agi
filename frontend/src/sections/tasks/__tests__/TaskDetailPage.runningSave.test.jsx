/* eslint-disable react/prop-types -- Lightweight UI doubles stand in for the page's panels. */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TaskDetailPage from "../TaskDetailPage";
import { useGetTaskData } from "src/sections/common/EvalsTasks/common";
import { endpoints } from "src/utils/axios";

const axiosPatchMock = vi.hoisted(() => vi.fn());
const axiosPostMock = vi.hoisted(() => vi.fn());
const confirmDialogMock = vi.hoisted(() => vi.fn());
const auth = vi.hoisted(() => ({ role: "Admin" }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { patch: axiosPatchMock, post: axiosPostMock },
    endpoints: {
      ...actual.endpoints,
      project: {
        ...actual.endpoints.project,
        patchEvalTask: () => "/tracer/eval-task/update_eval_task/",
      },
    },
  };
});

vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ role: auth.role }),
}));

vi.mock("src/sections/common/EvalsTasks/common", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetTaskData: vi.fn(),
}));

vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

vi.mock("src/components/snackbar", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

vi.mock(
  "src/components/resizablePanels/ResizablePanels",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: ({ leftPanel, rightPanel }) => (
      <div>
        {leftPanel}
        {rightPanel}
      </div>
    ),
  }),
);

vi.mock("../components/TaskHeader", async (importOriginal) => ({
  ...(await importOriginal()),
  default: ({ actions }) => <div>{actions}</div>,
}));

// The four panels below are replaced whole, as in the other TaskDetailPage
// tests, so the real panels' data hooks and eval picker never load.
vi.mock("src/sections/common/EvalsTasks/TaskLogsView", () => ({
  default: () => <div>logs</div>,
}));

// One configuration field stands in for the form, so a test can edit it the
// way a user does.
vi.mock("../components/TaskConfigPanel", () => ({
  default: ({ control }) => (
    <input
      aria-label="Sampling rate"
      type="number"
      {...control.register("samplingRate", { valueAsNumber: true })}
    />
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

vi.mock(
  "src/sections/common/EvalsTasks/EditTaskDrawer/TaskConfirmBox",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: (props) => {
      confirmDialogMock(props);
      return props.open ? <div>{props.title}</div> : null;
    },
  }),
);

const PAUSE_TO_EDIT = "Pause the task to edit it";

const runningTask = (overrides = {}) => ({
  id: "task-1",
  name: "Support replies",
  project_id: "project-1",
  project_name: "Project One",
  status: "running",
  last_run: "2026-01-01T09:30:00Z",
  filters_applied: {},
  evals_applied: [{ id: "eval-1" }],
  sampling_rate: 100,
  spans_limit: 100,
  run_type: "continuous",
  row_type: "spans",
  ...overrides,
});

const loadTask = (task) =>
  useGetTaskData.mockReturnValue({
    data: task,
    isLoading: false,
    isError: false,
  });

const renderTaskDetail = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const tree = () => (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/dashboard/tasks/task-1"]}>
        <Routes>
          <Route path="/dashboard/tasks/:taskId" element={<TaskDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
  const view = render(tree());
  return {
    // A poll (or the cache after Pause) brings the task's new status.
    showTask: (task) => {
      loadTask(task);
      view.rerender(tree());
    },
  };
};

const saveButton = () => screen.getByRole("button", { name: /^save$/i });
const samplingRate = () =>
  screen.getByRole("spinbutton", { name: "Sampling rate" });

describe("TaskDetailPage while the task runs", () => {
  beforeEach(() => {
    auth.role = "Admin";
    axiosPatchMock.mockReset();
    axiosPatchMock.mockResolvedValue({ data: { result: {} } });
    axiosPostMock.mockReset();
    axiosPostMock.mockResolvedValue({ data: { result: {} } });
    useGetTaskData.mockReset();
    confirmDialogMock.mockReset();
  });

  it("disables Save and says to pause next to Pause, with the fields still shown", async () => {
    loadTask(runningTask());
    renderTaskDetail();

    expect(saveButton()).toBeDisabled();
    const hint = screen.getByText(PAUSE_TO_EDIT);
    expect(hint.nextElementSibling).toBe(
      screen.getByRole("button", { name: /^pause$/i }),
    );
    expect(samplingRate()).toHaveValue(100);

    await act(async () => {
      fireEvent.click(saveButton());
    });
    expect(confirmDialogMock).not.toHaveBeenCalledWith(
      expect.objectContaining({ open: true }),
    );
    expect(axiosPatchMock).not.toHaveBeenCalled();
  });

  it("keeps an edit made while running and saves it once the task is paused", async () => {
    loadTask(runningTask());
    const { showTask } = renderTaskDetail();

    fireEvent.change(samplingRate(), { target: { value: "50" } });
    expect(saveButton()).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /^pause$/i }));
    await waitFor(() =>
      expect(axiosPostMock).toHaveBeenCalledWith(
        endpoints.project.pauseEvalTask("task-1"),
        {},
      ),
    );
    showTask(runningTask({ status: "paused" }));

    expect(screen.queryByText(PAUSE_TO_EDIT)).not.toBeInTheDocument();
    expect(samplingRate()).toHaveValue(50);
    expect(saveButton()).toBeEnabled();

    fireEvent.click(saveButton());
    await screen.findByText("Update Task");
    const { onConfirm } = confirmDialogMock.mock.calls.at(-1)[0];
    await act(async () => {
      onConfirm("edit");
    });

    await waitFor(() =>
      expect(axiosPatchMock).toHaveBeenCalledWith(
        "/tracer/eval-task/update_eval_task/",
        expect.objectContaining({
          eval_task_id: "task-1",
          sampling_rate: 50,
          edit_type: "edit",
        }),
      ),
    );
  });

  describe("leaves other states as they were", () => {
    it("keeps Save enabled on a pending task, which the server still updates", () => {
      loadTask(runningTask({ status: "pending" }));
      renderTaskDetail();

      expect(saveButton()).toBeEnabled();
      expect(screen.queryByText(PAUSE_TO_EDIT)).not.toBeInTheDocument();
    });

    it("shows no pause hint on a running Instant eval run, which is asked again from Ask", () => {
      loadTask(
        runningTask({
          filters_applied: {
            project_id: "project-1",
            instant_eval_run: true,
            instant_config_ids: ["eval-1"],
          },
        }),
      );
      renderTaskDetail();

      expect(screen.queryByText(PAUSE_TO_EDIT)).not.toBeInTheDocument();
      expect(
        screen.getByText(
          "This is an Instant eval run. Ask it again from its Ask page.",
        ),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: /^pause$/i }),
      ).toBeInTheDocument();
    });

    it("shows no pause hint to a viewer, who cannot edit after a pause either", () => {
      auth.role = "Viewer";
      loadTask(runningTask());
      renderTaskDetail();

      expect(saveButton()).toBeDisabled();
      expect(screen.queryByText(PAUSE_TO_EDIT)).not.toBeInTheDocument();
    });
  });
});
