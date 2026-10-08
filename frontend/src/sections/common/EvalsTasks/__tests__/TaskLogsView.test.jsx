import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
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

function renderView() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TaskLogsView evalTaskId="task-1" taskStatus="completed" />
    </QueryClientProvider>,
  );
}

// StatCard renders the value and its label as siblings in one box.
const statCardText = async (label) =>
  (await screen.findByText(label)).parentElement.textContent;

describe("TaskLogsView totals", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("counts spans, not eval runs, when one span ran two evals", async () => {
    mocks.readEvalTaskLogs.mockResolvedValue(summary());

    renderView();

    expect(await statCardText("Total Spans")).toBe("1Total Spans");
    expect(await statCardText("Eval Runs")).toBe("2Eval Runs");
    expect(screen.getByText("2 / 2 passed")).toBeInTheDocument();
    expect(
      screen.getByText("All evaluations completed successfully"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Skipped")).not.toBeInTheDocument();
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

  it.each([
    { success_count: 0, skipped_count: 373, total_count: 373 },
    { success_count: 0, skipped_count: 33, total_count: 33 },
    { success_count: 2, skipped_count: 1, total_count: 3 },
  ])(
    "shows skipped runs without claiming all $total_count succeeded",
    async (counts) => {
      mocks.readEvalTaskLogs.mockResolvedValue(summary(counts));

      renderView();

      expect(await statCardText("Skipped")).toBe(
        `${counts.skipped_count}Skipped`,
      );
      expect(await statCardText("Successful")).toBe(
        `${counts.success_count}Successful`,
      );
      expect(screen.getByText("Completed")).toBeInTheDocument();
      expect(
        screen.getByText("Evaluation processing completed"),
      ).toBeInTheDocument();
      expect(
        screen.queryByText("All evaluations completed successfully"),
      ).not.toBeInTheDocument();
    },
  );

  it("does not claim success for errors without a returned error group", async () => {
    mocks.readEvalTaskLogs.mockResolvedValue(
      summary({ success_count: 0, errors_count: 2 }),
    );

    renderView();

    expect(await statCardText("Errors")).toBe("2Errors");
    expect(
      screen.getByText("Evaluation processing completed"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("All evaluations completed successfully"),
    ).not.toBeInTheDocument();
  });

  it("keeps an empty task distinct from a completed evaluation run", async () => {
    mocks.readEvalTaskLogs.mockResolvedValue(
      summary({ success_count: 0, total_count: 0, target_count: 0 }),
    );

    renderView();

    expect(await screen.findByText("No data")).toBeInTheDocument();
    expect(screen.queryByText("Skipped")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Evaluation processing completed"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("All evaluations completed successfully"),
    ).not.toBeInTheDocument();
  });
});
