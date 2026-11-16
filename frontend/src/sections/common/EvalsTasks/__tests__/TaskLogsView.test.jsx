import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "src/utils/test-utils";
import TaskLogsView from "../TaskLogsView";

const mocks = vi.hoisted(() => ({
  readEvalTaskLogs: vi.fn(),
}));

vi.mock("../task_log_read", () => ({
  readEvalTaskLogs: mocks.readEvalTaskLogs,
}));

vi.mock("src/components/iconify", () => ({
  default: () => <span data-testid="iconify" />,
}));

const summary = (overrides = {}) => ({
  success_count: 2,
  errors_count: 0,
  skipped_count: 0,
  warnings_count: 0,
  total_count: 2,
  target_count: 1,
  error_groups: [],
  warning_groups: [],
  error_groups_truncated: false,
  warning_groups_truncated: false,
  status: "completed",
  run_type: "historical",
  row_type: "spans",
  start_time: null,
  end_time: null,
  ...overrides,
});

function renderView(taskStatus = "completed") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <TaskLogsView evalTaskId="task-1" taskStatus={taskStatus} />
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

// StatCard renders the value and its label as siblings in one box.
const statCardText = async (label) =>
  (await screen.findByText(label)).parentElement.textContent;

describe("TaskLogsView totals", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it.each(["failed", undefined])(
    "honestly explains an empty failed task using response or parent status (%s)",
    async (status) => {
      mocks.readEvalTaskLogs.mockResolvedValue(
        summary({ status, total_count: 0, target_count: 0, success_count: 0 }),
      );
      renderView("Failed");
      expect(
        await screen.findByText(
          "This task failed. No evaluation runs are available in this log, and the failure reason is unavailable.",
        ),
      ).toBeVisible();
      expect(
        screen.queryByText("All evaluations completed successfully"),
      ).not.toBeInTheDocument();
    },
  );

  it.each(["pending", "running", "completed", undefined])(
    "does not infer failure from an empty log with status %s",
    async (status) => {
      mocks.readEvalTaskLogs.mockResolvedValue(
        summary({ status, total_count: 0, target_count: 0, success_count: 0 }),
      );
      renderView(status);
      await screen.findByText("Completed runs");
      expect(
        screen.queryByText(/No evaluation runs are available in this log/),
      ).not.toBeInTheDocument();
    },
  );

  it("does not claim no evaluations ran when a failed task has executions", async () => {
    mocks.readEvalTaskLogs.mockResolvedValue(summary({ status: "failed" }));
    renderView("failed");
    await screen.findByText("Completed runs");
    expect(
      screen.queryByText(/No evaluation runs are available in this log/),
    ).not.toBeInTheDocument();
  });

  it.each([false, true])(
    "round8 bounds failed-empty copy to visible logs (archived prior runs=%s)",
    async (archived) => {
      // fresh_run soft-deletes completed entries. The logs API aggregates only
      // deleted=false; it exposes neither historical execution nor failure cause.
      const history = archived ? [{ status: "completed", deleted: true }] : [];
      const visible = history.filter((entry) => !entry.deleted);
      mocks.readEvalTaskLogs.mockResolvedValue(
        summary({
          status: "failed",
          total_count: visible.length,
          target_count: 0,
          success_count: visible.filter((entry) => entry.status === "completed")
            .length,
        }),
      );
      renderView("failed");
      expect(
        await screen.findByText(
          "This task failed. No evaluation runs are available in this log, and the failure reason is unavailable.",
        ),
      ).toBeVisible();
      expect(
        screen.queryByText(/before any evaluation ran|reason was not recorded/),
      ).not.toBeInTheDocument();
    },
  );

  it.each([
    "total_count",
    "success_count",
    "errors_count",
    "skipped_count",
    "warnings_count",
  ])("round8 does not infer zero executions from missing %s", async (key) => {
    const data = summary({
      status: "failed",
      total_count: 0,
      target_count: 0,
      success_count: 0,
    });
    delete data[key];
    mocks.readEvalTaskLogs.mockResolvedValue(data);
    renderView("failed");
    await screen.findByText("Completed runs");
    expect(screen.queryByText(/This task failed/)).not.toBeInTheDocument();
  });

  it("round8 does not infer empty execution history while logs are loading", () => {
    mocks.readEvalTaskLogs.mockReturnValue(new Promise(() => {}));
    renderView("failed");
    expect(screen.getByRole("progressbar")).toBeVisible();
    expect(screen.queryByText(/This task failed/)).not.toBeInTheDocument();
  });

  it("round8 hides stale failed-empty evidence during a refresh and after refresh failure", async () => {
    mocks.readEvalTaskLogs.mockResolvedValueOnce(
      summary({
        status: "failed",
        total_count: 0,
        target_count: 0,
        success_count: 0,
      }),
    );
    const { queryClient } = renderView("failed");
    await screen.findByText(/This task failed/);
    let rejectRefresh;
    mocks.readEvalTaskLogs.mockImplementationOnce(
      () =>
        new Promise((_, reject) => {
          rejectRefresh = reject;
        }),
    );
    let refreshing;
    act(() => {
      refreshing = queryClient.refetchQueries({
        queryKey: ["eval-task-logs", "task-1"],
      });
    });
    await waitFor(() =>
      expect(screen.queryByText(/This task failed/)).not.toBeInTheDocument(),
    );
    await act(async () => {
      rejectRefresh(new Error("offline"));
      await refreshing;
    });
    await waitFor(() =>
      expect(
        queryClient.getQueryState(["eval-task-logs", "task-1"]).status,
      ).toBe("error"),
    );
    expect(screen.queryByText(/This task failed/)).not.toBeInTheDocument();
  });

  it.each([
    { errors_count: 1 },
    { warnings_count: 1 },
    { skipped_count: 1 },
    { total_count: null },
    {
      error_groups: [
        { sample: "Unknown failure", normalized: "Unknown failure", count: 1 },
      ],
    },
    { warning_groups: [{ warning_type: "partial_input", count: 1 }] },
  ])("round8 retains count and diagnostic guards (%j)", async (override) => {
    mocks.readEvalTaskLogs.mockResolvedValue(
      summary({
        status: "failed",
        total_count: 0,
        target_count: 0,
        success_count: 0,
        ...override,
      }),
    );
    renderView("failed");
    await screen.findByText("Completed runs");
    expect(screen.queryByText(/This task failed/)).not.toBeInTheDocument();
  });

  it("counts a completed false result as an execution, never as an outcome pass", async () => {
    // The logs endpoint counts COMPLETED statuses, not output_bool values.
    const executions = [{ status: "completed", output_bool: false }];
    mocks.readEvalTaskLogs.mockResolvedValue(
      summary({
        success_count: executions.filter(
          (entry) => entry.status === "completed",
        ).length,
        total_count: executions.length,
      }),
    );
    renderView();
    expect(
      await screen.findByText("1 / 1 evaluation runs completed without errors"),
    ).toBeVisible();
    expect(await statCardText("Completed runs")).toBe("1Completed runs");
    expect(screen.queryByText(/passed|Successful/)).not.toBeInTheDocument();
  });

  it("explains provider setup and fresh-run recovery for the missing-key constructor error", async () => {
    const raw =
      "Error during evaluation: The api_key client option must be set either by passing api_key to the client or by setting the OPENAI_API_KEY environment variable";
    mocks.readEvalTaskLogs.mockResolvedValue(
      summary({
        success_count: 0,
        errors_count: 4,
        total_count: 4,
        status: "running",
        error_groups: [
          {
            normalized: raw.replace("Error during evaluation: ", ""),
            sample: raw,
            count: 4,
          },
        ],
      }),
    );
    renderView();
    expect(
      await screen.findByText("Missing model provider credential"),
    ).toBeVisible();
    expect(
      screen.getByText(/Ask a workspace admin or owner to configure/),
    ).toBeVisible();
    expect(
      screen.getByText(
        /After configuration is fixed, Pause the running task, then start a fresh Re-run/,
      ),
    ).toBeVisible();
    expect(screen.getByText(/Settings → AI providers/)).toBeVisible();
    expect(
      screen.getByText(
        /Delete Past Data & Start Fresh.*removes previous evaluations/,
      ),
    ).toBeVisible();
    expect(
      screen.getByText(/Refreshing logs does not retry evaluations/),
    ).toBeVisible();
    expect(screen.getByText(raw)).toBeVisible();
    expect(screen.queryByText("Unknown error")).not.toBeInTheDocument();
    expect(screen.queryByText(/custom template/)).not.toBeInTheDocument();
  });

  it.each([
    [
      "Required key api_key not found for span abc123",
      "Missing attribute on spans",
    ],
    ["Unexpected api_key diagnostic marker", "Unknown error"],
    ["Invalid API key", "Model provider authentication failed"],
  ])("does not guess missing credentials for %s", async (raw, title) => {
    mocks.readEvalTaskLogs.mockResolvedValue(
      summary({ error_groups: [{ sample: raw, normalized: raw, count: 1 }] }),
    );
    renderView();
    expect(await screen.findByText(title)).toBeVisible();
    expect(
      screen.queryByText("Missing model provider credential"),
    ).not.toBeInTheDocument();
  });

  it("counts spans, not eval runs, when one span ran two evals", async () => {
    mocks.readEvalTaskLogs.mockResolvedValue(summary());

    renderView();

    expect(await statCardText("Total Spans")).toBe("1Total Spans");
    expect(await statCardText("Eval Runs")).toBe("2Eval Runs");
    expect(
      screen.getByText("2 / 2 evaluation runs completed without errors"),
    ).toBeInTheDocument();
  });

  it("counts calls for a voice-call task", async () => {
    mocks.readEvalTaskLogs.mockResolvedValue(
      summary({
        row_type: "voiceCalls",
        success_count: 1,
        errors_count: 2,
        total_count: 3,
        target_count: 1,
      }),
    );

    renderView();

    expect(await statCardText("Total Calls")).toBe("1Total Calls");
    expect(await statCardText("Eval Runs")).toBe("3Eval Runs");
  });

  it("shows no separate eval-run card when each target ran one eval", async () => {
    mocks.readEvalTaskLogs.mockResolvedValue(
      summary({ success_count: 3, total_count: 3, target_count: 3 }),
    );

    renderView();

    expect(await statCardText("Total Spans")).toBe("3Total Spans");
    expect(screen.queryByText("Eval Runs")).not.toBeInTheDocument();
  });
});
