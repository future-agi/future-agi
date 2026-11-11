import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import TaskRunOutcome from "./task_run_outcome";
import { taskRunOutcome } from "./taskRunOutcome";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    project: { getEvalTaskLogs: () => "/tracer/eval-task/get_eval_task_logs/" },
  },
}));

const logs = (overrides = {}) => ({
  status: "completed",
  row_type: "spans",
  success_count: 50,
  errors_count: 110,
  skipped_count: 0,
  warnings_count: 0,
  total_count: 160,
  error_groups: [],
  warning_groups: [],
  error_groups_truncated: false,
  warning_groups_truncated: false,
  ...overrides,
});

let client;
const show = (props) => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <TaskRunOutcome taskId="task-1" {...props} />
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("taskRunOutcome", () => {
  it("names failed evaluation runs of a finished run", () => {
    expect(taskRunOutcome(logs())).toEqual({
      role: "warning",
      label: "110 of 160 evaluation runs failed",
    });
    expect(
      taskRunOutcome(logs({ success_count: 0, errors_count: 160 })),
    ).toEqual({ role: "danger", label: "160 of 160 evaluation runs failed" });
  });
  it("says when a finished run evaluated nothing", () => {
    expect(
      taskRunOutcome(
        logs({ success_count: 0, errors_count: 0, total_count: 0 }),
      ),
    ).toEqual({ role: "warning", label: "Nothing was evaluated", empty: true });
  });
  it("never reads a run whose every entry was skipped as a clean completion", () => {
    expect(
      taskRunOutcome(
        logs({ success_count: 0, errors_count: 0, skipped_count: 160 }),
      ),
    ).toEqual({
      role: "warning",
      label: "Nothing was evaluated: all 160 evaluation runs skipped",
    });
    expect(
      taskRunOutcome(
        logs({
          success_count: 0,
          errors_count: 0,
          skipped_count: 1,
          total_count: 1,
        }),
      ),
    ).toEqual({
      role: "warning",
      label: "Nothing was evaluated: 1 evaluation run skipped",
    });
  });
  it("names failed and skipped runs together, and danger only when all failed", () => {
    expect(
      taskRunOutcome(
        logs({ success_count: 10, errors_count: 50, skipped_count: 100 }),
      ),
    ).toEqual({
      role: "warning",
      label: "50 failed · 100 skipped of 160 evaluation runs",
    });
    // Nothing succeeded, but skipped runs never ran: not every run failed.
    expect(
      taskRunOutcome(
        logs({ success_count: 0, errors_count: 60, skipped_count: 100 }),
      ),
    ).toEqual({
      role: "warning",
      label: "60 failed · 100 skipped of 160 evaluation runs",
    });
    expect(
      taskRunOutcome(
        logs({ success_count: 150, errors_count: 0, skipped_count: 10 }),
      ),
    ).toEqual({ role: "warning", label: "10 of 160 evaluation runs skipped" });
  });
  it("adds nothing for a clean run, a run still in progress or unreadable counts", () => {
    expect(taskRunOutcome(logs({ errors_count: 0 }))).toBeNull();
    expect(taskRunOutcome(logs({ status: "running" }))).toBeNull();
    expect(taskRunOutcome(logs({ errors_count: undefined }))).toBeNull();
    expect(taskRunOutcome(logs({ skipped_count: null }))).toBeNull();
    expect(taskRunOutcome(null)).toBeNull();
  });
});

describe("TaskRunOutcome", () => {
  it("reads the Logs tab's query once for a completed task and links to it", async () => {
    axios.get.mockResolvedValue({ data: { status: true, result: logs() } });
    const onViewLogs = vi.fn();
    show({ status: "completed", onViewLogs });
    expect(
      await screen.findByText("110 of 160 evaluation runs failed"),
    ).toBeVisible();
    expect(axios.get).toHaveBeenCalledTimes(1);
    expect(axios.get.mock.calls[0][0]).toBe(
      "/tracer/eval-task/get_eval_task_logs/",
    );
    expect(axios.get.mock.calls[0][1].params).toEqual({
      eval_task_id: "task-1",
    });
    // Same cache entry the Logs tab reads, so opening it reuses the counts.
    expect(client.getQueryData(["eval-task-logs", "task-1"])).toMatchObject({
      errors_count: 110,
    });
    await userEvent.click(screen.getByRole("button", { name: "View logs" }));
    expect(onViewLogs).toHaveBeenCalledTimes(1);
  });

  it("points a run that evaluated nothing at the sampling audit, not empty logs", async () => {
    axios.get.mockResolvedValue({
      data: {
        status: true,
        result: logs({
          success_count: 0,
          errors_count: 0,
          total_count: 0,
          target_count: 0,
        }),
      },
    });
    const onViewLogs = vi.fn();
    const onViewSamplingAudit = vi.fn();
    const { unmount } = show({
      status: "completed",
      onViewLogs,
      onViewSamplingAudit,
    });
    expect(await screen.findByText("Nothing was evaluated")).toBeVisible();
    expect(screen.queryByRole("button", { name: "View logs" })).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: "View sampling audit" }),
    );
    expect(onViewSamplingAudit).toHaveBeenCalledTimes(1);
    expect(onViewLogs).not.toHaveBeenCalled();
    unmount();
    // Without sampling there is nowhere useful to point.
    show({ status: "completed", onViewLogs });
    expect(await screen.findByText("Nothing was evaluated")).toBeVisible();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it.each(["running", "pending", "failed", "paused"])(
    "does not read logs or show anything for a %s task",
    async (status) => {
      show({ status, onViewLogs: vi.fn() });
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(axios.get).not.toHaveBeenCalled();
      expect(screen.queryByLabelText("Last run outcome")).toBeNull();
    },
  );

  it("shows nothing while loading or when the read fails, never a 0", async () => {
    let reject;
    axios.get.mockImplementation(
      () =>
        new Promise((_, fail) => {
          reject = fail;
        }),
    );
    const { container } = show({ status: "completed" });
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    expect(container).toBeEmptyDOMElement();
    reject(new Error("unavailable"));
    await waitFor(() =>
      expect(client.getQueryState(["eval-task-logs", "task-1"]).status).toBe(
        "error",
      ),
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/0 of/)).toBeNull();
  });
});
