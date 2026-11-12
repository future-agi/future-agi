import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TaskDetailPage from "../TaskDetailPage";
import { useGetTaskData } from "src/sections/common/EvalsTasks/common";

const axiosPatchMock = vi.hoisted(() => vi.fn());
const confirmDialogMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { patch: axiosPatchMock, post: vi.fn() },
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
  useAuthContext: () => ({ role: "Admin" }),
}));

vi.mock("src/sections/common/EvalsTasks/common", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetTaskData: vi.fn(),
}));

vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: ({ icon }) => <span data-testid="icon">{icon}</span>,
}));

vi.mock("src/components/snackbar", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <div>
      {leftPanel}
      {rightPanel}
    </div>
  ),
}));

vi.mock("src/sections/common/EvalsTasks/TaskLogsView", () => ({
  default: () => <div>logs</div>,
}));

vi.mock("../components/TaskHeader", () => ({
  default: ({ actions }) => <div>{actions}</div>,
}));

// The window inputs stand in for the time-window picker, so a test can move
// the window the way a user does.
vi.mock("../components/TaskConfigPanel", () => ({
  default: ({ control }) => (
    <div>
      <input data-testid="start-date" {...control.register("startDate")} />
      <input data-testid="date-preset" {...control.register("datePreset")} />
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

const AskPageProbe = () => {
  const location = useLocation();
  return (
    <div data-testid="ask-page">{location.pathname + location.search}</div>
  );
};

const renderTaskDetail = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/dashboard/tasks/task-1"]}>
        <Routes>
          <Route path="/dashboard/tasks/:taskId" element={<TaskDetailPage />} />
          <Route
            path="/dashboard/observe/:observeId/ask"
            element={<AskPageProbe />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

// As the server stores them: microseconds, and none when they are zero.
const SAVED_RANGE = ["2026-01-01T09:30:00.123000Z", "2026-01-09T00:00:00Z"];

const historicalTask = (overrides = {}) => ({
  id: "task-1",
  name: "Past week task",
  project_id: "project-1",
  project_name: "Project One",
  status: "completed",
  filters_applied: {
    project_id: "project-1",
    date_preset: "7d",
    date_range: SAVED_RANGE,
  },
  evals_applied: [{ id: "eval-1" }],
  sampling_rate: 100,
  spans_limit: 100,
  run_type: "historical",
  row_type: "spans",
  ...overrides,
});

const loadTask = (task) =>
  useGetTaskData.mockReturnValue({
    data: task,
    isLoading: false,
    isError: false,
  });

const confirmFrom = async (buttonName, dialogTitle, editType) => {
  fireEvent.click(screen.getByRole("button", { name: buttonName }));
  await screen.findByText(dialogTitle);
  const { onConfirm } = confirmDialogMock.mock.calls.at(-1)[0];
  await act(async () => {
    onConfirm(editType);
  });
  await waitFor(() => expect(axiosPatchMock).toHaveBeenCalledTimes(1));
  return axiosPatchMock.mock.calls[0][1];
};

describe("TaskDetailPage save guards", () => {
  beforeEach(() => {
    axiosPatchMock.mockReset();
    axiosPatchMock.mockResolvedValue({ data: { result: {} } });
    useGetTaskData.mockReset();
    confirmDialogMock.mockReset();
  });

  describe("a historical task's saved window", () => {
    it("is sent unchanged when Save leaves the window alone", async () => {
      loadTask(historicalTask());
      renderTaskDetail();

      const payload = await confirmFrom(/^save$/i, "Update Task", "edit_rerun");

      expect(payload.edit_type).toBe("edit_rerun");
      expect(payload.filters).toEqual({
        project_id: "project-1",
        date_preset: "7d",
        date_range: SAVED_RANGE,
      });
    });

    it("leaves out a preset the task never stored", async () => {
      loadTask(
        historicalTask({
          filters_applied: { project_id: "project-1", date_range: SAVED_RANGE },
        }),
      );
      renderTaskDetail();

      const payload = await confirmFrom(/^save$/i, "Update Task", "edit_rerun");

      expect(payload.filters).toEqual({
        project_id: "project-1",
        date_range: SAVED_RANGE,
      });
    });

    it("moves to the newly picked preset when the user changes it", async () => {
      loadTask(historicalTask());
      renderTaskDetail();

      fireEvent.change(screen.getByTestId("date-preset"), {
        target: { value: "30D" },
      });
      const payload = await confirmFrom(/^save$/i, "Update Task", "fresh_run");

      expect(payload.filters.date_preset).toBe("30d");
      expect(payload.filters.date_range).not.toEqual(SAVED_RANGE);
    });

    it("is resolved against now on Re-run", async () => {
      loadTask(historicalTask());
      renderTaskDetail();

      const payload = await confirmFrom(/re-run/i, "Re-run Task", "fresh_run");

      expect(payload.filters.date_preset).toBe("7d");
      expect(payload.filters.date_range).not.toEqual(SAVED_RANGE);
    });
  });

  describe("an Instant eval run", () => {
    const instantRun = () =>
      historicalTask({
        name: "Instant eval · Did the agent answer?",
        filters_applied: {
          project_id: "project-1",
          instant_eval_run: true,
          instant_config_ids: ["eval-1"],
        },
      });

    it("offers no Save or Re-run, which would replace its answers", () => {
      loadTask(instantRun());
      renderTaskDetail();

      expect(
        screen.queryByRole("button", { name: /re-run/i }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: /^save$/i }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByText(
          "This is an Instant eval run. Ask it again from its Ask page.",
        ),
      ).toBeInTheDocument();
    });

    it("opens the run on its Ask page", () => {
      loadTask(instantRun());
      renderTaskDetail();

      fireEvent.click(screen.getByRole("button", { name: /open in ask/i }));

      expect(screen.getByTestId("ask-page")).toHaveTextContent(
        "/dashboard/observe/project-1/ask?run=task-1",
      );
    });

    it("keeps Save and Re-run on an ordinary task", () => {
      loadTask(historicalTask());
      renderTaskDetail();

      expect(screen.getByRole("button", { name: /re-run/i })).toBeEnabled();
      expect(screen.getByRole("button", { name: /^save$/i })).toBeEnabled();
      expect(
        screen.queryByRole("button", { name: /open in ask/i }),
      ).not.toBeInTheDocument();
    });
  });
});
